#!/usr/bin/env python
"""
合成多场景 ASR 测试集（零新增依赖：numpy + 标准库 wave）

为什么需要它
------------
`scripts/eval_asr_quality.py` 要回答「噪声 / 低音量 / 快速连读 / 叠话 / 长时连续」
下管道会怎样，而这些素材**目前一个都不存在**（仓库里只有 11 秒的 jfk.wav）。
真录音只能由人提供，所以这里先用信号处理把已有素材改造成压力用例，
把「管道鲁棒性与长时稳定性」这条线先跑通。

它解决了什么、没解决什么（重要）
--------------------------------
- **能**回答：低信噪比下 VAD 还切不切得准？远场小音量会不会整段丢？
  快语速会不会让片段变多、背压触发合并降级？连续 12 分钟会不会越拖越晚、丢片段？
- **不能**回答：真实口音、真实多人对话的识别准确率。合成的「噪声叠加」「自身叠话」
  与真人场景差距很大，**绝不能把合成的 WER 当成真实场景 WER**。

因此：
1. 产出文件统一命名为 `synth_<场景>_<源名>.wav`（下划线分隔），
   `eval_asr_quality.py:scenario_of()` 按 `_场景_` 匹配，因此能正确归到对应场景，
   同时在文件名上一眼看出这是合成素材而非真录音；
2. 报告里合成用例与真录音分开描述，不混在一起下结论；
3. `accent_` 场景**刻意不合成**。用变调去近似口音没有任何依据，
   与其产出一份看起来像数据、实际毫无意义的 WER，不如留空等真录音。

用法
----
    # 用默认源（backend/tests/fixtures/jfk.wav）生成全套到 tests/audio/
    <venv>/python scripts/make_asr_testset.py

    # 指定源与输出目录，长文件做成 12 分钟
    <venv>/python scripts/make_asr_testset.py --source <音频> --out tests/audio --long-minutes 12

    # 只生成某几个场景
    <venv>/python scripts/make_asr_testset.py --only noise lowvol fast
"""

from __future__ import annotations

import argparse
import random
import sys
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000

# 各场景的默认强度。改这些数字就是改「考得多难」，都写在明面上便于复现。
SNR_DB = 8.0          # 教室背景噪声：语音比噪声高 8 dB（真人听也吃力）
LOWVOL_PEAK_DBFS = -30.0   # 远场小音量：峰值压到 -30 dBFS
FAST_FACTOR = 1.35    # 快语速：1.35 倍速（约每分钟多念三分之一的内容）
OVERLAP_DELAY_S = 0.35     # 叠话：自身语音错位叠加
OVERLAP_GAIN = 0.75

SCENARIOS = ("clean", "noise", "lowvol", "fast", "multi", "long")


# ============ 读写 ============

def read_audio(path: Path) -> np.ndarray:
    """读成 float32 单声道 16kHz。WAV 走标准库，其它容器回退 PyAV。"""
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as w:
                if w.getsampwidth() == 2:
                    channels, rate = w.getnchannels(), w.getframerate()
                    raw = w.readframes(w.getnframes())
                    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                    if channels > 1:
                        data = data.reshape(-1, channels).mean(axis=1)
                    if rate != SAMPLE_RATE:
                        data = _resample(data, SAMPLE_RATE / rate)
                    return data
        except Exception:
            pass  # 非 16-bit PCM WAV 交给 PyAV

    import av

    chunks: list[np.ndarray] = []
    with av.open(str(path)) as container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise SystemExit(f"{path.name} 不含音频流")
        rs = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        for frame in container.decode(stream):
            for out in _as_list(rs.resample(frame)):
                arr = out.to_ndarray()
                chunks.append(arr.reshape(-1).astype(np.float32) / 32768.0)
        for out in _as_list(rs.resample(None)):
            arr = out.to_ndarray()
            chunks.append(arr.reshape(-1).astype(np.float32) / 32768.0)
    return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)


def _as_list(result) -> list:
    if result is None:
        return []
    return list(result) if isinstance(result, (list, tuple)) else [result]


def write_wav(path: Path, audio: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(audio, -1.0, 1.0)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes((clipped * 32767.0).astype(np.int16).tobytes())


def _resample(data: np.ndarray, ratio: float) -> np.ndarray:
    """线性重采样。对评测够用：VAD 只关心能量包络，Whisper 内部还会归一化到 16k。"""
    n = int(round(len(data) * ratio))
    if n <= 1:
        return np.zeros(0, dtype=np.float32)
    return np.interp(
        np.linspace(0, len(data) - 1, n, dtype=np.float64),
        np.arange(len(data)),
        data,
    ).astype(np.float32)


# ============ 信号处理 ============

def rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0


def peak_norm(audio: np.ndarray, target_dbfs: float) -> np.ndarray:
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak <= 1e-9:
        return audio
    target = 10.0 ** (target_dbfs / 20.0)
    return (audio * (target / peak)).astype(np.float32)


def babble_noise(n: int, rng: np.random.Generator) -> np.ndarray:
    """
    教室背景底噪的粗略代理：以低频能量为主 + 少量中频，模拟人声嗡鸣与空调风噪。

    不用白噪声是因为白噪声的能量集中在高频，而 Whisper 对高频噪声本来就不敏感，
    用白噪声会得出「加了很多噪声但 WER 几乎不变」的假乐观结论。
    """
    white = rng.standard_normal(n).astype(np.float32)
    low = np.convolve(white, np.ones(96, dtype=np.float32) / 96.0, mode="same")
    mid = np.convolve(white, np.ones(8, dtype=np.float32) / 8.0, mode="same")
    low = low / (np.max(np.abs(low)) + 1e-9)
    mid = mid / (np.max(np.abs(mid)) + 1e-9)
    return (0.85 * low + 0.15 * mid).astype(np.float32)


def add_noise(audio: np.ndarray, snr_db: float, rng: np.random.Generator) -> np.ndarray:
    """按目标信噪比混入底噪：噪声幅度 = 语音 RMS / 10^(SNR/20)"""
    if audio.size == 0:
        return audio
    noise = babble_noise(len(audio), rng)
    speech_rms = rms(audio)
    noise_scale = speech_rms / (10.0 ** (snr_db / 20.0))
    noise = noise * (noise_scale / (rms(noise) + 1e-9))
    return (audio + noise).astype(np.float32)


def overlap_self(audio: np.ndarray, delay_s: float, gain: float) -> np.ndarray:
    """
    叠话压力用例：把自身语音延后一点叠加回去，制造「同时有人在说」的干扰。

    ⚠ 这不是多人对话：它只有一个说话人，说的是同一句话。
    它测的是「有竞争性语音时 VAD 与识别会不会崩」，不是多人场景的准确率。
    """
    delay = int(delay_s * SAMPLE_RATE)
    out = np.zeros(len(audio) + delay, dtype=np.float32)
    out[: len(audio)] += audio
    out[delay:] += audio * gain
    return peak_norm(out, -3.0)


def build_long(source: np.ndarray, minutes: float, rng: np.random.Generator):
    """
    长时连续用例：语音 + 随机长短停顿循环拼接。

    停顿长度刻意随机（0.8~6 秒），因为固定间隔等于给 VAD 送一份过于规整的输入，
    测不出「停顿长短变化时片段边界是否稳定」。

    返回 (音频, 实际语音重复次数)。**重复次数必须由这里如实回传**：
    若改用 `len(音频) // len(source)` 推算，会把停顿也算成语音、
    凭空虚报若干次重复，参考文本比识别结果长出一截 —— WER 直接虚高。
    """
    target = int(minutes * 60 * SAMPLE_RATE)
    parts: list[np.ndarray] = []
    total = 0
    repeats = 0
    while total < target:
        parts.append(source)
        total += len(source)
        repeats += 1
        gap_s = float(rng.uniform(0.8, 6.0))
        gap = np.zeros(int(gap_s * SAMPLE_RATE), dtype=np.float32)
        parts.append(gap)
        total += len(gap)
    return np.concatenate(parts)[:target].astype(np.float32), repeats


# ============ 主流程 ============

def synth_one(source: np.ndarray, scenario: str, seed: int, long_minutes: float):
    """返回 (音频, 语音重复次数)。非 long 场景重复次数恒为 1。"""
    rng = np.random.default_rng(seed)
    base = peak_norm(source, -3.0)

    if scenario == "clean":
        return base, 1
    if scenario == "noise":
        return peak_norm(add_noise(base, SNR_DB, rng), -3.0), 1
    if scenario == "lowvol":
        return peak_norm(base, LOWVOL_PEAK_DBFS), 1
    if scenario == "fast":
        return peak_norm(_resample(base, 1.0 / FAST_FACTOR), -3.0), 1
    if scenario == "multi":
        return overlap_self(base, OVERLAP_DELAY_S, OVERLAP_GAIN), 1
    if scenario == "long":
        return build_long(base, long_minutes, rng)
    raise ValueError(scenario)


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="合成多场景 ASR 测试集")
    parser.add_argument("--source", default="backend/tests/fixtures/jfk.wav", help="源音频（需同名 .txt 参考文本）")
    parser.add_argument("--out", default="tests/audio", help="输出目录")
    parser.add_argument("--only", nargs="*", choices=SCENARIOS, default=None)
    parser.add_argument("--long-minutes", type=float, default=12.0)
    parser.add_argument("--seed", type=int, default=20261006)
    args = parser.parse_args()

    src = Path(args.source)
    if not src.is_absolute():
        src = root / src
    if not src.is_file():
        raise SystemExit(f"源音频不存在: {src}")

    ref_path = src.with_suffix(".txt")
    if not ref_path.is_file():
        raise SystemExit(
            f"缺少参考文本 {ref_path.name} —— 没有逐字稿就没有 WER，\n"
            f"请先在同目录放一份与 {src.name} 对应的 UTF-8 文本。"
        )
    ref_text = ref_path.read_text(encoding="utf-8").strip()

    source = read_audio(src)
    if source.size == 0:
        raise SystemExit(f"无法解码: {src}")
    print(f"源音频 : {src.name}  {len(source) / SAMPLE_RATE:.2f}s  RMS {rms(source):.4f}")

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = root / out_dir

    wanted = args.only or list(SCENARIOS)
    if "accent" in wanted:
        print("  跳过 accent：合成无法造出口音，留空等真录音")
        wanted = [s for s in wanted if s != "accent"]
    # 长文件放最后：它最慢，先让快的场景出结果
    wanted = sorted(wanted, key=lambda s: (s == "long", SCENARIOS.index(s) if s in SCENARIOS else 0))

    made: list[tuple[str, float, str]] = []
    for scenario in wanted:
        audio, repeats = synth_one(
            source, scenario, args.seed + SCENARIOS.index(scenario), args.long_minutes
        )
        # 短用例与长用例**必须分目录**：它们要用两种完全不同的跑法
        # （`--realtime` vs 尽快喂完）。放在同一个目录里，一次 --audio-dir 扫描
        # 就会把 12 分钟文件也按「尽快喂完」灌进去，人为触发背压丢弃，
        # 结论会被污染成「字幕会缺内容」——而真实听课根本不会那样喂。
        sub = "long" if scenario == "long" else "short"
        path = out_dir / sub / f"synth_{scenario}_{src.stem}.wav"
        write_wav(path, audio)

        # 参考文本：非长文件就是原文；长文件按**实际重复次数**展开。
        # 不要用 len(audio)//len(source) 推算 —— 那会把停顿算进去，虚报重复次数。
        text = "\n".join([ref_text] * repeats)
        path.with_suffix(".txt").write_text(text + "\n", encoding="utf-8")
        made.append((f"{sub}/synth_{scenario}_{src.stem}.wav", len(audio) / SAMPLE_RATE, scenario))
        print(f"  ✓ {sub + '/synth_' + scenario + '_' + src.stem + '.wav':<40}"
              f"{len(audio) / SAMPLE_RATE:>7.1f}s  场景={scenario}"
              + (f"  语音重复 {repeats} 次" if scenario == "long" else ""))

    print(f"\n共生成 {len(made)} 个文件到 {out_dir}")
    print("  · 短用例 → tests/audio/short  （配合 eval_asr_quality.py --audio-dir，尽快喂完）")
    print("  · 长用例 → tests/audio/long   （配合 eval_asr_quality.py --audio-dir --realtime）")
    print("注：这些是**合成**素材（由同一个源音频做信号处理得到），")
    print("    用于验证管道鲁棒性与长时稳定性，**不能替代真实网课录音的准确率结论**。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
