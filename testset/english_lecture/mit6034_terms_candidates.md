> ## ⚠️ 本文件已废弃 —— 它是一次**失败的**方法尝试，保留作为证据
>
> **方法**：纯统计抽术语（跨讲次共现次数 × 总频次），9 → 22 讲。
> **结果**：产出**全是课堂口语**（work / equal / problem / plus / stuff / actually /
> let's try / figure out / talk about）—— 口语词在**每讲都高频出现**，
> 「跨讲次共现」这个判据对它们一样成立 ⇒ 完全失效。
>
> 🔑 **结论**：**统计方法抽不出领域术语 —— 缺的是「领域先验」，不是「统计力度」。**
>
> **替代方案（已成功）**：见 `mit6034_terms.md`
> —— 「课程大纲当种子 + 转写验证」（讲次标题本身就是术语表）。

# MIT 6.034 术语候选（从官方 VTT 自动抽取）

来源：8 讲官方 VTT（`packaging/eval/ocw_vtt`）

> **判据**：出现讲次数（跨讲次共现）× 总频次 —— 跨讲次复现是「核心概念」的最直接信号；虚词/普通词已在抽取阶段剔除。

> ⚠️ **这是候选，不是定稿** —— 仍需人工筛（含中英对照）。

## 单词候选

| 词 | 讲次数 | 总频次 | 示例出处 |
|---|---|---|---|
| `that's` | 8 | 356 | `ocw_lecture-11-learning-identification-trees-disorder @216s` |
| `it's` | 8 | 340 | `ocw_lecture-11-learning-identification-trees-disorder @83s` |
| `we're` | 8 | 295 | `ocw_lecture-11-learning-identification-trees-disorder @221s` |
| `about` | 8 | 245 | `ocw_lecture-11-learning-identification-trees-disorder @22s` |
| `like` | 8 | 239 | `ocw_lecture-11-learning-identification-trees-disorder @131s` |
| `let's` | 8 | 169 | `ocw_lecture-11-learning-identification-trees-disorder @681s` |
| `over` | 8 | 141 | `ocw_lecture-11-learning-identification-trees-disorder @652s` |
| `down` | 8 | 133 | `ocw_lecture-11-learning-identification-trees-disorder @227s` |
| `out` | 8 | 133 | `ocw_lecture-11-learning-identification-trees-disorder @341s` |
| `i'm` | 8 | 129 | `ocw_lecture-11-learning-identification-trees-disorder @598s` |
| `we've` | 8 | 124 | `ocw_lecture-11-learning-identification-trees-disorder @29s` |
| `don't` | 8 | 123 | `ocw_lecture-11-learning-identification-trees-disorder @254s` |
| `want` | 8 | 116 | `ocw_lecture-11-learning-identification-trees-disorder @178s` |
| `little` | 8 | 114 | `ocw_lecture-11-learning-identification-trees-disorder @116s` |
| `there's` | 8 | 111 | `ocw_lecture-11-learning-identification-trees-disorder @116s` |
| `number` | 8 | 104 | `ocw_lecture-11-learning-identification-trees-disorder @234s` |
| `work` | 8 | 101 | `ocw_lecture-11-learning-identification-trees-disorder @153s` |
| `equal` | 8 | 98 | `ocw_lecture-11-learning-identification-trees-disorder @1462s` |
| `problem` | 8 | 90 | `ocw_lecture-11-learning-identification-trees-disorder @141s` |
| `plus` | 8 | 85 | `ocw_lecture-11-learning-identification-trees-disorder @652s` |
| `doesn't` | 8 | 83 | `ocw_lecture-11-learning-identification-trees-disorder @547s` |
| `something` | 8 | 80 | `ocw_lecture-11-learning-identification-trees-disorder @406s` |
| `into` | 8 | 77 | `ocw_lecture-11-learning-identification-trees-disorder @74s` |
| `stuff` | 8 | 76 | `ocw_lecture-11-learning-identification-trees-disorder @1403s` |
| `i've` | 8 | 73 | `ocw_lecture-11-learning-identification-trees-disorder @673s` |
| `we'll` | 8 | 68 | `ocw_lecture-11-learning-identification-trees-disorder @749s` |
| `back` | 8 | 68 | `ocw_lecture-11-learning-identification-trees-disorder @753s` |
| `another` | 8 | 66 | `ocw_lecture-11-learning-identification-trees-disorder @358s` |
| `need` | 8 | 66 | `ocw_lecture-11-learning-identification-trees-disorder @994s` |
| `minus` | 7 | 120 | `ocw_lecture-11-learning-identification-trees-disorder @856s` |
| `actually` | 8 | 58 | `ocw_lecture-11-learning-identification-trees-disorder @251s` |
| `you're` | 8 | 58 | `ocw_lecture-11-learning-identification-trees-disorder @425s` |
| `maybe` | 8 | 57 | `ocw_lecture-11-learning-identification-trees-disorder @436s` |
| `pretty` | 8 | 57 | `ocw_lecture-11-learning-identification-trees-disorder @610s` |
| `answer` | 8 | 56 | `ocw_lecture-11-learning-identification-trees-disorder @700s` |
| `off` | 8 | 56 | `ocw_lecture-11-learning-identification-trees-disorder @904s` |
| `idea` | 8 | 54 | `ocw_lecture-11-learning-identification-trees-disorder @1061s` |
| `much` | 8 | 53 | `ocw_lecture-11-learning-identification-trees-disorder @2528s` |
| `given` | 8 | 52 | `ocw_lecture-11-learning-identification-trees-disorder @371s` |
| `what's` | 8 | 50 | `ocw_lecture-11-learning-identification-trees-disorder @148s` |

## 双词短语候选

| 短语 | 讲次数 | 总频次 | 示例出处 |
|---|---|---|---|
| `let's try` | 8 | 12 | `ocw_lecture-11-learning-identification-trees-disorder @1190s` |
| `figure out` | 7 | 13 | `ocw_lecture-11-learning-identification-trees-disorder @1131s` |
| `we'll call` | 6 | 18 | `ocw_lecture-12a-neural-nets @570s` |
| `doesn't matter` | 7 | 11 | `ocw_lecture-11-learning-identification-trees-disorder @547s` |
| `talk about` | 5 | 28 | `ocw_lecture-16-learning-support-vector-machines @62s` |
| `we're trying` | 5 | 19 | `ocw_lecture-11-learning-identification-trees-disorder @713s` |
| `talking about` | 6 | 11 | `ocw_lecture-11-learning-identification-trees-disorder @382s` |
| `turns out` | 6 | 11 | `ocw_lecture-11-learning-identification-trees-disorder @2935s` |
| `something else` | 6 | 7 | `ocw_lecture-11-learning-identification-trees-disorder @406s` |
| `i'd like` | 5 | 10 | `ocw_lecture-16-learning-support-vector-machines @2321s` |
| `high school` | 5 | 9 | `ocw_lecture-11-learning-identification-trees-disorder @1740s` |
| `whole bunch` | 5 | 8 | `ocw_lecture-12a-neural-nets @513s` |
| `we'd like` | 5 | 7 | `ocw_lecture-11-learning-identification-trees-disorder @479s` |
| `don't care` | 5 | 7 | `ocw_lecture-11-learning-identification-trees-disorder @2528s` |
| `thinking about` | 5 | 6 | `ocw_lecture-11-learning-identification-trees-disorder @449s` |
| `back up` | 5 | 6 | `ocw_lecture-12a-neural-nets @2727s` |
| `difference between` | 5 | 5 | `ocw_lecture-11-learning-identification-trees-disorder @148s` |
| `little harder` | 5 | 5 | `ocw_lecture-11-learning-identification-trees-disorder @1079s` |
| `that's equal` | 4 | 8 | `ocw_lecture-12a-neural-nets @2107s` |
| `end up` | 4 | 7 | `ocw_lecture-11-learning-identification-trees-disorder @1992s` |

## 三词短语候选

| 短语 | 讲次数 | 总频次 | 示例出处 |
|---|---|---|---|
| `we're talking about` | 3 | 4 | `ocw_lecture-11-learning-identification-trees-disorder @382s` |
| `british museum algorithm` | 2 | 6 | `ocw_lecture-4-search-depth-first-hill-climbing-beam @471s` |
| `it's mathematically convenient` | 2 | 4 | `ocw_lecture-12a-neural-nets @2340s` |
| `can't tell whether` | 2 | 3 | `ocw_lecture-11-learning-identification-trees-disorder @173s` |
| `better figure out` | 2 | 2 | `ocw_lecture-16-learning-support-vector-machines @891s` |
| `without thinking about` | 2 | 2 | `ocw_lecture-16-learning-support-vector-machines @1420s` |
| `i've lost track` | 2 | 2 | `ocw_lecture-16-learning-support-vector-machines @1548s` |
| `can't talk about` | 2 | 2 | `ocw_lecture-4-search-depth-first-hill-climbing-beam @223s` |
| `gold star idea` | 2 | 2 | `ocw_lecture-4-search-depth-first-hill-climbing-beam @474s` |
| `we'll talk about` | 2 | 2 | `ocw_lecture-4-search-depth-first-hill-climbing-beam @738s` |
