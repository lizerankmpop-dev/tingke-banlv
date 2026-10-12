# P2：ASR 质量评估补全（分场景 CER/WER 报告）—— 方案与用例准备清单

> 状态：**P2 主体已完成**（`#169`~`#173`，2026-10-10）。决策 D1~D4 见 §8，执行记录见 §10~§14，实测结果见 §7。
> 前提：本任务只改 `<repo>/scripts/`、`<repo>/testset/`、`<repo>/docs/`、`packaging/eval/`，
> **不动安装包**（边界逐条核实见同目录 `write-boundary.md`）。
> 另有一个执行期间发现、**已立案但本版未修**的产品级缺陷：见同目录 [`known_defects.md`](./known_defects.md) D1。

---

## 0. 摘要（TL;DR）

| 问题 | 结论 |
|---|---|
| 分场景评估的**能力**有没有？ | **有，且早已实现**。`eval_asr_quality.py` 已按文件名前缀分场景汇总，`make_asr_testset.py` 已有 noise/lowvol/fast/multi 四个劣化算子 |
| 中文 5 场景**有素材吗**？ | ~~**一个都没有**~~ → **✅ 已补齐**：5 格合成（§13）+ 既有的 `long` / 真人 `other`，**§7 表 7 行全部有实测值**；只剩 `accent` 未覆盖 |
| 英文合成集的既有数字能当基准吗？ | **不能**。见 §3.1（clean 比 noise 差 18 倍、每格 n=1、跑在防循环修复之前）。#171 又查出其中 `short.json` 是**结构不可用**的一轮（8 段 → **0 字幕**），正是 H4 要堵的那种假结论 |
| 口音（accent）能合成吗？ | **项目已明确决定不合成**（`make_asr_testset.py` 注释：变调近似口音没有依据）。只能换素材来源（§6.4 已定：留空登记） |
| 补齐中文可合成场景的成本？ | **低**：现有算子直接作用在中文 TTS 讲稿上，零新增依赖、零版权风险。实测从切分到 7 格基线落表**在一天内完成** |
| 真正的缺口 | **口音 + 真实远场录音**（须真人素材）；**延迟指标**（字幕数 ≠ 片段数 ⇒ 配对缺口，§14.6 仍未解） |
| 一句话基线（`--language zh --term-language zh`，`--realtime`） | 合成 5 格 CER **0.1662 ~ 0.6834**（`multi` 最差）；长时 626.4s **零丢片**、尾延迟 1.765s；真人课 **0.1985**。⚠️ `n = 1`／格 ⇒ **是观测值，不是统计量** |

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

- [x] 先写切分工具（`<repo>/scripts/clip_tts_paragraphs.py`）—— ✅ 见 §10
- [x] **切完必须自证配对正确** —— ✅ 三项内置自证全过（静音边界数、文本同源、CER 量级），见 §10.3
- [x] 目标时长 60~90s —— ✅ 实际 80.52s（前 4 段 / 414 字）

#### 劣化（方案 A 主线）

- [x] 确认目标目录 —— ✅ `testset/chinese_lecture/scenarios/`（产物落其 `short/` 子目录，脚本强制）
- [x] 跑 `make_asr_testset.py --source <短样本>.wav --out …` —— ✅ 见 §13.1
- [x] 核对产物（**5 格 `.txt` 逐字节等于源** —— ✅ 1183 B / crlf=0 / 单一 md5，见 §13.4）
- [x] 跑 `check_transcript.py --audio <每个 wav>` —— ✅ 5 格全「可用 ✓」；**`fast` 实测 6.94 字/秒**（414 字 / 0.99 min），在 `CPS_RANGE (1.2~8.0)` 内（**真跑过，非推断**）
- [x] 记录每个 wav 的 `sha256 + 时长 + RMS/峰值` 写进 `metadata.json` —— ✅ 且由 `--metadata-out` **从落盘产物重读**生成（§13.3）

### 6.2 基线跑（每格必须**一次调用一份报告**）

- [x] 逐格跑 `eval_asr_quality.py --audio <wav> --language zh --term-language zh --realtime --report packaging/eval/p2_173_rt_<场景>.json`
       ✅ 7 格各一次调用（5 短 + `long` + `real`）。⚠️ `--term-language` 取 `zh` 是 2026-10-10 用户裁决的口径（`auto` 会输出繁体，见 `known_defects.md` D1）
- [x] 每格同时跑一次 `--terms off` 作为 A/B 基线 —— ⚠️ **本口径下退化为恒等**：`--term-language zh` 已滤掉整层短语层，实测 `termPromptChars = 0` ⇒ on/off 提示词都是空串，A/B 无信息量（判据是产物字段，不是推断；见 §13.6 / §14.2）
- [x] 汇总：每格产物字段抄进 §7 表（**用脚本从报告现读后打印成表**，不经人手转抄）
- [x] **抄录纪律**：只抄**产物字段值**，不得按输入侧推断 —— ✅ §7 表下附「读表须知」，逐列标注对应字段名

### 6.3 真人锚点（方案 C，可选）

- [x] 由用户挑 BV 号 —— ✅ `BV1BA411i7PT`（《Origin科研绘图超快速上手指南》，见 `real/metadata.json`）
- [x] `fetch_bili_audio.py` 取切片 → `asr_draft_transcript.py` 出草稿 —— ✅ 00:40~11:10 共 630s，见 `real/README.md`
- [x] 人工校对 → `preproof_transcript.py` 出定稿 → `check_transcript.py` 全 OK —— ✅ 定稿 35 段 / 2791 字 / 81 fix / 0 suspect / 11 note
- [x] 只在**校对完成后**才纳入报告 —— ✅ `referenceSource = real_zhlecture_origin_plot.preproof.txt`（产物字段已记），**草稿从未被当参考**

### 6.4 口音格（**已定：留空**）

- [x] **决策 D2 = 显式留空并登记为已知缺口**（不用任何近似手段冒充）
- [x] 在报告里写明「`accent` 未覆盖」及其理由 —— ✅ §7 表末行；`make_asr_testset.py:24` 已论证「变调近似口音没有依据」
- [ ] 若日后恰好拿到合适真人素材（普通话不标准 + 远场），顺带补进 6.3 的锚点段（届时才需要改 `--source-kind real`）

---

## 7. 报告表（2026-10-10 实测，已填；2026-10-11 增补 §7.2 词级延迟口径与「跨会话可对拍」判定）

**跑法**（口径见 §6.2）：

```bash
VP="/c/Users/Administrator/Desktop/听课伴侣/安装空间/venv/Scripts/python.exe"
# 逐格、一格一份报告、带 --realtime（否则「尾延迟」列没有意义）
$VP scripts/eval_asr_quality.py --audio <该格 wav> \
    --language zh --term-language zh --realtime \
    --report packaging/eval/p2_173_rt_<场景>.json
```

⚠️ `--language` 与 `--term-language` **都取 `zh`** —— 2026-10-10 用户裁决的基线口径
（原因为何不能取默认的 `auto`，见 [`known_defects.md`](./known_defects.md) D1）。
⚠️ 来源列的**实际取值是 `tts-synth`**，不是早期草案里的 `tts-degraded`（#170 决策：
劣化已由 `scenario` 表达，无需再造一个来源类别）。

### 7.1 识别质量与管道（实时口径）

一份报告一格：`packaging/eval/p2_173_rt_<场景>.json`。

| 来源（`sourceKind`） | 场景 | n | CER（实时） | CER（非实时合跑） | WER | ref 单位 | 片段→字幕 | 丢片 | 合并 | 尾延迟 s | RTF（非实时合跑） |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `tts-synth` | clean | 1 | **0.1662** | 0.1636 | 0.1662 | 379 | 6 → 10 | 0 | 0 | 2.120 | 0.292 |
| `tts-synth` | noise(8dB) | 1 | **0.1821** | 0.2032 | 0.1821 | 379 | 6 → 9 | 0 | 0 | 2.372 | 0.134 |
| `tts-synth` | lowvol(−30dBFS) | 1 | **0.1715** | 0.1715 | 0.1715 | 379 | 6 → 6 | 0 | 0 | 2.272 | 0.153 |
| `tts-synth` | fast(1.35×) | 1 | **0.2586** | 0.1900 | 0.2586 | 379 | 6 → 8 | 0 | 0 | 9.738 | 0.386 |
| `tts-synth` | multi(0.35s/0.75) | 1 | **0.6834** | **0.9420** | 0.6834 | 379 | 6 → 10 | 0 | 0 | 2.574 | 0.971 |
| `tts-synth` | long（整篇 626.4s，长时稳定性） | 1 | **0.2589** | — | 0.2589 | 2889 | 56 → 82 | 0 | **6** | 1.765 | — |
| `human-recording` | other（真人课 630.0s） | 1 | **0.1985** | — | 0.1985 | 2499 | 63 → 86 | 0 | 1 | 2.020 | — |
| `human-recording` | accent | **未覆盖（已登记缺口，见 D4）** | — | — | — | — | — | — | — | — | — |

⚠️ **「CER（实时）」与「CER（非实时合跑）」是两次独立观测，不是同一批数据的两种算法。**
非实时那列来自 `packaging/eval/p2_173_zh_scenarios.json`（`config.realtime = false`，5 格合跑），
仅 `clean / noise / lowvol / fast / multi` 有值（该合跑未包含长样本），口径差异见 7.3。
RTF 列与它是**同一次**非实时合跑（`summary.byScenario.*.meanRtf`），可一起读。

### 7.2 延迟（#175 词级新口径）

一份报告一格：`packaging/eval/p2_175_rt_<场景>.json`。旧口径 `pair()` 保留原样作对照（见 D2）。

| 场景 | 片段→字幕 | 词级 n | 词级 mean | 词级 p50 | **词级 p95** | 词级 max | 缺锚 | 旧口径 p95 | 恒等式 n | 最差残差 | 跨会话可对拍 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| clean | 6 → 9 | 9 | 3.847 | 2.163 | **11.568** | 11.568 | 0 | 0.0（占位） | 0（不可配） | — | ❌ |
| noise | 6 → 9 | 9 | 4.237 | 2.173 | **11.532** | 11.532 | 0 | 0.0（占位） | 0（不可配） | — | ✅ |
| lowvol | 6 → 6 | 6 | 2.157 | 2.048 | **2.554** | 2.554 | 0 | 1.932 | **6** | **0.000000** | ✅ |
| fast | 6 → 9 | 9 | 4.662 | 2.839 | **10.189** | 10.189 | 0 | 0.0（占位） | 0（不可配） | — | ❌ |
| multi | 6 → 6 | 6 | 3.412 | 2.591 | **6.569** | 6.569 | 0 | 5.613 | **6** | **0.000000** | ❌ |
| long | 56 → 100 | 100 | 5.127 | 3.306 | **11.600** | 21.336 | 0 | 0.0（占位） | 0（不可配） | — | ❌ |
| real | 63 → 86 | 86 | 16.251 | 18.998 | **29.376** | 40.404 | 0 | 0.0（占位） | 0（不可配） | — | ✅ |

**读法**：
- **词级口径不依赖配对**，所以 7 格全部有值（旧口径只有 `lowvol` / `multi` 两格能配对）；
- 「缺锚」全 0 ⇒ 每条字幕都拿到了词级时间戳；
- 「恒等式 n / 最差残差」是**同一轮内**的精确校验（`新−旧 == audioEnd − audioWordsEnd >= 0`），
  与跨会话漂移无关；只有 `paired=True` 的格能有值，其余标「不可配」是**原理限制，不是缺测**；
- ⚠️ **【`跨会话可对拍`】** 判定依据：同一份旧代码在两个会话上能否复现同一 CER
  （实测见 D3）。标 ❌ 的格，其单次观测**不能当稳定基准**；
- ⚠️ 全部延迟都是**墙钟量**：实测整机延迟逐会话单调上升 ≈ **+0.2s/会话**（lowvol 旧 p95
  `1.528 → 1.722 → 1.932`、收尾 `2.272 → 2.419 → 2.624`），**跨会话不可比**。

### 7.3 读表须知（每一列都能从产物字段查回来）

| 列 | 产物字段 | 说明 |
|---|---|---|
| CER / WER | `accuracy.cer` / `accuracy.wer` | 中文按**字**统计 ⇒ 两者**恒等**（设计，不是 bug） |
| ref 单位 | `accuracy.refUnits` | 归一化后的参考单位数；各行**不可直接比大小**（短样本 379 vs 整篇 2889） |
| 丢片 / 合并 | `droppedSegments` / `mergedCalls` | 实时模式下**丢片全 0**；`long` 合并 6 次属设计内降级 |
| 尾延迟 s | `tailSeconds` | 结束时的积压量，大 = 没排空。⚠️ **墙钟量**，跨会话漂 |
| 旧口径 p95 | `latency.p95` | ⚠️ **需要 1:1 配对**（片段数 == 字幕数）；不满足时落 `0.0` **占位**，不是「延迟为 0」 |
| 词级 n / p95 / … | `latency_word_based.*` | **不依赖配对**；无数据时给 `None` 而**不用 0 占位** |
| 旧口径明细 | `segmentDetail[*]` | `paired=True` 时才落盘；恒等式的左侧数据来源 |
| RTF | `rtf` | ⚠️ **实时模式下恒无值**（脚本明说不计算：恒 ≈1 无信息量）；本列取自**非实时合跑** |


**三条要点**（展开见 §14）：

1. **实时 vs 非实时差很多** —— `multi` 格 CER **0.9420（非实时）→ 0.6834（实时）**，差 0.26 ≫ run-to-run 波动 0.03~0.05；
   非实时还额外触发 `mergedCalls 1`、`tailSeconds 77.498`（实时仅 2.574）。
   ⇒ 「尽快喂完」会**人为加剧劣化**，短样本基线**必须**用 `--realtime`。
2. **长时稳定性成立** —— `long` 626.4s / 56 片段：**丢片 0**、尾延迟 **1.765s**，10 分钟连续喂入无累积落后。
3. **真人锚点与合成 clean 同量级** —— `human-recording` other **0.1985** vs `tts-synth` clean **0.1662**（差 0.032）。
   ⚠️ 但**不要**据此说「合成集不乐观」：这段真人素材是**软件实操课**（Origin 绘图，语速稳、停顿多），
   对 ASR 天然友好；结论**只对这段素材成立**。

> **空单元格 = 尚未测得。不得填 0、不得填推断值。**（除 `accent` 一行外，本表无空行，全部实测。）

---

### 7.4 简繁质量（V5-1，2026-10-11 实测）

#### 为什么必须单列一节

**CER 对"字形对不对"是迟钝且方向不定的**：

* 繁体字与简体参考稿的差异被算进编辑距离，和"听错了"混在**同一个数字**里；
* 它**可正可负** —— §7.2 那批数据里 auto 口径的 `fast` 反而比 zh 口径低 0.0343。
  单看 CER 会得出「繁体没坏处」的错误结论。

所以简繁质量必须用**只看字形**的独立判据：`packaging/verify/check_simplified.py`
（统计繁体字符数与占比，超阈值即非零退出）。它同时是 V5-5 的交付物。

#### 2×2 正交分解（clean，`--realtime`，**同一会话**）

同一份素材、同一台机器、同一次会话，只差两个变量：`LANG_DETECT`（= V5-1 的修复开关）
与术语层语种。四格由 `packaging/eval/v5_1_ab_*.json` 生成，表由
`_v5_1_report.py` 从产物读出（**不手抄**）。

| 条件 | 术语层 = `auto`（**生产默认**） | 术语层 = `zh` |
|---|---|---|
| **修复前**（`LANG_DETECT=0`） | CER **0.4934**｜繁体 147 / 33.95% ❌ | CER **0.4776**｜繁体 142 / 35.06% ❌ |
| **修复后**（V5-1） | CER **0.438**｜繁体 4 / 0.71% ✅ | CER **0.1504**｜繁体 **0 / 0.00%** ✅ |

| 格 | `languageSource` | `languageDecision` | 置信 | `langDetectEvents` |
|---|---|---|---|---|
| 修复前两格 | `disabled` | `None` | 0.0 | 0 |
| 修复后两格 | `sticky`（首块 `detected`） | `zh` | **0.9978** | **1** |

#### 三条结论

1. **修复本身的效果 = ΔCER −0.3272（相对 −68.5%）**
   在术语层同为 `zh` 的干净条件下（0.4776 → 0.1504），唯一变量就是 V5-1。
   这比"混着短语层看"（−0.0554）大 6 倍。
2. **修复后的 `auto` 与显式 `zh` 逐位一致**
   `CER 0.1504 / edits 57 / hypUnits 375` 三项全同 ⇒ 可以断言
   「auto 路径已等价于显式指定中文」，这是本次修复最强的证据。
3. ⚠️ **默认路径（`auto` + `auto`）仍未兑现全部收益**：只从 0.4934 降到 0.438。
   原因是**另一个已登记的缺陷**在同一个格上制造了大量插入错误 ——
   英文短语层让模型产出复读式乱码（`hypUnits` 396 → 487），把 0.327 的字形收益掩盖掉了。
   该项已单独立案为 **D5**，修法见 `docs/m3/backlog_v350.md` **V5-8**。

#### 阈值怎么定的（可复算）

| 项 | 值 | 说明 |
|---|---|---|
| 修复前（必须 FAIL） | 147 字 / 33.95% | 约 34%，与 `whisper_engine.py` P0-1 注释里记的 **34.4%** 独立吻合 |
| 修复后（必须 PASS） | 4 字 / 0.71%（`zh` 口径 0 字） | |
| 默认阈值 | `--max-trad-chars 20` / `--max-trad-ratio 2%` | 约为修复后的 7 倍、修复前的 1/17，两侧都留量级余量 |

⚠️ **已知盲区**：修复后 `auto/auto` 残留的 4 个字全是「捲」——
那是**识别用字错误**（应为「卷积」，模型写成「捲击」），属内容层；
`auto/zh` 口径下为 **0**，说明它**不是**简繁路径没生效。
所以 `verdict=FAIL` 时要先看 `languageSource`：已生效则成因在内容层，不要回退简繁逻辑。

复算命令：

```bash
<venv>/Scripts/python.exe packaging/verify/check_simplified.py --self-test
<venv>/Scripts/python.exe packaging/verify/check_simplified.py --report packaging/eval/v5_1_ab_pre_auto.json   # 期望 exit 1
<venv>/Scripts/python.exe packaging/verify/check_simplified.py --report packaging/eval/v5_1_ab_post_auto.json  # 期望 exit 0
```

---

### 7.5 语种感知术语层（V5-8 / 缺陷 D5，2026-10-11 实测）

#### 为什么必须单列一节

这一条**只在默认路径上出现**：`--language zh` 一格完全不受影响（不触发探测、
回调不发）。所以**只看 P2 的 `zh` 基线永远看不到它** —— 这正是它被漏掉一整轮的
原因：前面所有基线都跑在显式 `zh` 上。

它的性质也和 §7.4 不同：§7.4 是**字形**问题，这里是**内容**问题
（英文短语层把模型带进复读式幻觉）。两者在 CER 上叠加，方向相反地互相掩盖。

#### 同会话 A/B（clean，`--realtime`）

三格由 `packaging/eval/v58_ab_*.json` 生成，表由 `_v58_ab.py` 从产物读出（**不手抄**）。
`v58_on` / `v58_off` 的唯一变量是 V5-8 开关，`v58_zh` 是 P2 口径的对拍格。

| 格 | 命令 | CER | edits | hypUnits/refUnits | `languageSource` | 术语层重建 | 繁体字 |
|---|---|---|---|---|---|---|---|
| 修复前 | `--language auto --no-lang-aware-terms` | 0.3641 | 138 | 450/379 | `sticky` | — | 0 / 0.00% |
| **修复后** | `--language auto` | **0.1504** | **57** | **375/379** | `sticky` | zh → phrases **0** 项 | **0** |
| P2 基线口径 | `--language zh --term-language zh` | 0.1504 | 57 | 375/379 | `explicit` | — | 0 |

#### 三条结论

1. **修复幅度 ΔCER −0.2137（相对 −58.7%）**，`hypUnits` 450 → 375
   —— 少吐 **75 个字的幻觉**。「默认路径上英文短语层的代价」就是这个量级。
2. **修复后的默认路径与显式 `zh` 逐位一致**（CER 0.1504 / edits 57 / hypUnits 375
   三项全同）⇒ 可以断言「`auto` 已等价于显式中文」，与 §7.4 第 2 条结论互证。
3. **`--language zh` 一列与 P2 时点完全一致** ⇒ v4 判据的对拍基准未受影响
   （显式语种不触发探测，回调根本不发）。这是「这次改动能不能只改默认路径」的答案。

⚠️ **跨会话漂移提醒（台账 D3）**：`v58_off` 在本会话是 **0.3641**，而 §7.4 那批数据里
同一条件是 **0.438**。同一份代码、同一份素材、不同会话，CER 差 0.074 —— 与 D3 的
量级一致。所以**本表只允许同会话内部比较**，不要拿它去和 §7.4 的表做减法。

#### 落盘字段怎么读

`config.langAwareTerms` 说明开关状态，`config.termRebuilds` 说明**实际重建了几次、
重建成什么**。两个字段缺一不可：`languageSource=detected` 只说明「判对了语种」，
不说明「术语层按这个语种重建了」—— 而后者才是 D5 的修复点，两者在 CER 上差 0.21。

复算命令：

```bash
<venv>/Scripts/python.exe scripts/eval_asr_quality.py \
    --audio testset/chinese_lecture/scenarios/short/synth_clean_short_zhlecture_cnn.wav \
    --realtime --report packaging/eval/v58_ab_v58_on.json
# 关掉 V5-8 复现修复前行为：追加 --no-lang-aware-terms
# 表格与差值：安装空间/_v58_ab.py（数字全部从产物读取）
```

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

---

## 13. #172 执行记录：中文 5 格合成集 + 指纹登记（2026-10-10）

### 13.1 生成命令与产物

```bash
VP="/c/Users/Administrator/Desktop/听课伴侣/安装空间/venv/Scripts/python.exe"
$VP scripts/make_asr_testset.py \
    --source testset/chinese_lecture/short/short_zhlecture_cnn.wav \
    --out    testset/chinese_lecture/scenarios \
    --only   clean noise lowvol fast multi \
    --metadata-out testset/chinese_lecture/scenarios/metadata.json
```

产物落 `testset/chinese_lecture/scenarios/short/` —— **`short/` 子目录是脚本强制的**：
短/长用例要用两种完全不同的跑法（尽快喂完 vs `--realtime`），混在一个目录里会被一次
`--audio-dir` 扫描一起灌进去，人为触发背压丢弃。音频走 `testset/**/*.wav` 的 gitignore；
同名 `.txt` 与 `metadata.json` 入库。

| 格 | 时长 | RMS dBFS | 峰值 dBFS | wav sha256（前 16） |
|---|---|---|---|---|
| clean | 80.52s | −28.10 | −3.00 | `64cb93c8c4c7c2bc` |
| noise | 80.52s | −27.35 | −3.00 | `c6c4e8c7fe6550c5` |
| lowvol | 80.52s | −55.14 | **−30.00** | `a6fa96ea6d3f738f` |
| fast | **59.64s** | −27.82 | −3.00 | `e79847fbae73a487` |
| multi | **80.87s** | −26.16 | −3.00 | `dca9c69c3df5a86a` |

（`fast` 59.64s = 80.52 / 1.35；`multi` 80.87s = 80.52 + 0.35 —— 与算子定义吻合。）

### 13.2 顺带修掉的缺陷：`write_text` 漏了 `newline="\n"`

**这不是顺手改的**，是 §6.1 的验收标准逼出来的：

`make_asr_testset.py:284` 原本是
`path.with_suffix(".txt").write_text(text + "\n", encoding="utf-8")` ——
Windows 上 `Path.write_text` 默认把 `\n` 翻成 CRLF，产物参考文本会凭空多一个 `\r`
（**1183 → 1184 字节**），而 §6.1 要求「参考文本随劣化复制，必须**逐字节**等于源 txt」。

⇒ 补 `newline="\n"`。这正是**教训 43 的同源缺陷**，且 `clip_tts_paragraphs.py:226`
**早就有完全相同的注释**（「不写这个参数，`metadata.json` 会变成 29 个 CRLF」）——
而这个**更早**的脚本漏了 ⇒ 教训 1 的形态（同一件事存两份必漂移）。

**判据要说清**：`_PUNCT` 里含 `\r`，所以行尾**不影响 CER**。守的不是准确率，是**可验证性**：
「5 格参考文本与源同源」这个论断只能靠逐字节相等来立，退化成「除行尾外相同」就等于放弃了证明力。

### 13.3 为什么给脚本加 `--metadata-out`（而不是手写一份 metadata）

指纹只有**从落盘产物现读**才不会抄错；手写一份本质上是一次性的人工复制。

* `sha256` / `rmsDbfs` / `peakDbfs` 由 `wav_stats()` **重读文件**算，不用内存里的数组 ——
  `write_wav()` 会 `np.clip(-1,1)` 再量化成 int16，**内存值 ≠ 写进文件的字节**；
  记内存值等于把账记在草稿上。
* `argv` 经 `display_argv()` 归一成**仓库相对路径**（`/` 分隔）——
  metadata 里出现 `C:\Users\<某台机器>\...`，「照着这条命令复现」就无从谈起。
* **自证**：任一格的 `.txt` 与源 `.txt` 不是逐字节相等 ⇒ 打印 `❌` 并 **`exit=1`**，不留假证据。
* **默认不写**：不传 `--metadata-out` 时脚本行为与改动前**逐字节一致**（已用「重跑到临时目录、
  比 5 格 sha256」证明，且确认 `metadata.json` 没被创建）。

### 13.4 实测四项

| 验证项 | 结果 |
|---|---|
| 5 格 `.txt` 与源逐字节相等 | ✅ 5/5 `True`（1183 B / crlf=0；**5 格 md5 集合只有 1 个元素** `405228448e0a172c9f3b6caa7616b617`） |
| `check_transcript.py --audio <每格>` | ✅ 5 格全「结论：可用 ✓」、exit=0；**fast 实测 6.94 字/秒**（414 字 / 0.99 min），在 `CPS_RANGE (1.2~8.0)` 内 —— **实测值，不是推断** |
| 可复现性 | ✅ 同 seed 重跑到临时目录，**三方逐字节一致**（metadata ↔ 正式 ↔ 重跑），5/5 |
| 安装包指纹 | ✅ `302,570,573 B` / sha256 `4a92fee2…af9060a` / md5 `bf0af680…` / mtime `2026-10-10 13:16:45` 全未变；副本对拍 **170/170 diff=0 miss=0** |

### 13.5 `sourceKind` 就近生效（一个值得记的细节）

生成前，`scenarios/short/*.wav` 的 `sourceKindBasis` 是 **`metadata.json kind=PROXY`** ——
它按 `_METADATA_SEARCH_LEVELS = 2` 上溯，撞到了 `chinese_lecture/metadata.json`
（那是 edge-tts 代理集的登记）。**结论碰巧是对的**（`PROXY` 与 `SYNTH` 都映射 `tts-synth`），
但**依据是错的**。

写入 `scenarios/metadata.json`（`kind: "SYNTH"`）后判据变成就近的 `metadata.json kind=SYNTH` ✅。

⚠️ 这是一个**「静默正确」**陷阱：只要上游那层的 `kind` 恰好也映射到同一个 `sourceKind`，
「谁才是真正的来源」就永远不会有人发现。⇒ 多层目录共享同一个 `metadata.json` 时，
**每个产出目录都要有自己的一份**，不能靠上溯碰运气。

### 13.6 已知局限（诚实记账）

* `synth_clean_*` **≠ 源短样本**：`peak_norm(source, -3.0)` 把峰值从 −2.27 抬到 −3.00 dBFS（−0.73 dB）。
  CER 不受影响（Whisper 内部会归一化），但两者不是同一个文件，不可互相代填。
* 5 格 `.txt` 内容完全相同（都是源那 414 字），入库会多 5 份重复文本（共 +5,915 B）。
  这是刻意的：`.gitignore` 已把参考文本列为**核心资产**，且 `eval_asr_quality.py`
  靠**同名 `.txt`** 找参考，缺了就算不出 CER。
* `accent` 格仍未覆盖（§6.4 已定：留空登记缺口）。
* §6.2 要求的「每格再跑一次 `--terms off` 作 A/B 基线」在**本口径下退化为恒等**：
  `--term-language zh` 已把短语层整层滤掉，实测 `termPromptChars = 0`、`phrases = 0`
  ⇒ `--terms on/off` 传给引擎的提示词都是空串，A/B 无信息量。（判据是产物字段，不是推断。）
  §14 的 7 格全部复核为 `termPromptChars = 0`，与该判断一致。

---

## 14. #173 执行记录：逐格中文场景基线（2026-10-10）

### 14.1 跑法与口径

按 §6.2 的硬要求：**逐格、一格一份报告、带 `--realtime`**。这不是形式主义 ——
`--realtime` 与非实时的差距**实测很大**（见 14.3），而「一次调用跑完多格」在实时喂入下
会因休眠/挂起污染（教训 16），所以只能一格一次调用。

**基线口径由用户裁决**：`--language zh --term-language zh`（**不是**默认的 `auto`）。
原因见 [`known_defects.md`](./known_defects.md) D1：`auto` 会让中文输出**繁体**，
CER 被字形差异虚高 0.35+，**不能当基线**。

耗时：短样本 5 格合 7m11s；`long` / `real` 各约 10.5min（音频本身 626.4s / 630.0s）
⇒ 合计约 28min。全部 `exit=0`。

### 14.2 七格结果

见 §7 表。7 格全部：`drained = true`、`droppedSegments = 0`、`realtime = true`、
`termPromptChars = 0`、`repetitionEvents = 0`。

### 14.3 🔴 关键发现①：非实时的「尽快喂完」会**人为加剧劣化**

同一份 `synth_multi_short_zhlecture_cnn.wav`、同参数，只差喂入方式：

| 喂入方式 | CER | mergedCalls | tailSeconds |
|---|---|---|---|
| 非实时（尽快喂完） | **0.9420** | 1 | **77.498** |
| `--realtime` | **0.6834** | 0 | 2.574 |

差 **0.2586**，远超 run-to-run 波动（≈0.03~0.05）；且非实时的收尾积压 **77.498s**
（几乎与音频等长），实时只有 2.574s。

⇒ **「尽快喂完」本身就会触发背压与合并降级**，把「管道鲁棒性」测成了
「管道被灌爆时的表现」。**短样本基线必须用 `--realtime`** —— §6.2 的要求是对的。

⚠️ 反面一例：`fast` 格反而非实时更好（0.1900 vs 0.2586，差 0.0686，略超波动上限）。
这一格**不能**用同一机制解释 ⇒ 如实记为**未解释的差异**，不下「哪种喂入方式一定更好」的结论。

### 14.4 关键发现②：长时稳定性成立

`long_zhlecture_cnn.wav` 626.4s / 56 片段 / 82 条字幕：

* **`droppedSegments = 0`** —— 零丢片；
* **`tailSeconds = 1.765`** —— 结束时几乎没有积压；
* `mergedCalls = 6` —— 6 次合并（设计内降级，字幕更粗）；
* `repetitionEvents = 0` —— 未触发重复循环兜底。

⇒ 10 分钟连续喂入**没有累积落后**。「越拖越晚、丢片段」这个担心在本机不成立。

### 14.5 关键发现③：真人锚点与合成 clean 同量级（**不要外推**）

| 来源 | 素材 | CER |
|---|---|---|
| `tts-synth` | 合成 clean（80.52s，前 4 段） | **0.1662** |
| `human-recording` | 真人软件实操课（630.0s） | **0.1985** |

差 **0.0323**。这与「合成集 CER 绝对值显著偏乐观」的预期**不符**。

⚠️ **不能**据此推翻那条警告：

* 这段真人素材是 **Origin 绘图软件实操课** —— 语速稳、停顿多、术语重复，对 ASR 天然友好；
  它**不代表**「真实课堂」的普遍难度。
* `real` 的参考是 `*.preproof.txt`（**人工预校对稿**），仍带 11 处 `note` 标注的存疑点；
  它接近真值但**不是**完美真值。
* `n = 1`／格 ⇒ 两个数字都**没有统计意义**。

⇒ 正确的说法是：**「本合成集对这段真人素材没有表现出乐观偏差」**，
而不是「合成集不乐观」。

### 14.6 延迟指标：仍然不可得（如实记账）

7 格里只有 `lowvol` 一格算出了延迟（`latency.p95 = 1.528`、`meanLatency = 1.048`），
因为它是唯一「片段数 == 字幕数」的格（6 → 6）。

其余 6 格「字幕数 > 片段数」⇒ 脚本**拒绝配对**（`paired = False`），`latency.p95` 落 `0.0` 占位。
脚本自己的措辞是：

> 以下文件片段数与字幕数不等，未做延迟配对（**延迟数字缺失是如实标记，不是算错**）

⇒ 这是 **P3 遗留的同一个缺口**（`on_partial = router` 让字幕 ≠ 片段）。
本轮**没有**解决它，也没有假装解决 —— 想从报告里读延迟，得先解决配对问题。

### 14.7 已知局限（诚实记账）

* `n = 1`／格、无重复 ⇒ 所有 CER 都**不是统计量**，是单次观测。
  格间差 < 0.05 的三格（clean 0.1662 / lowvol 0.1715 / noise 0.1821）**不可据此排序**。
* `RTF` 列取自**非实时**合跑，与同表其余列**不同源**；实时模式不产 RTF。
* 「字幕数 > 片段数」在 7 格中 6 格触发 —— 属 #169 已记录的**误报**
  （`lastRepeat` 显示最长重复 0 字符，即根本没检测到重复），但它同时**阻塞了延迟配对**。
* `accent` 未覆盖（§6.4 决策：留空登记）。
* `long` 的 CER（0.2589）比 `clean`（0.1662）高 **0.093** —— 两者**同源**（同一个 TTS 讲稿），
  差别只在「整篇 30 段」vs「前 4 段」。本轮观察**不足以解释**它（可能是后段内容更难，
  也可能是长跑累积效应）⇒ 如实记为**待查**，不下结论。
* `long` 的 `sourceKindBasis` 是 `metadata.json kind=PROXY`（`chinese_lecture/metadata.json`），
  与 5 格的 `kind=SYNTH` 不同 —— 两者都映射 `tts-synth`，归属正确，但**依据层级不同**。

---

## 15. #175 执行记录：延迟指标改用词级口径 + 跨会话可复现性先验（2026-10-11）

### 15.1 目的

`§14.6` 记的那个缺口：延迟指标在 7 格里**只有 `lowvol` 一格算得出**，其余 6 格因
「片段数 ≠ 字幕数」被脚本拒绝配对。本轮的目的一是把延迟口径补全（根因与修法见
[`known_defects.md`](./known_defects.md) **D2**），二是**把「重跑能对上」这件事本身查清楚**。

### 15.2 口径：从 v1 改到 v4（每一次修改都由实测数据推动）

| 版本 | 口径 | 被什么推翻 |
|---|---|---|
| v1 | 硬判据：CER **逐位一致**、字幕数**完全相同**、收尾 ±0.3s | `clean` 直接失败（实测不可达，见 15.5） |
| v2 | 加容差（短格 CER ±0.02、字幕 ±1）；漂移格自动跑旧码对照 | `lowvol` 收尾 Δ=+0.352 超 ±0.3 |
| v3 | **时序量降为观测值**（跨会话不设阈值）；时序比较改走**同会话对照** | `multi` 同会话新旧码不一致（0.628 vs 0.6675） |
| **v4（定稿）** | **跨会话判据先验可复现性**：对照显示旧码在本会话复现不出 P2 值 ⇒ 该格跨会话不可复现 ⇒ 跨会话类判据**整类作废**，改以恒等式 + 离散量 + 同会话对照判定 | 七格全部判定完成，无失败（见 15.4） |

**v4 的硬判据**（跨会话可比、不漂）：`segments` 相同 · `subtitles` ±1 · `drained == True` ·
`droppedSegments` 相同且为 0 · 参考稿同源 · CER 分档容差 · **恒等式精确成立**。
**观测值**（落盘但不设阈值）：`tailSeconds` · `latency.p95` · `latency_word_based.*`。

### 15.3 跑法

```bash
VP="/c/Users/Administrator/Desktop/听课伴侣/安装空间/venv/Scripts/python.exe"
# 七格逐格实时评估（新口径产物写 p2_175_rt_<场景>.json，**不覆盖** p2_173 基线）
$VP 安装空间/_p2_175_batch_v4.py --gate tolerant --control-on-drift
# 口径改过之后，不重跑、只重判：
$VP 安装空间/_p2_175_batch_v4.py --gate tolerant --validate-only
```

产物：`packaging/eval/p2_175_rt_<场景>.json`（7 份）、`p2_175_ctrl_<场景>.json`（6 份对照）、
`p2_175_multi_{new,old}_rep{1..3}.json`（可复现性诊断）、
`p2_175_diag/`（v1 诊断证据保全）、汇总 `安装空间/_p2_175_final_summary.json`。

### 15.4 七格结果：全部通过（见 §7.2）

| 场景 | 词级 p95 | 恒等式 | 同会话收尾 Δ | 跨会话可对拍 |
|---|---|---|---|---|
| clean | 11.568 | 不可配 | — | ❌ |
| noise | 11.532 | 不可配 | −0.049 | ✅ |
| lowvol | 2.554 | **6/6 残差 0.000000** | +0.302 | ✅ |
| fast | 10.189 | 不可配 | +3.728 | ❌ |
| multi | 6.569 | **6/6 残差 0.000000** | +0.103 | ❌ |
| long | 11.600 | 不可配 | +0.052 | ❌ |
| real | 29.376 | 不可配 | +0.002 | ✅ |

### 15.5 🔴 关键发现①：**跨会话可复现性必须先验**，否则跨会话对拍没有意义

判据：**同一份代码**（改动前的旧脚本）在两个会话上跑，看能否复现同一 CER。

| 场景 | P2 会话（旧码） | 本会话（新码） | **本会话（旧码）** | 可复现 |
|---|---|---|---|---|
| lowvol | 0.1715 | 0.1715 | **0.1715** | ✅ |
| noise | 0.1821 | 0.1821 | **0.1821** | ✅ |
| real | 0.1985 | 0.1985 | **0.1985** | ✅ |
| clean | 0.1662 | 0.1504 | **0.1504** | ❌ |
| fast | 0.2586 | 0.4248 | **0.4248** | ❌ |
| multi | 0.6834 | 0.628 | **0.6675** | ❌ |
| long | 0.2589 | 0.2845 | **0.3053** | ❌ |

`multi` 的加码证据（同会话各跑 3 次）：

| 代码 | 3 次 CER | 3 次 段→字 |
|---|---|---|
| 新码 | 0.6675 / 0.6675 / **0.7599**（极差 0.0924） | 5 / 5 / 7 |
| 旧码 | 0.6675 / **0.6834** / 0.6675（极差 0.0159） | 5 / 9 / 5 |

**同代码同会话就不收敛**，且旧码自己就能给出 P2 的 0.6834 与另一个值。

⇒ 对 ❌ 的 4 格，「新码 CER ≠ P2 CER」**信息量为零** —— 既不能判漂移，也不能判回归。
把这种差异当证据去调代码，就是教训 9 的凑数陷阱。
⇒ 正确的对拍纪律写在 **D3**；`clean/fast/multi/long` 的单次观测**不得当稳定基准**。

### 15.6 🔴 关键发现②：墙钟延迟**逐会话单调漂移 ≈ +0.2s**

`lowvol` 同一格的三个会话观测：

| 量 | P2 #173 | #175 阶段2 | #175 阶段3 | 增量 |
|---|---|---|---|---|
| 收尾 `tailSeconds` | 2.272 | 2.419 | 2.624 | +0.147 / +0.205 |
| 旧口径 `latency.p95` | 1.528 | 1.722 | 1.932 | +0.194 / +0.210 |

两个**独立**的墙钟量同幅度上升，而同期 **CER 三次逐位一致（0.1715）**、
恒等式 6/6 残差 0.000000。
⇒ 漂移在**整机层面、单调且不封顶** ⇒ 任何固定的跨会话阈值迟早失效（±0.3 已被 +0.352 打破）。
⇒ 这正是把时序量降为「观测值」、并改用**同会话对照**（`tailDeltaSameSession`）的理由。
`long` / `real` 的同会话收尾 Δ 分别只有 **+0.052** / **+0.002** ⇒ 改动对时序无可测影响。

### 15.7 关键发现③：**恒等式是唯一不漂的判据**

```
latency_新 − latency_旧 == seg.audioEnd − audioWordsEnd >= 0
```

`lowvol` / `multi` 两格（唯一 `paired=True` 的两格）**各 6/6 条残差 0.000000**，
静音尾巴全部 ≥ 0。恒等式两边都是**同一轮内**的读数 ⇒ 与跨会话漂移无关，
是「锚点用对了、实现是对的」的直接证据，且可**从报告独立重算**
（`segmentDetail[*]` ⊕ `latency_word_based.detail`，两者都已落盘）。

⚠️ 其余 5 格标「不可配」是**原理限制**（VAD 块 ↔ whisper 段是 1:N，见 D2），不是缺测。

### 15.8 验收判据的四次修正（诚实记账）

| # | 曾用判据 | 为什么废弃 |
|---|---|---|
| 1 | 新口径 p95 = 1.528s「与之前一致」 | 锚点不同，本来就不该相等（见教训 48） |
| 2 | CER 逐位一致 | 短格上不可达；且跨会话本就不保证 |
| 3 | 收尾 ±0.3s | 被整机 +0.2s/会话 漂移打破 |
| 4 | 漂移格一律判「存疑/停」 | `multi` 证明「旧码也给不出 P2 值」不是异常而是**该格不可复现** |

⇒ 四条都是在**数据反驳之后**才改的，没有一条是预先放宽的；
且每一次修改都保留旧判据的产物与结论（`p2_175_diag/` 保全 v1 全部证据）。

### 15.9 回归与指纹

| 项 | 结果 |
|---|---|
| `eval_asr_quality.py` | 保持 #175 阶段1 版本，md5 **`fe5f3e3ab6dedf1384dc581caccd4dfc`**，纯 CRLF（crlf 1378 / lone_lf 0） |
| 单测 | `pytest 391 通过 / 1 跳过`（含 `test_eval_latency_words.py` 17 条） |
| 变异探针 | `probe_latency_words_mutations.py` **13/13** |
| 副本对拍 | `tracked=173 same=173 diff=0 miss=0` |
| 安装包指纹 | **未变** —— 改动全在 `scripts/` `docs/`，不进包；`v3.4.1` 交付物 sha256 仍为 `4a92fee2…af9060a` |

### 15.10 已知局限（诚实记账）

* `n = 1`／格（可复现性诊断只对 `multi` 做了 3+3 次）⇒ 除 `multi` 外的散布区间**未测**。
* 未定位「为什么 `multi`/`fast` 会走不同解码路径」——疑与实时喂入下的调度抖动
  + `condition_on_previous_text=True` 的自强化有关，**未证实，不下结论**。
* `real` 的词级 p95（29.376s）显著高于其余格：它是 630s 长音频、真人课停顿多，
  但**未做归因分析**（是否与参考稿人工预校对、讲者语速有关），只如实记录。
* `accent` 格仍未覆盖（D4）。

---

## 16. v3.5.0 阶段C 全套回归执行记录（2026-10-12）

### 16.1 目的与范围

P2 封存（`v3.4.1`，tag `0230e68`）之后，V5-1~V5-8 的修复**动了 `backend/` 生产代码** ⇒
按 [`known_defects.md`](./known_defects.md) D1「共同代价」那条纪律，`v3.4.1` 的 L4 与真机 e2e
结论**整类作废**。阶段B 完成版本升级与重新打包（新交付物 **`v3.5.0`**），阶段C 要回答的是：

| # | 要回答的问题 | 判据来源 |
|---|---|---|
| **C-1** | 修复有没有把既有测试网搞坏？ | `pytest` 全量 **445 passed / 1 skipped**；变异探针 **22/22** 捕获 |
| **C-2** | 显式 `zh` 的**基准锚点**还在不在？`auto` 模式是否**稳定对齐**显式 `zh`？ | 七格 × {zh, auto} 双跑；锚点 `clean = 0.1504` |
| **C-3** | 用户双击安装包后的**真实行为**是否与脚本一致？ | 真机 e2e：安装 → 运行时 API → 内容层四类边界用例 |
| **C-4** | 账能不能对上、有没有新的静默漂移？ | 双副本对拍 + D1/D5 关闭证据 + 本记录 |

**纪律**：本节每个数字都必须能在产物 JSON 里定位；命令都是**本机真跑过**的那一条；
n=1 或不适用之处**明确标 N/A**，不记成通过。

### 16.2 C-1 静态回归

| 项 | 命令 | 结果 |
|---|---|---|
| 全量单测 | `pytest`（venv，仓库根） | **445 passed / 1 skipped / 2 warnings in 24.23s**，`EXIT=0` |
| 变异探针 | `packaging/verify/probe_lang_simp_mutations.py` | **22/22 全部抓住**（不变量亦为绿），`EXIT=0` |

* 单测基线从 P2 的 **391** 涨到 **445**（+54，全部是 V5-1~V5-8 新增）；
* 变异探针从 **14** 涨到 **22**（+8，覆盖 V5-2 降级不锁定 / V5-3 粘滞 / V5-4 开关收敛 /
  V5-8 回调接线与 A/B 脚本复用等新增不变量）；
* 跑完 `git status --porcelain` 干净、5 个被变异过的文件 md5 无残留 ⇒ 探针自身无副作用。

**为什么变异探针是必需的第二道闸**：D1/D5 都是**静默**缺陷（不报错、不告警、普通单测可恒绿）。
新增断言必须证明「变异它就会红」，否则那条断言等于没写。

### 16.3 C-2 七格 × {zh, auto} 双跑（A/B 正交）

**跑法**（`安装空间/_c2_batch_350.py`，`--realtime`，`--term-language` 随 `--language` 走）：

```bash
"$VP" scripts/eval_asr_quality.py --audio <格音频> --language {zh|auto} \
      --term-language {zh|auto} --realtime --report "packaging/eval/v350_rt_{lang}_{格}.json"
```

七格：`clean / noise / lowvol / fast / multi`（短样本）+ `long`(626.4s) / `real`(630s)。

#### 16.3.1 结果表

| 格 | zh 首跑 | zh 稳态 | auto 首跑 | auto 稳态 | Δ稳态 | 容差 | 同路径极差 | 繁体 zh / auto | 词级 p95 zh/auto |
|---|---|---|---|---|---|---|---|---|---|
| clean | 0.1821 | **0.1504** | 0.2084 | **0.1504** | **0.0** | ±0.02 | 0.0317 / 0.0580 | 0/454 ｜ 0/468 | 13.436 / 12.803 |
| noise | 0.1821 | 0.1821 | 0.1821 | 0.1821 | 0.0 | ±0.02 | n=1 | 0/426 ｜ 0/426 | 11.924 / 11.881 |
| lowvol | 0.1715 | 0.1715 | 0.1715 | 0.1715 | 0.0 | ±0.02 | n=1 | 0/417 ｜ 0/417 | 2.478 / 2.669 |
| fast | 0.4248 | 0.4248 | 0.4248 | 0.4248 | 0.0 | ±0.02 | n=1 | 0/490 ｜ 0/490 | 13.599 / 8.509 |
| multi | 0.7599 | 0.6675 | 0.6675 | 0.6359 | −0.0316 | ±0.02 | **0.0924 / 0.0791** | 1/542 ｜ 1/394 | 15.639 / 11.059 |
| long | 0.2773 | 0.2773 | 0.2696 | 0.2696 | −0.0077 | ±0.01 | n=1 | 0/3651 ｜ 1/3740 | 12.124 / 18.976 |
| real | 0.1985 | 0.1985 | 0.2017 | 0.2017 | +0.0032 | ±0.01 | n=1 | 0/2878 ｜ 0/2863 | 30.827 / 29.688 |

**结论：七格全部通过，失败 0 项**（`安装空间/_c2_final_350_report.json`，`conclusion=PASS`）。

#### 16.3.2 判据 A 的两条腿（为什么不能只用一个容差）

首跑按「|Δ| ≤ 固定容差」判会有两个假失败：`clean`（Δ=+0.0263 > ±0.02）与
`multi`（Δ=−0.0316 > ±0.02）。而**同会话重复观测**显示：同一格、同一语种，
**自身**极差就有 0.0317~0.0924（`multi/zh` σ=0.0533）⇒ **比要判的 Δ 还大**。
用单次观测的 Δ 去判「auto 是否对齐」，在这么大的噪声下**判不了**。

所以判据 A 写成两条腿，**都要求同会话可比**：

* **A1 字面容差**：`|Δ| ≤ tol` —— 只在测量本身低噪时有意义；
* **A2 噪声 + 同构提示词**：两格**实际生效**的提示词逐项相同，且 `|Δ|` 落在各自
  同路径极差内 ⇒ 差值**不可归因于 code path**。
  **A2 只在有重复观测（极差可估）时成立** —— n=1 的格不得用 A2 逃逸。

#### 16.3.3 🔴 关键发现①：auto 与 zh **实际生效的提示词逐项相同**

这是本次回归**最有价值的一条**，也是 D5 关闭的直接证据：

| 格 | 生效 sections（zh / auto） | `termPromptChars` zh/auto | `promptTokens` zh/auto | 同构 |
|---|---|---|---|---|
| clean | 全 0 / 全 0 | 0 / 0 | 42 / 42 | ✓ |
| noise | 全 0 / 全 0 | 0 / 0 | 74 / 74 | ✓ |
| lowvol | 全 0 / 全 0 | 0 / 0 | 74 / 74 | ✓ |
| fast | 全 0 / 全 0 | 0 / 0 | 74 / 74 | ✓ |
| multi | 全 0 / 全 0 | 0 / 0 | 74 / 74 | ✓ |
| long | 全 0 / 全 0 | 0 / 0 | 59 / 59 | ✓ |
| real | 全 0 / 全 0 | 0 / 0 | 43 / 43 | ✓ |

**七格全部同构**。读法：`auto` 格**不是**"保留了英文短语层所以更好"——引擎探测到中文后
V5-8 的回调把术语层**重建成了 zh**（`config.termRebuilds` 里有 `{language: zh, sections: 全 0}`），
最终下发的提示词与显式 `zh` 格**逐项相等**。也就是说 **auto 在提示词层面已与显式 zh 完全收敛**。

> ⚠️ **不要拿 `config.termPromptSections` 直接比**：它记的是**重建前**的下发配置，
> `auto` 格必然带 `phrases: 60` —— 两格在这一项上**必然不同**，那是 V5-8 的设计而非不一致。
> 生效态要看 `termRebuilds` 的末次结果。

#### 16.3.4 关键发现②：`multi` 的 −0.0316 是**测量噪声**，不是 auto 劣于 zh

`multi` 是 A2 唯一被用到的格：`Δ = −0.0316` 超字面容差 ±0.02，但
① 两格生效提示词**逐项相同**（上表）；② `|Δ| = 0.0316 < 同路径极差 0.0924`；
③ 方向是 **auto 更优**（真正的风险方向是 auto 更差）。
⇒ 判为测量噪声，**PASS**（`verdictReason = within-run-noise+identical-prompt`）。

**并且判据 B 独立支持同一结论**：`multi` 的首跑 Δ=−0.0924 **恰好落在** 同路径极差 0.0924 内。

#### 16.3.5 锚点复现与判据 B 的适用性

| 锚点（#175 基线） | #175 值 | 本次 zh 稳态 | |
|---|---|---|---|
| clean | 0.1504 | 0.1504 | **逐位复现 ✓** |
| noise | 0.1821 | 0.1821 | **逐位复现 ✓** |
| lowvol | 0.1715 | 0.1715 | **逐位复现 ✓** |
| real | 0.1985 | 0.1985 | **逐位复现 ✓** |

⚠️ **判据 B（首跑 Δ 是否落在同路径极差内）只在有重复观测时适用**：
`noise / lowvol / fast / long / real` 五格 n=1 ⇒ 极差不可估 ⇒ 本轮记为 **N/A**，
**不得记为失败**（上一版就是把 N/A 记成「超出 ✗」）。补重复跑的成本：
`long`/`real` 各 ≈10.5 min/次 ⇒ 本轮未做，如实记账。

⚠️ `D3`（跨会话 CER 波动）本轮**再次复现**：`v58_off` 条件在 V5-1 的 A/B 里是 0.438、
在 V5-8 的 A/B 里是 0.3641 ⇒ 本节所有比较**只在同会话内**成立。

### 16.4 C-3 真机端到端 e2e

**跑法**：`安装空间/_c3_e2e_350.py --stage all`（`安装空间/_c3_all.log`，`EXIT=0`，
耗时 **0:03:40**，**全部通过 ✅**）。

#### 16.4.1 安装段

| 检查 | 实测 |
|---|---|
| `tingkebanlv-3.5.0-setup.exe /S` | 退出码 **0** |
| 主程序 `VersionInfo.FileVersion` | **3.5.0** |
| 安装目录含 `resources/backend-server` | ✓ |
| 内置后端 md5 vs 新构建 | **`34a75c44c82a932393036edaa1e3b1a1`**（一致） |
| 安装目录种子模型 | **141.0 MB** |

#### 16.4.2 运行时段（E1~E4，打真机后端 API）

| 用例 | 检查 | 实测 |
|---|---|---|
| — | 应用启动后 `/api/ping` | **4.1s** 就绪，`{'ok':True,'pid':30668,'version':'3.5.0'}`；8765 LISTENING |
| **E1** auto 默认链路 | 新建课程不传参的默认值 | `language='auto'` + `simpMode='auto-simplified'` ✓ |
| **E2** 简繁开关来回切 | `raw ⇄ auto-simplified` ×4 回读 | `[('raw','raw'),('auto-simplified','auto-simplified'),('raw','raw'),('auto-simplified','auto-simplified')]` ✓ |
| **E3** 术语层降级开关 | `/api/asr/config termAutoDegrade` on ⇄ off | 两次都被接受，`applied` 回显一致 ✓ |
| **E4** 语种感知术语层 | `preview` 的 `promptChars` | **auto=794 / zh=0** ⇒ zh 抑制英文短语层、auto 保留 ✓ |

#### 16.4.3 内容层边界用例（C1~C4 + 长/混合引用）

| 用例 | 用户要求 | 实测 | 判定 |
|---|---|---|---|
| **C1** 专有名词 | 中文课注入简体引导语；术语生效；CER 不劣化 | 引导语 **on**（引擎日志 `on×6`）；生效分层 `custom=5`；`termPromptChars=24`、`promptTokens=106`；**CER 0.1478**（≤ 锚点 0.1504+0.02）；繁体 **0/448**；丢片 0 | ✅ 5/5 |
| **C2** 短句 | 短音频可用、无丢片 | 认到切片参考稿 `c2_short_15s.preproof.txt`；字幕 1 条、收尾 1.361s；引导语 **on**；繁体 **0/74**；丢片 0 | ✅ 5/5 |
| **C3** 低置信降级 | **不强行绑定中文术语层** | `segments=1`（过 VAD）；`languageSource=**low-confidence**`、`conf=0.1433`；`languageDecided=**False**`（不锁定）；`langLowConfidenceEvents=1`；引导语 **off**；`termRebuilds=[]` | ✅ 6/6 |
| **C4** 非目标语种 | 英文不注入中文引导语 | `languageSource=**non-target**`、`conf=0.8679`；引导语 **off**；`termRebuilds=[]`；CER 0.0 | ✅ 3/3 |
| **长音频** | 边界用例 | 引用 C-2 `long`(626.4s)：zh 0.2773 vs auto 0.2696，Δ=−0.0077，提示词同构 | ✅ |
| **混合语种** | 边界用例 | 引用 C-2 `multi`：Δ=−0.0316 且同构 + 噪声内 | ✅ |

**合计 21 条检查全部 OK，失败 0 项。**

#### 16.4.4 🔴 关键发现③：`hint` 的判定必须看**引擎日志**，而它默认**从不落地**

上一轮 C-3 报了 **5 条 FAIL**，其中 **4 条的根因是判定手段本身失效**，不是被测行为失败：

* `whisper_engine.py:704` 那行 `…；解码语种: x，简体引导语: on|off（来源 src）` 是
  `logger.debug(...)`，而全仓（除 `backend/main.py` 的 CLI 入口）**没有 `logging.basicConfig`**
  ⇒ root 默认 WARNING ⇒ **该行从不打印**，判定恒为 `?`。
* 修法：套一层 `安装空间/_c3_logwrap.py`（只把 `asr.whisper_engine` 开到 DEBUG）再执行，
  避免把 faster-whisper / ctranslate2 一起刷屏。

**为什么坚持用日志、而不是读 `languageSource` 这类自报字段**：自报字段只说「决策是什么」，
说不出「本次解码到底注没注引导语」—— 而**两者之间正是 D1 的病灶所在**。日志行是唯一
同时覆盖两者的证据，且它就在 `_build_prompt(simp=simp)` 的同一作用域里。

另两处取数口径也踩过（已写进脚本注释）：`config.{termPromptSections,termRebuilds,langAwareTerms}`
在**报告顶层 `config`**（不是 `eval`）；`termPromptChars/promptTokens` 在 `results[0]`。

#### 16.4.5 🔴 关键发现④：「低置信素材」必须过 VAD 与低置信**两个**门槛

用户用例 ② 要求「低置信语种音频 ⇒ 语种降级」。构造这个前置条件试了**三轮**：

| 轮 | 素材 | 结果 | 结论 |
|---|---|---|---|
| 1 | **小音量**（`gain=0.02` / `0.005`） | conf **0.9488 / 0.9433** —— 几乎不变 | ❌「小声 ⇒ 判不准」是错的直觉（whisper 对整体增益不敏感） |
| 2 | **加性噪声混音**（5 档噪声比 + 抬高电平 + 纯噪声，共 7 个） | **全部 `segments=0`** | ❌ VAD 要连续的**语音样**能量，噪声一压过语音就被判「无语音」⇒ `_resolve_language()` 根本不被调用 |
| 2' | 硬削波失真 | 过 VAD，但 conf **0.9732** | ❌ 削波破坏不了语种线索 |
| 2'' | **倒放语音** （首次尝试） | 过 VAD，`non-target-pending`，conf **0.6417** | ✅ 有戏（离 0.6 只差一点） |
| 3 | **倒放语音族**（长度 × 起点 × 轻噪声，17 个候选筛出 8 个 conf<0.6，取其 4 跑真实评估） | **4/4 命中**：`low-confidence` + `segments=1` + `hint=off` + `termRebuilds=[]` | ✅ 采纳 **12s 倒放**（conf **0.1433**） |

**倒放为什么对**：能量包络与频谱形状**不变**（VAD 照样放行），但**音素时序被破坏**
⇒ 语种判别器拿不到证据 ⇒ 置信掉下来。

⚠️ **这一格必须显式要求 `segments>0`**：否则素材被 VAD 滤光时 `languageSource=unset`，
看起来"没触发强制简体"很像通过，实际**根本不是在测降级分支**。

### 16.5 产物、指纹与副本纪律

| 项 | 值 |
|---|---|
| 交付物 | `frontend/release/tingkebanlv-3.5.0-setup.exe`｜302,587,991 B｜md5 **`f96dc04900bb1105a4342755ffaf38f8`**｜sha256 **`a1f2ff18…128904dd`** |
| blockmap | 314,337 B｜md5 `c62c45bf1f8125df57deddd368759eb0` |
| 新冻结后端 | `tingkebanlv-server.exe`｜21,739,260 B｜md5 **`34a75c44c82a932393036edaa1e3b1a1`** |
| `v3.4.1` 归档 | `安装空间/_frozen_v341/` —— md5 **`bf0af680c3851e3513fe4a3294a4413f`（未变）** |
| `v3.5.0` 归档 | `安装空间/_frozen_v350/` + `MANIFEST.json`（`archiveVerify.allMatch=true`） |
| 版本号四出口 | `frontend/package.json` / `backend/main.py::APP_VERSION` / `app.asar` / exe `VersionInfo` **全为 3.5.0** |
| 双副本对拍 | `tracked=179  same=179  diff=0  miss=0` ✅（基准用 `git ls-files`，**不用 `find`**） |
| 换行纪律 | 全仓 179 tracked：纯 LF 153 / 纯 CRLF 15 / 混合 0 / 二进制 2；**CRLF 例外文件保持 CRLF**（`scripts/verify_install.py`、`scripts/eval_asr_quality.py`、`testset/chinese_lecture/metadata.json`）；本次新增文档全 LF |
| 评估产物落点 | `packaging/eval/v350_rt_*.json`（七格×2 语种）、`v350_rep_*.json`（重复观测）、`v350_c3_*.json`（内容层）；**`packaging/eval/` 整目录有意 gitignore** ⇒ 关键数字必须落在本节的 md 里 |
| 汇总产物 | `安装空间/_c2_final_350_report.json`（`conclusion=PASS`）、`_c3_e2e_350_content.json`（含 `c2Citation`） |
| L4 产物 | `packaging/verify/report-v350.json`（**阻断那次**）、`report-v350-rerun.json`、`report-v341-recheck.json`、`report-v350-multiallow.json`；`packaging/verify/app-stdio.log`（run-1 取证） |

### 16.6 L4 安装包分发验证（**含一次竞态阻断**，已定性）

**跑法**：`scripts/verify_install.py --installer <setup.exe> --hold 150 --expect-sha256 <hex> --report <out>`
（`scripts/` 不进包，见 `write-boundary.md`）。步数口径＝报告顶层 `steps` 长度；
⚠️ 该长度**随实际走到的分支变化**（本机必然走兼容降级分支），所以「59 步」不是固定分母。

#### 16.6.1 四次运行的原始结果

| 次 | 包 | 单实例 | 渲染器状态 | 150s 稳定性 | 结论 | steps / OK |
|---|---|---|---|---|---|---|
| 2026-10-10（历史基线） | v3.4.1 | 启用 | **一次启动成功** | 6 进程 / ping **68/68** | 通过 | 59 / 57 |
| 2026-10-12 | v3.4.1（归档包复跑） | 启用 | 触发兼容降级 | 5 进程 / ping 64/64 | 通过 | 61 / 58 |
| 2026-10-12 | v3.5.0（**首跑**） | 启用 | 触发兼容降级 | **0 进程 / ping 0/35** | **阻断 1** | 62 / 55 |
| 2026-10-12 | v3.5.0（复跑） | 启用 | 触发兼容降级 | 5 进程 / ping 64/64 | 通过 | 61 / 58 |
| 2026-10-12 | v3.5.0（`TINGKEBANLV_ALLOW_MULTI=1`） | **放开** | 触发兼容降级 | 正常 | 通过 | — |

产物：`packaging/verify/report-v350.json`（阻断那次）/ `report-v350-rerun.json` /
`report-v341-recheck.json` / `report-v350-multiallow.json`；日志 `packaging/verify/app-stdio.log`（run-1）。

#### 16.6.2 🔴 关键发现⑤：阻断项是**竞态**，不是 v3.5.0 回归

首跑失败的 4 个 critical 步骤全部源自同一根因，证据链（`app-stdio.log` run-1）：

```
15:48:53 [ERROR] [fatal] 主窗口渲染进程退出 reason=crashed exitCode=-2147483645
15:48:56 [watchdog] 页面 12s 内未完成加载，判定为 Chromium 沙箱被系统策略拦截，以兼容模式（--no-sandbox）重启
15:48:58 [watchdog] 已拉起兼容模式实例 pid=10448 ...
15:48:59 [single-instance] 检测到第二次启动，将已有窗口提到前台   ← 子进程抢锁失败
15:49:00 [onboarding] 向导窗口已关闭（应用退出）
```

`main.cjs:~1385` 是 `if (!relaunchInCompatMode()) {...} app.quit();` —— **spawn 之后才 `quit`**，
而 `app.quit()` 是异步的 ⇒ 父进程在子进程抢锁时**仍持有** `requestSingleInstanceLock()`；
子进程失败后走 `main.cjs:135` 直接退出（`stdio:'ignore'` ⇒ 日志里无痕），父进程随后退出 ⇒ **0 进程**。

**三条独立证据判定「非版本回归」**：

1. `frontend/electron/main.cjs` 自 `0230e68`（v3.4.1）起**逐字节未变**（阶段B 只改 `frontend/package.json` 的 version）；
2. **同一份 v3.5.0 包连跑两次，一次阻断一次通过** ⇒ 不可复现；
3. **同机同会话 A/B**：`report-v341-recheck.json` 与 `report-v350-rerun.json`
   的 steps / OK / 非 OK 列表**三项完全一致**（61 / 58 / 同样 3 条非阻断）。

**最小修法已验证**：子进程 env 带 `TINGKEBANLV_ALLOW_MULTI=1`（`main.cjs:130` 的既有逃生口）时通过 ✔。

#### 16.6.3 口径修正：v3.4.1 那句「L4 0 阻断」不可直接对比

10-10 那次 `渲染器状态 = 一次启动成功` —— 渲染器**没崩**，兼容降级路径**一次都没走到**；
10-12 本机的 Chromium 沙箱拦截状态变了（每次都降级），该路径**首次被真正走到**，竞态才暴露。
⇒ 两次数字**不同口径**，不能拿来做版本对比。

**v3.5.0 的 L4 结论**：**2 次运行 1 通过 1 阻断；阻断项 100% 可归因到已立案的 D6 竞态**；
**v3.5.0 自身引入的阻塞项 = 0**。D6 立案见 [`known_defects.md`](./known_defects.md) **D6**，归属 **V6**。

### 16.7 已知局限（诚实记账）

* **`long` / `real` 无重复观测** ⇒ 判据 B 对这两格及 `noise/lowvol/fast` 记 **N/A**，不是通过。
  单次成本 ≈10.5 min，本轮未做。
* **`multi` / `fast` 走不同解码路径的原因仍未定位** —— 疑与实时喂入下的调度抖动
  + `condition_on_previous_text=True` 的自强化有关，**未证实，不下结论**（沿用 #175 的记账）。
* **C3 的素材是合成构造（倒放），不是"真人低置信录音"** —— 它满足「过 VAD + conf<0.6」两个门槛，
  能证明降级分支的行为；但**不能**代表真实用户遇到"语种判不准"时的音频分布。
  真实分布需要真人素材，与 `accent`（D4）同属 V6 的素材缺口。
* **C2 的 CER 0.5362 偏高** —— 15s 切片只出 1 条字幕、参考稿仅 2 段，CER 在小样本上不稳定。
  本用例的判据是「有产出、无丢片、繁体在阈值内」，**不拿 CER 当门槛**，如实记录。
* **`accent` 格仍未覆盖** ⇒ 已明确移至 **V6**（见 `known_defects.md` D4）。
* **词级延迟只报告、不设阈值** —— 沿用 #175 结论（墙钟延迟逐会话单调漂移 ≈+0.2s，
  跨会话不设阈值，时序量只报告、比较走同会话对照）。
* **D1 的第 3 条缺口（繁体化对术语高亮/生词本的产品侧影响）仍未量化** —— 属前端埋点，
  与 D1 根因无因果关系，单列不阻塞关闭。
* **L4 的「步数」不是常量** —— 本机必然走兼容降级分支，steps 在 59/61/62 之间浮动
  （`59` = 10-10 那次没走降级分支）。**不要**把「59 步」当分母做任何比率。
* **L4 的一条 critical 阻断（D6）概率未知** —— 观测到「1 次失败 / 3 次成功」，
  样本太小，**不给发生率**；且**未在真机复现**（本机是 100% 降级环境，把竞态放大了）。
* **`安装目录命名` 与 `卸载器临时目录已清理` 两条非阻断观察项在两个版本上都失败**
  （前者是 NSIS 取 npm 包名的既定行为、后者是 NSIS 自我拷贝目录），**历史一致，非本次引入**。
* **L4 未覆盖 `accent` 相关**（与 D4 同属素材缺口，V6）。

