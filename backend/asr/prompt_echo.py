# -*- coding: utf-8 -*-
"""
提示词回显（prompt echo）清洗 —— 纯文本分析，不依赖 Whisper

## 现象

Whisper 在**几乎没有语音**的片段上，会不回「听不清」，而是把 `initial_prompt`
当成上文**接着念下去**。于是提示词本身变成了「识别结果」。

真实素材实测（B站 Origin 科研绘图课，630s，近静音占比 27.6%）：
草稿里 `SIMP_HINT` 的文本出现了 **17 次**，集中在 2 条字幕上：

* 一条 2.5s 的静音片段，整段文本就是「请使用简体中文转写。」——一条**纯虚假字幕**，
  会原样显示给用户；
* 一条 14.2s 的合并块里回显了 16 次，和真实讲课内容**糊在同一段**里。

## 为什么合成测试集测不出来

`make_asr_testset.py` 生成的 TTS 素材是**连续朗读**，没有静音段，所以这个缺陷
在代理集上永远不会触发；换机器考也不一定复现。这与 `asr/repetition.py` 的情况
同源：**只有真实素材才能暴露真实缺陷**。

## 为什么必须放在 `_last_text` 之前

`_transcribe()` 把本段文本存进 `self._last_text`，下一轮 `_build_prompt()` 又把它
当作连贯性提示拼进去。回显文本一旦进去，就变成了下一轮的提示词 —— **自我喂食**。
这解释了「同一个块里回显 16 次」：不是模型一次念了 16 遍，而是它在反复吃到自己
上一次吐出的回显。所以清洗必须发生在**回调之前、`_last_text` 赋值之前**。

## 两条规则（保守优先，不猜字）

1. **纯回显 → 整段丢弃**：去掉标点空白后，若全部字符都被回显字面量覆盖，
   这条字幕没有任何语音内容，直接不要（不回回调、不进 `_last_text`、不计入产出）。
2. **混入 → 子串剔除**：真实文本里嵌了回显片段，只把这几个片段删掉，
   其余识别内容原样保留。

**不做的事**：不做文本补全、不做同义改写、不猜字、不动任何非分隔符字符。

## 黑名单是「派生」的，不是另抄一份

字面量列表由 `whisper_engine.get_simp_hint()` 的**返回值**派生（含
`TINGKEBANLV_SIMP_HINT` 覆盖值）。另抄一份常量必然会漂移 —— 一旦有人改了引导语
文案，清洗就会**静默失效**（清洗表还在，但匹配不到任何东西，且不报错）。
派生方式：整句 + 按标点切出的子句 + 子句去掉句首「请」的变体。

**只派生引导语，不派生术语表**：术语（如「图层」「坐标轴」）是老师真会念出来的词，
把它们当黑名单会把正常内容删掉。实测回显的也始终是引导语 —— 因为它被刻意放在
提示词**末尾**（见 `_build_prompt`，为的是不被 token 预算截掉），而模型倾向于续写
「最后看到的东西」。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

# 短于这个长度的字面量不予剔除 —— 太短的字面量（比如自定义引导语里的「简体」）
# 会误伤正常文本。引导语默认两个子句都是 9 字，留足余量。
MIN_ECHO_CHARS = 5

# 子句切分用的标点类别：这里用显式字符集而不是 unicodedata，是为了让「按什么切」
# 成为可读、可测的常量，而不是隐式行为。
CLAUSE_BREAKS = "，。、；：！？,.;:!?…\n\r\t 　"

# 句首可剥离的敬语前缀（实测回显既有「请使用简体中文转写」也有「使用简体中文转写」）
_POLITE_PREFIXES = ("请你", "请使用", "请")


@dataclass(frozen=True)
class EchoResult:
    """一次清洗的结果。

    `isPureEcho` 与「文本为空」是两回事：空文本可能来自别的原因，
    调用方不该把两者混成一个分支 —— 混掉的代价是以后再也分不清
    「这条是回显」还是「这条本来就空」。
    """

    text: str
    isPureEcho: bool
    hits: int


@dataclass
class EchoStats:
    """埋点：让「这个问题出现多频繁」可被统计，而不是靠肉眼看草稿。"""

    segmentsSeen: int = 0
    segmentsDropped: int = 0
    segmentsCleaned: int = 0
    substringsRemoved: int = 0
    charsRemoved: int = 0

    def asDict(self) -> dict:
        return {
            "segmentsSeen": self.segmentsSeen,
            "segmentsDropped": self.segmentsDropped,
            "segmentsCleaned": self.segmentsCleaned,
            "substringsRemoved": self.substringsRemoved,
            "charsRemoved": self.charsRemoved,
        }


def _is_separator(ch: str) -> bool:
    """空白或标点。用 Unicode 类别判定，ASCII 与全角一视同仁。"""
    cat = unicodedata.category(ch)
    return cat[0] == "P" or cat[0] == "Z"


def build_blacklist(hint: str, min_chars: int = MIN_ECHO_CHARS) -> list[str]:
    """从实际生效的引导语文案派生要清洗的字面量列表（长 → 短）。

    返回值用于**匹配**，不含标点空白（匹配在归一化空间里做），所以
    「请使用简体中文转写。」与「请使用简体中文转写,」用同一个字面量就能命中。
    """
    if not hint:
        return []

    candidates: list[str] = []

    def add(s: str) -> None:
        s = "".join(ch for ch in s if not _is_separator(ch))
        if len(s) >= min_chars and s not in candidates:
            candidates.append(s)

    add(hint)
    for clause in _split_clauses(hint):
        add(clause)
        # 逐个前缀**都**加进来，不能在第一个命中处 break。
        # 实测教训：`请使用简体中文转写` 若只剥出 `简体中文转写`（6 字），
        # 那么模型吐出的 `使用简体中文转写`（8 字）就会退而匹配 6 字变体，
        # 结果删成 `使用` —— 一个没说出口的残字留在字幕里，
        # 而且**不报错**。这个坑正是靠 test_blacklist_includes_politeness_variant 抓到的。
        for prefix in _POLITE_PREFIXES:
            if clause.startswith(prefix):
                add(clause[len(prefix):])
        # 后缀同样要加：模型不一定从引导语的**开头**接，它可能从句中任意位置续上。
        # 实测残留：老师说了「这一项是」，模型接着把引导语从句中续上，吐成
        # `这一项是普通话的句子` —— `普通话的句子` 是 `以下是普通话的句子` 的**后缀**，
        # 靠上面那两行（整句 / 剥礼貌前缀）**永远生成不出来**，所以这条一直漏救，
        # 只能靠人工在校对表里补一刀。这就是旧注释里「部分回显会漏救」的成因。
        for suffix in _suffixes(clause, min_chars):
            add(suffix)

    # 长的先匹配：否则「请使用简体中文转写」会被更短的变体切碎，留下「请」残字。
    candidates.sort(key=len, reverse=True)
    return candidates


def _suffixes(s: str, min_chars: int) -> list[str]:
    """`s` 的全部后缀（长度 ≥ min_chars，长的在前）。"""
    return [s[i:] for i in range(len(s) - min_chars + 1)]


def _split_clauses(hint: str) -> list[str]:
    out: list[str] = []
    cur: list[str] = []
    for ch in hint:
        if ch in CLAUSE_BREAKS:
            if cur:
                out.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """去掉分隔符，同时记录每个归一化字符在原串里的下标。

    有了这张映射表，才能在**归一化空间里匹配、在原串空间里删除**。
    少了它就只能直接 `str.replace`，而回显文本的标点与提示词几乎不可能逐字相同
    （实测模型吐出的是半角逗号，提示词里是全角），匹配会全部落空 —— 而且不报错。
    """
    norm_chars: list[str] = []
    idx_map: list[int] = []
    for i, ch in enumerate(text):
        if _is_separator(ch):
            continue
        norm_chars.append(ch)
        idx_map.append(i)
    return "".join(norm_chars), idx_map


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not spans:
        return []
    spans = sorted(spans)
    merged = [list(spans[0])]
    for s, e in spans[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def _analyze(text: str, blacklist: list[str]) -> tuple[list[tuple[int, int]], float]:
    """一次分析同时给出两样东西：原串上的回显区间 + 归一化空间的覆盖率。

    分成两次算（一次找区间、一次算覆盖率）就必须重复构造 idx_map，
    而两处的口径一旦不同，`isPureEcho` 就会和实际删除范围对不上 ——
    表现出来就是「明明整段都删空了，却判定不是纯回显」。所以合在一处。
    """
    norm, idx_map = normalize_with_map(text)
    if not norm:
        return [], 0.0

    norm_spans: list[tuple[int, int]] = []
    for lit in blacklist:
        start = 0
        while True:
            i = norm.find(lit, start)
            if i < 0:
                break
            norm_spans.append((i, i + len(lit)))
            start = i + 1  # 允许重叠起点，交给 _merge_spans 合并
    if not norm_spans:
        return [], 0.0

    merged = _merge_spans(norm_spans)
    covered_ratio = sum(e - s for s, e in merged) / len(norm)
    orig_spans = [(idx_map[s], idx_map[e - 1] + 1) for s, e in merged]
    return orig_spans, covered_ratio


def find_echo_spans(text: str, blacklist: list[str]) -> list[tuple[int, int]]:
    """返回在原串中的回显区间（已合并重叠）。"""
    return _analyze(text, blacklist)[0]


def _tidy(text: str) -> str:
    """删除回显后会留下连续分隔符（`, , , `），压缩成一个并去掉首尾。

    刻意最小化：只动**分隔符**，不增删任何文字。之所以要做，是因为
    `"我们要继续做行图线, , , , ,"` 这种残留会原样显示在实时字幕上。
    """
    out: list[str] = []
    prev_sep = False
    for ch in text:
        sep = _is_separator(ch)
        if sep:
            if prev_sep:
                continue
            prev_sep = True
        else:
            prev_sep = False
        out.append(ch)
    result = "".join(out)
    # 去掉首尾分隔符（同样只动分隔符）
    start = 0
    end = len(result)
    while start < end and _is_separator(result[start]):
        start += 1
    while end > start and _is_separator(result[end - 1]):
        end -= 1
    return result[start:end]


def clean_text(text: str, blacklist: list[str], tidy: bool = True) -> EchoResult:
    """对单条字幕执行两条规则。"""
    if not text or not blacklist:
        return EchoResult(text=text, isPureEcho=False, hits=0)

    spans, covered_ratio = _analyze(text, blacklist)
    if not spans:
        return EchoResult(text=text, isPureEcho=False, hits=0)

    # 覆盖率 100% 才算纯回显：留一点余量会把「回显 + 一个残字」误判成纯回显，
    # 于是那个残字被静默丢掉 —— 宁可留下噪声，也不丢识别结果。
    is_pure = covered_ratio >= 1.0

    # 从后往前删，避免下标失效
    chars = text
    for s, e in reversed(spans):
        chars = chars[:s] + chars[e:]

    if tidy:
        chars = _tidy(chars)

    return EchoResult(text=chars, isPureEcho=is_pure, hits=len(spans))


class EchoCleaner:
    """有状态的清洗器：引擎持有一个，累计埋点供日志与草稿统计使用。"""

    def __init__(self, hint: str, min_chars: int = MIN_ECHO_CHARS, tidy: bool = True):
        self.hint = hint or ""
        self.blacklist = build_blacklist(self.hint, min_chars=min_chars)
        self.tidy = tidy
        self.stats = EchoStats()
        # 派生不出任何字面量时必须留痕：否则「清洗看起来在跑、实际什么都没匹配」
        # 会成为又一个静默失效（见模块开头「黑名单是派生的」一节）。
        self.inertReason: str | None = None
        if not self.hint:
            self.inertReason = "未注入引导语，无需清洗"
        elif not self.blacklist:
            self.inertReason = (
                f"引导语过短，未派生出生效字面量（最短需 {min_chars} 字）：{self.hint!r}"
            )

    @property
    def enabled(self) -> bool:
        return bool(self.blacklist)

    def clean(self, text: str) -> EchoResult:
        self.stats.segmentsSeen += 1
        result = clean_text(text, self.blacklist, tidy=self.tidy)
        # 埋点口径：被丢弃的整段只计入 segmentsDropped，**不再**计入剔除子串数。
        # 否则「一条纯回显」会同时出现在「丢弃 1 段」和「剔除 1 处」两个指标里，
        # 事后统计频次时两边都虚高、且没法对账。
        if result.isPureEcho:
            self.stats.segmentsDropped += 1
        elif result.hits:
            self.stats.segmentsCleaned += 1
            self.stats.substringsRemoved += result.hits
            self.stats.charsRemoved += max(0, len(text) - len(result.text))
        return result
