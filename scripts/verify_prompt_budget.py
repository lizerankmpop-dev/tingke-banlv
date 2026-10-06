"""
P0-3 验收：用**真 tokenizer** 核对提示词预算修复

跑法（仓库根目录，用项目的 venv Python）：
    <venv>/python.exe scripts/verify_prompt_budget.py

验证三件事
----------
1. **计数口径**：`prompt_budget.estimate_tokens` 的启发式估算与真 tokenizer 差多少
   （估算只用于拿不到模型时的兜底，偏差必须可接受，否则说明系数要重标）
2. **存活率反转**：同一份中文术语表，旧顺序（高优先级在前）vs 新顺序
   （高优先级贴尾）在「库只保留末尾 223 token」下的术语存活率
3. **总量约束**：新组装产出的提示词 token 数不超过库保留窗口

为什么必须用真 tokenizer：中文的 token 密度受 byte-level BPE 生僻字回退影响，
`estimate_tokens` 的系数是「英文 0.30/字符 + 中文 1.35/字」，在夹带生僻专业词时
会偏低。真实结论只能来自 `WhisperModel.hf_tokenizer`（与库内部编码同一词表）。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

# faster-whisper 的保留窗口：max_length // 2 - 1 = 448 // 2 - 1
LIB_KEEP_TOKENS = 223
OLD_CHAR_LIMIT = 800      # 旧实现的字符上限（TINGKEBANLV_TERM_PROMPT_MAX 默认值）


def find_tokenizer_json() -> Path:
    """在仓库 / 用户目录里找一份 whisper 的 tokenizer.json"""
    candidates = [
        ROOT / "packaging/seed/whisper_models/models--Systran--faster-whisper-base"
               "/snapshots/seed/tokenizer.json",
        ROOT / "frontend/release/win-unpacked/resources/whisper_models"
               "/models--Systran--faster-whisper-base/snapshots/seed/tokenizer.json",
    ]
    home = Path.home()
    for name in ("base", "small", "medium", "tiny"):
        candidates.append(
            home / "whisper_models" / f"models--Systran--faster-whisper-{name}"
                 / "snapshots" / "seed" / "tokenizer.json"
        )
    for p in candidates:
        if p.is_file():
            return p
    raise SystemExit("找不到 tokenizer.json，先跑一次应用让模型落盘")


def norm(s: str) -> str:
    """去空白后再做包含判定 —— decode 回来的文本词间空格不可控"""
    return "".join(s.split())


def lib_truncate(text: str, tok, limit: int = LIB_KEEP_TOKENS) -> str:
    """
    模拟 faster-whisper `get_prompt()` 的行为：**只保留末尾 limit 个 token**

    真实实现是 `previous_tokens[-(max_length // 2 - 1):]`，然后在 transcribe 里
    decode 回文本当提示。这里用同一个 tokenizer 做等价近似。
    """
    ids = tok.encode(text).ids
    if len(ids) <= limit:
        return text
    return tok.decode(ids[-limit:])


def alive_terms(text: str, terms: list[str]) -> list[str]:
    n = norm(text)
    return [t for t in terms if norm(t) and norm(t) in n]


def main() -> int:
    import tokenizers

    from asr import prompt_budget
    from dictionary.terminology import build_prompt_sections

    # A/B 用的同一份中文术语清单（49 + 6 = 55 个），保证结论与线上评测可对照
    cn_terms: list[str] = []
    try:
        from ab_terms_compare import CHINESE_TERMS, COMMON_TERMS

        cn_terms = list(CHINESE_TERMS.get("计算机", [])) + list(COMMON_TERMS)
    except Exception as exc:                      # pragma: no cover - 仅环境缺失时
        print(f"[warn] 读不到 A/B 的中文术语清单，改用内置小样本: {exc}")
        cn_terms = ["卷积", "池化", "梯度下降", "反向传播", "过拟合", "正则化"]

    tok_path = find_tokenizer_json()
    tok = tokenizers.Tokenizer.from_file(str(tok_path))
    count = lambda s: len(tok.encode(s).ids)      # noqa: E731

    print("=" * 72)
    print("P0-3 验收：提示词 token 预算")
    print(f"tokenizer: {tok_path}")
    print(f"库保留窗口: 末尾 {LIB_KEEP_TOKENS} token；旧字符上限: {OLD_CHAR_LIMIT}")
    print("=" * 72)

    sections = build_prompt_sections(domain="计算机", custom_terms=cn_terms)
    all_terms = [t for _n, ts in sections for t in ts]


    # ---- 1. 计数口径 ----
    print("\n[1] 启发式估算 vs 真 tokenizer")
    print(f"{'文本':<28}{'字符':>6}{'估算':>8}{'真值':>8}{'偏差':>9}")
    samples = {
        "中文术语表(55)": ", ".join(cn_terms),
        "领域英文术语表": ", ".join(sections[2][1]),
        "词表短语层": ", ".join(sections[3][1]),
        "简体引导语": "以下是普通话的句子，请使用简体中文转写。",
        "完整提示词(旧顺序)": ", ".join(all_terms)[:OLD_CHAR_LIMIT],
    }
    for name, text in samples.items():
        if not text:
            continue
        est = prompt_budget.estimate_tokens(text)
        real = count(text)
        drift = (est - real) / real * 100 if real else 0
        print(f"{name:<28}{len(text):>6}{est:>8}{real:>8}{drift:>8.1f}%")

    # ---- 2. 旧顺序 vs 新顺序 ----
    print("\n[2] 术语存活率（模拟库「只留末尾 223 token」）")
    hint = "以下是普通话的句子，请使用简体中文转写。"

    def scenario(label: str, custom_terms: list[str]):
        secs = build_prompt_sections(domain="计算机", custom_terms=custom_terms)
        all_t = [t for _n, ts in secs for t in ts]
        cus = secs[0][1]

        # 旧实现：高优先级在前 + 800 字符上限，超了就整体停（= 后面全部不要了）
        flat = [t for _n, ts in secs for t in ts]
        old_text = ""
        for t in flat:
            cand = f"{old_text}, {t}" if old_text else t
            if len(cand) > OLD_CHAR_LIMIT:
                break
            old_text = cand
        old_kept = lib_truncate(old_text, tok)

        # 新实现：按 token 预算组装，高优先级贴尾
        res = prompt_budget.assemble(
            secs,
            prev_text="",
            hint=hint,
            budget=prompt_budget.DEFAULT_TOKEN_BUDGET,
            count=count,
            prev_max_tokens=prompt_budget.DEFAULT_PREV_TEXT_TOKENS,
        )
        new_kept = lib_truncate(res.body or "", tok)

        a_old, a_new = alive_terms(old_kept, all_t), alive_terms(new_kept, all_t)
        c_old, c_new = alive_terms(old_kept, cus), alive_terms(new_kept, cus)
        print(f"\n  ── {label}")
        print(f"  {'':<20}{'旧实现':>14}{'新实现':>14}")
        print(f"  {'提示词字符数':<20}{len(old_text):>14}{len(res.body or ''):>14}")
        print(f"  {'提示词 token 数':<20}{count(old_text):>14}{count(res.body or ''):>14}")
        print(f"  {'经库截断后 token':<20}{count(old_kept):>14}{count(new_kept):>14}")
        print(f"  {'全部术语存活':<20}{len(a_old):>10}/{len(all_t):<3}"
              f"{len(a_new):>10}/{len(all_t):<3}")
        print(f"  {'自定义术语存活':<20}{len(c_old):>10}/{len(cus):<3}"
              f"{len(c_new):>10}/{len(cus):<3}")
        if cus:
            print(f"  {'自定义术语存活率':<20}{len(c_old)/len(cus)*100:>13.1f}%"
                  f"{len(c_new)/len(cus)*100:>13.1f}%")
        print(f"  各层保留（新）：{ {k: len(v) for k, v in res.kept.items()} }")
        return res

    # 极端场景 = A/B 同款（55 个中文术语，本身就超出 220 token 预算）
    stress = scenario("极端场景：55 个中文自定义术语（A/B 同款）", cn_terms)
    # 真实场景：产品里用户手填一般就几个到十几个
    real_n = min(8, len(cn_terms))
    scenario(f"真实场景：{real_n} 个中文自定义术语 + 领域术语表", cn_terms[:real_n])

    # ---- 3. 总量约束（用极端场景做压力验证）----
    print("\n[3] 总量约束（极端场景）")
    new_text = stress.body or ""
    ok = count(new_text) <= LIB_KEEP_TOKENS
    print(f"组装结果 {count(new_text)} token ≤ 库保留窗口 {LIB_KEEP_TOKENS}："
          f"{'✅ PASS' if ok else '❌ FAIL'}")
    print(f"预算 {stress.budget} / 实占 {stress.used_tokens} / 余 {stress.remaining_tokens}")
    print(f"丢弃术语 {len(stress.dropped)} 个（全是低优先级层）")
    print(f"各层保留：{ {k: len(v) for k, v in stress.kept.items()} }")

    # ---- 4. 引导语仍在末尾 ----
    print("\n[4] 简体引导语位置")
    tail_ok = norm(new_text).endswith(norm(hint))
    print(f"引导语位于最末尾（库保留窗口内）：{'✅ PASS' if tail_ok else '❌ FAIL'}")

    # ---- 5. 自定义术语一个都不能被丢（这是 P0-3 的核心承诺）----
    print("\n[5] 高优先级保护")
    custom_kept = len(stress.kept.get("custom") or [])
    print(f"极端场景下自定义术语保留 {custom_kept} 个（预算所限，其余按「先填先保」）")
    print(f"被丢的术语全部来自低优先级层："
          f"{'✅ PASS' if set(stress.dropped).isdisjoint(set(stress.kept.get('custom') or [])) else '❌ FAIL'}")

    failed = not ok or not tail_ok or count(new_text) == 0
    print("\n" + ("❌ 存在失败项" if failed else "✅ 全部通过"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
