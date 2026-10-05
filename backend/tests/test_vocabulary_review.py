"""
生词复习闭环：数据层测试

覆盖三件最容易出事、且一旦出事就无法挽回的事：
  1. **增量迁移不能碰老数据** —— 用户可能已经攒了几百个生词，
     迁移只允许 ADD COLUMN；这里专门构造「旧 schema 库 + 有数据」的场景验证
  2. **排程值必须被夹取** —— 前端算错/手工改包传进来的异常值会把某个词
     排到几十年后再复习，等价于这个词永久消失
  3. **导入备份不能删旧词** —— 导入一份旧备份把后来的新词冲掉是典型事故
"""

import asyncio
import sqlite3
import time

import pytest

from database import manager as mgr
from database.manager import DatabaseManager, _local_day_start_ms, _safe_float, _safe_int

DAY_MS = 86_400_000

#: v3.4.0 之前的 vocabulary 表结构（迁移测试的起点）
LEGACY_VOCABULARY_DDL = """
CREATE TABLE courses (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, domain TEXT NOT NULL DEFAULT '其他',
    description TEXT DEFAULT '', custom_terms TEXT DEFAULT '[]',
    course_type TEXT NOT NULL DEFAULT 'online', language TEXT DEFAULT 'auto',
    auto_extract_terms INTEGER DEFAULT 1, ollama_model TEXT DEFAULT 'qwen2.5:7b',
    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE vocabulary (
    id TEXT PRIMARY KEY, course_id TEXT NOT NULL, word TEXT NOT NULL,
    translation TEXT DEFAULT '', definition TEXT DEFAULT '', domain TEXT DEFAULT '',
    context_sentence TEXT DEFAULT '', source_subtitle_id TEXT DEFAULT '',
    is_auto_generated INTEGER DEFAULT 0, is_memorized INTEGER DEFAULT 0,
    review_count INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL, reviewed_at INTEGER NOT NULL
);
"""


def run(coro):
    return asyncio.run(coro)


# ============ 迁移 ============

def test_migration_adds_columns_and_keeps_legacy_rows(tmp_path, monkeypatch):
    """老库迁移：只加列、老数据一条不少、且立刻进入待复习队列"""
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_file)
    conn.executescript(LEGACY_VOCABULARY_DDL)
    now = int(time.time() * 1000)
    conn.execute(
        "INSERT INTO courses (id, name, created_at, updated_at) VALUES ('c1', '旧课程', ?, ?)",
        (now, now),
    )
    conn.execute(
        """INSERT INTO vocabulary
           (id, course_id, word, translation, review_count, created_at, reviewed_at)
           VALUES ('v1', 'c1', 'legacy', '遗留词', 3, ?, ?)""",
        (now, now),
    )
    conn.commit()
    conn.close()

    async def scenario():
        monkeypatch.setattr(mgr, "DB_PATH", db_file)
        db = DatabaseManager()
        await db.initialize()
        try:
            rows = await db.get_vocabulary("c1")
            assert len(rows) == 1, "迁移后老数据必须还在"
            row = rows[0]
            # 回填：efactor 取标准初值、repetitions 沿用已有的 review_count
            assert row["efactor"] == pytest.approx(2.5)
            assert row["repetitions"] == 3
            assert row["reviewCount"] == 3
            # 老词立刻进入待复习队列
            assert row["nextReviewAt"] == now
            due = await db.get_vocabulary_due("c1", now=now + 1)
            assert [r["word"] for r in due] == ["legacy"]
            # 老字段仍在（不能删列）
            assert row["translation"] == "遗留词"
        finally:
            await db.close()

    run(scenario())


def test_migration_is_idempotent(tmp_path, monkeypatch):
    """迁移可重复执行：第二次不应报错，也不应把已排程的词重置回创建时间"""
    db_file = tmp_path / "again.db"

    async def scenario():
        monkeypatch.setattr(mgr, "DB_PATH", db_file)
        db = DatabaseManager()
        await db.initialize()
        try:
            now = int(time.time() * 1000)
            await db.db.execute(
                "INSERT INTO courses (id, name, created_at, updated_at) VALUES ('c1','课',?,?)",
                (now, now),
            )
            await db.upsert_vocabulary({"course_id": "c1", "word": "alpha", "translation": "α"})
            entry = await db.get_vocabulary_by_word("c1", "alpha")
            future = now + 30 * DAY_MS
            await db.review_vocabulary(
                entry["id"],
                {"rating": "good", "efactor": 2.6, "interval": 30, "repetitions": 3,
                 "nextReviewAt": future},
            )
            # 再跑一次初始化（等价于用户下次启动应用）
            await db._migrate()
            after = await db.get_vocabulary_by_word("c1", "alpha")
            assert after["nextReviewAt"] == future, "幂等迁移不能把已排程的词重置"
            assert after["efactor"] == pytest.approx(2.6)
        finally:
            await db.close()

    run(scenario())


# ============ 排程落库与夹取 ============

def test_review_clamps_absurd_schedule(tmp_path, monkeypatch):
    """异常排程必须被夹住：否则一个词会被排到几十年后，等于永久消失"""
    db_file = tmp_path / "clamp.db"

    async def scenario():
        monkeypatch.setattr(mgr, "DB_PATH", db_file)
        db = DatabaseManager()
        await db.initialize()
        try:
            now = int(time.time() * 1000)
            await db.db.execute(
                "INSERT INTO courses (id, name, created_at, updated_at) VALUES ('c1','课',?,?)",
                (now, now),
            )
            await db.upsert_vocabulary({"course_id": "c1", "word": "beta"})
            entry = await db.get_vocabulary_by_word("c1", "beta")

            far = now + 9999 * DAY_MS
            updated = await db.review_vocabulary(
                entry["id"],
                {"rating": "easy", "efactor": 99, "interval": 1e9,
                 "repetitions": 10**9, "nextReviewAt": far},
            )
            assert updated["efactor"] == pytest.approx(mgr.MAX_EFACTOR)
            assert updated["interval"] == pytest.approx(mgr.REVIEW_INTERVAL_MAX_DAYS)
            assert updated["repetitions"] == 1000
            assert updated["nextReviewAt"] <= far

            # 反向：排到过去会被抬到「现在」，避免每次刷新都立刻到期
            past = await db.review_vocabulary(
                entry["id"],
                {"rating": "again", "efactor": -5, "interval": -3,
                 "repetitions": -1, "nextReviewAt": 0},
            )
            assert past["efactor"] == pytest.approx(mgr.MIN_EFACTOR)
            assert past["interval"] == 0
            assert past["repetitions"] == 0
            assert past["nextReviewAt"] >= now

            # 脏字符串不能让接口 500
            dirty = await db.review_vocabulary(
                entry["id"],
                {"rating": "good", "efactor": "abc", "interval": None,
                 "repetitions": "x", "nextReviewAt": "不是数字"},
            )
            assert dirty["efactor"] == pytest.approx(mgr.DEFAULT_EFACTOR)
            assert dirty["reviewCount"] == 3  # 三次提交都记了数
        finally:
            await db.close()

    run(scenario())


def test_safe_numeric_helpers():
    assert _safe_float(None, 2.5, 1.3, 5.0) == 2.5
    assert _safe_float("x", 2.5, 1.3, 5.0) == 2.5
    assert _safe_float(float("nan"), 2.5, 1.3, 5.0) == 2.5
    assert _safe_float(float("inf"), 2.5, 1.3, 5.0) == 2.5
    assert _safe_float(9.9, 2.5, 1.3, 5.0) == 5.0
    assert _safe_int("7", 0, 0, 10) == 7
    assert _safe_int(None, 3, 0, 10) == 3
    assert _safe_int(-4, 0, 0, 10) == 0


# ============ 统计 ============

def test_stats_counts(tmp_path, monkeypatch):
    db_file = tmp_path / "stats.db"

    async def scenario():
        monkeypatch.setattr(mgr, "DB_PATH", db_file)
        db = DatabaseManager()
        await db.initialize()
        try:
            now = int(time.time() * 1000)
            await db.db.execute(
                "INSERT INTO courses (id, name, created_at, updated_at) VALUES ('c1','课',?,?)",
                (now, now),
            )
            for w in ("a", "b", "c", "d"):
                await db.upsert_vocabulary({"course_id": "c1", "word": w})

            empty = await db.get_vocabulary_stats("c1")
            assert empty["total"] == 4
            # 新建的词 next_review_at=0 → 立刻到期
            assert empty["due"] == 4
            assert empty["memorized"] == 0
            assert empty["reviewedToday"] == 0
            assert empty["masteryRate"] == 0.0

            a = await db.get_vocabulary_by_word("c1", "a")
            await db.review_vocabulary(
                a["id"], {"rating": "good", "efactor": 2.5, "interval": 1,
                          "repetitions": 1, "nextReviewAt": now + DAY_MS}
            )
            b = await db.get_vocabulary_by_word("c1", "b")
            await db.review_vocabulary(
                b["id"], {"rating": "easy", "efactor": 2.65, "interval": 3,
                          "repetitions": 1, "nextReviewAt": now + 3 * DAY_MS,
                          "isMemorized": True}
            )

            stats = await db.get_vocabulary_stats("c1")
            assert stats["total"] == 4
            assert stats["memorized"] == 1
            assert stats["learned"] == 2          # review_count > 0
            assert stats["reviewedToday"] == 2
            assert stats["due"] == 2              # c、d 仍到期；a、b 已排到未来
            assert stats["masteryRate"] == pytest.approx(0.25)
            assert stats["avgEfactor"] > 2.5
            assert _local_day_start_ms(now) <= now
        finally:
            await db.close()

    run(scenario())


# ============ 导入备份 ============

def test_import_preserves_schedule_and_keeps_existing(tmp_path, monkeypatch):
    """导入备份：保留复习进度；且绝不动导入前已有的新词"""
    db_file = tmp_path / "import.db"

    async def scenario():
        monkeypatch.setattr(mgr, "DB_PATH", db_file)
        db = DatabaseManager()
        await db.initialize()
        try:
            now = int(time.time() * 1000)
            await db.db.execute(
                "INSERT INTO courses (id, name, created_at, updated_at) VALUES ('c1','课',?,?)",
                (now, now),
            )
            await db.upsert_vocabulary({"course_id": "c1", "word": "existing", "translation": "已存在"})

            future = now + 12 * DAY_MS
            result = await db.import_vocabulary(
                "c1",
                [
                    {"word": "restored", "translation": "恢复的词", "efactor": 2.8,
                     "interval": 12, "repetitions": 4, "nextReviewAt": future,
                     "reviewCount": 4, "contextSentence": "a restored sentence"},
                    {"word": "existing", "translation": "被覆盖"},
                    {"word": "   "},          # 空词 → 跳过
                    "不是字典",                # 脏数据 → 跳过
                ],
            )
            assert result == {"created": 1, "updated": 1, "skipped": 2}

            restored = await db.get_vocabulary_by_word("c1", "restored")
            assert restored["efactor"] == pytest.approx(2.8)
            assert restored["interval"] == pytest.approx(12)
            assert restored["repetitions"] == 4
            assert restored["reviewCount"] == 4
            assert restored["nextReviewAt"] == future
            assert restored["contextSentence"] == "a restored sentence"

            existing = await db.get_vocabulary_by_word("c1", "existing")
            assert existing is not None, "导入不能删除已有词"
            assert existing["translation"] == "被覆盖"

            # 导入后总词数为 2（没有被删）
            assert len(await db.get_vocabulary("c1")) == 2
        finally:
            await db.close()

    run(scenario())


def test_import_tolerates_broken_numbers(tmp_path, monkeypatch):
    """手改过的备份文件里常见 "efactor": "" / null，不能让整次导入失败"""
    db_file = tmp_path / "import2.db"

    async def scenario():
        monkeypatch.setattr(mgr, "DB_PATH", db_file)
        db = DatabaseManager()
        await db.initialize()
        try:
            now = int(time.time() * 1000)
            await db.db.execute(
                "INSERT INTO courses (id, name, created_at, updated_at) VALUES ('c1','课',?,?)",
                (now, now),
            )
            result = await db.import_vocabulary(
                "c1",
                [{"word": "broken", "efactor": "", "interval": None, "nextReviewAt": "x",
                  "repetitions": "", "reviewCount": None}],
            )
            assert result["created"] == 1
            row = await db.get_vocabulary_by_word("c1", "broken")
            assert row["efactor"] == pytest.approx(mgr.DEFAULT_EFACTOR)
            assert row["interval"] == 0
            assert row["repetitions"] == 0
            assert row["nextReviewAt"] >= now
        finally:
            await db.close()

    run(scenario())


# ============ 字段命名契约 ============

def test_row_to_dict_exposes_camel_case_for_frontend(tmp_path, monkeypatch):
    """
    前端读的是 camelCase（contextSentence / nextReviewAt / isMemorized ...）
    历史实现只给 courses 的字段做了改名，导致单词卡片里的「来源字幕句子」
    一直是 undefined —— 这里把契约固化下来。
    """
    db_file = tmp_path / "camel.db"

    async def scenario():
        monkeypatch.setattr(mgr, "DB_PATH", db_file)
        db = DatabaseManager()
        await db.initialize()
        try:
            now = int(time.time() * 1000)
            await db.db.execute(
                "INSERT INTO courses (id, name, created_at, updated_at) VALUES ('c1','课',?,?)",
                (now, now),
            )
            entry = await db.upsert_vocabulary(
                {"course_id": "c1", "word": "gamma", "context_sentence": "gamma ray burst"}
            )
            for key in (
                "contextSentence", "sourceSubtitleId", "sourceSrtTime", "isAutoGenerated",
                "isMemorized", "reviewCount", "reviewedAt", "interval", "nextReviewAt",
                "efactor", "repetitions", "createdAt", "updatedAt",
            ):
                assert key in entry, f"缺少前端所需字段 {key}"
            assert entry["contextSentence"] == "gamma ray burst"
            assert entry["isMemorized"] is False
            # 老的 snake_case 键必须保留：manager 内部仍在读它们
            for key in ("is_memorized", "review_count", "review_interval_value"):
                if key == "review_interval_value":
                    continue
                assert key in entry, f"内部依赖的 snake_case 键 {key} 被误删"
        finally:
            await db.close()

    run(scenario())
