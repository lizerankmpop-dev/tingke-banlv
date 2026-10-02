"""
AI 术语提取器
使用 Ollama 本地大模型从字幕中提取专业术语并生成中文释义

工作原理：
1. 接收累积的字幕文本
2. 结合课程领域信息构建 prompt
3. 调用 Ollama 提取术语 → 返回 {word, translation, definition, domain}
4. 结果自动写入课程词典
"""

import json
import logging
import re
from typing import Optional

from llm.client import chat as llm_chat, is_available as llm_available

logger = logging.getLogger(__name__)

# 提取 prompt 模板
EXTRACTION_PROMPT = """You are a professional term extraction assistant. 

Your task: Analyze the following course subtitle text and extract important technical or domain-specific terms.

Course: {course_name}
Domain: {domain}
Known terms (prioritize these): {custom_terms}

Subtitle text:
---
{text}
---

Instructions:
1. Extract 3-8 important technical terms, jargon, or domain-specific vocabulary
2. For each term, provide:
   - word: the original English term
   - translation: concise Chinese translation (简体中文)
   - definition: brief explanation in Chinese (1-2 sentences)
   - domain: the academic field
3. Skip common words (the, is, are, etc.) and very basic vocabulary
4. Focus on terms that a student in this course would need to learn
5. If the text contains no specialized terms, return an empty list

Respond ONLY with a JSON array (no other text):
[
  {{
    "word": "gradient descent",
    "translation": "梯度下降",
    "definition": "一种通过迭代最小化损失函数来优化机器学习模型参数的算法",
    "domain": "机器学习"
  }}
]"""


class TermExtractor:
    """
    基于 Ollama 的术语提取器
    从字幕上下文中提取专业词汇
    """

    def __init__(self, model: str = None):
        # 模型名由统一 client 管理（环境变量 TINGKEBANLV_OLLAMA_MODEL / TINGKEBANLV_CLOUD_MODEL）
        self.model = model
        self._available: Optional[bool] = None

    def is_available_cached(self) -> bool:
        """
        查 LLM 是否可用（结果缓存，避免每条字幕都探测一次）

        注意：探测结果一经确定为 False 会保持到进程重启；
        若用户在运行中才启动 Ollama，重启后端即可恢复。
        """
        if self._available is None:
            self._available = llm_available()
        return self._available

    def extract_terms(
        self,
        text: str,
        domain: str = "",
        course_name: str = "",
        custom_terms: list[str] = None,
    ) -> list[dict]:
        """
        从字幕文本中提取术语

        Args:
            text: 字幕文本
            domain: 课程领域
            course_name: 课程名称
            custom_terms: 用户自定义术语列表

        Returns:
            术语列表 [{word, translation, definition, domain, context_sentence}]
        """
        if not text.strip():
            return []

        # 检查 LLM 可用性（结果缓存）
        if not self.is_available_cached():
            logger.warning("LLM 不可用，跳过术语提取")
            return []

        # 构建 prompt
        prompt = EXTRACTION_PROMPT.format(
            course_name=course_name or "未知课程",
            domain=domain or "通用",
            custom_terms=", ".join(custom_terms) if custom_terms else "无",
            text=text[:2000],  # 限制长度
        )

        try:
            content = llm_chat(
                messages=[
                    {
                        "role": "system",
                        "content": "You are a precise term extraction assistant. Always respond with valid JSON only.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                num_predict=1024,
            )
            if content is None:
                self._available = False
                return []

            terms = self._parse_response(content, text)

            logger.info(f"提取到 {len(terms)} 个术语")
            return terms

        except Exception as e:
            logger.error(f"LLM 术语提取失败: {e}")
            return []

    def _parse_response(self, content: str, source_text: str) -> list[dict]:
        """解析 Ollama 返回的 JSON"""
        try:
            # 尝试直接解析
            terms = json.loads(content)
            if isinstance(terms, list):
                # 为每个术语添加上下文句子
                for term in terms:
                    if isinstance(term, dict) and "word" in term:
                        term["context_sentence"] = self._find_context(
                            term["word"], source_text
                        )
                return terms
        except json.JSONDecodeError:
            pass

        # 尝试提取 JSON 代码块
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', content)
        if json_match:
            try:
                terms = json.loads(json_match.group(1))
                if isinstance(terms, list):
                    for term in terms:
                        if isinstance(term, dict) and "word" in term:
                            term["context_sentence"] = self._find_context(
                                term["word"], source_text
                            )
                    return terms
            except json.JSONDecodeError:
                pass

        # 尝试用正则提取 JSON 数组
        array_match = re.search(r'\[[\s\S]*\]', content)
        if array_match:
            try:
                terms = json.loads(array_match.group(0))
                if isinstance(terms, list):
                    return terms
            except json.JSONDecodeError:
                pass

        logger.warning(f"无法解析 Ollama 响应: {content[:200]}")
        return []

    def _find_context(self, word: str, text: str) -> str:
        """在原文中查找包含该词的句子"""
        sentences = re.split(r'[.!?。！？]+', text)
        for sentence in sentences:
            if word.lower() in sentence.lower():
                return sentence.strip()
        return ""
