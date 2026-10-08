# real/ —— 真实中文课程素材（P3 唯一阻塞项的落位目录）

这里的素材**决定了 P3 的最终结论**。`../long/` 下的 `long_zhlecture_cnn.*` 是 TTS 合成代理集
（`metadata.json` 里 `kind: "PROXY"`，无噪声、无口音、无口误），它的 CER 显著偏乐观，
**不能**用来回答「真实网课能不能用」；只有本目录的 `kind: "REAL"` 素材可以。

## 目录约定

```
testset/chinese_lecture/real/
  <name>.wav|mp3|m4a|mp4|aac|ogg   ← 音频（本地持有，不入库）
  <name>.txt                       ← 人工校对后的逐字稿（本地持有，不入库）
  <name>.draft.json / .draft.txt   ← ASR 草稿（本地持有，不入库）
  <name>.draft.srt / .review.html  ← 字幕式 / 校对页（本地持有，不入库）
  <name>.corrections.tsv           ← ASR 纠错规则表 ★入库（见下方「例外②」）
  <name>.preproof.*                ← 预校对稿（txt/json/suspects/review.html，不入库）
  metadata.json                    ← 素材清单（kind: "REAL" + sha256 + 时长）★唯一入库
  README.md                        ← 本文件 ★入库
```

> **本目录除 `README.md`、`metadata.json` 与 `*.corrections.tsv` 外，一律不入库**
> （`.gitignore` 用白名单实现）。
> 音频含他人声音自不必说；**人工校对后的逐字稿同样是原音频的衍生转录作品，受版权保护**
> —— 与素材本身承担同一套约束（2026-10-07 裁决）。
>
> **为什么用白名单而不是逐个后缀屏蔽**：逐个列是「默认泄漏」的写法 —— 以后多出任何一种
> 中间产物（新的导出格式、临时切片…），漏加一条就进仓库了。白名单的默认值是安全的那一侧。
>
> **代价（明确接受过）**：P3 结论无法从仓库直接复现。缓解方式是 metadata 里记全
> `bv` + 截取区间 + `sha256`：音频可按 BV 号与区间重新获取，但**逐字稿需要重新校对**。

### 例外②：为什么 `*.corrections.tsv` 可以入库（答辩可照此解释）

判据一句话：**规则表 vs 完整转录稿** —— 不是「重要 vs 不重要」，也不是「我们整理的就能入库」。

| | 逐字稿（`<name>.txt` / `.preproof.*` / `.draft.*`） | 纠错规则表（`<name>.corrections.tsv`） |
|---|---|---|
| 内容性质 | 原音频的**完整转录作品**，可独立阅读 | `错字模式 → 正确术语` 的**映射规则**，不可独立阅读 |
| 与原音频的关系 | 一一对应，脱离音频即失去意义 | **可脱离**音频与该节课单独使用 |
| 复用性 | 换素材即作废，必须重校 | 换素材**直接复用**，是新素材的起点 |
| 体积 / 占比 | 成篇讲课文本（本节课 3039 字） | 只有短片段（模式与术语，合计千字以内） |
| 结论 | ❌ 不入库 | ✅ 入库，沉淀为项目 ASR 纠错知识库 |

三点说明：

1. **它不含成篇文本。** 表里是 `坐标戏 → 坐标系`、`离秘 → 厘米` 这类词级映射，
   外加少量上下文片段；README 本节的「逐字稿要求」已明确 `听不清的片段标 [听不清]`，
   规则表同理只保留**定不死就标出、不猜**的记录。
2. **它是可复用资产。** 本项目后续每一节真实课都要走同一套预校对流程
   （`scripts/preproof_transcript.py`），这份表是**唯一**能跨素材积累的部分；
   不入库等于每换一节课就把人工裁决重造一遍。
3. **不构成滑坡。** 判据是**可读性 + 可复用性**，而非「文件在哪个目录」：
   任何**可脱离音频独立阅读的成篇转录**（含人工定稿、预校对稿、字幕、校对页 HTML）
   依旧一律不入库。`.gitignore` 里对应的是 `*.corrections.tsv` **这一种后缀**，
   不是「real/ 下的一切」。

> 用通配 `*.corrections.tsv` 而不是写死 `real_zhlecture_origin_plot.corrections.tsv`：
> 写死 = 下次换素材**默认漏掉**（与白名单「默认值落在安全一侧」的本意相反）。
> 能匹配上这条通配的只有规则表这一类文件。

## 素材可复现性

| 产物 | 能否重新获得 | 方式 |
|---|---|---|
| 音频 | ✅ | 按 `metadata.json` 的 `bv` + `provenance.clip` 重跑 `fetch_bili_audio.py` |
| 音频一致性 | ✅ 可核验 | 比对 `sha256` |
| ASR 草稿 | ✅ | 同一音频 + 同一模型/参数重跑 `asr_draft_transcript.py` |
| **人工逐字稿** | ❌ | 需重新人工校对（这是不入库的代价） |

## 音频要求

| 项 | 要求 | 说明 |
|---|---|---|
| 时长 | **≥ 10 分钟**（连续，不拼接） | P3 验收硬指标 |
| 内容 | **真实中文专业课**（教师讲课，非朗读、非播客、非 TTS） | 决定结论可外推 |
| 质量 | 允许真实缺陷：口误、口水话、板书停顿、键盘/翻页声、轻微混响 | **这些正是要测的**，不要后期净化 |
| 术语 | 尽量含中英混说的专业词（如 `convolution`、`f1score`、`adam`） | 直接考验术语热词与防循环 |
| 采样率 | 不限（脚本按 PyAV 解码并重采样到 16k 单声道） | 手机录音（m4a）即可 |

## 逐字稿要求（人工产出）

- UTF-8 **无 BOM**，一段一行（与 `long_zhlecture_cnn.txt` 同格式）
- 记录**实际听到的内容**，包括口误（可加 `（口误）` 标注）、重复词、口头禅
- 英文术语按实际读音记（读作字母则记字母，如 `f one score` → `f1score`；按读音则记 `adam`）
- 宁缺毋滥：听不清的片段标 `[听不清]`，**不要猜**——猜出来的字会污染 CER
- 两种「存疑」标记分属不同阶段，别混：
  - **`【?…?】` = 预校对页的临时标记**（`scripts/preproof_transcript.py` 给「定不死」的碎片打的）。
    它**必须**在复核时被消掉 —— `check_transcript.py` 的「未校对痕迹」会把它判 FAIL。
  - **`[听不清]` = 定稿里保留的诚实留白**（复核时确实听不出来才用，不是没听）。
    它是终态标记，可以通过校验；CER 统计时按已知的留白处理。

## 制作流程（五步，工具已就绪）

```bash
# ⓪ 拉素材（仅第三方平台素材需要；自录的跳到 ①）
#     BV 号 → DASH 音频 → 16k 单声道 WAV + 按时间戳连续切片
<venv>/python.exe scripts/fetch_bili_audio.py BV1BA411i7PT \
    --start 00:40 --end 11:10 --name real_zhlecture_<主题> \
    --out-dir testset/chinese_lecture/real
#     建议先加 --dry-run 看时长是否够切；工具会打印 RMS/峰值/近静音占比，
#     近静音占比过高说明窗口选在了停顿/片头音乐里，换个区间重切。

# ① 生成带时间戳的分段草稿（分块喂入，产出 draft.json/txt/srt + review.html）
#     输出末尾会打印「提示词回显」埋点：丢弃整段 / 剔除子串 的条数。
#     静音段多时模型会把提示词当成上文接着念（实测命中 17 次），清洗逻辑见
#     backend/asr/prompt_echo.py；这里看的是它到底有没有生效。
<venv>/python.exe scripts/asr_draft_transcript.py --audio testset/chinese_lecture/real/<name>.wav

# ② 预校对：高置信同音误识别批量替换 + 定不死的碎片标 `【?…?】`（不猜）
#     修正表是外置 TSV（`kind pattern replacement note`），人工把某行 kind 改成
#     `suspect` 即等于否决该替换，**不必改脚本**。三条硬规则（fix 先于 suspect /
#     组内长 pattern 优先 / 零命中行报错退出）与产出清单见
#     scripts/preproof_transcript.py 的模块文档。
#     产出：<name>.preproof.{json,txt,review.html,changes.tsv,suspects.md}
#     ⚠ 修正表写完必须**再扫一遍输出**：工具只保证「表里每行都命中过」，
#       不保证「原文里的错字都被表覆盖」（同音字有多种写法，实测漏过 `做标系`）。
<venv>/python.exe scripts/preproof_transcript.py \
    --draft testset/chinese_lecture/real/<name>.draft.json \
    --corrections testset/chinese_lecture/real/<name>.corrections.tsv

# ③ 人工复核：打开 preproof.review.html（空格播放当前段 / Ctrl+←→ 切段 / 自动存浏览器）
#     把每一处 `【?】…【?】` 对着音频改成听清的词（连标记一起删），导出后再覆盖回去。
#     ✔ **本目录约定的「定稿」就是 `<name>.preproof.txt` 本身**，不要另存一份
#       `<name>.txt`：两份并存 = 留一份会静默过期的副本，而
#       `eval_asr_quality.reference_path()` 优先取 `.preproof.txt`。
#     裁决后仍没有候选可锁定的碎片，把该行 kind 改成 `note`（**文本一字不动、
#       只撤标记**）——它们会列在 `.preproof.suspects.md` 的「仍定不死」表里，
#       不确定性留在产物里，而不是靠脑补写进正文。

# ④ 合规校验（字数/语速/未校对标记/重复段/校对痕迹，含 --selftest 探针）
#     定稿叫 `.preproof.txt`，所以要**显式 --transcript**（默认只找 <name>.txt）。
#     第 7 项「与 draft 的差异度」需要同级存在 `<定稿名>.draft.txt`
#     （即 <name>.preproof.draft.txt），没有就 skip 该项。
<venv>/python.exe scripts/check_transcript.py \
    --audio testset/chinese_lecture/real/<name>.wav \
    --transcript testset/chinese_lecture/real/<name>.preproof.txt
```

只有 ④ 打印「可用 ✓」的逐字稿才算完成，之后才能跑最终评估：

```bash
<venv>/python.exe scripts/eval_asr_quality.py \
  --audio testset/chinese_lecture/real/<name>.wav \
  --language zh --terms on --custom-terms <本课术语，逗号分隔> \
  --report packaging/eval/real-<name>.json
```

四格对照（**术语 on/off × 防循环兜底 on/off**）直接跑 `scripts/ab_terms_compare.py`，
它会把四格拉平成一张表，并把每格真实的命令行与环境变量（`TINGKEBANLV_TERM_AUTODEGRADE`）
一并写进报告，用来回答 P3 的四个问题：**真实场景 CER 是多少 / 术语热词是否帮上忙 /
防循环兜底是否必要 / 延迟与稳定性是否达标**。

```bash
<venv>/python.exe scripts/ab_terms_compare.py \
  --audio-dir testset/chinese_lecture/real --language zh --realtime \
  --conditions off-noguard,off-guard,on-noguard,on \
  --out packaging/eval/real_<name>_ab4.json
```

⚠️ **长音频必须加 `--realtime`。** 非实时喂入会把整段音频瞬间灌满队列，引擎来不及
消费就按背压丢片段 —— 实测 630s 素材丢掉 46/63 段，WER 报 0.7771，这个数量的是
「丢弃策略」而不是识别准确率，而且各格丢的段不同、彼此不可比。遇到丢弃 runner 会
**直接中止**（`--allow-drops` 才继续）。四格的名字与含义见该脚本的模块文档。

### 第二组 A/B：课程原生术语表（2026-10-08，`@zh-origin`）

**立项理由**：第一组 `--terms on` 实际注入的是 `basic_vocab.json` 里按「含空格的多词
短语」挑出的 **41 个通用英文术语**（`gradient descent ... prosocial behavior`）。素材是
中文 Origin 绘图课，两者零交集 → 第一组测到的是「往中文课塞无关英文词表掉多少分」，
**不能**推出术语功能本身有没有用。四格术语召回命中数全为 0 就是这个的旁证。

**本轮唯一变量**：`--custom-terms` 由 `@zh` 换成 `@zh-origin`，其余全部复刻。

| 项 | 第一组 | 第二组（本轮） |
|---|---|---|
| 哨兵 | `@zh`（6 个 AI 术语） | `@zh-origin`（**24 个本课 Origin 术语**） |
| custom 层 | 6 个 | **24 个 / 156 token** |
| phrases 层 | **41 个无关英文短语** | 生成 60 条，但**被 custom 层抢先占满预算** → 实际进 0 条 |
| 召回目标 | 6 个 AI 术语 → 本课出现 **0** 次 | **同 24 词 → 本课出现 66 次** |
| 提示词预算 | 220（降级 70） | 同 |
| 代码锚点 | `9e5ea9d` | `20d50d6` + `scripts/ab_terms_compare.py` = `1229c596` |

> 🔴 **勘误（2026-10-08 实测纠正）**：本节原先写「phrases 层 **0**（不传 `--domain`，
> 该层不触发）」—— **这是错的**。`build_prompt_sections` 当时**无条件**产出 60 条英文短语
> （与 `--domain` 无关）。第二组之所以看起来是 0，是因为 `prompt_budget.assemble` **按层
> 抢预算**：`custom`（24 词 = 217 token）先抢完 220 的预算，`phrases` 层只能拿到残额。
> 实测 `kept = {'custom': 24, 'phrases': 0}`。
> **这层是「被挤掉」不是「没生成」** —— 两者的区别在第 4 格降级时会暴露
> （预算降到 70 时短语层仍可能挤进来）。
> **真正的结构性修复见第三组**（`_phrases_layer_enabled` 让 zh 直接不生成该层）。

**词表出处（可逐词回溯）**：`corrections.tsv` 的 `replacement` 列（= 老师真实说出的词）
+ `*.preproof.txt` 定稿逐段核对。收词三条边界：

- 只收**名词性专业术语**；虚词、口语（其实/就是/等等）、动词短语一律不收。
- **不收 `Book`/`Graph`/`Origin`/`Label`** —— 它们本就以英文形态出现在定稿里，而
  注入英文词对中文课是已验证的干扰项（第一组结论）。要测的是「中文术语热词」这
  一个变量，不能把干扰重新夹带进来。
- 规模上限按 **token 预算**定，不按个数定：**24 词 = 156 token**（真 tokenizer 实测），
  加简体引导语 26 + 上一段文本配额 48 = **230 < 223+** 的保留窗口
  （`faster_whisper` `get_prompt()` 只留 `previous_tokens[-(448//2-1):]` = 末 223 个）。
  ⚠️ 这个数**必须用真 tokenizer 算**：启发式 `estimate_tokens` 给的是 2.3 token/字，
  真值只有 **1.08 token/字**（差 2.1 倍）—— 按启发式估会误判成「已经超窗口」。

**精确 token 账（真 tokenizer，`medium` = `whisper_models/plain/tokenizer.json`）**：

| 段 | 字符 | token |
|---|---|---|
| 24 词术语层正文 | 108 | **158** |
| 简体引导语 | 20 | 26 |
| 上一段文本（配额 48） | — | 34（原文） |
| 合计链路 | — | **218 ≤ 223 窗口 → 静默截断 0** |

对照：第一组那 41 个英文短语整串只有 **147 token** —— 比 24 个中文词还**少**。所以
「术语表太长挤爆提示词」这个直觉在两份表上都不成立（都没超窗口），第一组的伤害来自
**内容不相关**，不是长度。

**`@zh` 已冻结**：不动 `CHINESE_TERMS`，新增哨兵 `@zh-origin`。理由是一个哨兵指向
不同词表 = 第一组报告**再也无法复现**（同一命令行在不同时间跑出不同结果，可比性被
静默毁掉）。`CUSTOM_TERM_SENTINELS` 里两个哨兵并存，各自的清单都会写进报告。

**召回目标跟着注入内容走**（本轮修的第二个坑）：第一组注入 41 个英文短语、却统计
6 个 AI 术语的命中 → 那个 `0` 什么也没证明。现在 `--custom-terms` 传哨兵时，
`cn_terms` 直接取**实际注入的清单**，从机制上排除「注入 A 统计 B」。

### 第三组 A/B：修复后复跑，验证 zh 不再注入英文短语层（2026-10-08，`e385a40`）

**立项理由**：第二组暴露了「中文课走默认配置会被塞 41 个英文短语」这条**默认即伤害**
的通道。修复（`terminology._phrases_layer_enabled` 按主语言子标签反选）后复跑，
验证 terms-off 基线是否回到干净状态、术语效应是否稳定。

**配置**：与第一组/第二组**逐项复刻**（同素材、同定稿、同模型、`--language zh`、
`--realtime` 串行、默认丢弃即中止守卫）；**唯一变量 = 代码锚点** `9e5ea9d` → `e385a40`。

| 项 | 第一组 | 第二组 | **第三组（本轮）** |
|---|---|---|---|
| 代码锚点 | `9e5ea9d` | `1229c596` | **`e385a40`** |
| phrases 层 | 41 条英文**实际进入** | 60 条生成 / **0 条进入**（被挤掉） | **0 条生成**（结构性关闭） |
| `terms off` 提示词 | **794 字符** | **794 字符** | **0 字符** |
| terms off 均值 CER | 0.2059 | 0.2025 | **0.1985** |
| terms on 均值 CER | 0.2943 | 0.1925 | **0.1775** |
| **Δ CER（on − off）** | **+0.0884（+42.9%）** | −0.0100（−4.9%） | **−0.0210（−10.6%）** |
| 可采信度 | ✅ 干净对照 | ❌ 均值配对反向 | ✅ 干净对照 |

> ⚠️ **第一组/第二组的 `terms off` 提示词也是 794 字符，不是 0** ——
> 早期文档把「CLI 没传术语表」当成「提示词为空」了。修复前 `build_prompt_sections`
> **无条件**产出 60 条英文短语（`_phrases_layer_enabled` 尚不存在），
> `assemble` 实测 `kept = {'custom':0,'phrases':41}` → 真实下发的就是 794 字符 / 177 token。
> **「0」只在修复后成立。** 这恰是缺陷 (b) 的现场。详见报告 §9.2 勘误块。

**四格结果（`totalDropped` 全 0，一次通过）**：

| 格 | 提示词 | 片段→字幕 | 合并 | 复读 | 降级 | 丢弃 | 收尾 s | CER |
|---|---|---|---|---|---|---|---|---|
| G1 `off-noguard` | 0 字符 / 43 tok | 63→86 | 1 | 0 | 0 | **0** | 2.067 | **0.1985** |
| G2 `on-noguard` | 793 / 217 tok | 63→68 | 8 | 0 | 0 | **0** | 4.140 | **0.1737** |
| G3 `off-guard` | 0 字符 / 43 tok | 63→86 | 1 | 0 | 0 | **0** | 2.065 | **0.1985** |
| G4 `on` | 793 / 217 tok | 63→70 | 7 | 0 | 0 | **0** | 3.424 | **0.1813** |

**语种分流验收表**（直接调 `build_prompt_sections`，看 `phrases` 层条目数）：

| `language` | `zh` | `zh-CN` | `zh-TW` | `cmn` | `yue` | `wuu` | `""` | `auto` | `en` | `en-US` | `ja` | `ko` |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `phrases` 条目 | 0 | 0 | 0 | 0 | 0 | 0 | 60 | 60 | 60 | 60 | 60 | 60 |

**产物**：`packaging/eval/real_zhlecture_ab4_zhfix.{json,log}` + 四格原始报告
（`*.off-noguard.json` / `*.on-noguard.json` / `*.off-guard.json` / `*.on.json`）。

**两条口径陷阱（本轮实测踩到，务必区分）**：

1. **`termPromptChars` ≠ 引擎实际用的量**。前者走 `build_initial_prompt`
   （**字符**口径兜底实现），后者走 `prompt_budget.assemble`（**token** 口径，按层抢预算）。
   判「谁进了模型」一律看 **`promptTokens`**。
2. **`on` 格的 794 → 793 只差 1 字符，别误读成「修复没生效」**。修复前 794 是
   **41 个英文短语排在最前**；修复后 793 是 **24 个中文术语**（短语层为 0 后术语上浮到首位）。
   内容完全不同，判据只能是**分层计数**与**实际文本**。

### 单格准入失败后怎么补跑（2026-10-08 实测流程）

某一格因丢弃被中止、其余格 `totalDropped = 0` 时，**不必整组重跑**，按下面三步补该格：

```bash
# ① 作废的那一格先改名留证，别让它被覆盖
mv packaging/eval/real_<name>_ab4.<cond>.json \
   packaging/eval/real_<name>_ab4.<cond>.FAILED-dropped<N>.json
# ② 只重跑该格（--out 换个名字，避免污染原有三格的证据）
<venv>/python.exe scripts/ab_terms_compare.py \
  --audio-dir testset/chinese_lecture/real --language zh --realtime \
  --conditions <cond> --out packaging/eval/real_<name>_ab4.<cond>rerun.json
# ③ 把补跑的单格原始报告复制回规范名，四格文件名保持齐整
cp packaging/eval/real_<name>_ab4.<cond>rerun.<cond>.json \
   packaging/eval/real_<name>_ab4.<cond>.json
```

补跑必须在 §9.3 里**注明哪一格用的是补跑结果**（本仓库记的是
`real_zhlecture_ab4_rt.onrerun.json`），并说明补跑那次跑在该格的
`commands` / `env` 字段里 —— 否则「四格来自同一次调用」这个前提就变成默认假设了。

⚠️ **跑之前先防休眠。** `--realtime` 靠墙钟节拍喂入；机器一旦休眠/长时间卡顿，
恢复瞬间会**追赶式突发**灌满队列 → 背压丢片段。实测本机 13:40 真休眠过一次。
可用一次性看门进程（不改系统设置，退出即失效）：

```bash
<venv>/python.exe -c "
import ctypes, time
ES_CONTINUOUS=0x80000000; ES_SYSTEM_REQUIRED=0x00000001
k=ctypes.windll.kernel32
for _ in range(360):
    k.SetThreadExecutionState(ES_CONTINUOUS|ES_SYSTEM_REQUIRED); time.sleep(5)
k.SetThreadExecutionState(ES_CONTINUOUS)"
```

⚠️ **看清「terms on」到底注入了什么。** 不传 `--domain` / `--custom-terms` 时，
提示词里只剩**词表短语层**（`basic_vocab.json` 里的英文多词短语）。实测 794 字符里
是 41 个通用英文术语（`gradient descent, loss landscape, ... prosocial behavior`）——
**与中文课内容无关**。要测术语层的真实价值，术语表必须来自当前课程
（`--custom-terms @zh` 或课程内已发现术语）；否则测到的只是「无关词表的伤害」。

## metadata.json 模板

```json
{
  "kind": "REAL",
  "entries": [
    {
      "name": "real_zhlecture_<主题>",
      "audio": "real_zhlecture_<主题>.m4a",
      "reference": "real_zhlecture_<主题>.txt",
      "sha256": "<音频 sha256，用于换机核对>",
      "durationSeconds": 720.0,
      "durationMinutes": 12.0,
      "referenceChars": 3200,
      "source": "自录 / 某公开课（写明来源与获取方式）",
      "channel": "self-record | bilibili | dataset | other",
      "provenance": {
        "url": "<原始视频/音频页 URL，第三方素材必填>",
        "title": "<原标题>",
        "author": "<原作者/上传者>",
        "clip": "<截取起止，如 12:30 ~ 23:05>",
        "license": "<平台协议；学术数据集填官方许可，并注明镜像标注是否一致>"
      },
      "consent": "仅用于本地评估，不再分发；含他人声音，故音频不入库",
      "recordedAt": "2026-10-XX",
      "notes": "手机录音，教室后排，有翻页声；中英混说术语 12 个"
    }
  ]
}
```

## 素材获取渠道（2026-10-07 实测结论，避免重复踩坑）

### ✅ 自录（首选，零版权风险）

逐字稿可入库，CER 结论可外推性最强。要求见上文「音频要求」。

### ✅ B 站公开课切片（链路已实测打通）

2026-10-07 实测：`api.bilibili.com` 可达（200 / 0.9s），全链路走通 ——
`ranking` → `view?bvid` 取 `cid` → `player/playurl?fnval=16` 拿 DASH 音频
→ 带 `Referer: https://www.bilibili.com` 下载 `m4s` 成功（ftyp 头正确）。

两条边界必须先知道：

- **搜索 API 匿名被风控**（返回 HTML 而非 JSON）→ 不能自动找片，**需人工给出 BV 号/链接**。
- **字幕 API 匿名返回空数组** → **B 站字幕不可依赖**（且其多为 ASR 自动生成，本身也不合
  「人工逐字稿」的定义）。这反而与 `check_transcript.py` 的设计一致：逐字稿必须人工校。
- 容器是 `m4s`（fMP4/AAC）。项目 venv 有 `av`，`eval_asr_quality.load_audio` 走 PyAV
  分支即可解码，**无需 ffmpeg CLI**。

版权：公开传播 ≠ 授权 → 按下方「第三方素材」条款处理，**音频与逐字稿都只留本地**。

### ❌ WenetSpeech（已实测否决，不要再走）

三重锁，全部实测确认：

1. 音频以 **AES 加密包**分发，解压密码需**填写 Google 表单申请并等邮件** —— 本机
   `google.com` 不可达，且有人工审批延迟。
2. **元数据 `WenetSpeech.json.aes.tgz` 同样加密** → 「筛 `domain=talk` + 时长 > 600s」
   这一步**根本执行不了**。ModelScope 上那个 `WenetSpeech.json` 只是 **681 字节的路径
   描述文件**（指向上述 aes 包），不是数据。
3. 总量 ~697 GB（HF 卡片标 1.02 TB），且**三分之二来源是 YouTube**（本机不可达）。

另有两处事实口径要注意（别写进答辩）：

- 官方**直接托管已转码的 `.opus` 音频包**，元数据里的 `url` 字段是**版权溯源**用途，
  **不是**「需要自己按 URL 去拉」。
- 许可在 **HF/官网 = CC BY 4.0 + 非商用**，而 **ModelScope 镜像页写的是 Apache-2.0**，
  渠道标注自相矛盾。以官网/HF 为准，并在元数据里注明镜像标注不一致。

### ❌ EchoFlow（`luzhenhua/echo-flow`）

英语点读跟读工具，仓库**零音频零字幕语料**，README 自声明「不提供任何教材音频或课文
内容」。其播放器交互（倍速/循环/连播）已借到 `review.html`，但**不能作素材源**。

## 隐私与合规

- 音频含教师/同学声音：**只做本地评估，不上传、不入库、不外发**。
- **本目录统一规则**：除 `README.md`、`metadata.json` 与 `*.corrections.tsv`（唯一例外，
  判据见上文「例外②：规则表 vs 完整转录稿」）外**全部不入库**，
  自录与第三方素材**同一标准**（2026-10-07 裁决）。自录素材在版权上本可入库，
  但规则越简单越不会被绕过 —— 所以**判据按内容性质写死（能否脱离音频独立阅读），
  而不是按文件名逐个开**；任何成篇转录（含人工定稿、预校对稿、字幕、校对页 HTML）
  都不在例外之列。
- **第三方素材**：`metadata.json` 里登记来源、授权方式与 `sha256`。
  **逐字稿同样是受版权保护的转录作品**，不能因为是我们整理的就可以入库。
- 逐字稿入库前确认不含姓名、学号、联系方式等个人信息（口误里常出现点名，注意删改）。
- 公开课素材请注明来源与授权方式；不确定时优先用**自己录的课**。
