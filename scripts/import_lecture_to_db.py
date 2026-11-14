#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线 ASR 草稿 → 产品数据库（真实课堂端到端验证用）

为什么需要这个脚本
------------------
产品管道 `ASRPipeline` 吃的是**实时采集流**（麦克风 / 系统回环），没有
「把一段音频文件灌进数据库」的入口。于是 `asr_draft_transcript.py` 产出的
`.draft.json` 与 `/api/courses/{id}/subtitles`、`/sessions`、`/export-srt`
这几条产品链路之间是断开的 —— 想用真实课堂录音验证「字幕落库 → 会话 →
导出」，只能手工造数据。

本脚本只做一件事：把草稿的 `audioStart/audioEnd/text` 按 `db.add_subtitle()`
的同一套列定义写进 `subtitles`，并打上一个可识别的 `session_id`。
**不调用产品的 LLM、不修改任何产品代码、不伪造识别结果**：入库的就是
本机 faster-whisper 真实跑出来的文本。

幂等：同一个 `--session` 重跑会先删掉该会话的字幕再写；同名课程复用。

用法
----
    <venv>/python.exe scripts/import_lecture_to_db.py \
        --draft testset/chinese_lecture/real/real_zhlecture_genai_02.draft.json \
        --course "生成式人工智能（二）" \
        --domain "人工智能" --course-type offline \
        [--db <sqlite 路径>] [--session <id>] [--dry-run]

默认库与产品一致：`$TINGKEBANLV_HOME/data/tingkebanlv.db` 或
`~/.tingkebanlv/data/tingkebanlv.db`。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def default_db() -> Path:
    home = os.environ.get("TINGKEBANLV_HOME")
    return (Path(home) if home else Path.home() / ".tingkebanlv") / "data" / "tingkebanlv.db"


def connect(db: Path) -> sqlite3.Connection:
    if not db.is_file():
        raise SystemExit(f"数据库不存在：{db}\n请先启动一次产品后端让它建库。")
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    # 幂等建表：老库若缺列/缺表，用与 manager._migrate 相同的语义补齐
    con.execute(
        """CREATE TABLE IF NOT EXISTS course_notes (
               course_id TEXT PRIMARY KEY, content TEXT DEFAULT '', updated_at INTEGER)"""
    )
    cols = {r[1] for r in con.execute("PRAGMA table_info(subtitles)")}
    for col, ddl in (
        ("session_id", "TEXT DEFAULT ''"),
        ("chapter_id", "TEXT DEFAULT ''"),
        ("translation", "TEXT DEFAULT ''"),
    ):
        if col not in cols:
            con.execute(f"ALTER TABLE subtitles ADD COLUMN {col} {ddl}")
    con.commit()
    return con


def ensure_course(con: sqlite3.Connection, name: str, domain: str, ctype: str,
                  language: str, custom_terms: list[str]) -> tuple[str, bool]:
    row = con.execute("SELECT id FROM courses WHERE name = ? ORDER BY created_at LIMIT 1",
                      (name,)).fetchone()
    if row:
        return row[0], False
    cid = str(uuid.uuid4())
    now = int(time.time() * 1000)
    con.execute(
        """INSERT INTO courses (id, name, domain, description, custom_terms, course_type,
                                language, simp_mode, subtitle_lang, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (cid, name, domain, "2026-11-13 线下课堂录音（生成式人工智能（二））",
         json.dumps(custom_terms, ensure_ascii=False), ctype, language,
         "auto", "both", now, now),
    )
    con.commit()
    return cid, True


def main() -> int:
    ap = argparse.ArgumentParser(description="把离线 ASR 草稿灌入产品数据库")
    ap.add_argument("--draft", required=True, help="asr_draft_transcript.py 产出的 .draft.json")
    ap.add_argument("--course", required=True, help="课程名（同名复用，幂等）")
    ap.add_argument("--domain", default="人工智能")
    ap.add_argument("--course-type", default="offline", choices=("online", "offline"))
    ap.add_argument("--language", default="zh")
    ap.add_argument("--custom-terms", default="", help="逗号分隔")
    ap.add_argument("--db", default="", help="sqlite 路径（默认与产品同库）")
    ap.add_argument("--session", default="", help="会话 ID（默认按草稿文件名生成，可复现）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    draft_path = Path(args.draft).expanduser().resolve()
    if not draft_path.is_file():
        raise SystemExit(f"找不到草稿：{draft_path}")
    payload = json.loads(draft_path.read_text(encoding="utf-8"))
    segments = payload.get("segments") or []
    info = payload.get("info") or {}
    if not segments:
        raise SystemExit("草稿里没有段落，拒绝灌库")

    sid = args.session or f"{draft_path.stem.replace('.draft', '')}-{int(time.time() * 1000):x}"
    terms = [x.strip() for x in args.custom_terms.split(",") if x.strip()]

    print(f"草稿      : {draft_path.name}")
    print(f"段落      : {len(segments)}｜{info.get('chars', 0)} 字｜{info.get('durationMinutes', '?')} 分钟")
    print(f"课程      : {args.course}（{args.course_type}/{args.domain}）")
    print(f"会话      : {sid}")
    print(f"数据库    : {args.db or default_db()}")
    if len(segments) > 500:
        print(f"⚠ 段落 {len(segments)} > 500：GET /export-srt 的 limit=500 会截断，"
              f"这是产品缺陷，验证时应能看到 count 与入库条数不一致")
    if args.dry_run:
        print("--dry-run：不写库")
        return 0

    db = Path(args.db).expanduser().resolve() if args.db else default_db()
    con = connect(db)
    cid, created = ensure_course(con, args.course, args.domain, args.course_type,
                                 args.language, terms)
    print(f"课程 ID   : {cid}（{'新建' if created else '复用'}）")

    old = con.execute("SELECT COUNT(*) FROM subtitles WHERE course_id=? AND session_id=?",
                      (cid, sid)).fetchone()[0]
    if old:
        con.execute("DELETE FROM subtitles WHERE course_id=? AND session_id=?", (cid, sid))
        print(f"幂等清理  : 删除本会话旧字幕 {old} 条")

    now = int(time.time() * 1000)
    n = 0
    for s in segments:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        con.execute(
            """INSERT INTO subtitles (id, course_id, video_id, text, words_json,
                                      start_ms, end_ms, is_final, created_at,
                                      session_id, chapter_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, '')""",
            (str(uuid.uuid4()), cid, "", text, "[]",
             int(round(float(s["audioStart"]) * 1000)),
             int(round(float(s["audioEnd"]) * 1000)),
             now, sid),
        )
        n += 1
    con.commit()

    total = con.execute("SELECT COUNT(*) FROM subtitles WHERE course_id=?", (cid,)).fetchone()[0]
    span = con.execute("SELECT MIN(start_ms), MAX(end_ms) FROM subtitles WHERE course_id=? AND session_id=?",
                       (cid, sid)).fetchone()
    print(f"写入      : {n} 条｜课程累计 {total} 条｜时间轴 {span[0]}~{span[1]} ms")
    print(f"\nCOURSE_ID={cid}\nSESSION_ID={sid}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
