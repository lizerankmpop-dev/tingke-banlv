# -*- coding: utf-8 -*-
"""D7 章节总结保存 API 防回归（2026-11-19）

- chapters 表新增 summary / summary_updated_at（新库 + 老库迁移）
- PUT /api/courses/{course_id}/chapters/{chapter_id}/summary 可保存并回读
- 归属校验：B 课不能改 A 课章节的总结
- 列表接口返回 summary / summaryUpdatedAt，供下次进课展示章节目录
"""
from __future__ import annotations

import asyncio
import sqlite3
import uuid

from fastapi.testclient import TestClient

from database import manager as manager_mod
from database.manager import DatabaseManager
from main import app


def _new_course(client: TestClient) -> str:
    resp = client.post("/api/courses", json={"name": f"章节总结测试-{uuid.uuid4().hex[:6]}"})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_fresh_db_chapters_have_summary_columns(tmp_path, monkeypatch):
    """全新库的 chapters 表必须原生带 summary 两列。"""
    db_path = tmp_path / "fresh-d7.db"
    monkeypatch.setattr(manager_mod, "DB_PATH", db_path)

    async def run():
        m = DatabaseManager()
        await m.initialize()
        await m.close()

    asyncio.run(run())
    conn = sqlite3.connect(str(db_path))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(chapters)")}
    conn.close()
    assert {"summary", "summary_updated_at"}.issubset(cols)


def test_legacy_db_migrates_summary_columns(tmp_path, monkeypatch):
    """老库（无 summary 列）升级后必须补列，且旧数据不丢。"""
    db_path = tmp_path / "legacy-d7.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE chapters (
             id TEXT PRIMARY KEY, course_id TEXT NOT NULL, name TEXT NOT NULL,
             sort_order INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL)"""
    )
    conn.execute(
        "INSERT INTO chapters VALUES ('old-1','c-1','第一讲',0,1)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(manager_mod, "DB_PATH", db_path)

    async def run():
        m = DatabaseManager()
        await m.initialize()
        rows = await m.list_chapters("c-1")
        await m.close()
        return rows

    rows = asyncio.run(run())
    assert rows and rows[0]["id"] == "old-1"
    assert rows[0]["summary"] == ""
    assert rows[0]["summaryUpdatedAt"] == 0


def test_save_and_read_chapter_summary():
    with TestClient(app) as client:
        c1 = _new_course(client)
        c2 = _new_course(client)
        created = client.post(f"/api/courses/{c1}/chapters", json={"name": "第一讲"})
        assert created.status_code == 200, created.text
        chapter_id = created.json()["id"]

        put = client.put(
            f"/api/courses/{c1}/chapters/{chapter_id}/summary",
            json={"summary": "本讲讲了梯度下降与学习率的选择。"},
        )
        assert put.status_code == 200, put.text
        body = put.json()
        assert body["ok"] is True
        assert body["id"] == chapter_id
        assert isinstance(body["summaryUpdatedAt"], int)

        listed = client.get(f"/api/courses/{c1}/chapters").json()
        row = next(r for r in listed if r["id"] == chapter_id)
        assert row["summary"] == "本讲讲了梯度下降与学习率的选择。"
        assert row["summaryUpdatedAt"] > 0

        # 归属校验：另一门课不能覆盖本章节总结
        wrong = client.put(
            f"/api/courses/{c2}/chapters/{chapter_id}/summary",
            json={"summary": "越权写入"},
        )
        assert wrong.status_code == 404
        again = client.get(f"/api/courses/{c1}/chapters").json()
        assert next(r for r in again if r["id"] == chapter_id)["summary"] == "本讲讲了梯度下降与学习率的选择。"


def test_empty_summary_rejected():
    with TestClient(app) as client:
        c1 = _new_course(client)
        chapter_id = client.post(f"/api/courses/{c1}/chapters", json={"name": "第二讲"}).json()["id"]
        resp = client.put(
            f"/api/courses/{c1}/chapters/{chapter_id}/summary", json={"summary": "   "}
        )
        assert resp.status_code == 400
