"""DatabaseManager 单元测试

覆盖：课程 CRUD、字幕写入与查询、词汇 upsert 幂等性。
所有用例使用临时数据目录（见 conftest.py），不会触碰真实数据。
"""

import asyncio

from database.manager import DatabaseManager


def _run(coro):
    """在同步测试中执行协程"""
    return asyncio.run(coro)


def test_course_crud():
    """课程创建 → 查询 → 更新 → 删除 全链路"""

    async def scenario():
        db = DatabaseManager()
        await db.initialize()
        try:
            course = await db.create_course(
                {"name": "线性代数", "domain": "数学", "courseType": "offline"}
            )
            assert course["name"] == "线性代数"
            assert course["courseType"] == "offline"
            assert course["domain"] == "数学"

            courses = await db.get_courses()
            assert any(c["id"] == course["id"] for c in courses)

            updated = await db.update_course(course["id"], {"name": "线性代数（2026 秋）"})
            assert updated["name"] == "线性代数（2026 秋）"

            assert await db.delete_course(course["id"]) is True
            assert await db.get_course(course["id"]) is None
        finally:
            await db.close()

    _run(scenario())


def test_missing_course_handling():
    """对不存在的课程做操作应安全返回 None / False"""

    async def scenario():
        db = DatabaseManager()
        await db.initialize()
        try:
            assert await db.get_course("not-exist") is None
            assert await db.update_course("not-exist", {"name": "x"}) is None
            assert await db.delete_course("not-exist") is False
        finally:
            await db.close()

    _run(scenario())


def test_subtitle_and_vocabulary_flow():
    """字幕写入 + 词汇 upsert（同一单词二次写入应更新而非新增）"""

    async def scenario():
        db = DatabaseManager()
        await db.initialize()
        try:
            course = await db.create_course({"name": "英语听力"})

            await db.add_subtitle(
                {
                    "course_id": course["id"],
                    "text": "Hello world",
                    "start_ms": 0,
                    "end_ms": 1200,
                }
            )
            subtitles = await db.get_subtitles(course["id"])
            assert len(subtitles) == 1
            assert subtitles[0]["text"] == "Hello world"

            await db.upsert_vocabulary(
                {"course_id": course["id"], "word": "hello", "translation": "你好"}
            )
            vocab = await db.get_vocabulary_by_word(course["id"], "hello")
            assert vocab is not None
            assert vocab["translation"] == "你好"

            await db.upsert_vocabulary(
                {"course_id": course["id"], "word": "hello", "translation": "喂，你好"}
            )
            assert len(await db.get_vocabulary(course["id"])) == 1
            again = await db.get_vocabulary_by_word(course["id"], "hello")
            assert again["translation"] == "喂，你好"
        finally:
            await db.close()

    _run(scenario())
