"""
AI 课后摘要生成器
使用 Ollama 本地大模型，将一节课的字幕记录浓缩为摘要 + 知识点 + 生词复习清单
"""

import json
import logging
import re

from llm.client import chat as llm_chat, is_available as llm_available

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = """You are a study assistant helping a Chinese student review a foreign course lecture.

Course: {course_name}
Domain: {domain}

Transcribed lecture text (most recent, in chronological order):
---
{text}
---

Vocabulary the student hasn't memorized yet (word: translation):
---
{vocab}
---

Instructions:
1. Write everything in simplified Chinese (technical terms may keep English)
2. Respond ONLY with a JSON object (no other text) with these fields:
   - summary: 本节课内容摘要（3-5 句）
   - key_points: 知识点列表（3-8 条，每条一句话）
   - review_words: 建议复习的重点生词（最多 6 个，从上面的 Vocabulary 中挑选，或从正文中挑生词，格式 "word 中文意思"）

Example response:
{{
  "summary": "本节课介绍了……",
  "key_points": ["……", "……"],
  "review_words": ["gradient 梯度", "overfitting 过拟合"]
}}"""


class LectureSummarizer:
    """课后摘要生成器（LLM 由统一 client 管理）"""

    def __init__(self, model: str = None):
        self._available: bool | None = None

    def summarize(
        self,
        text: str,
        domain: str = "",
        course_name: str = "",
        vocabulary: list[dict] = None,
    ) -> dict | None:
        """
        生成课后摘要

        Args:
            text: 字幕全文（已按时间正序拼接）
            domain: 课程领域
            course_name: 课程名称
            vocabulary: 课程词汇表 [{word, translation, isMemorized}]

        Returns:
            {summary, key_points[], review_words[]} 或 None
        """
        if not text.strip():
            return None

        # 检查 LLM 可用性（结果缓存）
        if self._available is None:
            self._available = llm_available()
        if not self._available:
            logger.warning("LLM 不可用，跳过摘要生成")
            return None

        # 未掌握词汇优先
        vocab_lines = []
        for v in (vocabulary or []):
            if not v.get("isMemorized"):
                vocab_lines.append(f"{v.get('word', '')}: {v.get('translation', '')}")
        vocab_text = "\n".join(vocab_lines[:30]) if vocab_lines else "（暂无）"

        prompt = SUMMARY_PROMPT.format(
            course_name=course_name or "未知课程",
            domain=domain or "通用",
            text=text[-6000:],  # 限制长度（取最近的内容）
            vocab=vocab_text,
        )

        try:
            content = llm_chat(
                messages=[
                    {
                        "role": "system",
                        "content": "You are a precise lecture summarizer. Always respond with valid JSON only.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                num_predict=1536,
            )
            if content is None:
                self._available = False
                return None

            return self._parse_response(content)

        except Exception as e:
            logger.error(f"摘要生成失败: {e}")
            return None

    def _parse_response(self, content: str) -> dict | None:
        """解析 Ollama 返回的 JSON 对象"""
        candidates = [content]

        m = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', content)
        if m:
            candidates.append(m.group(1))

        m = re.search(r'\{[\s\S]*\}', content)
        if m:
            candidates.append(m.group(0))

        for candidate in candidates:
            try:
                obj = json.loads(candidate)
                if isinstance(obj, dict) and "summary" in obj:
                    return {
                        "summary": str(obj.get("summary", "")),
                        "key_points": [str(p) for p in obj.get("key_points", [])][:8],
                        "review_words": [str(w) for w in obj.get("review_words", [])][:6],
                    }
            except json.JSONDecodeError:
                continue

        logger.warning(f"无法解析摘要响应: {content[:200]}")
        return None
