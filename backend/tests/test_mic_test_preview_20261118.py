# -*- coding: utf-8 -*-
"""v4.2.0 合并张春阳 4.1.3 update：麦克风试音转写预览的防回归守卫。

合并风险有两个，测试各有针对：
  1. 只取增量、不整包覆盖 —— 张春阳包基于旧版，没有窗口捕获修复；
     跨层守卫放在 frontend/tests/mic-test-preview.test.mts。
  2. 内存 PCM 绝不能泄漏给 HTTP 响应，也不能落盘 —— 守 `_pcm` 被 pop。
"""
from __future__ import annotations

from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


def test_request_schema_accepts_preview_and_language():
    from api.routes import MicTestRequest

    req = MicTestRequest(preview=True, language="zh")
    assert req.preview is True
    assert req.language == "zh"

    # 默认行为不变：老调用方不发 preview 时只测电平。
    plain = MicTestRequest()
    assert plain.preview is False
    assert plain.language == "auto"

    with pytest.raises(Exception):
        MicTestRequest(language="de")  # 不在 Literal 白名单里


def test_preview_false_stays_level_only(monkeypatch):
    import audio.capture as capture
    from api.routes import _run_mic_test

    seen = {}

    def fake_burst(device_index, seconds, *, include_pcm=False):
        seen["include_pcm"] = include_pcm
        return {
            "ok": True, "level": 0.5, "peak": 0.4,
            "deviceIndex": device_index, "message": "电平良好",
        }

    monkeypatch.setattr(capture.AudioCapture, "mic_test_burst", staticmethod(fake_burst))
    from api.routes import MicTestRequest

    result = _run_mic_test(MicTestRequest())
    assert seen["include_pcm"] is False
    assert result["ok"] is True
    assert "transcript" not in result
    assert "previewMessage" not in result


def test_preview_true_pops_pcm_and_reports_low_level(monkeypatch):
    import audio.capture as capture
    from api.routes import MicTestRequest, _run_mic_test

    seen = {}

    def fake_burst(device_index, seconds, *, include_pcm=False):
        seen["include_pcm"] = include_pcm
        return {
            "ok": True, "level": 0.01, "peak": 0.05,
            "deviceIndex": device_index, "message": "几乎没采到声音",
            "_pcm": bytearray(b"\x01\x00" * 100),
        }

    monkeypatch.setattr(capture.AudioCapture, "mic_test_burst", staticmethod(fake_burst))
    result = _run_mic_test(MicTestRequest(preview=True, language="zh"))

    assert seen["include_pcm"] is True, "preview=True 必须让采集侧保留内存 PCM"
    assert result["transcript"] == ""
    assert "没采到足够的声音" in result["previewMessage"]
    assert "_pcm" not in result, "内存 PCM 绝不能随 HTTP 响应返回"


def test_preview_model_not_ready_gives_actionable_message(monkeypatch):
    import audio.capture as capture
    import asr.model_bootstrap as bootstrap
    from api.routes import MicTestRequest, _run_mic_test

    def fake_burst(device_index, seconds, *, include_pcm=False):
        return {
            "ok": True, "level": 0.5, "peak": 0.4,
            "deviceIndex": device_index, "message": "电平良好",
            "_pcm": bytearray(b"\x01\x00" * 100),
        }

    monkeypatch.setattr(capture.AudioCapture, "mic_test_burst", staticmethod(fake_burst))
    monkeypatch.setattr(bootstrap, "ensure_model", lambda size: {"ok": False})
    result = _run_mic_test(MicTestRequest(preview=True))

    assert "base" in result["previewMessage"]
    assert "模型设置" in result["previewMessage"]
    assert "_pcm" not in result


def test_failed_capture_short_circuits_preview(monkeypatch):
    import audio.capture as capture
    from api.routes import MicTestRequest, _run_mic_test

    monkeypatch.setattr(capture.AudioCapture, "mic_test_burst", staticmethod(
        lambda device_index, seconds, *, include_pcm=False: {
            "ok": False, "level": 0.0, "peak": 0.0,
            "deviceIndex": device_index, "message": "试采失败",
        }
    ))
    result = _run_mic_test(MicTestRequest(preview=True))
    assert result["ok"] is False
    assert "transcript" not in result


def test_source_guard_keeps_pcm_out_of_response():
    """源码级守卫：防止后续重构把 _pcm 直接 return 出去。"""
    routes = (BACKEND / "api" / "routes.py").read_text(encoding="utf-8")
    capture = (BACKEND / "audio" / "capture.py").read_text(encoding="utf-8")

    assert "include_pcm=req.preview" in routes
    assert 'result.pop("_pcm", None)' in routes
    assert "include_pcm: bool = False" in capture
    assert 'result["_pcm"] = pcm' in capture
    assert "previewMessage" in routes
