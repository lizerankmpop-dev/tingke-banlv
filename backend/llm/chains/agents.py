"""
LangChain 智能体 —— 四条业务链的 LCEL 编排实现

这是「听课伴侣」响应第七章《智能应用开发》作业的核心代码：
用 LangChain 框架 + 离线大模型（Ollama / Qwen2.5）重新实现
「听课伴侣」的四项 AI 能力。

链路总览
--------

    ┌── 术语提取链 ────────────────────────────────────────────┐
    │ 字幕文本 → 提示词 → 模型 → 结构化术语列表 → 补上下文句  │
    └─────────────────────────────────────────────────────────┘

    ┌── 单词讲解链（RunnableBranch 分级） ────────────────────┐
    │ 词表命中 → 本地词库（零延迟零成本）                     │
    │ 词表未命中 → 提示词 → 模型 → 结构化讲解 → 标注来源       │
    └─────────────────────────────────────────────────────────┘

    ┌── 课后摘要链 ──────────────────────────────────────────┐
    │ 字幕全文 + 生词表 → 提示词 → 模型 → 结构化摘要/知识点/生词│
    └─────────────────────────────────────────────────────────┘

    ┌── 对话答疑链 ──────────────────────────────────────────┐
    │ 问题 + 最近字幕 → 提示词 → 模型 → 纯文本答案            │
    └─────────────────────────────────────────────────────────┘

对外接口与迁移前完全一致（见 llm/extractor.py 等模块的薄封装），
因此 API 路由与前端无需任何改动。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from langchain_core.runnables import Runnable, RunnableBranch, RunnableLambda, RunnablePassthrough

from llm.chains.base import (
    build_qa_chain,
    build_structured_chain,
    get_chat_model,
    is_available,
    safe_invoke,
)
from llm.chains.prompts import (
    EXPLAIN_PROMPT_TEMPLATE,
    EXTRACTION_PROMPT_TEMPLATE,
    QA_PROMPT_TEMPLATE,
    SUMMARY_PROMPT_TEMPLATE,
)
from llm.chains.schemas import (
    ExtractedTerm,
    LectureSummary,
    TermExtractionResult,
    WordExplanation,
)

logger = logging.getLogger(__name__)


# =============================================================================
# 一、术语提取链
# =============================================================================


def _find_context_sentence(word: str, text: str) -> str:
    """在原文中查找包含该词的句子（沿用迁移前的实现）"""
    sentences = re.split(r"[.!?。！？]+", text)
    for sentence in sentences:
        if word.lower() in sentence.lower():
            return sentence.strip()
    return ""


def build_term_extraction_chain(
    temperature: float = 0.1, num_predict: int = 1024, model: str | None = None
) -> Runnable:
    """
    构建术语提取链。

    链路：`提示词 → 模型(with_structured_output) → 术语列表 → 补上下文句`

    最后一步用 `RunnableLambda` 挂载后处理：为每个术语回填它在字幕原文中的
    上下文句子 —— 这是 LangChain 链式组合的典型用法：模型只负责抽取，
    确定性逻辑（正则分句）留在链的后处理节点。
    """
    structured = build_structured_chain(
        prompt=EXTRACTION_PROMPT_TEMPLATE,
        schema=TermExtractionResult,
        temperature=temperature,
        num_predict=num_predict,
        model=model,
    )

    def _attach_context(result: TermExtractionResult, source_text: str) -> list[dict]:
        """为术语补上原文上下文句"""
        out = []
        for term in result.terms:
            item = term.model_dump()
            item["context_sentence"] = _find_context_sentence(term.word, source_text)
            out.append(item)
        return out

    # 用 RunnablePassthrough 携带原始文本，供后处理节点回填上下文
    attach = RunnableLambda(
        lambda payload: _attach_context(
            payload["result"],
            payload.get("source_text", ""),
        )
    )

    return {
        "structured": structured,
        "attach_context": attach,
    }


class TermExtractionAgent:
    """术语提取智能体（LangChain 实现）"""

    def __init__(self, model: str | None = None):
        self.model = model
        self._available: bool | None = None
        self._chain = build_term_extraction_chain(model=model)

    def is_available_cached(self) -> bool:
        """查 LLM 是否可用（结果缓存，避免每条字幕都探测一次）"""
        if self._available is None:
            self._available = is_available()
        return self._available

    def extract_terms(
        self,
        text: str,
        domain: str = "",
        course_name: str = "",
        custom_terms: list[str] | None = None,
    ) -> list[dict]:
        """
        从字幕文本中提取术语。

        Args:
            text: 字幕文本
            domain: 课程领域
            course_name: 课程名称
            custom_terms: 用户自定义术语列表

        Returns:
            术语列表 [{word, translation, definition, domain, context_sentence}]
        """
        if not text.strip():
            return []
        if not self.is_available_cached():
            logger.warning("LLM 不可用，跳过术语提取")
            return []

        payload = {
            "course_name": course_name or "未知课程",
            "domain": domain or "通用",
            "custom_terms": ", ".join(custom_terms) if custom_terms else "无",
            "text": text[:2000],
            "source_text": text,
        }

        result = safe_invoke(
            self._chain["structured"], payload, default=None, label="术语提取"
        )
        if result is None:
            self._available = False
            return []

        terms = safe_invoke(
            self._chain["attach_context"],
            {"result": result, "source_text": text},
            default=[],
            label="术语上下文回填",
        )

        logger.info(f"提取到 {len(terms)} 个术语")
        return terms


# =============================================================================
# 二、单词讲解链（RunnableBranch 分级）
# =============================================================================


def build_word_explanation_chain(
    temperature: float = 0.3, num_predict: int = 768, model: str | None = None
) -> Runnable:
    """
    构建单词讲解链 —— 用 RunnableBranch 表达「本地词表优先、AI 兜底」。

    分支判定条件是「词表是否命中」，命中时完全不发 LLM 请求，
    保持零延迟零成本；未命中才走模型生成语境讲解。
    """
    structured = build_structured_chain(
        prompt=EXPLAIN_PROMPT_TEMPLATE,
        schema=WordExplanation,
        temperature=temperature,
        num_predict=num_predict,
        model=model,
    )

    def _local_lookup(payload: dict) -> dict | None:
        """查内置词表（3300+ 词），命中即返回"""
        from dictionary.terminology import lookup_vocab

        word = (payload.get("word") or "").strip()
        if not word:
            return None
        local = lookup_vocab(word)
        if not local:
            return None
        return {
            "translation": local.get("translation", ""),
            "meaning_in_context": local.get("definition", ""),
            "example": "",
            "related": [],
            "source": "local_vocab",
        }

    def _to_llm_payload(payload: dict) -> dict:
        """转成 LLM 链所需的输入"""
        return {
            "word": (payload.get("word") or "").strip(),
            "course_name": payload.get("course_name") or "未知课程",
            "domain": payload.get("domain") or "通用",
            "context": payload.get("context_sentence") or "（无上下文）",
        }

    def _mark_ai(payload: dict) -> dict:
        """AI 分支的结果统一标注来源"""
        obj = payload.get("result")
        if obj is None:
            return None
        result = obj.to_dict() if hasattr(obj, "to_dict") else dict(obj)
        result["source"] = "ai"
        return result

    local_branch = RunnableLambda(_local_lookup)

    def _ai_invoke(payload: dict) -> dict | None:
        """
        AI 分支的实际执行。

        可用性检查放在**分支内部**而非调用前 —— 这是与迁移前行为一致的关键：
        迁移前的流程是「先查本地词表，命中立即返回；未命中才检查 LLM 可用性」。
        若在调用链之前就检查可用性，本地词表命中的路径会在无 LLM 环境下被
        错误地短路成 None。
        """
        if not is_available():
            logger.warning("LLM 不可用，无法生成 AI 讲解")
            return None
        result = safe_invoke(
            structured, _to_llm_payload(payload), default=None, label="AI 讲解"
        )
        if result is None:
            return None
        return _mark_ai({"result": result})

    ai_branch = RunnableLambda(_ai_invoke)

    return RunnableBranch(
        (RunnableLambda(lambda p: _local_lookup(p) is not None), local_branch),
        ai_branch,
    )


class WordExplanationAgent:
    """单词讲解智能体（LangChain 实现，带内存缓存）"""

    def __init__(self, model: str | None = None):
        self.model = model
        self._cache: dict[str, dict] = {}
        self._chain = build_word_explanation_chain(model=model)

    def explain(
        self,
        word: str,
        domain: str = "",
        course_name: str = "",
        context_sentence: str = "",
    ) -> dict | None:
        """
        生成单词的上下文讲解。

        流程：查缓存 → RunnableBranch（本地词表优先 / AI 兜底）→ 写缓存

        Returns:
            {translation, meaning_in_context, example, related[], source} 或 None
        """
        word = word.strip()
        if not word:
            return None

        cache_key = f"{word.lower()}|{hash(context_sentence)}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # 注意：这里**不**做 LLM 可用性检查 —— 可用性判定属于 AI 分支的职责。
        # 本地词表命中时完全不需要 LLM，若提前检查会误伤这条零成本路径。
        result = safe_invoke(
            self._chain,
            {
                "word": word,
                "domain": domain,
                "course_name": course_name,
                "context_sentence": context_sentence,
            },
            default=None,
            label="单词讲解",
        )
        if result is None:
            return None

        # 限制缓存大小
        if len(self._cache) > 200:
            self._cache.clear()
        self._cache[cache_key] = result
        return result


# =============================================================================
# 三、课后摘要链
# =============================================================================


def build_summary_chain(
    temperature: float = 0.3, num_predict: int = 1536, model: str | None = None
) -> Runnable:
    """
    构建课后摘要链。

    链路：`提示词 → 模型(with_structured_output) → LectureSummary`
    """
    return build_structured_chain(
        prompt=SUMMARY_PROMPT_TEMPLATE,
        schema=LectureSummary,
        temperature=temperature,
        num_predict=num_predict,
        model=model,
    )


class LectureSummaryAgent:
    """课后摘要智能体（LangChain 实现）"""

    def __init__(self, model: str | None = None):
        self.model = model
        self._available: bool | None = None
        self._chain = build_summary_chain(model=model)

    def summarize(
        self,
        text: str,
        domain: str = "",
        course_name: str = "",
        vocabulary: list[dict] | None = None,
    ) -> dict | None:
        """
        生成课后摘要。

        Args:
            text: 字幕全文（已按时间正序拼接）
            domain: 课程领域
            course_name: 课程名称
            vocabulary: 课程词汇表 [{word, translation, isMemorized}]

        Returns:
            {summary, key_points[], review_words[]} 或 None
        """
        if not text.strip():
            return None

        if self._available is None:
            self._available = is_available()
        if not self._available:
            logger.warning("LLM 不可用，跳过摘要生成")
            return None

        # 未掌握词汇优先
        vocab_lines = []
        for v in vocabulary or []:
            if not v.get("isMemorized"):
                vocab_lines.append(f"{v.get('word', '')}: {v.get('translation', '')}")
        vocab_text = "\n".join(vocab_lines[:30]) if vocab_lines else "（暂无）"

        result = safe_invoke(
            self._chain,
            {
                "course_name": course_name or "未知课程",
                "domain": domain or "通用",
                "text": text[-6000:],
                "vocab": vocab_text,
            },
            default=None,
            label="摘要生成",
        )
        if result is None:
            self._available = False
            return None

        return result.to_dict()


# =============================================================================
# 四、对话答疑链
# =============================================================================


class ChatQAAgent:
    """对话式答疑智能体（LangChain 实现）"""

    def __init__(self, model: str | None = None):
        self.model = model
        self._available: bool | None = None
        self._chain = build_qa_chain(model=model)

    def answer(
        self,
        question: str,
        context: str,
        domain: str = "",
        course_name: str = "",
        custom_terms: list[str] | None = None,
    ) -> str | None:
        """
        回答学生提问。

        Args:
            question: 学生的问题
            context: 最近字幕文本（时间正序）
            domain: 课程领域
            course_name: 课程名称
            custom_terms: 自定义术语

        Returns:
            回答文本（Markdown 由前端按纯文本渲染），失败返回 None
        """
        question = question.strip()
        if not question:
            return None

        if self._available is None:
            self._available = is_available()
        if not self._available:
            logger.warning("LLM 不可用，无法答疑")
            return None

        result = safe_invoke(
            self._chain,
            {
                "course_name": course_name or "未知课程",
                "domain": domain or "通用",
                "custom_terms": ", ".join(custom_terms) if custom_terms else "无",
                "context": (
                    context[-5000:]
                    if context.strip()
                    else "（暂无字幕，请结合课程领域通用知识回答）"
                ),
                "question": question,
            },
            default=None,
            label="答疑",
        )
        if result is None:
            self._available = False
            return None
        return result


__all__ = [
    "TermExtractionAgent",
    "WordExplanationAgent",
    "LectureSummaryAgent",
    "ChatQAAgent",
    "build_term_extraction_chain",
    "build_word_explanation_chain",
    "build_summary_chain",
    "build_qa_chain",
]
