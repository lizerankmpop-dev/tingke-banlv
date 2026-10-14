#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""变异探针：验证 `frontend/tests/compat-relaunch.test.cjs` 的断言真有分辨力

为什么需要这个
--------------
D6（首启降级重启 × 单实例锁竞态）是一个**静默故障**：不报错、不崩溃、
日志里一个字都没有，用户只看到「双击图标闪一下就没了」。

它还有一个更麻烦的性质：**根因是两行代码的先后顺序反了**。
「先 spawn 再让锁」和「先让锁再 spawn」在代码评审里长得几乎一样，
而前者只在「渲染进程崩过、必须降级重启」的机器上才暴露 ——
本机以外的机器可能几年都碰不到一次，测试跑绿毫无难度。

所以新加的断言必须证明「改坏它就会红」，否则它就是一层假信心。
本脚本对两个源文件做局部变异（**原地改，跑完立即还原**，每步用 md5 核对确实复原）：

  * `frontend/electron/compat-relaunch.cjs` —— 顺序编排本体
  * `frontend/electron/main.cjs` —— 接线与「不许用错修法」的守卫

逐条确认：该变异必须让**点名的那条**用例变红；其余变红的用例只作提示打印，不判失败。

用法
----
    <venv>/python.exe packaging/verify/probe_compat_relaunch_mutations.py
退出码 0 = 全部变异都被抓住；1 = 有变异没被抓住（断言缺分辨力）。

这是一次性验证证据，不参与常规测试；常规回归跑 `frontend/tests/compat-relaunch.test.cjs`。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FRONTEND = REPO / "frontend"
TEST_FILE = "tests/compat-relaunch.test.cjs"

NODE_CANDIDATES = [
    Path(r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2-6\node.exe"),
    Path(r"C:\Program Files\nodejs\node.exe"),
]

FILES = {
    "relaunch": FRONTEND / "electron" / "compat-relaunch.cjs",
    "main": FRONTEND / "electron" / "main.cjs",
}

# ---------------------------------------------------------------- 变异锚点

# ① 不再让锁（等价于 D6 里「锁始终被父进程持有」）
RELEASE_CALL_OLD = "    releasedLock = !!(releaseLock && releaseLock());\n"
RELEASE_CALL_NEW = "    releasedLock = false; // 变异：不再让锁\n"

# ② 真正的顺序反转：spawn 提到让锁之前
ORDER_OLD = """  // ① 先让锁 —— 顺序是不变量，反了就是 D6 复现（见文件头）。
  let releasedLock = false;
  try {
    releasedLock = !!(releaseLock && releaseLock());
  } catch (err) {
    // 释放失败不能中断重启：子进程抢不到锁会退回「父进程还活着」的旧表现，
    // 但不至于比不重启更差；真正的兜底是调用方检查返回值后弹错误框。
    logError(`[single-instance] 释放单实例锁异常: ${err.message}`);
    releasedLock = false;
  }
"""
ORDER_NEW = """  // ⚠️ 变异：spawn 提到让锁之前 —— 这正是 D6 的原始顺序
  let releasedLock = false;
"""

# ③ 给子进程塞 ALLOW_MULTI（错修法：子进程不抢锁 ⇒ 锁无人持有）
ENV_OLD = """      cwd: spec.cwd,
      env,
    });
"""
ENV_NEW = """      cwd: spec.cwd,
      env: { ...env, TINGKEBANLV_ALLOW_MULTI: '1' },
    });
"""

# ④ 去掉 pid 检查（spawn 失败也报「已拉起」）
PID_CHECK_OLD = """    // pid 取不到 = 底层 spawn 没成功，此时绝不能报「已拉起」
    if (!child.pid) {
      logError('[watchdog] 兼容模式实例未取到 pid，判定为拉起失败');
      return { ok: false, pid: null, releasedLock };
    }
"""
PID_CHECK_NEW = ""

# ⑤ 释放锁异常直接中断重启（回退成「比不重启更差」）
RELEASE_THROW_OLD = """    logError(`[single-instance] 释放单实例锁异常: ${err.message}`);
    releasedLock = false;
  }
"""
RELEASE_THROW_NEW = """    logError(`[single-instance] 释放单实例锁异常: ${err.message}`);
    throw err;
  }
"""

# ⑥ main.cjs 把 ALLOW_MULTI 写进子进程 env（错修法接线）
MAIN_ENV_OLD = """    // 就会成功抢锁并开出第二个实例。正确做法是「父进程先让锁」，而不是「子进程不抢」。
    env: process.env,
"""
MAIN_ENV_NEW = """    // 就会成功抢锁并开出第二个实例。正确做法是「父进程先让锁」，而不是「子进程不抢」。
    env: { ...process.env, TINGKEBANLV_ALLOW_MULTI: '1' },
"""

# ⑦ main.cjs 绕过模块自己 spawn（顺序不变量失去守卫）
MAIN_BYPASS_OLD = "  markCompatModeNeeded();\n"
MAIN_BYPASS_NEW = (
    "  markCompatModeNeeded();\n"
    "  spawn(process.execPath, [], { detached: true, stdio: 'ignore' });\n"
)

# (变异名, 文件键, 原文锚点, 替换成, 必须变红的用例)
MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "A. 不再让锁（父进程始终持锁 —— D6 的本质）",
        "relaunch",
        RELEASE_CALL_OLD,
        RELEASE_CALL_NEW,
        "让锁必须早于 spawn（D6 根因：顺序反了就是复现）",
    ),
    (
        "B. 顺序反转：spawn 提到让锁之前",
        "relaunch",
        ORDER_OLD,
        ORDER_NEW,
        "让锁必须早于 spawn（D6 根因：顺序反了就是复现）",
    ),
    (
        "C. 给子进程 env 塞 ALLOW_MULTI（错修法）",
        "relaunch",
        ENV_OLD,
        ENV_NEW,
        "传给子进程的 env 不得被注入 TINGKEBANLV_ALLOW_MULTI",
    ),
    (
        "D. 去掉 pid 检查（spawn 失败也报已拉起）",
        "relaunch",
        PID_CHECK_OLD,
        PID_CHECK_NEW,
        "拿不到 pid 判为拉起失败，且不得调用 unref",
    ),
    (
        "E. 释放锁异常即中断重启",
        "relaunch",
        RELEASE_THROW_OLD,
        RELEASE_THROW_NEW,
        "释放锁抛异常不中断重启（回退到旧表现，但不比不重启更差）",
    ),
    (
        "F. main.cjs 向子进程 env 注入 ALLOW_MULTI",
        "main",
        MAIN_ENV_OLD,
        MAIN_ENV_NEW,
        "main.cjs 不得向重启子进程注入 ALLOW_MULTI（错修法守卫）",
    ),
    (
        "G. main.cjs 绕过模块自己 spawn",
        "main",
        MAIN_BYPASS_OLD,
        MAIN_BYPASS_NEW,
        "main.cjs 不得自己 spawn 兼容实例（必须走 compat-relaunch.cjs）",
    ),
]


def _node() -> Path:
    for p in NODE_CANDIDATES:
        if p.exists():
            return p
    raise SystemExit("!! 找不到可用的 node.exe")


def _read_src(key: str) -> tuple[str, str, str]:
    """返回 (归一化文本, eol, md5)；归一化只为匹配锚点，写回时按原 eol"""
    raw = FILES[key].read_bytes()
    eol = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return text, eol, hashlib.md5(raw).hexdigest()


def _write_src(key: str, text: str, eol: str) -> None:
    FILES[key].write_bytes(text.replace("\n", eol).encode("utf-8"))


def _run_tests() -> tuple[set[str], str]:
    """跑 node 测试，返回 (变红的用例名集合, 原始输出尾部)"""
    proc = subprocess.run(
        [_node(), "--test", TEST_FILE],
        cwd=str(FRONTEND),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ},
        timeout=300,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    red: set[str] = set()
    for line in out.splitlines():
        line = line.strip()
        # TAP：失败子测试形如 `not ok 3 - <用例名>`
        if line.startswith("not ok "):
            _, _, rest = line.partition(" - ")
            if rest:
                red.add(rest.strip())
    tail = "\n".join([ln for ln in out.splitlines() if ln.strip()][-3:])
    return red, tail


def main() -> int:
    if not FILES["relaunch"].exists() or not FILES["main"].exists():
        print("!! 源文件缺失，探针无法运行")
        return 2

    originals = {k: _read_src(k) for k in FILES}

    # 先跑不变量：真身必须全绿。否则「变红」可能只是环境坏了
    red, tail = _run_tests()
    if red:
        print(f"!! 不变量失败：真身本应全绿，却有 {sorted(red)}\n   {tail}")
        return 2
    print(f"[不变] 真身全绿（{tail}）\n")

    failures: list[str] = []
    for name, key, old, new, target in MUTATIONS:
        text, eol, md5 = originals[key]
        try:
            if old not in text:
                raise SystemExit(f"[{name}] 变异锚点在源码里找不到，探针本身失效：{old!r}")
            _write_src(key, text.replace(old, new, 1), eol)
            red, tail = _run_tests()

            if target not in red:
                print(
                    f"[漏掉] {name}\n"
                    f"        用例「{target}」仍为绿 —— 断言缺分辨力（tail: {tail}）"
                )
                failures.append(name)
            else:
                others = sorted(red - {target})
                print(f"[抓住] {name}\n        变红：{target}")
                if others:
                    print(f"        另有用例也变红 → {others}（该变异确实牵动多条规则）")
        finally:
            _write_src(key, text, eol)
            now_md5 = hashlib.md5(FILES[key].read_bytes()).hexdigest()
            if now_md5 != md5:
                print(f"!! 还原失败！{key} md5 {md5} -> {now_md5}，请手动 checkout")
                return 2

    print()
    if failures:
        print(f"结论：{len(failures)}/{len(MUTATIONS)} 条变异没被抓住，需补断言")
        return 1
    print(f"结论：{len(MUTATIONS)}/{len(MUTATIONS)} 条变异全部被抓住，断言有分辨力")
    for k in FILES:
        print(f"（已还原 {k}，md5={originals[k][2]}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
