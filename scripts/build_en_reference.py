#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""英文参考稿构建器 · MIT OCW 官方 VTT → 与 B 站切片对齐的参考稿（V7 · en_lecture 格）

为什么英文格的参考稿路线和中文格不同
-------------------------------------
中文格（`accent` / `real`）只能走「ASR 草稿 + 人工校对」，因为 B 站匿名访问拿不到字幕，
也没有别的权威文本可依。

英文格**有权威源**：MIT OpenCourseWare 为 6.034 Fall 2010 每讲提供了
**带精确时间码的官方 VTT 字幕**（`…_TjZBTDzGeGg.vtt`）。实测确认
**B 站搬运版的 0:00 与 OCW 原版 Lecture 1 的 0:00 零偏移**（逐字对齐验证，见下），
所以参考稿可以直接从官方 VTT 切出来 —— **零人工校对成本，且是权威稿**。

本脚本做三件事
--------------
1. 解析 VTT，抽出 `[start, end)` 区间的文本，剥掉 speaker label；
2. **交叉验证**对齐：用 ASR 草稿的每段去核对官方稿同时间窗的文本，
   报告「官方稿有、ASR 漏识」与「ASR 有、官方稿没有」两类分歧 ——
   这一步是必须的：官方 transcript 是**人工整理过**的（会保留 `[INAUDIBLE]`、
   会规整断句），而 ASR 草稿反映的是「模型实际听到了什么」。
   两者都要看，才能判断参考稿与识别结果之间差的到底是**内容**还是**口径**；
3. 输出候选参考稿 + 分歧清单，供人工裁决。

用法
----
    <venv>/python.exe scripts/build_en_reference.py \\
        --vtt <path.vtt> --draft <stem>.draft.json \\
        --clip-start 120 --clip-end 750 --out-dir <dir>

⚠️ **只做「产出候选 + 分歧清单」，不自动定稿** —— 定稿必须经人工确认，
否则就是把未经核对的东西变成基准。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# 官方稿里的说话人标签。B 站切片只有一位主讲（Patrick Winston），
# speaker label 必须在进参考稿前剥掉 —— 否则 15 个词的标签会变成 15 个词的
# 「插入错误」���凭空把 WER 抬高一大截。
_SPEAKER_RE = re.compile(r"^\s*[A-Z][A-Z .'-]{2,40}:\s*")
_CUE_RE = re.compile(
    r"(\d\d):(\d\d):(\d\d)\.(\d\d\d)\s*-->\s*(\d\d):(\d\d):(\d\d)\.(\d\d\d)")
# 官方稿用来标记「听不清」的地方。Whisper 训练数据里也大量出现这个 token，
# 所以**保留**它比删掉更公平：模型如果真输出了 inaudible，两边就对上了。
_INAUDIBLE_RE = re.compile(r"\[?\s*inaudible\s*\]?", re.IGNORECASE)


def parse_vtt(path: Path) -> list[dict]:
    """解析 VTT → [{start, end, text}]（text 已拼行、已剥 speaker label）"""
    cues: list[dict] = []
    cur: dict | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _CUE_RE.search(line)
        if m:
            if cur is not None:
                cues.append(cur)
            g = [int(x) for x in m.groups()]
            cur = {
                "start": g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000,
                "end": g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000,
                "text": "",
            }
            continue
        if cur is None:
            continue
        s = line.strip()
        if not s or s == "WEBVTT" or "-->" in s or s.startswith(("NOTE", "Kind:", "Language:")):
            continue
        s = _SPEAKER_RE.sub("", s)
        cur["text"] = (cur["text"] + " " + s).strip()
    if cur is not None:
        cues.append(cur)
    return cues


def slice_cues(cues: list[dict], start: float, end: float) -> list[dict]:
    """取时间窗内的 cue（按 cue 起点判定；跨界 cue 只在起点落入窗内时保留）"""
    return [c for c in cues if start <= c["start"] < end]


def main() -> int:
    ap = argparse.ArgumentParser(description="MIT OCW 官方 VTT → 英文参考稿候选")
    ap.add_argument("--vtt", required=True)
    ap.add_argument("--draft", required=True, help="<stem>.draft.json（用于交叉验证）")
    ap.add_argument("--clip-start", type=float, required=True, help="切片起点（秒，OCW 时间轴）")
    ap.add_argument("--clip-end", type=float, required=True, help="切片终点（秒，OCW 时间轴）")
    ap.add_argument("--out-dir", default="")
    args = ap.parse_args()

    cues = parse_vtt(Path(args.vtt))
    print(f"VTT cue 总数 {len(cues)}，覆盖 "
          f"{cues[0]['start']:.0f}~{cues[-1]['end']:.0f}s")

    win = slice_cues(cues, args.clip_start, args.clip_end)
    if not win:
        print("❌ 时间窗内没有 cue —— 检查 --clip-start/--clip-end")
        return 1
    official = " ".join(c["text"] for c in win)
    print(f"窗口 [{args.clip_start:.0f}, {args.clip_end:.0f}) → {len(win)} cues，"
          f"{len(official)} 字符")

    n_inaud = len(_INAUDIBLE_RE.findall(official))
    print(f"其中 [INAUDIBLE] 标记 {n_inaud} 处")

    out = Path(args.out_dir) if args.out_dir else Path(args.draft).parent
    out.mkdir(parents=True, exist_ok=True)

    # ---- 交叉验证：ASR 草稿 vs 官方稿，逐段对照 ----
    from eval_asr_quality import normalize, edit_distance  # 生产方同一套口径

    draft = json.loads(Path(args.draft).read_text(encoding="utf-8"))
    hyp = " ".join(s.get("text", "") for s in draft["segments"])
    # ASR 草稿的时间轴是「切片内」的相对时间；官方稿是 OCW 绝对时间。
    # 切片起点 = clip_start ⇒ 草稿 t 秒 ⇒ 官方稿 (clip_start + t) 秒。
    ref_n = normalize(official, "en")
    hyp_n = normalize(hyp, "en")
    edits = edit_distance(list(ref_n.split()), list(hyp_n.split()))
    print(f"\n【交叉验证 · 词级】官方稿 {len(ref_n.split())} 词 / "
          f"ASR 草稿 {len(hyp_n.split())} 词 / 编辑距离 {edits} ⇒ "
          f"WER {edits/max(1,len(ref_n.split())):.4f}")
    print("（这是「官方稿 vs ASR 草稿」的差，**不是**最终基线 —— "
          "最终基线要用生产管线的实时跑法。参考量级用。）")

    # 逐段：官方稿窗口 vs ASR 草稿段
    print("\n【逐段对照 · 官方稿 vs ASR 草稿】")
    rows = []
    for seg in draft["segments"]:
        t0 = args.clip_start + seg.get("audioStart", 0.0)
        t1 = args.clip_start + seg.get("audioEnd", 0.0)
        ref_seg = " ".join(c["text"] for c in cues if c["start"] < t1 and c["end"] > t0)
        r = normalize(ref_seg, "en").split()
        h = normalize(seg.get("text", ""), "en").split()
        e = edit_distance(r, h)
        rows.append({
            "index": seg["index"],
            "ocwStart": round(t0, 1), "ocwEnd": round(t1, 1),
            "refWords": len(r), "hypWords": len(h), "edits": e,
            "wer": round(e / len(r), 4) if r else None,
            "ref": ref_seg, "hyp": seg.get("text", ""),
        })
    bad = [r for r in rows if r["wer"] is not None and r["wer"] > 0.25]
    print(f"  共 {len(rows)} 段，其中词级差异 >25% 的有 {len(bad)} 段")
    for r in bad[:12]:
        print(f"  [段{r['index']} {int(r['ocwStart']//60)}:{r['ocwStart']%60:04.1f}] "
              f"WER {r['wer']:.3f} ({r['edits']}/{r['refWords']})")
        print(f"      官方: {r['ref'][:150]}")
        print(f"      ASR : {r['hyp'][:150]}")

    # ---- 产出候选参考稿（人工确认前不进正式位） ----
    # 按 cue **逐行**输出，而不是拼成一整段 —— 人工校对要能一行行对着音频看，
    # 一整段 7700 字符没法校对。
    cand = out / (Path(args.draft).name.replace(".draft.json", ".official.txt"))
    cand.write_text("\n".join(c["text"] for c in win) + "\n", encoding="utf-8")
    (out / (cand.stem + ".segments.json")).write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    # 同时留一份「整段」版，供 CER 计算（换行与空格在 normalize() 后等价）
    whole = out / (Path(args.draft).name.replace(".draft.json", ".official.whole.txt"))
    whole.write_text(official + "\n", encoding="utf-8")
    print(f"\n候选参考稿（逐 cue 行）→ {cand}")
    print(f"候选参考稿（整段）    → {whole}")
    print(f"逐段对照              → {out / (cand.stem + '.segments.json')}")
    print("\n⚠️ 这是**候选**，不是定稿。请人工确认后再落 <stem>.preproof.txt。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
