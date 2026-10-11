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

# ---------- V5-4：简繁输出开关的默认值 ----------
# 刻意**不**从 `asr.whisper_engine` 导入：数据库层不该依赖 ASR 层（那会为了拿一个字面量
# 把 numpy / faster-whisper 拉进导入链），而消费侧本来就会用
# `normalize_simp_mode()` 收敛非法值。两处字面量的一致性由单测
# `test_lang_simp.py::test_simp_mode_default_agrees_with_engine` 守住 ——
# 让它漂移的后果是新课程存进去的值不被引擎认作默认，行为与界面提示不一致。
SIMP_MODE_DEFAULT = "auto-simplified"

# ---------- 生词复习闭环（v3.4.0）字段定义 ----------
#
# 迁移原则（硬约束）：**只 ADD COLUMN，绝不 DROP / 改类型 / 重建表**。
# 用户可能已经积累了上百个生词，任何「先清空再建」的做法都会让数据永久丢失。
DEFAULT_EFACTOR = 2.5
MIN_EFACTOR = 1.3
MAX_EFACTOR = 5.0
REVIEW_INTERVAL_MAX_DAYS = 3650.0  # 单次排程上限 10 年，防止异常值把用户排到几十年后
DAY_MS = 86_400_000

#: (列名, 列定义) —— 老库缺哪列补哪列
VOCABULARY_MIGRATIONS: list[tuple[str, str]] = [
    ("repetitions", "INTEGER DEFAULT 0"),      # SM2 连对次数，决定下一次间隔
    ("efactor", "REAL DEFAULT 2.5"),           # SM2 难度因子
    ("review_interval", "REAL DEFAULT 0"),     # 上次排程的间隔（天，允许小数）
    ("next_review_at", "INTEGER DEFAULT 0"),   # 到期时间（毫秒），<= now 即在待复习队列
    ("source_srt_time", "INTEGER DEFAULT 0"),  # 采集时对应的字幕时间戳，复习时可回看上下文
    ("updated_at", "INTEGER DEFAULT 0"),
]


def _local_day_start_ms(now_ms: int) -> int:
    """本地时区当天 0 点（「今日已复习」按用户本地日历算，不用 UTC）"""
    lt = time.localtime(now_ms / 1000)
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    return int(midnight * 1000)


def _safe_float(value, fallback: float, lo: float, hi: float) -> float:
    """容错取浮点并夹到区间；NaN/±inf 一律回退（手改过的备份文件里很常见）"""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return fallback
    if n != n or n in (float("inf"), float("-inf")):
        return fallback
    return min(hi, max(lo, n))


def _safe_int(value, fallback: int, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return fallback
    return min(hi, max(lo, n))


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
                -- V5-4：简繁输出开关。'auto-simplified' = auto 判为中文时强制简体（默认，
                -- 修缺陷 D1）；'raw' = 保留原始识别字形（= 修复前的行为，给高级用户退路）。
                -- 这里**不做取值约束**：历史行与新写入口径不同是常态，取值统一由
                -- asr.whisper_engine.normalize_simp_mode() 在消费侧收敛，非法值回落默认。
                simp_mode TEXT DEFAULT 'auto-simplified',
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
                source_srt_time INTEGER DEFAULT 0,
                is_auto_generated INTEGER DEFAULT 0,
                is_memorized INTEGER DEFAULT 0,
                review_count INTEGER DEFAULT 0,
                repetitions INTEGER DEFAULT 0,
                efactor REAL DEFAULT 2.5,
                review_interval REAL DEFAULT 0,
                next_review_at INTEGER DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER DEFAULT 0,
                reviewed_at INTEGER NOT NULL,
                FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_subtitles_course ON subtitles(course_id);
            CREATE INDEX IF NOT EXISTS idx_vocabulary_course ON vocabulary(course_id);
            CREATE INDEX IF NOT EXISTS idx_vocabulary_word ON vocabulary(course_id, word);
        """)
        await self.db.commit()
        # 注意：idx_vocabulary_due 不能在这里建。
        # 老库的 vocabulary 表已经存在，CREATE TABLE IF NOT EXISTS 会被跳过，
        # 此时 next_review_at 列还没加上 —— 在这里建索引会直接报
        # `no such column: next_review_at`，后端启动即失败（所有老用户升级后打不开应用）。
        # 它必须等到 _migrate() 的 ALTER TABLE 之后再建。

    async def _migrate(self):
        """数据库迁移：为已有数据库补充新字段（只增列，不动老数据）"""
        # 检查 courses 表是否有 language 列
        cursor = await self.db.execute("PRAGMA table_info(courses)")
        columns = [row[1] for row in await cursor.fetchall()]
        if "language" not in columns:
            await self.db.execute(
                "ALTER TABLE courses ADD COLUMN language TEXT DEFAULT 'auto'"
            )
            await self.db.commit()
            logger.info("数据库迁移：courses 表已添加 language 列")

        # V5-4：简繁输出开关。老库没有这一列，而 CREATE TABLE IF NOT EXISTS 会被跳过，
        # 所以必须在这里补 —— 否则老用户升级后一读取课程就报 no such column。
        cursor = await self.db.execute("PRAGMA table_info(courses)")
        columns = [row[1] for row in await cursor.fetchall()]
        if "simp_mode" not in columns:
            await self.db.execute(
                "ALTER TABLE courses ADD COLUMN simp_mode TEXT DEFAULT 'auto-simplified'"
            )
            await self.db.commit()
            logger.info("数据库迁移：courses 表已添加 simp_mode 列")

        # 生词复习闭环字段
        cursor = await self.db.execute("PRAGMA table_info(vocabulary)")
        existing = {row[1] for row in await cursor.fetchall()}
        added = [name for name, _ in VOCABULARY_MIGRATIONS if name not in existing]
        for name, ddl in VOCABULARY_MIGRATIONS:
            if name in added:
                await self.db.execute(f"ALTER TABLE vocabulary ADD COLUMN {name} {ddl}")

        if added:
            await self.db.commit()
            logger.info("数据库迁移：vocabulary 表已添加列 %s", ", ".join(added))
            # 回填历史数据。老词从来没有复习记录，让它们**立刻进入待复习队列**
            # （next_review_at = created_at <= now），否则用户升级后会觉得生词本空了。
            # efactor 用标准初值，repetitions 沿用已有复习次数。
            await self.db.execute(
                "UPDATE vocabulary SET efactor = ? WHERE efactor IS NULL OR efactor <= 0",
                (DEFAULT_EFACTOR,),
            )
            await self.db.execute(
                "UPDATE vocabulary SET repetitions = review_count "
                "WHERE repetitions IS NULL OR repetitions = 0"
            )
            await self.db.execute(
                "UPDATE vocabulary SET next_review_at = created_at "
                "WHERE next_review_at IS NULL OR next_review_at = 0"
            )
            await self.db.execute(
                "UPDATE vocabulary SET updated_at = created_at "
                "WHERE updated_at IS NULL OR updated_at = 0"
            )
            await self.db.commit()
            logger.info("数据库迁移：历史生词已回填复习字段，直接进入待复习队列")

        # 待复习队列按 (course_id, next_review_at) 检索，词量大时是关键索引。
        # 必须放在加列之后（见 _create_tables 里的说明）。
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_vocabulary_due ON vocabulary(course_id, next_review_at)"
        )
        await self.db.commit()

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
            """INSERT INTO courses (id, name, domain, description, custom_terms, course_type, language, simp_mode, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                course_id,
                data["name"],
                data.get("domain", "其他"),
                data.get("description", ""),
                json.dumps(data.get("customTerms", []), ensure_ascii=False),
                data.get("courseType", "online"),
                data.get("language", "auto"),
                data.get("simpMode", SIMP_MODE_DEFAULT),
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
        for field in ["name", "domain", "description", "course_type", "language", "simp_mode"]:
            if field in data:
                updates.append(f"{field} = ?")
                params.append(data[field])
        if "simpMode" in data:
            updates.append("simp_mode = ?")
            params.append(data["simpMode"])
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
               SET is_memorized = ?, review_count = ?, reviewed_at = ?, updated_at = ?
               WHERE id = ?""",
            (
                1 if data.get("isMemorized", existing["is_memorized"]) else 0,
                data.get("reviewCount", existing["review_count"]),
                int(time.time() * 1000),
                int(time.time() * 1000),
                vocab_id,
            ),
        )
        await self.db.commit()
        rows = await self.db.execute_fetchall(
            "SELECT * FROM vocabulary WHERE id = ?", (vocab_id,)
        )
        return self._row_to_dict(rows[0])

    # ============ 生词复习闭环 ============

    async def get_vocabulary_due(
        self, course_id: str, limit: int = 50, now: int | None = None
    ) -> list[dict]:
        """待复习队列：未掌握且已到期，按到期时间升序（最欠复习的排最前）"""
        now = int(time.time() * 1000) if now is None else now
        rows = await self.db.execute_fetchall(
            """SELECT * FROM vocabulary
               WHERE course_id = ? AND is_memorized = 0 AND next_review_at <= ?
               ORDER BY next_review_at ASC
               LIMIT ?""",
            (course_id, now, limit),
        )
        return [self._row_to_dict(r) for r in rows]

    async def get_vocabulary_stats(self, course_id: str) -> dict:
        """生词本统计（仪表盘卡片用），单条 SQL 聚合避免多次往返"""
        now = int(time.time() * 1000)
        day_start = _local_day_start_ms(now)
        rows = await self.db.execute_fetchall(
            """SELECT
                 COUNT(*)                                                        AS total,
                 COALESCE(SUM(CASE WHEN is_memorized = 1 THEN 1 ELSE 0 END), 0)   AS memorized,
                 COALESCE(SUM(CASE WHEN is_memorized = 0 AND next_review_at <= ?
                                   THEN 1 ELSE 0 END), 0)                        AS due,
                 COALESCE(SUM(CASE WHEN review_count > 0 THEN 1 ELSE 0 END), 0)   AS learned,
                 -- 「今日复习」必须带 review_count > 0 这个前提：
                 -- 新建生词时 reviewed_at 会被写成创建时间（历史行为，且该列 NOT NULL），
                 -- 只看 reviewed_at 会把今天新采集、一次都没复习的词算成「已复习」。
                 COALESCE(SUM(CASE WHEN review_count > 0 AND reviewed_at >= ?
                                   THEN 1 ELSE 0 END), 0)                        AS reviewed_today,
                 COALESCE(AVG(efactor), ?)                                        AS avg_efactor
               FROM vocabulary WHERE course_id = ?""",
            (now, day_start, DEFAULT_EFACTOR, course_id),
        )
        row = dict(rows[0]) if rows else {}
        total = int(row.get("total") or 0)
        memorized = int(row.get("memorized") or 0)
        return {
            "courseId": course_id,
            "total": total,
            "due": int(row.get("due") or 0),
            "memorized": memorized,
            "learned": int(row.get("learned") or 0),
            "reviewedToday": int(row.get("reviewed_today") or 0),
            "avgEfactor": round(float(row.get("avg_efactor") or DEFAULT_EFACTOR), 2),
            "masteryRate": round(memorized / total, 4) if total else 0.0,
            "daysUntilReviewed": 0,
            "now": now,
        }

    async def get_due_summary(self, now: int | None = None) -> dict:
        """
        全局待复习概览（跨课程）

        为什么必须单独一个方法：启动提醒发生在用户选课程**之前** —— 那时还不知道
        course_id。逐个课程查 stats 是 N 次数据库往返，课程一多就会把启动拖慢，
        而这个提醒本身只是个「锦上添花」的提示。
        """
        now = int(time.time() * 1000) if now is None else now
        day_start = _local_day_start_ms(now)
        rows = await self.db.execute_fetchall(
            """SELECT
                 COALESCE(SUM(CASE WHEN is_memorized = 0 AND next_review_at <= ?
                                   THEN 1 ELSE 0 END), 0)                    AS due,
                 COUNT(*)                                                      AS total,
                 COALESCE(SUM(CASE WHEN review_count > 0 AND reviewed_at >= ?
                                   THEN 1 ELSE 0 END), 0)                    AS reviewed_today
               FROM vocabulary""",
            (now, day_start),
        )
        row = dict(rows[0]) if rows else {}
        # 待复习最多的那门课：提醒卡片上的「去复习」按钮据此直达最有价值的那一课
        top = await self.db.execute_fetchall(
            """SELECT course_id, COUNT(*) AS due FROM vocabulary
               WHERE is_memorized = 0 AND next_review_at <= ?
               GROUP BY course_id ORDER BY due DESC LIMIT 1""",
            (now,),
        )
        top_row = dict(top[0]) if top else {}
        return {
            "due": int(row.get("due") or 0),
            "total": int(row.get("total") or 0),
            "reviewedToday": int(row.get("reviewed_today") or 0),
            "topCourseId": top_row.get("course_id"),
            "topCourseDue": int(top_row.get("due") or 0),
            "now": now,
        }

    async def review_vocabulary(self, vocab_id: str, schedule: dict) -> dict | None:
        """
        记录一次复习结果

        排程本身由前端 SM2 算好（见 frontend/src/lib/sm2.ts），这里只负责落库 +
        **兜底夹取**：前端算错、手工改包、或旧客户端传来异常值都可能把用户排到
        几十年后再复习，那等于这个词永远消失了，必须在下游挡住。
        """
        rows = await self.db.execute_fetchall(
            "SELECT * FROM vocabulary WHERE id = ?", (vocab_id,)
        )
        if not rows:
            return None

        now = int(time.time() * 1000)
        efactor = _safe_float(schedule.get("efactor"), DEFAULT_EFACTOR, MIN_EFACTOR, MAX_EFACTOR)
        interval = _safe_float(schedule.get("interval"), 0.0, 0.0, REVIEW_INTERVAL_MAX_DAYS)
        repetitions = _safe_int(schedule.get("repetitions"), 0, 0, 1000)

        # 允许最多 10 年的排程；不允许「排到过去」（那会导致每次复习都立刻再次到期）
        next_review_at = _safe_int(
            schedule.get("nextReviewAt"), now, now, now + int(REVIEW_INTERVAL_MAX_DAYS * DAY_MS)
        )

        await self.db.execute(
            """UPDATE vocabulary
               SET efactor = ?, review_interval = ?, repetitions = ?, next_review_at = ?,
                   review_count = review_count + 1, reviewed_at = ?, updated_at = ?,
                   is_memorized = ?
               WHERE id = ?""",
            (
                efactor,
                interval,
                repetitions,
                next_review_at,
                now,
                now,
                1 if schedule.get("isMemorized") else 0,
                vocab_id,
            ),
        )
        await self.db.commit()
        rows = await self.db.execute_fetchall(
            "SELECT * FROM vocabulary WHERE id = ?", (vocab_id,)
        )
        return self._row_to_dict(rows[0])

    async def delete_vocabulary(self, vocab_id: str) -> bool:
        cursor = await self.db.execute("DELETE FROM vocabulary WHERE id = ?", (vocab_id,))
        await self.db.commit()
        return cursor.rowcount > 0

    async def import_vocabulary(self, course_id: str, items: list[dict]) -> dict:
        """
        导入生词备份（JSON）

        与 upsert_vocabulary 的区别：**保留复习进度**（efactor/interval/repetitions/
        next_review_at）。用户重装应用后导入备份，不该从零开始背。
        """
        now = int(time.time() * 1000)
        created = updated = skipped = 0
        max_next = now + int(REVIEW_INTERVAL_MAX_DAYS * DAY_MS)

        for item in items:
            if not isinstance(item, dict):
                skipped += 1
                continue
            word = str(item.get("word") or "").strip()
            if not word:
                skipped += 1
                continue

            payload = (
                str(item.get("translation") or ""),
                str(item.get("definition") or ""),
                str(item.get("domain") or ""),
                str(item.get("contextSentence") or item.get("context_sentence") or ""),
                str(item.get("sourceSubtitleId") or item.get("source_subtitle_id") or ""),
                _safe_int(item.get("sourceSrtTime") or item.get("source_srt_time"), 0, 0, 2**53),
                1 if item.get("isAutoGenerated") else 0,
                1 if item.get("isMemorized") else 0,
                _safe_int(item.get("reviewCount"), 0, 0, 1_000_000),
                _safe_int(item.get("repetitions"), 0, 0, 1000),
                _safe_float(item.get("efactor"), DEFAULT_EFACTOR, MIN_EFACTOR, MAX_EFACTOR),
                _safe_float(item.get("interval"), 0.0, 0.0, REVIEW_INTERVAL_MAX_DAYS),
                _safe_int(item.get("nextReviewAt"), now, 0, max_next),
            )
            created_at = _safe_int(item.get("createdAt"), now, 0, 2**53)
            reviewed_at = _safe_int(item.get("reviewedAt"), now, 0, 2**53)

            existing = await self.get_vocabulary_by_word(course_id, word)
            if existing:
                await self.db.execute(
                    """UPDATE vocabulary
                       SET translation = ?, definition = ?, domain = ?, context_sentence = ?,
                           source_subtitle_id = ?, source_srt_time = ?, is_auto_generated = ?,
                           is_memorized = ?, review_count = ?, repetitions = ?, efactor = ?,
                           review_interval = ?, next_review_at = ?, updated_at = ?, reviewed_at = ?
                       WHERE id = ?""",
                    (*payload, now, reviewed_at, existing["id"]),
                )
                updated += 1
            else:
                await self.db.execute(
                    """INSERT INTO vocabulary
                       (id, course_id, word, translation, definition, domain, context_sentence,
                        source_subtitle_id, source_srt_time, is_auto_generated, is_memorized,
                        review_count, repetitions, efactor, review_interval, next_review_at,
                        created_at, updated_at, reviewed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (str(uuid.uuid4()), course_id, word, *payload, created_at, now, reviewed_at),
                )
                created += 1

        await self.db.commit()
        logger.info("生词导入完成：新增 %d，更新 %d，跳过 %d", created, updated, skipped)
        return {"created": created, "updated": updated, "skipped": skipped}

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
        if "simp_mode" in d:
            # 老库刚迁移出来时可能是 NULL；给默认值，免得前端拿到 null 后 select 空白
            d["simpMode"] = d.pop("simp_mode") or SIMP_MODE_DEFAULT
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

        # ---------- 词汇字段的 camelCase 别名 ----------
        # 前端（WordPanel / VocabularyBook）读的是 camelCase，但历史实现只给 courses
        # 的字段做了改名 —— 结果 wordEntry.contextSentence 一直是 undefined，
        # 单词卡片里的「来源字幕句子」从来没显示过（与 custom_terms 静默丢弃是同一类问题）。
        # 这里**补别名而不是改名**：manager 内部还在读 existing["is_memorized"] /
        # existing["review_count"]，直接改名会连带改坏它们。
        for snake, camel in (
            ("context_sentence", "contextSentence"),
            ("source_subtitle_id", "sourceSubtitleId"),
            ("source_srt_time", "sourceSrtTime"),
            ("is_auto_generated", "isAutoGenerated"),
            ("is_memorized", "isMemorized"),
            ("review_count", "reviewCount"),
            ("reviewed_at", "reviewedAt"),
            ("review_interval", "interval"),
            ("next_review_at", "nextReviewAt"),
        ):
            if snake in d:
                d[camel] = d[snake]
        return d
