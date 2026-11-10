# -*- coding: utf-8 -*-
"""字幕语言选择 + 章节管理（feat/lang-chapters）—— 接口与 DB 层单测。

覆盖四类风险：
1. 语言归一化：非法值回落 both（老客户端/手改库不致炸管道）；
2. 章节生命周期：增（空名 400 / 追加排序）、删（字幕保留、归属置空）、跨课隔离；
3. 字幕归属：chapter_id 随 add_subtitle 入库、按章节过滤（'' = 未分章）；
4. 翻译链路的 ID 语义：add_subtitle 返回 id=DB uuid、clientId=管道 sub_N
   （两者混用曾是真实缺陷：译文 UPDATE 永远命中不了行）。

DB 测试沿用 test_database.py 的模式：独立 DatabaseManager + 单个 async
scenario（aiosqlite 连接绑定创建它的 loop，一个 scenario 一个 loop）。
conftest 已把数据目录指到临时目录，测试库与 app 库同文件，接口层断言可见。
"""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def course_id(client):
    r = client.post("/api/courses", json={"name": f"语言章节测试课-{uuid.uuid4().hex[:6]}"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ============ 语言归一化 ============

def test_create_course_subtitle_lang_invalid_falls_back_to_both(client):
    r = client.post("/api/courses", json={
        "name": f"非法语言课-{uuid.uuid4().hex[:6]}", "subtitleLang": "klingon",
    })
    assert r.status_code == 200, r.text
    assert r.json()["subtitleLang"] == "both"


def test_create_course_subtitle_lang_passthrough(client):
    r = client.post("/api/courses", json={
        "name": f"仅原文课-{uuid.uuid4().hex[:6]}", "subtitleLang": "orig",
    })
    assert r.status_code == 200, r.text
    assert r.json()["subtitleLang"] == "orig"


def test_update_course_subtitle_lang(client, course_id):
    r = client.put(f"/api/courses/{course_id}", json={"subtitleLang": "zh"})
    assert r.status_code == 200, r.text
    assert r.json()["subtitleLang"] == "zh"


# ============ 章节生命周期 ============

def test_chapter_crud_and_ordering(client, course_id):
    ids = []
    for name in ("第一章", "第二章", "第三章"):
        r = client.post(f"/api/courses/{course_id}/chapters", json={"name": name})
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])

    chapters = client.get(f"/api/courses/{course_id}/chapters").json()
    assert [c["sortOrder"] for c in chapters] == [1, 2, 3]
    assert [c["name"] for c in chapters] == ["第一章", "第二章", "第三章"]

    # 纯空白的章节名要拦（400）
    assert client.post(f"/api/courses/{course_id}/chapters", json={"name": "   "}).status_code == 400

    # 删除中间章节：列表少一条，序号不重排（追加语义）
    assert client.delete(f"/api/courses/{course_id}/chapters/{ids[1]}").status_code == 200
    chapters = client.get(f"/api/courses/{course_id}/chapters").json()
    assert [c["name"] for c in chapters] == ["第一章", "第三章"]

    # 重复删除 → 404
    assert client.delete(f"/api/courses/{course_id}/chapters/{ids[1]}").status_code == 404


def test_chapter_isolated_between_courses(client, course_id):
    """A 课的章节不能被 B 课的路径删掉（路径级隔离）。"""
    other = client.post("/api/courses", json={"name": f"隔离课-{uuid.uuid4().hex[:6]}"}).json()["id"]
    cid = client.post(f"/api/courses/{course_id}/chapters", json={"name": "A课章节"}).json()["id"]
    assert client.delete(f"/api/courses/{other}/chapters/{cid}").status_code == 404
    names = [c["name"] for c in client.get(f"/api/courses/{course_id}/chapters").json()]
    assert "A课章节" in names


# ============ 字幕归属与过滤（DB 层 + 接口层）============

def test_subtitle_chapter_filtering(client, course_id):
    """chapter_id 随字幕入库；按章节过滤；'' = 只看未分章。"""

    async def scenario():
        from database.manager import DatabaseManager
        db = DatabaseManager()
        await db.initialize()
        try:
            c1 = await db.add_chapter(course_id, "过滤章A")
            c2 = await db.add_chapter(course_id, "过滤章B")
            for text, ch in (("in A", c1["id"]), ("in B", c2["id"]), ("no chapter", "")):
                await db.add_subtitle({
                    "course_id": course_id, "text": text, "words": [],
                    "start_ms": 0, "end_ms": 50, "is_final": True,
                    "session_id": "s2", "chapter_id": ch,
                })

            # 不带过滤 → 全部
            all_subs = await db.get_subtitles(course_id, 50)
            assert {s["text"] for s in all_subs} == {"in A", "in B", "no chapter"}

            # 按章节过滤
            only_a = await db.get_subtitles(course_id, 50, chapter_id=c1["id"])
            assert {s["text"] for s in only_a} == {"in A"}

            # 空串过滤 = 只看未分章
            loose = await db.get_subtitles(course_id, 50, chapter_id="")
            assert {s["text"] for s in loose} == {"no chapter"}
        finally:
            await db.close()

    asyncio.run(scenario())

    # 接口层：chapterId 查询参数（'' 与不传语义不同）
    r = client.get(f"/api/courses/{course_id}/subtitles")
    assert {s["text"] for s in r.json()} == {"in A", "in B", "no chapter"}
    r = client.get(f"/api/courses/{course_id}/subtitles?chapterId=")
    assert {s["text"] for s in r.json()} == {"no chapter"}


def test_delete_chapter_keeps_subtitles(client, course_id):
    """删章节：字幕保留、chapter_id 置空 —— 课还是上过的，只是解除归属。"""

    async def scenario():
        from database.manager import DatabaseManager
        db = DatabaseManager()
        await db.initialize()
        try:
            ch = await db.add_chapter(course_id, "将删章")
            await db.add_subtitle({
                "course_id": course_id, "text": "chapter subtitle", "words": [],
                "start_ms": 0, "end_ms": 100, "is_final": True,
                "session_id": "s1", "chapter_id": ch["id"],
            })
        finally:
            await db.close()

    asyncio.run(scenario())

    # 走接口删除（同一 sqlite 文件）
    chapters = client.get(f"/api/courses/{course_id}/chapters").json()
    target = next(c for c in chapters if c["name"] == "将删章")
    assert client.delete(f"/api/courses/{course_id}/chapters/{target['id']}").status_code == 200

    async def verify():
        from database.manager import DatabaseManager
        db = DatabaseManager()
        await db.initialize()
        try:
            subs = await db.get_subtitles(course_id, 10)
            assert any(s["text"] == "chapter subtitle" for s in subs)
            assert all(s.get("chapterId") == "" for s in subs)
        finally:
            await db.close()

    asyncio.run(verify())


# ============ 翻译链路 ID 语义 ============

def test_add_subtitle_returns_db_id_and_client_id(course_id):
    """id 必须是 DB uuid（译文回填用），clientId 才是管道的 sub_N（前端合并用）。"""

    async def scenario():
        from database.manager import DatabaseManager
        db = DatabaseManager()
        await db.initialize()
        try:
            saved = await db.add_subtitle({
                "course_id": course_id, "text": "hello world", "words": [],
                "start_ms": 0, "end_ms": 100, "is_final": True,
                "session_id": "s4", "chapter_id": "",
                "id": "sub_7",  # 管道的客户端序号
            })
            assert saved["id"] != "sub_7"
            assert saved["id"]  # DB uuid
            assert saved["clientId"] == "sub_7"
        finally:
            await db.close()

    asyncio.run(scenario())


def test_update_subtitle_translation_roundtrip(course_id):
    async def scenario():
        from database.manager import DatabaseManager
        db = DatabaseManager()
        await db.initialize()
        try:
            saved = await db.add_subtitle({
                "course_id": course_id, "text": "good morning", "words": [],
                "start_ms": 0, "end_ms": 100, "is_final": True,
                "session_id": "s5", "chapter_id": "",
            })
            assert await db.update_subtitle_translation(saved["id"], "早上好") is True
            subs = await db.get_subtitles(course_id, 10)
            assert subs[0]["translation"] == "早上好"
            # 不存在的行 → False（译文晚到但字幕已被清库）
            assert await db.update_subtitle_translation("no-such-id", "x") is False
        finally:
            await db.close()

    asyncio.run(scenario())


def test_process_translation_item_updates_db_and_enqueues(course_id, monkeypatch):
    """worker 单元逻辑：译文写库 + 消息带 clientId（前端按它合并）。

    _process_translation_item 用 run_coroutine_threadsafe 把回填投回运行中的
    loop，所以 scenario 里用 to_thread 跑它、await 让出控制权等回填完成。
    """

    async def scenario():
        from api import routes as R
        from database.manager import DatabaseManager
        db = DatabaseManager()
        await db.initialize()
        try:
            # 换成测试自己的 db 实例（模块级 R.db 绑在 app 的 loop 上）
            monkeypatch.setattr(R, "db", db)
            saved = await db.add_subtitle({
                "course_id": course_id, "text": "see you tomorrow", "words": [],
                "start_ms": 0, "end_ms": 100, "is_final": True,
                "session_id": "s6", "chapter_id": "",
            })
            monkeypatch.setattr(R, "_translate_text", lambda t: "明天见")

            got = []
            loop = asyncio.get_running_loop()
            # to_thread 跑完 = worker 内部的 fut.result(30) 已返回
            # = 回填协程已在事件循环上执行完毕，无需再轮询
            await asyncio.to_thread(
                R._process_translation_item,
                course_id, saved["id"], "see you tomorrow",
                lambda k, p: got.append((k, p)), loop, "sub_9",
            )

            subs = await db.get_subtitles(course_id, 10)
            assert subs[0]["translation"] == "明天见"
            assert got == [("subtitle_translation", {"id": "sub_9", "translation": "明天见"})]
        finally:
            await db.close()

    asyncio.run(scenario())


def test_process_translation_item_llm_unavailable_noop(course_id, monkeypatch):
    """LLM 不可用 → 完全静默：不写库、不发消息（软依赖语义）。"""

    async def scenario():
        from api import routes as R
        from database.manager import DatabaseManager
        db = DatabaseManager()
        await db.initialize()
        try:
            monkeypatch.setattr(R, "db", db)
            saved = await db.add_subtitle({
                "course_id": course_id, "text": "no llm here", "words": [],
                "start_ms": 0, "end_ms": 100, "is_final": True,
                "session_id": "s7", "chapter_id": "",
            })
            monkeypatch.setattr(R, "_translate_text", lambda t: None)
            got = []
            loop = asyncio.get_running_loop()
            await asyncio.to_thread(
                R._process_translation_item,
                course_id, saved["id"], "no llm here",
                lambda k, p: got.append((k, p)), loop, "sub_10",
            )
            await asyncio.sleep(0.05)
            subs = await db.get_subtitles(course_id, 10)
            assert subs[0].get("translation") in ("", None)
            assert got == []
        finally:
            await db.close()

    asyncio.run(scenario())
