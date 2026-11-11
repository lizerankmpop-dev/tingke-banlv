"""老库升级回归测试（2026-11-12 打包实测踩出来的）

背景
----
v3.8.0 打包后**实跑包内冻结后端**时炸了：

    sqlite3.OperationalError: no such column: chapter_id

根因不是新代码写错，而是**建索引的时机错了**：

    _create_tables()  →  CREATE TABLE IF NOT EXISTS subtitles (...)
                         CREATE INDEX ... ON subtitles(chapter_id)   ← 这里
    _migrate()        →  ALTER TABLE subtitles ADD COLUMN chapter_id  ← 列在这才出现

老库（v3.7.x 及更早）里 `subtitles` 表**已经存在** ⇒ `CREATE TABLE IF NOT EXISTS`
被跳过 ⇒ 表里没有 `chapter_id` 列 ⇒ 紧接着的 `CREATE INDEX` 立刻报列不存在
⇒ `initialize()` 抛异常 ⇒ **后端启动失败 ⇒ 所有老用户升级后应用打不开**。

同一个坑**已经犯过两次**：
  ① 10-30  `idx_vocabulary_due` → `vocabulary.next_review_at`
  ② 11-10  `idx_subtitles_chapter` → `subtitles.chapter_id`（本次）

所以本文件的作用不只是"测一个 bug"，而是**把这个坑的通用形态锁死**：
凡是「老库没有、靠 ALTER TABLE 补出来的列」相关的索引，都不能建在 _create_tables 里。

判据（本文件逐条守住）
--------------------
1. 老库（subtitles 无 chapter_id / translation / session_id）启动**不抛异常**；
2. 迁移后 chapter_id / translation / session_id 三列都在；
3. idx_subtitles_chapter 索引**确实建出来了**（不是靠 try/except 吞掉）；
4. 老数据没被破坏（原有字幕行还在、内容没变）；
5. 全新库（没有任何旧表）也能正常初始化 —— 修索引时机不能把新装用户搞坏；
6. _create_tables() 的建表语句里**不再出现**任何引用「迁移新增列」的索引
   （防止第三次再犯 —— 这条是结构守卫，比行为测试更早失败）。
"""

import asyncio
import inspect

import pytest

from database import manager as manager_mod
from database.manager import DatabaseManager


# 老库（v3.7.x 及更早）的 subtitles 表定义 —— **故意不含** chapter_id /
# translation / session_id，这三列在 v3.8.0 才由 ALTER TABLE 补出来。
_LEGACY_SUBTITLES = """
CREATE TABLE IF NOT EXISTS subtitles (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL,
    video_id TEXT DEFAULT '',
    text TEXT NOT NULL,
    words_json TEXT DEFAULT '[]',
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    is_final INTEGER DEFAULT 1,
    created_at INTEGER NOT NULL
);
"""

_LEGACY_COURSES = """
CREATE TABLE IF NOT EXISTS courses (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT '其他',
    description TEXT DEFAULT '',
    custom_terms TEXT DEFAULT '[]',
    course_type TEXT NOT NULL DEFAULT 'online',
    language TEXT DEFAULT 'auto',
    simp_mode TEXT DEFAULT 'auto-simplified',
    auto_extract_terms INTEGER DEFAULT 1,
    ollama_model TEXT DEFAULT 'qwen2.5:7b',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
"""


def _run(coro):
    return asyncio.run(coro)


async def _make_legacy_db(db_path):
    """造一个「老库」：subtitles 表已存在但缺三列，且已有一条历史字幕。"""
    import aiosqlite

    db = await aiosqlite.connect(str(db_path))
    await db.executescript(_LEGACY_COURSES)
    await db.executescript(_LEGACY_SUBTITLES)
    await db.execute(
        """INSERT INTO subtitles
           (id, course_id, video_id, text, words_json, start_ms, end_ms,
            is_final, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("sub-old-1", "course-old", "v1", "这是老用户的历史字幕", "[]",
         1000, 3000, 1, 1700000000000),
    )
    await db.commit()
    await db.close()


@pytest.fixture()
def legacy_db_path(tmp_path, monkeypatch):
    """把 DB_PATH 指到一个临时路径，并在该路径上预置一份老库。"""
    db_path = tmp_path / "legacy.db"
    monkeypatch.setattr(manager_mod, "DB_PATH", db_path)
    _run(_make_legacy_db(db_path))
    return db_path


def _index_names(db_path):
    """读出库里真实存在的索引名。"""
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def _columns(db_path, table):
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {r[1] for r in rows}
    finally:
        conn.close()


def test_legacy_db_startup_does_not_raise(legacy_db_path):
    """① 老库启动不抛异常 —— 这条挂了就是「老用户升级后应用打不开」"""
    db = DatabaseManager()

    async def scenario():
        await db.initialize()
        # 必须 close：aiosqlite 的连接跑在自己的工作线程里，不关的话
        # 事件循环结束后它再回传结果会抛 "Event loop is closed"，
        # 在 pytest 里表现为 PytestUnhandledThreadExceptionWarning。
        await db.close()

    _run(scenario())


def test_legacy_db_gets_new_columns(legacy_db_path):
    """② 迁移后三列都在"""
    db = DatabaseManager()

    async def scenario():
        await db.initialize()
        try:
            cols = _columns(legacy_db_path, "subtitles")
            assert "chapter_id" in cols, "chapter_id 列未补出"
            assert "translation" in cols, "translation 列未补出"
            assert "session_id" in cols, "session_id 列未补出"
        finally:
            await db.close()

    _run(scenario())


def test_legacy_db_index_actually_created(legacy_db_path):
    """③ idx_subtitles_chapter 真的建出来了

    注意：不能只断言「启动没报错」—— 修复里给索引建库加了 try/except 容错，
    万一索引因为别的原因被吞掉，测试必须能发现（否则查询会退化成全表扫）。
    """
    db = DatabaseManager()

    async def scenario():
        await db.initialize()
        try:
            idx = _index_names(legacy_db_path)
            assert "idx_subtitles_chapter" in idx, (
                f"章节索引未建出，现有索引：{sorted(idx)}"
            )
        finally:
            await db.close()

    _run(scenario())


def test_legacy_db_data_preserved(legacy_db_path):
    """④ 老数据没被破坏"""
    db = DatabaseManager()

    async def scenario():
        await db.initialize()
        try:
            rows = await db.db.execute_fetchall(
                "SELECT id, text FROM subtitles"
            )
            assert len(rows) == 1, "老字幕行丢了"
            assert rows[0]["text"] == "这是老用户的历史字幕"
        finally:
            await db.close()

    _run(scenario())


def test_fresh_db_still_initializes(tmp_path, monkeypatch):
    """⑤ 全新库（新装用户）也能正常初始化 —— 修索引时机不能把新装搞坏"""
    monkeypatch.setattr(manager_mod, "DB_PATH", tmp_path / "fresh.db")

    async def scenario():
        db = DatabaseManager()
        await db.initialize()
        try:
            cols = _columns(tmp_path / "fresh.db", "subtitles")
            assert "chapter_id" in cols
            idx = _index_names(tmp_path / "fresh.db")
            assert "idx_subtitles_chapter" in idx
        finally:
            await db.close()

    _run(scenario())


def test_create_tables_has_no_migration_dependent_index():
    """⑥ 结构守卫：_create_tables() 里不得再出现引用「迁移新增列」的索引

    这是**比行为测试更早失败**的一道闸 —— 它防的是「第三次再犯」，
    而不是等某个用户升级时才炸。任何人往 _create_tables 的 executescript 里
    加了引用 chapter_id / translation / session_id / next_review_at 的索引，
    本用例立刻红。

    判据：从 _create_tables 的源码里找 CREATE INDEX ... ON <表>(<列>)，
    只要 <列> 落在「靠 ALTER TABLE 补出来的列」集合里，就判失败。
    """
    src = inspect.getsource(DatabaseManager._create_tables)

    # 由 ALTER TABLE 补出来的列（_migrate 里新增的）
    migration_columns = {
        "chapter_id",
        "translation",
        "session_id",
        "next_review_at",
        "language",
        "simp_mode",
        "subtitle_lang",
    }

    import re

    # 抓 CREATE INDEX ... ON <table>(<cols>) 的列名部分
    pattern = re.compile(
        r"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+\w+\s+ON\s+\w+\s*\(([^)]*)\)",
        re.IGNORECASE,
    )
    for cols in pattern.findall(src):
        for col in [c.strip().strip('"').strip("`") for c in cols.split(",")]:
            assert col not in migration_columns, (
                f"_create_tables() 里建了依赖迁移列的索引（{col}）—— "
                "老库缺该列，会报 `no such column` 导致启动失败。"
                "请把索引挪到 _migrate() 末尾的「依赖新增列的索引」区块。"
            )


def test_migration_index_registry_is_not_empty():
    """⑦ 收口区存在且非空 —— 防止有人把索引又挪回 _create_tables 后忘了补回来"""
    src = inspect.getsource(DatabaseManager._migrate)
    assert "idx_subtitles_chapter" in src, (
        "迁移收口区里没有 idx_subtitles_chapter —— "
        "章节索引要么被误挪走了，要么根本没建"
    )
    assert "idx_vocabulary_due" in src
