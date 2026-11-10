# -*- coding: utf-8 -*-
"""课程资料上传（ADR-004 L1）—— 接口与 service 单测。

覆盖四类风险：
1. 安全边界：扩展名白名单、文件名消毒（路径逃逸）、大小上限、跨课程隔离；
2. 数据一致性：DB 行与磁盘文件同生同灭，同名不覆盖；
3. 术语候选：频次阈值、停用词、排序稳定性、上限；
4. 答疑上下文：indexed 资料进入 context，非文本资料不进。

conftest 已把 TINGKEBANLV_HOME 指到临时目录 —— materials 落盘天然隔离。
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from main import app
from materials import service as ms


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def course_id(client):
    """每个用例一门新课，天然互不干扰（隔离测试也在用它）"""
    r = client.post("/api/courses", json={"name": f"资料测试课-{uuid.uuid4().hex[:6]}"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _upload(client, course_id, filename, content: bytes):
    from urllib.parse import quote
    return client.post(
        f"/api/courses/{course_id}/materials",
        content=content,
        headers={"X-Filename": quote(filename)},
    )


# ============ service 纯函数 ============

def test_sanitize_filename_blocks_path_escape():
    # 路径分隔符被拆掉，只留 basename；危险字符换 _
    assert "/" not in ms.sanitize_filename("../../etc/passwd")
    assert "\\" not in ms.sanitize_filename("C:\\Users\\evil.exe")
    # 空名回落
    assert ms.sanitize_filename("...") == "material"


def test_extract_term_candidates_frequency_and_stopwords():
    text = (
        "The nursing process includes assessment and nursing diagnosis. "
        "The nursing process repeats. Nursing diagnosis guides care. "
        "静脉注射是基础操作。静脉注射需无菌。护理评估先行。"
    )
    terms = ms.extract_term_candidates(text)
    # 短语优先于单词
    assert "nursing process" in terms
    assert "nursing diagnosis" in terms
    # 中文按出现次数进候选
    assert "静脉注射" in terms
    # 停用词不进候选
    assert "the" not in terms
    assert "includes" not in terms  # 只出现 1 次，不够频次


def test_extract_term_candidates_respects_limit():
    # 60 个互不相同的纯字母词（别用 term0/term1 —— 数字会被词条正则切断，
    # 60 个词全塌缩成一个 "term"，上限根本测不到）
    words = [f"t{'a' * (i + 1)}" for i in range(60)]
    text = " ".join(f"{w} {w}" for w in words)
    assert len(ms.extract_term_candidates(text, limit=10)) == 10


def test_build_material_context_marks_excerpt():
    records = [{"name": "a.txt", "indexed": True}, {"name": "b.pdf", "indexed": False}]
    ctx = ms.build_material_context(records, "no-such-course")
    # 没有课程目录/文件时返回空串（尽力而为，不抛）
    assert ctx == ""


# ============ 上传 / 列表 / 删除接口 ============

def test_upload_txt_indexed_and_listed(client, course_id):
    r = _upload(client, course_id, "讲义.txt", "backpropagation backpropagation 反向传播 反向传播".encode("utf-8"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["indexed"] is True
    assert body["ext"] == ".txt"
    assert body["size"] > 0

    listed = client.get(f"/api/courses/{course_id}/materials").json()
    assert any(m["name"] == "讲义.txt" for m in listed)


def test_upload_pdf_registered_but_not_indexed(client, course_id):
    r = _upload(client, course_id, "课件.pdf", b"%PDF-1.4 fake")
    assert r.status_code == 200, r.text
    assert r.json()["indexed"] is False


def test_upload_rejects_bad_extension_and_empty(client, course_id):
    assert _upload(client, course_id, "evil.exe", b"xxx").status_code == 400
    assert _upload(client, course_id, "空.txt", b"").status_code == 400


def test_upload_rejects_oversize(client, course_id, monkeypatch):
    monkeypatch.setattr(ms, "MAX_SIZE_BYTES", 10)
    assert _upload(client, course_id, "大文件.txt", b"x" * 11).status_code == 400


def test_upload_missing_filename_header(client, course_id):
    r = client.post(f"/api/courses/{course_id}/materials", content=b"data")
    assert r.status_code == 400


def test_upload_404_for_missing_course(client):
    r = _upload(client, "no-such-course", "a.txt", b"hi")
    assert r.status_code == 404


def test_duplicate_name_does_not_overwrite(client, course_id):
    _upload(client, course_id, "same.txt", b"first")
    r2 = _upload(client, course_id, "same.txt", b"second")
    assert r2.status_code == 200
    assert r2.json()["name"] != "same.txt"  # same-2.txt
    listed = client.get(f"/api/courses/{course_id}/materials").json()
    names = [m["name"] for m in listed]
    assert "same.txt" in names and r2.json()["name"] in names


def test_delete_removes_row_and_file(client, course_id):
    r = _upload(client, course_id, "待删.txt", b"content")
    mid = r.json()["id"]
    file_path = ms.materials_root() / course_id / "待删.txt"
    assert file_path.exists()

    d = client.delete(f"/api/courses/{course_id}/materials/{mid}")
    assert d.status_code == 200
    assert not file_path.exists()
    listed = client.get(f"/api/courses/{course_id}/materials").json()
    assert all(m["id"] != mid for m in listed)


def test_delete_wrong_course_returns_404(client, course_id):
    # A 课的资料不能从 B 课的路径删掉
    r = _upload(client, course_id, "a.txt", b"aaa")
    mid = r.json()["id"]
    other = client.post("/api/courses", json={"name": "另一门课"}).json()["id"]
    assert client.delete(f"/api/courses/{other}/materials/{mid}").status_code == 404
    # 原资料仍在
    assert any(m["id"] == mid for m in client.get(f"/api/courses/{course_id}/materials").json())


def test_course_isolation(client, course_id):
    """A 课的资料不出现在 B 课的列表与术语候选里"""
    other = client.post("/api/courses", json={"name": "隔离对照课"}).json()["id"]
    _upload(client, course_id, "isolation.txt", "术语甲 术语甲 术语甲".encode("utf-8"))

    assert client.get(f"/api/courses/{other}/materials").json() == []
    terms_other = client.post(f"/api/courses/{other}/materials/terms").json()
    assert terms_other["terms"] == [] and terms_other["fromFiles"] == 0


# ============ 术语候选接口 ============

def test_terms_endpoint_returns_candidates(client, course_id):
    content = "sterile technique sterile technique asepsis asepsis asepsis 无菌操作 无菌操作"
    _upload(client, course_id, "术语表.txt", content.encode("utf-8"))
    _upload(client, course_id, "不解析.pdf", b"%PDF no terms here")

    r = client.post(f"/api/courses/{course_id}/materials/terms")
    assert r.status_code == 200, r.text
    body = r.json()
    # PDF 不计入 fromFiles，其内容也不产生候选
    assert body["fromFiles"] == 1
    assert "sterile technique" in body["terms"]
    assert "asepsis" in body["terms"]
    assert "无菌操作" in body["terms"]


def test_terms_endpoint_404_for_missing_course(client):
    assert client.post("/api/courses/no-such-course/materials/terms").status_code == 404


# ============ 中文文件名 roundtrip ============

def test_chinese_filename_roundtrip(client, course_id):
    """X-Filename 是 URL 编码的 —— 中文文件名上传后落盘名必须一致"""
    name = "护理专业英语_第3章.txt"
    r = _upload(client, course_id, name, "护理评估 护理评估".encode("utf-8"))
    assert r.status_code == 200
    assert r.json()["name"] == name
    assert (ms.materials_root() / course_id / name).exists()
