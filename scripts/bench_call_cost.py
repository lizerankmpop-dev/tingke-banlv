"""
Whisper 单次调用成本曲线

目的：验证「Faster-Whisper 每次 transcribe 都有 ~30 秒固定成本」这一推断。
方法：用同一条音频切出 2/5/10/20/30/45/60 秒的片段，逐段测量单次调用耗时。

若成本在 <=30s 区间近似持平、超过 30s 后开始随长度线性增长，
即证实了 30 秒窗口 padding 效应。这直接决定 VAD 该把片段切多长。
"""
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_asr import load_wav_mono16k  # noqa: E402


def main():
    audio_path = sys.argv[1]
    model_path = sys.argv[2]
    threads = int(sys.argv[3]) if len(sys.argv) > 3 else 8
    beam = int(sys.argv[4]) if len(sys.argv) > 4 else 5

    from faster_whisper import WhisperModel

    base = load_wav_mono16k(Path(audio_path))
    # 拼成长音频，保证每种长度都有足够素材
    audio = np.tile(base, 6)

    model = WhisperModel(model_path, device="cpu", compute_type="int8",
                         num_workers=1, cpu_threads=threads)

    print("=" * 76)
    print(f"模型: {Path(model_path).name}  threads={threads}  beam={beam}  "
          f"word_timestamps=True")
    print("=" * 76)
    print(f"{'片段s':>6} {'实测耗时s':>10} {'相对2s的增量s':>13} {'每秒音频边际成本s':>17} {'等效RTF':>9}")

    results = []
    for sec in (2, 5, 10, 20, 30, 45, 60):
        n = int(16000 * sec)
        if n > len(audio):
            continue
        chunk = audio[:n]
        # 预热一次，排除首次调用的一次性初始化
        list(model.transcribe(audio[:16000], beam_size=beam, word_timestamps=True)[0])
        t0 = time.perf_counter()
        segs, _ = model.transcribe(chunk, language=None, beam_size=beam,
                                   word_timestamps=True, vad_filter=False)
        list(segs)
        el = time.perf_counter() - t0
        results.append((sec, el))

        if sec == 2:
            print(f"{sec:>6} {el:>10.2f} {'—':>13} {'—':>17} {el/sec:>9.3f}")
        else:
            prev_sec, prev_el = results[-2]
            d_el = el - prev_el
            d_sec = sec - prev_sec
            print(f"{sec:>6} {el:>10.2f} {el-results[0][1]:>13.2f} "
                  f"{d_el/d_sec:>17.3f} {el/sec:>9.3f}")

    print("-" * 76)
    first = results[0][1]
    print(f"\n推断检验：")
    print(f"  2s 片段耗时          = {results[0][1]:.2f}s")
    print(f"  30s 片段耗时         = {[e for s,e in results if s==30][0]:.2f}s" if any(s==30 for s,_ in results) else "")
    print(f"  纯音频部分边际成本   ≈ {(results[-1][1]-results[0][1])/(results[-1][0]-2):.3f} s/s")
    print(f"  固定成本（截距）估算 ≈ {first - (results[-1][1]-first)/(results[-1][0]-2)*2:.2f}s")
    print(f"\n  若固定成本占总耗时绝大部分，即证实每次调用都有巨大的窗口 padding 开销。")
    print(f"  → 设计含义：VAD 应尽量把片段切到接近 30s，用足这一次调用；")
    print(f"     但片段越长字幕出字越晚，需在「延迟」与「吞吐」之间取舍。")


if __name__ == "__main__":
    main()
