"""
Whisper initial_prompt 的 **token 预算** 组装器（P0-3 修复）

背景
----
faster-whisper 的 `WhisperModel.get_prompt()` 只保留 `previous_tokens[-223:]`
（`max_length // 2 - 1`，`max_length = 448`），也就是**只留末尾**，超出部分静默
丢弃 —— 不报错、不打日志（1.2.1 `transcribe.py:1550`）。

而产品的提示词构建顺序恰好是**高优先级在前**：
    custom_terms → discovered_terms → DOMAIN_TERMS → 词表短语
于是优先级最高的自定义术语**最先**被丢掉。中文代理集实测
（`ASR_Pipeline_Validation_Report.md` §6.10）：

| 提示词 | 字符 | token | 被丢 | 术语存活 |
|---|---|---|---|---|
| 英文术语表 | 792 | 236 | 13（6%） | 36/39 |
| 中文术语表+英文 | 799 | **506** | **283（56%）** | **54/94** |

且存活率与优先级**完全颠倒**：自定义中文术语 14/55 = **25%**，
内置英文术语 39/39 = **100%**（后者优先级最低）。
因果闭环：`卷积`（存活）0/20 → 13/20 ✅；`梯度`（被丢）0/17 → **0/17** ❌。
放大因素：`术语(506) + _last_text[-200:](≈280) = 627 token`，
**从第二段起术语被整体挤出**。

修法
----
把「谁在前」反过来：**按高→低优先级分配预算，按低→高优先级拼接**，
让高优先级落在尾部保留窗口里。总预算取 220（库保留 223，留 3 冗余）。

本模块不依赖 Whisper 运行时：真 tokenizer 可注入（`WhisperModel.hf_tokenizer`），
拿不到时退化为启发式估算，并在结果里标注 `exact=False` —— **估算只用于兜底，
真实场景请务必让引擎注入 `hf_tokenizer`**，因为中文的 token 密度受生僻字
byte-level BPE 回退影响，启发式系数天然不准（见 `estimate_tokens` 的说明）。
"""

from dataclasses import dataclass, field
from typing import Callable, Optional

# ============ 预算常量 ============

#: 提示词总 token 预算。faster-whisper 实际保留 223（`max_length // 2 - 1`），
#: 取 220 留 3 个 token 冗余 —— 估算（或 tokenizer 与库内编码的细微差异）
#: 不至于把我们精心保住的高优先级术语又顶出去。
DEFAULT_TOKEN_BUDGET = 220

#: 预留给「上一段识别文本」的 token 配额（上下文连贯性）。
#: 旧实现是 `_last_text[-200:]` 字符 —— 中文 200 字符 ≈ 280 token，
#: 单它一项就超过整个预算，这才是「第二段起术语全丢」的直接原因。
#: 48 token 约等于 35 个中文字 / 160 个英文字符，足够提供语境。
DEFAULT_PREV_TEXT_TOKENS = 48

#: 无论 `prev_text` 多有用，都要给术语留出这么多 token。
#: 否则一段很长的上一段文本会把术语挤成空，退回成「无术语」状态。
MIN_TERMS_TOKENS = 60

#: 术语之间的分隔符（", "）大致占 1 个 token，分配预算时计进去。
SEP_TOKENS = 1


# ============ token 计数 ============

# CJK 统一表意文字 + 假名 + 谚文。这些字符在 Whisper 的 byte-level BPE 里
# 通常是 1 token/字，生僻字回退到 UTF-8 字节序列时会涨到 2~3 token/字。
_CJK_RANGES = (
    (0x3400, 0x4DBF),    # CJK 扩展 A
    (0x4E00, 0x9FFF),    # CJK 基本区
    (0xF900, 0xFAFF),    # CJK 兼容表意
    (0x3040, 0x30FF),    # 平假名 / 片假名
    (0xAC00, 0xD7AF),    # 谚文音节
    (0x20000, 0x2FA1F),  # CJK 扩展 B~
)


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    for lo, hi in _CJK_RANGES:
        if lo <= cp <= hi:
            return True
    return False


def estimate_tokens(text: str) -> int:
    """
    启发式 token 估算，**仅在拿不到真 tokenizer 时使用**

    系数用 `scripts/verify_prompt_budget.py` 对**真 tokenizer** 实测标定：

      | 文本 | 字符 | 真值 token |
      |---|---|---|
      | 55 个中文术语表 | 296 | 379 |
      | 简体引导语（19 汉字） | 20 | 28 |
      | 领域英文术语表 | 478 | 122 |
      | 词表英文短语层 | 643 | 212 |

    反推中文侧约 **1.85 token/字**（远超「一字一 token」的直觉 —— Whisper 的
    byte-level BPE 对不在常用字表里的专业词会回退到 UTF-8 字节序列，涨到 2~3
    token/字），英文侧约 **0.26 token/字符**。此处取中文 1.7、其它 0.28 作为
    **偏保守（略高估）** 的折中：高估只会少放几个术语，低估才会让组装结果越界。

    注意方向性风险：低估时越界的部分会被 faster-whisper 从**头部**截掉，而头部
    恰是低优先级内容（「上一段文本」「词表短语」），所以本设计对低估是 fail-safe
    的；但生产路径仍应注入 `hf_tokenizer`，不要依赖估算。
    """
    if not text:
        return 0
    cjk = 0
    other = 0
    for ch in text:
        if _is_cjk(ch):
            cjk += 1
        else:
            other += 1
    return int(cjk * 1.70 + other * 0.28 + 0.999)


def make_counter(tokenizer=None) -> tuple[Callable[[str], int], bool]:
    """
    返回 `(count_fn, exact)`。`exact=False` 表示这是启发式估算。

    `tokenizer` 传 `WhisperModel.hf_tokenizer`（`tokenizers.Tokenizer`）即为真值，
    这与 faster-whisper 内部 `get_prompt()` 编码 initial_prompt 用的是**同一个
    词表**，所以计数与库的截断判定口径一致。
    """
    if tokenizer is not None:
        try:
            ids = tokenizer.encode("").ids
            if isinstance(ids, list):

                def _count(text: str) -> int:
                    return len(tokenizer.encode(text).ids)

                return _count, True
        except Exception:
            pass          # 任何异常都退回估算，绝不让计数失败拖垮识别
    return estimate_tokens, False


def truncate_to_tokens(text: str, max_tokens: int, count: Callable[[str], int]) -> str:
    """
    从**尾部**截取，使结果不超过 `max_tokens`

    必须从尾部截而不是头部：调用方拿到的上一段文本要保留的是**最近**的内容。
    token 数对子串长度单调不减，所以直接二分。

    🔴 `max_tokens` 小于**单个字符**的代价时必须返回空串（2026-10-08 修复）

    实测：byte-level BPE 对中文单字会回退到 UTF-8 字节序列，**一个汉字 ≈ 4 token**
    （`count("好") == 4`）。于是 `max_tokens=1` 时**任何非空子串都超预算** ——
    二分找不到可行解，`lo` 停在 0，而最后的 `text[-lo:]` 在 `lo == 0` 时
    **等于 `text[0:]` 也就是全文**（Python 里 `-0 == 0`，这不是笔误而是语义陷阱）。

    后果不是「截得不够」而是**反向吃光预算**：预算 90 的场景下
    `prev_budget = min(48, 90-28-1-60) = 1` → 本该留 0 字符的上一段文本
    实际留下整段 120 字符 / 100 token，把术语层的 `remaining` 挤成 **-40**，
    24 个术语**一个都没进去**（`kept=[]`）。这条路径在真实降级运行里会命中。
    """
    if not text or max_tokens <= 0:
        return ""
    if count(text) <= max_tokens:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if count(text[-mid:]) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    if lo <= 0:
        # 连 1 个字符都放不下（中文单字 4 token 这类情形）—— 明确返回空，
        # 绝不能让 `text[-0:]` 把全文放回去。
        return ""
    return text[-lo:]


# ============ 预算组装 ============

@dataclass
class BudgetedPrompt:
    """组装结果，同时作为埋点数据源（供 `WhisperEngine.get_stats()` 读取）"""

    body: Optional[str]                                   # 最终提示词；None = 不该传 prompt
    budget: int                                           # 本次预算
    used_tokens: int                                      # 实际占用
    exact: bool                                           # token 计数是否为真值
    kept: dict[str, list[str]] = field(default_factory=dict)     # 各层保留的术语
    dropped: list[str] = field(default_factory=list)             # 被预算丢掉的术语
    prev_kept: str = ""
    hint_kept: str = ""

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.budget - self.used_tokens)

    @property
    def kept_count(self) -> int:
        return sum(len(v) for v in self.kept.values())

    def summary(self) -> str:
        parts = [
            f"预算 {self.budget} token，实占 {self.used_tokens}"
            f"（留 {self.remaining_tokens}）",
            f"术语保留 {self.kept_count} 个",
        ]
        if self.dropped:
            parts.append(f"丢弃 {len(self.dropped)} 个")
        if not self.exact:
            parts.append("token 为启发式估算")
        return "；".join(parts)


class _PickResult:
    """单层「按预算挑选」的结果（内部用，不外露）"""

    __slots__ = ("kept", "dropped", "cost")

    def __init__(self, kept: list[str], dropped: list[str], cost: int):
        self.kept = kept
        self.dropped = dropped
        self.cost = cost


def _pick_within_budget(
    terms: list[str],
    budget: int,
    count: Callable[[str], int],
) -> "_PickResult":
    """
    在一层术语里挑出放得进 `budget` 的那些（按传入顺序贪心，直到放不下）

    从 `assemble` 里抽出来只为可读性与可测性，**语义与内联版逐字一致**。

    历史记录（别再走一遍）：这里曾加过 `rank` 参数用来「按价值排序再抢预算」，
    想把降级时「按列表顺序丢词」改成「按重要性丢词」。**实测证明那是伪问题**：

        budget=70、24 词课程术语表，保留 5 个词时覆盖的真实出现次数 ——
            原顺序（书写序）  38/66
            按词长降序        7/66   ← 更差
            按真实频次降序    38/66  ← 与原顺序相同

    原因：词表本就按语义重要性书写，高频词天然在前列；而 `assemble` 是
    **按层**依次抢预算，层内顺序不是瓶颈。G4 那次「丢掉 `坐标系`（18 次）」
    的真实原因是**降级预算只够放 5 个词**（`坐标系` 成本 8 token、剩余额度 41），
    不是排序 —— 加排序参数救不了，只是凭空多出一个没人用的 API。

    结论：预算不足时该做的是**提高预算**或**缩短词表**，不是重排。

    Args:
        terms: 该层术语，原顺序即保留优先级、也是输出顺序
        budget: 该层可用的 token 预算
        count: token 计数器

    Returns:
        `_PickResult`（`kept` 保持原顺序）
    """
    cleaned = [t.strip() for t in ((x or "").strip() for x in terms) if t]
    if not cleaned:
        return _PickResult([], [], 0)

    remaining = budget
    kept: list[str] = []
    dropped: list[str] = []
    for term in cleaned:
        cost = count(term) + SEP_TOKENS
        if cost > remaining:
            dropped.append(term)
            continue
        remaining -= cost
        kept.append(term)
    return _PickResult(kept, dropped, budget - remaining)


def assemble(
    sections=None,
    prev_text: str = "",
    hint: str = "",
    budget: int = DEFAULT_TOKEN_BUDGET,
    count: Optional[Callable[[str], int]] = None,
    prev_max_tokens: int = DEFAULT_PREV_TEXT_TOKENS,
    prebuilt_body: str = "",
) -> BudgetedPrompt:
    """
    按 token 预算组装 initial_prompt（P0-3 的核心）

    两种入口，二选一：

    **A. `sections`（正常路径，能按优先级重排）**
    拼接顺序（**从低优先级到高优先级**，尾部 = 库保留窗口）：

        [上一段文本] [词表短语] [领域术语] [课程已发现] [自定义术语] [简体引导语]

    分配顺序与之相反（**从高优先级到低优先级**），保证预算不够时先丢低优先级：

        1. 简体引导语    全量保留（它决定简繁，砍掉等于 P0-1 复发）
        2. 自定义术语    用户手填，一定是这门课的核心词
        3. 课程已发现    本课确实在用
        4. 领域术语      标准术语表
        5. 词表短语      最泛的一层
        6. 上一段文本    有独立配额，物理位置也在最前（超额时第一个被牺牲）

    **层内**按传入顺序贪心到放不下为止。⚠️ 别想着「按重要性重排能救回更多词」——
    实测过，那是伪问题（见 `_pick_within_budget` 的历史记录）。

    **B. `prebuilt_body`（退化路径）**
    调用方只给了一段已经拼好的字符串、拿不到分层信息时走这里。此时**不重排**
    ——`build_initial_prompt()` 的输出本身已是「低 → 高」，再排一次就转回去了。
    只在 `prev_text` 与 `hint` 上做预算约束；正文超预算时从**头部**裁
    （头部 = 低优先级）。

    Args:
        sections: `[(层名, [术语...]), ...]`，**从高优先级到低优先级**；与
            `prebuilt_body` 互斥，`prebuilt_body` 非空时忽略本参数
        prev_text: 上一段识别文本（从尾部按 token 截）
        hint: 简体引导语（中文语种才有；永远放最后、永远保留）
        budget: 本次总 token 预算
        count: token 计数器，见 `make_counter`；None 时用启发式
        prev_max_tokens: `prev_text` 的配额上限
        prebuilt_body: 已拼好的正文（低 → 高优先级）

    Returns:
        `BudgetedPrompt`；`body is None` 表示没有任何内容可传，调用方应传 `None`
        （给 Whisper 塞空串会得到一段无意义的 prompt token）。
    """
    if count is None:
        count = estimate_tokens
    sections = list(sections or [])
    prebuilt_body = (prebuilt_body or "").strip()

    hint = (hint or "").strip()
    prev_text = prev_text or ""
    exact = count is not estimate_tokens

    hint_cost = count(hint) + (SEP_TOKENS if hint else 0)
    if hint_cost >= budget:
        # 预算小到连引导语都放不下（只可能出现在降级后的极端预算里）。
        # 引导语优先于一切 —— 它决定简繁，砍术语也不能砍它。
        hint = truncate_to_tokens(hint, max(0, budget - SEP_TOKENS), count)
        return BudgetedPrompt(
            body=hint or None, budget=budget, used_tokens=count(hint),
            exact=exact, hint_kept=hint,
        )

    if prebuilt_body:
        # 退化路径：正文已排好序，超预算时裁头（头部 = 最低优先级）
        body = truncate_to_tokens(prebuilt_body, budget - hint_cost, count)
        body_cost = count(body) + SEP_TOKENS
        prev_budget = min(
            prev_max_tokens, max(0, budget - hint_cost - body_cost - MIN_TERMS_TOKENS)
        )
        prev_kept = truncate_to_tokens(prev_text, prev_budget, count) if prev_text else ""
        chunks = [c for c in (prev_kept, body) if c]
        chunks.append(hint)
        final = " ".join(chunks).strip()
        return BudgetedPrompt(
            body=final or None, budget=budget, used_tokens=count(final),
            exact=exact, prev_kept=prev_kept, hint_kept=hint,
        )

    # 上一段文本：给配额，但不许吃光术语预算
    prev_budget = min(prev_max_tokens, max(0, budget - hint_cost - MIN_TERMS_TOKENS))
    prev_kept = truncate_to_tokens(prev_text, prev_budget, count) if prev_text else ""
    prev_cost = (count(prev_kept) + SEP_TOKENS) if prev_kept else 0

    # 剩余预算交给术语，从高优先级开始抢
    remaining = budget - hint_cost - prev_cost
    kept: dict[str, list[str]] = {}
    dropped: list[str] = []
    for name, terms in sections:
        # 「谁先抢预算」只跟**层**的优先级有关，层内就是传入顺序 ——
        # 别在这里再排一次：见 `_pick_within_budget` 的历史记录（排序是伪问题）。
        picked = _pick_within_budget(terms or [], remaining, count)
        remaining -= picked.cost
        kept[name] = picked.kept
        dropped.extend(picked.dropped)

    # 拼接：低优先级在前，高优先级贴尾（尾部才是库的保留窗口）
    order_low_to_high = [name for name, _ in reversed(list(sections))]
    chunks: list[str] = []
    if prev_kept:
        chunks.append(prev_kept)
    for name in order_low_to_high:
        terms = kept.get(name) or []
        if terms:
            chunks.append(", ".join(terms))
    body = " ".join(chunks).strip()
    if hint:
        body = f"{body} {hint}".strip() if body else hint

    used = count(body)
    return BudgetedPrompt(
        body=body or None,
        budget=budget,
        used_tokens=used,
        exact=exact,
        kept=kept,
        dropped=dropped,
        prev_kept=prev_kept,
        hint_kept=hint,
    )
