"""诊断 4（决定性）：生产链路的 EchoCleaner + 重复检测能否挡住复读循环？

背景：`probe_context_order.py` 发现，带上文的分段识别在第 20/21 段出现
**222 字 / 221 字**的输出（不带上下文只有 17 / 23 字）—— 这是 Whisper 的
**复读循环**（模型把 initial_prompt 当成上文接着念，越滚越长）。

生产链路有两道防线：
    ① `asr/prompt_echo.py` 的 EchoCleaner —— 清洗「把提示词念出来」的回显；
    ② `asr/repetition.py` 的 `detect()` —— 判定文本是否落入重复循环。

但基准脚本 `bench_latency.py --carry-context` **没有**这两道防线，
所以它测出的「带上文后 CER 变差」很可能是**生产里不会发生的**。

本脚本用**生产同款** EchoCleaner + repetition.detect 重跑，
回答：带上下文到底对生产是净收益还是净损失？

用法：
    python scripts/probe_context_production.py --lang zh --seconds 120
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
from asr import repetition  # noqa: E402
from asr.prompt_echo import EchoCleaner  # noqa: E402
from asr.text_norm import normalize_cjk_spacing  # noqa: E402

MODEL_DIR = os.path.join(REPO, "packaging", "seed", "whisper_models")
_PUNCT = "，。！？；：、（）()《》“”‘’\"'!?,.;:~-—…·「」『』【】{}[]"

DEFAULT_WAV = {
    "zh": os.path.join(REPO, "testset", "chinese_lecture", "real",
                       "real_zhlecture_origin_plot.wav"),
    "en": os.path.join(REPO, "testset", "english_lecture", "en_lecture_mit6034_p4.wav"),
}


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
        data /= 2147483648.0
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


def norm_chars(text):
    return [c for c in text if not c.isspace() and c not in _PUNCT]


def cer(ref, hyp):
    """字符级编辑距离率（与 bench_latency.char_cer 同口径）"""
    r, h = norm_chars(ref), norm_chars(hyp)
    if not r:
        return 0.0
    prev = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        cur = [i] + [0] * len(h)
        for j in range(1, len(h) + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1,
                         prev[j - 1] + (0 if r[i - 1] == h[j - 1] else 1))
        prev = cur
    return prev[len(h)] / len(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=["en", "zh"], default="zh")
    ap.add_argument("--seconds", type=int, default=120)
    ap.add_argument("--carry", type=int, default=20)
    args = ap.parse_args()

    from faster_whisper import WhisperModel

    audio, sr = read_wav(DEFAULT_WAV[args.lang], args.seconds)
    segs = segment_audio(audio, sr, 0.8, 5.0)
    model = WhisperModel("base", device="cpu", compute_type="int8", download_root=MODEL_DIR)

    # 生产同款：EchoCleaner 的黑名单来自简体引导语（生产用 get_simp_hint()）
    from asr.whisper_engine import get_simp_hint
    echo_cleaner = EchoCleaner(get_simp_hint())

    def rec(arr, prompt=None):
        out, _ = model.transcribe(
            arr, language=args.lang, beam_size=5, vad_filter=False,
            word_timestamps=True, condition_on_previous_text=False, initial_prompt=prompt)
        return " ".join(s.text.strip() for s in out)

    ref = rec(audio)
    print(f"基准(整段一次): {len(norm_chars(ref))} 字 / 段落 {len(segs)}\n")

    for carry in (0, args.carry):
        label = "不带上下文" if not carry else f"带上下文(carry={carry})"
        prev = ""
        parts_raw, parts_clean = [], []
        repeats = 0
        echos = 0
        t = time.time()
        for s, _, _ in segs:
            raw = rec(s, prompt=prev or None)
            parts_raw.append(raw)

            # ---- 生产同款清洗 ----
            norm = normalize_cjk_spacing(raw.strip())
            echo = echo_cleaner.clean(norm)
            if echo.hits:
                echos += 1
            clean = echo.text
            if clean:
                parts_clean.append(clean)

            # ---- 生产同款重复检测 ----
            verdict = repetition.detect(clean or norm)
            if verdict.triggered:
                repeats += 1
                print(f"  🔴 段{len(parts_raw):3} 复读判定: {verdict.summary()}")
                print(f"     原文({len(norm_chars(norm))}字): {norm[:120]}")

            if carry:
                prev = " ".join(clean.split()[-carry:])

        dt = time.time() - t
        raw_text = " ".join(parts_raw)
        clean_text = " ".join(parts_clean)
        print(f"── {label} ──")
        print(f"  原始    CER差 {cer(ref, raw_text)*100:6.2f}%   输出 {len(norm_chars(raw_text)):4d} 字   耗时 {dt:6.2f}s")
        print(f"  生产清洗后 CER差 {cer(ref, clean_text)*100:6.2f}%   输出 {len(norm_chars(clean_text)):4d} 字")
        print(f"  回显命中 {echos} 段 / 复读判定 {repeats} 段")
        print()


if __name__ == "__main__":
    main()
