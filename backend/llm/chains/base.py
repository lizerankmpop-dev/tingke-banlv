"""
LangChain 智能体核心层 —— 基于 LCEL (LangChain Expression Language) 的链式编排

本模块是「听课伴侣」在第七章《智能应用开发》中要求的 LangChain 框架落地实现。

设计要点
--------
1. **模型统一抽象**：用 LangChain 的 `BaseChatModel` 屏蔽 Ollama 本地模型与
   OpenAI 兼容云端 API（DeepSeek / Kimi / 智谱 / OpenAI）的差异，业务代码无需感知。
2. **声明式提示词**：`ChatPromptTemplate` 取代手写的 `str.format()`，
   变量缺失会在构图期直接抛错，而不是在运行时静默产出畸形 prompt。
3. **结构化输出**：用 Pydantic 模型 + `with_structured_output()` 取代
   「让模型输出 JSON + 正则抠 JSON」的手工解析，消除代码块围栏、尾部逗号等脆弱环节。
4. **分支编排**：`RunnableBranch` 表达「本地词表优先、AI 兜底」的分级策略，
   命中词表时不发起任何 LLM 调用。
5. **降级不中断**：任一环节异常均返回 None / 空列表，与迁移前行为完全一致，
   前端无需任何改动。

依赖：langchain-core / langchain / langchain-ollama
"""

from __future__ import annotations

import logging
import os
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable, RunnableBranch, RunnableLambda, RunnablePassthrough

logger = logging.getLogger(__name__)


# =============================================================================
# 一、模型提供方抽象
# =============================================================================


def get_provider() -> str:
    """当前 LLM 提供方：ollama | cloud"""
    return os.environ.get("TINGKEBANLV_LLM_PROVIDER", "ollama").strip().lower()


def _build_ollama_model(temperature: float = 0.3, num_predict: int = 1024) -> BaseChatModel:
    """构造本地 Ollama 模型（LangChain 的 ChatOllama 封装）"""
    from langchain_ollama import ChatOllama

    model_name = os.environ.get("TINGKEBANLV_OLLAMA_MODEL", "qwen2.5:7b")
    return ChatOllama(
        model=model_name,
        temperature=temperature,
        num_predict=num_predict,
        # 保持与迁移前一致：调用失败时抛异常，由上层统一降级
        client_kwargs={"timeout": 180},
    )


def _build_cloud_model(temperature: float = 0.3, num_predict: int = 1024) -> BaseChatModel:
    """构造云端 OpenAI 兼容模型（DeepSeek / Kimi / 智谱 / OpenAI 等）"""
    from langchain_openai import ChatOpenAI

    base = os.environ["TINGKEBANLV_CLOUD_API_BASE"].strip().rstrip("/")
    key = os.environ["TINGKEBANLV_CLOUD_API_KEY"].strip()
    model = os.environ["TINGKEBANLV_CLOUD_MODEL"].strip()
    return ChatOpenAI(
        model=model,
        base_url=base,
        api_key=key,
        temperature=temperature,
        max_tokens=num_predict,
        timeout=180,
    )


def get_chat_model(
    temperature: float = 0.3, num_predict: int = 1024, model: str | None = None
) -> BaseChatModel:
    """
    按当前环境变量构造 LangChain 聊天模型实例。

    这是整个智能体层唯一的模型入口 —— 所有链都从这里取模型，
    从而实现「换模型不改业务代码」。

    Args:
        temperature: 采样温度
        num_predict: 最大生成 token 数
        model: 显式指定模型名（覆盖环境变量），主要用于测试

    Returns:
        LangChain BaseChatModel 实例

    Raises:
        RuntimeError: 云端配置不完整时抛出
    """
    if get_provider() == "cloud":
        if not cloud_configured():
            raise RuntimeError(
                "云端 LLM 配置不完整，需要设置 TINGKEBANLV_CLOUD_API_BASE / "
                "TINGKEBANLV_CLOUD_API_KEY / TINGKEBANLV_CLOUD_MODEL"
            )
        return _build_cloud_model(temperature=temperature, num_predict=num_predict)

    if model:
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=model,
            temperature=temperature,
            num_predict=num_predict,
            client_kwargs={"timeout": 180},
        )
    return _build_ollama_model(temperature=temperature, num_predict=num_predict)


def cloud_configured() -> bool:
    """云端配置是否齐全（不发实际请求）"""
    return bool(
        os.environ.get("TINGKEBANLV_CLOUD_API_BASE", "").strip()
        and os.environ.get("TINGKEBANLV_CLOUD_API_KEY", "").strip()
        and os.environ.get("TINGKEBANLV_CLOUD_MODEL", "").strip()
    )


def is_available() -> bool:
    """探测当前 provider 是否可用（结果不缓存，由调用方自行缓存）"""
    if get_provider() == "cloud":
        return cloud_configured()
    try:
        import ollama

        ollama.list()
        return True
    except Exception:
        return False


# =============================================================================
# 二、通用工具：容错装饰器
# =============================================================================


def safe_invoke(
    chain: Runnable,
    payload: dict,
    default=None,
    label: str = "chain",
    config: dict | None = None,
):
    """
    执行链并统一降级。

    迁移前各模块都是「try/except + logger.error + return None」的手写模式，
    这里收敛为一个装饰器式工具，保证所有链的失败语义一致：返回 default 而不抛出。

    Args:
        chain: 待执行的 LangChain Runnable
        payload: 链的输入字典
        default: 失败时的返回值
        label: 日志标签
        config: 可选的 LangChain config（如 checkpointer 用的 thread_id）

    Returns:
        链的输出，或 default
    """
    try:
        if config is not None:
            return chain.invoke(payload, config)
        return chain.invoke(payload)
    except Exception as e:  # noqa: BLE001 —— 业务要求：任何 LLM 失败都不应中断前端
        logger.error(f"{label} 执行失败: {e}")
        return default


# =============================================================================
# 三、结构化输出模型（取代手写正则解析 JSON）
# =============================================================================


def build_structured_chain(
    prompt: ChatPromptTemplate,
    schema: type,
    temperature: float = 0.3,
    num_predict: int = 1024,
    model: str | None = None,
) -> Runnable:
    """
    构造「声明式提示词 → 模型 → 结构化对象」的链。

    LangChain 的 `with_structured_output()` 会：
      - 在 prompt 中自动追加 JSON Schema 格式约束
      - 用模型原生的 structured output 能力（若支持）或解析回退保证返回合法对象
      - 直接产出 Pydantic 实例，彻底消除手写 `re.search(r'\\{...\\}')` 的脆弱性

    Args:
        prompt: 提示词模板
        schema: Pydantic 输出模型
        temperature: 采样温度
        num_predict: 最大生成 token 数
        model: 显式模型名

    Returns:
        可 invoke 的 Runnable，输入 dict、输出 schema 实例
    """
    # 模型在**调用期**才绑定，而非构造期。
    # 这样做的原因：构造期绑定会让链在创建时就实例化真实模型，
    # 既无法注入假模型做离线测试，也让「换模型」必须重建整条链。
    # 惰性绑定后，链本身是可复用的纯结构，模型由 RunnableLambda 在调用时解析。
    def _bind(payload: dict):
        chat_model = get_chat_model(
            temperature=temperature, num_predict=num_predict, model=model
        )
        structured = chat_model.with_structured_output(schema)
        # 先用模板渲染出消息，再交给结构化输出链
        messages = prompt.invoke(payload)
        return structured.invoke(messages)

    return RunnableLambda(_bind)


# =============================================================================
# 四、链式组合工具
# =============================================================================


def build_qa_chain(
    temperature: float = 0.4,
    num_predict: int = 1024,
    model: str | None = None,
) -> Runnable:
    """
    构建「对话式答疑」链。

    链路：问题 + 字幕上下文 → 提示词 → 模型 → 纯文本答案

    相比迁移前手写的 `messages=[...]`，此处用 ChatPromptTemplate 的
    MessagesPlaceholder 分离 system / human 角色，结构更清晰且可组合。

    注意：LangChain 1.x 的 StrOutputParser 会返回 `TextAccessor`（str 的子类）。
    它在多数场景与 str 等价，但为保证前端拿到的始终是标准字符串
    （API 响应会被 JSON 序列化），这里显式转成 `str`。

    与 `build_structured_chain` 一样，模型在调用期才绑定，
    以便离线测试时注入假模型。
    """
    from llm.chains.prompts import QA_PROMPT_TEMPLATE

    def _run(payload: dict) -> str:
        chat_model = get_chat_model(
            temperature=temperature, num_predict=num_predict, model=model
        )
        chain = QA_PROMPT_TEMPLATE | chat_model | StrOutputParser()
        return _to_plain_str(chain.invoke(payload))

    return RunnableLambda(_run)


def _to_plain_str(value: Any) -> str:
    """把 LangChain 的 TextAccessor 等富文本包装统一转成标准 str"""
    if isinstance(value, str):
        # TextAccessor 是 str 子类，但需转成精确的 str 以保证 JSON 序列化行为一致
        return str.__str__(value) if type(value) is not str else value
    if hasattr(value, "content"):
        return str(value.content)
    return str(value)


def build_runnable_branch(
    condition: Runnable,
    true_branch: Runnable,
    false_branch: Runnable,
) -> RunnableBranch:
    """
    构造分支链（表达「A 条件成立走 X，否则走 Y」）。

    「本地词表优先、AI 兜底」正是这一模式的典型场景：
    词表命中 → 走本地检索（零延迟零成本）；未命中 → 走 LLM 讲解。
    """
    return RunnableBranch(
        (condition, true_branch),
        false_branch,
    )


__all__ = [
    "get_provider",
    "get_chat_model",
    "cloud_configured",
    "is_available",
    "safe_invoke",
    "build_structured_chain",
    "build_qa_chain",
    "build_runnable_branch",
    "BaseChatModel",
    "ChatPromptTemplate",
    "Runnable",
    "RunnableBranch",
    "RunnableLambda",
    "RunnablePassthrough",
]
