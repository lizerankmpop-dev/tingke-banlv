#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""变异探针：验证「调用点完整性 + 真实链路透传」的新断言真有分辨力（2026-10-08）

## 它拦的是什么

缺陷 (b) 的第一版修复把 `language` 透传只加在 `pipeline.py:61` 一处，其余 6 处全漏。
漏传的后果是**静默**的：代码不报错、测试全绿（因为测试直接调函数、必然生效），
而真实链路的提示词仍被塞进 41 条英文短语；A/B harness 更是因为漏传，
让两个代码锚点下发的提示词 **md5 逐字节相同**，整组对照变成空实验，
还被当成「修复已验证」写进了报告。

所以本轮新增的断言必须逐条证明「变异它就会红」，否则它们只是好看的装饰。

## 探针怎么工作

1. **先跑不变量**：真身（未变异）下所有被测用例必须全绿 —— 否则「变红」可能只是
   环境坏了，而不是断言抓到了变异。
2. 对每条变异：备份真实源文件 → 按文本替换注入缺陷 → 跑指定用例 → 期望**失败** →
   `finally` 还原。
3. 退出码 0 = 全部变异都被抓住；1 = 有变异没被抓住（断言缺分辨力）。

⚠️ 这里**必须改真实文件**，不能用内存里的副本：被测的 `test_every_call_site_passes_language`
读的是磁盘上的源码，`TestRealCallChain` 走的是真实 import。改内存副本只能证明
「我的正则能匹配」，证明不了「这条断言会红」。

⚠️ 还原放在 `finally`，并且每条变异前后都核对文件字节数 —— 探针自己崩掉时
**不允许**把仓库留在变异状态。

用法
----
    <venv>/python.exe packaging/verify/probe_language_plumbing_mutations.py

一次性验证证据，不参与常规回归；常规回归跑：
    backend/tests/test_terminology_call_sites.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
BASETEMP = REPO.parent / "安装空间" / "_pytest_tmp_probe"

# 被测用例节点（相对 backend/）
NODE_CALL_SITES = "tests/test_terminology_call_sites.py"
NODE_FRONTEND = f"{NODE_CALL_SITES}::test_frontend_preview_sends_language"
NODE_PREVIEW = f"{NODE_CALL_SITES}::TestRealCallChain::test_preview_endpoint_forwards_language"
NODE_COURSE = (
    f"{NODE_CALL_SITES}::TestRealCallChain::test_course_terminology_endpoint_uses_course_language"
)
NODE_HOTUPDATE = (
    f"{NODE_CALL_SITES}::TestRealCallChain::test_pipeline_hot_update_uses_course_language"
)

# ---- 变异定义：(名称, 目标文件, 原文, 替换, 期望变红的用例节点) ----
MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "M1 课程术语接口漏传 language（routes.py:636）",
        "backend/api/routes.py",
        '        discovered_terms=discovered,\n        language=course.get("language", "auto"),\n',
        "        discovered_terms=discovered,\n",
        NODE_COURSE,
    ),
    (
        "M2 术语预览接口漏传 language（routes.py:678）",
        "backend/api/routes.py",
        "        custom_terms=req.customTerms,\n        language=req.language,\n",
        "        custom_terms=req.customTerms,\n",
        NODE_PREVIEW,
    ),
    (
        "M3 术语热更新漏传 language（pipeline.py:143）",
        "backend/asr/pipeline.py",
        '                discovered_terms=self._discovered_terms,\n                language=self.course_config.get("language", "auto"),\n',
        "                discovered_terms=self._discovered_terms,\n",
        NODE_HOTUPDATE,
    ),
    (
        "M4 前端 preview 请求体不带 language（CourseConfig.tsx）",
        "frontend/src/pages/CourseConfig.tsx",
        "        body: JSON.stringify({ domain, language, customTerms }),",
        "        body: JSON.stringify({ domain, customTerms }),",
        NODE_FRONTEND,
    ),
    (
        "M5 A/B harness 漏传 language（eval_asr_quality.py:890）",
        "scripts/eval_asr_quality.py",
        "                domain=args.domain, custom_terms=custom, language=term_language\n",
        "                domain=args.domain, custom_terms=custom\n",
        NODE_CALL_SITES,
    ),
    (
        "M6 草稿流水线漏传 language（asr_draft_transcript.py:66）",
        "scripts/asr_draft_transcript.py",
        "            domain=args.domain, custom_terms=custom, language=args.language\n",
        "            domain=args.domain, custom_terms=custom\n",
        NODE_CALL_SITES,
    ),
    (
        "M7 `**kwargs` 转发绕开静态核对（合成注入 routes.py）",
        "backend/api/routes.py",
        "        custom_terms=req.customTerms,\n        language=req.language,\n",
        "        **({'custom_terms': req.customTerms}),\n",
        NODE_CALL_SITES,
    ),
]


def run_pytest(node: str) -> tuple[bool, str]:
    """跑一个用例节点，返回 (是否通过, 末几行输出)"""
    BASETEMP.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            node,
            "-q",
            "-p",
            "no:cacheprovider",
            f"--basetemp={BASETEMP}",
        ],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = "\n".join([ln for ln in out.splitlines() if ln.strip()][-6:])
    return proc.returncode == 0, tail


def mutate_text(text: str, old: str, new: str) -> str | None:
    """
    按文件**自身的换行风格**做替换；锚点未命中返回 None

    🔴 踩过的坑：本仓库的 .py 行尾**不统一** —— 实测 5 个文件是纯 CRLF
    （`backend/main.py`、`scripts/{asr_draft_transcript,eval_asr_quality,review_page,verify_install}.py`），
    其余 55 个是纯 LF。锚点里写死 `\\n` 时，CRLF 文件会「找不到锚点」，
    而探针的报错是「变异锚点未找到」—— 看起来像源码变了，其实是换行符问题。
    """
    eol = "\r\n" if "\r\n" in text else "\n"

    def norm(s: str) -> str:
        return s.replace("\r\n", "\n").replace("\r", "\n")

    t, o, n = norm(text), norm(old), norm(new)
    if o not in t:
        return None
    return t.replace(o, n, 1).replace("\n", eol)


def main() -> int:
    print("=" * 92)
    print("变异探针：调用点完整性 + 真实链路 language 透传")
    print("=" * 92)

    # ---- 不变量：真身必须全绿 ----
    print("\n[不变量] 真身（未变异）下被测用例应全部通过")
    ok, tail = run_pytest(NODE_CALL_SITES)
    print(("  ✓ 全绿" if ok else "  ✗ 真身都没过 —— 先修环境，探针结论无意义"))
    if not ok:
        print(tail)
        return 1

    failures: list[str] = []
    for name, rel, old, new, node in MUTATIONS:
        path = REPO / rel
        original = path.read_bytes()
        mutated = mutate_text(original.decode("utf-8"), old, new)
        if mutated is None:
            print(f"\n✗ {name}\n    变异锚点未找到（源码已变，请更新探针）：{old!r}")
            failures.append(name)
            continue

        try:
            path.write_text(mutated, encoding="utf-8", newline="")
            passed, tail = run_pytest(node)
        finally:
            path.write_bytes(original)
            if path.read_bytes() != original:
                print(f"!! 还原失败，请立即检查 {rel}")
                return 2

        caught = not passed
        mark = "✓ 抓住" if caught else "✗ 漏掉"
        print(f"\n{mark} {name}")
        print(f"    用例: {node.split('::', 1)[1] if '::' in node else node}")
        if not caught:
            print("    —— 变异后该用例仍然通过，说明这条断言没有分辨力：")
            print("    " + tail.replace("\n", "\n    "))
            failures.append(name)

    print("\n" + "=" * 92)
    if failures:
        print(f"结果：{len(MUTATIONS) - len(failures)}/{len(MUTATIONS)} 抓住 —— 有漏网：")
        for f in failures:
            print(f"  · {f}")
        return 1
    print(f"结果：{len(MUTATIONS)}/{len(MUTATIONS)} 全部抓住 ✓（不变量亦为绿）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
