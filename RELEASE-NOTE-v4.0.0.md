# 听课伴侣 · RELEASE NOTE v4.0.0（2026-11-13）

> 分支 `release/v4.0.0`，基线 `feat/limeng-frontend`（`64139d9`，v3.9.0 新界面骨架）。
> 这是**合入队友两个功能分支后的第一个正式版本**。主界面仍是李梓萌的新骨架，没有回退、没有补丁式混搭。
> 前序版本见 `RELEASE-NOTE-v3.8.1.md` 与《本地跑通操作卡_免安装成品版_2026-11-13》。

---

## 一、这一版做了什么

| # | 内容 | 来源 |
|---|---|---|
| 1 | 合入**华为云 ASR 后端骨架 + 本地/云切换** | CodeArts `feat/huawei-asr`（`b4285bc`）→ merge `629afee` |
| 2 | 合入 **ADR-001 AI 生成专业词汇**（云端主通道 + 本地 Ollama 降级） | CodeArts `feat/ai-term-suggest`（`c34a02b` + `6869a42`）→ merge `cb4667b` |
| 3 | 把 AI 生成入口**移植到新骨架主路由** | 本次适配（否则成品里看不见） |
| 4 | 版本号 3.9.0 → **4.0.0**、PyInstaller hiddenimports 补齐 | 本次适配 |
| 5 | 重新打包 + 包内验收 | 本次交付 |

两次 `git merge --no-ff` **均无冲突**。合并前用 `git merge-base` 确认两个分支的真实基线是 `e052c2a`
（不是 `cd2a41f`），因此没有出现「整片删除」式假 diff。

### 1.1 华为云 ASR（🔴 骨架，不是真实接入）

- 新增 `backend/asr/huawei_asr.py`（534 行）：`HuaweiASRBackend` 生命周期、可注入 transport、
  float32 16k 单声道 → int16 PCM、200ms 分块、有界队列、结果事件与统计；
- 新增 `backend/asr/backend_factory.py`（63 行）：`create_asr_backend()` 按
  `course_config["asrBackend"]` > `TINGKEBANLV_ASR_BACKEND` > **本地 Whisper** 选择，未知值告警后回退本地；
- `backend/asr/pipeline.py` **只替换了后端构造点**，本地 Whisper / 音频 / VAD / 字幕 / 术语链路未动。

> ⚠️ **对外口径**：华为云 SIS 服务**未开通**，AK/SK / endpoint / projectId / token 均未配置；
> 真实 SIS endpoint、签名、音频帧与 partial/final 事件解析、心跳重连刻意保留为
> `TODO(huawei-sis-1..4)`，不猜协议、不联网。**默认仍是本地 Whisper，演示与验收不依赖云。**
> 说「已支持华为云 ASR」是错的，只能说「已完成可切换的后端抽象与骨架」。

### 1.2 ADR-001 AI 生成专业词汇

- 新增 `backend/llm/term_generator.py`（301 行）：云端主通道 5s → 超时降级本地 Ollama，
  总预算约 8s；**双通道都不可用返回 503，不阻塞 ASR 主链路**；结果不落库，由前端写入 `customTerms`；
  只读环境变量 `TINGKEBANLV_CLOUD_API_BASE / _KEY / _MODEL`，无硬编码密钥；
- 新增 `POST /api/terms/generate`；
- 新增 `frontend/src/lib/termApi.ts`（102 行）：前端超时 `TERM_GENERATE_TIMEOUT_MS = 8500`，
  `mergeTerms()` 按中英文逗号拆分、trim、大小写不敏感去重、保持原顺序，**绝不清空用户已填内容**。

> ⚠️ **使用前提**：本机未启动 Ollama、也没配云端密钥时，点「AI 生成术语」会返回明确的
> 失败提示（包内验收实测 `503`）。这是**设计内的优雅降级**，不是打包坏了。

### 1.3 🔴 关键适配：按钮差点在成品里看不见

原分支把「AI 生成」按钮加在旧页 `frontend/src/pages/CourseConfig.tsx`，
而 v3.9.0 之后主路由挂的是 `frontend/src/shell/` 下的五页，旧页已不挂载。
若直接合并，功能存在但**界面上找不到入口**。

本次把 `generateTerms / mergeTerms / TERM_GENERATE_TIMEOUT_MS`、按钮 UI、状态提示
整体移植到 `frontend/src/shell/pages/ConfigPage.tsx`，并在
`frontend/tests/limeng-shell.test.mts` 加了一条守卫测试（新骨架必须含该入口）。

### 1.4 冻结后端的隐性坑（已堵）

`/api/terms/generate` 里的 `llm.term_generator` 是**函数内惰性 import**，
`asr.pipeline → asr.backend_factory → asr.huawei_asr` 是新增链，PyInstaller 静态扫描都可能漏收
（表现是包能启动、端点却 500/404，最难排查）。`packaging/backend_server.spec` 显式补了：

```python
"llm.term_generator",
"asr.backend_factory",
"asr.huawei_asr",
```

---

## 二、门禁与验收（实测，非引用旧文档）

| 项 | 结果 |
|---|---|
| 后端 pytest | **620 passed / 1 skipped / 1 warning**（39.46s） |
| 前端 `npm test` | **256 pass / 0 fail** |
| `tsc --noEmit` | **0** |
| `vite build` | OK |
| 包内验收 | **13/13 全过** |

包内验收（仓库内可复核：`scripts/v400_upgrade_smoke.py`，路径从脚本自身推导、不写死机器绝对路径）逐条：

- 冻结后端启动 3.9s，`/api/ping` → `version = 4.0.0`；
- 老库升级：3 门课程 / 14 条字幕无损，`course_notes` 自动建表，note GET/PUT/回读一致；
- 🔴 `POST /api/terms/generate` → **503**（云端未配置 + 本地 Ollama 不可用），
  **不是 404 / 500** ⇒ 模块确实打进了包、降级链路按设计工作；
- `GET /api/asr/model-status` → 200，本地 ASR 链路未被云骨架破坏；
- `GET /api/courses` → 200。

> 验收脚本本轮顺带修了一个**过时前提**：本机老库已被 v3.9.0 真实启动过，`course_notes` 早就在了。
> 现在脚本只在测试副本里 `DROP TABLE course_notes` 构造「升级前快照」，**绝不动真实库**，
> 这样仍然严格验证了启动时自动建表这条链路。

---

## 三、产物（认准指纹，别拿错包）

| 文件 | 大小 | sha256（前 16 位） |
|---|---|---|
| `tingkebanlv-4.0.0-setup.exe` | 312,153,901 B | `d47a0e5691a0896e` |
| `win-unpacked-4.0.0.zip` | 381,121,366 B | `ed5f73341b1697d2` |

zip 含 976 个文件、解压后约 900MB。**v3.9.0 的 `79155fdc…` / `b6a09f6c…` 是上一版，不是废包，但别再当最新发。**

**升级方式**：老用户直接解压覆盖 `win-unpacked` 即可，课程 / 字幕 / 笔记 / 生词全部保留（已实测）。

---

## 四、这一版明确没做（防口径翻车）

| 没做 | 说明 |
|---|---|
| 华为云 SIS 真实接入 | 只有骨架与切换开关；服务开通、鉴权、协议解析、心跳重连都没做 |
| 云端 LLM / 本地 Ollama 的可用环境 | 代码链路齐，但本机没起 Ollama、没配云密钥 ⇒ 点按钮会提示失败 |
| F11 手动添加生词 | 后端已就绪、前端仍 0 入口 |
| F12 补漏清单快照 | 未开始 |
| F3 章节选择 / 按章筛选 | 新骨架中仍退化，`connectASR(course.id, '')` 传空章节 |
| 结束课堂 / 历史字幕回看 | 字幕**数据一直在库里**（有 `session_id`），缺的是 UI 读 `/subtitles`；不是数据丢失 |
| 证据类（10dB 加噪 A/B、访谈纪要、平台留痕、五文件夹） | 见《改进方向与收口行动清单_2026-11-13》 |

---

## 五、下一步

按《改进方向与收口行动清单_2026-11-13》§四的最小集合推进：
`F13 10dB 加噪 A/B → F11（顺带 F3）→ 文档数字与证据口径统一`。
本次 4.0.0 是**代码合并交付**，不替代上述收口动作。

---

*版本自检：侧栏底部版本号或「设置 → 关于」应显示 **v4.0.0**。*
