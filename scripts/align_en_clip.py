#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""英文格 · 时间轴对齐器（V7 · 多讲次扩展）

做什么
------
给定「某讲的 OCW 官方 VTT」+「B 站该 P 的 ASR 草稿」，算出：
1. **偏移量**（B 站 P 的 0:00 对应 OCW 的第几秒）—— 27+ 点锚点拟合（中位数 + 标准差）
2. **语义边界校验**（首句 / 末句）—— 决定切窗
3. **切窗** `[clip_start, clip_end)`（OCW 绝对时间）并产出候选参考稿

🔴 为什么必须**逐格独立对齐**
----------------------------
B 站是**搬运版**，每个 P 的片头长度**可能不同**（P1 实测 5 秒）。
而 OCW 与 B 站的**讲次编号也不是一一对应**（OCW 多一讲、还把神经网络拆成 12A/12B），
所以**不能沿用 P1 算出的偏移**。
⇒ 每格都独立做锚点拟合 + 语义边界校验。

🔴 **不能用「WER 最小」选窗口**
------------------------------
那是**作弊解**：多包一句能稀释分母。实测 P1 的窗口从 125.1s 挪到 121.0s，
WER 从 0.1421 降到 0.1392 —— 但那是**多包含了一句**，不是对齐更准。
正确判据是**语义边界**：切片首句必须在窗内、上一句必须在窗外。

用法
----
    <venv>/python.exe scripts/align_en_clip.py \\
        --vtt <lec.vtt> --draft <stem>.draft.json \\
        --clip-start 0 --clip-end 630 --out-dir <dir> --stem <name>
"""
from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from build_en_reference import parse_vtt, slice_cues  # noqa: E402


def _load_eval():
    spec = importlib.util.spec_from_file_location("_ev", HERE / "eval_asr_quality.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def token_times(cues: list[dict]) -> list[float]:
    """token 序号 → 该 token 所属 cue 的起始时间"""
    out = []
    for c in cues:
        n = len(c["text"].split())
        out.extend([c["start"]] * n)
    return out


def same_source_ratio(cues, draft, n: int = 3) -> float:
    """🔴 **素材同一性预检**：ASR 草稿的 n-gram 有多少能在官方稿里找到

    为什么必须有这一步（2026-10-27，P12 踩出来的）
    ----------------------------------------------
    P12（B 站「12.-学习：神经网络、反向传播」）↔ OCW Lecture 12A（Neural Nets）
    —— **标题与主题都吻合，但重合率只有 12.8%** ⇒ **不是同一段视频**
    （很可能是不同剪辑版本/不同来源的同名讲次）。

    表现是：自动对齐的三种方法**全都失败**，而手工扫 `--offset` 也给不出
    合理结果（所有 offset 的词级差都 ≈0.96）⇒ 我在那格上耗了大量时间，
    根因却是「素材根本对不上」，**不是算法不够好**。

    ⇒ **对齐之前先花 1 秒验「是不是同一素材」**，比调一小时算法划算得多。

    实测基准（MIT 6.034）：
    | 格 | 3-gram 重合率 | 判定 |
    |---|---|---|
    | P4（Lec-4） | 60.8% | ✅ 同一素材 |
    | P20（Lec-21） | 65.2% | ✅ 同一素材 |
    | **P12（Lec-12A）** | **12.8%** | ❌ **不是同一素材** |

    阈值：>45% 值得做对齐；25~45% 可疑；<25% 直接判「不同源」。
    """
    from collections import Counter

    full = " ".join(c["text"] for c in cues).lower()
    grams: Counter = Counter()
    for seg in draft["segments"]:
        w = seg["text"].lower().split()
        for i in range(len(w) - n + 1):
            grams[" ".join(w[i:i + n])] += 1
    if not grams:
        return 0.0
    hit = sum(1 for g in grams if g in full)
    return hit / len(grams)


def fit_by_ngram(cues, draft, n: int = 4) -> tuple[list[float], float, float, float]:
    """🔴 **n-gram 索引 + 投票**（2026-10-26，P12 逼出来的最终定位法）

    为什么必须换掉 `find_longest_match`
    -----------------------------------
    `find_longest_match` **只返回一个**最长匹配（全稿唯一）。在 7000 token 的
    讲次里，**别的段落的偶然匹配往往更长**，于是真正对齐的那一段被挤掉。
    实测 P12：全稿搜只得到 1 个锚点（1459.50，偶然），真实值 ≈ 1493。

    n-gram 法的优势
    --------------
    ① 建一个 `4-gram → 出现位置` 的倒排索引；
    ② 对**每一段**的每个 4-gram，把它的所有出现位置都投一票；
    ③ 投票峰值就是这一段的最佳匹配位置（**每段独立**，互不挤占）；
    ④ 各段算出的偏移再做一次**众数投票**（去掉个别离群段）。

    这是标准的 n-gram 对齐做法，比「最长公共子串」稳健得多。
    """
    from collections import Counter, defaultdict

    full = " ".join(c["text"] for c in cues)
    tokens = full.split()
    tt = token_times(cues)

    idx: dict[tuple, list[int]] = defaultdict(list)
    for i in range(len(tokens) - n + 1):
        idx[tuple(tokens[i:i + n])].append(i)

    seg_offs = []
    for seg in draft["segments"]:
        h = seg["text"].split()
        if len(h) < n + 2:
            continue
        votes: Counter = Counter()
        for g in h[:24]:
            key = tuple(g[i:i + n] for i in range(len(g) - n + 1))
            for pos in idx.get(key, ()):
                votes[pos] += 1
        if not votes:
            continue
        # 峰值取「相邻位置聚合」后的最高点（一个词可能错一位）
        agg: Counter = Counter()
        for pos, v in votes.items():
            agg[pos] += v
        best_pos, best_v = agg.most_common(1)[0]
        if best_v < 2:
            continue
        seg_offs.append(tt[best_pos] - seg["audioStart"])

    if not seg_offs:
        return [], 0.0, 0.0, 0.0
    buckets: dict[float, int] = {}
    for o in seg_offs:
        k = round(o * 2) / 2
        buckets[k] = buckets.get(k, 0) + 1
    mode = max(buckets, key=lambda k: buckets[k])
    share = buckets[mode] / len(seg_offs)
    near = [o for o in seg_offs if abs(o - mode) <= 4.0]
    sd = statistics.pstdev(near) if len(near) > 1 else 0.0
    return seg_offs, mode, share, sd


def fit_by_boundaries(cues, draft, clip_len: float) -> tuple[list[float], float, float, float]:
    """🔴 **首尾双重定位**（2026-10-26，P12 逼出来的最终解法）

    为什么必须这么做
    ----------------
    前两版（单点最长公共子串 / 全稿搜 + 受限窗口精搜）在 P12 上都失败：
      - 全稿搜：7000 token 里必然撞上偶然匹配，唯一锚点是错的
      - 粗定位+精搜：粗定位用 3 词锚点，召回太高 ⇒ 猜测本身是错的
    手工核对确认 P12 的真实偏移 ≈ **1493**（官方稿 `1500.70s` =
    "So this is trick number two" ↔ ASR 的 "we'll call this insight number two"）。

    核心洞察：**切片的边界本身就是最强的约束**
    ------------------------------------------------
    ASR 草稿的第 0 段一定对应官方稿的**开头**，最后一段对应官方稿的**结尾**，
    而**两者的间距已知**（= 切片时长 clip_len）。

    ⇒ 对每个「首段匹配位置 a」与「尾段匹配位置 b」，若
    `|（b − a） − clip_len| ≤ 20 s`，则 `offset = a − 首段起点` 是一个**合法候选**。
    **两个独立锚点 + 已知间距**这个三重约束，几乎不可能被偶然满足
    （一次偶然匹配要同时满足两个位置才成立）。

    返回 (候选偏移列表, 众数, 众数占比, 标准差)
    """
    full = " ".join(c["text"] for c in cues)
    tokens = full.split()
    tt = token_times(cues)
    segs = draft["segments"]
    if len(segs) < 3:
        return [], 0.0, 0.0, 0.0

    def positions(head: str, min_anchor: int) -> list[float]:
        h = head.split()
        if len(h) < 6:
            return []
        hits = []
        # 用 find_longest_match 反复推进：找出所有 ≥min_anchor 的匹配位置
        used = 0
        while used < len(tokens) - min_anchor:
            sm = difflib.SequenceMatcher(None, tokens[used:], h, autojunk=False)
            mt = sm.find_longest_match(0, len(tokens) - used, 0, len(h))
            if mt.size < min_anchor:
                break
            hits.append(tt[used + mt.a])
            used += used + mt.a - (used + mt.a) + mt.size + 1
            if used >= len(tokens):
                break
        return hits

    first_txt = " ".join(segs[0]["text"].split()[:14])
    # 🔴 尾锚点用**倒数第 3 段**而不是最后一段：
    #    最后一段常常是残句（P4 尾段只有 "There you go. Now, if we were content."），
    #    短句在官方稿里找不到 5 词匹配 ⇒ 首尾定位直接无解（P4/P12 实测）。
    #    倒数第 3 段是完整句子，匹配得上。
    tail_idx = max(0, len(segs) - 3)
    last_txt = " ".join(segs[tail_idx]["text"].split()[-14:])
    a_list = positions(first_txt, 4)
    b_list = positions(last_txt, 4)
    if not a_list or not b_list:
        return [], 0.0, 0.0, 0.0
    tail_start = segs[tail_idx]["audioStart"]
    cands = []
    for a in a_list:
        for b in b_list:
            # 首尾距离：首段起点 → 尾锚点段起点，应约等于 (tail_start - 首段起点)
            want = tail_start - segs[0]["audioStart"]
            if abs((b - a) - want) <= 20.0:
                cands.append(a - segs[0]["audioStart"])
    if not cands:
        return [], 0.0, 0.0, 0.0
    buckets: dict[float, int] = {}
    for o in cands:
        k = round(o * 2) / 2
        buckets[k] = buckets.get(k, 0) + 1
    mode = max(buckets, key=lambda k: buckets[k])
    share = buckets[mode] / len(cands)
    near = [o for o in cands if abs(o - mode) <= 3.0]
    sd = statistics.pstdev(near) if len(near) > 1 else 0.0
    return cands, mode, share, sd


def fit_offset(cues, draft, guess: float | None = None,
               tol: float = 40.0) -> tuple[list[float], float, float, float]:
    """锚点拟合。返回 (逐点偏移, 众数, 众数占比, 标准差)

    🔴 **根本解法：限制搜索范围**（2026-10-26 补，P12 踩出来的）
    --------------------------------------------------
    `find_longest_match` 是在**全稿**（P12 那个讲次有 888 cue ≈ 7000 token）里找，
    必然撞上偶然匹配。实测 P12：
      - 全稿搜 → 只找到 **1** 个锚点，值 1459.50（是偶然匹配，真实值 ≈ 1493）
      - 手工核对：`1500.70s` = "So this is trick number two" ↔ ASR 的
        "we'll call this insight number two" ⇒ 真实偏移 ≈ 1493
    差异 33 秒 ⇒ **1 个锚点不可信**。

    ⇒ 改成**两轮**：
      ① **粗定位**：全稿搜，只用「出现次数最多的偏移」当猜测（投票）
      ② **精定位**：只在 `[猜测 ± tol]` 的窗口内搜 ⇒ 偶然匹配被排除
    偏移在同一次运行里**本来就该稳定**（同一搬运版的片头长度固定），
    所以「限制在合理范围内搜」是**有依据的约束**，不是作弊。

    🔴 另用**众数**而非中位数：`find_longest_match` 会给离群点，
    离群点能把中位数拉偏（P4 实测 min 433.71 / max 605.42）。
    """
    full = " ".join(c["text"] for c in cues)
    tokens = full.split()
    tt = token_times(cues)

    def search(win_lo: float, win_hi: float, min_anchor: int) -> list[float]:
        lo = next((i for i, t in enumerate(tt) if t >= win_lo), 0)
        hi = next((i for i, t in enumerate(tt) if t > win_hi), len(tt))
        sub = tokens[lo:hi]
        offs = []
        for seg in draft["segments"]:
            h = " ".join(seg["text"].split()[:12]).split()
            if len(h) < 8:
                continue
            sm = difflib.SequenceMatcher(None, sub, h, autojunk=False)
            mt = sm.find_longest_match(0, len(sub), 0, len(h))
            if mt.size >= min_anchor:
                offs.append(tt[lo + mt.a] - seg["audioStart"])
        return offs

    def vote(offs: list[float]) -> tuple[float, float]:
        buckets: dict[float, int] = {}
        for o in offs:
            k = round(o * 2) / 2
            buckets[k] = buckets.get(k, 0) + 1
        mode = max(buckets, key=lambda k: buckets[k])
        return mode, buckets[mode] / max(1, len(offs))

    # ① 粗定位：全稿搜，允许短锚点（3 词）以提高召回
    rough = search(-1e9, 1e9, 3)
    if not rough:
        return [], 0.0, 0.0, 0.0
    g, _ = vote(rough)
    # ② 精定位：限制在猜测附近，要求更长锚点（6 词）
    fine = search(g - tol, g + tol, 6)
    if not fine:
        fine = rough
    mode, share = vote(fine)
    near = [o for o in fine if abs(o - mode) <= 3.0]
    sd = statistics.pstdev(near) if len(near) > 1 else 0.0
    return fine, mode, share, sd


def main() -> int:
    ap = argparse.ArgumentParser(description="英文格时间轴对齐")
    ap.add_argument("--vtt", required=True)
    ap.add_argument("--draft", required=True)
    ap.add_argument("--clip-start", type=float, required=True, help="B 站切片起点（秒）")
    ap.add_argument("--clip-end", type=float, required=True, help="B 站切片终点（秒）")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--stem", required=True, help="输出文件名前缀")
    ap.add_argument("--offset", type=float, default=None,
                    help="**人工确认过**的偏移（OCW绝对 − 切片内相对）。"
                         "给了就跳过自动定位；自动定位在长讲次上不可靠时用这个兜底")
    args = ap.parse_args()

    ev = _load_eval()
    cues = parse_vtt(Path(args.vtt))
    draft = json.loads(Path(args.draft).read_text(encoding="utf-8"))

    print(f"VTT: {len(cues)} cues，覆盖 {cues[0]['start']:.0f}~{cues[-1]['end']:.0f}s")

    clip_len = args.clip_end - args.clip_start

    # 🔴 **先验「是不是同一素材」**（P12 的教训：对不上就别调算法了）
    ratio = same_source_ratio(cues, draft)
    print(f"素材同一性预检：3-gram 重合率 {ratio:.1%}")
    if ratio < 0.25:
        print(f"🔴🔴 重合率仅 {ratio:.1%}（同一素材实测 60~65%）⇒ "
              f"**B 站素材与这份官方 VTT 不是同一段视频**。")
        print("    对齐在这里**不可能成功** —— 请先确认讲次对应关系"
              "（B 站分 P ↔ OCW 讲次可能不同源），或换一份官方转写。")
        print("    （本轮不产出候选稿 —— 与其产出一份错位的稿，不如明确报「不同源」。）")
        return 2
    if ratio < 0.45:
        print(f"⚠ 重合率 {ratio:.1%} 偏低，对齐结果需**加倍核对**（首句/末句边界 + 词级差）")

    if args.offset is not None:
        # 人工确认路径：偏移由**两个以上独立位置**手工核对得出
        cands, med, share, sd = [args.offset], args.offset, 1.0, 0.0
        method = "人工确认（--offset）"
    else:
        # ① n-gram 索引 + 投票（每段独立定位，最稳）
        cands, med, share, sd = fit_by_ngram(cues, draft)
        method = "n-gram 索引 + 投票"
        if not cands or share < 0.25:
            # ② 首尾双重定位（两个独立锚点 + 已知间距）
            c2, m2, s2, sd2 = fit_by_boundaries(cues, draft, clip_len)
            if c2 and s2 > share:
                cands, med, share, sd, method = c2, m2, s2, sd2, "首尾双重定位"
            elif not cands:
                # ③ 退回：粗定位 + 受限窗口精搜
                cands, med, share, sd = fit_offset(cues, draft)
                method = "锚点投票（其它方法无解）"
    if not cands:
        print("❌ 对齐失败（三种方法都无解）")
        return 1
    print(f"对齐方法: {method}")
    print(f"锚点拟合: n={len(cands)} 众数 {med:.2f}s 占比 {share:.0%} σ={sd:.2f}s "
          f"(min {min(cands):.2f} max {max(cands):.2f})")
    if share < 0.4:
        print(f"🔴 众数占比仅 {share:.0%} —— 锚点不可信，**不要**直接用这个偏移")

    # 🔴 关键：med 本身就是「OCW 绝对时间 − 切片内相对时间」的差值，
    #    所以切片内 0 s（即 B 站 clip_start）对应的 OCW 绝对时间就是 **med**。
    #    ⚠️ 写成 `clip_start + med` 会**把切片起点重复加一次**（P1 时我手工算对了，
    #    写脚本时又算错了一次 —— 症状：切窗整体右移，WER 直接飙到 1.0）。
    # 🔴 切窗起点 = **ASR 首段匹配所在 cue 的起点**（不是 med 本身）
    #
    # 理由：med 是「偏移量」的统计估计，误差 ±0.5 s 就可能**把首句切掉一半**。
    # 实测 P20：med=296.50 而 ASR 首句的 cue 起点是 296.33 ⇒ 差 0.17 s，
    # 用 med 当窗起点 ⇒ 首句 "So that's the probability for each of these events."
    # 被切在窗外（自检立刻报 ❌）。
    # ⇒ 窗起点取**首句 cue 的起点**，首句完整包含；窗终点 = 起点 + 切片时长。
    first_key = " ".join(" ".join(draft["segments"][0]["text"].split()[:14]).split()[:6]).lower()
    cands_all = [c for c in cues if abs(c["start"] - med) <= 30.0]
    win_start = med
    if cands_all:
        # 在 med 附近找「文本最接近 ASR 首段」的那个 cue
        best, best_score = None, -1
        for c in cands_all:
            t = c["text"].lower()
            score = sum(1 for w in first_key.split() if w in t)
            if score > best_score:
                best, best_score = c, score
        if best is not None and best_score >= 3:
            win_start = best["start"]
    ocw_start = win_start
    ocw_end = ocw_start + clip_len
    print(f"⇒ B 站 {args.clip_start:.0f}s ↔ OCW {ocw_start:.2f}s；"
          f"切窗 [{ocw_start:.2f}, {ocw_end:.2f})"
          + (f"（众数 {med:.2f}，窗起点改用首句 cue）" if abs(win_start - med) > 0.05 else ""))

    # ---- 语义边界校验 ----
    win = slice_cues(cues, ocw_start, ocw_end)
    if not win:
        print("❌ 切窗内没有 cue")
        return 1
    first = win[0]
    prev = [c for c in cues if c["start"] < first["start"]]
    print(f"\n首句 cue: [{first['start']:.2f}] {first['text'][:70]!r}")
    if prev:
        p = prev[-1]
        print(f"上一句  : [{p['start']:.2f}] {p['text'][:70]!r}  （必须在窗外）")
    # ASR 侧首句
    print(f"ASR 首段: [{draft['segments'][0]['audioStart']:.1f}] "
          f"{draft['segments'][0]['text'][:70]!r}")

    # ---- 独立自检：ASR 首句的内容必须能在切窗内找到 ----
    # 这条比「WER 最小」可靠得多，也比「锚点投票」更独立 ——
    # 它验的是**内容**（老师说的第一句话在不在官方稿的这一段里），
    # 而锚点验的是**位置**。两者都过，才算对齐。
    first_hyp = " ".join(draft["segments"][0]["text"].split()[:10]).lower()
    win_text = " ".join(c["text"] for c in win).lower()
    key = " ".join(first_hyp.split()[:6])
    hit = key in win_text
    print(f"\n【独立自检】ASR 首句前 6 词 {key!r}")
    print(f"            在切窗内找到：{'✅ 是' if hit else '❌ 否 —— 窗口或讲次对不上'}")
    if not hit:
        # 退一步：只用 4 个词
        key4 = " ".join(first_hyp.split()[:4])
        hit4 = key4 in win_text
        print(f"            放宽到 4 词 {key4!r}：{'✅ 命中' if hit4 else '❌ 仍不命中'}")
        hit = hit4

    # ---- 官方稿 vs ASR 草稿的词级差（量级自检）----
    official = " ".join(c["text"] for c in win)
    hyp = " ".join(s.get("text", "") for s in draft["segments"])
    r = ev.normalize(official, "en").split()
    h = ev.normalize(hyp, "en").split()
    e = ev.edit_distance(r, h)
    wer = e / max(1, len(r))
    print(f"\n词级差（官方稿 vs ASR 草稿）: ref {len(r)} / hyp {len(h)} / edits {e} ⇒ WER {wer:.4f}")
    if wer > 0.30:
        print("🔴 WER > 0.30 —— **窗口多半是错的**（P1 实测：错 200s ⇒ WER 0.72）。"
              "请先查偏移，不要直接归因给模型。")

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    # 逐行候选稿（按 cue）
    (out / f"{args.stem}.official.txt").write_text(
        "\n".join(c["text"] for c in win) + "\n", encoding="utf-8")
    (out / f"{args.stem}.official.whole.txt").write_text(official + "\n", encoding="utf-8")
    meta = {
        "vtt": str(args.vtt),
        "draft": str(args.draft),
        "biliClipStart": args.clip_start, "biliClipEnd": args.clip_end,
        "ocwClipStart": round(ocw_start, 3), "ocwClipEnd": round(ocw_end, 3),
        "offsetSeconds": round(med, 3), "offsetSigma": round(sd, 3),
        "anchorCount": len(cands),
        "firstCue": {"start": first["start"], "text": first["text"]},
        "prevCue": ({"start": prev[-1]["start"], "text": prev[-1]["text"]} if prev else None),
        "crossCheckWer": round(wer, 4),
        "refWords": len(r), "hypWords": len(h), "edits": e,
    }
    (out / f"{args.stem}.align.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n候选稿 → {out / (args.stem + '.official.txt')}")
    print(f"对齐记录 → {out / (args.stem + '.align.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
