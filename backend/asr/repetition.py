# -*- coding: utf-8 -*-
"""
识别文本的「重复循环」检测（纯文本分析，不依赖 Whisper）

## 为什么要换掉旧判据

旧判据是 `产出字幕数 / 送入片段数 > 1.2`（见 whisper_engine 的 AUTO_DEGRADE）。
中文代理集 A/B 实测证明它在中文场景下**两个方向都错**：

| 实测 | 字幕/片段 | 旧判据结论 | 真实情况 |
|---|---|---|---|
| `--terms off`（无提示词） | 1.625 | 触发 | 无提示词可降级，误报 |
| `--terms on` | 1.4286 | 触发 | 第 4 段就误触发，797 字符术语表被砍成 200 |
| `--terms on-noguard` | 1.0893 | **未触发** | 真复读机故障「级测试级测试…」重复 40+ 次，**漏报** |

根因：中文一句话经 Whisper 输出常被标点切成好几条字幕，**「字幕数 > 片段数」是中文的
天然形态**，不是异常。反过来，真正的复读机故障会把一大段文字塞进**一条**字幕里，
分母（片段数）不变而分子（字幕数）也不涨，比值反而偏低 —— 于是最危险的情况恰好漏掉。

## 新判据：局部窗口内的 n-gram 自重复率（主）+ 最长重复子串（辅）

为什么必须**局部窗口**而不是全文：Whisper 复读是**局部**现象（模型把刚生成的内容当上文
接着念），而「内容本身重复」是**全局**现象（比如用循环拼接音频做长测）。全文扫描会把
后者误判成故障 —— 12 分钟英文长测是把 jfk.wav 循环拼出来的，全文最长重复高达 746 字符，
但它一个循环故障都没有。所以只在**最近一段输出**里扫。

窗口取 240 字符：与 whisper_engine 的 `_build_prompt()` 用 `_last_text[-200:]` 做
initial_prompt 的量级一致 —— 模型能「看到」多少上文，就扫多少。

两个信号同时成立才算触发：
  1. `dupRatio >= minDupRatio`：窗内 n-gram 大量自我重复（密度信号）
  2. `longestRepeat >= minRepeatChars`：重复的是一个**成句的片段**而不是几个常用词
只满足其一都不触发，避免把「老师口头重复一个短语」「英文里的 the / of」判成故障。

## 阈值是实测标定出来的，不是拍脑袋

两轮中文代理集 A/B 共 6 个样本（`packaging/eval/run1-english-terms/` + `packaging/eval/`），
窗口 160 字符、峰值窗内 8-gram 重复率与最长重复子串：

| 样本 | 最长重复子串 | 窗内重复率 | 真实情况 |
|---|---|---|---|
| run1 `off` | 45 | 0.4314 | 真循环（「…神经缘」成句重复） |
| run1 `on-noguard` | 80 | 0.7320 | 真复读（130 字串 ×40） |
| run2 `on-noguard` | 54 | 0.3072 | 真复读（54 字句 ×3） |
| run1 `on` | 23 | 0.1961 | 正常 |
| run2 `off` | 22 | 0.1569 | 正常 |
| run2 `on` | 29 | 0.1699 | 正常 |
| 人工撰写讲稿 | 11 | 0.0000 | 正常 |

**真循环 45–80，干净样本 11–29**。据此标定 `minRepeatChars = 35`：
干净侧余量 35/29 = **1.21×**，循环侧余量 45/35 = **1.29×** —— 这是唯一让两侧余量都 >1.2× 的取值
（30 会让干净侧只剩 1.03×，40 会让循环侧只剩 1.12×）。

`minDupRatio` 在 0.20–0.30 区间内不改变任何样本的分类（真正的判别器是重复片段长度），
取 0.25 作为**第二道闸**：它挡住「两句很长的话被原样引用一次」这类情形（长度够但没有密度）。

刻意**不**把阈值压到能抓住「29 字重复一次」那一类：那种轻微重复对字幕质量损伤很小，
为它调低阈值会把干净侧余量压到 1.2× 以下，得不偿失。

⚠️ 上述标定只有 6 个样本、且全部来自 TTS 合成音。**真实课堂素材进来后必须复核**
（真人讲课更可能有「老师原样复述一句很长的话」的情形，可能推高干净侧上界）。
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field

# ---- 默认参数 ----
#
# 全部集中在这里，方便按实测数据标定与回归测试覆盖。
DEFAULT_WINDOW_CHARS = 160      # 只扫最近这么多字符（标定见模块 docstring）
DEFAULT_NGRAM = 8               # CJK 按字符 8-gram；拉丁文本会用更长的 n
DEFAULT_LATIN_NGRAM = 24        # 拉丁文本 8 字符≈1.5 个词太短，容易被常用词误命中
DEFAULT_MIN_DUP_RATIO = 0.25    # 窗内 n-gram 重复质量占比阈值（密度信号，见下）
DEFAULT_MIN_REPEAT_CHARS = 35   # 重复片段至少这么长（成句信号，实际判别器）
DEFAULT_MIN_WINDOW_CHARS = 48   # 窗口太短没有统计意义，直接不判
DEFAULT_ADVISORY_RATIO = 1.2    # 旧的「字幕/片段」判据阈值，降级为辅助告警

# 归一化时丢掉的字符：空白与各类中英文标点
#
# 与 eval_asr_quality.normalize 的中文分支同一套口径 —— 检测逻辑必须和离线评估
# 用同一把尺子，否则离线看到循环、线上看不到（或反过来）。
_STRIP = set(
    " \t\r\n\u3000"
    "，。、；：？！“”‘’（）《》〈〉【】—…·「」『』"
    ",.;:?!\"'()[]{}<>-—_/\\|@#$%^&*+=~`"
)


def normalize(text: str) -> str:
    """去掉空白与标点，只留内容字符（保留拉丁字母与数字）"""
    return "".join(ch for ch in text if ch not in _STRIP)


def _is_mostly_cjk(text: str) -> bool:
    """判定文本是否以中日韩字符为主，用于选择 n-gram 粒度"""
    if not text:
        return False
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk * 2 >= len(text)


def pick_ngram(text: str, ng: int | None = None) -> int:
    """按文本语种选 n：显式传入则优先"""
    if ng is not None:
        return ng
    return DEFAULT_NGRAM if _is_mostly_cjk(text) else DEFAULT_LATIN_NGRAM


def dup_ngram_ratio(text: str, n: int) -> float:
    """
    窗内 n-gram 的「重复质量」占比 = Σ(count-1) / 窗口数

    用「重复质量」而不是「去重后减少了多少字符」：后者在只重复一次时也会给出很高的
    数值（300 字符里重复 200 字符 → 0.67），分不清「重复一次」和「重复十次」。
    这里 count-1 会把 10 次重复计成 9 份，能如实反映严重程度。
    """
    if n <= 0 or len(text) < n:
        return 0.0
    windows = len(text) - n + 1
    cnt = collections.Counter(text[i : i + n] for i in range(windows))
    dup = sum(c - 1 for c in cnt.values() if c > 1)
    return dup / windows


def has_repeat(text: str, n: int) -> bool:
    """是否存在长度恰为 n、出现 >=2 次的子串（用集合短路，比 Counter 快）"""
    if n <= 0 or len(text) < n:
        return False
    seen = set()
    for i in range(len(text) - n + 1):
        g = text[i : i + n]
        if g in seen:
            return True
        seen.add(g)
    return False


def longest_repeat_len(text: str, lo: int = 8) -> int:
    """
    最长「出现 >=2 次」的子串长度

    「存在长度 >= n 的重复子串」对 n 单调（长 n 重复 ⇒ 其前缀长 n-1 也重复），
    所以可以二分。上界取 len//2：重复子串至少出现两次、两处不重叠。
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


def top_repeat_gram(text: str, n: int, limit: int = 3) -> list[tuple[str, int]]:
    """出现次数最多的若干 n-gram，用于日志与排障时给出「重复了什么」的证据"""
    if n <= 0 or len(text) < n:
        return []
    cnt = collections.Counter(text[i : i + n] for i in range(len(text) - n + 1))
    items = [(g, c) for g, c in cnt.items() if c > 1]
    items.sort(key=lambda x: (-x[1], x[0]))
    return items[:limit]


@dataclass
class RepetitionVerdict:
    """一次检测的结论（不可变语义，供引擎决定是否降级术语提示词）"""

    triggered: bool = False
    dup_ratio: float = 0.0
    longest_repeat: int = 0
    window_chars: int = 0
    ngram: int = 0
    samples: list[tuple[str, int]] = field(default_factory=list)
    reason: str = ""

    @property
    def summary(self) -> str:
        if not self.triggered:
            return f"未检测到重复循环（窗内重复率 {self.dup_ratio:.3f}，最长重复 {self.longest_repeat} 字符）"
        head = f"检测到重复循环（窗内重复率 {self.dup_ratio:.3f}，最长重复 {self.longest_repeat} 字符）"
        if self.samples:
            g, c = self.samples[0]
            head += f"：{g[:24]!r} 出现 {c} 次"
        return head


def subtitle_ratio_advisory(
    out_segments: int, in_segments: int, threshold: float = DEFAULT_ADVISORY_RATIO
) -> dict:
    """
    旧的「产出字幕数 / 送入片段数」比值 —— 降级为**辅助告警**

    它**不再触发**术语提示词降级，只记日志。理由（实测，中文代理集）：

    · 中文一句话经 Whisper 输出常被标点切成好几条字幕，`--terms off`（无任何提示词）
      的比值天然就是 1.625 —— 拿它当故障信号必然误报；
    · 反过来，真正的复读机故障会把大段文字塞进**一条**字幕，分子分母都不涨，
      实测真复读样本的比值只有 1.089 —— 最危险的情况恰好漏报。

    保留它是因为英文场景下 1 片段≈1 字幕，比值仍有参考价值（人工排障时看一眼无害）。
    判定重复循环请用 `detect()`。
    """
    ratio = (out_segments / in_segments) if in_segments else 0.0
    return {
        "ratio": round(ratio, 4),
        "aboveAdvisory": ratio > threshold,
        "threshold": threshold,
        "note": "仅辅助告警；中文下比值天然 > 1.2，不用于触发降级",
    }


def detect(
    text: str,
    *,
    window: int = DEFAULT_WINDOW_CHARS,
    ngram: int | None = None,
    min_dup_ratio: float = DEFAULT_MIN_DUP_RATIO,
    min_repeat_chars: int = DEFAULT_MIN_REPEAT_CHARS,
    min_window_chars: int = DEFAULT_MIN_WINDOW_CHARS,
) -> RepetitionVerdict:
    """
    检测文本**尾部**是否存在重复循环

    只看尾部（最近 `window` 个字符）而不是全文，原因见模块开头：复读是局部现象，
    而用循环音频做长测这类「内容本身重复」是全局现象，全文扫描会误判。

    调用时机建议：每产出一条字幕就调一次，传入累计识别文本的尾部即可
    （引擎里就是 `_last_text[-window:]`），单次开销 O(window)。
    """
    tail = normalize(text)[-max(window, min_window_chars) :]
    ng = pick_ngram(tail, ngram)
    verdict = RepetitionVerdict(
        dup_ratio=round(dup_ngram_ratio(tail, ng), 4),
        window_chars=len(tail),
        ngram=ng,
    )
    if len(tail) < min_window_chars:
        verdict.reason = f"窗口过短（{len(tail)} < {min_window_chars} 字符），不判定"
        return verdict

    verdict.longest_repeat = longest_repeat_len(tail)

    density_ok = verdict.dup_ratio >= min_dup_ratio
    phrase_ok = verdict.longest_repeat >= min_repeat_chars
    verdict.triggered = density_ok and phrase_ok

    if verdict.triggered:
        verdict.reason = "重复密度与重复片段长度同时超阈"
        verdict.samples = top_repeat_gram(tail, ng)
    elif density_ok:
        verdict.reason = f"窗内重复密度高（{verdict.dup_ratio:.3f}）但没有成句的重复片段，判为正常重复用词"
    elif phrase_ok:
        verdict.reason = f"存在较长重复片段（{verdict.longest_repeat} 字符）但密度不足，判为正常引用/口头重复"
    else:
        verdict.reason = "两个信号均未超阈"
    return verdict
