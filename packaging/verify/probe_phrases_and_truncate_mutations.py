#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""变异探针：验证本轮两处修复的新断言真有分辨力（2026-10-08）

覆盖两条独立的修复：

  b) 词表短语层的语种分流（`dictionary/terminology.py`）
     —— 中文课不该被注入 60 个英文多词短语（第一组 A/B 实测 CER +42.9%）

  t) `truncate_to_tokens` 在「配额小于单字代价」时返回空串（`asr/prompt_budget.py`）
     —— 原实现 `text[-lo:]` 在 `lo == 0` 时等于 `text[0:]`（全文），反向吃光预算

为什么必须做探针
----------------
「有测试覆盖」不等于「断言验得到」。本轮 **已经踩过一次判断错误**：
最初以为「术语降级按列表顺序丢词」是缺陷 (a)，写了 `rank` 排序参数；
实测发现原顺序已是最优（覆盖 38/66 次出现），按词长排序反而更差（7/66），
于是回退了那段改动 —— 详见 `_pick_within_budget` 的历史记录。
探针的作用就是拦住这类「为修不存在的坑而加代码」。

而且新断言**很容易写成空转**：本轮第一版 truncate 用例用启发式计数器
（单字代价 = 1）去测「单字代价 > 1」的分支，前提断言当场就失败 ——
即「用例根本测不到它想测的东西」。真 tokenizer 才是唯一能构造该情形的计数器。

用法
----
    <venv>/python.exe packaging/verify/probe_phrases_and_truncate_mutations.py

退出码 0 = 全部变异都被抓住；1 = 有变异没被抓住（断言缺分辨力）。
一次性验证证据，不参与常规回归；常规回归跑：
    backend/tests/test_terminology_language.py
    backend/tests/test_asr_pipeline_e2e.py::TestTruncateToTokensEdge
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
TESTS = BACKEND / "tests"

sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(TESTS))

TERMINOLOGY = BACKEND / "dictionary" / "terminology.py"
PROMPT_BUDGET = BACKEND / "asr" / "prompt_budget.py"


def _load(name: str, path: Path) -> types.ModuleType:
    """按路径加载模块（同名文件要能同时存在多个实例）"""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod            # dataclasses 需要先入 sys.modules
    spec.loader.exec_module(mod)
    return mod


REAL_TERM_SRC = TERMINOLOGY.read_text(encoding="utf-8")
REAL_PB_SRC = PROMPT_BUDGET.read_text(encoding="utf-8")

# ---- 变异定义：(名称, 临时模块名, 目标文件, 原文, 替换, 检查项列表) ----
#
# 检查项 = (说明, callable)，callable 接收**变异后的模块**，返回：
#     True  → 该性质被破坏（= 变红，说明断言有分辨力）★这是本探针要的结果
#     False → 性质仍成立（= 仍绿，说明这条断言抓不住该变异）
#     None  → 无法判定，跳过（如真 tokenizer 不可用）
#
# ⚠️ 第一版把语义写反了（返回 True 表示「正常」），导致**五条变异全部报漏掉** ——
#    当时连「一律返回空串」这种必然破坏功能的变异都没被抓到，就该意识到是探针
#    自己坏了而不是断言真没分辨力。教训：探针报「全漏」时先自证探针。
#    自证方法：手动构造变异模块，确认它确实改动了行为（实测 `phrases(zh) == 60`）。

def _c_zh_has_phrases(term):
    """变红条件：中文语种竟然产出了短语层"""
    return len(dict(term.build_prompt_sections(language="zh"))["phrases"]) > 0


def _c_zhcn_has_phrases(term):
    """变红条件：带地区后缀的中文语种产出了短语层（zh-CN 漏网）"""
    return len(dict(term.build_prompt_sections(language="zh-CN"))["phrases"]) > 0


def _c_auto_loses_phrases(term):
    """变红条件：auto 语种把短语层关掉了（反向破坏英文课能力）"""
    return len(dict(term.build_prompt_sections(language="auto"))["phrases"]) == 0


def _c_zh_prompt_has_english(term):
    """变红条件：中文课的提示词里出现了英文短语"""
    p = term.build_initial_prompt(domain="computer", custom_terms=["项目管理器"], language="zh")
    return "gradient descent" in p


def _c_tiny_quota_not_empty(pb):
    """变红条件：配额低于单字代价时没返回空串（-0 陷阱复发）"""
    count = _real_tokenizer_count(pb)
    if count is None:
        return None
    text = "老师说这个坐标系的横坐标要设置成厘米。" * 4
    one = count(text[-1])
    if one <= 1:
        return None                     # 计数器不支持该情形，跳过
    return not all(pb.truncate_to_tokens(text, q, count) == "" for q in range(1, one))


def _c_normal_quota_not_truncating(pb):
    """变红条件：正常配额下截断失效（返回空 / 超预算 / 不从尾部截）"""
    count = _real_tokenizer_count(pb)
    if count is None:
        return None
    text = "老师说这个坐标系的横坐标要设置成厘米。" * 4
    quota = count(text) // 4
    out = pb.truncate_to_tokens(text, quota, count)
    if not out:
        return True
    if count(out) > quota:
        return True
    return out != text[-len(out):]


def _c_assemble_terms_starved(pb):
    """变红条件：prev 配额极小时术语被挤光 / 结果超预算"""
    count = _real_tokenizer_count(pb)
    if count is None:
        return None
    hint = "以下是普通话的句子，请使用简体中文转写。"
    terms = ["项目管理器", "工作区", "工具栏", "菜单栏", "新建图", "画布", "坐标系"]
    hint_cost = count(hint) + pb.SEP_TOKENS
    budget = hint_cost + pb.MIN_TERMS_TOKENS + 1
    b = pb.assemble(
        [("custom", terms)], prev_text="老师说这个坐标系的横坐标要设置成厘米。" * 4,
        hint=hint, budget=budget, count=count,
        prev_max_tokens=pb.DEFAULT_PREV_TEXT_TOKENS,
    )
    return not (b.prev_kept == "" and bool(b.kept["custom"]) and b.used_tokens <= budget)


def _real_tokenizer_count(pb):
    """真 tokenizer 计数器；找不到就返回 None（用例跳过）"""
    import os
    candidates = []
    env = os.environ.get("TINGKEBANLV_TOKENIZER_JSON")
    if env:
        candidates.append(Path(env))
    for parent in REPO.parents:
        candidates.append(parent / "安装空间" / "whisper_models" / "plain" / "tokenizer.json")
    for p in candidates:
        try:
            if p.is_file():
                from tokenizers import Tokenizer
                return pb.make_counter(Tokenizer.from_file(str(p)))[0]
        except Exception:
            continue
    return None


MUTATIONS = [
    # ---- 缺陷 b：语种分流 ----
    (
        "b1. 短语层语种开关恒为 True（退回「中文课也注入英文短语」）",
        "_term_mut_b1",
        TERMINOLOGY,
        REAL_TERM_SRC,
        "    return primary not in _PHRASES_LAYER_EXCLUDED_LANGS",
        "    return True",
        [("zh 产出短语层", _c_zh_has_phrases),
         ("zh-CN 产出短语层", _c_zhcn_has_phrases),
         ("zh 提示词含英文短语", _c_zh_prompt_has_english)],
    ),
    (
        "b2. 语种判断丢掉「主语言子标签」归一化（zh-CN 漏网）",
        "_term_mut_b2",
        TERMINOLOGY,
        REAL_TERM_SRC,
        '    primary = tag.split("-", 1)[0]       # zh-CN → zh；cmn-Hans → cmn',
        "    primary = tag",
        [("zh-CN 产出短语层", _c_zhcn_has_phrases)],
    ),
    (
        "b3. 排除集清空（中文语种不再被识别）",
        "_term_mut_b3",
        TERMINOLOGY,
        REAL_TERM_SRC,
        '_PHRASES_LAYER_EXCLUDED_LANGS = frozenset({"zh", "yue", "wuu", "cmn"})',
        "_PHRASES_LAYER_EXCLUDED_LANGS = frozenset()",
        [("zh 产出短语层", _c_zh_has_phrases),
         ("zh-CN 产出短语层", _c_zhcn_has_phrases)],
    ),
    # ---- 缺陷 t：truncate_to_tokens 的 -0 陷阱 ----
    (
        "t1. 去掉「找不到可行解返回空」的兜底（退回 text[-0:] 全文）",
        "_pb_mut_t1",
        PROMPT_BUDGET,
        REAL_PB_SRC,
        "    if lo <= 0:\n"
        "        # 连 1 个字符都放不下（中文单字 4 token 这类情形）—— 明确返回空，\n"
        "        # 绝不能让 `text[-0:]` 把全文放回去。\n"
        "        return \"\"\n"
        "    return text[-lo:]",
        "    return text[-lo:]",
        [("极小额配额未返回空", _c_tiny_quota_not_empty),
         ("assemble 术语被挤光", _c_assemble_terms_starved)],
    ),
    (
        "t2. 一律返回空串（反向：正常配额下截断被砍掉）",
        "_pb_mut_t2",
        PROMPT_BUDGET,
        REAL_PB_SRC,
        "    return text[-lo:]",
        "    return \"\"",
        [("正常配额截断失效", _c_normal_quota_not_truncating)],
    ),
    (
        "t3. 截断改成从**头部**取（保留最旧内容而非最近内容）",
        "_pb_mut_t3",
        PROMPT_BUDGET,
        REAL_PB_SRC,
        "    return text[-lo:]",
        "    return text[:lo]",
        [("截断不按尾部", _c_normal_quota_not_truncating)],
    ),
]

# 必须保持**绿**的检查项（防止「砍功能换绿灯」）
INVARIANTS = [
    ("auto 保留短语层", "_term_real", TERMINOLOGY, REAL_TERM_SRC,
     ("auto 未产出短语层", _c_auto_loses_phrases)),
    ("正常配额仍截断", "_pb_real", PROMPT_BUDGET, REAL_PB_SRC,
     ("正常配额截断失效", _c_normal_quota_not_truncating)),
]


def _write_mutant(tag: str, filename: Path, src: str, old: str, new: str) -> Path:
    if old not in src:
        raise SystemExit(f"[{tag}] 变异锚点在源码里找不到，探针本身失效：{old[:70]!r}")
    path = filename.with_name(f"{tag}{filename.suffix}")
    path.write_text(src.replace(old, new), encoding="utf-8")
    return path


def main() -> int:
    failures: list[str] = []

    print("=" * 72)
    print("不变量（真身必须全绿 —— 否则下面的「变红」可能只是环境坏了）")
    print("=" * 72)
    for name, tag, path, src, (bad_name, bad_check) in INVARIANTS:
        mod = _load(tag, path)
        broken = bad_check(mod)
        if broken is None:
            print(f"[跳过] {name}：真 tokenizer 不可用")
            continue
        print(f"[{'红??' if broken else '绿'}] {name}")
        if broken:
            failures.append(f"不变量失败：{name}（{bad_name}）")

    print()
    print("=" * 72)
    print("变异（每条都必须让**至少一条**对应检查变红）")
    print("=" * 72)
    for name, tag, path, src, old, new, checks in MUTATIONS:
        mutant = _write_mutant(tag, path, src, old, new)
        try:
            mod = _load(tag, mutant)
            reds, skips = [], []
            for cname, check in checks:
                r = check(mod)
                if r is None:
                    skips.append(cname)
                elif r:
                    reds.append(cname)
            if not reds and not skips:
                print(f"[漏掉] {name}\n        {[c for c, _ in checks]} 全绿 —— 断言缺分辨力")
                failures.append(name)
                continue
            if not reds and skips:
                print(f"[跳过] {name}：真 tokenizer 不可用")
                continue
            print(f"[抓住] {name}\n        变红：{reds}"
                  + (f"（跳过：{skips}）" if skips else ""))
        finally:
            mutant.unlink(missing_ok=True)

    print()
    if failures:
        print(f"✗ {len(failures)} 条未通过：")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("✓ 全部变异都被抓住，不变量保持为绿")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
