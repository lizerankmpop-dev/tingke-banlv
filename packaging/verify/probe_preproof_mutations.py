#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""变异探针：验证 `test_preproof_transcript.py` 的断言真有分辨力

为什么需要这个
--------------
「有测试覆盖」不等于「断言验得到」。新加的断言如果被别的路径顺手满足，
就会**永远绿**、永不报警 —— 这比没有断言更糟，因为它给出的是假信心。

本脚本对被测源码做**局部变异**（在 `scripts/` 下临时落一份改过的副本、
跑完即删），逐条确认：

    * 该变异必须让**指定的那条**用例变红（`去掉即红`）
    * 未被点名的用例不受影响（`只红那一条`）

用法
----
    <venv>/python.exe packaging/verify/probe_preproof_mutations.py
退出码 0 = 全部变异都被抓住；1 = 有变异没被抓住（断言缺分辨力）。

这是一次性验证证据，不参与常规测试；常规回归跑 `backend/tests/test_preproof_transcript.py`。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TESTS = REPO / "backend" / "tests"
SCRIPTS = REPO / "scripts"

sys.path.insert(0, str(TESTS))

import test_preproof_transcript as T  # noqa: E402

PREPROOF = (SCRIPTS / "preproof_transcript.py").read_text(encoding="utf-8")
REVIEW = (SCRIPTS / "review_page.py").read_text(encoding="utf-8")

ORDER_KEY = '        return (0 if c.kind == "fix" else 1, -len(c.pattern), c.pattern)'

# (变异名, 目标文件, 原文锚点, 替换成, 必须变红的用例)
MUTATIONS = [
    (
        "A. 存疑清单改回「按原始文本找模式」（本轮真 bug 的形态）",
        "preproof_transcript.py",
        '        new_seg["suspects"] = extract_suspects(text)',
        '        new_seg["suspects"] = [c.pattern for c in corrections'
        ' if c.kind == "suspect" and c.pattern in seg.get("text", "")]',
        T.test_suspects_field_matches_actual_markers,
    ),
    (
        "B. 去掉 BOM 剥离",
        "preproof_transcript.py",
        '    text = text.lstrip("\\ufeff")\n',
        "",
        T.test_parse_corrections_strips_leading_bom,
    ),
    (
        "C. 排序丢掉「先全部 fix 再全部 suspect」",
        "preproof_transcript.py",
        ORDER_KEY,
        "        return (-len(c.pattern), c.pattern)",
        T.test_suspect_needs_fix_to_run_first,
    ),
    (
        "D. 排序丢掉「组内长 pattern 优先」",
        "preproof_transcript.py",
        ORDER_KEY,
        '        return (0 if c.kind == "fix" else 1, c.pattern)',
        T.test_order_for_apply_longest_pattern_first_within_group,
    ),
    (
        "E. 清单不再合并同一片段的多次出现",
        "preproof_transcript.py",
        "        for pat, n in Counter(pats).items():",
        "        for pat, n in ((p, 1) for p in pats):",
        T.test_write_suspects_md_declares_real_total_and_marks_multiplicity,
    ),
    (
        "F. 校对页的 localStorage 键不再跟随 info.audio（两页会串稿）",
        "review_page.py",
        "const KEY = 'tingke-transcript-' + DRAFT.info.audio;",
        "const KEY = 'tingke-transcript-' + DRAFT.info.durationMinutes;",
        T.test_review_page_localstorage_key_follows_info_audio,
    ),
]

CHECKED = [
    T.test_suspects_field_matches_actual_markers,
    T.test_parse_corrections_strips_leading_bom,
    T.test_suspect_needs_fix_to_run_first,
    T.test_order_for_apply_longest_pattern_first_within_group,
    T.test_write_suspects_md_declares_real_total_and_marks_multiplicity,
    T.test_review_page_localstorage_key_follows_info_audio,
]

# 吃哪个被测对象的用例。变异 review_page 时，吃 preproof 的用例必须仍喂**真身** ——
# 否则它们会因为「拿到一个根本不是 preproof 的模块」而集体报 AttributeError，
# 被探针误报成「该变异牵动多条规则」。这是探针自己的假信号，实测踩过。
CHECKED_PP = set(CHECKED) - {T.test_review_page_localstorage_key_follows_info_audio}
CHECKED_RP = {T.test_review_page_localstorage_key_follows_info_audio}


def _write_mutant(name: str, filename: str, old: str, new: str) -> Path:
    src = PREPROOF if filename == "preproof_transcript.py" else REVIEW
    if old not in src:
        raise SystemExit(f"[{name}] 变异锚点在源码里找不到，探针本身失效：{old!r}")
    # 落在 scripts/ 下：被测模块 import review_page，换个目录就 import 不到
    path = SCRIPTS / f"_mutant_{name.split('.')[0]}.py"
    path.write_text(src.replace(old, new), encoding="utf-8")
    return path


def _make_arg(fn, pp_mod, rp_mod):
    return rp_mod if fn in CHECKED_RP else pp_mod


def _is_red(fn, pp_mod, rp_mod) -> tuple[bool, str]:
    """断言失败（AssertionError）与被测代码抛异常（ValueError 等）**都算红**。

    按 pytest 的语义后者算 error，同样是失败。只捕 AssertionError 会让探针自己
    崩在变异 B（去掉 BOM 剥离 → parse_corrections 抛 ValueError）上，把
    「探针没跑完」伪装成「源码有问题」。
    """
    try:
        fn(_make_arg(fn, pp_mod, rp_mod))
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).splitlines()[0][:88] if str(exc) else "(无消息)"
        return True, f"{type(exc).__name__}: {msg}"
    return False, ""


def main() -> int:
    # 未变异的真身，供「未被点名」的用例交叉验证
    real_pp = T._load("probe_real_pp", SCRIPTS / "preproof_transcript.py")
    real_rp = T._load("probe_real_rp", SCRIPTS / "review_page.py")

    failures: list[str] = []
    for name, filename, old, new, target in MUTATIONS:
        path = _write_mutant(name, filename, old, new)
        try:
            mod = T._load("mutant_under_test", path)
            pp_mod = mod if filename == "preproof_transcript.py" else real_pp
            rp_mod = mod if filename == "review_page.py" else real_rp

            red, detail = _is_red(target, pp_mod, rp_mod)
            if not red:
                print(f"[漏掉] {name}\n        用例 {target.__name__} 仍为绿 —— 断言缺分辨力")
                failures.append(name)
                continue

            print(f"[抓住] {name}\n        变红：{target.__name__} → {detail}")
            others = [fn.__name__ for fn in CHECKED
                      if fn is not target and _is_red(fn, pp_mod, rp_mod)[0]]
            if others:
                print(f"        注意：另有用例也变红 → {others}（该变异确实牵动多条规则）")
        finally:
            path.unlink(missing_ok=True)

    print()
    if failures:
        print(f"结论：{len(failures)}/{len(MUTATIONS)} 条变异没被抓住，需补断言")
        return 1
    print(f"结论：{len(MUTATIONS)}/{len(MUTATIONS)} 条变异全部被抓住，断言有分辨力")
    return 0


if __name__ == "__main__":
    sys.exit(main())
