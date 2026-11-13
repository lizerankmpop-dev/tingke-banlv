"""v4.0.0 包内验收：冻结后端 + 老库升级 + 合入功能端点确实在包里。

仓库内可独立运行的复核工具（路径从 __file__ 推导，不写死任何机器绝对路径）。
用法：

    python scripts/v400_upgrade_smoke.py

比 v3.9.0 多验两条（合并分支后最关键的回归点）：
  · /api/terms/generate 必须在冻结后端里可用 —— 若 PyInstaller 漏收
    llm.term_generator，端点会 500/404，而包表面能启动，最难排查；
  · 本地 ASR 链路未被云骨架破坏（/asr/model-status 可达）。

两个前提说明：
  · 需要一个已产生过数据的老库（~/.tingkebanlv/data/tingkebanlv.db）用于升级复验；
  · 老库可能已被 v3.9.0 真实启动过、course_notes 已自动建表。为了仍然验证
    「启动时自动建表」这条升级链路，脚本只在 temp 测试副本里 DROP 掉该表
    构造升级前快照，**绝不动真实库**。
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

# <桌面>/听课伴侣/项目源代码/scripts/ → 上两级 = <桌面>/听课伴侣
BASE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
EXE = os.path.join(BASE, "项目源代码", "frontend", "release", "win-unpacked",
                   "resources", "backend-server", "tingkebanlv-server.exe")
WORK = tempfile.mkdtemp(prefix="tingkebanlv_v400_upgrade_")
HOME = os.path.join(WORK, "home")
LOG = os.path.join(WORK, "server.log")
OLD_DB = os.path.expanduser(r"~\.tingkebanlv\data\tingkebanlv.db")
NEW_DB = os.path.join(HOME, "data", "tingkebanlv.db")
PORT = 18791
ROOT = f"http://127.0.0.1:{PORT}"
FAIL = []


def _tables(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _req(method, path, payload=None, timeout=30):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(ROOT + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
        return r.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        return e.code, (json.loads(raw) if raw.strip() else None)


def check(name, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}  {detail}", flush=True)
    if not ok:
        FAIL.append(name)


def safe_check(name, method, path, payload, ok_fn, timeout=30):
    """单个端点超时/连接异常也计入 FAIL，不让整份验收中途崩掉。"""
    try:
        st, body = _req(method, path, payload, timeout=timeout)
        detail = (body or {}).get("detail", "") if isinstance(body, dict) else ""
        check(name, ok_fn(st), f"status={st} detail={str(detail)[:80]}")
        return st, body
    except Exception as e:
        check(name, False, f"exception={type(e).__name__}: {e}")
        return None, None


print("=" * 78)
print("v4.0.0 包内验收 —", EXE)
print("临时工作目录 —", WORK)
if not os.path.exists(EXE):
    raise SystemExit("!! 找不到冻结 exe，请先跑 frontend/scripts/build-v400.sh")
if not os.path.exists(OLD_DB):
    raise SystemExit(
        "!! 找不到老库 ~/.tingkebanlv/data/tingkebanlv.db\n"
        "   本脚本用于「已有老库 → 新版本」升级复验，请先启动一次应用产生数据。"
    )

if os.path.isdir(HOME):
    shutil.rmtree(HOME)
os.makedirs(os.path.dirname(NEW_DB), exist_ok=True)
shutil.copy2(OLD_DB, NEW_DB)

con = sqlite3.connect(NEW_DB)
pre_tables = _tables(con)
pre_courses = con.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
pre_subs = con.execute("SELECT COUNT(*) FROM subtitles").fetchone()[0]
rows = con.execute("SELECT id FROM courses LIMIT 1").fetchall()
con.close()
print(f"老库基线：tables={sorted(pre_tables)} courses={pre_courses} subtitles={pre_subs}", flush=True)

# 构造「升级前」快照：只在测试副本里摘掉 course_notes，真实库不受影响。
if "course_notes" in pre_tables:
    con = sqlite3.connect(NEW_DB)
    con.execute("DROP TABLE course_notes")
    con.commit()
    con.close()
    print("  · 老库已含 course_notes → 在测试副本中 DROP，重建升级前快照", flush=True)
    pre_tables.discard("course_notes")

con = sqlite3.connect(NEW_DB)
assert "course_notes" not in _tables(con), "复验前提失败：测试副本未构造出升级前老库"
con.close()
if not rows:
    raise SystemExit("!! 老库没有课程，note 端点验收无法进行")
MIT_CID = rows[0][0]
print(f"  · 验收课程 id = {MIT_CID}", flush=True)

env = dict(os.environ)
env.update({"TINGKEBANLV_PORT": str(PORT), "TINGKEBANLV_HOST": "127.0.0.1",
            "TINGKEBANLV_RELOAD": "0", "TINGKEBANLV_HOME": HOME})
lf = open(LOG, "wb")
t0 = time.time()
proc = subprocess.Popen([EXE], cwd=os.path.dirname(EXE), env=env,
                        stdout=lf, stderr=subprocess.STDOUT)
ping = None
deadline = time.time() + 120
while time.time() < deadline:
    if proc.poll() is not None:
        break
    try:
        _, ping = _req("GET", "/api/ping", timeout=3)
        break
    except Exception:
        time.sleep(1.5)

try:
    check("冻结后端启动", ping is not None, f"{time.time()-t0:.1f}s")
    if ping:
        check("version == 4.0.0", ping.get("version") == "4.0.0",
              f"version={ping.get('version')!r}")

    # 老库升级
    con = sqlite3.connect(NEW_DB)
    tables = _tables(con)
    courses_now = con.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
    subs_now = con.execute("SELECT COUNT(*) FROM subtitles").fetchone()[0]
    con.close()
    check("course_notes 自动建表", "course_notes" in tables, f"tables={sorted(tables)}")
    check("老课程无损", courses_now == pre_courses, f"courses={courses_now}")
    check("老字幕无损", subs_now == pre_subs, f"subtitles={subs_now}")

    safe_check("note 端点读", "GET", f"/api/courses/{MIT_CID}/note", None,
               lambda st: st == 200)
    safe_check("note 端点写", "PUT", f"/api/courses/{MIT_CID}/note",
               {"content": "v400 验收"}, lambda s: s == 200)
    safe_check("note 端点写回可读", "GET", f"/api/courses/{MIT_CID}/note", None,
               lambda s: s == 200)
    try:
        _, g1 = _req("GET", f"/api/courses/{MIT_CID}/note", timeout=10)
        check("note 内容 == 写入值", (g1 or {}).get("content") == "v400 验收",
              f"content={(g1 or {}).get('content')!r}")
    except Exception as e:
        check("note 内容 == 写入值", False, f"exception={e}")

    # 🔴 合入功能：/api/terms/generate 必须存在（无模型时应为 503/400，绝不 404 或 500）
    st, _ = safe_check("AI 术语端点已打进包（非 404/500）", "POST",
                       "/api/terms/generate",
                       {"courseName": "验收课程", "domain": "计算机科学", "language": "auto"},
                       lambda s: s in (200, 400, 422, 503), timeout=40)
    if st == 500:
        check("llm.term_generator 未被 PyInstaller 漏收", False, "冻结后端缺模块")

    safe_check("本地 ASR 链路未被云骨架破坏", "GET", "/api/asr/model-status", None,
               lambda s: s == 200, timeout=45)
    safe_check("课程列表（老数据）", "GET", "/api/courses", None,
               lambda s: s == 200, timeout=20)
finally:
    print("\n关闭引擎 ...", flush=True)
    try:
        proc.terminate(); proc.wait(timeout=15)
    except Exception:
        try: proc.kill()
        except Exception: pass
    lf.close()
    print("服务器日志:", LOG)

print("\n" + "=" * 78)
if FAIL:
    print(f"验收结论：**失败 {len(FAIL)} 项** -> {FAIL}")
    sys.exit(1)
print("验收结论：全部通过 ✅（4.0.0 + 老库升级 + 合入端点在包内）")
