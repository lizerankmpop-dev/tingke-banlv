#!/usr/bin/env python
"""
真实音频端到端质量评估：准确率(WER/CER) + 字幕推送延迟 + 长时稳定性

为什么需要它
------------
`scripts/bench_asr.py` 只回答一个问题：**跟不跟得上**（RTF）。
但用户实际体感由另外三件事决定，现有脚本一个都没覆盖：

  1. **准不准** —— 学生看到的字幕本身错了多少（WER / CER）。识别得再快，
     字幕是错的也没有意义。
  2. **字幕什么时候到** —— 从「这句话说完」到「字幕出现在屏幕上」隔了多久。
     RTF < 1 只说明不累积滞后，不等于单句延迟可接受。
  3. **连续听 10 分钟以上会怎样** —— 会不会丢片段、会不会越拖越晚。
     这是背压（MAX_PENDING_JOBS）与合并降级策略的验收点，短样本永远测不出来。

关键设计：**复用真实管道的同一套分段与识别**
----------------------------------------
`audio.vad.VoiceActivityDetector` + `asr.whisper_engine.WhisperEngine` 原样使用，
只把音频来源从「系统回环采集」换成文件。因此分段长度、单次调用固定开销、
背压与合并行为都和用户实际经历的一致 —— 自己另写一套切分再评估是没有意义的。

用法
----
    # 只测准确率与 RTF（尽快喂完，快）
    PYTHONPATH=backend <venv>/python scripts/eval_asr_quality.py --audio-dir tests/audio

    # 按真实速率喂入：额外测推送延迟与长时稳定性（长音频推荐）
    PYTHONPATH=backend <venv>/python scripts/eval_asr_quality.py \
        --audio-dir tests/audio --realtime --report packaging/eval/report.json

参考文本
--------
与音频同名的 `.txt`（UTF-8）即为参考答案。有则算 WER/CER，没有则只输出识别文本
供人工核对 —— 真实网课录音很难拿到逐字稿，所以「缺参考文本」是常态而非错误。

场景分类
--------
文件名前缀会被识别为场景并分类汇总，用于横向对比不同录音条件下的表现：
`clean_` / `accent_` / `noise_` / `multi_`（多人叠话）/ `lowvol_` / `fast_`（连读快语速）

术语热词 A/B
------------
`--terms on|off` 控制是否注入领域术语热词（默认 on）。同一个音频分别跑一次即可
量化「术语注入到底把 WER 降低了多少」——这在面试里是能直接讲数据的点。
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import threading
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

# 允许不带 PYTHONPATH 直接运行（真实管道在 backend/ 下）
_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if _BACKEND.is_dir() and str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import numpy as np  # noqa: E402

SAMPLE_RATE = 16000
# 与 audio/capture.py 的 CHUNK_SIZE 保持一致：Silero VAD 要求恰好 512 样本。
# 这里若用别的块大小，VAD 会因为内部对齐而产生与真实运行不同的分段边界。
CHUNK_SIZE = 512

SCENARIOS = ("clean", "accent", "noise", "multi", "lowvol", "fast", "long")


# ============ 音频读取 ============

def load_audio(path: Path) -> np.ndarray:
    """
    读成 float32 单声道 16kHz。

    优先用 PyAV 解码：真实网课录音几乎都是 mp3 / m4a / mp4 容器，
    而 `wave` 只认 PCM WAV。PyAV 已是 faster-whisper 的依赖，不算新增依赖。
    纯 WAV 走标准库更快，所以先试 wave。
    """
    if path.suffix.lower() == ".wav":
        try:
            return _load_wav(path)
        except Exception:
            pass  # 非 16-bit PCM 的 WAV 回退到 PyAV

    try:
        import av
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(f"无法读取 {path.name}：需要 PyAV 解码非 WAV 音频（{exc}）")

    chunks: list[np.ndarray] = []
    with av.open(str(path)) as container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise SystemExit(f"{path.name} 不含音频流")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        for frame in container.decode(stream):
            for out in _as_frames(resampler.resample(frame)):
                chunks.append(_frame_to_mono(out))
        for out in _as_frames(resampler.resample(None)):
            chunks.append(_frame_to_mono(out))

    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def _as_frames(result) -> list:
    """PyAV 的 resample() 在不同版本可能返回单帧、None 或列表，统一成列表"""
    if result is None:
        return []
    return list(result) if isinstance(result, (list, tuple)) else [result]


def _frame_to_mono(frame) -> np.ndarray:
    arr = frame.to_ndarray()
    # s16 mono 的 ndarray 形状是 (1, n)；立体声会多一维
    return arr.reshape(-1) if arr.ndim == 1 else arr.reshape(arr.shape[0], -1).mean(axis=1)


def _load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("仅支持 16-bit PCM")
        channels = w.getnchannels()
        rate = w.getframerate()
        raw = w.readframes(w.getnframes())
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    if rate != SAMPLE_RATE:
        # 线性重采样：对评测足够（VAD 只关心能量包络，Whisper 内部还会再归一化到 16k）
        n = int(round(len(data) * SAMPLE_RATE / rate))
        data = np.interp(
            np.linspace(0, len(data) - 1, n, dtype=np.float64),
            np.arange(len(data)),
            data,
        ).astype(np.float32)
    return data


# ============ 准确率：WER / CER ============
#
# 刻意不引入 jiwer 之类的第三方库：本项目对依赖很克制，
# 而编辑距离本身不到 30 行，还能顺手把「中文按字、英文按词」的归一化规则写清楚。

_PUNCT = set(
    " \t\n\r.,!?;:\"'“”‘’()[]{}<>«»…、。，！？；：（）《》〈〉「」『』【】—-_/\\|~`@#$%^&*+=·•"
)


def _is_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def normalize(text: str, language: str) -> str:
    """
    归一化：去掉标点与大小写差异，只保留「内容」。

    为什么必须做：Whisper 会自动加标点和大写，而参考答案通常是裸文本；
    不归一化的话 WER 会被标点差异污染，测出来的是标点习惯而不是识别准确率。
    """
    text = text.strip()
    if language == "zh" or _is_cjk(text):
        return "".join(ch for ch in text if ch not in _PUNCT)
    out = []
    for ch in text:
        if ch in _PUNCT:
            out.append(" ")
        else:
            out.append(ch.lower())
    return " ".join("".join(out).split())


def edit_distance(ref: list, hyp: list) -> int:
    """标准 Levenshtein，滚动数组，O(min(n,m)) 空间"""
    if not ref:
        return len(hyp)
    if not hyp:
        return len(ref)
    if len(ref) < len(hyp):
        ref, hyp = hyp, ref
    prev = list(range(len(hyp) + 1))
    for i, rc in enumerate(ref, 1):
        cur = [i]
        for j, hc in enumerate(hyp, 1):
            cur.append(
                min(
                    prev[j] + 1,          # 删除
                    cur[j - 1] + 1,       # 插入
                    prev[j - 1] + (rc != hc),  # 替换
                )
            )
        prev = cur
    return prev[-1]


def wer(ref_text: str, hyp_text: str, language: str) -> dict:
    """
    返回 {wer, cer, refUnits, hypUnits, edits}

    · 英文：WER 按词、CER 按字符
    · 中文：没有分词器，WER 与 CER 同按字符统计（此时两者相等，不是 bug）
    """
    is_zh = language == "zh" or _is_cjk(ref_text)
    ref_n = normalize(ref_text, language)
    hyp_n = normalize(hyp_text, language)

    ref_units = list(ref_n) if is_zh else ref_n.split()
    hyp_units = list(hyp_n) if is_zh else hyp_n.split()
    edits = edit_distance(ref_units, hyp_units)
    word_rate = edits / len(ref_units) if ref_units else 0.0

    ref_chars = list(ref_n.replace(" ", ""))
    hyp_chars = list(hyp_n.replace(" ", ""))
    char_edits = edit_distance(ref_chars, hyp_chars)
    char_rate = char_edits / len(ref_chars) if ref_chars else 0.0

    return {
        "wer": round(word_rate, 4),
        "cer": round(char_rate, 4),
        "refUnits": len(ref_units),
        "hypUnits": len(hyp_units),
        "edits": edits,
    }


# ============ 采集器：把 VAD 分段与识别结果按时间对齐 ============

class Recorder:
    """
    记录「音频被喂入的时刻」与「字幕被推出来的时刻」，二者之差就是推送延迟。

    VAD 回调给出的 start/end 是**音频流内的时间**，加上喂入起点就换算成墙钟时刻 ——
    这正是真实场景里「这句话说完」的那一刻。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.feed_t0 = 0.0
        self.segments: list[dict] = []      # VAD 切出的片段
        self.subtitles: list[dict] = []     # 引擎推出来的字幕

    def mark_feed_start(self) -> None:
        self.feed_t0 = time.perf_counter()

    def on_segment(self, audio, start_time: float, end_time: float) -> dict:
        rec = {
            "index": len(self.segments),
            "audioStart": round(start_time, 3),
            "audioEnd": round(end_time, 3),
            "duration": round(end_time - start_time, 3),
            "readyWall": round(self.feed_t0 + end_time, 6),
        }
        with self._lock:
            self.segments.append(rec)
        return rec

    def on_subtitle(self, text: str, words) -> None:
        # 与 pipeline._on_recognition_result 保持一致：空文本不产生字幕。
        # 否则「字幕条数 == 片段条数」会变得过于宽松，把空识别也算成一次成功配对。
        if not text or not text.strip():
            return
        now = time.perf_counter()
        with self._lock:
            self.subtitles.append(
                {
                    "index": len(self.subtitles),
                    "text": text,
                    "pushWall": round(now, 6),
                    "latency": round(now - self.feed_t0, 3) if self.feed_t0 else None,
                }
            )

    def pair(self) -> list[dict]:
        """
        把字幕与片段配对并算出延迟。**只在 --realtime 模式下调用。**

        只有在「字幕条数 == 片段条数」时才 1:1 配对：一旦发生合并降级
        （引擎把多个片段并成一次识别），条数就不相等了，此时强行配对会得到
        看起来合理、实际完全错位的延迟数据。这种情况如实标记出来。
        """
        with self._lock:
            segs, subs = list(self.segments), list(self.subtitles)
        if len(segs) != len(subs):
            return []
        out = []
        for seg, sub in zip(segs, subs):
            out.append(
                {
                    "index": seg["index"],
                    "audioStart": seg["audioStart"],
                    "audioEnd": seg["audioEnd"],
                    "duration": seg["duration"],
                    "latency": round(sub["pushWall"] - seg["readyWall"], 3),
                    "text": sub["text"],
                }
            )
        return out


class SubtitleRouter:
    """
    把引擎的字幕回调转发给「当前正在评估的那个 Recorder」。

    为什么需要它：`WhisperEngine` 只创建一次（模型加载很贵），`on_partial`
    在构造时就被绑定死了；但每个音频文件都新建自己的 `Recorder`。
    如果直接把某个 Recorder 传给引擎，所有文件的字幕都会记到那一个实例上 ——
    评估文件自己的 Recorder 永远是空的，报告里就是「片段 8 → 字幕 0、WER 恒为 1.0」。
    这种失败不会报错、不会崩溃，只是安静地把结论变成完全相反的样子。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: Recorder | None = None

    def bind(self, recorder: Recorder) -> None:
        with self._lock:
            self._current = recorder

    def __call__(self, text: str, words) -> None:
        with self._lock:
            recorder = self._current
        if recorder is not None:
            recorder.on_subtitle(text, words)


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(ratio * (len(ordered) - 1)))))
    return round(ordered[idx], 3)


# ============ 引擎 / VAD 组装 ============

def build_engine(args, router: "SubtitleRouter", prompt: str):
    from asr.whisper_engine import COMPUTE_TYPE, DEFAULT_MODEL, DEVICE, WhisperEngine

    # 绝不能把 None 透传给引擎：model_size=None 会被拼成
    # `Systran/faster-whisper-None`，然后走「未缓存 → 下载」分支，
    # 表现是模型名看起来正常、却 401 失败并干等满超时。
    model_size = args.model or DEFAULT_MODEL

    status: list[str] = []

    engine = WhisperEngine(
        model_size=model_size,
        device=DEVICE,
        compute_type=COMPUTE_TYPE,
        language=args.language,
        # 走 router 而不是直接绑某个 Recorder，理由见 SubtitleRouter 的注释
        on_partial=router,
        on_progress=lambda stage, pct: status.append(f"{stage}:{pct:.0f}%"),
        initial_prompt=prompt,
    )

    started = time.time()
    idle_since: float | None = None
    while not engine.is_loaded:
        elapsed = time.time() - started
        if elapsed > args.model_timeout:
            raise SystemExit(
                f"模型加载超时（{args.model_timeout:.0f}s）: {status[-3:]}｜模型={model_size}"
            )
        if engine.is_loading:
            idle_since = None
        else:
            # is_loading 已复位却没 load 成功 = 这次加载已经失败。
            # 立刻报错比干等满超时有用得多（首次踩坑就白等了 300 秒）。
            idle_since = idle_since or time.time()
            if time.time() - idle_since > 15:
                raise SystemExit(
                    f"模型加载失败: {status[-3:]}｜模型={model_size}｜"
                    f"可用内置种子播种到 ~/.tingkebanlv/whisper_models，无需联网"
                )
        time.sleep(0.5)
    print(f"  模型就绪（{model_size} / {DEVICE} / {COMPUTE_TYPE}，"
          f"{time.time() - started:.1f}s）")
    return engine


def build_vad(recorder: Recorder, engine):
    """
    组装 VAD，并把片段送去识别。

    ⚠ 这里必须同时做两件事：记录片段 + `engine.add_audio(audio)`。
    漏掉后者的后果极具欺骗性 —— VAD 照常切出片段、脚本照常跑完、
    报告照常输出一张表，但**一句都没识别**：WER 恒为 1.0、RTF 0.01、
    "未丢弃任何片段" 全绿。看起来像「识别质量极差」，实际是「没接上」。
    真实管道在 asr/pipeline.py 的 _on_speech_segment 里做的就是这两步。
    """
    from audio.vad import VoiceActivityDetector

    def on_segment(audio, start_time, end_time):
        recorder.on_segment(audio, start_time, end_time)
        engine.add_audio(audio)

    return VoiceActivityDetector(on_speech_segment=on_segment)


def drain(engine, timeout: float = 600.0, quiet: float = 1.0) -> bool:
    """
    等引擎把手上的片段全部识别完。

    ⚠ 不能只看「一次采样的 busy=False 且 backlog==0」。引擎消费者线程是
    「先 list+clear 队列，再置 _busy=True」，两件事不在同一个临界区里：
      1) jobs = list(self._jobs) / self._jobs.clear()  ← 此时 backlog 已经变 0
      2) self._merge_jobs(jobs)                        ← 合并还要花时间
      3) self._busy = True
    于是第 1~3 步之间存在一个「队列空 + 不忙」的假空闲窗口。踩中它的后果是
    致命的静默错误：drain 提前返回 → 这一段的字幕算作没有 → WER 直接变成 1.0，
    而真实原因是它被漏到下一个文件里去了（下一轮识别会把两段并成一次调用）。
    这里要求「连续 quiet 秒都空闲」才判定结束，把这个窗口盖住。
    """
    deadline = time.time() + timeout
    idle_since: float | None = None
    while time.time() < deadline:
        st = engine.get_stats()
        if not st.get("busy") and st.get("backlog", 0) == 0:
            idle_since = idle_since or time.time()
            if time.time() - idle_since >= quiet:
                return True
        else:
            idle_since = None
        time.sleep(0.05)
    return False


# ============ 单个文件的评测 ============

def eval_file(path: Path, args, engine, router: "SubtitleRouter") -> dict:
    audio = load_audio(path)
    if audio.size == 0:
        return {"file": path.name, "error": "音频为空或无法解码"}

    duration = len(audio) / SAMPLE_RATE
    ref_path = path.with_suffix(".txt")
    ref_text = ref_path.read_text(encoding="utf-8").strip() if ref_path.is_file() else None

    recorder = Recorder()
    # 关键：把字幕回调切到本文件的 Recorder，否则字幕会记到上一个/顶层实例上
    router.bind(recorder)
    vad = build_vad(recorder, engine)

    stats_before = engine.get_stats()
    recorder.mark_feed_start()
    feed_start = time.perf_counter()

    # 按 512 样本（32ms）喂入，与 audio/capture.py 的真实块大小一致
    starvation = 0.0
    for i in range(0, len(audio), CHUNK_SIZE):
        vad.process_chunk(audio[i : i + CHUNK_SIZE])
        if args.realtime:
            target = feed_start + min(i + CHUNK_SIZE, len(audio)) / SAMPLE_RATE
            gap = target - time.perf_counter()
            if gap > 0:
                time.sleep(gap)
            else:
                starvation += -gap  # 喂入本身跟不上 → 必须如实报告，否则结论会失真
    vad.flush()
    feed_end = time.perf_counter()
    feed_elapsed = feed_end - feed_start

    completed = drain(engine, timeout=args.drain_timeout)
    drain_end = time.perf_counter()
    pipeline_elapsed = drain_end - feed_start
    tail_elapsed = drain_end - feed_end
    stats_after = engine.get_stats()

    def delta(key: str) -> int:
        return int(stats_after.get(key, 0)) - int(stats_before.get(key, 0))

    # 延迟只在实时模式下有意义。非实时模式里音频被尽可能快地灌进去，
    # 而 readyWall = 喂入起点 + 音频内时间，会落到墙钟的**未来**，
    # 算出来是一堆负数（例如 -2.3s、-10.4s），看着像异常其实毫无意义。
    # 与其输出会被误读的数字，不如在这一模式下如实不给。
    paired = recorder.pair() if args.realtime else []
    latencies = [p["latency"] for p in paired]

    # 累积滞后判定：把已配对片段按时间切成前后两半比均值。
    # 后半段明显更慢 = 背压没起作用/跟不上，这才是「越听越迟」的真实信号。
    half = len(latencies) // 2
    first_half = round(statistics.fmean(latencies[:half]), 3) if half else None
    second_half = round(statistics.fmean(latencies[half:]), 3) if half else None

    recognized = " ".join(s["text"] for s in recorder.subtitles).strip()

    # RTF 的算法要分模式，否则报出来的数字没有意义：
    #   非实时 → 从开始喂入到全部识别完的总耗时 / 音频时长。喂入本身几乎不耗时，
    #     所以这个比值就是「处理 1 秒音频需要多少秒」，即「跟不跟得上实时」，必须 < 1。
    #   实时   → 喂入被刻意拖到真实速率，总耗时必然 ≈ 音频时长，RTF 恒等于 1，
    #     毫无信息量。此时改报 tailSeconds（喂完后还要等多久才收尾干净）：
    #     这数很大 = 结束时还有积压没消化完。
    if args.realtime:
        rtf = None
    else:
        rtf = round(pipeline_elapsed / duration, 3) if duration else None
    tail = round(tail_elapsed, 3)

    result = {
        "file": path.name,
        "scenario": scenario_of(path.name),
        "audioSeconds": round(duration, 2),
        "segments": len(recorder.segments),
        "subtitles": len(recorder.subtitles),
        "rtf": rtf,
        "feedSeconds": round(feed_elapsed, 2),
        "pipelineSeconds": round(pipeline_elapsed, 2),
        "tailSeconds": tail,
        "realtime": bool(args.realtime),
        "starvationSeconds": round(starvation, 3),
        "droppedSegments": delta("droppedSegments"),
        "mergedCalls": delta("mergedCalls"),
        "backlogRemaining": stats_after.get("backlog", 0),
        "drained": completed,
        "recognition": recognized,
        "reference": ref_text,
        # ---- 术语提示词防循环（P3 核心观测项）----
        # 全都是差值：engine 是多个文件共用的，读累计值会让第一个文件
        # 之后的每个文件都「继承」上一个文件触发过的降级
        "termPromptChars": stats_after.get("termPromptChars"),
        "termPromptDegraded": bool(stats_after.get("termPromptDegraded")),
        "termAutoDegrade": bool(stats_after.get("termAutoDegrade")),
        "repetitionEvents": delta("repetitionEvents"),
        "degradeEvents": delta("degradeEvents"),
        # P0-3：真正送进模型的 token 分量（字符数早已不是有意义的指标 ——
        # 中文和英文的 token/字符 差 4.5 倍）
        "promptTokens": stats_after.get("promptTokens"),
        "promptBudget": stats_after.get("promptBudget"),
        "promptTokenExact": bool(stats_after.get("promptTokenExact")),
        "droppedTerms": stats_after.get("droppedTerms") or [],
        # 最近一次复读检测的证据（关闭降级时也有值，用来区分「没检出」和「检出但没管」）
        "lastRepeat": stats_after.get("lastRepeat"),
        "inSegments": delta("inSegments"),
        "outSegments": delta("outSegments"),
        "paired": bool(paired),
        "latency": {
            "mean": round(statistics.fmean(latencies), 3) if latencies else None,
            "p50": percentile(latencies, 0.5),
            "p95": percentile(latencies, 0.95),
            "max": round(max(latencies), 3) if latencies else None,
            "firstHalfMean": first_half,
            "secondHalfMean": second_half,
        },
    }

    if ref_text:
        result["accuracy"] = wer(ref_text, recognized, args.language)
    if paired:
        result["segmentDetail"] = paired[:args.detail_limit]
    return result


def scenario_of(name: str) -> str:
    lowered = name.lower()
    for s in SCENARIOS:
        if lowered.startswith(s) or f"_{s}_" in lowered or f"_{s}." in lowered:
            return s
    return "other"


# ============ 汇总与输出 ============

def summarize(results: list[dict]) -> dict:
    ok = [r for r in results if "error" not in r]
    acc = [r for r in ok if r.get("accuracy")]
    lat = [r["latency"] for r in ok if r.get("paired") and r["latency"]["mean"] is not None]
    # 只把真的算出来的 RTF 收进来：实时模式下 rtf 是 None（恒 ≈1 无信息量），
    # 直接喂给 fmean / max 会 TypeError，或者把 None 当成 0 得出「RTF 极好」的假结论。
    rtfs = [r["rtf"] for r in ok if r.get("rtf") is not None]

    by_scenario: dict[str, dict] = {}
    for r in ok:
        bucket = by_scenario.setdefault(r["scenario"], {"files": 0, "wer": [], "cer": [], "rtf": []})
        bucket["files"] += 1
        if r.get("rtf") is not None:
            bucket["rtf"].append(r["rtf"])
        if r.get("accuracy"):
            bucket["wer"].append(r["accuracy"]["wer"])
            bucket["cer"].append(r["accuracy"]["cer"])

    def mean(values: list[float]):
        return round(statistics.fmean(values), 4) if values else None

    return {
        "files": len(ok),
        "failed": len(results) - len(ok),
        "withReference": len(acc),
        "meanWer": mean([r["accuracy"]["wer"] for r in acc]),
        "meanCer": mean([r["accuracy"]["cer"] for r in acc]),
        "meanRtf": mean(rtfs),
        "maxRtf": round(max(rtfs), 3) if rtfs else None,
        "maxTailSeconds": round(max([r["tailSeconds"] for r in ok]), 3) if ok else None,
        "totalDropped": sum(r["droppedSegments"] for r in ok),
        "totalMerged": sum(r["mergedCalls"] for r in ok),
        "totalSegments": sum(r["segments"] for r in ok),
        "totalSubtitles": sum(r["subtitles"] for r in ok),
        # ---- 术语提示词防循环（P3 核心观测项）----
        # 「字幕数 > 片段数」是重复循环最直观的结构信号：一条音频片段正常只产出
        # 一条字幕，多出来的就是模型把提示词当上文继续往下编的产物。
        "totalInSegments": sum(r.get("inSegments", 0) for r in ok),
        "totalOutSegments": sum(r.get("outSegments", 0) for r in ok),
        "totalRepetitionEvents": sum(r.get("repetitionEvents", 0) for r in ok),
        "degradedFiles": [r["file"] for r in ok if r.get("termPromptDegraded")],
        "filesWithMoreSubtitlesThanSegments": [
            r["file"] for r in ok if r["subtitles"] > r["segments"]
        ],
        "termPromptChars": next(
            (r["termPromptChars"] for r in ok if r.get("termPromptChars") is not None), None
        ),
        "peakLatency": round(max([l["max"] for l in lat if l["max"] is not None]), 3) if lat else None,
        "meanLatency": round(statistics.fmean([l["mean"] for l in lat]), 3) if lat else None,
        "byScenario": {
            k: {
                "files": v["files"],
                "meanWer": mean(v["wer"]),
                "meanCer": mean(v["cer"]),
                "meanRtf": mean(v["rtf"]),
            }
            for k, v in sorted(by_scenario.items())
        },
    }


def verdict(summary: dict, results: list[dict], args) -> tuple[bool, list[str]]:
    notes: list[str] = []
    passed = True

    # 最先查「有没有真的跑起来」。这两条是整轮评估的可用性前提：
    # 没有片段 = VAD 没工作；有片段但零字幕 = 片段没接到引擎上。
    # 少了这两条，脚本会在完全没识别的情况下输出一张「全绿」的表。
    if summary["totalSegments"] == 0:
        passed = False
        notes.append("✗ 一个语音片段都没切出来（VAD 未工作或音频全静音）—— 本次结果无效")
    elif summary["totalSubtitles"] == 0:
        passed = False
        notes.append("✗ 切出了片段但没有任何字幕（片段未送入识别引擎）—— 本次结果无效")
    else:
        notes.append(
            f"✓ 管道连通：{summary['totalSegments']} 个片段 → {summary['totalSubtitles']} 条字幕"
        )

    if summary["totalDropped"] > 0:
        passed = False
        notes.append(f"✗ 丢弃片段 {summary['totalDropped']} 个 —— 字幕会缺内容（背压触发）")
    else:
        notes.append("✓ 未丢弃任何片段")

    if summary["totalMerged"] > 0:
        notes.append(f"⚠ 合并识别 {summary['totalMerged']} 次 —— 属于设计内的降级，字幕会更粗")
    else:
        notes.append("✓ 未触发合并降级")

    if summary["meanRtf"] is not None:
        if summary["meanRtf"] < 1:
            notes.append(f"✓ 平均 RTF {summary['meanRtf']} < 1（跟得上实时）")
        else:
            passed = False
            notes.append(f"✗ 平均 RTF {summary['meanRtf']} ≥ 1 —— 字幕会持续滞后")
    if args.realtime:
        tail = summary["maxTailSeconds"]
        notes.append(f"— 实时模式不计算 RTF（恒 ≈1 无信息量）；收尾最长 {tail}s（大 = 结束时有积压）")
    else:
        notes.append("— 非实时模式不计算推送延迟（音频被加速灌入，延迟必然为负、无意义）")

    if summary["withReference"] == 0:
        notes.append("⚠ 没有任何参考文本，本次只测出实时性与稳定性，准确率未评估")
    elif summary["meanWer"] is not None and summary["totalSubtitles"] > 0:
        notes.append(f"— 平均 WER {summary['meanWer']}｜CER {summary['meanCer']}（{summary['withReference']} 个有逐字稿）")

    if args.max_wer is not None and summary["meanWer"] is not None:
        if summary["meanWer"] <= args.max_wer:
            notes.append(f"✓ 平均 WER {summary['meanWer']} ≤ 阈值 {args.max_wer}")
        else:
            passed = False
            notes.append(f"✗ 平均 WER {summary['meanWer']} > 阈值 {args.max_wer}")

    # 没等干净就收尾 = 有片段被漏到下一轮（合并进后面的文件），
    # 表现为「这一个文件片段有、字幕没有」，但根因不在识别而在收尾判定。
    not_drained = [r["file"] for r in results if "error" not in r and not r.get("drained")]
    if not_drained:
        passed = False
        notes.append(
            f"✗ 收尾超时，仍有片段没识别完：{', '.join(not_drained[:3])}"
            + ("…" if len(not_drained) > 3 else "")
            + "（这些文件的字幕数与 WER 不可信）"
        )

    # 有片段却配对不上，说明发生了合并降级 —— 这时的延迟数字不可信，必须说清楚
    if args.realtime:
        unpaired = [r["file"] for r in results if r.get("segments") and not r.get("paired")]
        if unpaired:
            notes.append(
                f"⚠ 以下文件片段数与字幕数不等，未做延迟配对（延迟数字缺失是如实标记，不是算错）"
                f"：{', '.join(unpaired[:3])}" + ("…" if len(unpaired) > 3 else "")
            )

    for r in results:
        if r.get("paired") and r["latency"]["firstHalfMean"] and r["latency"]["secondHalfMean"]:
            if r["latency"]["secondHalfMean"] > r["latency"]["firstHalfMean"] * 1.5 + 1:
                notes.append(
                    f"⚠ {r['file']}: 后半段延迟明显高于前半段"
                    f"（{r['latency']['firstHalfMean']}s → {r['latency']['secondHalfMean']}s），疑似累积滞后"
                )
    return passed, notes


def print_report(results: list[dict], summary: dict, notes: list[str]) -> None:
    print("\n" + "=" * 104)
    print(f"{'文件':<32}{'场景':<9}{'秒':>7}{'片段':>5}{'字幕':>5}{'RTF':>7}{'WER':>7}{'收尾s':>8}{'延迟p95':>9}")
    print("-" * 104)
    for r in results:
        if "error" in r:
            print(f"{r['file'][:31]:<32}{'-':<9}{'-':>7}{'-':>5}{'-':>5}{'-':>7}{'-':>7}{'-':>8}{r['error'][:9]:>9}")
            continue
        acc = r.get("accuracy") or {}
        wer_v = acc.get("wer")
        print(
            f"{r['file'][:31]:<32}{r['scenario']:<9}{r['audioSeconds']:>7.1f}"
            f"{r['segments']:>5}{r['subtitles']:>5}"
            f"{(r['rtf'] if r['rtf'] is not None else '-'):>7}"
            f"{(wer_v if wer_v is not None else '-'):>7}"
            f"{r['tailSeconds']:>8}"
            f"{(str(r['latency']['p95']) if r['paired'] else '-'):>9}"
        )
    print("-" * 104)
    print(
        f"汇总：{summary['files']} 个文件（{summary['withReference']} 个有参考文本）｜"
        f"片段 {summary['totalSegments']} → 字幕 {summary['totalSubtitles']}｜"
        f"平均 WER {summary['meanWer']}｜CER {summary['meanCer']}"
    )
    print(
        f"      RTF 平均 {summary['meanRtf']}（最差 {summary['maxRtf']}）｜"
        f"收尾最长 {summary['maxTailSeconds']}s｜峰值延迟 {summary['peakLatency']}s｜"
        f"丢弃 {summary['totalDropped']}｜合并 {summary['totalMerged']}"
    )
    # 术语提示词防循环：这几行是 P3 判断「中文长术语表会不会触发重复循环」的直接依据
    print(
        f"      术语提示词 {summary.get('termPromptChars')} 字符｜"
        f"送入 {summary.get('totalInSegments')} 段 → 产出 {summary.get('totalOutSegments')} 条字幕｜"
        f"重复循环判定 {summary.get('totalRepetitionEvents')} 次"
    )
    if summary.get("filesWithMoreSubtitlesThanSegments"):
        print(
            "      ⚠ 字幕数 > 片段数的文件: "
            + ", ".join(summary["filesWithMoreSubtitlesThanSegments"])
        )
    if summary.get("degradedFiles"):
        print("      ⚠ 已触发自动降级（提示词被截断）的文件: " + ", ".join(summary["degradedFiles"]))
    for name, stat in summary["byScenario"].items():
        print(
            f"  · {name:<8} {stat['files']} 个文件｜WER {stat['meanWer']}｜"
            f"CER {stat['meanCer']}｜RTF {stat['meanRtf']}"
        )
    print("=" * 104)
    for note in notes:
        print(" ", note)


def main() -> int:
    parser = argparse.ArgumentParser(description="真实音频端到端质量评估")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--audio-dir", help="音频目录（递归扫描）")
    src.add_argument("--audio", help="单个音频文件")
    parser.add_argument("--realtime", action="store_true", help="按真实速率喂入（测延迟与稳定性）")
    parser.add_argument("--model", default=None, help="模型规格，默认取管道默认值（base）")
    parser.add_argument("--model-timeout", type=float, default=300.0, help="模型加载超时（秒）")
    parser.add_argument("--language", default="auto", help="auto|zh|en")
    parser.add_argument("--terms", choices=("on", "off"), default="on", help="是否注入术语热词")
    parser.add_argument("--domain", default="", help="术语领域（配合 --terms on）")
    parser.add_argument(
        "--custom-terms",
        default="",
        help="逗号分隔的自定义术语（配合 --terms on）。"
        "中文课程要验证「术语表能不能帮上忙」时必须用它 —— "
        "内置领域术语表全是英文，注入不到中文专业词上。",
    )
    parser.add_argument(
        "--autodegrade",
        choices=("on", "off"),
        default=None,
        help="术语提示词防循环兜底开关；不传则沿用引擎默认（开）。"
        "关掉它可以观察「完全不干预时重复循环能持续多久」——只在评估里用。",
    )
    parser.add_argument("--max-wer", type=float, default=None, help="WER 阈值，超过则失败")
    parser.add_argument("--report", default="packaging/eval/report.json")
    parser.add_argument("--drain-timeout", type=float, default=600.0)
    parser.add_argument("--detail-limit", type=int, default=200)
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 个文件（调试用）")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    if args.audio:
        files = [Path(args.audio)]
    else:
        folder = Path(args.audio_dir)
        if not folder.is_absolute():
            folder = root / folder
        if not folder.is_dir():
            raise SystemExit(f"目录不存在: {folder}")
        patterns = ("*.wav", "*.mp3", "*.m4a", "*.flac", "*.mp4", "*.aac", "*.ogg")
        files = sorted({p for pat in patterns for p in folder.rglob(pat)})
    if args.limit:
        files = files[: args.limit]
    if not files:
        raise SystemExit("没有找到任何音频文件")

    print("=" * 96)
    print("真实音频端到端质量评估")
    print(f"  文件数  : {len(files)}")
    print(f"  喂入方式: {'真实速率（含延迟与稳定性）' if args.realtime else '尽快（只测准确率与 RTF）'}")
    print(f"  术语热词: {args.terms}" + (f"（领域 {args.domain}）" if args.domain and args.terms == "on" else ""))
    print("=" * 96)

    prompt = ""
    if args.terms == "on":
        try:
            from dictionary.terminology import build_terminology

            custom = [x.strip() for x in (args.custom_terms or "").split(",") if x.strip()]
            term = build_terminology(domain=args.domain, custom_terms=custom)
            prompt = term["prompt"]
            total = term["stats"]["total"]
            print(
                f"  术语热词: {total} 个"
                + (f"（含自定义 {len(custom)} 个）" if custom else "")
                + ("（未指定 --domain，仅词表多词短语）" if total == 0 else "")
            )
            print(f"  提示词  : {term['promptChars']} 字符")
        except Exception as exc:
            print(f"  ⚠ 术语热词构建失败，按不注入处理: {exc}")

    router = SubtitleRouter()
    engine = build_engine(args, router, prompt)
    if args.model is None:
        args.model = engine.model_size

    # 防循环兜底开关只在评估时暴露：产品里它的真理源在桌面端的 overlay.json
    # （经 POST /api/asr/config 下发），脚本直接改引擎，不绕那一条链。
    if args.autodegrade == "off":
        engine.set_auto_degrade(False)
        print("防循环兜底：已关闭（可观察不受干预的重复循环）")
    elif args.autodegrade == "on":
        engine.set_auto_degrade(True)
        print("防循环兜底：已强制开启")
    print(f"术语提示词：{len(engine.initial_prompt or '')} 字符，兜底开关={'开' if engine.get_stats()['termAutoDegrade'] else '关'}")

    results: list[dict] = []
    try:
        for idx, path in enumerate(files, 1):
            print(f"\n[{idx}/{len(files)}] {path.name}")
            try:
                results.append(eval_file(path, args, engine, router))
            except Exception as exc:
                results.append({"file": path.name, "error": f"{type(exc).__name__}: {exc}"})
                print(f"  失败: {exc}")
    finally:
        try:
            engine.stop()
        except Exception:
            pass

    summary = summarize(results)
    passed, notes = verdict(summary, results, args)
    print_report(results, summary, notes)

    report = {
        "generatedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "config": {
            "model": args.model,
            "language": args.language,
            "terms": args.terms,
            "domain": args.domain,
            "realtime": args.realtime,
            "maxWer": args.max_wer,
        },
        "summary": summary,
        "verdict": {"passed": passed, "notes": notes},
        "results": results,
    }
    out = Path(args.report)
    if not out.is_absolute():
        out = root / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写入: {out}")
    print(f"结论: {'通过 ✓' if passed else '未通过 ✗'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
