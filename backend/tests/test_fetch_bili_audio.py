"""B 站素材拉取工具（scripts/fetch_bili_audio.py）的常驻用例

为什么必须有这组测试
--------------------
这个脚本会直接决定 P3 评估用的**是哪个样本**。如果它的时间解析、音频轨选择或
切片边界出错，我们不会看到报错 —— 只会拿到一段「看起来正常」的音频，然后基于
它得出「真实网课 CER 是多少」的结论。**错误的样本比没有样本更糟。**

所以这里只测不碰网络的纯逻辑，但每条都要有反向断言：

* `slice_samples` 越界时必须**报错**。最反直觉的坏实现是静默裁剪 —— 它会让
  「截取 00:40~11:10」在源视频只有 8 分钟时悄悄变成 7 分 20 秒，且无人察觉。
  因此下面专门有一条用例断言「越界 → 抛异常」，而不是「返回非空数组」。
* `extract_bv` 必须**保留 BV 号内部的大小写**。B 站 BV 号大小写敏感，
  整体 upper() 或 lower() 会得到另一个不存在的视频号。
* `pick_audio_stream` 无可用轨时必须**抛异常而非返回 None**，否则问题会被
  推迟到解码阶段，报错信息更含糊。

约定：变异在进程内构造数据，不落任何真实文件，不发起任何网络请求。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "fetch_bili_audio.py"

pytestmark = pytest.mark.skipif(
    not SCRIPT.is_file(), reason="scripts/fetch_bili_audio.py 不存在（仓库不完整？）"
)


def _load_tool():
    spec = importlib.util.spec_from_file_location("fetch_bili_audio_under_test", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


# ============ 时间解析 ============


def test_plain_number_is_seconds_not_minutes(tool):
    """`90` 必须解释成 90 秒。若实现成「分钟」会得到 5400 秒，静默跑飞。"""
    assert tool.parse_timecode("90") == 90.0


def test_mmss_and_hms(tool):
    assert tool.parse_timecode("00:40") == 40.0
    assert tool.parse_timecode("11:10") == 670.0
    assert tool.parse_timecode("1:02:03") == 3723.0
    assert tool.parse_timecode("1:02:03.5") == pytest.approx(3723.5)


def test_rejects_minute_or_second_overflow(tool):
    """反向断言：分/秒位越界必须报错。

    去掉脚本里那两个 `>= 60` 判断，`00:99` 会被算成 99 秒照常返回。
    """
    with pytest.raises(ValueError):
        tool.parse_timecode("1:99:00")
    with pytest.raises(ValueError):
        tool.parse_timecode("00:99")


def test_rejects_garbage_and_empty(tool):
    for bad in ("", "   ", None, "abc", "1:2:3:4", "--:--"):
        with pytest.raises(ValueError):
            tool.parse_timecode(bad)


def test_format_timecode(tool):
    assert tool.format_timecode(40) == "00:40"
    assert tool.format_timecode(670) == "11:10"
    assert tool.format_timecode(3723) == "1:02:03"


# ============ BV 号解析 ============


def test_extract_bv_from_url_and_bare(tool):
    assert tool.extract_bv("BV1BA411i7PT") == "BV1BA411i7PT"
    assert tool.extract_bv("https://www.bilibili.com/video/BV1BA411i7PT") == "BV1BA411i7PT"
    assert tool.extract_bv(
        "https://www.bilibili.com/video/BV1BA411i7PT?p=1&t=40"
    ) == "BV1BA411i7PT"


def test_extract_bv_normalizes_prefix_but_keeps_body_verbatim(tool):
    """契约：`BV` 前缀统一成大写，**后面 10 位原样保留**。

    为什么不做「规范化大小写」：BV 号的大小写是**信息**，不是格式。
    用户给小写输入时，正确写法无从还原 —— 猜一个「看起来对」的大小写组合，
    只会得到另一个视频号或一个含糊的 400 错误，比直接失败更难排查。
    所以这里断言的是「原样透传 + 前缀归一」，不是「猜出规范写法」。
    """
    assert tool.extract_bv("bv1ba411i7pt") == "BV1ba411i7pt"
    assert tool.extract_bv("看看这个 bv1ba411i7pt 教程") == "BV1ba411i7pt"
    # 前缀一定被归一
    assert tool.extract_bv("bv1BA411i7PT").startswith("BV")


def test_extract_bv_preserves_inner_case(tool):
    """大小写必须原样保留 —— 整体 upper() 会指向另一个（不存在的）视频号。"""
    assert tool.extract_bv("BV1BA411i7PT").endswith("1BA411i7PT")
    assert tool.extract_bv("BV1BA411i7PT") != "BV1BA411I7PT"


def test_extract_bv_rejects_rubbish(tool):
    for bad in ("", None, "https://www.bilibili.com/video/av328693264", "BV123"):
        with pytest.raises(ValueError):
            tool.extract_bv(bad)


# ============ 音频轨选择 ============


def test_pick_audio_prefers_30280(tool):
    streams = [
        {"id": 30216, "bandwidth": 65552, "baseUrl": "a"},
        {"id": 30280, "bandwidth": 175955, "baseUrl": "b"},
    ]
    assert tool.pick_audio_stream(streams)["baseUrl"] == "b"


def test_pick_audio_falls_back_to_highest_bandwidth(tool):
    streams = [
        {"id": 99999, "bandwidth": 100, "baseUrl": "low"},
        {"id": 88888, "bandwidth": 900, "baseUrl": "high"},
    ]
    assert tool.pick_audio_stream(streams)["baseUrl"] == "high"


def test_pick_audio_skips_entries_without_baseurl(tool):
    streams = [
        {"id": 30280, "bandwidth": 175955},  # 无 baseUrl，不可用
        {"id": 30216, "bandwidth": 65552, "baseUrl": "ok"},
    ]
    assert tool.pick_audio_stream(streams)["baseUrl"] == "ok"


def test_pick_audio_raises_on_empty_instead_of_returning_none(tool):
    """反向断言：拿不到音频轨必须抛异常。返回 None 会把故障推迟到解码阶段。"""
    with pytest.raises(ValueError):
        tool.pick_audio_stream([])
    with pytest.raises(ValueError):
        tool.pick_audio_stream([{"id": 30280, "bandwidth": 1}])


# ============ 切片边界 ============


def test_slice_samples_basic(tool):
    import numpy as np

    audio = np.arange(16000 * 3, dtype=np.float32)
    clip = tool.slice_samples(audio, 16000, 1.0, 2.0)
    assert clip.size == 16000
    assert clip[0] == pytest.approx(16000.0)


def test_slice_samples_rejects_out_of_range(tool):
    """反向断言：越界必须报错。

    若实现改成 `min(i1, len(audio))` 这类静默裁剪，这条会红 —— 而「返回非空数组」
    这种弱断言根本抓不到它。
    """
    import numpy as np

    audio = np.zeros(16000 * 8, dtype=np.float32)  # 只有 8 秒
    with pytest.raises(ValueError) as ei:
        tool.slice_samples(audio, 16000, 40.0, 670.0)
    assert "超出" in str(ei.value)


def test_slice_samples_rejects_nonpositive_length(tool):
    import numpy as np

    audio = np.zeros(16000 * 10, dtype=np.float32)
    with pytest.raises(ValueError):
        tool.slice_samples(audio, 16000, 5.0, 5.0)
    with pytest.raises(ValueError):
        tool.slice_samples(audio, 16000, 6.0, 5.0)
    with pytest.raises(ValueError):
        tool.slice_samples(audio, 16000, -1.0, 5.0)


# ============ 音频体检 ============


def test_audio_stats_flags_silence(tool):
    """全静音片段必须被判为「近静音占比 100%」。

    截取区间很容易落进片头音乐或长时间停顿，用户在跑 ASR 之前无法察觉。
    """
    import numpy as np

    silent = np.zeros(16000 * 5, dtype=np.float32)
    s = tool.audio_stats(silent, 16000)
    assert s["quietRatio"] == pytest.approx(1.0)
    assert s["peakDbfs"] is None


def test_audio_stats_accepts_tone(tool):
    import numpy as np

    t = np.arange(16000 * 5, dtype=np.float32) / 16000.0
    tone = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    s = tool.audio_stats(tone, 16000)
    assert s["quietRatio"] < 0.1
    assert s["peakDbfs"] is not None and s["peakDbfs"] < 0


def test_write_wav_roundtrip(tool, tmp_path):
    """写出的 WAV 必须能被标准库读回，且参数为 16k/单声道/16-bit。"""
    import wave

    import numpy as np

    t = np.arange(16000, dtype=np.float32) / 16000.0
    tone = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    out = tmp_path / "t.wav"
    tool.write_wav(out, tone, 16000)

    with wave.open(str(out), "rb") as w:
        assert w.getframerate() == 16000
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getnframes() == 16000


def test_write_wav_clips_instead_of_wrapping(tool, tmp_path):
    """超过 ±1.0 的样本必须被夹紧。若直接 astype(int16) 会溢出回绕成噪声。"""
    import wave

    import numpy as np

    loud = np.array([2.0, -2.0, 0.0] * 1000, dtype=np.float32)
    out = tmp_path / "loud.wav"
    tool.write_wav(out, loud, 16000)

    with wave.open(str(out), "rb") as w:
        frames = w.readframes(w.getnframes())
    vals = np.frombuffer(frames, dtype="<i2")
    assert vals.max() == 32767
    assert vals.min() == -32767
