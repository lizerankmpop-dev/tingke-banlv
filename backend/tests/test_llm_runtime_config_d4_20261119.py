# -*- coding: utf-8 -*-
"""D4 云端模型运行时配置防回归（2026-11-19）

- 运行时配置优先于环境变量，且立即生效（无需重启）
- GET 回显脱敏：API key 只回 cloudApiKeySet，绝不回传明文
- provider / cloud_configured 走运行时配置
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from main import app
from llm.chains import base

client = TestClient(app)


def test_runtime_config_roundtrip_and_masking():
    base.set_runtime_llm_config({
        "provider": "cloud",
        "cloudApiBase": "https://api.example.com/v1",
        "cloudApiKey": "sk-super-secret",
        "cloudModel": "test-model",
    })
    try:
        assert base.get_provider() == "cloud"
        assert base.cloud_configured() is True

        r = client.get("/api/llm/config")
        assert r.status_code == 200, r.text
        cfg = r.json()["config"]
        assert cfg["provider"] == "cloud"
        assert cfg["cloudApiKeySet"] is True
        assert "sk-super-secret" not in r.text, "API key 绝不能出现在 GET 响应里"
        assert "cloudApiKey" not in cfg, "响应不应含明文 key 字段"
    finally:
        base.set_runtime_llm_config({
            "provider": "", "cloudApiBase": "", "cloudApiKey": "", "cloudModel": "",
        })


def test_put_config_takes_effect_immediately():
    r = client.put("/api/llm/config", json={
        "provider": "cloud",
        "cloudApiBase": "https://x.example/v1",
        "cloudApiKey": "k",
        "cloudModel": "m",
    })
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == "cloud"
    assert base.get_provider() == "cloud"
    assert base.cloud_configured() is True
    client.put("/api/llm/config", json={"provider": "", "cloudApiBase": "", "cloudApiKey": "", "cloudModel": ""})


def test_empty_value_clears_override_and_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("TINGKEBANLV_LLM_PROVIDER", "ollama")
    base.set_runtime_llm_config({"provider": "cloud"})
    assert base.get_provider() == "cloud"
    base.set_runtime_llm_config({"provider": ""})
    assert base.get_provider() == "ollama", "清空覆盖后应回退到环境变量"


def test_env_still_works_when_no_runtime_override(monkeypatch):
    """保留 CI/部署用 env 配置：没有任何运行时覆盖时完全走 env。"""
    monkeypatch.setenv("TINGKEBANLV_CLOUD_API_BASE", "https://env.example/v1")
    monkeypatch.setenv("TINGKEBANLV_CLOUD_API_KEY", "env-key")
    monkeypatch.setenv("TINGKEBANLV_CLOUD_MODEL", "env-model")
    base.set_runtime_llm_config({
        "cloudApiBase": "", "cloudApiKey": "", "cloudModel": "", "provider": "",
    })
    assert base.cloud_configured() is True
