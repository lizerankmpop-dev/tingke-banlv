"""
LangChain 智能体链集合

本包是「听课伴侣」的 LangChain 框架层，把原本手写的 prompt 拼接 + JSON 正则解析，
改造为声明式的 LCEL 链，并在此之上构建了 LangChain 的三种关键范式：

- **Chain（LCEL 编排）** —— 术语提取 / 单词讲解 / 课后摘要
- **Agent（自主决策）** —— 对话答疑，模型运行时自主调用工具
- **RAG（检索增强）** —— 字幕向量索引，突破固定窗口限制
- **Memory（会话记忆）** —— checkpointer 按 thread_id 隔离多轮对话

模块划分：
    base.py        模型提供方抽象（ChatOllama / ChatOpenAI）、容错降级工具
    prompts.py     ChatPromptTemplate 提示词模板
    schemas.py     Pydantic 结构化输出模型
    agents.py      四条业务链的 LCEL 编排
    agent_tools.py Agent 工具定义 + create_agent 组装
    rag.py         字幕向量索引与检索
    memory.py      会话持久化（checkpointer）
"""

from llm.chains.agent_tools import (
    build_qa_agent,
    lookup_term,
    match_course_terms,
    search_subtitle_context,
)
from llm.chains.agents import (
    ChatQAAgent,
    LectureSummaryAgent,
    TermExtractionAgent,
    WordExplanationAgent,
)
from llm.chains.base import (
    build_qa_chain,
    build_runnable_branch,
    build_structured_chain,
    cloud_configured,
    get_chat_model,
    get_provider,
    is_available,
    safe_invoke,
)
from llm.chains.memory import (
    CheckpointStore,
    InMemoryCheckpointStore,
    get_checkpoint_store,
    make_thread_config,
)
from llm.chains.rag import (
    SubtitleChunk,
    SubtitleKnowledgeBase,
    make_retrieve_tool,
)

__all__ = [
    # 智能体
    "TermExtractionAgent",
    "WordExplanationAgent",
    "LectureSummaryAgent",
    "ChatQAAgent",
    # Agent 工具
    "build_qa_agent",
    "lookup_term",
    "match_course_terms",
    "search_subtitle_context",
    # RAG
    "SubtitleChunk",
    "SubtitleKnowledgeBase",
    "make_retrieve_tool",
    # Memory
    "CheckpointStore",
    "InMemoryCheckpointStore",
    "get_checkpoint_store",
    "make_thread_config",
    # 模型与工具
    "get_chat_model",
    "get_provider",
    "cloud_configured",
    "is_available",
    "safe_invoke",
    # 链构造
    "build_structured_chain",
    "build_qa_chain",
    "build_runnable_branch",
]
