#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""变异探针：验证 `test_ab_invalid_round.py` 的断言真有分辨力（P2 #171 / H4）

本脚本对 `scripts/ab_terms_compare.py` 做局部变异（**原地改，跑完立即还原**，
每一步都用 md5 核对文件确实复原），逐条确认：该变异必须让**点名的那条**用例变红。
其余变红的用例只作提示打印，不判失败。

为什么需要这个（#171 的三类「静默」）
------------------------------------
  * **漏拦**：0 片段 / 0 字幕的轮次不再被拦 → WER 恒 1.0 的假数字进对照表；
  * **过拦**：把 `verdict.passed` 写进判据 → `short-terms.json` 那类「只是跑得慢」
    的有效证据被一起拦掉（实测 42 份历史子报告里 10 份 passed=False，其中 2 份
    「非背压」的都是 RTF≥1，不是无效轮次）；
  * **变砖**：判据没有出口开关，或守卫被挪到条件循环之外（先跑完剩余条件才报错，
    白烧机时且坏报告已落盘）。

用法
----
    <venv>/python.exe packaging/verify/probe_ab_invalid_round_mutations.py
退出码 0 = 全部变异都被抓住；1 = 有变异没被抓住（断言缺分辨力）。

这是一次性验证证据，不参与常规测试；常规回归跑 `backend/tests/test_ab_invalid_round.py`。

诚实记账（已知未覆盖）
----------------------
* 「警告层」只挡了「读不读得到 verdict / withReference」与「有没有打出来」，
  没有挡「打出来的时机」（比如把警告塞到循环外）。那需要跑 main() 才能观测，
  成本远高于收益（探针本身要起子进程跑 ASR）。
* `--allow-drops`（兄弟守卫的出口）只用「参数还在不在」挡住，
  没有挡「它的判断条件被换掉」—— 那属于 #147 之前那条守卫的职责范围。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "scripts" / "ab_terms_compare.py"
TEST_FILE = "tests/test_ab_invalid_round.py"
BASETEMP = REPO.parent / "安装空间" / "_pytest_tmp_ab_probe"

PY = REPO.parent / "安装空间" / "venv" / "Scripts" / "python.exe"

# ============================ 变异锚点 ============================

BODY_OLD = '''    segs = summary.get("totalSegments") or 0
    subs = summary.get("totalSubtitles") or 0
    if segs == 0:
        return f"切出 {segs} 个片段（VAD 未工作或音频全静音）"
    if subs == 0:
        return f"{segs} 个片段 → 0 条字幕（片段未送入识别引擎）"
'''

# A1：0 片段不再拦（VAD 挂了也能出表）
SEG_CHECK_OFF = '''    segs = summary.get("totalSegments") or 0
    subs = summary.get("totalSubtitles") or 0
    if False:
        return f"切出 {segs} 个片段（VAD 未工作或音频全静音）"
    if subs == 0:
        return f"{segs} 个片段 → 0 条字幕（片段未送入识别引擎）"
'''

# A2：0 字幕不再拦（**H4 的核心**：short.json 那种轮次会重新混进对照表）
SUB_CHECK_OFF = '''    segs = summary.get("totalSegments") or 0
    subs = summary.get("totalSubtitles") or 0
    if segs == 0:
        return f"切出 {segs} 个片段（VAD 未工作或音频全静音）"
    if False:
        return f"{segs} 个片段 → 0 条字幕（片段未送入识别引擎）"
'''

# A3：判序颠倒（先判字幕）→ 0 片段时报出「0 个片段 → 0 条字幕」，把根因说成现象
ORDER_SWAPPED = '''    segs = summary.get("totalSegments") or 0
    subs = summary.get("totalSubtitles") or 0
    if subs == 0:
        return f"{segs} 个片段 → 0 条字幕（片段未送入识别引擎）"
    if segs == 0:
        return f"切出 {segs} 个片段（VAD 未工作或音频全静音）"
'''

# A4：缺键时默认 1（旧报告/异常字典被放行）
DEFAULT_ONE = '''    segs = summary.get("totalSegments") or 1
    subs = summary.get("totalSubtitles") or 1
    if segs == 0:
        return f"切出 {segs} 个片段（VAD 未工作或音频全静音）"
    if subs == 0:
        return f"{segs} 个片段 → 0 条字幕（片段未送入识别引擎）"
'''

# B1：把 verdict.passed 写进判据（方案 H4 的原建议 —— 实测证明会过拦）
VERDICT_IN_REASON = '''    if (summary.get("verdict") or {}).get("passed") is False:
        return "变异：拿 verdict.passed 当中止条件"
    segs = summary.get("totalSegments") or 0
    subs = summary.get("totalSubtitles") or 0
    if segs == 0:
        return f"切出 {segs} 个片段（VAD 未工作或音频全静音）"
    if subs == 0:
        return f"{segs} 个片段 → 0 条字幕（片段未送入识别引擎）"
'''

# B2：把「背压丢弃」也塞进本判据（职责重叠，以后调一条会牵动另一条）
DROPPED_IN_REASON = '''    segs = summary.get("totalSegments") or 0
    subs = summary.get("totalSubtitles") or 0
    if segs == 0 or summary.get("totalDropped"):
        return f"切出 {segs} 个片段（VAD 未工作或音频全静音）"
    if subs == 0:
        return f"{segs} 个片段 → 0 条字幕（片段未送入识别引擎）"
'''

# C1：判据不在循环内调用（改成循环外/被删）
CALL_IN_LOOP_OFF = "        why = None  # 变异：判据不在条件循环内调用"

# C2：没有出口开关（守卫变成无法绕过的硬墙）
NO_OVERRIDE = "        if why:"

# D1：CLI 参数被改名
ALLOW_INVALID_RENAMED = '        "--allow-invalid-renamed",'
ALLOW_DROPS_RENAMED = '        "--allow-drops-renamed",'

# E1：不再读子报告的 verdict（passed=False 静默）
VERDICT_READ_OFF = '        v = {"passed": True}  # 变异：不再读子报告的 verdict'

# E2：不再检查参考文本（WER 列全 `-` 时静默）
WITHREF_CHECK_OFF = "        if False:  # 变异：不再检查参考文本"

CALL_IN_LOOP_OLD = '        why = invalid_round_reason(runs[cond].get("summary", {}))'
OVERRIDE_OLD = "        if why and not args.allow_invalid:"
ALLOW_INVALID_OLD = '        "--allow-invalid",'
ALLOW_DROPS_OLD = '        "--allow-drops",'
VERDICT_READ_OLD = '        v = runs[cond].get("verdict") or {}'
WITHREF_CHECK_OLD = '        if (runs[cond].get("summary", {}).get("withReference") or 0) == 0:'

# (变异名, 原文锚点, 替换成, 必须变红的用例)
MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "A1. 0 片段不再拦（VAD 挂了也照出对照表）",
        BODY_OLD,
        SEG_CHECK_OFF,
        "test_zero_segments_is_invalid",
    ),
    (
        "A2. 0 字幕不再拦 —— **H4 的核心漏洞**（short.json 那种轮次重新混进表）",
        BODY_OLD,
        SUB_CHECK_OFF,
        "test_zero_subtitles_is_invalid",
    ),
    (
        "A3. 判序颠倒：0 片段时报「0 个片段 → 0 条字幕」（把根因说成现象）",
        BODY_OLD,
        ORDER_SWAPPED,
        "test_zero_segments_is_invalid",
    ),
    (
        "A4. 缺键默认成 1（旧报告/异常字典被静默放行）",
        BODY_OLD,
        DEFAULT_ONE,
        "test_missing_keys_are_treated_as_zero",
    ),
    (
        "B1. 把 verdict.passed 写进判据（会一起拦掉「只是跑得慢」的有效证据）",
        '    segs = summary.get("totalSegments") or 0\n',
        VERDICT_IN_REASON,
        "test_reason_only_reads_the_two_totals",
    ),
    (
        "B2. 把「背压丢弃」塞进本判据（职责重叠，两个守卫开始互相牵动）",
        BODY_OLD,
        DROPPED_IN_REASON,
        "test_dropped_is_not_this_guard_s_business",
    ),
    (
        "C1. 判据不在条件循环内调用（守卫被挪走/删掉）",
        CALL_IN_LOOP_OLD,
        CALL_IN_LOOP_OFF,
        "test_guard_runs_inside_the_condition_loop",
    ),
    (
        "C2. 没有出口开关（守卫变成无法绕过的硬墙）",
        OVERRIDE_OLD,
        NO_OVERRIDE,
        "test_main_is_wired_to_the_guard",
    ),
    (
        "D1. CLI 的 --allow-invalid 被改名（用户传不进去）",
        ALLOW_INVALID_OLD,
        ALLOW_INVALID_RENAMED,
        "test_allow_invalid_option_exists",
    ),
    (
        "D2. 兄弟守卫的出口 --allow-drops 被改名",
        ALLOW_DROPS_OLD,
        ALLOW_DROPS_RENAMED,
        "test_allow_invalid_option_exists",
    ),
    (
        "E1. 不再读子报告的 verdict（passed=False 静默通过）",
        VERDICT_READ_OLD,
        VERDICT_READ_OFF,
        "test_main_warns_when_verdict_failed",
    ),
    (
        "E2. 不再检查参考文本（WER 列全 `-` 时静默）",
        WITHREF_CHECK_OLD,
        WITHREF_CHECK_OFF,
        "test_main_warns_when_verdict_failed",
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
