"""
语音活动检测 (VAD)
优先使用 Silero VAD，不可用时降级为能量检测
"""

import logging
import os
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000

# Silero VAD（16kHz）要求**恰好 512 样本**一个窗口。
# 多一个少一个都会抛异常，而原实现直接抛上游块大小（480）→ 每次都失败并被
# except 吞掉、静默降级为能量检测。因此这里内部强制按 512 对齐。
VAD_WINDOW = 512
WINDOW_DURATION = VAD_WINDOW / SAMPLE_RATE  # 0.032s

SPEECH_THRESHOLD = 0.5       # 语音概率阈值
SILENCE_DURATION = float(os.environ.get("TINGKEBANLV_VAD_SILENCE", "0.8"))
                             # 静音多久后断句（秒）
MAX_SPEECH_DURATION = 12.0   # 最大语音片段长度（秒）
MIN_SPEECH_DURATION = 1.2    # 最小语音片段长度（秒），低于此不提交识别


class VoiceActivityDetector:
    """
    Silero VAD 语音活动检测器
    将连续音频流切分为语音片段

    断句参数的取值依据（实测，见 scripts/bench_matrix.py）：
        Faster-Whisper 每次调用有约 4 秒固定开销（Whisper 按 30 秒窗口处理，
        短输入被补齐），所以片段切得越碎越亏：
            2s 片段 → RTF 2.8（完全跟不上）
            4s 片段 → RTF 1.4（跟不上）
            8s 片段 → RTF 0.74（跟得上）
        但片段越长，字幕出字越晚（延迟 = 片段时长 + 识别耗时）。
        故默认静音阈值由 0.5s 放宽到 0.8s，让片段自然落在 4~12 秒。
    """

    def __init__(
        self,
        on_speech_segment: Callable[[np.ndarray, float, float], None],
        sample_rate: int = SAMPLE_RATE,
    ):
        self.on_speech_segment = on_speech_segment
        self.sample_rate = sample_rate

        self._model = None
        self._backend = "energy"
        self._pending = np.zeros(0, dtype=np.float32)   # 凑满 512 的残余
        self._audio_buffer: list[np.ndarray] = []
        self._speech_buffer: list[np.ndarray] = []
        self._is_speaking = False
        self._silence_windows = 0
        self._speech_windows = 0
        self._total_samples = 0
        self._speech_start_sample: Optional[int] = None

        self._init_model()

    # ---------- 模型加载 ----------

    def _init_model(self):
        """
        加载 Silero VAD

        刻意不用 torch.hub.load('snakers4/silero-vad')：那个路径需要访问 GitHub，
        而在网络受限环境下不是在几秒后失败，而是**无限挂起**
        （实测 11 分 16 秒仍未返回，我已主动终止）。
        又因为 VAD 在 /asr/start 的请求路径上同步构造，挂起会连带阻塞整个
        FastAPI 事件循环，让应用整体假死。

        改用 pip 包 silero-vad 自带的 load_silero_vad，且强制 onnx=True：
            - torch JIT 后端用 C++ fopen 读模型文件，**无法处理非 ASCII 路径**，
              而本产品的默认安装目录就是中文（%LOCALAPPDATA%\\Programs\\听课伴侣），
              实测会报 "open file failed ... silero_vad.jit"
            - ONNX 后端实测在中文路径下 0.25s 正常加载
        """
        if os.environ.get("TINGKEBANLV_DISABLE_VAD_MODEL") == "1":
            logger.info("已按环境变量禁用 Silero VAD，使用能量检测")
            return

        try:
            from silero_vad import load_silero_vad
        except ImportError:
            logger.warning("silero-vad 未安装，使用能量检测降级方案")
            return

        # 先试 ONNX（兼容非 ASCII 路径），失败再试 torch JIT
        for onnx_backend in (True, False):
            try:
                self._model = load_silero_vad(onnx=onnx_backend)
                self._backend = "silero-onnx" if onnx_backend else "silero-jit"
                logger.info(f"Silero VAD 加载成功 (后端={self._backend})")
                return
            except Exception as e:
                logger.debug(f"Silero VAD 加载失败 (onnx={onnx_backend}): {e}")

        self._model = None
        logger.warning("Silero VAD 加载失败，使用能量检测降级方案")
        self._backend = "energy"

    # ---------- 流式处理 ----------

    def process_chunk(self, audio: np.ndarray):
        """
        处理音频块（任意长度）

        audio: float32 numpy array, 范围 [-1, 1]
        内部按 512 样本对齐后再送 VAD，因此上游块大小可以任意。
        """
        if audio is None or len(audio) == 0:
            return

        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)

        audio = np.ascontiguousarray(audio, dtype=np.float32)
        if len(self._pending):
            audio = np.concatenate([self._pending, audio])

        n_windows = len(audio) // VAD_WINDOW
        for i in range(n_windows):
            window = audio[i * VAD_WINDOW:(i + 1) * VAD_WINDOW]
            self._process_window(window)

        self._pending = audio[n_windows * VAD_WINDOW:].copy()

    def _process_window(self, window: np.ndarray):
        """处理一个 512 样本窗口（32ms）"""
        has_speech = self._detect_speech(window)
        self._total_samples += VAD_WINDOW

        if has_speech:
            if not self._is_speaking:
                # 语音开始
                self._is_speaking = True
                self._speech_start_sample = self._total_samples - VAD_WINDOW
                # 将之前的静音缓冲加入（作为上下文，让首字不被削掉）
                self._speech_buffer = list(self._audio_buffer)
                self._audio_buffer.clear()

            self._speech_buffer.append(window)
            self._speech_windows += 1
            self._silence_windows = 0
        else:
            if self._is_speaking:
                self._speech_buffer.append(window)
                self._silence_windows += 1

                silence_time = self._silence_windows * WINDOW_DURATION
                speech_time = self._speech_windows * WINDOW_DURATION

                if (
                    silence_time >= SILENCE_DURATION
                    or speech_time >= MAX_SPEECH_DURATION
                ) and speech_time >= MIN_SPEECH_DURATION:
                    self._emit_segment()
            else:
                # 非说话状态，保持小缓冲（最多 0.5s 上下文）
                self._audio_buffer.append(window)
                max_keep = int(0.5 / WINDOW_DURATION)
                if len(self._audio_buffer) > max_keep:
                    self._audio_buffer.pop(0)

    def flush(self):
        """强制输出当前语音片段（停止/暂停时调用）"""
        if self._is_speaking and self._speech_buffer:
            speech_time = self._speech_windows * WINDOW_DURATION
            if speech_time >= MIN_SPEECH_DURATION:
                self._emit_segment()
            else:
                self._reset_state()

    def _reset_state(self):
        self._speech_buffer.clear()
        self._audio_buffer.clear()
        self._is_speaking = False
        self._speech_windows = 0
        self._silence_windows = 0
        self._speech_start_sample = None
        self._pending = np.zeros(0, dtype=np.float32)

    def _emit_segment(self):
        """输出语音片段"""
        if not self._speech_buffer:
            return

        segment = np.concatenate(self._speech_buffer)
        duration = len(segment) / self.sample_rate
        start_time = (self._speech_start_sample or 0) / self.sample_rate

        if duration >= MIN_SPEECH_DURATION:
            logger.debug(
                f"语音片段: {start_time:.1f}s - {start_time + duration:.1f}s "
                f"({duration:.1f}s)"
            )
            self.on_speech_segment(segment, start_time, start_time + duration)

        self._reset_state()

    # ---------- 语音检测 ----------

    def _detect_speech(self, window: np.ndarray) -> bool:
        if self._model is not None:
            try:
                return self._detect_silero(window)
            except Exception as e:
                # 只告警一次级别，避免每个窗口刷屏
                if self._backend != "energy":
                    logger.warning(f"Silero 推理失败，降级为能量检测: {e}")
                    self._model = None
                    self._backend = "energy"
        return self._detect_energy(window)

    def _detect_silero(self, window: np.ndarray) -> bool:
        """使用 Silero 模型检测（window 必须恰好 512 样本）"""
        import torch

        tensor = torch.from_numpy(window)
        prob = self._model(tensor, self.sample_rate)
        prob = prob.item() if hasattr(prob, "item") else float(prob)
        return prob > SPEECH_THRESHOLD

    def _detect_energy(self, window: np.ndarray) -> bool:
        """基于能量的语音检测（降级方案）"""
        rms = float(np.sqrt(np.mean(window ** 2)))
        return rms > 0.01  # 简单的 RMS 阈值

    # ---------- 状态查询 ----------

    @property
    def backend(self) -> str:
        """当前实际生效的检测后端，用于 /api/health 与排障"""
        return self._backend

    @property
    def is_model_loaded(self) -> bool:
        return self._model is not None
