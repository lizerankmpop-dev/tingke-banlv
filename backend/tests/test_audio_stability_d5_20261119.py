# -*- coding: utf-8 -*-
"""D5 音频识别稳定性防回归（2026-11-19）

三类「假识别」修复的守卫：
  ① 采集线程静默死掉 → on_error 上报 + captureAlive=false 透给前端
  ② 回环设备索引缓存漂移 → 开流前校验，失效清缓存重枚举
  ③ Silero 永久降级 → 可恢复重试 + 能量阈值 env 可调
"""
from __future__ import annotations

from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


def test_capture_exposes_health_snapshot_and_error_callback():
    from audio.capture import AudioCapture

    errors: list[str] = []
    cap = AudioCapture(on_audio_chunk=lambda _a: None, on_error=errors.append)
    # 未启动时线程为 None ⇒ 不健康；这是前端该拦截的信号
    snap = cap.health_snapshot()
    assert snap["captureAlive"] is False
    assert snap["audioChunks"] == 0

    # _report_error 必须把消息传给上层，且回调抛异常也不能外泄
    cap._report_error("设备丢失")
    assert errors == ["设备丢失"]
    cap.on_error = lambda _m: (_ for _ in ()).throw(RuntimeError("boom"))
    cap._report_error("不会外泄")


def test_capture_requires_no_on_error_is_backward_compatible():
    from audio.capture import AudioCapture

    # 旧调用方（如测试夹具）不传 on_error，_report_error 应静默返回不抛
    cap = AudioCapture(on_audio_chunk=lambda _a: None)
    cap._report_error("无回调也不抛")


def test_loopback_index_cache_is_validated(monkeypatch):
    """源码守卫：缓存命中后必须有 get_device_info_by_index 校验，不能直接 return。"""
    src = (BACKEND / "audio" / "capture.py").read_text(encoding="utf-8")
    assert "校验失败" in src or "已无输入通道" in src, \
        "缓存命中不能无条件返回，必须校验设备索引仍有效"
    assert "_DEFAULT_LOOPBACK_INDEX_CACHE = None" in src, \
        "索引失效时必须清缓存触发重新枚举"


def test_pipeline_wires_on_error_and_merges_health():
    src = (BACKEND / "asr" / "pipeline.py").read_text(encoding="utf-8")
    assert "on_error=lambda msg: self.on_status(\"error\", msg)" in src, \
        "采集失败必须通过 on_status('error') 上报到前端"
    assert "health_snapshot()" in src, \
        "input_meter 必须合并采集健康快照"


def test_energy_threshold_is_env_tunable():
    from audio import vad

    assert hasattr(vad, "ENERGY_RMS_THRESHOLD"), "能量检测阈值必须可 env 配置"
    assert vad.ENERGY_RMS_THRESHOLD > 0


def test_silero_degrade_is_recoverable():
    from audio.vad import VoiceActivityDetector, SILERO_RECOVER_INTERVAL

    v = VoiceActivityDetector(on_speech_segment=lambda *_a: None)
    assert hasattr(v, "_degraded_at")
    assert v._degraded_at is None
    assert hasattr(v, "_try_reload_silero")
    # 默认间隔为正数（可恢复），不重试部署可由 env 设为 0
    assert SILERO_RECOVER_INTERVAL >= 0


def test_source_guard_keeps_d5_fixes():
    """跨文件守卫：防止后续重构把 D5 的修复回退掉。"""
    capture = (BACKEND / "audio" / "capture.py").read_text(encoding="utf-8")
    frontend = (BACKEND.parent / "frontend" / "src" / "stores" / "useSubtitleStore.ts").read_text(encoding="utf-8")

    assert "captureAlive" in capture
    assert "captureAlive === false" in frontend, \
        "前端必须在 poll 里拦截采集线程死亡，否则继续显示假识别"
