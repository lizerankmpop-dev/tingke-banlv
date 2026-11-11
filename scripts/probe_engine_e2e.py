"""诊断 5（端到端）：用**生产引擎**跑真实音频，看复读到底会不会发生。

背景：
    `probe_context_production.py` 用裸 faster-whisper + 生产清洗器，
    发现「带上文后第 20/21 段输出 222 字」且**生产的 EchoCleaner 与
    repetition.detect 都没拦住**。

但那不是生产链路！生产是 `WhisperEngine`，它多做了几件事：
    ① `initial_prompt` = 术语 + 上一段文本 + 简体引导语（按 token 预算拼）
    ② `condition_on_previous_text=True`
    ③ 每段产出后调 `_note_produced_text()` → 命中复读则**降级提示词预算**
    ④ `EchoCleaner` 清洗回显，且回显文本**不进 `_last_text`**（防自我喂食）

本脚本直接用 `WhisperEngine` 跑整段音频，输出：
    - 引擎统计（`repetitionEvents` / `termPromptDegraded` / `merged` / `dropped`）
    - 每段字幕文本与长度
    - 与整段一次识别的 CER 差

这样才能回答「生产到底受不受影响」，而不是靠裸模型推演。
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

from asr.whisper_engine import WhisperEngine  # noqa: E402

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


def norm_chars(text):
    return [c for c in text if not c.isspace() and c not in _PUNCT]


def cer(ref, hyp):
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
    ap.add_argument("--max-speech", type=float, default=None,
                    help="覆盖 MAX_SPEECH_DURATION（用于 A/B：旧值 12 vs 新值 5）")
    ap.add_argument("--serial", action="store_true",
                    help="串行直调 _transcribe（跳过队列与背压），用于快速多档位对比")
    args = ap.parse_args()

    if args.max_speech is not None:
        from audio import vad as vadmod
        vadmod.MAX_SPEECH_DURATION = args.max_speech
        print(f"[A/B] MAX_SPEECH_DURATION = {args.max_speech}\n")

    wav = DEFAULT_WAV[args.lang]
    audio, sr = read_wav(wav, args.seconds)
    dur = len(audio) / sr
    print(f"音频: {os.path.basename(wav)}  {dur:.0f}s\n")

    # ---- 基准：整段一次（生产引擎自己跑一遍）----
    chunks = []

    def on_partial(text, words):
        chunks.append(text)

    engine = WhisperEngine(
        on_partial=on_partial,
        language=args.lang,
        model_size="base",
        initial_prompt=None,
        prompt_sections=None,
    )
    engine._model = engine._construct(
        __import__("faster_whisper").WhisperModel,
        dict(device="cpu", compute_type="int8", num_workers=1,
             cpu_threads=engine.cpu_threads, download_root=MODEL_DIR),
        cached=True,
    )

    t0 = time.time()
    segs, _info = engine._model.transcribe(
        audio, language=args.lang, beam_size=5, word_timestamps=True, vad_filter=False)
    ref = " ".join(s.text.strip() for s in segs)
    print(f"基准(整段一次, 裸模型): {time.time()-t0:.2f}s  {len(norm_chars(ref))} 字\n")

    # ---- 生产引擎逐段跑（走真实的 add_audio → 后台线程 → _transcribe 路径）----
    print("=== 生产引擎 WhisperEngine 逐段识别 ===")
    produced = []
    engine.on_partial = lambda text, words: produced.append(text)

    from audio import vad as vadmod
    segs = []

    def _on_seg(a, st, en):
        segs.append((a, st, en))

    det = vadmod.VoiceActivityDetector(on_speech_segment=_on_seg, profile="near")
    block = sr // 4
    for i in range(0, len(audio), block):
        det.process_chunk(np.ascontiguousarray(audio[i:i + block], dtype=np.float32))
    det.flush()
    print(f"VAD 切出 {len(segs)} 段")

    engine.start = None  # 无 start()：add_audio 会自动拉起 worker 线程
    t0 = time.time()
    if args.serial:
        # 串行直调生产识别主路径 `_transcribe`：
        # 它包含 _build_prompt / _last_text / EchoCleaner / repetition 检测，
        # 只是不走队列调度（队列在实时流里才有意义，且会因背压丢片段）。
        offset = 0.0
        for a, st, en in segs:
            engine._transcribe(a, offset, len(a) / sr, 1)
            offset += len(a) / sr
    else:
        for a, st, en in segs:
            engine.add_audio(a)
            # ⚠️ 必须按**实时节奏**喂入。
            # 一次性把 120 秒音频全灌进去会让引擎背压（MAX_PENDING_JOBS）丢片段，
            # 那是测试脚手架的假象，生产里音频是随时间到达的。
            time.sleep(len(a) / sr)
    engine.flush(timeout=180.0)
    dt = time.time() - t0
    engine.stop()

    stats = engine.get_stats()
    print(f"\n耗时 {dt:.2f}s   产出 {len(produced)} 段")
    print("引擎统计（关键项）:")
    for k in ("repetitionEvents", "termPromptDegraded", "mergedCalls",
              "droppedSegments", "degradeEvents", "repetitionDetected",
              "audioSeconds", "backlog"):
        if k in stats:
            print(f"  {k} = {stats[k]}")

    print("\n每段产出:")
    for i, t in enumerate(produced):
        flag = "🔴 长" if len(norm_chars(t)) > 80 else "  "
        print(f"  {flag} 段{i+1:3} [{len(norm_chars(t)):4d}字] {t[:90]}")

    hyp = " ".join(produced)
    print(f"\n产出总长 {len(norm_chars(hyp))} 字   "
          f"相对基准 CER差 {cer(ref, hyp)*100:.2f}%")


if __name__ == "__main__":
    main()
