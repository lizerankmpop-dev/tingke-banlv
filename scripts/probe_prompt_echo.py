"""诊断：跨片段上下文为什么反而让准确率变差？

现象（2026-11-11 实测）：
    中文 0.8/5.0  不带上下文 → CER差 18.63%，推理总时 29.49s
             带上下文 → CER差 25.62%，推理总时 73.77s（翻 2.5 倍）
    英文 0.8/5.0  不带上下文 → WER差 15.41%，推理总时 30.88s
             带上下文 → WER差 22.26%，推理总时 32.86s

假设：模型把 `initial_prompt`（上一段文本）**当成上文念了出来** ——
即「提示词回显」。生产链路有 `asr/prompt_echo.py` 的 EchoCleaner 专门清洗它，
本脚本没有清洗器，所以把回显算进了结果里。

本脚本验证：统计每段输出中**与上一段文本重复**的部分占多少。
"""

import argparse
import os
import sys
import wave

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(REPO, "backend")
for _p in (BACKEND, REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from audio import vad as vadmod  # noqa: E402

DEFAULT_WAV_ZH = os.path.join(REPO, "testset", "chinese_lecture", "real",
                              "real_zhlecture_origin_plot.wav")
DEFAULT_WAV_EN = os.path.join(REPO, "testset", "english_lecture", "en_lecture_mit6034_p4.wav")
MODEL_DIR = os.path.join(REPO, "packaging", "seed", "whisper_models")


def read_wav(path, seconds):
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        sw = w.getsampwidth()
        raw = w.readframes(int(sr * seconds))
    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}[sw]
    data = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    if sw == 2:
        data /= 32768.0
    elif sw == 4:
        data /= 214748.8 * 100
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    return np.ascontiguousarray(data, dtype=np.float32), sr


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


def overlap_ratio(prev, cur, n=12):
    """当前段输出里，有多大比例是「上一段文本的 n-gram 重复」"""
    if not prev or not cur:
        return 0.0
    prev_ngrams = {prev[i:i + n] for i in range(max(0, len(prev) - n + 1))}
    cur_ngrams = [cur[i:i + n] for i in range(max(0, len(cur) - n + 1))]
    if not cur_ngrams:
        return 0.0
    hit = sum(1 for g in cur_ngrams if g in prev_ngrams)
    return hit / len(cur_ngrams)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=["en", "zh"], default="zh")
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--carry", type=int, default=20)
    ap.add_argument("--segments", type=int, default=8)
    args = ap.parse_args()

    wav = DEFAULT_WAV_ZH if args.lang == "zh" else DEFAULT_WAV_EN
    from faster_whisper import WhisperModel

    audio, sr = read_wav(wav, args.seconds)
    segs = segment_audio(audio, sr, 0.8, 5.0)[: args.segments]
    model = WhisperModel("base", device="cpu", compute_type="int8", download_root=MODEL_DIR)

    def rec(arr, prompt=None):
        out, _ = model.transcribe(
            arr, language=args.lang, beam_size=5, vad_filter=False,
            word_timestamps=True, condition_on_previous_text=False, initial_prompt=prompt)
        return " ".join(s.text.strip() for s in out)

    print(f"=== {args.lang} · 前 {len(segs)} 段 · 带上下文（carry={args.carry} 词）===\n")
    prev = ""
    ratios = []
    for i, (s, _, _) in enumerate(segs):
        text = rec(s, prompt=prev or None)
        r = overlap_ratio(prev, text)
        ratios.append(r)
        flag = "🔴 回显" if r > 0.15 else ("🟡 疑似" if r > 0.05 else "  正常")
        print(f"[段{i + 1}] 回显占比 {r*100:5.1f}% {flag}")
        print(f"  输出: {text[:110]}")
        if i > 0:
            print(f"  提示词(=上段末尾): ...{prev[-60:]}")
        print()
        prev = " ".join(text.split()[-args.carry:])

    print(f"平均回显占比: {np.mean(ratios)*100:.1f}%")
    print(f"回显超 15% 的段数: {sum(1 for r in ratios if r > 0.15)}/{len(ratios)}")


if __name__ == "__main__":
    main()
