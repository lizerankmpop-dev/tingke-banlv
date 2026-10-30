"""
AI 对话式答疑（LangChain Agent + LCEL 链 双模式）

答疑是四条 AI 能力中最适合 Agent 形态的一个：学生的问题千差万别，
该不该查术语库、要不要翻字幕、查几个词，全由模型在运行时判断。

因此本模块提供两个实现，按场景切换：

- **Agent 模式（默认）**：`create_agent()` + 三个工具
  （查术语库 / 匹配术语 / 检索字幕上下文），模型自主决定调用与否。
- **链模式**：`ChatPromptTemplate → 模型 → StrOutputParser` 的纯 LCEL 链，
  无工具调用，延迟更低、更可控。

两条路径的对外接口完全一致，调用方（api/routes.py）无需任何改动。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ChatQA:
    """基于字幕上下文的对话答疑（LangChain Agent / 链 双模式）"""

    def __init__(self, model: str = None, use_agent: bool = True):
        """
        Args:
            model: 显式模型名（None 表示用环境变量配置）
            use_agent: True 走 Agent 模式；False 走纯 LCEL 链模式
        """
        self.model = model
        self.use_agent = use_agent
        self._agent = None
        self._chain = None
        self._available: bool | None = None

    # ---- 懒加载：避免构造期就实例化真实模型 ----

    def _get_agent(self):
        if self._agent is None:
            from llm.chains.agent_tools import build_qa_agent

            self._agent = build_qa_agent(model=self.model)
        return self._agent

    def _get_chain(self):
        if self._chain is None:
            from llm.chains.base import build_qa_chain

            self._chain = build_qa_chain(model=self.model)
        return self._chain

    # ---- 对外接口（与迁移前逐字一致） ----

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

        if self.use_agent:
            return self._answer_by_agent(payload)
        return self._answer_by_chain(payload)

    # ---- Agent 模式 ----

    def _answer_by_agent(self, payload: dict) -> str | None:
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
            agent = self._get_agent()
        except Exception as e:  # noqa: BLE001 —— Agent 构建失败时降级到链模式
            logger.warning(f"Agent 构建失败，降级为链模式: {e}")
            self.use_agent = False
            return self._answer_by_chain(payload)

        result = safe_invoke(
            agent,
            {
                "messages": [
                    {"role": "system", "content": rendered_system},
                    {"role": "user", "content": payload["question"]},
                ]
            },
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

        Agent 返回的是 AIMessage，其中可能包含 tool_call 中间态；
        最终回答是最后一条不含 tool_calls 的 AIMessage。
        """
        messages = getattr(result, "messages", None)
        if messages:
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
        content = getattr(result, "content", None)
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
