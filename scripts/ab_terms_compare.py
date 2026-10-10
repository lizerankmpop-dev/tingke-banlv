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

# ============ 课程原生术语表（第二组 A/B，2026-10-08） ============
#
# 为什么要有第二份、而不是改 CHINESE_TERMS：
#   第一组 A/B 的结论「terms 让 CER +42.9%」是在 `@zh`（= 6 个 AI 术语）条件下
#   得到的。若直接把 CHINESE_TERMS 改成本课词表，第一组报告就**再也无法复现** ——
#   同一个哨兵在不同时间指向不同词表，等于把「可比性」静默毁掉。所以新增哨兵
#   `@zh-origin`，`@zh` 原样冻结。
#
# 🔴 第一组的致命偏差（这是本组的立项理由）：
#   第一组 `--terms on` 实际注入了 `basic_vocab.json` 里按「含空格的多词短语」挑出的
#   **41 个通用英文术语**（gradient descent ... prosocial behavior）。素材是中文
#   Origin 绘图课，两者毫无交集 → 第一组测到的是「往中文课里塞无关英文词表会掉多少
#   分」，**不能**推出术语功能本身有没有用。术语召回命中数四格全为 0 就是这个的旁证。
#
# 本表来源（可逐词回溯，这是它的价值所在）：
#   1. `real_zhlecture_origin_plot.corrections.tsv` 的 `replacement` 列 —— 那 81 条
#      fix 的右端就是老师真实说出的词（`做标戏`→`坐标系` 这类误识别形态的左端一律不收）；
#   2. `real_zhlecture_origin_plot.preproof.txt` 定稿全文（35 段 / 2791 字）逐段核对，
#      只收「老师作为知识点讲出来的名词性术语」。
#
# 收词三条边界（定过就得守着，否则 CER 口径会漂）：
#   · 只收**名词性专业术语**。虚词、口语（其实/就是/等等）、动词短语（想要/选择/添加）
#     一律不收 —— 它们不是「热词能救回来的东西」，收进来只会稀释预算。
#   · **不收 `Book` / `Graph` / `Origin` / `Label` 这四个英文词**。理由不是它们不重要，
#     而是它们本来就以英文形态出现在定稿里，而提示词注入英文词对中文课是已验证的
#     干扰项（第一组结论）。要测的是「中文术语热词」这一个变量，不能把这个干扰
#     重新夹带进来 —— 那正是第一组的错误。
#   · 规模上限按 **token 预算**定，不按个数定：本表 26 词 = 158 token（真 tokenizer 实测），
#     加简体引导语 26 + 上一段文本配额 48 = 232，逼近但**不超** faster-whisper 的
#     223 保留窗口（`max_length//2-1`）。再多收一个词就可能触发静默截断，把
#     「高优先级贴尾」的安全边界悄悄吃掉。
COURSE_TERMS_ORIGIN = [
    # Origin 界面结构（老师逐个介绍的部分名）
    "项目管理器", "工作区", "工具栏", "菜单栏",
    # Graph 三层结构
    "新建图", "画布", "坐标系", "图层", "子图",
    # 图型
    "折线图", "柱形图",
    # 数据文件与数据表
    "数据表", "曲线数据",
    # 菜单项与对话框
    "图表绘制", "对话框",
    # 轴 / 列标语义
    "横坐标", "纵坐标", "误差", "数据标签",
    # 尺寸与单位（老师反复讲的排版约束）
    "单位", "厘米", "毫米", "双栏", "单栏",
]

# 哨兵 → 词表。`@zh` 冻结在第一组的值上，新实验一律用 `@zh-origin`。
CUSTOM_TERM_SENTINELS = {
    "@zh": lambda domain: CHINESE_TERMS.get(domain, []) + COMMON_TERMS,
    "@zh-origin": lambda domain: list(COURSE_TERMS_ORIGIN),
}

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
    elif cond == "phrases-on":
        # 短语层开 = **修复前**的行为：术语语种 auto → 中文课照旧注入词表短语层
        # （实测 41 条英文多词短语，794 字符 / 177 token）。兜底关，避免降级混淆。
        cmd += ["--terms", "on", "--autodegrade", "off", "--term-language", "auto"]
    elif cond == "phrases-off":
        # 短语层关 = **修复后**的行为：术语语种 zh → 该层不产出。
        # 与 phrases-on 构成单变量对照（同锚点、同素材、同 ASR 语种，只差术语语种）。
        cmd += ["--terms", "on", "--autodegrade", "off", "--term-language", "zh"]
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

    # 全局 `--term-language`：只对**没有自己指定**的条件生效（条件级优先）。
    # `phrases-on` / `phrases-off` 就是各自指定语种的，靠它们做单变量对照。
    if args.term_language and "--term-language" not in cmd:
        cmd += ["--term-language", args.term_language]

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


def invalid_round_reason(summary: dict) -> str | None:
    """这一轮在**结构上**是否可用？不可用则返回原因，可用返回 None。

    只判「有没有真的测到识别」这两条：

      · `totalSegments == 0` —— VAD 没工作，或音频全静音；
      · `totalSubtitles == 0` —— 切出了片段但一条字幕都没有（片段没接到引擎上）。

    两种情况下 WER/CER 都**恒等于 1.0**（参考全文被整块 delete），表里看着像
    「识别很差」，其实什么都没测到。放进对照表，就会以
    「术语注入把 WER 从 1.0 降到 0.8」这种形态产出**完全虚假**的结论。

    🔴 判据刻意**不含** `verdict.passed == False`，这是实测定的（不是偷懒）：
    盘了 42 份历史子报告，10 份 `passed=False` —— 8 份是背压丢弃（另有专门守卫），
    另 2 份里 `short-terms.json` 是**平均 RTF ≥ 1** 才判 False，那是「跑得慢」的
    如实记录，准确率数据本身完全有效。拿 `passed` 当中止条件会把有效证据一起拦掉
    （方案文档 H4 原建议就是 `totalSubtitles==0 或 passed==false`，此处按数据修正）。

    旧报告可能整个 `summary` 缺键 → 一律按 0 处理（保守：宁可拦下去人工看一眼）。
    """
    segs = summary.get("totalSegments") or 0
    subs = summary.get("totalSubtitles") or 0
    if segs == 0:
        return f"切出 {segs} 个片段（VAD 未工作或音频全静音）"
    if subs == 0:
        return f"{segs} 个片段 → 0 条字幕（片段未送入识别引擎）"
    return None


def main() -> int:
    p = argparse.ArgumentParser(description="术语热词 A/B 对照")
    p.add_argument("--audio-dir", required=True)
    p.add_argument("--language", default="zh")
    p.add_argument(
        "--term-language",
        default=None,
        help="单独指定术语层语种（默认跟随 --language）。用于把「术语语种分流」"
        "当唯一变量做对照：`--language zh --term-language auto` = 修复前行为，"
        "`--term-language zh` = 修复后行为；不动 --language 以免 ASR 语种也变。",
    )
    p.add_argument("--domain", default="")
    p.add_argument("--custom-terms", default="",
                   help='逗号分隔；传 "@zh" 或 "@zh-origin" 表示用脚本内置的中文术语清单'
                        '（"@zh" = 6 个 AI 术语，第一组实验冻结用；'
                        '"@zh-origin" = 本课 Origin 原生术语，做课程内 A/B 用这个）')
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
    p.add_argument(
        "--allow-invalid",
        action="store_true",
        help="即使某轮**结构不可用**（0 片段 / 0 字幕 → WER 恒 1.0）也继续出对照表"
        "（默认直接中止 —— 那类数字看着像「识别很差」，其实什么都没测到；"
        "见 invalid_round_reason() 的说明）",
    )
    p.add_argument("--out", default="packaging/eval/ab_terms.json")
    args = p.parse_args()

    conds = [c.strip() for c in args.conditions.split(",") if c.strip()]
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    domain_terms = load_domain_terms(args.domain)
    custom = [t.strip() for t in args.custom_terms.split(",") if t.strip()]
    # "@zh" / "@zh-origin" 是内置词表的简写：命令行塞几十个中文词又长又容易因
    # 编码出问题，用哨兵值代替，实际清单仍会完整写进报告，可追溯。
    #
    # ⚠️ 召回目标（`cn_terms`）必须跟着**注入内容**走，不能固定成 CHINESE_TERMS。
    #    第一组就是这么错的：注入的是 41 个英文短语，召回目标却统计 6 个 AI 术语
    #    → 命中数四格全 0，表格看着"术语一个都没救回来"，其实两个清单毫无交集，
    #    这个 0 什么也没证明。这里改成：用哪份词表注入，就用哪份词表算召回。
    cn_terms = CHINESE_TERMS.get(args.domain, []) + COMMON_TERMS
    sentinel = custom[0] if len(custom) == 1 and custom[0] in CUSTOM_TERM_SENTINELS else None
    if sentinel:
        custom = CUSTOM_TERM_SENTINELS[sentinel](args.domain)
        cn_terms = list(custom)      # 召回目标 = 实际注入的清单

    print(f"目标英文术语（产品术语表 · {args.domain}）: {len(domain_terms)} 个")
    if sentinel:
        print(f"自定义术语来源                           : 哨兵 {sentinel}")
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

        # ---- 无效轮次守卫（H4）：结构上不可用的报告不许进对照表 ----
        # 实测来历：英文测试集 `short.json` 那轮 8 段 → **0 条字幕**、
        # 每格 WER 恒 1.0，却照样被写进了历史对照表（见 WER_REPORT_PLAN §3.1）。
        # 判据与「为什么不用 verdict.passed」见 `invalid_round_reason()`。
        why = invalid_round_reason(runs[cond].get("summary", {}))
        if why and not args.allow_invalid:
            raise SystemExit(
                f"条件 [{cond}] 这一轮**结构不可用**：{why} —— 已中止。\n"
                f"  · 此时参考全文会被整块 delete，WER/CER 恒等于 1.0，"
                f"表里看着像「识别很差」，其实什么都没测到。\n"
                f"  · 它一旦进对照表，就会以「术语注入把 WER 从 1.0 降到 0.8」这种形态"
                f"产出**完全虚假**的结论。\n"
                f"  · 先查为什么没出字幕（模型没加载？音频格式？引擎异常？），再重跑。\n"
                f"  确实要观察这种轮次本身时，加 --allow-invalid 继续。"
            )

        # 下面两条**只警告不中止**：它们不影响「这一轮算过没有」，
        # 只影响「这一轮能不能用来比准确率」。静默吞掉它们同样危险 ——
        # 无参考文本时 WER 列会全是 `-`，而 passed=False 的原因可能藏在四格交错输出里。
        v = runs[cond].get("verdict") or {}
        if v.get("passed") is False:
            failed = [n for n in (v.get("notes") or []) if str(n).startswith("✗")]
            print(
                f"  ⚠ 条件 [{cond}] verdict.passed=False（**不中止**，RTF≥1 等如实记录也会判 False）："
                + ("；".join(failed[:3]) if failed else "（无 ✗ 条目）"),
                flush=True,
            )
        if (runs[cond].get("summary", {}).get("withReference") or 0) == 0:
            print(
                f"  ⚠ 条件 [{cond}] 没有任何参考文本 —— WER/CER 未评估，"
                f"本格只有实时性/稳定性数据，**不可与其它格的准确率并列比较**",
                flush=True,
            )

    def summary(cond):
        return runs[cond].get("summary", {})

    def per_file(cond, field, default=None):
        files = runs[cond].get("results", [])
        vals = [f.get(field) for f in files if "error" not in f]
        vals = [v for v in vals if v is not None]
        return vals[0] if vals else default

    names = list(runs.keys())

    # ---- 被测输入对拍：唯一变量真的改变了喂给模型的提示词吗？ ----
    # 🔴 实测踩过：修复代码已提交，但 harness 漏传 `language` → 两个代码锚点下发的
    # 提示词 md5 **逐字节相同** → 四格 CER 的差异全是运行波动，却被当成「修复生效」
    # 写进了报告。所以每次对照先打印这张指纹表：
    # **md5 相同的条件，其被测输入完全相同**，它们之间的差异不可归因于配置。
    print("\n" + "=" * 90)
    print("被测输入对拍（术语提示词指纹）—— md5 相同 = 喂给模型的输入完全相同")
    print("=" * 90)
    fingerprints: dict[str, str] = {}
    for c in names:
        cfg = runs[c].get("config", {})
        md5 = cfg.get("termPromptMd5") or ""
        fingerprints[c] = md5
        chars = summary(c).get("termPromptChars")
        print(
            f"  [{c:<14}] md5 {(md5[:16] if md5 else '(空 · 未注入术语层)'):<20}"
            f" 术语语种 {str(cfg.get('termLanguage') or '-'):<6}"
            f" 提示词 {str(chars if chars is not None else '-'):<5} 字符"
            f" 分层 {cfg.get('termPromptSections') or '-'}"
        )
    # ⚠️ 只能覆盖「组内」这一半：若每个条件注入的内容**完全一致**（且确实有内容），
    #    那这组对照没有可观测的输入变量。**跨运行**的「唯一变量没引起输入变化」
    #    它看不到（实测踩过：两个代码锚点 md5 逐字节相同却报「验证通过」）——
    #    那种情况靠三样东西挡：① 改开关后 grep 全部调用点；② 产物里的
    #    `config.termPromptMd5` 字段可跨运行对拍；③ 跑之前先证变量真的变了。
    vals = list(fingerprints.values())
    if len(names) > 1 and len(set(vals)) == 1 and vals[0]:
        print(
            "  ⚠️ 警告：所有条件注入的术语提示词**完全相同**（md5 一致且有内容）——\n"
            "     本组没有可观测的输入变量，差异只能反映运行波动。\n"
            "     跨运行对拍请看各产物 `config.termPromptMd5` 字段。"
        )

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
        # 下面两行是「注入的到底是什么」的紧凑指纹：`phrases` 条目数直接暴露
        # 「中文课有没有被塞英文短语层」，比字符总数更能说明问题。
        (
            "术语层 phrases 条目",
            lambda c: (runs[c].get("config", {}).get("termPromptSections") or {}).get("phrases"),
            "lower",
        ),
        ("术语语种", lambda c: runs[c].get("config", {}).get("termLanguage"), "lower"),
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
