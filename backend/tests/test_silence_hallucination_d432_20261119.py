# -*- coding: utf-8 -*-
"""静音幻觉字幕过滤守卫（v4.3.2）

视频暂停后 Whisper 在近静音上输出「轻轻轻轻…」单字循环，现有成句重复
检测（≥35 字符）抓不到；这条守的是新增的短路过滤，同时防误杀叠词/拟声词。
"""
from __future__ import annotations

import pytest

from asr.repetition import is_silence_hallucination


@pytest.mark.parametrize("text", [
    "进行轻轻轻轻轻轻轻轻轻轻轻轻轻轻轻轻轻轻轻轻轻轻轻轻轻轻轻轻轻轻轻轻",
    "啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊",
    "学习学习学习学习学习学习学习学习学习学习",
])
def test_silence_hallucination_is_filtered(text):
    assert is_silence_hallucination(text) is True


@pytest.mark.parametrize("text", [
    "今天我们来讲梯度下降算法，它是机器学习中常用的优化方法。",
    "轻轻轻轻，轻轻地放在桌上。",
    "哈哈哈，这个玩笑很好笑。",
    "Gradient descent is a common optimization algorithm in machine learning.",
    "好的",
    "",
])
def test_normal_utterances_are_kept(text):
    assert is_silence_hallucination(text) is False


def test_pipeline_wires_hallucination_filter():
    from pathlib import Path
    pipeline = Path(__file__).resolve().parents[1] / "asr" / "pipeline.py"
    src = pipeline.read_text(encoding="utf-8")
    assert "is_silence_hallucination" in src
    assert "丢弃静音幻觉字幕" in src
