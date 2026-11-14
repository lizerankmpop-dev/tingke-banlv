#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线直转（A/B/C 三档归因）—— 真实长音频识别质量评测工具

为什么要有这一档
----------------
生产草稿 `asr_draft_transcript.py` 复用 `ASRPipeline` 的
`VoiceActivityDetector + WhisperEngine`，会注入两类提示词：
  · 术语热词（custom_terms / DOMAIN_TERMS）
  · 简体引导语（SIMP_HINT，「以下是普通话…」）
在**远场低音量 + 长静音**的真实课堂录音上，Whisper 会把提示词「念」出来，
再经 `condition_on_previous_text=True` 传播成复读循环；`EchoCleaner` 只能事后
丢段，救不回内容。

本脚本绕开生产 VAD 分块，直接调 faster-whisper 的整段 `transcribe()`，
用参数组合跑出 A/B/C 三档，判断问题出在「音频 / 模型」还是「提示词配置」：

  档位       模型   增益   提示词                    上下文连贯
  ----------------------------------------------------------------
  baseline   base   无     None                      False
  enhanced   small  AGC    一句短领域引导语           True
  （生产管道 base  + 无 + 长术语表 + 生产 VAD → 见 .draft.json）

**不是产品功能**：F13 表下「先录后转」明确属赛后路线图。这里只是评测工具。

输出 schema 与 `asr_draft_transcript.py` 完全一致（`audioStart/audioEnd/text`），
可直接喂给 `import_lecture_to_db.py`。

用法
----
    <venv>/python.exe scripts/transcribe_baseline.py \
        --audio testset/chinese_lecture/real/real_zhlecture_genai_02.m4a \
        [--suffix baseline|enhanced] [--model base|small|medium] \
        [--model-path <本地模型目录>] [--gain-db -20] \
        [--prompt "以下是一段中文课堂讲解……"] [--condition-on-previous-text]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np  # noqa: E402
from eval_asr_quality import SAMPLE_RATE, load_audio  # noqa: E402


def srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def repetition_ratio(text: str) -> float:
    """极简复读率：相邻 4-gram 重复占比，用来量化污染程度（不是 CER）。"""
    if len(text) < 8:
        return 0.0
    grams = [text[i:i + 4] for i in range(len(text) - 3)]
    if not grams:
        return 0.0
    dup = sum(1 for a, b in zip(grams, grams[1:]) if a == b)
    return round(dup / len(grams), 4)


def enhance_far_field(audio: np.ndarray, target_dbfs: float = -20.0,
                      gate_dbfs: float = -50.0, max_gain_db: float = 20.0):
    """远场低音量录音的帧级增益归一化 + 静音门。

    不做全局增益：峰值已经 -5 dBFS，整体再抬会削波。
    只把「判定为语音」的 20ms 帧拉到目标 RMS，静音帧压低，
    增益包络做 15 帧平滑避免呼吸效应/边界爆音。
    """
    win = int(0.02 * SAMPLE_RATE)          # 320 samples @16k
    n = len(audio) // win
    if n == 0:
        return audio, 0.0, 0.0
    frames = audio[:n * win].reshape(n, win).astype(np.float64)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    db = 20 * np.log10(rms + 1e-12)
    active = db > gate_dbfs
    quiet_ratio = float(1.0 - active.mean())
    if not active.any():
        return audio, 0.0, quiet_ratio
    speech_rms = float(np.sqrt(np.mean(rms[active] ** 2)))
    gain = min((10 ** (target_dbfs / 20)) / (speech_rms + 1e-12),
               10 ** (max_gain_db / 20))
    per_frame = np.where(active, gain, 0.15)
    kernel = np.ones(15) / 15
    smooth = np.convolve(per_frame, kernel, mode="same")
    out = audio.copy().astype(np.float64)
    out[:n * win] *= np.repeat(smooth, win)
    out[n * win:] *= gain
    return np.clip(out, -1.0, 1.0).astype(np.float32), 20 * np.log10(gain), quiet_ratio


def main() -> int:
    ap = argparse.ArgumentParser(description="离线直转 A/B/C 归因")
    ap.add_argument("--audio", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--model", default="base")
    ap.add_argument("--model-path", default="", help="直接指向本地模型目录（绕过 HF 名解析）")
    ap.add_argument("--language", default="zh")
    ap.add_argument("--beam-size", type=int, default=5)
    ap.add_argument("--suffix", default="baseline", help="输出文件后缀")
    ap.add_argument("--gain-db", type=float, default=0.0,
                    help="0=不做增益；非 0 时把语音帧归一化到该目标 dBFS（如 -20）")
    ap.add_argument("--gate-db", type=float, default=-50.0)
    ap.add_argument("--prompt", default="", help="短 initial_prompt，留空=None")
    ap.add_argument("--condition-on-previous-text", action="store_true")
    ap.add_argument("--no-speech-threshold", type=float, default=0.6)
    args = ap.parse_args()

    audio_path = Path(args.audio).expanduser().resolve()
    if not audio_path.is_file():
        raise SystemExit(f"找不到音频：{audio_path}")
    out = Path(args.out).expanduser().resolve() if args.out else audio_path.parent
    out.mkdir(parents=True, exist_ok=True)

    print(f"解码 {audio_path.name} …", flush=True)
    audio = load_audio(audio_path)
    duration = len(audio) / SAMPLE_RATE
    print(f"  {duration:.1f}s / {SAMPLE_RATE}Hz mono float32", flush=True)

    gain_db, quiet = 0.0, None
    if args.gain_db:
        audio, gain_db, quiet = enhance_far_field(audio, target_dbfs=args.gain_db,
                                                  gate_dbfs=args.gate_db)
        print(f"  远场增强：语音帧目标 {args.gain_db} dBFS｜实际增益 +{gain_db:.1f} dB｜"
              f"静音帧占比 {quiet:.3f}", flush=True)

    from faster_whisper import WhisperModel
    model_dir = (os.environ.get("WHISPER_MODEL_DIR")
                 or str(Path(os.environ.get("TINGKEBANLV_HOME")
                             or Path.home() / ".tingkebanlv") / "whisper_models"))
    model_ref = args.model_path or args.model
    t_load = time.perf_counter()
    model = WhisperModel(model_ref, device="cpu", compute_type="int8",
                         download_root=model_dir, local_files_only=True,
                         num_workers=1)
    print(f"模型就绪 {model_ref}/cpu/int8（{time.perf_counter() - t_load:.1f}s）", flush=True)

    t0 = time.perf_counter()
    segments_iter, info = model.transcribe(
        audio,
        language=args.language,
        beam_size=args.beam_size,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        initial_prompt=args.prompt or None,
        condition_on_previous_text=args.condition_on_previous_text,
        no_speech_threshold=args.no_speech_threshold,
        word_timestamps=False,
    )
    segments = []
    for i, seg in enumerate(segments_iter):
        text = (seg.text or "").strip()
        if not text:
            continue
        segments.append({
            "index": len(segments),
            "audioStart": round(float(seg.start), 3),
            "audioEnd": round(float(seg.end), 3),
            "text": text,
            "merged": False,
        })
        if (i + 1) % 50 == 0:
            print(f"  … {seg.end:6.1f}s / {duration:.1f}s  段落 {len(segments)}  "
                  f"用时 {time.perf_counter() - t0:.0f}s", flush=True)
    elapsed = time.perf_counter() - t0

    chars = sum(len(s["text"]) for s in segments)
    rep = repetition_ratio("".join(s["text"] for s in segments))
    payload = {
        "info": {
            "audio": audio_path.name,
            "pipeline": "offline-direct (A/B/C attribution tool)",
            "suffix": args.suffix,
            "model": model_ref,
            "language": args.language,
            "detectedLanguage": getattr(info, "language", None),
            "durationSeconds": round(duration, 3),
            "durationMinutes": round(duration / 60, 2),
            "paragraphs": len(segments),
            "chars": chars,
            "charsPerMinute": round(chars / (duration / 60), 1) if duration else 0,
            "elapsedSeconds": round(elapsed, 1),
            "rtf": round(elapsed / duration, 3) if duration else 0,
            "repetitionRatio": rep,
            "gainDb": round(gain_db, 2),
            "quietFrameRatio": round(quiet, 3) if quiet is not None else None,
            "settings": {
                "vad_filter": True,
                "condition_on_previous_text": args.condition_on_previous_text,
                "initial_prompt": args.prompt or None,
                "no_speech_threshold": args.no_speech_threshold,
                "beam_size": args.beam_size,
            },
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
        "segments": segments,
    }
    stem = audio_path.stem
    (out / f"{stem}.{args.suffix}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / f"{stem}.{args.suffix}.txt").write_text(
        "\n".join(s["text"] for s in segments) + "\n", encoding="utf-8")
    srt = [f"{i}\n{srt_time(s['audioStart'])} --> {srt_time(s['audioEnd'])}\n{s['text']}\n"
           for i, s in enumerate(segments, 1)]
    (out / f"{stem}.{args.suffix}.srt").write_text("\n".join(srt), encoding="utf-8")

    print("-" * 80, flush=True)
    print(f"段落 {len(segments)}｜{chars} 字｜{payload['info']['charsPerMinute']} 字/分｜"
          f"复读率 {rep}｜用时 {elapsed:.1f}s（RTF {payload['info']['rtf']}）", flush=True)
    for k in ("json", "txt", "srt"):
        print(f"  {k:5s} → {out / f'{stem}.{args.suffix}.{k}'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
