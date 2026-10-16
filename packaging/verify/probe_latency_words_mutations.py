#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""变异探针：验证 `test_eval_latency_words.py` 的断言真有分辨力

为什么需要这个
--------------
本轮修的是一类**结构性**缺陷，它有一整套「改坏了也全绿」的路径：

  * 锚点从「最后一个词」退化成「第一个词」或「最后一个数组元素」
    —— 只在**乱序**输入上才看得出来，顺手的用例永远绿；
  * 拿不到词级时间戳时用 `0.0` 顶上
    —— 延迟变小、看起来「性能更好」，而报告里没有任何异常；
  * 统计量无数据时用 `0.0` 占位（旧 `latency.p95` 就是这个坑）
    —— 「算不出来」被读成「延迟极小」；
  * 公式把锚点整个丢掉（`pushWall - feed_t0`）
    —— 数值仍然「像模像样」，但量的是别的东西。

本脚本对 `scripts/eval_asr_quality.py` 做局部变异（**原地改，跑完立即还原**，
每一步都用 md5 核对文件确实复原），逐条确认：该变异必须让**点名的那条**用例变红。
其余变红的用例只作提示打印，不判失败（一个变异可以合理地牵动多条规则）。

为什么是「原地改」而不是「写一份 `_mutant_*.py` 副本」：
被测模块由测试模块**按固定路径**加载（`SCRIPT` 指向 `scripts/eval_asr_quality.py`），
写副本的话测试仍然会去读真身，变异等于没生效 —— 那叫空转。

行尾：`_read_src/_write_src` 把 CRLF 归一化成 LF 只为匹配锚点，写回时按原 eol
还原 —— `eval_asr_quality.py` 是**纯 CRLF 例外文件**，探针不能把它改成 LF。

用法
----
    <venv>/python.exe packaging/verify/probe_latency_words_mutations.py
退出码 0 = 全部变异都被抓住；1 = 有变异没被抓住（断言缺分辨力）。

这是一次性验证证据，不参与常规测试；常规回归跑 `backend/tests/test_eval_latency_words.py`。

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
TEST_FILE = "tests/test_eval_latency_words.py"
BASETEMP = REPO.parent / "安装空间" / "_pytest_tmp_latency_probe"

PY = REPO.parent / "安装空间" / "venv" / "Scripts" / "python.exe"

# ============================ 变异锚点 ============================

# --- A 组：锚点选取（word_end_seconds）---
END_RETURN_OLD = "    return max(ends) if ends else None"
END_RETURN_MIN = "    return min(ends) if ends else None"
END_RETURN_FIRST = "    return ends[0] if ends else None"
END_RETURN_ZERO = "    return max(ends) if ends else 0.0"

# --- B 组：缺锚点语义 ---
SKIP_ANCHOR_OLD = """            if anchor is None:
                continue"""
SKIP_ANCHOR_ZERO = """            if anchor is None:
                anchor = 0.0"""

FEED_GUARD_OLD = """        if not self.feed_t0:
            return []"""
FEED_GUARD_OFF = """        if False:
            return []"""

# --- C 组：延迟公式 ---
FORMULA_OLD = '                    "latency": round(sub["pushWall"] - (self.feed_t0 + anchor), 3),'
FORMULA_NO_ANCHOR = '                    "latency": round(sub["pushWall"] - self.feed_t0, 3),'

ANCHOR_OUT_OLD = '                    "audioWordsEnd": anchor,'
ANCHOR_OUT_ZERO = '                    "audioWordsEnd": 0.0,'

# --- D 组：旧口径保留 / 统计量守卫 / 接线 ---
PAIR_GUARD_OLD = """        if len(segs) != len(subs):
            return []"""
PAIR_GUARD_OFF = """        if False:
            return []"""

NEW_P95_GUARD_OLD = '            "p95": percentile(word_lat_values, 0.95) if word_lat_values else None,'
NEW_P95_GUARD_OFF = '            "p95": percentile(word_lat_values, 0.95),'

RENAME_OLD = "    def words_latency(self) -> list[dict]:"
RENAME_NEW = "    def words_latency_renamed(self) -> list[dict]:"

MISSING_WORDS_OLD = '            "missingWords": words_missing,'
MISSING_WORDS_RENAMED = '            "detachedCount": words_missing,'

ANCHOR_GUARD_OLD = "            if anchor is None:"
ANCHOR_GUARD_OFF = "            if anchor is 0:"

WIRE_ANCHOR_OLD = "        audio_words_end = word_end_seconds(words)"
WIRE_ANCHOR_ZERO = "        audio_words_end = 0.0"

# --- E 组：V6-6 的「whisper 段 → VAD 块」时间包含映射（1:N 恒等式）---
# 这一组的共同特征：**1:1 的格永远看不出问题**（lowvol / multi），
# 只有 `long` / `real` 那样的 1:N 格才会暴露 —— 所以每条都必须有分辨力。
HITS_OLD = "            seg = hits[0]"
HITS_LAST = "            seg = hits[-1]"

TAIL_OLD = '            tail = round(seg["audioEnd"] - anchor, 3)'
TAIL_REVERSED = '            tail = round(anchor - seg["audioEnd"], 3)'

RESIDUAL_OLD = '                    "residual": round((lat_new - lat_old) - tail, 3),'
RESIDUAL_PLUS = '                    "residual": round((lat_new - lat_old) + tail, 3),'

UNMAPPED_OLD = """            if not hits:
                unmapped.append({"index": sub["index"], "audioWordsEnd": anchor})
                continue"""
UNMAPPED_SILENT = """            if not hits:
                continue"""

OVERLAP_OLD = """            if len(hits) > 1:
                overlapped.append("""
OVERLAP_SILENT = """            if False:
                overlapped.append("""

TOL_OLD = "IDENTITY_TOL = 0.005"
TOL_LOOSE = "IDENTITY_TOL = 1.0"

IDENT_FIELD_OLD = '        "latency_identity": {'
IDENT_FIELD_RENAMED = '        "latency_identity_mutated": {'

BLOCK_GUARD_OLD = """        if not self.feed_t0:
            return {"pairs": [], "unmapped": [], "overlapped": []}"""
BLOCK_GUARD_OFF = """        if False:
            return {"pairs": [], "unmapped": [], "overlapped": []}"""

# (变异名, 原文锚点, 替换成, 必须变红的用例)
MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "A1. 锚点取**最早**的词尾（min）而不是最晚的 —— 延迟被系统性低估",
        END_RETURN_OLD,
        END_RETURN_MIN,
        "test_word_end_uses_max_not_first_nor_last",
    ),
    (
        "A2. 锚点取数组**第一个元素**的词尾 —— 词戳乱序时结果错",
        END_RETURN_OLD,
        END_RETURN_FIRST,
        "test_word_end_uses_max_not_first_nor_last",
    ),
    (
        "A3. 取不到词戳时返回 0 而不是 None（拿不到锚点伪装成延迟极小）",
        END_RETURN_OLD,
        END_RETURN_ZERO,
        # 注意：点名的是「全坏输入」那条用例，不是「空输入」那条 ——
        # 空输入被函数开头的 `if not words` 提前拦下，走不到这一行。
        # 第一版探针点错了名，这里按实际抓住它的用例改正（教训 25：探针自己也会骗人）。
        "test_word_end_skips_malformed_but_keeps_valid",
    ),
    (
        "B1. 缺锚点的字幕不再跳过，改成拿 0 顶上（错位样本混进统计）",
        SKIP_ANCHOR_OLD,
        SKIP_ANCHOR_ZERO,
        "test_words_latency_skips_subtitles_without_anchor",
    ),
    (
        "B2. 去掉 feed_t0 未设置的守卫（锚点基准成 0，得到巨大假延迟）",
        FEED_GUARD_OLD,
        FEED_GUARD_OFF,
        "test_words_latency_empty_before_feed_start",
    ),
    (
        "C1. 公式丢掉词级锚点（量的是「喂入至今」，不是这句话的延迟）",
        FORMULA_OLD,
        FORMULA_NO_ANCHOR,
        "test_words_latency_computes_anchor_based_value",
    ),
    (
        "C2. 明细里回写 anchor 为 0（恒等式对不上，无法事后对账）",
        ANCHOR_OUT_OLD,
        ANCHOR_OUT_ZERO,
        "test_identity_new_minus_old_equals_silence_tail",
    ),
    (
        "D1. 旧 `pair()` 放宽条数相等的要求（产出错位但看着合理的延迟）",
        PAIR_GUARD_OLD,
        PAIR_GUARD_OFF,
        "test_pair_still_requires_equal_counts",
    ),
    (
        "D2. 新口径 p95 丢掉 None 守卫（无数据时落 0.0 = 「延迟极小」）",
        NEW_P95_GUARD_OLD,
        NEW_P95_GUARD_OFF,
        "test_new_metric_never_uses_zero_placeholder",
    ),
    (
        "D3. `words_latency` 被改名（新口径写了一整段却没人调用）",
        RENAME_OLD,
        RENAME_NEW,
        "test_words_latency_computes_anchor_based_value",
    ),
    (
        "D4. 缺锚点计数被改名（覆盖缺口从报告里消失）",
        MISSING_WORDS_OLD,
        MISSING_WORDS_RENAMED,
        "test_legacy_latency_field_keys_unchanged",
    ),
    (
        "D5. 缺锚点守卫被改成 `is 0`（守卫形同虚设，None 不再被拦）",
        ANCHOR_GUARD_OLD,
        ANCHOR_GUARD_OFF,
        "test_missing_words_guard_present",
    ),
    (
        "D6. `on_subtitle` 不再算词级锚点，直接写 0（新口径整条退化）",
        WIRE_ANCHOR_OLD,
        WIRE_ANCHOR_ZERO,
        "test_on_subtitle_without_words_marks_none_not_zero",
    ),
    (
        "E1. 块映射取**最晚**命中的块而不是最早（区间重叠时结果不可预测）",
        HITS_OLD,
        HITS_LAST,
        "test_block_map_overlap_takes_earliest",
    ),
    (
        "E2. 静音尾巴算反（块尾−词尾 → 词尾−块尾，得到负尾巴）",
        TAIL_OLD,
        TAIL_REVERSED,
        "test_block_map_tail_is_block_end_minus_anchor",
    ),
    (
        "E3. 恒等式残差的符号写反（− 尾巴 → + 尾巴）—— 残差不再恒为 0",
        RESIDUAL_OLD,
        RESIDUAL_PLUS,
        "test_block_map_covers_one_to_many",
    ),
    (
        "E4. 落在块外的字幕被**静默丢弃**（「验了几条 / 漏了几条」从报告里消失）",
        UNMAPPED_OLD,
        UNMAPPED_SILENT,
        "test_block_map_marks_out_of_range_as_unmapped",
    ),
    (
        "E5. 区间重叠不再记录（最该排查的异常被吞掉）",
        OVERLAP_OLD,
        OVERLAP_SILENT,
        "test_block_map_overlap_takes_earliest",
    ),
    (
        "E6. 恒等式容差被放大到 1.0 秒（半秒的错位会被放过）",
        TOL_OLD,
        TOL_LOOSE,
        "test_identity_tolerance_is_tight",
    ),
    (
        "E7. `latency_identity` 字段改名（恒等式落了盘却没人读）",
        IDENT_FIELD_OLD,
        IDENT_FIELD_RENAMED,
        "test_identity_field_wired_into_report_and_summary",
    ),
    (
        "E8. 去掉块映射的 feed_t0 守卫（未开始喂入时算出假延迟）",
        BLOCK_GUARD_OLD,
        BLOCK_GUARD_OFF,
        "test_block_map_empty_before_feed_start",
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
    if eol != "\r\n":
        print(f"!! 目标文件不是 CRLF（eol={eol!r}）—— 与「纯 CRLF 例外文件」的记录不符，先查清")
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
