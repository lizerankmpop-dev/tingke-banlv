"""
华为云实时语音转写后端（骨架）

## 本次范围

只提供与 `WhisperEngine` 相同的公开生命周期，让 `ASRPipeline` 可以无感替换后端；
**没有猜测**华为云 SIS 的真实 endpoint、AK/SK 签名算法和 WebSocket 事件格式。
真实协议适配全部集中在 `HuaweiSISWebSocketTransport`，并用 TODO 标明。

## 为什么不直接写死协议

任务书要求「先骨架、拿到官方文档/密钥后再补协议」。在没有官方文档的情况下
编造签名和消息格式，联调时会把「协议不对」伪装成「密钥不对」，浪费排查时间；
更糟的是把猜测写进生产路径，后续没人敢改。因此默认 transport 只做配置校验，
配置齐全后明确报「协议尚未接入」，绝不发起任何网络连接。

## 密钥纪律

AK/SK/临时 token 只从环境变量读取，不写进代码、日志、`repr` 或 `get_stats()`。
"""

import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

import numpy as np

from asr.whisper_engine import WordTimestamp

logger = logging.getLogger(__name__)

# 环境变量名。测试与文档共用这一份，避免文案漂移。
ENV_AK = "HUAWEI_ASR_AK"
ENV_SK = "HUAWEI_ASR_SK"
ENV_REGION = "HUAWEI_ASR_REGION"
ENV_PROJECT_ID = "HUAWEI_ASR_PROJECT_ID"
ENV_ENDPOINT = "HUAWEI_ASR_ENDPOINT"
ENV_AUTH_TOKEN = "HUAWEI_ASR_AUTH_TOKEN"


class HuaweiASRConfigError(RuntimeError):
    """配置缺失/非法。消息可直接展示给用户。"""


@dataclass
class HuaweiASRConfig:
    """华为云 ASR 配置（默认从环境变量读取）

    `HUAWEI_ASR_AUTH_TOKEN` 是预留的临时 token/预签名入口：拿到密钥但还没接
    AK/SK 签名时，可先用它联调。存在 token 时不再要求 AK/SK。

    `region` / `project_id` 目前只读取保存，是否参与真实鉴权/URL 拼接待官方
    协议确认，因此**不纳入 `missing`**，避免骨架阶段因非必需项阻断启动。
    """

    ak: str = field(default="", repr=False)
    sk: str = field(default="", repr=False)
    region: str = ""
    project_id: str = ""
    endpoint: str = ""
    auth_token: str = field(default="", repr=False)

    @classmethod
    def from_env(cls, environ: Optional[dict] = None) -> "HuaweiASRConfig":
        env = os.environ if environ is None else environ

        def read(name: str) -> str:
            return str(env.get(name) or "").strip()

        return cls(
            ak=read(ENV_AK),
            sk=read(ENV_SK),
            region=read(ENV_REGION),
            project_id=read(ENV_PROJECT_ID),
            endpoint=read(ENV_ENDPOINT),
            auth_token=read(ENV_AUTH_TOKEN),
        )

    @property
    def missing(self) -> list[str]:
        """缺失的**必需**环境变量名（只返回名字，不返回任何值）"""
        missing: list[str] = []
        if not self.endpoint:
            missing.append(ENV_ENDPOINT)
        if not self.auth_token:
            if not self.ak:
                missing.append(ENV_AK)
            if not self.sk:
                missing.append(ENV_SK)
        return missing

    @property
    def configured(self) -> bool:
        return not self.missing

    def describe_error(self) -> str:
        """配置缺失时的中文错误。密钥只以变量名出现。"""
        if self.configured:
            return ""
        return (
            "华为云实时语音转写配置缺失："
            + "、".join(self.missing)
            + "。请通过环境变量提供（没接 AK/SK 签名时可先用 "
            + ENV_AUTH_TOKEN
            + " 临时 token）；密钥只放环境变量，不要写入代码或提交到 git。"
        )

    def require(self) -> "HuaweiASRConfig":
        if not self.configured:
            raise HuaweiASRConfigError(self.describe_error())
        return self


class HuaweiASRTransport(Protocol):
    """协议适配层接口（测试可注入 fake 实现）

    `on_event` 接收本仓库的**规范化事件**（见 `HuaweiASRBackend._handle_event`）。
    真实 SIS JSON → 规范化事件的转换属于 transport 的职责；本阶段没有官方
    协议，不做任何解析。
    """

    def start(self, on_event: Callable[[dict], None]) -> None:
        ...

    def send_audio(self, data: bytes) -> None:
        ...

    def close(self) -> None:
        ...


class HuaweiSISWebSocketTransport:
    """华为云 SIS 实时语音转写 WebSocket 适配**占位**

    ⚠️ 以下内容全部**待官方文档确认**，本文件刻意不猜测、不实现：

    TODO(huawei-sis-1): 真实 WebSocket endpoint —— 需要官方给出 region/project
        维度的 URL 模板；`HuaweiASRConfig.endpoint` 只作为联调入口原样保存。
    TODO(huawei-sis-2): AK/SK 签名（或 `HUAWEI_ASR_AUTH_TOKEN` 预签名）——
        包括签名算法、时间戳、请求头/查询参数位置。
    TODO(huawei-sis-3): 音频帧与事件格式 —— 包括帧大小、二进制/文本帧、开始/结束
        指令、partial/final 事件字段，以及词级时间戳口径（相对/绝对）。
    TODO(huawei-sis-4): 心跳、重连、限流与服务端错误码映射。

    在 TODO 补齐前，`start()` 只校验配置并抛出明确的 `NotImplementedError`，
    不建立任何 socket 连接。
    """

    def __init__(self, config: HuaweiASRConfig):
        self._config = config
        self._closed = False

    def start(self, on_event: Callable[[dict], None]) -> None:
        self._config.require()
        raise NotImplementedError(
            "华为云 SIS 实时语音转写协议尚未接入：真实 endpoint、AK/SK/Token 签名"
            "与事件格式仍需官方文档确认。本次骨架没有猜测协议，也未发起网络连接。"
        )

    def send_audio(self, data: bytes) -> None:
        raise RuntimeError("华为云 SIS 实时语音转写连接尚未建立")

    def close(self) -> None:
        self._closed = True


class HuaweiASRBackend:
    """华为云实时语音转写后端（与 `WhisperEngine` 同生命周期）

    公开接口与本地引擎一致，`ASRPipeline` 只依赖这些成员：
    `is_loaded` / `is_loading` / `backend_name` / `last_error`、`add_audio()`、
    `set_initial_prompt()`、`set_simp_mode()`、`set_auto_degrade()`、
    `get_stats()`、`flush()`、`stop()`。

    线程模型：
      · 构造时后台线程负责 `transport.start()` 并持续消费音频队列；
      · `add_audio()` 把 float32 16k mono 转成 int16 PCM，按 200ms 分块入队；
      · 有界队列满时丢**最旧**的块（越新的内容对用户越有价值），并计数；
      · `stop()` 先尽力发完已入队音频，再停止线程并关闭 transport，可重复调用。
    """

    backend_name = "huawei-asr"

    # 200ms @16k。真实 SIS 对帧长的要求待文档确认，先按通用实时流粒度切分。
    CHUNK_SAMPLES = 3200
    # 有界队列上限（约 12.8 秒音频）。正常实时流不会积压，仅兜底防内存失控。
    MAX_PENDING_CHUNKS = 64

    def __init__(
        self,
        language: str = "auto",
        on_partial: Optional[Callable[[str, list[WordTimestamp]], None]] = None,
        on_progress: Optional[Callable[[str, float], None]] = None,
        initial_prompt: Optional[str] = None,
        prompt_sections: Optional[list[tuple[str, list[str]]]] = None,
        simp_mode: str = "auto-simplified",
        on_language_decided: Optional[Callable[[str], None]] = None,
        config: Optional[HuaweiASRConfig] = None,
        transport: Optional[HuaweiASRTransport] = None,
        **kwargs,
    ):
        # 本地引擎专属参数（model_size/device/compute_type/cpu_threads）由工厂原样
        # 传入，云端后端不需要，统一吃掉，保证切换后端时调用点不必改。
        self.model_size = kwargs.get("model_size", "huawei-sis")
        self.device = kwargs.get("device", "cloud")
        self.compute_type = kwargs.get("compute_type", "cloud")

        self.language = language or "auto"
        self.on_partial = on_partial
        self.on_progress = on_progress
        self.on_language_decided = on_language_decided

        # 以下三项与本地引擎同名保存，仅为兼容 pipeline 的热更新调用。
        # 真实 SIS 是否支持热词/简繁提示，待官方协议确认；本阶段不发往云端。
        self.initial_prompt = (initial_prompt or "").strip() or None
        self._prompt_sections = list(prompt_sections) if prompt_sections else None
        self.simp_mode = simp_mode or "auto-simplified"
        self._auto_degrade = True

        self._config = config or HuaweiASRConfig.from_env()
        self._transport = transport
        self.last_error: Optional[str] = None

        self._state_lock = threading.Lock()
        self._total_duration = 0.0   # 已接收音频累计秒数（时间戳基准/统计）
        self._sent_duration = 0.0    # 已发送音频累计秒数
        self._is_loading = False
        self._is_loaded = False
        self._chunks_sent = 0
        self._dropped_chunks = 0
        self._events = 0

        # 有界队列 + 条件变量（与 WhisperEngine 的作业队列同一套模式）
        self._chunks: deque[bytes] = deque(maxlen=self.MAX_PENDING_CHUNKS)
        self._chunks_cv = threading.Condition()
        self._busy = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        if not self._config.configured:
            # 配置缺失：不启动线程、不连接、不排队，错误直接给上层展示。
            self.last_error = self._config.describe_error()
            logger.error(self.last_error)
            return

        self._is_loading = True
        self._thread = threading.Thread(
            target=self._run,
            name="huawei-asr-backend",
            daemon=True,
        )
        self._thread.start()

    # ---- 公开状态 ----

    @property
    def is_loaded(self) -> bool:
        with self._state_lock:
            return self._is_loaded

    @property
    def is_loading(self) -> bool:
        with self._state_lock:
            return self._is_loading

    @property
    def is_running(self) -> bool:
        """后台线程是否仍在运行（测试与排障用）"""
        return self._thread is not None and self._thread.is_alive()

    # ---- 音频输入 ----

    def add_audio(self, audio: np.ndarray):
        """提交一段 float32 16k mono 音频（VAD 切出的独立片段）"""
        if audio is None or len(audio) == 0:
            return

        samples = np.asarray(audio)
        if samples.ndim > 1:
            samples = samples.reshape(-1)
        if samples.dtype != np.float32:
            samples = samples.astype(np.float32)

        with self._state_lock:
            self._total_duration += len(samples) / 16000.0

        if self._stop_event.is_set():
            with self._state_lock:
                self._dropped_chunks += 1
            return

        # float32 [-1, 1] → int16 PCM。先截断再缩放，避免溢出回绕产生爆音。
        pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
        for offset in range(0, len(pcm), self.CHUNK_SAMPLES):
            chunk = pcm[offset: offset + self.CHUNK_SAMPLES].tobytes()
            with self._chunks_cv:
                if len(self._chunks) >= self.MAX_PENDING_CHUNKS:
                    # deque(maxlen=...) 会在 append 时自动丢最旧的一块
                    with self._state_lock:
                        self._dropped_chunks += 1
                    if self._dropped_chunks % 10 == 1:
                        logger.warning(
                            f"华为云 ASR 发送积压，已丢弃 {self._dropped_chunks} 个音频块"
                        )
                self._chunks.append(chunk)
                self._chunks_cv.notify()

    # ---- 兼容本地引擎的热更新接口 ----

    def set_initial_prompt(
        self,
        prompt: str,
        sections: Optional[list[tuple[str, list[str]]]] = None,
    ):
        """保存术语提示词（云端是否支持热词待协议确认，本阶段不发送）"""
        self.initial_prompt = (prompt or "").strip() or None
        self._prompt_sections = list(sections) if sections else None

    def set_simp_mode(self, mode: str):
        self.simp_mode = mode or "auto-simplified"

    def set_auto_degrade(self, enabled: bool):
        self._auto_degrade = bool(enabled)

    # ---- 后台线程 ----

    def _run(self):
        transport: Optional[HuaweiASRTransport] = None
        try:
            transport = self._transport or HuaweiSISWebSocketTransport(self._config)
            with self._state_lock:
                self._transport = transport
            transport.start(self._handle_event)

            with self._state_lock:
                self._is_loading = False
                self._is_loaded = True
            logger.info("华为云 ASR 传输层已连接")

            self._consume(transport)
        except Exception as exc:                      # noqa: BLE001 —— 统一转成 last_error
            self._record_failure(exc)
        finally:
            if transport is not None:
                try:
                    transport.close()
                except Exception as exc:              # noqa: BLE001 —— 关闭失败不影响退出
                    logger.warning(f"华为云 ASR 传输层关闭失败：{exc}")
            with self._state_lock:
                self._is_loading = False
                self._is_loaded = False
            with self._chunks_cv:
                self._busy = False
                self._chunks_cv.notify_all()

    def _consume(self, transport: HuaweiASRTransport):
        while not self._stop_event.is_set():
            with self._chunks_cv:
                while not self._chunks and not self._stop_event.is_set():
                    self._chunks_cv.wait(0.2)
                if self._stop_event.is_set():
                    return
                chunk = self._chunks.popleft()
                # 与出队同临界区置位：避免 flush() 看到「队列空 + 不忙」的假空闲窗口
                self._busy = True

            try:
                transport.send_audio(chunk)
            except Exception as exc:                  # noqa: BLE001 —— 发送失败即终止
                with self._chunks_cv:
                    self._busy = False
                    self._chunks_cv.notify_all()
                self._record_failure(exc, prefix="华为云语音识别发送音频失败")
                return

            with self._state_lock:
                self._chunks_sent += 1
                self._sent_duration += self.CHUNK_SAMPLES / 16000.0
            with self._chunks_cv:
                self._busy = False
                self._chunks_cv.notify_all()

    def _record_failure(self, exc: Exception, prefix: Optional[str] = None):
        # 先落状态再写 last_error：观察者看到错误文案时，状态必须已经是「未加载」，
        # 否则会存在「已报错但 is_loading 仍为 True」的竞态窗口。
        with self._state_lock:
            self._is_loading = False
            self._is_loaded = False
        message = str(exc).strip() or exc.__class__.__name__
        if prefix:
            self.last_error = f"{prefix}（{type(exc).__name__}）：{message}"
        elif isinstance(exc, (HuaweiASRConfigError, NotImplementedError)):
            self.last_error = message
        else:
            self.last_error = f"华为云语音识别连接失败（{type(exc).__name__}）：{message}"
        logger.error(self.last_error)

    # ---- 结果事件 ----

    def _handle_event(self, event: dict):
        """把 transport 的规范化事件转成 `on_partial(text, words)`

        规范化事件格式（本仓库内部约定，真实 SIS JSON 映射待协议确认）::

            {"type": "result" | "partial",
             "text": "识别文本",
             "words": [{"word": "词", "start": 0.1, "end": 0.3, "confidence": 0.9}]}

        `words` 可为空；词级时间戳缺失时按「当前累计音频时长」兜底。真实 SIS
        事件里的时间口径（相对/绝对）尚未确认，因此这里只做原样透传 + 兜底，
        不做任何无依据的偏移换算。
        """
        if not isinstance(event, dict):
            logger.warning(f"忽略无法识别的华为云 ASR 事件类型：{type(event).__name__}")
            return

        event_type = str(event.get("type") or "result").lower()
        if event_type == "error":
            self.last_error = str(event.get("message") or "华为云语音识别返回错误")
            logger.error(self.last_error)
            return
        if event_type not in ("result", "partial"):
            return

        text = str(event.get("text") or "").strip()
        if not text:
            return

        with self._state_lock:
            fallback_seconds = self._total_duration
        raw_words = event.get("words")
        words: list[WordTimestamp] = []
        if isinstance(raw_words, list):
            for item in raw_words:
                word = self._coerce_word(item, fallback_seconds)
                if word is not None:
                    words.append(word)

        with self._state_lock:
            self._events += 1

        if self.on_partial:
            try:
                self.on_partial(text, words)
            except Exception as exc:                  # noqa: BLE001 —— 回调失败不能拖垮收包
                logger.warning(f"华为云 ASR 结果回调失败（不影响连接）：{exc}")

    @staticmethod
    def _coerce_word(item, fallback_seconds: float) -> Optional[WordTimestamp]:
        if isinstance(item, WordTimestamp):
            return item
        if not isinstance(item, dict):
            return None
        word = str(item.get("word") or item.get("text") or "").strip()
        if not word:
            return None
        try:
            start = float(item.get("start", fallback_seconds))
            end = float(item.get("end", start))
            confidence = float(item.get("confidence", item.get("probability", 0.0)))
        except (TypeError, ValueError):
            start = end = fallback_seconds
            confidence = 0.0
        return WordTimestamp(word=word, start=start, end=end, confidence=confidence)

    # ---- 收尾与观测 ----

    def flush(self, timeout: float = 8.0):
        """等待已入队的音频块发送完（不关闭连接）"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._thread is None or not self._thread.is_alive():
                return
            with self._chunks_cv:
                if not self._chunks and not self._busy:
                    return
                self._chunks_cv.wait(0.05)
        with self._chunks_cv:
            remaining = len(self._chunks)
        if remaining:
            logger.warning(f"华为云 ASR flush 超时，仍有 {remaining} 个音频块未发送")

    def stop(self):
        """停止后端：先尽力发完队列，再停线程并关闭 transport（可重复调用）"""
        self.flush(timeout=10.0)
        self._stop_event.set()
        with self._chunks_cv:
            self._chunks.clear()
            self._chunks_cv.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                logger.warning("华为云 ASR 后台线程未在 5 秒内退出")
        with self._state_lock:
            self._is_loading = False
            self._is_loaded = False

    def get_stats(self) -> dict:
        """运行时统计（供 /api/health 与排障，绝不返回密钥）"""
        with self._state_lock:
            loaded = self._is_loaded
            loading = self._is_loading
            audio_seconds = self._total_duration
            sent_seconds = self._sent_duration
            sent = self._chunks_sent
            dropped = self._dropped_chunks
            events = self._events
        with self._chunks_cv:
            backlog = len(self._chunks)
            busy = self._busy
        return {
            "backend": self.backend_name,
            "loaded": loaded,
            "loading": loading,
            "lastError": self.last_error,
            "configured": self._config.configured,
            "missing": list(self._config.missing),
            "language": self.language,
            "simpMode": self.simp_mode,
            "termAutoDegrade": self._auto_degrade,
            "termPromptChars": len(self.initial_prompt or ""),
            "audioSeconds": round(audio_seconds, 1),
            "sentAudioSeconds": round(sent_seconds, 1),
            "chunksSent": sent,
            "droppedChunks": dropped,
            "backlog": backlog,
            "busy": busy,
            "events": events,
            "threadAlive": self.is_running,
        }