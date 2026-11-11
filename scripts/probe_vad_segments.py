"""VAD 分段行为埋点：搞清「片段实际多长」由什么决定。

对真实讲课音频跑项目自带的 VoiceActivityDetector，逐片段打印
起止、时长，以及该片段内的语音/静音构成。
"""
import os
import sys
import time
import wave

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(REPO, "backend"), REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from audio import vad as vadmod  # noqa: E402

WAV = os.path.join(REPO, "testset", "english_lecture", "en_lecture_mit6034_p4.wav")


def read_wav(path, seconds):
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        raw = w.readframes(int(sr * seconds))
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    return np.ascontiguousarray(data, dtype=np.float32), sr


def main():
    silence = float(os.environ.get("BENCH_SILENCE", "0.8"))
    max_speech = float(os.environ.get("BENCH_MAX_SPEECH", "5.0"))
    vadmod.SILENCE_DURATION = silence
    vadmod.MAX_SPEECH_DURATION = max_speech

    audio, sr = read_wav(WAV, 60)
    print(f"参数: silence={silence} max_speech={max_speech}  音频 {len(audio)/sr:.0f}s")
    print(f"窗口 {vadmod.VAD_WINDOW} 样本 = {vadmod.WINDOW_DURATION*1000:.0f}ms\n")

    segs = []
    det = vadmod.VoiceActivityDetector(
        on_speech_segment=lambda s, a, b: segs.append((a, b, len(s) / sr)),
        profile="near",
    )

    # 埋点：记录每次 _emit_segment 触发前的状态，用来区分
    # 「静音够久」「达到最长上限」「flush 强制输出」三条提交路径
    emits = []
    orig_emit = det._emit_segment

    def emit_spy():
        emits.append({
            "buffer_dur": len(det._speech_buffer) * vadmod.WINDOW_DURATION,
            "silence": det._silence_windows * vadmod.WINDOW_DURATION,
            "speech": det._speech_windows * vadmod.WINDOW_DURATION,
        })
        orig_emit()

    det._emit_segment = emit_spy

    # 统计每个窗口的 VAD 判定，验证「语音窗口占比」
    speech_flags = []
    orig = det._detect_speech
    def spy(window):
        r = orig(window)
        speech_flags.append(r)
        return r
    det._detect_speech = spy

    t0 = time.time()
    block = sr // 4
    for i in range(0, len(audio), block):
        det.process_chunk(np.ascontiguousarray(audio[i:i + block], dtype=np.float32))
    n_before_flush = len(emits)
    det.flush()
    dt = time.time() - t0

    total_win = len(speech_flags)
    sp_win = sum(1 for f in speech_flags if f)
    print(f"窗口总数 {total_win}，判为语音 {sp_win} ({sp_win/max(total_win,1)*100:.1f}%)")
    print(f"分段耗时 {dt:.2f}s   上限 max_speech={max_speech}s  静音阈值={silence}s\n")

    print(f"{'#':>3} {'start':>7} {'end':>7} {'时长':>7} | {'触发时buffer':>11} {'静音':>6} {'语音':>6} {'路径':>8}")
    print("-" * 78)
    for i, (a, b, d) in enumerate(segs):
        if i < len(emits):
            e = emits[i]
            if e["silence"] >= silence:
                path = "静音"
            elif e["buffer_dur"] >= max_speech:
                path = "上限"
            else:
                path = "?"
            buf, sil, spc = f"{e['buffer_dur']:.2f}", f"{e['silence']:.2f}", f"{e['speech']:.2f}"
        else:
            path, buf, sil, spc = "flush", "-", "-", "-"
        mark = "  <== 超上限" if d > max_speech + 0.2 else ""
        print(f"{i:>3} {a:>7.2f} {b:>7.2f} {d:>7.2f} | {buf:>11} {sil:>6} {spc:>6} {path:>8}{mark}")

    if segs:
        lens = [d for _, _, d in segs]
        print(f"\n片段数 {len(segs)}  平均 {np.mean(lens):.2f}s  最长 {max(lens):.2f}s  "
              f"(flush 前 {n_before_flush} 段)")


if __name__ == "__main__":
    main()
