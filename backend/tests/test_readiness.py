"""
就绪探测与健康检查的回归测试

背景（打包后的真实故障，2026-10-02 复现）
----------------------------------------
`/api/health` 里同步等待了一次外部 LLM 探测（`ollama.list()`）。
在企业/校园网络下，回环请求被 HTTP_PROXY 劫持到代理服务器，实测单次耗时
**2.0 秒**并返回 502；而 Electron 主进程判断「后端是否就绪」用的是
带 2 秒超时的 `/api/health` 探测 —— 于是每次探测都超时判定失败，
界面永远等不到就绪，90 秒后弹「后端启动超时」，应用直接不可用。

这里锁死三件事，任何一条回归都会让安装版在受限网络下重新变砖：
  1) `/api/ping` 恒定毫秒级返回，不依赖数据库与任何外部服务；
  2) `/api/health` 不同步等待 LLM 探测（只读缓存）；
  3) 启动时把回环地址并入 NO_PROXY，避免本地请求被代理劫持。
"""

import os
import time

import pytest
from fastapi.testclient import TestClient

import main as app_main


@pytest.fixture
def client():
    # 用 with 触发 lifespan，否则后台探测任务与数据库都不会初始化
    with TestClient(app_main.app) as c:
        yield c


def test_ping_returns_ok_and_is_cheap(client):
    """就绪探测接口必须能立刻响应"""
    started = time.perf_counter()
    resp = client.get("/api/ping")
    elapsed = time.perf_counter() - started

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "pid" in body and "version" in body
    # 放宽到 0.5s：CI/冷启动下首次请求可能要做路径解析，但仍远低于任何超时阈值
    assert elapsed < 0.5, f"/api/ping 耗时 {elapsed:.3f}s，已不再是轻量接口"


def test_ping_is_not_shadowed_by_static_mount(client):
    """
    前端静态资源挂在 "/"（兜底路由），必须注册在所有 API 路由之后。
    一旦顺序反了，/api/ping 会返回 index.html 而不是 JSON —— 就绪探测随即失效。
    """
    resp = client.get("/api/ping")
    assert resp.headers["content-type"].startswith("application/json")


def test_health_does_not_block_on_llm_probe(client, monkeypatch):
    """
    LLM 探测再慢也不能拖慢 /api/health

    直接把探测函数换成必然耗时 5 秒的版本：health 若同步等它，
    本用例会因超过 1 秒而失败。
    """
    import llm.client as llm_client

    def slow_probe():
        time.sleep(5)
        return False

    monkeypatch.setattr(llm_client, "is_available", slow_probe)

    started = time.perf_counter()
    resp = client.get("/api/health")
    elapsed = time.perf_counter() - started

    assert resp.status_code == 200
    assert elapsed < 1.0, f"/api/health 被 LLM 探测阻塞了 {elapsed:.3f}s"
    body = resp.json()
    assert "llm_available" in body
    # 向后兼容字段仍在（旧前端/旧脚本可能还在读它）
    assert "ollama_available" in body


def test_loopback_is_excluded_from_proxy():
    """
    回环流量必须绕过系统代理

    httpx / urllib 默认信任 HTTP_PROXY，而代理对 127.0.0.1 的请求会返回 502，
    这正是上面那起「启动超时」事故的根因之一。
    """
    bypass = os.environ.get("NO_PROXY", "")
    assert "127.0.0.1" in bypass, f"NO_PROXY 未包含 127.0.0.1: {bypass!r}"
    assert "localhost" in bypass, f"NO_PROXY 未包含 localhost: {bypass!r}"
    assert "::1" in bypass, f"NO_PROXY 未包含 IPv6 回环: {bypass!r}"


def test_probe_endpoint_refreshes_status(client):
    """手动探测接口应返回当前 provider 与可用性"""
    resp = client.post("/api/llm/probe")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] in ("ollama", "cloud")
    assert isinstance(body["available"], bool)
    assert body["checked_at"] > 0
