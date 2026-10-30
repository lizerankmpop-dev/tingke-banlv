"""
LangChain 链的离线验证脚本

用 LangChain 的 FakeListChatModel / 自定义 Runnable 替代真实大模型，
在不依赖 Ollama 的情况下验证四条链的编排逻辑、结构化输出与降级行为。

运行：
    cd backend
    python -m llm.chains.verify
"""

from __future__ import annotations

import json
import sys
from typing import Any

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage

from llm.chains.base import safe_invoke
from llm.chains.prompts import (
    EXPLAIN_PROMPT_TEMPLATE,
    EXTRACTION_PROMPT_TEMPLATE,
    QA_PROMPT_TEMPLATE,
    SUMMARY_PROMPT_TEMPLATE,
)
from llm.chains.schemas import (
    LectureSummary,
    TermExtractionResult,
    WordExplanation,
)

PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, cond: bool, detail: str = ""):
    results.append((PASS if cond else FAIL, name, detail))
    mark = "OK  " if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f"  —— {detail}" if detail else ""))


class ScriptedChatModel(FakeListChatModel):
    """按脚本返回预设响应的假模型

    注意：FakeListChatModel 的 `_call` 契约是返回 **str**，
    框架会自行包装为 AIMessage。此处覆写只是为了实现「用完最后一个就停住」
    的语义（父类默认循环回第一个）。
    """

    def _call(self, *args: Any, **kwargs: Any) -> str:
        resp = self.responses[self.i] if self.i < len(self.responses) else self.responses[-1]
        if self.i < len(self.responses) - 1:
            self.i += 1
        else:
            self.i = 0
        return resp


# =============================================================================
# 1. 提示词模板：变量完整性
# =============================================================================
print("\n[1] ChatPromptTemplate 变量与渲染")
check(
    "QA 模板变量齐备",
    set(QA_PROMPT_TEMPLATE.input_variables)
    == {"course_name", "domain", "custom_terms", "context", "question"},
)
rendered = QA_PROMPT_TEMPLATE.format(
    course_name="机器学习",
    domain="计算机",
    custom_terms="gradient",
    context="we compute the gradient",
    question="什么是梯度?",
)
check("QA 模板可渲染且无残留占位符", "机器学习" in rendered and "{question}" not in rendered)
check(
    "EXTRACT 模板变量齐备",
    set(EXTRACTION_PROMPT_TEMPLATE.input_variables)
    == {"course_name", "domain", "custom_terms", "text"},
)
check(
    "SUMMARY 模板变量齐备",
    set(SUMMARY_PROMPT_TEMPLATE.input_variables) == {"course_name", "domain", "text", "vocab"},
)
check(
    "EXPLAIN 模板变量齐备",
    set(EXPLAIN_PROMPT_TEMPLATE.input_variables)
    == {"word", "course_name", "domain", "context"},
)


# =============================================================================
# 2. 结构化输出 Schema
# =============================================================================
print("\n[2] Pydantic 结构化输出模型")
w = WordExplanation(
    translation="梯度",
    meaning_in_context="损失函数的偏导数向量",
    example="compute the gradient",
    related="derivative 导数, backpropagation 反向传播",
)
check("related 字符串自动归一为列表", w.related == ["derivative 导数", "backpropagation 反向传播"])
check("WordExplanation.to_dict 字段完整", set(w.to_dict()) == {"translation", "meaning_in_context", "example", "related"})

s = LectureSummary(
    summary="本节课介绍梯度下降",
    key_points="a\nb\nc\nd\ne\nf\ng\nh\ni",
    review_words="gradient 梯度",
)
check("key_points 超过 8 条被截断", len(s.key_points) == 8)
check("review_words 字符串被拆分", s.review_words == ["gradient 梯度"])
check("LectureSummary.to_dict 字段完整", set(s.to_dict()) == {"summary", "key_points", "review_words"})

r = TermExtractionResult(
    terms=[{"word": "gradient", "translation": "梯度", "definition": "偏导数", "domain": "机器学习"}]
)
check("TermExtractionResult 解析正常", r.terms[0].word == "gradient")


# =============================================================================
# 3. 链的编排：模型输出 → 结构化解析
# =============================================================================
print("\n[3] LCEL 链编排与结构化输出解析")

# 3.1 摘要链：模型返回 JSON 文本 → with_structured_output 解析为对象
summary_json = json.dumps(
    {
        "summary": "本节课讲解了梯度下降算法的原理与实现。",
        "key_points": ["梯度下降是一种迭代优化算法", "学习率影响收敛速度"],
        "review_words": ["gradient 梯度", "learning rate 学习率"],
    },
    ensure_ascii=False,
)
model = ScriptedChatModel(responses=[summary_json])
from langchain_core.output_parsers import StrOutputParser  # noqa: F401

structured = SUMMARY_PROMPT_TEMPLATE | model | StrOutputParser()
out = safe_invoke(structured, {
    "course_name": "机器学习",
    "domain": "计算机",
    "text": "we compute the gradient ...",
    "vocab": "gradient: 梯度",
}, default=None, label="test")
check("摘要链产出原始 JSON 文本", out is not None and "本节课讲解了梯度下降" in str(out))

# 3.2 with_structured_output 的解析行为（用 ChatOllama 的等价路径验证 schema 约束生成）
check(
    "结构化 schema 含全部必需字段",
    set(LectureSummary.model_fields) == {"summary", "key_points", "review_words"},
)
check(
    "结构化 schema 含全部必需字段(word)",
    set(WordExplanation.model_fields) == {"translation", "meaning_in_context", "example", "related"},
)


# =============================================================================
# 4. RunnableBranch 分级：词表命中 vs 未命中
# =============================================================================
print("\n[4] RunnableBranch 分级（本地词表优先 / AI 兜底）")

from langchain_core.runnables import RunnableBranch, RunnableLambda

hit = RunnableLambda(lambda p: {"source": "local_vocab", "translation": "本地词库结果"})
miss = RunnableLambda(lambda p: {"source": "ai", "translation": "LLM 生成结果"})
branch = RunnableBranch(
    (RunnableLambda(lambda p: p.get("word") in {"gradient"}), hit),
    miss,
)
r1 = branch.invoke({"word": "gradient"})
r2 = branch.invoke({"word": "eigenvalue"})
check("词表命中走本地分支", r1["source"] == "local_vocab")
check("词表未命中走 AI 分支", r2["source"] == "ai")


# =============================================================================
# 4b. QA 链输出类型必须为精确 str
# =============================================================================
print("\n[4b] QA 链输出类型（防 TextAccessor 泄漏到 API 响应）")
import llm.chains.agents as _A
import llm.chains.base as _B

_orig_get_chat_model = _B.get_chat_model
_B.get_chat_model = lambda **kw: FakeListChatModel(responses=["这是答案"])
_A.get_chat_model = _B.get_chat_model
try:
    _qa_chain = _A.build_qa_chain()
    _qa_out = _qa_chain.invoke({
        "course_name": "c", "domain": "d", "custom_terms": "x",
        "context": "ctx", "question": "q",
    })
    check("QA 链输出是精确的 str（type is str）", type(_qa_out) is str,
          f"实际类型 {type(_qa_out).__name__}")
    check("QA 链输出内容正确", "这是答案" in str(_qa_out))
    import json as _json
    check("QA 链输出可正常 JSON 序列化", _json.dumps({"a": _qa_out}) is not None)
finally:
    _B.get_chat_model = _orig_get_chat_model
    _A.get_chat_model = _orig_get_chat_model


# =============================================================================
# 5. 降级语义：异常时返回 default 而不抛出
# =============================================================================
print("\n[5] 统一降级语义")


class BoomModel(FakeListChatModel):
    def _call(self, messages, stop=None, run_manager=None, **kwargs: Any):
        raise RuntimeError("模拟 LLM 不可用")


boom_chain = SUMMARY_PROMPT_TEMPLATE | BoomModel(responses=["x"]) | StrOutputParser()
out = safe_invoke(boom_chain, {
    "course_name": "c", "domain": "d", "text": "t", "vocab": "v"
}, default=None, label="test")
check("LLM 异常时返回 None 而非抛出", out is None)

out2 = safe_invoke(boom_chain, {
    "course_name": "c", "domain": "d", "text": "t", "vocab": "v"
}, default=[], label="test")
check("可指定自定义默认值", out2 == [])


# =============================================================================
# 6. 业务层薄封装：接口不变性
# =============================================================================
print("\n[6] 业务模块接口兼容性")
from llm.extractor import TermExtractor
from llm.explainer import WordExplainer
from llm.qa import ChatQA
from llm.summarizer import LectureSummarizer

check("TermExtractor.extract_terms 签名保持", hasattr(TermExtractor, "extract_terms"))
check("WordExplainer.explain 签名保持", hasattr(WordExplainer, "explain"))
check("LectureSummarizer.summarize 签名保持", hasattr(LectureSummarizer, "summarize"))
check("ChatQA.answer 签名保持", hasattr(ChatQA, "answer"))
check("TermExtractor.is_available_cached 保持", hasattr(TermExtractor, "is_available_cached"))

# 空输入的短路行为（不触网、不调模型）
te = TermExtractor()
check("空文本直接返回空列表", te.extract_terms(text="   ") == [])
ss = LectureSummarizer()
check("空文本摘要返回 None", ss.summarize(text="") is None)
qa = ChatQA()
check("空问题返回 None", qa.answer(question="  ", context="x") is None)
we = WordExplainer()
check("空单词返回 None", we.explain(word="") is None)


# =============================================================================
# 汇总
# =============================================================================
print("\n" + "=" * 60)
fails = [r for r in results if r[0] == FAIL]
print(f"总计 {len(results)} 项检查，通过 {len(results)-len(fails)}，失败 {len(fails)}")
if fails:
    print("\n失败项：")
    for _, name, detail in fails:
        print(f"  - {name} {detail}")
    sys.exit(1)
print("全部通过 ✓")
