"""诊断 7（归因）：5.0s 档的「断崖」到底由什么造成？

现象（中文真实课堂音频，生产引擎端到端）：
    上限 8s → CER差 22.18%  漏字  90  插入  65  替换 209  repetition 2
    上限 6s → CER差 20.17%  漏字  67  插入  82  替换 182  repetition 0
    上限 5s → CER差 39.06%  漏字 218  插入 230  替换 193  repetition 8  ⚠️ 断崖

5.0s 处 CER差 翻倍，且「插入」与「漏字」同时暴涨（65→230 / 90→218），
同时 repetition 事件 0→8、出现「请使用简体中文转。」这类引导语回显。

候选原因：
    H1 **复读 → 提示词降级 → 毒化后续所有片段**
       生产链路：复读命中 → `_degrade_prompt()` 把提示词预算从 220 砍到 70 token
       ⇒ 之后每段都带着残缺的提示词（含简体引导语）⇒ 引导语回显 ⇒ 更多复读。
       片段越短 ⇒ 段数越多 ⇒ 触发复读的概率越高 ⇒ 5s 档触发得早、毒化范围大。
       验证：关闭「自动降级」后，5s 档的 CER差 应显著回落。
    H2 **跨片段上下文把错误传播下去**
       `_last_text` 会成为下一段的 initial_prompt ⇒ 一旦某段出错，后面跟着错。
       验证：关闭上下文携带后，5s 档的 CER差 应回落。
    H3 **纯粹的切碎代价**（无级联效应）
       那 H1/H2 都关掉后，CER差 仍应与 6s 档接近（差距只来自切碎本身）。

本脚本对 6s 与 5s 各跑三种配置：
    基线 / 关自动降级 / 关上下文携带
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
from asr.whisper_engine import WhisperEngine  # noqa: E402

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


def run(max_speech, audio, sr, lang, degrade=True, carry_context=True):
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
    engine.set_auto_degrade(degrade)
    if not carry_context:
        # 关掉跨片段上下文：把 `_last_text` 的更新掐掉，
        # 于是 `_build_prompt()` 拿不到上一段文本，initial_prompt 只剩术语+引导语。
        engine._last_text = None
        orig_clean = engine._echo.clean

        def _clean_and_block(text):
            # 同时让 echo 清洗后的文本不进入 _last_text
            return orig_clean(text)
        engine._echo.clean = _clean_and_block

    offset = 0.0
    for a, st, en in segs:
        engine._transcribe(a, offset, len(a) / sr, 1)
        offset += len(a) / sr
        if not carry_context:
            engine._last_text = None
    stats = engine.get_stats()
    echo = engine.echo_stats()
    return produced, segs, stats, echo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=["en", "zh"], default="zh")
    ap.add_argument("--seconds", type=int, default=300)
    ap.add_argument("--maxes", default="6,5")
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

    hdr = (f"{'上限':>4}{'配置':>16}{'切出':>5}{'产出':>5}{'总字':>6}{'插入':>6}"
           f"{'漏字':>6}{'替换':>6}{'CER差':>9}{'repet':>7}{'降级':>6}{'回显丢段':>9}")
    print(hdr)
    print("-" * 92)
    for mx in [float(x) for x in args.maxes.split(",")]:
        for label, kw in (
            ("基线", dict(degrade=True, carry_context=True)),
            ("关自动降级", dict(degrade=False, carry_context=True)),
            ("关上下文携带", dict(degrade=True, carry_context=False)),
        ):
            produced, segs, stats, echo = run(mx, audio, sr, args.lang, **kw)
            hyp = " ".join(produced)
            ins, dele, sub, nref = cer_counts(ref, hyp)
            cer = (ins + dele + sub) / max(1, nref)
            print(f"{mx:>4.0f}{label:>16}{len(segs):>5}{len(produced):>5}"
                  f"{len(norm_chars(hyp)):>6}{ins:>6}{dele:>6}{sub:>6}{cer*100:>8.2f}%"
                  f"{stats.get('repetitionEvents', 0):>7}"
                  f"{str(stats.get('termPromptDegraded', False)):>6}"
                  f"{echo.get('segmentsDropped', 0):>9}")
        print()


if __name__ == "__main__":
    main()
