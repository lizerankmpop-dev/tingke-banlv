#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""换行归一化工具（LF 纪律）—— **只处理显式白名单内的文本扩展名**

为什么要有这个工具（教训 79）
------------------------------
2026-10-26 我用 `glob('testset/english_lecture/*')` 做换行归一化，
结果把同目录下的 **20 MB 的 `.wav` 二进制**也当文本处理了 ——
它的波形数据里恰好含 7 组 `0D 0A`，被替换成 `0A`，
**音频被静默改坏 7 个字节**（`wave` 模块仍能读、帧数仍对，只有 sha256 能发现）。

**根因不是「归一化」这个操作，而是「用目录 glob 当隐式白名单」**。
⇒ 本工具**只接受显式扩展名白名单**，其余一律跳过并打印原因。
**「上一处没出事」不是「写法安全」的证据**。

用法
----
    <venv>/python.exe scripts/normalize_eol.py --root testset/english_lecture
    <venv>/python.exe scripts/normalize_eol.py --root testset --check-only
    <venv>/python.exe scripts/normalize_eol.py --root scripts --verify-binary

`--verify-binary` 会对目录下的**非白名单文件**（即二进制/媒体）打印 sha256，
用于在归一化前后比对「媒体文件有没有被误改」。
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

# ✅ 允许做换行归一化的扩展名（纯文本）
TEXT_EXTS = {
    ".txt", ".tsv", ".json", ".md", ".py", ".srt", ".vtt", ".html", ".css",
    ".js", ".jsx", ".ts", ".tsx", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".sh",
}
# 🔴 绝不能碰的（二进制/媒体）
BINARY_EXTS = {
    ".wav", ".m4s", ".mp4", ".mp3", ".m4a", ".aac", ".flac", ".ogg",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svgz", ".pdf",
    ".exe", ".zip", ".7z", ".gz", ".whl", ".onnx", ".bin", ".ttf", ".woff",
}

CRLF = b"\r\n"

# 🔴 **C-6 的 CRLF 例外清单**（工作区必须保持**纯 CRLF**，lone_lf == 0）
#
# 这三个文件**不在**归一化范围内 —— 归一化它们会**破坏** C-6 纪律。
# 我在 2026-10-26 连续踩了两次同一类坑（第一次把 wav 当文本改坏，第二次把
# 这三个 CRLF 例外文件归一化成 LF），所以规则直接写进工具，不再靠人记。
#
# 路径写成**仓库相对路径**，按仓库根解析。
CRLF_EXCEPTIONS = {
    "scripts/verify_install.py",
    "scripts/eval_asr_quality.py",
    "testset/chinese_lecture/metadata.json",
}


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def is_crlf_exception(p: Path) -> bool:
    try:
        rel = p.resolve().relative_to(repo_root())
    except ValueError:
        return False
    return rel.as_posix() in CRLF_EXCEPTIONS


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def classify(p: Path) -> str:
    ext = p.suffix.lower()
    if ext in TEXT_EXTS:
        return "text"
    if ext in BINARY_EXTS:
        return "binary"
    return "unknown"


def main() -> int:
    ap = argparse.ArgumentParser(description="换行归一化（只处理白名单内的文本扩展名）")
    ap.add_argument("--root", required=True, help="目录（递归）")
    ap.add_argument("--check-only", action="store_true", help="只报告，不改")
    ap.add_argument("--verify-binary", action="store_true",
                    help="额外打印二进制文件的 sha256（归一化前后各跑一次对比）")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"❌ {root} 不是目录")
        return 1

    changed, skipped_bin, skipped_unk, already, bin_hashes = [], [], [], [], []
    skipped_exc = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if is_crlf_exception(p):
            skipped_exc.append(p)
            continue
        kind = classify(p)
        if kind == "binary":
            skipped_bin.append(p)
            if args.verify_binary:
                bin_hashes.append((p, sha256_of(p)))
            continue
        if kind == "unknown":
            skipped_unk.append(p)
            continue
        b = p.read_bytes()
        if b"\r\n" in b:
            if not args.check_only:
                p.write_bytes(b.replace(CRLF, b"\n"))
            changed.append(p)
        else:
            already.append(p)

    print(f"目录 {root}")
    print(f"  已归一化（CRLF→LF）: {len(changed)}"
          + ("（--check-only，未落盘）" if args.check_only else ""))
    for p in changed[:20]:
        print(f"      {p}")
    print(f"  本来就是 LF        : {len(already)}")
    print(f"  🔴 跳过（二进制）   : {len(skipped_bin)}  ← 绝不碰")
    print(f"  🔴 跳过（C-6 CRLF 例外）: {len(skipped_exc)}  ← 必须保持纯 CRLF")
    for p in skipped_exc:
        print(f"      {p}")
    print(f"  ⏭  跳过（扩展名不在白名单）: {len(skipped_unk)}")
    for p in skipped_unk[:10]:
        print(f"      {p}")
    if args.verify_binary:
        print("  二进制 sha256：")
        for p, h in bin_hashes:
            print(f"      {h}  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
