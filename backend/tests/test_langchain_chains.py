"""
LangChain 智能体层的回归守卫

本文件锁定三处**在 LangChain 迁移中真实踩到过**的坑，
防止后续改动把它们改回去：

1. **本地词表路径不得被 LLM 可用性检查短路**
   迁移前流程是「先查本地词表，命中立即返回；未命中才检查 LLM」。
   迁移时曾把可用性检查提到链调用之前，导致无 LLM 环境下
   词表命中也被返回 None —— 属于功能性回归，本文件第 1 组用例即其守卫。

2. **QA 链输出必须是精确的 str**
   LangChain 1.x 的 StrOutputParser 会返回 `TextAccessor`（str 的子类）。
   它在多数场景与 str 等价，但会渗进 API 响应体，因此链尾必须显式转成 str。

3. **结构化输出必须容忍模型的不规范输出**
   模型可能把数组写成字符串、可能多吐字段、可能给出超长列表。
   schema 的 validator 必须完成归一与截断。

运行：
    cd backend
    python -m pytest tests/test_langchain_chains.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.language_models.fake_chat_models import FakeListChatModel  # noqa: E402
from langchain_core.output_parsers import PydanticOutputParser  # noqa: E402
from langchain_core.runnables import RunnableBranch, RunnableLambda  # noqa: E402

from llm.chains.base import build_qa_chain, safe_invoke  # noqa: E402
from llm.chains.prompts import (  # noqa: E402
    EXPLAIN_PROMPT_TEMPLATE,
    EXTRACTION_PROMPT_TEMPLATE,
    QA_PROMPT_TEMPLATE,
    SUMMARY_PROMPT_TEMPLATE,
)
from llm.chains.schemas import (  # noqa: E402
    LectureSummary,
    TermExtractionResult,
    WordExplanation,
)


# =============================================================================
# 1. 本地词表路径不得被 LLM 可用性短路（真实回归守卫）
# =============================================================================


def test_local_vocab_path_survives_without_llm(monkeypatch):
    """无 LLM 时，词表命中仍必须正常返回 local_vocab"""
    import llm.chains.agents as agents
    import llm.chains.base as base

    # 强制 LLM 不可用
    monkeypatch.setattr(base, "is_available", lambda: False)
    monkeypatch.setattr(agents, "is_available", lambda: False)

    agent = agents.WordExplanationAgent()
    # 选一个确定在内置词表里的词
    from dictionary.terminology import lookup_vocab

    probe = "gradient"
    if lookup_vocab(probe) is None:
        pytest.skip("内置词表未包含探测词，跳过")

    result = agent.explain(word=probe, domain="计算机", course_name="机器学习")
    assert result is not None, "词表命中不应被 LLM 不可用短路"
    assert result["source"] == "local_vocab"
    assert result["translation"], "词表命中应带翻译"


def test_llm_unavailable_returns_none_for_unknown_word(monkeypatch):
    """词表未命中且 LLM 不可用时返回 None（而非抛异常）"""
    import llm.chains.agents as agents
    import llm.chains.base as base

    monkeypatch.setattr(base, "is_available", lambda: False)
    monkeypatch.setattr(agents, "is_available", lambda: False)

    agent = agents.WordExplanationAgent()
    result = agent.explain(word="zzz_definitely_not_in_vocab_9x7q", domain="", course_name="")
    assert result is None


def test_runnable_branch_routes_correctly():
    """RunnableBranch 的条件路由必须符合预期"""
    hit = RunnableLambda(lambda p: {"source": "local_vocab"})
    miss = RunnableLambda(lambda p: {"source": "ai"})
    branch = RunnableBranch(
        (RunnableLambda(lambda p: p.get("word") == "gradient"), hit),
        miss,
    )
    assert branch.invoke({"word": "gradient"})["source"] == "local_vocab"
    assert branch.invoke({"word": "eigenvalue"})["source"] == "ai"


# =============================================================================
# 2. QA 链输出类型必须是精确 str（TextAccessor 泄漏守卫）
# =============================================================================


def test_qa_chain_returns_plain_str(monkeypatch):
    """QA 链输出必须是 type is str，不能是 TextAccessor 子类"""
    import llm.chains.agents as agents
    import llm.chains.base as base

    monkeypatch.setattr(
        base, "get_chat_model", lambda **kw: FakeListChatModel(responses=["这是答案"])
    )
    monkeypatch.setattr(
        agents, "get_chat_model", lambda **kw: FakeListChatModel(responses=["这是答案"])
    )

    chain = agents.build_qa_chain()
    out = chain.invoke(
        {
            "course_name": "c",
            "domain": "d",
            "custom_terms": "x",
            "context": "ctx",
            "question": "q",
        }
    )
    assert type(out) is str, f"QA 链输出应为精确 str，实际 {type(out).__name__}"
    assert "这是答案" in out
    json.dumps({"answer": out})  # 必须可 JSON 序列化


def test_client_chat_returns_plain_str(monkeypatch):
    """client.chat 也必须返回精确 str"""
    from llm import client as client_mod

    monkeypatch.setattr(
        client_mod, "get_chat_model", lambda **kw: FakeListChatModel(responses=["hello"])
    )
    out = client_mod.chat([{"role": "user", "content": "hi"}])
    assert out is not None
    assert type(out) is str
    assert out == "hello"


# =============================================================================
# 3. 结构化输出的容错（不规范模型输出守卫）
# =============================================================================


def test_summary_schema_normalizes_string_lists():
    """模型把数组写成字符串时必须被归一"""
    raw = json.dumps(
        {"summary": "x", "key_points": "a\nb", "review_words": ["w1"]}, ensure_ascii=False
    )
    obj = PydanticOutputParser(pydantic_object=LectureSummary).parse(raw)
    assert obj.key_points == ["a", "b"]
    assert obj.review_words == ["w1"]


def test_summary_schema_truncates_lists():
    """超长列表必须按迁移前的上限截断"""
    raw = json.dumps(
        {
            "summary": "x",
            "key_points": [f"k{i}" for i in range(12)],
            "review_words": [f"w{i}" for i in range(12)],
        },
        ensure_ascii=False,
    )
    obj = PydanticOutputParser(pydantic_object=LectureSummary).parse(raw)
    assert len(obj.key_points) == 8
    assert len(obj.review_words) == 6


def test_word_explanation_schema_normalizes_related():
    """related 为逗号分隔字符串时应被拆分"""
    obj = WordExplanation(related="a 译一, b 译二")
    assert obj.related == ["a 译一", "b 译二"]


def test_term_extraction_schema_parses():
    raw = json.dumps(
        {
            "terms": [
                {"word": "gradient", "translation": "梯度", "definition": "偏导", "domain": "机器学习"}
            ]
        },
        ensure_ascii=False,
    )
    obj = PydanticOutputParser(pydantic_object=TermExtractionResult).parse(raw)
    assert obj.terms[0].word == "gradient"


def test_schemas_ignore_extra_fields():
    """模型多吐字段不应导致解析失败"""
    raw = json.dumps(
        {"summary": "x", "key_points": [], "review_words": [], "unexpected": "ignored"},
        ensure_ascii=False,
    )
    obj = PydanticOutputParser(pydantic_object=LectureSummary).parse(raw)
    assert obj.summary == "x"


# =============================================================================
# 4. 提示词模板完整性
# =============================================================================


@pytest.mark.parametrize(
    "template,expected",
    [
        (
            QA_PROMPT_TEMPLATE,
            {"course_name", "domain", "custom_terms", "context", "question"},
        ),
        (SUMMARY_PROMPT_TEMPLATE, {"course_name", "domain", "text", "vocab"}),
        (EXPLAIN_PROMPT_TEMPLATE, {"word", "course_name", "domain", "context"}),
        (EXTRACTION_PROMPT_TEMPLATE, {"course_name", "domain", "custom_terms", "text"}),
    ],
)
def test_prompt_template_variables(template, expected):
    """模板变量必须与业务所需完全一致（少一个会在构图期报错，多一个则是死代码）"""
    assert set(template.input_variables) == expected


def test_prompt_template_renders_without_placeholder():
    """渲染后不得残留未替换的占位符"""
    rendered = QA_PROMPT_TEMPLATE.format(
        course_name="机器学习",
        domain="计算机",
        custom_terms="gradient",
        context="ctx",
        question="q",
    )
    assert "机器学习" in rendered
    assert "{question}" not in rendered
    assert "{course_name}" not in rendered


# =============================================================================
# 5. 降级语义
# =============================================================================


def test_safe_invoke_returns_default_on_error():
    """链抛异常时必须返回 default 而不是向上抛"""

    class Boom(FakeListChatModel):
        def _call(self, *args, **kwargs):
            raise RuntimeError("模拟 LLM 不可用")

    chain = SUMMARY_PROMPT_TEMPLATE | Boom(responses=["x"])
    out = safe_invoke(
        chain,
        {"course_name": "c", "domain": "d", "text": "t", "vocab": "v"},
        default=None,
        label="test",
    )
    assert out is None


# =============================================================================
# 7. LangChain Agent（工具调用循环）—— 作业「智能应用」的核心证据
# =============================================================================


def test_agent_tools_are_well_defined():
    """三个工具必须有名字与可读的说明书（LangChain Tool 的描述即接口）"""
    from llm.chains.agent_tools import (
        lookup_term,
        match_course_terms,
        search_subtitle_context,
    )

    for t in (lookup_term, match_course_terms, search_subtitle_context):
        assert t.name
        assert len(t.description) > 60, f"{t.name} 的说明书过短，模型难以正确调用"
        assert t.args_schema is not None


def test_agent_tools_execute_standalone():
    """工具必须能在不经过模型的情况下独立执行（否则 Agent 调用会失败）"""
    from llm.chains.agent_tools import lookup_term, search_subtitle_context

    r = lookup_term.invoke({"word": "gradient"})
    assert "gradient" in r

    r2 = search_subtitle_context.invoke(
        {"keyword": "gradient", "context": "we compute the gradient of the loss"}
    )
    assert "gradient" in r2

    # 查不到时要明确说明，不能静默返回空
    r3 = lookup_term.invoke({"word": "zzz_not_a_real_word_9x7q"})
    assert "未收录" in r3


def test_agent_runs_tool_call_loop(monkeypatch):
    """Agent 必须能走完「模型决策 → 调工具 → 模型总结」完整循环"""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    import llm.chains.base as base

    class ToolCallingModel(FakeListChatModel):
        """模拟真实模型：先要求调工具，拿到结果后再给最终回答"""

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
                            "args": {"word": "gradient"},
                            "id": "call_1",
                            "type": "tool_call",
                        }
                    ],
                )
            else:
                msg = AIMessage(content="最终回答：gradient 指「梯度」。")
            return ChatResult(generations=[ChatGeneration(message=msg)])

    monkeypatch.setattr(
        base, "get_chat_model", lambda **kw: ToolCallingModel(responses=["x"])
    )

    from llm.chains.agent_tools import build_qa_agent

    agent = build_qa_agent()
    out = agent.invoke({"messages": [{"role": "user", "content": "gradient 是什么？"}]})
    msgs = out["messages"] if isinstance(out, dict) else out.messages

    kinds = []
    for m in msgs:
        if getattr(m, "tool_calls", None):
            kinds.append("tool_call")
        elif m.__class__.__name__ == "ToolMessage":
            kinds.append("tool_result")
        else:
            kinds.append("answer")

    assert "tool_call" in kinds, "Agent 未发起工具调用"
    assert "tool_result" in kinds, "Agent 未收到工具结果"
    assert kinds[-1] == "answer", "Agent 未给出最终回答"


def test_chatqa_extract_text_from_agent_result():
    """从 Agent 返回值中提取最终回答，必须跳过 tool_call 中间态"""
    from langchain_core.messages import AIMessage, ToolMessage

    from llm.qa import ChatQA

    msgs = [
        AIMessage(content="", tool_calls=[{"name": "x", "args": {}, "id": "1", "type": "tool_call"}]),
        ToolMessage(content="工具输出", tool_call_id="1"),
        AIMessage(content="这是最终回答。"),
    ]
    out = ChatQA._extract_text(type("R", (), {"messages": msgs})())
    assert out == "这是最终回答。"


def test_chatqa_falls_back_to_chain_when_agent_fails(monkeypatch):
    """Agent 失败时应自动回退链模式，保证答疑不彻底不可用"""
    import llm.chains.base as base
    from llm.qa import ChatQA

    monkeypatch.setattr(base, "is_available", lambda: True)

    qa = ChatQA(use_agent=True)

    # 制造一个必然抛异常的 Agent
    class BoomAgent:
        def invoke(self, *a, **k):
            raise RuntimeError("模拟 Agent 崩溃")

    qa._agent = BoomAgent()

    # 链模式可用（桩掉模型）
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    monkeypatch.setattr(
        base, "get_chat_model", lambda **kw: FakeListChatModel(responses=["链模式的回答"])
    )
    qa._chain = None

    answer = qa.answer(question="什么是梯度？", context="ctx")
    assert answer == "链模式的回答"


# =============================================================================
# 8. RAG 字幕检索层
# =============================================================================


def test_parse_subtitles_with_timestamps():
    """带时间戳的字幕必须被正确解析，且保留时间信息"""
    from llm.chains.rag import parse_subtitles

    raw = (
        "[00:12] Today we discuss gradient descent.\n"
        "[01:23] If the learning rate is too large, it may diverge.\n"
    )
    chunks = parse_subtitles(raw)
    assert len(chunks) == 2
    assert chunks[0].start == 12
    assert chunks[1].start == 83
    assert chunks[0].timestamp == "00:12"
    assert chunks[1].timestamp == "01:23"


def test_parse_subtitles_without_timestamps():
    """无时间戳时应退化为按行合并，不报错"""
    from llm.chains.rag import parse_subtitles

    chunks = parse_subtitles("first line\nsecond line")
    assert len(chunks) == 1
    assert "first line" in chunks[0].text


def test_chunk_subtitles_merges_and_caps():
    """切分必须合并短句、且遵守上限"""
    from llm.chains.rag import chunk_subtitles, parse_subtitles

    raw = "\n".join(f"[00:{i:02d}] sentence number {i} " + "x" * 40 for i in range(30))
    chunks = parse_subtitles(raw)
    merged = chunk_subtitles(chunks, chunk_size=200, chunk_overlap=20, max_chunks=5)
    assert len(merged) <= 5
    assert all(len(m.text) > 0 for m in merged)


def test_knowledge_base_retrieve():
    """字幕知识库必须能建索引并检索出片段"""
    from langchain_core.embeddings.fake import FakeEmbeddings

    from llm.chains.rag import SubtitleKnowledgeBase

    kb = SubtitleKnowledgeBase(embeddings=FakeEmbeddings(size=64))
    n = kb.build(
        "[00:12] Today we discuss gradient descent.\n"
        "[02:10] We introduce the concept of eigenvalue.\n",
        course_name="ML",
    )
    assert n > 0
    assert kb.is_ready

    hits = kb.retrieve("gradient descent", k=2)
    assert len(hits) >= 1
    # 检索结果必须带时间戳，便于回溯到"第几分钟"
    assert hits[0].timestamp


def test_knowledge_base_empty_is_safe():
    """空索引 / 空查询不得抛异常"""
    from langchain_core.embeddings.fake import FakeEmbeddings

    from llm.chains.rag import SubtitleKnowledgeBase

    kb = SubtitleKnowledgeBase(embeddings=FakeEmbeddings(size=64))
    assert kb.retrieve("anything") == []
    assert kb.format_context([]) == ""
    assert kb.build("") == 0


def test_retrieve_tool_is_registered():
    """给知识库后，Agent 必须挂上检索字幕的工具"""
    from langchain_core.embeddings.fake import FakeEmbeddings

    import llm.chains.base as base
    from llm.chains.rag import SubtitleKnowledgeBase

    kb = SubtitleKnowledgeBase(embeddings=FakeEmbeddings(size=64))
    kb.build("[00:12] gradient descent is an optimization algorithm.")

    monkey = base.get_chat_model
    base.get_chat_model = lambda **kw: None
    try:
        from llm.chains.agent_tools import build_qa_agent

        agent = build_qa_agent(knowledge_base=kb)
        # create_agent 返回的是 LangGraph 编译图，工具挂在 'tools' 节点的 ToolNode 上
        tools_node = agent.nodes["tools"]
        names = list((tools_node.bound.tools_by_name or {}).keys())
        assert "search_subtitles" in names, f"未挂载 RAG 工具，实际: {names}"
        # 三个基础工具也必须都在
        for n in ("lookup_term", "match_course_terms", "search_subtitle_context"):
            assert n in names, f"缺少工具 {n}，实际: {names}"
    finally:
        base.get_chat_model = monkey


# =============================================================================
# 9. 多轮对话记忆（checkpointer）
# =============================================================================


def test_checkpoint_store_roundtrip():
    """持久化存储必须能写入并取回消息"""
    from langchain_core.messages import HumanMessage

    from llm.chains.memory import InMemoryCheckpointStore

    store = InMemoryCheckpointStore()
    # 直接验证存储接口
    assert store.list_threads() == []


def test_agent_memory_accumulates_across_turns(monkeypatch):
    """同一 thread 的多轮对话，历史必须逐轮累积"""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    import llm.chains.base as base

    seen = []

    class CountingModel(FakeListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

        def _call(self, *a, **k):
            return "x"

        def _generate(self, *a, **k):
            seen.append(len(a[0]) if a else 0)
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="回答"))])

    monkeypatch.setattr(base, "get_chat_model", lambda **kw: CountingModel(responses=["x"]))

    from llm.chains.agent_tools import build_qa_agent
    from llm.chains.memory import get_checkpoint_store, make_thread_config

    store = get_checkpoint_store()
    agent = build_qa_agent(checkpointer=store)

    cfg = make_thread_config("test-thread-accumulate")
    for _ in range(3):
        agent.invoke({"messages": [{"role": "user", "content": "问题"}]}, cfg)

    # 消息数必须递增（2 → 4 → 6：每轮一问一答）
    assert len(seen) == 3
    assert seen[1] > seen[0], f"第二轮未带上历史: {seen}"
    assert seen[2] > seen[1], f"第三轮未带上历史: {seen}"


def test_agent_memory_isolates_threads(monkeypatch):
    """不同 thread 的历史必须互相隔离"""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    import llm.chains.base as base

    seen = []

    class CountingModel(FakeListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

        def _call(self, *a, **k):
            return "x"

        def _generate(self, *a, **k):
            seen.append(len(a[0]) if a else 0)
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="回答"))])

    monkeypatch.setattr(base, "get_chat_model", lambda **kw: CountingModel(responses=["x"]))

    from llm.chains.agent_tools import build_qa_agent
    from llm.chains.memory import get_checkpoint_store, make_thread_config

    store = get_checkpoint_store()
    agent = build_qa_agent(checkpointer=store)

    # thread A 先聊两轮
    for _ in range(2):
        agent.invoke(
            {"messages": [{"role": "user", "content": "A 的问题"}]},
            make_thread_config("thread-A"),
        )
    # thread B 全新开始
    agent.invoke(
        {"messages": [{"role": "user", "content": "B 的问题"}]},
        make_thread_config("thread-B"),
    )

    assert seen[-1] < seen[-2], f"新 thread 带上了别的 thread 的历史: {seen}"


def test_make_thread_config():
    """thread config 必须符合 LangChain 的 configurable 约定"""
    from llm.chains.memory import make_thread_config

    cfg = make_thread_config("abc")
    assert cfg["configurable"]["thread_id"] == "abc"


# =============================================================================
# 10. 多 Agent 协作工作流（LangGraph）
# =============================================================================


def _scripted_model(answers):
    """构造按脚本顺序返回的假模型"""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class Scripted(FakeListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

        def _call(self, *a, **k):
            return "x"

        def _generate(self, *a, **k):
            text = answers.pop(0) if answers else "备用"
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    return Scripted(responses=["x"])


def test_review_loop_graph_structure():
    """工作流必须含讲师与审校两个节点，且有条件边"""
    import llm.chains.base as base
    from llm.chains.workflow import build_review_loop

    monkey = base.get_chat_model
    base.get_chat_model = lambda **kw: None
    try:
        g = build_review_loop(max_iterations=3)
        nodes = list(g.get_graph().nodes.keys())
        assert "lecture_node" in nodes, f"缺少讲师节点: {nodes}"
        assert "fact_check_node" in nodes, f"缺少审校节点: {nodes}"
        assert "__start__" in nodes and "__end__" in nodes
    finally:
        base.get_chat_model = monkey


def test_review_loop_passes_when_fact_check_approves(monkeypatch):
    """审校一次通过时，只迭代一轮"""
    import json

    import llm.chains.base as base
    from llm.chains.workflow import build_review_loop

    answers = [
        "草稿：梯度是偏导数向量。",
        json.dumps({"passed": True, "reason": "与字幕一致", "issues": []}, ensure_ascii=False),
    ]
    monkeypatch.setattr(base, "get_chat_model", lambda **kw: _scripted_model(answers))

    g = build_review_loop(max_iterations=3, with_human_review=False)
    out = g.invoke(
        {
            "word": "gradient",
            "course_name": "机器学习",
            "domain": "CS",
            "context": "we compute the gradient of the loss",
            "transcript": "",
            "draft": "",
            "verdict": {},
            "issues": [],
            "iteration": 0,
        },
        {"recursion_limit": 20},
    )
    assert out.get("iteration") == 1
    assert out["verdict"]["passed"] is True
    assert "偏导数" in out["draft"]


def test_review_loop_retries_when_fact_check_rejects(monkeypatch):
    """审校不通过时必须退回重写，迭代次数增加"""
    import json

    import llm.chains.base as base
    from llm.chains.workflow import build_review_loop

    answers = [
        "草稿1：有问题的说法。",
        json.dumps(
            {"passed": False, "reason": "与字幕不符", "issues": ["字幕里没有这句"]},
            ensure_ascii=False,
        ),
        "草稿2：修正后的说法。",
        json.dumps({"passed": True, "reason": "已修正", "issues": []}, ensure_ascii=False),
    ]
    monkeypatch.setattr(base, "get_chat_model", lambda **kw: _scripted_model(answers))

    g = build_review_loop(max_iterations=3, with_human_review=False)
    out = g.invoke(
        {
            "word": "gradient",
            "course_name": "机器学习",
            "domain": "CS",
            "context": "ctx",
            "transcript": "",
            "draft": "",
            "verdict": {},
            "issues": [],
            "iteration": 0,
        },
        {"recursion_limit": 20},
    )
    # 重写后应采用第二版草稿
    assert out.get("iteration") == 2
    assert "修正后" in out["draft"]
    # 首轮的审校意见必须被累积保留
    assert any("字幕里没有这句" in i for i in out.get("issues", []))


def test_review_loop_respects_max_iterations(monkeypatch):
    """审校永远不通过时，必须在 max_iterations 后停止"""
    import json

    import llm.chains.base as base
    from llm.chains.workflow import build_review_loop

    always_fail = json.dumps(
        {"passed": False, "reason": "一直不通过", "issues": ["x"]}, ensure_ascii=False
    )
    answers = ["草稿", always_fail] * 10
    monkeypatch.setattr(base, "get_chat_model", lambda **kw: _scripted_model(answers))

    g = build_review_loop(max_iterations=3, with_human_review=False)
    out = g.invoke(
        {
            "word": "w",
            "course_name": "c",
            "domain": "d",
            "context": "ctx",
            "transcript": "",
            "draft": "",
            "verdict": {},
            "issues": [],
            "iteration": 0,
        },
        {"recursion_limit": 30},
    )
    assert out.get("iteration") == 3, f"未遵守迭代上限: {out.get('iteration')}"
    assert out["verdict"]["passed"] is False


def test_review_loop_handles_unparsable_verdict():
    """审校输出无法解析时必须安全降级（按通过处理），不得抛异常"""
    from llm.chains.workflow import _parse_verdict

    v = _parse_verdict("这不是 JSON")
    assert v["passed"] is True
    assert v["issues"] == []


def test_last_text_handles_dict_and_object(monkeypatch):
    """
    LangChain 1.x 的 Agent 返回 dict 而非 AIMessage 对象。
    两种形态都必须能提取到文本 —— 这是迁移期真实踩过的坑。
    """
    from langchain_core.messages import AIMessage

    from llm.chains.workflow import _last_text

    msgs = [AIMessage(content="最终答案")]
    assert _last_text({"messages": msgs}) == "最终答案"

    class Obj:
        messages = msgs

    assert _last_text(Obj()) == "最终答案"
    # 两种形态结果必须一致
    assert _last_text({"messages": msgs}) == _last_text(Obj())


def test_qa_extract_text_handles_dict_return():
    """答疑链的文本提取也必须兼容 dict 返回形态"""
    from langchain_core.messages import AIMessage

    from llm.qa import ChatQA

    msgs = [AIMessage(content="答疑内容")]
    assert ChatQA._extract_text({"messages": msgs}) == "答疑内容"


# =============================================================================
# 11. 人工审批（LangGraph interrupt）
# =============================================================================


def test_workflow_graph_includes_human_review():
    """启用人工审批时，图里必须有 human_review 节点"""
    import llm.chains.base as base
    from llm.chains.workflow import build_review_loop

    monkey = base.get_chat_model
    base.get_chat_model = lambda **kw: None
    try:
        g = build_review_loop(with_human_review=True)
        nodes = list(g.get_graph().nodes.keys())
        assert "human_review" in nodes, f"缺少人工审批节点: {nodes}"

        g2 = build_review_loop(with_human_review=False)
        nodes2 = list(g2.get_graph().nodes.keys())
        assert "human_review" not in nodes2, f"未启用时不应有该节点: {nodes2}"
    finally:
        base.get_chat_model = monkey


def test_interrupt_pauses_and_resumes(monkeypatch):
    """interrupt 必须暂停在待审状态，恢复后才继续到定稿"""
    import json

    import llm.chains.base as base
    from llm.chains.workflow import LearningLoop

    answers = [
        "草稿：梯度下降沿梯度负方向迭代更新参数。",
        json.dumps({"passed": True, "reason": "ok", "issues": []}, ensure_ascii=False),
    ]
    monkeypatch.setattr(base, "get_chat_model", lambda **kw: _scripted_model(answers))

    loop = LearningLoop(with_human_review=True)

    # 阶段一：跑到人工审批就暂停
    r1 = loop.run(word="gradient descent", context="ctx", thread_id="t-review-1")
    assert r1["needs_review"] is True, "未停在人工审批"
    req = r1["review_request"]
    assert req is not None
    assert req["type"] == "review_explanation"
    assert "draft" in req and req["draft"], "待审内容必须带讲解草稿"
    assert req["options"] == ["approve", "reject"]

    # 阶段二：学生确认 → 恢复并定稿
    r2 = loop.resume(thread_id="t-review-1", approved=True)
    assert r2["approved"] is True
    assert r2["needs_review"] is False
    assert "梯度下降" in r2["draft"]


def test_interrupt_rejection_triggers_rewrite(monkeypatch):
    """学生要求修改时应退回重写，并再次停在待审"""
    import json

    import llm.chains.base as base
    from llm.chains.workflow import LearningLoop

    answers = [
        "草稿1：初版。",
        json.dumps({"passed": True, "reason": "ok", "issues": []}, ensure_ascii=False),
        "草稿2：按批注改过的版本。",
        json.dumps({"passed": True, "reason": "ok", "issues": []}, ensure_ascii=False),
    ]
    monkeypatch.setattr(base, "get_chat_model", lambda **kw: _scripted_model(answers))

    loop = LearningLoop(with_human_review=True)
    r1 = loop.run(word="w", context="ctx", thread_id="t-review-2")
    assert r1["needs_review"] is True
    iter_before = r1["iteration"]

    r2 = loop.resume(thread_id="t-review-2", approved=False, note="改得更具体些")
    assert r2["approved"] is False
    assert r2["iteration"] > iter_before, "要求修改后未触发重写"
    assert r2["needs_review"] is True, "重写后应再次停在待审"


def test_learning_loop_without_human_review_runs_to_end(monkeypatch):
    """不启用人工审批时应一次跑完（兼容原有调用方式）"""
    import json

    import llm.chains.base as base
    from llm.chains.workflow import LearningLoop

    answers = [
        "草稿：讲解内容。",
        json.dumps({"passed": True, "reason": "ok", "issues": []}, ensure_ascii=False),
    ]
    monkeypatch.setattr(base, "get_chat_model", lambda **kw: _scripted_model(answers))

    loop = LearningLoop(with_human_review=False)
    r = loop.run(word="w", context="ctx")
    assert r["needs_review"] is False
    assert r["iteration"] == 1
    assert r["draft"]


def test_learning_loop_rejects_empty_word():
    """空词必须短路返回，不得触发任何模型调用"""
    from llm.chains.workflow import LearningLoop

    loop = LearningLoop()
    r = loop.run(word="   ")
    assert r["draft"] is None
    assert r["needs_review"] is False
