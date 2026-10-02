"""
ASR 实时性基准测试

回答一个具体问题：在目标机器上，Faster-Whisper 能否跟上真实讲课语速？

核心指标是 RTF（Real-Time Factor）：
    RTF = 识别耗时 / 音频时长
    RTF < 1  → 处理快于实时，不会累积延迟（可用）
    RTF >= 1 → 处理慢于实时，字幕会越来越滞后（不可用）

关键：必须按真实管道的分段方式（VAD 切出的独立片段）逐段串行识别，
而不是把整段音频一次性喂进去。整段识别的 RTF 会因批处理效应偏乐观，
掩盖「片段间无法并行」的真实瓶颈。

用法：
    python scripts/bench_asr.py --audio jfk.wav --model small --repeat 6
    python scripts/bench_asr.py --audio jfk.wav --model base --device cpu --compute int8
"""

import argparse
import statistics
import sys
import time
import wave
from pathlib import Path


def load_wav_mono16k(path: Path) -> "list[float]":
    """读取 16-bit PCM WAV，转成 [-1, 1] 的 float 列表（只依赖标准库）"""
    import numpy as np

    with wave.open(str(path), "rb") as w:
        channels = w.getnchannels()
        width = w.getsampwidth()
        rate = w.getframerate()
        frames = w.readframes(w.getnframes())

    if width != 2:
        raise SystemExit(f"仅支持 16-bit PCM WAV，当前 sampwidth={width}")

    data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)

    if rate != 16000:
        # 线性重采样（基准测试够用，且不引入额外依赖）
        target_len = int(len(data) * 16000 / rate)
        idx = np.linspace(0, len(data) - 1, target_len)
        data = np.interp(idx, np.arange(len(data)), data).astype(np.float32)
        print(f"[warn] 输入为 {rate}Hz，已线性重采样到 16000Hz（可能影响识别质量，但不影响耗时测量）")

    return data


def build_long_audio(base, repeat: int, gap_seconds: float):
    """把短音频重复拼接并插入静音间隙，模拟连续讲课的长录音"""
    import numpy as np

    gap = np.zeros(int(16000 * gap_seconds), dtype=np.float32)
    parts = []
    for _ in range(repeat):
        parts.append(base)
        parts.append(gap)
    return np.concatenate(parts)


def segment_audio(audio, segment_seconds: float, hop_seconds: float):
    """按固定窗口切分，模拟 VAD 输出的独立语音片段"""
    seg = int(16000 * segment_seconds)
    hop = int(16000 * hop_seconds)
    out = []
    for start in range(0, max(1, len(audio) - seg + 1), hop):
        out.append(audio[start:start + seg])
    if not out:
        out.append(audio)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True, help="16kHz 单声道 WAV")
    ap.add_argument("--model", default="small")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--compute", default="int8")
    ap.add_argument("--repeat", type=int, default=1,
                    help="把输入音频重复拼接多少次（用于构造长音频）")
    ap.add_argument("--gap", type=float, default=0.5, help="拼接处的静音间隙秒数")
    ap.add_argument("--segment", type=float, default=8.0, help="模拟 VAD 片段长度（秒）")
    ap.add_argument("--hop", type=float, default=8.0, help="片段步进（秒）")
    ap.add_argument("--download-root", default=None, help="模型缓存目录")
    ap.add_argument("--beam", type=int, default=5)
    ap.add_argument("--word-timestamps", action="store_true", default=True)
    ap.add_argument("--no-word-timestamps", dest="word_timestamps", action="store_false")
    ap.add_argument("--threads", type=int, default=4, help="cpu_threads")
    args = ap.parse_args()

    import numpy as np
    from faster_whisper import WhisperModel

    audio_path = Path(args.audio)
    if not audio_path.is_file():
        raise SystemExit(f"音频文件不存在: {audio_path}")

    base = load_wav_mono16k(audio_path)
    audio = build_long_audio(base, args.repeat, args.gap) if args.repeat > 1 else base
    total_duration = len(audio) / 16000.0

    print("=" * 72)
    print(f"模型        : {args.model} / {args.device} / {args.compute}")
    print(f"音频        : {audio_path.name}  片段 {len(base)/16000:.1f}s"
          f"  x{args.repeat}  →  总长 {total_duration:.1f}s")
    print(f"cpu_threads : {args.threads}   beam_size={args.beam}  "
          f"word_timestamps={args.word_timestamps}")
    print("=" * 72)

    t0 = time.perf_counter()
    model = WhisperModel(
        args.model,
        device=args.device,
        compute_type=args.compute,
        num_workers=1,
        cpu_threads=args.threads,
        download_root=args.download_root,
    )
    load_elapsed = time.perf_counter() - t0
    print(f"\n[1] 模型加载耗时: {load_elapsed:.2f}s")

    # ---------- 整段识别 ----------
    t0 = time.perf_counter()
    segments, info = model.transcribe(
        audio,
        language=None,
        beam_size=args.beam,
        word_timestamps=args.word_timestamps,
        vad_filter=False,
        condition_on_previous_text=True,
    )
    seg_list = list(segments)  # generator，必须消费完才算识别完
    whole_elapsed = time.perf_counter() - t0
    whole_rtf = whole_elapsed / total_duration

    text = " ".join(s.text.strip() for s in seg_list).strip()
    print(f"\n[2] 整段识别: {whole_elapsed:.2f}s / {total_duration:.1f}s  "
          f"RTF={whole_rtf:.3f}  语言={info.language}({info.language_probability:.2f})")
    print(f"    识别文本: {text[:160]}{'...' if len(text) > 160 else ''}")

    # ---------- 分段串行识别（贴近真实管道） ----------
    chunks = segment_audio(audio, args.segment, args.hop)
    print(f"\n[3] 分段串行识别 {len(chunks)} 段（每段 {args.segment:.1f}s，模拟 VAD 输出）")
    print(f"    轮次：每段音频占用 dur 秒实时时间，处理耗时 elapsed 秒。")
    print(f"    余量 = dur - elapsed，正值表示该段处理完还有空闲，负值表示开始欠账。")
    print(f"    {'段':>3}  {'音频s':>6}  {'耗时s':>7}  {'段内RTF':>8}  {'累计余量s':>10}")

    latencies = []
    balance = 0.0          # 正值=仍有空闲余量，负值=已累积滞后
    worst_balance = 0.0    # 记录最坏（最小）值
    for i, chunk in enumerate(chunks, 1):
        dur = len(chunk) / 16000.0
        t0 = time.perf_counter()
        segs, _ = model.transcribe(
            chunk,
            language=None,
            beam_size=args.beam,
            word_timestamps=args.word_timestamps,
            vad_filter=False,
            condition_on_previous_text=True,
        )
        list(segs)
        elapsed = time.perf_counter() - t0
        latencies.append(elapsed)

        # 该段音频本身占用 dur 的实时时间，处理又花了 elapsed
        balance += dur - elapsed
        worst_balance = min(worst_balance, balance)
        if i <= 12 or i == len(chunks):
            print(f"    {i:>3}  {dur:>6.1f}  {elapsed:>7.2f}  {elapsed/dur:>8.3f}  {balance:>10.2f}")
        if i == 12 and len(chunks) > 13:
            print(f"    ...  中间省略 {len(chunks) - 13} 段")

    seg_total = sum(latencies)
    seg_rtf = seg_total / total_duration
    print("\n" + "=" * 72)
    print(f"分段平均单段耗时 : {statistics.mean(latencies):.2f}s "
          f"(中位 {statistics.median(latencies):.2f}s, 最大 {max(latencies):.2f}s)")
    print(f"分段总耗时/总音频: {seg_total:.2f}s / {total_duration:.1f}s  →  RTF={seg_rtf:.3f}")
    print(f"最坏累计余量     : {worst_balance:+.2f}s "
          f"({'始终有余量，不累积延迟' if worst_balance >= 0 else '出现过累积滞后'})")
    verdict = "✅ 跟得上实时" if seg_rtf < 1.0 else "❌ 跟不上，字幕会持续滞后"
    print(f"结论             : {verdict}（分段 RTF={seg_rtf:.3f}）")
    print("=" * 72)

    if seg_rtf >= 1.0:
        print("\n建议：")
        print("  - 降低模型规格（small → base → tiny）")
        print(f"  - 当前 CPU 逻辑核数 {__import__('os').cpu_count()}，"
              f"cpu_threads={args.threads} 可尝试调大")
        print("  - 关闭 word_timestamps 可显著降低耗时（但会丢失逐词高亮能力）")
        print("  - 降低 beam_size（5 → 1）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
