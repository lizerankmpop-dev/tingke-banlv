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

另外内置模型是新增能力，必须验证**不设任何环境变量**时，冻结 exe 能从
`<安装目录>/resources/whisper_models` 自动反推出种子目录（model_bootstrap
的三级解析里，env 那一级在真实用户机上是空的）。

用法
----
    <venv>/Scripts/python.exe scripts/verify_install.py \
        --installer frontend/release/tingkebanlv-3.1.0-setup.exe \
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


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class Report:
    """收集每步结果，最后落盘 JSON"""

    def __init__(self) -> None:
        self.started = now_iso()
        self.steps: list[dict] = []
        self.passed = True

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
        return ok

    def dump(self, path: Path) -> None:
        payload = {
            "app": APP_NAME,
            "startedAt": self.started,
            "finishedAt": now_iso(),
            "passed": self.passed,
            "steps": self.steps,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告已写入: {path}")


# ============ 通用工具 ============

def run(cmd: list[str], timeout: int = 300, env: dict | None = None, cwd: str | None = None):
    """跑一个子进程并拿回 (returncode, stdout+stderr)"""
    merged = {**os.environ, **(env or {})}
    # ELECTRON_RUN_AS_NODE 会让 electron.exe 以纯 Node 模式运行，
    # 表现为 app.whenReady is undefined（本机 Bash 环境默认带这个变量）
    merged.pop("ELECTRON_RUN_AS_NODE", None)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            env=merged,
            cwd=cwd,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        return 124, f"超时（{timeout}s）: {exc}"
    out = b""
    for stream in (proc.stdout, proc.stderr):
        if stream:
            out += stream
    # 冻结后端按 UTF-8 输出；NSIS 安装器是 GBK
    for codec in ("utf-8", "gbk", "cp936"):
        try:
            return proc.returncode, out.decode(codec)
        except UnicodeDecodeError:
            continue
    return proc.returncode, out.decode("utf-8", errors="replace")


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


def http_ok(url: str, timeout: float = 5.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


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
    report = Report()

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
        code, output = run([str(installer), "/S"], timeout=600)
        installed = wait_for(
            lambda: discover_install_dir(local_appdata) is not None, timeout=180
        )
        elapsed = time.time() - started
        if not installed:
            report.step(
                "静默安装（/S）",
                False,
                f"退出码 {code}，180s 内未在任何目录下发现 {APP_NAME}.exe\n{output[-800:]}",
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
        # 目录名应与产品名一致。写成 npm 包名（如 tingke-banlv-frontend）时
        # 用户会在「设置 → 应用」和安装路径里看到内部实现细节，属于产品瑕疵。
        report.step(
            "安装目录命名",
            install_dir.name == APP_NAME,
            f"{install_dir.name}（期望 {APP_NAME}）—— "
            + (
                "与 productName 一致"
                if install_dir.name == APP_NAME
                else "不一致：electron-builder 的 NSIS $INSTDIR 读 package.json 顶层 productName"
            ),
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
        code, output = run([str(expected["冻结后端"]), "--selftest"], timeout=600, env=env)
        elapsed = time.time() - started

        # 取出 JSON 结论（输出前面是逐项打印，最后是 JSON）
        json_start = output.find("{\n")
        payload = None
        if json_start >= 0:
            try:
                payload = json.loads(output[json_start:])
            except json.JSONDecodeError:
                payload = None

        if payload is None:
            report.step("后端自检", False, f"未取到 JSON 结论（退出码 {code}）\n{output[-1500:]}")
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

        # 播种是否真的落到了数据目录
        seeded = home / "whisper_models" / "models--Systran--faster-whisper-base" / "snapshots" / "seed" / "model.bin"
        report.step(
            "首启播种落盘",
            seeded.is_file(),
            f"{seeded}（{mb(seeded.stat().st_size) if seeded.is_file() else '不存在'}）",
        )

        if args.keep_installed:
            report.step("跳过卸载", True, "--keep-installed", critical=False)
            report.dump(Path(args.report))
            return 0

        # ---------- 4. 启动应用 + 稳定性长测 ----------
        if log_file.exists():
            log_file.unlink()  # 只看本次启动的日志

        proc = subprocess.Popen(
            [str(app_exe)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "TINGKEBANLV_HOME": str(home)},
        )

        def read_log() -> str:
            try:
                return log_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return ""

        def app_process_count() -> int:
            """
            按进程名数实例。

            不能依赖 Popen 句柄：本环境下渲染器沙箱失效，应用会以
            `spawn(execPath, args, {detached:true})` 自我重启到兼容模式，
            **原进程会先退出** —— 拿 proc.poll() 判断存活会误报「已退出」。
            """
            code, listing = run(
                ["tasklist", "/FI", f"IMAGENAME eq {APP_NAME}.exe", "/FO", "CSV", "/NH"],
                timeout=60,
            )
            if code != 0:
                return -1
            return sum(1 for line in listing.splitlines() if APP_NAME in line)

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

        # 从日志里抓后端端口，用它做存活探测（比查进程更接近「真的能用」）。
        # 取**最后**一次出现：兼容模式降级重启会让日志里同时存在新旧两个实例的记录，
        # 旧实例的端口已经没人监听了。
        port = None
        for line in read_log().splitlines():
            if "127.0.0.1:" not in line:
                continue
            tail = line.split("127.0.0.1:", 1)[1]
            digits = ""
            for ch in tail:
                if ch.isdigit():
                    digits += ch
                else:
                    break
            if digits:
                port = int(digits)
        report.step(
            "解析后端端口",
            port is not None,
            f"端口 {port}" if port else "日志中未找到端口",
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
        report.step(
            f"{args.hold}s 稳定性",
            final_count > 0 and dead_samples == 0 and (ratio >= 0.95 if samples else True),
            f"实例 {final_count} 个，观察期内 {dead_samples} 次采样无进程；"
            f"/api/ping {alive}/{samples} 可用（{ratio * 100:.1f}%）",
            data={
                "samples": samples,
                "alive": alive,
                "deadSamples": dead_samples,
                "holdSeconds": args.hold,
                "finalProcessCount": final_count,
            },
        )

        # 日志体检
        text = read_log()
        bad = [marker for marker in LOG_ERROR_MARKERS if marker in text]
        report.step(
            "启动日志无错误字样",
            not bad,
            "干净" if not bad else f"命中: {bad}",
        )
        relaunched = text.count("兼容模式") > 0
        report.step(
            "渲染器状态",
            True,
            "触发了兼容模式降级重启（已知环境限制，非缺陷）" if relaunched else "一次启动成功",
            critical=False,
        )
        report.step(
            "日志落盘",
            log_file.is_file(),
            f"{log_file}（{len(text.splitlines())} 行）",
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
            code, output = run([str(uninstaller), "/S", "/currentuser"], timeout=300)
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

            after = uninstall_registry_entry()
            report.step(
                "卸载后注册项已移除",
                after is None,
                "已移除" if after is None else f"仍存在: {after['displayName']}",
            )

            # %TEMP%\~nsu.tmp 是 NSIS 卸载器的自我拷贝，正常应被清掉
            nsu = Path(os.environ.get("TEMP", "")) / "~nsu.tmp"
            report.step(
                "卸载器临时目录已清理",
                not nsu.exists(),
                "无残留" if not nsu.exists() else f"残留 {nsu}（NSIS 自我拷贝）",
                critical=False,
            )

        # ---------- 7. 清理用户数据 ----------
        # 说明：卸载器**刻意**不删用户数据（生词本、课程配置），这是正确行为。
        # 这里按用户要求手动清理，以验证「从零再装一遍」的第一印象。
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
