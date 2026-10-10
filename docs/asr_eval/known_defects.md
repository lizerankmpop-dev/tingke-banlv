# ASR 评估 · 已知缺陷台账

> **用途**：**立案**。每条记录「现象 → 证据链 → 复现命令 → 影响面 → 修法候选 → 缺口」，
> 保证将来任何人（包括我们自己）能凭这份文档**独立复现并判断能不能修**，
> 而不必重新考古一遍当时的实验。
>
> **纪律**（与 `WER_REPORT_PLAN.md` 一致）：
> · 每个数字都必须是**产物字段的实测值**，不得按输入侧推断；
> · 写下的命令必须是**在本机真跑过**的那一条，不是「应该能跑」的那一条；
> · 缺口要**明写**（「未确认」就写未确认），不用「应该没问题」糊过去。
>
> 相关文档：[`WER_REPORT_PLAN.md`](./WER_REPORT_PLAN.md)（P2 评估方案与执行记录）、
> [`write-boundary.md`](./write-boundary.md)（哪些路径会进安装包）

---

## D1 · `language="auto"` 让中文课程输出繁体字幕

| 项 | 值 |
|---|---|
| 状态 | 🔴 **已立案，v3.4.1 未修**（2026-10-10 用户裁决：登记不改，不重新打包） |
| 严重度 | **高** —— 命中**默认路径**上的**主要用户群**（中文课程），症状（满屏繁体）比「识别不准」更显眼 |
| 发现于 | P2 #172 跑中文 5 格场景集时（clean 格 CER 0.5198 vs 预期 ~0.17） |
| 影响文件 | `backend/asr/whisper_engine.py`（判定）+ 前端/后端默认值 |

### 一、现象

`language` 取默认值 `auto` 时，中文音频的识别输出是**繁体**：

| `--language` | 识别输出（前 40 字） |
|---|---|
| `auto` | `'同學們好,今天這節刻,我們來講戰機神經網絡,以及它背後最重要的訓練算法,反向傳播'` |
| `zh` | `'同学们好,今天这节课,我们来讲《卷机神经》网络,以及它背后最重要的训练算法,反向'` |

同一份音频、同一份参考文本，只因这个参数不同，CER 从 **0.1636** 变成 **0.5198**。

### 二、证据链

**① 判定条件**（`backend/asr/whisper_engine.py`）：

```python
SIMP_HINT = "以下是普通话的句子，请使用简体中文转写。"      # :195
SIMP_HINT_LANGS = frozenset({"zh", "zh-cn", "zh-tw"})        # :197  ← 不含 "auto"
```

注入点两处，都按同一个集合判断：

```python
self._echo = EchoCleaner(                                     # :267-269
    get_simp_hint() if (self.language or "").lower() in SIMP_HINT_LANGS else ""
)
hint = (                                                      # :922-926
    get_simp_hint() if (self.language or "").lower() in SIMP_HINT_LANGS else ""
)
```

⇒ `self.language == "auto"`（字符串，见 `:249` 赋值；`:604` 才把它折成 `None` 传给 Whisper）
时，`"auto" not in SIMP_HINT_LANGS` ⇒ **引导语根本不注入**。

**② 默认值四处都是 `auto`**：

| 位置 | 代码 |
|---|---|
| 前端新建课程表单 | `frontend/src/pages/CourseList.tsx:25` / `:42`（`language: 'auto'`） |
| 前端课程配置页 | `frontend/src/pages/CourseConfig.tsx:210` / `:224`（`course?.language \|\| 'auto'`） |
| 后端请求模型 | `backend/api/routes.py:68` / `:132`（`language: str = "auto"`） |
| 后端管道取配置 | `backend/asr/pipeline.py:64` / `:94` / `:149`（`course_config.get("language", "auto")`） |

⇒ 用户**不主动动那个下拉框**，走的就是 `auto`。

**③ 单变量对照**（同一素材 `synth_clean_short_zhlecture_cnn.wav`，`--terms on` 固定）：

| `--language` | `--term-language` | `termPromptSections.phrases` | `termPromptMd5` | `accuracy.cer` | 输出 |
|---|---|---|---|---|---|
| `auto` | `auto` | 60 | `0f5e8159…` | **0.5198** | 繁体 |
| `auto` | `zh` | 0 | `d41d8cd9…`（空串） | **0.4617** | 繁体 |
| `zh` | `auto` | 60 | `0f5e8159…` | **0.1900** | 简体 |
| `zh` | `zh` | 0 | `d41d8cd9…`（空串） | **0.1636** | 简体 |

**读数**：第 1 行与第 3 行的提示词 md5 **完全相同**（`0f5e8159…`），简繁却翻转
⇒ **`--language` 是主因，与短语层无关**。`--term-language` 只贡献次要伤害
（同样 `auto` 下 0.5198 → 0.4617；同样 `zh` 下 0.19 → 0.1636，量级 +0.03~0.06）。

**④ 与设计意图矛盾**（这是判断「遗漏 vs 取舍」的关键）：

`whisper_engine.py:194` 的注释写着：

> 但术语表可能为空（新课程、用户没选领域），所以**必须显式注入**这句，不能靠术语表顺带解决。

即设计意图是「任何情况下都要让简体中文出现在提示词里」。而 `auto` 恰恰是**最容易术语表为空**
的那条路径（新课程默认值），却没有注入 ⇒ **更像遗漏，不像有意取舍**。

### 三、复现命令（本机真跑过）

```bash
VP="/c/Users/Administrator/Desktop/听课伴侣/安装空间/venv/Scripts/python.exe"
W="testset/chinese_lecture/scenarios/short/synth_clean_short_zhlecture_cnn.wav"

for pair in "zh zh" "zh auto" "auto zh" "auto auto"; do
  set -- $pair
  $VP scripts/eval_asr_quality.py --audio "$W" --language "$1" --term-language "$2" \
      --report "packaging/verify/_cfg_$1-$2.json" >/dev/null 2>&1
done

# 从产物里读（不要凭回忆）：
$VP - <<'PY'
from pathlib import Path; import json
for p in sorted(Path("packaging/verify").glob("_cfg_*.json")):
    r = json.loads(p.read_text(encoding="utf-8")); c = r["config"]; res = r["results"][0]
    print(c["language"], c["termLanguage"], c["termPromptSections"]["phrases"],
          c["termPromptMd5"][:8], res["accuracy"]["cer"], res["recognition"][:30])
PY
```

（报告落在 `packaging/verify/`，该目录整目录被 gitignore，不会污染仓库。）

### 四、影响面

- **谁**：用中文课程、且没手动改过「语言」下拉框的用户 —— 即默认路径上的绝大多数中文用户。
- **多严重**：字幕仍是**可读**的（繁体 ≠ 乱码），但：① 与用户输入的简体术语/笔记不一致；
  ② 逐字稿导出、术语高亮、生词本匹配都会因字形不同而**失配**；
  ③ 评估口径上，CER 会被字形差异吃掉 **+0.35 以上**，让「识别质量」看起来比实际差一倍。
- **不会影响**：英文 / 日文 / 韩文课程（`SIMP_HINT_LANGS` 本就该排除它们）。

### 五、修法候选（未实施，供将来评估）

| # | 修法 | 代价 | 风险 |
|---|---|---|---|
| a | `auto` 时也注入引导语（把 `"auto"` 加进 `SIMP_HINT_LANGS`） | 一行 | **高** —— 英文课会被塞进一句中文，可能把输出带偏。**必须先用英文素材做对照**才能定 |
| b | 首段识别后拿 `info.language`，检测到 `zh*` 就把 `self.language` 改成 `zh`（并同步 `_echo`） | 中（需改引擎状态机） | 低，但**第一段**仍是繁体 |
| c | 前端新建课程默认值改 `zh` | 一行 | 低，但英文课用户变成需要手动改（把问题换了个方向） |
| d | 保持现状，仅本文档登记 | 零 | 用户侧无感知改善 |

**共同代价**：a/b/c 都要动 `backend/` 或 `frontend/` 生产代码 ⇒ **必须重新打包**
⇒ 现有 L4（`packaging/verify/report-v341.json`）与真机 e2e 结论**全部作废**，需重跑。
（划界见 [`write-boundary.md`](./write-boundary.md)：`frontend/{electron,package.json,node_modules,dist}`
与 `backend/`（非 tests）改动都会改 `setup.exe` 的哈希。）

### 六、缺口（明确未确认）

- ❌ **未在打包后的 App 里端到端确认**。本文档的证据是「代码判定条件 + 脚本直调管道」，
  二者合起来足以定位根因，但「真实用户双击安装包、新建一门默认配置的中文课、看到繁体字幕」
  这一步**没有做过**。补这一步需要：装当前安装包 → 录一段中文 → 观察字幕。
- ❌ 未测 `auto` 在**英文素材**上的表现（这恰好是修法 a 的前置实验）。
- ❌ 未量化「繁体化对术语高亮 / 生词本匹配」的实际影响（第四节②是推断，不是实测）。
