#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 MIT OCW 官方 VTT 抽术语候选（V7 · P5）

为什么用「跨讲次共现」做判据
--------------------------
从课堂转写里挑「专业术语」，最直接的判据是**词频** —— 但词频会把
`the` / `like` / `know` 这类普通词排上来。⇒ 换一个更强的判据：

> **一个词在越多讲次里出现，它越是这门课的核心概念。**

「人工智能」「表示」「约束」「搜索」在多讲反复出现；「basically」不会。
⇒ 主判据 = **出现讲次数**，辅以**总频次**与**词长/形态**（排除虚词、排除 too/very 这类程度词）。

产出
----
1. **单词候选**（去虚词后的高频实义词）
2. **多词短语候选**（2~3 词，AI 领域术语大量是多词：goal tree、alpha-beta pruning…）
3. 每个候选带：出现讲次数、总频次、示例出处（讲次 + 时间码）

⚠️ 产出的是**候选**，最终术语库要**人工筛**（含中英对照）。

用法
----
    <venv>/python.exe scripts/extract_terms_from_vtt.py --vtt-dir <dir> --out <md>
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from build_en_reference import parse_vtt  # noqa: E402

# 英语虚词/功能词（不作为术语候选）
STOP = set("""
a an the of to in for on with at by from as is are was were be been being am
this that these those it its he she they we you i him her them us me my your our their
and or but so if then than because while when where which who whom what how why not no nor
yes do does did done doing have has had having will would shall should can could may might must
one two three four five six seven eight nine ten first second third next last
some any all more most other others such own same very just also only both each every
there here now then well okay yeah oh right know think see say said get got go goes going
come comes came take takes took make makes made use uses used using
thing things lot lots bit kind sort way ways time times good bad big small long short
man men woman women people person student students course class
let lets put puts see look looks give gives gave find finds found
""".split())


def ngrams(tokens: list[str], n: int) -> list[tuple[str, ...]]:
    return [tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


def main() -> int:
    ap = argparse.ArgumentParser(description="从 OCW 官方 VTT 抽术语候选")
    ap.add_argument("--vtt-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-lectures", type=int, default=3,
                    help="至少在几讲里出现才算核心概念")
    ap.add_argument("--top", type=int, default=60)
    args = ap.parse_args()

    files = sorted(Path(args.vtt_dir).glob("*.vtt"))
    if not files:
        print(f"❌ {args.vtt_dir} 里没有 .vtt")
        return 1

    unigram_lect: dict[str, set[str]] = defaultdict(set)
    unigram_freq: Counter = Counter()
    bigram_lect: dict[tuple, set[str]] = defaultdict(set)
    bigram_freq: Counter = Counter()
    trigram_lect: dict[tuple, set[str]] = defaultdict(set)
    trigram_freq: Counter = Counter()
    example: dict[str, str] = {}

    for f in files:
        lec = f.stem
        cues = parse_vtt(f)
        for c in cues:
            toks = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]*", c["text"])]
            toks = [w.lower() for w in toks]
            for w in toks:
                if w not in STOP and len(w) > 2:
                    unigram_lect[w].add(lec)
                    unigram_freq[w] += 1
                    example.setdefault(w, f"{lec} @{c['start']:.0f}s")
            for g in ngrams(toks, 2):
                if all(w not in STOP for w in g) and len(g[0]) > 2:
                    bigram_lect[g].add(lec)
                    bigram_freq[g] += 1
                    example.setdefault(" ".join(g), f"{lec} @{c['start']:.0f}s")
            for g in ngrams(toks, 3):
                if all(w not in STOP for w in g) and len(g[0]) > 2:
                    trigram_lect[g].add(lec)
                    trigram_freq[g] += 1
                    example.setdefault(" ".join(g), f"{lec} @{c['start']:.0f}s")

    def score(lect_n: int, freq: int) -> float:
        return lect_n * math.log(freq + 1)

    def pick(d_lect, d_freq, min_lect, top, label):
        items = [(w, len(d_lect[w]), d_freq[w]) for w in d_freq
                 if len(d_lect[w]) >= min_lect]
        items.sort(key=lambda x: (-score(x[1], x[2]), -x[2]))
        out = items[:top]
        print(f"\n== {label}（≥{min_lect} 讲，top {len(out)}）==")
        for w, ln, fr in out:
            key = w if isinstance(w, str) else " ".join(w)
            print(f"  {ln:2d}讲 {fr:4d}次  {key}")
        return out

    uni = pick(unigram_lect, unigram_freq, args.min_lectures, args.top, "单词候选")
    bi = pick(bigram_lect, bigram_freq, max(2, args.min_lectures - 1), args.top // 2,
              "双词短语候选")
    tri = pick(trigram_lect, trigram_freq, 2, args.top // 4, "三词短语候选")

    md = [
        "# MIT 6.034 术语候选（从官方 VTT 自动抽取）", "",
        f"来源：{len(files)} 讲官方 VTT（`{args.vtt_dir}`）", "",
        "> **判据**：出现讲次数（跨讲次共现）× 总频次 —— 跨讲次复现是「核心概念」的"
        "最直接信号；虚词/普通词已在抽取阶段剔除。", "",
        "> ⚠️ **这是候选，不是定稿** —— 仍需人工筛（含中英对照）。", "",
        "## 单词候选", "", "| 词 | 讲次数 | 总频次 | 示例出处 |", "|---|---|---|---|",
    ]
    for w, ln, fr in uni:
        md.append(f"| `{w}` | {ln} | {fr} | `{example.get(w,'')}` |")
    md += ["", "## 双词短语候选", "", "| 短语 | 讲次数 | 总频次 | 示例出处 |", "|---|---|---|---|"]
    for w, ln, fr in bi:
        key = " ".join(w)
        md.append(f"| `{key}` | {ln} | {fr} | `{example.get(key,'')}` |")
    md += ["", "## 三词短语候选", "", "| 短语 | 讲次数 | 总频次 | 示例出处 |", "|---|---|---|---|"]
    for w, ln, fr in tri:
        key = " ".join(w)
        md.append(f"| `{key}` | {ln} | {fr} | `{example.get(key,'')}` |")
    Path(args.out).write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
