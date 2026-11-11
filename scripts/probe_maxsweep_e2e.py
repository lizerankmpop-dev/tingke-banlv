"""诊断 6：生产 E2E 的 CER差 为什么从 16.76%（12s 档）跳到 34.65%（5s 档）？

上一轮 A/B（`probe_engine_e2e.py`，真实生产引擎 + 实时喂入）：

    上限 12s → 12 段切出 / 14 段产出 / 499 字 / CER差 16.76%
    上限  5s → 25 段切出 / 22 段产出 / 450 字 / CER差 34.65%

裸模型基准（`bench_latency.py`，不带上下文）只显示 4.6pp 的差距（14.07% → 18.63%），
生产实测却差 17.9pp。差了一个数量级，必须查清原因。

三个候选解释：
    H1 **基准口径不一致** —— 生产引擎注入简体引导语 + 术语 + 上一段文本，
       而 CER 的基准是「裸模型整段一次」。5s 档切得碎 ⇒ 引导语被注入了 25 次
       ⇒ 简体引导语在更多片段上「续写」⇒ 产出里混入更多引导语文本。
    H2 **内容被切碎丢失** —— 5s 档切点更多，落点处的字被 Whisper 丢掉
       （漏字 25 → 更高）。
    H3 **复读/回显污染** —— 5s 档出现「请使用简体中文转。」这类回显。

本脚本把三者的贡献**分别量化**：逐段输出对齐、统计漏字/替换/插入，
并单独统计「产出里有多少字来自引导语」。
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
from asr.whisper_engine import WhisperEngine, get_simp_hint  # noqa: E402

MODEL_DIR = os.path.join(REPO, "packaging", "seed", "whisper_models")
_PUNCT = "，。！？；：、（）()《》“”‘’\"'!?,.;:~-—…·「」『』【】{}[]"


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


def norm_chars(text):
    return [c for c in text if not c.isspace() and c not in _PUNCT]


def cer_counts(ref, hyp):
    """返回 (插入, 漏字, 替换, 基准长度)"""
    r, h = norm_chars(ref), norm_chars(hyp)
    n, m = len(r), len(h)
    dp = np.zeros((n + 1, m + 1), dtype=np.int32)
    dp[:, 0] = np.arange(n + 1)
    dp[0, :] = np.arange(m + 1)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if r[i - 1] == h[j - 1] else 1
            dp[i, j] = min(dp[i - 1, j] + 1, dp[i, j - 1] + 1,
                           dp[i - 1, j - 1] + cost)
    i, j, ins, dele, sub = n, m, 0, 0, 0
    while i > 0 or j > 0:
        if i > 0 and j > 0:
            cost = 0 if r[i - 1] == h[j - 1] else 1
            if dp[i, j] == dp[i - 1, j - 1] + cost:
                if cost:
                    sub += 1
                i, j = i - 1, j - 1
                continue
        if i > 0 and dp[i, j] == dp[i - 1, j] + 1:
            dele += 1
            i -= 1
            continue
        ins += 1
        j -= 1
    return ins, dele, sub, len(r)


def run(max_speech, audio, sr, lang, quiet=True):
    vadmod.MAX_SPEECH_DURATION = max_speech
    segs = []
    det = vadmod.VoiceActivityDetector(
        on_speech_segment=lambda s, a, b: segs.append((s, a, b)), profile="near")
    block = sr // 4
    for i in range(0, len(audio), block):
        det.process_chunk(np.ascontiguousarray(audio[i:i + block], dtype=np.float32))
    det.flush()

    produced = []
    engine = WhisperEngine(on_partial=lambda t, w: produced.append(t),
                            language=lang, model_size="base",
                            initial_prompt=None, prompt_sections=None)
    engine._model = engine._construct(
        __import__("faster_whisper").WhisperModel,
        dict(device="cpu", compute_type="int8", num_workers=1,
             cpu_threads=engine.cpu_threads, download_root=MODEL_DIR),
        cached=True,
    )
    offset = 0.0
    t0 = time.time()
    for a, st, en in segs:
        engine._transcribe(a, offset, len(a) / sr, 1)
        offset += len(a) / sr
    dt = time.time() - t0
    stats = engine.get_stats()
    return produced, segs, dt, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=["en", "zh"], default="zh")
    ap.add_argument("--seconds", type=int, default=120)
    ap.add_argument("--maxes", default="12,8,6,5,4")
    args = ap.parse_args()

    wav = {
        "zh": os.path.join(REPO, "testset", "chinese_lecture", "real",
                           "real_zhlecture_origin_plot.wav"),
        "en": os.path.join(REPO, "testset", "english_lecture", "en_lecture_mit6034_p4.wav"),
    }[args.lang]
    audio, sr = read_wav(wav, args.seconds)

    from faster_whisper import WhisperModel
    model = WhisperModel("base", device="cpu", compute_type="int8", download_root=MODEL_DIR)
    segs_out, _ = model.transcribe(audio, language=args.lang, beam_size=5,
                                   word_timestamps=True, vad_filter=False)
    ref = " ".join(s.text.strip() for s in segs_out)
    print(f"基准(裸模型整段一次): {len(norm_chars(ref))} 字\n")

    hint_chars = set(norm_chars(get_simp_hint()))
    print(f"引导语字集: {''.join(sorted(hint_chars))}\n")

    hdr = (f"{'上限':>5}{'切出':>5}{'产出':>5}{'总字':>6}{'插入':>6}{'漏字':>6}"
           f"{'替换':>6}{'CER差':>8}{'引导语字':>9}{'repetition':>12}")
    print(hdr)
    print("-" * len(hdr.encode('gbk', errors='ignore')))
    for mx in [float(x) for x in args.maxes.split(",")]:
        produced, segs, dt, stats = run(mx, audio, sr, args.lang)
        hyp = " ".join(produced)
        ins, dele, sub, nref = cer_counts(ref, hyp)
        cer = (ins + dele + sub) / max(1, nref)
        hint_hit = sum(1 for c in norm_chars(hyp) if c in hint_chars)
        print(f"{mx:>5.0f}{len(segs):>5}{len(produced):>5}{len(norm_chars(hyp)):>6}"
              f"{ins:>6}{dele:>6}{sub:>6}{cer*100:>7.2f}%{hint_hit:>9}"
              f"{stats.get('repetitionEvents', 0):>12}")
        # 打印产出里含引导语的段
        for i, t in enumerate(produced):
            if sum(1 for c in norm_chars(t) if c in hint_chars) >= 6:
                print(f"      ⚠️ 段{i+1}: {t[:80]}")


if __name__ == "__main__":
    main()
