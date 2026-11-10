# -*- coding: utf-8 -*-
"""集成分支契约守卫（2026-11-11 四分支合入后加）。

合入时 `start_asr` 被两条功能线同时改过：
- feat/lang-chapters 加了 query 参数 chapterId（字幕归属章节）
- feat/classroom-audio 加了 JSON body AsrStartRequest（麦克风/AGC/VAD 档）

这两处是**逻辑冲突**，手工合并后必须守住，否则将来任一分支再合入
很容易把另一边的参数悄悄冲掉 —— 页面不会报错，只会表现为
「选了章节但字幕没归档」或「线下音频增强点了没反应」。
"""

from fastapi.testclient import TestClient

from main import app


def test_start_asr_openapi_keeps_both_chapter_and_audio_body():
    """OpenAPI 里必须同时有 chapterId query 与 requestBody（音频参数）。"""
    with TestClient(app) as client:
        schema = client.app.openapi()
        op = schema["paths"]["/api/asr/start"]["post"]
        names = {p["name"] for p in op.get("parameters", [])}
        assert "course_id" in names
        assert "chapterId" in names, "章节归属参数被合入冲掉了"
        body = op.get("requestBody")
        assert body, "线下音频增强 body 参数被合入冲掉了"
        assert "application/json" in body["content"]

        # body schema 必须含 classroom-audio 的三个字段
        components = schema.get("components", {}).get("schemas", {})
        req_schema = components.get("AsrStartRequest", {})
        props = req_schema.get("properties", {})
        assert {"micDeviceIndex", "agc", "vadProfile"} <= set(props)


def test_start_asr_rejects_unknown_chapter_before_building_pipeline():
    """无效 chapterId 必须在构造管道之前被挡下（不触发任何音频设备枚举）。"""
    with TestClient(app) as client:
        cid = client.post("/api/courses", json={"name": "合入契约测试课"}).json()["id"]
        r = client.post(f"/api/asr/start?course_id={cid}&chapterId=no-such-chapter")
        assert r.status_code == 404, r.text
        assert "章节不存在" in r.json()["detail"]
        client.delete(f"/api/courses/{cid}")
