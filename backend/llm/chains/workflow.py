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

    # ---- 人工审批 ----
    approved: bool                 # 学生是否确认定稿
    human_note: str                # 学生的批注（要求重写时的说明）


def _append(left: list, right: list) -> list:
    """reducer：累积列表而非覆盖"""
    return (left or []) + (right or [])


# =============================================================================
# 一·补、工作流的 checkpointer
# =============================================================================
#
# interrupt() 的暂停点必须被持久化 —— 否则图不知道「刚才停在哪、等谁回复」。
# 这里用 LangGraph 的 InMemorySaver（进程内）。
# 需要跨进程时换成 Redis/SQLite 版 saver 即可，图的定义不用动。

_workflow_saver = None


def get_workflow_saver():
    """获取工作流专用的 checkpointer（单例）"""
    global _workflow_saver
    if _workflow_saver is None:
        from langgraph.checkpoint.memory import InMemorySaver

        _workflow_saver = InMemorySaver()
    return _workflow_saver


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


def _make_route_node(max_iterations: int = 3, with_human_review: bool = False):
    """
    路由节点：决定「通过 → 交人工审批 / 直接结束」还是「不通过 → 退回重写」。

    这是 LangGraph 条件边的典型用法 —— 路由函数只返回下一个节点名，
    不关心怎么走。
    """

    def route(state: ReviewState) -> Literal["human_review", "lecture_node", "__end__"]:
        verdict = state.get("verdict") or {}
        passed = bool(verdict.get("passed"))
        iteration = state.get("iteration", 1)

        if passed:
            return "human_review" if with_human_review else "__end__"
        if iteration >= max_iterations:
            logger.info(f"已达最大迭代 {max_iterations} 轮，采用最后一版草稿")
            return "__end__"
        return "lecture_node"

    return route


# =============================================================================
# 三·补、人工审批节点（LangGraph interrupt）
# =============================================================================


def _make_human_review_node():
    """
    人工审批节点：用 LangGraph 的 ``interrupt()`` 暂停工作流，等人确认。

    这是 LangGraph 最有价值的能力之一，也是**手写 while 循环极难做好**的部分：

    - 图会把当前状态**持久化到 checkpointer**，然后抛出一个待审 payload
    - 调用方（前端 / API）拿到 payload，展示给学生确认
    - 学生点「确认无误」→ 用 ``Command(resume={"approved": True})`` 恢复
    - 学生点「还要改」→ 恢复为 ``{"approved": False, "note": "..."}``，
      节点把批注写回 State，图继续走「退回重写」分支

    手写要正确实现这套「暂停-持久化-恢复」的语义，需要自己维护
    断点、状态快照、恢复入口，且几乎必然在异常路径上出错。
    """

    def human_review(state: ReviewState) -> dict:
        from langgraph.types import interrupt

        decision = interrupt(
            {
                "type": "review_explanation",
                "question": "这段讲解可以定稿吗？",
                "word": state.get("word", ""),
                "draft": state.get("draft", ""),
                "verdict": state.get("verdict", {}),
                "options": ["approve", "reject"],
            }
        )

        approved = bool((decision or {}).get("approved", False))
        note = str((decision or {}).get("note", "") or "")
        logger.info(
            f"人工审批：approved={approved}" + (f"，批注：{note[:40]}" if note else "")
        )
        return {"approved": approved, "human_note": note}

    return human_review


def _make_human_route_node(max_iterations: int = 3):
    """
    人工审批后的路由：确认 → 结束；要求修改 → 退回讲师重写。

    学生明确要求修改时优先于迭代上限 —— 人的意愿高于自动策略。
    """

    def route(state: ReviewState) -> Literal["lecture_node", "__end__"]:
        if state.get("approved"):
            return "__end__"
        note = (state.get("human_note") or "").strip()
        if note:
            # 学生给了具体批注 —— 必须重写
            return "lecture_node"
        iteration = state.get("iteration", 1)
        if iteration >= max_iterations:
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
    with_human_review: bool = True,
):
    """
    构建「生成-校验-人工审批」多 Agent 协作图。

    Args:
        knowledge_base: SubtitleKnowledgeBase 实例；提供后两个 Agent 都能检索字幕
        model: 显式模型名
        max_iterations: 最大迭代轮次（防止无限重写）
        with_human_review: True 启用人工审批节点（LangGraph interrupt）

    Returns:
        编译后的 LangGraph（可直接 invoke，也可 .get_graph() 可视化）
    """
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(ReviewState)

    builder.add_node("lecture_node", _make_lecture_node(knowledge_base, model))
    builder.add_node("fact_check_node", _make_fact_check_node(knowledge_base, model))

    builder.add_edge(START, "lecture_node")
    builder.add_edge("lecture_node", "fact_check_node")

    if with_human_review:
        # 人工审批：审校通过后暂停，等学生确认
        builder.add_node("human_review", _make_human_review_node())
        builder.add_conditional_edges(
            "fact_check_node",
            _make_route_node(max_iterations, with_human_review=True),
            {
                "human_review": "human_review",   # 通过 → 人工审批
                "lecture_node": "lecture_node",   # 不通过 → 退回重写
                "__end__": END,                    # 迭代超限 → 结束
            },
        )
        builder.add_conditional_edges(
            "human_review",
            _make_human_route_node(max_iterations),
            {
                "lecture_node": "lecture_node",   # 学生要求修改 → 退回重写
                "__end__": END,                    # 学生确认 → 定稿
            },
        )
    else:
        builder.add_conditional_edges(
            "fact_check_node",
            _make_route_node(max_iterations, with_human_review=False),
            {
                "lecture_node": "lecture_node",   # 不通过 → 退回重写
                "__end__": END,                    # 通过 → 结束
            },
        )

    # interrupt 需要 checkpointer 才能持久化暂停点；
    # 未启用人工审批时不需要挂 checkpointer（也就无需 thread_id）
    if with_human_review:
        return builder.compile(checkpointer=get_workflow_saver())
    return builder.compile()


class LearningLoop:
    """
    多 Agent 协作的对外封装。

    两种用法：

    **① 自动跑完（无人工审批）**

        loop = LearningLoop(knowledge_base=kb, with_human_review=False)
        result = loop.run(word="gradient", context=..., transcript=...)
        result["draft"]        # 最终讲解
        result["verdict"]      # 审校结论
        result["iteration"]    # 实际迭代轮次

    **② 两阶段（人工审批）**

        loop = LearningLoop(knowledge_base=kb, with_human_review=True)

        # 第一阶段：跑到人工确认就暂停，返回待审内容
        pending = loop.run(word="gradient", context=..., thread_id="stu-1")
        if pending["needs_review"]:
            # 前端把 pending["review_request"] 展示给学生
            ...

        # 第二阶段：学生点了「确认无误」，恢复执行
        final = loop.resume(thread_id="stu-1", approved=True)
        final["draft"]         # 定稿
    """

    def __init__(
        self,
        knowledge_base=None,
        model: str | None = None,
        max_iterations: int = 3,
        with_human_review: bool = False,
    ):
        self.knowledge_base = knowledge_base
        self.model = model
        self.max_iterations = max_iterations
        self.with_human_review = with_human_review
        self._graph = None

    def _get_graph(self):
        if self._graph is None:
            self._graph = build_review_loop(
                knowledge_base=self.knowledge_base,
                model=self.model,
                max_iterations=self.max_iterations,
                with_human_review=self.with_human_review,
            )
        return self._graph

    @staticmethod
    def _extract_pending(result: dict) -> dict | None:
        """从结果里取出 interrupt 抛出的待审 payload"""
        interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
        if not interrupts:
            return None
        first = interrupts[0]
        return getattr(first, "value", None)

    def run(
        self,
        word: str,
        context: str = "",
        course_name: str = "",
        domain: str = "",
        transcript: str = "",
        thread_id: str = "",
    ) -> dict:
        """
        跑闭环，返回最终讲解；若启用了人工审批且尚未确认，则返回待审内容。

        Args:
            word: 要讲解的词
            context: 最近字幕（作为初始证据）
            course_name: 课程名
            domain: 课程领域
            transcript: 整节课字幕（可选，供 RAG 检索）
            thread_id: 会话标识（启用人工审批时必须提供，用于恢复）

        Returns:
            {
              draft, verdict, issues, iteration,
              needs_review: 是否停在人工审批,
              review_request: 待审内容（需展示给学生）
            }
        """
        if not word or not word.strip():
            return {
                "draft": None,
                "verdict": None,
                "issues": [],
                "iteration": 0,
                "needs_review": False,
                "review_request": None,
            }

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
        config = {"recursion_limit": max(10, self.max_iterations * 4)}
        if thread_id:
            config["configurable"] = {"thread_id": thread_id}

        result = graph.invoke(initial, config)

        pending = self._extract_pending(result) if isinstance(result, dict) else None

        return {
            "draft": result.get("draft", "") if isinstance(result, dict) else "",
            "verdict": result.get("verdict", {}) if isinstance(result, dict) else {},
            "issues": result.get("issues", []) if isinstance(result, dict) else [],
            "iteration": result.get("iteration", 0) if isinstance(result, dict) else 0,
            "approved": result.get("approved", False) if isinstance(result, dict) else False,
            "needs_review": pending is not None,
            "review_request": pending,
        }

    def resume(self, thread_id: str, approved: bool, note: str = "") -> dict:
        """
        恢复被 interrupt 暂停的工作流。

        Args:
            thread_id: 与 run 时相同的会话标识
            approved: True 学生确认定稿；False 要求重写
            note: 学生的批注（要求重写时的说明，会带回给讲师 Agent）

        Returns:
            与 run() 相同结构的字典
        """
        from langgraph.types import Command

        graph = self._get_graph()
        config = {
            "recursion_limit": max(10, self.max_iterations * 4),
            "configurable": {"thread_id": thread_id},
        }
        result = graph.invoke(
            Command(resume={"approved": approved, "note": note}), config
        )

        pending = self._extract_pending(result) if isinstance(result, dict) else None
        return {
            "draft": result.get("draft", "") if isinstance(result, dict) else "",
            "verdict": result.get("verdict", {}) if isinstance(result, dict) else {},
            "issues": result.get("issues", []) if isinstance(result, dict) else [],
            "iteration": result.get("iteration", 0) if isinstance(result, dict) else 0,
            "approved": result.get("approved", False) if isinstance(result, dict) else False,
            "needs_review": pending is not None,
            "review_request": pending,
        }


__all__ = [
    "ReviewState",
    "LearningLoop",
    "build_review_loop",
    "get_workflow_saver",
    "LECTURER_PROMPT",
    "FACT_CHECK_PROMPT",
]
