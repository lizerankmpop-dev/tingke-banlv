"""
AI 对话式答疑（LangChain Agent + RAG + Memory 三位一体）

答疑是四条 AI 能力中最适合 Agent 形态的一个：学生的问题千差万别，
该不该查术语库、要不要翻字幕、查几个词，全由模型在运行时判断。

本模块整合了 LangChain 的三种关键能力：

| 能力 | 作用 | 实现 |
|---|---|---|
| **Agent** | 模型自主决定调用哪些工具 | `create_agent()` + 4 个工具 |
| **RAG** | 能回答整节课的任意内容，而非最近几句 | 字幕向量索引 + `search_subtitles` 工具 |
| **Memory** | 能理解「那它和上一节课讲的 XX 呢」这类追问 | `RunnableWithMessageHistory` + session 隔离 |

三者的关系：Agent 决定「要不要查」，RAG 提供「查什么、查到了什么」，
Memory 保证「上一轮说了什么」不丢。

对外接口与迁移前完全一致，API 路由与前端零改动。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ChatQA:
    """基于字幕上下文的对话答疑（LangChain Agent / RAG / Memory）"""

    def __init__(
        self,
        model: str = None,
        use_agent: bool = True,
        use_rag: bool = True,
        use_memory: bool = True,
    ):
        """
        Args:
            model: 显式模型名（None 表示用环境变量配置）
            use_agent: True 走 Agent 模式；False 走纯 LCEL 链模式
            use_rag: True 启用字幕向量检索（Agent 自主调用）
            use_memory: True 启用多轮会话记忆
        """
        self.model = model
        self.use_agent = use_agent
        self.use_rag = use_rag
        self.use_memory = use_memory

        self._agent = None
        self._chain = None
        self._checkpointer = None
        self._available: bool | None = None
        self._kb = None

    # ---- 懒加载：避免构造期就实例化真实模型 ----

    def _get_knowledge_base(self):
        """获取字幕知识库（首次调用时创建）"""
        if self._kb is None:
            from llm.chains.rag import SubtitleKnowledgeBase

            self._kb = SubtitleKnowledgeBase()
        return self._kb

    def _get_agent(self):
        if self._agent is None:
            from llm.chains.agent_tools import build_qa_agent

            kb = self._get_knowledge_base() if self.use_rag else None
            self._agent = build_qa_agent(
                model=self.model,
                knowledge_base=kb,
                checkpointer=self._get_checkpointer() if self.use_memory else None,
            )
        return self._agent

    def _get_checkpointer(self):
        """获取持久化存储（Agent 的会话记忆由它托管）"""
        if self._checkpointer is None:
            from llm.chains.memory import get_checkpoint_store

            self._checkpointer = get_checkpoint_store()
        return self._checkpointer

    def _get_chain(self):
        if self._chain is None:
            from llm.chains.base import build_qa_chain

            self._chain = build_qa_chain(model=self.model)
        return self._chain

    # ---- 字幕索引 ----

    def index_subtitles(
        self,
        subtitle_text: str,
        course_name: str = "",
        domain: str = "",
        force: bool = False,
    ) -> int:
        """
        把字幕建成向量索引，供 Agent 检索。

        Args:
            subtitle_text: 字幕全文
            course_name: 课程名
            domain: 课程领域
            force: True 强制重建

        Returns:
            索引片段数（0 表示未启用 RAG 或索引失败）
        """
        if not self.use_rag:
            return 0
        try:
            kb = self._get_knowledge_base()
            return kb.build(
                subtitle_text,
                course_name=course_name,
                domain=domain,
                force=force,
            )
        except Exception as e:  # noqa: BLE001 —— 索引失败不应影响答疑主流程
            logger.warning(f"字幕索引构建失败: {e}")
            return 0

    # ---- 对外接口（与迁移前逐字一致） ----

    def answer(
        self,
        question: str,
        context: str,
        domain: str = "",
        course_name: str = "",
        custom_terms: list[str] = None,
        session_id: str = "",
    ) -> str | None:
        """
        回答学生提问

        Args:
            question: 学生的问题
            context: 最近字幕文本（时间正序）
            domain: 课程领域
            course_name: 课程名称
            custom_terms: 自定义术语
            session_id: 会话标识（启用记忆时用于隔离不同学生的对话历史；
                传空字符串则本次调用不读写历史）

        Returns:
            回答文本（Markdown 由前端按纯文本渲染），失败返回 None
        """
        question = (question or "").strip()
        if not question:
            return None

        # LLM 可用性检查（结果缓存，与迁移前一致）
        from llm.chains.base import is_available

        if self._available is None:
            self._available = is_available()
        if not self._available:
            logger.warning("LLM 不可用，无法答疑")
            return None

        payload = {
            "question": question,
            "context": context or "",
            "course_name": course_name or "未知课程",
            "domain": domain or "通用",
            "custom_terms": ", ".join(custom_terms) if custom_terms else "无",
        }

        # 顺带把字幕增量入索引（Agent 需要时自己检索）
        if self.use_rag and context and context.strip():
            self.index_subtitles(
                context, course_name=course_name, domain=domain
            )

        if self.use_agent:
            return self._answer_by_agent(payload, session_id)
        return self._answer_by_chain(payload)

    # ---- Agent 模式 ----

    def _answer_by_agent(self, payload: dict, session_id: str = "") -> str | None:
        """走 LangChain Agent：模型自主决定是否调用工具"""
        from llm.chains.base import safe_invoke
        from llm.chains.agent_tools import AGENT_SYSTEM_PROMPT

        # system prompt 里的课程信息需要按本次请求渲染
        rendered_system = AGENT_SYSTEM_PROMPT.format(
            course_name=payload["course_name"],
            domain=payload["domain"],
            custom_terms=payload["custom_terms"],
            context=payload["context"][-5000:]
            if payload["context"].strip()
            else "（暂无字幕，请结合课程领域通用知识回答）",
        )

        try:
            runnable = self._get_agent()
            invoke_payload = {
                "messages": [
                    {"role": "system", "content": rendered_system},
                    {"role": "user", "content": payload["question"]},
                ]
            }
            # 记忆：checkpointer 按 thread_id 隔离会话，
            # config 必须作为 invoke 的第二个参数传入（不能放进 payload）
            invoke_config = None
            if self.use_memory and session_id:
                from llm.chains.memory import make_thread_config

                invoke_config = make_thread_config(session_id)
        except Exception as e:  # noqa: BLE001 —— Agent 构建失败时降级到链模式
            logger.warning(f"Agent 构建失败，降级为链模式: {e}")
            self.use_agent = False
            return self._answer_by_chain(payload)

        if invoke_config is not None:
            result = safe_invoke(
                runnable,
                invoke_payload,
                default=None,
                label="答疑 Agent",
                config=invoke_config,
            )
        else:
            result = safe_invoke(
                runnable,
                invoke_payload,
                default=None,
                label="答疑 Agent",
            )
        if result is None:
            # Agent 失败时回退到链模式，保证答疑不彻底不可用
            logger.warning("Agent 答疑失败，回退链模式")
            return self._answer_by_chain(payload)

        text = self._extract_text(result)
        return text if text else self._answer_by_chain(payload)

    @staticmethod
    def _extract_text(result: Any) -> str:
        """
        从 Agent 返回值中提取最终回答文本。

        注意：LangChain 1.x 的 Agent 返回的是 **dict**（形如 {"messages": [...]}），
        而非 AIMessage 对象 —— 早期版本返回的是对象。
        两种形态都要兼容，否则会静默取到空串（答疑变成「什么都没说」）。
        """
        if isinstance(result, dict):
            messages = result.get("messages") or []
        else:
            messages = getattr(result, "messages", None) or []

        for msg in reversed(messages):
            content = getattr(msg, "content", "")
            if getattr(msg, "tool_calls", None):
                continue
            if isinstance(content, str) and content.strip():
                return content.strip()
            # 部分模型返回 content 为列表
            if isinstance(content, list):
                parts = [
                    c.get("text", "")
                    for c in content
                    if isinstance(c, dict) and c.get("text")
                ]
                if parts:
                    return "".join(parts).strip()

        # 兜底：直接取 content
        content = (
            result.get("content")
            if isinstance(result, dict)
            else getattr(result, "content", None)
        )
        if isinstance(content, str) and content.strip():
            return content.strip()
        return ""

    # ---- 链模式 ----

    def _answer_by_chain(self, payload: dict) -> str | None:
        """走纯 LCEL 链：无工具调用，延迟更低"""
        from llm.chains.base import safe_invoke

        context = payload["context"]
        result = safe_invoke(
            self._get_chain(),
            {
                "course_name": payload["course_name"],
                "domain": payload["domain"],
                "custom_terms": payload["custom_terms"],
                "context": context[-5000:]
                if context.strip()
                else "（暂无字幕，请结合课程领域通用知识回答）",
                "question": payload["question"],
            },
            default=None,
            label="答疑链",
        )
        return result if result else None
