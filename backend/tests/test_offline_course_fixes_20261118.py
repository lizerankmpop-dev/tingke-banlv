# -*- coding: utf-8 -*-
"""线下课实测 5 个问题的后端防回归（2026-11-18）

① far-field 片段上限回落到 6s 甜点，不再 +2s 放宽到 8s
② fast 档只收紧静音判定，不把 6s 片段再 ×0.7 打进准确率断崖
③ /export-md 新增：Markdown 导出，带时间标记 + 译文，不需要 offsetMs
④ /summary 与 translation_status 的降级提示带可自助的模型/拉取命令
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app

BACKEND = Path(__file__).resolve().parents[1]
SESSION = "offline-fix-20261118"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def course_id(client):
    resp = client.post("/api/courses", json={
        "name": f"线下课修复-{uuid.uuid4().hex[:6]}", "courseType": "offline",
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_far_field_max_speech_is_six_seconds():
    """far-field 不再 +2.0s：6s 是归因文档验证过的延迟-准确率甜点。"""
    from audio.vad import VoiceActivityDetector, MAX_SPEECH_DURATION

    near = VoiceActivityDetector(on_speech_segment=lambda *a: None, profile="near")
    far = VoiceActivityDetector(on_speech_segment=lambda *a: None, profile="far-field")
    assert near._max_speech_duration == MAX_SPEECH_DURATION
    assert far._max_speech_duration == MAX_SPEECH_DURATION, "far-field 又被放宽了，延迟会回到三四句"
    assert MAX_SPEECH_DURATION == pytest.approx(6.0), "6s 甜点被动过"


def test_fast_profile_keeps_max_speech():
    """fast 档不能再把最长片段 ×0.7（6s→4.2s 落入断崖区）。"""
    from audio.vad import VoiceActivityDetector, MAX_SPEECH_DURATION

    v = VoiceActivityDetector(on_speech_segment=lambda *a: None, profile="far-field")
    before_silence = v._silence_duration
    v.apply_latency_profile("fast")
    assert v._silence_duration < before_silence, "fast 应收紧静音判定"
    assert v._max_speech_duration == pytest.approx(MAX_SPEECH_DURATION), \
        "fast 把片段切碎会触发准确率断崖（5s 中文 CER差 39%）"


def test_export_markdown(client, course_id, monkeypatch):
    """MD 导出：时间戳 + 原文 + 译文；按会话；不需要 offsetMs。"""
    from api import routes

    rows = [
        {"id": "s1", "text": "同学们好", "start_ms": 2500, "end_ms": 4000,
         "is_final": True, "session_id": SESSION, "translation": "Hello class"},
        {"id": "s2", "text": "今天我们讲梯度下降", "start_ms": 65000, "end_ms": 68000,
         "is_final": True, "session_id": SESSION},
    ]

    async def fake_get_subtitles(cid, limit=100, chapter_id=None):
        return list(reversed(rows))  # 模仿 DB 倒序

    monkeypatch.setattr(routes.db, "get_subtitles", fake_get_subtitles)
    resp = client.get(f"/api/courses/{course_id}/export-md?sessionId={SESSION}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["count"] == 2
    assert "**[00:00:02]** 同学们好" in data["markdown"]
    assert "**[00:01:05]** 今天我们讲梯度下降" in data["markdown"]
    assert "> Hello class" in data["markdown"], "译文要作为引用行保留"


def test_export_markdown_empty(client, course_id, monkeypatch):
    """没有字幕时 count=0，前端据此提示「没有可导出的字幕」。"""
    from api import routes

    async def fake_get_subtitles(cid, limit=100, chapter_id=None):
        return []

    monkeypatch.setattr(routes.db, "get_subtitles", fake_get_subtitles)
    resp = client.get(f"/api/courses/{course_id}/export-md")
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


def test_summary_unavailable_hint_has_model(client, course_id, monkeypatch):
    """/summary 降级提示要告诉用户拉哪个模型（原来只说「拉取了模型」）。"""
    from api import routes

    async def fake_get_subtitles(cid, limit=100, chapter_id=None):
        return [{"id": "x", "text": "测试字幕", "start_ms": 0, "end_ms": 100, "is_final": True}]

    async def fake_get_vocabulary(cid):
        return []

    monkeypatch.setattr(routes.db, "get_subtitles", fake_get_subtitles)
    monkeypatch.setattr(routes.db, "get_vocabulary", fake_get_vocabulary)
    # 隔离运行时 LLM 配置：其它测试（D4）可能把 provider 设成 cloud 未清理，
    # 导致本用例的降级提示走错分支；这里显式清零，保证确定性走 ollama 分支。
    from llm.chains import base as _base
    _base.set_runtime_llm_config({
        "provider": "", "cloudApiBase": "", "cloudApiKey": "", "cloudModel": "",
        "ollamaModel": "",
    })
    monkeypatch.setattr(routes, "_llm_call", lambda *a, **k: lambda *x: None)
    resp = client.post(f"/api/courses/{course_id}/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert "Ollama" in body["message"] or "云端" in body["message"]
    assert "ollama pull" in body["message"] or "TINGKEBANLV_CLOUD" in body["message"]
