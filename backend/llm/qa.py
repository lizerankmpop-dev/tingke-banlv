"""
AI 对话式答疑（LangChain 实现）

原实现手写了 prompt 拼接与 messages 构造，现改为 LangChain LCEL 链：

    上下文组装 → ChatPromptTemplate → ChatOllama/ChatOpenAI → StrOutputParser

对外接口 `ChatQA.answer()` 与迁移前完全一致，API 路由无需改动。
"""

from __future__ import annotations

import logging

from llm.chains.agents import ChatQAAgent

logger = logging.getLogger(__name__)


class ChatQA:
    """基于字幕上下文的对话答疑（LangChain 链实现）"""

    def __init__(self, model: str = None):
        self._agent = ChatQAAgent(model=model)

    def answer(
        self,
        question: str,
        context: str,
        domain: str = "",
        course_name: str = "",
        custom_terms: list[str] = None,
    ) -> str | None:
        """
        回答学生提问

        Args:
            question: 学生的问题
            context: 最近字幕文本（时间正序）
            domain: 课程领域
            course_name: 课程名称
            custom_terms: 自定义术语

        Returns:
            回答文本（Markdown 由前端按纯文本渲染），失败返回 None
        """
        return self._agent.answer(
            question=question,
            context=context,
            domain=domain,
            course_name=course_name,
            custom_terms=custom_terms,
        )
