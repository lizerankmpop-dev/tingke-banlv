"""
LangChain 结构化输出模型（Pydantic Schema）

迁移前，四个 LLM 模块各自用 `re.search(r'\\{...\\}')` + `json.loads()` 手工抠 JSON，
这是典型的脆弱设计 —— 模型一旦输出带代码块围栏、尾随逗号或前后寒暄，
解析就会静默失败并返回空结果。

改用 Pydantic 模型后：
1. **Schema 即契约**：字段与类型写死在代码里，LangChain 会把它作为 JSON Schema
   注入 prompt，并在解析端做类型校验与强制转换。
2. **容错增强**：`extra="ignore"` 容忍模型多吐字段；
   列表字段用 `field_validator` 兜住「模型把数组写成字符串」的情况。
3. **失败可见**：解析失败会抛 `ValidationError`，由统一降级层记录日志，
   而不是在 `_parse_response` 里悄悄 `return None`。

各 Schema 的字段与迁移前的返回结构逐字对齐，前端契约不变。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


# =============================================================================
# 一、单词讲解（对应 explainer.explain）
# =============================================================================


class WordExplanation(BaseModel):
    """单词语境讲解的结构化输出"""

    translation: str = Field(default="", description="简洁的中文翻译")
    meaning_in_context: str = Field(default="", description="在课程语境中的具体含义")
    example: str = Field(default="", description="英文例句及其中文翻译")
    related: list[str] = Field(default_factory=list, description="相关词/近义词列表")

    @field_validator("related", mode="before")
    @classmethod
    def _coerce_related(cls, v: Any) -> list[str]:
        """模型可能把 related 写成单个字符串或逗号分隔串，统一归一为列表"""
        if v is None:
            return []
        if isinstance(v, str):
            parts = [p.strip() for p in v.split(",") if p.strip()]
            return parts[:4]
        if isinstance(v, (list, tuple)):
            return [str(x) for x in v][:4]
        return []

    def to_dict(self) -> dict:
        """转成前端期望的字典结构"""
        return {
            "translation": self.translation,
            "meaning_in_context": self.meaning_in_context,
            "example": self.example,
            "related": self.related,
        }


# =============================================================================
# 二、课后摘要（对应 summarizer.summarize）
# =============================================================================


class LectureSummary(BaseModel):
    """课后摘要的结构化输出"""

    summary: str = Field(default="", description="本节课内容摘要")
    key_points: list[str] = Field(default_factory=list, description="知识点列表")
    review_words: list[str] = Field(default_factory=list, description="建议复习的重点生词")

    @field_validator("key_points", mode="before")
    @classmethod
    def _coerce_key_points(cls, v: Any) -> list[str]:
        """归一化知识点列表，并按迁移前逻辑截断到 8 条"""
        if v is None:
            return []
        if isinstance(v, str):
            parts = [p.strip() for p in v.split("\n") if p.strip()]
            return parts[:8]
        if isinstance(v, (list, tuple)):
            return [str(x) for x in v][:8]
        return []

    @field_validator("review_words", mode="before")
    @classmethod
    def _coerce_review_words(cls, v: Any) -> list[str]:
        """归一化复习词列表，并按迁移前逻辑截断到 6 条"""
        if v is None:
            return []
        if isinstance(v, str):
            parts = [p.strip() for p in v.split("\n") if p.strip()]
            return parts[:6]
        if isinstance(v, (list, tuple)):
            return [str(x) for x in v][:6]
        return []

    def to_dict(self) -> dict:
        """转成前端期望的字典结构"""
        return {
            "summary": self.summary,
            "key_points": self.key_points,
            "review_words": self.review_words,
        }


# =============================================================================
# 三、术语提取（对应 extractor.extract_terms）
# =============================================================================


class ExtractedTerm(BaseModel):
    """单个术语的结构化输出"""

    word: str = Field(default="", description="英文原词")
    translation: str = Field(default="", description="简体中文翻译")
    definition: str = Field(default="", description="中文释义（1-2 句）")
    domain: str = Field(default="", description="所属学术领域")


class TermExtractionResult(BaseModel):
    """术语提取的整体输出（一个列表）"""

    terms: list[ExtractedTerm] = Field(default_factory=list, description="提取到的术语列表")


# =============================================================================
# 四、答疑（对应 qa.answer）—— 纯文本，无需 schema
# =============================================================================
# 答疑输出是自由文本，直接用 StrOutputParser 即可。


__all__ = [
    "WordExplanation",
    "LectureSummary",
    "ExtractedTerm",
    "TermExtractionResult",
]
