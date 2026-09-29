"""
音频捕获模块
支持两种模式：
  - 'loopback'：捕获系统音频输出（在线网课场景）
  - 'microphone'：麦克风输入（线下课堂场景）

跨平台支持 Windows(WASAPI) / macOS(BlackHole) / Linux(PulseAudio)
"""

import logging
import threading
import queue
import time
import platform
from typing import Optional, Callable, Literal

import numpy as np

logger = logging.getLogger(__name__)

# 音频配置
SAMPLE_RATE = 16000  # Whisper 使用 16kHz
CHANNELS = 1          # 单声道
CHUNK_DURATION = 0.03  # 30ms 块
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION)  # 480 samples
FORMAT = "int16"

SYSTEM = platform.system()

CaptureMode = Literal["loopback", "microphone"]


class AudioCapture:
    """
    音频捕获器
    支持 Loopback（系统音频）和 Microphone（麦克风）两种模式
    """

    def __init__(
        self,
        on_audio_chunk: Callable[[np.ndarray], None],
        mode: CaptureMode = "loopback",
        sample_rate: int = SAMPLE_RATE,
        chunk_size: int = CHUNK_SIZE,
        device_index: Optional[int] = None,  # 手动指定设备
    ):
        self.on_audio_chunk = on_audio_chunk
        self.mode = mode
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self._device_index = device_index

        self._audio_queue: queue.Queue = queue.Queue()
        self._running = False
        self._paused = False
        self._thread: Optional[threading.Thread] = None
        self._stream = None
        self._pyaudio = None

    def start(self):
        """开始捕获音频"""
        if self._running:
            return

        self._running = True
        self._paused = False
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info(f"音频捕获已启动 (模式: {self.mode})")

    def stop(self):
        """停止捕获"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
        if self._pyaudio:
            try:
                self._pyaudio.terminate()
            except Exception:
                pass
        logger.info("音频捕获已停止")

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    @staticmethod
    def list_devices() -> list[dict]:
        """列出所有音频设备（供前端选择麦克风用）"""
        try:
            import pyaudio
            p = pyaudio.PyAudio()
            devices = []
            for i in range(p.get_device_count()):
                info = p.get_device_info_by_index(i)
                devices.append({
                    "index": i,
                    "name": info["name"],
                    "maxInputChannels": info["maxInputChannels"],
                    "maxOutputChannels": info["maxOutputChannels"],
                    "defaultSampleRate": info["defaultSampleRate"],
                    "isDefaultInput": bool(i == p.get_default_input_device_info().get("index")),
                })
            p.terminate()
            return devices
        except ImportError:
            logger.warning("PyAudio 未安装")
            return []

    def _capture_loop(self):
        """音频捕获主循环"""
        try:
            import pyaudio

            self._pyaudio = pyaudio.PyAudio()

            # 根据模式选择设备
            if self._device_index is not None:
                device_index = self._device_index
            elif self.mode == "loopback":
                device_index = self._find_loopback_device(self._pyaudio)
            else:
                device_index = self._find_microphone_device(self._pyaudio)

            if device_index is None:
                logger.error(f"未找到可用的音频设备 (模式: {self.mode})")
                return

            device_info = self._pyaudio.get_device_info_by_index(device_index)
            logger.info(f"使用音频设备: {device_info['name']} (index={device_index}, 模式={self.mode})")

            self._stream = self._pyaudio.open(
                format=self._pyaudio.get_format_from_width(2),  # int16 = 2 bytes
                channels=CHANNELS,
                rate=self.sample_rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=self.chunk_size,
                stream_callback=self._audio_callback,
            )

            self._stream.start_stream()

            while self._running and self._stream.is_active():
                time.sleep(0.1)

        except ImportError:
            logger.error("PyAudio 未安装。请运行: pip install pyaudio")
        except Exception as e:
            logger.error(f"音频捕获错误: {e}")

    def _audio_callback(self, in_data, frame_count, time_info, status):
        """PyAudio 回调：音频数据到达时调用"""
        if status:
            logger.warning(f"音频状态: {status}")

        if not self._paused and in_data:
            # 转换为 numpy 数组
            audio_array = np.frombuffer(in_data, dtype=np.int16).astype(np.float32) / 32768.0
            try:
                self.on_audio_chunk(audio_array)
            except Exception as e:
                logger.error(f"音频处理回调错误: {e}")

        return (in_data, pyaudio.paContinue)  # noqa: F821

    def _find_loopback_device(self, pyaudio_obj) -> Optional[int]:
        """查找系统 Loopback 设备，找不到返回 None（不再回退到麦克风）"""
        for i in range(pyaudio_obj.get_device_count()):
            device = pyaudio_obj.get_device_info_by_index(i)
            name = device["name"].lower()

            if SYSTEM == "Windows":
                # WASAPI Loopback / 立体声混音 / VB-Cable
                if device["maxInputChannels"] > 0 and (
                    "loopback" in name or "立体声混音" in name or "stereo mix" in name
                    or "vb-cable" in name or "cable" in name
                ):
                    return i
            elif SYSTEM == "Darwin":
                # macOS: BlackHole / Soundflower
                if device["maxInputChannels"] > 0 and ("blackhole" in name or "soundflower" in name):
                    return i
            elif SYSTEM == "Linux":
                # PulseAudio: 查找 monitor 设备
                if device["maxInputChannels"] > 0 and "monitor" in name:
                    return i

        # 找不到 Loopback 设备：返回 None，不再回退到麦克风
        logger.warning(
            f"未找到 Loopback 设备。请安装虚拟音频设备：\n"
            f"  Windows: 安装 VB-Cable (https://vb-audio.com/Cable/)\n"
            f"  macOS:   安装 BlackHole (brew install blackhole-2ch)\n"
            f"  Linux:   pactl load-module module-loopback"
        )
        return None

    def _find_microphone_device(self, pyaudio_obj) -> Optional[int]:
        """查找麦克风设备"""
        # 优先使用默认输入设备（通常是麦克风）
        try:
            default = pyaudio_obj.get_default_input_device_info()
            logger.info(f"使用默认麦克风: {default['name']} (index={default['index']})")
            return default["index"]
        except Exception:
            pass

        # 回退：查找任何有输入通道的设备
        for i in range(pyaudio_obj.get_device_count()):
            device = pyaudio_obj.get_device_info_by_index(i)
            if device["maxInputChannels"] > 0:
                logger.info(f"使用第一个可用输入设备: {device['name']} (index={i})")
                return i

        return None
