"""
音频捕获模块
支持两种模式：
  - 'loopback'：捕获系统音频输出（在线网课场景）
  - 'microphone'：麦克风输入（线下课堂场景）

跨平台支持 Windows(WASAPI Loopback) / macOS(BlackHole) / Linux(PulseAudio)

Windows 说明
    WASAPI 回环采集依赖 PyAudioWPatch（PyAudio 的 WASAPI-loopback 分支）。
    它以 `pyaudiowpatch` 之名发布，**不提供 `pyaudio` 这个模块名**，
    因此必须写 `import pyaudiowpatch as pyaudio`，不能只在 requirements 里替换。
    这样可省掉对 VB-Cable 之类虚拟声卡的依赖。
"""

import logging
import queue
import threading
import time
import platform
from typing import Optional, Callable, Literal

import numpy as np

logger = logging.getLogger(__name__)

# ============ 音频后端 ============
# 优先 PyAudioWPatch：只有它能枚举 Windows 的 WASAPI 回环设备
try:
    import pyaudiowpatch as pyaudio  # type: ignore
    AUDIO_BACKEND = "pyaudiowpatch"
    _HAS_LOOPBACK_API = True
except ImportError:
    try:
        import pyaudio  # type: ignore
        AUDIO_BACKEND = "pyaudio"
        _HAS_LOOPBACK_API = False
    except ImportError:
        pyaudio = None  # type: ignore
        AUDIO_BACKEND = None
        _HAS_LOOPBACK_API = False

# PyAudio 的回调返回值必须在模块级取到。
# 原实现在回调里写 pyaudio.paContinue，但 pyaudio 只在函数内部 import，
# 于是每次回调都抛 NameError（并被 # noqa: F821 压掉了告警）。
PA_CONTINUE = 0
if pyaudio is not None:
    PA_CONTINUE = getattr(pyaudio, "paContinue", 0)

# 音频配置
SAMPLE_RATE = 16000  # Whisper 使用 16kHz
CHANNELS = 1          # 单声道

# 30ms 曾是 480 样本，但 Silero VAD 要求恰好 512 样本（16kHz），
# 480 会让 VAD 每次都抛 "Input audio chunk is too short" 并静默降级。
CHUNK_DURATION = 0.032  # 32ms
CHUNK_SIZE = 512        # = 16000 * 0.032，正好满足 Silero VAD
FORMAT = "int16"

SYSTEM = platform.system()

# 🔴 **音频设备枚举的全局互斥锁 + 默认回环设备缓存**（2026-10-29）
#
# 为什么必须加：PortAudio 的 `get_default_wasapi_loopback()` 会读
# `hostApi->info.defaultOutputDevice`。**多个采集线程同时枚举**时这里会读到
# 非法值，PortAudio 直接断言失败并 **abort 整个进程**：
#     Assertion failed: hostApi->info.defaultOutputDevice < hostApi->info.deviceCount,
#     file src/common/pa_front.c, line 233
# 实际触发路径：前端在多个课程上各起了一个 ASR 管道 ⇒ 三个采集线程同一毫秒
# 启动 ⇒ 同时枚举 ⇒ 后端进程崩掉（页面显示「已连接」但永远没有字幕）。
#
# 两道防线：
# 1. 互斥锁 —— 枚举串行化（枚举很便宜，串行没有代价）
# 2. 缓存 —— 默认回环设备**不会变**，第一次查到就复用，后续启动不再碰 PortAudio
_DEVICE_ENUM_LOCK = threading.Lock()
_DEFAULT_LOOPBACK_INDEX_CACHE: Optional[int] = None

CaptureMode = Literal["loopback", "microphone"]


def _resample_to_16k(data: np.ndarray, src_rate: int) -> np.ndarray:
    """
    把任意采样率的重采样到 16kHz

    必要性：WASAPI 回环流在共享模式下**必须使用设备原生采样率**
    （通常 44100 / 48000），按 16000 打开会直接失败。
    所以先按原生率采集，再在这里转换。

    整数倍降采样时先做等长均值（简易低通），避免高频混叠进语音频段；
    非整数倍退化为线性插值。
    """
    if src_rate == SAMPLE_RATE or len(data) == 0:
        return data

    if src_rate % SAMPLE_RATE == 0:
        ratio = src_rate // SAMPLE_RATE
        usable = len(data) - (len(data) % ratio)
        if usable == 0:
            return np.zeros(0, dtype=np.float32)
        return data[:usable].reshape(-1, ratio).mean(axis=1).astype(np.float32)

    target_len = int(len(data) * SAMPLE_RATE / src_rate)
    if target_len <= 0:
        return np.zeros(0, dtype=np.float32)
    idx = np.linspace(0, len(data) - 1, target_len)
    return np.interp(idx, np.arange(len(data)), data).astype(np.float32)


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
        self.sample_rate = sample_rate          # 输出采样率，固定 16000
        self.chunk_size = chunk_size
        self._device_index = device_index

        self._audio_queue: queue.Queue = queue.Queue()
        self._running = False
        self._paused = False
        self._thread: Optional[threading.Thread] = None
        self._stream = None
        self._pyaudio = None
        self._native_rate = sample_rate          # 设备实际使用的采样率
        self._in_channels = CHANNELS             # 流实际打开的输入声道数

        # 🔴 音频流被外部打断后的**重建重试**参数（2026-10-28）
        #    host error 最常见的触发方式就是「另一个程序开始播放音频」
        #    —— 浏览器开播就会打断 ⇒ 采集必须能自己恢复，而不是永久死掉。
        self.max_retries = 8          # 最多重建 8 次
        self.retry_base_delay = 1.0   # 首次退避 1s
        self.retry_max_delay = 8.0    # 退避上限 8s（1→2→4→8→8…）
        self._chunk_count = 0
        self._dropped = 0

    # ---------- 生命周期 ----------

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
        logger.info(f"音频捕获已停止 (共 {self._chunk_count} 块, 丢弃 {self._dropped} 块)")

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    # ---------- 设备枚举 ----------

    @staticmethod
    def list_devices() -> list[dict]:
        """列出所有音频设备（供前端选择麦克风用）"""
        if pyaudio is None:
            logger.warning(
                f"未安装音频库（当前后端={AUDIO_BACKEND}）。"
                f"Windows 请安装: pip install PyAudioWPatch"
            )
            return []
        try:
            p = pyaudio.PyAudio()
            devices = []
            default_input = None
            try:
                default_input = p.get_default_input_device_info().get("index")
            except Exception:
                pass

            for i in range(p.get_device_count()):
                info = p.get_device_info_by_index(i)
                devices.append({
                    "index": i,
                    "name": info["name"],
                    "maxInputChannels": info["maxInputChannels"],
                    "maxOutputChannels": info["maxOutputChannels"],
                    "defaultSampleRate": info["defaultSampleRate"],
                    "isDefaultInput": bool(i == default_input),
                    "isLoopback": bool(info.get("isLoopbackDevice", False)),
                })
            p.terminate()
            return devices
        except Exception as e:
            logger.error(f"枚举音频设备失败: {e}")
            return []

    # ---------- 捕获主循环 ----------

    def _capture_loop(self):
        """音频捕获主循环"""
        if pyaudio is None:
            logger.error(
                "音频库不可用，无法采集。请安装 PyAudioWPatch:\n"
                "  pip install PyAudioWPatch"
            )
            return

        try:
            self._pyaudio = pyaudio.PyAudio()

            if self._device_index is not None:
                device_index = self._device_index
            elif self.mode == "loopback":
                # PyAudioWPatch 自带默认回环设备，优先用它
                device_index = self._default_loopback_index(self._pyaudio)
                if device_index is None:
                    device_index = self._find_loopback_device(self._pyaudio)
            else:
                device_index = self._find_microphone_device(self._pyaudio)

            if device_index is None:
                logger.error(f"未找到可用的音频设备 (模式: {self.mode})")
                self._running = False
                return

            device_info = self._pyaudio.get_device_info_by_index(device_index)
            self._native_rate = int(device_info.get("defaultSampleRate") or self.sample_rate)
            logger.info(
                f"使用音频设备: {device_info['name']} "
                f"(index={device_index}, 模式={self.mode}, "
                f"原生采样率={self._native_rate}Hz, 输入声道={device_info['maxInputChannels']})"
            )

            # 回环/多声道设备需要按声道数打开，否则 PortAudio 报错
            in_channels = 1 if self.mode == "microphone" else 2
            max_in = int(device_info.get("maxInputChannels") or 1)
            if max_in > 1:
                in_channels = min(2, max_in)
            elif max_in == 1:
                in_channels = 1
            self._in_channels = in_channels

            # 关键：按设备原生采样率打开，采集后再重采样到 16k。
            # （回环设备在共享模式下不接受任意采样率）
            frames_per_buffer = max(1, int(self._native_rate * CHUNK_DURATION))

            self._stream = self._pyaudio.open(
                format=self._pyaudio.get_format_from_width(2),  # int16 = 2 bytes
                channels=in_channels,
                rate=self._native_rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=frames_per_buffer,
                stream_callback=self._audio_callback,
            )

            self._stream.start_stream()
            logger.info(
                f"音频流已打开: {self._native_rate}Hz/{in_channels}ch, "
                f"块长={frames_per_buffer} 帧"
            )

            while self._running and self._stream.is_active():
                time.sleep(0.1)

            # 退出循环时若仍在运行，说明流被动中断了
            if self._running:
                logger.warning("音频流已非活跃状态，捕获退出")

        except Exception as e:
            # 🔴 **host error 是可恢复的** —— 它最常见的触发方式就是
            #    「**另一个程序开始播放音频**」（浏览器开播、播放器开声、
            #    甚至另一个 ASR 管道抢占设备）。Windows 的 WASAPI 回环设备
            #    同一时刻只能被一个流独占，被抢时 PortAudio 直接报
            #    `OSError: [Errno -9999] Unanticipated host error`。
            #
            #    原实现在这里**直接把 `_running` 置 False** ⇒ 采集永久死掉，
            #    字幕永远不来，而前端只能显示「等待音频输入...」——
            #    用户完全看不出是「设备被抢」还是「模型坏了」。
            #    （2026-10-28 用户测试时实际踩到：浏览器一开始播视频，
            #      采集就被打断，界面一直停在「等待音频输入...」。）
            #
            #    ⇒ 现在改成**重建音频流 + 指数退避重试**。
            #    重试放在采集线程里（daemon thread），不阻塞事件循环。
            if not self._running:
                return

            attempt = 0
            while self._running and attempt < self.max_retries:
                attempt += 1
                delay = min(self.retry_base_delay * (2 ** (attempt - 1)),
                            self.retry_max_delay)
                logger.warning(
                    f"音频捕获被中断（{type(e).__name__}: {e}）→ "
                    f"{delay:.1f}s 后重建音频流（第 {attempt}/{self.max_retries} 次）"
                )
                # 退避期间也要能被 stop() 立刻打断
                deadline = time.time() + delay
                while self._running and time.time() < deadline:
                    time.sleep(0.1)
                if not self._running:
                    return
                try:
                    self._close_stream_quietly()
                    self._pyaudio = pyaudio.PyAudio()
                    self._stream = self._pyaudio.open(
                        format=self._pyaudio.get_format_from_width(2),
                        channels=in_channels,
                        rate=self._native_rate,
                        input=True,
                        input_device_index=device_index,
                        frames_per_buffer=frames_per_buffer,
                        stream_callback=self._audio_callback,
                    )
                    self._stream.start_stream()
                    logger.info("音频流已重建，恢复捕获")
                    e = None
                    break
                except Exception as e2:
                    logger.warning(f"重建音频流失败: {type(e2).__name__}: {e2}")
                    e = e2

            if e is not None:
                logger.error(f"音频捕获错误（重试 {attempt} 次后仍失败）: "
                             f"{type(e).__name__}: {e}")
                self._running = False
        finally:
            self._close_stream_quietly()

    def _close_stream_quietly(self) -> None:
        """关流 + 释放 PyAudio —— 任何时候都不能让它抛出来打断调用方"""
        try:
            if self._stream is not None:
                try:
                    self._stream.stop_stream()
                except Exception:
                    pass
                try:
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
        except Exception:
            pass
        try:
            if self._pyaudio is not None:
                self._pyaudio.terminate()
                self._pyaudio = None
        except Exception:
            pass

    def _audio_callback(self, in_data, frame_count, time_info, status):
        """
        PyAudio 回调：音频数据到达时调用

        注意：这里绝不能抛异常。PyAudio 的回调抛异常会中止整个音频流，
        而这是原实现里 `pyaudio.paContinue` 未定义导致 NameError 的后果。
        """
        try:
            if status:
                logger.debug(f"音频状态: {status}")

            if not self._paused and in_data:
                pcm = np.frombuffer(in_data, dtype=np.int16)
                if pcm.size:
                    # 多声道 → 单声道
                    channels = self._in_channels
                    if channels > 1 and pcm.size % channels == 0:
                        pcm = pcm.reshape(-1, channels).mean(axis=1).astype(np.int16)
                    audio_array = _resample_to_16k(
                        pcm.astype(np.float32) / 32768.0, self._native_rate
                    )
                    if audio_array.size:
                        self._chunk_count += 1
                        try:
                            self.on_audio_chunk(audio_array)
                        except Exception as e:
                            logger.error(f"音频处理回调错误: {e}")
        except Exception as e:
            logger.error(f"音频回调异常（已吞掉以避免中止音频流）: {type(e).__name__}: {e}")

        return (None, PA_CONTINUE)

    # ---------- 设备查找 ----------

    def _default_loopback_index(self, pyaudio_obj) -> Optional[int]:
        """
        用 PyAudioWPatch 的 WASAPI API 取默认回环设备

        这是回环采集的正确做法：靠设备名子串匹配（'loopback'/'立体声混音'/'cable'）
        既不可靠，也会把「立体声混音」（需要用户手动启用）和 VB-Cable 误当成首选。

        🔴 **必须加锁 + 缓存**（2026-10-29 修的真实崩溃）
        ----------------------------------------------
        `get_default_wasapi_loopback()` 会去读 PortAudio 的
        `hostApi->info.defaultOutputDevice`。**当多个采集线程同时枚举设备时，
        这里会读到非法值，PortAudio 直接断言失败并 abort 整个进程**：

            Assertion failed: hostApi->info.defaultOutputDevice < hostApi->info.deviceCount,
            file src/common/pa_front.c, line 233

        触发路径（实际发生）：**前端在多个课程上各起了一个 ASR 管道**
        ⇒ 三个采集线程在同一毫秒启动 ⇒ 同时枚举 ⇒ 后端整个进程崩掉，
        页面只剩「已连接」但永远没有字幕。

        ⇒ 两道防线：
        1. **模块级互斥锁** —— 设备枚举串行化（枚举很便宜，串行无代价）
        2. **缓存索引** —— 默认回环设备**不会变**，第一次查到就存起来复用，
           后续启动根本不再枚举（⇒ 连并发都不会发生）
        """
        global _DEFAULT_LOOPBACK_INDEX_CACHE

        if not _HAS_LOOPBACK_API:
            return None

        # 防线 2：命中缓存就不再碰 PortAudio
        with _DEVICE_ENUM_LOCK:
            cached = _DEFAULT_LOOPBACK_INDEX_CACHE
        if cached is not None:
            return cached

        # 防线 1：枚举串行化
        with _DEVICE_ENUM_LOCK:
            info = pyaudio_obj.get_default_wasapi_loopback()
            if info and info.get("index") is not None:
                idx = int(info["index"])
                logger.info(f"WASAPI 默认回环设备: {info.get('name')} (index={idx})")
                _DEFAULT_LOOPBACK_INDEX_CACHE = idx
                return idx
        return None

    def _find_loopback_device(self, pyaudio_obj) -> Optional[int]:
        """查找系统 Loopback 设备，找不到返回 None（不再回退到麦克风）"""
        # 回退路径 1：PyAudioWPatch 枚举出的回环设备
        if _HAS_LOOPBACK_API:
            try:
                for info in pyaudio_obj.get_loopback_device_info_generator():
                    idx = info.get("index")
                    if idx is not None:
                        return int(idx)
            except Exception:
                pass

        # 回退路径 2：按名字匹配（macOS/Linux 或旧版 PortAudio）
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
            f"未找到系统音频回环设备。\n"
            f"  Windows: 需安装 PyAudioWPatch（本机当前后端={AUDIO_BACKEND}），"
            f"安装后可直接采集系统声音，无需 VB-Cable\n"
            f"  macOS:   brew install blackhole-2ch\n"
            f"  Linux:   pactl load-module module-loopback"
        )
        return None

    def _find_microphone_device(self, pyaudio_obj) -> Optional[int]:
        """查找麦克风设备"""
        # 优先使用默认输入设备（通常是麦克风）
        try:
            default = pyaudio_obj.get_default_input_device_info()
            logger.info(f"使用默认麦克风: {default['name']} (index={default['index']})")
            return int(default["index"])
        except Exception:
            pass

        # 回退：查找任何有输入通道的设备
        for i in range(pyaudio_obj.get_device_count()):
            device = pyaudio_obj.get_device_info_by_index(i)
            if device["maxInputChannels"] > 0:
                logger.info(f"使用第一个可用输入设备: {device['name']} (index={i})")
                return i

        return None
