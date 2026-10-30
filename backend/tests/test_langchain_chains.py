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
