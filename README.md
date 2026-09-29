# 听课伴侣 — 实时字幕 · 交互学词

实时字幕工具，支持**在线网课**和**线下课堂**两种模式。捕获系统音频或麦克风收音，生成逐词同步字幕，点击单词即可学习。完全免费，本地运行。

## 核心功能

- 🎤 **实时语音识别** — 基于 Faster-Whisper，本地运行，无需联网
- 🏫 **双模式支持** — 在线网课（系统音频捕获）/ 线下课堂（麦克风收音）
- 📝 **逐词高亮字幕** — 精确到单词级别的时间戳同步，中英混合支持
- 🖱️ **点击查词学习** — 点击字幕任意单词，即时显示释义
- 🤖 **AI 术语提取** — Ollama 自动分析课程内容，提取专业词汇
- 📚 **课程专属词典** — 每个课程独立管理词汇，支持生词本和搜索

## 两种使用场景

| | 💻 在线网课 | 🏫 线下课堂 |
|---|---|---|
| 音频来源 | 系统音频（WASAPI loopback） | 麦克风 |
| 适用场景 | Coursera / B站 / YouTube | 教室 / 讲座 / 会议室 |
| 识别语言 | 英文为主 | 中英混合 |
| 额外需求 | 虚拟音频设备 | 降噪麦克风（建议） |

## 技术栈

| 层级 | 技术 |
|------|------|
| 桌面框架 | Electron + React + TypeScript |
| 状态管理 | Zustand |
| ASR 引擎 | Faster-Whisper (small) |
| 语音检测 | Silero VAD |
| 音频捕获 | PyAudio（Loopback + Microphone） |
| 术语提取 | Ollama + Qwen2.5 |
| 本地词典 | ECDICT |
| 数据存储 | SQLite |
| 后端 | Python (FastAPI + WebSocket) |

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
│   ├── asr/               # ASR 管道 + Whisper 引擎
│   ├── audio/             # 音频捕获（Loopback + 麦克风）+ VAD
│   ├── llm/               # Ollama 术语提取
│   ├── dictionary/        # 本地词典 (ECDICT)
│   └── database/          # SQLite 数据管理
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
