# 听课伴侣 — 实时字幕 · 交互学词

![CI](https://github.com/lizerankmpop-dev/tingke-banlv/actions/workflows/ci.yml/badge.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

实时字幕工具，支持**在线网课**和**线下课堂**两种模式。捕获系统音频或麦克风收音，生成逐词同步字幕，点击单词即可学习。完全免费，本地运行。

## 核心功能

- 🎤 **实时语音识别** — 基于 Faster-Whisper，本地运行，无需联网
- 🎯 **术语热词注入** — 领域术语表注入 ASR，专业词识别准确率显著提升
- 🏫 **双模式支持** — 在线网课（系统音频捕获）/ 线下课堂（麦克风收音）
- 📝 **逐词高亮字幕** — 精确到单词级别的时间戳同步，中英混合支持
- 🖱️ **点击查词学习** — 点击字幕任意单词，即时显示释义（词库优先，AI 兜底）
- 🤖 **AI 术语提取** — Ollama 自动分析课程内容，提取专业词汇
- 💡 **AI 深度讲解** — 结合课程语境解释"这个词在这里什么意思"
- 📋 **课后摘要** — 一键生成课程摘要 / 知识点 / 生词复习清单
- 💬 **对话式答疑** — 基于最近字幕上下文提问，"刚才那段没听懂"场景
- 📚 **课程专属词典** — 每个课程独立管理词汇，支持生词本和搜索

## 术语识别优化

国外课程最大的障碍是专业术语听不懂、查不到。本项目对此做了针对性优化：

**问题**：Whisper 对专业词识别很差 —— `convolution` 被听成 `convalution`，
`eigenvalue` 被切成 `eigen value`，学生看到的字幕本身就是错的。

**方案**：三层术语热词注入

| 层级 | 来源 | 说明 |
|---|---|---|
| 1. 自定义术语 | 用户手填 | 课程核心词，识别优先级最高 |
| 2. 课程已识别术语 | 运行中自动累积 | 本课实际出现的词，动态加入 |
| 3. 领域术语表 | 内置 15 个领域 / 320+ 术语 | 覆盖计算机、医学、物理、经济等 |

热词通过 Whisper 的 `initial_prompt` 注入，并随识别进程动态更新（无需重启监听）。

**配套能力**：

- **本地词表匹配**：内置 3300+ 词，字幕里的术语零延迟匹配入库，完全不依赖 LLM
- **点击讲解分级**：词表命中直接返回（~0ms），未命中的词才调用 AI 讲解，省时省钱
- **术语库可视化**：课程配置页实时预览"这节课会重点识别哪些词"、词库覆盖率

## 两种使用场景

| | 💻 在线网课 | 🏫 线下课堂 |
|---|---|---|
| 音频来源 | 系统音频（WASAPI loopback） | 麦克风 |
| 适用场景 | Coursera / B站 / YouTube | 教室 / 讲座 / 会议室 |
| 识别语言 | 英文为主 | 中英混合 |
| 额外需求 | 无（Windows 下由 PyAudioWPatch 直接采集） | 降噪麦克风（建议） |

> Windows 的 WASAPI 回环采集由 `PyAudioWPatch` 提供，**不需要**安装 VB-Cable
> 之类的虚拟声卡，也不需要在系统声音设置里把默认播放设备改掉。

## 技术栈

| 层级 | 技术 |
|------|------|
| 桌面框架 | Electron + React + TypeScript |
| 状态管理 | Zustand |
| ASR 引擎 | Faster-Whisper（默认 `base`，可配置） |
| 语音检测 | Silero VAD（ONNX 后端） |
| 音频捕获 | PyAudioWPatch（WASAPI Loopback，Windows）/ PyAudio（麦克风） |
| 术语提取 | Ollama + Qwen2.5（可切换云端 API） |
| 术语热词 | 内置领域术语库 + 自定义术语 → Whisper initial_prompt |
| 本地词典 | ECDICT + 内置词表（3300+ 词） |
| 数据存储 | SQLite |
| 后端 | Python (FastAPI + WebSocket/HTTP 轮询) |

### 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `TINGKEBANLV_LLM_PROVIDER` | `ollama` | LLM 提供方：`ollama` / `cloud` |
| `TINGKEBANLV_OLLAMA_MODEL` | `qwen2.5:7b` | 本地模型名 |
| `TINGKEBANLV_CLOUD_API_BASE` | — | 云端 API 地址，如 `https://api.deepseek.com/v1` |
| `TINGKEBANLV_CLOUD_API_KEY` | — | 云端 API 密钥 |
| `TINGKEBANLV_CLOUD_MODEL` | — | 云端模型名，如 `deepseek-chat` |
| `TINGKEBANLV_TERM_PROMPT_MAX` | `800` | 术语热词最大字符数 |
| `WHISPER_DEVICE` | 自动检测 | `cuda` / `cpu` |
| `TINGKEBANLV_WHISPER_MODEL` | `base` | 模型规格：`tiny`/`base`/`small`/`medium`/`large-v3` |
| `TINGKEBANLV_WHISPER_THREADS` | 自动（≤8） | ctranslate2 的 CPU 线程数 |
| `TINGKEBANLV_VAD_SILENCE` | `0.8` | 静音多久后断句（秒），越大片段越长 |
| `TINGKEBANLV_MAX_PENDING_JOBS` | `8` | 待识别队列上限（背压兜底） |
| `TINGKEBANLV_DISABLE_VAD_MODEL` | — | 设 `1` 强制使用能量检测（排障用） |
| `HF_ENDPOINT` | `https://hf-mirror.com` | 模型下载镜像 |
| `TINGKEBANLV_HOME` | `~/.tingkebanlv` | 数据与模型存储目录（日志落盘于此） |
| `WHISPER_MODEL_DIR` | `<TINGKEBANLV_HOME>/whisper_models` | 模型缓存目录 |
| `TINGKEBANLV_GPU_SANDBOX` | — | 设为 `1` 恢复 Chromium GPU 沙箱默认行为（排障用） |
| `TINGKEBANLV_PYTHON` | 自动探测 | 指定后端 Python 解释器（打包排障用） |

### 实时性：为什么默认是 base 而不是 small

实测（`scripts/bench_matrix.py`，32 核 CPU，`int8`，8 秒片段）：

| 模型 | 线程 | 2s 片段 | 4s 片段 | 8s 片段 | 15s 片段 |
|---|---|---|---|---|---|
| tiny | 4 | 0.551 | 0.251 | 0.138 | 0.077 |
| base | 4 | 0.908 | 0.482 | 0.249 | 0.164 |
| small | 4 | **2.814** | **1.438** | 0.738 | 0.428 |
| small | 8 | **2.126** | **1.115** | 0.584 | 0.332 |

表内为 RTF（识别耗时 / 音频时长），**必须 < 1**，否则字幕会持续滞后。

关键规律是：**每次调用都有约 4 秒固定开销**，与音频长短几乎无关
（small/4 线程下 2 秒片段耗时 5.63s，而 15 秒片段只耗时 6.42s；Whisper 按 30 秒窗口处理、
短输入会被补齐）。所以片段切得越碎越亏，2 秒片段直接不可用。

据此确定的默认值：`base` 模型 + 自动线程数 + 静音阈值放宽到 0.8s，
让片段自然落在 4~12 秒。若机器较强想换回 `small`，设
`TINGKEBANLV_WHISPER_MODEL=small`，但需注意 4 秒片段上它会跟不上。

运行时可通过 `GET /api/asr/poll` 返回的 `stats` 观察是否降级：
`backlog` 持续 >0 说明跟不上，`mergedCalls` >0 说明已在合并片段，
`droppedSegments` >0 说明开始丢内容。

切换云端 LLM 示例：

```bash
set TINGKEBANLV_LLM_PROVIDER=cloud
set TINGKEBANLV_CLOUD_API_BASE=https://api.deepseek.com/v1
set TINGKEBANLV_CLOUD_API_KEY=sk-xxx
set TINGKEBANLV_CLOUD_MODEL=deepseek-chat
```

## 快速开始

### 前置要求

- Python 3.10+
- Node.js 18+
- Ollama（可选，用于 AI 术语提取）

### 安装

```bash
# 一键安装
bash scripts/setup.sh

# 或手动安装
cd backend && pip install -r requirements.txt
cd frontend && npm install
```

### 安装 Ollama（可选但推荐）

```bash
# macOS / Linux
curl -fsSL https://ollama.com/install.sh | sh

# Windows
# 从 https://ollama.com/download 下载安装

# 拉取推荐模型
ollama pull qwen2.5:7b
```

### 启动开发环境

```bash
npm run dev
```

### 测试

```bash
cd backend && PYTHONPATH=. python -m pytest tests/ -v
```

其中 `tests/test_asr_pipeline_e2e.py` 覆盖音频捕获、VAD、消息队列、术语入库、
背压与完整链路（文件音频 → VAD → Whisper → 字幕）。

端到端用例需要 faster-whisper 模型，未指定时自动跳过：

```bash
TINGKEBANLV_TEST_MODEL_DIR=<含 model.bin 的模型目录> \
  PYTHONPATH=. python -m pytest tests/test_asr_pipeline_e2e.py -v -k end_to_end
```

### 实时性基准

改动 ASR 参数（模型规格、线程数、断句阈值）后，建议用基准脚本确认仍能跟上实时：

```bash
cd scripts
# 单配置：整段 + 分段串行两种口径的 RTF
python bench_asr.py --audio <16k单声道wav> --model base --repeat 6 --segment 8

# 参数矩阵：模型 × 线程 × 片段长度
python bench_matrix.py --audio <wav> --models tiny=/path,tiny,/path --threads 4,8,16

# 单次调用成本曲线（用于判断片段该切多长）
python bench_call_cost.py <wav> /path/to/model_dir 8 5
```

判据是 RTF < 1；同时看「单段平均耗时」，它决定字幕出字延迟。

## 打包分发

后端与前端产物由 Electron 主进程一并打包：后端源码放入 `resources/backend`，
前端构建产物放入 `resources/dist`，运行时由 FastAPI 同源托管 `dist`，
因此前端 `fetch('/api/...')` 无需代理即可工作。

```bash
cd frontend

# 只出解压目录（快，便于自测）
npx electron-builder --win --dir

# 出 NSIS 安装包
npx electron-builder --win
```

产物位于 `frontend/release/`：`win-unpacked/`（免安装）与 `tingkebanlv-<版本>-setup.exe`。

### 打包环境两个常见阻塞点

**1. `Cannot create symbolic link`（winCodeSign 解压失败）**

electron-builder 会下载 `winCodeSign` 工具包，其中含两个 macOS 动态库的符号链接。
Windows 下创建符号链接需要管理员权限或已开启「开发者模式」，否则解压报错中断构建。

三选一：

- 开启开发者模式：设置 → 隐私和安全性 → 开发者选项 → 开发人员模式
- 以管理员身份运行构建命令
- 无需管理员权限的做法 —— 手动预置缓存（只丢弃那两个 macOS 符号链接，Windows 用不到）：

  ```bash
  CACHE="$LOCALAPPDATA/electron-builder/Cache/winCodeSign"
  Z7="frontend/node_modules/7zip-bin/win/x64/7za.exe"
  "$Z7" x "$CACHE/$(ls $CACHE | grep '\.7z$' | head -1)" \
        "-o$CACHE/winCodeSign-2.6.0" -snl -y
  ```

  解压后确认 `rcedit-x64.exe` 与 `windows-10/x64/signtool.exe` 存在即可。

**2. GitHub Release 下载超时 / 502**

`nsis`、`nsis-resources`、`winCodeSign` 均从 GitHub Releases 下载，
国内网络常不可达。走镜像即可（electron-builder 原生支持）：

```bash
export ELECTRON_BUILDER_BINARIES_MIRROR="https://gh-proxy.com/https://github.com/electron-userland/electron-builder-binaries/releases/download/"
```

### 运行时环境适配（无需配置，自动生效）

打包后的应用会在启动时做两件事，避免在受限环境下白屏或闪退：

- 关闭 Chromium **GPU 沙箱**。部分虚拟机 / 远程桌面 / 企业安全策略环境下，
  GPU 进程会以 `0xC0000005` 崩溃并抛出 `FATAL: GPU process isn't usable`，
  导致应用在界面出现前直接退出。
- **首屏看门狗**。同类环境下渲染器沙箱也可能失效，表现为后端与静态资源都正常，
  但渲染器进程卡死、页面永久白屏且无任何报错。若界面 12 秒内未完成加载，
  应用会自行以 `--no-sandbox` 重启一次（约 20 秒内呈现界面），并记录到日志。

运行日志（含后端输出）位于 `<TINGKEBANLV_HOME>/logs/electron.log`，
打包后没有 stdout，排障请直接看这个文件。

## 项目结构

```
tingke-banlv/
├── frontend/              # Electron + React 前端
│   ├── src/
│   │   ├── components/    # UI 组件
│   │   │   └── WordPanel.tsx        # 单词查询面板
│   │   ├── pages/
│   │   │   ├── CourseList.tsx       # 课程列表（含类型选择）
│   │   │   ├── CourseConfig.tsx     # 课程配置
│   │   │   ├── SubtitleOverlay.tsx  # 悬浮字幕
│   │   │   └── DictionaryWindow.tsx # 课程词典
│   │   ├── stores/        # Zustand 状态管理
│   │   └── types/         # TypeScript 类型定义
│   └── electron/          # Electron 主进程
├── backend/               # Python 后端
│   ├── api/               # FastAPI 路由 + WebSocket
│   ├── asr/               # ASR 管道 + Whisper 引擎（含术语热词注入）
│   ├── audio/             # 音频捕获（Loopback + 麦克风）+ VAD
│   ├── llm/               # LLM 层：统一客户端 + 提词/讲解/摘要/答疑
│   ├── dictionary/        # 本地词典 + 术语库（terminology.py）+ 内置词表
│   ├── database/          # SQLite 数据管理
│   └── tests/             # 集成测试
└── scripts/               # 工具脚本
```

## 使用流程

1. 创建课程 → 选择课程类型（在线网课 / 线下课堂），填写名称、领域、术语
2. 进入课程配置 → 调整领域和自定义术语
3. 点击「开始识别」→ 窗口缩小为悬浮字幕条，开始实时识别
4. 点击字幕中的任意单词 → 查看释义
5. AI 自动提取专业术语 → 积累课程专属词典

## License

MIT
