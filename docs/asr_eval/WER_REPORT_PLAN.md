# P2：ASR 质量评估补全（分场景 CER/WER 报告）—— 方案与用例准备清单

> 状态：**方案待决策**（§8 有 4 个待定项）。
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
| H1 | **来源维度缺失**：`scenario` 只有 clean/noise/…/long/other，**合成劣化**与**真实录音**会被同名合并进同一个 `byScenario` 桶 | 结果里加 `sourceKind`（`tts` / `tts-degraded` / `real` / `synth-en`），由文件名或 metadata 判定；汇总里按 `(sourceKind, scenario)` 二维分组 | 改汇总结构 → 会动 `summarize()` 的产物 schema；需同步 `analyze_zh_ab.py` |
| H2 | 中文只有 `--max-wer` 门禁，没有 `--max-cer` | 加 `--max-cer`（与 `--max-wer` 同构） | 低 |
| H3 | 长素材做场景矩阵太慢（4 场景 × 10min，`--realtime` 下约 40min/轮） | 给 `make_asr_testset.py` 加「先截 N 秒再劣化」（如 `--clip-seconds 90`）；或在生成阶段用更短的源 | 低 |
| **H4** | **无效轮次防护不均匀**（已核实）：`ab_terms_compare.py:435` **有**「背压丢弃 → 默认中止」的守卫（`--allow-drops` 才继续）；但**没有**「`totalSubtitles == 0` → 中止」的守卫（全文不引用 `verdict`/`passed`） | 加一条同构守卫：子报告 `summary.totalSubtitles == 0` 或 `verdict.passed == false` → 中止（除非显式 `--allow-invalid`） | 中（这正是 `short.json` 那种「整轮一个字幕没出、每格 WER 恒 1.0」还能进对照表的通道） |
| H5 | ✅ **已核实无缺口**：报告的 `recognition` = 全部字幕文本 `" ".join(...)`（`eval_asr_quality.py:553/583`），**无截断**；`--detail-limit 200` 只作用于 `segmentDetail` | 无需改动（错误归因可直接用 `recognition` vs `reference`） | — |
| H6 | 素材指纹未进入报告 | 把**音频 sha256 + 参考文本 sha256** 写进报告（教训 29：对照实验先证「被操纵的变量真变了」；P1 的 `artifact` 字段就是这个思路） | 低～中 |

🔴 **H1 是硬需求**：如果不加来源维度，方案 C 的「合成」与「真人」数字会被平均成一个没有意义的数
—— 这正是本仓库反复踩过的「口径混用」（见 `LESSONS.md` §10 / §35）。

---

## 6. 用例准备清单（可勾选执行）

### 6.1 素材（方案 A 主线）

- [ ] 确认目标目录（建议 `testset/chinese_lecture/scenarios/`；`testset/**/*.wav` 已被 gitignore 屏蔽，音频不入库 ✅）
- [ ] 跑 `make_asr_testset.py --source testset/chinese_lecture/long/long_zhlecture_cnn.wav --out …`
- [ ] 核对产物：`synth_{clean,noise,lowvol,fast,multi}_long_zhlecture_cnn.wav` **+ 同名 `.txt`**（参考文本随劣化复制，必须逐字节等于源 txt）
- [ ] 跑 `check_transcript.py --audio <每个 wav>` 确认逐字稿仍合规（语速区间会因变速而变，**fast 格必然偏离**，需在白名单里说明）
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

### 6.4 口音格（依赖 §8 决策）

- [ ] 若走方言 TTS：先 `pip install edge-tts`（**当前 venv 与管理 python 都没有**）→ `edge-tts --list-voices` **实测**可用 voice（不凭记忆写 voice id）
- [ ] 若走真人：并入 6.3 的锚点段
- [ ] 若决定留空：在报告里显式登记为**已知缺口**（现状做法），不得用「变调」冒充

---

## 7. 报告表样（跑完填）

| 来源 | 场景 | n | CER | WER | 丢片 | 合并 | 字幕>片段 | 尾延迟 s | RTF |
|---|---|---|---|---|---|---|---|---|---|
| tts-degraded | clean | | | | | | | | |
| tts-degraded | noise(8dB) | | | | | | | | |
| tts-degraded | lowvol(−30dBFS) | | | | | | | | |
| tts-degraded | fast(1.35×) | | | | | | | | |
| tts-degraded | multi(0.35s/0.75) | | | | | | | | |
| real | clean（真人课 630s） | | | | | | | | |
| real | accent | | | | | | | | |

> 空单元格 = 尚未测得。**不得填 0、不得填推断值。**

---

## 8. 待决策项（需要拍板）

| # | 决策 | 选项 | 我的建议 |
|---|---|---|---|
| **D1** | 中文场景素材走哪条路线 | A 纯合成 / B 纯真人 / **C 合成 + 真人锚点** | **C**：先用 A 拿到管道鲁棒性量化，再补 1 段真人做锚点；两者分开统计 |
| **D2** | `accent` 格怎么办 | ①方言 TTS（需装 edge-tts，且「方言播音腔」≠「带口音普通话」）②真人带口音 ③显式留空登记为缺口 | **③ 留空 + ② 若恰好有合适真人素材则顺带补**；不要为了凑满 6 格而用 TTS 冒充口音 |
| **D3** | 是否先做中文短样本（60~90s）以加快场景矩阵 | 是（快，但失去「长时稳定性」）/ 否（10min 直接上，慢） | **是**：短样本做 4 场景矩阵，10min 只留 clean/long 测长时稳定性 |
| **D4** | 是否改 harness 加 `sourceKind`（H1） | 改（口径干净，但要动 `summarize()` schema）/ 不改（只靠文件名区分） | **改**：不改的话方案 C 的两类数字必然被平均，等于白测 |

---

## 9. 不做的事（明确划界）

- ❌ 不用「变调」造口音（`make_asr_testset.py` 已写死不做，理由充分）
- ❌ 不把合成劣化的 CER 说成「真实课堂的识别准确率」
- ❌ 不把 `check_transcript.py` 未通过的逐字稿当参考文本
- ❌ 不用 11 秒名句类素材建基准（§3.1 的三个坑）
- ❌ 不改 `backend/`、`frontend/`（会改安装包 → 作废 v3.4.1 证据；见 `write-boundary.md`）
- ❌ 不把 `packaging/eval/` 的报告当可入库证据（该目录 gitignore）→ 关键数字必须落到**仓库内的 md**
