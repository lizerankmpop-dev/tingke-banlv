#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
按段落静音边界切出中文 TTS 短样本（音频 + 逐字稿**成对**产出）

为什么需要它
------------
P2 要在中文上做「场景 × 条件」矩阵（clean / noise / lowvol / fast / multi），
而中文只有一条 626s 的长素材。四个场景各跑一次 10 分钟（`--realtime` 下约 40min/轮）
太慢，必须先用 60~90s 的短样本把矩阵跑起来。

为什么不能「按秒粗暴截断」
--------------------------
`long_zhlecture_cnn.txt` 实测**只有 1 个非空行 / 3153 字** —— 整篇讲稿没有任何
段落分隔符。按秒截音频之后，**没有任何办法知道这一段对应的文本是哪一截**，
参考文本一错，测出来的 CER 就是垃圾（而且看起来像「识别很差」这种合理结论）。

所以改成**两边同源构造**：
  · 文本侧：`scripts/make_chinese_testset.py` 里的内置 `LECTURE`（30 段）就是
    这份 TTS 的**生成输入**，段落文本天然已知；
  · 音频侧：TTS 是按段合成、段间插 `PARAGRAPH_GAP_S` 静音的，所以**段落边界
    在音频里表现为长静音段**，可以从静音精确恢复。
    实测：整篇 626.41s 里 **静音 ≥1.1s 的段数 = 29 = len(LECTURE) − 1**（一一对应）。
  · 于是「取前 k 段」= 文本取 `LECTURE[:k]`、音频切到第 k 个段落末尾的两侧拼接点，
    **配对精确性由构造保证**，不靠任何对齐算法。

红线（本脚本必须能自证，否则不许用它产出的素材）
------------------------------------------------
1. 切之前先验**整篇**的假设成立：静音段数 == len(LECTURE) − 1；
2. 切之后再验**短片**：内部静音段数 == k − 1（少一段 = 切进了段落中间）；
3. 文本必须能对上源：`参考文本 == ''.join(LECTURE[:k]).replace(' ', '')`。

判据怎么来的：`long_zhlecture_cnn.txt` 与 `''.join(LECTURE).replace(' ', '')`
**逐字符相等**（LECTURE 里有 30 个空格，正好是两者长度差 30）。
顺带：`eval_asr_quality._PUNCT` 里含空格、中文归一化会把空格删掉
⇒ 参考文本带不带空格**对 CER 无影响**，这里沿用官方 txt 的「去空格」写法保持一致。

用法
----
    <venv>/python scripts/clip_tts_paragraphs.py \
        --audio testset/chinese_lecture/long/long_zhlecture_cnn.wav \
        --paragraphs 3 --out-dir testset/chinese_lecture/short

产物
----
    <out-dir>/<stem>.wav            切出的短音频（16kHz 单声道，与源同格式）
    <out-dir>/<stem>.txt            与短音频逐字对应的参考文本
    <out-dir>/metadata.json         段落数/边界时刻/时长/指纹/统计（供事后核对）

依赖：numpy + 标准库 wave（与 make_asr_testset.py 同一条「零新增依赖」路线）
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AUDIO = ROOT / "testset" / "chinese_lecture" / "long" / "long_zhlecture_cnn.wav"
MAKER = ROOT / "scripts" / "make_chinese_testset.py"

# 判定静音用的两个参数，都写在明面上便于复现：
RMS_WINDOW_S = 0.01     # 10ms 一帧
SILENCE_REL_THRESHOLD = 0.02    # 低于「峰值 RMS 的 2%」算静音
GAP_MIN_S = 1.1         # 判定为「段落边界」的最小静音长度（实测 1.1s/1.2s → 29；0.9/1.0 → 30）
TAIL_KEEP_S = 0.20      # 切口后仍保留的一点静音：避免削掉字尾，
                        # 又**短于 GAP_MIN_S**，不会被自己的静音检测误数成一个段落边界
EDGE_GUARD_S = 0.30     # 贴头/贴尾的静音段不计入「内部边界」（切片样本常有尾静音）


def load_lecture() -> list[str]:
    """从 make_chinese_testset.py 里取内置讲稿（= 这份 TTS 的生成输入）。"""
    spec = importlib.util.spec_from_file_location("make_chinese_testset", MAKER)
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法载入 {MAKER}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # 该模块的 edge_tts 是函数内延迟导入，这里不需要它
    lecture = list(getattr(mod, "LECTURE"))
    if not lecture:
        raise SystemExit("make_chinese_testset.LECTURE 为空")
    return lecture


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        channels, width, sr, frames = (
            w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes(),
        )
        raw = w.readframes(frames)
    if channels != 1 or width != 2:
        raise SystemExit(f"只支持 16-bit 单声道 wav，实际 channels={channels} width={width}")
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return audio, sr


def write_wav(path: Path, audio: np.ndarray, sr: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(audio, -1.0, 1.0)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((clipped * 32767.0).astype(np.int16).tobytes())


def silence_runs(audio: np.ndarray, sr: int) -> list[tuple[float, float]]:
    """返回 [(起点秒, 时长秒)]，按时间升序。"""
    hop = max(1, int(sr * RMS_WINDOW_S))
    n_frames = len(audio) // hop
    if n_frames == 0:
        return []
    frames = audio[: n_frames * hop].reshape(n_frames, hop)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    quiet = rms < rms.max() * SILENCE_REL_THRESHOLD

    runs: list[tuple[float, float]] = []
    i = 0
    while i < len(quiet):
        if not quiet[i]:
            i += 1
            continue
        j = i
        while j < len(quiet) and quiet[j]:
            j += 1
        runs.append((i * RMS_WINDOW_S, (j - i) * RMS_WINDOW_S))
        i = j
    return runs


def internal_gaps(audio: np.ndarray, sr: int, gap_min_s: float) -> list[tuple[float, float]]:
    """
    只保留「内部」的段落边界：长度达标，且**不贴头、不贴尾**。

    为什么要排除贴边的：切片样本的尾部本来就带一点静音（TAIL_KEEP_S），
    若把它也数进来，断言会恒多 1，工具就会开始骗自己。
    """
    total = len(audio) / sr
    return [
        (start, dur)
        for start, dur in silence_runs(audio, sr)
        if dur >= gap_min_s
        and start > EDGE_GUARD_S
        and (start + dur) < total - EDGE_GUARD_S
    ]


def stats(audio: np.ndarray, sr: int) -> dict:
    rms = float(np.sqrt((audio ** 2).mean())) if len(audio) else 0.0
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0

    def dbfs(x: float) -> float | None:
        return round(20 * float(np.log10(x)), 2) if x > 0 else None

    return {
        "rmsDbfs": dbfs(rms),
        "peakDbfs": dbfs(peak),
        "durationSeconds": round(len(audio) / sr, 2),
    }


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description="按段落静音边界切出中文 TTS 短样本")
    ap.add_argument("--audio", default=str(DEFAULT_AUDIO), help="长素材 wav")
    ap.add_argument("--paragraphs", type=int, default=3, help="取前几段（默认 3）")
    ap.add_argument("--out-dir", default=str(ROOT / "testset" / "chinese_lecture" / "short"))
    ap.add_argument("--gap-min", type=float, default=GAP_MIN_S, help="段落边界的最小静音长度（秒）")
    ap.add_argument("--tail-keep", type=float, default=TAIL_KEEP_S, help="切口后保留的静音（秒）")
    ap.add_argument("--stem", default="", help="产物名（默认由源名派生 short_<源名>）")
    args = ap.parse_args()

    audio_path = Path(args.audio)
    if not audio_path.is_file():
        raise SystemExit(f"找不到音频：{audio_path}")
    if not 1 <= args.paragraphs < 30:
        raise SystemExit("--paragraphs 需在 1..29（第 30 段后面没有段落边界可切）")
    if args.tail_keep >= args.gap_min:
        raise SystemExit("--tail-keep 必须小于 --gap-min，否则尾静音会被自己的检测数成段落边界")

    lecture = load_lecture()
    audio, sr = read_wav(audio_path)
    total_s = len(audio) / sr
    print(f"源音频 : {audio_path.name}  {total_s:.2f}s  {sr}Hz")
    print(f"讲稿   : LECTURE {len(lecture)} 段 / {len(''.join(lecture))} 字（含空格）")

    # ---- 自证①：整篇的「静音段 == 段落边界」假设必须成立 ----
    whole_gaps = [
        (s, d) for s, d in silence_runs(audio, sr) if d >= args.gap_min
    ]
    expect = len(lecture) - 1
    print(f"整篇静音 ≥{args.gap_min}s 的段数 = {len(whole_gaps)}（期望 {expect} = 段落数 − 1）")
    if len(whole_gaps) != expect:
        print("❌ 整篇的「静音边界 ↔ 段落」对应关系不成立，不能据此切分。")
        print("   先把 --gap-min 调到能对上，或改用别的素材；不要手动挑一个能对上的数字。")
        return 1
    print("✓ 自证①：整篇静音段数与段落数一一对应")

    # ---- 切点 = 第 k 个段落边界之后一点点（保留一点静音，保护字尾）----
    boundary_start, boundary_dur = whole_gaps[args.paragraphs - 1]
    cut_s = boundary_start + min(args.tail_keep, boundary_dur / 2)
    cut = int(round(cut_s * sr))
    clip = audio[:cut]

    stem = args.stem or f"short_{audio_path.stem}"
    out_dir = Path(args.out_dir)
    out_wav = out_dir / f"{stem}.wav"
    out_txt = out_dir / f"{stem}.txt"
    write_wav(out_wav, clip, sr)

    # 文本沿用官方 txt 的口径：去空格（对 CER 无影响，只是为了跟 long/ 那份保持一致）
    text = "".join(lecture[: args.paragraphs]).replace(" ", "")
    out_txt.parent.mkdir(parents=True, exist_ok=True)
    # 🔴 必须显式 newline="\n"：Windows 上 Path.write_text 默认会把 "\n" 写成 CRLF，
    # 而仓库主流约定是 LF（real/README.md、real/metadata.json 都是 LF）。
    # 实测过：不加这个参数，metadata.json 会变成 29 个 CRLF。
    out_txt.write_text(text + "\n", encoding="utf-8", newline="\n")

    # ---- 自证②：切出来的片子内部边界数必须正好是 k − 1 ----
    got = internal_gaps(clip, sr, args.gap_min)
    print(f"切片时长 = {len(clip) / sr:.2f}s（切点 {cut_s:.2f}s）")
    print(f"切片内部静音 ≥{args.gap_min}s 的段数 = {len(got)}（期望 {args.paragraphs - 1}）")
    if len(got) != args.paragraphs - 1:
        print("❌ 切片内部边界数与段落数对不上 —— 切口很可能落进了段落中间。")
        print(f"   实测边界时刻：{[(round(s, 2), round(d, 2)) for s, d in got]}")
        return 1
    print("✓ 自证②：切片内部的段落边界数与「取前 k 段」一致")

    # ---- 自证③：文本能对上源讲稿 ----
    if text != "".join(lecture[: args.paragraphs]).replace(" ", ""):
        print("❌ 参考文本与源讲稿不一致")
        return 1
    print(f"✓ 自证③：参考文本 = ''.join(LECTURE[:{args.paragraphs}]) 去空格，{len(text)} 字")

    meta = {
        "kind": "CLIP-FROM-TTS",
        "warning": (
            "从 long_zhlecture_cnn.wav 按段落静音边界切出的短样本。音频与文本同源构造"
            "（文本 = make_chinese_testset.LECTURE[:k]，音频切到第 k 个段落边界），"
            "因此配对精确；但**它仍然是 TTS 代理素材**：无口音、无口误、无混响、无背景噪声，"
            "CER 绝对值偏乐观，不得作为真实中文课堂的结论。"
        ),
        "source": {
            "audio": audio_path.name,
            "audioSeconds": round(total_s, 2),
            "audioSha256": sha256_file(audio_path),
            "lectureParagraphs": len(lecture),
            "textSource": "scripts/make_chinese_testset.py::LECTURE[:k]",
        },
        "clip": {
            "paragraphs": args.paragraphs,
            "cutSeconds": round(cut_s, 2),
            "boundarySeconds": round(boundary_start, 2),
            "boundarySilenceSeconds": round(boundary_dur, 2),
            "gapMinSeconds": args.gap_min,
            "tailKeepSeconds": args.tail_keep,
            "internalGaps": len(got),
            "audio": out_wav.name,
            "reference": out_txt.name,
            "referenceChars": len(text),
            **stats(clip, sr),
            "audioSha256": sha256_file(out_wav),
            "referenceSha256": hashlib.sha256(out_txt.read_bytes()).hexdigest(),
        },
        "generatedBy": "scripts/clip_tts_paragraphs.py",
    }
    meta_path = out_dir / "metadata.json"
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",          # 同上：不写死 LF 就会在 Windows 上变成 CRLF
    )

    print()
    print(f"写出 {out_wav}  ({out_wav.stat().st_size:,} B)")
    print(f"写出 {out_txt}  ({len(text)} 字)")
    print(f"写出 {meta_path}")
    print(f"切片 sha256 = {meta['clip']['audioSha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
