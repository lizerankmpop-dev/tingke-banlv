#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取 MIT OCW 各讲的官方 VTT 字幕（V7 · en_lecture 多讲次扩展）

为什么要有这个脚本
------------------
英文格的参考稿来源是**官方 VTT**（带精确时间码）。但**每讲的 VTT 文件名不同**
（`{32位hex}.{youtube_id}.vtt`，那个 hex 是 OCW 自己的内容哈希，**无法从别处推导**），
所以只能**逐个访问资源页**从 `<track src=...>` 里取。

⚠️ **B 站分 P 与 OCW 讲次不是一一对应**（实测 2026-10-26）：
- OCW 把神经网络**拆成 12A / 12B**（12A=Neural Nets，12B=Deep Neural Nets）
- OCW 多了 **Lecture 20: The AI business**（B 站没有对应）
⇒ **B 站 P12 ↔ OCW Lecture 12A；B 站 P20 ↔ OCW Lecture 21**（偏移 1）。
**这个偏移必须用时间轴对齐验证，不能假设** —— 见 `build_en_reference.py` 的锚点拟合。

用法
----
    <venv>/python.exe scripts/fetch_ocw_vtt.py --slug lecture-4-search-depth-first-hill-climbing-beam
    <venv>/python.exe scripts/fetch_ocw_vtt.py --slug lecture-21-probabilistic-inference-i --out-dir <dir>
"""
from __future__ import annotations

import argparse
import re
import ssl
import sys
import urllib.request
from pathlib import Path

COURSE = "6-034-artificial-intelligence-fall-2010"
BASE = f"https://ocw.mit.edu/courses/{COURSE}/resources/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _ctx():
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


def _get(url: str, timeout: int = 40) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as resp:
        return resp.read()


def find_vtt(slug: str) -> tuple[str, str]:
    """返回 (vtt_url, 页面标题)"""
    html = _get(BASE + slug).decode("utf-8", "replace")
    m = re.search(r'<track[^>]+src="([^"]+\.vtt)"', html)
    if not m:
        raise SystemExit(f"❌ {slug} 页面里没有 <track src=...vtt>")
    vtt = m.group(1)
    if vtt.startswith("/"):
        vtt = f"https://ocw.mit.edu{vtt}"
    t = re.search(r"<title>(.*?)</title>", html, re.S)
    title = (t.group(1).strip() if t else slug)[:120]
    return vtt, title


def main() -> int:
    ap = argparse.ArgumentParser(description="抓 MIT OCW 官方 VTT")
    ap.add_argument("--slug", required=True, help="资源页 slug，如 lecture-4-search-...")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    vtt_url, title = find_vtt(args.slug)
    data = _get(vtt_url)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    dest = out / f"ocw_{args.slug}.vtt"
    dest.write_bytes(data)

    cues = len(re.findall(r"-->", data.decode("utf-8", "replace")))
    print(f"✅ {dest}")
    print(f"   标题: {title}")
    print(f"   来源: {vtt_url}")
    print(f"   大小: {len(data)/1024:.1f} KB｜cue 数: {cues}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
