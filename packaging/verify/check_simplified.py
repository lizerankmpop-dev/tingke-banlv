#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
简繁校验探针（V5-5）—— 扫描 ASR 输出文本，统计繁体字符数，可阻断回归

## 为什么需要它

缺陷 D1（`language="auto"` 让中文课程输出繁体）的**唯一体征就是繁体字符**。
但 CER 这条指标对字形差是"迟钝"的：繁体字的 CER 代价会被算成识别错误，
和"听错了"混在同一个数字里，从 CER 上分不出「字形不对」还是「内容不对」。
更糟的是它**方向可正可负** —— 实测 auto 口径下 fast 反而比 zh 口径低 0.0343，
单看 CER 会得出"繁体没坏处"的错误结论。

所以必须有一条只看**字形**的独立判据。本探针只做一件事：
数出识别文本里的繁体字符，超阈值即失败（退出码 1）。

## 判据

繁体字符的判定用 `zhconv`：对每个汉字，若 `convert(ch, 'zh-hans') != ch`
即视为繁体字形。刻意**逐字**判而不用整句转换后做 diff ——
整句转换会把 `計程車 → 出租车` 这种**词汇**替换也算进来（3 个字全变），
把"用词差异"伪装成"字形差异"，统计口径就不再是"繁体字符数"了。

已知的保守性（可接受，且必须写下来）：个别汉字需要上下文才能定形
（如「著」在不同词里），逐字判定会漏计。所以本探针的数是**下界**，
不能当成"繁体问题的总量"，只能当回归门禁。

## 用法

    # 扫评估产物（取 results[*].recognition）
    python packaging/verify/check_simplified.py --report packaging/eval/xxx.json

    # 扫纯文本 / 目录（.txt / .preproof.txt / .srt 都行，按行统计）
    python packaging/verify/check_simplified.py --dir testset/chinese_lecture

    # 自检（探针自己的变异验证：繁必须检出、简必须不检出）
    python packaging/verify/check_simplified.py --self-test

`packaging/verify/` 不进安装包（见 docs/asr_eval/write-boundary.md），
所以本文件的改动**不会**作废 v3.4.1 交付物指纹，也不需要重打包。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 默认阈值。**由实测标定，不是拍的** —— 标定数据来自 V5-1 的同会话 A/B
# （clean 场景，`packaging/eval/v5_1_ab_*.json`）：
#
#   pre_auto （LANG_DETECT=0，= 修复前 auto）  繁体 147 字 / 433 字 = 33.95%  → 必须 FAIL
#   post_auto（修复后 auto）                   繁体   4 字 / 560 字 =  0.71%  → 必须 PASS
#
# 取 20 字 / 2%（修复后的约 7 倍，修复前的约 1/17）—— 两侧都留了量级余量，
# 所以它既能发现「简体引导语被改坏/开关被默认关掉」这种整片繁体，也不会因为
# 一两个专有名词的正常异体字就误报。
#
# ⚠️ 已知的判据盲区（不要用本探针下"内容对不对"的结论）：修复后残留的 4 个字
# 全是「捲」——那是**识别用字错误**（正確是「卷积」，模型写成了「捲击」），
# 属于内容层问题，不是简繁层没生效。本探针只区分"汉字是不是繁体字形"，
# 区分不了这两种成因。所以 verdict=FAIL 时先看 `languageSource` /
# `langDetectEvents`：若引导语已生效（如 sticky/detected），那 FAIL 的成因在内容层。
DEFAULT_MAX_TRAD_CHARS = 20
DEFAULT_MAX_TRAD_RATIO = 0.02


def _converter():
    """延迟导入 zhconv：它是**评估期**依赖，不在 backend/requirements.txt 里。

    这一点必须显式失败而不是静默跳过 —— 探针"因为缺依赖而什么都没查"
    和"查过且通过"在 CI 上长得一模一样，那才是真正的危险。
    """
    try:
        import zhconv
    except ImportError:  # pragma: no cover - 环境问题
        print(
            "缺少 zhconv（繁简判定所需）。安装：\n"
            "  <venv>/Scripts/python.exe -m pip install --no-deps zhconv",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return zhconv


def is_traditional(ch: str, zhconv) -> bool:
    """单个字符是否是繁体字形（逐字判定，理由见模块头「判据」节）"""
    return zhconv.convert(ch, "zh-hans") != ch


def scan_text(text: str, zhconv, sample_limit: int = 10) -> dict:
    """统计一段文本里的繁体字符（去重前计数 + 去重后清单 + 上下文样例）"""
    total = 0
    hits: list[dict] = []
    seen: dict[str, int] = {}
    for i, ch in enumerate(text):
        if not is_traditional(ch, zhconv):
            continue
        total += 1
        seen[ch] = seen.get(ch, 0) + 1
        if len(hits) < sample_limit:
            hits.append({
                "index": i,
                "char": ch,
                "simplified": zhconv.convert(ch, "zh-hans"),
                "context": text[max(0, i - 8): i + 9],
            })
    n_chars = len(text)
    return {
        "chars": n_chars,
        "tradChars": total,
        "tradRatio": round(total / n_chars, 6) if n_chars else 0.0,
        "distinct": sorted(seen.items(), key=lambda kv: -kv[1]),
        "samples": hits,
    }


# ============ 取文本的三个来源 ============


def texts_from_report(path: Path, sample_limit: int):
    """评估产物：results[*].recognition（识别出的全文）"""
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for idx, r in enumerate(data.get("results") or []):
        text = r.get("recognition")
        if isinstance(text, str) and text.strip():
            label = r.get("scenario") or r.get("audio") or f"results[{idx}]"
            out.append((f"{path.name}::{label}", text))
    if not out:
        print(f"!! {path} 里没有可扫的 recognition 字段", file=sys.stderr)
    return out


def texts_from_file(path: Path, sample_limit: int):
    return [(str(path), path.read_text(encoding="utf-8", errors="replace"))]


def texts_from_dir(root: Path, sample_limit: int):
    out = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in (".txt", ".srt", ".vtt"):
            try:
                out.append((str(p.relative_to(root)), p.read_text(encoding="utf-8", errors="replace")))
            except OSError as e:
                print(f"!! 跳过 {p}: {e}", file=sys.stderr)
    return out


# ============ 自检（探针自己的"变异验证"）============


SELF_TEST_CASES = [
    ("這是測試的繁體字，請使用簡體中文轉寫。", True, "整句繁体必须检出"),
    ("这是测试的繁体字，请使用简体中文转写。", False, "整句简体必须不检出"),
    ("我们要继续做行图线，也就是这个文件了。", False, "正常中文课程文本不能误报"),
    ("卷积神经网络与反向传播", False, "简体专业术语不能误报"),
    ("卷積神經網路與反向傳播", True, "繁体专业术语必须检出"),
    ("abc 123 ,.?!", False, "非汉字不能被当成繁体"),
]


def self_test() -> int:
    """探针自检：**繁必须检出、简必须不检出、非汉字不能误报**

    为什么必须带这条：探针最危险的失效形态是**恒绿** —— 判据写错（比如把
    `!=` 写成 `==`、或字典没加载）之后，它对任何输入都说"0 个繁体"，
    而 CI 上"查过且通过"和"什么都没查"完全同形。这里用正例+反例双向卡住它。
    """
    zhconv = _converter()
    failures = []
    for text, expect_trad, why in SELF_TEST_CASES:
        got = scan_text(text, zhconv)["tradChars"] > 0
        if got != expect_trad:
            failures.append(f"  · {why}：期望 繁体={expect_trad}，实际 繁体={got}  「{text}」")
    if failures:
        print("自检失败：\n" + "\n".join(failures), file=sys.stderr)
        return 1
    print(f"自检通过：{len(SELF_TEST_CASES)} 条用例（繁检出 / 简不检出 / 非汉字不误报）")
    return 0


# ============ 主流程 ============


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="简繁校验探针（V5-5）：统计 ASR 输出里的繁体字符并阻断回归")
    src = ap.add_mutually_exclusive_group(required=False)
    src.add_argument("--report", action="append", type=Path, default=[],
                     help="评估产物 JSON（可多次；取 results[*].recognition）")
    src.add_argument("--text", action="append", type=Path, default=[], help="纯文本文件（可多次）")
    src.add_argument("--dir", type=Path, help="递归扫描目录下的 .txt/.srt/.vtt")
    ap.add_argument("--max-trad-chars", type=int, default=DEFAULT_MAX_TRAD_CHARS,
                    help=f"允许的繁体字符总数上限（默认 {DEFAULT_MAX_TRAD_CHARS}）")
    ap.add_argument("--max-trad-ratio", type=float, default=DEFAULT_MAX_TRAD_RATIO,
                    help=f"允许的繁体字符占比上限（默认 {DEFAULT_MAX_TRAD_RATIO}）")
    ap.add_argument("--sample-limit", type=int, default=10, help="每条来源最多打印多少样例")
    ap.add_argument("--json", type=Path, help="把逐条结果落盘为 JSON（供归档/对拍）")
    ap.add_argument("--self-test", action="store_true", help="只跑探针自检")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()

    items: list[tuple[str, str]] = []
    for p in args.report:
        items += texts_from_report(p, args.sample_limit)
    for p in args.text:
        items += texts_from_file(p, args.sample_limit)
    if args.dir:
        items += texts_from_dir(args.dir, args.sample_limit)

    if not items:
        ap.print_usage(sys.stderr)
        print("!! 没有可扫的输入（--report / --text / --dir 至少给一个）", file=sys.stderr)
        return 2

    zhconv = _converter()
    rows = []
    failed = []
    for label, text in items:
        r = scan_text(text, zhconv, sample_limit=args.sample_limit)
        r["source"] = label
        over = r["tradChars"] > args.max_trad_chars or r["tradRatio"] > args.max_trad_ratio
        r["verdict"] = "FAIL" if over else "ok"
        rows.append(r)
        if over:
            failed.append(r)

    print(f"简繁校验：{len(rows)} 条来源｜阈值 繁体字符 ≤ {args.max_trad_chars} "
          f"且 占比 ≤ {args.max_trad_ratio}")
    print()
    print("| 来源 | 字符数 | 繁体字符 | 占比 | 判定 |")
    print("|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['source']} | {r['chars']} | {r['tradChars']} | {r['tradRatio']:.4%} | {r['verdict']} |")

    for r in failed:
        print(f"\n[{r['verdict']}] {r['source']} —— 繁体 {r['tradChars']} 个 / 占比 {r['tradRatio']:.4%}")
        if r["distinct"]:
            print("  出现最多：" + "、".join(f"{c}×{n}" for c, n in r["distinct"][:10]))
        for s in r["samples"]:
            print(f"  位置 {s['index']}：{s['char']} → {s['simplified']}   …{s['context']}…")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({
            "thresholds": {"maxTradChars": args.max_trad_chars,
                           "maxTradRatio": args.max_trad_ratio},
            "results": rows,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果已落盘 {args.json}")

    if failed:
        print(f"\n❌ 简繁校验未通过：{len(failed)}/{len(rows)} 条来源超出阈值"
              f"（形如「满屏繁体」通常意味着简体引导语没有生效 —— 查 langDetectEvents / languageSource）")
        return 1
    print(f"\n✅ 简繁校验通过：{len(rows)} 条来源全部在阈值内")
    return 0


if __name__ == "__main__":
    sys.exit(main())
