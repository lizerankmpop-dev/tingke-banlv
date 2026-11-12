"""课堂笔记本（course_notes）+ 麦克风试采端点测试。

背景：2026-11-12 界面定版 —— 识别页右侧「小笔记本」（李梓萌原型）需要
GET/PUT /api/courses/{id}/note；配置页「试采 3 秒」需要 POST /audio/mic-test。
note 是全新表（老库 CREATE TABLE IF NOT EXISTS 也会执行，不涉及
「依赖迁移新增列」的索引坑），但仍按老库升级守卫的惯例验证一次。
"""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from main import app
from database import manager as manager_mod
from database.manager import DatabaseManager


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def course_id(client):
    resp = client.post("/api/courses", json={"name": f"笔记本测试课-{uuid.uuid4().hex[:6]}"})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


class TestCourseNote:
    @pytest.fixture(autouse=True)
    def _client(self, client):
        self.client = client

    def test_note_initially_empty(self, course_id):
        resp = self.client.get(f"/api/courses/{course_id}/note")
        assert resp.status_code == 200
        assert resp.json() == {"content": "", "updatedAt": None}

    def test_set_and_get_note(self, course_id):
        put = self.client.put(
            f"/api/courses/{course_id}/note", json={"content": "洛必达要先确认 0/0"}
        )
        assert put.status_code == 200
        body = put.json()
        assert body["content"] == "洛必达要先确认 0/0"
        assert isinstance(body["updatedAt"], int)

        got = self.client.get(f"/api/courses/{course_id}/note")
        assert got.status_code == 200
        assert got.json()["content"] == "洛必达要先确认 0/0"

    def test_note_upsert_updates_in_place(self, course_id):
        self.client.put(f"/api/courses/{course_id}/note", json={"content": "第一版"})
        second = self.client.put(f"/api/courses/{course_id}/note", json={"content": "第二版"})
        assert second.status_code == 200

        got = self.client.get(f"/api/courses/{course_id}/note")
        assert got.json()["content"] == "第二版"

    def test_note_content_limit(self, course_id):
        resp = self.client.put(
            f"/api/courses/{course_id}/note", json={"content": "x" * 20001}
        )
        assert resp.status_code == 422

    def test_note_unknown_course_404(self):
        assert self.client.get("/api/courses/not-exist/note").status_code == 404
        assert self.client.put("/api/courses/not-exist/note", json={"content": ""}).status_code == 404

    def test_note_table_created_on_fresh_db(self, tmp_path, monkeypatch):
        """全新库：course_notes 表必须随 _create_tables 建出来（老库同理）"""
        db_path = tmp_path / "fresh.db"
        monkeypatch.setattr(manager_mod, "DB_PATH", db_path)

        async def run():
            manager = DatabaseManager()
            await manager.initialize()
            await manager.close()

        asyncio.run(run())
        import sqlite3

        conn = sqlite3.connect(str(db_path))
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert "course_notes" in tables


class TestMicTest:
    @pytest.fixture(autouse=True)
    def _client(self, client):
        self.client = client

    def test_mic_test_returns_verdict_shape(self):
        """CI/无音频设备环境：端点必须 200 且给出可展示的 message（不 500）"""
        resp = self.client.post("/api/audio/mic-test", json={"seconds": 0.5})
        assert resp.status_code == 200
        body = resp.json()
        # 两种都合法：ok=True（有设备并采到）或 ok=False（无音频库/无设备/开流失败）
        assert isinstance(body["ok"], bool)
        assert 0.0 <= body["level"] <= 1.0
        assert 0.0 <= body["peak"] <= 1.0
        assert body["message"]

    def test_mic_test_seconds_bounds(self):
        assert self.client.post("/api/audio/mic-test", json={"seconds": 0.1}).status_code == 422
        assert self.client.post("/api/audio/mic-test", json={"seconds": 11}).status_code == 422
