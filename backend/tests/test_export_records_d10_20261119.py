# -*- coding: utf-8 -*-
"""D10 导出记录 API 防回归（2026-11-19）

- 空列表可正常进入（返回 records: []，由前端渲染「没有记录」）
- 成功下载后登记，列表倒序返回
- 非法类型/格式被拒；可按 id 删除
"""
from fastapi.testclient import TestClient

from main import app


def test_exports_empty_state_is_reachable():
    with TestClient(app) as client:
        resp = client.get("/api/exports")
        assert resp.status_code == 200
        body = resp.json()
        assert "records" in body
        assert isinstance(body["records"], list)


def test_record_list_and_delete():
    with TestClient(app) as client:
        a = client.post("/api/exports", json={
            "courseId": "c1", "courseName": "生成式AI", "kind": "subtitles",
            "format": "md", "fileName": "生成式AI.md", "rowCount": 42,
        })
        assert a.status_code == 200, a.text
        b = client.post("/api/exports", json={
            "courseId": "c2", "courseName": "离散数学", "kind": "vocabulary",
            "format": "csv", "fileName": "离散数学.csv", "rowCount": 7,
        })
        assert b.status_code == 200

        records = client.get("/api/exports").json()["records"]
        ids = [r["id"] for r in records]
        assert a.json()["id"] in ids and b.json()["id"] in ids
        assert records[0]["createdAt"] >= records[1]["createdAt"]
        assert records[0]["rowCount"] in (7, 42)

        assert client.delete(f"/api/exports/{b.json()['id']}").status_code == 200
        assert client.delete(f"/api/exports/{b.json()['id']}").status_code == 404
        assert b.json()["id"] not in [r["id"] for r in client.get("/api/exports").json()["records"]]


def test_invalid_kind_rejected():
    with TestClient(app) as client:
        resp = client.post("/api/exports", json={"kind": "unknown", "format": "md"})
        assert resp.status_code == 400
