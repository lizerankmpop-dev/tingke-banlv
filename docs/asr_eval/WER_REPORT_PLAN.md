# P2：ASR 质量评估补全（分场景 CER/WER 报告）—— 方案与用例准备清单

> 状态：**决策已定，进入执行**（D1~D4 见 §8；执行顺序见 §8 末）。
> 前提：本任务只改 `<repo>/scripts/`、`<repo>/testset/`、`<repo>/docs/`、`packaging/eval/`，
> **不动安装包**（边界逐条核实见同目录 `write-boundary.md`）。

---

## 0. 摘要（TL;DR）

| 问题 | 结论 |
|---|---|
| 分场景评估的**能力**有没有？ | **有，且早已实现**。`eval_asr_quality.py` 已按文件名前缀分场景汇总，`make_asr_testset.py` 已有 noise/lowvol/fast/multi 四个劣化算子 |
| 中文 5 场景（多人/噪音/低音量/口音/连读）**有素材吗**？ | **一个都没有**。中文只有 1 条干净 TTS（626s）+ 1 条真人干净（630s），`scenario` 分别是 `long` 和 `other` |
| 英文合成集的既有数字能当基准吗？ | **不能**。见 §3.1（clean 比 noise 差 18 倍、第一轮 0 字幕、每格 n=1、且跑在防循环修复之前） |
| 口音（accent）能合成吗？ | **项目已明确决定不合成**（`make_asr_testset.py` 注释：变调近似口音没有依据）。只能换素材来源 |
| 补齐中文 4 个可合成场景的成本？ | **低**：现有算子直接作用在中文 TTS 讲稿上即可，零新增依赖、零版权风险 |
| 真正的缺口 | **口音 + 真实远场录音**，这两个必须靠真人素材 |

---

## 1. 现有家底盘点（全部为机器核对结果）

### 1.1 工具链

| 脚本 | 职责 | 关键参数 | 产物 |
|---|---|---|---|
| `scripts/eval_asr_quality.py` | 端到端质量评估：**准确率（WER/CER）+ 推送延迟 + 长时稳定性**，复用真实管道的 VAD+引擎 | `--audio-dir/--audio`（互斥必填）、`--realtime`、`--language auto\|zh\|en`、`--term-language`、`--terms on\|off`、`--domain`、`--custom-terms`、`--autodegrade on\|off`、`--max-wer`、`--report`（默认 `packaging/eval/report.json`）、`--limit` | JSON 报告（`results[]` + `summary` + `verdict`） |
| `scripts/make_asr_testset.py` | **合成劣化算子**（纯 numpy+wave，零新增依赖） | `--source <音频（需同名 .txt）>`、`--out`（默认 `tests/audio`）、`--only <场景…>`、`--long-minutes`、`--seed` | `synth_<场景>_<源名>.wav` + 同名 `.txt` |
| `scripts/make_chinese_testset.py` | 中文 TTS 代理集（edge-tts） | `--out`、`--voice`、`--gap` | 12 分钟讲稿 wav + 逐字稿 txt + `metadata.json` |
| `scripts/ab_terms_compare.py` | 术语热词 A/B 运行器（把 eval 跑多遍后拉平成对照表） | `--audio-dir/--audio`、`--language`、`--domain`、`--custom-terms`、`--conditions off,on,on-noguard,simp`、`--realtime`（**长音频必加**）、`--allow-drops`、`--out` | 合并报告 + 术语命中率 |
| `scripts/analyze_zh_ab.py` | 把 A/B 报告按**错误类型**归因（复读/漏识/术语错…） | `--report`、`--out`、`--errors`、`--print-errors` | `<report>.analysis.json` |
| `scripts/check_transcript.py` | 逐字稿合规校验（**真值可用性**前置门禁） | `--audio`、`--transcript`、`--report`、`--selftest` | `packaging/eval/transcript_check.json` |
| `scripts/asr_draft_transcript.py` | 长音频分块出草稿（供人工校对） | `--audio`、`--block-seconds`、`--language`、`--terms`、`--custom-terms` | `*.draft.json/txt/srt` + `review.html` |
| `scripts/preproof_transcript.py` | 草稿 + 修正表 → 预校对定稿 | `--draft`、`--corrections`、`--tag`、`--check-only` | `*.preproof.txt/json/suspects.md` |
| `scripts/fetch_bili_audio.py` | B 站 BV 号 → 16kHz 单声道切片（纯标准库 + PyAV 解码） | 只接 BV 号/链接（匿名搜索接口被风控） | 音频切片 + `metadata.json` 登记 |
| `scripts/bench_asr.py` | 只回答「跟不跟得上」（RTF），**不测准不准** | — | — |

### 1.2 场景口径（已实现）

```python
# eval_asr_quality.py:76
SCENARIOS = ("clean", "accent", "noise", "multi", "lowvol", "fast", "long")
# eval_asr_quality.py:624  scenario_of()
#   命中规则：lowered.startswith(s)  或  "_s_" 在名字里  或  "_s." 在名字里；都不中 → "other"
```

```python
# make_asr_testset.py:52  各场景的默认强度（写在明面上便于复现）
SNR_DB            = 8.0     # noise：语音只比噪声高 8 dB
LOWVOL_PEAK_DBFS  = -30.0   # lowvol：峰值压到 -30 dBFS
FAST_FACTOR       = 1.35    # fast：1.35 倍速
OVERLAP_DELAY_S   = 0.35    # multi：自身语音延迟叠加
OVERLAP_GAIN      = 0.75
SCENARIOS = ("clean", "noise", "lowvol", "fast", "multi", "long")   # ← 没有 accent，是刻意的
```

### 1.3 度量口径（已实现）

```python
# eval_asr_quality.py:191-269
_PUNCT = 去标点用（中英文共用一张表）
normalize(text, language):
    中文（或含 CJK）→ 直接删掉所有 _PUNCT 字符
    英文          → _PUNCT → 空格、转小写、压缩空白
wer(ref, hyp, language):
    中文：ref_units = 逐**字**  → WER 与 CER 数值相同（不是 bug，是设计）
    英文：ref_units = 逐**词**  → WER 按词、CER 按字
    返回 {wer, cer, refUnits, hypUnits, edits}
```

🔴 **口径纪律（P2 报告里必须写死）**：
- **中文的主指标是 CER（字级）**；同一份报告里的 `wer` 字段是同一个数，**不得当成两个独立指标对比**。
- 英文才是 WER（词级）与 CER（字级）两个数。
- 标点被完全剔除 → **标点正确性从未被评估**（这是当前口径的固有盲区，若要评需另立指标）。

### 1.4 素材家底

| 素材 | 路径 | 时长 | 语言 | 条件 | 参考文本 | 入库？ |
|---|---|---|---|---|---|---|
| 英文合成短集（5 格） | `tests/audio/short/synth_{clean,fast,lowvol,multi,noise}_jfk.wav` | 各 ~11s | 英 | 干净/快/低音量/叠话/噪声 | 同名 `.txt`（22 词） | ❌ gitignore（可由脚本重建） |
| 英文合成长集 | `tests/audio/long/synth_long_jfk.wav` | ~12min | 英 | 干净（拼接） | 同名 `.txt` | ❌ gitignore |
| 中文 TTS 代理集 | `testset/chinese_lecture/long/long_zhlecture_cnn.wav` | 626.41s | 中 | 干净（无噪声/无口音/无口误/无混响） | 同源逐字稿（3153 字） | 音频 ❌／txt ✅ |
| 中文真人课 | `testset/chinese_lecture/real/real_zhlecture_origin_plot.wav` | 630.0s | 中 | 单人授课、远场、停顿多（静音占比 27.6%） | `*.preproof.txt` 定稿（35 段/2791 字） | 音频 ❌／逐字稿 ❌（只有 README + metadata + corrections.tsv 入库） |

素材元数据实测：中文 TTS `RMS -27.2 dBFS / 估 SNR 42.8 dB / 峰值 -1.4 dBFS`；
真人课 `RMS -30.9 dBFS / 峰值 -5.58 dBFS / 静音占比 27.6%`。

### 1.5 历史报告存量

`packaging/eval/` 有 **60+ 个** JSON/日志（全部 gitignore）。其中能用的只有：

| 报告 | 内容 | 可用性 |
|---|---|---|
| `long-realtime.json` | 英文 12min 合成，`meanWer 0.0019 / meanCer 0.0005` | ✅ 可用（素材与参考文本同源） |
| `short.json` | 英文 5 短格 | ❌ **无效轮次**：`totalSubtitles=0` → 每格 WER 恒 `1.0`（一个字幕都没出） |
| `short-terms.json` | 英文 5 短格（第二轮） | ⚠️ 见 §3.1，**不可作基准** |

---

## 2. 目标口径定义（P2 要产出的东西）

### 2.1 报告形态

一份**按场景 × 按来源**的中文 CER 表，外加英文对照，形如：

| 场景 | 可操作判据 | 覆盖素材 | 主指标 | 次指标 |
|---|---|---|---|---|
| clean | 无附加劣化 | 中文 TTS / 真人课 | **CER** | RTF、丢片数、合并次数 |
| noise | 加 babble 噪声至 SNR = 8 dB | 待造 | **CER** | 同上 |
| lowvol | 峰值归一化到 −30 dBFS | 待造 | **CER** | **片段数**（远场小音量最可能整段丢） |
| multi | 自身语音延迟 0.35s、增益 0.75 叠加 | 待造 | **CER** | 片段数/字幕数（VAD 会被叠话切碎） |
| fast | 1.35× 变速 | 待造 | **CER** | 片段数、合并次数、尾延迟 |
| accent | 带口音的普通话（**必须真人或方言 TTS**） | 待补 | **CER** | 同上 |
| long | ≥10min 连续 | 中文 TTS / 真人课 | **CER** | 尾延迟、后半段延迟漂移、丢片数 |

### 2.2 判据必须可复现

每个场景的强度是**常量**（§1.2 已写死在脚本里），P2 报告必须把
「算子名 + 常量值 + 随机种子」一起写进文档 —— 否则下一个人改一个数字就复现不出来了。

---

## 3. 现状诊断：三个必须知道的坑

### 3.1 🔴 英文合成短集的既有数字**自相矛盾**，不能当基准

`short-terms.json`（2026-10-06）的 `byScenario` 实测：

| 场景 | WER | CER | refUnits | hypUnits |
|---|---|---|---|---|
| **clean** | **0.8182** | 0.7711 | 22 | **40** |
| fast | 0.2273 | 0.2651 | 22 | 17 |
| multi | 0.3636 | 0.3976 | 22 | 19 |
| **lowvol** | **1.0909** | 1.1566 | 22 | **46** |
| **noise** | **0.0455** | 0.0241 | 22 | 22 |

**「干净」比「噪声」差 18 倍** —— 这在物理上不可能，说明这批数字量的是别的东西：

1. **`hypUnits` 膨胀**（clean 40 > ref 22、lowvol 46 > ref 22）=
   识别文本比参考文本还长 → 典型的**重复/续写**。该轮跑在 2026-10-06，
   **早于回显清洗与防循环兜底**（那是 10-07 之后的事）。
2. **素材与参考文本的固有错配**：JFK 源只有 11 秒，而 Whisper 对这段名句有强先验，
   会把后半句补出来 → 与「录音条件」无关的误差，**在所有格上等量存在**。
3. **每格 n = 1**（5 个文件 = 5 个场景），单文件 WER 抖动主导，无法做统计推断。
4. **第一轮 `short.json` 整轮无效**（`totalSubtitles = 0`，每格 WER 恒 1.0）。

⇒ **结论：P2 的基准必须新跑**，且不能沿用「11 秒名句 + 只写前几句」这种素材模式。

### 3.2 🔴 中文场景覆盖 = 0，但补齐成本很低

`make_asr_testset.py` 的四个劣化算子是**语言无关的纯信号处理**：

```python
read_audio()      # PyAV/标准库皆可
peak_norm()       # 峰值归一化（lowvol）
babble_noise()    # babble 噪声合成
add_noise()       # 按 SNR 叠加（noise）
_resample()       # 变速（fast）
overlap_self()    # 自身延迟叠加（multi）
build_long()      # 长时拼接（long）
```

它只要求 `--source` 旁边有**同名 `.txt`**。
而中文 TTS 集**天然满足**（`long_zhlecture_cnn.txt` = 生成用的讲稿，精确无错配）。
⇒ 一条命令就能造出中文的 noise/lowvol/fast/multi 四格，零新增依赖、零版权风险。

### 3.3 口音（accent）是**唯一无法合成**的场景

`make_asr_testset.py` 第 24 行注释已经把话说死：

> `accent_` 场景**刻意不合成**。用变调去近似口音没有任何依据，
> 与其产出一份看起来像数据、实际毫无意义的 WER，不如留空等真录音。

并且 `eval_asr_quality.py:76` 的 `SCENARIOS` 里有 `accent`，
而 `make_asr_testset.py:58` 的 `SCENARIOS` 里**没有**（`--only accent` 会被显式跳过）。
⇒ 口径有、素材空 —— **这是 P2 最需要用户拍板的一项**。

---

## 4. 三条素材路线

### 方案 A：中文 TTS 讲稿 + 现有劣化算子（推荐做主线）

```
scripts/make_asr_testset.py \
    --source testset/chinese_lecture/long/long_zhlecture_cnn.wav \
    --out <新目录>  --only clean noise lowvol fast multi
```

| 项 | 评价 |
|---|---|
| 成本 | 极低：一条命令 + 一台机器跑几分钟 |
| 新增依赖 | 无 |
| 版权风险 | 无（自建讲稿 + 自建劣化） |
| 真值 | **精确且免费**（同源文本，天然无错配） |
| 能回答 | VAD 在低 SNR 下还切不切得准？远场小音量会不会整段丢？快语速会不会触发合并/丢片？ |
| **不能**回答 | **真实课堂的识别准确率**（合成的噪声/叠话与真人差得远，且 TTS 无口误、无自我纠正、无混响） |
| 必须写进报告的免责 | 「合成劣化 ≠ 真实现场」；`metadata.json` 里已有同类措辞可延用（`make_chinese_testset.py` 生成的 `warning` 字段就是这个口径） |

### 方案 B：真人中文课多段录音（最真实，最贵）

用 `fetch_bili_audio.py` 取多段**条件不同**的真人课（教室内录、教师麦、学生提问插话、语速快、普通话不标准），
每段走 `asr_draft_transcript.py → 人工校对 → preproof` 出真值。

| 项 | 评价 |
|---|---|
| 成本 | 高：**每段 10 分钟都要人工校对**（现有那条 630s 的定稿花了整条 P3 的人力，人工修改率 7.2%） |
| 版权 | 音频与逐字稿**都不入包**（现状已是白名单屏蔽 + metadata 登记 BV 号） |
| 能回答 | 真实现场的准确率 —— 这是唯一有对外说服力的数字 |
| 风险 | 匿名搜索不可用 → 素材必须**由人挑 BV 号**，机器不能自动选 |

### 方案 C：A + 1 段真人锚点（折中，建议）

方案 A 覆盖 4 个可合成场景拿到**管道鲁棒性**的量化结论；
再加**一段真人**（优先选「普通话不标准 + 远场」的那类课）同时补上 `accent` 与真实场地的锚点。
——即：**A 证明「管道扛不扛得住」，C 的真人段证明「真实场景准不准」，两者不混在一个均值里**。

---

## 5. harness 需补的能力（清单，全部落 `scripts/`，不改包）

| # | 缺口 | 建议改动 | 风险 |
|---|---|---|---|
| H1 | ✅ **已完成（#170 `924c1be`）**：来源维度缺失 → 结果加 `sourceKind`，汇总按 `(sourceKind, scenario)` 二维分组 | 实现取 **`tts-synth` / `human-recording` / `unknown`**（**偏离原建议**：弃 `tts-degraded`——劣化与否已由 `scenario` 表达，两维不该互相编码；弃 `synth-en`——语言由 `--language` 表达，且实测里「语言」不改变「是合成还是真人」这个事实） | 低（additive：`byScenario` 逐字未动 ⇒ `analyze_zh_ab.py` 无需同步） |
| H2 | ✅ **已完成（#170 `924c1be`）**：加 `--max-cer` | 逐样本标记 `rejected`，**不中断批量评估**（与 `--max-wer` 的「整轮均值门禁」分工不同，两者并存） | 低 |
| H3 | 长素材做场景矩阵太慢（4 场景 × 10min，`--realtime` 下约 40min/轮） | **已定实现路径（见 §6.1 的实测）**：不做「模糊的 `--clip-seconds`」，改为**按段落静音边界切分**——切出来的音频与文本都来自同一份内置讲稿，配对天然精确 | 低（但必须先证边界数对得上，已证：29 = 30−1） |
| **H4** | ✅ **已完成（#171，见 §12）**：无效轮次防护不均匀（已核实）：`ab_terms_compare.py:435` **有**「背压丢弃 → 默认中止」的守卫（`--allow-drops` 才继续）；但**没有**「`totalSubtitles == 0` → 中止」的守卫（全文不引用 `verdict`/`passed`） | 加同构守卫 `invalid_round_reason()`：`totalSegments==0 或 totalSubtitles==0` → 中止（`--allow-invalid` 放行）。**按数据修正原建议**：`verdict.passed==false` **不**作为中止条件（42 份历史子报告里 10 份 passed=False，其中非背压的 2 份都是「RTF≥1」这类如实记录，准确率数据有效） | 中（这正是 `short.json` 那种「整轮一个字幕没出、每格 WER 恒 1.0」还能进对照表的通道） |
| H5 | ✅ **已核实无缺口**：报告的 `recognition` = 全部字幕文本 `" ".join(...)`（`eval_asr_quality.py:553/583`），**无截断**；`--detail-limit 200` 只作用于 `segmentDetail` | 无需改动（错误归因可直接用 `recognition` vs `reference`） | — |
| H6 | 素材指纹未进入报告 | 把**音频 sha256 + 参考文本 sha256** 写进报告（教训 29：对照实验先证「被操纵的变量真变了」；P1 的 `artifact` 字段就是这个思路） | 低～中 |

🔴 **H1 是硬需求**（**已于 #170 落地，见 §11**）：如果不加来源维度，方案 C 的「合成」与「真人」数字会被平均成一个没有意义的数
—— 这正是本仓库反复踩过的「口径混用」（见 `LESSONS.md` §10 / §35）。

---

## 6. 用例准备清单（可勾选执行）

### 6.1 素材（方案 A 主线）

#### ★ 短样本怎么切：实测结论（D3 的实现依据）

「给 `make_asr_testset.py` 加个 `--clip-seconds`」这个直觉做法**不可行**，原因是实测出来的：

| 实测项 | 结果 |
|---|---|
| `long_zhlecture_cnn.txt` 的结构 | **只有 1 个非空行 / 3153 字** —— 整篇讲稿**没有任何段落分隔符**。按行切片拿不到段落 |
| 音频里的段落边界 | 10ms 帧 RMS + 阈值 2% 峰值找静音段：**静音 ≥1.1s 的段数 = 29 = `len(LECTURE) − 1`**（阈值 1.1s 与 1.2s 都是 29；0.9s/1.0s 会多切出 1 段）→ **段落边界可从静音精确恢复** |
| 文本真值在哪 | `scripts/make_chinese_testset.py` 内置 `LECTURE`（30 段，汉字 2680） |
| `txt` 与 `LECTURE` 的关系 | **`txt == ''.join(LECTURE).replace(' ', '')`，逐字符相等**（LECTURE 里有 30 个空格，正好是长度差 30） |

⇒ **零新增依赖的切分法**：取 `LECTURE[:k]` 作为文本、音频切到第 k 个段落边界（该边界右侧留足静音，避免切断字尾）。
两者同源，**配对精确性由构造保证**，不靠人工对齐。
（顺带：`_PUNCT` 里含空格，`normalize()` 对中文会把空格一并删掉 → 短样本文本要不要带空格**对 CER 无影响**。）

- [ ] 先写切分工具（建议 `<repo>/scripts/clip_tts_paragraphs.py`，只读长素材、写短样本 + 精确文本）
- [ ] **切完必须自证配对正确**：对切出的短样本跑一次 `eval_asr_quality.py`，CER 应与整篇同一量级
      （配对错了 CER 会爆掉 —— 这是「先证被操纵的变量真变了」的可操作形式）；
      同时断言「切出音频里 ≥1.1s 静音段数 == k − 1」
- [ ] 目标时长 60~90s（按前 3 段约 291 字、语速 ~4.4 字/秒推算，3~4 段约 66~90s）

#### 劣化（方案 A 主线）

- [ ] 确认目标目录（建议 `testset/chinese_lecture/scenarios/`；`testset/**/*.wav` 已被 gitignore 屏蔽，音频不入库 ✅）
- [ ] 跑 `make_asr_testset.py --source <短样本>.wav --out …`（算子语言无关，中文可直接用）
- [ ] 核对产物：`synth_{clean,noise,lowvol,fast,multi}_<短样本名>.wav` **+ 同名 `.txt`**（参考文本随劣化复制，必须逐字节等于源 txt）
- [ ] 跑 `check_transcript.py --audio <每个 wav>` 确认逐字稿仍合规。⚠️ 变速会改语速：源 ~4.4 字/秒，`fast` 格 1.35× ⇒ ~5.9 字/秒，仍在 `CPS_RANGE (1.2~8.0)` 内；但 `check_transcript.py` 算的是**字数/音频时长**，fast 格音频变短、字数不变 ⇒ **必须真跑一遍确认，不得推断**
- [ ] 记录每个 wav 的 `sha256 + 时长 + RMS/峰值` 写进 `metadata.json`（可复用 `make_chinese_testset.py` 的统计口径）

### 6.2 基线跑（每格必须**一次调用一份报告**）

- [ ] 逐格跑 `eval_asr_quality.py --audio <wav> --language zh --terms on --realtime --report packaging/eval/p2_<场景>.json`
       ⚠️ 一格一份报告，不要把多格塞进一次调用（休眠/挂起会摧毁实时喂入 → 教训见 `LESSONS.md`）
- [ ] 每格同时跑一次 `--terms off` 作为 A/B 基线（术语收益必须落在具体词上，用 `ab_terms_compare.py` 更省事）
- [ ] 汇总：把每格报告的 `meanCer / meanWer / maxTailSeconds / totalDropped / totalMerged / filesWithMoreSubtitlesThanSegments` 抄进 §7 的表
- [ ] **抄录纪律**：只能抄**产物字段值**，不得按输入侧推断（`LESSONS.md` §10）

### 6.3 真人锚点（方案 C，可选）

- [ ] 由用户挑 BV 号（匿名搜索不可用）
- [ ] `fetch_bili_audio.py` 取切片 → `asr_draft_transcript.py` 出草稿
- [ ] 人工校对 → `preproof_transcript.py` 出定稿 → `check_transcript.py` 全 OK
- [ ] 只在**校对完成后**才纳入报告（拿草稿当参考文本 = 测自己的输出，`check_transcript.py` 第 7 条就是防这个）

### 6.4 口音格（**已定：留空**）

- [x] **决策 D2 = 显式留空并登记为已知缺口**（不用任何近似手段冒充）
- [ ] 在报告里写明「`accent` 未覆盖」及其理由（`make_asr_testset.py:24` 已论证「变调近似口音没有依据」）
- [ ] 若日后恰好拿到合适真人素材（普通话不标准 + 远场），顺带补进 6.3 的锚点段（届时才需要改 `--source-kind real`）

---

## 7. 报告表样（跑完填）

| 来源（sourceKind） | 场景 | n | CER | WER | 丢片 | 合并 | 字幕>片段 | 尾延迟 s | RTF |
|---|---|---|---|---|---|---|---|---|---|
| tts-degraded | clean | | | | | | | | |
| tts-degraded | noise(8dB) | | | | | | | | |
| tts-degraded | lowvol(−30dBFS) | | | | | | | | |
| tts-degraded | fast(1.35×) | | | | | | | | |
| tts-degraded | multi(0.35s/0.75) | | | | | | | | |
| tts | long（整篇 626s，长时稳定性） | | | | | | | | |
| real | other（真人课 630s） | | | | | | | | |
| real | accent | **未覆盖（已登记缺口）** | — | — | — | — | — | — | — |

> 空单元格 = 尚未测得。**不得填 0、不得填推断值。**

---

## 8. 决策记录（2026-10-10 已拍板）

| # | 决策 | **已定** | 落地要点 |
|---|---|---|---|
| **D1** | 中文场景素材路线 | **C：合成 + 真人锚点** | 合成 4 格（noise/lowvol/fast/multi）拿管道鲁棒性；再补 1 段真人课做真实锚点。**两类数字分开统计，合并不出一个均值**（依赖 H1） |
| **D2** | `accent` 格 | **显式留空 + 登记为缺口** | 报告里写明未覆盖及理由；拿到合适真人素材再顺带补 |
| **D3** | 是否先做短样本 | **是（60~90s）** | 实现路径见 §6.1「★ 短样本怎么切」——**按段落静音边界切**，零新增依赖；10min 只留 clean/long 测长时稳定性 |
| **D4** | 是否改 harness | **改：H1 + H4** | H1 加 `sourceKind` 并新增 `bySourceScenario`（**additive，不动既有 `byScenario`**，避免破坏 `analyze_zh_ab.py`）；H4 给 `ab_terms_compare.py` 补「0 字幕 → 中止」守卫 |

### 由此确定的执行顺序

1. **H3 前置**：切分工具（段落静音边界）→ 产出 60~90s 短样本 + 精确文本 → **自证配对**（CER 量级 + 静音段数断言）
2. **H1**：`eval_asr_quality.py` 加 `sourceKind`（`--source-kind auto|<label>`，auto 按路径/文件名启发式）+ 汇总新增 `bySourceScenario`
3. **H4**：`ab_terms_compare.py` 加无效轮次守卫
4. **H2**（顺手）：加 `--max-cer`
5. 造中文 4 格 → 逐格跑基线（一格一份报告）→ 填 §7 表
6. 真人锚点（用户挑 BV 号后）

> ⚠️ 全部改动落在 `<repo>/scripts/` 与 `<repo>/testset/` —— **不进安装包**（`write-boundary.md`）；
> 但每加一个文件都会让**双副本对拍基准** +1，改完必须 `cp` 同步镜像并重跑 `_mirror_check.py`。

---

## 9. 不做的事（明确划界）

- ❌ 不用「变调」造口音（`make_asr_testset.py` 已写死不做，理由充分）
- ❌ 不把合成劣化的 CER 说成「真实课堂的识别准确率」
- ❌ 不把 `check_transcript.py` 未通过的逐字稿当参考文本
- ❌ 不用 11 秒名句类素材建基准（§3.1 的三个坑）
- ❌ 不改 `backend/`、`frontend/`（会改安装包 → 作废 v3.4.1 证据；见 `write-boundary.md`）
- ❌ 不把 `packaging/eval/` 的报告当可入库证据（该目录 gitignore）→ 关键数字必须落到**仓库内的 md**

---

## 10. #169 执行记录：切分工具 + 首轮 CER 量级验证（2026-10-10）

### 10.1 工具

`scripts/clip_tts_paragraphs.py`（新增；只读长素材 → 写短样本；零新增依赖：numpy + 标准库 wave）

```bash
<venv>/python scripts/clip_tts_paragraphs.py \
    --audio testset/chinese_lecture/long/long_zhlecture_cnn.wav \
    --paragraphs 4 --out-dir testset/chinese_lecture/short
```

内置**三项自证**，任一不过就 `exit 1` 且不写产物（工具必须能自证，否则不许用它产出的素材）：

| # | 断言 | 本次实测 |
|---|---|---|
| ① | 整篇「静音 ≥`--gap-min` 的段数 == `len(LECTURE) − 1`」 | **29 == 29** ✓ |
| ② | 切片内部「静音 ≥`--gap-min` 的段数 == k − 1」 | **3 == 3** ✓ |
| ③ | 参考文本 == `''.join(LECTURE[:k]).replace(' ', '')` | 414 字 ✓ |

另有第 4 条**独立交叉校验**（不在脚本内，手工核）：**短文本 == 官方长 txt 的前 414 字（逐字符相等）**，
且长 txt 第 415 字起是「滑动的步长我们称为str…」（= 第 5 段开头）⇒ 切点落在正确的段落边界上。

> 设计要点：切口取「第 k 个段落边界 + 0.20s」——保留一点静音**保护字尾**，
> 又**短于 `gap_min`**，不会被自己的静音检测误数成一个边界（`--tail-keep` 必须 < `--gap-min`，脚本会拦）。
> 断言②只看「内部」静音（排除贴头/贴尾 0.3s 内的段），否则尾静音会恒多算 1 —— 工具开始骗自己。

### 10.2 产物与指纹

```
testset/chinese_lecture/short/short_zhlecture_cnn.wav   80.52s  2,576,684 B   （gitignore）
testset/chinese_lecture/short/short_zhlecture_cnn.txt   414 字                （入库）
testset/chinese_lecture/short/metadata.json            切片参数 + 双份 sha256 （入库）
```

| 字段 | 值 |
|---|---|
| `clip.paragraphs` / `cutSeconds` / `boundarySeconds` / `boundarySilenceSeconds` | 4 / **80.52** / 80.32 / 1.34 |
| `clip.internalGaps` | 3 |
| `clip.durationSeconds` / `rmsDbfs` / `peakDbfs` | 80.52 / **−27.37** / −2.27 |
| `clip.audioSha256` | `c8b672e46eddbed966aa99efda4d01fe1f3a94f6ee151821f99a93366931c3b4` |
| `clip.referenceSha256` | `db33812cc181f180729ea9104613dd2b1af007b21d87352e2fa048006b4d7c20` |
| `source.audioSha256`（长素材） | `5d823fc76cc9e683c8213595b1960d221f2c690e3a32e6ec9ca00d8c812ebbd2` |

### 10.3 配对正确性的直接证据（首尾对齐）

`eval_asr_quality.py` 报告的 `reference` vs `recognition`（未做任何繁简转换）：

```
ref 首: 同学们好，今天这节课我们来讲卷积神经网络，以及它背后最重要的训练算法，反向传播。
hyp 首: 同学们好,今天这节课我们来讲《卷机神经》网络,以及它背后最重要的训练算法,反向传…
ref 末: 图上反复使用，这就是所谓参数共享，它让模型的参数量大幅下降。
hyp 末: 反复使用, 这就是所谓参数共享, 它让模型的参数量大幅下降。
```

**首尾都对得上** —— 若切分配错，CER 会直接爆到接近 1.0，且首尾必然错位。这是「先证被操纵的变量真的变了」的可操作形式（教训 29）。

### 10.4 首轮 CER 量级（干净 TTS 短样本）

命令：`eval_asr_quality.py --audio short_zhlecture_cnn.wav --language zh --terms on --realtime`
（⚠️ 未传 `--domain`/`--custom-terms` ⇒ 实测**提示词 0 字符**，所以本次是**无术语注入口径**）

| 指标 | 实测值 |
|---|---|
| **CER / WER** | **0.1847 / 0.1847**（中文按字，两者同值） |
| `refUnits` / `hypUnits` / `edits` | 379 / 356 / 70 |
| 片段 → 字幕 | 6 → 9（`subtitles > segments`，verdict 记 ⚠） |
| 丢弃 / 合并 / 收尾 | 0 / 0 / `drained=True`，`tailSeconds` 2.271 |
| `referenceSource` | `short_zhlecture_cnn.txt`（短样本自己的 txt，非 preproof） |
| 提示词 | 0 字符，`分层条目数 {'custom':0,'discovered':0,'domain':0,'phrases':0}`，术语语种 zh |
| verdict | `passed = true` |

**量级判读**：落在 P3 已建立的干净中文 TTS 基线上——`on`（799 字符提示词）原始 CER `0.1876`／繁转简后 `0.1862`。
本机 0.1847 与之同一量级（Δ ≤ 0.003，远小于已记录的 run-to-run 波动 ±0.03~0.05），**不是 1.0 那种配对失败的形态**。

🔴 **不得跨素材比较**：P3 那批的 `off` 原始 CER 是 **0.5182**，看着比 0.1847 差得多，
但那是**全网课 626s + 2026-10-06 的旧代码**（繁体占比 34.4%）。本次短样本与它**不是同一个东西**，
两者相减没有意义（教训 19）。

### 10.5 逐字稿合规（`check_transcript.py`）

```
[ OK ] 非空: 1 段 / 414 字        [ OK ] 字数下限: 414 字 ≥ 200
[ OK ] 语速区间: 414 字 / 1.34 分钟 = 5.14 字/秒（合理区间 1.2~8.0）
[ OK ] 未校对痕迹: 无占位标记      [ OK ] 重复段: 无异常重复
[ OK ] 尾部完整性: 末段正常        结论：可用 ✓
```
（「人工校对痕迹」项**跳过**：短样本没有 `*.draft.json`。这是 TTS 同源文本的正常情况，非缺陷。）

### 10.6 官方分析器分解（`analyze_zh_ab.py`）

| 项 | 实测 |
|---|---|
| CER → 繁转简后 | **0.1847 → 0.1847**（等值 ⇒ 输出本就是简体，**无繁简虚高**） |
| 识别文本繁体字占比 | **0.00%**（自测 345 个汉字里 0 个繁体形态） |
| 最长重复子串 | **0 字** |
| 「字幕数/片段数」判据 | **1.5，会触发** —— 但最长重复 0 字 ⇒ **误报**（与 P3 §6.8 的 `off` 轮 1.6607 触发同类） |
| 中文术语命中率 | **0.3684**（内置 `@zh` 词表比对，**无术语注入**下） |
| 漏词清单 | 卷积 0/5 · 激活函数 0/3 · 卷积核 0/2 · 收敛 0/1 · 卷积神经网络 0/1 |
| 典型错误 | `convolution` **整块 delete（漏识）**、`激活函数`→`几乎韩术`、`卷积核`→`球盒卷机盒`、`relu`→`锐炉` |

**判读**：错误集中在**英文原词**（`convolution` 整块漏掉、`relu`→`锐炉`）与中文术语上——
这与 P3 的既有结论一致（`base` 模型对中文专业课「基本不可用」），也说明 P2 的场景矩阵有诊断价值。

### 10.7 结论

- ✅ **切分工具可用**：三项自证 + 一项独立交叉校验 + 首尾对齐，四重证据下配对无疑义。
- ✅ **短样本（4 段 / 80.52s / 414 字）通过全部前置校验**，可作为 #172 的劣化输入。
- ⚠️ 本次是**无术语注入**口径（提示词 0 字符）；术语收益要等 `ab_terms_compare.py` 的 off/on 对照（且它需要 H4 守卫先落地）。
- ⚠️ 「字幕数 > 片段数」判据在该素材上会**误报**，解读时必须同时看「最长重复子串」——单看计数比会给假警报。
- 下一步：**#170**（`sourceKind` + `bySourceScenario` + `--max-cer`）→ **#171**（0 字幕守卫）→ **#172**（劣化 5 格）→ **#173**（逐格基线）。

---

## 11. #170 执行记录：sourceKind / bySourceScenario / `--max-cer`（2026-10-10）

提交 `924c1be`（`git describe = v3.4.1-5-g924c1be`）。**改动只在 `scripts/` + `backend/tests/` +
`packaging/verify/` + `.gitignore`，不进包**；交付物指纹逐位未变（见 §11.5）。

### 11.1 为什么不能直接编辑 `eval_asr_quality.py`

该文件是**纯 CRLF**（改前实测 `bytes=46,234 crlf=995 lone_lf=0`）。
按仓库纪律走补丁脚本 `安装空间/_patch_eval_source_kind.py`：

1. 归一 CRLF→LF（原始 `lone_lf=0` ⇒ 「全 CRLF → 全 LF → 全 CRLF」是无损往返）；
2. **逐项断言锚点命中次数**（11 处，任何一处不符即 `exit 1` 且**不写盘**）；
3. 换回 CRLF 写回 → 复验 `lone_lf=0` → `py_compile`。

改后：`bytes=59,292 crlf=1256 lone_lf=0`。首轮**只读预演**先跑一遍确认 11 处全命中才动手。

### 11.2 三项功能的口径（实现与原建议的差异）

| 项 | 实现 |
|---|---|
| `sourceKind` | `tts-synth` / `human-recording` / `unknown`。优先级：`--source-kind` ＞ metadata 的 `sourceKind` 字段 ＞ metadata 的 `kind` 映射（`PROXY`/`CLIP-FROM-TTS`/`SYNTH`→TTS，`REAL`→真人） ＞ `unknown` |
| metadata 查找 | 从样本目录**向上最多 2 层**找 `metadata.json`（`long/` 自己没有、登记在上一级；再往上是仓库根，串过去就是无声串桶）。坏 JSON / 非对象 → 当「取不到」，**不抛异常** |
| 未登记的 `kind` | 落 `unknown` **且不猜**，判据里写明「未登记映射」——猜出来的来源比没有来源更坏 |
| `sourceKindBasis` | 每条样本落盘判据（事后可审计，与 `referenceSource` 同一理由） |
| `bySourceScenario` | `sourceKind + scenario` 二维；每格 `files` + `{mean,min,max,range}` + `meanRtf`。**additive**：`byScenario` 一字未动 |
| 附加账目 | `sourceKindCounts`（**文件数**，不是场景格数）/ `rejectedCount` / `rejectedFiles` / `cerUndecidedFiles` |
| `--max-cer` | 逐样本标记；**恰好等于阈值不算超**（`>` 非 `>=`）；无参考文本 → `rejected = None`（**未判定，≠ `False`**）；**不中断批量评估** |
| 表格 | 104 → 113 字符宽，加「来源 / CER / 拒」；「拒」四态：`-` 未给参数 ｜ `·` 判过且通过 ｜ `拒` 超阈值 ｜ `?` 无参考文本未判定 |

### 11.3 实跑验证（本机真跑，用户指定四项）

命令口径同 §10（`--language zh --terms on --realtime`）：

1. ✅ **中文短样本**：`来源` 列 = `tts-synth`；`· other`（`byScenario`）与
   `◆ tts-synth other`（`bySourceScenario`）**两块表并存**。
2. ✅ **阈值守卫**：`--max-cer 0.18` → 该样本 CER **0.1847** > 0.18 → 表格「拒」列 = `拒`、
   结论 `✗ 1 个样本 CER > 阈值 0.18`、**exit=1**；
   `--max-cer 0.19` → 「拒」列 = `·`、结论 `✓ 全部有参考文本的样本 CER ≤ 阈值 0.19`、**exit=0**。
   两轮 CER **逐位一致 0.1847**，与 #169 基线相同。
3. ✅ **英文旧测试集**（`tests/audio/short`，5 文件）：脚本正常执行（exit=0），
   `来源` 列全 `unknown`，并显式提示「5 个文件未识别出来源类型…这不是错误（旧素材没有 metadata 是常态）」。
4. ✅ **`byScenario` 无回归** —— 用**回灌**取证（见 §11.4），不靠重跑。

### 11.4 回归证据：为什么用「历史产物回灌」而不是重跑（→ 教训 44）

重跑会同时改动「代码」与「模型 run-to-run 波动（实测 ≈0.03~0.05 CER）」——
数字对不上时**无法区分是改坏了还是模型抖了**（违反教训 29：先证被操纵的变量真变了）。

做法：把历史报告的 `results` 数组**原样**喂给改后的 `summarize()`，与历史
`summary.byScenario` **逐字比较**。变量只有代码一个。

| 历史报告 | 文件数 | `byScenario` 回灌结果 |
|---|---|---|
| `short.json` | 5 | ✅ 逐字相同（`clean` 1.0 / `fast` 1.0 / … 那轮整轮无效但结构一致） |
| `short-terms.json` | 5 | ✅ 逐字相同（`clean` 0.8182 / `noise` 0.0455 …） |
| `long-realtime.json` | 1 | ✅ 逐字相同（`long` 0.0019） |
| `p2_probe_short_clean.json` | 1 | ✅ 逐字相同（`other` 0.1847） |

**顺带白赚的一条**：旧报告的 `results` 里**根本没有 `sourceKind` 键**（实测
`'sourceKind' in results[0] == False`），回灌后整批落 `unknown` 且不抛异常 ——
这才是「兼容旧测试集」的证据，而不是嘴上声明。
已落成常驻用例 `test_replay_history_by_scenario_is_identical`（4 个参数化用例 + `skipif`，
gitignore 的产物不在本机就跳过，**不假装通过**）。

### 11.5 测试、探针与产物校验

| 项 | 实测 |
|---|---|
| 全量 pytest | **306 → 353 通过 / 1 跳过**（新增 47 条，全绿） |
| 新增用例 | `backend/tests/test_eval_source_kind.py`（652 行）：find_metadata 边界（含「第 3 层必须找不到」）/ 来源解析全分支 / 门禁三态与边界 / 两维分桶 / 拒绝账目 / verdict / **AST 接线守卫**（纯函数写了没人调 = 死功能且不会有任何用例变红） |
| 变异探针 | `packaging/verify/probe_eval_source_kind_mutations.py` **24/24 全部被抓住**（前置「真身全绿」不变量校验；`.gitignore` 反选例外 5 → 6） |
| 行尾 | `eval_asr_quality.py` 保持纯 CRLF；两个新文件纯 LF（`crlf=0`） |
| 无人解析 stdout | 表头列数改了；全仓 grep 确认**没有**消费该脚本 stdout 的地方；`summary` 的消费方（`analyze_zh_ab.py` / `ab_terms_compare.py`）都按 key 取值 → additive 安全 |
| 双副本对拍 | **160 → 162**，`same=162 diff=0 miss=0` |
| 交付物指纹 | `302,570,573 B` / md5 `bf0af680c3851e3513fe4a3294a4413f` / sha256 `4a92fee21a7949ee5a6ade1376770b703fbdeaf13514d96e027187a3faf9060a` / mtime `2026-10-10 13:16:45` —— **全部未变** |

### 11.6 结论与已知局限（诚实记账）

- ✅ 三项功能落地，`byScenario` 无回归（回灌逐字相同），阈值守卫两端行为符合预期。
- ⚠️ 短样本 `short_zhlecture_cnn.wav` 落在 **`scenario=other`** —— `scenario_of()` 按文件名
  **前缀**判别，`short_*` 不匹配任何场景。**#172 生成场景集时按 `clean_*` / `noise_*` / … 命名即归位**，
  本阶段不重命名（改名会牵动 metadata / 文档 / 工具默认参数）。
- ⚠️ 「字幕数 > 片段数」告警在该素材上仍触发，属 §10.6 已记的**误报**（最长重复 0 字）；
  解读必须同时看「最长重复子串」，单看计数比会给假警报。
- ⚠️ 探针**未覆盖** `test_summarize_uses_once_per_result` 这条结构守卫 —— 要造一个
  「少遍历但仍自洽」的变异并不自然，故诚实记账而不硬凑。
- 下一步：**#171**（`ab_terms_compare.py` 补「0 字幕 → 提前中止」守卫，堵 H4）→
  **#172**（中文 5 格场景集 + 指纹登记）→ **#173**（逐格基线填 §7 表）。

---

## 12. #171 执行记录：堵住「无效轮次进对照表」的通道（H4，2026-10-10）

提交见 `#171`（紧随 `924c1be`）。改动只在 `scripts/` + `backend/tests/` +
`packaging/verify/` + `.gitignore`，**不进包**。

### 12.1 判据：拦什么、不拦什么（**偏离 H4 原建议，按数据修正**）

§5 的 H4 原建议是「`totalSubtitles == 0` **或** `verdict.passed == false` → 中止」。
盘了全仓 **42 份历史子报告**后发现后半条**过拦**：

| `passed=False` 的子报告 | 份数 | 实际原因 | 该不该拦 |
|---|---|---|---|
| 背压丢弃（`totalDropped > 0`） | 8 | 背压溢出、字幕缺内容 | 已有 **专门守卫**（`--allow-drops`） |
| `short-terms.json` | 1 | **平均 RTF ≥ 1**（lowvol 单文件 RTF 2.6） | ❌ **不该拦** —— 准确率数据完全有效 |
| `p2_170_maxcer018.json` | 1 | `--max-cer 0.18` 触发的**逐样本**门禁 | ❌ 不该拦（那是使用者显式设的阈值） |

结论：**`verdict.passed` 不能当中止条件** —— 它把「跑得慢」这类如实记录也算了进去。
最终判据（纯函数 `invalid_round_reason()`，可单测）只判「有没有真的测到识别」：

* `totalSegments == 0` → VAD 未工作 / 音频全静音；
* `totalSubtitles == 0` → 片段未送入识别引擎。

两种情形 WER/CER **恒等于 1.0**（参考全文被整块 delete），一旦进表就会以
「术语注入把 WER 从 1.0 降到 0.8」这种形态产出**完全虚假**的结论 ——
`packaging/eval/short.json` 那轮（8 段 → **0 条字幕**、每格 WER 恒 1.0）
就是这么写进历史对照表的。

另外两条**只警告不中止**（同样不能静默）：
`verdict.passed=False`（列出 ✗ 条目）、`withReference == 0`（WER 列会全 `-`）。
理由：没有逐字稿的真实录音是常态，中止会把「只测实时性」的合法用法一起禁掉。

新出口：`--allow-invalid`（与兄弟守卫的 `--allow-drops` 并列）。

### 12.2 真机验证（三条，全部本机真跑）

| 轮 | 素材 | 命令要点 | 结果 |
|---|---|---|---|
| **A** | 自造 6.00s **纯静音** wav（`安装空间/_ab_guard_probe/`，不入库） | `--conditions off` | **exit=1**，打印「切出 0 个片段（VAD 未工作或音频全静音）—— 已中止」+ 三行后果说明 |
| **B** | 同上 | `+ --allow-invalid` | **exit=0**，放行；并打印 `⚠ 条件 [off] verdict.passed=False（**不中止**…）：✗ 一个语音片段都没切出来…` |
| **C** | `tests/audio/short`（正常英文旧集） | `--conditions off` | **exit=0**，**未误拦**（子报告 8 段 → 8 字幕、`passed=True`、`withReference=5`） |

轮 A 的输出正是 H4 描述的那个形态，一字不差：

```
汇总：1 个文件（1 个有参考文本）｜片段 0 → 字幕 0｜平均 WER 1.0｜CER 1.0
```

轮 A/B 的子报告 `段=0 字幕=0 passed=False` 完全一致 —— 差别只在**有没有 `--allow-invalid`**，
这正是单变量：证明「拦住 / 放行」由开关决定，不由别的因素决定。

### 12.3 测试、探针与产物校验

| 项 | 实测 |
|---|---|
| 新增用例 | `backend/tests/test_ab_invalid_round.py` **21 条** |
| 关键反向断言 | `verdict.passed == False` 但管道正常 → **必须放行**（`test_passed_false_with_valid_pipeline_is_usable`）；`totalDropped > 0` 与 `withReference == 0` **不属本判据职责** |
| 真实报告回灌 | `short.json` **必须被拦**；`short-terms.json` / `long-realtime.json` / `p2_probe_short_clean.json` / `real_zhlecture_ab4_phrases.phrases-on.json` **必须放行**（带 `skipif`） |
| 结构守卫 | 判据从 `summary` 里 `get` 的键**恰好** = {`totalSegments`,`totalSubtitles`}；非 docstring 常量里不得出现 `passed`/`verdict`；守卫调用必须是条件循环的**后代节点**（不是比行号） |
| 变异探针 | `packaging/verify/probe_ab_invalid_round_mutations.py` **12/12 全部被抓住**（`.gitignore` 反选例外 6 → 7） |
| 行尾 | `ab_terms_compare.py` 本就是 LF（crlf=0），改后仍 LF；新增两文件 LF |

### 12.4 已知局限（诚实记账）

* 「警告层」只挡了「读不读得到 `verdict` / `withReference`」与「有没有打出来」，
  没挡「打出来的时机」—— 那需要跑 `main()` 才能观测，成本高于收益。
* 探针里 C1 那条变异是**删掉循环内的调用**，而不是把它「搬到循环外」；
  两者对 `test_guard_runs_inside_the_condition_loop` 是同一种失败，
  但严格说只有前者被真跑过。
* 轮 A/B 的子报告**仍然落盘**（`p2_171_guard_abort.off.json`）。中止发生在
  「子报告写完之后、汇总之前」—— 这是有意的：坏轮次的原始证据要留着便于排查，
  被拦住的只是它**进对照表**这条路。
