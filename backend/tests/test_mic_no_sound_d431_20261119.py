# -*- coding: utf-8 -*-
"""麦克风试采「没有声音」修复守卫（v4.3.1）

根因：
  1. mic_test_burst 写死 16000Hz/1ch 打开设备，而 Realtek 阵列麦原生
     48000Hz/2ch —— 静默返回 -1~1 的数字静音，或抛 Errno -9997。
  2. 电平阈值 0.02 把远场真实语音（level 0.004~0.02）误判成没声音。
  3. 「系统默认麦克风」恰好指向没接麦的映射器，用户不知道该换设备。
"""
from __future__ import annotations

from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_mic_test_burst_uses_native_device_format():
    src = (BACKEND / "audio" / "capture.py").read_text(encoding="utf-8")
    # 不能再写死 16000/1ch 开流
    assert 'rate=16000,\n                        input=True' not in src
    assert 'defaultSampleRate' in src, "应按设备原生采样率开流"
    assert 'maxInputChannels' in src, "应按设备原生通道数开流"
    assert 'rate != 16000' in src, "非 16k 必须重采样后再给 Whisper"
    assert 'np.interp' in src, "重采样实现应存在"


def test_mic_test_burst_multi_channel_and_quiet_floor():
    src = (BACKEND / "audio" / "capture.py").read_text(encoding="utf-8")
    assert 'reshape(-1, channels)' in src, "多通道不能只取第一通道"
    assert 'quiet_floor = 0.004' in src, "低电平阈值应下调，别把远场语音判成静音"
    assert 'gain = 8.0 if rate <= 16000 else 14.0' in src, "48k 设备要有更大增益"


def test_probe_input_devices_exists_and_skips_loopback():
    src = (BACKEND / "audio" / "capture.py").read_text(encoding="utf-8")
    assert "def probe_input_devices" in src
    assert 'device.get("isLoopback")' in src, "回环设备不参与麦克风推荐"


def test_mic_scan_route_recommends_audible_device():
    routes = (BACKEND / "api" / "routes.py").read_text(encoding="utf-8")
    assert '@router.post("/audio/mic-scan")' in routes
    assert '"recommendedIndex"' in routes
    assert '"candidateIndex"' in routes
    assert "not d.get(\"isDefaultInput\")" in routes, "有声设备优先于默认设备"
    assert "捕获驱动|capture driver|loopback|回环" in routes, "回环设备不能推荐成麦克风"


def test_frontend_auto_selects_and_shows_probe_levels():
    page = (BACKEND.parent / "frontend" / "src" / "shell" / "pages" / "ConfigPage.tsx").read_text(encoding="utf-8")
    assert "/api/audio/mic-scan" in page
    assert "mic-device-chip" in page
    assert "const pick = data.recommendedIndex ?? data.candidateIndex ?? null" in page
    assert "setSettings({ micDeviceIndex: pick })" in page
    assert "有声" in page and "静音" in page
