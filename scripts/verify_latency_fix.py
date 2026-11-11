"""最终验收：修复后的实际延迟表现（生产引擎 + 真实音频）。

验证三件事：
  ① 片段长度严格受 MAX_SPEECH_DURATION 约束（修复前声明 12s 实测 15.07s）；
  ② 端到端最坏延迟 = 片段时长 + 推理 + 前端轮询，落在预期区间；
  ③ 无复读失控、无虚假字幕回显。

用法：
    python scripts/verify_latency_fix.py --lang zh --seconds 120
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

MODEL_DIR = os.path.join(REPO, "packaging", "seed", "whisper_models")

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=["en", "zh"], default="zh")
    ap.add_argument("--seconds", type=int, default=120)
    args = ap.parse_args()

    audio, sr = read_wav(DEFAULT_WAV[args.lang], args.seconds)

    print("=" * 72)
    print("① 片段长度是否严格受上限约束")
    print("=" * 72)
    cap = vadmod.MAX_SPEECH_DURATION
    print(f"当前 MAX_SPEECH_DURATION = {cap}（修复前默认 12.0 且**从未生效**）\n")
    segs = []
    det = vadmod.VoiceActivityDetector(
        on_speech_segment=lambda s, a, b: segs.append((s, a, b)), profile="near")
    block = sr // 4
    for i in range(0, len(audio), block):
        det.process_chunk(np.ascontiguousarray(audio[i:i + block], dtype=np.float32))
    det.flush()

    lens = [len(s) / sr for s, _, _ in segs]
    tol = vadmod.WINDOW_DURATION
    print(f"切出 {len(segs)} 段   均长 {np.mean(lens):.2f}s   最长 {max(lens):.2f}s")
    print(f"上限 {cap}s  ⇒ 实际最长 {max(lens):.2f}s（容差 {tol:.3f}s）")
    ok = max(lens) <= cap + tol
    print(f"{'✅ 通过：片段长度严格受约束' if ok else '❌ 失败：仍有片段超限'}")
    print(f"   （修复前实测：声明 12.0s → 实际最长 15.07s）")

    print()
    print("=" * 72)
    print("② 端到端最坏延迟")
    print("=" * 72)
    from faster_whisper import WhisperModel
    model = WhisperModel("base", device="cpu", compute_type="int8", download_root=MODEL_DIR)

    t0 = time.time()
    for s, _, _ in segs:
        out, _ = model.transcribe(s, language=args.lang, beam_size=5,
                                   vad_filter=False, word_timestamps=True,
                                   condition_on_previous_text=False)
        list(out)
    dt = time.time() - t0
    rtf = dt / (len(audio) / sr)
    worst = max(lens) * (1 + rtf) + 0.5
    print(f"分段识别总耗时 {dt:.2f}s   RTF {rtf:.3f}（<1 表示 CPU 跟得上实时）")
    print(f"最坏延迟 = 最长片段 {max(lens):.2f}s × (1 + RTF {rtf:.3f}) + 轮询 0.5s"
          f" = {worst:.1f}s")
    print(f"   （修复前实测：最长片段 15.07s ⇒ 端到端 ≈ 16s ≈ 三句话）")

    print()
    print("=" * 72)
    print("③ 生产引擎端到端：复读与回显")
    print("=" * 72)
    from asr.whisper_engine import WhisperEngine
    produced = []
    engine = WhisperEngine(on_partial=lambda t, w: produced.append(t),
                            language=args.lang, model_size="base",
                            initial_prompt=None, prompt_sections=None)
    engine._model = engine._construct(
        __import__("faster_whisper").WhisperModel,
        dict(device="cpu", compute_type="int8", num_workers=1,
             cpu_threads=engine.cpu_threads, download_root=MODEL_DIR),
        cached=True,
    )
    offset = 0.0
    for a, st, en in segs:
        engine._transcribe(a, offset, len(a) / sr, 1)
        offset += len(a) / sr
    stats = engine.get_stats()
    echo = engine.echo_stats()
    print(f"产出 {len(produced)} 段")
    print(f"  repetitionEvents   = {stats.get('repetitionEvents', 0)}")
    print(f"  termPromptDegraded = {stats.get('termPromptDegraded', False)}")
    print(f"  回显丢段           = {echo.get('segmentsDropped', 0)}")
    print(f"  回显剔除处数       = {echo.get('substringsRemoved', 0)}")
    longest = max((len(t) for t in produced), default=0)
    print(f"  最长产出段         = {longest} 字")
    if stats.get("repetitionEvents", 0) == 0:
        print("✅ 无复读失控")
    else:
        print(f"⚠️ 复读事件 {stats['repetitionEvents']} 次")


if __name__ == "__main__":
    main()
