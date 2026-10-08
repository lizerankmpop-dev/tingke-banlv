"""预校对工具（scripts/preproof_transcript.py）与校对页模板（scripts/review_page.py）的常驻用例

为什么必须有这组测试
--------------------
预校对产出的 `*.preproof.txt` 是 P3 的**评估基准**，而 `*.suspects.md` 是人工复核的
**唯一入口**。这两样东西出错都不会抛异常：

* 修正表里某个模式写错一个字 → 永远匹配不上 → 该错字被原样留进基准。
  本文件的 `test_unmatched_patterns_*` 与「每行 fix/suspect 至少命中一次」的
  `--check-only` 是同一道防线的两层。
* 存疑清单少列条目 → 人工**漏听**那一段 → 基准里带着一个没人确认过的碎片。
  这条是本轮实测踩到的真 bug（见 `test_suspects_field_matches_actual_markers`）。

约定：全部变异在进程内构造数据，不落任何真实文件、不读素材、不跑模型。
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "preproof_transcript.py"

pytestmark = pytest.mark.skipif(
    not SCRIPT.is_file(), reason="scripts/preproof_transcript.py 不存在（仓库不完整？）"
)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # 必须先注册进 sys.modules 再 exec：被测模块用了
    # `from __future__ import annotations`，dataclasses 解析字符串注解时要回
    # sys.modules 找它所属的模块。少了这一步会报
    # 「annotation = 'str'」——一个与真实原因毫无关系的错。
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


@pytest.fixture(scope="module")
def pp():
    return _load("preproof_under_test", SCRIPT)


@pytest.fixture(scope="module")
def rp():
    return _load("review_page_under_test", REPO / "scripts" / "review_page.py")


def _small_draft():
    return {
        "info": {"audio": "clip.wav", "durationMinutes": 0.2,
                 "paragraphs": 2, "chars": 20},
        "segments": [
            {"index": 0, "text": "我们看到布可厅的这一打开了",
             "audioStart": 0.0, "audioEnd": 5.0},
            {"index": 1, "text": "招呼,招呼,招呼",
             "audioStart": 5.0, "audioEnd": 9.0},
        ],
    }


# ---------- 修正表解析 ----------

def test_parse_corrections_strips_leading_bom(pp):
    """表是给人编辑的：Excel / 记事本存一次就带 BOM，不能因此报「只收到 1 列」。

    反向断言：不带 BOM 的同一份内容必须解析出**完全相同**的结果 ——
    否则说明「去 BOM」顺手动到了别的字符。
    """
    body = "# 注释行\nfix\t会图\t绘图\t说明\nsuspect\t猜答来\t\t说明\n"
    with_bom = pp.parse_corrections("\ufeff" + body)
    without = pp.parse_corrections(body)
    assert [c.pattern for c in with_bom] == ["会图", "猜答来"]
    assert with_bom == without


def test_parse_corrections_keeps_pattern_verbatim(pp):
    """pattern / replacement 不做 strip：含前导空格或标点时 strip 会静默匹配不上。"""
    cs = pp.parse_corrections("fix\t 会图 \t 绘图 \tn\n")
    assert cs[0].pattern == " 会图 "
    assert cs[0].replacement == " 绘图 "
    assert cs[0].note == "n"          # note 才是去空白的那个


def test_parse_corrections_skips_blank_and_comment_lines(pp):
    cs = pp.parse_corrections("\n\n# 整行注释\nfix\tA\tB\t\n\n")
    assert len(cs) == 1 and cs[0].pattern == "A"


@pytest.mark.parametrize("bad, why", [
    ("fix\n", "只有一列（没有制表符）"),
    ("nope\tA\tB\t\n", "kind 非法"),
    ("fix\t\tB\t\n", "pattern 为空"),
    ("fix\tA\tA\t\n", "fix 空操作"),
])
def test_parse_corrections_rejects_malformed_rows(pp, bad, why):
    with pytest.raises(ValueError):
        pp.parse_corrections(bad)


def test_parse_corrections_accepts_two_columns(pp):
    """反向断言：只有 kind + pattern 两列是**合法**的（replacement 默认空串）。

    少了这条，上面那组「必须报错」的用例可以靠「一律报错」蒙对。
    """
    cs = pp.parse_corrections("suspect\t招呼\n")
    assert cs[0].replacement == "" and cs[0].pattern == "招呼"


def test_parse_corrections_rejects_duplicate_pattern(pp):
    """重复行会各执行一次，结果取决于行序 —— 属隐式行为，必须报错。"""
    with pytest.raises(ValueError):
        pp.parse_corrections("fix\tA\tB\t\nfix\tA\tC\t\n")


# ---------- 校验 / 排序 ----------

def test_validate_rejects_nested_suspects(pp):
    cs = pp.parse_corrections("suspect\tAKS\t\t\nsuspect\tAKS-Y\t\t\n")
    problems = pp.validate(cs)
    assert problems, "互相包含的两个 suspect 必须被拒绝（会嵌套出双重标记）"
    assert "AKS" in problems[0]


def test_validate_allows_disjoint_suspects(pp):
    """反向断言：不相交的 suspect 必须放行，避免上一条靠「一律报错」蒙对。"""
    cs = pp.parse_corrections("suspect\t大小和速度\t\t\nsuspect\t招呼\t\t\n")
    assert pp.validate(cs) == []


def test_order_for_apply_puts_all_fix_before_all_suspect(pp):
    cs = pp.parse_corrections("suspect\tBook厅\t\t\nfix\t布可\tBook\t\n")
    kinds = [c.kind for c in pp.order_for_apply(cs)]
    assert kinds == ["fix", "suspect"]


def test_order_for_apply_longest_pattern_first_within_group(pp):
    """短模式先跑会吃掉长模式的前缀，留下没说出口的残字且不报错。

    用合成 pattern 构造：`AB` → `X`、`ABC` → `Y`，文本 `ABC` 只能得到 `Y`。
    """
    cs = pp.parse_corrections("fix\tAB\tX\t\nfix\tABC\tY\t\n")
    out, _ = pp.apply_corrections("ABC", cs)
    assert out == "Y"


# ---------- 单段执行 ----------

def test_suspect_needs_fix_to_run_first(pp):
    """存疑模式按**修正后**的文本写：`布可厅` 要先变成 `Book厅` 才可能被标上。

    这条是本轮实测踩到的坑：顺序颠倒时该模式一条都匹配不上，**而工具照样跑完**。
    """
    cs = pp.parse_corrections("fix\t布可\tBook\t\nsuspect\tBook厅\t\t\n")
    out, _ = pp.apply_corrections("我们看到布可厅的这一打开了", cs)
    assert out == "我们看到【?Book厅?】的这一打开了"


def test_fix_group_reaches_fixpoint_across_ordering(pp):
    """一条 fix 的替换串造出另一条 fix 的 pattern 时，必须仍然生效。

    实测来历：规则表里同时有 `布可 → Book` 与 `Book厅的这一 → Book这一个`。
    后者更长 → 组内「长优先」让它**先**跑，此刻 `Book厅的这一` 还不存在 →
    静默不生效。唯一兜底是零命中守卫，但它只会说「这行没命中」，
    **不会告诉你是排序问题** —— 作者很可能去改 pattern 的写法，把真因盖住。

    修法是跑到不动点，于是「对着原始文本写」与「对着中间态写」两种写法都对。
    """
    cs = pp.parse_corrections("fix\t布可\tBook\t\nfix\tBook厅的这一\tBook这一个\t\n")
    out, applied = pp.apply_corrections("我们看到布可厅的这一打开了", cs)
    assert out == "我们看到Book这一个打开了", out
    assert {a.correction.pattern: a.count for a in applied} == {"布可": 1, "Book厅的这一": 1}


def test_fix_group_is_idempotent_across_passes(pp):
    """反向断言：多跑几轮不能重复计数，也不能改动已收敛的文本。"""
    cs = pp.parse_corrections("fix\t会图\t绘图\t\nfix\t会治\t绘制\t\n")
    out, applied = pp.apply_corrections("我们会图也会治图线", cs)
    assert out == "我们绘图也绘制图线"
    assert [a.count for a in applied] == [1, 1], [a.count for a in applied]


def test_mutually_recursive_fixes_raise_instead_of_spinning(pp):
    """反向断言：`A→B` 与 `B→A` 这种互相咬合的表必须**报错**，不能无声转圈。"""
    cs = pp.parse_corrections("fix\t甲\t乙\t\nfix\t乙\t甲\t\n")
    with pytest.raises(ValueError, match="仍未收敛"):
        pp.apply_corrections("甲", cs)


def test_apply_corrections_refuses_already_marked_text(pp):
    """重复执行会把标记套成两层，而输出看着仍然「正常」—— 必须挡住。

    反向断言：同一段**未带标记**的文本不得抛异常。
    """
    cs = pp.parse_corrections("suspect\t招呼\t\t\n")
    with pytest.raises(ValueError):
        pp.apply_corrections("【?招呼?】", cs)
    assert pp.apply_corrections("招呼", cs)[0] == "【?招呼?】"


def test_has_markers_detects_both_halves(pp):
    assert pp.has_markers("【?x?】")
    assert pp.has_markers("开头【?")
    assert pp.has_markers("?】结尾")
    assert not pp.has_markers("干干净净")


# ---------- 存疑片段回读（本轮修的 bug） ----------

def test_extract_suspects_counts_every_occurrence(pp):
    """同一段里出现 N 次就要回读 N 个：只答「模式在不在」会少报 N-1 个。"""
    got = pp.extract_suspects("【?招呼?】,【?招呼?】,【?招呼?】")
    assert got == ["招呼", "招呼", "招呼"]


def test_extract_suspects_reads_output_not_raw(pp):
    """回读必须直接切标记，不能拿某份文本按模式匹配 —— 否则会漏掉只在 fix 后才成立的片段。"""
    assert pp.extract_suspects("【?Book厅?】的") == ["Book厅"]
    assert pp.extract_suspects("布可厅的") == []


def test_suspects_field_matches_actual_markers(pp):
    """不变量：`segments[].suspects` 的总数必须等于输出文本里真实标记数。

    这是本轮真 bug 的回归：第一版按**原始草稿**匹配模式，于是
    `Book厅`（要等 `布可→Book` 跑完才存在）在清单里**整个消失**，
    工具自报「共 46 处」而实际有 55 处 —— 少列的 9 处就是人工会漏听的 9 处。
    """
    cs = pp.parse_corrections("fix\t布可\tBook\t\nsuspect\tBook厅\t\t\nsuspect\t招呼\t\t\n")
    draft, applied = pp.preproof(_small_draft(), cs)

    field_total = sum(len(s.get("suspects") or []) for s in draft["segments"])
    real_total = sum(len(re.findall(r"【\?(.*?)\?】", s["text"])) for s in draft["segments"])
    assert field_total == real_total, (
        f"存疑清单条目数（{field_total}）≠ 输出文本真实标记数（{real_total}）——"
        f" 清单少列 = 人工漏听，而且不会有任何报错")
    assert real_total == 4, f"构造用例应恰好 4 处标记，实际 {real_total}"
    assert draft["segments"][0]["suspects"] == ["Book厅"]
    assert draft["segments"][1]["suspects"] == ["招呼"] * 3
    assert len(applied) == 3


def test_preproof_keeps_raw_text_and_recounts(pp):
    """原识别文本要留档（日后核对「改动前是什么」），字数按**段落**重算。"""
    cs = pp.parse_corrections("fix\t布可\tBook\t\n")
    draft, _ = pp.preproof(_small_draft(), cs)
    assert draft["segments"][0]["rawText"] == "我们看到布可厅的这一打开了"
    assert draft["segments"][0]["text"] == "我们看到Book厅的这一打开了"
    assert draft["info"]["paragraphs"] == 2
    assert draft["info"]["chars"] == len("我们看到Book厅的这一打开了") + len("招呼,招呼,招呼")


def test_preproof_draft_inputs_are_not_mutated(pp):
    """不得就地改输入：草稿是后续 A/B 对照的基准，被改掉就再也回不去了。"""
    src = _small_draft()
    cs = pp.parse_corrections("fix\t布可\tBook\t\n")
    pp.preproof(src, cs)
    assert src["segments"][0]["text"] == "我们看到布可厅的这一打开了"
    assert "rawText" not in src["segments"][0]


# ---------- 静默失效防御 ----------

def test_unmatched_patterns_flags_rows_that_never_fire(pp):
    """一次都没命中的行必须报出来 —— 这是本模块最需要防的失败形态：静默失效。"""
    cs = pp.parse_corrections("fix\t会图\t绘图\t\nfix\t单内的宽度\t单栏的宽度\t\n")
    _out, applied = pp.apply_corrections("我们会图数据", cs)
    missing = pp.unmatched_patterns(cs, applied)
    assert [c.pattern for c in missing] == ["单内的宽度"]


def test_unmatched_patterns_ignores_note_kind(pp):
    """`note` 只登记不改动，不该参与「必须命中」的判定（反向断言）。"""
    cs = pp.parse_corrections("note\t任意描述\t\t\n")
    assert pp.unmatched_patterns(cs, []) == []


def test_unmatched_patterns_counts_multi_hit_as_hit(pp):
    """命中多次仍是「命中」，不能因为列表长度不等于 1 就报成死的。"""
    cs = pp.parse_corrections("fix\t招呼\t哈喽\t\n")
    _out, applied = pp.apply_corrections("招呼,招呼", cs)
    assert pp.unmatched_patterns(cs, applied) == []


# ---------- 清单 / 变更表 ----------

def test_write_suspects_md_declares_real_total_and_marks_multiplicity(pp):
    cs = pp.parse_corrections("fix\t布可\tBook\t\nsuspect\tBook厅\t\t备注甲\nsuspect\t招呼\t\t备注乙\n")
    draft, _ = pp.preproof(_small_draft(), cs)
    md = pp.write_suspects_md("clip.preproof", draft["segments"], cs)

    assert "共 4 处，分布在 2 段。" in md, md.splitlines()[:8]
    assert "`Book厅`" in md, "只在 fix 之后才成立的片段也必须进清单"
    assert "`招呼` ×3" in md, "同一段多次出现要合并成一行并标次数"
    assert "备注甲" in md and "备注乙" in md
    assert md.count("| `招呼`") == 1, "3 行内容完全相同的重复行会把 1 个位置读成 3 个位置"


def test_write_changes_tsv_has_header_and_rows(pp):
    cs = pp.parse_corrections("fix\t布可\tBook\t说明\nsuspect\t招呼\t\t\n")
    _draft, applied = pp.preproof(_small_draft(), cs)
    tsv = pp.write_changes_tsv(applied)
    lines = [l for l in tsv.strip().splitlines() if l]
    assert lines[0] == "kind\tpattern\treplacement\tcount\tsegments\tnote"
    assert any(l.startswith("fix\t布可\tBook\t1\t0") for l in lines), lines


# ---------- 校对页模板（单一来源 + 键隔离） ----------

def _render(rp, stem: str, audio: str, key_audio: str) -> str:
    info = {"audio": key_audio, "durationMinutes": 0.2, "paragraphs": 1, "chars": 3}
    segs = [{"index": 0, "text": "【?招呼?】", "audioStart": 0.0, "audioEnd": 1.0}]
    return rp.render_review_html(stem, audio, segs, info)


def test_render_review_html_fills_all_placeholders(rp):
    html = _render(rp, "clip.preproof", "clip.wav", "clip.preproof.wav")
    for ph in ("__STEM__", "__AUDIO__", "__DRAFT__"):
        assert ph not in html, f"占位符 {ph} 没被替换干净"


def test_render_review_html_audio_src_is_real_audio_file(rp):
    """`<audio src>` 必须是音频**真实文件名**，不能是加了隔离后缀的那个。"""
    html = _render(rp, "clip.preproof", "clip.wav", "clip.preproof.wav")
    assert 'src="clip.wav"' in html
    assert 'src="clip.preproof.wav"' not in html


def test_render_review_html_export_uses_stem(rp):
    html = _render(rp, "clip.preproof", "clip.wav", "clip.preproof.wav")
    assert "a.download = 'clip.preproof' + '.txt'" in html


def _draft_payload(html: str) -> dict:
    return json.loads(re.search(r"const DRAFT = (\{.*?\});\n", html, re.S).group(1))


def _page_key(html: str) -> str:
    """按页面里**实际写的那段表达式**算键，而不是在测试里照抄一遍。

    踩过的坑：第一版直接写 `"tingke-transcript-" + payload["info"]["audio"]`，
    看着一模一样，其实是在测试里把被测逻辑重写了一份 —— 模板里的表达式改成
    什么都不影响它，断言退化成**自证**（变异探针 F 当时就是绿的）。
    正确做法是先把表达式从页面里抠出来，再照着它求值；遇到不认识的成分就报错，
    而不是默默返回一个"看起来对"的串。
    """
    expr = re.search(r"const KEY = (.+?);", html).group(1)
    payload = _draft_payload(html)
    out = ""
    for part in (p.strip() for p in expr.split("+")):
        if part.startswith("'"):
            out += part.strip("'")
        elif part.startswith("DRAFT.info."):
            out += str(payload["info"][part[len("DRAFT.info."):]])
        else:
            raise AssertionError(f"模板里的 KEY 表达式出现了测试不认识的成分：{part!r}")
    return out


def test_review_page_localstorage_key_follows_info_audio(rp):
    """两根页面不能共用 localStorage 键：模板只比条数就还原，会静默串稿。"""
    a = _render(rp, "clip", "clip.wav", "clip.wav")
    b = _render(rp, "clip.preproof", "clip.wav", "clip.preproof.wav")
    assert _page_key(a) == "tingke-transcript-clip.wav"
    assert _page_key(b) == "tingke-transcript-clip.preproof.wav"
    assert _page_key(a) != _page_key(b)


def test_review_page_embeds_segments_as_json(rp):
    """内嵌 JSON 必须能被 json.loads 解析且段数一致 —— 模板里手拼字符串会破坏转义。"""
    html = _render(rp, "clip", "clip.wav", "clip.wav")
    payload = _draft_payload(html)
    assert len(payload["segments"]) == 1
    assert payload["segments"][0]["text"] == "【?招呼?】"
