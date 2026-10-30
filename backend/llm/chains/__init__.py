"""
LangChain 智能体链集合

本包是「听课伴侣」的 LangChain 框架层，把原本手写的 prompt 拼接 + JSON 正则解析，
改造为声明式的 LCEL 链。

模块划分：
    base.py     —— 模型提供方抽象（ChatOllama / ChatOpenAI）、容错降级工具
    prompts.py  —— ChatPromptTemplate 提示词模板
    schemas.py  —— Pydantic 结构化输出模型
    agents.py   —— 四条业务链的 LCEL 编排与智能体类
"""

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

__all__ = [
    # 智能体
    "TermExtractionAgent",
    "WordExplanationAgent",
    "LectureSummaryAgent",
    "ChatQAAgent",
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
