"""
统一 LLM 客户端
屏蔽底层差异：本地 Ollama 或云端 OpenAI 兼容 API（DeepSeek / Kimi / 智谱 / OpenAI 等）

通过环境变量切换：
- TINGKEBANLV_LLM_PROVIDER: "ollama"（默认）| "cloud"
- TINGKEBANLV_OLLAMA_MODEL: 本地模型名（默认 qwen2.5:7b）
- TINGKEBANLV_CLOUD_API_BASE: 如 https://api.deepseek.com/v1
- TINGKEBANLV_CLOUD_API_KEY: API 密钥（不要硬编码到代码/提交到仓库）
- TINGKEBANLV_CLOUD_MODEL: 如 deepseek-chat

切换示例（云端 DeepSeek）：
    set TINGKEBANLV_LLM_PROVIDER=cloud
    set TINGKEBANLV_CLOUD_API_BASE=https://api.deepseek.com/v1
    set TINGKEBANLV_CLOUD_API_KEY=sk-xxx
    set TINGKEBANLV_CLOUD_MODEL=deepseek-chat

云端调用用标准库 urllib 实现，不引入额外依赖。
所有调用均为同步阻塞（与 Ollama 一致），在 FastAPI 路由中需用 asyncio.to_thread 包装。
"""

import json
import logging
import os

logger = logging.getLogger(__name__)


def get_provider() -> str:
    """当前 LLM 提供方：ollama | cloud"""
    return os.environ.get("TINGKEBANLV_LLM_PROVIDER", "ollama").strip().lower()


def chat(messages: list[dict], temperature: float = 0.3, num_predict: int = 1024) -> str | None:
    """
    统一对话接口

    Args:
        messages: OpenAI 格式消息列表 [{role, content}]
        temperature: 采样温度
        num_predict: 最大生成 token 数（cloud 映射为 max_tokens）

    Returns:
        assistant 回复文本；任何失败返回 None
    """
    if get_provider() == "cloud":
        return _chat_cloud(messages, temperature, num_predict)
    return _chat_ollama(messages, temperature, num_predict)


def is_available() -> bool:
    """探测当前 provider 是否可用（结果不缓存，由调用方自行缓存）"""
    if get_provider() == "cloud":
        return _cloud_configured()
    return _ollama_running()


# ============ Ollama 本地 ============

def _ollama_running() -> bool:
    try:
        import ollama
        ollama.list()
        return True
    except Exception:
        return False


def _chat_ollama(messages: list[dict], temperature: float, num_predict: int) -> str | None:
    try:
        import ollama

        model = os.environ.get("TINGKEBANLV_OLLAMA_MODEL", "qwen2.5:7b")
        response = ollama.chat(
            model=model,
            messages=messages,
            options={
                "temperature": temperature,
                "num_predict": num_predict,
            },
        )
        return response["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Ollama 调用失败: {e}")
        return None


# ============ 云端 OpenAI 兼容 API ============

def _cloud_configured() -> bool:
    """云端配置是否齐全（不发实际请求）"""
    return bool(
        os.environ.get("TINGKEBANLV_CLOUD_API_BASE", "").strip()
        and os.environ.get("TINGKEBANLV_CLOUD_API_KEY", "").strip()
        and os.environ.get("TINGKEBANLV_CLOUD_MODEL", "").strip()
    )


def _chat_cloud(messages: list[dict], temperature: float, num_predict: int) -> str | None:
    if not _cloud_configured():
        logger.error(
            "云端 LLM 配置不完整，需要设置 TINGKEBANLV_CLOUD_API_BASE / "
            "TINGKEBANLV_CLOUD_API_KEY / TINGKEBANLV_CLOUD_MODEL"
        )
        return None

    import urllib.request
    import urllib.error

    base = os.environ["TINGKEBANLV_CLOUD_API_BASE"].strip().rstrip("/")
    key = os.environ["TINGKEBANLV_CLOUD_API_KEY"].strip()
    model = os.environ["TINGKEBANLV_CLOUD_MODEL"].strip()
    url = f"{base}/chat/completions"

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": num_predict,
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            return content.strip() if content else None
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        logger.error(f"云端 LLM HTTP 错误 {e.code}: {body}")
        return None
    except Exception as e:
        logger.error(f"云端 LLM 调用失败: {e}")
        return None
