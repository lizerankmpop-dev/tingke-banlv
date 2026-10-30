# -*- coding: utf-8 -*-
"""🔴 防回归：**会话列表的时间戳不能是 0**（2026-10-31）

现象（打包版真机点出来的）
--------------------------
导出面板里的会话选择器显示：

    1970/1/1 08:00:00 · 6 条（最近）
    1970/1/1 08:00:00 · 2 条

根因
----
`DatabaseManager._row_to_dict` 会把字段**重命名成驼峰**：

    if "created_at" in d:
        d["createdAt"] = d.pop("created_at")

⇒ 而 `/sessions` 端点里写的是 `s.get("created_at")` ⇒ **永远拿不到**
⇒ `startedAt = 0` ⇒ 前端 `new Date(0)` ⇒ **1970/1/1**
⇒ 而且排序键全为 0 ⇒ **「最近一次」也排错了**。

⇒ 修复：两种拼写都兼容（`created_at` / `createdAt`）。

本测试守住
----------
① `/sessions` 返回的 `startedAt` **必须 > 0**（不能是 0）
② `isLatest` 那个必须是**时间最大**的那个会话（排序正确）
"""
from __future__ import annotations

import asyncio

import pytest


def test_sessions_started_at_is_real_timestamp(monkeypatch):
    """`startedAt` 必须是真实毫秒时间戳，不能是 0"""
    from api import routes

    # 造两批字幕：注意**驼峰** `createdAt`（模仿 _row_to_dict 的重命名）
    rows = [
        {"session_id": "sess-old", "text": "old one", "createdAt": 1_700_000_000_000},
        {"session_id": "sess-old", "text": "old two", "createdAt": 1_700_000_001_000},
        {"session_id": "sess-new", "text": "new one", "createdAt": 1_700_000_100_000},
    ]

    async def fake_get_subtitles(course_id, limit=100):
        return rows

    monkeypatch.setattr(routes.db, "get_subtitles", fake_get_subtitles)

    out = asyncio.run(routes.list_sessions("course-x"))
    sessions = out["sessions"]
    assert len(sessions) == 2
    for s in sessions:
        assert s["startedAt"] > 0, f"startedAt 不该是 0：{s}"
    # 排序：时间大的在前
    assert sessions[0]["sessionId"] == "sess-new", sessions
    assert sessions[0]["isLatest"] is True
    assert sessions[1]["isLatest"] is False
    # 条数
    by_id = {s["sessionId"]: s["count"] for s in sessions}
    assert by_id == {"sess-new": 1, "sess-old": 2}


def test_sessions_tolerates_snake_case(monkeypatch):
    """蛇形 `created_at` 也要认（万一以后改了重命名规则）"""
    from api import routes

    rows = [{"session_id": "s1", "text": "x", "created_at": 1_700_000_000_000}]

    async def fake_get_subtitles(course_id, limit=100):
        return rows

    monkeypatch.setattr(routes.db, "get_subtitles", fake_get_subtitles)
    out = asyncio.run(routes.list_sessions("c"))
    assert out["sessions"][0]["startedAt"] == 1_700_000_000_000
