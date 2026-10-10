"""`eval_asr_quality` 的 #170 新增件：sourceKind / bySourceScenario / --max-cer

为什么值得一组测试
------------------
这三样坏掉的共同形态都是**静默**：

  * `sourceKind` 取错 → 合成素材的 CER 被并进真人桶，横向对比表**看起来很正常**；
  * `bySourceScenario` 把 `unknown` 静默归组 → 凭空多一个桶，事后没人知道它怎么来的；
  * `--max-cer` 的 `rejected` 用 `False` 表示「没判定」→ 读报告的人以为「验过且通过」。

「没有报错」在这三处都不是通过标准。本文件的重点是**反向断言**（教训 5）：
每个「应该发生」的断言都配一个「不该发生」的对称断言，否则断言会被前置条件
顺手满足，永远绿（教训 2）。

约定：全部在 `tmp_path` 里造文件，不读真实素材、不跑模型、不启动管道。
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
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
    return _load("eval_asr_quality_source_kind_under_test", SCRIPT)


# ============================ 夹具 ============================

def _res(
    file: str,
    scenario: str,
    *,
    wer: float | None = None,
    cer: float | None = None,
    source: str | None = None,
    rtf: float | None = 0.5,
    segments: int = 3,
    subtitles: int = 3,
) -> dict:
    """造一条 `eval_file()` 形态的结果。字段名与真实产物逐字对齐。

    `source=None` 时**不写** `sourceKind` 键 —— 这正是旧报告（#170 之前生成）的形态，
    用来验证「兼容旧测试集」而不是「我假装兼容了」。
    """
    r: dict = {
        "file": file,
        "scenario": scenario,
        "audioSeconds": 10.0,
        "segments": segments,
        "subtitles": subtitles,
        "rtf": rtf,
        "tailSeconds": 0.1,
        "droppedSegments": 0,
        "mergedCalls": 0,
        "drained": True,
        "paired": False,
        "latency": {
            "mean": None,
            "p95": None,
            "max": None,
            "firstHalfMean": None,
            "secondHalfMean": None,
        },
        "inSegments": segments,
        "outSegments": segments,
        "repetitionEvents": 0,
        "termPromptChars": None,
        "termPromptDegraded": False,
    }
    if wer is not None:
        r["accuracy"] = {"wer": wer, "cer": cer}
    if source is not None:
        r["sourceKind"] = source
    return r


def _args(**kw) -> argparse.Namespace:
    base = {"realtime": False, "max_wer": None, "max_cer": None}
    base.update(kw)
    return argparse.Namespace(**base)


def _meta(dir_: Path, payload) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    p = dir_ / "metadata.json"
    p.write_text(
        payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    return p


# ============================ find_metadata ============================

def test_metadata_found_in_sample_dir(ev, tmp_path):
    _meta(tmp_path, {"kind": "CLIP-FROM-TTS"})
    assert ev.find_metadata(tmp_path / "a.wav") == {"kind": "CLIP-FROM-TTS"}


def test_metadata_found_one_level_up(ev, tmp_path):
    """`short/` 与 `real/` 是同级各一份；`long/` 自己没有、登记在上一级。"""
    _meta(tmp_path, {"kind": "PROXY"})
    sub = tmp_path / "long"
    sub.mkdir()
    assert ev.find_metadata(sub / "a.wav") == {"kind": "PROXY"}


def test_metadata_found_two_levels_up(ev, tmp_path):
    _meta(tmp_path, {"kind": "REAL"})
    deep = tmp_path / "x" / "y"
    deep.mkdir(parents=True)
    assert ev.find_metadata(deep / "a.wav") == {"kind": "REAL"}


def test_metadata_stops_at_two_levels(ev, tmp_path):
    """反向断言：`_METADATA_SEARCH_LEVELS = 2` 是**上限**，不是「一路上溯到根」。

    没有这条，实现里把 `range(levels + 1)` 改成 `range(9)` 不会有任何用例变红，
    而后果是某个样本会串到仓库某个角落的 metadata 上（串桶，且全程无声）。
    """
    _meta(tmp_path, {"kind": "REAL"})
    too_deep = tmp_path / "a" / "b" / "c"
    too_deep.mkdir(parents=True)
    assert ev.find_metadata(too_deep / "x.wav") is None


def test_metadata_absent_returns_none(ev, tmp_path):
    """找不到是常态（旧素材本来就没有 metadata），必须是 None 而不是异常。"""
    assert ev.find_metadata(tmp_path / "a.wav") is None


def test_metadata_malformed_json_returns_none(ev, tmp_path):
    _meta(tmp_path, "{这不是 JSON")
    assert ev.find_metadata(tmp_path / "a.wav") is None


def test_metadata_non_dict_returns_none(ev, tmp_path):
    """反向断言：JSON 能解析但不是对象时也要当「取不到」。

    否则 `meta.get(...)` 会 AttributeError，把整轮评估打断 —— 而这份 metadata
    只是素材的旁注，不该有这种权力。
    """
    _meta(tmp_path, [1, 2, 3])
    assert ev.find_metadata(tmp_path / "a.wav") is None


# ============================ resolve_source_kind ============================

def test_source_kind_from_metadata_kind(ev, tmp_path):
    _meta(tmp_path, {"kind": "PROXY"})
    kind, basis = ev.resolve_source_kind(tmp_path / "a.wav")
    assert kind == ev.SOURCE_KIND_TTS
    assert basis, "判据必须能写进报告，不能是空串"


def test_source_kind_explicit_overrides_metadata(ev, tmp_path):
    """显式参数压过 metadata —— 真人锚点素材复用它自己的目录时靠这条。"""
    _meta(tmp_path, {"kind": "PROXY"})
    kind, basis = ev.resolve_source_kind(tmp_path / "a.wav", "human-recording")
    assert kind == "human-recording"
    assert "命令行" in basis


def test_source_kind_field_beats_kind_field(ev, tmp_path):
    """反向断言：两个字段同时存在时 `sourceKind` 优先，不能倒过来。"""
    _meta(tmp_path, {"sourceKind": "human-recording", "kind": "PROXY"})
    kind, basis = ev.resolve_source_kind(tmp_path / "a.wav")
    assert kind == "human-recording"
    assert "sourceKind" in basis


def test_source_kind_mapping_is_case_insensitive(ev, tmp_path):
    """实测素材里 `kind` 的大小写并不统一（`PROXY` / `CLIP-FROM-TTS`）。"""
    _meta(tmp_path, {"kind": "proxy"})
    assert ev.resolve_source_kind(tmp_path / "a.wav")[0] == ev.SOURCE_KIND_TTS


def test_source_kind_unknown_kind_is_not_guessed(ev, tmp_path):
    """反向断言：没登记的取值必须落 unknown，**不能**默认成 tts-synth。

    猜出来的来源比没有来源更坏 —— 事后没人能发现它是猜的。
    """
    _meta(tmp_path, {"kind": "SOMETHING-ELSE"})
    kind, basis = ev.resolve_source_kind(tmp_path / "a.wav")
    assert kind == ev.SOURCE_KIND_UNKNOWN
    assert "未登记" in basis


def test_source_kind_missing_both_fields_is_unknown(ev, tmp_path):
    _meta(tmp_path, {"note": "只有备注"})
    kind, basis = ev.resolve_source_kind(tmp_path / "a.wav")
    assert kind == ev.SOURCE_KIND_UNKNOWN
    assert "既无" in basis


def test_source_kind_without_metadata_is_unknown(ev, tmp_path):
    """旧英文测试集（`tests/audio/**`）就长这样：没有任何 metadata。"""
    kind, basis = ev.resolve_source_kind(tmp_path / "synth_clean_jfk.wav")
    assert kind == ev.SOURCE_KIND_UNKNOWN
    assert basis


def test_source_kind_basis_always_nonempty(ev, tmp_path):
    """穷举分支：**每一条**路径都必须留下判据（事后可审计）。"""
    cases = [
        (None, None),
        (None, {"kind": "PROXY"}),
        (None, {"kind": "NOPE"}),
        (None, {"sourceKind": "x"}),
        ("human-recording", None),
    ]
    for explicit, payload in cases:
        d = tmp_path / f"c{abs(hash((explicit, str(payload)))) % 10**6}"
        d.mkdir()
        if payload is not None:
            _meta(d, payload)
        kind, basis = ev.resolve_source_kind(d / "a.wav", explicit)
        assert kind and isinstance(basis, str) and basis.strip(), (explicit, payload)


def test_kind_map_only_produces_documented_values(ev):
    """结构守卫：映射表里不允许出现「第三种来源类型」。

    否则 `bySourceScenario` 会多出一个没人认识的桶，而报告照常输出。
    """
    allowed = {ev.SOURCE_KIND_TTS, ev.SOURCE_KIND_HUMAN}
    assert set(ev._METADATA_KIND_MAP.values()) <= allowed
    assert ev.SOURCE_KIND_UNKNOWN not in set(ev._METADATA_KIND_MAP.values())


# ============================ apply_cer_gate ============================

def test_cer_gate_no_threshold_adds_nothing(ev):
    """反向断言：不给 `--max-cer` 时**不得**写入 `rejected`。

    写了 `rejected=False` 会被读成「验过且通过」，而实际根本没判 ——
    这正是本功能最想避免的那种静默。
    """
    r = _res("a.wav", "clean", wer=0.5, cer=0.5)
    ev.apply_cer_gate(r, None)
    assert "rejected" not in r
    assert "rejectReason" not in r


def test_cer_gate_rejects_above_threshold(ev):
    r = _res("a.wav", "clean", wer=0.1847, cer=0.1847)
    ev.apply_cer_gate(r, 0.18)
    assert r["rejected"] is True
    assert "0.1847" in r["rejectReason"] and "0.18" in r["rejectReason"]


def test_cer_gate_passes_at_exact_threshold(ev):
    """边界：等于阈值**不算超**（`>` 不是 `>=`）。用例把 0.18/0.18 钉死。"""
    r = _res("a.wav", "clean", wer=0.18, cer=0.18)
    ev.apply_cer_gate(r, 0.18)
    assert r["rejected"] is False
    assert r["rejectReason"] is None


def test_cer_gate_passes_below_threshold(ev):
    r = _res("a.wav", "clean", wer=0.1, cer=0.1)
    ev.apply_cer_gate(r, 0.18)
    assert r["rejected"] is False
    assert r["rejectReason"] is None


def test_cer_gate_without_reference_is_undecided_not_passed(ev):
    """没有参考文本 → `None`（未判定），**不是 `False`**。

    实测来历：真实网课录音拿不到逐字稿是常态，若记成 `False`，
    报告里「未判定」的样本会与「判定通过」的样本长得一模一样。
    """
    r = _res("a.wav", "clean")  # 无 accuracy
    ev.apply_cer_gate(r, 0.18)
    assert r["rejected"] is None
    assert "无参考文本" in r["rejectReason"]


def test_cer_gate_without_cer_value_is_undecided(ev):
    """反向断言：有 accuracy 但缺 cer 字段时同样不能判成 False。"""
    r = _res("a.wav", "clean", wer=0.1, cer=0.1)
    del r["accuracy"]["cer"]
    ev.apply_cer_gate(r, 0.18)
    assert r["rejected"] is None


# ============================ summarize: byScenario（回归） ============================

def test_by_scenario_values_are_exact(ev):
    """`byScenario` 是历史对标表的唯一来源，取值必须逐字钉死。

    期望值是手算的：clean 两文件 WER 0.1/0.3 → 均值 0.2；noise 单文件 0.5；
    RTF 只有非 None 的进均值（0.5/0.7 → 0.6），noise 的 RTF 是 None → 均值 None。

    🔴 夹具里故意让 **WER ≠ CER**（0.1/0.2、0.3/0.4）：两者相等的话，
    把某个槽位从 `wer` 误写成 `cer` 不会有任何用例变红 —— 实测过，
    这类「两个口径互相顶替」的错是最容易全绿通过的一种。
    """
    summary = ev.summarize(
        [
            _res("a.wav", "clean", wer=0.1, cer=0.2, rtf=0.5),
            _res("b.wav", "clean", wer=0.3, cer=0.4, rtf=0.7),
            _res("c.wav", "noise", wer=0.5, cer=0.6, rtf=None),
        ]
    )
    assert summary["byScenario"] == {
        "clean": {"files": 2, "meanWer": 0.2, "meanCer": 0.3, "meanRtf": 0.6},
        "noise": {"files": 1, "meanWer": 0.5, "meanCer": 0.6, "meanRtf": None},
    }


def test_by_scenario_has_no_source_dimension(ev):
    """反向断言：加了来源维度之后，`byScenario` 的键**只能**是场景。

    否则「来源」把「场景」的键空间污染了，历史对标表就没法与旧报告对账。
    """
    summary = ev.summarize(
        [
            _res("a.wav", "clean", wer=0.1, cer=0.1, source=ev.SOURCE_KIND_TTS),
            _res("b.wav", "clean", wer=0.1, cer=0.1, source=ev.SOURCE_KIND_HUMAN),
        ]
    )
    assert set(summary["byScenario"]) == {"clean"}
    assert summary["byScenario"]["clean"]["files"] == 2


def test_by_scenario_unchanged_without_source_kind(ev):
    """旧报告（无 sourceKind 字段）回灌 `summarize()` 时 byScenario 不受影响。"""
    summary = ev.summarize(
        [
            _res("a.wav", "clean", wer=0.1, cer=0.1, rtf=0.5),
            _res("c.wav", "noise", wer=0.5, cer=0.5, rtf=None),
        ]
    )
    assert summary["byScenario"] == {
        "clean": {"files": 1, "meanWer": 0.1, "meanCer": 0.1, "meanRtf": 0.5},
        "noise": {"files": 1, "meanWer": 0.5, "meanCer": 0.5, "meanRtf": None},
    }


# ============================ summarize: bySourceScenario ============================

def test_by_source_scenario_two_dim_bucket(ev):
    """两维分桶：同样的录音条件（scenario）下，按来源（sourceKind）再切一刀。

    夹具让 **WER ≠ CER**，理由同 `test_by_scenario_values_are_exact`。
    """
    summary = ev.summarize(
        [
            _res("a.wav", "clean", wer=0.1, cer=0.2, rtf=0.5, source=ev.SOURCE_KIND_TTS),
            _res("b.wav", "clean", wer=0.3, cer=0.4, rtf=0.7, source=ev.SOURCE_KIND_TTS),
            _res("c.wav", "noise", wer=0.5, cer=0.6, rtf=None, source=ev.SOURCE_KIND_HUMAN),
        ]
    )
    assert summary["bySourceScenario"] == {
        "tts-synth": {
            "clean": {
                "files": 2,
                "wer": {"mean": 0.2, "min": 0.1, "max": 0.3, "range": 0.2},
                "cer": {"mean": 0.3, "min": 0.2, "max": 0.4, "range": 0.2},
                "meanRtf": 0.6,
            }
        },
        "human-recording": {
            "noise": {
                "files": 1,
                "wer": {"mean": 0.5, "min": 0.5, "max": 0.5, "range": 0.0},
                "cer": {"mean": 0.6, "min": 0.6, "max": 0.6, "range": 0.0},
                "meanRtf": None,
            }
        },
    }
    assert summary["sourceKindCounts"] == {"tts-synth": 2, "human-recording": 1}


def test_source_kind_counts_counts_files_not_scenarios(ev):
    """反向断言：`sourceKindCounts` 数的是**文件数**，不是场景格数。

    回退成 `len(每个 kind 的场景字典)` 时，同一来源跨两个场景仍只报 2，
    数值看着也「合理」—— 只有把文件数钉死才分得开。
    """
    summary = ev.summarize(
        [
            _res("a.wav", "clean", wer=0.1, cer=0.1, source=ev.SOURCE_KIND_TTS),
            _res("b.wav", "clean", wer=0.1, cer=0.1, source=ev.SOURCE_KIND_TTS),
            _res("c.wav", "noise", wer=0.1, cer=0.1, source=ev.SOURCE_KIND_TTS),
        ]
    )
    assert summary["sourceKindCounts"] == {"tts-synth": 3}


def test_missing_source_kind_falls_back_to_unknown(ev):
    """兼容旧测试集：没有 `sourceKind` 的样本归 unknown，**不抛异常**。"""
    summary = ev.summarize([_res("x.wav", "clean", wer=0.1, cer=0.1)])
    assert set(summary["bySourceScenario"]) == {ev.SOURCE_KIND_UNKNOWN}
    assert summary["sourceKindCounts"] == {ev.SOURCE_KIND_UNKNOWN: 1}


def test_spread_single_value_has_zero_range(ev):
    """n=1 时 min == max、range == 0 —— 报告里一眼能看出这格没有重复。

    只报均值会把「n=1 的偶然」与「稳定的窄分布」混为一谈。
    """
    summary = ev.summarize(
        [_res("a.wav", "clean", wer=0.4, cer=0.4, source=ev.SOURCE_KIND_TTS)]
    )
    cell = summary["bySourceScenario"]["tts-synth"]["clean"]
    assert cell["wer"] == {"mean": 0.4, "min": 0.4, "max": 0.4, "range": 0.0}
    assert cell["cer"]["range"] == 0.0


def test_spread_without_values_is_all_none(ev):
    """反向断言：没有参考文本的格子不能把 None 当成 0（会得出「CER 极好」的假结论）。"""
    summary = ev.summarize([_res("a.wav", "clean", source=ev.SOURCE_KIND_TTS)])
    cell = summary["bySourceScenario"]["tts-synth"]["clean"]
    assert cell["wer"] == {"mean": None, "min": None, "max": None, "range": None}
    assert cell["cer"]["range"] is None


# ============================ summarize: 拒绝账目 ============================

def test_rejected_accounting_ignores_undecided(ev):
    """`rejectedCount` 只认 `is True`；`None`（未判定）不得混进来。"""
    rejected = _res("bad.wav", "clean", wer=0.9, cer=0.9, source=ev.SOURCE_KIND_TTS)
    ev.apply_cer_gate(rejected, 0.18)
    undecided = _res("noref.wav", "clean", source=ev.SOURCE_KIND_TTS)
    ev.apply_cer_gate(undecided, 0.18)
    passed = _res("ok.wav", "clean", wer=0.1, cer=0.1, source=ev.SOURCE_KIND_TTS)
    ev.apply_cer_gate(passed, 0.18)

    summary = ev.summarize([rejected, undecided, passed])
    assert summary["rejectedCount"] == 1
    assert summary["rejectedFiles"] == ["bad.wav"]
    assert summary["cerUndecidedFiles"] == ["noref.wav"]


def test_undecided_list_empty_when_gate_off(ev):
    """反向断言：没给 `--max-cer` 时两个账目都必须为空（不是「全部未判定」）。"""
    summary = ev.summarize([_res("a.wav", "clean", wer=0.1, cer=0.1)])
    assert summary["rejectedCount"] == 0
    assert summary["rejectedFiles"] == []
    assert summary["cerUndecidedFiles"] == []


# ============================ verdict ============================

def test_verdict_fails_on_rejected_sample(ev):
    """逐样本超标 → 整轮判不通过，且结论里点名到文件。"""
    r = _res("bad.wav", "clean", wer=0.9, cer=0.9, source=ev.SOURCE_KIND_TTS)
    ev.apply_cer_gate(r, 0.18)
    summary = ev.summarize([r])
    passed, notes = ev.verdict(summary, [r], _args(max_cer=0.18))
    assert passed is False
    assert any("bad.wav" in n for n in notes)


def test_verdict_passes_when_all_under_threshold(ev):
    """反向断言：全都在阈值内时必须有明确的「✓ … ≤ 阈值」，不能沉默。"""
    r = _res("ok.wav", "clean", wer=0.1, cer=0.1, source=ev.SOURCE_KIND_TTS)
    ev.apply_cer_gate(r, 0.18)
    summary = ev.summarize([r])
    passed, notes = ev.verdict(summary, [r], _args(max_cer=0.18))
    assert passed is True
    assert any("≤ 阈值 0.18" in n for n in notes)


def test_verdict_reports_undecided_separately(ev):
    r = _res("noref.wav", "clean", source=ev.SOURCE_KIND_TTS)
    ev.apply_cer_gate(r, 0.18)
    summary = ev.summarize([r])
    passed, notes = ev.verdict(summary, [r], _args(max_cer=0.18))
    assert passed is True, "未判定不是失败"
    assert any("未参与" in n and "不等于通过" in n for n in notes)


def test_verdict_silent_about_cer_when_gate_off(ev):
    """反向断言：不给 `--max-cer` 时不得出现任何 CER 阈值结论（避免假阳性提示）。"""
    r = _res("a.wav", "clean", wer=0.9, cer=0.9, source=ev.SOURCE_KIND_TTS)
    summary = ev.summarize([r])
    _, notes = ev.verdict(summary, [r], _args())
    assert not any("阈值" in n and "CER" in n for n in notes)


def test_verdict_flags_unknown_source(ev):
    """unknown 必须被说出来 —— 静默归组会让 bySourceScenario 凭空多一个桶。"""
    r = _res("a.wav", "clean", wer=0.1, cer=0.1)  # 无 sourceKind
    summary = ev.summarize([r])
    _, notes = ev.verdict(summary, [r], _args())
    assert any("unknown" in n for n in notes)


def test_verdict_no_unknown_note_when_all_known(ev):
    """反向断言：来源全知道时不得出现 unknown 提示（否则提示噪声会让人忽略它）。"""
    r = _res("a.wav", "clean", wer=0.1, cer=0.1, source=ev.SOURCE_KIND_TTS)
    summary = ev.summarize([r])
    _, notes = ev.verdict(summary, [r], _args())
    assert not any("unknown" in n for n in notes)


# ============================ 结构与接线守卫 ============================

def _tree() -> ast.Module:
    return ast.parse(SCRIPT.read_text(encoding="utf-8"))


def _cli_options() -> set[str]:
    out: set[str] = set()
    for node in ast.walk(_tree()):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            out.add(node.args[0].value)
    return out


def test_new_cli_options_exist(ev):
    opts = _cli_options()
    assert "--max-cer" in opts
    assert "--source-kind" in opts


def test_max_wer_still_exists(ev):
    """反向断言：`--max-wer`（整轮均值门禁）**不得**被新参数顶掉 ——
    两者分工不同，合并会静默把「整轮失败」降级成「标一条样本」。
    """
    assert "--max-wer" in _cli_options()


def test_report_config_keys_present(ev):
    """结构守卫：报告 `config` 里必须有取证字段，否则报告无法自证口径。"""
    cfg = None
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Dict):
            keys = {
                k.value
                for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
            if {"model", "language", "maxWer"} <= keys:
                cfg = keys
                break
    assert cfg is not None, "没找到报告的 config 字典（被重构了？）"
    assert {"maxCer", "sourceKindOverride"} <= cfg


def _func_calls(name: str) -> set[str]:
    """收集 `name` 函数体里被调用的函数名（只看 Call 的末级名字）。"""
    calls: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    f = sub.func
                    if isinstance(f, ast.Name):
                        calls.add(f.id)
                    elif isinstance(f, ast.Attribute):
                        calls.add(f.attr)
    return calls


def test_eval_file_is_wired_to_new_helpers(ev):
    """接线守卫：纯函数写好了却没人调 = 死功能（且不会有任何用例变红）。

    `eval_file` 是唯一的生产调用点：`sourceKind` 要在**每条样本**上落盘，
    门禁要跟着这条样本的 `accuracy` 一起进 `results`。
    """
    calls = _func_calls("eval_file")
    assert "resolve_source_kind" in calls
    assert "apply_cer_gate" in calls


def test_summarize_uses_once_per_result(ev):
    """结构守卫：`summarize` 里两个维度的分桶都必须只遍历一次。

    这不是洁癖：漏掉一次遍历会让新维度少算文件而不报错（`byScenario` 分母不变、
    `bySourceScenario` 分母偏小），看起来只是「数字不太一样」。
    """
    src = SCRIPT.read_text(encoding="utf-8")
    body = src[src.index("def summarize(") : src.index("def verdict(")]
    assert body.count("by_scenario.setdefault") == 1
    assert body.count("by_source_scenario.setdefault") == 1


# ============================ 历史产物回灌（真数据） ============================

_HISTORY = [
    REPO / "packaging" / "eval" / "short.json",             # 英文旧短集（整轮无效那轮）
    REPO / "packaging" / "eval" / "short-terms.json",       # 英文旧短集（WER 自相矛盾那轮）
    REPO / "packaging" / "eval" / "long-realtime.json",     # 英文旧长集
    REPO / "packaging" / "eval" / "p2_probe_short_clean.json",  # #169 中文短样本
]


@pytest.mark.parametrize("path", _HISTORY, ids=lambda p: p.name)
def test_replay_history_by_scenario_is_identical(ev, path):
    """真实历史报告回灌：`byScenario` 必须**逐字相同**，且旧样本全落 unknown。

    这是「无回归」最强的证据形态 —— 它把「代码改动」与「模型随机性」彻底分开：
    重新跑一遍 ASR 会引入 run-to-run 波动（≈0.03~0.05 CER），届时数字对不上
    也分不清是改坏了还是模型抖了；回灌同一份 `results` 则只有代码一个变量。

    同时验证兼容性：这些报告的 `results` 里**没有** `sourceKind` 键。
    """
    if not path.is_file():
        pytest.skip(f"{path.name} 不存在（gitignore 的评估产物，未在本机跑过）")
    report = json.loads(path.read_text(encoding="utf-8"))
    old_summary = report["summary"]["byScenario"]
    results = report["results"]

    fresh = ev.summarize(results)

    assert fresh["byScenario"] == old_summary, (
        f"{path.name}: byScenario 出现回归 —— 旧 {old_summary} / 新 {fresh['byScenario']}"
    )
    # 旧报告的样本一律没有 sourceKind 字段 → 必须整批落 unknown，一个不落
    assert set(fresh["bySourceScenario"]) == {ev.SOURCE_KIND_UNKNOWN}
    assert fresh["sourceKindCounts"] == {ev.SOURCE_KIND_UNKNOWN: fresh["files"]}
    assert fresh["rejectedCount"] == 0
    assert fresh["cerUndecidedFiles"] == []
