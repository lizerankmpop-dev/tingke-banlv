"""
AI 单词讲解器
使用 Ollama 本地大模型，结合课程领域和字幕上下文，生成单词的深度讲解

与 local_dict / extractor 的分工：
- local_dict: 离线查表（基础词表，无需 AI）
- extractor:  从字幕流中批量提取术语
- explainer:  用户点击单词时，按需生成"这个词在这门课里是什么意思"
"""

import json
import logging
import re

from llm.client import chat as llm_chat, is_available as llm_available

logger = logging.getLogger(__name__)

# 讲解 prompt 模板
EXPLAIN_PROMPT = """You are a professional bilingual study assistant helping a Chinese student follow a foreign course.

The student just clicked on a word while reading live subtitles.

Word: "{word}"
Course: {course_name}
Domain: {domain}
Sentence where the word appeared (may be empty):
"{context}"

Instructions:
1. Explain this word FOR THIS COURSE CONTEXT, in simplified Chinese
2. Respond ONLY with a JSON object (no other text) with these fields:
   - translation: 简洁的中文翻译（该词最常见的意思）
   - meaning_in_context: 在上述课程语境中的具体含义（2-3 句，说明老师在这里讲的是什么）
   - example: 一个简短的英文例句（与课程领域相关）及其中文翻译
   - related: 相关词/近义词列表（最多 4 个，中英对照，如 "gradient 梯度"）
3. If the "word" is actually a common word with no special meaning here, still explain it but keep it brief
4. If the word is a technical term, prioritize the technical meaning in this domain

Example response:
{{
  "translation": "梯度",
  "meaning_in_context": "在本课中，gradient 指损失函数的梯度……",
  "example": "We compute the gradient of the loss function. 我们计算损失函数的梯度。",
  "related": ["slope 斜率", "derivative 导数", "gradient descent 梯度下降"]
}}"""


class WordExplainer:
    """单词讲解器（按需调用，带内存缓存；LLM 由统一 client 管理）"""

    def __init__(self, model: str = None):
        self._cache: dict[str, dict] = {}  # key: word|context_hash
        self._available: bool | None = None

    def explain(
        self,
        word: str,
        domain: str = "",
        course_name: str = "",
        context_sentence: str = "",
    ) -> dict | None:
        """
        生成单词的上下文讲解

        Returns:
            {translation, meaning_in_context, example, related[]} 或 None（不可用/失败）
        """
        word = word.strip()
        if not word:
            return None

        # 缓存命中（相同词 + 相同上下文才复用）
        cache_key = f"{word.lower()}|{hash(context_sentence)}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # 检查 LLM 可用性（结果缓存）
        if self._available is None:
            self._available = llm_available()
        if not self._available:
            logger.warning("LLM 不可用，无法生成 AI 讲解")
            return None

        prompt = EXPLAIN_PROMPT.format(
            word=word,
            course_name=course_name or "未知课程",
            domain=domain or "通用",
            context=context_sentence or "（无上下文）",
        )

        try:
            content = llm_chat(
                messages=[
                    {
                        "role": "system",
                        "content": "You are a precise bilingual dictionary assistant. Always respond with valid JSON only.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                num_predict=768,
            )
            if content is None:
                self._available = False
                return None

            result = self._parse_response(content)
            if result:
                # 限制缓存大小
                if len(self._cache) > 200:
                    self._cache.clear()
                self._cache[cache_key] = result
            return result

        except Exception as e:
            logger.error(f"AI 讲解生成失败: {e}")
            return None

    def _parse_response(self, content: str) -> dict | None:
        """解析 Ollama 返回的 JSON 对象"""
        candidates = [content]

        # 尝试提取 ```json ... ``` 代码块
        m = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', content)
        if m:
            candidates.append(m.group(1))

        # 尝试提取第一个 {...} 对象
        m = re.search(r'\{[\s\S]*\}', content)
        if m:
            candidates.append(m.group(0))

        for candidate in candidates:
            try:
                obj = json.loads(candidate)
                if isinstance(obj, dict) and ("translation" in obj or "meaning_in_context" in obj):
                    # 规范化字段
                    related = obj.get("related", [])
                    if isinstance(related, str):
                        related = [related]
                    return {
                        "translation": str(obj.get("translation", "")),
                        "meaning_in_context": str(obj.get("meaning_in_context", "")),
                        "example": str(obj.get("example", "")),
                        "related": [str(r) for r in related][:4],
                    }
            except json.JSONDecodeError:
                continue

        logger.warning(f"无法解析 AI 讲解响应: {content[:200]}")
        return None
