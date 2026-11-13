"""
AI 专业词汇生成器（云端主通道 + 本地 Ollama 降级）

定位
----
课程创建页的「AI 生成专业词汇」按钮背后的一次性生成能力：根据课程名，
生成 15 个该课程可能出现的专业术语，交给前端展示/勾选，再由前端写入
现有 `customTerms`。**后端不落库**，避免用户没确认的词污染课程配置。

为什么独立于术语提取链
----------------------
`extractor.py` 处理的是「已有字幕 → 抽取术语」，输入是长文本；
本模块处理的是「只有课程名 → 生成候选术语」，输入极短、输出是纯词表。
两者提示词与输出 schema 完全不同，放在一起只会让结构化输出互相迁就。

双通道策略
----------
1. 云端（华为云 / OpenAI 兼容接口）为**主通道**，配置只读
   `TINGKEBANLV_CLOUD_API_BASE / TINGKEBANLV_CLOUD_API_KEY / TINGKEBANLV_CLOUD_MODEL`。
2. 云端未配置、抛错、超时或解析失败时，自动降级本地 Ollama
   （模型名沿用 `TINGKEBANLV_OLLAMA_MODEL`，与 `llm/chains/base.py` 一致）。
3. 两条通道都不可用 → 抛 `TermGenerationUnavailable`，由路由转成 503。
   该异常只影响本次生成，不触碰 ASR 主链路。

超时
----
总预算 8 秒，其中云端子预算 5 秒。这样云端超时后仍有约 3 秒可以降级到
Ollama —— 若云端独占全部 8 秒，超时即等于双通道失败，降级形同虚设。
`asyncio.to_thread` 包住同步的 LangChain 调用，`asyncio.wait_for` 负责在
事件循环层超时。注意：线程本身无法被强制取消，因此超时后工作线程可能继续
运行到 HTTP 客户端自身的超时；但它**不会阻塞事件循环**，也不会影响「开始识别」。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field, field_validator

from llm.chains.base import (
    _build_cloud_model,
    _build_ollama_model,
    cloud_configured,
)

logger = logging.getLogger(__name__)

# 一次生成的目标条数 / 上限。模型多吐时截断，少吐时按实际数量返回。
MAX_TERMS = 15

# 总超时（秒）。云端与本地降级共享同一 deadline，不是每通道各 8 秒。
TOTAL_TIMEOUT_SECONDS = 8.0

# 云端子预算（秒）。必须小于总预算，否则云端一超时就没有时间降级 Ollama。
CLOUD_TIMEOUT_SECONDS = 5.0


# =============================================================================
# 一、提示词
# =============================================================================

TERM_GENERATION_SYSTEM = (
    "You are a precise curriculum terminology assistant. "
    "Always respond with valid JSON only."
)

TERM_GENERATION_HUMAN = """You are helping a Chinese student prepare for an upcoming course.

Course name: {course_name}
Domain: {domain}
Course language: {language}

Task: propose exactly {term_count} important technical or domain-specific terms for this course.

Instructions:
1. Every term must be directly relevant to the course name and domain.
2. Chinese and English terms are both acceptable.
3. Do not repeat a term (case-insensitive) and never return empty strings.
4. Prefer terms that a student would actually need to look up during class.
5. Respond ONLY with a JSON object in this exact shape:
   {{"terms": ["term one", "术语二", "..."]}}"""

TERM_GENERATION_PROMPT_TEMPLATE = ChatPromptTemplate.from_messages(
    [
        ("system", TERM_GENERATION_SYSTEM),
        ("human", TERM_GENERATION_HUMAN),
    ]
)


# =============================================================================
# 二、结构化输出与归一化
# =============================================================================


class TermGenerationUnavailable(RuntimeError):
    """两条通道路都不可用时抛出；路由据此返回 503"""


def _normalize_terms(values) -> list[str]:
    """
    去空串、去重（大小写不敏感）、截断到 MAX_TERMS。

    模型偶尔会把数组写成换行/逗号分隔的字符串，或塞进对象数组，
    这里统一容错，避免一条不规范输出直接让整次生成失败。
    """
    if isinstance(values, str):
        values = re.split(r"[\n,，、;；]+", values)
    if not isinstance(values, (list, tuple)):
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, dict):
            value = value.get("term") or value.get("word") or value.get("name") or ""
        term = str(value).strip().strip('"').strip("'").strip()
        if not term:
            continue
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(term)
        if len(normalized) >= MAX_TERMS:
            break
    return normalized


class GeneratedTerms(BaseModel):
    """专业词汇生成的结构化输出"""

    terms: list[str] = Field(default_factory=list, description="去重后的专业术语列表")

    @field_validator("terms", mode="before")
    @classmethod
    def _coerce_terms(cls, value) -> list[str]:
        return _normalize_terms(value)


# =============================================================================
# 三、单通道调用（同步，由 asyncio.to_thread 执行）
# =============================================================================


def _make_payload(course_name: str, domain: str, language: str) -> dict:
    return {
        "course_name": course_name,
        "domain": (domain or "").strip() or "通用",
        "language": (language or "").strip() or "不限",
        "term_count": MAX_TERMS,
    }


def _run_term_chain(model, payload: dict) -> list[str]:
    """执行「提示词 → 模型 → 结构化词汇列表」，并做统一归一化"""
    structured = model.with_structured_output(GeneratedTerms)
    messages = TERM_GENERATION_PROMPT_TEMPLATE.invoke(payload)
    result = structured.invoke(messages)

    if isinstance(result, dict):
        raw_terms = result.get("terms", [])
    else:
        raw_terms = getattr(result, "terms", [])

    terms = _normalize_terms(raw_terms)
    if not terms:
        # 空结果视为解析失败，交给上层降级 —— 返回空列表对用户没有价值
        raise ValueError("模型未返回有效术语")
    return terms


def _call_cloud(payload: dict) -> list[str]:
    """云端主通道（华为云 OpenAI 兼容接口）"""
    if not cloud_configured():
        raise TermGenerationUnavailable(
            "云端 LLM 配置不完整，需要设置 TINGKEBANLV_CLOUD_API_BASE / "
            "TINGKEBANLV_CLOUD_API_KEY / TINGKEBANLV_CLOUD_MODEL"
        )
    model = _build_cloud_model(temperature=0.2, num_predict=1024)
    return _run_term_chain(model, payload)


def _call_ollama(payload: dict) -> list[str]:
    """本地 Ollama 降级通道"""
    model = _build_ollama_model(temperature=0.2, num_predict=1024)
    return _run_term_chain(model, payload)


# =============================================================================
# 四、对外入口
# =============================================================================


def _build_result(terms: list[str], provider: str, started: float) -> dict:
    return {
        "terms": _normalize_terms(terms),
        "provider": provider,
        "elapsedMs": int((time.monotonic() - started) * 1000),
    }


async def generate_course_terms(
    course_name: str,
    domain: str = "",
    language: str = "",
    timeout: float | None = None,
    cloud_timeout: float | None = None,
) -> dict:
    """
    根据课程名生成专业术语。

    Args:
        course_name: 课程名称
        domain: 课程领域（可选）
        language: 课程语种（可选）
        timeout: 总超时（秒），默认 TOTAL_TIMEOUT_SECONDS；测试可注入更短预算
        cloud_timeout: 云端子预算（秒），默认 CLOUD_TIMEOUT_SECONDS；
                       剩余时间自动留给 Ollama 降级

    Returns:
        {"terms": [...], "provider": "cloud" | "ollama", "elapsedMs": int}

    Raises:
        ValueError: 课程名为空
        TermGenerationUnavailable: 两条通道都不可用或总超时耗尽
    """
    name = (course_name or "").strip()
    if not name:
        raise ValueError("课程名不能为空")

    budget = TOTAL_TIMEOUT_SECONDS if timeout is None else max(0.05, float(timeout))
    cloud_budget = (
        CLOUD_TIMEOUT_SECONDS if cloud_timeout is None else max(0.0, float(cloud_timeout))
    )
    # 云端不能吃掉全部预算，否则失败后没有时间降级 Ollama
    cloud_budget = min(cloud_budget, budget)
    payload = _make_payload(name, domain, language)
    started = time.monotonic()
    deadline = started + budget
    failures: list[str] = []

    # ---- 主通道：云端（子预算 5 秒，给 Ollama 留出降级时间）----
    if cloud_configured():
        try:
            terms = await asyncio.wait_for(
                asyncio.to_thread(_call_cloud, payload),
                timeout=cloud_budget,
            )
            logger.info(f"专业词汇生成成功（cloud）：{len(terms)} 个")
            return _build_result(terms, "cloud", started)
        except asyncio.TimeoutError:
            failures.append(f"云端超时（{cloud_budget:g} 秒）")
            logger.warning("云端专业词汇生成超时，尝试降级本地 Ollama")
        except Exception as exc:  # noqa: BLE001 —— 任何云端失败都应触发降级
            failures.append(f"云端不可用（{type(exc).__name__}: {exc}）")
            logger.warning(f"云端专业词汇生成失败，尝试降级本地 Ollama: {exc}")
    else:
        failures.append("云端未配置")
        logger.info("云端 LLM 未配置，专业词汇生成直接走本地 Ollama")

    # ---- 降级通道：本地 Ollama（共享同一 deadline）----
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TermGenerationUnavailable(
            f"生成专业术语超时：总时限 {budget:g} 秒内云端未完成，"
            f"本地 Ollama 已无剩余可用时间。实时字幕与开始识别等主功能不受影响。"
        )

    try:
        terms = await asyncio.wait_for(
            asyncio.to_thread(_call_ollama, payload),
            timeout=remaining,
        )
        logger.info(f"专业词汇生成成功（ollama）：{len(terms)} 个")
        return _build_result(terms, "ollama", started)
    except asyncio.TimeoutError:
        failures.append(f"本地 Ollama 超时（剩余 {remaining:g} 秒）")
    except Exception as exc:  # noqa: BLE001 —— 降级失败统一转成 503
        failures.append(f"本地 Ollama 不可用（{type(exc).__name__}: {exc}）")

    raise TermGenerationUnavailable(
        "生成专业术语失败：华为云与本地 Ollama 均不可用（"
        + "；".join(failures)
        + "）。请检查云端配置或确认 Ollama 已启动并拉取模型；"
        "实时字幕与开始识别等主功能不受影响。"
    )


__all__ = [
    "GeneratedTerms",
    "TermGenerationUnavailable",
    "generate_course_terms",
    "MAX_TERMS",
    "TOTAL_TIMEOUT_SECONDS",
    "CLOUD_TIMEOUT_SECONDS",
]