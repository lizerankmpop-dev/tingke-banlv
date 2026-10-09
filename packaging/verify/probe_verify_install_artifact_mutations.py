#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""变异探针：验证 `test_verify_install_artifact.py` 的断言真有分辨力

为什么需要这个
--------------
「有测试覆盖」不等于「断言验得到」。新加的断言如果被别的路径顺手满足，
就会**永远绿**、永不报警 —— 比没有断言更糟，因为它给出的是假信心。

本脚本对 `scripts/verify_install.py` 做局部变异（**原地改，跑完立即还原**，
每一步都用 md5 核对文件确实复原），逐条确认：

    * 该变异必须让**点名的那条**用例变红（`去掉即红`）；
    * 其余变红的用例只作提示打印，不判失败（一个变异可以合理地牵动多条规则）。

为什么是「原地改」而不是「写一份 `_mutant_*.py` 副本」：
被测模块由测试模块**按固定路径**加载（`SRC` 指向 `scripts/verify_install.py`），
写副本的话测试仍然会去读真身，变异等于没生效 —— 那才叫空转。

⚠️ 该文件是**纯 CRLF**（全仓 14 个纯 CRLF 文件之一）。`_read_src` / `_write_src`
都按文件自身换行风格还原，main() 结尾再核一次 md5。

用法
----
    <venv>/python.exe packaging/verify/probe_verify_install_artifact_mutations.py
退出码 0 = 全部变异都被抓住；1 = 有变异没被抓住（断言缺分辨力）。

这是一次性验证证据，不参与常规测试；常规回归跑 `backend/tests/test_verify_install_artifact.py`。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
SRC = SCRIPTS / "verify_install.py"
TEST_FILE = "tests/test_verify_install_artifact.py"
BASETEMP = REPO.parent / "安装空间" / "_pytest_tmp_vi_artifact_probe"

PY = REPO.parent / "安装空间" / "venv" / "Scripts" / "python.exe"

# ---- 变异锚点 ----

DUMP_ARTIFACT_OLD = '            "artifact": self.artifact,\n'

SHA256_AS_MD5_OLD = "    h256, hmd5 = hashlib.sha256(), hashlib.md5()\n"
SHA256_AS_MD5_NEW = "    h256, hmd5 = hashlib.md5(), hashlib.md5()\n"

CHUNK_LOOP_OLD = """    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h256.update(block)
            hmd5.update(block)
"""
CHUNK_LOOP_ONE_BLOCK = """    with path.open("rb") as fh:
        block = fh.read(chunk)
        h256.update(block)
        hmd5.update(block)
"""

DUMP_REHASH_OLD = """        payload = {
            "app": APP_NAME,
"""
DUMP_REHASH_NEW = """        if self.artifact:
            sha256_file(Path(self.artifact["path"]))
        payload = {
            "app": APP_NAME,
"""

LOCATING_DATA_OLD = "        data=artifact,\n"

# (变异名, 原文锚点, 替换成, 必须变红的用例)
MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "A. 删掉报告顶层的 artifact 字段（指纹又只能靠文件名对账）",
        DUMP_ARTIFACT_OLD,
        "",
        "test_report_dump_contains_artifact",
    ),
    (
        "B. sha256 退化成 md5（两个哈希口径混用）",
        SHA256_AS_MD5_OLD,
        SHA256_AS_MD5_NEW,
        "test_sha256_file_known_vector",
    ),
    (
        "C. 分块循环只读第一块（大文件尾部全丢）",
        CHUNK_LOOP_OLD,
        CHUNK_LOOP_ONE_BLOCK,
        "test_chunked_read_matches_single_shot",
    ),
    (
        "D. 把哈希塞回 dump()（一轮 73 步 → 73 遍 302MB ≈ 22GB）",
        DUMP_REHASH_OLD,
        DUMP_REHASH_NEW,
        "test_dump_does_not_hash",
    ),
    (
        "E. 去掉「定位安装包」的 data=（报告有指纹，步骤里没有）",
        LOCATING_DATA_OLD,
        "",
        "test_locating_step_carries_artifact_data",
    ),
]


def _read_src() -> tuple[str, str, str]:
    """返回 (归一化文本, eol, md5)；归一化只为匹配锚点，写回时按原 eol"""
    raw = SRC.read_bytes()
    eol = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return text, eol, hashlib.md5(raw).hexdigest()


def _write_src(text: str, eol: str) -> None:
    SRC.write_bytes(text.replace("\n", eol).encode("utf-8"))


def _run_tests() -> tuple[set[str], str]:
    """跑测试文件，返回 (变红的用例名集合, 原始输出尾部)"""
    proc = subprocess.run(
        [
            str(PY), "-m", "pytest", TEST_FILE,
            "-q", "--tb=no", "-p", "no:cacheprovider",
            f"--basetemp={BASETEMP}",
        ],
        cwd=str(REPO / "backend"),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ},
        timeout=600,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    red = set()
    for line in out.splitlines():
        line = line.strip()
        if line.startswith(("FAILED ", "ERROR ")):
            red.add(line.split("::")[-1].split(" ")[0].strip())
    tail = "\n".join([ln for ln in out.splitlines() if ln.strip()][-2:])
    return red, tail


def main() -> int:
    original_text, eol, original_md5 = _read_src()
    if not PY.exists():
        print(f"!! venv python 不存在: {PY}")
        return 2

    # 先跑不变量：真身必须全绿。否则「变红」可能只是环境坏了（探针的第四层自骗）
    red, tail = _run_tests()
    if red:
        print(f"!! 不变量失败：真身本应全绿，却有 {sorted(red)}\n   {tail}")
        return 2
    print(f"[不变] 真身全绿（{tail}）\n")

    failures: list[str] = []
    for name, old, new, target in MUTATIONS:
        try:
            if old not in original_text:
                raise SystemExit(f"[{name}] 变异锚点在源码里找不到，探针本身失效：{old!r}")
            _write_src(original_text.replace(old, new, 1), eol)
            red, tail = _run_tests()

            if target not in red:
                print(f"[漏掉] {name}\n        用例 {target} 仍为绿 —— 断言缺分辨力（tail: {tail}）")
                failures.append(name)
            else:
                others = sorted(red - {target})
                print(f"[抓住] {name}\n        变红：{target}")
                if others:
                    print(f"        另有用例也变红 → {others}（该变异确实牵动多条规则）")
        finally:
            _write_src(original_text, eol)
            now_md5 = hashlib.md5(SRC.read_bytes()).hexdigest()
            if now_md5 != original_md5:
                print(f"!! 还原失败！md5 {original_md5} -> {now_md5}，请手动 checkout")
                return 2

    print()
    if failures:
        print(f"结论：{len(failures)}/{len(MUTATIONS)} 条变异没被抓住，需补断言")
        return 1
    print(f"结论：{len(MUTATIONS)}/{len(MUTATIONS)} 条变异全部被抓住，断言有分辨力")
    print(f"（文件已还原，md5={original_md5}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
