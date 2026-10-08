# -*- coding: utf-8 -*-
"""
术语热词 A/B 对照运行器

它自己不做识别，只负责把 eval_asr_quality.py 按不同条件跑两（三）遍，
再把两侧报告拉平成一张对照表，并回答一个 eval 脚本本身答不了的问题：

  **术语热词到底有没有让「专业词」识别得更准？**

为什么必须有这一步：整体 WER 下降可能只是因为标点、虚词、口语重复的偶然差异。
术语功能的收益必须落在具体词上才叫收益。所以这里做「术语命中率」统计 ——
对每个目标术语，在参考文本里数出现次数，再去识别文本里核对命中几次。

三个条件：
  off      不注入术语表（基线）
  on       注入术语表 + 打开防循环兜底（= 出厂默认）
  on-noguard  注入术语表但关闭兜底（用于观察「完全不干预时重复循环能持续多久」）
  simp     只注入一段简体中文引导语、不注入任何术语表（检验简繁引导，见 SIMP_HINT）

**2×2 正式对照条件（术语 × 防循环兜底，两轴都显式锁死 —— 推荐做结论时用这四格）**：

  off-noguard   术语 off × 兜底 off
  off-guard     术语 off × 兜底 on
  on-noguard    术语 on  × 兜底 off
  on            术语 on  × 兜底 on（= 出厂默认）

`off` 只是历史别名，等价于「术语 off + 兜底沿用引擎默认」；做正式对照请用上面四个
名字，并把 `_env` 里记下的 `TINGKEBANLV_TERM_AUTODEGRADE` 一并写进报告。

⚠️ **长音频（>2min）必须加 `--realtime`。** 非实时喂入会把整段音频瞬间灌满队列，
引擎来不及消费就按背压**丢片段** —— 实测 630s 素材丢掉 46/63 段，WER 报 0.77，
这个数其实量的是「丢弃策略」而不是识别准确率，而且各格丢的段不同、彼此也不可比。
现在这种情况会**直接中止**（要观察丢弃行为本身才加 `--allow-drops`）。

用法：
  <venv>/Scripts/python.exe scripts/ab_terms_compare.py \
      --audio-dir testset/chinese_lecture/long --language zh --domain 计算机 \
      --custom-terms @zh --conditions off,on,on-noguard --realtime \
      --out packaging/eval/chinese_ab.json

`--custom-terms` 支持哨兵 "@zh"（= 脚本内置的中文术语清单）：中文课程必须用它，
因为产品内置的 DOMAIN_TERMS 全是英文，只传 --domain 是测不出中文术语收益的。
哨兵会在 main() 里展开成真正的词表，再按条件拼进命令行。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = PROJECT_ROOT / "backend"
if _BACKEND.is_dir() and str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# 中文技术术语（评测用，不是产品术语表）
#
# 为什么要单独列一份：内置 DOMAIN_TERMS 全是英文（algorithm / convolution /
# overfitting ...）。中文课堂里老师会说「卷积」「过拟合」，这些词只靠英文术语表
# 根本管不到。把中文词一并统计，才能看出这个缺口有多大 —— 这是本次 A/B 的
# 主要发现之一，不能因为「术语表里没有」就不测。
CHINESE_TERMS = {
    "计算机": [
        "卷积", "卷积核", "池化", "最大池化", "步长", "感受野", "空洞卷积",
        "深度可分离卷积", "全连接", "参数共享", "平移不变性", "特征图",
        "反向传播", "链式法则", "梯度", "梯度下降", "学习率", "随机梯度下降",
        "动量", "收敛", "发散", "震荡", "梯度消失", "梯度爆炸", "初始化",
        "过拟合", "欠拟合", "正则化", "权重衰减", "批归一化", "残差连接",
        "损失函数", "交叉熵", "均方误差", "激活函数", "神经元", "张量",
        "精确率", "召回率", "混淆矩阵", "准确率", "验证集", "测试集",
        "数据增强", "超参数", "批大小", "推理", "算子融合", "量化",
    ],
}
# 多领域共用词：不管 domain 传什么，这些词出现了都值得统计
COMMON_TERMS = ["深度学习", "神经网络", "卷积神经网络", "注意力机制", "注意力", "循环网络"]

# `simp` 条件用的简体中文引导语
#
# Whisper 的 language="zh" 不区分简繁，模型倾向输出繁体。业界常用的做法是往
# initial_prompt 里塞一句简体中文，把输出风格拉回简体。这里用它做单一变量对照。
SIMP_HINT = "以下是普通话的句子，请使用简体中文转写。"

# 备选引导语（用户指定原文）—— 与上面那句做单变量对照，看简繁效果是否等价
SIMP_HINT_ORIGINAL = "下面的内容为简体中文课堂讲课内容，输出全部使用简体中文，不要使用繁体汉字。"

# 「只换引导语、其它全不动」的对照条件 → 下发给子进程的环境变量。
#
# 为什么不复用 `simp` 条件那种「把引导语当自定义术语注入」的做法：那条路径走的是
# 术语层（`--terms on`），引导语会同时出现在**术语层和 hint 层**，不是纯单变量。
# 这里改用 `--terms off` + `TINGKEBANLV_SIMP_HINT`，与 `off` 条件完全同构，
# 唯一差异就是那一句引导语的文本。
SIMP_HINT_VARIANTS: dict[str, dict[str, str]] = {
    "simp-current": {"TINGKEBANLV_SIMP_HINT": SIMP_HINT},
    "simp-original": {"TINGKEBANLV_SIMP_HINT": SIMP_HINT_ORIGINAL},
}

# 「防循环兜底」的显式锁定：CLI 开关 + 环境变量**双写**。
#
# 为什么两边都要写：`--autodegrade` 走 `engine.set_auto_degrade()`（运行时覆盖），
# 而 `TINGKEBANLV_TERM_AUTODEGRADE` 是 `whisper_engine.AUTO_DEGRADE_ENABLED`
# 这个**模块级默认值**（import 时读一次）。只写一边，报告里的 `termAutoDegrade`
# 就可能与引擎默认值不一致，事后无法判断「当时到底哪个开关生效」。
#
# 这条不是洁癖：本素材上真实踩过 —— 同一份音频两次跑，一次触发了自动降级
# （206→70 token，字数多出 672），另一次没触发，而两次日志都没记下 promptChars，
# 差异**再也无法追溯**。所以现在把真实环境变量原样写进报告（`_env`）。
#
# 刻意不给 `simp` / `simp-*` 列值：那几条历史条件当时没传 `--autodegrade`，
# 保持「沿用引擎默认」才能与历史报告对得上。
AUTODEGRADE_ENV: dict[str, str] = {
    "off": "1",
    "off-guard": "1",
    "off-noguard": "0",
    "on": "1",
    "on-noguard": "0",
}


def load_domain_terms(domain: str) -> list[str]:
    """从产品术语表里取该领域的英文术语（与 --terms on 实际注入的内容一致）"""
    if not domain:
        return []
    try:
        from dictionary.terminology import DOMAIN_TERMS

        return list(DOMAIN_TERMS.get(domain, []))
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 读取产品术语表失败，仅统计中文术语: {exc}")
        return []


_PUNCT = set(" \t\r\n，。、；：？！“”‘’（）《》〈〉【】—…·「」『』,.;:?!\"'()[]{}<>-—_/\\|@#$%^&*+=~`")


def norm_zh(text: str) -> str:
    """与 eval_asr_quality.normalize 的中文分支保持一致（去标点、留字符）"""
    return "".join(ch for ch in text if ch not in _PUNCT)


def count_occurrences(haystack: str, needle: str) -> int:
    if not needle:
        return 0
    return haystack.count(needle)


def term_stats(terms: list[str], reference: str, hypothesis: str) -> dict:
    """
    逐个术语统计：参考里出现几次、识别里命中几次

    用「出现次数」而不是「是否出现」：一个术语在 10 分钟的课里通常会说好几次，
    只判存在会掩盖「五次里只认出一次」这种情况。
    """
    ref = norm_zh(reference)
    hyp = norm_zh(hypothesis)
    items = []
    expected_total = 0
    hit_total = 0
    for t in terms:
        key = norm_zh(t)
        exp = count_occurrences(ref, key)
        if exp == 0:
            continue
        got = count_occurrences(hyp, key)
        hit = min(exp, got)
        expected_total += exp
        hit_total += hit
        items.append(
            {"term": t, "expected": exp, "hit": hit, "rate": round(hit / exp, 3)}
        )
    items.sort(key=lambda x: (x["rate"], -x["expected"]))
    return {
        "expectedOccurrences": expected_total,
        "hitOccurrences": hit_total,
        "recall": round(hit_total / expected_total, 4) if expected_total else None,
        "terms": items,
    }


def run_condition(args, cond: str, report: Path, custom: list[str],
                  env_extra: dict | None = None) -> dict:
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "eval_asr_quality.py"),
        "--audio-dir",
        str(Path(args.audio_dir).resolve()),
        "--language",
        args.language,
        "--report",
        str(report),
        "--drain-timeout",
        str(args.drain_timeout),
    ]
    if args.realtime:
        cmd.append("--realtime")
    if args.model:
        cmd += ["--model", args.model]
    if args.limit:
        cmd += ["--limit", str(args.limit)]

    if cond == "off":
        cmd += ["--terms", "off"]
    elif cond == "off-guard":
        # 术语 off × 兜底 on。这是个**对照格**：没有提示词时兜底理应无事可做，
        # 它若与 off-noguard 有差异，说明差异来自别处（噪声/顺序），不是来自兜底。
        cmd += ["--terms", "off", "--autodegrade", "on"]
    elif cond == "off-noguard":
        cmd += ["--terms", "off", "--autodegrade", "off"]
    elif cond in SIMP_HINT_VARIANTS:
        # 简体引导语对照：与 off 完全同构（无术语），只靠环境变量换那一句引导语
        cmd += ["--terms", "off"]
    elif cond == "on":
        cmd += ["--terms", "on", "--autodegrade", "on"]
    elif cond == "on-noguard":
        cmd += ["--terms", "on", "--autodegrade", "off"]
    elif cond == "simp":
        # 只注入一段简体中文引导语、不注入任何术语表（domain 置空，见下方 domain_c）：
        # 用来单独检验「提示词里的简体中文能不能把 Whisper 从繁体拉回来」，
        # 与术语收益解耦。兜底关闭，避免引导语被当成重复循环截断。
        #
        # ⚠ 历史遗留条件，不推荐再用：它把引导语当**自定义术语**注入，走的是术语层，
        # 而引擎在中文语种下还会在 hint 层再注入一次 → 引导语出现两遍，不是纯单变量。
        # 做引导语对照请用 `simp-current` / `simp-original`。
        cmd += ["--terms", "on", "--autodegrade", "off"]
    else:
        raise SystemExit(f"未知条件: {cond}")

    # 注意：这里必须用展开后的 custom（而不是 args.custom_terms 的原始字符串）。
    # 踩过的坑：`--custom-terms @zh` 的哨兵是在 main() 里展开成 55 个中文术语的，
    # 若这里直接透传 args.custom_terms，下发给引擎的就是字面量 "@zh" ——
    # 引擎把它当成「一个自定义术语」，日志显示「40 个（含自定义 1 个）」，
    # 看着像成功，实际 55 个中文术语一个都没注入，整轮 A/B 白跑。
    custom_c = [SIMP_HINT] if cond == "simp" else custom
    domain_c = "" if cond == "simp" else args.domain
    if cond in SIMP_HINT_VARIANTS:
        custom_c, domain_c = [], ""      # 引导语对照条件不注入任何术语
    if custom_c:
        cmd += ["--custom-terms", ",".join(custom_c)]
    if domain_c:
        cmd += ["--domain", domain_c]

    # 防循环兜底：CLI 与环境变量双写（理由见 AUTODEGRADE_ENV 上方注释）
    env_all = dict(env_extra or {})
    guard = AUTODEGRADE_ENV.get(cond)
    if guard is not None:
        env_all["TINGKEBANLV_TERM_AUTODEGRADE"] = guard

    tail = f"  [{env_all}]" if env_all else ""
    print("\n" + "#" * 90)
    print(f"# 条件 [{cond}]  {' '.join(cmd[2:])}{tail}")
    print("#" * 90, flush=True)
    started = time.time()
    proc = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        env={**os.environ, **env_all},
    )
    elapsed = time.time() - started
    if not report.is_file():
        raise SystemExit(f"条件 [{cond}] 未产出报告（退出码 {proc.returncode}）")
    data = json.loads(report.read_text(encoding="utf-8"))
    data["_condition"] = cond
    data["_wallSeconds"] = round(elapsed, 1)
    # 把真实下发过的命令行记进报告：参数拼错时（比如哨兵没展开）从报告就能看出来，
    # 不用去翻日志。踩过坑，所以留证。
    data["_cmd"] = cmd
    if env_all:
        data["_env"] = env_all
    return data


def kvline(label: str, values, better="lower") -> str:
    """任意条件数一行；"←" 只标在最优的那一列（全体并列则不标）"""
    def fmt(v):
        if v is None:
            return "-"
        if isinstance(v, float):
            return f"{v:.4g}"
        return str(v)

    nums = [v for v in values if isinstance(v, (int, float))]
    best = None
    if len(nums) > 1 and len(set(nums)) > 1:
        best = min(nums) if better == "lower" else max(nums)

    cells = ""
    for v in values:
        mark = "←" if (best is not None and isinstance(v, (int, float)) and v == best) else ""
        cells += f"{fmt(v):>14} {mark:<2}"
    return f"{label:<34}{cells}"


def main() -> int:
    p = argparse.ArgumentParser(description="术语热词 A/B 对照")
    p.add_argument("--audio-dir", required=True)
    p.add_argument("--language", default="zh")
    p.add_argument("--domain", default="")
    p.add_argument("--custom-terms", default="",
                   help='逗号分隔；传 "@zh" 表示用脚本内置的中文术语清单')
    p.add_argument("--conditions", default="off,on")
    p.add_argument("--realtime", action="store_true")
    p.add_argument("--model", default=None)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--drain-timeout", type=float, default=900.0)
    p.add_argument(
        "--allow-drops",
        action="store_true",
        help="即使有片段被背压丢弃也继续出对照表（默认直接中止 —— 见下方说明）",
    )
    p.add_argument("--out", default="packaging/eval/ab_terms.json")
    args = p.parse_args()

    conds = [c.strip() for c in args.conditions.split(",") if c.strip()]
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    domain_terms = load_domain_terms(args.domain)
    cn_terms = CHINESE_TERMS.get(args.domain, []) + COMMON_TERMS
    custom = [t.strip() for t in args.custom_terms.split(",") if t.strip()]
    # "@zh" 是内置中文术语清单的简写：命令行塞 50 个中文词又长又容易因编码出问题，
    # 用哨兵值代替，实际清单仍会完整写进报告，可追溯
    if custom == ["@zh"]:
        custom = list(cn_terms)

    print(f"目标英文术语（产品术语表 · {args.domain}）: {len(domain_terms)} 个")
    print(f"目标中文术语（本次评测专用清单）        : {len(cn_terms)} 个")
    print(f"自定义术语                              : {custom or '（无）'}")

    runs: dict[str, dict] = {}
    for cond in conds:
        # 每个条件的**原始**报告名从 `--out` 派生，不再用固定名 `_ab_<cond>.json`。
        # 固定名的含义是「换一份素材重跑一次，就静默覆盖掉上一份素材的证据」——
        # 英文测试集的 `_ab_on.json` / `_ab_on-noguard.json` 就是这么被撞掉的。
        # 派生后不同 A/B 运行的产物互不干扰，且从文件名就能看出属于哪一次。
        report = out_path.with_name(f"{out_path.stem}.{cond}.json")
        runs[cond] = run_condition(
            args, cond, report, custom, SIMP_HINT_VARIANTS.get(cond)
        )
        # 丢弃片段 = 背压溢出，字幕直接缺内容。此时表里的 WER/CER 量的是
        # 「丢弃策略」而不是「识别准确率」，**格与格之间也不可比**（各格丢的段不同）。
        # 实测：630s 素材按「尽快喂入」跑，63 个 VAD 片段只产出 10~17 条字幕、
        # 丢掉 46~48 段，WER 报 0.77 —— 这个数字看着像「识别很差」，其实绝大部分
        # 是根本没送去识别。所以默认**中止**，而不是照常打印一张漂亮的表。
        # 长音频请用 `--realtime`（按真实速率喂入，引擎跟得上就不会积压）。
        dropped = runs[cond].get("summary", {}).get("totalDropped") or 0
        if dropped and not args.allow_drops:
            raise SystemExit(
                f"条件 [{cond}] 丢弃了 {dropped} 个片段（背压触发）—— 已中止。\n"
                f"  长音频请加 --realtime（非实时喂入会把音频瞬间灌满队列，"
                f"引擎来不及消费就丢；这跟真实使用不是一回事）。\n"
                f"  确实想观察丢弃行为本身时，加 --allow-drops 继续。"
            )

    def summary(cond):
        return runs[cond].get("summary", {})

    def per_file(cond, field, default=None):
        files = runs[cond].get("results", [])
        vals = [f.get(field) for f in files if "error" not in f]
        vals = [v for v in vals if v is not None]
        return vals[0] if vals else default

    names = list(runs.keys())
    print("\n" + "=" * 90)
    print(f"对照结果（语言 {args.language}｜领域 {args.domain or '未指定'}｜"
          f"{'真实速率' if args.realtime else '尽快喂入'}）")
    print("=" * 90)
    header = f"{'指标':<34}" + "".join(f"{n:>16}" for n in names)
    print(header)
    print("-" * len(header))

    rows = [
        ("整体 WER", lambda c: summary(c).get("meanWer"), "lower"),
        ("整体 CER", lambda c: summary(c).get("meanCer"), "lower"),
        ("平均 RTF", lambda c: summary(c).get("meanRtf"), "lower"),
        ("最差 RTF", lambda c: summary(c).get("maxRtf"), "lower"),
        ("片段数", lambda c: summary(c).get("totalSegments"), "lower"),
        ("字幕数", lambda c: summary(c).get("totalSubtitles"), "lower"),
        ("送入片段数（引擎累计）", lambda c: summary(c).get("totalInSegments"), "lower"),
        ("产出字幕数（引擎累计）", lambda c: summary(c).get("totalOutSegments"), "lower"),
        ("重复循环判定次数", lambda c: summary(c).get("totalRepetitionEvents"), "lower"),
        ("提示词字符数", lambda c: summary(c).get("termPromptChars"), "lower"),
        ("平均延迟 s", lambda c: summary(c).get("meanLatency"), "lower"),
        ("峰值延迟 s", lambda c: summary(c).get("peakLatency"), "lower"),
        ("丢弃片段", lambda c: summary(c).get("totalDropped"), "lower"),
        ("合并次数", lambda c: summary(c).get("totalMerged"), "lower"),
        ("总耗时 s", lambda c: runs[c].get("_wallSeconds"), "lower"),
    ]
    table = {}
    for label, getter, better in rows:
        vals = [getter(c) for c in names]
        print(kvline(label, vals, better))
        table[label] = dict(zip(names, vals))

    degraded = {c: summary(c).get("degradedFiles") for c in names}
    more_sub = {c: summary(c).get("filesWithMoreSubtitlesThanSegments") for c in names}
    print("-" * len(header))
    for c in names:
        print(f"  [{c}] 触发降级的文件: {degraded[c] or '无'}｜字幕>片段的文件: {more_sub[c] or '无'}")

    # ---- 术语命中率（本次 A/B 的核心问题之二）----
    term_report = {}
    print("\n" + "=" * 90)
    print("术语命中率（参考里出现的术语，在识别文本里认出了多少）")
    print("=" * 90)
    ref_text = None
    for c in names:
        for f in runs[c].get("results", []):
            if f.get("reference"):
                ref_text = f["reference"]
                break
        if ref_text:
            break

    if ref_text:
        groups = {
            "中文术语": cn_terms,
            "英文术语(产品术语表)": domain_terms,
            "自定义术语": custom,
        }
        for gname, terms in groups.items():
            if not terms:
                continue
            print(f"\n[{gname}]")
            hits = {}
            for c in names:
                hyp = " ".join(
                    f.get("recognition") or "" for f in runs[c].get("results", [])
                )
                hits[c] = term_stats(terms, ref_text, hyp)
            hh = f"{'术语':<22}" + "".join(f"{n:>18}" for n in names)
            print(hh)
            all_terms = sorted(
                {i["term"] for st in hits.values() for i in st["terms"]},
                key=lambda t: next(
                    (i["rate"] for st in hits.values() for i in st["terms"] if i["term"] == t),
                    1,
                ),
            )
            for t in all_terms:
                cells = []
                for c in names:
                    item = next((i for i in hits[c]["terms"] if i["term"] == t), None)
                    cells.append(f"{item['hit']}/{item['expected']}" if item else "-")
                print(f"{t:<22}" + "".join(f"{x:>18}" for x in cells))
            for c in names:
                tag = f"→ {c} 命中率 {hits[c]['recall']}"
                print(f"  小计：{hits[c]['hitOccurrences']}/{hits[c]['expectedOccurrences']} {tag}")
            term_report[gname] = hits
    else:
        print("（无参考文本，跳过术语命中率统计）")

    combined = {
        "config": {
            "audioDir": str(Path(args.audio_dir).resolve()),
            "language": args.language,
            "domain": args.domain,
            "customTerms": custom,
            "realtime": bool(args.realtime),
            "conditions": names,
            "model": args.model,
        },
        "table": table,
        "degradedFiles": degraded,
        "filesWithMoreSubtitlesThanSegments": more_sub,
        "termRecall": term_report,
        "runs": {c: {"summary": runs[c].get("summary"), "results": runs[c].get("results")} for c in names},
        # 每个条件实际跑过的命令行，便于事后核对参数到底生效没有
        "commands": {c: runs[c].get("_cmd") for c in names},
        # 每个条件真实下发的环境变量（`TINGKEBANLV_TERM_AUTODEGRADE`、
        # `TINGKEBANLV_SIMP_HINT`）。只记 CLI 是不够的：`AUTO_DEGRADE_ENABLED`
        # 是 **import 时读的模块级默认值**，只受环境变量影响 —— 少了这一栏，
        # 事后就无法回答「当时那个开关到底是哪个值」。
        "env": {c: runs[c].get("_env") for c in names},
    }
    out_path.write_text(json.dumps(combined, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n对照报告已写入: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
