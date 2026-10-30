"""
LangChain 智能体（Agent）—— 让答疑具备「自主调用工具」的能力

背景
----
前述四条链属于 **LCEL 编排（Chain）**：调用路径由开发者预先确定，
模型只负责在既定步骤内填充内容。

而 **Agent** 是 LangChain 的另一类范式：把「该做什么」的决定权交给模型，
模型在运行时自主选择调用哪些工具（Tool），直到得出答案。

本模块把「对话式答疑」从 Chain 升级为 Agent，因为答疑天然适合这一形态：

    学生：「eigenvalue 和特征向量什么关系？」
      ↓ 模型自主判断需要查术语库
    调用工具 lookup_term()  → 拿到课程词条
      ↓ 仍需查词表补基础释义
    调用工具 lookup_vocab() → 拿到基础词典释义
      ↓ 信息充足，生成回答

两条链并存，按场景切换：

- ``ChatQAAgent``  —— Agent 版（默认），可自主调用工具
- ``ChatQAChain`` —— 纯 LCEL 链版，无工具调用，延迟更低、更可控

为什么保留链版
--------------
Agent 模式每轮要多走一次「模型决策 → 工具调用 → 模型总结」的循环，
延迟与 token 消耗都更高。在「问题简单、上下文充分」的场景下，
纯链版表现更好。因此两条并存，由调用方按需选择，
默认走 Agent 版（作业要求体现 LangChain 的 Agent 能力）。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


# =============================================================================
# 一、工具定义
# =============================================================================
#
# 用 @tool 装饰器把普通 Python 函数暴露成 LangChain 工具。
# docstring 就是给模型看的「工具说明书」——写得越清楚，模型调用得越准。
# 这是 LangChain Tool 的关键设计：**描述即接口**。


@tool
def lookup_term(word: str) -> str:
    """查询课程专属术语库中的术语释义。

    当学生问到课程里的专业术语（如 eigenvalue、gradient descent）
    且该术语已被本课记录过时，用这个工具获取它在**本课程语境**下的含义。

    Args:
        word: 要查询的术语（英文单词或短语，大小写不敏感）

    Returns:
        术语的释义与出处；若术语库中没有则明确说明未收录。
    """
    from dictionary.terminology import lookup_vocab

    word = (word or "").strip()
    if not word:
        return "未提供要查询的术语。"

    result = lookup_vocab(word)
    if not result:
        return f"术语库中未收录「{word}」。"

    parts = [f"「{word}」：{result.get('translation', '')}"]
    definition = result.get("definition", "")
    if definition:
        parts.append(definition)
    example = result.get("example", "")
    if example:
        parts.append(f"例：{example}")
    related = result.get("related") or []
    if related:
        parts.append("相关词：" + "、".join(str(r) for r in related[:4]))
    return " | ".join(p for p in parts if p)


@tool
def match_course_terms(text: str) -> str:
    """在给定的字幕文本中匹配课程术语库里的专业术语。

    适用于「这句话里的专业词都是什么」这类问题，
    或需要确认某段字幕涉及哪些知识点时。

    Args:
        text: 待分析的文本（通常是学生的提问或最近的字幕片段）

    Returns:
        命中的术语列表；若未命中任何术语则明确说明。
    """
    from dictionary.terminology import match_terms_in_text

    text = (text or "").strip()
    if not text:
        return "未提供待分析文本。"

    try:
        matches = match_terms_in_text(text, max_terms=8)
    except Exception as e:  # noqa: BLE001 —— 工具内部失败不应中断整个 Agent
        return f"术语匹配失败：{e}"

    if not matches:
        return "未在文本中匹配到课程术语。"

    lines = []
    for m in matches[:8]:
        word = m.get("word") or m.get("term") or ""
        translation = m.get("translation", "")
        sentence = (m.get("sentence") or "")[:80]
        if word:
            lines.append(f"- {word} {translation}".rstrip())
            if sentence:
                lines.append(f"  原句：{sentence}")
    return "\n".join(lines) if lines else "未在文本中匹配到课程术语。"


@tool
def search_subtitle_context(keyword: str, context: str) -> str:
    """在字幕上下文中定位包含指定关键词的片段。

    当学生问「老师刚才说的那句 XX 是什么意思」时，
    用这个工具从字幕里找出对应的句子，避免模型凭空猜测。

    Args:
        keyword: 要在字幕中定位的关键词
        context: 最近的字幕文本（时间正序）

    Returns:
        包含该关键词的字幕片段；若未找到则说明字幕中未出现。
    """
    keyword = (keyword or "").strip()
    context = context or ""
    if not keyword:
        return "未提供关键词。"
    if not context.strip():
        return "暂无字幕上下文可供检索。"

    lowered = context.lower()
    needle = keyword.lower()
    idx = lowered.find(needle)
    if idx < 0:
        return f"字幕中未出现「{keyword}」。"

    # 向前回溯到句子起点，向后取到句子终点
    start = max(0, idx - 160)
    end = min(len(context), idx + len(keyword) + 160)
    snippet = context[start:end].strip()
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(context) else ""
    return f"{prefix}{snippet}{suffix}"


# =============================================================================
# 二、Agent 构建
# =============================================================================

AGENT_SYSTEM_PROMPT = """You are a patient bilingual teaching assistant helping a Chinese student follow a foreign course in real time.

Course: {course_name}
Domain: {domain}
Student's custom terms: {custom_terms}

The most recent transcribed subtitles (chronological order):
---
{context}
---

You have access to tools that look up terms in this course's terminology database,
match course terms in text, and search the subtitle context.

Instructions:
1. Use the tools when the question involves a specific technical term, when you need to
   verify what the lecturer actually said, or when the subtitles mention the concept.
   Do not guess when a tool can answer the question accurately.
2. If a tool returns no result, fall back on your own knowledge and say so.
3. Answer in simplified Chinese (technical terms may keep English, with Chinese in
   parentheses on first use).
4. Base your answer on the subtitle context above; if the context is insufficient,
   say so and give the general knowledge answer.
5. Be concise but complete — this is an in-class quick reference, not an essay
   (usually under 300 words).
6. If the student's question contains a typo (likely from ASR), infer the intended meaning.
7. Do not mention tool names or that you used tools; just answer naturally."""


def build_qa_agent(
    temperature: float = 0.4,
    num_predict: int = 1024,
    model: str | None = None,
):
    """
    构建「对话式答疑」智能体（LangChain Agent）。

    使用 LangChain 1.x 的 `create_agent()`：
    模型在运行时自主决定是否调用工具、调用哪个工具、调用几次，
    直到 `ToolStrategy` 认为信息足够后给出最终回答。

    Args:
        temperature: 采样温度
        num_predict: 最大生成 token 数
        model: 显式模型名

    Returns:
        可 invoke 的 Runnable，输入 dict（含 question / context 等），输出 AIMessage
    """
    from langchain.agents import create_agent

    from llm.chains.base import get_chat_model

    chat_model = get_chat_model(
        temperature=temperature, num_predict=num_predict, model=model
    )

    return create_agent(
        model=chat_model,
        tools=[lookup_term, match_course_terms, search_subtitle_context],
        system_prompt=AGENT_SYSTEM_PROMPT,
    )


__all__ = [
    "lookup_term",
    "match_course_terms",
    "search_subtitle_context",
    "build_qa_agent",
    "AGENT_SYSTEM_PROMPT",
]
