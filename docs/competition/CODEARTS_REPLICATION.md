# 听课伴侣 · 华为云码道（CodeArts）完整复刻手册

> 文档性质：架构师交付物（面向开发团队）  
> 适用场景：2026 华为开发者大赛・智能体创新赛「学业辅助工具」方向  
> 当前基线版本：`frontend v3.7.0` / 仓库 tag `v3.7.2`（V7 全闭环里程碑）  
> 编写视角：多年国企软件工程架构师，强调"可追溯、可复刻、可交付"

---

## 0. 一句话定位与参赛可行性

**听课伴侣**是一套面向高校学生的「实时听课辅助桌面软件」：把网课/会议的系统声音实时转写成字幕，自动识别生词并可一键查词、加入复习词库（SM2 间隔重复算法），配套课程管理、导出字幕、AI 问答与课程总结。

- 技术形态：Electron 桌面壳 + React/Vite 前端 + FastAPI(Python) 后端 + faster-whisper 本地 ASR + SQLite 本地库 + LangChain 驱动的词义讲解/问答 Agent。
- 与赛题契合度：**高**。属于"智能体（Agent）创新赛"中"学业辅助"赛道——LLM 以 Agent 范式（LangChain `RunnableBranch` + checkpointer 记忆 + 本地 RAG）支撑"查词讲解 / 课程问答 / 总结"三类智能体行为，符合"智能体"定义。
- **参赛红线（务必先读 §9）**：本赛 25 分权重考核"是否在华为云码道（CodeArts）中完成核心开发"。本项目原始代码在本机 WorkBuddy 中产出，**当前 0 条 CodeArts 痕迹**。本手册第 9 章给出"把全部开发动作迁移到 CodeArts"的可执行工作流与留痕清单；**真实开发动作必须由团队本人在 CodeArts 内完成，AI 无法伪造**。

---

## 1. 技术栈与版本清单（精确到版本，复刻时锁定）

### 1.1 前端 / 桌面壳

| 组件 | 版本 | 用途 |
|---|---|---|
| Node.js | 20.x（LTS） | 构建与 electron 运行环境 |
| Electron | ^31.3.1 | 桌面壳、多窗口、热键、Python 后端拉起 |
| Vite | ^5.3.5 | 前端构建（dev server / 生产打包） |
| TypeScript | ^5.5.4 | 类型安全 |
| React | ^18.3.1 | UI 框架 |
| react-dom | ^18.3.1 | DOM 渲染 |
| react-router-dom | ^6.26.0 | SPA 路由（注意 FIX-10 路由守卫） |
| zustand | ^4.5.4 | 轻量全局状态 |
| dexie | ^4.0.4 | 前端本地 IndexedDB（课程/配置缓存） |
| lucide-react | ^0.419.0 | 图标 |
| electron-builder | ^24.13.3 | NSIS 安装包打包 |
| concurrently / wait-on | ^8.2.2 / ^7.2.0 | dev 联调编排 |

> `package.json` 关键入口：`"main": "electron/main.cjs"`，`"productName": "听课伴侣"`。  
> 构建脚本：`dev`(vite) / `build`(`tsc && vite build`) / `electron:dev`(vite+electron 并行) / `backend:build`(PyInstaller 冻结后端) / `package:win`(种子+前端+后端+electron-builder)。

### 1.2 后端（Python）

| 组件 | 版本约束 | 用途 |
|---|---|---|
| Python | 3.11（推荐；3.13 亦可但需验证 faster-whisper） | 运行时 |
| faster-whisper | >=1.0.3 | 本地 ASR 引擎（Systran 实现，CPU/GPU 自适应） |
| silero-vad | >=5.1 | 语音活动检测（VAD），只在有语音时推理，省算力 |
| PyAudioWPatch | >=0.2.12（仅 win32） | **WASAPI loopback 采集系统声音**，免去虚拟声卡 |
| pyaudio | >=0.2.14（非 win32） | 跨平台音频采集后备 |
| fastapi | >=0.112.0 | Web API |
| uvicorn[standard] | >=0.30.6 | ASGI 服务器 |
| websockets | >=12.0 | 预留通道 |
| httpx | >=0.27.0 | 后端对外 HTTP 客户端 |
| aiosqlite | >=0.20.0 | 异步 SQLite |
| numpy | >=1.26.0 | 数值计算 |
| soundfile | >=0.12.1 | 音频读写（WAV） |
| langchain-core / langchain | >=1.0.0 | Agent / LCEL 链 / 记忆 |
| langchain-ollama | >=1.0.0 | 本地 Ollama 模型接入 |
| langchain-openai | >=0.3.0 | 云端 OpenAI 兼容（DeepSeek/Kimi/智谱/OpenAI） |
| langchain-text-splitters | >=0.3.0 | 字幕语义分块（RAG） |
| pydantic | >=2.0.0 | 结构化输出 schema |
| ollama | >=0.3.0 | Ollama 客户端 |
| python-dotenv | >=1.0.1 | 环境变量 |
| pytest | >=8.0.0 | 测试 |

> 注释：LangChain 引入是课程作业（"第四章作业：基于 LangChain 构建智能应用"）的硬要求——把原手写 prompt 拼接改为声明式 LCEL 链，并用 `create_agent()` + checkpointer 实现 Agent/RAG/Memory 三范式。**复刻时必须保留 LangChain 依赖，否则偏离作业要求**。

### 1.3 模型与资源

| 资源 | 规格 | 说明 |
|---|---|---|
| faster-whisper 模型 | `base`（默认，~141 MB）/ `tiny`(~75MB) / `small`(~464MB) | 环境变量 `TINGKEBANLV_WHISPER_MODEL` 覆盖；模型目录 `packaging/seed/whisper_models`（HF 缓存结构 `models--Systran--faster-whisper-{size}`） |
| Ollama 本地 LLM | `qwen2.5:7b`（默认讲解模型） | 词义讲解、课程问答、总结；embedding 用 `nomic-embed-text`(~274MB) |
| 术语库 | 23 讲次 / 76 条（52 条已验证） | OCW 课程术语，位于 `backend/dictionary/` |
| 本地词典 | 离线兜底 | `backend/dictionary/local_dict.py`，命中即返回，不调 LLM |

---

## 2. 仓库目录结构（复刻后应保持）

```
听课伴侣/
├─ README.md                      # 项目总说明（已 43KB）
├─ SUBMISSION.md                  # 原提交说明
├─ package.json                   # 根：dev 编排（concurrently 前端 + 后端 main.py）
├─ backend/                       # Python 后端（FastAPI）
│  ├─ main.py                     # 入口：创建 app、挂载路由、CORS、生命周期
│  ├─ requirements.txt            # 依赖（见 §1.2）
│  ├─ dependencies.py             # 依赖注入（DB / LLM 客户端单例）
│  ├─ api/
│  │  └─ routes.py                # 全部 REST 路由（见 §5）
│  ├─ asr/                        # ASR 流水线
│  │  ├─ whisper_engine.py        # faster-whisper 封装
│  │  ├─ pipeline.py              # 协调 采集→VAD→ASR→热词
│  │  ├─ repetition.py            # 重复片段抑制
│  │  ├─ prompt_echo.py           # 调试回声
│  │  ├─ prompt_budget.py         # 提示词预算（热词上限）
│  │  └─ model_bootstrap.py       # 模型懒加载
│  ├─ audio/
│  │  ├─ capture.py               # PyAudioWPatch loopback 采集
│  │  └─ vad.py                   # silero-vad 封装
│  ├─ llm/
│  │  ├─ client.py                # LLM 客户端（Ollama/OpenAI 兼容）
│  │  ├─ explainer.py             # 词义讲解 Agent（核心）
│  │  ├─ extractor.py             # 术语抽取
│  │  ├─ qa.py                    # 课程问答 Agent
│  │  ├─ summarizer.py            # 课程总结 Agent
│  │  └─ chains/                  # LCEL 链定义
│  ├─ dictionary/
│  │  ├─ terminology.py           # 术语库加载/匹配
│  │  ├─ local_dict.py            # 离线词典
│  │  └─ _merge_batches.py        # 批合并工具
│  ├─ database/
│  │  └─ manager.py               # SQLite 管理 + 迁移纪律（见 §6）
│  └─ tests/                      # pytest 套件（512 passed / 1 skipped 门禁）
├─ frontend/                      # React/Vite/Electron 前端
│  ├─ package.json                # 见 §1.1（version 3.7.0）
│  ├─ vite.config.ts
│  ├─ tsconfig.json
│  ├─ electron/
│  │  └─ main.cjs                 # Electron 主进程（见 §7）
│  ├─ src/                        # React 源码
│  │  ├─ pages/                   # CourseList / CourseConfig / ReviewPage …
│  │  ├─ components/              # WordPanel / OverlayWindow / DictionaryWindow …
│  │  └─ store/                   # zustand
│  ├─ build/icon.ico              # 安装包图标
│  └─ release/                    # 打包产出（win-unpacked / setup.exe）
├─ packaging/
│  ├─ backend_server.spec         # PyInstaller 冻结脚本
│  ├─ requirements-build.txt      # 打包期依赖
│  ├─ seed/whisper_models/        # 预置 Whisper 模型（HF 缓存结构）
│  └─ eval/                       # ASR 质量评估脚本与测试集
├─ testset/                       # 评测语料（english_lecture / chinese_lecture）
├─ scripts/                       # 工具脚本（verify_install / eval_asr_quality …）
├─ docs/                          # 文档（asr_eval / m3 / competition）
└─ tingke-banlv/                  # 平铺副本（交付用，需与 main 同步）
```

> **复刻纪律**：`backend/`、`frontend/` 是唯二源码根；`tingke-banlv/` 是给评委/学长的"开箱副本"，必须与 main 同步（本仓库已通过 `v3.7.2` 同步关键修复）。

---

## 3. 本地环境搭建（团队每人一套）

### 3.1 前端环境

```bash
# 安装 Node 20 LTS（建议 nvm 管理）
node -v   # 期望 v20.x
npm -v

cd frontend
npm install          # 安装全部依赖（含 electron / electron-builder）
```

### 3.2 后端环境（Python venv）

```bash
cd backend
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt

# 安装 Ollama（本地 LLM 运行时，独立于 Python）
# 见 https://ollama.com ，拉取模型：
ollama pull qwen2.5:7b
ollama pull nomic-embed-text
```

### 3.3 Whisper 模型种子

模型不进 git（体积大）。两种获取方式：
- **联网**：首次启动 `whisper_engine` 会自动从 HuggingFace 下载 `Systran/faster-whisper-base` 到 `HF_HOME`。
- **离线/内网（推荐交付方式）**：把预置目录 `packaging/seed/whisper_models/`（`models--Systran--faster-whisper-base/...` 的 HF 缓存结构）随包分发，并设置环境变量：

```bash
export HF_HOME=<项目>/packaging/seed/whisper_models
# 或 Windows
set HF_HOME=C:\path\to\packaging\seed\whisper_models
```

> 评测/部署务必加 `--realtime` 参数（见 §5 末尾注）：离线一次性喂 630 秒音频会让 Whisper 只认最后 30 秒，WER 会虚假飙到 0.67；真实基线 WER≈0.1130。

---

## 4. 后端启动与运行模式

### 4.1 开发联调（根目录一键）

```bash
# 根 package.json: "dev": concurrently "cd frontend && npm run dev" + "cd backend && python main.py"
npm run dev
```

### 4.2 后端独立启动

```bash
cd backend
python main.py
# 默认监听由 Electron 动态分配端口；独立运行时读取 PORT 环境变量（默认 8787 左右）
# 健康检查：GET /api/ping  → {"status":"ok"}
# 完整健康：GET /api/health
```

### 4.3 Electron 主进程如何拉起后端（关键机制，复刻必读）

`frontend/electron/main.cjs` 的逻辑（不可省略）：
1. 启动时**动态挑选一个空闲端口**（避免与已占用端口冲突）。
2. 开发模式按以下优先级解析 Python 解释器：`TINGKEBANLV_PYTHON` 环境变量 → `backend/.venv`（贡献者标准流程）→ `安装空间/venv`（开发机布局）→ 系统 `py -3`/`python`（须能 `import fastapi, uvicorn`）；解析到后 `spawn <python> main.py`（工作目录 backend）。打包模式：spawn PyInstaller 冻结出的 `backend-server/backend-server.exe`。
3. 轮询 `GET /api/ping`，直到返回 ok（超时则弹错误）。
4. 把端口写入前端可读取的位置（环境变量 `TINGKEBANLV_PORT` / 注入到渲染进程）。
5. 前端**以 HTTP 轮询（每 500ms）** 而非 WebSocket 与后端通信：`POST /asr/start` 开流，`GET /asr/poll` 取字幕增量。

> **复刻要点**：前端与后端**不是** WebSocket 长连，是 500ms 轮询。这是 FIX-01 稳定性攻坚后的最终架构，不要"优化"成 WS，否则会引入重连复杂度。

---

## 5. 后端 API 全量清单（routes.py + main.py）

> 基址：`http://127.0.0.1:<PORT>`。下列为复刻时必须实现的接口。

### 5.1 课程管理
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/courses` | 课程列表 |
| POST | `/courses` | 新建课程 |
| PUT | `/courses/{id}` | 更新课程配置（语言、术语域、简化模式等） |
| DELETE | `/courses/{id}` | 删除课程 |
| GET | `/courses/{id}/subtitles` | 该课程字幕 |
| POST | `/courses/{id}/summary` | 生成课程总结（AI） |
| GET/PUT | `/courses/{id}/terminology` | 课程自定义术语 |

### 5.2 识别会话
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/sessions` | 会话列表（含时间戳，已修复 v3.7.1） |
| GET | `/export-srt` | 导出 SRT 字幕 |

### 5.3 词汇本（SM2 间隔重复）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/vocabulary` | 词库列表 |
| GET | `/vocabulary/stats` | 统计 |
| GET | `/vocabulary/due-summary` | 今日待复习概览 |
| GET | `/vocabulary/review-queue` | 复习队列 |
| GET | `/vocabulary/export.csv` | 导出 CSV |
| GET | `/vocabulary/backup.json` / POST | 备份/恢复 |
| GET | `/vocabulary/{word}` | 单词详情 |
| POST | `/vocabulary/{word}/collect` | 收藏单词 |
| POST | `/vocabulary/{word}/review` | 提交复习结果（更新 SM2） |
| PUT | `/vocabulary/{vocab_id}` | 编辑 |
| DELETE | `/vocabulary/{word}` | 删除 |

### 5.4 AI / 智能体（LangChain 驱动）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/ai/status` | LLM 可用状态 |
| POST | `/ai/explain` | **词义讲解 Agent**（核心） |
| POST | `/ai/ask` | 课程问答 Agent |
| POST | `/courses/{id}/terminology` | 术语抽取（见 5.1） |
| GET | `/terminology/domains` | 术语域列表 |
| POST | `/terminology/preview` | 术语预览 |

### 5.5 音频 / ASR 控制
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/audio/check-loopback` | 系统声音回环采集自检 |
| GET | `/asr/model-status` | 模型加载状态 |
| POST | `/asr/start` | 开始识别（传课程/语言/术语） |
| POST | `/asr/stop` | 停止 |
| POST | `/asr/config` | 运行时配置 |
| GET | `/asr/poll` | **轮询取字幕增量**（500ms） |

### 5.6 实例 / 健康检查
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/instance/status` | 单实例状态 |
| GET | `/api/ping` | Electron 拉起探活 |
| GET | `/api/health` | 完整健康 |
| POST | `/api/llm/probe` | LLM 探测 |

> **评测参数注**：`scripts/eval_asr_quality.py` 必须带 `--realtime`（或 `--realtime-speed 2/3/4` 压缩墙钟）才能复现真实 WER；`verify_install.py` 用 `build_opener(ProxyHandler({}))` 绕过代理直连后端，避免"看到代理响应误判成功"。

---

## 6. 数据库 Schema 与迁移纪律（SQLite）

- 路径：环境变量 `TINGKEBANLV_DATA_DIR` 或 `TINGKEBANLV_HOME`，默认 `~/.tingkebanlv/data/tingkebanlv.db`。
- 核心表：`courses`（课程）、`subtitles`（字幕）、`vocabulary`（词库）。

### 6.1 vocabulary 表（SM2 字段）
| 字段 | 类型 | 说明 |
|---|---|---|
| word | TEXT PK | 单词 |
| translation | TEXT | 释义 |
| repetitions | INT | 连续答对次数 |
| efactor | REAL | **易用度因子，默认 2.5**（区间 1.3~5.0） |
| review_interval | INT | 当前复习间隔（天） |
| next_review_at | TEXT/INT | 下次复习时间 |
| source_srt_time | TEXT | 来源字幕时间戳 |
| updated_at | TEXT | 更新时间 |

SM2 常量：`DEFAULT_EFACTOR=2.5, MIN_EFACTOR=1.3, MAX_EFACTOR=5.0`。

### 6.2 迁移纪律（国企级硬性规定）
- **只增不改（ADD COLUMN only）**：`VOCABULARY_MIGRATIONS` 只允许 `ALTER TABLE ... ADD COLUMN`，**严禁 DROP / RENAME 列**，保证老用户库向前兼容。
- 迁移在 `database/manager.py` 初始化时按序执行，幂等。
- 词库备份/恢复走 `/vocabulary/backup.json`，不依赖 SQL dump。

---

## 7. Electron 多窗口、热键与兼容模式

### 7.1 窗口结构
- **主窗口**：课程列表 / 配置 / 复习页（React SPA）。
- **浮窗（overlay）**：桌面字幕悬浮窗（`SubtitleOverlay` / `OverlayWindow`），可置顶显示实时字幕。
- **词典窗 / 查词面板**（`DictionaryWindow` / `WordPanel`）：点字幕生词弹出。

### 7.2 全局热键
- `Alt+Shift+O`：切换浮窗显隐。
- `Alt+Shift+D`：切换识别模式。

### 7.3 关键兼容开关（复刻必含）
| 机制 | 说明 |
|---|---|
| 单实例锁 | 防止多开导致端口/后端冲突 |
| 动态端口 | 启动随机空闲端口，避免占用 |
| 兼容模式 | `--no-sandbox --tingkebanlv-compat`，用于沙箱/VM/云桌面（**华为云桌面开发调试时极可能需要**） |
| GPU 沙箱默认关闭 | 避免云桌面 GPU 上下文初始化失败 |
| `TINGKEBANLV_DEBUG_PORT` | 设 CDP 调试端口（9222），用于自动化驱动/录屏取证 |
| 环境变量族 | `TINGKEBANLV_PYTHON` / `BACKEND_DIR` / `PORT` / `HOME` / `GPU_SANDBOX` / `DEBUG_PORT` / `WHISPER_MODEL` / `DATA_DIR` |

> 华为云桌面（CodeArts 云端 IDE / 云主机）多为虚拟化环境，**务必保留 `--no-sandbox --tingkebanlv-compat` 兼容分支**，否则渲染进程起不来进行录屏取证。

---

## 8. ASR 与 LLM 流水线详解

### 8.1 ASR 流水线（`asr/pipeline.py` + `whisper_engine.py` + `audio/`）
```
系统声音 ──PyAudioWPatch(loopback)──▶ AudioCapture(16kHz)
        ──silero-vad──▶ 仅在有语音段推理
        ──faster-whisper(base)──▶ 文本
        ──术语热词注入──▶ 引导拼写（课程语言 + 术语域 + 短语）
        ──repetition 抑制──▶ 去重复
        ──SQLite(subtitles)──▶ 落库
        ──HTTP poll──▶ 前端字幕
```
- `SAMPLE_RATE=16000`；术语语言 `_term_language` 与课程 `language` 分开跟踪。
- **V7 结论（已闭环）**：术语层收益由"配的词对不对"决定（模型会不会听错），啰嗦代价由"词的内容"决定；**15 个准词比 60 条无关短语更好**。产品侧已把内置无关短语层下线，改为"用户自由配术语"。

### 8.2 LLM 智能体（`llm/explainer.py` 为核心）
- `WordExplainer.explain(word, domain, course_name, context_sentence)`：
  - **本地词典命中** → 直接返回（零延迟、零成本）。
  - **未命中** → 走 `WordExplanationAgent`（LangChain `RunnableBranch` + Pydantic 结构化输出 + 记忆缓存）调 LLM 生成。
  - 返回结构：`{translation, meaning_in_context, example, related[], source}`。
- 三类 Agent 范式（作业要求）：
  - **Agent**：`create_agent()` + checkpointer 记忆（课程问答 / 总结）。
  - **RAG**：字幕语义分块（`langchain-text-splitters`）+ `InMemoryVectorStore` + `nomic-embed-text` 嵌入。
  - **Memory**：LangGraph checkpointer（替代废弃的 `RunnableWithMessageHistory`）。
- 默认本地 Ollama `qwen2.5:7b`；可切云端 OpenAI 兼容（DeepSeek/Kimi/智谱）走 `langchain-openai`。

---

## 9. 华为云码道（CodeArts）复刻工作流【参赛核心，务必执行】

> **红线提醒**：赛题 25 分考核"是否在华为云码道完成核心 AI 编码"。**AI（我）只能写文档与本地代码，无法伪造你在 CodeArts 里的真实操作记录**。以下步骤必须由团队本人在 CodeArts 内完成并截图/留存。

### 9.1 工作流总览（四阶段留痕）
| 阶段 | CodeArts 动作 | 留痕物（进 codearts_proof/） |
|---|---|---|
| ① 环境 | CodeArts IDE / 云主机装 Node20 + Python3.11 + Ollama | 环境截图、终端 `node -v`/`python -v` 输出 |
| ② 写码 | 在 CodeArts 内 clone 本仓库 → 改代码（哪怕小修） | 提交记录、差异截图、CodeArts 编辑器录屏 |
| ③ 调试 | CodeArts 内 `npm run dev` / `python main.py` 跑通 | 运行日志、本地 500ms 轮询字幕截图、CDP 录屏 |
| ④ 文档 | CodeArts Wiki / 代码托管提交说明 | Wiki 页面、PR 描述、评审记录 |

### 9.2 codearts_proof/ 必采集清单（对应 25 分）
1. `01_env_node_python.png` — CodeArts 终端 `node -v` / `python --version`。
2. `02_codearts_editor.png` — CodeArts 打开 `backend/main.py` 或 `frontend/electron/main.cjs` 编辑器界面。
3. `03_commit_log.png` — CodeArts 代码托管的提交历史（含你的账号）。
4. `04_run_dev.png` — `npm run dev` 后前端+后端联调成功界面。
5. `05_asr_poll.png` — 播放网课、前端 500ms 轮询出字幕的截图（最好用 CDP 录屏 `outputs/_flow_4_subtitle.png` 风格）。
6. `06_word_explain.png` — 点生词弹查词面板（AI 讲解）截图。
7. `07_llm_agent.png` — `/ai/ask` 或 `/courses/{id}/summary` 的 Agent 输出。
8. `08_wiki_doc.png` — CodeArts Wiki 写的架构/使用文档页面。
9. `09_build_package.png` — `npm run package:win` 产出 `听课伴侣-setup.exe` 截图（可选，证明交付规范）。
10. `README_proof.md` — 把以上 9 项编号对应到赛题"码道深度"评分点。

### 9.3 避坑（本仓库已踩过，复刻时直接绕开）
- **代理劫持**：华为云环境若有 http_proxy，验证后端必须 `build_opener(ProxyHandler({}))` 直连，否则看到的是代理响应。
- **僵尸端口**：Windows 强杀后端会留 LISTEN 但无进程的死端口，新进程 bind 失败 `[Errno 10048]`；健康检查只看端口会误判"服务还活着"。用动态端口 + 探活 `/api/ping` 规避。
- **NSIS 静默安装**：`setup.exe /S` 在本沙箱退出码 0 但啥都不装；**以"目标目录出现 听课伴侣.exe"为唯一判据**，不要信退出码。等价可用 `release/win-unpacked/听课伴侣.exe` 便携版。
- **CRLF 纪律**：`scripts/verify_install.py`、`scripts/eval_asr_quality.py`、`testset/chinese_lecture/metadata.json` 三个文件必须保持 CRLF，**不要归一化**。
- **PyInstaller 路径**：`npm run backend:build` 的 `python` 必须解析到 venv（含 PyInstaller），否则冻结失败。

---

## 10. 可选：华为云服务接入（架构贴合 +25 分加分项）

在"本地优先"架构之上， optionally 把三类能力对接华为云，提升"专业贴合度 / 技术架构"分：

| 本地能力 | 华为云替代/增强 | 接入点 |
|---|---|---|
| faster-whisper 本地 ASR | **华为云实时语音转写**（REST/WebSocket） | `asr/whisper_engine.py` 增加 `HuaweiASRBackend`，按课程配置切换本地/云 |
| Ollama 本地 LLM | **华为云盘古大模型 / ModelArts** 部署 qwen 或盘古 | `llm/client.py` 增加 `HuaweiCloudLLM`（OpenAI 兼容网关） |
| 本地 SQLite | 可选 **华为云 GaussDB / RDS** 做多端同步 | `database/manager.py` 抽象存储层 |
| 桌面分发 | **华为云部署演示地址**（ECS + 公网） | 提交 `readme` 内附 demo 链接 |

> 评分映射：**功能完备度 30**（本软件功能完整）/ **码道深度 25**（§9 工作流）/ **专业贴合 25**（学业辅助 + §10 华为云增强）/ **技术架构 15**（Electron+FastAPI+LangChain+SQLite 分层清晰）/ **交付规范 5**（§11 五文件夹）。

---

## 11. 五文件夹提交包结构（赛题要求）

```
提交包.zip/
├─ ppt/            # 答辩 PPT（正式风、宋体、正文≥14pt）：架构图、痛点、Agent 范式、码道留痕截图
├─ project/        # 项目源码（本仓库快照，含 backend/ frontend/ packaging/）
├─ video/          # 演示视频（≤3 分钟精华）：CDP 录屏"播网课→出字幕→点词讲解→复习"
├─ codearts_proof/ # §9.2 的 10 项留痕
└─ readme/         # 安装运行说明 + 华为云增强说明 + 评分点映射表
```

> 演示视频建议用 CDP 真点击录屏（见本仓库 `安装空间/_cdp_flow.py` 思路）：课程列表→配置→开始识别→播真实音频→出字幕→点生词弹查词面板→导出面板时间戳正确。

---

## 12. 复刻验收清单（门禁）

- [ ] `npm install` + `pip install -r requirements.txt` 均成功。
- [ ] `npm run dev` 能起前端 + 后端，`/api/ping` 返回 ok。
- [ ] 播放一段英文网课，前端 500ms 轮询出字幕（WER≈0.1130 基线，需 `--realtime`）。
- [ ] 点字幕生词 → 弹查词面板（本地词典或 LLM 讲解）。
- [ ] 收藏单词 → `/vocabulary` 可见 + SM2 字段正确。
- [ ] `npm run package:win` 产出 `听课伴侣-3.7.0-setup.exe`（或便携 `win-unpacked`）。
- [ ] pytest 后端 **512 passed / 1 skipped**；前端 **205 pass / 0 fail**。
- [ ] CodeArts 内完成 §9 四阶段，codearts_proof/ 10 项齐全。

---

## 13. 给团队的架构师建议（落地顺序）

1. **先本地跑通**（§3–§8），确认功能与门禁（§12），这是"功能完备度 30 分"地基。
2. **再迁 CodeArts**（§9）：把仓库推到 CodeArts 代码托管，团队本人在里面做"环境→写码→调试→文档"全流程并截图。这是"码道深度 25 分"的命门，**不可代劳**。
3. **最后做华为云增强**（§10）与五文件夹打包（§11），冲"专业贴合 25 + 技术架构 15 + 交付规范 5"。
4. 演示视频用 CDP 真点击录屏，避免"窗口不可见渲染冻结"导致录屏黑屏（本仓库已验证根治开关：`--disable-renderer-backgrounding --disable-background-timer-throttling --disable-backgrounding-occluded-windows --disable-features=CalculateNativeWinOcclusion`）。

---

*本手册由架构师基于仓库 `v3.7.2`（tag）现状整理，覆盖环境、依赖、目录、API、DB、ASR/LLM、Electron、CodeArts 工作流与华为云增强。团队复刻时如遇版本漂移，以 `frontend/package.json` 与 `backend/requirements.txt` 锁定版本为准。*
