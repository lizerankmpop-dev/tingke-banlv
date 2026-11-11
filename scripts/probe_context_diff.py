"""诊断 2：带上文后多出来 / 丢掉的到底是什么？

回显诊断（probe_prompt_echo.py）显示回显占比 0%，所以准确率变差**不是**提示词回显。
本脚本做**字符级对齐 diff**，把「带上文」与「不带上文」两种结果分别与整段一次识别的
基准做对齐，统计插入 / 删除 / 替换的字符数，并打印实际多出来的文本。

目的：分清三种可能
    A. 重复（insertion 集中在与上文重复的内容）
    B. 幻觉（insertion 是基准里完全没有的内容）
    C. 替换（insertion 与 deletion 同时很高 ⇒ 识别错字）
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


def norm(text, lang):
    """去掉空白与标点；英文小写；中文保持原样"""
    t = [c for c in text if not c.isspace() and c not in _PUNCT]
    if lang == "en":
        t = [c.lower() for c in t]
    return t


def align(ref, hyp):
    """返回 (对齐后的 ref 串, 对齐后的 hyp 串)，用 Levenshtein 回溯"""
    n, m = len(ref), len(hyp)
    # dp[i][j] = 编辑距离
    dp = np.zeros((n + 1, m + 1), dtype=np.int32)
    dp[:, 0] = np.arange(n + 1)
    dp[0, :] = np.arange(m + 1)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            dp[i, j] = min(dp[i - 1, j] + 1, dp[i, j - 1] + 1, dp[i - 1, j - 1] + cost)

    a, b = [], []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0:
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            if dp[i, j] == dp[i - 1, j - 1] + cost:
                a.append(ref[i - 1])
                b.append(hyp[j - 1] if cost == 0 else "␥")  # ␥ = 替换
                i, j = i - 1, j - 1
                continue
        if i > 0 and dp[i, j] == dp[i - 1, j] + 1:
            a.append(ref[i - 1])
            b.append("␦")   # ␦ = 基准有、结果缺（漏字）
            i -= 1
            continue
        a.append("␣")       # ␣ = 结果多出（幻觉/重复）
        b.append(hyp[j - 1])
        j -= 1
    return "".join(reversed(a)), "".join(reversed(b))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=["en", "zh"], default="zh")
    ap.add_argument("--seconds", type=int, default=120)
    ap.add_argument("--carry", type=int, default=20)
    args = ap.parse_args()

    wav = DEFAULT_WAV_ZH if args.lang == "zh" else DEFAULT_WAV_EN
    from faster_whisper import WhisperModel

    audio, sr = read_wav(wav, args.seconds)
    segs = segment_audio(audio, sr, 0.8, 5.0)
    model = WhisperModel("base", device="cpu", compute_type="int8", download_root=MODEL_DIR)

    def rec(arr, prompt=None):
        out, _ = model.transcribe(
            arr, language=args.lang, beam_size=5, vad_filter=False,
            word_timestamps=True, condition_on_previous_text=False, initial_prompt=prompt)
        return " ".join(s.text.strip() for s in out)

    ref_raw = rec(audio)
    print(f"基准（整段一次）: {len(norm(ref_raw, args.lang))} 字\n")

    for carry in (0, args.carry):
        prev = ""
        parts = []
        for s, _, _ in segs:
            text = rec(s, prompt=prev or None)
            parts.append(text)
            if carry:
                prev = " ".join(text.split()[-carry:])
        hyp_raw = " ".join(parts)

        r = norm(ref_raw, args.lang)
        h = norm(hyp_raw, args.lang)
        a, b = align(r, h)
        ins = b.count("␣")
        dele = b.count("␦")
        sub = b.count("␥")
        cer = (ins + dele + sub) / max(1, len(r))

        tag = "不带上下文" if not carry else f"带上下文(carry={carry})"
        print(f"── {tag} ──")
        print(f"  结果长度 {len(h)} 字（基准 {len(r)}）  ⇒ 长度差 {len(h) - len(r):+d}")
        print(f"  多出(␣) {ins}   漏字(␦) {dele}   替换(␥) {sub}   CER {cer*100:.2f}%")

        # 找出多出来的连续片段
        extras = []
        cur = []
        for ch in b:
            if ch == "␣":
                cur.append(ch)
            else:
                if len(cur) >= 4:
                    extras.append("".join(cur))
                cur = []
        if len(cur) >= 4:
            extras.append("".join(cur))
        if extras:
            print(f"  多出的片段（前 6 个，共 {len(extras)} 处）:")
            for e in extras[:6]:
                print(f"    「{e}」")
        else:
            print("  无长度 ≥4 的连续多出片段")
        print()


if __name__ == "__main__":
    main()
