#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""官方稿转写疏漏模式扫描器（英文格专用）

为什么是这个思路
----------------
第一版用「官方稿 vs ASR 草稿的逐词 diff」找疏漏，**信噪比太低**（P4 报 100 个可疑，
P20 报 105 个）—— 绝大多数是**正常差异**：
  · 课程里满屏的符号/字母（搜索课的 `s, a, b, d` 节点标记、公式、字母表）
  · 数字（`0.781` / `10` / `16`）
  · 官方稿的 `[INAUDIBLE]` 与 ASR 的对应内容本来就不一样
  · ASR 省略虚词、官方稿保留（英文格的老问题）
⇒ 拿它当「疏漏清单」会淹没真问题。

**高杠杆做法**：P1 人工通读时发现了 **13 类**官方转写疏漏，它们有**可识别的语法形态**
（漏系动词/冠词、同音错字、固定搭配写错）。⇒ 把这 13 类**泛化成模式**，
一次扫描所有讲次。
⇒ 命中的是「形态可疑」的句子，**人再复核**，工作量从「通读 194 行」降到「看十几处」。

⚠️ 本工具**只报线索，不改文本**。每一条仍需人工裁决。

用法
----
    <venv>/python.exe scripts/scan_official_typos.py --official <stem>.official.txt
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

# (正则, 说明, 严重度)
# 严重度：high = 语法上唯一可能（可直接 fix）；mid = 高度可疑（需核一句）；low = 提示
PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # ── 漏系动词 / 冠词（P1 实测最常见的一类）──
    (re.compile(r"\b(This|That|These|Those) (a|an|the)\b"),
     "缺系动词：应为 This is/These are …", "high"),
    (re.compile(r"\bIt's (very|really|quite|extremely) \w+"),
     "缺冠词：It's a very …", "high"),
    (re.compile(r"\bSo that's (powerful|important|interesting|good|bad|hard|easy|useful) "),
     "缺冠词：So that's a …", "high"),
    (re.compile(r"\b(all|each|both) (four|three|two|five|six|seven|eight|nine|ten) of individuals\b"),
     "缺限定词：… of the individuals", "high"),
    (re.compile(r"\brepresentations that (support|model) models? targeted to\b"),
     "介词错：targeted **at** …", "high"),
    (re.compile(r"\bmodels? targeted to thinking\b"),
     "介词错：targeted **at** thinking", "high"),

    # ── 同音 / 形近错字（P1 实测）──
    (re.compile(r"\bwhey\b", re.I), "同音错：whey → **why**", "high"),
    (re.compile(r"\bThat's whey\b", re.I), "同音错：whey → **why**", "high"),
    (re.compile(r"\bat MIT approach\b", re.I), "at → **the**（the MIT approach）", "high"),
    (re.compile(r"\bEven in humanities class, MIT approach\b", re.I),
     "漏 the：the MIT approach", "high"),
    (re.compile(r"\byou algebra\b", re.I), "漏词：you **learned** algebra", "high"),
    (re.compile(r"\bexplain to best\b", re.I), "to → **it**（explain it best）", "high"),
    (re.compile(r"\bthe right representation most involve\b", re.I),
     "most → **must**", "high"),
    (re.compile(r"\bI don't anybody\b", re.I),
     "语序错：I don't **think anybody thinks** …", "mid"),
    (re.compile(r"\bgenerated test\b", re.I),
     "错：generate **and** test（本课自用术语）", "high"),
    (re.compile(r"\bgetting somewhere where with\b", re.I),
     "多余 where：getting somewhere with …", "mid"),
    (re.compile(r"\bground f here\b", re.I), "疑为 ground it / a ground for this", "mid"),
    (re.compile(r"\bI don't anybody think that I'm cheating\b", re.I),
     "语序错", "mid"),

    # ── 其它常见形态 ──
    (re.compile(r"\bThis a Course\b"), "缺 is：This **is** a Course", "high"),
    (re.compile(r"\balgae infested rivers\b", re.I), "疑为单数：algae-infested **river**", "low"),
    (re.compile(r"\bsaid aggressively and without hesitation\b", re.I),
     "转写串行：这是课堂另一句话（不改；归一化后不影响 WER）", "low"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="官方稿转写疏漏模式扫描")
    ap.add_argument("--official", required=True, help="<stem>.official.txt")
    ap.add_argument("--out", default="", help="线索清单输出（md）")
    args = ap.parse_args()

    lines = [ln.strip() for ln in
             Path(args.official).read_text(encoding="utf-8").splitlines() if ln.strip()]

    hits = []
    for i, ln in enumerate(lines):
        for pat, desc, sev in PATTERNS:
            for m in pat.finditer(ln):
                hits.append({"line": i + 1, "text": ln, "desc": desc,
                             "sev": sev, "matched": m.group(0)})

    order = {"high": 0, "mid": 1, "low": 2}
    hits.sort(key=lambda h: (order[h["sev"]], h["line"]))

    print(f"官方稿 {len(lines)} 行｜命中 {len(hits)} 处"
          f"（high {sum(1 for h in hits if h['sev']=='high')} / "
          f"mid {sum(1 for h in hits if h['sev']=='mid')} / "
          f"low {sum(1 for h in hits if h['sev']=='low')}）")
    for h in hits:
        print(f"  [{h['sev']:4s}] 行{h['line']:4d}  {h['matched']!r:46s} {h['desc']}")

    if args.out:
        out = Path(args.out)
        md = [f"# 官方稿疏漏线索 · {Path(args.official).stem}", "",
              "> 由**模式扫描**生成（模式来自 P1 人工通读时发现的 13 类疏漏）。",
              "> **只是线索** —— 每条仍需人工裁决；`low` 级别的往往不用改。", "",
              "| 行 | 命中 | 说明 | 严重度 | 原文 |", "|---|---|---|---|---|"]
        for h in hits:
            md.append(f"| {h['line']} | `{h['matched']}` | {h['desc']} | {h['sev']} | "
                      f"{h['text'][:110].replace('|', chr(92)+'|')} |")
        out.write_text("\n".join(md) + "\n", encoding="utf-8")
        print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
