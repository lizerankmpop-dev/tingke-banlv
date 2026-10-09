#!/usr/bin/env python
"""
安装包分发验证：静默安装 → 自检 → 稳定性长测 → 卸载清理 → 报告

为什么需要它
------------
「安装包能打出来」和「在干净机器上装完就能用」是两件事。本轮有三处风险
只有在真实安装后的目录布局下才会暴露：

  1. **安装目录命名**：perMachine:false → `%LOCALAPPDATA%\\Programs\\<name>`。
     这里的 `<name>` 取 package.json **顶层 productName**，缺省时退化为 `name`；
     实测踩过一次，目录被建成 `tingke-banlv-frontend`。因此脚本一律**探测**
     实际落点，不假定名称。
  2. **静默部署**：`/S` 是否真把 resources/ 落全（少一个 manifest.json，
     首启播种就退化成联网下载 —— 而这正是本轮要消灭的场景）。
  3. **静默卸载**：必须 `/S /currentuser`，只传 `/S` 会静默失败（退出码 2、
     目录原样保留），是本次实装才暴露出来的。
  4. **注册表项**：装了要在「设置 → 应用」可见，卸载后要消失。

v3.4.0 起新增 **S 层（稳定性加固）断言**。为什么非要在「实装」这一步做：
`overlay-settings.cjs` 的单元测试用的是内存 fs 桩，真实文件系统的权限、
路径解析、原子改名语义都不在覆盖范围里；而主进程的迁移 / 节流 / 心跳
三条链路又恰好全部只在打包后的 Electron 运行时里才会真正跑起来。

    · 配置 v1→最新版迁移 + 迁移后用户设置原样保留（字号/颜色不能被重置；
      断言只要求 schemaVersion 为 ≥3 的整数、不绑定具体版本号，避免产品侧
      每次升 schema 本脚本就跟着过时——M3 时 v3→v4 就踩过一次）
    · 原子写无残留（overlay.json.tmp / .lock 必须为空）
    · 悬浮窗按 `enabled: true` 自动恢复
    · 节流决策：出口=悬浮窗时必须「不节流」（否则字幕会停）
    · IPC 心跳链路：必须至少收到一次渲染进程心跳，且全程无失联误报
    · 重启后配置与窗口位置保持，且不再重复迁移
    · 模块加载 / 数据目录权限（Cannot find module、EACCES/EPERM）无报错

另外内置模型是 v3.1.0 起的能力，必须验证**不设任何环境变量**时，冻结 exe 能从
`<安装目录>/resources/whisper_models` 自动反推出种子目录（model_bootstrap
的三级解析里，env 那一级在真实用户机上是空的）。

用法
----
    <venv>/Scripts/python.exe scripts/verify_install.py \
        --installer frontend/release/tingkebanlv-3.4.0-setup.exe \
        [--hold 150] [--report packaging/verify/report.json]

退出码：0 全部通过；1 有阻断项失败。报告同时落盘 JSON 与文末摘要。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ============ 常量 ============

APP_NAME = "听课伴侣"
BACKEND_EXE = "tingkebanlv-server.exe"
UNINSTALLER = f"Uninstall {APP_NAME}.exe"

# 这些字样出现在日志里就说明启动链路出了问题
LOG_ERROR_MARKERS = (
    "FATAL",
    "GPU process isn't usable",
    "后端启动超时",
    "未找到可用的 Python",
    "ERROR:",
    "Traceback",
)
LOG_OK_MARKERS = ("界面加载完成",)

# ---- 日志体检：把 traceback 当「块」看，而不是当关键词看 ----
# 原实现 `[m for m in LOG_ERROR_MARKERS if m in text]` 是全文子串匹配，两条后果
# 都在 2026-10-07 18:43 那轮真的发生了（`report-icon-rehearsal-v4-full.json`：
# `ok=false, critical=true, 命中: ['Traceback']`）：
#   1. **没有区分度** —— `Traceback` 一出现就判 FAIL，良性（客户端断开、解释器收尾）
#      与真实崩溃同罪；
#   2. **没有证据** —— 报告只留了「命中 Traceback」四个字，既没留是哪一段、
#      也没留终端异常行。事后完全无法追溯（修它时原日志已被下一轮覆盖，只能靠猜）。
# 现在按块取「终端异常行」，只有白名单里的终端异常才豁免；并把整个扫描结果写进
# 报告 data，下次再出现时自带证据。
BENIGN_TRACEBACK_TERMINALS = (
    # 对端在响应写回前断开（浏览器 / 渲染进程 / 探针提前关连接）—— 与应用逻辑无关
    "ConnectionResetError",
    "ConnectionAbortedError",
    # 解释器收尾竞态（Windows proactor），只发生在进程退出路径上
    "RuntimeError: Event loop is closed",
)


# ---- S 层断言用的日志锚点 ----
# 全部与 frontend/electron/main.cjs 里的字面量一一对应，改那边必须同步改这里。
# 刻意用「精确字面量」而不是宽松正则：宁可脚本报「标记缺失」让人来核对，
# 也不要因为正则太宽把一次真实的失败判成通过。
MIGRATION_LOG = "配置已从旧版本迁移到 schemaVersion="
OVERLAY_SHOWN_LOG = "[overlay] 悬浮窗已显示"
OVERLAY_LOADED_LOG = "[overlay] 页面加载完成"
HEARTBEAT_OK_LOG = "[overlay] 心跳链路已通"
HOST_LOST_LOG = "[overlay] 主窗口失联"
# 失联告警分两类：渲染进程真崩了（正确提示）vs 心跳超时（误报）。
# 只有后者算失败 —— 前者恰恰是「该提示的时候必须提示」。
HOST_CRASHED_LOG = "[fatal] 主窗口渲染进程退出"
HB_TIMEOUT_LOST_LOG = "未收到渲染进程心跳"
THROTTLE_LOG = "[perf] 后台节流="

# ---- M3 首启向导断言用的日志锚点 ----
# 与 frontend/electron/onboarding-state.cjs 的 describeEvent() 及 main.cjs 的
# 字面量一一对应，改那边必须同步改这里（沿用上面 S 层的「精确字面量」约定）。
ONBOARDING_OPEN_LOG = "[onboarding] 向导已打开"
ONBOARDING_WINDOW_LOG = "[onboarding] 向导窗口已创建"
ONBOARDING_DONE_LOG = "[onboarding] 完成"
ONBOARDING_DISMISS_LOG = "[onboarding] 跳过"

# 后端播种真发生时才会打的日志（asr/model_bootstrap.py 的 seed_model）。
# 它有两个用途，都是「靠副作用反推行为」的关键证据：
#   1. run-1 必须出现 —— 证明播种是**应用自己**做的，而不是验收脚本代劳；
#   2. run-2 必须消失 —— 幂等（已就绪走 verified 快路径，不再拷 142MB）。
SEED_START_LOG = "开始播种内置模型"

# Electron 主进程向「已销毁的渲染帧」发消息时，会由内部打一行
#   Error sending from webFrameMain: Error: Render frame was disposed ...
# 它只出现在渲染进程崩溃之后，属于 Electron 自身的告警，不是应用缺陷。
# 不过滤掉的话，「stdout 无致命字样」这项会被它永久污染。
BENIGN_STDIO_MARKERS = ("Error sending from webFrameMain",)

# 打包后「模块找不到」与「目录不可写」这两类问题都不会弹窗、界面看着照常，
# 只会静默退回默认配置 —— 所以必须主动搜日志。
MODULE_ERROR_MARKERS = ("Cannot find module", "MODULE_NOT_FOUND")
PERMISSION_ERROR_MARKERS = ("EACCES", "EPERM")

# 模拟「老用户升级」用的 v1 配置。三个关键缺失：无 schemaVersion、
# 无 backgroundUpdate（v1 时代是硬编码不节流）、无 anchor（多屏自愈是 v3 才有的）。
# fontSize=30 是个**非默认值**：迁移若写成「整体替换为默认配置」，
# 用户自己调好的字号/颜色会被静默抹掉 —— 这类 bug 不报错，只能靠逐字段核对。
LEGACY_V1_OVERLAY = {
    "enabled": True,
    "fontSize": 30,
    "color": "#ff5a5f",
    "lineCount": 4,
    "textBgOpacity": 0.5,
    "windowOpacity": 1,
    "clickThrough": True,
    "bounds": {"x": None, "y": None, "width": 900, "height": 220},
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class Report:
    """收集每步结果，最后落盘 JSON"""

    def __init__(self, path: Path | None = None) -> None:
        self.started = now_iso()
        self.steps: list[dict] = []
        self.passed = True
        self.path = path

    def step(self, name: str, ok: bool, detail: str = "", data=None, critical: bool = True) -> bool:
        entry = {
            "name": name,
            "ok": bool(ok),
            "critical": critical,
            "detail": str(detail),
            "at": now_iso(),
        }
        if data is not None:
            entry["data"] = data
        self.steps.append(entry)
        if not ok and critical:
            self.passed = False
        tag = " OK " if ok else ("FAIL" if critical else "WARN")
        print(f"[{tag}] {name}: {detail}")
        # 每一步都落盘。踩过的坑：报告原本只在结尾写一次，而这一轮验证的进程被
        # 环境中途杀掉（本机跑 700MB 的全新安装时，杀软首次扫描会把整个会话拖住），
        # 结果是「跑了半小时、一个结论都没留下」，只能靠翻日志反推。
        # 写一份 JSON 的成本远低于丢整轮证据。
        if self.path is not None:
            self.dump(self.path, quiet=True)
        return ok

    def dump(self, path: Path | None = None, quiet: bool = False) -> None:
        target = Path(path or self.path or "packaging/verify/report.json")
        payload = {
            "app": APP_NAME,
            "startedAt": self.started,
            "finishedAt": now_iso(),
            "passed": self.passed,
            "steps": self.steps,
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if not quiet:
            print(f"\n报告已写入: {target}")


# ============ 通用工具 ============

def clean_env(extra: dict | None = None) -> dict:
    """
    构造子进程环境

    **必须剥掉 ELECTRON_RUN_AS_NODE**。本机 Bash 环境默认带 `=1`，
    它会让 Electron 以纯 Node 模式启动：不开窗口、不初始化日志、不起后端，
    进程只是挂着 —— 表现为「实例数 1、存活 150s、日志 0 行」，
    测出来的稳定性结论完全是假的。第一次跑验证就栽在这里。
    """
    env = {**os.environ, **(extra or {})}
    env.pop("ELECTRON_RUN_AS_NODE", None)
    # NODE_OPTIONS 在打包后的 Electron 里不受支持，只会刷一行
    # "Most NODE_OPTIONs are not supported in packaged apps" 告警；
    # 真实用户机上不会有它，剥掉让测试环境更接近真实环境。
    env.pop("NODE_OPTIONS", None)
    return env


def run(cmd: list[str], timeout: int = 300, env: dict | None = None, cwd: str | None = None):
    """
    跑一个子进程，返回 (returncode, stdout, stderr)

    stdout 与 stderr **分开返回**：后端自检把结论 JSON 打到 stdout，
    而 logging 默认走 stderr。合并成一份的话，日志会插在 JSON 后面导致
    解析失败（曾把一次「passed: true」的自检误报成「未取到 JSON 结论」）。
    """
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            env=clean_env(env),
            cwd=cwd,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        return 124, "", f"超时（{timeout}s）: {exc}"

    def decode(raw: bytes | None) -> str:
        if not raw:
            return ""
        # 冻结后端与 Electron 走 UTF-8；NSIS 安装器是 GBK
        for codec in ("utf-8", "gbk", "cp936"):
            try:
                return raw.decode(codec)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    return proc.returncode, decode(proc.stdout), decode(proc.stderr)


def dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            pass
    return total


def mb(value: int) -> str:
    return f"{value / 1048576:.1f}MB"


def wait_for(predicate, timeout: float, interval: float = 0.5, desc: str = ""):
    """轮询等待条件成立；超时返回 False"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# 回环探测**必须绕开系统代理**。本机 shell 里 HTTP_PROXY=http://127.0.0.1:61450，
# urllib 对 http:// 默认信任它，连 127.0.0.1 也会被送进代理 —— 表现是
# /api/ping 34 次全部失败，看起来像「后端没起来」，其实是探测方式错了。
# 这正是本项目最初那个「打包应用永远不就绪」缺陷的同一类根因。
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_ok(url: str, timeout: float = 5.0) -> bool:
    try:
        with _NO_PROXY_OPENER.open(url, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def read_overlay_json(home: Path) -> dict | None:
    """读 overlay.json；不存在 / 损坏 / 权限不足都返回 None（由调用方决定怎么判）"""
    try:
        return json.loads((home / "overlay.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_onboarding_json(home: Path) -> dict | None:
    """
    读 onboarding.json（M3 首启向导状态）；不存在 / 损坏 / 权限不足都返回 None

    与 overlay.json 同等对待：**只读，绝不写** —— completed / dismissedAt
    决定用户还会不会被弹向导，验证脚本没有任何场景需要改它。
    """
    try:
        return json.loads((home / "onboarding.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _traceback_terminals(text: str) -> tuple[int, list[str]]:
    """
    数 traceback 块，并取每块的**终端异常行**

    判据来自 traceback 的固定体型：头行 `Traceback (most recent call last):`，
    正文全部缩进，最后一行顶格的 `XxxError: ...`。
    于是「头行之后的第一条顶格非空行」就是该块的终端异常。
    取不到（日志被截断）时该块记为空串，由调用方保守判坏。
    """
    lines = text.splitlines()
    count = 0
    terminals: list[str] = []
    for idx, line in enumerate(lines):
        if line.strip() != "Traceback (most recent call last):":
            continue
        count += 1
        terminal = ""
        for nxt in lines[idx + 1:]:
            if not nxt.strip() or nxt[:1].isspace():
                continue
            terminal = nxt.strip()
            break
        terminals.append(terminal)
    return count, terminals


def scan_log_errors(text: str) -> dict:
    """
    日志体检（纯函数，便于单测）

    返回 hitMarkers / tracebacks / terminals / benignTerminals / badTerminals。

    `Traceback` 这个 marker 的判定权**移交**给 terminals —— 否则良性 traceback
    永远过不去；其余 marker 仍是全文子串匹配。
    解析不到 terminal 时保守判坏：宁可让人来核对，也不要把一次真实崩溃判成通过。
    """
    hit = [m for m in LOG_ERROR_MARKERS if m != "Traceback" and m in text]
    count, terminals = _traceback_terminals(text)
    benign = [
        t for t in terminals
        if t and any(b in t for b in BENIGN_TRACEBACK_TERMINALS)
    ]
    bad = [t for t in terminals if t not in benign]
    if "Traceback" in text and count == 0:
        # 字样在、头行不在（日志被截断或换了形态）→ 保守判坏
        bad.append("<有 Traceback 字样但解析不出 traceback 块>")
    return {
        "hitMarkers": hit,
        "tracebacks": count,
        "terminals": terminals,
        "benignTerminals": benign,
        "badTerminals": bad,
    }


def _describe_log_scan(scan: dict, text: str) -> str:
    """把 scan_log_errors 的结果压成一行 detail（detail 是要给人看的）"""
    if not text:
        return "日志为空，无从判断"
    if scan["hitMarkers"]:
        return f"命中: {scan['hitMarkers']}"
    if scan["badTerminals"]:
        return f"traceback 终端异常: {scan['badTerminals']}"
    if scan["benignTerminals"]:
        return (
            f"干净（豁免 {len(scan['benignTerminals'])} 个良性 traceback: "
            f"{scan['benignTerminals'][:2]}）"
        )
    return "干净"


def overlay_residue(home: Path) -> list[str]:
    """
    原子写与文件锁的残留：`overlay.json.tmp` / `overlay.json.lock`

    正常退出后这两个都必须是空的：
      · 留下 .tmp → 进程在 rename 之前就死了，下次启动读到的是**旧**配置
      · 留下 .lock → releaseLock 没执行到（下次要靠 5s 陈旧判定自学，能用但会白重试一次）

    注意 `overlay.json.bad` 不在检查范围：那是配置损坏时**刻意**留下的证据文件，
    保留它是对的（直接删掉会让用户彻底无法自救）。
    """
    if not home.is_dir():
        return []
    return sorted(
        p.name for p in home.glob("overlay.json.*") if p.suffix in (".tmp", ".lock")
    )


# ============ M3 首启向导 · 人工验收清单 ============
#
# 这 5 条是**重启级 / 视觉级**场景，刻意不做自动 pass/fail 判定：
#   · ②③④ 要「改完状态再重启」—— 自动化里驱动 UI 走完向导需要 CDP 逐页
#     点击，打包环境下不稳定（docs/m3/onboarding_wizard.md §八 有同样结论）
#   · ①⑤ 里「弹窗肉眼是否正常」「文案读起来是否顺」是视觉/语言判断
# 分工：脚本采集**自动化证据**（日志锚点 + onboarding.json 字段）并生成清单；
# 人工按清单逐条执行、勾选，结果可整段追加进 docs/m3/onboarding_wizard.md §八。
# 人工项在报告里只做记录（critical=False），不影响 passed。

ONBOARDING_MANUAL_ITEMS = (
    {
        "id": "①",
        "title": "全新安装首启，向导自动弹出",
        "steps": "把 TINGKEBANLV_HOME 指向一个空目录（先按「安全操作」备份），启动应用，主窗口出现后观察",
        "expect": "向导窗口自动弹出，停在第 1/6 步「欢迎使用听课伴侣」",
        "evidence": "electron.log 的「[onboarding] 向导已打开」（本轮已自动采集）+ 弹窗截图",
        "note": "兼容模式按设计会推迟自动弹出 —— 若当轮走了兼容模式，以界面实际表现为准并记录",
    },
    {
        "id": "②",
        "title": "走完「完成」→ 重启不再弹出",
        "steps": "完整走完 6 步点「开始使用」→ 确认 onboarding.json 的 completed=true → 完全退出应用 → 重新启动",
        "expect": "重启后不再自动弹出",
        "evidence": "onboarding.json（completed 字段）；重启轮 electron.log 无「向导已打开」行",
        "note": "建议在 --keep-installed 保留安装后人工执行；数据目录安全要求见清单末尾「安全操作」",
    },
    {
        "id": "③",
        "title": "点「跳过」→ 重启不再弹出",
        "steps": "走前几步后点「跳过，直接使用」→ 确认 onboarding.json 的 dismissedAt 已写入 → 完全退出 → 重新启动",
        "expect": "重启后不再自动弹出",
        "evidence": "onboarding.json（dismissedAt 字段）；重启轮 electron.log",
        "note": "右上角 × 只关窗口、不写标记，下次启动仍应弹 —— 这条也要顺手确认",
    },
    {
        "id": "④",
        "title": "升级场景（已有 onboarding.json）不重复弹出",
        "steps": "在已有 completed=true 的 onboarding.json 的数据目录上，安装新版本并启动",
        "expect": "不弹向导；标记不被新版本重置（升级不重置向导是设计约束）",
        "evidence": "升级前后各读一次 onboarding.json，逐字段一致",
        "note": "⚠️ 在副本目录上做：先整目录改名备份，或复制一份用 TINGKEBANLV_HOME 指过去 —— 绝不清空真实数据目录",
    },
    {
        "id": "⑤",
        "title": "第 4 步穿透提示、第 6 步降级文案与控制面板口径一致",
        "steps": "向导第 4 步读「鼠标穿透」三态说明、第 6 步读术语开关四问；再开始识别进入字幕视图打开控制面板逐句对照",
        "expect": "两处均无「提示词 / token / 预算 / 截断 / 智能降级」；第 6 步含「重新开始听课…恢复完整术语表」；开关名两处同口径",
        "evidence": "自动化守卫 tests/onboarding-copy.test.mts（npm test 已拦截禁词）+ 两处截图",
        "note": "禁词拦截已自动化（提交时），人工只核对语义与两处一致性",
    },
)


def build_onboarding_checklist(paths: dict[str, str]) -> str:
    """
    生成《首启向导人工验收清单》markdown

    结构刻意做成**可整体追加**：标题为 `##` 级、自带证据路径表与「安全操作」，
    人工验收完成后整段拷进 docs/m3/onboarding_wizard.md 的验收章节即可归档。
    `paths` 的键原样出现在表格第一列，值是路径字符串。
    """
    rows = "\n".join(f"| {key} | `{value}` |" for key, value in paths.items())
    items = []
    for item in ONBOARDING_MANUAL_ITEMS:
        items.append(
            f"### {item['id']} {item['title']}\n\n"
            f"- [ ] **已执行并确认**\n\n"
            f"**操作**：{item['steps']}\n\n"
            f"**预期**：{item['expect']}\n\n"
            f"**证据**：{item['evidence']}\n\n"
            f"> 注意：{item['note']}\n"
        )
    return (
        f"## 首启向导 · 人工验收清单（verify_install 生成于 {now_iso()}）\n\n"
        "以下 5 条属于重启级 / 视觉级场景，**不做自动化 pass/fail 判定**："
        "自动化部分只采集证据，人工逐条执行并勾选，结果整段追加到 "
        "`docs/m3/onboarding_wizard.md` 的验收章节归档。\n\n"
        "### 证据文件\n\n"
        "| 文件 | 路径 |\n"
        "|---|---|\n"
        f"{rows}\n\n"
        + "\n---\n\n".join(items)
        + "\n---\n\n"
        "### 安全操作（务必先读）\n\n"
        f"- 本轮验证对数据目录（`{paths.get('数据目录', '')}`）只有**先备份后还原**，"
        "脚本内没有任何清理真实用户数据的逻辑。\n"
        "- 人工复测涉及「清空数据目录」时：先整目录改名备份，"
        "或用 `TINGKEBANLV_HOME` 指向临时空目录，绝不动真实配置。\n"
        "- 升级场景（条目④）在副本目录上做：复制整个数据目录再装新版本。\n"
    )


# ============ 主流程 ============

def discover_install_dir(local_appdata: Path) -> Path | None:
    """
    找到实际安装目录

    为什么不直接拼 `%LOCALAPPDATA%\\Programs\\<产品名>`：
    electron-builder 的 NSIS `$INSTDIR` 取的是 **package.json 顶层 productName**
    （缺省时退化为 `name`），与 `build.productName` 不是同一个来源。
    实测踩过一次：没写顶层 productName 时，目录被建成 `tingke-banlv-frontend`
    （npm 包名，还暴露了 frontend 这个内部实现词）。
    所以这里以探测为准，期望路径只作为首选候选。
    """
    programs = local_appdata / "Programs"
    if not programs.is_dir():
        return None
    for child in sorted(programs.iterdir()):
        if child.is_dir() and (child / f"{APP_NAME}.exe").is_file():
            return child
    return None


def uninstall_registry_entry() -> dict | None:
    """
    读取 HKCU 下的卸载注册表项

    只用于核验：装了必须有、卸载后必须没有。
    刻意不走 `reg.exe`（本机沙箱把它列进了程序黑名单），用 winreg。
    """
    if os.name != "nt":
        return None
    import winreg

    base = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, base)
    except OSError:
        return None
    info: dict | None = None
    for i in range(winreg.QueryInfoKey(root)[0]):
        try:
            sub = winreg.EnumKey(root, i)
            handle = winreg.OpenKey(root, sub)
            name = str(winreg.QueryValueEx(handle, "DisplayName")[0])
        except OSError:
            continue
        if APP_NAME not in name:
            continue
        entry = {"key": sub, "displayName": name}
        for field in ("DisplayVersion", "InstallLocation", "UninstallString", "Publisher"):
            try:
                entry[field] = str(winreg.QueryValueEx(handle, field)[0])
            except OSError:
                entry[field] = None
        info = entry
    return info


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", required=True, help="NSIS 安装包路径")
    parser.add_argument("--hold", type=int, default=150, help="启动后稳定性观察秒数")
    parser.add_argument("--report", default="packaging/verify/report.json")
    parser.add_argument("--keep-installed", action="store_true", help="跳过后半段卸载，便于人工点检")
    args = parser.parse_args()

    installer = Path(args.installer).resolve()
    report = Report(Path(args.report))

    print("=" * 76)
    print(f"{APP_NAME} 安装包分发验证")
    print(f"  时间    : {report.started}")
    print(f"  安装包  : {installer}")
    print(f"  观察时长: {args.hold}s")
    print("=" * 76)

    if not installer.is_file():
        report.step("定位安装包", False, f"不存在: {installer}")
        report.dump(Path(args.report))
        return 1
    report.step("定位安装包", True, f"{installer.name}（{mb(installer.stat().st_size)}）")

    local_appdata = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    expected_dir = local_appdata / "Programs" / APP_NAME
    home = Path(os.environ.get("TINGKEBANLV_HOME") or (Path.home() / ".tingkebanlv"))
    log_file = home / "logs" / "electron.log"

    # 安装前若有历史残留，先记一笔（卸载是否彻底要能对照）
    stale = discover_install_dir(local_appdata)
    if stale is not None:
        report.step(
            "安装前环境",
            True,
            f"检测到已存在的安装目录 {stale}（本次验证会覆盖/卸载它）",
            critical=False,
        )

    # ---------- 0. 备份既有用户数据 ----------
    # 卸载测试要验证「清理是否彻底」，因此需要清空数据目录重新走一遍首启。
    # 但真实用户数据不能丢，所以先改名备份，全部结束后再还原。
    backup: Path | None = None
    if home.exists():
        backup = home.with_name(home.name + f".preverify-{int(time.time())}")
        shutil.move(str(home), str(backup))
        report.step(
            "备份既有用户数据",
            True,
            f"{home} → {backup}（{mb(dir_size(backup))}，验证结束后还原）",
            critical=False,
        )
    else:
        report.step("备份既有用户数据", True, "无需备份（数据目录不存在）", critical=False)

    try:
        # ---------- 1. 静默安装 ----------
        started = time.time()
        code, out, err = run([str(installer), "/S"], timeout=600)
        installed = wait_for(
            lambda: discover_install_dir(local_appdata) is not None, timeout=180
        )
        elapsed = time.time() - started
        if not installed:
            report.step(
                "静默安装（/S）",
                False,
                f"退出码 {code}，180s 内未在任何目录下发现 {APP_NAME}.exe\n{err[-800:]}",
            )
            report.dump(Path(args.report))
            return 1

        install_dir = discover_install_dir(local_appdata)
        app_exe = install_dir / f"{APP_NAME}.exe"
        resources = install_dir / "resources"
        report.step(
            "静默安装（/S）",
            True,
            f"退出码 {code}，耗时 {elapsed:.1f}s，落点 {install_dir}",
            data={"installDir": str(install_dir)},
        )
        # 目录名是用户几乎看不到的实现细节。真正影响用户的注册表显示名、
        # 开始菜单快捷方式、主程序名都取 productName，且实测均正确；
        # 所以这里只作为观察项，不阻断结论。
        report.step(
            "安装目录命名（观察项，非阻断）",
            install_dir.name == APP_NAME,
            f"{install_dir.name}（期望 {APP_NAME}）—— "
            + (
                "与 productName 一致"
                if install_dir.name == APP_NAME
                else "NSIS $INSTDIR 取 npm 包名（顶层 productName 只影响程序名/快捷方式/注册表显示名）"
            ),
            critical=False,
        )

        # 注册表项：装了必须能出现在「设置 → 应用」里，否则用户没法正常卸载
        entry = uninstall_registry_entry()
        report.step(
            "注册卸载项（设置→应用 可见）",
            entry is not None,
            (
                f"{entry['displayName']}，UninstallString={entry['UninstallString']}"
                if entry
                else "未在 HKCU\\...\\Uninstall 下找到条目，用户将无法从系统正常卸载"
            ),
            data=entry,
        )

        # ---------- 2. 安装内容清点 ----------
        expected = {
            "主程序": app_exe,
            "冻结后端": resources / "backend-server" / BACKEND_EXE,
            "后端源码": resources / "backend" / "main.py",
            "前端产物": resources / "dist" / "index.html",
            "内置模型清单": resources / "whisper_models" / "manifest.json",
            "内置模型权重": resources / "whisper_models"
            / "models--Systran--faster-whisper-base" / "snapshots" / "seed" / "model.bin",
        }
        missing = [name for name, path in expected.items() if not path.is_file()]
        report.step(
            "安装内容清点",
            not missing,
            "全部就位" if not missing else f"缺失: {missing}",
            data={name: str(path) for name, path in expected.items()},
        )

        if expected["内置模型权重"].is_file():
            report.step(
                "内置模型权重体积",
                expected["内置模型权重"].stat().st_size > 100 * 1048576,
                mb(expected["内置模型权重"].stat().st_size),
            )

        # 中文路径本身是事实记录；能不能用由后面的自检与启动实测覆盖
        # （VAD 走 ONNX 后端而非 torch JIT，后者才不兼容非 ASCII 路径）
        non_ascii = [ch for ch in str(install_dir) if ord(ch) > 127]
        report.step(
            "中文安装路径",
            True,
            f"含 {len(non_ascii)} 个非 ASCII 字符：{install_dir}"
            "（可用性由自检与启动实测覆盖）",
            critical=False,
        )

        total = dir_size(install_dir)
        report.step("安装体积", True, mb(total), data={"bytes": total}, critical=False)

        # ---------- 3. 后端自检（关键：不设任何模型相关环境变量）----------
        # 真实用户机上 TINGKEBANLV_SEED_MODEL_DIR 是空的，冻结 exe 必须
        # 自己从 <安装目录>/resources/whisper_models 反推出种子目录。
        env = {
            "TINGKEBANLV_HOME": str(home),
            "HF_HUB_OFFLINE": "1",  # 强制离线：内置模型的意义就是断网可用
            "HF_ENDPOINT": "http://127.0.0.1:9",  # 仍要联网就一定失败
        }
        env.pop("TINGKEBANLV_SEED_MODEL_DIR", None)
        started = time.time()
        code, out, err = run([str(expected["冻结后端"]), "--selftest"], timeout=600, env=env)
        elapsed = time.time() - started

        # 取出 JSON 结论。**只从 stdout 里找**：logging 默认写 stderr，
        # 合并成一份的话日志会插在 JSON 尾部导致 json.loads 失败
        # （曾把一次 passed:true 的自检误报成「未取到 JSON 结论」）。
        # Windows 上 print 会把 \n 换成 \r\n，直接找 '{\n' 一定找不到
        # （第一次跑就因此把一次 passed:true 的自检判成「未取到 JSON 结论」）
        out_norm = out.replace("\r\n", "\n")
        json_start = out_norm.rfind('{\n  "selftest"')
        if json_start < 0:
            json_start = out_norm.find("{\n")
        out = out_norm
        payload = None
        if json_start >= 0:
            try:
                payload = json.loads(out[json_start:])
            except json.JSONDecodeError:
                payload = None

        if payload is None:
            report.step(
                "后端自检",
                False,
                f"未取到 JSON 结论（退出码 {code}）\n--- stdout ---\n{out[-1200:]}\n--- stderr ---\n{err[-800:]}",
            )
        else:
            # main.py 的输出结构是 {"selftest": [...], "passed": bool}
            checks = payload.get("selftest", [])
            failed = [s for s in checks if not s["ok"] and s["critical"]]
            warnings = [s for s in checks if not s["ok"] and not s["critical"]]
            report.step(
                "后端自检（离线，无环境变量）",
                bool(payload.get("passed")) and not failed,
                f"{len(checks)} 项，关键项全通过，非关键告警 {len(warnings)} 项，耗时 {elapsed:.1f}s",
                data=payload,
            )
            for name in ("内置模型种子", "模型完整性（sha256，损坏自动修复）", "完整识别链路（真实音频）"):
                hit = next((s for s in checks if s["name"] == name), None)
                if hit:
                    report.step(f"  · {name}", hit["ok"], hit["detail"], critical=hit["critical"])

        # 播种是否落到了数据目录。
        #
        # **注意这条断言证明不了「应用会播种」**：上面的 --selftest 自己就会调
        # ensure_model 把模型拷进来，所以不论产品是否播种它都会绿。
        # M3 实装验收正是因此漏掉了缺陷 B（首启第 3 步必然「模型准备失败」）。
        # 真正验产品行为的断言在下面 3c + 4 那两条（先清空、再启动应用）。
        seeded = home / "whisper_models" / "models--Systran--faster-whisper-base" / "snapshots" / "seed" / "model.bin"
        report.step(
            "后端自检播种落盘（--selftest 触发，非应用行为）",
            seeded.is_file(),
            f"{seeded}（{mb(seeded.stat().st_size) if seeded.is_file() else '不存在'}）",
            critical=False,
        )

        if args.keep_installed:
            report.step("跳过卸载", True, "--keep-installed", critical=False)
            report.dump(Path(args.report))
            return 0

        # ---------- 3b. 预置一份「老用户」的 v1 配置 ----------
        # 把「老用户升级」这条路径变成可断言的。先写一份 v1 格式的 overlay.json
        # （无 schemaVersion / 无 backgroundUpdate / 无 anchor，且 fontSize=30 非默认），
        # 启动后逐字段核对：该补的补上了、该留的一个没丢。
        home.mkdir(parents=True, exist_ok=True)
        (home / "overlay.json").write_text(
            json.dumps(LEGACY_V1_OVERLAY, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        report.step(
            "预置 v1 配置（模拟老用户升级）",
            True,
            "overlay.json：无 schemaVersion / 无 anchor / fontSize=30（非默认，用于验证不被重置）",
            critical=False,
        )

        # ---------- 3c. 清空模型工作副本：把「应用会不会自己播种」变成可断言的 ----------
        # 为什么必须清：`--selftest`（第 3 步）已经替产品把模型播种到了数据目录，
        # 于是「数据目录里有没有模型」这条断言对**应用行为**毫无分辨力 ——
        # M3 实装验收就是这么把缺陷 B 放过去的（第 3 步必然「模型准备失败」，
        # 而报告全绿）。这里把工作副本连同完整性标记一起移走，之后唯一还能把它
        # 写回来的，就只有后端 lifespan 的后台播种线程。
        #
        # 完整性标记 .seed-integrity-base.json 就落在该目录内部，随目录一起删除 ——
        # 留着它会让下一次 ensure_model 跳过深度校验，掩盖「模型其实已被删」的情形。
        seeded_dir = home / "whisper_models"
        if seeded_dir.exists():
            shutil.rmtree(seeded_dir, ignore_errors=True)
        report.step(
            "清空模型工作副本（为验证「应用自己播种」）",
            not seeded_dir.exists(),
            f"已移除 {seeded_dir}" if not seeded_dir.exists() else f"仍存在：{seeded_dir}",
            critical=False,
        )

        # ---------- 4. 启动应用 + 稳定性长测 ----------
        if log_file.exists():
            log_file.unlink()  # 只看本次启动的日志

        # 应用的 stdout/stderr 单独落一份。打包后的 Electron 是 GUI 子系统程序，
        # stdout 通常收不到东西；但一旦收到（例如 ELECTRON_RUN_AS_NODE 那种
        # 「根本没进 GUI 主进程」的情况），这份文件就是唯一的线索。
        app_stdio = Path(args.report).parent / "app-stdio.log"
        app_stdio.parent.mkdir(parents=True, exist_ok=True)
        stdio_handle = app_stdio.open("wb")

        proc = subprocess.Popen(
            [str(app_exe)],
            stdout=stdio_handle,
            stderr=subprocess.STDOUT,
            env=clean_env({"TINGKEBANLV_HOME": str(home)}),
        )

        def read_log() -> str:
            try:
                return log_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return ""

        def app_process_count() -> int:
            """
            按进程名数实例。

            注意这个数**包含 Electron 的子进程**（GPU / 渲染器 / utility 都用同一个 exe
            镜像名启动），所以 1 个「应用实例」通常显示为 3~5 个进程。
            判定存活只看是否 > 0，不看等于几。

            另外不能依赖 Popen 句柄：本环境下渲染器沙箱失效，应用会以
            `spawn(execPath, args, {detached:true})` 自我重启到兼容模式，
            **原进程会先退出** —— 拿 proc.poll() 判断存活会误报「已退出」。
            """
            code, listing, _ = run(
                ["tasklist", "/FI", f"IMAGENAME eq {APP_NAME}.exe", "/FO", "CSV", "/NH"],
                timeout=60,
            )
            if code != 0:
                return -1
            return sum(1 for line in listing.splitlines() if APP_NAME in line)

        # 先做一次「到底进没进 GUI 主进程」的判定：主进程启动第一步就是
        # 建 <TINGKEBANLV_HOME>/logs 并写第一行。这一步 20s 内就能出结论，
        # 不用等满 150s 才发现整个观察期都是无效的
        # （曾因 ELECTRON_RUN_AS_NODE 未剥离，应用以纯 Node 模式空转 150s，
        #   所有稳定性结论全部作废）。
        entered_gui = wait_for(lambda: log_file.is_file(), timeout=25, interval=1.0)
        report.step(
            "启动进入 GUI 主进程",
            entered_gui,
            "已创建日志文件" if entered_gui else "25s 内未创建日志文件（进程可能未进入 Electron 主进程）",
        )

        # 先等日志出现「内置模型种子」，确认主进程把种子路径传给了后端
        got_seed_log = wait_for(lambda: "内置模型种子" in read_log(), timeout=60, interval=1.0)
        report.step(
            "主进程传递种子路径",
            got_seed_log,
            next(
                (ln for ln in read_log().splitlines() if "内置模型种子" in ln),
                "60s 内日志中未出现",
            ),
        )

        # 等界面渲染完成
        painted = wait_for(
            lambda: any(marker in read_log() for marker in LOG_OK_MARKERS),
            timeout=150,
            interval=1.0,
        )
        report.step("界面加载完成", painted, "日志出现「界面加载完成」" if painted else "150s 内未出现")

        # ---- 缺陷 B 的正面断言：模型是**应用自己**播种的 ----
        # 前置条件由 3c 保证（工作副本已清空），所以这里的文件只可能来自
        # 后端 lifespan 的后台播种线程。日志里那条「开始播种内置模型」是直接证据：
        # 它由 seed_model 在真的开始拷贝时才打，已就绪的快路径不会打。
        app_seeded = wait_for(lambda: seeded.is_file(), timeout=180, interval=1.0)
        seed_start_line = next(
            (ln for ln in read_log().splitlines() if SEED_START_LOG in ln), ""
        )
        report.step(
            "应用启动后自行播种模型（lifespan 后台线程）",
            app_seeded and bool(seed_start_line),
            (
                f"{mb(seeded.stat().st_size)}；日志：{seed_start_line.strip()}"
                if app_seeded and seed_start_line
                else f"文件已出现={app_seeded}，播种日志={'有' if seed_start_line else '无'}"
                "（无日志说明走的是 ensure_model 快路径，播种其实不是这次做的）"
            ),
        )

        # 从日志里抓后端端口，用它做存活探测（比查进程更接近「真的能用」）。
        #
        # **必须锚定 `[backend] 就绪: http://127.0.0.1:<port>` 这一行**，
        # 不能笼统地取「最后一个含 127.0.0.1 的端口」。踩过的坑：uvicorn 的
        # 访问日志长这样 `127.0.0.1:65273 - "GET /api/courses HTTP/1.1" 200 OK`，
        # 里面是**客户端临时端口**；按「最后一个」解析会拿到 65273 而不是
        # 真正的服务端口 65255，于是 150 秒里 34 次探测全部失败，
        # 看起来像后端挂了，其实只是探错了端口。
        port = None
        for line in read_log().splitlines():
            marker = "[backend] 就绪: http://127.0.0.1:"
            if marker not in line:
                continue
            digits = ""
            for ch in line.split(marker, 1)[1]:
                if ch.isdigit():
                    digits += ch
                else:
                    break
            if digits:
                port = int(digits)
        report.step(
            "解析后端端口",
            port is not None,
            f"端口 {port}（取自「[backend] 就绪」标记行）" if port else "日志中未找到就绪标记行",
            critical=False,
        )

        # 长测：每 2 秒探一次 /api/ping，统计可用率；同时按进程名确认存活
        print(f"\n开始 {args.hold}s 稳定性观察（每 2s 探一次 /api/ping）…")
        started = time.time()
        samples = 0
        alive = 0
        dead_samples = 0
        last_progress = 0
        while time.time() - started < args.hold:
            count = app_process_count()
            if count == 0:
                dead_samples += 1
            if port:
                samples += 1
                if http_ok(f"http://127.0.0.1:{port}/api/ping"):
                    alive += 1
            elapsed = int(time.time() - started)
            if elapsed - last_progress >= 30:
                print(f"  … {elapsed}s / {args.hold}s（实例 {count} 个，ping {alive}/{samples}）")
                last_progress = elapsed
            time.sleep(2)

        final_count = app_process_count()
        ratio = (alive / samples) if samples else 0.0
        # 把日志里所有含回环地址的行留进报告：端口是这里解析出来的，
        # 一旦 ping 不通，这几行就是判断「端口取错」还是「后端真的没在听」的关键
        loopback_lines = [ln for ln in read_log().splitlines() if "127.0.0.1" in ln]
        report.step(
            f"{args.hold}s 稳定性",
            final_count > 0 and dead_samples == 0 and (ratio >= 0.95 if samples else True),
            f"进程 {final_count} 个（含 Electron 子进程），观察期内 {dead_samples} 次采样为 0；"
            f"/api/ping {alive}/{samples} 可用（{ratio * 100:.1f}%）",
            data={
                "samples": samples,
                "alive": alive,
                "deadSamples": dead_samples,
                "holdSeconds": args.hold,
                "finalProcessCount": final_count,
                "probedPort": port,
                "loopbackLogLines": loopback_lines[-12:],
            },
        )

        # 日志体检
        text = read_log()
        scan = scan_log_errors(text)
        report.step(
            "启动日志无错误字样",
            bool(text) and not scan["hitMarkers"] and not scan["badTerminals"],
            _describe_log_scan(scan, text),
            # 证据必须随报告落盘：命中时把「扫描结果 + 日志尾部」一并留下，
            # 否则下次出现仍然只能靠猜（这正是本次修它的起因）
            data={**scan, "logTail": text.splitlines()[-30:]},
        )
        relaunched = text.count("兼容模式") > 0
        report.step(
            "渲染器状态",
            bool(text),
            (
                "触发了兼容模式降级重启（已知环境限制，非缺陷）"
                if relaunched
                else ("一次启动成功" if text else "日志为空，无法判定")
            ),
            critical=False,
        )
        report.step(
            "日志落盘",
            log_file.is_file() and bool(text),
            f"{log_file}（{len(text.splitlines())} 行）",
        )

        # ---------- 4b. S 层稳定性加固断言 ----------
        # 悬浮窗在主窗口之后创建、页面异步加载，所以先等它就位，再统一取一次日志
        # 快照做断言（下面的断言全都基于这一份，避免「前半段读到的是旧内容」）。
        overlay_ready = wait_for(
            lambda: OVERLAY_SHOWN_LOG in read_log() and OVERLAY_LOADED_LOG in read_log(),
            timeout=90,
            interval=1.0,
        )
        text = read_log()

        report.step(
            "S层·悬浮窗自动恢复",
            overlay_ready,
            "上次 enabled=true → 本次已自动创建并加载完成"
            if overlay_ready
            else "90s 内未见「悬浮窗已显示 / 页面加载完成」，自动恢复失效",
        )

        migrated = MIGRATION_LOG in text
        report.step(
            "S层·配置迁移 v1→最新版",
            migrated,
            "日志出现「配置已从旧版本迁移到 schemaVersion=…」"
            if migrated
            else "未触发迁移（v1 配置被当成最新版读了，老用户会丢设置）",
        )

        cfg = read_overlay_json(home) or {}
        cfg_bounds = cfg.get("bounds") or {}
        cfg_schema = cfg.get("schemaVersion")
        preserved = all(
            (
                isinstance(cfg_schema, int) and cfg_schema >= 3,  # 迁移只升不降；不绑死具体版本
                cfg.get("fontSize") == 30,          # 用户改过的值必须原样保留
                cfg.get("color") == "#ff5a5f",
                cfg.get("backgroundUpdate") is True,  # v1→v2 补的默认值
                isinstance(cfg.get("anchor"), dict),  # v2→v3 补的锚点
                isinstance(cfg_bounds.get("x"), int),  # 未存位置 → 必须算出真实坐标
                isinstance(cfg_bounds.get("y"), int),
            )
        )
        report.step(
            "S层·迁移后设置保留",
            preserved,
            "schemaVersion≥3 / fontSize=30 与颜色原样保留 / 已补 backgroundUpdate 与 anchor / 坐标已落盘"
            if preserved
            else f"迁移结果不符预期: {json.dumps(cfg, ensure_ascii=False)[:500]}",
            data={"config": cfg},
        )

        residue = overlay_residue(home)
        report.step(
            "S层·原子写无残留",
            not residue,
            "无 .tmp / .lock 残留" if not residue else f"残留 {residue}（原子写或锁释放未收尾）",
        )

        throttle_line = next((ln for ln in text.splitlines() if THROTTLE_LOG in ln), "")
        throttle_ok = "后台节流=关" in throttle_line and "出口=悬浮窗" in throttle_line
        report.step(
            "S层·节流决策（有出口不节流）",
            throttle_ok,
            throttle_line.strip() if throttle_line
            else "日志中没有 [perf] 后台节流= 行，节流策略从未生效",
        )

        hb_ok = HEARTBEAT_OK_LOG in text and HB_TIMEOUT_LOST_LOG not in text
        report.step(
            "S层·IPC 心跳链路",
            hb_ok,
            "已收到渲染进程心跳，且无「心跳超时型」失联误报"
            if hb_ok
            else (
                "出现「超过 8000ms 未收到渲染进程心跳」→ 心跳监视误报（悬浮窗会闪红条）"
                if HB_TIMEOUT_LOST_LOG in text
                else "未见「心跳链路已通」，渲染进程根本没发出心跳"
            ),
            data={
                # 渲染进程真崩过时，跟着出现的「主窗口失联」是**正确**行为：
                # 悬浮窗此刻确实该提示用户。真正要防的是心跳超时造成的误报，
                # 所以两者分开记，只对后者判定失败。
                "crashDerivedHostLost": HOST_CRASHED_LOG in text,
                "heartbeatTimeoutHostLost": HB_TIMEOUT_LOST_LOG in text,
            },
        )

        mod_bad = [m for m in MODULE_ERROR_MARKERS if m in text]
        report.step(
            "S层·模块加载无报错",
            not mod_bad,
            "overlay-settings.cjs 随包正常加载" if not mod_bad
            else f"命中 {mod_bad}（打包后模块路径解析有问题）",
        )

        perm_bad = [m for m in PERMISSION_ERROR_MARKERS if m in text]
        report.step(
            "S层·数据目录可写（无 EACCES）",
            not perm_bad,
            f"overlay.json 读写正常（{home}）" if not perm_bad
            else f"命中 {perm_bad}，真实用户机可能受权限策略限制",
        )

        # 兼容模式记忆：这台机器一旦确认渲染器沙箱被拦，就要把标记落下，
        # 否则每次开应用都要「先崩一次渲染进程 + 白等 12 秒」。
        # 断言的是**一致性**而不是「必须有标记」：标准模式一次渲染成功的机器
        # 本来就不该有标记，那种情况下这一步自动通过。
        watchdog_fired = "判定为 Chromium 沙箱被系统策略拦截" in text
        compat_marker = (home / ".compat-mode").is_file()
        report.step(
            "S层·兼容模式记忆",
            (not watchdog_fired) or compat_marker,
            "标准模式一次渲染成功，无需兼容模式"
            if not watchdog_fired
            else (
                "已写 .compat-mode，下次启动直接进兼容模式"
                if compat_marker
                else "降级重启后没写记忆标记 → 下次启动还要再崩一次"
            ),
        )
        if watchdog_fired:
            # 这是本轮实装才暴露的阻断级缺陷：cwd 传成了 app.asar（文件），
            # spawn 直接 ENOENT。日志里错误信息指向 exe 路径，很容易误判成
            # 「找不到程序」。
            spawn_failed = "调用失败" in text or "ENOENT" in text
            report.step(
                "S层·兼容模式重启成功拉起",
                not spawn_failed,
                "降级实例已成功启动"
                if not spawn_failed
                else "降级重启失败（打包后 cwd/appPath 解析错误），应用将无法显示界面",
            )

        # 主进程的未捕获异常一律视为缺陷。这一条是本轮新加的回归护栏：
        # 修复前的日志里正是这样一行 —— spawn 的异步失败没被监听，
        # 一路冒到 uncaughtException，用户什么提示都看不到。
        uncaught = [m for m in ("未捕获异常", "未处理的 Promise 拒绝") if m in text]
        report.step(
            "S层·主进程无未捕获异常",
            not uncaught,
            "无未捕获异常 / 无未处理的 Promise 拒绝" if not uncaught
            else f"命中 {uncaught}，详见 electron.log",
        )

        # 这个应用的 logLine() 同时写文件与 console.log。打包后接管道时
        # stdout 是能收到的，所以「有输出」是**正常现象**，不是错误；
        # 真正值得看的是里面有没有异常字样 —— 而且这份文件的另一层价值是
        # 「有没有进 GUI 主进程」的兜底证据（纯 Node 模式不会有这些日志）。
        try:
            stdio_peek = app_stdio.read_text(encoding="utf-8", errors="replace")
        except OSError:
            stdio_peek = ""
        stdio_bad = [
            m
            for m in ("Traceback", "Error: ", "FATAL")
            if any(
                m in ln and not any(b in ln for b in BENIGN_STDIO_MARKERS)
                for ln in stdio_peek.splitlines()
            )
        ]
        report.step(
            "应用 stdout/stderr 无致命字样",
            not stdio_bad,
            f"{len(stdio_peek.splitlines())} 行（日志镜像，正常）"
            if not stdio_bad
            else f"命中 {stdio_bad}，见 {app_stdio}",
        )

        # ---------- 4c. M3 首启向导：自动化证据采集（不参与 pass/fail） ----------
        # 本轮启动前数据目录已被第 0 步整体备份移走，等价于「全新安装首启」，
        # 向导按设计应自动弹出 —— 这条可以自动采集日志证据。
        # 但「弹窗肉眼是否正常」「完成/跳过后不再弹」是重启级/视觉级场景，
        # 刻意不做自动判定：生成人工清单随报告归档（onboarding-manual-checklist.md）。
        wizard_log = wait_for(
            lambda: ONBOARDING_OPEN_LOG in read_log(), timeout=60, interval=1.0
        )
        ob_text = read_log()
        ob_state = read_onboarding_json(home)
        report.step(
            "M3·向导自动弹出（自动化证据）",
            wizard_log,
            (
                f"日志出现「{ONBOARDING_OPEN_LOG}」"
                + ("，向导窗口已创建" if ONBOARDING_WINDOW_LOG in ob_text else "")
                + "；onboarding.json："
                + (json.dumps(ob_state, ensure_ascii=False) if ob_state else "尚未落盘")
            )
            if wizard_log
            else "60s 内未见向导日志。兼容模式按设计会推迟自动弹出 —— 若界面确实弹了以界面为准；"
            "若当轮未走兼容模式也没弹，属缺陷，人工记入清单①",
            critical=False,
            data={"onboardingJson": ob_state, "windowCreated": ONBOARDING_WINDOW_LOG in ob_text},
        )

        # 人工验收清单：5 条勾选项 + 证据路径，随报告归档；结构可整段追加进
        # docs/m3/onboarding_wizard.md 的验收章节
        report_dir = Path(args.report).parent
        screenshots_dir = report_dir / "screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        checklist_path = report_dir / "onboarding-manual-checklist.md"
        checklist_paths = {
            "数据目录": str(home),
            "onboarding.json": str(home / "onboarding.json"),
            "本轮启动日志": str(log_file),
            "run-1 日志留档": str(report_dir / "electron-run1.log"),
            "日志总留档": str(report_dir / "electron.log"),
            "验证报告": str(Path(args.report)),
            "截图目录": str(screenshots_dir),
        }
        try:
            checklist_path.write_text(
                build_onboarding_checklist(checklist_paths), encoding="utf-8"
            )
            report.step("M3·人工验收清单已生成", True, str(checklist_path), critical=False)
        except OSError as exc:
            report.step("M3·人工验收清单已生成", False, str(exc), critical=False)
        for item in ONBOARDING_MANUAL_ITEMS:
            report.step(
                f"M3·人工验收{item['id']} {item['title']}（待人工，不参与判定）",
                True,
                f"操作与预期见 {checklist_path.name}",
                critical=False,
            )

        # ---------- 5. 关闭应用 ----------
        # 必须按进程名强杀：兼容模式下真正在跑的是自我分离出来的那个实例，
        # Popen 句柄指向的原进程早已退出，proc.terminate() 打不到它。
        run(["taskkill", "/IM", f"{APP_NAME}.exe", "/T", "/F"], timeout=60)
        gone = wait_for(lambda: app_process_count() == 0, timeout=30, interval=1.0)
        report.step(
            "退出后无残留进程",
            gone,
            "无残留" if gone else f"仍有 {app_process_count()} 个 {APP_NAME}.exe 在运行",
        )
        try:
            stdio_handle.close()
        except OSError:
            pass
        stdio_text = ""
        try:
            stdio_text = app_stdio.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass

        # ---------- 5b. 重启持久化 ----------
        # 只有「关掉再开」这条路径才会暴露的问题：窗口位置到底有没有真的落盘、
        # 上次开着的悬浮窗这次还会不会自己回来、迁移会不会每次启动都重跑一遍。
        #
        # logLine 是按天归档的，重启不会新建日志文件 —— 所以先把第一轮日志复制
        # 一份留档，再删掉 electron.log 让第二轮从空文件开始。否则「这一轮有没有
        # 出现 XX」会被上一轮的内容污染，`MIGRATION_LOG not in text2` 这种断言
        # 会永远为假。
        try:
            shutil.copy2(log_file, Path(args.report).parent / "electron-run1.log")
        except OSError:
            pass

        before_bounds = (read_overlay_json(home) or {}).get("bounds") or {}
        try:
            log_file.unlink()
        except OSError:
            pass

        print("\n开始重启持久化校验…")
        app_stdio2 = Path(args.report).parent / "app-stdio-run2.log"
        stdio_handle2 = app_stdio2.open("wb")
        subprocess.Popen(
            [str(app_exe)],
            stdout=stdio_handle2,
            stderr=subprocess.STDOUT,
            env=clean_env({"TINGKEBANLV_HOME": str(home)}),
        )
        entered2 = wait_for(lambda: log_file.is_file(), timeout=25, interval=1.0)
        painted2 = wait_for(
            lambda: any(marker in read_log() for marker in LOG_OK_MARKERS),
            timeout=150,
            interval=1.0,
        )
        restored2 = wait_for(
            lambda: OVERLAY_SHOWN_LOG in read_log(), timeout=90, interval=1.0
        )
        text2 = read_log()

        report.step(
            "S层·重启后悬浮窗自动恢复",
            entered2 and painted2 and restored2,
            "重启后主窗口加载完成，且悬浮窗按已保存的 enabled 自动恢复"
            if (entered2 and painted2 and restored2)
            else f"进入GUI={entered2} / 界面加载={painted2} / 悬浮窗恢复={restored2}",
        )
        report.step(
            "S层·重启不重复迁移",
            entered2 and MIGRATION_LOG not in text2,
            "已是最新 schemaVersion，未再触发迁移"
            if MIGRATION_LOG not in text2
            else "重启后又报了一次迁移 → 上一轮没有把迁移结果落盘",
        )

        # 播种幂等：run-1 已经把模型播好了，这一轮必须走 ensure_model 的
        # verified 快路径 —— 再拷一次 142MB 不只是慢，还会在每次启动时制造
        # 一段「模型目录正在被覆写」的窗口，正好和识别抢文件。
        report.step(
            "播种幂等：二次启动不重复拷贝模型",
            entered2 and SEED_START_LOG not in text2,
            "run-2 未出现「开始播种内置模型」，命中 check_installed 快路径"
            if SEED_START_LOG not in text2
            else "run-2 又完整播种了一次 → 幂等失效（完整性标记可能没落盘）",
        )

        # M3：本轮没有任何人驱动向导「完成/跳过」，onboarding.json 仍是未完成态，
        # 按设计重启后向导**应该**再次弹出 —— 这条自动证据反向支撑清单②/③
        # （只有写了 completed / dismissedAt 才不弹）；「不再弹」本身仍由人工验。
        wizard_again = ONBOARDING_OPEN_LOG in text2
        report.step(
            "M3·未完成向导时重启仍弹出（自动化证据）",
            wizard_again,
            "run-2 日志再次出现「向导已打开」，符合「未完成 → 每次启动都弹」的设计"
            if wizard_again
            else "run-2 未见向导日志。若向导确实弹了以界面为准；若没弹，先核对 onboarding.json 是否被误写成 completed/dismissed",
            critical=False,
        )

        # 上一轮若落下了兼容模式标记，这一轮必须直接走兼容模式：
        # 这是「用户第二次开应用不再白等 12 秒」的验收点。
        if compat_marker:
            via_marker = "来自记忆标记" in text2
            report.step(
                "S层·重启直接走兼容模式记忆",
                via_marker,
                "启动日志确认「来自记忆标记」，未再经历一次渲染进程崩溃"
                if via_marker
                else "标记存在但本轮回到了标准模式，记忆化没生效",
            )

        after_bounds = (read_overlay_json(home) or {}).get("bounds") or {}
        kept = all(
            before_bounds.get(key) == after_bounds.get(key)
            for key in ("x", "y", "width", "height")
        ) and before_bounds.get("x") is not None
        report.step(
            "S层·重启后窗口位置保持",
            kept,
            f"{before_bounds} 保持不变"
            if kept
            else f"位置在重启后被改写: {before_bounds} → {after_bounds}",
            data={"before": before_bounds, "after": after_bounds},
        )

        run(["taskkill", "/IM", f"{APP_NAME}.exe", "/T", "/F"], timeout=60)
        gone2 = wait_for(lambda: app_process_count() == 0, timeout=30, interval=1.0)
        report.step(
            "重启轮退出后无残留进程",
            gone2,
            "无残留" if gone2 else f"仍有 {app_process_count()} 个 {APP_NAME}.exe 在运行",
            critical=False,
        )
        try:
            stdio_handle2.close()
        except OSError:
            pass

        # ---------- 6. 静默卸载 ----------
        # **必须带 /currentuser**。踩过的坑：只传 /S 时卸载器退出码 2 且什么都不做
        # （安装目录 19 个条目原样保留，只在 %TEMP%\~nsu.tmp 留个自我拷贝就退出）。
        # 原因是 electron-builder 为 per-user 安装生成的卸载器要从参数里判定
        # 注册表视图与 shell 上下文，缺了这个开关就直接判定失败。
        # 这个坑只影响自动化/批量卸载，用户从「设置 → 应用」点卸载走的是
        # 注册表里的 UninstallString（本身就带 /currentuser），不受影响。
        uninstaller = install_dir / UNINSTALLER
        if not uninstaller.is_file():
            report.step("定位卸载器", False, f"未找到 {uninstaller}")
        else:
            code, out, err = run([str(uninstaller), "/S", "/currentuser"], timeout=300)
            gone = wait_for(lambda: not app_exe.is_file(), timeout=120)
            report.step(
                "静默卸载（/S /currentuser）",
                gone,
                f"退出码 {code}，主程序已移除" if gone else f"退出码 {code}，120s 后主程序仍在",
            )

            left = dir_size(install_dir) if install_dir.exists() else 0
            leftovers = sorted(p.name for p in install_dir.iterdir()) if install_dir.exists() else []
            report.step(
                "卸载后安装目录残留",
                left < 1024 * 1024,  # 小于 1MB 视为干净（NSIS 可能留空壳目录）
                "已清空" if left == 0 else f"{mb(left)}，残留: {leftovers[:8]}",
                critical=False,
            )

            # 注册表项是卸载器**最后**才删的，早于主程序文件消失。
            # 不做轮询会误判成「卸载没删注册项」（第一次跑就是这样）。
            removed = wait_for(lambda: uninstall_registry_entry() is None, timeout=60, interval=1.0)
            after = uninstall_registry_entry()
            report.step(
                "卸载后注册项已移除",
                removed,
                "已移除" if removed else f"60s 后仍存在: {after and after['displayName']}",
            )

            # %TEMP%\~nsu.tmp 是 NSIS 卸载器的自我拷贝，理论上它退出时会清掉自己。
            # 实测会残留，给一点时间再判（这是 NSIS 的固有行为，只影响临时目录整洁度，
            # 不阻断结论）。
            nsu = Path(os.environ.get("TEMP", "")) / "~nsu.tmp"
            wait_for(lambda: not nsu.exists(), timeout=20, interval=1.0)
            report.step(
                "卸载器临时目录已清理",
                not nsu.exists(),
                "无残留" if not nsu.exists() else f"残留 {nsu}（NSIS 自我拷贝）",
                critical=False,
            )

        # ---------- 7. 清理用户数据 ----------
        # 说明：卸载器**刻意**不删用户数据（生词本、课程配置），这是正确行为。
        # 这里按用户要求手动清理，以验证「从零再装一遍」的第一印象。
        #
        # 但**清理前必须先把日志留档**：否则一旦某个检查失败，唯一的现场证据
        # 就跟着一起被删了（第一次跑时就是这样，ping 全失败却查不到原因）。
        kept_log = Path(args.report).parent / "electron.log"
        if log_file.is_file():
            try:
                shutil.copy2(log_file, kept_log)
                report.step("留存启动日志", True, str(kept_log), critical=False)
            except OSError as exc:
                report.step("留存启动日志", False, str(exc), critical=False)

        data_before = dir_size(home)
        db_files = list(home.glob("data/*.db")) if home.exists() else []
        model_dirs = list(home.glob("whisper_models/models--*")) if home.exists() else []
        if home.exists():
            shutil.rmtree(home, ignore_errors=True)
        report.step(
            "清理用户数据",
            not home.exists(),
            f"移除 {mb(data_before)}（含 {len(db_files)} 个数据库、{len(model_dirs)} 份模型）",
        )
        report.step(
            "卸载不删用户数据（设计如此）",
            True,
            "生词本/课程配置保留在数据目录，重装后仍在；本次按验证需要手动清理",
            critical=False,
        )

    finally:
        # ---------- 8. 还原备份 ----------
        if backup is not None and backup.exists():
            if home.exists():
                shutil.rmtree(home, ignore_errors=True)
            shutil.move(str(backup), str(home))
            report.step("还原用户数据备份", True, f"{backup} → {home}", critical=False)

        report.dump(Path(args.report))
        print("\n" + "=" * 76)
        print(f"结论: {'全部通过 ✓' if report.passed else '存在阻断项 ✗'}")
        print("=" * 76)

    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
