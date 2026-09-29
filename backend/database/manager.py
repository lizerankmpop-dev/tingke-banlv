"""
数据库管理器
使用 SQLite 存储课程、字幕、词汇数据
每个课程独立管理词汇表
"""

import os
import json
import uuid
import time
import logging
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

# 数据目录：优先环境变量，默认 ~/.tingkebanlv/data
def _get_data_dir() -> Path:
    """获取数据目录，优先环境变量，其次用户目录"""
    if env_dir := os.environ.get("TINGKEBANLV_DATA_DIR"):
        return Path(env_dir)
    if home := os.environ.get("TINGKEBANLV_HOME"):
        return Path(home) / "data"
    return Path.home() / ".tingkebanlv" / "data"

DB_DIR = _get_data_dir()
DB_PATH = DB_DIR / "tingkebanlv.db"


class DatabaseManager:
    def __init__(self):
        self.db = None

    async def initialize(self):
        """初始化数据库和表结构"""
        DB_DIR.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(str(DB_PATH))
        self.db.row_factory = aiosqlite.Row
        await self._create_tables()
        await self._migrate()

    async def close(self):
        if self.db:
            await self.db.close()

    async def _create_tables(self):
        """创建所有表"""
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS courses (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                domain TEXT NOT NULL DEFAULT '其他',
                description TEXT DEFAULT '',
                custom_terms TEXT DEFAULT '[]',
                course_type TEXT NOT NULL DEFAULT 'online',
                language TEXT DEFAULT 'auto',
                auto_extract_terms INTEGER DEFAULT 1,
                ollama_model TEXT DEFAULT 'qwen2.5:7b',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS subtitles (
                id TEXT PRIMARY KEY,
                course_id TEXT NOT NULL,
                video_id TEXT DEFAULT '',
                text TEXT NOT NULL,
                words_json TEXT DEFAULT '[]',
                start_ms INTEGER NOT NULL,
                end_ms INTEGER NOT NULL,
                is_final INTEGER DEFAULT 1,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS vocabulary (
                id TEXT PRIMARY KEY,
                course_id TEXT NOT NULL,
                word TEXT NOT NULL,
                translation TEXT DEFAULT '',
                definition TEXT DEFAULT '',
                domain TEXT DEFAULT '',
                context_sentence TEXT DEFAULT '',
                source_subtitle_id TEXT DEFAULT '',
                is_auto_generated INTEGER DEFAULT 0,
                is_memorized INTEGER DEFAULT 0,
                review_count INTEGER DEFAULT 0,
                created_at INTEGER NOT NULL,
                reviewed_at INTEGER NOT NULL,
                FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_subtitles_course ON subtitles(course_id);
            CREATE INDEX IF NOT EXISTS idx_vocabulary_course ON vocabulary(course_id);
            CREATE INDEX IF NOT EXISTS idx_vocabulary_word ON vocabulary(course_id, word);
        """)
        await self.db.commit()

    async def _migrate(self):
        """数据库迁移：为已有数据库补充新字段"""
        # 检查 courses 表是否有 language 列
        cursor = await self.db.execute("PRAGMA table_info(courses)")
        columns = [row[1] for row in await cursor.fetchall()]
        if "language" not in columns:
            await self.db.execute(
                "ALTER TABLE courses ADD COLUMN language TEXT DEFAULT 'auto'"
            )
            await self.db.commit()
            logger.info("数据库迁移：courses 表已添加 language 列")

    # ============ 课程 CRUD ============

    async def get_courses(self) -> list[dict]:
        """获取所有课程，附带视频和词汇计数"""
        rows = await self.db.execute_fetchall("""
            SELECT c.*,
                   (SELECT COUNT(DISTINCT video_id) FROM subtitles WHERE course_id = c.id) as video_count,
                   (SELECT COUNT(*) FROM vocabulary WHERE course_id = c.id) as word_count
            FROM courses c
            ORDER BY c.updated_at DESC
        """)
        return [self._row_to_dict(r) for r in rows]

    async def get_course(self, course_id: str) -> dict | None:
        row = await self.db.execute_fetchall(
            "SELECT * FROM courses WHERE id = ?", (course_id,)
        )
        if row:
            return self._row_to_dict(row[0])
        return None

    async def create_course(self, data: dict) -> dict:
        course_id = str(uuid.uuid4())
        now = int(time.time() * 1000)
        await self.db.execute(
            """INSERT INTO courses (id, name, domain, description, custom_terms, course_type, language, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                course_id,
                data["name"],
                data.get("domain", "其他"),
                data.get("description", ""),
                json.dumps(data.get("customTerms", []), ensure_ascii=False),
                data.get("courseType", "online"),
                data.get("language", "auto"),
                now,
                now,
            ),
        )
        await self.db.commit()
        return await self.get_course(course_id)

    async def update_course(self, course_id: str, data: dict) -> dict | None:
        course = await self.get_course(course_id)
        if not course:
            return None

        updates = []
        params = []
        for field in ["name", "domain", "description", "course_type", "language"]:
            if field in data:
                updates.append(f"{field} = ?")
                params.append(data[field])
        if "customTerms" in data:
            updates.append("custom_terms = ?")
            params.append(json.dumps(data["customTerms"], ensure_ascii=False))

        if updates:
            updates.append("updated_at = ?")
            params.append(int(time.time() * 1000))
            params.append(course_id)
            await self.db.execute(
                f"UPDATE courses SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            await self.db.commit()

        return await self.get_course(course_id)

    async def delete_course(self, course_id: str) -> bool:
        cursor = await self.db.execute("DELETE FROM courses WHERE id = ?", (course_id,))
        await self.db.commit()
        return cursor.rowcount > 0

    # ============ 字幕操作 ============

    async def add_subtitle(self, data: dict) -> dict:
        subtitle_id = str(uuid.uuid4())
        now = int(time.time() * 1000)
        await self.db.execute(
            """INSERT INTO subtitles (id, course_id, video_id, text, words_json, start_ms, end_ms, is_final, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                subtitle_id,
                data["course_id"],
                data.get("video_id", ""),
                data["text"],
                json.dumps(data.get("words", []), ensure_ascii=False),
                data["start_ms"],
                data["end_ms"],
                1 if data.get("is_final", True) else 0,
                now,
            ),
        )
        await self.db.commit()
        return {"id": subtitle_id, **data}

    async def get_subtitles(self, course_id: str, limit: int = 100) -> list[dict]:
        rows = await self.db.execute_fetchall(
            "SELECT * FROM subtitles WHERE course_id = ? ORDER BY start_ms DESC LIMIT ?",
            (course_id, limit),
        )
        return [self._row_to_dict(r) for r in rows]

    # ============ 词汇操作 ============

    async def get_vocabulary(self, course_id: str) -> list[dict]:
        rows = await self.db.execute_fetchall(
            "SELECT * FROM vocabulary WHERE course_id = ? ORDER BY created_at DESC",
            (course_id,),
        )
        return [self._row_to_dict(r) for r in rows]

    async def get_vocabulary_by_word(self, course_id: str, word: str) -> dict | None:
        rows = await self.db.execute_fetchall(
            "SELECT * FROM vocabulary WHERE course_id = ? AND word = ?",
            (course_id, word),
        )
        if rows:
            return self._row_to_dict(rows[0])
        return None

    async def upsert_vocabulary(self, data: dict) -> dict:
        """插入或更新词汇"""
        existing = await self.get_vocabulary_by_word(data["course_id"], data["word"])
        now = int(time.time() * 1000)

        if existing:
            # 更新
            await self.db.execute(
                """UPDATE vocabulary
                   SET translation = ?, definition = ?, domain = ?,
                       context_sentence = ?, source_subtitle_id = ?,
                       is_auto_generated = ?, reviewed_at = ?
                   WHERE id = ?""",
                (
                    data.get("translation", existing["translation"]),
                    data.get("definition", existing["definition"]),
                    data.get("domain", existing["domain"]),
                    data.get("context_sentence", existing.get("context_sentence", "")),
                    data.get("source_subtitle_id", existing["source_subtitle_id"]),
                    1 if data.get("isAutoGenerated", False) else existing["is_auto_generated"],
                    now,
                    existing["id"],
                ),
            )
            await self.db.commit()
            return await self.get_vocabulary_by_word(data["course_id"], data["word"])
        else:
            # 新建
            vocab_id = str(uuid.uuid4())
            await self.db.execute(
                """INSERT INTO vocabulary
                   (id, course_id, word, translation, definition, domain,
                    context_sentence, source_subtitle_id, is_auto_generated, created_at, reviewed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    vocab_id,
                    data["course_id"],
                    data["word"],
                    data.get("translation", ""),
                    data.get("definition", ""),
                    data.get("domain", ""),
                    data.get("context_sentence", ""),
                    data.get("source_subtitle_id", ""),
                    1 if data.get("isAutoGenerated", False) else 0,
                    now,
                    now,
                ),
            )
            await self.db.commit()
            return await self.get_vocabulary_by_word(data["course_id"], data["word"])

    async def update_vocabulary(self, vocab_id: str, data: dict) -> dict | None:
        rows = await self.db.execute_fetchall(
            "SELECT * FROM vocabulary WHERE id = ?", (vocab_id,)
        )
        if not rows:
            return None

        existing = self._row_to_dict(rows[0])
        await self.db.execute(
            """UPDATE vocabulary
               SET is_memorized = ?, review_count = ?, reviewed_at = ?
               WHERE id = ?""",
            (
                1 if data.get("isMemorized", existing["is_memorized"]) else 0,
                data.get("reviewCount", existing["review_count"]),
                int(time.time() * 1000),
                vocab_id,
            ),
        )
        await self.db.commit()
        rows = await self.db.execute_fetchall(
            "SELECT * FROM vocabulary WHERE id = ?", (vocab_id,)
        )
        return self._row_to_dict(rows[0])

    # ============ 工具方法 ============

    def _row_to_dict(self, row: aiosqlite.Row) -> dict:
        """将 SQLite Row 转换为字典，处理 JSON 字段"""
        d = dict(row)
        # 解析 JSON 字段
        for field in ["custom_terms", "words_json"]:
            if field in d and isinstance(d[field], str):
                try:
                    d[field] = json.loads(d[field])
                except json.JSONDecodeError:
                    d[field] = []
        # 重命名字段
        if "words_json" in d:
            d["words"] = d.pop("words_json")
        if "course_type" in d:
            d["courseType"] = d.pop("course_type")
        if "custom_terms" in d:
            d["customTerms"] = d.pop("custom_terms")
        if "created_at" in d:
            d["createdAt"] = d.pop("created_at")
        if "updated_at" in d:
            d["updatedAt"] = d.pop("updated_at")
        if "video_count" in d:
            d["videoCount"] = d.pop("video_count")
        if "word_count" in d:
            d["wordCount"] = d.pop("word_count")
        if "auto_extract_terms" in d:
            d["autoExtractTerms"] = d.pop("auto_extract_terms")
        if "ollama_model" in d:
            d["ollamaModel"] = d.pop("ollama_model")
        # 布尔转换
        for field in ["is_final", "is_auto_generated", "is_memorized", "auto_extract_terms"]:
            if field in d:
                d[field] = bool(d[field])
        return d
