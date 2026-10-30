# V7 · 网页用户测试：发现与修复（2026-10-30）

> **背景**：以真实用户身份用「B 站 MIT 6.034 课堂实拍 + 网页版听课伴侣」跑完整流程。
> **性质**：本文件记录**用户视角实测**发现的问题与对应修法，
> 以便日后回看时知道「为什么这么改」（其中部分改动随 10-30 的其他提交一并入库）。

---

## 一、测试环境与结论

| 项 | 结果 |
|---|---|
| 视频来源 | B 站 Edge 窗口真在播 MIT 6.034（课堂实拍 + 中英字幕）|
| 音频捕获 | `音频流已打开: 48000Hz/2ch`（扬声器 Loopback，无 host error）|
| 实时识别 | 持续产出（`Processing audio` 连续）|
| 字幕内容 | 与视频对得上（`and how do you get rid of stuff? Well, by calling get rid of.`）|
| **跨虚拟桌面** | 🔑 切到桌面 2、再切回桌面 1，**识别全程未断** ⇒ 视频在哪个桌面都不影响捕获 |
| 点词查释义 | `perception`→知觉 / `representation`→表示 / `constraint`→约束 / `queue`→队列 全部有中文 |
| 全流程自测 | 14 步中 13 步通过（`安装空间/_user_flow_test.py`，可重跑）|

---

## 二、🔴 修了什么

### 1. PortAudio 断言崩溃 —— **同款连踩三次**，第三次才找到全貌

症状：后端进程**直接消失**，日志只留一行，Python 层拿不到任何异常：

```
Assertion failed: hostApi->info.defaultOutputDevice < hostApi->info.deviceCount,
file src/common/pa_front.c, line 233
```

根因：PortAudio 的 `hostApi->info.defaultOutputDevice` 在**并发访问**时会被读成非法值。
凡是碰设备枚举的地方（`PyAudio()` 构造 / `get_device_count()` /
`get_device_info_by_index()` / `get_default_wasapi_loopback()`），两个线程同时做就可能触发。

**三次的并发点各不相同** ⇒ 锁必须覆盖**所有**接触点：

| # | 时间 | 并发点 | 漏在哪 |
|---|---|---|---|
| 1 | 10-29 13:28 | 两个 ASR 管道同时启动 ⇒ 两个采集线程同时构造 PyAudio | 完全没有锁 |
| 2 | 10-29 14:46 | 同上 | 只锁了 `_default_loopback_index`，**漏了构造** |
| 3 | 10-30 13:03 | **设备检测接口** `/api/audio/check-loopback`（事件循环线程）**与**采集线程并发 | 锁了采集侧，**漏了检测侧** |

**最终修法**（`backend/audio/capture.py` + `backend/api/routes.py`）：

1. 模块级 **`_DEVICE_ENUM_LOCK`（`threading.RLock`）**
   —— ⚠️ **必须是 RLock**：`_capture_loop` 持锁开流时，内部调的
   `_default_loopback_index` 会**再拿同一把锁** ⇒ 用 `Lock` 会**自死锁**
   （症状很有迷惑性：日志只有「音频捕获已启动」，既没有「使用音频设备」
   也没有「音频流已打开」，然后永远不出数据）
2. **「构造 PyAudio + 找设备 + 开流」整段进锁**（不是只锁其中一步）
3. **`list_devices()` 也进同一把锁** —— 它被检测接口在**事件循环线程**里调用
4. **检测接口改 `asyncio.to_thread`** —— 设备枚举要几百毫秒，不能阻塞事件循环
5. **重试路径（重建流）同样进锁**
6. 新增防回归测试 `backend/tests/test_audio_portaudio_safety.py`（3 条）：
   锁可重入 / 枚举不死锁 / **并发枚举+采集不崩**

🔑 **教训**：并发问题要按「**谁碰了共享资源**」全量列一遍，
而不是「改到哪儿算哪儿」—— 我前两次都是**修了自己当时看到的那一处**。

### 2. 🔴 生词本被噪音淹没（**最影响产品价值的一个**）

实测：一节课下来生词本 **187 条，其中自动入库 173 条**，内容是：

```
partly · subsequently · relationship · sufficient · interval · suppose
study · procedure · ultimately · theory · knowledge · technique · interest
```

而本课真正的 12 个术语**只有 3 条**在里面 ⇒ 用户的复习队列被淹没，
「生词本」失去意义。

根因：`match_terms_in_text` 匹配的是**通用词表**（3804 条，含 study/theory/knowledge）
⇒ **模型本来就会的普通词**也被当成「术语」塞进生词本。

**修法**（`backend/asr/pipeline.py` + `backend/dictionary/terminology.py`）：
只有「**值得学**」的词才入库 ——
- ① 用户自己配的 `custom_terms` 命中 ⇒ 用户明确关注 ⇒ 入库
- ② **多词短语** ⇒ 专业名词的典型形态 ⇒ 入库
  —— 但**排除口语填充短语**（新增 `FILLER_PHRASES` 黑名单：
  `first of all` / `note that` / `in terms of` / `by virtue of` / `so far` / `pretty much` …）
- ③ 单个普通词 ⇒ 模型已经会 ⇒ **不入库**（留给用户手动采集）

依据仍是 V7 那条结论：**术语的价值取决于「模型会不会听错这个词」**，
与它专不专业无关 ⇒ 模型已经会的词配进去**零收益还招致啰嗦**。

**实测效果**：生词本 **187 条 → 22 条，且剩下的全是真术语**：

```
conclusion · problem solving · artificial intelligence · ground truth
mechanical engineering · prior · enqueued list · conditional probability
goal tree · branch and bound · heuristic · game tree · beam search
backtracking · constraint · minimax · breadth-first · hill climbing
depth-first · admissible · probability
```

### 3. 🔴 「后端不可达」被误报成「未检测到音频 Loopback 设备」

前端 `fetch('/api/audio/check-loopback')` 失败时一律设成 `missing`
⇒ 页面显示「未检测到音频 Loopback 设备」⇒ **把人引去装虚拟声卡**，
而真实原因只是「后端服务没运行」。两者修的方向完全不同。

**修法**（`frontend/src/pages/CourseConfig.tsx`）：拆成两种状态 ——
「后端服务未运行」（附重新检测）/「确实没有设备」（附安装指引），
且后端不可达时**不再锁死「开始识别」按钮**。

---

## 三、⚠️ 记录在案但未修

- **前端没有「导出字幕」入口**：后端 `GET /api/courses/{id}/export-srt` 一直存在且正常
  （返回 `{"srt": "...", "count": N}`），但**前端从头到尾没有调用它**
  ⇒ 这是「有后端、没入口」的半成品功能。
- **`llm` 是模块级硬依赖**：`langchain_core` 一旦缺失，**整个后端起不来**
  （哪怕用户只想用字幕功能）。2026-10-30 实际发生：venv 里 `langchain` 整个消失
  ⇒ 守护脚本反复重启 13 次全失败。已按 `backend/requirements.txt` 装回
  （`langchain-core 1.7.3` / `langchain 1.4.7` / `langchain-ollama` / `langchain-openai`）。
  ⇒ **待办**：把 LLM 相关 import 改成**软依赖**（缺了就降级，不影响字幕主链路）。
  ⚠️ 注意这个 venv **也是打包环境**，缺依赖会导致下次打包失败。

---

## 四、⚠️ 本轮我自己的失误（记下来）

- **归一化工具波及了用户正在改的文件**：`scripts/normalize_eol.py --root backend`
  一次改了 **24 个文件**，其中包含 `backend/llm/chains/*` 全套 ——
  而那些是**用户自己在改的**（日志里能看到 `WatchFiles detected changes in 'llm\chains\rag.py'`）。
  ⚠️ 幸好 `git diff --ignore-cr-at-eol` 显示**内容都没变**（纯 EOL）⇒ 恢复无损。
  ⇒ **以后只对我改过的文件跑归一化，不要整目录跑。**
- **用户的 `git add -A` 把我的改动裹进了他的作业提交**（`c6f05aa`
  「docs: 补人工在环章节…」）⇒ 改动没丢，但 commit message 里没有它们。
  ⇒ **不动用户的提交历史**，改为补这份独立文档说明改动意图。

---

## 五、后续（同日第二轮）：LLM 软依赖 + 跨进程互斥（提交 `12c9330`）

### 5.1 🔴 LLM 改软依赖 —— 缺依赖不再让整个软件打不开

**原状**：`api/routes.py` 顶层硬 import + 模块级实例化
（`from llm.explainer import WordExplainer` / `word_explainer = WordExplainer()`）
⇒ `langchain_core` 一缺失 ⇒ **整个后端起不来**，而「实时字幕 + 点词」**完全不依赖 LLM**。

**修法**（**`backend/llm/` 一行不动** —— 那是别人的工作区）：
- 顶层去硬 import ⇒ 按需构造 `_get_llm(kind)`
- `_llm_call(kind, method)` 统一取用；不可用 ⇒ 抛 **503 + 可操作提示**
- `main.py` 的 `_refresh_llm_status`：import 挪进 try
  （原来在 try 外 ⇒ 异常冲出协程 ⇒ 状态探测协程直接死掉）

**实测**（用会抛 ImportError 的假 `langchain` 模块起独立实例）：
- `/api/courses` 200、`/api/audio/check-loopback` 200 ⇒ **后端照样起来**
- `/api/ai/explain` 对**词表里有的词** ⇒ **200 + `source: local_vocab`**
  ⇒ **优雅降级到本地词表**（比 503 更好用）；词表也没有的词 ⇒ 503 + 明确提示

防回归：`backend/tests/test_llm_soft_dependency.py`（3 条）

### 5.2 🔴 PortAudio **跨进程**互斥 —— 同款崩溃的第四次根治

进程内 RLock 挡不住**两个后端进程**（正式版 + dev 版同时跑）。
实测：为了验证软依赖起了第二个实例（8766）⇒ **两个后端一起崩**。

修法：Windows **命名互斥量** `Global\TingkeBanLV_PortAudioGuard`，
五处接触点全部改用（构造+找设备+开流 / `list_devices()` / 重建路径 /
`_default_loopback_index` / `_find_loopback_device`）；
拿不到锁就**等待**而不是并发硬闯；检测到 `ABANDONED`（上个进程崩过）会告警。

**实测**：8765 + 8766 **同时存活**（都 HTTP 200）⇒ 不再崩。

⚠️ 这只是「让崩溃变成等待」，**不是禁止两个实例共存** ——
真正的单实例约束（启动时检测并提示）仍是待办。

### 5.3 ⚠️ 关于「同款崩溃连踩四次」的复盘

| # | 时间 | 并发点 | 漏在哪 |
|---|---|---|---|
| 1 | 10-29 13:28 | 两个 ASR 管道同时启动 | 完全没有锁 |
| 2 | 10-29 14:46 | 同上 | 只锁 `_default_loopback_index`，**漏了 `PyAudio()` 构造** |
| 3 | 10-30 13:03 | **检测接口**在事件循环线程枚举设备 | 锁了采集侧，**漏了检测侧** |
| 4 | 10-30 13:39 | **两个后端进程** | 进程内锁挡不住**跨进程** |

🔑 **教训**：并发问题要按「**谁碰了共享资源**」**全量**列一遍（跨线程 + 跨进程），
再逐个加锁/串行化；而不是「改到哪儿算哪儿」——
我前三次都是**只修了自己当时看到的那一处**。

---

## 六、第三轮（提交 `35fcdbb` / `90bc4c4`）：导出功能真正可用

### 6.1 🔴 字幕从不写数据库 ⇒ 导出功能形同虚设

`db.add_subtitle()` 一直存在，但**没有任何地方调用它**；字幕只经 `enqueue`
推给前端（内存队列）⇒

- `GET /api/courses/{id}/subtitles` **永远空**
- `GET /api/courses/{id}/export-srt` **永远 `count=0`**
- 关掉应用字幕就没了 ⇒「课程」失去了它最该有的东西（这节课讲了什么）

修法：`_build_pipeline` 的 `on_subtitle` 回调**既推消息又入库**；
回调来自**识别线程** ⇒ `run_coroutine_threadsafe` 回投；
字段名要对齐（管道给 `start`/`end`/`isFinal`，数据库要
`start_ms`/`end_ms`/`is_final`，后两者是**必需键**）；
`interim` 不入库；入库失败只记日志。

### 6.2 🔴 导出的时间轴对不上原视频

字幕时间戳来自 **Whisper 在音频缓冲区里的相对位置** ⇒ **每次识别会话都从 0 起算**。

修法：后端 `export-srt` 支持 `offsetMs`；前端导出按钮改成**小面板**
（填「识别开始时视频播到第几秒」+ 说明）。
实测：0s→`00:00:00,000` / 90s→`00:01:30,000` / 3661s→`01:01:01,000` ✓

### 6.3 🔴 同一门课重跑 ⇒ 字幕累积、各批时间轴重叠 ⇒ SRT 错乱

修法：**session_id**
- `subtitles` 表加 `session_id` 列 + 老库自动 `ALTER TABLE` 迁移
- 每次「开始识别」生成一个 session_id，随字幕入库
- `export-srt` 默认**只导最新一次会话**（也可传 `sessionId`）

实测：新会话 6 条带 session_id / 老数据 68 条不动 / 导出 `count=6`（不是 74）✓

### 6.4 🔴 AI 不可用时显示的是硬编码的误导性文案

原来固定显示「AI 讲解服务不可用（Ollama 未启动）」⇒ 真实原因可能是
① 依赖没装 ② ollama 没装 ③ 模型没下 ④ 服务没起。

修法：后端新增 `GET /api/ai/status`（**真实状态 + 真实原因** +
`coreUnaffected: true`）；前端检查 `res.ok`（原来只看 `data.available`，
而 503 的 body 是 `{"detail": ...}` ⇒ 真实原因被整个吞掉）⇒ 现在显示真实原因。

实测：`{"available": false, "reason": "Ollama 未启动（安装后需 `ollama serve` 并拉取模型）",
"provider": "ollama", "coreUnaffected": true}`

---

## 七、⚠️ 两个**环境坑**（今天排查了很久，值得单独记）

### 7.1 🔴 `http_proxy` 环境变量会劫持 curl / urllib

本机环境有 `http_proxy=http://127.0.0.1:52608` ⇒ **所有 curl 与 urllib
请求都走代理** ⇒ 看到的是**代理转发/缓存的响应**，不是真实后端。

⇒ 排查时因此反复困惑「改了代码怎么没生效」—— 实际是**旧进程**在服务
（见 7.2），而 curl 又把真实状态掩盖了。

⇒ **验证本机后端必须绕过代理**：

```python
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
op.open("http://127.0.0.1:8765/api/courses")
```

### 7.2 🔴 Windows 强杀进程会留下**僵尸端口**

`Stop-Process -Force` 之后，socket 留在内核里等清理 ⇒
**端口仍显示 `LISTENING`，但已没有任何进程在听**：

- 新进程 bind 失败：`[Errno 10048] 端口已被占用`
- 而「**端口能否连上**」的检查**依然成功**（内核在 accept 队列里）
  ⇒ 只看端口的健康检查会**误判「服务还活着」** ⇒ 永远不重启

⇒ 两条对策（已落到 `安装空间/_start_web_test.py`）：
1. 健康检查改成**真的发 HTTP 请求**（`http_ok`），不看端口
2. 启动前**清场**（`kill_port_occupants`）⇒ 保证跑的是当前代码
   （否则「健康就跳过」⇒ 旧代码一直活着 ⇒ 改代码看不到效果）
3. 重试 3 次仍失败 ⇒ **自动换端口**（前后端一起换，Vite proxy 同步）

⚠️ 附带发现：`netstat` 输出在中文 Windows 上是 **ANSI/GBK** ⇒
`text=True`（默认 utf-8）会抛 `UnicodeDecodeError` ⇒ 要用
`.stdout.decode("mbcs", errors="replace")`。

---

## 八、第四轮（提交 `cb8571d`）：收尾三个待办

### 8.1 ✅ 导出面板可**选哪一次识别**

`export-srt` 已默认取「最新一次会话」，但用户可能想导**之前那次**
⇒ 后端 `GET /api/courses/{id}/sessions`
（返回 `[{sessionId, startedAt, count, firstText, isLatest}]`）
⇒ 前端导出面板加下拉（`时间 · N 条（最近）`），默认选最近那次。

实测：`total=1`，`193 条`，`latest=True` ✓

### 8.2 🔴 单实例检测 —— 明确告诉用户「为什么没字幕」

同一台机器跑两个后端实例（正式版 + dev 版 / 双击两次图标）⇒
两者争抢系统音频设备 ⇒ **后启动的那个识别不出字幕**，
而用户完全不知道原因（看起来就像「软件坏了」）。

修法：
- `main.py` 加**实例级命名互斥量** `Global\TingkeBanLV_Instance`
  （在 `lifespan` 最前面检测）⇒ 已有实例时记明确告警日志
- 新增 `GET /api/instance/status`
  → `{isPrimary, anotherInstanceRunning, hint, port}`
- 前端课程配置页拉这个状态 ⇒ 是次要实例就显示红色警告条 + 可操作提示

实测：

```
主实例 8765 → isPrimary=True   anotherInstanceRunning=False
第二实例 8766 → isPrimary=False  anotherInstanceRunning=True
  hint: 检测到另一个听课伴侣后端实例正在运行。两个实例会争抢系统音频设备…
日志: ⚠️ 检测到**另一个听课伴侣后端实例**已在运行…
且两个实例都还活着（HTTP 200）⇒ 跨进程互斥生效，没崩
```

### 8.3 🔴 音频流重建的重试上限 8 → 200（近乎无限）

实测 Chrome 独占设备时会**持续**占着（不是抢一下就松）⇒
8 次退避（1+2+4+8×5 ≈ 47s）用完管道就死，而用户的视频往往还在播
⇒ 「莫名其妙就没字幕了」。

⇒ 改成近乎无限重试（退避封顶 10s ⇒ 连续重试约半小时），
设备一放开就自动恢复；`stop()` 仍能立刻打断（退避期间可中断）。

---

## 九、⚠️⚠️ 这批修复**都还没进任何交付物**（发布门槛）

自 10-29 起累计改动的**进包区**文件（`backend/` 非 tests、`frontend/src/`）：

| 文件 | 改了什么 |
|---|---|
| `backend/audio/capture.py` | PortAudio 跨线程 + 跨进程互斥；流重建退避重试（上限 200）|
| `backend/api/routes.py` | 前端假成功（检查 res.ok）；409 单例；check-loopback 改 to_thread；字幕落库；会话隔离 + 导出偏移；`/ai/status`；`/sessions`|
| `backend/main.py` | LLM 软依赖（import 挪进 try）；单实例检测 + `/api/instance/status` |
| `backend/database/manager.py` | `subtitles.session_id` 列 + 老库 ALTER 迁移 |
| `backend/dictionary/terminology.py` | `FILLER_PHRASES` 口语填充短语黑名单 |
| `backend/asr/pipeline.py` | 自动入库的价值判据（只让「值得学」的进生词本）|
| `frontend/src/stores/useSubtitleStore.ts` | 检查 res.ok；409 不重连；先 stop 再 start 串行化 |
| `frontend/src/pages/CourseConfig.tsx` | 后端不可达 vs 设备缺失分开提示；次要实例警告条 |
| `frontend/src/pages/SubtitleOverlay.tsx` | AI 真实原因；导出 SRT 入口 + 偏移 + 会话选择 |

⇒ **`v3.6.0`（tag `36a61ff`）不含以上任何一条**，它的 L4/e2e 结论**不受影响**；
⇒ **下一版必须带上这批 + 重新打包 + 重跑 L4 + e2e** 才算验证过。
⇒ 建议下一版直接定为 **v3.7.0**（功能面变化明显：导出字幕、单实例提示、
   生词本降噪、会话隔离都是用户可见的）。

