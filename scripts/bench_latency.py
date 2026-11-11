"""延迟-准确率权衡基准（Latency vs Accuracy Trade-off）

问题：课堂场景下字幕出字延迟高达 ~13 秒（≈三句话）。
归因：延迟 = VAD 攒片段（静音 0.8s / 最长 12s 才提交）+ 推理 + 推送轮询。

本脚本对同一段真实讲课音频，扫描不同的 (静音断句阈值, 最长片段) 组合，量化：
  1. 分段统计：片段数 / 平均长 / 最长  → 决定最坏延迟
  2. 分段识别总耗时 vs 整段一次识别  → 决定 CPU 是否跟得上
  3. 拼接文本与整段识别的词级 WER 差 → 量化「切碎」付出的准确率代价

用法（必须在项目源代码目录下执行）：
    "安装空间/venv/Scripts/python.exe" scripts/bench_latency.py
    "安装空间/venv/Scripts/python.exe" scripts/bench_latency.py --seconds 120
"""

import argparse
import os
import sys
import time
import wave

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(REPO, "backend")
# `audio` / `asr` / `dictionary` 都是 backend 下的包，与服务运行时一致
for _p in (BACKEND, REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from audio import vad as vadmod  # noqa: E402

DEFAULT_WAV = os.path.join(REPO, "testset", "english_lecture", "en_lecture_mit6034_p4.wav")
DEFAULT_WAV_ZH = os.path.join(REPO, "testset", "chinese_lecture", "real",
                              "real_zhlecture_origin_plot.wav")
MODEL_DIR = os.path.join(REPO, "packaging", "seed", "whisper_models")

# (静音断句秒, 最长片段秒)
COMBOS = [
    (0.8, 12.0),   # 上限很宽 ≈ 主要靠静音断句（等价于修复前的实际效果）
    (0.8, 5.0),    # 推荐
    (0.6, 4.0),    # 更激进：静音阈值也调低
]


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


def word_wer(ref, hyp):
    """词级编辑距离 → WER（引用为空时返回 0.0）"""
    r = ref.split()
    h = hyp.split()
    if not r:
        return 0.0
    prev = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        cur = [i] + [0] * len(h)
        for j in range(1, len(h) + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1,
                         prev[j - 1] + (0 if r[i - 1].lower() == h[j - 1].lower() else 1))
        prev = cur
    return prev[len(h)] / len(r)


_PUNCT = "，。！？；：、（）()《》“”‘’\"'!?,.;:~-—…·「」『』【】{}[]"


def char_cer(ref, hyp):
    """字符级编辑距离 → CER（中文用；先去掉空白与标点，与 Whisper 论文口径一致）"""
    r = [c for c in ref if not c.isspace() and c not in _PUNCT]
    h = [c for c in hyp if not c.isspace() and c not in _PUNCT]
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


def segment_audio(audio, sr, silence, max_speech):
    """用项目自带的 VAD 按给定参数切段（不识别，只统计/取音频）"""
    vadmod.SILENCE_DURATION = silence
    vadmod.MAX_SPEECH_DURATION = max_speech
    segs = []
    det = vadmod.VoiceActivityDetector(
        on_speech_segment=lambda s, a, b: segs.append((s, a, b)),
        profile="near",
    )
    block = sr // 4  # 250ms
    for i in range(0, len(audio), block):
        det.process_chunk(np.ascontiguousarray(audio[i:i + block], dtype=np.float32))
    det.flush()
    return segs


def parse_combos(spec):
    """解析 --combos 参数，格式 "0.8:12,0.8:5,0.6:4"（静音阈值:最长片段）"""
    out = []
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        sil, mx = item.split(":")
        out.append((float(sil), float(mx)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", default=None)
    ap.add_argument("--seconds", type=int, default=120)
    ap.add_argument("--beam", type=int, default=5)
    ap.add_argument("--model", default="base")
    ap.add_argument("--lang", choices=["en", "zh"], default="en")
    ap.add_argument("--combos", default=None,
                    help='自定义扫描组合，格式 "0.8:12,0.8:5"（静音阈值:最长片段秒）')
    ap.add_argument("--carry-context", type=int, default=0,
                    help="把上一段识别结果的最后 N 个词作为下一段的 initial_prompt "
                         "（0=不携带上下文，用于评估「跨片段上下文」能否回收切碎误差）")
    args = ap.parse_args()

    combos = parse_combos(args.combos) if args.combos else COMBOS

    wav = args.wav or (DEFAULT_WAV if args.lang == "en" else DEFAULT_WAV_ZH)
    metric = word_wer if args.lang == "en" else char_cer
    metric_name = "WER差" if args.lang == "en" else "CER差"

    from faster_whisper import WhisperModel

    audio, sr = read_wav(wav, args.seconds)
    dur = len(audio) / sr
    print(f"音频: {os.path.basename(wav)}  {dur:.0f}s @ {sr}Hz")
    print(f"模型: {args.model} / CPU int8 / beam{args.beam} / lang={args.lang}\n")

    model = WhisperModel(args.model, device="cpu", compute_type="int8", download_root=MODEL_DIR)

    def recognize(arr, prompt=None):
        segs, _info = model.transcribe(
            arr, language=args.lang, beam_size=args.beam, vad_filter=False,
            word_timestamps=True, condition_on_previous_text=False,
            initial_prompt=prompt,
        )
        return " ".join(s.text.strip() for s in segs)

    # 基准：整段一次识别（同一段音频的最优切分，作为「切分代价」的参照）
    t0 = time.time()
    ref = recognize(audio)
    ref_dt = time.time() - t0
    print(f"基准(整段一次): {ref_dt:.2f}s  RTF {ref_dt/dur:.3f}  "
          f"词数 {len(ref.split())}  字符 {len(ref.strip())}\n")

    hdr = f"{'静音/最长':<12}{'段数':>5}{'均长':>7}{'最长':>7}{'推理总时':>9}{'RTF':>7}{metric_name:>8}{'最坏延迟':>9}"
    print(hdr)
    print("-" * 66)

    for silence, max_speech in combos:
        segs = segment_audio(audio, sr, silence, max_speech)
        if not segs:
            print(f"{silence}/{max_speech:<8} 无语音段")
            continue
        lens = [len(s) / sr for s, _, _ in segs]
        t0 = time.time()
        if args.carry_context:
            # 把上一段识别结果的最后 N 个词作为下一段的 initial_prompt，
            # 用来检验「跨片段上下文」能否回收切碎带来的误差。
            carry = args.carry_context
            parts = []
            prev_text = ""
            for s, _, _ in segs:
                text = recognize(s, prompt=prev_text or None)
                parts.append(text)
                prev_text = " ".join(text.split()[-carry:])
            text = " ".join(parts)
        else:
            text = " ".join(recognize(s) for s, _, _ in segs)
        dt = time.time() - t0
        err = metric(ref, text)
        # 最坏延迟 = 攒片段时长 + 该片段推理耗时 + 前端轮询 0.5s
        worst = max(lens) * (1 + dt / dur) + 0.5
        print(f"{silence}/{max_speech:<8}{len(segs):>5}{np.mean(lens):>7.2f}{max(lens):>7.2f}"
              f"{dt:>9.2f}{dt/dur:>7.3f}{err*100:>7.2f}%{worst:>9.1f}s")

    print(f"\n说明：「{metric_name}」= 分段拼接结果相对整段一次识别的增量误差（越低越好）；")
    print("      「最坏延迟」= 最长片段 + 该片段推理耗时 + 前端 500ms 轮询。")
    print("      「RTF」< 1 表示 CPU 跟得上实时；越高说明切碎后固定开销占比越大。")
    if args.carry_context:
        print(f"      本轮携带了跨片段上下文（initial_prompt 带上一段最后 {args.carry_context} 词）。")


if __name__ == "__main__":
    main()
