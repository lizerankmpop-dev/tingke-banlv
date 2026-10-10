"""`eval_asr_quality` 的 #175 新增件：词级时间戳延迟口径（`latency_word_based`）

为什么值得一组测试
------------------
旧延迟口径的死因是**结构性**的，不是参数没调好：

  `Recorder.segments` 记的是 **VAD 语音块**（`on_speech_segment` 回调），
  `Recorder.subtitles` 记的是 **whisper 段**（`on_partial` 回调），
  而一个 VAD 块里可以含好几个 whisper 段 ⇒ 实测 6→10、56→82、63→86，
  **字幕条数恒多于片段条数**。`pair()` 要求 `len(segs) == len(subs)`，
  这个等式在原理上就不成立，只有 lowvol 那种 6→6 的巧合才配对得上。

真正的修法不是「把配对放宽一点」（那只会产出看起来合理、实际错位的延迟，
旧注释已经点明这一点），而是**换锚点**：引擎的 `on_partial(text, words)` 本来就
交了词级时间戳，旧代码把它丢掉了。有了「这句话最后一个词的音频位置」，
压根不需要配对。

由此得到一个可验证的**恒等式**（本文件的核心断言）：

    latency_新 - latency_旧 == seg.audioEnd - audioWordsEnd >= 0

因为 VAD 块尾巴通常挂着一段静音，`audioWordsEnd <= seg.audioEnd` 恒成立，
差值恰是该块的静音尾巴长度。这不是巧合，而是两套口径唯一的定义性差别。

⚠️ 反过来说：**「新 p95 == 旧 p95」不是通过标准**。锚点不同，数值本来就不该相等；
把它当目标会逼着人去凑数（教训 9：断言重抄被测逻辑 = 自证）。

约定：全部在内存里造 Recorder，不读真实素材、不跑模型、不启动管道。
反向断言（教训 5）是本文件的重点：每个「应该是」都配一个「不该是」。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "eval_asr_quality.py"

pytestmark = pytest.mark.skipif(
    not SCRIPT.is_file(), reason="scripts/eval_asr_quality.py 不存在（仓库不完整？）"
)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # 必须先注册进 sys.modules 再 exec（理由见 test_eval_reference._load）
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


@pytest.fixture(scope="module")
def ev():
    return _load("eval_asr_quality_latency_words_under_test", SCRIPT)


# ===================== 1. word_end_seconds：取最靠后的 end =====================

def test_word_end_returns_none_for_empty(ev):
    assert ev.word_end_seconds(None) is None
    assert ev.word_end_seconds([]) is None


def test_word_end_uses_max_not_first_nor_last(ev):
    """乱序输入下必须取 **max(end)**，而不是第一个元素、也不是末元素的 end。

    三种取法在乱序数据上给出三个不同的数，所以这条断言真有分辨力 ——
    反向断言把这三种取法互相区分开（教训 5）。
    """
    words = [
        {"start": 0.0, "end": 1.0},
        {"start": 2.0, "end": 3.9},
        {"start": 1.0, "end": 2.5},
    ]
    assert ev.word_end_seconds(words) == pytest.approx(3.9)
    assert ev.word_end_seconds(words) != pytest.approx(1.0)   # 不是第一个
    assert ev.word_end_seconds(words) != pytest.approx(2.5)   # 不是最后一个元素


def test_word_end_accepts_object_form(ev):
    """引擎给的是 `WordTimestamp` 数据类，不是 dict —— 两种形态都要吃得下。"""
    class W:
        def __init__(self, end: float) -> None:
            self.end = end

    assert ev.word_end_seconds([W(1.5), W(4.25)]) == pytest.approx(4.25)


def test_word_end_skips_malformed_but_keeps_valid(ev):
    """坏元素跳过、好元素照用；**全坏** 才返回 None。"""
    words = [
        {"start": 0.0, "end": None},
        {"start": 1.0, "end": "not-a-number"},
        {"start": 2.0, "end": 7.5},
        {},
    ]
    assert ev.word_end_seconds(words) == pytest.approx(7.5)
    assert ev.word_end_seconds([{"end": None}, {"end": "x"}, {}]) is None


# ===================== 2. Recorder 是否真的留下了锚点 =====================

def test_on_subtitle_records_audio_words_end(ev):
    r = ev.Recorder()
    r.feed_t0 = 100.0
    r.on_subtitle("同学们好", [{"start": 1.0, "end": 3.5}])
    assert len(r.subtitles) == 1
    assert r.subtitles[0]["audioWordsEnd"] == pytest.approx(3.5)
    assert r.subtitles[0]["wordCount"] == 1


def test_on_subtitle_without_words_marks_none_not_zero(ev):
    """**反向断言**：拿不到锚点必须落成 `None`，绝不能是 0。

    0 会被下游读成「延迟极小」，把「算不出来」伪装成「性能极好」——
    满分级别的假结论，正是本项目最忌讳的静默形态（教训 14）。
    """
    r = ev.Recorder()
    r.feed_t0 = 100.0
    r.on_subtitle("无词戳", [])
    assert r.subtitles[0]["audioWordsEnd"] is None
    assert r.subtitles[0]["audioWordsEnd"] is not 0


def test_on_subtitle_ignores_blank_text(ev):
    """空文本不产生字幕（与 pipeline 一致），否则会虚增分母。"""
    r = ev.Recorder()
    r.feed_t0 = 100.0
    r.on_subtitle("", [{"start": 0.0, "end": 1.0}])
    r.on_subtitle("   ", [{"start": 0.0, "end": 1.0}])
    assert r.subtitles == []


# ===================== 3. words_latency()：不依赖配对的延迟 =====================

def test_words_latency_computes_anchor_based_value(ev):
    r = ev.Recorder()
    r.feed_t0 = 100.0
    r.on_subtitle("这句", [{"start": 3.0, "end": 3.5}])
    # pushWall = 100 + 3.5 + 2.0 = 105.5 → 延迟 = 105.5 - 103.5 = 2.0
    r.subtitles[0]["pushWall"] = 105.5
    out = r.words_latency()
    assert len(out) == 1
    assert out[0]["audioWordsEnd"] == pytest.approx(3.5)
    assert out[0]["latency"] == pytest.approx(2.0)
    # 反向：不是「pushWall - feed_t0」（那就是 5.5，等于把锚点整个丢掉）
    assert out[0]["latency"] != pytest.approx(5.5)


def test_words_latency_survives_count_mismatch(ev):
    """核心：**片段数 != 字幕数 时依然算得出来** —— 这正是旧口径的死穴。

    2 个 VAD 块 / 3 条字幕（结构性 1:N）。`pair()` 必须返回空（旧行为保留），
    而 `words_latency()` 必须 3 条全给。
    """
    r = ev.Recorder()
    r.feed_t0 = 50.0
    r.on_segment(None, 0.0, 4.0)
    r.on_segment(None, 4.0, 8.0)
    for end in (1.5, 4.5, 7.0):
        r.on_subtitle(f"字幕{end}", [{"start": end - 0.5, "end": end}])
    for sub in r.subtitles:
        sub["pushWall"] = 50.0 + sub["audioWordsEnd"] + 1.0

    assert r.pair() == []                    # 旧口径：仍然算不出来（保留，不删）
    wl = r.words_latency()
    assert len(wl) == 3                      # 新口径：三条全给
    assert [p["latency"] for p in wl] == pytest.approx([1.0, 1.0, 1.0])


def test_words_latency_skips_subtitles_without_anchor(ev):
    """**反向断言**：缺锚点的那条要被跳过，而不是拿 0 顶上。

    混入一条无词戳字幕：覆盖数从 2 掉到 2（该条不算），
    且「跳过的条数」要能被算出来（= 字幕总数 - 覆盖数），不能静默消失。
    """
    r = ev.Recorder()
    r.feed_t0 = 10.0
    r.on_subtitle("有词", [{"start": 0.0, "end": 2.0}])
    r.on_subtitle("无词", [])
    r.subtitles[0]["pushWall"] = 13.0
    wl = r.words_latency()
    assert len(wl) == 1
    assert len(r.subtitles) - len(wl) == 1   # 缺口可被算出来
    assert all(p["index"] != 1 for p in wl)  # 缺锚点那条确实不在结果里


def test_words_latency_empty_before_feed_start(ev):
    """`feed_t0` 还没设（==0）时不能算：否则锚点基准是 0，得到一堆巨大假延迟。"""
    r = ev.Recorder()
    r.on_subtitle("这句", [{"start": 0.0, "end": 1.0}])
    assert r.words_latency() == []


# ===================== 4. 核心恒等式：新 - 旧 == 静音尾巴 =====================

def test_identity_new_minus_old_equals_silence_tail(ev):
    """**本文件最重要的一条断言。**

    1 个 VAD 块 [0, 5.0]（readyWall = feed_t0 + 5.0），块内一条字幕最后
    一个词结束于 3.5 ⇒ 静音尾巴 1.5s。于是：

        latency_旧 = pushWall - (feed_t0 + 5.0)
        latency_新 = pushWall - (feed_t0 + 3.5)
        latency_新 - latency_旧 == 5.0 - 3.5 == 1.5

    这条恒等式把「实现是否用对了锚点」变成一个**可精确计算**的判据，
    比「新 p95 应该等于旧 p95」强得多 —— 后者锚点不同，本来就不该相等。
    """
    r = ev.Recorder()
    r.feed_t0 = 100.0
    r.on_segment(None, 0.0, 5.0)
    r.on_subtitle("同学们好", [{"start": 3.0, "end": 3.5}])
    r.subtitles[0]["pushWall"] = 105.5

    paired = r.pair()
    wl = r.words_latency()
    assert len(paired) == 1 and len(wl) == 1

    seg_end = paired[0]["audioEnd"]
    words_end = wl[0]["audioWordsEnd"]
    tail = seg_end - words_end
    assert tail == pytest.approx(1.5)

    assert paired[0]["latency"] == pytest.approx(0.5)
    assert wl[0]["latency"] == pytest.approx(2.0)
    assert wl[0]["latency"] - paired[0]["latency"] == pytest.approx(tail)

    # 方向性：静音尾巴 >= 0 ⇒ 新口径恒 >= 旧口径。反过来说明锚点取反了。
    assert tail >= 0
    assert wl[0]["latency"] >= paired[0]["latency"]
    # 反向断言：新口径不等于旧口径（锚点确实换了，而不是原地复读旧公式）
    assert wl[0]["latency"] != pytest.approx(paired[0]["latency"])


def test_identity_holds_when_tail_is_zero(ev):
    """边界：静音尾巴为 0（词尾 == 块尾）时两套口径**必须相等**。

    这是恒等式的下界情形，用来证明上一条的偏移真的来自「静音尾巴」，
    而不是两套公式之间存在某个固定常数偏差。
    """
    r = ev.Recorder()
    r.feed_t0 = 20.0
    r.on_segment(None, 0.0, 4.0)
    r.on_subtitle("刚好顶满", [{"start": 2.0, "end": 4.0}])
    r.subtitles[0]["pushWall"] = 25.0

    paired = r.pair()
    wl = r.words_latency()
    assert paired[0]["audioEnd"] - wl[0]["audioWordsEnd"] == pytest.approx(0.0)
    assert wl[0]["latency"] == pytest.approx(paired[0]["latency"])


# ===================== 5. 旧口径一字未动（回归守卫） =====================

def test_pair_still_requires_equal_counts(ev):
    """旧逻辑保留：条数不等 ⇒ 空结果。这是**刻意的**，不是待修的 bug。

    旧口径作为对照基线必须原样留着（约束红线③），所以这里反向锁死它，
    避免日后有人「顺手」把它放宽 —— 放宽会产出错位但看似合理的延迟。
    """
    r = ev.Recorder()
    r.feed_t0 = 1.0
    r.on_segment(None, 0.0, 1.0)
    r.on_segment(None, 1.0, 2.0)
    r.on_subtitle("只有一条", [{"start": 0.0, "end": 1.0}])
    assert r.pair() == []


def test_legacy_latency_field_keys_unchanged(ev):
    """接线守卫：旧 `latency` 的字段集合与语义不许被本改动波及。"""
    src = SCRIPT.read_text(encoding="utf-8")
    for key in ('"mean"', '"p50"', '"p95"', '"max"', '"firstHalfMean"', '"secondHalfMean"'):
        assert key in src, f"旧 latency 字段 {key} 丢了"
    # 新字段确实存在，且带上了口径三件套
    assert "latency_word_based" in src
    for key in ('"p99"', '"missingWords"', '"n"'):
        assert key in src, f"新口径字段 {key} 丢了"
    # 「缺锚点计数」必须**既落盘、又被汇总消费**。
    # 只在结果里写、顶层汇总不读 ⇒ 覆盖缺口在报告首页看不见（教训 36：
    # 新入口先跑通副作用链）。判据用**出现次数**而不是 `in`：
    # `in` 会被另一处（summarize 里的读取）顺手满足，永远绿（教训 2）。
    n_missing = src.count('"missingWords"')
    assert n_missing >= 2, (
        f'"missingWords" 应同时出现在结果字段与 summarize 汇总里，实际只有 {n_missing} 处'
    )


def test_new_metric_never_uses_zero_placeholder(ev):
    """守卫：新口径的统计量在无数据时必须走 `None`，**不许用 0.0 占位**。

    旧字段 `latency.p95` 在没配对时落 `0.0`（历史行为，保留不动、也不改它），
    新字段不能沿用这个坑：0 会被读成「延迟极小」，把「算不出来」伪装成
    「性能极好」—— 正是本项目最忌讳的静默假结论（教训 14）。

    mean/p50/p95/p99/max 五处都要有这个守卫，少一处就是一个静默洞。
    """
    src = SCRIPT.read_text(encoding="utf-8")
    assert src.count("if word_lat_values else None") >= 5, (
        "新口径的 mean/p50/p95/p99/max 五处都应带 `... if word_lat_values else None` 守卫，"
        f"实际只有 {src.count('if word_lat_values else None')} 处"
    )
    assert '"p95": percentile(word_lat_values, 0.95) if word_lat_values else None,' in src


def test_missing_words_guard_present(ev):
    """接线守卫：缺锚点必须走 `continue` 跳过，而不是落成 0。

    用源码级断言守住这条 —— 行为级断言（测试 3.3）能证明现在的行为对，
    这条能证明「跳过」这个决定没有被改成「补 0」。
    """
    src = SCRIPT.read_text(encoding="utf-8")
    assert "if anchor is None:" in src
    assert "return None" in src  # word_end_seconds 的「不可得」语义
