"""
AI 单词讲解器（LangChain 实现）

原实现的分级策略是手写 if/else：「查内置词表 → 命中就返回 → 未命中才调 LLM」。
现改为 LangChain 的 `RunnableBranch`：

    条件（词表是否命中）
      ├── True  → 本地词库分支（零延迟、零 LLM 成本）
      └── False → LLM 讲解分支（提示词 → 模型 → 结构化讲解）

结构化输出由 Pydantic schema 保证，不再依赖手写 JSON 正则解析。

对外接口 `WordExplainer.explain()` 与迁移前完全一致。
"""

from __future__ import annotations

import logging

from llm.chains.agents import WordExplanationAgent

logger = logging.getLogger(__name__)


class WordExplainer:
    """单词讲解器（LangChain RunnableBranch 分级实现；带内存缓存）"""

    def __init__(self, model: str = None):
        self._agent = WordExplanationAgent(model=model)

    def explain(
        self,
        word: str,
        domain: str = "",
        course_name: str = "",
        context_sentence: str = "",
    ) -> dict | None:
        """
        生成单词的上下文讲解

        流程：
            1. 查缓存
            2. RunnableBranch 分级：词表命中 → 本地返回；未命中 → LLM 讲解

        Returns:
            {translation, meaning_in_context, example, related[], source} 或 None
        """
        return self._agent.explain(
            word=word,
            domain=domain,
            course_name=course_name,
            context_sentence=context_sentence,
        )
