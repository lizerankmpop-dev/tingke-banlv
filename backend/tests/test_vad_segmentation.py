"""VAD 分段行为回归测试（2026-11-11 延迟修复）

背景：课堂场景下字幕出字延迟实测达 ~15 秒（≈三句话）。归因过程见
`scripts/probe_vad_segments.py` 与 `scripts/bench_latency.py`，
根因是 `MAX_SPEECH_DURATION` 这个「最长片段保护」**从来没有真正生效**：

    ① 检查只写在静音分支 ⇒ 连续讲话时永远进不了该分支；
    ② 量的是「语音窗口累计时长」而非片段实际时长（讲课时语音窗口只占 66%）；
    ③ 达到上限时还会被 `MIN_SPEECH_DURATION` 守卫挡掉，既不提交也不重置。

本文件用**合成信号**（不依赖 Silero 模型与真实音频）把这三条都锁死。
"""

import numpy as np
import pytest

from audio.vad import (
    MIN_SPEECH_DURATION,
    VoiceActivityDetector,
    WINDOW_DURATION,
)


def _feed(det, seconds: float, is_speech: bool):
    """按 512 样本窗口喂入音频，并强制指定每个窗口的 VAD 判定"""
    det._detect_speech = lambda w: is_speech
    n = int(16000 * seconds / 512)
    chunk = np.zeros(512, dtype=np.float32)
    for _ in range(n):
        det.process_chunk(chunk)


def test_continuous_speech_is_capped_by_max_speech_duration():
    """🔴 连续讲话时，片段长度必须受 MAX_SPEECH_DURATION 约束。

    修复前：声明上限 12s，实测片段长达 15.07s（上限根本没触发）；
    再修后（只把检查搬到语音分支）：声明 4s，实测仍有 7.26s
    —— 因为上限量的是语音累计时长，且被 MIN 守卫挡掉。
    """
    segs = []
    det = VoiceActivityDetector(on_speech_segment=lambda s, a, b: segs.append((a, b)))
    det._silence_duration = 0.8
    det._max_speech_duration = 2.0

    _feed(det, 10.0, is_speech=True)   # 连续讲话，全程没有静音
    det.flush()

    assert segs, "连续讲话应当产生多个片段"
    for start, end in segs:
        assert (end - start) <= 2.0 + WINDOW_DURATION, (
            f"片段 {end - start:.2f}s 超过上限 2.0s —— "
            f"「最长片段保护」又失效了（延迟会回到三句话级别）"
        )
    # 10 秒连续讲话 / 2 秒上限 ⇒ 至少切成 4 段
    assert len(segs) >= 4, f"期望至少 4 段，实际 {len(segs)} 段"


def test_limit_is_not_vetoed_by_min_speech_duration():
    """🔴 达到上限时，不能再被 MIN_SPEECH_DURATION 挡掉。

    构造「语音占比极低但片段很长」的场景：每 10 个窗口只有 1 个判为语音，
    语音累计长时间达不到 MIN_SPEECH_DURATION(1.2s)。
    修复前这种情况下片段会一直涨（实测 4s 上限下首段 7.26s，语音仅 1.22s）。
    """
    segs = []
    det = VoiceActivityDetector(on_speech_segment=lambda s, a, b: segs.append((a, b)))
    det._silence_duration = 0.8
    det._max_speech_duration = 2.0

    # 让 VAD 判定「每 10 个窗口里只有第 1 个是语音」
    counter = {"i": 0}

    def sparse(window):
        counter["i"] += 1
        return counter["i"] % 10 == 0

    det._detect_speech = sparse
    chunk = np.zeros(512, dtype=np.float32)
    for _ in range(int(16000 * 12 / 512)):
        det.process_chunk(chunk)
    det.flush()

    assert segs, "应当仍然产生片段（上限触发不受 MIN 守卫否决）"
    for start, end in segs:
        assert (end - start) <= 2.0 + WINDOW_DURATION, (
            f"片段 {end - start:.2f}s 超过上限 2.0s —— 上限被 MIN 守卫挡掉了"
        )


def test_silence_still_breaks_sentences():
    """✅ 静音断句这条主路径不能被改坏：讲完一句停一下就该提交。"""
    segs = []
    det = VoiceActivityDetector(on_speech_segment=lambda s, a, b: segs.append((a, b)))
    det._silence_duration = 0.8
    det._max_speech_duration = 12.0

    # 2 秒语音
    _feed(det, 2.0, is_speech=True)
    # 1.2 秒静音（超过 0.8s 阈值）
    _feed(det, 1.2, is_speech=False)

    assert len(segs) == 1, f"静音后应提交 1 段，实际 {len(segs)} 段"
    start, end = segs[0]
    assert 1.5 <= (end - start) <= 3.5, f"片段时长异常：{end - start:.2f}s"


def test_pure_silence_is_not_submitted():
    """✅ 纯静音不该被送去识别（否则白跑一次推理）。"""
    segs = []
    det = VoiceActivityDetector(on_speech_segment=lambda s, a, b: segs.append((a, b)))
    det._silence_duration = 0.8
    det._max_speech_duration = 2.0

    _feed(det, 6.0, is_speech=False)
    det.flush()

    assert segs == [], f"纯静音不应产生片段，实际 {len(segs)} 段"


def test_far_field_profile_allows_longer_segments():
    """✅ 线下教室（far-field）语句更长，最长片段应比线上更宽松。"""
    near = VoiceActivityDetector(on_speech_segment=lambda *_: None)
    far = VoiceActivityDetector(on_speech_segment=lambda *_: None, profile="far-field")
    assert far._max_speech_duration > near._max_speech_duration
    assert far._silence_duration > near._silence_duration


def test_min_speech_constant_is_sane():
    """MIN_SPEECH_DURATION 必须为正且在合理量级（否则静音断句路径会失效）。"""
    assert 0 < MIN_SPEECH_DURATION <= 3.0
