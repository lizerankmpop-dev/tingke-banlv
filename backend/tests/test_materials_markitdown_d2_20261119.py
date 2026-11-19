# -*- coding: utf-8 -*-
"""D2 课程资料 markitdown 解析防回归（2026-11-19）

- 白名单扩展：PDF/DOCX/PPTX/XLSX/HTML/CSV/JSON/XML/图片都能上传
- TXT/MD 继续原生直读（不受 markitdown 可用性影响）
- 非文本走 markitdown 转 md；转换结果缓存；失败降级为空串不抛
- 图片需联网：转换拿不到内容时返回空串，界面按扩展名提示
"""
from __future__ import annotations

import pytest

from materials import service as ms


def test_allowed_exts_cover_office_and_web():
    for ext in (".txt", ".md", ".pdf", ".docx", ".pptx", ".xlsx", ".html", ".csv", ".json", ".xml", ".png"):
        assert ext in ms.ALLOWED_EXTS, f"{ext} 应在上传白名单里"
    assert ms.TEXT_EXTS == {".txt", ".md"}
    assert ms.NETWORK_HINT_EXTS.issubset(ms.ALLOWED_EXTS)


def test_markitdown_is_available_after_install():
    """联网已安装 markitdown；若未来打包漏带，这条会红。"""
    assert ms._get_markitdown() is not None, "markitdown 应可用（PDF/DOCX 等才能进上下文）"


def test_html_converts_to_markdown(tmp_path, monkeypatch):
    """真实转换：HTML → md，验证 markitdown 链路确实产出可读文本。"""
    monkeypatch.setattr(ms, "materials_root", lambda: tmp_path)
    course = "c1"
    d = tmp_path / course
    d.mkdir(parents=True)
    (d / "讲义.html").write_text(
        "<html><body><h1>机器学习</h1><p>反向传播 backpropagation 反向传播</p></body></html>",
        encoding="utf-8",
    )
    # 清缓存避免命中其它测试的残留
    ms._CONVERT_CACHE.clear()
    text = ms.read_material_text(course, "讲义.html")
    assert "机器学习" in text
    assert "backpropagation" in text


def test_txt_still_read_directly_even_if_markitdown_broken(tmp_path, monkeypatch):
    """markitdown 不可用时 TXT/MD 必须照常可读（降级不能波及老功能）。"""
    monkeypatch.setattr(ms, "materials_root", lambda: tmp_path)
    monkeypatch.setattr(ms, "_MARKITDOWN", None)
    monkeypatch.setattr(ms, "_MARKITDOWN_PROBED", True)
    d = tmp_path / "c2"
    d.mkdir(parents=True)
    (d / "notes.md").write_text("# 标题\n反向传播 反向传播", encoding="utf-8")
    assert "反向传播" in ms.read_material_text("c2", "notes.md")


def test_convert_failure_returns_empty_not_raise(tmp_path, monkeypatch):
    """转换抛异常时返回空串，答疑/术语链路不受影响。"""
    class Broken:
        def convert(self, _path):
            raise RuntimeError("boom")

    monkeypatch.setattr(ms, "materials_root", lambda: tmp_path)
    monkeypatch.setattr(ms, "_MARKITDOWN", Broken())
    monkeypatch.setattr(ms, "_MARKITDOWN_PROBED", True)
    d = tmp_path / "c3"
    d.mkdir(parents=True)
    (d / "bad.pdf").write_bytes(b"%PDF fake")
    ms._CONVERT_CACHE.clear()
    assert ms.read_material_text("c3", "bad.pdf") == ""


def test_cache_hit_skips_reconvert(tmp_path, monkeypatch):
    """同一文件重复读不应重复转换（缓存按 mtime 失效）。"""
    calls = []

    class Counting:
        def convert(self, _path):
            calls.append(1)
            class R:
                markdown = "cached text"
            return R()

    monkeypatch.setattr(ms, "materials_root", lambda: tmp_path)
    monkeypatch.setattr(ms, "_MARKITDOWN", Counting())
    monkeypatch.setattr(ms, "_MARKITDOWN_PROBED", True)
    d = tmp_path / "c4"
    d.mkdir(parents=True)
    f = d / "a.docx"
    f.write_bytes(b"fake")
    ms._CONVERT_CACHE.clear()
    ms.read_material_text("c4", "a.docx")
    ms.read_material_text("c4", "a.docx")
    assert len(calls) == 1, "第二次读应命中缓存"


def test_save_material_marks_nontext_indexed(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "materials_root", lambda: tmp_path)
    rec = ms.save_material("c5", "课件.pdf", b"%PDF fake")
    assert rec["indexed"] == 1
    assert rec["ext"] == ".pdf"
