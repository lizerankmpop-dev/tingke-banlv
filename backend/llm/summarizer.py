"""
AI 课后摘要生成器（LangChain 实现）

原实现靠「提示模型输出 JSON + 正则抠 JSON」拿结构化结果，
现改为 LangChain 的结构化输出链：

    ChatPromptTemplate → ChatOllama/ChatOpenAI.with_structured_output(LectureSummary)

`with_structured_output()` 会在 prompt 中注入 JSON Schema 约束，
并由框架负责解析与类型校验，彻底消除手写正则解析的脆弱性。

对外接口 `LectureSummarizer.summarize()` 与迁移前完全一致。
"""

from __future__ import annotations

import logging

from llm.chains.agents import LectureSummaryAgent

logger = logging.getLogger(__name__)


class LectureSummarizer:
    """课后摘要生成器（LangChain 结构化输出链实现）"""

    def __init__(self, model: str = None):
        self._agent = LectureSummaryAgent(model=model)

    def summarize(
        self,
        text: str,
        domain: str = "",
        course_name: str = "",
        vocabulary: list[dict] = None,
    ) -> dict | None:
        """
        生成课后摘要

        Args:
            text: 字幕全文（已按时间正序拼接）
            domain: 课程领域
            course_name: 课程名称
            vocabulary: 课程词汇表 [{word, translation, isMemorized}]

        Returns:
            {summary, key_points[], review_words[]} 或 None
        """
        return self._agent.summarize(
            text=text,
            domain=domain,
            course_name=course_name,
            vocabulary=vocabulary,
        )
