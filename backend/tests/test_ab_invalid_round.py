"""`ab_terms_compare.invalid_round_reason()` 的常驻用例（P2 #171 / H4）

为什么值一组测试
----------------
这条守卫拦的是**结构不可用**的轮次：`0 片段` 或 `0 字幕`。此时参考全文会被整块
delete，WER/CER 恒等于 **1.0** —— 表里看着像「识别很差」，其实什么都没测到。
放进对照表，它就会以「术语注入把 WER 从 1.0 降到 0.8」这种形态产出**完全虚假**的结论。

实测来历：英文测试集 `packaging/eval/short.json` 那一轮 = 8 段 → **0 条字幕**，
每格 WER 恒 1.0，却照样被写进了历史对照表（见 `docs/asr_eval/WER_REPORT_PLAN.md` §3.1）。

🔴 本文件最重要的一条是**反向断言**：`verdict.passed == False` 不得成为中止条件。
盘了 42 份历史子报告，10 份 `passed=False` —— 8 份是背压丢弃（另有专门守卫），
另 2 份里 `short-terms.json` 是**平均 RTF ≥ 1** 才判 False，那是「跑得慢」的如实
记录，准确率数据本身完全有效。拿 `passed` 当中止条件会把有效证据一起拦掉。
（方案文档 H4 原建议是 `totalSubtitles==0 或 passed==false`，此处按数据修正。）

约定：全部用内存字典 + 真实历史报告回灌，不跑模型、不起子进程。
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "ab_terms_compare.py"

pytestmark = pytest.mark.skipif(
    not SCRIPT.is_file(), reason="scripts/ab_terms_compare.py 不存在（仓库不完整？）"
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
def ab():
    return _load("ab_terms_compare_under_test", SCRIPT)


# ============================ 纯函数：判据本身 ============================

def test_usable_round_returns_none(ab):
    """正常一轮（有片段、有字幕）→ None。"""
    assert ab.invalid_round_reason({"totalSegments": 63, "totalSubtitles": 88}) is None


def test_zero_subtitles_is_invalid(ab):
    """H4 的核心情形：切出了片段但零字幕。"""
    why = ab.invalid_round_reason({"totalSegments": 8, "totalSubtitles": 0})
    assert why is not None
    assert "0 条字幕" in why
    assert "8" in why, "原因里要带上片段数，否则没法判断是 VAD 还是引擎的问题"


def test_zero_segments_is_invalid(ab):
    """对称情形：VAD 没工作 / 音频全静音。

    `"0 条字幕" not in why` 是**判序断言**：0 片段时若走字幕分支，会报出
    「0 个片段 → 0 条字幕」——结论没错，但把根因（VAD）说成了现象（字幕）。
    """
    why = ab.invalid_round_reason({"totalSegments": 0, "totalSubtitles": 0})
    assert why is not None
    assert "片段" in why
    assert "0 条字幕" not in why, "判序颠倒了：该报 VAD/静音，不该报字幕"


def test_zero_segments_reported_even_if_subtitles_present(ab):
    """反向断言：0 片段必然 0 字幕；即便字典自相矛盾（有字幕无片段）也判不可用。

    这与 `eval_asr_quality.verdict()` 的判序一致（先查片段再查字幕）——
    否则会报出「0 个片段 → 有字幕」这种无意义的结论。
    """
    why = ab.invalid_round_reason({"totalSegments": 0, "totalSubtitles": 5})
    assert why is not None and "片段" in why
    assert "0 条字幕" not in why, "判序颠倒了：该报 VAD/静音，不该报字幕"


def test_missing_keys_are_treated_as_zero(ab):
    """旧报告可能整个 summary 缺键 → 按 0 处理（保守：宁可拦下去人工看一眼）。"""
    assert ab.invalid_round_reason({}) is not None
    assert ab.invalid_round_reason({"files": 3}) is not None


def test_explicit_none_is_treated_as_zero(ab):
    """JSON 里的 `null` 会变成 Python `None`，不能被 `or 0` 之外的真值判断放过。"""
    why = ab.invalid_round_reason({"totalSegments": None, "totalSubtitles": None})
    assert why is not None


# ---------- 反向断言：不得过度拦截 ----------

def test_passed_false_with_valid_pipeline_is_usable(ab):
    """🔴 核心反向断言：`passed=False` 但管道正常 → **可用**。

    实例来自真实报告 `short-terms.json`：它 `passed=False` 的唯一原因是
    「平均 RTF ≥ 1」（lowvol 单文件 RTF 2.6），而它的 8 段 → 11 条字幕、
    WER 0.0455~0.8182 都是**真实测出来的**数。
    若把 `passed` 写进判据，这份证据会被静默拦掉 —— 本用例就是为了让那种改动变红。
    """
    assert ab.invalid_round_reason({"totalSegments": 8, "totalSubtitles": 11}) is None


def test_dropped_is_not_this_guard_s_business(ab):
    """职责边界：`totalDropped > 0` 由**另一条**守卫（`--allow-drops`）管。

    这里必须返回 None，否则两条守卫的判据会重叠，
    以后调其中一条时另一条的行为会跟着变（而没人会想到）。
    """
    assert ab.invalid_round_reason(
        {"totalSegments": 63, "totalSubtitles": 10, "totalDropped": 48}
    ) is None


def test_no_reference_is_not_this_guard_s_business(ab):
    """反面：无参考文本（WER 列全 `-`）也不在这里拦 —— 它只降级为**警告**。

    理由：没有逐字稿的真实录音是常态，「缺参考文本」是数据属性而不是这一轮跑坏了；
    直接中止会把「只测实时性」的合法用法一并禁掉。
    """
    assert ab.invalid_round_reason(
        {"totalSegments": 63, "totalSubtitles": 88, "withReference": 0}
    ) is None


# ============================ 真实历史报告回灌 ============================

_HIST = {
    "short.json": True,        # 🔴 8 段 → 0 字幕：**就是它**推动了 H4
    "short-terms.json": False,  # passed=False（RTF≥1）但数据有效 → 必须放行
    "p2_probe_short_clean.json": False,
    "long-realtime.json": False,
    "real_zhlecture_ab4_phrases.phrases-on.json": False,
}


@pytest.mark.parametrize("name,should_block", sorted(_HIST.items()))
def test_replay_history_reports(ab, name, should_block):
    """把真实历史报告的 `summary` 喂进判据，看拦得对不对。

    这是「守卫有没有打在该打的地方」最直接的证据：
      * `short.json` **必须被拦**（它就是当年混进对照表的那一轮）；
      * 其余四份**必须放行**（它们的管道都正常工作过）。
    带 `skipif`：gitignore 的评估产物不在本机就跳过，**不假装通过**。
    """
    p = REPO / "packaging" / "eval" / name
    if not p.is_file():
        pytest.skip(f"{name} 不存在（gitignore 的评估产物，未在本机跑过）")
    report = json.loads(p.read_text(encoding="utf-8"))
    why = ab.invalid_round_reason(report["summary"])
    if should_block:
        assert why is not None, f"{name} 结构不可用，判据却没拦住"
        assert report["summary"]["totalSubtitles"] == 0, "前提：它确实是 0 字幕那一轮"
    else:
        assert why is None, f"{name} 管道正常，判据误拦：{why}"


# ============================ 结构与接线守卫 ============================

def _tree() -> ast.Module:
    return ast.parse(SCRIPT.read_text(encoding="utf-8"))


def _func(name: str) -> ast.FunctionDef:
    for node in ast.walk(_tree()):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"没找到函数 {name}")


def test_reason_never_consults_verdict(ab):
    """🔴 结构守卫：判据**不得**引用 `verdict` / `passed`。

    这条把「按数据修正 H4」这个决策钉住：以后有人「顺手」把 `passed` 加进判据，
    即使注释还写着理由，这条用例也会立刻变红。
    """
    fn = _func("invalid_round_reason")
    referenced: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)
        elif isinstance(node, ast.arg):
            referenced.add(node.arg)
    assert "passed" not in referenced
    assert "verdict" not in referenced
    # 正向：确认真在读 summary（否则上面两条会被一个空函数顺手满足）
    assert "summary" in referenced


def _reason_body_nodes() -> list[ast.AST]:
    """`invalid_round_reason` 的**非 docstring** 语句节点（docstring 不算逻辑）。"""
    fn = _func("invalid_round_reason")
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]                      # 排除 docstring
    return body


def test_reason_only_reads_the_two_totals(ab):
    """🔴 结构守卫：判据从 summary 里 `get` 的键**恰好**是那两个。

    为什么钉「读了哪些键」而不是「读没读某几个键」：
    写成排除式的话，只要将来多读一个**没被列进排除表**的键（比如 `passed`、
    `verdict`、`meanRtf`）就抓不住 —— 而这类键恰恰是「顺手加一条中止条件」的入口。
    多读一个键 = 多一种拦截理由，而本函数的契约是「只判有没有真的测到识别」。

    判据取「`.get(...)` 的第一个实参」而不是「函数体内所有字符串常量」：
    f-string 的字面片段（`" 个片段 → 0 条字幕…"`）也是字符串常量，
    按常量集比对会把它们一起算进来，断言就永远不成立（退化成「跑不起来」）。
    """
    keys: set[str] = set()
    for stmt in _reason_body_nodes():
        for node in ast.walk(stmt):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                keys.add(node.args[0].value)
    assert keys == {"totalSegments", "totalSubtitles"}, f"判据多读了键：{keys}"


def test_reason_has_no_verdict_strings(ab):
    """补充守卫：非 docstring 的字符串常量里不得出现 `passed` / `verdict`。

    上一条盯的是 `.get()` 的键；这一条盯的是「不用 `.get()` 也能读到」的写法
    （`summary["verdict"]["passed"]`、或把键名拼在别处）。两条一起才闭合。
    """
    for stmt in _reason_body_nodes():
        for node in ast.walk(stmt):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert "passed" not in node.value, f"判据引用了 passed：{node.value!r}"
                assert "verdict" not in node.value, f"判据引用了 verdict：{node.value!r}"


def test_allow_invalid_option_exists(ab):
    """`--allow-invalid` 必须存在 —— 没有出口的守卫会把合法探索变成硬墙。"""
    opts: set[str] = set()
    for node in ast.walk(_tree()):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            opts.add(node.args[0].value)
    assert "--allow-invalid" in opts
    # 反向：兄弟守卫的出口不能被顶掉
    assert "--allow-drops" in opts


def test_main_is_wired_to_the_guard(ab):
    """接线守卫：纯函数写好了却没人调 = 死功能，且不会有任何用例变红。"""
    src = SCRIPT.read_text(encoding="utf-8")
    body = src[src.index("def main(") :]
    assert "invalid_round_reason(" in body, "main() 没有调用判据"
    assert "args.allow_invalid" in body, "判据没有出口开关（会变成无法绕过的硬墙）"
    assert "raise SystemExit" in body


def test_main_warns_when_verdict_failed(ab):
    """警告层守卫：`passed=False` 与「无参考文本」**不中止，但绝不能静默**。

    只做中止不做警告是不够的：`passed=False` 的原因（RTF≥1、收尾超时…）
    与「无参考文本」都会让某一格**不能用来比准确率**，而四格输出交错，
    人眼很容易漏掉 —— 见过一次就会知道那比报错更难查。
    """
    src = SCRIPT.read_text(encoding="utf-8")
    body = src[src.index("def main(") :]
    assert 'runs[cond].get("verdict")' in body, "不再读取子报告的 verdict"
    assert 'get("passed") is False' in body, "不再单独提示 passed=False"
    assert 'get("withReference") or 0) == 0' in body, "不再单独提示「无参考文本」"
    assert body.count("⚠ 条件") >= 2, "警告被删成了 pass（静默）"


def test_guard_runs_inside_the_condition_loop(ab):
    """结构守卫：守卫必须在**每个条件跑完就查**，不能挪到汇总之后。

    挪到汇总之后的话：脚本会先跑完剩余条件（长音频每格 10 分钟）才报错 ——
    白烧机时，而且坏轮次的报告文件已经落盘了。
    判据用「守卫调用是不是 `for cond in conds` 的**后代节点**」，
    而不是比行号 —— 比行号只证明「写在后面」，不证明「在循环体内」。
    """
    fn = _func("main")
    for_node = None
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.For)
            and isinstance(node.target, ast.Name)
            and node.target.id == "cond"
        ):
            for_node = node
            break
    assert for_node is not None, "没找到 `for cond in conds` 循环（被重构了？）"
    assert any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "invalid_round_reason"
        for n in ast.walk(for_node)
    ), "守卫不在条件循环体内"
