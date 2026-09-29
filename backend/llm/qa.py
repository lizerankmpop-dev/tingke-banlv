"""
AI 对话式答疑
基于最近字幕上下文回答学生问题（"刚才老师讲的那段没听懂"场景）

设计要点：
- 上下文 = 最近 N 条字幕（时间正序拼接，截断控制长度）
- 不做多轮记忆，每次问答独立（悬浮窗轻量场景，避免状态管理复杂度）
- 课程信息（名称/领域/自定义术语）注入 prompt 提升回答针对性
"""

import logging

from llm.client import chat as llm_chat, is_available as llm_available

logger = logging.getLogger(__name__)

QA_PROMPT = """You are a patient teaching assistant helping a Chinese student follow a foreign course in real time.

Course: {course_name}
Domain: {domain}
Student's custom terms: {custom_terms}

The student is watching/listening to this lecture. Below are the most recent transcribed subtitles (chronological order):
---
{context}
---

The student asks: {question}

Instructions:
1. Answer in simplified Chinese (technical terms may keep English, with Chinese in parentheses on first use)
2. Base your answer on the subtitle context above; if the context is insufficient to answer, say so and give the general knowledge answer
3. Be concise but complete — this is an in-class quick reference, not an essay (usually under 300 words)
4. If the student's question contains a typo (likely from ASR), infer the intended meaning
5. If the question is about a specific term, explain it in this course's context"""


class ChatQA:
    """基于字幕上下文的对话答疑"""

    def __init__(self):
        self._available: bool | None = None

    def answer(
        self,
        question: str,
        context: str,
        domain: str = "",
        course_name: str = "",
        custom_terms: list[str] = None,
    ) -> str | None:
        """
        回答学生提问

        Args:
            question: 学生的问题
            context: 最近字幕文本（时间正序）
            domain: 课程领域
            course_name: 课程名称
            custom_terms: 自定义术语

        Returns:
            回答文本（Markdown 由前端按纯文本渲染），失败返回 None
        """
        question = question.strip()
        if not question:
            return None

        # 检查 LLM 可用性（结果缓存）
        if self._available is None:
            self._available = llm_available()
        if not self._available:
            logger.warning("LLM 不可用，无法答疑")
            return None

        prompt = QA_PROMPT.format(
            course_name=course_name or "未知课程",
            domain=domain or "通用",
            custom_terms=", ".join(custom_terms) if custom_terms else "无",
            context=context[-5000:] if context.strip() else "（暂无字幕，请结合课程领域通用知识回答）",
            question=question,
        )

        try:
            content = llm_chat(
                messages=[
                    {
                        "role": "system",
                        "content": "You are a patient bilingual teaching assistant. Answer concisely in Chinese.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.4,
                num_predict=1024,
            )
            if content is None:
                self._available = False
                return None
            return content

        except Exception as e:
            logger.error(f"答疑失败: {e}")
            return None
