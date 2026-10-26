# en_lecture（英文课程）场景素材入口

> **本格测的是「英文语种 + 领域术语」，不是「中文口音」。**
> 口音是 `../accent/` 那一格的事 —— **两格不可互相替代、不可合并统计**。
> 全套排期见 `docs/m3/plan_v7.md`。

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

1. **时间轴有偏移，必须多点锚点拟合**
   B 站搬运版比 OCW 原版**多约 5 秒片头** ⇒ 切片起点 `02:00` 对应 OCW **125.12 s**。
   实测方法：拿 ASR 草稿每段的首 8 个词在官方稿里找最长公共子串 → 反查 cue 时间
   → 27 个锚点拟合，**中位数 124.89 s、σ = 2.63 s**。

2. **对齐质量要用 WER 兜底**
   窗口错 200 秒 ⇒ WER 从 0.14 飙到 **0.72**。
   🔴 **但不能用「WER 最小」选窗口** —— 那是作弊解（多包一句能稀释分母）。
   正确判据是**语义边界**：切片首句是 `So what is it?`（OCW 125.62 s），
   上一句 `I'll explain why we have these covenants at the end.`（120.90~125.62 s）
   **在切片外**，且 ASR 侧也没识别到它。

3. **官方稿自己会错，且方向对 ASR 不利**
   官方转写漏了 `is` / `a` / `the`，把 `why` 转成 `whey`。
   这会让 WER **系统性偏高**（音频说对了、参考稿没写 ⇒ ASR 被判错）。
   ⇒ **逐字稿必须对着音频校，不能对着「权威稿」照抄。**

## 四、复现步骤

```bash
# 1) 抓素材（BV + 区间）
<venv>/Scripts/python.exe scripts/fetch_bili_audio.py BV1dM411U7qK \
    --start 2:00 --end 12:30 --name en_lecture_mit6034_p1 \
    --out-dir testset/english_lecture

# 2) 取官方 VTT（外部资源，不入库）
#    https://ocw.mit.edu/courses/6-034-artificial-intelligence-fall-2010/
#      c392f6e074945c7488e5835ed9a74b7d_TjZBTDzGeGg.vtt

# 3) 交叉验证对齐（官方稿 vs ASR 草稿的词级差）
<venv>/Scripts/python.exe scripts/build_en_reference.py \
    --vtt <lec1.vtt> --draft testset/english_lecture/en_lecture_mit6034_p1.draft.json \
    --clip-start 125.12 --clip-end 755.12

# 4) 包装成校对链能吃的形状
<venv>/Scripts/python.exe scripts/wrap_official_as_draft.py \
    --vtt <lec1.vtt> --audio en_lecture_mit6034_p1.wav \
    --clip-start 125.12 --clip-end 755.12 \
    --out testset/english_lecture/en_lecture_mit6034_p1.official.draft.json

# 5) 预校对（修正表逐条改官方稿自己的错）
<venv>/Scripts/python.exe scripts/preproof_transcript.py \
    --draft testset/english_lecture/en_lecture_mit6034_p1.official.draft.json \
    --corrections testset/english_lecture/en_lecture_mit6034_p1.corrections.tsv
#    ⇒ 产出 en_lecture_mit6034_p1.official.preproof.txt
#    ⇒ 复制为 en_lecture_mit6034_p1.preproof.txt（这是 resolve_reference() 认的名字）

# 6) 合规校验
<venv>/Scripts/python.exe scripts/check_transcript.py \
    --audio testset/english_lecture/en_lecture_mit6034_p1.wav \
    --transcript testset/english_lecture/en_lecture_mit6034_p1.preproof.txt

# 7) 跑基线
<venv>/Scripts/python.exe scripts/eval_asr_quality.py \
    --audio testset/english_lecture/en_lecture_mit6034_p1.wav \
    --realtime --language en --source-kind human-recording \
    --report packaging/eval/en_lecture_baseline.json
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
