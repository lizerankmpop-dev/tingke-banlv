"""提示词回显清洗（backend/asr/prompt_echo.py）的常驻用例

这个文件为一个**只有真实素材才能暴露**的缺陷而存在
--------------------------------------------------
静音段上 Whisper 会把 `initial_prompt` 接着念下去，于是「请使用简体中文转写。」
变成了识别结果本身。B站 Origin 课程（630s，近静音 27.6%）实测命中 17 次；
而 `make_asr_testset.py` 生成的 TTS 代理集是连续朗读、没有静音段，
**这个缺陷在代理集上永远不会触发** —— 所以必须有常驻用例把它钉住。

三层验证
--------
1. **纯函数**：两条规则（纯回显丢弃 / 混入则剔除子串）与计数。
2. **不变量**：`除了删掉回显字面量，任何非分隔符字符都不许变`。
   这条最关键 —— 清洗最危险的失败形态不是漏删，而是**顺手改掉了正常内容**，
   而那种改动不会报错、只会让逐字稿不知不觉偏离真实录音。
3. **接线顺序（源码结构守卫）**：清洗必须发生在 `on_partial` 回调与
   `_last_text` 赋值**之前**。顺序错了不会报错，只会让回显进入下一轮提示词、
   形成自我喂食（实测同一块内滚到 16 次）—— 纯函数测试完全守不住这一点。

约定：变异在进程内构造，不落任何真实文件，不加载任何模型。
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pytest

from asr.prompt_echo import (
    MIN_ECHO_CHARS,
    EchoCleaner,
    build_blacklist,
    clean_text,
    find_echo_spans,
    normalize_with_map,
)
from asr.whisper_engine import SIMP_HINT, WhisperEngine, get_simp_hint

REPO = Path(__file__).resolve().parents[2]
ENGINE_SRC = REPO / "backend" / "asr" / "whisper_engine.py"

BLACKLIST = build_blacklist(SIMP_HINT)


def _is_sep(ch: str) -> bool:
    cat = unicodedata.category(ch)
    return cat[0] == "P" or cat[0] == "Z"


def _nonsep(s: str) -> str:
    return "".join(ch for ch in s if not _is_sep(ch))


def _nonsep_without_echo(s: str, blacklist: list[str]) -> str:
    """独立实现「只删回显字面量」，作为不变量测试的对照口径。

    刻意不复用被测代码的任何函数：如果两边共享同一段逻辑，
    它错了测试也跟着错，就变成了自证。
    """
    out = _nonsep(s)
    for lit in blacklist:
        out = out.replace(lit, "")
    return out


# ============ 规则 ①：纯回显 → 整段丢弃 ============


def test_pure_echo_full_sentence_is_dropped():
    r = clean_text("请使用简体中文转写。", BLACKLIST)
    assert r.isPureEcho is True
    assert r.text == ""


def test_pure_echo_with_repeated_ascii_separators():
    """实测形态：模型吐的是半角逗号 + 空格，与提示词的全角标点逐字不同。

    若实现用 `str.replace(SIMP_HINT, ...)` 直接匹配，这条会原样漏掉 ——
    清洗看起来在跑，实际一条都没删。
    """
    r = clean_text("使用简体中文转写, 使用简体中文转写, 使用简体中文转写,", BLACKLIST)
    assert r.isPureEcho is True
    assert r.text == ""


def test_pure_echo_whole_hint_is_dropped():
    r = clean_text(SIMP_HINT, BLACKLIST)
    assert r.isPureEcho is True
    assert r.text == ""


def test_almost_pure_echo_is_not_dropped():
    """反向断言：**只差一个字**也不算纯回显，必须保留。

    这是「宁可留噪声，也不丢识别结果」的边界。若把覆盖率判据放宽
    （`covered_ratio > 0.9` 之类），那个残字会被静默丢掉。
    """
    r = clean_text("请使用简体中文转写。好", BLACKLIST)
    assert r.isPureEcho is False
    assert "好" in r.text


# ============ 规则 ②：混入 → 子串剔除，其余原样保留 ============


def test_mixed_text_keeps_real_content():
    raw = "我们要继续做行图线, 使用简体中文转写, 使用简体中文转写, 也就是这个文件了。"
    r = clean_text(raw, BLACKLIST)
    assert r.isPureEcho is False
    # 相邻/重叠的回显会被合并成一个区间再删，所以处数是 1 而不是 2
    assert r.hits >= 1
    assert "我们要继续做行图线" in r.text
    assert "也就是这个文件了" in r.text
    # 回显已被清掉
    assert "简体中文转写" not in r.text


def test_invariant_non_separator_chars_are_never_altered():
    """核心不变量：除了删回显，非分隔符字符一个都不许多、少、改。"""
    samples = [
        "我们要继续做行图线, 使用简体中文转写, 使用简体中文转写, 也就是这个文件了。",
        "请使用简体中文转写。",
        "使用简体中文转写, 使用简体中文转写,",
        "这里是正常的一句讲课内容, 完全不涉及提示词。",
        "以下是普通话的句子，请使用简体中文转写。我们打开 Origin 以后。",
        "混合中间 请使用简体中文转写 夹在中间的写法。",
    ]
    for raw in samples:
        got = clean_text(raw, BLACKLIST)
        assert _nonsep(got.text) == _nonsep_without_echo(raw, BLACKLIST), (
            f"清洗动了非分隔符字符：{raw!r} → {got.text!r}"
        )


def test_normal_sentence_untouched():
    raw = "我们现在要往坐标系中加速数据，点击图，选择图表绘制这一项。"
    r = clean_text(raw, BLACKLIST)
    assert r.hits == 0
    assert r.isPureEcho is False
    assert r.text == raw


def test_terms_are_never_stripped():
    """反向断言：术语（老师真会念的词）绝不能被清掉。

    黑名单若被扩展到术语表，「图层」「坐标轴」这些正常内容会被静默删除 ——
    这正是本条要拦住的未来改动。
    """
    raw = "图层、坐标轴、误差棒、拟合、归一化，这些都是常用操作。"
    r = clean_text(raw, BLACKLIST)
    assert r.hits == 0
    assert r.text == raw


def test_blacklist_only_derived_from_hint_not_terms():
    """黑名单的每一条都必须是引导语（去分隔符后）的子串。

    把「派生」写成「另抄一份常量」会让文案一改就静默失效；
    而把术语表并进来又会误删正常内容。两个方向都由这条钉住。
    """
    hint_norm = _nonsep(SIMP_HINT)
    for lit in BLACKLIST:
        assert lit in hint_norm, f"黑名单出现了引导语里没有的字面量：{lit!r}"
    assert BLACKLIST, "默认引导语必须能派生出至少一条字面量"


def test_blacklist_includes_politeness_variant():
    """实测两种形态都要覆盖：带「请」与不带「请」。"""
    assert "使用简体中文转写" in BLACKLIST


def test_blacklist_includes_clause_suffix_variant():
    """从句中接上引导语也要能救：`普通话的句子` 是第一个分句的**后缀**。

    实测残留（B站 Origin 课，段 29）：老师说了「这一项是」，模型接着把引导语从句中
    续上，吐成 `这一项是普通话的句子`。整句派生与剥礼貌前缀**都生成不出**
    `普通话的句子` —— 前缀/整句和这个后缀没有任何公共路径 —— 于是这条一直漏救，
    只能靠人工在校对表里补一刀（旧注释里那句「部分回显会漏救」说的就是它）。

    判据用**后缀覆盖率**而不是只测那一个串：只钉一个串的话，以后有人把派生写成
    「硬编码这一个例外」也能过，而下一个变体照旧漏。
    """
    assert "普通话的句子" in BLACKLIST
    for clause in ("以下是普通话的句子", "请使用简体中文转写"):
        for i in range(len(clause) - MIN_ECHO_CHARS + 1):
            assert clause[i:] in BLACKLIST, f"分句后缀 {clause[i:]!r} 未派生"


def test_partial_echo_starting_mid_clause_keeps_real_head():
    """反向断言：剔回显**不能连真实语音一起删掉**。

    这里 `这一项` 是老师真说的话，`普通话的句子` 才是回显 —— 清洗后必须恰好剩下
    `这一项`。删多（把句首也当回显）或删少（留着回显尾巴）都是错的，
    而两者都不会报错。
    """
    got = clean_text("这一项是普通话的句子", BLACKLIST)
    assert got.text == "这一项", f"应只剩真实语音部分，实际 {got.text!r}"
    assert got.isPureEcho is False, "这不是纯回显，不该整段丢弃"


def test_ordinary_sentences_are_not_touched_by_suffix_derivation():
    """反向断言：后缀派生只认引导语的后缀，不能扩大成「含常见词就删」。"""
    for s in ("大家注意句子的结构", "我们今天讲普通话的发音", "这一项我们先跳过"):
        got = clean_text(s, BLACKLIST)
        assert got.text == s, f"正常句子被误删：{s!r} → {got.text!r}"


# ============ 派生 / 归一化 ============


def test_blacklist_follows_custom_hint(monkeypatch):
    """引导语被 `TINGKEBANLV_SIMP_HINT` 覆盖时，黑名单必须跟着走。

    若实现里另抄了一份 SIMP_HINT 常量，这条会红 —— 而且是**静默**红：
    产品改了文案，清洗还在，却再也匹配不到任何东西。
    """
    custom = "请务必输出繁体以外的规范字形。"
    monkeypatch.setenv("TINGKEBANLV_SIMP_HINT", custom)
    assert get_simp_hint() == custom
    derived = build_blacklist(get_simp_hint())
    assert derived, "自定义引导语也应能派生字面量"
    assert all(lit in _nonsep(custom) for lit in derived)
    assert "使用简体中文转写" not in derived


def test_blacklist_is_sorted_long_first():
    """长字面量必须先匹配，否则短变体会把长句切碎、留下残字。"""
    lengths = [len(x) for x in BLACKLIST]
    assert lengths == sorted(lengths, reverse=True)


def test_blacklist_respects_min_chars():
    short = "转写"
    assert build_blacklist(short) == []
    assert len(short) < MIN_ECHO_CHARS


def test_normalize_with_map_is_invertible():
    text = "请使用简体中文转写。"
    norm, idx_map = normalize_with_map(text)
    assert norm == "请使用简体中文转写"
    assert len(norm) == len(idx_map)
    # 映射回原串能取到同一字符
    assert all(text[i] == c for c, i in zip(norm, idx_map))


def test_find_echo_spans_are_original_indices():
    text = "abc, 请使用简体中文转写。def"
    spans = find_echo_spans(text, BLACKLIST)
    assert len(spans) == 1
    s, e = spans[0]
    assert text[s:e] == "请使用简体中文转写"


# ============ tidy 开关 ============


def test_tidy_collapses_leftover_separators():
    raw = "我们要继续做行图线, 使用简体中文转写, 使用简体中文转写, 使用简体中文转写,"
    got = clean_text(raw, BLACKLIST, tidy=True).text
    assert "  ," not in got
    assert ",," not in got
    assert got.endswith("行图线")


def test_tidy_off_keeps_separators_but_still_removes_echo():
    raw = "我们要继续做行图线, 使用简体中文转写,"
    got = clean_text(raw, BLACKLIST, tidy=False).text
    assert "简体中文转写" not in got
    assert got == "我们要继续做行图线, ,"


# ============ 埋点 ============


def test_stats_count_drops_and_substring_removals():
    c = EchoCleaner(SIMP_HINT)
    c.clean("请使用简体中文转写。")                      # 丢弃
    c.clean("我们要继续做行图线, 使用简体中文转写,")      # 剔除 1 处
    c.clean("完全正常的一句话。")                        # 不动

    s = c.stats.asDict()
    assert s["segmentsSeen"] == 3
    assert s["segmentsDropped"] == 1
    assert s["segmentsCleaned"] == 1
    assert s["substringsRemoved"] == 1
    assert s["charsRemoved"] > 0


def test_cleaner_is_inert_without_hint():
    c = EchoCleaner("")
    assert c.enabled is False
    assert c.inertReason
    r = c.clean("请使用简体中文转写。")
    assert r.text == "请使用简体中文转写。"  # 不动
    assert r.isPureEcho is False


def test_cleaner_reports_inert_reason_when_hint_too_short():
    """派生不出字面量时必须留痕，不能静默失效。"""
    c = EchoCleaner("转写")
    assert c.enabled is False
    assert c.inertReason and "过短" in c.inertReason


# ============ 接线：引擎侧 ============


def test_engine_enables_cleaner_for_chinese():
    engine = WhisperEngine(language="zh")
    stats = engine.echo_stats()
    assert stats["enabled"] is True
    assert stats["blacklistSize"] > 0


def test_engine_skips_cleaner_for_english():
    """英文场景不注入引导语，也就不可能回显 —— 清洗器必须同样不启用。"""
    engine = WhisperEngine(language="en")
    stats = engine.echo_stats()
    assert stats["enabled"] is False
    assert stats["inertReason"]


def _engine_src_without_comments() -> str:
    """读源码并剥掉注释与文档字符串。

    必须剥：说明性注释里会引用 `self._echo.clean(` 这类字面量，
    不剥就会把「注释里提到」当成「代码里调用」，守卫变成假绿。
    只剥整行注释与三引号块，不按 `#` 切行 —— 行内 `#` 可能属于字符串。
    """
    src = ENGINE_SRC.read_text(encoding="utf-8")
    src = re.sub(r'"""(?:.|\n)*?"""', "", src)
    src = re.sub(r"'''(?:.|\n)*?'''", "", src)
    lines = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#")]
    return "\n".join(lines)


def test_clean_runs_before_callback_and_before_last_text():
    """源码顺序守卫：清洗必须在回调与 `_last_text` 赋值之前。

    顺序错了不会报错：回显文本会进入 `_last_text`，下一轮又被当成提示词拼回去，
    于是越滚越多（实测同一块内 16 次）。纯函数测试对这一层完全无感。
    """
    src = _engine_src_without_comments()
    i_clean = src.index("self._echo.clean(")
    i_callback = src.index("self.on_partial(segment_data.text")
    i_last_text = src.index("self._last_text = segment_data.text")

    assert i_clean < i_callback, "清洗必须发生在 on_partial 回调之前"
    assert i_clean < i_last_text, "清洗必须发生在 _last_text 赋值之前（否则自我喂食）"


def test_pure_echo_branch_skips_callback():
    """纯回显分支必须在回调之前 `continue`，否则虚假字幕照样推给用户。"""
    src = _engine_src_without_comments()
    i_pure = src.index("if echo.isPureEcho:")
    i_callback = src.index("self.on_partial(segment_data.text")
    assert i_pure < i_callback
    # 该分支体内必须真的退出循环
    branch = src[i_pure:i_callback]
    assert "continue" in branch
