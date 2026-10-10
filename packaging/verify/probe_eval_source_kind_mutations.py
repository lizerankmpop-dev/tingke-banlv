#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""变异探针：验证 `test_eval_source_kind.py` 的断言真有分辨力

为什么需要这个
--------------
「有测试覆盖」不等于「断言验得到」。新加的断言如果被别的路径顺手满足，
就会**永远绿**、永不报警 —— 比没有断言更糟，因为它给出的是假信心。

本脚本对 `scripts/eval_asr_quality.py` 做局部变异（**原地改，跑完立即还原**，
每一步都用 md5 核对文件确实复原），逐条确认：该变异必须让**点名的那条**用例变红。
其余变红的用例只作提示打印，不判失败（一个变异可以合理地牵动多条规则）。

为什么是「原地改」而不是「写一份 `_mutant_*.py` 副本」：
被测模块由测试模块**按固定路径**加载（`SCRIPT` 指向 `scripts/eval_asr_quality.py`），
写副本的话测试仍然会去读真身，变异等于没生效 —— 那叫空转。

特别针对本轮的三类「静默失效」（它们的共同点是**都不会报错**）：
  * 阈值判据 `>` 写成 `>=`（边界样本被误拒）；
  * `rejected` 的「未判定」被写成 `False`（报告里与「判定通过」长得一样）；
  * 新维度少遍历/串桶（`byScenario` 分母不变、`bySourceScenario` 分母偏小）。

用法
----
    <venv>/python.exe packaging/verify/probe_eval_source_kind_mutations.py
退出码 0 = 全部变异都被抓住；1 = 有变异没被抓住（断言缺分辨力）。

这是一次性验证证据，不参与常规测试；常规回归跑 `backend/tests/test_eval_source_kind.py`。

检查函数语义：`_run_tests()` 返回的集合是**该轮变红的用例名**。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "scripts" / "eval_asr_quality.py"
TEST_FILE = "tests/test_eval_source_kind.py"
BASETEMP = REPO.parent / "安装空间" / "_pytest_tmp_eval_probe"

PY = REPO.parent / "安装空间" / "venv" / "Scripts" / "python.exe"

# ============================ 变异锚点 ============================

# --- A 组：逐样本 CER 门禁 ---
GATE_CMP_OLD = "    rejected = cer_value > max_cer"
GATE_CMP_GE = "    rejected = cer_value >= max_cer"

GATE_UNDECIDED_OLD = '''        result["rejected"] = None
        result["rejectReason"] = "无参考文本，未参与判定"'''
GATE_UNDECIDED_FALSE = '''        result["rejected"] = False
        result["rejectReason"] = "无参考文本，未参与判定"'''

GATE_OFF_OLD = """    if max_cer is None:
        return
"""
GATE_OFF_WRITES_FALSE = """    if max_cer is None:
        result["rejected"] = False
        return
"""

GATE_VERDICT_GUARD_OLD = "    if max_cer is not None:"
GATE_VERDICT_GUARD_OFF = "    if True:  # 变异：不给 --max-cer 也输出阈值结论"

# --- B 组：来源解析 ---
META_RANGE_OLD = "    for _ in range(levels + 1):"
META_RANGE_UNBOUNDED = "    for _ in range(9):  # 变异：不再限制上溯层数"

META_JSON_OLD = '''            try:
                data = json.loads(cand.read_text(encoding="utf-8"))
            except Exception:
                return None      # 坏 metadata 只当「取不到」，不能让整轮评估崩掉'''
META_JSON_NO_GUARD = "            data = json.loads(cand.read_text(encoding=\"utf-8\"))"

META_DICT_OLD = "            return data if isinstance(data, dict) else None"
META_DICT_ANY = "            return data"

KIND_FIELD_OLD = '''    direct = meta.get("sourceKind")
    if isinstance(direct, str) and direct.strip():
        return direct.strip(), "metadata.json 的 sourceKind 字段"'''
KIND_FIELD_OFF = "    direct = None  # 变异：不再优先读 sourceKind 字段"

KIND_UNKNOWN_OLD = '        return SOURCE_KIND_UNKNOWN, f"metadata.json kind={kind.strip()}（未登记映射）"'
KIND_UNKNOWN_GUESS = '        return SOURCE_KIND_TTS, f"metadata.json kind={kind.strip()}（未登记映射）"'

KIND_MAP_OLD = '    "REAL": SOURCE_KIND_HUMAN,'
KIND_MAP_EXTRA = '    "REAL": SOURCE_KIND_HUMAN,\n    "WAT": "made-up-kind",'

# --- C 组：两维聚合 ---
BUCKET_SCEN_OLD = '            r["scenario"], {"files": 0, "wer": [], "cer": [], "rtf": []}'
BUCKET_SCEN_COLLAPSED = '            "ALL", {"files": 0, "wer": [], "cer": [], "rtf": []}'

COUNT_KIND_OLD = '            kind: sum(v["files"] for v in by_kind.values())'
COUNT_KIND_SCENARIOS = "            kind: len(by_kind)"

UNKNOWN_FALLBACK_OLD = '        kind = r.get("sourceKind") or SOURCE_KIND_UNKNOWN'
UNKNOWN_FALLBACK_DROP = '        kind = r.get("sourceKind") or "?"'

BYSCEN_WER_OLD = '                "meanWer": mean(v["wer"]),'
BYSCEN_WER_SWAP = '                "meanWer": mean(v["cer"]),'

BYSRC_WER_OLD = '                    "wer": spread(v["wer"]),'
BYSRC_WER_SWAP = '                    "wer": spread(v["cer"]),'

REJ_COUNT_OLD = '        "rejectedCount": sum(1 for r in ok if r.get("rejected") is True),'
REJ_COUNT_LOOSE = '        "rejectedCount": sum(1 for r in ok if r.get("rejected") is not False),'

SPREAD_EMPTY_OLD = '            return {"mean": None, "min": None, "max": None, "range": None}'
SPREAD_EMPTY_ZERO = '            return {"mean": 0, "min": 0, "max": 0, "range": 0}'

# --- D 组：结论与接线 ---
UNKNOWN_NOTE_OLD = "    if SOURCE_KIND_UNKNOWN in kinds:"
UNKNOWN_NOTE_OFF = "    if False:  # 变异：不再提示 unknown 来源"

WIRE_SOURCE_OLD = '''    result["sourceKind"], result["sourceKindBasis"] = resolve_source_kind(
        path, getattr(args, "source_kind", None)
    )'''
WIRE_SOURCE_OFF = "    pass  # 变异：不再解析来源"

WIRE_GATE_OLD = '    apply_cer_gate(result, getattr(args, "max_cer", None))'
WIRE_GATE_OFF = "    pass  # 变异：不再做逐样本门禁"

CLI_MAXCER_OLD = '        "--max-cer",'
CLI_MAXCER_RENAMED = '        "--max-cer-renamed",'

CLI_SOURCEKIND_OLD = '        "--source-kind",'
CLI_SOURCEKIND_RENAMED = '        "--source-kind-renamed",'

CLI_MAXWER_OLD = '    parser.add_argument("--max-wer", type=float, default=None, help="WER 阈值，超过则失败（整轮均值门禁）")'
CLI_MAXWER_OFF = "    pass  # 变异：删掉整轮 WER 均值门禁参数"

CONFIG_MAXCER_OLD = '            "maxCer": args.max_cer,\n'
CONFIG_MAXCER_OFF = ""

# (变异名, 原文锚点, 替换成, 必须变红的用例)
MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "A1. 阈值判据 `>` 写成 `>=`（恰好等于阈值的样本被误拒）",
        GATE_CMP_OLD,
        GATE_CMP_GE,
        "test_cer_gate_passes_at_exact_threshold",
    ),
    (
        "A2. 「无参考文本」的 rejected 从 None 写成 False（未判定伪装成通过）",
        GATE_UNDECIDED_OLD,
        GATE_UNDECIDED_FALSE,
        "test_cer_gate_without_reference_is_undecided_not_passed",
    ),
    (
        "A3. 不给 --max-cer 时照样写 rejected=False（没判过却说判过了）",
        GATE_OFF_OLD,
        GATE_OFF_WRITES_FALSE,
        "test_cer_gate_no_threshold_adds_nothing",
    ),
    (
        "A4. 报告结论无条件输出阈值判定（没给 --max-cer 也报「全部通过」）",
        GATE_VERDICT_GUARD_OLD,
        GATE_VERDICT_GUARD_OFF,
        "test_verdict_silent_about_cer_when_gate_off",
    ),
    (
        "B1. metadata 上溯层数不再受限（串到别处的 metadata 上）",
        META_RANGE_OLD,
        META_RANGE_UNBOUNDED,
        "test_metadata_stops_at_two_levels",
    ),
    (
        "B2. 去掉坏 metadata 的容错（整轮评估被一份旁注打断）",
        META_JSON_OLD,
        META_JSON_NO_GUARD,
        "test_metadata_malformed_json_returns_none",
    ),
    (
        "B3. 不再校验 metadata 是对象（列表会让 .get 抛 AttributeError）",
        META_DICT_OLD,
        META_DICT_ANY,
        "test_metadata_non_dict_returns_none",
    ),
    (
        "B4. 不再优先读 sourceKind 字段（显式字段被 kind 映射盖掉）",
        KIND_FIELD_OLD,
        KIND_FIELD_OFF,
        "test_source_kind_field_beats_kind_field",
    ),
    (
        "B5. 未登记的 kind 猜成 tts-synth（猜出来的来源无人能发现）",
        KIND_UNKNOWN_OLD,
        KIND_UNKNOWN_GUESS,
        "test_source_kind_unknown_kind_is_not_guessed",
    ),
    (
        "B6. 映射表混进第三类来源（凭空多一个没人认识的桶）",
        KIND_MAP_OLD,
        KIND_MAP_EXTRA,
        "test_kind_map_only_produces_documented_values",
    ),
    (
        "C1. 两维聚合退化成只按来源（scenario 维度被抹掉）",
        BUCKET_SCEN_OLD,
        BUCKET_SCEN_COLLAPSED,
        "test_by_source_scenario_two_dim_bucket",
    ),
    (
        "C2. sourceKindCounts 改数场景格数（同来源多场景时分母偏小）",
        COUNT_KIND_OLD,
        COUNT_KIND_SCENARIOS,
        "test_source_kind_counts_counts_files_not_scenarios",
    ),
    (
        "C3. 缺 sourceKind 时不再回落 unknown（旧测试集被静默丢桶）",
        UNKNOWN_FALLBACK_OLD,
        UNKNOWN_FALLBACK_DROP,
        "test_missing_source_kind_falls_back_to_unknown",
    ),
    (
        "C4. byScenario 的 meanWer 误写成 cer（历史对标表口径被换掉）",
        BYSCEN_WER_OLD,
        BYSCEN_WER_SWAP,
        "test_by_scenario_values_are_exact",
    ),
    (
        "C5. bySourceScenario 的 wer 误写成 cer",
        BYSRC_WER_OLD,
        BYSRC_WER_SWAP,
        "test_by_source_scenario_two_dim_bucket",
    ),
    (
        "C6. rejectedCount 把「未判定」也数进去（分母虚高）",
        REJ_COUNT_OLD,
        REJ_COUNT_LOOSE,
        "test_rejected_accounting_ignores_undecided",
    ),
    (
        "C7. 空样本的 spread 返回 0 而不是 None（得出「CER 极好」的假结论）",
        SPREAD_EMPTY_OLD,
        SPREAD_EMPTY_ZERO,
        "test_spread_without_values_is_all_none",
    ),
    (
        "D1. 不再提示 unknown 来源（凭空多出的桶没人解释）",
        UNKNOWN_NOTE_OLD,
        UNKNOWN_NOTE_OFF,
        "test_verdict_flags_unknown_source",
    ),
    (
        "D2. eval_file 不再解析来源（纯函数写好了没人调）",
        WIRE_SOURCE_OLD,
        WIRE_SOURCE_OFF,
        "test_eval_file_is_wired_to_new_helpers",
    ),
    (
        "D3. eval_file 不再做逐样本门禁（功能整条不存在）",
        WIRE_GATE_OLD,
        WIRE_GATE_OFF,
        "test_eval_file_is_wired_to_new_helpers",
    ),
    (
        "D4. CLI 的 --max-cer 被改名（参数名不对，用户传不进去）",
        CLI_MAXCER_OLD,
        CLI_MAXCER_RENAMED,
        "test_new_cli_options_exist",
    ),
    (
        "D5. CLI 的 --source-kind 被改名",
        CLI_SOURCEKIND_OLD,
        CLI_SOURCEKIND_RENAMED,
        "test_new_cli_options_exist",
    ),
    (
        "D6. 删掉 --max-wer（整轮均值门禁被静默移除）",
        CLI_MAXWER_OLD,
        CLI_MAXWER_OFF,
        "test_max_wer_still_exists",
    ),
    (
        "D7. 报告 config 不再记 maxCer（报告无法自证口径）",
        CONFIG_MAXCER_OLD,
        CONFIG_MAXCER_OFF,
        "test_report_config_keys_present",
    ),
]


def _read_src() -> tuple[str, str, str]:
    """返回 (归一化文本, eol, md5)；归一化只为匹配锚点，写回时按原 eol"""
    raw = SRC.read_bytes()
    eol = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return text, eol, hashlib.md5(raw).hexdigest()


def _write_src(text: str, eol: str) -> None:
    SRC.write_bytes(text.replace("\n", eol).encode("utf-8"))


def _run_tests() -> tuple[set[str], str]:
    """跑测试文件，返回 (变红的用例名集合, 原始输出尾部)"""
    proc = subprocess.run(
        [
            str(PY), "-m", "pytest", TEST_FILE,
            "-q", "--tb=no", "-p", "no:cacheprovider",
            f"--basetemp={BASETEMP}",
        ],
        cwd=str(REPO / "backend"),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ},
        timeout=900,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    red = set()
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("FAILED "):
            red.add(line.split("::")[-1].split(" ")[0].strip())
    tail = "\n".join([ln for ln in out.splitlines() if ln.strip()][-2:])
    return red, tail


def main() -> int:
    original_text, eol, original_md5 = _read_src()
    if not PY.exists():
        print(f"!! venv python 不存在: {PY}")
        return 2

    # 先跑不变量：真身必须全绿。否则「变红」可能只是环境坏了（探针的第四层自骗）
    red, tail = _run_tests()
    if red:
        print(f"!! 不变量失败：真身本应全绿，却有 {sorted(red)}\n   {tail}")
        return 2
    print(f"[不变] 真身全绿（{tail}）\n")

    failures: list[str] = []
    for name, old, new, target in MUTATIONS:
        try:
            if old not in original_text:
                raise SystemExit(f"[{name}] 变异锚点在源码里找不到，探针本身失效：{old!r}")
            _write_src(original_text.replace(old, new, 1), eol)
            red, tail = _run_tests()

            if target not in red:
                print(f"[漏掉] {name}\n        用例 {target} 仍为绿 —— 断言缺分辨力（tail: {tail}）")
                failures.append(name)
            else:
                others = sorted(red - {target})
                print(f"[抓住] {name}\n        变红：{target}")
                if others:
                    print(f"        另有用例也变红 → {others}（该变异确实牵动多条规则）")
        finally:
            _write_src(original_text, eol)
            now_md5 = hashlib.md5(SRC.read_bytes()).hexdigest()
            if now_md5 != original_md5:
                print(f"!! 还原失败！md5 {original_md5} -> {now_md5}，请手动 checkout")
                return 2

    print()
    if failures:
        print(f"结论：{len(failures)}/{len(MUTATIONS)} 条变异没被抓住，需补断言")
        return 1
    print(f"结论：{len(MUTATIONS)}/{len(MUTATIONS)} 条变异全部被抓住，断言有分辨力")
    print(f"（文件已还原，md5={original_md5}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
