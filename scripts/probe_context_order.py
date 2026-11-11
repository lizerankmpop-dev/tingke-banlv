"""诊断 3：同进程 A/B，定位「上下文实验数据自相矛盾」的根因。

现象（2026-11-11）：
    bench_latency.py  --carry-context 20 → 中文 CER差 25.62%（变差），推理总时 73.77s
    probe_context_diff.py（自带对齐）      → 中文 CER 14.45%（变好）

两者逻辑相同，但结果相反。本脚本在**同一个进程、同一个模型实例**里，
按两种顺序各跑一遍「不带上下文 / 带上下文」，并打印每段的输出长度与耗时，
用来区分两种可能：
    A. 执行顺序 / 首次调用预热导致（顺序换了结论就变）
    B. 两个脚本的代码路径真的有差异（逐段输出对比找出来）
"""

import argparse
import os
import sys
import time
import wave

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(REPO, "backend")
for _p in (BACKEND, REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from audio import vad as vadmod  # noqa: E402
from scripts.bench_latency import char_cer, read_wav  # noqa: E402

DEFAULT_WAV_ZH = os.path.join(REPO, "testset", "chinese_lecture", "real",
                              "real_zhlecture_origin_plot.wav")
MODEL_DIR = os.path.join(REPO, "packaging", "seed", "whisper_models")


def segment_audio(audio, sr, silence, max_speech):
    vadmod.SILENCE_DURATION = silence
    vadmod.MAX_SPEECH_DURATION = max_speech
    segs = []
    det = vadmod.VoiceActivityDetector(
        on_speech_segment=lambda s, a, b: segs.append((s, a, b)), profile="near")
    block = sr // 4
    for i in range(0, len(audio), block):
        det.process_chunk(np.ascontiguousarray(audio[i:i + block], dtype=np.float32))
    det.flush()
    return segs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=["en", "zh"], default="zh")
    ap.add_argument("--seconds", type=int, default=120)
    ap.add_argument("--carry", type=int, default=20)
    args = ap.parse_args()

    from faster_whisper import WhisperModel

    audio, sr = read_wav(DEFAULT_WAV_ZH if args.lang == "zh" else
                          os.path.join(REPO, "testset", "english_lecture",
                                       "en_lecture_mit6034_p4.wav"),
                          args.seconds)
    segs = segment_audio(audio, sr, 0.8, 5.0)
    model = WhisperModel("base", device="cpu", compute_type="int8", download_root=MODEL_DIR)

    def rec(arr, prompt=None):
        out, _ = model.transcribe(
            arr, language=args.lang, beam_size=5, vad_filter=False,
            word_timestamps=True, condition_on_previous_text=False, initial_prompt=prompt)
        return " ".join(s.text.strip() for s in out)

    # ---- 基准（整段一次）----
    t0 = time.time()
    ref = rec(audio)
    print(f"基准(整段一次): {time.time()-t0:.2f}s  字符 {len(ref.strip())}\n")

    def run(carry):
        """carry=0 不带上下文；carry>0 带上下文。返回 (文本, 耗时, 每段长度)"""
        prev = ""
        parts = []
        lens = []
        t = time.time()
        for s, _, _ in segs:
            text = rec(s, prompt=prev or None)
            parts.append(text)
            lens.append(len(text))
            if carry:
                prev = " ".join(text.split()[-carry:])
        return " ".join(parts), time.time() - t, lens

    results = {}
    for order in (("with", "without"), ("without", "with")):
        print(f"═══ 本轮顺序: {order[0]} → {order[1]} ═══")
        for which in order:
            carry = args.carry if which == "with" else 0
            text, dt, lens = run(carry)
            err = char_cer(ref, text)
            key = f"{which}@order{order[0]}"
            results[key] = (err, dt, sum(lens))
            print(f"  {which:8} CER差 {err*100:6.2f}%   耗时 {dt:6.2f}s   "
                  f"总输出 {sum(lens):5d} 字   均 {np.mean(lens):5.1f} 字/段")
        print()

    print("═══ 汇总 ═══")
    for k, (err, dt, ln) in results.items():
        print(f"  {k:22} CER差 {err*100:6.2f}%  耗时 {dt:6.2f}s  输出 {ln:5d} 字")

    # 逐段对比：带上下文 vs 不带上下文，看输出长度差异集中在哪
    print("\n═══ 逐段输出长度对比（带上下文 vs 不带）═══")
    _, _, lens_with = run(args.carry)
    _, _, lens_without = run(0)
    for i, (a, b) in enumerate(zip(lens_with, lens_without)):
        mark = "🔴" if a > b * 1.6 and a - b > 20 else ("  " if abs(a - b) < 20 else "🟡")
        print(f"  段{i+1:3}  带上下文 {a:4d} 字   不带 {b:4d} 字   {mark}")


if __name__ == "__main__":
    main()
