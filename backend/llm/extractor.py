"""
AI 术语提取器（LangChain 实现）

原实现让模型输出 JSON 数组，再用手写正则从可能带围栏的文本里抠数组。
现改为 LangChain 结构化输出链：

    ChatPromptTemplate → ChatOllama/ChatOpenAI.with_structured_output(TermExtractionResult)
                        → RunnableLambda 补上下文句

「补上下文句」这一步放在链的后处理节点：模型只负责抽取，
确定性逻辑（正则分句回填）由链上的 RunnableLambda 承担。

对外接口 `TermExtractor.extract_terms()` 与迁移前完全一致。
"""

from __future__ import annotations

import logging
from typing import Optional

from llm.chains.agents import TermExtractionAgent

logger = logging.getLogger(__name__)


class TermExtractor:
    """
    基于 LangChain 结构化输出链的术语提取器
    从字幕上下文中提取专业词汇
    """

    def __init__(self, model: str = None):
        self.model = model
        self._agent = TermExtractionAgent(model=model)

    def is_available_cached(self) -> bool:
        """
        查 LLM 是否可用（结果缓存，避免每条字幕都探测一次）

        注意：探测结果一经确定为 False 会保持到进程重启；
        若用户在运行中才启动 Ollama，重启后端即可恢复。
        """
        return self._agent.is_available_cached()

    def extract_terms(
        self,
        text: str,
        domain: str = "",
        course_name: str = "",
        custom_terms: list[str] = None,
    ) -> list[dict]:
        """
        从字幕文本中提取术语

        Args:
            text: 字幕文本
            domain: 课程领域
            course_name: 课程名称
            custom_terms: 用户自定义术语列表

        Returns:
            术语列表 [{word, translation, definition, domain, context_sentence}]
        """
        return self._agent.extract_terms(
            text=text,
            domain=domain,
            course_name=course_name,
            custom_terms=custom_terms,
        )
