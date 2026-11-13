"""
ASR 后端工厂

选择优先级：课程配置 `asrBackend` > 环境变量 `TINGKEBANLV_ASR_BACKEND` > `local`。
未知取值记 warning 并回退本地，避免一个拼写错误就让整堂课无法开始。
"""

import logging
import os
from typing import Any, Optional

from asr.huawei_asr import HuaweiASRBackend
from asr.whisper_engine import WhisperEngine

logger = logging.getLogger(__name__)

ENV_ASR_BACKEND = "TINGKEBANLV_ASR_BACKEND"

LOCAL_ALIASES = frozenset({"local", "whisper"})
CLOUD_ALIASES = frozenset({"huawei", "huawei-asr", "cloud"})

# 只对云端后端有意义的参数。切回本地时过滤掉，避免 TypeError；其余参数原样透传。
_CLOUD_ONLY_KWARGS = frozenset({"config", "transport"})


def resolve_backend(course_config: Optional[dict] = None) -> str:
    """返回 `"local"` 或 `"cloud"`（未知值已回退并记录 warning）"""
    raw = (course_config or {}).get("asrBackend")
    if raw is None or not str(raw).strip():
        raw = os.environ.get(ENV_ASR_BACKEND)

    name = str(raw or "local").strip().lower()
    if name in LOCAL_ALIASES:
        return "local"
    if name in CLOUD_ALIASES:
        return "cloud"

    logger.warning(
        f"未知的 ASR 后端配置 {raw!r}（课程配置 asrBackend / 环境变量 "
        f"{ENV_ASR_BACKEND}），已回退本地 Whisper"
    )
    return "local"


def create_asr_backend(course_config: Optional[dict] = None, **kwargs: Any):
    """
    创建 ASR 后端实例

    `kwargs` 与 `ASRPipeline` 现有构造参数一致：
    model_size / device / compute_type / language / on_partial / on_progress /
    initial_prompt / prompt_sections / simp_mode / on_language_decided。
    云端后端会忽略本地模型参数；本地分支原样透传给 `WhisperEngine`。
    """
    backend = resolve_backend(course_config)
    if backend == "cloud":
        logger.info("ASR 后端：华为云实时语音转写（huawei-asr）")
        return HuaweiASRBackend(**kwargs)

    local_kwargs = {
        key: value for key, value in kwargs.items() if key not in _CLOUD_ONLY_KWARGS
    }
    logger.info("ASR 后端：本地 Whisper")
    return WhisperEngine(**local_kwargs)