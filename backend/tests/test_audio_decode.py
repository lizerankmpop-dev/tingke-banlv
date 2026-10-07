"""音频解码路径（scripts/eval_asr_quality.load_audio）的常驻用例

这个文件为一个**真实发生过的 P0 缺陷**而存在
---------------------------------------------
`load_audio()` 的 PyAV 分支专门为非 WAV 素材（mp3 / m4a / mp4，即真实网课录音）
而写，但它的 `_frame_to_mono()` 用错了数组布局约定：

    arr.reshape(arr.shape[0], -1).mean(axis=1)      # 错：把每帧压成 1 个样本

那是 `_load_wav()` 里处理**交错存储**的写法。而 `AudioFrame.to_ndarray()` 在
packed 格式下返回 `(1, samples)`，于是轴 1（真正的样本）被当成声道求了均值。
实测：1200s 的素材只解出 3.5s，**样本数比 0.00293**。

为什么长期没暴露：所有历史输入都是 `.wav`，走标准库分支，
这条 PyAV 分支从未被真实调用 —— 是「写了但没跑过」的死代码。
直到 P3 第一次喂入 B 站 `m4s` 才浮现。

所以本文件的重点是两条：

1. **样本数量不变量**：一帧进去多少样本，展平出来就必须是多少样本（不依赖文件）。
2. **端到端时长**：真造一个 m4a 丢给 `load_audio()`，解出来的时长必须与原始一致。
   仅靠第 1 条不够 —— 它守的是 `_frame_to_mono` 这个函数；第 2 条守的是整条链路。

另外把**旧的错误写法**固化成一条永久变异探针（`test_guard_catches_the_old_buggy_downmix`），
用来确保新加的「解码时长完整性兜底」真的会在那种情况下报错，而不是形同虚设。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

av = pytest.importorskip("av", reason="需要 PyAV（项目 venv 已内置）")

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "eval_asr_quality.py"

pytestmark = pytest.mark.skipif(
    not SCRIPT.is_file(), reason="scripts/eval_asr_quality.py 不存在（仓库不完整？）"
)


def _load_tool():
    spec = importlib.util.spec_from_file_location("eval_asr_quality_under_test", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


def _make_m4a(path: Path, seconds: float = 3.0, freq: float = 440.0,
              amp: float = 0.4, rate: int = 44100) -> Path:
    """进程内编码一个 AAC(m4a) 正弦音，作为「非 WAV 真实素材」的最小替身。"""
    n = int(rate * seconds)
    t = np.arange(n, dtype=np.float32) / rate
    tone = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)

    path.parent.mkdir(parents=True, exist_ok=True)
    with av.open(str(path), "w", format="ipod") as c:
        st = c.add_stream("aac", rate=rate)
        st.layout = "mono"
        for i in range(0, n, 1024):
            blk = tone[i:i + 1024]
            if blk.size < 1024:
                break
            arr = (np.clip(blk, -1.0, 1.0) * 32767.0).astype("<i2").reshape(1, -1)
            fr = av.AudioFrame.from_ndarray(arr, format="s16", layout="mono")
            fr.rate = rate
            for pkt in st.encode(fr):
                c.mux(pkt)
        for pkt in st.encode(None):
            c.mux(pkt)
    return path


# ============ 不变量：展平不得改变样本数 ============


def test_frame_to_mono_preserves_samples_packed_s16_mono(tool):
    """核心不变量。旧实现会把 325 个样本压成 1 个 —— 这条会直接红。"""
    arr = np.arange(325, dtype=np.int16).reshape(1, 325)
    frame = av.AudioFrame.from_ndarray(arr, format="s16", layout="mono")
    out = tool._frame_to_mono(frame)
    assert out.shape == (325,), f"期望 325 个样本，实得 {out.shape}"
    # 值也必须原样保留（不能被均值糊掉）
    assert int(out[0]) == 0 and int(out[324]) == 324


def test_frame_to_mono_packed_s16_stereo(tool):
    """packed 立体声在 to_ndarray() 里是 (1, samples*2)，展平后应得 samples*2 个。"""
    arr = np.zeros((1, 200 * 2), dtype=np.int16)
    arr[0, ::2] = 1000   # 左
    arr[0, 1::2] = -1000  # 右
    frame = av.AudioFrame.from_ndarray(arr, format="s16", layout="stereo")
    out = tool._frame_to_mono(frame)
    assert out.shape == (400,)


def test_frame_to_mono_planar_fltp_stereo_downmixes_along_channel_axis(tool):
    """平面多声道 (channels, samples) 才需要降混，且必须沿**轴 0**。"""
    arr = np.zeros((2, 200), dtype=np.float32)
    arr[0, :] = 1.0
    arr[1, :] = -1.0
    frame = av.AudioFrame.from_ndarray(arr, format="fltp", layout="stereo")
    out = tool._frame_to_mono(frame)
    assert out.shape == (200,), f"期望 200 个样本（降混到单声道），实得 {out.shape}"
    assert np.allclose(out, 0.0)


# ============ 端到端：非 WAV 素材的时长必须守恒 ============


def test_load_audio_reads_non_wav_with_full_duration(tool, tmp_path):
    """3 秒 m4a 解出来必须≈3 秒。

    旧实现下这里会得到约 130 个样本（≈0.008s），而不是 48000。
    """
    m4a = _make_m4a(tmp_path / "tone.m4a", seconds=3.0)
    audio = tool.load_audio(m4a)

    dur = len(audio) / tool.SAMPLE_RATE
    assert 2.9 <= dur <= 3.1, f"时长应为 3s 左右，实得 {dur:.3f}s（{len(audio)} 样本）"


def test_load_audio_non_wav_keeps_signal_not_averaged(tool, tmp_path):
    """不仅要时长对，**信号也得还在**。

    旧实现即使长度侥幸对上，「每帧一个均值」也会把 440Hz 正弦糊成噪声；
    因此这里同时校验能量量级（0.4 幅度正弦的 RMS≈0.283）与过零率。
    """
    m4a = _make_m4a(tmp_path / "tone.m4a", seconds=3.0, amp=0.4, freq=440.0)
    audio = tool.load_audio(m4a)

    rms = float(np.sqrt(np.mean(np.square(audio))))
    assert 0.22 <= rms <= 0.33, f"RMS 应≈0.283，实得 {rms:.4f}"

    # 过零率：440Hz 在 16kHz 下约 2*440/16000 = 0.055
    zc = float(np.mean(np.diff(np.signbit(audio)) != 0))
    assert 0.04 <= zc <= 0.07, f"过零率应≈0.055（说明是 440Hz 正弦），实得 {zc:.4f}"


def test_load_audio_wav_path_still_works(tool, tmp_path):
    """回归：WAV 走标准库分支，不能被这次改动带坏。"""
    import wave

    rate = tool.SAMPLE_RATE
    t = np.arange(rate * 2, dtype=np.float32) / rate
    tone = (0.4 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    pcm = (np.clip(tone, -1, 1) * 32767).astype("<i2")
    wav = tmp_path / "tone.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())

    audio = tool.load_audio(wav)
    assert abs(len(audio) - rate * 2) <= 2


# ============ 永久变异探针：把旧的错误写法钉住 ============


def _buggy_downmix(frame):
    """P0 缺陷的原写法（交错存储的降混，套在了 PyAV 帧布局上）。"""
    arr = frame.to_ndarray()
    return arr.reshape(-1) if arr.ndim == 1 else arr.reshape(arr.shape[0], -1).mean(axis=1)


def test_guard_catches_the_old_buggy_downmix(tool, tmp_path, monkeypatch):
    """新增的「解码时长完整性兜底」必须真的会拦下旧缺陷。

    没有这条，兜底就只是一段永远不触发的代码 —— 而它存在的全部意义
    就是拦住「静默解出短音频」。这里把旧写法装回去，断言它报错。
    """
    m4a = _make_m4a(tmp_path / "tone.m4a", seconds=3.0)
    monkeypatch.setattr(tool, "_frame_to_mono", _buggy_downmix)

    with pytest.raises(SystemExit) as ei:
        tool.load_audio(m4a)
    msg = str(ei.value)
    assert "解码异常" in msg
    assert "3.0s" in msg  # 报错里要带容器自报时长，便于定位
