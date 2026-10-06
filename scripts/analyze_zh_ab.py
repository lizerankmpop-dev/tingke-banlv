# -*- coding: utf-8 -*-
"""
中文 A/B 结果解读：把「整体 CER」拆成「简繁差异」与「真实识别错误」

为什么需要这一步 —— 跑完第一轮中文 A/B 后出现了两个不能用整体 CER 解释的现象：

1. `--terms off`（提示词 0 字符）的 CER 高达 0.5718，而音频是干净的 TTS 合成音。
   打开识别文本一看，Whisper 输出的是**繁体中文**：
       参考  同学们好，今天这节课我们来讲卷积神经网络……
       识别  同學們好 今天這節刻 我們來講劍擊神經網絡……
   简体参考 vs 繁体识别 → 每个字都算错，CER 被虚高。真实识别质量必须先把
   识别结果繁转简再算，否则测的是编码习惯而不是识别能力。

2. `--terms off` 的「字幕数/片段数」= 91/56 = 1.625，远超兜底判据的 1.2 阈值，
   但此时提示词是 0 字符、根本没有术语表可退化。说明**兜底判据在中文下会误报**：
   中文一句话经 Whisper 输出常被标点切成好几条字幕，「字幕数 > 片段数」是
   中文的天然形态。这需要用真正的重复检测（n-gram 自重复）来对照。

所以本脚本做四件事：
  · 繁简分解：CER_raw / CER_after_t2s / 简繁贡献占比
  · 真实重复检测：n-gram 自重复质量 + 最长重复子串长度（对照计数比判据）
  · 术语命中率（繁转简后重算，否则命中率被简繁压成 0）
  · 对齐错误样例：取出最长的若干处差异块，便于人工判断「哪些词被听错了」

用法：
  <venv>/Scripts/python.exe scripts/analyze_zh_ab.py \
      --report packaging/eval/chinese_ab_proxy.json \
      --out packaging/eval/chinese_ab_proxy.analysis.json
"""

from __future__ import annotations

import argparse
import collections
import difflib
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

# 复用 A/B 脚本的归一化与术语统计，保证两张表口径一致
from ab_terms_compare import CHINESE_TERMS, COMMON_TERMS, norm_zh, term_stats  # noqa: E402

try:
    import zhconv
except ImportError:  # pragma: no cover - 环境缺依赖时给出可执行的提示
    raise SystemExit(
        "缺少 zhconv（繁简转换）。安装：\n"
        "  <venv>/Scripts/python.exe -m pip install --no-deps zhconv\n"
        "离线安装：先用 pip download 拿到 sdist，再 --no-build-isolation 安装"
    )

_PUNCT = set(" \t\r\n，。、；：？！“”‘’（）《》〈〉【】—…·「」『』,.;:?!\"'()[]{}<>-—_/\\|@#$%^&*+=~`")


def to_simplified(text: str) -> str:
    """繁体 → 简体（保留标点，调用方自己决定要不要再归一化）"""
    return zhconv.convert(text, "zh-cn")


def round4(x):
    return None if x is None else round(x, 4)


# --------------------------------------------------------------------------
# 真实重复检测
#
# 兜底判据用的是「产出字幕数 / 送入片段数 > 1.2」，这在中文下必然误报（见文件头）。
# 真正的重复循环在文本上表现为**大段子串自我重复**，所以直接量它。
# --------------------------------------------------------------------------

def dup_ngram_stats(text: str, sizes=(8, 12, 16, 24, 32)) -> dict:
    """各长度下 n-gram 的自重复质量（重复次数 - 1 之和）与占比"""
    out = {}
    for n in sizes:
        if len(text) < n:
            out[n] = {"windows": 0, "dupMass": 0, "ratio": 0.0}
            continue
        cnt = collections.Counter(text[i : i + n] for i in range(len(text) - n + 1))
        dup = sum(c - 1 for c in cnt.values() if c > 1)
        out[n] = {
            "windows": len(text) - n + 1,
            "dupMass": dup,
            "ratio": round4(dup / (len(text) - n + 1)),
        }
    return out


def has_repeat(text: str, n: int) -> bool:
    """是否存在长度恰为 n 的、出现 >=2 次的子串"""
    seen = set()
    for i in range(len(text) - n + 1):
        g = text[i : i + n]
        if g in seen:
            return True
        seen.add(g)
    return False


def max_repeat_len(text: str, lo: int = 8) -> int:
    """
    最长「出现 >=2 次」的子串长度

    「存在长度 >= n 的重复子串」对 n 单调（长度 n 重复 ⇒ 其前缀长度 n-1 也重复），
    所以可以直接二分。上界取 len//2：重复子串至少出现两次、不重叠。
    """
    hi = len(text) // 2
    if hi < lo:
        return 0
    best = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if has_repeat(text, mid):
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def top_repeats(text: str, n: int = 16, k: int = 5) -> list[dict]:
    """出现次数最多的若干个 n-gram（给人工看「到底重复了什么」）"""
    cnt = collections.Counter(text[i : i + n] for i in range(len(text) - n + 1))
    items = [{"gram": g, "count": c} for g, c in cnt.items() if c > 1]
    items.sort(key=lambda x: (-x["count"], x["gram"]))
    return items[:k]


def repeat_blocks(text: str, n: int, limit: int = 6) -> list[dict]:
    """
    长度为 n、出现 >=2 次的子串，重叠的合并成「重复区域」后返回

    为什么必须合并：同一个重复区域会被 n 个滑动窗口各命中一次，直接列 n-gram
    只会看到「在训练时随机丢掉一部分神 / 训练时随机丢掉一部分神经 / ...」
    这种看着像 5 个不同问题的同一件事。
    """
    if n <= 0 or len(text) < n:
        return []
    pos: dict[str, list[int]] = collections.defaultdict(list)
    for i in range(len(text) - n + 1):
        pos[text[i : i + n]].append(i)

    spans = [(ps[0], ps[0] + n, len(ps), g) for g, ps in pos.items() if len(ps) > 1]
    spans.sort()
    merged: list[list] = []
    for s, e, c, g in spans:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
            merged[-1][2] = max(merged[-1][2], c)
        else:
            merged.append([s, e, c, g])
    merged.sort(key=lambda m: (-m[2], m[0]))
    return [
        {"text": g, "start": s, "occurrences": c, "spanChars": e - s}
        for s, e, c, g in merged[:limit]
    ]


def longest(text: str) -> dict:
    """整段文本的重复画像（用去标点后的裸文本，避免标点干扰）"""
    t = "".join(ch for ch in text if ch not in _PUNCT)
    mrl = max_repeat_len(t)
    return {
        "chars": len(t),
        "dupBySize": dup_ngram_stats(t),
        "maxRepeatLen": mrl,
        # 在「最长重复长度」这一档上列重复块：这一档的块最像人工判定的重复循环
        "repeatBlocks": repeat_blocks(t, mrl) if mrl else [],
        "top": top_repeats(t, n=16, k=5),
    }


# --------------------------------------------------------------------------
# 错误样例（对齐差异块）
# --------------------------------------------------------------------------

def diff_blocks(ref: str, hyp: str, limit: int = 12, min_len: int = 3) -> list[dict]:
    """
    取出参考与识别之间的差异块，按长度降序返回

    用 SequenceMatcher 而不是逐字符扫：中文里一个字的错位会让后面整段错开，
    逐字符比只能看到一长串「错」，看不出「其实只是漏了一个字」。
    """
    blocks = []
    sm = difflib.SequenceMatcher(None, ref, hyp, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if max(i2 - i1, j2 - j1) < min_len:
            continue
        blocks.append(
            {
                "op": tag,
                "ref": ref[i1:i2],
                "hyp": hyp[j1:j2],
                "len": max(i2 - i1, j2 - j1),
                "refPos": i1,
            }
        )
    blocks.sort(key=lambda b: -b["len"])
    return blocks[:limit]


# --------------------------------------------------------------------------
# 主分析
# --------------------------------------------------------------------------

def analyze_file(res: dict, cn_terms: list[str], diff_limit: int) -> dict:
    ref_raw = res.get("reference") or ""
    hyp_raw = res.get("recognition") or ""
    if not ref_raw or not hyp_raw:
        return {"file": res.get("file"), "skipped": "缺参考或识别文本"}

    hyp_s = to_simplified(hyp_raw)
    ref_n = norm_zh(ref_raw)
    hyp_n = norm_zh(hyp_raw)
    hyp_sn = norm_zh(hyp_s)

    # 用与 eval_asr_quality 一致的编辑距离口径重算 CER
    from eval_asr_quality import edit_distance  # noqa: E402

    edits_raw = edit_distance(list(ref_n), list(hyp_n))
    edits_t2s = edit_distance(list(ref_n), list(hyp_sn))
    cer_raw = edits_raw / len(ref_n) if ref_n else None
    cer_t2s = edits_t2s / len(ref_n) if ref_n else None
    # 简繁贡献：被繁简吃掉的错误量占原始错误量的比例
    trad_share = (
        (edits_raw - edits_t2s) / edits_raw if edits_raw else 0.0
    )

    # 繁体字占比（识别字符里不在「繁转简后」字符集中的比例，纯统计描述）
    hyp_outside = sum(1 for c in hyp_n if c not in set(hyp_sn))

    segs = res.get("segments")
    subs = res.get("subtitles")
    ratio = (subs / segs) if segs else None

    dup = longest(hyp_raw)
    # 同一个重复画像在「繁转简后」重算一份，验证重复不是简繁造成的假象
    dup_s = longest(hyp_s)

    return {
        "file": res.get("file"),
        "audioSeconds": res.get("audioSeconds"),
        "segments": segs,
        "subtitles": subs,
        "subtitlePerSegment": round4(ratio),
        "guardThreshold": 1.2,
        "guardWouldFire": (ratio is not None and ratio > 1.2),
        "termPromptChars": res.get("termPromptChars"),
        "termPromptDegraded": res.get("termPromptDegraded"),
        "repetitionEvents": res.get("repetitionEvents"),
        "inSegments": res.get("inSegments"),
        "outSegments": res.get("outSegments"),
        "cer": {
            "raw": round4(cer_raw),
            "afterT2S": round4(cer_t2s),
            "editsRaw": edits_raw,
            "editsAfterT2S": edits_t2s,
            "traditionalShareOfErrors": round4(trad_share),
            "hypTraditionalCharShare": round4(hyp_outside / len(hyp_n)) if hyp_n else None,
            "refUnits": len(ref_n),
        },
        "repetition": {
            "raw": dup,
            "simplified": dup_s,
            "maxRepeatLenSimplified": dup_s["maxRepeatLen"],
        },
        "terms": term_stats(cn_terms, ref_raw, hyp_s),
        "termStatsRawEncoding": term_stats(cn_terms, ref_raw, hyp_raw),
        "errors": diff_blocks(ref_n, hyp_sn, limit=diff_limit),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="中文 A/B 结果解读（繁简分解 + 真实重复检测）")
    p.add_argument("--report", required=True, help="ab_terms_compare 汇总报告，或单个 eval 报告")
    p.add_argument("--out", default="", help="分析结果输出（默认 <report>.analysis.json）")
    p.add_argument("--errors", type=int, default=12, help="每个文件输出多少处错误样例")
    p.add_argument("--print-errors", type=int, default=6, help="控制台打印多少处错误样例")
    args = p.parse_args()

    report_path = Path(args.report).resolve()
    data = json.loads(report_path.read_text(encoding="utf-8"))

    # 汇总报告有 runs；单个 eval 报告只有 results
    if "runs" in data:
        conds = list(data["runs"].keys())
        sources = {c: data["runs"][c] for c in conds}
        config = data.get("config", {})
    else:
        conds = [data.get("config", {}).get("terms", "single")]
        sources = {conds[0]: data}
        config = data.get("config", {})

    domain = config.get("domain") or "计算机"
    cn_terms = CHINESE_TERMS.get(domain, []) + COMMON_TERMS

    out = {"report": str(report_path), "config": config, "conditions": {}, "answers": {}}
    print("=" * 96)
    print("中文 A/B 结果解读（繁简分解 + 真实重复检测）")
    print(f"报告: {report_path.name}｜条件: {', '.join(conds)}｜中文术语目标 {len(cn_terms)} 个")
    print("=" * 96)

    for cond in conds:
        files = [r for r in sources[cond].get("results", []) if "error" not in r]
        out["conditions"][cond] = {
            "files": [analyze_file(r, cn_terms, args.errors) for r in files],
            "summary": sources[cond].get("summary"),
        }

    # ---- 表 1：CER 简繁分解 ----
    print("\n【表 1】CER 简繁分解 —— 整体 CER 里有多少是繁体输出造成的")
    head = f"{'条件':<14}{'CER 原始':>12}{'CER 繁转简后':>14}{'简繁贡献占比':>14}{'繁体字占比':>12}"
    print(head)
    print("-" * len(head))
    for cond in conds:
        for f in out["conditions"][cond]["files"]:
            c = f.get("cer")
            if not c:
                continue
            print(
                f"{cond:<14}{c['raw']:>12}{c['afterT2S']:>14}"
                f"{c['traditionalShareOfErrors']:>13.1%}{c['hypTraditionalCharShare']:>12.1%}"
            )

    # ---- 表 2：兜底判据 vs 真实重复 ----
    print("\n【表 2】兜底判据（字幕/片段 > 1.2） vs 真实重复（最长自我重复子串）")
    head = (
        f"{'条件':<14}{'字幕/片段':>12}{'判据是否触发':>14}"
        f"{'最长重复子串':>14}{'12gram 重复率':>15}{'引擎判定次数':>14}"
    )
    print(head)
    print("-" * len(head))
    for cond in conds:
        for f in out["conditions"][cond]["files"]:
            if "repetition" not in f:
                continue
            r = f["repetition"]["simplified"]
            print(
                f"{cond:<14}{f['subtitlePerSegment']:>12}{('触发' if f['guardWouldFire'] else '未触发'):>14}"
                f"{r['maxRepeatLen']:>14}{r['dupBySize'][12]['ratio']:>15}"
                f"{f['repetitionEvents']:>14}"
            )

    # ---- 重复块清单：直接给出「重复了什么」，供人工判定是否属重复循环 ----
    print("\n【重复块清单】最长重复长度这一档上、出现 >=2 次的原文（去标点）")
    for cond in conds:
        for f in out["conditions"][cond]["files"]:
            blocks = f.get("repetition", {}).get("simplified", {}).get("repeatBlocks") or []
            print(f"\n  ── [{cond}] 最长重复 {f['repetition']['simplified']['maxRepeatLen']} 字 ──")
            if not blocks:
                print("     （无重复块）")
            for b in blocks:
                print(f"     ×{b['occurrences']}  {b['text']}")


    # ---- 表 3：术语命中率（繁转简后） ----
    print("\n【表 3】中文术语命中率 —— 繁转简后重算（不转换会被简繁压成 0，结论完全相反）")
    head = f"{'条件':<14}{'命中/应出现':>14}{'命中率':>10}{'（未转换时）':>16}"
    print(head)
    print("-" * len(head))
    for cond in conds:
        for f in out["conditions"][cond]["files"]:
            t = f.get("terms")
            tr = f.get("termStatsRawEncoding")
            if not t:
                continue
            frac = f"{t['hitOccurrences']}/{t['expectedOccurrences']}"
            print(
                f"{cond:<14}{frac:>14}"
                f"{str(t['recall']):>10}{str(tr['recall']):>16}"
            )

    # ---- 逐词明细：只列「繁转简后仍未命中」的词（真正需要术语热词救的） ----
    print("\n【漏词清单】参考里出现过、但繁转简后仍未识别的中文术语")
    for cond in conds:
        for f in out["conditions"][cond]["files"]:
            t = f.get("terms")
            if not t:
                continue
            missed = [i for i in t["terms"] if i["hit"] < i["expected"]]
            if not missed:
                print(f"  [{cond}] 无漏词")
                continue
            print(f"  [{cond}] {len(missed)} 个：")
            for i in missed:
                print(f"      {i['term']:<14} 命中 {i['hit']}/{i['expected']}")

    # ---- 错误样例 ----
    if args.print_errors:
        print(f"\n【错误样例】每条件前 {args.print_errors} 处最长差异块（繁转简后对齐）")
        for cond in conds:
            for f in out["conditions"][cond]["files"]:
                print(f"\n  ── [{cond}] {f.get('file')} ──")
                for e in f.get("errors", [])[: args.print_errors]:
                    print(f"    参考: {e['ref']}")
                    print(f"    识别: {e['hyp']}   ({e['op']})")

    # ---- 回答两个核心问题 ----
    print("\n" + "=" * 96)
    print("核心问题回答")
    print("=" * 96)
    answers = {}
    for cond in conds:
        files = [f for f in out["conditions"][cond]["files"] if f.get("cer")]
        if not files:
            continue
        f = files[0]
        answers[cond] = {
            "cer_raw": f["cer"]["raw"],
            "cer_after_t2s": f["cer"]["afterT2S"],
            "max_repeat_len": f["repetition"]["simplified"]["maxRepeatLen"],
            "guard_would_fire": f["guardWouldFire"],
            "subtitle_per_segment": f["subtitlePerSegment"],
            "term_recall": f["terms"]["recall"],
        }
    out["answers"] = answers
    for cond, a in answers.items():
        print(
            f"  [{cond}] CER {a['cer_raw']} → 繁转简后 {a['cer_after_t2s']}｜"
            f"最长重复子串 {a['max_repeat_len']} 字｜"
            f"计数比判据{'会触发' if a['guard_would_fire'] else '不触发'}"
            f"（{a['subtitle_per_segment']}）｜中文术语命中率 {a['term_recall']}"
        )

    out_path = (
        Path(args.out).resolve()
        if args.out
        else report_path.with_suffix("").with_suffix(".analysis.json")
    )
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n分析结果已写入: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
