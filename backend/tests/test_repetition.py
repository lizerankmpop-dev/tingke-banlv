# -*- coding: utf-8 -*-
"""
重复循环检测（backend/asr/repetition.py）—— 单元测试

这些用例全部来自**中文代理集 A/B 的真实发现**，不是构造出来的假想场景：

| 用例 | 来源 | 为什么必须有 |
|---|---|---|
| 130 字复读「级测试级测试…」 | run1 `--terms on-noguard` 实测 | 旧判据（字幕/片段>1.2）对它**漏报**：比值只有 1.089，引擎判定 0 次，而文本里重复了 40+ 次 |
| 54 字复读「不平衡的时候只看…」 | run2 `--terms on-noguard` 实测 | 同上，且是**更温和**的真复读 —— 它是阈值标定的循环侧下界 |
| 45 字循环「相当于在训练时随机丢掉…」 | run1 `--terms off` 实测（无任何提示词） | 证明重复循环与术语提示词无关；也证明旧的比值判据会**误报**（该样本比值 1.625） |
| 人工撰写的中文讲稿 | 代理集参考文本 | 干净的负样本：最长重复仅 11 字符 |
| 中文短句自然切分 | 中文的天然形态 | 一句话被标点切成多条字幕 ⇒ 字幕数 > 片段数，**绝不能**因为这个判成重复循环 |

阈值（窗口 160 / 重复片段 ≥ 35 / 密度 ≥ 0.25）是在**两轮共 6 个真实样本**上标定出来的，
实测真循环最长重复 45–80、干净样本 11–29（见模块 docstring）。本文件把当时的
实测数值固化成回归断言：阈值一旦被改动，`test_calibration_margin` 会立刻失败。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from asr import repetition as R  # noqa: E402

# ---- 真实样本（取自中文代理集 A/B 的识别输出）----

# on-noguard：130 字复读，实测重复率 0.7320 / 最长重复 80（窗口截断后的值）
LOOP_TANDEM = "级测试" * 40

# off：成句循环，实测重复率 0.4314 / 最长重复 45
#
# 重复 5 次而不是 3~4 次：单元本身 17 字符，重复 4 次只有 68 字符、最长重复恰好卡在 34，
# 差一个字符就测不出「长度信号」的作用（第一版就写成了 ×4，被 pytest 抓出来）。
# 真实样本里的重复串更长（实测最长 45），这里取 ×5 与实测同量级。
LOOP_SENTENCE = "相当于在训练时随机丢掉一部分神经缘" * 5

# 人工撰写的中文讲稿片段（负样本）：实测重复率 0.0131 / 最长重复 9
CLEAN_LECTURE = (
    "同学们好，今天这节课我们来讲卷积神经网络，以及它背后最重要的训练算法，反向传播。"
    "这部分内容是深度学习的核心，也是后面所有模型的基础，希望大家认真听。"
    "我们先回顾一个概念，什么叫神经网络。简单说，它是由很多层神经元堆叠起来的函数逼近器，"
    "每一层做的事情都是对上一层的输出做一次线性变换，再接一个非线性的激活函数。"
    "常用的激活函数有sigmoid、tanh和relu，其中relu计算最简单，收敛也最快。"
)

# 中文课堂的自然形态：许多**互不相同**的短句连在一起
#
# 这是「字幕数 > 片段数」的成因（一句话被标点切成好几条字幕），绝不能判成重复循环。
# 刻意写成互不重复的句子：若用同一段短句复制几份，重复来自「内容重复」而非
# 「中文短句形态」，就测不出这里想测的东西了。
CLEAN_SHORT_SENTENCES = (
    "好。我们看下一个。这个是损失函数。"
    "那么它怎么算呢。先看预测值。再看真实标签。"
    "两者差多少。差得越大。说明模型越差。"
    "所以我们希望它小一点。越小越好。对不对。"
    "那怎么让它变小。用梯度下降。一步一步来。"
    "步长太大会震荡。步长太小又太慢。所以要调。"
    "调参靠经验。也靠实验。多做几组对照。"
    "还有一个技巧。叫学习率衰减。前期大步走。"
    "后期小步走。这样更容易收敛。我们看个例子。"
    "这个例子里。模型一开始很差。后来慢慢变好。"
)


def test_tandem_loop_is_detected():
    """回归：中英文复读机故障（旧判据漏报的那一类）必须被抓到"""
    v = R.detect(LOOP_TANDEM)
    assert v.triggered is True
    assert v.dup_ratio >= R.DEFAULT_MIN_DUP_RATIO
    assert v.longest_repeat >= R.DEFAULT_MIN_REPEAT_CHARS


def test_sentence_loop_is_detected():
    """回归：无提示词时的成句循环也要抓到（旧判据在此误报）"""
    v = R.detect(LOOP_SENTENCE)
    assert v.triggered is True
    assert v.longest_repeat >= R.DEFAULT_MIN_REPEAT_CHARS


def test_run2_mild_loop_is_detected():
    """
    回归（run2 实测）：更温和的真复读也必须抓到

    run2 的 `on-noguard` 里重复块只有 54 字符 ×3（run1 是 130 字符 ×40），
    密度 0.3072 —— 距阈值很近。它是标定表的**循环侧下界**，用真实原文锁定它。
    """
    mild = (
        "我们看这个例子。调和平均在类别不平衡的时候只看准确率是会骗人的。"
        "调和平均在类别不平衡的时候只看准确率是会骗人的。"
        "调和平均在类别不平衡的时候只看准确率是会骗人的。"
        "所以要看精确率和召回率，不能只看一个数。"
    )
    v = R.detect(mild)
    assert v.triggered is True
    assert v.longest_repeat >= R.DEFAULT_MIN_REPEAT_CHARS


def test_calibration_margin():
    """
    锁定阈值标定：干净样本上界 29 < 阈值 < 真循环下界 45，且两侧余量都 > 1.2×

    想改 `DEFAULT_MIN_REPEAT_CHARS` 的话请先看模块 docstring 的标定表 ——
    30 会让干净侧余量只剩 1.03×（随时误报），40 会让循环侧只剩 1.12×（随时漏报）。
    """
    t = R.DEFAULT_MIN_REPEAT_CHARS
    assert 29 < t < 45
    assert t / 29 > 1.2
    assert 45 / t > 1.2


def test_clean_lecture_not_flagged():
    """负样本：人工撰写的干净讲稿不得误报"""
    v = R.detect(CLEAN_LECTURE)
    assert v.triggered is False
    assert v.longest_repeat < R.DEFAULT_MIN_REPEAT_CHARS


def test_chinese_short_sentences_not_flagged():
    """防误报：中文短句自然切分（字幕数 > 片段数）不是重复循环"""
    v = R.detect(CLEAN_SHORT_SENTENCES)
    assert v.triggered is False


def test_short_window_is_not_judged():
    """窗口过短时不判定，避免拿几十个字符就下结论（误报温床）"""
    v = R.detect("级测试级测试级测试")
    assert v.triggered is False
    assert "窗口过短" in v.reason


def test_loop_only_in_tail_is_detected():
    """
    复读发生在「刚产出的内容」里才是故障

    引擎每产出一条字幕就用 `_last_text[-160:]` 判一次，所以循环在尾部必然被抓到；
    若循环早已过去（不在窗口内）就不该反复触发 —— 否则会把一次故障变成永久降级。
    """
    past = R.detect(CLEAN_LECTURE + "。" + LOOP_TANDEM)  # 循环在尾部
    assert past.triggered is True

    # 窗口只看尾部 160 字符：循环在很前面时不应再触发
    stale = R.detect(LOOP_TANDEM + CLEAN_LECTURE)
    assert stale.triggered is False


def test_legit_oral_repetition_not_flagged():
    """
    老师口头强调（短片段重复）不得误报 —— 这是两个信号必须同时成立的意义

    注意这里**不能**写 `text * 2`：把整段文本复制一份，尾部窗口里就会正好装下两份，
    那确实构成了窗口级的串联重复，检测器判「触发」是对的（本用例第一版就踩了这个坑，
    pytest 直接报出来了）。真实的课堂强调是「短句重复 + 后面接大量不同内容」，
    所以这里给足变化的内容，让重复只占窗口很小一部分。
    """
    text = (
        "这个一定要记住，一定要记住。很重要，很重要。我们再强调一遍，再强调一遍。"
        "好，我们往下讲。下一部分是反向传播，它依赖链式法则。"
        "链式法则说的是，复合函数的导数等于各层导数相乘。"
        "所以要从输出层往回走，一层一层地算梯度。"
        "算出梯度以后，再用它去更新每一层的权重参数。"
        "更新完再前向算一遍损失，看损失有没有下降。"
    )
    normalized = R.normalize(text)
    # 前提：文本里确实存在口头重复，否则这个用例测不到东西
    assert normalized.count("一定要记住") == 2

    v = R.detect(text)
    assert v.triggered is False


def test_advisory_ratio_no_longer_triggers():
    """
    旧判据降级为辅助告警：中文下比值天然偏高，且对真复读反而偏低

    两组数字都取自实测：
      · `--terms off`：56 片段 → 91 字幕，比值 1.625（无提示词，纯天然）
      · 真复读样本：56 片段 → 61 字幕，比值 1.089（却在重复 40+ 次）
    """
    natural_zh = R.subtitle_ratio_advisory(91, 56)
    assert natural_zh["ratio"] == 1.625
    assert natural_zh["aboveAdvisory"] is True  # 会告警，但不触发降级

    real_loop = R.subtitle_ratio_advisory(61, 56)
    assert real_loop["ratio"] == 1.0893
    assert real_loop["aboveAdvisory"] is False  # 旧判据在此漏报

    # 同一段真复读文本，新判据必须抓到 —— 这就是换判据的意义
    assert R.detect(LOOP_TANDEM).triggered is True


def test_ngram_granularity_by_script():
    """n 的粒度按语种选：CJK 用 8 字符，拉丁用 24（8 字符≈1.5 个词太短）"""
    assert R.pick_ngram("这是中文讲稿内容") == R.DEFAULT_NGRAM
    assert R.pick_ngram("this is an english transcript") == R.DEFAULT_LATIN_NGRAM


def test_english_common_words_not_flagged():
    """
    负样本：英文里 the/of/and 天然高频

    两个考点：
    1. 拉丁文本用 24 字符的 n（8 字符≈1.5 个词，会被常用词打满）
    2. 这段文本整体被复制了一份（全局内容重复），只扫尾部的设计**不该**因此报警 ——
       这正是「全文扫描」方案的致命伤（12 分钟英文长测是循环拼接音频做的，
       全文最长重复 746 字符，但一个解码故障都没有）。
    """
    text = (
        "The gradient of the loss with respect to the weights is computed by "
        "backpropagation, and the weights are then updated by the optimizer. "
        "The learning rate controls the size of the step, and the momentum term "
        "helps the optimizer keep moving in a consistent direction. "
        "In practice the network is trained for many epochs on the training set."
    ) * 2
    v = R.detect(text)
    assert v.triggered is False
    assert v.ngram == R.DEFAULT_LATIN_NGRAM


def test_normalize_strips_punctuation_and_space():
    assert R.normalize("同学们好，今天我们讲 CNN！") == "同学们好今天我们讲CNN"
    assert R.normalize("  a b\tc\n") == "abc"


def test_pure_helpers_edge_cases():
    """纯函数边界：空串、超长 n、无重复文本"""
    assert R.dup_ngram_ratio("", 8) == 0.0
    assert R.dup_ngram_ratio("abc", 8) == 0.0
    assert R.longest_repeat_len("") == 0
    assert R.longest_repeat_len("abcabc", lo=1) == 3
    assert R.top_repeat_gram("abc", 8) == []
    assert R.longest_repeat_len(CLEAN_LECTURE) < 20


def test_dup_ratio_counts_severity_not_just_presence():
    """
    重复率必须反映「重复了几次」而不是「有没有重复」

    两个输入**长度相同**（24 字符），只是一个重复 2 次、一个重复 6 次。
    用「去重后少了多少字符」实现的话两者差不多；用 Σ(count-1) 才能拉开差距 ——
    而严重程度正是靠这个差值判断的。
    """
    varied = "卷积神经网络反向传播梯度"
    low = R.dup_ngram_ratio("级测试" * 2 + varied, 8)
    high = R.dup_ngram_ratio("级测试" * 6 + varied[:6], 8)
    assert high > low
    assert high >= R.DEFAULT_MIN_DUP_RATIO


def test_verdict_summary_mentions_evidence():
    """结论文字里要带上证据（重复了什么、几次），排障时不用再翻日志"""
    v = R.detect(LOOP_TANDEM)
    assert "重复循环" in v.summary
    assert v.samples and v.samples[0][1] >= 2
