#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 MIT OCW 官方 VTT 包装成 `preproof_transcript.py` 能吃的 `.draft.json` 形状（V7 · en_lecture）

为什么需要这个包装
------------------
`preproof_transcript.py` 的输入是 `<stem>.draft.json`（`segments[].text` + 时间码），
修正表按**行文本**匹配。英文格的参考稿来源不是 ASR 草稿，而是**官方 VTT**，
形状对不上。与其另写一套校对链（多一份要维护、也多一处可能出错），
不如**把官方稿包装成同一种形状**，让整条已验证的校对链原样复用：
修正表 → changes.tsv / suspects.md / review.html → preproof.txt。

🔴 **必须自证的一点**：包装出来的 `audioStart/audioEnd` 是**切片内相对时间**
（= cue 的 OCW 绝对时间 − 偏移）。校对页的播放定位依赖这两个字段，
写错了会导致「点某一行去听」听到错误的秒数。

用法
----
    <venv>/python.exe scripts/wrap_official_as_draft.py \\
        --vtt <lec1.vtt> --audio <name.wav> \\
        --clip-start 125.12 --clip-end 755.12 \\
        --out testset/english_lecture/en_lecture_mit6034_p1.official.draft.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from build_en_reference import parse_vtt  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="官方 VTT → draft.json 形状（供预校对链复用）")
    ap.add_argument("--vtt", required=True)
    ap.add_argument("--audio", required=True, help="音频文件名（写进 info.audio）")
    ap.add_argument("--clip-start", type=float, required=True)
    ap.add_argument("--clip-end", type=float, required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cues = parse_vtt(Path(args.vtt))
    win = [c for c in cues if args.clip_start <= c["start"] < args.clip_end]
    if not win:
        print("❌ 时间窗内没有 cue")
        return 1

    segs = []
    for i, c in enumerate(win):
        segs.append({
            "index": i,
            # 切片内相对时间：校对页要能对得上音频
            "audioStart": round(c["start"] - args.clip_start, 3),
            "audioEnd": round(c["end"] - args.clip_start, 3),
            "text": c["text"],
        })

    dur = args.clip_end - args.clip_start
    doc = {
        "info": {
            "audio": args.audio,
            "durationSeconds": round(dur, 3),
            "durationMinutes": round(dur / 60, 2),
            "vadSegments": len(segs),
            "paragraphs": len(segs),
            "chars": sum(len(s["text"]) for s in segs),
            "engine": {
                "model": "official-vtt",
                "language": "en",
                "terms": "off",
                "domain": "",
            },
            "source": "MIT OpenCourseWare 6-034 Fall 2010, Lecture 1 (official VTT)",
            "note": (
                "本文件的 text 是**官方 VTT 字幕**（不是 ASR 输出）。"
                "audioStart/audioEnd 已换算成切片内相对时间"
                f"（OCW 绝对时间 − {args.clip_start}）。"
                "用它驱动 preproof_transcript.py，让校对链与中文格完全同构。"
            ),
            "generatedAt": "2026-10-26",
        },
        "segments": segs,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ {out}")
    print(f"   {len(segs)} 段 / {doc['info']['chars']} 字符 / "
          f"相对时间 0 ~ {max(s['audioEnd'] for s in segs):.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
