"""
多 Agent 协作工作流 —— 用 LangGraph 实现「生成-校验」闭环

为什么需要多 Agent
------------------
前面四条链与答疑 Agent 都是**单 Agent**形态：模型自己调用工具、自己给答案。
但「生成一段讲解」这件事有个天然的可靠性问题：

    讲师 Agent 基于字幕写了一段讲解，
    但它可能**记错、夸大、或编造**老师没讲过的内容（幻觉）。

单 Agent 无法自我校验 —— 让它自己检查自己写的，等于让它自己给自己打分。

多 Agent 协作的价值就在这里：**把生成与校验拆给两个不同的 Agent**，
校验方拥有独立的检索能力（能翻字幕原文）和独立的判断立场，
形成「生成 → 校验 → 退回重写 → 再校验」的闭环。

这就是本模块实现的 LearningLoop：

    ┌──────────────────────────────────────────────┐
    │                                              │
    ▼                                              │
  lecture_agent ──► fact_check_agent ──► 路由判断   │
  （讲师：写讲解）    （审校：查原文核对）   ├─ 通过 → END
                                          └─ 不通过（带意见）─┘

为什么用 LangGraph 而不是手写 while 循环
------------------------------------
1. **状态共享**：多个节点读写同一份 State（讲解、审校意见、迭代次数）
2. **条件边**：`add_conditional_edges` 声明式表达「通过 / 退回」的分叉
3. **递归控制**：`recursion_limit` 天然防止无限循环
4. **可观测**：编译出的图能直接可视化，节点/边一目了然
5. **可扩展**：加节点不用改控制流

这三点（尤其第 2、3、5）正是手写 while 循环要小心处理、且容易写错的。

两个 Agent 的分工
----------------
**讲师 Agent**（lecture_agent）
    职责：根据字幕上下文 + 课程背景，写出一段面向学生的讲解。
    特点：生成导向，工具是「检索字幕」补充细节。

**审校 Agent**（fact_check_agent）
    职责：逐句核对讲师讲解是否与字幕原文一致。
    特点：校验导向，工具是「检索字幕」找依据。
    输出结构化结论：``{"passed": bool, "issues": [...], "reason": str}``

审校 Agent 是**独立实例**（不同 system prompt、不同工具集合），
这正是多 Agent 协作的要义 —— 不是同一个 Agent 换个提示词，
而是两个有不同职责、不同能力、不同立场角色的 Agent 互相配合。
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal, TypedDict

logger = logging.getLogger(__name__)


# =============================================================================
# 一、工作流状态
# =============================================================================


class ReviewState(TypedDict, total=False):
    """
    工作流的共享状态。

    每个节点读自己需要的字段，写自己产出的字段 —— 这就是 LangGraph 的
    「reducer」模型：状态是所有节点通信的唯一媒介。
    """

    # ---- 输入 ----
    word: str                      # 要讲解的词
    course_name: str
    domain: str
    context: str                   # 最近字幕
    transcript: str                # 整节课字幕（RAG 检索源）

    # ---- 节点间传递 ----
    draft: str                     # 讲师 Agent 产出的讲解草稿
    verdict: dict                  # 审校 Agent 的结构化结论
    issues: Annotated[list, _append]  # 累积的审校意见
    iteration: int                 # 当前迭代轮次


def _append(left: list, right: list) -> list:
    """reducer：累积列表而非覆盖"""
    return (left or []) + (right or [])


# =============================================================================
# 二、两个 Agent 的提示词
# =============================================================================

LECTURER_PROMPT = """You are an expert bilingual teaching assistant explaining a term to a Chinese student.

Course: {course_name}
Domain: {domain}

Relevant transcript excerpts:
---
{context}
---

Task: write a clear explanation of "{word}" for this student.

Requirements:
1. Start with a concise Chinese translation of "{word}".
2. Then explain what it means **in this specific course's context** — tie it to the
   lecture content above where relevant.
3. If the term has a well-known general meaning that differs from the course-specific
   one, mention the difference briefly.
4. If the transcript excerpts do not actually cover this term, say so explicitly and
   give the general academic meaning instead. Do NOT invent course-specific claims.
5. Use simplified Chinese. Keep technical terms in English where that is the standard.
6. Keep it under 200 words. Do not use markdown headers or bullet lists.

Output only the explanation text."""

FACT_CHECK_PROMPT = """You are a meticulous fact-checker verifying that a teaching explanation matches the lecture transcript.

Course: {course_name}

Transcript excerpts retrieved from the lecture:
---
{evidence}
---

A teaching assistant wrote this explanation for the term "{word}":
---
{draft}
---

Your job: check whether EVERY course-specific claim in the explanation is actually
supported by the transcript excerpts.

Rules:
1. Search the transcript for the relevant passages yourself before judging.
2. Flag any statement about what the lecturer said that is NOT supported by the transcript.
3. Flag any invented details, exaggerated claims, or misattributed concepts.
4. Do NOT object to standard textbook knowledge that is presented as general knowledge
   (not as something the lecturer said).
5. If the explanation is consistent with the transcript (or makes no course-specific
   claims at all), mark it as passed.

Respond ONLY with a JSON object:
{{
  "passed": true or false,
  "reason": "one sentence explaining the verdict",
  "issues": ["specific unsupported claim", "..."]
}}"""


# =============================================================================
# 三、节点实现
# =============================================================================


def _make_lecture_node(knowledge_base=None, model: str | None = None):
    """
    讲师节点：生成讲解草稿。

    做成工厂函数而非顶层函数，是为了在构图时才注入模型与知识库 ——
    保持「构图期不实例化真实模型」的原则。
    """

    def lecture_node(state: ReviewState) -> dict:
        from langchain.agents import create_agent

        from llm.chains.base import get_chat_model
        from llm.chains.rag import make_retrieve_tool

        tools = []
        if knowledge_base is not None:
            tools.append(make_retrieve_tool(lambda: knowledge_base))

        chat_model = get_chat_model(temperature=0.4, num_predict=1024, model=model)
        agent = create_agent(
            model=chat_model,
            tools=tools,
            system_prompt=LECTURER_PROMPT.format(
                course_name=state.get("course_name", "未知课程"),
                domain=state.get("domain", "通用"),
                context=(state.get("context") or "（暂无字幕）")[-4000:],
                word=state.get("word", ""),
            ),
        )

        from langchain_core.messages import HumanMessage

        result = agent.invoke(
            {"messages": [HumanMessage(content=f"请讲解「{state.get('word', '')}」。")]}
        )
        draft = _last_text(result)
        logger.info(f"讲师 Agent 生成草稿（{len(draft)} 字）")
        return {"draft": draft, "iteration": state.get("iteration", 0) + 1}

    return lecture_node


def _make_fact_check_node(knowledge_base=None, model: str | None = None):
    """
    审校节点：用 RAG 检索字幕原文，逐条核对讲师草稿。
    """

    def fact_check_node(state: ReviewState) -> dict:
        from langchain.agents import create_agent

        from llm.chains.base import get_chat_model
        from llm.chains.rag import make_retrieve_tool

        tools = []
        if knowledge_base is not None:
            tools.append(make_retrieve_tool(lambda: knowledge_base))

        chat_model = get_chat_model(temperature=0.1, num_predict=1024, model=model)
        agent = create_agent(
            model=chat_model,
            tools=tools,
            system_prompt=FACT_CHECK_PROMPT.format(
                course_name=state.get("course_name", "未知课程"),
                evidence=(state.get("context") or "（暂无字幕）")[-4000:],
                draft=state.get("draft", ""),
                word=state.get("word", ""),
            ),
        )

        from langchain_core.messages import HumanMessage

        result = agent.invoke(
            {
                "messages": [
                    HumanMessage(
                        content=(
                            f"请核对这段关于「{state.get('word', '')}」的讲解是否与字幕一致。"
                        )
                    )
                ]
            }
        )
        raw = _last_text(result)
        verdict = _parse_verdict(raw)

        issues = verdict.get("issues", [])
        logger.info(
            f"审校 Agent 结论：passed={verdict.get('passed')} "
            f"issues={len(issues)}（第 {state.get('iteration', 1)} 轮）"
        )
        return {"verdict": verdict, "issues": issues}

    return fact_check_node


def _make_route_node(max_iterations: int = 3):
    """
    路由节点：决定「通过 → 结束」还是「不通过 → 退回重写」。

    这是 LangGraph 条件边的典型用法 —— 路由函数只返回下一个节点名，
    不关心怎么走。
    """

    def route(state: ReviewState) -> Literal["lecture_node", "__end__"]:
        verdict = state.get("verdict") or {}
        passed = bool(verdict.get("passed"))
        iteration = state.get("iteration", 1)

        if passed:
            return "__end__"
        if iteration >= max_iterations:
            logger.info(f"已达最大迭代 {max_iterations} 轮，采用最后一版草稿")
            return "__end__"
        return "lecture_node"

    return route


def _last_text(result: Any) -> str:
    """
    从 Agent/链的返回值中取最后一条文本。

    注意：LangChain 1.x 的 Agent 返回的是 **dict**（形如 ``{"messages": [...]}``），
    而非 AIMessage 对象 —— 早期版本返回的是对象。
    两种形态都要兼容，否则会静默取到空串。
    """
    # 1) dict 形态
    if isinstance(result, dict):
        messages = result.get("messages") or []
    else:
        # 2) 对象形态
        messages = getattr(result, "messages", None) or []

    for msg in reversed(messages):
        if getattr(msg, "tool_calls", None):
            continue
        content = getattr(msg, "content", "")
        if isinstance(content, str) and content.strip():
            return content.strip()
        # 部分模型返回 content 为列表
        if isinstance(content, list):
            parts = [
                c.get("text", "")
                for c in content
                if isinstance(c, dict) and c.get("text")
            ]
            if parts:
                return "".join(parts).strip()

    # 3) 直接取 content 字段
    content = (
        result.get("content")
        if isinstance(result, dict)
        else getattr(result, "content", None)
    )
    if isinstance(content, str) and content.strip():
        return content.strip()
    return ""


def _parse_verdict(raw: str) -> dict:
    """解析审校 Agent 的 JSON 结论（容忍围栏与前后噪声）"""
    import json
    import re

    candidates = [raw or ""]
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw or "")
    if m:
        candidates.append(m.group(1))
    m = re.search(r"\{[\s\S]*\}", raw or "")
    if m:
        candidates.append(m.group(0))

    for c in candidates:
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                issues = obj.get("issues") or []
                if isinstance(issues, str):
                    issues = [issues]
                return {
                    "passed": bool(obj.get("passed")),
                    "reason": str(obj.get("reason", "")),
                    "issues": [str(i) for i in issues][:5],
                }
        except Exception:  # noqa: BLE001
            continue

    logger.warning(f"无法解析审校结论，按「通过」处理: {(raw or '')[:120]}")
    return {"passed": True, "reason": "审校输出无法解析", "issues": []}


# =============================================================================
# 四、图构建
# =============================================================================


def build_review_loop(
    knowledge_base=None,
    model: str | None = None,
    max_iterations: int = 3,
):
    """
    构建「生成-校验」多 Agent 协作图。

    Args:
        knowledge_base: SubtitleKnowledgeBase 实例；提供后两个 Agent 都能检索字幕
        model: 显式模型名
        max_iterations: 最大迭代轮次（防止无限重写）

    Returns:
        编译后的 LangGraph（可直接 invoke，也可 .get_graph() 可视化）
    """
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(ReviewState)

    builder.add_node("lecture_node", _make_lecture_node(knowledge_base, model))
    builder.add_node("fact_check_node", _make_fact_check_node(knowledge_base, model))

    builder.add_edge(START, "lecture_node")
    builder.add_edge("lecture_node", "fact_check_node")
    builder.add_conditional_edges(
        "fact_check_node",
        _make_route_node(max_iterations),
        {
            "lecture_node": "lecture_node",   # 不通过 → 退回重写
            "__end__": END,                    # 通过 → 结束
        },
    )

    return builder.compile()


class LearningLoop:
    """
    多 Agent 协作的对外封装。

    用法：

        loop = LearningLoop(knowledge_base=kb)
        result = loop.run(word="gradient", context=..., transcript=...)
        result["draft"]        # 最终讲解
        result["verdict"]      # 审校结论
        result["iteration"]    # 实际迭代轮次
    """

    def __init__(
        self,
        knowledge_base=None,
        model: str | None = None,
        max_iterations: int = 3,
    ):
        self.knowledge_base = knowledge_base
        self.model = model
        self.max_iterations = max_iterations
        self._graph = None

    def _get_graph(self):
        if self._graph is None:
            self._graph = build_review_loop(
                knowledge_base=self.knowledge_base,
                model=self.model,
                max_iterations=self.max_iterations,
            )
        return self._graph

    def run(
        self,
        word: str,
        context: str = "",
        course_name: str = "",
        domain: str = "",
        transcript: str = "",
    ) -> dict:
        """
        跑完整闭环，返回最终讲解与审校过程。

        Args:
            word: 要讲解的词
            context: 最近字幕（作为初始证据）
            course_name: 课程名
            domain: 课程领域
            transcript: 整节课字幕（可选，供 RAG 检索）

        Returns:
            {draft, verdict, issues, iteration}
        """
        if not word or not word.strip():
            return {"draft": None, "verdict": None, "issues": [], "iteration": 0}

        if self.knowledge_base is not None and transcript:
            try:
                self.knowledge_base.build(
                    transcript, course_name=course_name, domain=domain
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"工作流内建索引失败: {e}")

        initial: ReviewState = {
            "word": word.strip(),
            "course_name": course_name or "未知课程",
            "domain": domain or "通用",
            "context": context or "",
            "transcript": transcript or "",
            "draft": "",
            "verdict": {},
            "issues": [],
            "iteration": 0,
        }

        graph = self._get_graph()
        # recursion_limit 防止图内循环失控（与 max_iterations 双保险）
        result = graph.invoke(initial, {"recursion_limit": max(10, self.max_iterations * 4)})

        return {
            "draft": result.get("draft", ""),
            "verdict": result.get("verdict", {}),
            "issues": result.get("issues", []),
            "iteration": result.get("iteration", 0),
        }


__all__ = [
    "ReviewState",
    "LearningLoop",
    "build_review_loop",
    "LECTURER_PROMPT",
    "FACT_CHECK_PROMPT",
]
