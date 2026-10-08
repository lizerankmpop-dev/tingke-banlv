#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""预校对：批量修正高置信同音误识别 + 标出「无法唯一确定」的碎片

为什么要有这一步
----------------
ASR 草稿里两类错误性质完全不同，处理方式也必须不同：

* **同音/形近误识别**（`会图`→`绘图`、`坐标戏`→`坐标系`、`离秘`→`厘米`）：
  候选**唯一**，上下文能定死。批量替换既快又不会引入新错误。
* **无法唯一确定的碎片**（`铁针头`、`1D,1Alien,為AKS-B和C为Y`）：
  没有任何一个候选能靠上下文定死。这时候唯一正确的动作是 **不猜** ——
  原样保留，前后插 `【?】`，留给人工对着音频听。

为什么绝不能顺手「读通顺了就改」
--------------------------------
逐字稿是 P3 的**评估基准**。任何一处脑补都会让后续 CER / 术语命中率失去意义，
而且**不会报错** —— 改错了只会让基准悄悄偏离真实录音，然后把偏差记到
「ASR 在真实网课上的表现」头上。所以本工具只承认两种**动作**：`fix` 与 `suspect`。
不做：润色、改断句/分段、补字、删口头语、删除重复（重复循环只在清单里登记）。

第三种 `kind` 是 `note`：**它不改文本**，只留一条记录。两种用途 ——
① 标注「这里是 ASR 重复循环，不是新内容」；② 人工裁决后确认「没有任何候选能
被锁定」的碎片（终态留白：我不猜，也不谎称校对过）。note 是**终态**，
不是「还没处理」，所以它不出现在存疑清单里。

为什么修正表是**外置的 TSV 而不是写死在代码里**
----------------------------------------------
它是**数据**：每换一份素材就要重写一遍，而且人需要逐行否决。
外置后「复核 → 修正 → 重跑」是编辑表格，不是改代码；人工把某行的 `kind`
改成 `suspect` 就等于否决这条替换，不必动脚本。

用法
----
    <venv>/python.exe scripts/preproof_transcript.py \\
        --draft <stem>.draft.json --corrections <name>.corrections.tsv

产出（落在 --out-dir，默认与 draft 同目录）
    <base>.preproof.json          带预校对文本 + 变更/存疑记录
    <base>.preproof.txt           预校对文稿（可直接读）
    <base>.preproof.review.html   同款校对页，`【?】` 原样可见，可边听边改
    <base>.preproof.changes.tsv   全部替换清单（原文 → 修正 → 依据 → 命中处数）
    <base>.preproof.suspects.md   存疑片段清单（段号 + 时间码 + 上下文 + 候选）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from review_page import render_review_html  # noqa: E402

# 存疑标记。用「问号夹在方括号里」而不是颜色：txt/json/srt 都是纯文本，
# 换行/查表都不该依赖富文本。页面里靠肉眼扫 `【?】` 即可。
SUSPECT_OPEN = "【?"
SUSPECT_CLOSE = "?】"

KINDS = ("fix", "suspect", "note")

# 匹配一个存疑标记并捕获其中片段。`.*?` 非贪婪 + `?】` 收尾，保证嵌套标记
# （本该被 validate() 挡掉）即使漏进来也能被切成多个片段而不是一坨。
_MARKER_RE = re.compile(
    re.escape(SUSPECT_OPEN) + "(.*?)" + re.escape(SUSPECT_CLOSE), re.S)


@dataclass(frozen=True)
class Correction:
    kind: str
    pattern: str
    replacement: str = ""
    note: str = ""


@dataclass
class Applied:
    correction: Correction
    count: int = 0
    segments: list = field(default_factory=list)


# ---------- 修正表解析 / 校验 ----------

def parse_corrections(text: str) -> list[Correction]:
    """解析 TSV：`kind <TAB> pattern <TAB> replacement <TAB> note`。

    `pattern` 与 `replacement` **原样保留**（不 strip）—— 它们可能刻意包含标点或空格，
    一旦 strip 就会静默匹配不上。只对 `kind` 与 `note` 去空白。

    首行的 BOM 也必须先去掉：这张表**是给人编辑的**，用 Excel / 记事本存一次就会
    带上 `\\ufeff`。它既不是空白也不以 `#` 开头，会让第 1 行注释被当成数据行而报
    「只收到 1 列」——报错信息还指向无关的地方。（读盘侧同样用 `utf-8-sig`，双保险。）

    **行尾则刻意不处理**（别在这里加 `.replace("\\r", "")`）：`str.splitlines()` 已把
    `\\r\\n` 与孤立 `\\r` 都当作换行消费掉，`read_text` 还会先做一次通用换行转换 ——
    两层都在，`\\r` 根本到不了字段里。实测：加上的 `\\r` 归一是**死代码**，
    连配套的「CRLF 用例」在没有它时也是绿的（变异探针判不出来）。
    """
    text = text.lstrip("\ufeff")
    out: list[Correction] = []
    seen: dict[str, int] = {}
    for lineno, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        parts = raw.split("\t")
        if len(parts) < 2:
            raise ValueError(
                f"第 {lineno} 行：至少要有 kind 与 pattern 两列，且用制表符分隔 "
                f"（收到 {len(parts)} 列）"
            )
        kind = parts[0].strip()
        pattern = parts[1]
        replacement = parts[2] if len(parts) > 2 else ""
        note = parts[3].strip() if len(parts) > 3 else ""
        if kind not in KINDS:
            raise ValueError(f"第 {lineno} 行：kind 只能是 {KINDS}，收到 {kind!r}")
        if not pattern:
            raise ValueError(f"第 {lineno} 行：pattern 不能为空")
        if kind == "fix" and pattern == replacement:
            raise ValueError(f"第 {lineno} 行：fix 的原串与替换串相同（空操作，删掉这行）")
        if pattern in seen:
            raise ValueError(
                f"第 {lineno} 行：pattern {pattern!r} 与第 {seen[pattern]} 行重复 —— "
                f"重复行会按顺序各执行一次，结果取决于行序，属于隐式行为"
            )
        seen[pattern] = lineno
        out.append(Correction(kind=kind, pattern=pattern,
                             replacement=replacement, note=note))
    return out


def validate(corrections: list[Correction]) -> list[str]:
    """返回问题清单（空 = 通过）。

    唯一被禁止的形状是**两个 suspect 互相包含**：短的会嵌进长的标记里，
    变成 `【?】【?】片段?】?】` —— 页面上一眼看不出层次，人工复核时无法判断
    该听哪一截。这属于必须由人先解决掉的歧义，不能靠程序"兼容"掉。
    """
    problems: list[str] = []
    suspects = [c.pattern for c in corrections if c.kind == "suspect"]
    for i, a in enumerate(suspects):
        for b in suspects[i + 1:]:
            if a in b or b in a:
                problems.append(f"suspect 模式互相包含：{a!r} 与 {b!r}（会嵌套出双重标记）")
    return problems


def order_for_apply(corrections: list[Correction]) -> list[Correction]:
    """**先全部 fix、再全部 suspect**；组内长 pattern 优先。

    两个顺序都不能省：

    * `suspect` 必须跑在 `fix` **之后**，因为存疑模式是按**修正后**的文本写的
      （例：先 `布可 → Book`，存疑片段才会是 `Book厅的这一打开了` 里的后半截）。
      反过来会让这些行一条都匹配不上 —— 而匹配不上**不报错**。
    * 组内长优先，否则 `会致成图线` 会被短模式 `会致` 先命中，删出残字。
      实测教训：前缀匹配只要漏掉长变体就会留下没说出口的残字，且**不报错**。

    ⚠️ 别以为这里 key 的 `0 if fix else 1` 分量就是「fix 先跑」的保证 ——
    `apply_corrections` 是把结果**再拆成两组**（`fixes` / `suspects`）分别跑的，
    它压根不看这个分量；删掉 `0/1` 分量，`apply_corrections` 的行为**一模一样**。
    这个分量管的是：① 本函数自身的契约（外部直接调用时也拿得到正确次序）
    ② `apply_corrections` 输出 `applied` 的排序基准 `rank`（决定 changes.tsv 行序，
    可对拍）。真正守「fix 整体先于 suspect」的是 `apply_corrections` 里的分组 + 两段循环。
    """
    def key(c: Correction) -> tuple:
        return (0 if c.kind == "fix" else 1, -len(c.pattern), c.pattern)

    return sorted((c for c in corrections if c.kind in ("fix", "suspect")), key=key)


def has_markers(text: str) -> bool:
    return SUSPECT_OPEN in text or SUSPECT_CLOSE in text


def apply_corrections(text: str, corrections: list[Correction]) -> tuple[str, list[Applied]]:
    """对**一段**文本先跑到 fix 不动点，再打 suspect 标记。

    为什么 fix 要循环到不动点，而不是「长优先跑一遍」
    --------------------------------------------------
    一条 fix 的**替换串**可能正好造出另一条 fix 的 pattern，而组内是「长优先」——
    长的会**先**跑，此时目标还不存在，于是它静默不生效。实测：
    `布可 → Book` 与 `Book厅的这一 → Book这一个`，后者更长、先跑、找不到目标。
    只跑一遍时唯一的兜底是「零命中就报错」，但报错信息只会说「这行没命中」，
    **不会告诉你是排序问题**，作者很可能去改 pattern 的写法而把真因盖住。

    跑成不动点后，表达同一件事的两种写法（对着原始文本写 / 对着中间态写）都对，
    规则表不再依赖作者的排序直觉。循环有上界：真出现 `A→B` 且 `B→A` 这种
    互相咬合的表会**报错**而不是无声地转圈。

    suspect 仍在 fix 全部结束之后跑（理由见 `order_for_apply`），且只跑一遍：
    它只负责加标记，加完不会产生新的 suspect 目标。
    """
    if has_markers(text):
        raise ValueError("输入文本已含 【?】 标记：应只对原始草稿执行一次预校对")
    cur = text
    ordered = order_for_apply(corrections)
    fixes = [c for c in ordered if c.kind == "fix"]
    suspects = [c for c in ordered if c.kind == "suspect"]
    applied: dict[str, Applied] = {}

    limit = len(fixes) + 8
    for _pass in range(limit):
        changed = False
        for c in fixes:
            if c.pattern not in cur:
                continue
            n = cur.count(c.pattern)
            cur = cur.replace(c.pattern, c.replacement)
            rec = applied.setdefault(c.pattern, Applied(correction=c))
            rec.count += n
            changed = True
        if not changed:
            break
    else:
        raise ValueError(
            f"fix 阶段跑了 {limit} 轮仍未收敛 —— 规则表里存在互相咬合的替换"
            f"（例如 A→B 且 B→A），会无声地转圈，请先拆掉")

    for c in suspects:
        if c.pattern not in cur:
            continue
        n = cur.count(c.pattern)
        cur = cur.replace(c.pattern, f"{SUSPECT_OPEN}{c.pattern}{SUSPECT_CLOSE}")
        rec = applied.setdefault(c.pattern, Applied(correction=c))
        rec.count += n

    # 顺序按规则表的稳定次序输出（dict 插入序 = 首次命中序，会随段落漂移）；
    # 这里改成按 order_for_apply 的次序，保证 changes.tsv 可对拍。
    rank = {c.pattern: i for i, c in enumerate(ordered)}
    return cur, sorted(applied.values(), key=lambda a: rank[a.correction.pattern])


def extract_suspects(text: str) -> list[str]:
    """从**已完成标记**的文本里取出全部存疑片段（按出现顺序，同一片段可重复出现）。

    必须在**输出文本**上取，不能拿原始草稿「按模式 `in` 一遍」。两个理由：

    * 有些模式要等 `fix` 跑完才存在（`布可厅` → `Book厅`），在原文里永远查不到；
    * 同一段里出现多次时，「模式在不在」只能给出 1，而标记实际有 N 个。

    两种情形都会让**存疑清单静默少列条目**。实测教训：第一版按原文取，
    清单报「共 46 处」，而输出文本里真实标记有 55 处，且 `Book厅` 这一条
    在清单里**整个消失** —— 清单少列 = 人工复核漏听，且没有任何报错。
    """
    return _MARKER_RE.findall(text)


# ---------- 主流程 ----------

def preproof(draft: dict, corrections: list[Correction]) -> tuple[dict, list[Applied]]:
    """对整个 draft 执行预校对，返回 (新 draft, 命中的替换汇总)。"""
    segments = draft["segments"]
    summary: dict[tuple, Applied] = {}
    new_segments = []
    for seg in segments:
        text, applied = apply_corrections(seg.get("text", ""), corrections)
        for a in applied:
            key = (a.correction.kind, a.correction.pattern)
            rec = summary.setdefault(key, Applied(correction=a.correction))
            rec.count += a.count
            rec.segments.append(seg.get("index"))
        new_seg = dict(seg)
        new_seg["text"] = text
        # 保留原始识别文本，便于日后核对「改动前是什么」
        new_seg["rawText"] = seg.get("text", "")
        # 从**打完标记的输出文本**里回读，而不是拿原文按模式匹配 —— 理由见 extract_suspects
        new_seg["suspects"] = extract_suspects(text)
        new_segments.append(new_seg)

    info = dict(draft.get("info", {}))
    texts = [s["text"] for s in new_segments if s["text"]]
    info["paragraphs"] = len(texts)
    info["chars"] = sum(len(t) for t in texts)
    info["preproof"] = {
        "fixes": [{"pattern": a.correction.pattern, "replacement": a.correction.replacement,
                   "count": a.count, "segments": a.segments, "note": a.correction.note}
                  for a in summary.values() if a.correction.kind == "fix"],
        "suspects": [{"pattern": a.correction.pattern, "count": a.count,
                      "segments": a.segments, "note": a.correction.note}
                     for a in summary.values() if a.correction.kind == "suspect"],
    }
    return {"info": info, "segments": new_segments}, list(summary.values())


def write_suspects_md(base: str, segments: list[dict], corrections: list[Correction]) -> str:
    """存疑片段清单（Markdown）：按段号排序，带时间码与上下文，便于对照音频。

    「0 处」不是边缘情况，而是**裁决完成后的终态**：原 suspect 已逐行消化成
    `fix`（有确定候选）与 `note`（定不死、原样保留）。此时若还照抄那句
    「去把 `【?】` 改成听清的词」，就是把读者指向一个**已经不存在的动作**，
    而真正需要他知道的信息 ——「哪些碎片仍然定不死」—— 反而在清单里看不见。
    所以零存疑时改印终态说明 + 未锁定碎片表（这就是「改动机制后必须搜遍所有
    描述该机制的文案」那条教训在产物侧的应用）。
    """
    notes = {c.pattern: c.note for c in corrections}
    total = sum(len(s.get("suspects") or []) for s in segments)
    n_seg = sum(1 for s in segments if s.get("suspects"))
    lines = [f"# 存疑片段清单 · {base}", ""]

    if total == 0:
        lines += [
            "**本素材已无待核验的 `【?】` 标记。**",
            "",
            "这不是「都听清了」，而是「不猜」：`【?…?】` 是过程标记，裁决后逐行消化成",
            "两种终态 —— 得到确定候选的改成 `fix`（见 `.preproof.changes.tsv`），",
            "仍无候选可锁定的改成 `note`（**文本一字不动**，不确定性记录在修正表与",
            "`.preproof.rulings.md` 里，正文不会因此被脑补）。",
            "",
        ]
        kept = [c for c in corrections if c.kind == "note"]
        if kept:
            lines += [f"仍定不死、按原样保留的碎片共 **{len(kept)}** 处：", "",
                      "| 片段 | 备注 |", "|---|---|"]
            lines += [f"| `{c.pattern}` | {c.note} |" for c in kept]
            lines.append("")
        return "\n".join(lines)

    lines += [
        "规则：**原样保留，不猜**。每一条都要在 `review.html` 里对着音频确认——",
        "确认后在页面文本框中把 `【?】…【?】` 改成听清的词（连标记一起去掉）。",
        "",
        f"共 {total} 处，分布在 {n_seg} 段。",
        "",
        "| 段号 | 时间码 | 片段 | 上下文 | 备注与候选 |",
        "|---|---|---|---|---|",
    ]
    def fmt(sec: float) -> str:
        m, s = divmod(sec, 60)
        return f"{int(m)}:{s:04.1f}"

    for seg in segments:
        pats = seg.get("suspects") or []
        if not pats:
            continue
        text = seg["text"]
        # 同一片段在一段里可能出现多次（如幻觉循环里的 `招呼` ×5）。
        # 表格里只列一行、用 `×N` 标注次数：重复行会给复核者 5 行**完全相同**的
        # 上下文，读起来像 5 个不同位置，反而更难判断哪里该听。
        for pat, n in Counter(pats).items():
            marked = f"{SUSPECT_OPEN}{pat}{SUSPECT_CLOSE}"
            i = text.find(marked)
            ctx = text[max(0, i - 14): i + len(marked) + 14].replace("|", "\\|")
            label = f"`{pat}`" + (f" ×{n}" if n > 1 else "")
            lines.append(
                f"| {seg['index']} | {fmt(seg['audioStart'])}–{fmt(seg['audioEnd'])} "
                f"| {label} | …{ctx}… | {notes.get(pat, '')} |"
            )
    lines.append("")
    return "\n".join(lines)


def write_changes_tsv(applied: list[Applied]) -> str:
    lines = ["kind\tpattern\treplacement\tcount\tsegments\tnote"]
    for a in applied:
        c = a.correction
        segs = ",".join(str(s) for s in a.segments)
        lines.append(f"{c.kind}\t{c.pattern}\t{c.replacement}\t{a.count}\t{segs}\t{c.note}")
    return "\n".join(lines) + "\n"


def unmatched_patterns(corrections: list[Correction], applied: list[Applied]) -> list[Correction]:
    """一张都没命中的 `fix`/`suspect` 行。

    为什么必须报出来：修正表里的模式**写错一个字就永远匹配不上**，
    而工具照样跑完、照样打印「替换 N 类」—— 少的那条没人知道。
    这是本模块最需要防的失败形态：**静默失效**。

    注意不能拿原始草稿去 `in` 一遍了事：`suspect` 模式是按**修正后**的文本写的
    （见 `order_for_apply`），在原始文本里本来就可能查无此串。
    判据只能是「实际执行时命中次数」，所以这里吃的是 `applied` 而不是文本。
    """
    hit = {a.correction.pattern for a in applied if a.count}
    return [c for c in corrections if c.kind in ("fix", "suspect") and c.pattern not in hit]


def main() -> int:
    ap = argparse.ArgumentParser(description="ASR 草稿预校对（高置信批量修正 + 存疑标红）")
    ap.add_argument("--draft", required=True, help="<stem>.draft.json")
    ap.add_argument("--corrections", required=True, help="修正表 TSV")
    ap.add_argument("--out-dir", default="", help="输出目录（默认与 draft 同目录）")
    ap.add_argument("--tag", default="preproof", help="产物文件名中缀")
    ap.add_argument("--check-only", action="store_true", help="只校验修正表，不写产物")
    args = ap.parse_args()

    draft_path = Path(args.draft)
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    corrections = parse_corrections(Path(args.corrections).read_text(encoding="utf-8-sig"))

    problems = validate(corrections)
    if problems:
        print("修正表校验未通过：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2

    n_fix = sum(1 for c in corrections if c.kind == "fix")
    n_sus = sum(1 for c in corrections if c.kind == "suspect")
    n_note = sum(1 for c in corrections if c.kind == "note")
    print(f"修正表：fix {n_fix} 条 / suspect {n_sus} 条 / note {n_note} 条")

    new_draft, applied = preproof(draft, corrections)

    missing = unmatched_patterns(corrections, applied)
    if missing:
        print("以下模式一次都没命中 —— 多半是写错了字，属于「写了但永远不生效」的死规则：",
              file=sys.stderr)
        for c in missing:
            print(f"  - [{c.kind}] {c.pattern!r}", file=sys.stderr)
        return 3

    # 只在 fix 跑完才存在的 suspect（例：`布可厅` → `Book厅`）。单独点出来是因为
    # 这类模式在**原始草稿里查不到**，人工抽查修正表时最容易误判成「写错了没生效」，
    # 而它恰恰是靠 fix 才成立的（见 order_for_apply）。
    raw_all = "\n".join(s.get("text", "") for s in draft["segments"])
    late = [a.correction for a in applied
            if a.correction.kind == "suspect" and a.correction.pattern not in raw_all]
    if late:
        print(f"其中 {len(late)} 条存疑模式的匹配位置由 fix 产生（原文里查不到，属预期）：")
        for c in late:
            print(f"  - {c.pattern!r}")

    if args.check_only:
        print(f"--check-only：校验通过（含「{n_fix + n_sus} 条模式全部命中」），未写任何产物")
        return 0

    out = Path(args.out_dir) if args.out_dir else draft_path.parent
    out.mkdir(parents=True, exist_ok=True)

    base = draft_path.name
    if base.endswith(".draft.json"):
        base = base[: -len(".draft.json")]
    elif base.endswith(".json"):
        base = base[: -len(".json")]
    stem = f"{base}.{args.tag}"

    (out / f"{stem}.json").write_text(
        json.dumps(new_draft, ensure_ascii=False, indent=2), encoding="utf-8")
    texts = [s["text"] for s in new_draft["segments"] if s["text"]]
    (out / f"{stem}.txt").write_text("\n".join(texts) + "\n", encoding="utf-8")
    (out / f"{stem}.changes.tsv").write_text(write_changes_tsv(applied), encoding="utf-8")
    (out / f"{stem}.suspects.md").write_text(
        write_suspects_md(stem, new_draft["segments"], corrections), encoding="utf-8")

    # 校对页：<audio src> 必须是**音频真实文件名**，但 info["audio"] 兼作
    # localStorage 键（模板只比条数就还原），所以给它加后缀做隔离 ——
    # 否则这个页面会把「草稿页」保存过的编辑静默当成自己的校对稿还原回来。
    page_info = dict(new_draft["info"])
    page_info["audio"] = f"{stem}.wav"
    (out / f"{stem}.review.html").write_text(
        render_review_html(stem, draft["info"]["audio"], new_draft["segments"], page_info),
        encoding="utf-8")

    info = new_draft["info"]
    print(f"段落 {info['paragraphs']}｜{info['chars']} 字"
          f"｜替换 {len(info['preproof']['fixes'])} 类"
          f"｜存疑 {sum(1 for s in new_draft['segments'] for _ in s.get('suspects') or [])} 处")
    for k in ("json", "txt", "changes.tsv", "suspects.md", "review.html"):
        print(f"  {k:12s} → {out / f'{stem}.{k}'}")
    print("下一步：打开 review.html，对着音频把每一处 【?】…【?】 改成听清的词（连标记一起删），"
          "导出 txt 覆盖为 <同名>.txt，再跑 scripts/check_transcript.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
