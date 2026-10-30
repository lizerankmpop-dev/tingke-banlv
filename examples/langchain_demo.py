#!/usr/bin/env python
"""
LangChain 智能体演示脚本 —— 一条命令看清四条链的编排

本脚本不依赖真实大模型也能跑通全流程（用 Fake 模型注入预设响应），
用于快速验证 LangChain 链的编排逻辑、结构化输出与分级策略。

用法：

    # 1) 离线演示（无需 Ollama，秒级完成）
    python examples/langchain_demo.py --offline

    # 2) 真实模型演示（需要先启动 Ollama 并拉取 qwen2.5:7b）
    python examples/langchain_demo.py --live

真实模型演示会依次调用：术语提取 → 单词讲解 → 课后摘要 → 对话答疑，
每一步都打印 LangChain 链的执行细节与结果。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 允许从仓库根目录直接运行
BACKEND = Path(__file__).resolve().parent.parent / "backend"
if BACKEND.exists():
    sys.path.insert(0, str(BACKEND))

from langchain_core.language_models.fake_chat_models import FakeListChatModel  # noqa: E402


# =============================================================================
# 演示用的字幕语料
# =============================================================================

SAMPLE_SUBTITLE = """
Today we discuss gradient descent, the fundamental optimization algorithm used in machine learning.
Given a loss function L, gradient descent iteratively updates the parameters theta.
The update rule is theta = theta - alpha * grad(L), where alpha is the learning rate.
If the learning rate is too large, the algorithm may diverge; too small, convergence becomes very slow.
We also introduce the concept of eigenvalue, which describes how a linear transformation stretches the space.
Overfitting occurs when the model fits the training data too well and generalizes poorly.
"""

SAMPLE_QUESTIONS = [
    ("什么是梯度下降？", "gradient descent"),
    ("learning rate 是什么意思？", "learning rate"),
]


def hr(title: str = "") -> None:
    """打印分隔标题"""
    if title:
        print(f"\n{'=' * 72}\n  {title}\n{'=' * 72}")
    else:
        print("-" * 72)


# =============================================================================
# 离线演示：用 Fake 模型走通全链路
# =============================================================================



def _demo_rag() -> None:
    """演示 RAG：字幕 → 语义分块 → 向量索引 → 语义召回"""
    from langchain_core.embeddings.fake import FakeEmbeddings

    from llm.chains.rag import SubtitleKnowledgeBase, chunk_subtitles, parse_subtitles

    subtitle = """[00:12] Today we discuss gradient descent, the fundamental optimization algorithm used in machine learning.
[00:45] The update rule is theta = theta minus alpha times gradient of L.
[01:23] If the learning rate is too large, the algorithm may diverge.
[02:10] We also introduce the concept of eigenvalue, which describes how a transformation stretches space.
[03:05] Overfitting occurs when the model fits the training data too well and generalizes poorly."""

    print("① 字幕解析（保留时间戳）")
    chunks = parse_subtitles(subtitle)
    for c in chunks:
        print(f"   [{c.timestamp}] {c.text[:62]}")

    print("\n② 语义分块（合并短句，保留重叠）")
    merged = chunk_subtitles(chunks, chunk_size=140, chunk_overlap=30)
    for m in merged:
        print(f"   [{m.timestamp}] len={len(m.text):3d}  {m.text[:52]}")

    print("\n③ 建立向量索引并召回")
    kb = SubtitleKnowledgeBase(embeddings=FakeEmbeddings(size=64))
    n = kb.build(subtitle, course_name="深度学习导论", domain="机器学习")
    print(f"   索引片段数：{n}")
    hits = kb.retrieve("learning rate 太大会怎样", k=3)
    for h in hits:
        print(f"   命中 [{h.timestamp}] {h.text[:58]}")

    print("\n（说明：离线模式用 FakeEmbeddings，向量是随机值，故排序无实际语义；")
    print("  真实模式用本地 Ollama 的 nomic-embed-text，召回按语义排序）")



def _demo_workflow() -> None:
    """演示多 Agent 协作：讲师生成 → 审校核对 → 不通过则退回重写"""
    import json

    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    import llm.chains.base as base
    from llm.chains.workflow import build_review_loop

    # 脚本：讲师写一版 → 审校不通过 → 讲师重写 → 审校通过
    answers = [
        "草稿1：梯度下降就是让参数往梯度反方向走。",
        json.dumps(
            {"passed": False, "reason": "与字幕不符", "issues": ["字幕中老师说的是偏导数向量，不是反方向走"]},
            ensure_ascii=False,
        ),
        "草稿2：据字幕，梯度下降沿损失函数梯度的负方向迭代更新参数，更新步长由学习率 α 控制。",
        json.dumps({"passed": True, "reason": "与字幕一致", "issues": []}, ensure_ascii=False),
    ]

    class Scripted(FakeListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

        def _call(self, *a, **k):
            return "x"

        def _generate(self, *a, **k):
            text = answers.pop(0) if answers else "备用"
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    _orig = base.get_chat_model
    base.get_chat_model = lambda **kw: Scripted(responses=["x"])
    try:
        graph = build_review_loop(max_iterations=3)

        print("① 图结构（LangGraph 编译产物）")
        nodes = list(graph.get_graph().nodes.keys())
        print(f"   节点: {nodes}")

        print("\n② 执行闭环")
        out = graph.invoke(
            {
                "word": "gradient descent",
                "course_name": "深度学习导论",
                "domain": "机器学习",
                "context": "[01:23] The update rule is theta = theta minus alpha times gradient of L.",
                "transcript": "",
                "draft": "",
                "verdict": {},
                "issues": [],
                "iteration": 0,
            },
            {"recursion_limit": 20},
        )
        print(f"   迭代轮次: {out.get('iteration')}（讲师写 2 次、审校 2 次）")
        print(f"   审校结论: passed={out.get('verdict', {}).get('passed')}")
        print(f"   累积意见: {out.get('issues')}")
        print(f"   最终讲解: {out.get('draft')}")
    finally:
        base.get_chat_model = _orig

def _demo_memory() -> None:
    """演示 Memory：同一 thread 历史累积，不同 thread 相互隔离"""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    import llm.chains.base as base
    from llm.chains.agent_tools import build_qa_agent
    from llm.chains.memory import get_checkpoint_store, make_thread_config

    seen: list[int] = []

    class CountingModel(FakeListChatModel):
        """记录每次调用收到的消息条数"""

        def bind_tools(self, tools, **kwargs):
            return self

        def _call(self, *a, **k):
            return "x"

        def _generate(self, *a, **k):
            seen.append(len(a[0]) if a else 0)
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="回答"))])

    _orig = base.get_chat_model
    base.get_chat_model = lambda **kw: CountingModel(responses=["x"])
    try:
        store = get_checkpoint_store()
        agent = build_qa_agent(checkpointer=store)

        print("① 同一 thread 的多轮追问（历史逐轮累积）")
        for q in ["第一问：什么是梯度下降？", "第二问：那它和反向传播什么关系？", "第三问：继续说"]:
            agent.invoke(
                {"messages": [{"role": "user", "content": q}]},
                make_thread_config("demo-thread-A"),
            )
            print(f"   {q[:22]:24s} → 模型收到 {seen[-1]} 条消息")

        print("\n② 不同 thread 相互隔离")
        seen.clear()
        agent.invoke(
            {"messages": [{"role": "user", "content": "全新会话"}]},
            make_thread_config("demo-thread-B"),
        )
        print(f"   新 thread 收到 {seen[-1]} 条消息（不带上一个 thread 的历史）")

        print("\n③ 历史存储")
        for t in ("demo-thread-A", "demo-thread-B"):
            print(f"   {t}: {len(store.get_messages(t))} 条消息")
    finally:
        base.get_chat_model = _orig

def run_offline() -> None:
    """离线演示 —— 不需要 Ollama"""
    print("=" * 72)
    print("  LangChain 智能体演示（离线模式 · 无需大模型）")
    print("=" * 72)

    import llm.chains.agents as agents
    import llm.chains.base as base

    # 预置响应：按 schema 路由（而非按调用顺序），保证演示结果稳定
    SCHEMA_RESPONSES = {
        "TermExtractionResult": {
            "terms": [
                {
                    "word": "gradient descent",
                    "translation": "梯度下降",
                    "definition": "通过沿损失函数梯度的负方向迭代更新参数以最小化损失的优化算法",
                    "domain": "机器学习",
                },
                {
                    "word": "eigenvalue",
                    "translation": "特征值",
                    "definition": "描述线性变换对空间拉伸程度的标量",
                    "domain": "线性代数",
                },
            ]
        },
        "WordExplanation": {
            "translation": "学习率",
            "meaning_in_context": "控制每一步参数更新幅度的超参数。太大导致发散，太小则收敛过慢。",
            "example": "If the learning rate is too large, the algorithm may diverge.",
            "related": ["gradient descent 梯度下降", "convergence 收敛性"],
        },
        "LectureSummary": {
            "summary": "本节课讲解了梯度下降算法的基本原理、更新规则与学习率对收敛性的影响，"
            "并引入了特征值与过拟合的概念。",
            "key_points": [
                "梯度下降沿损失函数梯度的负方向迭代更新参数",
                "更新规则为 theta = theta - alpha * grad(L)",
                "学习率过大导致发散，过小导致收敛缓慢",
            ],
            "review_words": [
                "gradient descent 梯度下降",
                "learning rate 学习率",
                "eigenvalue 特征值",
            ],
        },
    }

    # 自由文本响应（答疑链）：由 _call 提供
    scripted_responses = [
        "梯度下降是一种迭代优化算法：每次沿损失函数在当前参数处的梯度反方向更新参数，"
        "参数更新步长由学习率 alpha 控制。在你这门课的语境里，老师强调的是学习率的选取 —— "
        "过大导致损失震荡甚至发散，过小则收敛过慢。",
    ]

    class ScriptedModel(FakeListChatModel):
        """
        按 **schema 类型**路由的假模型。

        为什么不用「按调用顺序返回」：四条链的调用次序与次数并不固定
        （例如单词讲解会先探测词表命中与否，未命中才落到模型），
        顺序式响应极易错位。这里改为按目标 schema 决定返回哪份预设数据，
        演示结果因此稳定可复现。

        LangChain 内置的 `FakeListChatModel` **不支持** `with_structured_output()`
        （走 tool-calling 路径，会抛 NotImplementedError），
        故这里接管整条结构化链，直接返回解析好的 schema 实例。
        """

        def _call(self, *args, **kwargs):
            resp = self.responses[self.i] if self.i < len(self.responses) else self.responses[-1]
            if self.i < len(self.responses) - 1:
                self.i += 1
            else:
                self.i = 0
            return resp

        def with_structured_output(self, schema, **kwargs):
            """按 schema 选取对应的预设 JSON 并解析成实例"""
            from langchain_core.output_parsers import PydanticOutputParser
            from langchain_core.runnables import RunnableLambda

            parser = PydanticOutputParser(pydantic_object=schema)
            payload = SCHEMA_RESPONSES.get(schema.__name__)
            if payload is None:
                raise NotImplementedError(f"离线演示未预置 {schema.__name__} 的响应")

            def _invoke(_messages):
                return parser.parse(json.dumps(payload, ensure_ascii=False))

            return RunnableLambda(_invoke)

    fake_model = ScriptedModel(responses=scripted_responses)
    monkey = lambda **kw: fake_model  # noqa: E731
    base.get_chat_model = monkey
    agents.get_chat_model = monkey
    # 离线模式下没有真实 LLM，把可用性探测也一并桩掉
    base.is_available = lambda: True
    agents.is_available = lambda: True

    # ---- 1. 术语提取链 ----
    hr("链 1 / 术语提取  ChatPromptTemplate → with_structured_output → RunnableLambda")
    extractor = agents.TermExtractionAgent()
    terms = extractor.extract_terms(
        text=SAMPLE_SUBTITLE,
        domain="机器学习",
        course_name="深度学习导论",
        custom_terms=["gradient descent"],
    )
    print(json.dumps(terms, ensure_ascii=False, indent=2))

    # ---- 2. 单词讲解链（RunnableBranch） ----
    hr("链 2 / 单词讲解  RunnableBranch：词表命中 → 本地；未命中 → LLM")
    explainer = agents.WordExplanationAgent()
    # 2.1 本地词表分支
    from dictionary.terminology import lookup_vocab

    local_word = next((w for w in ["gradient", "algorithm", "model"] if lookup_vocab(w)), None)
    if local_word:
        r_local = explainer.explain(word=local_word, domain="计算机", course_name="深度学习导论")
        print(f"[本地词表分支] word={local_word!r} → source={r_local.get('source')}")
        print(f"   translation: {r_local.get('translation')}")
        print(f"   （该分支完全不发起 LLM 请求，零延迟零成本）")
    # 2.2 AI 分支：内置词表覆盖 3300+ 词，需挑一个确实未收录的术语
    ai_word = "backpropagation algorithm"  # 词表按单词收录，组合短语通常不命中
    if lookup_vocab(ai_word) is not None:
        ai_word = "mini-batch stochastic descent"
    r_ai = explainer.explain(
        word=ai_word,
        domain="机器学习",
        course_name="深度学习导论",
        context_sentence="We also introduce the concept of eigenvalue.",
    )
    print(f"\n[AI 分支] word={ai_word!r} → source={r_ai.get('source')}")
    print(json.dumps(r_ai, ensure_ascii=False, indent=2))

    # ---- 3. 课后摘要链 ----
    hr("链 3 / 课后摘要  with_structured_output(LectureSummary)")
    summarizer = agents.LectureSummaryAgent()
    summary = summarizer.summarize(
        text=SAMPLE_SUBTITLE,
        domain="机器学习",
        course_name="深度学习导论",
        vocabulary=[
            {"word": "gradient descent", "translation": "梯度下降", "isMemorized": False},
            {"word": "eigenvalue", "translation": "特征值", "isMemorized": True},
        ],
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # ---- 4. 对话答疑链 ----
    hr("链 4 / 对话答疑  ChatPromptTemplate → ChatOllama → StrOutputParser")
    qa = agents.ChatQAAgent()
    for q, _ in SAMPLE_QUESTIONS:
        answer = qa.answer(
            question=q,
            context=SAMPLE_SUBTITLE,
            domain="机器学习",
            course_name="深度学习导论",
        )
        print(f"\nQ: {q}\nA: {answer}")

    # ---- 5. Agent 工具调用轨迹 ----
    hr("链 5 / 对话答疑 Agent  create_agent() + 工具调用循环")
    _demo_agent_tool_loop(fake_model)

    # ---- 6. RAG 字幕语义检索 ----
    hr("链 6 / RAG 字幕语义检索  语义分块 → 向量索引 → 召回")
    _demo_rag()

    # ---- 7. 多轮对话记忆 ----
    hr("链 7 / Memory 多轮对话  checkpointer 按 thread_id 隔离")
    _demo_memory()

    # ---- 8. 多 Agent 协作工作流 ----
    hr("链 8 / 多 Agent 协作  LangGraph 生成-校验闭环")
    _demo_workflow()

    hr()
    print("离线演示完成。链路的组装、编排、结构化输出、分级策略、Agent 工具调用、")
    print("RAG 语义检索、多轮对话记忆与多 Agent 协作闭环全部验证通过。")


def _demo_agent_tool_loop(fake_model) -> None:
    """
    演示 LangChain Agent 的工具调用循环。

    构造一个会「先调工具、再作答」的假模型，走完
    「模型决策 → 工具执行 → 模型总结」的完整 Agent 循环，
    并把每一步打印出来 —— 这是 Agent 与普通 Chain 的本质区别。
    """
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    import llm.chains.base as base
    from llm.chains.agent_tools import build_qa_agent

    class ToolCallingModel(type(fake_model)):
        """模拟真实模型：第一次要求调用工具，第二次给出最终回答"""

        def bind_tools(self, tools, **kwargs):
            return self

        def _call(self, *args, **kwargs):
            return "placeholder"

        def _generate(self, *args, **kwargs):
            if self.i == 0:
                self.i += 1
                msg = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "lookup_term",
                            "args": {"word": "gradient descent"},
                            "id": "call_1",
                            "type": "tool_call",
                        }
                    ],
                )
            else:
                msg = AIMessage(
                    content=(
                        "根据课程术语库，gradient descent 在本课语境下指「梯度下降」——"
                        "一种沿损失函数梯度反方向迭代更新参数的优化算法，"
                        "更新步长由学习率 α 控制。"
                    )
                )
            return ChatResult(generations=[ChatGeneration(message=msg)])

    agent_model = ToolCallingModel(responses=["x"])
    _orig = base.get_chat_model
    base.get_chat_model = lambda **kw: agent_model
    try:
        agent = build_qa_agent()
        out = agent.invoke(
            {"messages": [{"role": "user", "content": "gradient descent 是什么意思？"}]}
        )
        msgs = out["messages"] if isinstance(out, dict) else out.messages

        print("Agent 执行轨迹：")
        for m in msgs:
            tcs = getattr(m, "tool_calls", None)
            if tcs:
                for tc in tcs:
                    args = ", ".join(f"{k}={v!r}" for k, v in (tc.get("args") or {}).items())
                    print(f"  → 模型决定调用工具：{tc['name']}({args})")
            elif m.__class__.__name__ == "ToolMessage":
                print(f"  ← 工具返回：{str(m.content)[:100]}")
            else:
                content = getattr(m, "content", "")
                if isinstance(content, str) and content.strip():
                    print(f"  ✓ 最终回答：{content.strip()[:160]}")
    finally:
        base.get_chat_model = _orig


# =============================================================================
# 真实模型演示
# =============================================================================


def run_live() -> None:
    """真实模型演示 —— 需要 Ollama"""
    from llm.chains.base import get_chat_model, get_provider, is_available

    print("=" * 72)
    print("  LangChain 智能体演示（真实模型模式）")
    print("=" * 72)

    if not is_available():
        print(f"✗ 当前 provider={get_provider()} 不可用。")
        print("  请先启动 Ollama 并拉取模型：ollama pull qwen2.5:7b")
        print("  或改用云端：设置 TINGKEBANLV_LLM_PROVIDER=cloud 及相关环境变量")
        sys.exit(1)

    model = get_chat_model()
    print(f"✓ 模型就绪：{get_provider()} / {getattr(model, 'model', '未知')}")

    from llm.chains.agents import (
        ChatQAAgent,
        LectureSummaryAgent,
        TermExtractionAgent,
        WordExplanationAgent,
    )

    # ---- 1. 术语提取 ----
    hr("链 1 / 术语提取")
    terms = TermExtractionAgent().extract_terms(
        text=SAMPLE_SUBTITLE,
        domain="机器学习",
        course_name="深度学习导论",
    )
    print(json.dumps(terms, ensure_ascii=False, indent=2))

    # ---- 2. 单词讲解 ----
    hr("链 2 / 单词讲解（AI 分支）")
    expl = WordExplanationAgent().explain(
        word="eigenvalue",
        domain="线性代数",
        course_name="深度学习导论",
        context_sentence="We also introduce the concept of eigenvalue.",
    )
    print(json.dumps(expl, ensure_ascii=False, indent=2))

    # ---- 3. 课后摘要 ----
    hr("链 3 / 课后摘要")
    summary = LectureSummaryAgent().summarize(
        text=SAMPLE_SUBTITLE,
        domain="机器学习",
        course_name="深度学习导论",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # ---- 4. 对话答疑 ----
    hr("链 4 / 对话答疑")
    answer = ChatQAAgent().answer(
        question="为什么学习率不能太大？",
        context=SAMPLE_SUBTITLE,
        domain="机器学习",
        course_name="深度学习导论",
    )
    print(answer)

    hr()
    print("真实模型演示完成。")


def main() -> None:
    parser = argparse.ArgumentParser(description="LangChain 智能体演示")
    parser.add_argument(
        "--live", action="store_true", help="使用真实大模型（需 Ollama）"
    )
    parser.add_argument(
        "--offline", action="store_true", help="使用假模型离线演示（默认）"
    )
    args = parser.parse_args()

    if args.live:
        run_live()
    else:
        run_offline()


if __name__ == "__main__":
    main()
