"""
ASR 管道
整合音频捕获 → VAD → Whisper 识别 → 结果输出
"""

import logging
import asyncio
import os
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
        self._discovered_terms: list[str] = []  # 本课程运行时已识别的术语（动态加入热词）

        # 构建术语热词（自定义术语 + 领域术语 + 词表短语）
        # 作用：让 Whisper 把 "convolution" 拼对，而不是听成 "convalution"
        #
        # `language` 必须传：词表短语层只能命中英文多词短语，对中文课是纯干扰
        # （实测 CER 相对 +42.9%），terminology 需要知道语种才能关掉该层（缺陷 b）。
        try:
            from dictionary.terminology import build_terminology
            self._terminology = build_terminology(
                domain=course_config.get("domain", ""),
                custom_terms=course_config.get("custom_terms", []),
                language=course_config.get("language", "auto"),
            )
            initial_prompt = self._terminology["prompt"]
            if initial_prompt:
                logger.info(
                    f"术语热词已启用: {self._terminology['stats']['total']} 个术语, "
                    f"{self._terminology['promptChars']} 字符"
                )
        except Exception as e:
            logger.warning(f"术语热词构建失败，将不使用 initial_prompt: {e}")
            self._terminology = {"prompt": "", "sections": None, "stats": {"total": 0}}
            initial_prompt = ""

        # 初始化各组件
        #
        # 模型规格可配置：课程级 modelSize > 环境变量 TINGKEBANLV_WHISPER_MODEL > 默认值。
        # 原实现硬编码 "small"，而实测 small 在 2~4 秒片段上 RTF > 1（跟不上实时），
        # 详见 scripts/bench_matrix.py。
        from asr.whisper_engine import DEFAULT_MODEL
        model_size = (
            course_config.get("modelSize")
            or os.environ.get("TINGKEBANLV_WHISPER_MODEL")
            or DEFAULT_MODEL
        )
        logger.info(f"Whisper 模型规格: {model_size}")

        self._whisper = WhisperEngine(
            model_size=model_size,
            device=DEVICE,
            compute_type=COMPUTE_TYPE,
            language=course_config.get("language", "auto"),
            on_partial=self._on_recognition_result,
            on_progress=lambda stage, pct: self.on_status(
                "loading" if stage in ("downloading", "loading") else stage,
                f"模型{'下载' if stage == 'downloading' else '加载'}中... {pct:.0f}%" if pct < 100 and stage != "error"
                else ("模型就绪" if stage == "ready" else "模型加载失败")
            ),
            initial_prompt=initial_prompt,
            # 分层术语（高 → 低优先级）交给引擎按 token 预算重排（P0-3）：
            # 高优先级贴尾部保留窗口，预算不够时丢低优先级，而不是丢自定义术语
            prompt_sections=self._terminology.get("sections"),
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

    def update_terminology(self, custom_terms: Optional[list[str]] = None):
        """
        热更新术语热词（用户在课程里新增自定义术语后调用，无需重启管道）
        """
        try:
            from dictionary.terminology import build_terminology
            new_custom = custom_terms if custom_terms is not None else self.course_config.get("custom_terms", [])
            term = build_terminology(
                domain=self.course_config.get("domain", ""),
                custom_terms=new_custom,
                discovered_terms=self._discovered_terms,
            )
            self._terminology = term
            self._whisper.set_initial_prompt(term["prompt"], term.get("sections"))
            logger.info(f"术语热词已热更新: {term['stats']['total']} 个术语")
        except Exception as e:
            logger.error(f"术语热词热更新失败: {e}")

    def get_terminology(self) -> dict:
        """返回当前术语库信息（供 API 查询）"""
        return self._terminology

    def set_term_auto_degrade(self, enabled: bool) -> bool:
        """
        开关术语提示词的自动降级兜底（无需重启管道）

        设置面板里的「术语提示词自动防循环降级」。关掉它不代表术语表失效，
        只是不再在检测到重复循环时自动截断 —— 用户明确选择「识别质量优先」。

        注意：**只改开关，不重置已发生的降级**。引擎侧刻意不在开关打开时
        自动恢复完整提示词（会在重复循环的边界上反复抖动），要恢复得显式
        调 `restore_full_prompt()`；下次重启管道时自然回到完整提示词。
        """
        try:
            self._whisper.set_auto_degrade(enabled)
            return True
        except Exception as e:
            logger.error(f"设置术语降级开关失败: {e}")
            return False

    def get_stats(self) -> dict:
        """
        运行时健康度（供 /api/health 与排障）

        vadBackend 尤其重要：若为 "energy" 说明 Silero 没加载成功，
        分段质量已降级，此时字幕的断句会明显不如预期。
        """
        stats = {
            "vadBackend": getattr(self._vad, "backend", "unknown"),
            "vadModelLoaded": getattr(self._vad, "is_model_loaded", False),
            "courseId": self.course_id,
            "subtitleCount": self._subtitle_counter,
            "discoveredTerms": len(self._discovered_terms),
        }
        try:
            stats["whisper"] = self._whisper.get_stats()
        except Exception as e:
            stats["whisper"] = {"error": str(e)}
        return stats

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
        # 顺序很关键：必须先 flush VAD，再把 _running 置 False。
        # 否则 _on_speech_segment 会因为 _running 已为假而直接 return，
        # VAD 里还没提交的最后一段语音被静默丢掉 —— 每节课的结尾都会少一句字幕。
        self._vad.flush()
        self._running = False
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
        """
        术语提取（双通道）

        通道 1（始终生效）：本地词表匹配 —— 从字幕里找出命中 basic_vocab.json 的术语。
            不依赖任何外部服务，保证"AI 不可用时也有术语可用"。

        通道 2（可选）：LLM 提取 —— 补充词表里没有的、本课特有的新术语。
        """
        # ---- 通道 1：本地词表匹配 ----
        try:
            from dictionary.terminology import match_terms_in_text
            matched = match_terms_in_text(text)

            new_terms = []
            for m in matched:
                self.on_term_extracted({
                    "word": m["word"],
                    "translation": m["translation"],
                    "definition": m["definition"],
                    "domain": self.course_config.get("domain", ""),
                    "context_sentence": m.get("context_sentence", ""),
                    "source_subtitle_id": "",
                    "origin": "local_vocab",
                })
                if m["word"] not in self._discovered_terms:
                    self._discovered_terms.append(m["word"])
                    new_terms.append(m["word"])

            if matched:
                logger.info(f"本地词表命中 {len(matched)} 个术语")

            # 命中新术语 → 热更新 Whisper 热词，下一轮识别更准
            if new_terms:
                self.update_terminology()
        except Exception as e:
            logger.error(f"本地词表术语匹配失败: {e}")

        # ---- 通道 2：LLM 提取 ----
        try:
            from llm.extractor import TermExtractor
            extractor = TermExtractor()

            if not extractor.is_available_cached():
                logger.debug("LLM 不可用，跳过 AI 术语提取")
                return

            terms = extractor.extract_terms(
                text=text,
                domain=self.course_config.get("domain", ""),
                course_name=self.course_config.get("name", ""),
                custom_terms=self.course_config.get("custom_terms", []),
            )

            added = False
            for term in terms:
                term["origin"] = "ai"
                self.on_term_extracted(term)
                w = term.get("word", "")
                if w and w not in self._discovered_terms:
                    self._discovered_terms.append(w)
                    added = True

            if terms:
                logger.info(f"AI 提取了 {len(terms)} 个术语")
            if added:
                self.update_terminology()

        except ImportError:
            logger.debug("LLM 提取器不可用，跳过 AI 术语提取")
        except Exception as e:
            logger.error(f"AI 术语提取失败: {e}")
