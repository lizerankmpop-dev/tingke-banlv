# -*- coding: utf-8 -*-
"""v4.4.1：ASR 模型运行时可配置的防回归守卫。

覆盖三件事：
  1. POST /api/asr/config 接受合法 modelSize、拒绝非法值；
  2. 试采预览跟随配置的模型（不再写死 base）；
  3. GET /api/asr/config 返回每个候选模型的本地就绪状态。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from main import app
from api import routes


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _restore_default():
    original = routes.whisper_model_default
    yield
    routes.whisper_model_default = original


def test_config_accepts_supported_model(client):
    for size in routes.SUPPORTED_WHISPER_MODELS:
        r = client.post("/api/asr/config", json={"modelSize": size})
        assert r.status_code == 200, r.text
        assert r.json()["applied"]["modelSize"] == size
        assert routes.whisper_model_default == size


def test_config_rejects_unknown_model(client):
    r = client.post("/api/asr/config", json={"modelSize": "huge-v999"})
    assert r.status_code == 422, r.text
    assert "不支持" in r.json()["detail"]
    # 非法值不能污染当前默认值
    assert routes.whisper_model_default in routes.SUPPORTED_WHISPER_MODELS


def test_config_missing_field_keeps_model(client):
    client.post("/api/asr/config", json={"modelSize": "medium"})
    r = client.post("/api/asr/config", json={})
    assert r.status_code == 200
    assert routes.whisper_model_default == "medium"
    assert "modelSize" not in r.json()["applied"]


def test_get_config_lists_models_with_installed_flag(client):
    r = client.get("/api/asr/config")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["modelSize"] in routes.SUPPORTED_WHISPER_MODELS
    sizes = [m["size"] for m in body["models"]]
    assert set(sizes) == set(routes.SUPPORTED_WHISPER_MODELS)
    for m in body["models"]:
        assert isinstance(m["installed"], bool)
        assert isinstance(m["isDefault"], bool)
    assert sum(1 for m in body["models"] if m["isDefault"]) == 1


def test_mic_test_preview_uses_configured_model(monkeypatch):
    """试采预览必须用运行时配置的模型，不能写死 base。"""
    import audio.capture as capture
    import asr.model_bootstrap as bootstrap
    from api.routes import MicTestRequest, _run_mic_test

    seen = {"ensure": [], "model_arg": []}

    def fake_burst(device_index, seconds, *, include_pcm=False):
        # 电平足够，进入转写分支
        return {
            "ok": True, "level": 0.5, "peak": 0.4,
            "deviceIndex": device_index, "message": "ok",
            "_pcm": bytearray(b"\x01\x00" * 100),
        }

    monkeypatch.setattr(capture.AudioCapture, "mic_test_burst", staticmethod(fake_burst))
    monkeypatch.setattr(bootstrap, "ensure_model",
                        lambda size: (seen["ensure"].append(size), {"ok": False})[1])

    # 配置为 medium，试采没显式传 modelSize → 跟随配置
    routes.whisper_model_default = "medium"
    result = _run_mic_test(MicTestRequest(preview=True))
    assert seen["ensure"][-1] == "medium"
    assert "medium" in result["previewMessage"]

    # 请求里显式指定 small → 覆盖配置
    result2 = _run_mic_test(MicTestRequest(preview=True, modelSize="small"))
    assert seen["ensure"][-1] == "small"
    assert "small" in result2["previewMessage"]


def test_source_guard_no_hardcoded_base_in_preview():
    """源码守卫：试采预览分支里不得再出现硬编码 WhisperModel("base")。"""
    routes_src = (routes.__file__ and open(routes.__file__, encoding="utf-8").read())
    preview_block = routes_src[routes_src.index("def _run_mic_test"):routes_src.index("@router.post(\"/audio/mic-scan\"")]
    assert 'WhisperModel(\n            "base"' not in preview_block
    assert "preview_model" in preview_block


def test_course_config_for_asr_passes_model_size():
    """v4.4.3 回归守卫：modelSize 必须透传给管道配置。

    事故：/_build_pipeline 用 _course_config_for_asr(course) 重建字典，
    此前漏掉 modelSize ⇒ 全局切 medium 对正式听课无效，管道永远用 base。
    """
    cfg = routes._course_config_for_asr({
        "name": "测试课",
        "domain": "计算机科学",
        "courseType": "offline",
        "language": "zh",
        "modelSize": "medium",
    })
    assert cfg.get("modelSize") == "medium", "modelSize 未透传给 ASR 管道"

    # 没设过也要带键（值为 None），让 /asr/start 的兜底能被 pipeline 看见
    cfg2 = routes._course_config_for_asr({"name": "老课程", "courseType": "online"})
    assert "modelSize" in cfg2, "缺 modelSize 键会让全局默认无法生效"
