#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真实线下课端到端验收：字幕落库 → 会话 → SRT 导出 → 总结 → 生词/笔记

配合 `import_lecture_to_db.py` 使用：先把离线 ASR 草稿灌库，再启动产品后端，
用本脚本逐条打真实 HTTP 端点，把「功能期望」变成可复核的 PASS/WARN/FAIL。

设计原则：
  * **不 mock**。每一条结论都来自真实后端 + 真实 SQLite 数据。
  * **区分 PASS / WARN / FAIL**：WARN = 端点活着但暴露了已知产品缺口
    （例如 export-srt 的 limit=500 截断、summary 的 LLM 未配置），
    这些恰恰是这次实测最值钱的发现，不能当成「测试通过」糊过去。
  * 报告落 JSON，可直接进 evidence 目录。

用法
----
    <venv>/python.exe scripts/verify_lecture_e2e.py \
        --course-id <id> --session <id> --expected <入库条数> \
        [--note-file <课堂笔记.md>] \
        [--report docs/competition/evidence/xxx.json] \
        [--base-url http://127.0.0.1:8765]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def call(base: str, method: str, path: str, body=None, timeout: float = 30):
    url = base.rstrip("/") + path
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = raw
            return resp.status, payload, dict(resp.headers)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw), dict(e.headers)
        except json.JSONDecodeError:
            return e.code, raw, dict(e.headers)
    except Exception as e:  # 连接失败 / 超时
        return 0, f"{type(e).__name__}: {e}", {}


def main() -> int:
    ap = argparse.ArgumentParser(description="真实课堂数据端到端验收")
    ap.add_argument("--base-url", default="http://127.0.0.1:8765")
    ap.add_argument("--course-id", required=True)
    ap.add_argument("--session", default="")
    ap.add_argument("--expected", type=int, default=0, help="灌库条数，用于比对导出是否被截断")
    ap.add_argument("--note-file", default="", help="可选：把这份笔记写入课程笔记并回读")
    ap.add_argument("--report", default="")
    ap.add_argument("--timeout", type=float, default=30)
    ap.add_argument("--wait", type=float, default=60, help="等待后端就绪的秒数")
    args = ap.parse_args()

    base = args.base_url
    cid = args.course_id
    checks: list[dict] = []

    def add(name, ok, detail, level="PASS"):
        checks.append({"name": name, "status": level if ok else "FAIL",
                       "detail": str(detail)[:500]})
        mark = {"PASS": "✅", "WARN": "🟡", "FAIL": "❌"}[level if ok else "FAIL"]
        print(f"  {mark} {name}: {str(detail)[:180]}")

    # 等后端
    deadline = time.time() + args.wait
    st = 0
    while time.time() < deadline:
        st, p, _ = call(base, "GET", "/api/ping", timeout=3)
        if st == 200:
            break
        time.sleep(1)
    add("后端 /api/ping 就绪", st == 200,
        f"HTTP {st} / version={p.get('version') if isinstance(p, dict) else p}", "PASS")
    if st != 200:
        print("后端未就绪，终止")
        return 1

    st, courses, _ = call(base, "GET", "/api/courses", timeout=args.timeout)
    found = None
    if isinstance(courses, list):
        found = next((c for c in courses if c.get("id") == cid), None)
    add("课程出现在 /api/courses", st == 200 and found is not None,
        f"HTTP {st} / name={found.get('name') if found else None}")

    # 🔴 2026-11-15 真实课实测：原 Query(le=500) 让 limit=1000 直接 422，
    #    且 /export-srt 内部写死 500 导致 715 条字幕静默截断。
    #    本轮已统一为 SUBTITLE_MAX_ROWS=5000，下面两组检查在修复前后都要如实报：
    #    修复前 → WARN（旧缺陷复现）；修复后 → PASS（可一次取全量、导出不截断）。
    st_over, subs_over, _ = call(base, "GET", f"/api/courses/{cid}/subtitles?limit=1000",
                                 timeout=args.timeout)
    if st_over == 422:
        add("🟡 /subtitles 单次上限仍为 500", True,
            "limit=1000 → HTTP 422（Query le=500）；记忆回放必须分页或改契约，不能一次拉全量",
            "WARN")
    else:
        n_over = len(subs_over) if isinstance(subs_over, list) else 0
        add("/subtitles limit=1000 可读（上限已放宽）",
            st_over == 200 and isinstance(subs_over, list) and n_over > 0,
            f"HTTP {st_over} / 返回 {n_over} 条 / 不再 422")

    st_all, subs_all, _ = call(base, "GET", f"/api/courses/{cid}/subtitles?limit=5000",
                               timeout=args.timeout)
    n_all = len(subs_all) if isinstance(subs_all, list) else 0
    all_ok = st_all == 200 and (not args.expected or n_all == args.expected)
    add("一次拉全量字幕（记忆回放前提）", all_ok,
        f"HTTP {st_all} / 返回 {n_all} 条 / 入库 {args.expected or '?'} 条")
    st, subs, _ = call(base, "GET", f"/api/courses/{cid}/subtitles?limit=500",
                       timeout=args.timeout)
    n = len(subs) if isinstance(subs, list) else 0
    add("字幕可读（/subtitles 裸数组，limit=500）",
        st == 200 and isinstance(subs, list) and n > 0, f"HTTP {st} / {n} 条")
    if isinstance(subs, list) and len(subs) > 1:
        ms = [s.get("start_ms", 0) for s in subs]
        add("/subtitles 按 start_ms 倒序", all(a >= b for a, b in zip(ms, ms[1:])),
            f"首 {ms[0]} / 末 {ms[-1]}")
        keys = set(subs[0].keys())
        add("字段为 snake_case（is_final / session_id）",
            "is_final" in keys and "session_id" in keys,
            f"字段样例 {sorted(keys)[:12]}")

    st, sess, _ = call(base, "GET", f"/api/courses/{cid}/sessions", timeout=args.timeout)
    slist = sess.get("sessions", []) if isinstance(sess, dict) else []
    target = next((s for s in slist if s["sessionId"] == args.session), None) if args.session else (slist[0] if slist else None)
    add("识别会话可列（/sessions）", st == 200 and target is not None,
        f"HTTP {st} / total={sess.get('total') if isinstance(sess, dict) else '?'} / "
        f"本会话 count={target.get('count') if target else '?'} / isLatest={target.get('isLatest') if target else '?'}")

    st, srt, _ = call(base, "GET", f"/api/courses/{cid}/export-srt", timeout=args.timeout)
    cnt = srt.get("count") if isinstance(srt, dict) else None
    srt_text = srt.get("srt", "") if isinstance(srt, dict) else ""
    blocks = len([b for b in srt_text.strip().split("\n\n") if b.strip()]) if srt_text else 0
    add("SRT 可导出（/export-srt）", st == 200 and cnt and cnt > 0,
        f"HTTP {st} / count={cnt} / 实际块={blocks} / 首行={srt_text[:60]!r}")
    # 时间轴不能错位：修复前 DB 按 start_ms DESC LIMIT 500 再 reverse，
    # 首行会是 00:08:22,760 —— 开头 8 分钟整段丢失，但 count 看着「正常」。
    import re as _re
    m_first = _re.search(r"(\d{2}):(\d{2}):(\d{2}),\d{3}\s*-->", srt_text)
    first_ms = (int(m_first.group(1)) * 3600 + int(m_first.group(2)) * 60
                + int(m_first.group(3))) * 1000 if m_first else -1
    add("SRT 时间轴从课程开头开始（无静默截断）", 0 <= first_ms < 60_000,
        f"首条时间 {m_first.group(0).strip() if m_first else 'N/A'} / 阈值 < 60s")
    if args.expected and cnt is not None and cnt < args.expected:
        add("🟡 export-srt 是否被 limit=500 截断", True,
            f"入库 {args.expected} 条，导出只有 {cnt} 条 → 端点内硬上限，产品缺陷已复现",
            "WARN")
    elif args.expected:
        add("导出条数与入库一致", cnt == args.expected, f"入库 {args.expected} / 导出 {cnt}")

    st, summary, _ = call(base, "POST", f"/api/courses/{cid}/summary", timeout=args.timeout)
    if st == 200 and isinstance(summary, dict) and summary.get("available") is False:
        add("🟡 AI 总结端点可用但 LLM 未配置", True,
            f"HTTP 200 / available=false / {summary.get('message')}", "WARN")
    elif st == 200:
        add("AI 总结可用", True, f"HTTP 200 / keys={list(summary.keys())[:8]}")
    else:
        add("AI 总结端点", st in (200, 503), f"HTTP {st} / {str(summary)[:150]}",
            "WARN" if st in (200, 503) else "PASS")

    st, vocab, _ = call(base, "GET", f"/api/courses/{cid}/vocabulary", timeout=args.timeout)
    add("生词本可读", st == 200 and isinstance(vocab, list),
        f"HTTP {st} / {len(vocab) if isinstance(vocab, list) else '?'} 词")

    st, backup, hdrs = call(base, "GET", f"/api/courses/{cid}/vocabulary/backup.json", timeout=args.timeout)
    cd = hdrs.get("Content-Disposition") or hdrs.get("content-disposition")
    if st == 200 and not cd:
        add("🟡 backup.json 缺下载头", True,
            "HTTP 200 但无 Content-Disposition → 浏览器不会下载，第 11 条反馈的后端根因之一", "WARN")
    else:
        add("backup.json 可导出", st == 200, f"HTTP {st} / Content-Disposition={cd}")

    if args.note_file:
        p = Path(args.note_file)
        if p.is_file():
            content = p.read_text(encoding="utf-8")[:20_000]
            st1, _, _ = call(base, "PUT", f"/api/courses/{cid}/note",
                             {"content": content}, timeout=args.timeout)
            st2, note, _ = call(base, "GET", f"/api/courses/{cid}/note", timeout=args.timeout)
            ok = st1 == 200 and st2 == 200 and isinstance(note, dict) and note.get("content") == content
            add("课堂笔记写入并回读（/note）", ok,
                f"PUT {st1} / GET {st2} / {len(content)} 字 / 回读一致={ok}")
        else:
            add("课堂笔记文件", False, f"找不到 {p}")

    st, ms, _ = call(base, "GET", "/api/asr/model-status", timeout=args.timeout)
    add("ASR 模型状态端点", st == 200, f"HTTP {st} / {str(ms)[:120]}")

    passed = sum(1 for c in checks if c["status"] == "PASS")
    warned = sum(1 for c in checks if c["status"] == "WARN")
    failed = sum(1 for c in checks if c["status"] == "FAIL")
    report = {
        "title": "真实线下课《生成式人工智能（二）》端到端验收",
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "baseUrl": base,
        "courseId": cid,
        "sessionId": args.session,
        "expectedSubtitleCount": args.expected,
        "summary": {"pass": passed, "warn": warned, "fail": failed, "total": len(checks)},
        "checks": checks,
    }
    out = Path(args.report) if args.report else ROOT / "docs" / "competition" / "evidence" / "lecture_genai02_e2e.json"
    out = out.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n合计 {len(checks)} 项：PASS {passed} / WARN {warned} / FAIL {failed}")
    print(f"报告 → {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
