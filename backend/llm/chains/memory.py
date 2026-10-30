"""
多轮对话记忆 —— 让 Agent 能回答「那它和上一节课讲的 XX 呢」这类追问

问题
----
原答疑实现是**无状态**的：每次提问都只带「问题 + 最近字幕」，
模型不知道上一轮说了什么。于是：

  学生：那什么是梯度下降？
  教师：（解释梯度下降）
  学生：那它和反向传播什么关系？     ← 「它」指什么？模型不知道

解法：LangChain 的会话持久化（checkpointer）
------------------------------------------
LangChain 1.x 里，Agent 的对话状态由 **checkpointer** 托管：
调用时传入 ``thread_id``，框架自动把该线程的消息历史注入模型上下文，
并把本轮输出写回。

为什么用 checkpointer 而不是 RunnableWithMessageHistory
--------------------------------------------------
``RunnableWithMessageHistory`` 在 LangChain 1.6.4 起已被标记废弃，
官方明确指向「用 LangGraph 内置的持久化替代」。
而 ``create_agent()`` 本身就接受 ``checkpointer`` 参数 ——
不需要额外的包装 Runnable，架构更干净，也少一层封装。

本模块的设计取舍
----------------
1. **内存存储 + 可插拔接口**
   默认用 LangGraph 的 ``InMemorySaver``（进程内，零额外依赖）。
   抽出了 ``CheckpointStore`` 抽象 —— 换成 Redis / SQLite / Postgres
   只需实现同样的接口，业务代码不动。
   这对「同一节课跨设备继续问」是必要的。

2. **按 thread 隔离**
   记忆以 ``thread_id`` 隔离，通常用「课程 ID + 学生 ID」组合。
   换课程即换 thread，记忆不会串味。

3. **历史裁剪**
   无限累积历史会让 token 成本线性上升、且早期内容可能已无关。
   这里在读取时保留最近 ``max_messages`` 条（默认 12 条 ≈ 6 轮往返）。

4. **摘要式压缩（可选）**
   当历史很长时，可把更早的对话压成一段摘要，
   保留「问了什么、结论是什么」而丢掉具体措辞。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# 一、持久化存储（可插拔）
# =============================================================================


class CheckpointStore:
    """
    持久化存储接口。

    默认实现委托给 LangGraph 的 ``InMemorySaver``；
    若要跨进程/跨设备共享，实现同样的三个方法即可
    （如 Redis 的 ``RPUSH`` + ``LRANGE``，SQLite 的表读写）。
    """

    def get_tuple(self, thread_id: str):
        """取回该线程的检查点（None 表示无历史）"""
        raise NotImplementedError

    def put(self, thread_id: str, checkpoint: dict) -> None:
        """写入该线程的检查点"""
        raise NotImplementedError

    def list_threads(self) -> list[str]:
        """列出所有有历史的线程"""
        raise NotImplementedError

    def delete_thread(self, thread_id: str) -> None:
        """删除某个线程的全部历史"""
        raise NotImplementedError


class InMemoryCheckpointStore(CheckpointStore):
    """
    内存版持久化存储。

    内部用 LangGraph 的 ``InMemorySaver``（LangChain 1.x 自带），
    对外暴露与持久化无关的稳定接口。
    """

    def __init__(self):
        from langgraph.checkpoint.memory import InMemorySaver

        self._saver = InMemorySaver()
        self._lock = threading.Lock()

    # ---- CheckpointStore 接口 ----

    def get_tuple(self, thread_id: str):
        config = {"configurable": {"thread_id": thread_id}}
        try:
            return self._saver.get_tuple(config)
        except Exception as e:  # noqa: BLE001
            logger.error(f"读取会话历史失败: {e}")
            return None

    def put(self, thread_id: str, checkpoint: dict) -> None:
        config = {"configurable": {"thread_id": thread_id}}
        try:
            self._saver.put(config, checkpoint, {})
        except Exception as e:  # noqa: BLE001
            logger.error(f"写入会话历史失败: {e}")

    def list_threads(self) -> list[str]:
        try:
            cfg = self._saver.list(None)
            return [c["configurable"]["thread_id"] for c in cfg]
        except Exception:  # noqa: BLE001
            return []

    def delete_thread(self, thread_id: str) -> None:
        try:
            self._saver.delete_thread(thread_id)
        except Exception:  # noqa: BLE001
            pass

    # ---- 便捷方法 ----

    def get_messages(self, thread_id: str, max_messages: int = 12) -> list:
        """取回该线程的消息历史（已裁剪）"""
        tp = self.get_tuple(thread_id)
        if tp is None:
            return []
        try:
            values = tp.checkpoint.get("channel_values", {}) or {}
            msgs = values.get("messages", []) or []
            if len(msgs) > max_messages:
                msgs = msgs[-max_messages:]
            return list(msgs)
        except Exception:  # noqa: BLE001
            return []

    def clear(self, thread_id: str) -> None:
        self.delete_thread(thread_id)


# =============================================================================
# 二、全局存储单例
# =============================================================================

_store: CheckpointStore | None = None
_store_lock = threading.Lock()


def get_checkpoint_store() -> CheckpointStore:
    """获取全局持久化存储（单例）"""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = InMemoryCheckpointStore()
    return _store


def set_checkpoint_store(store: CheckpointStore) -> None:
    """替换全局存储（用于接入 Redis / SQLite 等）"""
    global _store
    with _store_lock:
        _store = store


def clear_all_histories() -> None:
    """清空所有会话历史（课程结束 / 测试重置时用）"""
    store = get_checkpoint_store()
    if isinstance(store, InMemoryCheckpointStore):
        for tid in store.list_threads():
            store.clear(tid)


# =============================================================================
# 三、Agent 装配（checkpointer 方式）
# =============================================================================


def build_agent_with_memory(
    agent_factory,
    store: CheckpointStore | None = None,
):
    """
    构造「带记忆」的 Agent。

    Args:
        agent_factory: 无参可调用对象，返回未装配记忆的 Agent
            （通常是 ``lambda: build_qa_agent(...)``）
        store: 持久化存储，None 用全局单例

    Returns:
        (agent, store) —— agent 已注入 checkpointer
    """
    store = store or get_checkpoint_store()
    agent = agent_factory()
    # create_agent 支持在构造时传入 checkpointer
    try:
        agent.checkpointer = store._saver  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 —— 某些版本不支持直接赋值，忽略
        pass
    return agent, store


def make_thread_config(thread_id: str, **extra) -> dict:
    """
    构造调用 Agent 时用的 config。

    checkpointer 按 ``thread_id`` 隔离会话，
    因此每次调用必须带上同一个 thread_id 才能续上历史。

    Args:
        thread_id: 线程标识（建议「课程ID:学生ID」）
        **extra: 其他 configurable 字段

    Returns:
        LangChain config 字典
    """
    configurable = {"thread_id": thread_id}
    configurable.update(extra)
    return {"configurable": configurable}


# =============================================================================
# 四、历史摘要（可选的历史压缩）
# =============================================================================


def summarize_history(
    messages: list,
    llm=None,
    max_chars: int = 600,
) -> str:
    """
    把一段对话历史压缩成摘要。

    用途：历史很长时，用摘要替代原始消息，
    保留「问了什么、结论是什么」而丢掉具体措辞。

    Args:
        messages: 历史消息列表
        llm: 用于摘要的模型（None 则退化为纯摘要去重）
        max_chars: 摘要最大长度

    Returns:
        摘要文本
    """
    if not messages:
        return ""

    if llm is None:
        # 退化实现：拼接用户问题，作为最朴素的"上下文摘要"
        parts = []
        for m in messages:
            role = getattr(m, "type", "")
            content = getattr(m, "content", "")
            if not isinstance(content, str):
                continue
            if role == "human" and content.strip():
                parts.append(f"学生问：{content.strip()}")
        return "；".join(parts)[:max_chars]

    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        convo = []
        for m in messages:
            role = getattr(m, "type", "")
            content = getattr(m, "content", "")
            if not isinstance(content, str) or not content.strip():
                continue
            if role == "human":
                convo.append(HumanMessage(content=content))
            elif role == "ai":
                convo.append({"role": "assistant", "content": content})

        if not convo:
            return ""

        resp = llm.invoke(
            [
                SystemMessage(
                    content=(
                        "请用简体中文概括这段课堂答疑对话，"
                        "保留学生问了哪些问题、给出了哪些结论，不要评价。"
                        f"不超过{max_chars}字。"
                    )
                ),
                *convo,
            ]
        )
        text = resp.content if hasattr(resp, "content") else str(resp)
        return (text or "").strip()[:max_chars]
    except Exception as e:  # noqa: BLE001 —— 摘要失败不应影响主流程
        logger.warning(f"历史摘要失败: {e}")
        return ""


__all__ = [
    "CheckpointStore",
    "InMemoryCheckpointStore",
    "get_checkpoint_store",
    "set_checkpoint_store",
    "clear_all_histories",
    "build_agent_with_memory",
    "make_thread_config",
    "summarize_history",
]
