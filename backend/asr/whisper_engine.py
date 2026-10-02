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

logger = logging.getLogger(__name__)

# 模型下载目录：优先环境变量，默认 ~/.tingkebanlv/whisper_models
def _get_model_dir() -> str:
    if env_dir := os.environ.get("WHISPER_MODEL_DIR"):
        return env_dir
    if home := os.environ.get("TINGKEBANLV_HOME"):
        return str(Path(home) / "whisper_models")
    return str(Path.home() / ".tingkebanlv" / "whisper_models")


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


_MODEL_DIR = _get_model_dir()
os.makedirs(_MODEL_DIR, exist_ok=True)
_configure_hf_environment(_MODEL_DIR)

# 模型配置
DEFAULT_MODEL = "small"  # tiny, base, small, medium, large-v3


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
MIN_SEGMENT_DURATION = 0.5   # 最短识别片段（秒）
MAX_BUFFER_DURATION = 15.0   # 最大缓冲时长（秒）
OVERLAP_DURATION = 0.2       # 片段重叠时长（秒）


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
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.on_partial = on_partial
        self.on_progress = on_progress
        # 术语热词先验：给 Whisper 一段领域术语文本，显著提升专业词拼写准确率
        self.initial_prompt = (initial_prompt or "").strip() or None

        # 多轮识别共享的"上一段文本"，让跨片段的术语拼写保持连贯
        self._last_text: Optional[str] = None

        self._model = None
        self._loading = False
        self._lock = threading.Lock()
        self._audio_buffer: deque[np.ndarray] = deque()
        self._buffer_duration = 0.0
        self._last_segment_end = 0.0
        self._total_duration = 0.0
        self._running = False
        self._pending_segments: list[np.ndarray] = []
        self._process_thread: Optional[threading.Thread] = None

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
        检查模型是否已缓存

        刻意校验文件**大小**而不只是「是否存在」：
        在「HF 镜像 + hf-xet」组合下 snapshot_download 会一边报告下载成功、
        一边留下 0 字节的 model.bin。只判断存在性会把这种损坏缓存当成可用，
        之后每次启动都在加载阶段抛 "model.bin is incomplete"。
        """
        cache_dir = Path(_MODEL_DIR) / f"models--Systran--faster-whisper-{self.model_size}"
        if not cache_dir.exists():
            return False
        snapshots = cache_dir / "snapshots"
        if not snapshots.exists():
            return False
        for snapshot in snapshots.iterdir():
            if not snapshot.is_dir():
                continue
            for bin_file in snapshot.glob("*.bin"):
                try:
                    # 真实模型至少几十 MB，1MB 阈值足以识别 0 字节/截断文件
                    if bin_file.stat().st_size > 1024 * 1024:
                        return True
                except OSError:
                    continue
        return False

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

            if not self._is_model_cached():
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

            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=ct,
                num_workers=2,
                cpu_threads=4,
                download_root=_MODEL_DIR,
            )

            self._report_progress("ready", 100)
            logger.info("Faster-Whisper 模型加载完成")

        except Exception as e:
            logger.error(f"Faster-Whisper 加载失败: {e}")
            self._model = None
            self._report_progress("error", 0)
        finally:
            self._loading = False

    def add_audio(self, audio: np.ndarray):
        """
        添加音频数据到缓冲区
        audio: float32 numpy array, 采样率 16000
        """
        with self._lock:
            self._audio_buffer.append(audio.copy())
            self._buffer_duration += len(audio) / 16000.0
            self._total_duration += len(audio) / 16000.0

            # 缓冲区达到阈值，触发识别
            if self._buffer_duration >= MIN_SEGMENT_DURATION:
                self._trigger_recognition()

    def _trigger_recognition(self):
        """触发异步识别"""
        if not self._model:
            logger.error("模型未加载，无法识别")
            return

        # 取出缓冲的音频
        audio = np.concatenate(list(self._audio_buffer))
        self._audio_buffer.clear()
        segment_start = max(0, self._total_duration - self._buffer_duration)
        duration = self._buffer_duration
        self._buffer_duration = 0.0

        # 在新线程中执行识别（避免阻塞音频流）
        t = threading.Thread(
            target=self._transcribe,
            args=(audio, segment_start, duration),
            daemon=True,
        )
        t.start()

    def _transcribe(self, audio: np.ndarray, segment_start: float, duration: float):
        """执行语音识别"""
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
                    self._last_text = segment_data.text

            elapsed = time.time() - start_time
            rtf = elapsed / max(duration, 0.01)
            logger.debug(f"识别完成: {duration:.1f}s 音频 → {elapsed:.1f}s 处理 (RTF={rtf:.2f})")

        except Exception as e:
            logger.error(f"语音识别错误: {e}")

    def flush(self):
        """等待所有待处理任务完成"""
        # 简化实现：等待一小段时间
        time.sleep(0.5)

    def set_initial_prompt(self, prompt: str):
        """
        运行时更新术语热词（用户中途添加自定义术语时可热更新，无需重启管道）

        注意：只在下一轮识别生效，不影响正在进行中的识别。
        """
        new_prompt = (prompt or "").strip() or None
        if new_prompt != self.initial_prompt:
            self.initial_prompt = new_prompt
            logger.info(f"术语热词已更新 ({len(new_prompt or '')} 字符)")

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
        """停止引擎"""
        self._running = False
        self.flush()

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def is_loading(self) -> bool:
        return self._loading
