# -*- coding: utf-8 -*-
"""🔴 防回归：**长课字幕不能被 500 条上限静默截断**（2026-11-15 真实线下课实测）

现象（29 分 47 秒《生成式人工智能（二）》跑出来的）
----------------------------------------------------
课程入库 **715 条**字幕后：

    GET  /subtitles?limit=1000   → 422（Query le=500）
    GET  /sessions                → count=715（内部绕过了 Query 校验）
    GET  /export-srt              → count=500，SRT 首条 00:08:22,760

根因是**三处各写了一个不同的数字**：

    get_subtitles(): Query(default=100, le=500)
    list_sessions(): db.get_subtitles(course_id, limit=1000)
    export_srt():    db.get_subtitles(course_id, limit=500)

而 DB 是 `ORDER BY start_ms DESC LIMIT ?`，导出再 `reversed()` 成正序
⇒ 拿到的是**最新的 500 条**，开头 8 分 22 秒整段消失，且 `count` 看着还挺正常。
会话统计因为绕过校验反而是全的 —— 这种「数字对、文件残」最容易被漏掉。

修复：`routes.py` 统一 `SUBTITLE_MAX_ROWS = 5000`，三处共用一个常量；
`backup.json` 补 `Content-Disposition`（原来 200 但浏览器不下载）。

本测试守住
----------
① 三处只能引用同一个常量，不许再各自写死 limit 数字
② `limit=1000 / 5000` 可读且不截断；超过上限仍 422（有边界，不是无限）
③ SRT 导出条数 == 入库条数，且**首条时间从 00:00 开始**（时间轴断言是关键，
   单看 count 发现不了「取了最新 500 条」这种错位）
④ `backup.json` 带 `Content-Disposition: attachment`

真实 HTTP 全量复验另见 `scripts/verify_lecture_e2e.py`（16 项）。
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app

ROUTES_PY = Path(__file__).resolve().parents[1] / "api" / "routes.py"
N_ROWS = 715  # 真实课实测条数，故意 > 500 才能复现旧缺陷
SESSION = "genai02-offline-20261115"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def course_id(client):
    resp = client.post("/api/courses", json={"name": f"长课字幕上限-{uuid.uuid4().hex[:6]}"})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _rows():
    """模仿 DB：按 start_ms DESC 返回，字段是 snake_case（见 _row_to_dict）。"""
    return [
        {
            "id": f"sub-{i}",
            "text": f"第 {i} 句",
            "start_ms": (N_ROWS - 1 - i) * 2500,
            "end_ms": (N_ROWS - 1 - i) * 2500 + 2400,
            "is_final": True,
            "session_id": SESSION,
        }
        for i in range(N_ROWS)
    ]


@pytest.fixture()
def long_lecture(monkeypatch):
    """把 715 条字幕挂到被测课程上，不走真实 DB 写入（快，且不影响其他用例）。"""
    from api import routes

    async def fake_get_subtitles(course_id, limit=100, chapter_id=None):
        assert limit <= routes.SUBTITLE_MAX_ROWS, "端点传了超过统一上限的 limit"
        return _rows()[:limit]

    monkeypatch.setattr(routes.db, "get_subtitles", fake_get_subtitles)
    return routes


def test_subtitle_limit_uses_one_shared_constant():
    """三处只能引用 SUBTITLE_MAX_ROWS，不许再各写死 500 / 1000。"""
    from api import routes

    assert routes.SUBTITLE_MAX_ROWS >= 5000, "1 小时课约 1500 条，上限要覆盖一节长课"
    src = ROUTES_PY.read_text(encoding="utf-8")
    assert re.search(r"SUBTITLE_MAX_ROWS\s*=\s*\d+", src), "统一常量不见了"
    assert re.search(r"le\s*=\s*SUBTITLE_MAX_ROWS", src), "/subtitles 的 Query 没引用常量"
    # /sessions 与 /export-srt 各一次，加上可能的其他读取点：至少两处
    assert src.count("limit=SUBTITLE_MAX_ROWS") >= 2, "内部读取点没全部改用统一常量"
    # 旧的三个魔法数字不能回来（排除注释与文档字符串，只看代码行）
    code_lines = [ln for ln in src.splitlines()
                  if not ln.lstrip().startswith("#") and '"""' not in ln]
    code = "\n".join(code_lines)
    assert "le=500" not in code, "又写死了 le=500"
    assert "limit=500)" not in code, "又写死了 limit=500"
    assert "limit=1000)" not in code, "又写死了 limit=1000"


def test_subtitles_over_500_rows_are_readable(client, course_id, long_lecture):
    """记忆回放可以一次拉全量；超过统一上限仍要有边界（不是放开到无限）。"""
    r1 = client.get(f"/api/courses/{course_id}/subtitles?limit=1000")
    assert r1.status_code == 200, r1.text
    assert isinstance(r1.json(), list) and len(r1.json()) == N_ROWS

    r2 = client.get(f"/api/courses/{course_id}/subtitles?limit=5000")
    assert r2.status_code == 200, r2.text
    assert len(r2.json()) == N_ROWS

    r3 = client.get(f"/api/courses/{course_id}/subtitles?limit=5001")
    assert r3.status_code == 422, "上限之上必须仍被 FastAPI 拒绝"


def test_export_srt_full_and_starts_at_zero(client, course_id, long_lecture):
    """count 对齐 + 首条 00:00：只判 count 抓不到「取了最新 500 条」的错位。"""
    resp = client.get(f"/api/courses/{course_id}/export-srt")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["count"] == N_ROWS, f"入库 {N_ROWS} 条，只导出 {data['count']} 条"
    srt = data["srt"]
    assert len([b for b in srt.strip().split("\n\n") if b.strip()]) == N_ROWS
    first_tc = re.search(r"(\d{2}):(\d{2}):(\d{2}),\d{3}\s*-->", srt)
    assert first_tc, "SRT 没有时间轴"
    first_ms = (int(first_tc.group(1)) * 3600
                + int(first_tc.group(2)) * 60
                + int(first_tc.group(3))) * 1000
    assert first_ms < 60_000, f"SRT 从 {first_tc.group(0)} 才开始，开头被截掉了"


def test_vocabulary_backup_has_download_header(client, course_id):
    """原来返回 200 但浏览器只显示 JSON 文本，不触发下载。"""
    resp = client.get(f"/api/courses/{course_id}/vocabulary/backup.json")
    assert resp.status_code == 200, resp.text
    cd = resp.headers.get("content-disposition") or ""
    assert cd.startswith("attachment"), f"缺下载头：{dict(resp.headers)}"
    assert ".json" in cd
    # 加头不能破坏导入 roundtrip 依赖的 JSON 解析
    assert resp.json()["format"] == "tingkebanlv-vocabulary-backup"
