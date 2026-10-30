"""
LangChain 提示词模板层

把原先散落在各模块顶层的裸字符串常量 + `.format()` 调用，
统一收敛为 LangChain 的 `ChatPromptTemplate`。

相比手写 `.format()` 的三点改进：
1. **构图期校验**：模板变量与实际传入的 key 不匹配时立即抛错，
   而不是在运行时把 `{course_name}` 原样发给模型。
2. **角色分离**：system 与 human 用 MessagesPlaceholder 显式区分，
   这正是 LangChain 多轮对话与结构化输出的基础。
3. **可组合**：模板是 Runnable，可直接用 `|` 与模型、解析器串联成链。

所有提示词的业务内容与迁移前逐字保持一致，确保模型行为不变。
"""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

# =============================================================================
# 一、对话式答疑（对应 llm/qa.py）
# =============================================================================

QA_SYSTEM = (
    "You are a patient bilingual teaching assistant. "
    "Answer concisely in Chinese."
)

QA_HUMAN = """You are a patient teaching assistant helping a Chinese student follow a foreign course in real time.

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

QA_PROMPT_TEMPLATE = ChatPromptTemplate.from_messages(
    [
        ("system", QA_SYSTEM),
        ("human", QA_HUMAN),
    ]
)


# =============================================================================
# 二、课后摘要（对应 llm/summarizer.py）
# =============================================================================

SUMMARY_SYSTEM = (
    "You are a precise lecture summarizer. Always respond with valid JSON only."
)

SUMMARY_HUMAN = """You are a study assistant helping a Chinese student review a foreign course lecture.

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

SUMMARY_PROMPT_TEMPLATE = ChatPromptTemplate.from_messages(
    [
        ("system", SUMMARY_SYSTEM),
        ("human", SUMMARY_HUMAN),
    ]
)


# =============================================================================
# 三、单词讲解（对应 llm/explainer.py）
# =============================================================================

EXPLAIN_SYSTEM = (
    "You are a precise bilingual dictionary assistant. Always respond with valid JSON only."
)

EXPLAIN_HUMAN = """You are a professional bilingual study assistant helping a Chinese student follow a foreign course.

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
  "meaning_in_context": "在这门课里指损失函数对参数的偏导数向量，用于描述最陡上升方向。",
  "example": "We compute the gradient of the loss before each update.",
  "related": ["derivative 导数", "backpropagation 反向传播"]
}}"""

EXPLAIN_PROMPT_TEMPLATE = ChatPromptTemplate.from_messages(
    [
        ("system", EXPLAIN_SYSTEM),
        ("human", EXPLAIN_HUMAN),
    ]
)


# =============================================================================
# 四、术语提取（对应 llm/extractor.py）
# =============================================================================

EXTRACTION_SYSTEM = (
    "You are a precise term extraction assistant. Always respond with valid JSON only."
)

EXTRACTION_HUMAN = """You are a professional term extraction assistant.

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

EXTRACTION_PROMPT_TEMPLATE = ChatPromptTemplate.from_messages(
    [
        ("system", EXTRACTION_SYSTEM),
        ("human", EXTRACTION_HUMAN),
    ]
)


__all__ = [
    "QA_PROMPT_TEMPLATE",
    "SUMMARY_PROMPT_TEMPLATE",
    "EXPLAIN_PROMPT_TEMPLATE",
    "EXTRACTION_PROMPT_TEMPLATE",
]
