#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""官方稿转写疏漏定位器（英文格专用）

为什么需要它
------------
英文格的参考稿主体是 **MIT OCW 官方 VTT**（人工转写）。它比 ASR 草稿准得多，
但**自己也会错**（漏 `is`/`a`/`the`、`why`→`whey`），而且**方向对 ASR 不利**
（参考稿比音频少/错词 ⇒ ASR 说对了反被判错 ⇒ WER 系统性偏高）。

人工通读 194 行找错效率低。⇒ 改用**独立第三方证据**：
**ASR 草稿与官方稿逐段 diff**，分歧大的段落就是官方稿可疑处。
⚠️ ASR 也会错，所以这个工具**只产出「线索清单」，不自动改文本** ——
每一条仍需人工裁决（`fix` / `suspect` / `note`）。

与中文格的差别
--------------
中文格必须通读整篇（错字是随机分布的）；英文格只需看**分歧大**的段落
（官方转写质量高，错处集中）。⇒ 校对量从 194 行降到十几处。

用法
----
    <venv>/python.exe scripts/find_official_typos.py \\
        --official <stem>.official.txt --draft <stem>.draft.json --out <stem>.typos.md
"""
from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _load_eval():
    spec = importlib.util.spec_from_file_location("_ev", HERE / "eval_asr_quality.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main() -> int:
    ap = argparse.ArgumentParser(description="官方稿转写疏漏定位（只产线索，不改文本）")
    ap.add_argument("--official", required=True, help="<stem>.official.txt（逐 cue 行）")
    ap.add_argument("--draft", required=True, help="<stem>.draft.json（ASR 草稿）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--threshold", type=float, default=0.22,
                    help="段级 WER 超过它就列为可疑")
    args = ap.parse_args()

    ev = _load_eval()
    official_lines = [ln.strip() for ln in
                      Path(args.official).read_text(encoding="utf-8").splitlines() if ln.strip()]
    draft = json.loads(Path(args.draft).read_text(encoding="utf-8"))
    segs = draft["segments"]

    # 官方稿按行拼成 token 流；ASR 草稿也拼成 token 流；**整体做一次对齐**，
    # 再把差异映射回「官方稿的第几行」—— 这样即使切分粒度不同也能比。
    ofl = official_lines
    otok = [(i, w) for i, ln in enumerate(ofl) for w in ln.split()]
    htok = [w for s in segs for w in s["text"].split()]
    owords = [w for _, w in otok]

    sm = difflib.SequenceMatcher(None, owords, htok, autojunk=False)
    rows = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        o_slice = otok[i1:i2]
        if not o_slice:
            continue
        lines_hit = sorted({ln for ln, _ in o_slice})
        o_txt = " ".join(w for _, w in o_slice)
        h_txt = " ".join(htok[j1:j2])
        r = ev.normalize(o_txt, "en").split()
        h = ev.normalize(h_txt, "en").split()
        e = ev.edit_distance(r, h)
        rate = e / len(r) if r else 1.0
        rows.append({
            "lines": lines_hit,
            "official": o_txt,
            "asr": h_txt,
            "refUnits": len(r), "edits": e, "rate": round(rate, 3),
        })

    sus = [r for r in rows if r["rate"] > args.threshold or r["edits"] >= 4]
    sus.sort(key=lambda r: (-r["edits"], -r["rate"]))

    out = Path(args.out)
    lines = [
        f"# 官方稿疏漏线索 · {Path(args.official).stem}",
        "",
        "> **这是线索，不是定论。** 由「官方 VTT vs ASR 草稿」的逐词 diff 生成 ——",
        "> ASR 是**独立第三方证据**，能指出官方稿可疑的位置；但 **ASR 自己也可能错**，",
        "> 所以每一条都**必须人工裁决**（`fix` / `suspect` / `note`），不能自动改。",
        "",
        f"官方稿 {len(ofl)} 行 / ASR 草稿 {len(segs)} 段；"
        f"差异块 {len(rows)} 个，其中可疑 {len(sus)} 个（阈值：段级差异 > {args.threshold} 或 ≥4 词）。",
        "",
        "| 官方稿行 | 官方稿文本 | ASR 听到的 | 差异 |",
        "|---|---|---|---|",
    ]
    for r in sus[:60]:
        ln = ",".join(str(x + 1) for x in r["lines"])
        o = r["official"][:150].replace("|", "\\|")
        h = r["asr"][:150].replace("|", "\\|")
        lines.append(f"| {ln} | {o} | {h} | {r['edits']}/{r['refUnits']} |")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"差异块 {len(rows)} 个，可疑 {len(sus)} 个 → {out}")
    for r in sus[:15]:
        ln = ",".join(str(x + 1) for x in r["lines"])
        print(f"  行{ln:10s} {r['edits']:3d}/{r['refUnits']:<3d} 官方: {r['official'][:70]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
