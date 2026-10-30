# 📚 第七章《智能应用开发》作业提交说明

> 本文件是本次作业的**提交入口**，指向仓库中与作业相关的全部材料。

---

## 一、作业要求对照

| 作业要求 | 本项目对应内容 | 位置 |
|---|---|---|
| **基于 LangChain 框架** | 四条 LLM 智能链用 LangChain 1.x 实现（LCEL 链 / 结构化输出 / RunnableBranch），答疑另用 `create_agent()` 做自主工具调用 | `backend/llm/chains/` |
| **在线大模型或离线大模型** | 离线 Ollama + Qwen2.5（默认 `qwen2.5:7b`），可一键切换云端 OpenAI 兼容 API | `backend/llm/chains/base.py` |
| **开发一款智能应用** | 听课伴侣 —— 实时字幕 + 交互式单词学习桌面应用 | 全仓库 |
| **上传 README 文档说明** | 完整项目说明（含「🧠 智能体架构」专章） | [`README.md`](README.md) |
| **开源仓库链接** | https://github.com/lizerankmpop-dev/tingke-banlv/tree/feat/langchain-agent | — |

---

## 二、提交分支

**分支：[`feat/langchain-agent`](https://github.com/lizerankmpop-dev/tingke-banlv/tree/feat/langchain-agent)**

- commit：`53f8c09a50` — *feat(llm): 用 LangChain 框架重构四条 LLM 智能链*
- 相对 `main` 仅新增 **1 个提交**，主分支不受影响，可随时对比与回滚
- ⚠️ **请务必从上述分支链接进入**：`main` 分支是改造前的版本

---

## 三、LangChain 落地一览

```
┌── 术语提取链 ────────────────────────────────────────────┐
│  字幕文本 → ChatPromptTemplate → 模型(结构化输出)          │
│                                   → RunnableLambda 回填上下文句 │
└─────────────────────────────────────────────────────────┘

┌── 单词讲解链（RunnableBranch 分级） ────────────────────┐
│                    ┌─ 词表命中 → 本地词库（零延迟零成本）    │
│  点击的单词 ───────┤                                       │
│                    └─ 未命中 → 提示词 → 模型 → 结构化讲解   │
└─────────────────────────────────────────────────────────┘

┌── 课后摘要链 ──────────────────────────────────────────┐
│  字幕全文 + 生词表 → 提示词 → 模型 → 摘要/知识点/生词      │
└─────────────────────────────────────────────────────────┘

┌── 对话答疑 Agent ───────────────────────────────────────┐
│  问题 + 最近字幕 ─→ create_agent() + 三个工具             │
│         ↓ 模型自主决定是否调用                            │
│   lookup_term / match_course_terms / search_subtitle_ctx │
│         ↓ 拿到工具结果                                    │
│   模型生成最终答案                                        │
└─────────────────────────────────────────────────────────┘
```

### 代码位置

```
backend/llm/chains/
├── base.py       模型提供方抽象（ChatOllama / ChatOpenAI）+ 统一降级 safe_invoke
├── prompts.py    ChatPromptTemplate 提示词模板（构图期校验变量）
├── schemas.py    Pydantic 结构化输出模型（容错 validator）
├── agents.py      四条业务链的 LCEL 编排
├── agent_tools.py 答疑 Agent 的四个工具 + create_agent 组装
├── rag.py         字幕向量索引与语义检索（RAG）
├── memory.py      会话持久化（checkpointer，Memory）
└── verify.py      离线自检脚本（30 项检查，无需大模型）
```

原有 `client.py` / `extractor.py` / `explainer.py` / `summarizer.py` / `qa.py`
改为薄封装，**对外接口与返回结构逐字未变**，因此 `api/routes.py` 与前端零改动。

---

## 三·补、LangChain 的两类范式都用了

| 范式 | 本项目对应 | 说明 |
|---|---|---|
| **Chain（LCEL 编排）** | 术语提取 / 单词讲解 / 课后摘要 | 调用路径由开发者预先确定，模型只在既定步骤内填充内容 |
| **Agent（自主决策）** | 对话答疑 | 模型运行时自主决定调用哪个工具、调几次 |
| **RAG（检索增强）** | 字幕语义检索 | 字幕→语义分块→向量索引→按语义召回，突破固定窗口限制 |
| **Memory（会话记忆）** | 多轮对话 | checkpointer 按 thread_id 隔离，历史逐轮累积 |

Agent 的三个工具：

| 工具 | 作用 | 底层 |
|---|---|---|
| `lookup_term` | 查课程术语库中该术语的**本课语境**释义 | `dictionary.terminology.lookup_vocab` |
| `match_course_terms` | 在字幕中匹配本课专业术语 | `dictionary.terminology.match_terms_in_text` |
| `search_subtitle_context` | 从字幕里定位含关键词的原句 | 字符串检索 |

工具用 `@tool` 装饰，**docstring 即给模型看的接口文档** ——
写明「何时该用」与「返回什么」，模型据此决定调用与调用时机。

Agent 与链并存（`ChatQA(use_agent=...)`）：Agent 适合开放问题，
链适合上下文充分的问题（延迟更低、token 更省）。
Agent 异常时自动回退链模式，答疑功能不中断。

### RAG（字幕检索）

| 环节 | 组件 |
|---|---|
| 切分 | `RecursiveCharacterTextSplitter` + 自定义字幕分块（保留时间戳与重叠） |
| 向量化 | `Embeddings`（本地 Ollama `nomic-embed-text`，约 274 MB，离线可用） |
| 存储 | `InMemoryVectorStore`（纯 numpy，零额外依赖；可换 FAISS，接口一致） |
| 召回 | `similarity_search` + 工具封装（由 Agent 自主决定何时检索） |

字幕是带时间戳的时序知识库，检索结果直接给出「第 12:35 讲的」。

### Memory（多轮对话）

用 LangChain 1.x 推荐的 **checkpointer** 方案，而非已废弃的
`RunnableWithMessageHistory`（LangChain 1.6.4 起标记废弃，官方指向 LangGraph）。

`create_agent()` 原生接受 `checkpointer` 参数，无需额外包装 Runnable。
实现抽出 `CheckpointStore` 抽象，换 Redis/SQLite 只需实现同一接口。
记忆按 `thread_id` 隔离（建议「课程ID:学生ID」），换课程不串味。

---

## 四、一键验证

```bash
# 1. 克隆（注意分支）
git clone -b feat/langchain-agent https://github.com/lizerankmpop-dev/tingke-banlv.git
cd tingke-banlv

# 2. 安装后端依赖
cd backend
python -m venv venv
./venv/Scripts/python.exe -m pip install -r requirements.txt

# 3. 离线自检（30 项，无需大模型，秒级完成）
./venv/Scripts/python.exe -m llm.chains.verify

# 4. 四条链编排演示（离线模式）
./venv/Scripts/python.exe ../examples/langchain_demo.py --offline

# 5. 真实模型演示（需先启动 Ollama）
ollama pull qwen2.5:7b
./venv/Scripts/python.exe ../examples/langchain_demo.py --live
```

---

## 五、验证结果

| 项目 | 结果 |
|---|---|
| 全量测试套件 | **478 passed** / 9 skipped（改造前 447 passed，无回归） |
| LangChain 链离线自检 | **30 / 30** 通过 |
| LangChain 专项回归守卫 | **31 / 31** 通过（Agent / RAG / Memory） |
| 链编排演示 | 四条链全部正确输出 |

---

## 六、迁移中解决的两个真实问题

**① 功能性回归**
迁移时曾把 LLM 可用性检查提到链调用**之前**，导致无 LLM 环境下「词表命中」被
错误短路成 `None`。原语义是「先查词表，命中立即返回；未命中才检查 LLM」。
→ 可用性判定已下沉到 AI 分支内部，`RunnableBranch` 的本地分支完全不受 LLM 状态影响。
→ 守卫：`tests/test_langchain_chains.py::test_local_vocab_path_survives_without_llm`

**② 类型泄漏**
LangChain 1.x 的 `StrOutputParser` 返回 `TextAccessor`（`str` 的子类），
会渗进 API 响应体，影响 JSON 序列化一致性。→ 链尾已显式转为精确的 `str`。
→ 守卫：`tests/test_langchain_chains.py::test_qa_chain_returns_plain_str`

---

## 七、许可协议

MIT（见 [`LICENSE`](LICENSE)）。依赖均为宽松型许可证，无兼容性风险。
