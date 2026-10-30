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
