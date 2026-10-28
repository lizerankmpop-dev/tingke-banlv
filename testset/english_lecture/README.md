# en_lecture（英文课程）场景素材入口

> **本格测的是「英文语种 + 领域术语」，不是「中文口音」。**
> 口音是 `../accent/` 那一格的事 —— **两格不可互相替代、不可合并统计**。
> 全套排期见 `docs/m3/plan_v7.md`。

---

## 〇、当前有三格（同一门课的不同讲次）

| 素材 | B 站 | OCW 对应讲次 | 内容 | 参考稿 | 状态 |
|---|---|---|---|---|---|
| `en_lecture_mit6034_p1.wav` | P1 `02:00~12:30` | **Lecture 1** Introduction and Scope | AI 的定义 → 模型 → 表示（陀螺仪 / 农民狐狸鹅） | 官方稿 + **13 处人工修正** | ✅ 基线 WER 0.1130 |
| `en_lecture_mit6034_p4.wav` | P4 `10:00~20:30` | **Lecture 4** Search: Depth-First, Hill Climbing, Beam | 搜索算法 | **官方稿原文**（模式扫描零命中） | ⏳ 基线在跑 |
| `en_lecture_mit6034_p20.wav` | P20 `05:00~15:30` | **Lecture 21** Probabilistic Inference I | 概率推理 | **官方稿原文**（模式扫描零命中） | ⏳ 基线在跑 |

🔴 **B 站分 P 与 OCW 讲次不是一一对应**（实测）：
- OCW 把神经网络**拆成 12A / 12B**（12A=Neural Nets，12B=Deep Neural Nets）
- OCW 多了 **Lecture 20: The AI business**（B 站没有对应）
⇒ **B 站 P12 ↔ OCW Lecture 12A；B 站 P20 ↔ OCW Lecture 21**（偏移 1）。
**这个偏移不是假设，是用时间轴对齐独立验证出来的**（P20 的对齐记录在
`en_lecture_mit6034_p20.align.json`）。

---

## 一、这一格为什么单独存在

产品要解决的是「国外课程没字幕 + 专业名词听不懂」。
此前七格实测**全在中文上** —— 等于在产品最需要的那一半场景里**没有任何实测**。
这一格用 MIT 6.034 人工智能入门课（英文真人课堂、术语密度高、美式口音、即兴表达）
把「换成英文会怎样」这个问题第一次变成可测量的数字。

## 二、素材

| 项 | 值 |
|---|---|
| 音频 | `en_lecture_mit6034_p1.wav`（**不入库**，见下） |
| BV | `BV1dM411U7qK`（UP 汤臣安保大队长，转载 MIT 6.034 全 21 讲） |
| 分 P | P1「1.-什么是人工智能（课程介绍）」（47:14） |
| 区间 | `02:00~12:30` = **630.0 s**（与中文各格同长） |
| 规格 | 16 kHz / 单声道 / 16-bit PCM（与中文各格同口径） |
| sha256 | `229381234daf3e9dd21e206a0b660527071ee89532276d9d3b6125bd8e55cc3c` |
| 内容 | AI 的定义 → 模型思维 → 表示（陀螺仪实验、农民狐狸鹅过河）→ generate and test |

**版权**：含他人声音，音频与逐字稿**均不入库**（`.gitignore` 的 `testset/**/*.wav`
与 `testset/**/*.draft.*`）。入库的是 `metadata.json`（保可复现）与**校对链的结论**
（`*.corrections.tsv` / `*.preproof.txt` / `*.preproof.changes.tsv`）。

## 三、参考稿：这一格与中文格**路线不同**（重要）

| | 中文格（`accent` / `real`） | **英文格（本目录）** |
|---|---|---|
| 来源 | ASR 草稿 + 人工校对 | **MIT OCW 官方 VTT 字幕** |
| 成本 | 高（需逐条裁决） | 低（权威稿，只需校官方稿自己的错） |
| 风险 | 校对遗漏 | **对齐错位**（见下） |

B 站匿名访问拿不到字幕（`fetch_bili_audio.py` 模块头第 2 条），所以中文格只能自造稿。
英文格有 MIT OpenCourseWare 的**带时间码官方 VTT**（`…_TjZBTDzGeGg.vtt`，864 cue）。

### 🔴 三个必须知道的坑

1. **时间轴有偏移，必须逐格独立对齐**
   B 站搬运版比 OCW 原版**多约 5 秒片头**（P1 实测；**每格可能不同**）。
   实测方法（`scripts/align_en_clip.py`）：
   - **首尾双重定位**（最强）：首段与尾锚点各找匹配位置，**间距已知 = 切片时长**
     ⇒ 两个独立锚点 + 已知间距这个三重约束，几乎不可能被偶然满足
   - **n-gram 索引 + 投票**（每段独立定位）
   - **锚点投票**（众数而非中位数 —— 离群点会拉偏中位数）
   - 最后的窗起点取「**ASR 首段匹配所在 cue 的起点**」，保证首句完整包含

2. **对齐质量要用「词级差」兜底**
   窗口错 200 秒 ⇒ 词级差从 0.15 飙到 **0.72**。
   🔴 **但不能用「WER 最小」选窗口** —— 那是作弊解（多包一句能稀释分母）。
   正确判据是**语义边界**：切片首句必须在窗内、上一句必须在窗外。

3. **官方稿自己会错，且方向对 ASR 不利**（漏 `is`/`a`/`the`、`why`→`whey`）⇒
   WER 会**系统性偏高**（音频说对了、参考稿没写 ⇒ ASR 被判错）。
   ⇒ **逐字稿必须对着音频校，不能对着「权威稿」照抄。**
   ⇒ 校对用 `scripts/scan_official_typos.py`（模式扫描，把 P1 人工发现的 13 类
   疏漏**泛化**成扫描器；P1 复现 19 处，P4/P20 **零命中** ⇒ 讲次之间质量差异明显）。

### 🔴 第四个坑（后来才发现的）：**官方 VTT 的详略度逐讲不同**

SVM 讲（Lecture 16）的官方转写里 `margin` / `hyperplane` 出现 **0 次**，
神经网络讲里 `backpropagation` / `hidden unit` / `activation` 也是 **0 次** ——
而这些必然是那两讲的核心内容。

⇒ **MIT OCW 的 VTT 不是逐字稿，是「详略不一的字幕」**。
拿**某讲偏简略的转写**当逐字稿基准 ⇒ 音频里说了的词参考稿里没有
⇒ ASR 识别出来反被判插入错误 ⇒ **WER 系统性偏高且对 ASR 不利**。

⇒ **纪律：每一格用官方 VTT 做参考稿前，先验「详略度」。**
判据 = 「官方稿 vs ASR 草稿」的**词数与词级差**：

| 格 | 官方稿词 | ASR 词 | 词级差 |
|---|---|---|---|
| P1（Lec-1） | 1478 | 1526 | 0.1421 |
| P4（Lec-4） | 1547 | 1495 | **0.1500** |
| P20（Lec-21） | 1578 | 1607 | **0.1546** |

⇒ 三讲**词数相近、词级差一致（0.14~0.15）** ⇒ 详略度正常，参考稿可用。

## 三之三、术语库（副产品）

`mit6034_terms.md` —— 从 24 个讲次的大纲 + 官方 VTT 构建的**中英文对照术语库**
（76 条，52 条已验证 / 8 条「该讲字幕未提及但其它讲出现」/ 16 条真猜错已剔除）。

🔑 **方法教训**：第一版试图「纯统计抽术语」（跨讲次共现 × 词频），**失败** ——
产出全是课堂口语（work/equal/problem/stuff/actually），
因为口语词在**每讲都高频出现** ⇒ 跨讲次共现这个判据对它们一样成立。
**统计方法抽不出领域术语 —— 缺的是「领域先验」，不是「统计力度」。**
替代方案：**课程大纲当种子 + 转写验证**（讲次标题本身就是术语表）。
（失败版的产物保留在 `mit6034_terms_candidates.md`，文件头已标注废弃。）

## 四、复现步骤

```bash
# 1) 抓素材（BV + 分P + 区间）
<venv>/Scripts/python.exe scripts/fetch_bili_audio.py BV1dM411U7qK --page <P> \
    --start MM:SS --end MM:SS --name en_lecture_mit6034_<p> \
    --out-dir testset/english_lecture

# 2) 取该讲的官方 VTT（外部资源，不入库）
<venv>/Scripts/python.exe scripts/fetch_ocw_vtt.py \
    --slug lecture-<N>-<title> --out-dir packaging/eval/ocw_vtt

# 3) 出 ASR 草稿（用于交叉验证对齐 + 找官方稿疏漏）
<venv>/Scripts/python.exe scripts/asr_draft_transcript.py \
    --audio testset/english_lecture/en_lecture_mit6034_<p>.wav --language en --terms off

# 4) 时间轴对齐（产出切窗 + 对齐记录 + 候选稿）
<venv>/Scripts/python.exe scripts/align_en_clip.py \
    --vtt packaging/eval/ocw_vtt/ocw_lecture-<N>-<title>.vtt \
    --draft testset/english_lecture/en_lecture_mit6034_<p>.draft.json \
    --clip-start <B站切片起点> --clip-end <B站切片终点> \
    --out-dir testset/english_lecture --stem en_lecture_mit6034_<p>
#    ⚠️ 看「词级差」：>0.30 ⇒ 窗口多半错了，先查偏移
#    ⚠️ 自动定位失败时可用 --offset <人工确认的偏移> 兜底

# 5) 官方稿疏漏扫描（只产线索，不改文本）
<venv>/Scripts/python.exe scripts/scan_official_typos.py \
    --official testset/english_lecture/en_lecture_mit6034_<p>.official.txt \
    --out testset/english_lecture/en_lecture_mit6034_<p>.typos.md

# 6) 包装成校对链能吃的形状 → 预校对 → 定稿
<venv>/Scripts/python.exe scripts/wrap_official_as_draft.py \
    --vtt packaging/eval/ocw_vtt/ocw_lecture-<N>-<title>.vtt \
    --audio en_lecture_mit6034_<p>.wav \
    --clip-start <对齐得到的起点> --clip-end <对齐得到的终点> \
    --out testset/english_lecture/en_lecture_mit6034_<p>.official.draft.json
<venv>/Scripts/python.exe scripts/preproof_transcript.py \
    --draft testset/english_lecture/en_lecture_mit6034_<p>.official.draft.json \
    --corrections testset/english_lecture/en_lecture_mit6034_<p>.corrections.tsv
#    ⇒ 复制为 en_lecture_mit6034_<p>.preproof.txt（resolve_reference() 认这个名字）

# 7) 合规校验
<venv>/Scripts/python.exe scripts/check_transcript.py \
    --audio testset/english_lecture/en_lecture_mit6034_<p>.wav \
    --transcript testset/english_lecture/en_lecture_mit6034_<p>.preproof.txt

# 8) 跑基线
<venv>/Scripts/python.exe scripts/eval_asr_quality.py \
    --audio testset/english_lecture/en_lecture_mit6034_<p>.wav \
    --realtime --language en --source-kind human-recording \
    --report packaging/eval/en_lecture_<p>_baseline.json
```

### 术语库（P5）

```bash
# 抓全部 24 讲的 VTT
<venv>/Scripts/python.exe scripts/fetch_ocw_vtt.py --slug <slug> --out-dir packaging/eval/ocw_vtt

# 构建中英文对照术语库（大纲种子 + 转写验证）
<venv>/Scripts/python.exe scripts/build_mit6034_terms.py \
    --vtt-dir packaging/eval/ocw_vtt --out testset/english_lecture/mit6034_terms.md
```

## 五、口径纪律

- **英文格的 WER 与中文各格不可直接比大小**（语言不同、归一化分词不同）。
  英文按**词**统计（`normalize()` 对英文把标点换成空格再 split），
  中文按**字**统计。
- **语速检查用两套口径**：中文字/秒（1.2~8.0），英文词/分钟（70~200 wpm）。
  本讲实测 **134.8 wpm**。
- **「重复段」只抓连续重复**（ASR 循环的特征）；不连续的重复降为 WARN ——
  MIT 那讲里 Winston 故意把核心定义句重复 4 次，那是**修辞不是故障**。
- 延迟是**观测指标，不设阈值**；跨会话时序量只允许同会话对照。
- ⭐ **英文格特有的自检闸门**：WER 必须落在合理量级（首跑约 0.14）。
  **若突然跳到 0.3+，先查对齐窗口与参考稿版本**，别直接归因给模型 ——
  因为参考稿来自外部权威源，**对齐错了不会报错**。

---

## 六、术语层怎么配（**这一格跑出来的最实用结论**）

在 P1 / P4 / P20 三格上做了 12+ 次对照（详见 `docs/m3/plan_v7.md`
§〇之二 / §六之二 / §六之三），**只换「配什么词」，其余全同**：

| 选词依据 | 词数 | P4 的 WER | 机制（替换/插入） |
|---|---|---|---|
| 默认（内置 60 条**心理学**短语） | — | 0.1610 | 147 / 69 |
| 该讲**高频实义词** | 12 | 0.1441 | 94 / 89 |
| **术语库（课程大纲）** | 12 | 0.1325 | 109 / 57 |
| **模型会听错的词** | 5 | 0.1280 | 124 / 37 |
| **术语库 + 模型会听错的词** | **15** | **0.1092** ⬇️ | **76** / 54 |

### 三条结论

1. 🔑 **「全而准」，不是「少而准」** —— 15 个**对**的词比 5 个更好；
   且 15 个词的**插入错误比那 60 条无关短语还低**
   ⇒ **啰嗦不是「词数」的函数，是「词的内容」的函数**
2. 🔑 **术语价值取决于「模型会不会听错这个词」，与它专不专业无关**
   —— P1 配 8 个**真专业**的词（`representation`/`constraint`…）**一个都没帮上**
   （比关掉术语层还差），因为模型本来就会说它们
3. ⚠️ **术语库预设不能无条件推给用户** —— 对搜索课有效、对定义课无效
   ⇒ 正确形态是「**先看识别结果再推荐**」

⚠️ **这三条只覆盖英文格**。中文各格的术语层是另一回事
（那边短语层**按语种被排除**，见 `backend/dictionary/terminology.py:242`）。
