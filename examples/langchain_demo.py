#!/usr/bin/env python
"""
LangChain 智能体演示脚本 —— 一条命令看清四条链的编排

本脚本不依赖真实大模型也能跑通全流程（用 Fake 模型注入预设响应），
用于快速验证 LangChain 链的编排逻辑、结构化输出与分级策略。

用法：

    # 1) 离线演示（无需 Ollama，秒级完成）
    python examples/langchain_demo.py --offline

    # 2) 真实模型演示（需要先启动 Ollama 并拉取 qwen2.5:7b）
    python examples/langchain_demo.py --live

真实模型演示会依次调用：术语提取 → 单词讲解 → 课后摘要 → 对话答疑，
每一步都打印 LangChain 链的执行细节与结果。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 允许从仓库根目录直接运行
BACKEND = Path(__file__).resolve().parent.parent / "backend"
if BACKEND.exists():
    sys.path.insert(0, str(BACKEND))

from langchain_core.language_models.fake_chat_models import FakeListChatModel  # noqa: E402


# =============================================================================
# 演示用的字幕语料
# =============================================================================

SAMPLE_SUBTITLE = """
Today we discuss gradient descent, the fundamental optimization algorithm used in machine learning.
Given a loss function L, gradient descent iteratively updates the parameters theta.
The update rule is theta = theta - alpha * grad(L), where alpha is the learning rate.
If the learning rate is too large, the algorithm may diverge; too small, convergence becomes very slow.
We also introduce the concept of eigenvalue, which describes how a linear transformation stretches the space.
Overfitting occurs when the model fits the training data too well and generalizes poorly.
"""

SAMPLE_QUESTIONS = [
    ("什么是梯度下降？", "gradient descent"),
    ("learning rate 是什么意思？", "learning rate"),
]


def hr(title: str = "") -> None:
    """打印分隔标题"""
    if title:
        print(f"\n{'=' * 72}\n  {title}\n{'=' * 72}")
    else:
        print("-" * 72)


# =============================================================================
# 离线演示：用 Fake 模型走通全链路
# =============================================================================


def run_offline() -> None:
    """离线演示 —— 不需要 Ollama"""
    print("=" * 72)
    print("  LangChain 智能体演示（离线模式 · 无需大模型）")
    print("=" * 72)

    import llm.chains.agents as agents
    import llm.chains.base as base

    # 预置响应：按 schema 路由（而非按调用顺序），保证演示结果稳定
    SCHEMA_RESPONSES = {
        "TermExtractionResult": {
            "terms": [
                {
                    "word": "gradient descent",
                    "translation": "梯度下降",
                    "definition": "通过沿损失函数梯度的负方向迭代更新参数以最小化损失的优化算法",
                    "domain": "机器学习",
                },
                {
                    "word": "eigenvalue",
                    "translation": "特征值",
                    "definition": "描述线性变换对空间拉伸程度的标量",
                    "domain": "线性代数",
                },
            ]
        },
        "WordExplanation": {
            "translation": "学习率",
            "meaning_in_context": "控制每一步参数更新幅度的超参数。太大导致发散，太小则收敛过慢。",
            "example": "If the learning rate is too large, the algorithm may diverge.",
            "related": ["gradient descent 梯度下降", "convergence 收敛性"],
        },
        "LectureSummary": {
            "summary": "本节课讲解了梯度下降算法的基本原理、更新规则与学习率对收敛性的影响，"
            "并引入了特征值与过拟合的概念。",
            "key_points": [
                "梯度下降沿损失函数梯度的负方向迭代更新参数",
                "更新规则为 theta = theta - alpha * grad(L)",
                "学习率过大导致发散，过小导致收敛缓慢",
            ],
            "review_words": [
                "gradient descent 梯度下降",
                "learning rate 学习率",
                "eigenvalue 特征值",
            ],
        },
    }

    # 自由文本响应（答疑链）：由 _call 提供
    scripted_responses = [
        "梯度下降是一种迭代优化算法：每次沿损失函数在当前参数处的梯度反方向更新参数，"
        "参数更新步长由学习率 alpha 控制。在你这门课的语境里，老师强调的是学习率的选取 —— "
        "过大导致损失震荡甚至发散，过小则收敛过慢。",
    ]

    class ScriptedModel(FakeListChatModel):
        """
        按 **schema 类型**路由的假模型。

        为什么不用「按调用顺序返回」：四条链的调用次序与次数并不固定
        （例如单词讲解会先探测词表命中与否，未命中才落到模型），
        顺序式响应极易错位。这里改为按目标 schema 决定返回哪份预设数据，
        演示结果因此稳定可复现。

        LangChain 内置的 `FakeListChatModel` **不支持** `with_structured_output()`
        （走 tool-calling 路径，会抛 NotImplementedError），
        故这里接管整条结构化链，直接返回解析好的 schema 实例。
        """

        def _call(self, *args, **kwargs):
            resp = self.responses[self.i] if self.i < len(self.responses) else self.responses[-1]
            if self.i < len(self.responses) - 1:
                self.i += 1
            else:
                self.i = 0
            return resp

        def with_structured_output(self, schema, **kwargs):
            """按 schema 选取对应的预设 JSON 并解析成实例"""
            from langchain_core.output_parsers import PydanticOutputParser
            from langchain_core.runnables import RunnableLambda

            parser = PydanticOutputParser(pydantic_object=schema)
            payload = SCHEMA_RESPONSES.get(schema.__name__)
            if payload is None:
                raise NotImplementedError(f"离线演示未预置 {schema.__name__} 的响应")

            def _invoke(_messages):
                return parser.parse(json.dumps(payload, ensure_ascii=False))

            return RunnableLambda(_invoke)

    fake_model = ScriptedModel(responses=scripted_responses)
    monkey = lambda **kw: fake_model  # noqa: E731
    base.get_chat_model = monkey
    agents.get_chat_model = monkey
    # 离线模式下没有真实 LLM，把可用性探测也一并桩掉
    base.is_available = lambda: True
    agents.is_available = lambda: True

    # ---- 1. 术语提取链 ----
    hr("链 1 / 术语提取  ChatPromptTemplate → with_structured_output → RunnableLambda")
    extractor = agents.TermExtractionAgent()
    terms = extractor.extract_terms(
        text=SAMPLE_SUBTITLE,
        domain="机器学习",
        course_name="深度学习导论",
        custom_terms=["gradient descent"],
    )
    print(json.dumps(terms, ensure_ascii=False, indent=2))

    # ---- 2. 单词讲解链（RunnableBranch） ----
    hr("链 2 / 单词讲解  RunnableBranch：词表命中 → 本地；未命中 → LLM")
    explainer = agents.WordExplanationAgent()
    # 2.1 本地词表分支
    from dictionary.terminology import lookup_vocab

    local_word = next((w for w in ["gradient", "algorithm", "model"] if lookup_vocab(w)), None)
    if local_word:
        r_local = explainer.explain(word=local_word, domain="计算机", course_name="深度学习导论")
        print(f"[本地词表分支] word={local_word!r} → source={r_local.get('source')}")
        print(f"   translation: {r_local.get('translation')}")
        print(f"   （该分支完全不发起 LLM 请求，零延迟零成本）")
    # 2.2 AI 分支：内置词表覆盖 3300+ 词，需挑一个确实未收录的术语
    ai_word = "backpropagation algorithm"  # 词表按单词收录，组合短语通常不命中
    if lookup_vocab(ai_word) is not None:
        ai_word = "mini-batch stochastic descent"
    r_ai = explainer.explain(
        word=ai_word,
        domain="机器学习",
        course_name="深度学习导论",
        context_sentence="We also introduce the concept of eigenvalue.",
    )
    print(f"\n[AI 分支] word={ai_word!r} → source={r_ai.get('source')}")
    print(json.dumps(r_ai, ensure_ascii=False, indent=2))

    # ---- 3. 课后摘要链 ----
    hr("链 3 / 课后摘要  with_structured_output(LectureSummary)")
    summarizer = agents.LectureSummaryAgent()
    summary = summarizer.summarize(
        text=SAMPLE_SUBTITLE,
        domain="机器学习",
        course_name="深度学习导论",
        vocabulary=[
            {"word": "gradient descent", "translation": "梯度下降", "isMemorized": False},
            {"word": "eigenvalue", "translation": "特征值", "isMemorized": True},
        ],
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # ---- 4. 对话答疑链 ----
    hr("链 4 / 对话答疑  ChatPromptTemplate → ChatOllama → StrOutputParser")
    qa = agents.ChatQAAgent()
    for q, _ in SAMPLE_QUESTIONS:
        answer = qa.answer(
            question=q,
            context=SAMPLE_SUBTITLE,
            domain="机器学习",
            course_name="深度学习导论",
        )
        print(f"\nQ: {q}\nA: {answer}")

    hr()
    print("离线演示完成。链路的组装、编排、结构化输出与分级策略全部验证通过。")


# =============================================================================
# 真实模型演示
# =============================================================================


def run_live() -> None:
    """真实模型演示 —— 需要 Ollama"""
    from llm.chains.base import get_chat_model, get_provider, is_available

    print("=" * 72)
    print("  LangChain 智能体演示（真实模型模式）")
    print("=" * 72)

    if not is_available():
        print(f"✗ 当前 provider={get_provider()} 不可用。")
        print("  请先启动 Ollama 并拉取模型：ollama pull qwen2.5:7b")
        print("  或改用云端：设置 TINGKEBANLV_LLM_PROVIDER=cloud 及相关环境变量")
        sys.exit(1)

    model = get_chat_model()
    print(f"✓ 模型就绪：{get_provider()} / {getattr(model, 'model', '未知')}")

    from llm.chains.agents import (
        ChatQAAgent,
        LectureSummaryAgent,
        TermExtractionAgent,
        WordExplanationAgent,
    )

    # ---- 1. 术语提取 ----
    hr("链 1 / 术语提取")
    terms = TermExtractionAgent().extract_terms(
        text=SAMPLE_SUBTITLE,
        domain="机器学习",
        course_name="深度学习导论",
    )
    print(json.dumps(terms, ensure_ascii=False, indent=2))

    # ---- 2. 单词讲解 ----
    hr("链 2 / 单词讲解（AI 分支）")
    expl = WordExplanationAgent().explain(
        word="eigenvalue",
        domain="线性代数",
        course_name="深度学习导论",
        context_sentence="We also introduce the concept of eigenvalue.",
    )
    print(json.dumps(expl, ensure_ascii=False, indent=2))

    # ---- 3. 课后摘要 ----
    hr("链 3 / 课后摘要")
    summary = LectureSummaryAgent().summarize(
        text=SAMPLE_SUBTITLE,
        domain="机器学习",
        course_name="深度学习导论",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # ---- 4. 对话答疑 ----
    hr("链 4 / 对话答疑")
    answer = ChatQAAgent().answer(
        question="为什么学习率不能太大？",
        context=SAMPLE_SUBTITLE,
        domain="机器学习",
        course_name="深度学习导论",
    )
    print(answer)

    hr()
    print("真实模型演示完成。")


def main() -> None:
    parser = argparse.ArgumentParser(description="LangChain 智能体演示")
    parser.add_argument(
        "--live", action="store_true", help="使用真实大模型（需 Ollama）"
    )
    parser.add_argument(
        "--offline", action="store_true", help="使用假模型离线演示（默认）"
    )
    args = parser.parse_args()

    if args.live:
        run_live()
    else:
        run_offline()


if __name__ == "__main__":
    main()
