"""
语音活动检测 (VAD)
使用 Silero VAD 检测语音片段边界
"""

import logging
from typing import Callable, Optional
from collections import deque

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
SPEECH_THRESHOLD = 0.5      # 语音概率阈值
SILENCE_DURATION = 0.5       # 静音多久后断句（秒）
MAX_SPEECH_DURATION = 10.0   # 最大语音片段长度（秒）
MIN_SPEECH_DURATION = 0.3    # 最小语音片段长度（秒）


class VoiceActivityDetector:
    """
    Silero VAD 语音活动检测器
    将连续音频流切分为语音片段
    """

    def __init__(
        self,
        on_speech_segment: Callable[[np.ndarray, float, float], None],
        sample_rate: int = SAMPLE_RATE,
    ):
        self.on_speech_segment = on_speech_segment
        self.sample_rate = sample_rate

        self._model = None
        self._audio_buffer: list[np.ndarray] = []
        self._speech_buffer: list[np.ndarray] = []
        self._is_speaking = False
        self._silence_frames = 0
        self._speech_frames = 0
        self._total_frames = 0
        self._speech_start_time: Optional[float] = None

        self._init_model()

    def _init_model(self):
        """加载 Silero VAD 模型"""
        try:
            import torch
            # Silero VAD 模型通过 torch.hub 加载
            self._model, _ = torch.hub.load(
                repo_or_dir='snakers4/silero-vad',
                model='silero_vad',
                force_reload=False,
                onnx=False,
            )
            logger.info("Silero VAD 模型加载成功")
        except Exception as e:
            logger.warning(f"Silero VAD 加载失败，使用能量检测降级方案: {e}")
            self._model = None

    def process_chunk(self, audio: np.ndarray):
        """
        处理音频块
        audio: float32 numpy array, 范围 [-1, 1]
        """
        if len(audio) == 0:
            return

        chunk_duration = len(audio) / self.sample_rate

        # 检测是否有语音
        has_speech = self._detect_speech(audio)
        self._total_frames += 1

        if has_speech:
            if not self._is_speaking:
                # 语音开始
                self._is_speaking = True
                self._speech_start_time = self._total_frames * chunk_duration - chunk_duration
                # 将之前的静音缓冲加入（作为上下文）
                self._speech_buffer = list(self._audio_buffer)
                self._audio_buffer.clear()

            self._speech_buffer.append(audio)
            self._speech_frames += 1
            self._silence_frames = 0
        else:
            if self._is_speaking:
                self._speech_buffer.append(audio)
                self._silence_frames += 1

                # 检查是否应该断句
                silence_time = self._silence_frames * chunk_duration
                speech_time = self._speech_frames * chunk_duration

                should_cut = (
                    silence_time >= SILENCE_DURATION or
                    speech_time >= MAX_SPEECH_DURATION
                )

                if should_cut and speech_time >= MIN_SPEECH_DURATION:
                    self._emit_segment()
            else:
                # 非说话状态，保持小缓冲
                self._audio_buffer.append(audio)
                if len(self._audio_buffer) > int(0.5 / chunk_duration):
                    self._audio_buffer.pop(0)

    def flush(self):
        """强制输出当前语音片段"""
        if self._is_speaking and len(self._speech_buffer) > 0:
            speech_time = self._speech_frames * (len(self._speech_buffer[0]) / self.sample_rate if self._speech_buffer else 0)
            if speech_time >= MIN_SPEECH_DURATION:
                self._emit_segment()

    def _detect_speech(self, audio: np.ndarray) -> bool:
        """检测音频块是否包含语音"""
        if self._model is not None:
            return self._detect_silero(audio)
        return self._detect_energy(audio)

    def _detect_silero(self, audio: np.ndarray) -> bool:
        """使用 Silero 模型检测"""
        try:
            import torch
            audio_tensor = torch.from_numpy(audio).float()
            # Silero 期望 16000 Hz
            speech_prob = self._model(audio_tensor, self.sample_rate).item()
            return speech_prob > SPEECH_THRESHOLD
        except Exception:
            return self._detect_energy(audio)

    def _detect_energy(self, audio: np.ndarray) -> bool:
        """基于能量的语音检测（降级方案）"""
        rms = np.sqrt(np.mean(audio ** 2))
        return rms > 0.01  # 简单的 RMS 阈值

    def _emit_segment(self):
        """输出语音片段"""
        if not self._speech_buffer:
            return

        segment = np.concatenate(self._speech_buffer)
        duration = len(segment) / self.sample_rate

        if duration >= MIN_SPEECH_DURATION:
            start_time = self._speech_start_time or 0
            self.on_speech_segment(segment, start_time, start_time + duration)

        # 重置
        self._speech_buffer.clear()
        self._audio_buffer.clear()
        self._is_speaking = False
        self._speech_frames = 0
        self._silence_frames = 0
        self._speech_start_time = None
