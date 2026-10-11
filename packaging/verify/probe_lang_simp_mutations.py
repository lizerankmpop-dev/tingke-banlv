#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""变异探针：证明 V5-1~V5-4 的新断言**真有分辨力**（2026-10-11）

## 它拦的是什么

本次修的是一条**静默**缺陷：`auto` 走默认路径却从不注入简体引导语，字幕满屏繁体，
但代码不报错、日志不告警、测试也会全绿（除非测试正好断言了引导语本身）。

静默缺陷的测试有一个共同的失败模式 —— **写了看着像在测，其实恒绿**：

* 断言了 `transcribe` 的返回值，却没人检查它收到的 `initial_prompt` 实参；
* 断言了「探测判为中文」，却没有断言这个判定**真的驱动了引导语注入**
  （于是「按 zh 解码但没注引导语」这种半修状态照样能过）；
* 断言了「锁住了」，却没断言「不该锁的时候不锁」（于是英文片头锁死整场课也能过）；
* 断言了路由返回 200，却没人为「下发到正在跑的管道」写一行。

所以每条新断言都要逐条证明「变异它就会红」。

## 探针怎么工作

1. **先跑不变量**：真身（未变异）下整个测试文件必须全绿 —— 否则「变红」可能只是
   环境坏了，而不是断言抓到了变异。
2. 对每条变异：备份真实源文件 → 按文本替换注入缺陷 → 跑指定用例 → 期望**失败** →
   `finally` 还原。
3. 退出码 0 = 全部变异都被抓住；1 = 有变异没被抓住（断言缺分辨力）；2 = 还原失败。

⚠️ 这里**必须改真实文件**，不能用内存副本：被测用例里既有读磁盘源码的静态守卫，
也有走真实 import 的 TestClient 链路，改内存副本只能证明「我的正则能匹配」。

⚠️ 还原放在 `finally`，且每条变异按**字节**还原并复验 —— 探针自己崩掉时
**不允许**把仓库留在变异状态（否则下一次提交会把注入的缺陷带进去）。

用法
----
    <venv>/python.exe packaging/verify/probe_lang_simp_mutations.py

一次性验证证据，不参与常规回归；常规回归跑：
    backend/tests/test_lang_simp.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
BASETEMP = REPO.parent / "安装空间" / "_pytest_tmp_probe_lang_simp"

TEST = "tests/test_lang_simp.py"
ENGINE = "backend/asr/whisper_engine.py"
MANAGER = "backend/database/manager.py"
PIPELINE = "backend/asr/pipeline.py"
ROUTES = "backend/api/routes.py"

# ---- 变异定义：(名称, 目标文件, 原文, 替换, 期望变红的用例节点) ----
MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "M1 `_hint_possible()` 的 auto 分支失效 ⇒ auto 不再被认为「可能注入引导语」",
        ENGINE,
        '        return (\n'
        '            lang == "auto"\n'
        '            and LANG_DETECT_ENABLED\n'
        '            and self.simp_mode == SIMP_MODE_AUTO_SIMPLIFIED\n'
        '        )\n',
        '        return False\n',
        f"{TEST}::test_hint_possible_true_for_auto",
    ),
    (
        "M2 探测的关闭判据失效（V5-4 开关 / env 关不掉了）",
        ENGINE,
        '        if not LANG_DETECT_ENABLED or self.simp_mode == SIMP_MODE_RAW:\n'
        '            return None, 0.0, "disabled"\n',
        '        if False:\n            return None, 0.0, "disabled"\n',
        f"{TEST}::test_detect_can_be_disabled_by_env_switch",
    ),
    (
        "M3 置信阈值写死 0.6（V5-2 的「可调」名存实亡）",
        ENGINE,
        '        if conf < LANG_MIN_CONFIDENCE:\n',
        '        if conf < 0.6:\n',
        f"{TEST}::test_threshold_is_configurable",
    ),
    (
        "M4 会话级粘滞失效 ⇒ 每块都重新探测（字幕在语种间来回抖）",
        ENGINE,
        '        if self._lang_decided:\n'
        '            return self._lang_decision, self._lang_confidence, "sticky"\n',
        '        if False:\n'
        '            return self._lang_decision, self._lang_confidence, "sticky"\n',
        f"{TEST}::test_chinese_decision_is_sticky_and_probes_once",
    ),
    (
        "M5 非目标语种即刻锁定 ⇒ 英文片头把整场课锁成不注引导语（缺陷原样复发）",
        ENGINE,
        '        if self._lang_non_target_streak >= LANG_NON_TARGET_LOCK_AFTER:\n',
        '        if True:\n',
        f"{TEST}::test_english_intro_then_chinese_still_gets_hint",
    ),
    (
        "M6 探测结果没驱动引导语注入 ⇒ 「按 zh 解码但仍满屏繁体」半修状态",
        ENGINE,
        '            simp = (decode_lang or "").lower() in SIMP_HINT_LANGS\n',
        '            simp = False\n',
        f"{TEST}::test_transcribe_wires_detection_to_both",
    ),
    (
        "M7 `set_simp_mode()` 不清已锁定的决策 ⇒ 关掉开关在本会话内不生效",
        ENGINE,
        '        self.simp_mode = normalize_simp_mode(mode)\n'
        '        self.reset_language_decision()\n',
        '        self.simp_mode = normalize_simp_mode(mode)\n',
        f"{TEST}::test_set_simp_mode_clears_locked_decision",
    ),
    (
        "M8 回显清洗器判据退回旧写法 ⇒ auto 注入的引导语漏洗，被当字幕念出来",
        ENGINE,
        '        self._echo = EchoCleaner(get_simp_hint() if self._hint_possible() else "")\n',
        '        self._echo = EchoCleaner(\n'
        '            get_simp_hint() if (self.language or "").lower() in SIMP_HINT_LANGS else ""\n'
        '        )\n',
        f"{TEST}::test_echo_cleaner_construction_is_derived_not_duplicated",
    ),
    (
        "M9 DB 层默认值漂移（与 ASR 层分家）⇒ 界面显示「强制简体」实际没注入",
        MANAGER,
        'SIMP_MODE_DEFAULT = "auto-simplified"\n',
        'SIMP_MODE_DEFAULT = "raw"\n',
        f"{TEST}::test_simp_mode_default_agrees_with_db_layer",
    ),
    (
        "M10 `normalize_simp_mode()` 不再收敛非法值 ⇒ 脏值直达引擎",
        ENGINE,
        '    return v if v in SIMP_MODES else SIMP_MODE_AUTO_SIMPLIFIED\n',
        '    return v\n',
        f"{TEST}::test_normalize_simp_mode_falls_back_on_garbage",
    ),
    (
        "M11 老库缺 ALTER ⇒ 老用户升级后 `no such column: simp_mode`，应用打不开",
        MANAGER,
        '        cursor = await self.db.execute("PRAGMA table_info(courses)")\n'
        '        columns = [row[1] for row in await cursor.fetchall()]\n'
        '        if "simp_mode" not in columns:\n'
        '            await self.db.execute(\n'
        '                "ALTER TABLE courses ADD COLUMN simp_mode TEXT DEFAULT \'auto-simplified\'"\n'
        '            )\n'
        '            await self.db.commit()\n'
        '            logger.info("数据库迁移：courses 表已添加 simp_mode 列")\n',
        '        pass\n',
        f"{TEST}::test_legacy_db_gets_simp_mode_column",
    ),
    (
        "M12 路由拿到 simpMode 却忘了下发 ⇒ 界面已关，字幕仍繁体（要停课重开）",
        ROUTES,
        '    if req.simpMode is not None and course_id in active_pipelines:\n'
        '        active_pipelines[course_id].update_simp_mode(req.simpMode)\n',
        '',
        f"{TEST}::test_update_course_hot_updates_running_pipeline",
    ),
    (
        "M13 `pipeline.update_simp_mode()` 漏改 course_config ⇒ 管道重建后静默回退",
        PIPELINE,
        '        self.course_config["simpMode"] = mode\n',
        '',
        f"{TEST}::test_pipeline_update_simp_mode_updates_course_config",
    ),
    (
        "M14 建课入口不收敛非法值 ⇒ 脏值进库，开关状态与界面不一致",
        ROUTES,
        '    data["simpMode"] = normalize_simp_mode(data.get("simpMode"))\n',
        '    data["simpMode"] = data.get("simpMode")\n',
        f"{TEST}::test_create_course_normalizes_garbage_simp_mode",
    ),
    # ---- V5-8：auto 判出语种后重建术语层 ----
    (
        "M15 引擎判出目标语种却不通知 ⇒ 中文课一直带着英文短语层（D5 未修）",
        ENGINE,
        '            self._notify_language_decided(lang)\n',
        '            pass\n',
        f"{TEST}::test_auto_detected_chinese_notifies_once",
    ),
    (
        "M16 「只通知一次」的守卫失效 ⇒ 提示词每块都重建，连贯性条件一直抖",
        ENGINE,
        '        if self._lang_decided_notified:\n            return\n',
        '        if False:\n            return\n',
        # ⚠️ 这里**不能**指向 `test_auto_detected_chinese_notifies_once`：那条用例里
        # 第二次通知是被**粘滞**挡住的（`_lang_decided=True` 后走不到通知那行），
        # 所以删掉守卫它照样全绿 —— 首次跑这个探针就是在这里漏网的。
        # 一次性守卫要由直接调 `_notify_language_decided()` 的用例来守。
        f"{TEST}::test_notify_guard_stands_on_its_own",
    ),
    (
        "M17 `reset_language_decision()` 不清通知标记 ⇒ 换课后术语层停在上一个语种",
        ENGINE,
        '        # V5-8：重新探测后要能重新通知，否则「换到中文课」时术语层还是旧语种建的\n'
        '        self._lang_decided_notified = False\n',
        '        pass\n',
        f"{TEST}::test_reset_reallows_notification",
    ),
    (
        "M18 回调异常没被吞 ⇒ 术语层重建失败会把整条识别链路带崩",
        ENGINE,
        '        except Exception as e:                      # noqa: BLE001 —— 见 docstring 第 2 点\n'
        '            logger.warning(f"语种决策回调失败（不影响识别）：{e}")\n',
        '        except ValueError as e:\n'
        '            logger.warning(f"语种决策回调失败（不影响识别）：{e}")\n',
        f"{TEST}::test_callback_failure_does_not_break_recognition",
    ),
    (
        "M19 管道收到通知却不记语种 ⇒ 后续重建退回 auto，短语层又回来",
        PIPELINE,
        '            self._term_language = new_lang\n',
        '            pass\n',
        f"{TEST}::test_pipeline_rebuild_uses_detected_language",
    ),
    (
        "M20 `_terminology_language()` 忽略已解析语种 ⇒ 术语热更新复活英文短语层",
        PIPELINE,
        '        return self._term_language or self.course_config.get("language", "auto")\n',
        '        return self.course_config.get("language", "auto")\n',
        f"{TEST}::test_hot_update_after_detection_keeps_phrases_out",
    ),
    (
        "M21 引擎构造漏接回调 ⇒ V5-8 整条链路静默失效（方法级用例仍全绿）",
        PIPELINE,
        '            on_language_decided=self._on_language_decided,\n',
        '',
        f"{TEST}::test_engine_notification_is_actually_wired_in_pipeline",
    ),
    (
        "M22 A/B 脚本不复用管道实现 ⇒ 「测到的」不再是「用户遇到的」",
        "scripts/eval_asr_quality.py",
        '            from asr.pipeline import make_term_rebuilder\n',
        '',
        f"{TEST}::test_eval_script_reuses_the_pipeline_rebuilder",
    ),
]


def run_pytest(node: str) -> tuple[bool, str]:
    """跑一个用例节点，返回 (是否通过, 末几行输出)"""
    BASETEMP.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            sys.executable, "-m", "pytest", node, "-q",
            "-p", "no:cacheprovider", f"--basetemp={BASETEMP}",
        ],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1200,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = "\n".join([ln for ln in out.splitlines() if ln.strip()][-6:])
    return proc.returncode == 0, tail


def mutate_text(text: str, old: str, new: str) -> str | None:
    """按文件**自身的换行风格**做替换；锚点未命中返回 None

    本仓库的 .py 行尾不统一（实测 `scripts/eval_asr_quality.py` 等 5 个是纯 CRLF，
    其余是纯 LF）。锚点里写死 `\\n` 时 CRLF 文件会「找不到锚点」，而探针报的是
    「变异锚点未找到」—— 看起来像源码变了，其实是换行符问题。
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
    print("变异探针：V5-1~V5-4（auto 语种探测 / 简体强制 / 开关 / 热更新）")
    print("=" * 92)

    # ---- 不变量：真身必须全绿 ----
    print("\n[不变量] 真身（未变异）下被测用例应全部通过")
    ok, tail = run_pytest(TEST)
    print("  ✓ 全绿" if ok else "  ✗ 真身都没过 —— 先修环境，探针结论无意义")
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
        print(f"    用例: {node.split('::', 1)[1]}")
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
