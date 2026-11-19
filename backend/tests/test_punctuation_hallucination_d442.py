# -*- coding: utf-8 -*-
"""v4.4.2：纯标点字幕幻觉过滤守卫。

真实线下课（2026-11-19）字幕全屏 `. . .`，根因是 Whisper 在近静音/低信噪比
输入上只输出一串标点。旧 is_silence_hallucination 把这些文本 normalize 后
变成空串，因 len<16 直接放行 ⇒ 满屏点字幕落库。本守卫锁住修复。
"""
from __future__ import annotations

import pytest


@pytest.mark.parametrize("text", [
    ". . . . . .",   # 截图里的真实产物
    ". . .",
    "。 。 。",
    "… … … …",
    "， ， ，",
    "..........",
    "？ ？ ？",
])
def test_pure_punctuation_is_hallucination(text):
    from asr.repetition import is_silence_hallucination
    assert is_silence_hallucination(text) is True, f"纯标点字幕未被过滤: {text!r}"


@pytest.mark.parametrize("text", [
    "老师讲的是生成式人工智能。",
    "嗯……这个模型",
    "Hello world...",
    "第一，第二，第三。",
    "这个——怎么说呢",
])
def test_real_content_with_punctuation_is_kept(text):
    """带标点的正常句子 normalize 后仍有内容字符，不得误杀。"""
    from asr.repetition import is_silence_hallucination
    assert is_silence_hallucination(text) is False, f"正常句子被误杀: {text!r}"


def test_pipeline_drops_pure_punctuation_before_subtitle(monkeypatch):
    """管道层：纯标点识别结果应在落库前被丢弃。"""
    from asr import pipeline as pl

    calls = {"status": []}

    p = pl.ASRPipeline.__new__(pl.ASRPipeline)
    p._subtitle_counter = 0
    p.on_status = lambda stage, msg: calls["status"].append((stage, msg))
    emitted = {}
    p.on_subtitle = lambda sub: emitted.update(sub)
    p._terminology = {"prompt": "", "sections": None, "stats": {"total": 0}}
    p._offline = False
    p._classroom_audio = None

    p._on_recognition_result(". . . .", [])
    assert p._subtitle_counter == 0, "纯标点不应计入字幕"
    assert not emitted, "纯标点不应产出字幕"
