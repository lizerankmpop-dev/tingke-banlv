"""
AI 专业词汇生成接口 —— 双通道降级测试

覆盖：
  1. 云端（华为云）成功时返回 cloud 结果，并完成去重 / 去空 / 截断 15 条；
  2. 云端抛错时自动降级本地 Ollama；
  3. 两条通道都失败时返回 503 + 明确中文提示；
  4. 云端未配置时跳过云端，直接走 Ollama；
  5. 云端子预算超时后，仍能用剩余时间成功降级 Ollama；
  6. 云端与 Ollama 都超时时返回 503（而不是无限等待）。

所有用例都用 monkeypatch 替换真实模型调用，不访问网络、不依赖本机 Ollama。
用 httpx.ASGITransport 直连 ASGI 应用（等价于 TestClient，但不依赖本机回环 socket）。
"""

import asyncio
import time as time_module

import httpx
import pytest

from main import app


async def _request_terms(payload: dict) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/api/terms/generate", json=payload)


def _post_terms(payload: dict) -> httpx.Response:
    return asyncio.run(_request_terms(payload))


def _configure_cloud(monkeypatch) -> None:
    monkeypatch.setenv("TINGKEBANLV_CLOUD_API_BASE", "https://example.invalid/v1")
    monkeypatch.setenv("TINGKEBANLV_CLOUD_API_KEY", "test-key-not-real")
    monkeypatch.setenv("TINGKEBANLV_CLOUD_MODEL", "test-model")


def test_cloud_success_dedupes_and_caps_terms(monkeypatch):
    """云端成功：不降级，结果去重（大小写不敏感）、去空、最多 15 条"""
    _configure_cloud(monkeypatch)
    import llm.term_generator as term_generator

    raw_terms = [f"term-{i}" for i in range(20)] + ["TERM-0", "  ", ""]
    monkeypatch.setattr(term_generator, "_call_cloud", lambda payload: list(raw_terms))

    def _must_not_fallback(payload):
        raise AssertionError("云端成功时不应调用 Ollama")

    monkeypatch.setattr(term_generator, "_call_ollama", _must_not_fallback)

    resp = _post_terms(
        {"courseName": "机器学习", "domain": "计算机", "language": "en"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["provider"] == "cloud"
    assert len(data["terms"]) == term_generator.MAX_TERMS
    assert len({t.casefold() for t in data["terms"]}) == len(data["terms"])
    assert all(t.strip() for t in data["terms"])
    assert isinstance(data["elapsedMs"], int)
    assert data["elapsedMs"] >= 0


def test_cloud_failure_falls_back_to_ollama(monkeypatch):
    """云端抛错：自动切换 Ollama，两条通道各被调用一次"""
    _configure_cloud(monkeypatch)
    import llm.term_generator as term_generator

    calls = {"cloud": 0, "ollama": 0}

    def _cloud_fails(payload):
        calls["cloud"] += 1
        raise RuntimeError("模拟云端不可用")

    def _ollama_ok(payload):
        calls["ollama"] += 1
        return [f"本地术语{i}" for i in range(15)]

    monkeypatch.setattr(term_generator, "_call_cloud", _cloud_fails)
    monkeypatch.setattr(term_generator, "_call_ollama", _ollama_ok)

    resp = _post_terms({"courseName": "数据结构"})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["provider"] == "ollama"
    assert len(data["terms"]) == 15
    assert calls == {"cloud": 1, "ollama": 1}


def test_both_channels_failure_returns_503(monkeypatch):
    """双通道都不可用：503 + 明确指出主流程不受影响"""
    _configure_cloud(monkeypatch)
    import llm.term_generator as term_generator

    def _boom(payload):
        raise RuntimeError("模拟服务不可用")

    monkeypatch.setattr(term_generator, "_call_cloud", _boom)
    monkeypatch.setattr(term_generator, "_call_ollama", _boom)

    resp = _post_terms({"courseName": "编译原理"})
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "均不可用" in detail
    assert "主功能不受影响" in detail


def test_ollama_only_when_cloud_not_configured(monkeypatch):
    """云端三项配置缺失：不尝试云端，直接走 Ollama"""
    for key in (
        "TINGKEBANLV_CLOUD_API_BASE",
        "TINGKEBANLV_CLOUD_API_KEY",
        "TINGKEBANLV_CLOUD_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)

    import llm.term_generator as term_generator

    cloud_called = False

    def _cloud_must_not_run(payload):
        nonlocal cloud_called
        cloud_called = True
        return ["不应出现"]

    monkeypatch.setattr(term_generator, "_call_cloud", _cloud_must_not_run)
    monkeypatch.setattr(
        term_generator, "_call_ollama", lambda payload: ["梯度下降", "反向传播"]
    )

    resp = _post_terms({"courseName": "深度学习"})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["provider"] == "ollama"
    assert data["terms"] == ["梯度下降", "反向传播"]
    assert cloud_called is False


def test_cloud_timeout_falls_back_to_ollama(monkeypatch):
    """云端子预算超时：剩余时间仍能降级 Ollama 并成功返回"""
    _configure_cloud(monkeypatch)
    import llm.term_generator as term_generator

    # 模拟真实预算比例：总 8 秒中的云端 5 秒，这里按比例缩短到 0.3 / 0.05
    monkeypatch.setattr(term_generator, "TOTAL_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(term_generator, "CLOUD_TIMEOUT_SECONDS", 0.05)

    def _cloud_slow(payload):
        time_module.sleep(0.6)
        return ["云端慢结果"]

    monkeypatch.setattr(term_generator, "_call_cloud", _cloud_slow)
    monkeypatch.setattr(
        term_generator, "_call_ollama", lambda payload: [f"降级术语{i}" for i in range(15)]
    )

    resp = _post_terms({"courseName": "操作系统"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["provider"] == "ollama"
    assert len(data["terms"]) == 15


def test_cloud_and_ollama_timeout_returns_503(monkeypatch):
    """云端与 Ollama 都超时：返回 503，并同时报告两条通道超时"""
    _configure_cloud(monkeypatch)
    import llm.term_generator as term_generator

    monkeypatch.setattr(term_generator, "TOTAL_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(term_generator, "CLOUD_TIMEOUT_SECONDS", 0.05)

    def _slow(payload):
        time_module.sleep(0.6)
        return ["慢结果"]

    monkeypatch.setattr(term_generator, "_call_cloud", _slow)
    monkeypatch.setattr(term_generator, "_call_ollama", _slow)

    resp = _post_terms({"courseName": "操作系统"})
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "均不可用" in detail
    assert "超时" in detail