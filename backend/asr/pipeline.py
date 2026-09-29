"""
ASR 管道
整合音频捕获 → VAD → Whisper 识别 → 结果输出
"""

import logging
import asyncio
import time
import threading
from typing import Optional, Callable
from dataclasses import dataclass

import numpy as np

from audio.capture import AudioCapture
from audio.vad import VoiceActivityDetector
from asr.whisper_engine import WhisperEngine, WordTimestamp, DEVICE, COMPUTE_TYPE

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000


class ASRPipeline:
    """
    完整的 ASR 管道
    协调音频捕获、VAD 和语音识别
    """

    def __init__(
        self,
        course_id: str,
        course_config: dict,
        on_subtitle: Callable[[dict], None],
        on_interim: Callable[[str, list], None],
        on_status: Callable[[str, str], None],
        on_term_extracted: Callable[[dict], None],
    ):
        self.course_id = course_id
        self.course_config = course_config
        self.on_subtitle = on_subtitle
        self.on_interim = on_interim
        self.on_status = on_status
        self.on_term_extracted = on_term_extracted

        self._running = False
        self._paused = False
        self._subtitle_counter = 0
        self._last_term_extraction = 0.0
        self._accumulated_text: list[str] = []

        # 初始化各组件
        self._whisper = WhisperEngine(
            model_size="small",
            device=DEVICE,
            compute_type=COMPUTE_TYPE,
            language=course_config.get("language", "auto"),
            on_partial=self._on_recognition_result,
            on_progress=lambda stage, pct: self.on_status(
                "loading" if stage in ("downloading", "loading") else stage,
                f"模型{'下载' if stage == 'downloading' else '加载'}中... {pct:.0f}%" if pct < 100 and stage != "error"
                else ("模型就绪" if stage == "ready" else "模型加载失败")
            ),
        )

        self._vad = VoiceActivityDetector(
            on_speech_segment=self._on_speech_segment,
        )

        self._capture = AudioCapture(
            on_audio_chunk=self._on_audio_chunk,
            mode="microphone" if course_config.get("courseType") == "offline" else "loopback",
        )

    async def start(self):
        """启动 ASR 管道"""
        self._running = True
        self._paused = False
        self._subtitle_counter = 0
        self._accumulated_text.clear()

        if not self._whisper.is_loaded:
            if self._whisper.is_loading:
                # 模型正在后台加载，等待完成后自动启动
                self.on_status("loading", "模型加载中，请稍候...")
                t = threading.Thread(target=self._wait_for_model, daemon=True)
                t.start()
                return
            else:
                self.on_status("error", "Whisper 模型加载失败")
                return

        self._start_listening()

    def _wait_for_model(self):
        """等待模型加载完成后自动启动监听"""
        while self._whisper.is_loading and self._running:
            time.sleep(1)
        if self._running and self._whisper.is_loaded:
            self._start_listening()
        elif self._running:
            self.on_status("error", "Whisper 模型加载失败")

    def _start_listening(self):
        """启动音频监听"""
        self._capture.start()
        mode_text = "麦克风收音" if self.course_config.get("courseType") == "offline" else "系统音频"
        self.on_status("listening", f"正在监听{mode_text}...")

    async def stop(self):
        """停止 ASR 管道"""
        self._running = False
        self._vad.flush()
        self._capture.stop()
        self._whisper.stop()
        self.on_status("idle", "已停止")

    async def pause(self):
        self._paused = True
        self._capture.pause()
        self.on_status("idle", "已暂停")

    async def resume(self):
        self._paused = False
        self._capture.resume()
        self.on_status("listening", "已恢复监听")

    def _on_audio_chunk(self, audio: np.ndarray):
        """音频块到达 → VAD"""
        if self._running and not self._paused:
            self._vad.process_chunk(audio)

    def _on_speech_segment(self, audio: np.ndarray, start_time: float, end_time: float):
        """VAD 检测到语音片段 → Whisper 识别"""
        if not self._running:
            return

        duration = end_time - start_time
        logger.debug(f"语音片段: {start_time:.1f}s - {end_time:.1f}s ({duration:.1f}s)")

        # 发送处理中状态
        self.on_status("processing", f"识别中... ({duration:.1f}s)")

        # 送入 Whisper
        self._whisper.add_audio(audio)

    def _on_recognition_result(self, text: str, words: list[WordTimestamp]):
        """Whisper 识别完成 → 输出字幕"""
        if not text.strip():
            return

        self._subtitle_counter += 1
        now_ms = int(time.time() * 1000)

        # 构建字幕数据
        subtitle = {
            "id": f"sub_{self._subtitle_counter}",
            "text": text,
            "words": [
                {
                    "word": w.word,
                    "start": int(w.start * 1000),
                    "end": int(w.end * 1000),
                    "confidence": round(w.confidence, 3),
                }
                for w in words
            ],
            "start": int(words[0].start * 1000) if words else now_ms,
            "end": int(words[-1].end * 1000) if words else now_ms,
            "isFinal": True,
        }

        # 回调前端
        self.on_subtitle(subtitle)

        # 累积文本用于术语提取
        self._accumulated_text.append(text)

        # 每 10 条字幕尝试提取术语
        if len(self._accumulated_text) >= 10:
            self._maybe_extract_terms()

        # 恢复监听状态
        self.on_status("listening", f"已识别 {self._subtitle_counter} 条字幕")

    def _maybe_extract_terms(self):
        """尝试从累积字幕中提取专业术语"""
        if not self._accumulated_text:
            return

        now = time.time()
        if now - self._last_term_extraction < 15:  # 至少间隔 15 秒
            return

        self._last_term_extraction = now
        combined_text = " ".join(self._accumulated_text[-10:])

        # 异步提取术语（不阻塞主流程）
        import threading
        t = threading.Thread(
            target=self._extract_terms,
            args=(combined_text,),
            daemon=True,
        )
        t.start()

    def _extract_terms(self, text: str):
        """使用 Ollama LLM 提取专业术语"""
        try:
            from llm.extractor import TermExtractor
            extractor = TermExtractor()

            # Ollama 不可用时通知前端，不再静默失败
            if not extractor._check_ollama():
                logger.warning("Ollama 不可用，跳过术语提取")
                self.on_status("listening", "AI术语提取未启用（需安装Ollama）")
                return

            terms = extractor.extract_terms(
                text=text,
                domain=self.course_config.get("domain", ""),
                course_name=self.course_config.get("name", ""),
                custom_terms=self.course_config.get("custom_terms", []),
            )

            for term in terms:
                self.on_term_extracted(term)

            if terms:
                logger.info(f"提取了 {len(terms)} 个术语")

        except ImportError:
            logger.debug("LLM 提取器不可用，跳过术语提取")
            self.on_status("listening", "AI术语提取模块未安装")
        except Exception as e:
            logger.error(f"术语提取失败: {e}")
