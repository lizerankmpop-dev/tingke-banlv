"""
ASR 实时性参数矩阵

在单次运行内加载模型一次并遍历多种配置，避免反复加载干扰测量。
输出一张 (模型 × 线程数 × 片段长度) → RTF 的对照表，用于确定：
  - 默认该用哪个模型规格
  - cpu_threads 该设多少
  - VAD 断句长度该如何取舍（越短字幕越实时，但单次推理固定开销占比越高）

用法：
    python scripts/bench_matrix.py --audio jfk.wav --repeat 6 \
        --models tiny=path,base=path,small=path --threads 4,8,16
"""

import argparse
import statistics
import time
import wave
from pathlib import Path

import numpy as np

from bench_asr import load_wav_mono16k, build_long_audio  # 复用，避免重复实现


def bench_one(model, audio, segment_seconds, beam, word_ts):
    """按固定窗口切段、逐段串行识别，返回 (RTF, 平均单段耗时, 最坏余量)"""
    seg = int(16000 * segment_seconds)
    chunks = [audio[s:s + seg] for s in range(0, max(1, len(audio) - seg + 1), seg)]
    if not chunks:
        chunks = [audio]

    total = sum(len(c) for c in chunks) / 16000.0
    latencies = []
    balance = 0.0
    worst = float("inf")   # 注意：不能用 0.0 初始化，否则余量恒为正时会被错误夹到 0
    for c in chunks:
        dur = len(c) / 16000.0
        t0 = time.perf_counter()
        segs, _ = model.transcribe(
            c, language=None, beam_size=beam, word_timestamps=word_ts,
            vad_filter=False, condition_on_previous_text=True,
        )
        list(segs)
        el = time.perf_counter() - t0
        latencies.append(el)
        balance += dur - el
        worst = min(worst, balance)

    return sum(latencies) / total, statistics.mean(latencies), worst, len(chunks)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--repeat", type=int, default=6)
    ap.add_argument("--gap", type=float, default=0.6)
    ap.add_argument("--models", required=True,
                    help="逗号分隔，格式 name=path 或 name（自动从 --model-root 拼路径）")
    ap.add_argument("--model-root", default=None)
    ap.add_argument("--threads", default="4")
    ap.add_argument("--segments", default="2,4,8,15")
    ap.add_argument("--beam", type=int, default=5)
    ap.add_argument("--compute", default="int8")
    ap.add_argument("--no-word-timestamps", dest="word_ts", action="store_false")
    args = ap.parse_args()

    from faster_whisper import WhisperModel

    base_audio = load_wav_mono16k(Path(args.audio))
    audio = build_long_audio(base_audio, args.repeat, args.gap)
    total_dur = len(audio) / 16000.0

    specs = []
    for item in args.models.split(","):
        item = item.strip()
        if "=" in item:
            name, path = item.split("=", 1)
        else:
            name = item
            path = str(Path(args.model_root) / f"model_{item}")
        specs.append((name, path))

    thread_list = [int(t) for t in args.threads.split(",")]
    seg_list = [float(s) for s in args.segments.split(",")]

    print("=" * 88)
    print(f"音频  : {Path(args.audio).name}  片段 {len(base_audio)/16000:.1f}s x{args.repeat} "
          f"→ 总长 {total_dur:.1f}s")
    print(f"配置  : compute={args.compute}  beam={args.beam}  word_timestamps={args.word_ts}")
    print(f"指标  : RTF = 识别耗时/音频时长；<1 才不累积延迟。"
          f"表中为分段串行 RTF（贴近真实管道）")
    print("=" * 88)
    print(f"{'模型':>7} {'线程':>4} " + "".join(f"{f'{s:g}s':>11}" for s in seg_list))
    print("-" * 88)

    results = []
    for name, path in specs:
        if not Path(path).is_dir():
            print(f"{name:>7} {'---':>4}  模型目录不存在: {path}")
            continue
        for threads in thread_list:
            t0 = time.perf_counter()
            model = WhisperModel(path, device="cpu", compute_type=args.compute,
                                 num_workers=1, cpu_threads=threads)
            load_s = time.perf_counter() - t0

            row = []
            for s in seg_list:
                rtf, avg, worst, n = bench_one(model, audio, s, args.beam, args.word_ts)
                row.append(rtf)
                results.append((name, threads, s, rtf, avg, worst, n, load_s))
            cells = "".join(f"{r:>10.3f}{' ' if r < 1 else '!'}" for r in row)
            print(f"{name:>7} {threads:>4} {cells}")

            del model  # 释放，避免多模型叠加占内存

    print("-" * 88)
    print("带 ! 的表示 RTF>=1（跟不上实时）")

    ok = [r for r in results if r[3] < 1.0]
    if ok:
        best = min(ok, key=lambda r: (r[3]))
        print(f"\n最佳配置: 模型={best[0]} 线程={best[1]} 片段={best[2]:g}s "
              f"→ RTF={best[3]:.3f}（单段平均 {best[4]:.2f}s，最坏余量 {best[5]:+.2f}s）")
    print("\n单段平均耗时明细（决定字幕出字延迟）:")
    print(f"{'模型':>7} {'线程':>4} {'片段s':>6} {'段数':>5} {'平均耗时s':>10} {'RTF':>7} {'最坏余量s':>10} {'加载s':>7}")
    for name, threads, s, rtf, avg, worst, n, load_s in results:
        flag = "  <-- RTF>=1" if rtf >= 1 else ""
        print(f"{name:>7} {threads:>4} {s:>6g} {n:>5} {avg:>10.2f} {rtf:>7.3f} {worst:>+10.2f} {load_s:>7.1f}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
