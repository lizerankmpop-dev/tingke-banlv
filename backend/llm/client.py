"""
LLM 统一入口（LangChain 版）

本模块保留原 `client.py` 的对外接口（`chat` / `is_available` / `get_provider`），
但底层已切换为 LangChain 的 `BaseChatModel` 抽象：

- 原实现：手写 `ollama.chat()` 与 `urllib` 请求，provider 分支散落在函数内部；
- 现实现：统一构造 LangChain 聊天模型对象，provider 差异由
  `ChatOllama` / `ChatOpenAI` 两个适配器吸收。

这样，任何需要「直接对话」而不走结构化链的调用方，
也能享受 LangChain 带来的统一模型抽象与可观测性。
"""

from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage

from llm.chains.base import (
    cloud_configured,
    get_chat_model,
    get_provider,
    is_available,
)

logger = logging.getLogger(__name__)

__all__ = [
    "chat",
    "is_available",
    "get_provider",
    "cloud_configured",
    "get_chat_model",
]


def chat(messages: list[dict], temperature: float = 0.3, num_predict: int = 1024) -> str | None:
    """
    统一对话接口（LangChain 实现）

    Args:
        messages: OpenAI 格式消息列表 [{role, content}]
        temperature: 采样温度
        num_predict: 最大生成 token 数

    Returns:
        assistant 回复文本；任何失败返回 None
    """
    try:
        model = get_chat_model(temperature=temperature, num_predict=num_predict)

        langchain_messages = []
        for m in messages:
            role = (m.get("role") or "user").lower()
            content = m.get("content") or ""
            if role == "system":
                langchain_messages.append(SystemMessage(content=content))
            elif role == "assistant":
                from langchain_core.messages import AIMessage

                langchain_messages.append(AIMessage(content=content))
            else:
                langchain_messages.append(HumanMessage(content=content))

        response = model.invoke(langchain_messages)
        text = response.content if hasattr(response, "content") else str(response)
        # LangChain 1.x 的 content 可能是 TextAccessor（str 子类），
        # 显式转成标准 str，保证调用方拿到的类型与迁移前完全一致
        if not isinstance(text, str) or type(text) is not str:
            text = str.__str__(text) if isinstance(text, str) else str(text)
        return text.strip() if text else None

    except Exception as e:  # noqa: BLE001 —— 保持与迁移前一致的失败语义
        logger.error(f"LLM 调用失败: {e}")
        return None
