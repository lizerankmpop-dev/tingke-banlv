"""
Faster-Whisper 语音识别引擎
提供实时语音转文字功能，支持词级时间戳
"""

import logging
import threading
import time
import os
from typing import Optional, Callable
from dataclasses import dataclass
from collections import deque
from pathlib import Path

import numpy as np

from asr.model_bootstrap import check_installed, ensure_model, model_root

logger = logging.getLogger(__name__)


def _configure_hf_environment(model_dir: str):
    """
    配置 HuggingFace 下载环境（直接决定首次启动能否下到模型）

    1) 镜像：huggingface.co 在国内网络下常常**完全不可达**
       （实测连接 10 秒超时、http 状态 000），而 hf-mirror.com 正常（0.88s）。
       原代码没有任何兜底，用户首次启动只会看到「模型下载失败」。
       默认走镜像，可用 HF_ENDPOINT 覆盖（例如有专线时指回官方站）。

    2) 关闭 Xet：huggingface-hub 默认启用 hf-xet，文件内容会走
       cas-server.xethub.hf.co，而镜像站**不代理**这个 CAS 服务，返回 401 Unauthorized。
       最坑的是：cache 模式下它会一边报「下载成功」一边留下 **0 字节的快照文件**，
       直到加载模型才报 "model.bin is incomplete: failed to read a value of size 4"。
       关掉之后走普通 HTTPS 下载，配合镜像可正常工作。

    3) HF_HOME 与 download_root 必须指向同一目录，理由见 _load_model 注释。
    """
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HOME", model_dir)


_MODEL_DIR = str(model_root())
os.makedirs(_MODEL_DIR, exist_ok=True)
_configure_hf_environment(_MODEL_DIR)

# 模型配置
# 默认 base 而非 small：实测在 32 核机器、4 秒片段下
#   small 需 RTF 1.438（跟不上实时），base 为 0.482（跟得上）
# 术语热词注入已能补回一部分专业词准确率，故优先保证实时性。
DEFAULT_MODEL = os.environ.get("TINGKEBANLV_WHISPER_MODEL", "base")


def _detect_cpu_threads() -> int:
    """
    选择 ctranslate2 的 CPU 线程数

    原实现硬编码 4，在 32 核机器上只用了 1/8 算力。实测（small 模型 / 8 秒片段）：
        4 线程 → RTF 0.738
        8 线程 → RTF 0.584
       16 线程 → RTF 0.493
    收益随线程数递减，且过多线程会与其他环节抢 CPU，故上限取 8。
    """
    env_threads = os.environ.get("TINGKEBANLV_WHISPER_THREADS")
    if env_threads:
        try:
            return max(1, int(env_threads))
        except ValueError:
            logger.warning(f"无法解析 TINGKEBANLV_WHISPER_THREADS={env_threads}，改用自动探测")
    logical = os.cpu_count() or 4
    return max(2, min(8, logical // 2))


def _detect_device() -> str:
    """自动检测最佳计算设备"""
    # 1. 环境变量强制指定
    if env_device := os.environ.get("WHISPER_DEVICE"):
        logger.info(f"使用环境变量指定的设备: {env_device}")
        return env_device

    # 2. 检查 CUDA
    try:
        import torch
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            logger.info(f"检测到 CUDA GPU: {gpu_name}")
            return "cuda"
    except ImportError:
        pass

    # 3. 检查 Apple Silicon (MPS)
    try:
        import torch
        if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
            logger.info("检测到 Apple Silicon MPS，当前降级使用 CPU（faster-whisper 对 MPS 支持不稳定）")
            return "cpu"
    except ImportError:
        pass

    logger.info("未检测到 GPU，使用 CPU")
    return "cpu"


def _detect_compute_type(device: str) -> str:
    """根据设备选择最优 compute_type"""
    if device == "cuda":
        return "float16"
    return "int8"


DEVICE = _detect_device()
COMPUTE_TYPE = _detect_compute_type(DEVICE)

# 实时处理配置
#
# MIN_SEGMENT_DURATION / MAX_BUFFER_DURATION / OVERLAP_DURATION 已移除：
# 分段由 audio/vad.py 负责，识别侧不再自己攒缓冲。那三个常量原先从未被真正使用
# （MAX_BUFFER_DURATION 的「最大缓冲」从未生效），留着只会误导后来的人。

# 待识别队列上限（背压）
# 正常跟得上时队列始终只有 1 个任务；只有在识别落后时才会积压，
# 消费者会把积压片段合并成一次调用，所以这个上限只用于兜底防内存失控。
MAX_PENDING_JOBS = int(os.environ.get("TINGKEBANLV_MAX_PENDING_JOBS", "8"))


# ---- 术语提示词「重复循环」的运行时防护 ----
#
# 背景（实测于 scripts/eval_asr_quality.py，2026-10-06，英文合成压力集）：
#   术语提示词给到 794 字符（当前默认上限）时出现「产出字幕片段数 > 送入片段数」，
#   且单次识别 RTF 突破 1 —— 这是 Whisper 重复循环的特征，同一句会被吐两遍。
#   短提示词（≤195 字符）在 5/5 用例里都没出现。
#
# 但 WER 在各档位**不单调**（66 字符档甚至最好、794 字符档最差），
# 单条素材的噪声远大于趋势 —— 所以**不能直接把默认上限调小**，
# 那会在中文场景下白白牺牲术语热词带来的识别增益（而中文长术语列表恰恰更容易触发）。
#
# 因此这段做的是**兜底**，不是改默认值：只在真的观察到循环时才缩短提示词。
AUTO_DEGRADE_ENABLED = os.environ.get("TINGKEBANLV_TERM_AUTODEGRADE", "1").lower() not in (
    "0",
    "false",
    "no",
)
# 降级后保留的长度。取 200 是因为实测 ≤195 字符的档位没出现过重复循环。
DEGRADE_PROMPT_CHARS = int(os.environ.get("TINGKEBANLV_TERM_DEGRADE_TO", "200"))
# 触发判据刻意带「最小样本量 + 比值容差」，而不是严格按单次「输出 > 输入」：
#   - 单个长片段被 Whisper 自己再切成多段是**正常**行为，单次比较会误杀；
#   - 样本太少时比值噪声极大（2 进 3 出就是 1.5 倍）。
# 宁可晚一点降级，也不要在一节正常的课上误降。
REPETITION_MIN_SAMPLES = int(os.environ.get("TINGKEBANLV_TERM_REPEAT_MIN_SAMPLES", "4"))
REPETITION_RATIO = float(os.environ.get("TINGKEBANLV_TERM_REPEAT_RATIO", "1.2"))


@dataclass
class WordTimestamp:
    word: str
    start: float  # 秒
    end: float    # 秒
    confidence: float


@dataclass
class SubtitleSegment:
    text: str
    words: list[WordTimestamp]
    start: float
    end: float
    is_final: bool


class WhisperEngine:
    """
    Faster-Whisper 识别引擎
    接收语音片段，返回带时间戳的文本
    """

    def __init__(
        self,
        model_size: str = DEFAULT_MODEL,
        device: str = DEVICE,
        compute_type: str = COMPUTE_TYPE,
        language: str = "auto",
        on_partial: Optional[Callable[[str, list[WordTimestamp]], None]] = None,
        on_progress: Optional[Callable[[str, float], None]] = None,
        initial_prompt: Optional[str] = None,
        cpu_threads: Optional[int] = None,
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.cpu_threads = cpu_threads or _detect_cpu_threads()
        self.on_partial = on_partial
        self.on_progress = on_progress
        # 术语热词先验：给 Whisper 一段领域术语文本，显著提升专业词拼写准确率
        self.initial_prompt = (initial_prompt or "").strip() or None

        # 多轮识别共享的"上一段文本"，让跨片段的术语拼写保持连贯
        self._last_text: Optional[str] = None

        self._model = None
        self._loading = False
        self._lock = threading.Lock()
        self._total_duration = 0.0
        self._running = False

        # ---- 识别任务队列（背压所在）----
        # 原实现每来一段音频就裸起一个线程去 transcribe，既无队列上限也无串行化：
        # 一旦识别速度跟不上音频速度（实测 small 模型在 2~4 秒片段上 RTF>1），
        # 线程会无界堆积 → CPU 被抢死、字幕乱序、内存持续上涨直至 OOM。
        # 现在改为「单消费者 + 有界队列」：
        #   - 正常情况下队列里只有 1 个任务，字幕照常即时产出
        #   - 跟不上时任务会积压，消费者把积压片段**合并成一次调用**
        #     （单次调用有约 4 秒固定开销，合并远优于丢弃）
        self._jobs: deque[tuple[np.ndarray, float, float]] = deque()
        self._jobs_cv = threading.Condition()
        self._stop_worker = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._busy = False             # 是否正在做一次识别（供 flush 判断）
        self._merged_jobs = 0          # 合并过几次（用于观测是否在降级）
        self._dropped_jobs = 0

        # ---- 术语提示词重复循环防护（见模块顶部 AUTO_DEGRADE_* 注释）----
        self._auto_degrade = AUTO_DEGRADE_ENABLED
        self._prompt_degraded = False          # 是否已因检测到重复而缩短过提示词
        self._prompt_original: Optional[str] = None   # 缩短前的完整提示词（供恢复）
        self._stat_in_segments = 0             # 累计送入识别的片段数（比值分母）
        self._stat_out_segments = 0            # 累计产出字幕的片段数（比值分子）
        self._repetition_events = 0            # 检测到重复循环的次数

        # 后台异步加载模型（不阻塞事件循环）
        self._loading = True
        self._load_thread = threading.Thread(target=self._load_model, daemon=True)
        self._load_thread.start()

    def _report_progress(self, stage: str, progress: float):
        """通过回调报告加载进度"""
        if self.on_progress:
            self.on_progress(stage, progress)
        logger.info(f"模型加载进度: {stage} {progress:.0f}%")

    def _is_model_cached(self) -> bool:
        """
        检查本地是否已有可用模型

        校验逻辑集中在 asr/model_bootstrap.check_installed，这里只做转发：
        两处各写一份「什么算缓存可用」迟早会漂移，而它们漂移的后果是
        「安装包说内置了模型、加载时却以为没有又去下载」。

        刻意校验文件**大小**而不只是「是否存在」：
        在「HF 镜像 + hf-xet」组合下 snapshot_download 会一边报告下载成功、
        一边留下 0 字节的 model.bin。只判断存在性会把这种损坏缓存当成可用，
        之后每次启动都在加载阶段抛 "model.bin is incomplete"。
        """
        return bool(check_installed(self.model_size)["ok"])

    def _monitor_download(self, stop_event: threading.Event):
        """监控下载进度，通过缓存目录大小估算百分比"""
        # 数值来自对 Systran/faster-whisper-* 的实际测量（model.bin + tokenizer + config）：
        #   tiny 72MB / base 139MB / small 461MB / medium 1458MB / large-v3 约3.1GB
        # 注意该值就是进度条的**分母**：原实现给 small 写 2GB（真实 464MB）、
        # medium 写 5GB（真实 1.46GB），导致进度条长期封顶在 24% 左右再突跳完成。
        expected_sizes = {
            "tiny": 75 * 1024 * 1024,
            "base": 141 * 1024 * 1024,
            "small": 464 * 1024 * 1024,
            "medium": 1460 * 1024 * 1024,
            "large-v3": 3100 * 1024 * 1024,
        }
        expected = expected_sizes.get(self.model_size, 464 * 1024 * 1024)
        cache_dir = Path(_MODEL_DIR) / f"models--Systran--faster-whisper-{self.model_size}"

        while not stop_event.is_set():
            try:
                if cache_dir.exists():
                    # 只统计 blobs/：在不支持符号链接的 Windows 上，
                    # snapshots/ 里会有一份实拷贝，两份都算会让进度显示翻倍
                    blobs = cache_dir / "blobs"
                    if blobs.is_dir():
                        total_size = sum(f.stat().st_size for f in blobs.rglob("*") if f.is_file())
                    else:
                        total_size = sum(f.stat().st_size for f in cache_dir.rglob("*") if f.is_file())
                    progress = min(total_size / expected, 0.99) * 100
                    self._report_progress("downloading", progress)
            except Exception:
                pass
            stop_event.wait(2)

    def _load_model(self):
        """加载 Faster-Whisper 模型（在后台线程中执行）"""
        try:
            from faster_whisper import WhisperModel

            # 启用高速下载
            os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "1")

            # 先看安装包内置的模型种子：
            # 首启即可离线使用，且能自动修复损坏的缓存（不联网）
            bootstrap = ensure_model(self.model_size)
            if bootstrap["action"] in ("seeded", "repaired"):
                logger.info(f"已从内置模型播种: {bootstrap['detail']}")
            elif not bootstrap["ok"]:
                logger.info(f"内置模型不可用: {bootstrap['detail']}")

            cached = self._is_model_cached()

            if not cached:
                # 需要下载
                self._report_progress("downloading", 0)
                logger.info(f"模型 {self.model_size} 未缓存，开始下载...")

                # 启动进度监控线程
                monitor_stop = threading.Event()
                monitor = threading.Thread(
                    target=self._monitor_download,
                    args=(monitor_stop,),
                    daemon=True,
                )
                monitor.start()

                try:
                    import huggingface_hub
                    # cache_dir 必须与下面 WhisperModel(download_root=_MODEL_DIR) 一致。
                    # 原实现没传 cache_dir，配合 HF_HOME=_MODEL_DIR 会落到
                    # <root>/hub/models--... ，而加载与缓存检查都看
                    # <root>/models--... ，于是首次启动把同一个模型下了两遍：
                    # 预下载一份（没人用），WhisperModel 再下一份。
                    huggingface_hub.snapshot_download(
                        f"Systran/faster-whisper-{self.model_size}",
                        cache_dir=_MODEL_DIR,
                        allow_patterns=["*.bin", "*.json", "*.txt"],
                    )
                except Exception as e:
                    logger.warning(f"huggingface_hub 预下载失败，将使用 WhisperModel 内置下载: {e}")

                monitor_stop.set()
            else:
                logger.info("模型已缓存，跳过下载")

            self._report_progress("loading", 0)
            logger.info(f"加载 Faster-Whisper 模型: {self.model_size} ({self.device}/{self.compute_type})")

            # 自动选择 compute_type
            ct = self.compute_type
            if self.device == "cpu" and ct == "float16":
                ct = "int8"

            self._model = self._instantiate_model(WhisperModel, ct, cached)

            self._report_progress("ready", 100)
            logger.info("Faster-Whisper 模型加载完成")

        except Exception as e:
            logger.error(f"Faster-Whisper 加载失败: {e}")
            self._model = None
            self._report_progress("error", 0)
        finally:
            self._loading = False

    def _instantiate_model(self, whisper_model_cls, compute_type: str, cached: bool):
        """
        真正构造 WhisperModel

        cached=True 时**强制离线加载**（local_files_only=True）：
        安装包内置了 base 模型，用户首启可能完全没网 —— 此时
        faster-whisper 默认会去 HF 校验元数据，网络不通就直接失败，
        等于把「内置模型」这个卖点浪费了。
        只有离线加载真的报错（例如缓存被改坏）时才回退到联网，
        让它有机会自己修复。
        """
        common = dict(
            device=self.device,
            compute_type=compute_type,
            # num_workers>1 会创建多份模型副本（每份数百 MB 内存），
            # 那是为批量离线转写准备的；流式场景一次只处理一段，用 1 即可
            num_workers=1,
            cpu_threads=self.cpu_threads,
            download_root=_MODEL_DIR,
        )

        if not cached:
            return whisper_model_cls(self.model_size, **common)

        try:
            return whisper_model_cls(self.model_size, local_files_only=True, **common)
        except Exception as exc:
            logger.warning(
                f"离线加载模型失败（{type(exc).__name__}: {exc}），回退联网加载以尝试修复缓存"
            )
            # 再给一次机会：从内置种子修复后重试
            repaired = ensure_model(self.model_size, deep_check=True)
            if repaired["action"] in ("seeded", "repaired"):
                logger.info(f"已用内置模型修复缓存: {repaired['detail']}")
            return whisper_model_cls(self.model_size, **common)

    def add_audio(self, audio: np.ndarray):
        """
        提交一段音频用于识别

        audio: float32 numpy array, 采样率 16000
        由上游 VAD 切出的独立语音片段，自带起止时间。
        """
        if audio is None or len(audio) == 0:
            return

        duration = len(audio) / 16000.0
        with self._lock:
            segment_start = self._total_duration
            self._total_duration += duration

        with self._jobs_cv:
            self._jobs.append((audio.copy(), segment_start, duration))
            # 背压上限：极端情况下（长时间跟不上）也不让队列无限增长。
            # 丢弃最旧的片段而不是最新的 —— 越新的内容对用户越有价值。
            while len(self._jobs) > MAX_PENDING_JOBS:
                self._jobs.popleft()
                self._dropped_jobs += 1
                if self._dropped_jobs % 5 == 1:
                    logger.warning(
                        f"识别积压严重，已丢弃 {self._dropped_jobs} 个片段 "
                        f"（当前模型 {self.model_size}，可考虑换更小的模型）"
                    )
            self._jobs_cv.notify()

        self._ensure_worker()

    def _ensure_worker(self):
        """惰性启动唯一的识别消费者线程"""
        if self._worker is not None and self._worker.is_alive():
            return
        self._stop_worker.clear()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    def _worker_loop(self):
        """
        唯一的识别消费者

        取出当前所有待处理片段；多于一个时合并成一次调用。
        合并是有意的降级策略：每次 transcribe 有约 4 秒固定开销
        （Whisper 按 30 秒窗口处理，短输入会被补齐），
        把两段 4 秒合成一次 8 秒调用，比串行两次调用快得多。
        """
        while not self._stop_worker.is_set():
            with self._jobs_cv:
                while not self._jobs and not self._stop_worker.is_set():
                    self._jobs_cv.wait(0.2)
                if self._stop_worker.is_set():
                    return
                if not self._model:
                    # 模型还没加载完：保留队列，等下一轮
                    self._jobs_cv.wait(0.5)
                    continue
                jobs = list(self._jobs)
                self._jobs.clear()
                # _busy 必须与「清空队列」在同一个临界区内置位。
                # 观察者（flush()、以及任何轮询 backlog 的调用方）的判据是
                # 「队列空 **且** 不忙」；若等到下面 _merge_jobs() 跑完再置位，
                # merge 那段时间就是一个「队列已空 + 还不忙」的假空闲窗口 ——
                # flush() 会据此提前返回，于是**停止听课时最后一段字幕被丢掉**。
                # 这正是 flush() 注释里说要修的那个问题，之前只修了 sleep 那一半。
                self._busy = True
                self._jobs_cv.notify_all()

            if len(jobs) > 1:
                self._merged_jobs += 1
                logger.info(f"识别落后，合并 {len(jobs)} 个片段为一次调用（第 {self._merged_jobs} 次）")

            audio, start, duration = self._merge_jobs(jobs)
            try:
                self._transcribe(audio, start, duration, len(jobs))
            finally:
                with self._jobs_cv:
                    self._busy = False
                    self._jobs_cv.notify_all()

    @staticmethod
    def _merge_jobs(jobs: list) -> tuple:
        """
        把多个片段合并成一段音频

        片段之间按时间轴补零，保证 Whisper 输出的时间戳仍对应真实时间，
        否则字幕时间会整体前移、与课件进度对不上。
        """
        if len(jobs) == 1:
            return jobs[0]

        parts: list[np.ndarray] = []
        cursor = jobs[0][1]
        for audio, start, duration in jobs:
            gap = start - cursor
            if gap > 0.05:
                parts.append(np.zeros(int(gap * 16000), dtype=np.float32))
            parts.append(audio)
            cursor = start + duration

        merged = np.concatenate(parts)
        total = sum(d for _, _, d in jobs) + sum(
            max(0.0, jobs[i][1] - (jobs[i - 1][1] + jobs[i - 1][2])) for i in range(1, len(jobs))
        )
        return merged, jobs[0][1], total

    def _transcribe(self, audio: np.ndarray, segment_start: float, duration: float, job_count: int = 1):
        """
        执行语音识别

        job_count：本次调用实际合并了几个输入片段，仅用于重复循环检测
        （见模块顶部 AUTO_DEGRADE_* 的说明）。
        """
        produced = 0   # 本次产出的**非空**字幕片段数（空文本不会变成字幕，不计入）
        try:
            start_time = time.time()

            # 确保音频是 float32 格式
            if audio.dtype != np.float32:
                audio = audio.astype(np.float32)

            # 调用 Faster-Whisper
            lang = None if self.language == "auto" else self.language
            segments, info = self._model.transcribe(
                audio,
                language=lang,                # auto / en / zh / ja / ...
                beam_size=5,
                word_timestamps=True,         # 启用词级时间戳
                vad_filter=False,             # 我们已有 VAD
                vad_parameters=dict(
                    min_silence_duration_ms=200,
                ),
                # 术语热词注入：领域术语 + 课程自定义术语
                initial_prompt=self._build_prompt(),
                condition_on_previous_text=True,
                no_speech_threshold=0.4,
            )

            detected_lang = info.language
            logger.debug(f"检测语言: {detected_lang}, 概率: {info.language_probability:.2f}")

            # 处理识别结果
            for segment in segments:
                words = []
                if segment.words:
                    for w in segment.words:
                        words.append(WordTimestamp(
                            word=w.word.strip(),
                            start=segment_start + w.start,
                            end=segment_start + w.end,
                            confidence=w.probability,
                        ))

                segment_data = SubtitleSegment(
                    text=segment.text.strip(),
                    words=words,
                    start=segment_start + segment.start,
                    end=segment_start + segment.end,
                    is_final=True,
                )

                # 回调
                if self.on_partial:
                    self.on_partial(segment_data.text, words)

                # 记录本段文本，供下一轮识别作为连贯性提示
                if segment_data.text:
                    produced += 1
                    self._last_text = segment_data.text

            elapsed = time.time() - start_time
            rtf = elapsed / max(duration, 0.01)
            logger.debug(f"识别完成: {duration:.1f}s 音频 → {elapsed:.1f}s 处理 (RTF={rtf:.2f})")

        except Exception as e:
            logger.error(f"语音识别错误: {e}")
        finally:
            # 计数放 finally：即使识别抛错也要把「送入量」计入分母，
            # 否则分母长期偏小会让比值虚高、出现假触发。
            self._note_segment_counts(job_count, produced)

    def _note_segment_counts(self, incoming: int, outgoing: int):
        """
        累计「送入片段数」与「产出字幕片段数」，必要时降级术语提示词

        判据用**累计比值**而不是单次比较：单次「输出 > 输入」在长片段上属正常行为
        （Whisper 会把长音频自己再切），且样本少时比值噪声大。详见模块顶部注释。
        """
        self._stat_in_segments += incoming
        self._stat_out_segments += outgoing

        if not self._auto_degrade or self._prompt_degraded:
            return

        prompt = self.initial_prompt or ""
        if len(prompt) <= DEGRADE_PROMPT_CHARS:
            return          # 本来就短，降无可降
        if self._stat_in_segments < REPETITION_MIN_SAMPLES:
            return          # 样本不足，先攒够再判
        if self._stat_out_segments <= self._stat_in_segments * REPETITION_RATIO:
            return          # 还在容差内，属于正常波动

        self._repetition_events += 1
        self._prompt_original = prompt
        self.initial_prompt = prompt[:DEGRADE_PROMPT_CHARS]
        self._prompt_degraded = True
        ratio = self._stat_out_segments / max(self._stat_in_segments, 1)
        logger.warning(
            "[WARN] 检测到字幕重复循环，自动缩减术语提示词长度: "
            f"{len(prompt)} → {len(self.initial_prompt)} 字符"
            f"（累计送入 {self._stat_in_segments} 段 → 产出 {self._stat_out_segments} 条字幕，"
            f"比值 {ratio:.2f} > 阈值 {REPETITION_RATIO}）。"
            "设 TINGKEBANLV_TERM_AUTODEGRADE=0 可关闭该保护"
        )

    def flush(self, timeout: float = 8.0):
        """
        等待队列中的识别任务处理完

        原实现只是 sleep(0.5)，无论队列里还有多少任务都会「假装等完了」，
        停止听课时就可能丢掉最后几段字幕。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._jobs_cv:
                if not self._jobs and not self._busy:
                    return
                self._jobs_cv.wait(0.1)
        with self._jobs_cv:
            remaining = len(self._jobs)
        if remaining:
            logger.warning(f"flush 超时，仍有 {remaining} 个片段未识别")

    def set_initial_prompt(self, prompt: str):
        """
        运行时更新术语热词（用户中途添加自定义术语时可热更新，无需重启管道）

        注意：只在下一轮识别生效，不影响正在进行中的识别。

        若已因重复循环降级过，则**保持降级**：`pipeline.update_terminology()`
        每次热更新都会推完整提示词，若在这里恢复成完整版，就会变成
        「恢复 → 再次触发循环 → 再降级」的来回抖动，比一直保持降级更糟。
        要恢复请显式调用 `restore_full_prompt()`。
        """
        new_prompt = (prompt or "").strip() or None

        if self._prompt_degraded and new_prompt and len(new_prompt) > DEGRADE_PROMPT_CHARS:
            self._prompt_original = new_prompt
            logger.debug(
                f"术语提示词处于降级保护中，继续使用 {len(self.initial_prompt or '')} 字符版本"
                f"（本次收到 {len(new_prompt)} 字符，已留存备用）"
            )
            return

        if new_prompt != self.initial_prompt:
            self.initial_prompt = new_prompt
            logger.info(f"术语热词已更新 ({len(new_prompt or '')} 字符)")

    def set_auto_degrade(self, enabled: bool):
        """
        运行时开关「术语提示词自动降级」（对应设置面板同名开关，默认开启）

        关闭只影响**之后**的检测；已降级过的提示词不会自动恢复 ——
        要恢复请显式调用 `restore_full_prompt()`，理由同上：自动恢复会来回抖动。
        """
        self._auto_degrade = bool(enabled)
        logger.info(f"术语提示词自动降级已{'开启' if self._auto_degrade else '关闭'}")

    def restore_full_prompt(self):
        """
        解除降级保护，恢复被缩短前的完整提示词（换课程 / 重开管道时调用）

        计数一并清零，重新观察若干片段后再决定是否需要再次降级。
        """
        if self._prompt_original:
            self.initial_prompt = self._prompt_original
        self._prompt_original = None
        self._prompt_degraded = False
        self._stat_in_segments = 0
        self._stat_out_segments = 0
        logger.info(f"术语提示词已恢复完整版本 ({len(self.initial_prompt or '')} 字符)")

    def _build_prompt(self) -> Optional[str]:
        """
        组合 initial_prompt：术语热词 + 上一段识别文本

        Whisper 的 initial_prompt 只有一段，所以这里做拼接；
        上一段文本放在后面且截断，避免它把术语先验挤出去。
        """
        parts = []
        if self.initial_prompt:
            parts.append(self.initial_prompt)
        if self._last_text:
            # 只取尾部，保持最近语境；再长会挤占音频上下文
            parts.append(self._last_text[-200:])

        if not parts:
            return None
        return " ".join(parts)[:1000]

    def stop(self):
        """停止引擎：先把手头积压的片段识别完，再收掉消费者线程"""
        self._running = False
        self.flush(timeout=10.0)
        self._stop_worker.set()
        with self._jobs_cv:
            self._jobs.clear()
            self._jobs_cv.notify_all()
        if self._worker is not None:
            self._worker.join(timeout=3)

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def is_loading(self) -> bool:
        return self._loading

    def get_stats(self) -> dict:
        """
        运行时统计（供 /api/health 与排障）

        重点看 backlog 与 merged：
          - backlog 持续 > 0 → 识别跟不上音频速度
          - merged > 0       → 已经进入「合并片段」的降级状态，字幕会更粗
          - dropped > 0      → 已开始丢内容，必须减小模型或调大 cpu_threads
          - termPromptDegraded=true → 检测到过字幕重复循环并已自动缩短术语提示词
        """
        with self._jobs_cv:
            backlog = len(self._jobs)
            busy = self._busy
        return {
            "model": self.model_size,
            "device": self.device,
            "computeType": self.compute_type,
            "cpuThreads": self.cpu_threads,
            "backlog": backlog,
            "busy": busy,
            "mergedCalls": self._merged_jobs,
            "droppedSegments": self._dropped_jobs,
            "audioSeconds": round(self._total_duration, 1),
            # ---- 术语提示词健康度（供设置面板展示降级状态）----
            "termPromptChars": len(self.initial_prompt or ""),
            "termPromptDegraded": self._prompt_degraded,
            "termAutoDegrade": self._auto_degrade,
            "repetitionDetected": self._repetition_events > 0,
            "inSegments": self._stat_in_segments,
            "outSegments": self._stat_out_segments,
        }
