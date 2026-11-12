# 听课伴侣 · 项目交接 HANDOFF（2026-11-09）

> **读者**：接手本项目的 AI agent（另一个账号）。
> **目的**：让接手方在**不读历史对话**的情况下，30 分钟内掌握全部状态、继续推进而不踩旧坑。
> **优先级**：本文 > 逐日日志 > 历史对话。若三者冲突，以本文 + 实际 `git status` 为准。
> **最后更新**：2026-11-11 12:40（11/11 实时字幕延迟修复见 §0.4；界面交付与整合决策见 §0.3；五分支合入见 §0.2；11/10 增量见 §0.1；11/09 17:15 初版由李泽然 / 原 agent 整理）
> **同内容副本**：`项目源代码/HANDOFF.md`（随仓库走，供通过 git/CodeArts 接手的 agent 阅读；两份内容一致，更新时请同步）。

---

## 0. 接手第一分钟：照这个顺序做

```bash
# 1) 进仓库，看真实状态（不要假设本文 100% 仍准确）
cd "/c/Users/Administrator/Desktop/听课伴侣/项目源代码"
git status --short && git log --oneline -6 && git branch -a

# 2) 读本文 §0.2「最新状态」—— 11/11 四分支已合入主线；§3.2 的历史「未提交改动」已闭环，勿再照做
# 3) 跑门禁确认环境没坏（约 35s + 1s）
"/c/Users/Administrator/Desktop/听课伴侣/安装空间/venv/Scripts/python.exe" -m pytest backend/tests -q
cd frontend && npm test

# 4) 读 §10「地雷清单」再动手，尤其是 proxy / 僵尸端口 / CDP 三条
```

✅ **11/10 更新：原「未提交改动」已全部闭环**（提交 `26f21e7` → 推 GitHub → 已合入 CodeArts，见 §0.1）。工作区现仅剩 `HANDOFF.md` 副本与评分表脚本目录两处 untracked，无未提交代码改动。

## 0.1 · 11/10 增量更新（14:55，接手 agent 实测）

1. **§3.2 的 5 个文件已闭环**：`26f21e7`（中文空格修复）已提交并推 GitHub；**经 CodeArts SSH 实测已合入** `work/codearts-replication`，合入链 `023b02e`（CodeCheck 修复 merge）→ `26f21e7` → `80b9754`（spacing merge）。§3.2 的「接手建议」命令已执行完毕，作废。
2. **张春阳 CodeArts 侧新增**：`358e072`（Windows 打包排除本地 venv）+ `e55d518`（CodeArts 排障案例文档）。CodeCheck 复扫 / 5 项误报平台标记结果待群里确认。
3. **门禁基线以 `524 passed / 1 skipped` 为准**（新增 12 条 text_norm 单测），前端 `205 pass / 0 fail`、tsc 通过——核对 CI 别再用 512。
4. **CodeArts 连接已打通**：HTTPS 403 根因 = 本机无 HTTPS 凭证且无系统 Git（git 只存在于 WorkBuddy 便携版）；remote 已切 **SSH**（公钥已录入 CodeArts），fetch/push 免密。历史上关于「CH.01100058 权限问题」的描述作废，客服工单不再需要。
5. **ADR-001（AI 生成专业词汇，朱鸿康）/ ADR-002（识别页内嵌网页方框，张春阳）已派发评审**，见 `docs/competition/ADR001/ADR002`；独立分支开发，11/20 前收口，任一延期单独冻结不互相挤占。
6. **今日剩余待办**：22:00 前收 4 张本地跑通截图（用免安装成品版操作卡 `本地跑通操作卡_免安装成品版_2026-11-09.md`）；郭宜平按 6 人分配启动访谈（11/12 交纪要）；确认张春阳 CI 数字为 524/1。

---

## 0.2 · 11/11 增量更新（01:10 修订：五分支全部合入，接手 agent 主刀）

> **本文 > 日志 > 历史对话。** §1 / §3 / §4.3 里写「功能已封存、门禁 524/1」是 11/09–11/10 的历史状态，
> **接手请以本节为准**。

1. **CodeArts 主线 `work/codearts-replication` 已推进到 `f7d0b4b`**（从 `e55d518` 快进），合入五条线：
   `c24c67b`（HANDOFF + ADR-001/002 文档）→ `68bb9c4`（ADR-004 课程资料上传）→
   `3cb85a7`（ADR-003 线下音频增强）→ `ccb73ce`（字幕语言选择 + 章节管理）→
   `050c1cc`（**ADR-002 内嵌网页方框，01:10 队长解除 11/10 挂起决策后补合**，merge `f7d0b4b`）。
   GitHub 同时保留集成分支 `integrate/2026-11-11` 作备份（含全部 merge commits）。
2. **合入冲突点（已解决，未来再合要盯）**：
   - `backend/api/routes.py` 与 `CourseConfig.tsx` 的 import 区叠加（合并两边 import 即可）；
   - 🔴 **`start_asr` 是逻辑冲突**：lang-chapters 加 `chapterId` query、classroom-audio 加
     `AsrStartRequest` JSON body（`micDeviceIndex`/`agc`/`vadProfile`）。合并后签名
     `start_asr(course_id, chapterId="", req=None)`，两参数并存；前端 `_startBackendASR`
     线下课发 body + URL 带 chapterId、线上课纯 POST + URL 带 chapterId。
     已加 `backend/tests/test_integration_contract.py`（2 条）+ 前端契约测试（1 条）守住。
3. **门禁基线（本机实测，核对 CI 用这个）**：后端 **586 passed / 1 skipped**、前端 **243 pass / 0 fail**、
   `tsc --noEmit` 0。数字叠加口径：524（11/09 基线）+17 资料 +5 音频 +11 语言章节 +2 合入契约
   = **559**（11/11 01:10 合入态）+ **20** 延迟修复新增测试（`test_vad_segmentation` 6 +
   `test_whisper_device_detection` 9 + `test_prompt_echo` 增补 5）= **579**；
   前端 205+5+5+3+1+4 webview = 223（以实测为准，别按估算报数）。
   ⚠️ 00:25 版写的 219/0 是**合入 webview 之前**的数字，已作废。
   ⚠️ 11/11 23:45 更新：**559/1 也已作废**（那是合入态的中间数字）。
   ⚠️ **11/12 22:50 更新（最新，以此为准）：586/1、243/0、tsc 0。**
   增量来源：后端 +7（老库升级结构守卫 `test_db_legacy_upgrade.py`）；前端 +20（UI 组件行为与移植纪律守卫 `ui-components.test.mts`）。
4. **跑测试的两个环境坑**：① 必须用 `安装空间/venv/Scripts/python.exe`（系统 python 缺 langchain_core）；
   ② pytest 加 `--basetemp=<全新临时目录>`，否则沙箱批量删除保护会让用例全绿但退出码 1；
   ③ 前端用 `npm test`（runner 带 tsx），手动 `node --test xxx.mts` 会因 TS 无扩展名导入报错。
5. **ADR 状态**：ADR-002 / 003 / 004 均已随代码合入主线（Implemented）；ADR-001（AI 术语生成）仍派发评审中，
   建议降级为「资料优先、LLM 兜底」。11/10 的「webview 挂起」决策已被 11/11 01:10 队长指令推翻。
6. **官方留痕待补**：本次是本地 merge + 直推主线，提交历史有 merge commits 但**平台 MR 记录为空**。
   需让张春阳在 CodeArts 上跑一次 CI（**新数字 586/1、243/0、tsc 0**，目标分支
   `work/codearts-replication`，对**最新提交**触发即可）+ CodeCheck 复扫，并按需补 MR 记录。
   💡 **门禁数字不随提交号变**：最后一次**改代码**的提交是 `207e930`（L1 色值定版 + L2 组件移植），
   其后的提交只改文档 ⇒ 数字恒为 586/1 + 243/0。**不要在文档里硬写"CI 目标提交号"** —— 那会让每次文档更新都作废一次留痕。
   📄 已发《给张春阳_最新成果简报与留痕更新_2026-11-11.md》；旧《CI/CodeCheck/MR 补留痕操作包》**已标注作废**
   （其中 `863677a` / `559` 全部过期，但三步操作流程、坑位速查、九项截图清单仍有效）。
7. **待办**：郭宜平访谈还差 2 人（研究生工科/人文，现 4 人全本科生 < 报告 ≥5 线）；
   10dB 加噪 CER A/B 对比仍缺（ADR-003 验收未闭环，等队长明早录音）。
   ⚠️ **11/11 09:40 更新**：原「李梓萌返工清单 3 项 + 补交线上课页」**已作废** ——
   她 11/10 晚已重做 v1.1 全套并交付可运行原型，见 **§0.3**；
   麦克风测试弹窗**已有设计稿**，ADR-003 验收缺口只剩实现。

---

## 0.3 · 11/11 增量更新（09:40，界面交付与整合决策）

1. **李梓萌 11/10 晚交付「界面初稿与前端代码」包**（`听课伴侣_界面初稿与前端代码_李梓萌_2026-11-10.zip`，
   39 条目 / 2.8 MB）：**可运行的 Vite + React 原型**（不是上一轮那种静态截图）+ 11 张样张 + 12 张截图。
   已解压到 `桌面/_eval_fixtures_pkg/李梓萌原型_2026-11-10/` 供查阅。
2. **整合决策：吸收，不替换。** 三层处理 —— L1 token 定版与线下青 token 收敛（≈0.5 天，**待色值拍板**）；
   L2 组件级移植 `LevelMeter` / `OverlayBar` / `Sidebar` / `HighlightedText` / 通用件（≈0.5–1 天）；
   L3 **明确不做**整体换皮与页面级重写。
   理由（三个已核实事实）：原型是 `.jsx` + 内存假数据 + 无路由，主线是 `.tsx` + zustand + react-router + 真实 API；
   且主线已合入 5 条功能线（字幕语言 / 章节 / 资料 / 麦克风 / webview）原型**均无真实实现**，
   换皮 = 重写全部页面 + 丢功能 + 收口期必炸。
   完整评估见交付目录《界面整合方案_李梓萌原型吸收_2026-11-11.md》。
3. 🟢 **关键利好**：主线 `frontend/package.json` **已依赖 `lucide-react@0.419.0`**，
   与原型同一图标库 ⇒ 组件层可低成本移植，**不需引入任何新依赖**。
4. 🔴 **主线技术债（已定位，待色值拍板后修）**：线下青 `#00cec9` **没有 token**，
   魔法值散落 `CourseConfig.tsx`(5 处) / `CourseList.tsx`(4) / `SubtitleOverlay.tsx`(3) /
   `MaterialsPanel.tsx`(2) 共 **14 处**；且底色 `--bg-secondary:#1a1a2e` / `--bg-tertiary:#16213e`
   偏蓝紫，与紫 accent 同色域导致层次发糊。原型 token（中性灰黑底 + `#8b84e8` / `#2dd4cf`）更干净。
   另有**三套色值并存**（主线 / 原型 / 11-10 设计文档 `#7F77DD` + `#00CEC9`），必须一次性定死。
5. 🟢 **白捡三项**：① 麦克风测试弹窗**已有设计稿**（试采 3 秒 / 回听效果 / 电平判定 good·偏亮·偏低）
   ⇒ 可直接照实现并关闭 **ADR-003 §0 验收缺口**，省一轮设计往返；
   ② 原型「课程大纲」= 我们的章节管理，可作 PPT 第 8 页视觉参考；
   ③ 原型带时间码的「历史字幕」列表 = #2「课后字幕回看页」的视觉参考（但无多选/标签交互，
   该页仍需自建，≈1.5–2 天估算不变）。
6. 🟡 **待催的交付缺口**：梓萌 README 自己承诺的「页面结构 + token + **需要接入的接口清单**」
   三样**目前一份未交**。接口清单最关键（原型全假数据）；**建议我方主动出一份《主线真实 API 清单》给她**。
7. 🟢 **PPT 素材「待拍」状态可整体清空**：11 张样张已覆盖第 4–10 页全部素材需求
   （`样张03_识别页_双场景对照` = 第 4 页最佳主图）。⚠️ 但**必须标注「界面设计稿」**，不得当作成品实拍。
8. ⚠️ **成品红线**：原型底部有「界面初稿 v1.1」水印，**不得进入正式成品**（验收：全量 grep 应为 0）。

---

## 0.3.1 · 11/12 增量更新（22:50，L1 色值定版 + L2 组件移植已落地）

> **这一节推翻了 §0.3 的「待色值拍板」状态。** 拍板已做、代码已进主线、已打包。

1. **拍板结论**：**L1 以李梓萌 token 为准 + L2 只移植最小高价值集**（`LevelMeter`/`IconButton`/
   `StatusDot`/`Pill`/`Toast`/`EmptyState` 6 个）；**L3 整体换皮明确不做**。
2. **L1 色值定版**（`frontend/src/styles/global.css`）：
   - 底色系：蓝紫调 `#1a1a2e/#16213e/#1e1e30` → **中性灰黑** `#0e0e11/#151519/#1a1a20/#202027/#2a2a32`；
   - 一色一场景：线上紫 `--accent #8b84e8`、线下青 `--accent-offline #2dd4cf`；
   - 语义色：绿 `#6cc9a5` / 琥珀 `#d8a35f` / 红 `#d97765`（沿用主线命名）；
   - 圆角 10px → 12px；新增 5 档线下青透明度 + 语义色派生 + 遮罩类 token 共 20 余个；
   - **魔法值收敛：§0.3 第 4 条说的「14 处」实测是 16 处**（`#00cec9`），现已全部归零；
   - 「改配色只改一处」写进了 CI 守卫（`frontend/tests/css-vars.test.mts`：旧色值复活即失败）。
3. **L2 组件移植**（`frontend/src/components/ui/`）：原型是 `.jsx` + `useState` + 内存假数据 ⇒ **不能 cp**，
   全部人工转写为 TSX，颜色一律走主线 `var(--x)`。移植时补齐原型缺口（`Pill` 四档 tone 原型一个都没实现、
   `IconButton` 空 label、`StatusDot` 补 `purple` 档、`EmptyState` 支持 `icon`）。
4. **电平表升级**：`ClassroomMicrophone` 的原生 `<meter>` 换成 26 段电平柱（`LevelMeter`），
   并补齐 **good / low / silent 三态判定**（ADR-003 §0 验收项，此前只有 silent 一档）。
5. ✅ **§0.3 第 8 条「成品红线」已由 CI 守住**：`ui-components.test.mts` 断言原型水印「界面初稿」
   不得进成品，也断言组件源码与 `ui.css` 不得写死 hex/rgb。
6. **验收方式（不是看截图，是回读计算样式）**：用真实 Chromium 渲染 `frontend/dist` 产物后
   `getComputedStyle` 回读 —— 底色 `rgb(14,14,17)` ✅、旧蓝紫 `rgb(26,26,46)` 归零 ✅、
   紫/青/三语义色全部生效 ✅、线上卡用紫线下卡用青 ✅、电平表 26 段柱柱色 = 新线下青 ✅、
   状态点 = 新线上紫 ✅、三态判定文案正确 ✅。
7. 🔴 **对外材料口径纪律**：**v3.8.0 及更早的包仍是旧蓝紫界面**，
   界面截图必须来自 **v3.8.1 及以后**的产物。旧包截图不能当新界面展示。
8. 📦 **v3.8.1 产物已就绪**（2026-11-12 22:51 打包，7 分 19 秒）：

   | 文件 | 大小 | sha256（前 16 位） |
   |---|---|---|
   | `tingkebanlv-3.8.1-setup.exe` | 297.7 MB | `70424d569d4dee71` |
   | `win-unpacked-3.8.1.zip` | 363.4 MB | `0ea9ea8a5d49bc2f`（972 文件） |

   打包实测两道关全过：① 包内冻结后端 `/api/health` → `version 3.8.1`；
   ② **老库升级复验**（造 v3.7.x 形态老库）→ 启动不炸、三列补出、老数据完整、索引全建成、日志无异常。
   提交：`207e930`（界面定版）→ `e7e9eb0`（打包 + 文档），已推双库（CodeArts `work/codearts-replication` + GitHub）。
8. 📌 **本次顺带修掉的两个环境坑**（都在 `packaging/verify/launch-ui.cjs`，一次性工具）：
   ① `--remote-debugging-port` **必须作为命令行参数**传给 electron.exe ——
   靠 `app.commandLine.appendSwitch()` 追加对 DevTools HTTP 端点**无效**（浏览器进程在 JS 主脚本之前
   就已读该开关）⇒ 表现为「Electron 正常起来了但端口永远 ECONNREFUSED」；
   ② `TINGKEBANLV_HOME` 目录**必须先建好**，否则日志初始化在 app ready 之前抛错 ⇒ 进程在起后端前就退出。

---

## 0.4 · 11/11 增量更新（12:40，实时字幕延迟修复 —— 🔴 最影响演示效果的一次改动）

1. **触发**：队长本地实测「换一个没听过的课程」时发现字幕延迟过高，**经常延迟三句话左右**。
2. **根因不是机器慢**：`backend/audio/vad.py` 的 `MAX_SPEECH_DURATION`（最长片段保护）
   **从来没有真正生效**，三层根因逐层剥出：
   ① 上限检查**只写在静音分支**里 ⇒ 老师连续讲话时永远进不了该分支
      ⇒ 片段只能等 0.8s 静音才提交（实测声明 12.0s → 实际最长 **15.07s**）；
   ② 上限量的是「**语音窗口累计**时长」而非片段实际时长
      （讲课时语音窗口只占 **66.3%**，静音窗口也进 buffer ⇒ 实测声明 5.0s → 实际 **11.23s**）；
   ③ 达到上限时还会被 `MIN_SPEECH_DURATION(1.2s)` 守卫**挡掉** ⇒ 既不提交也不重置
      （实测声明 4.0s → 首段 **7.26s**，而该段语音累计仅 1.22s）。
3. **修复后**：片段长度**严格**受上限约束（5.0→5.02s / 4.0→4.00s / 3.0→3.01s），
   端到端延迟从 **~16s（≈三句话）降到 ~8s（≈两句话）**。
4. 🟢 **最终参数是 `MAX_SPEECH_DURATION = 6.0`（不是 5.0）** ——
   用**生产引擎端到端**在中文真实课堂音频 420s 上扫档位，**5.0s 处有准确率断崖**：
   | 上限 | 6s | 8s | 7s | 6s | **5s** | 4s |
   |---|---|---|---|---|---|---|
   | CER差 | **20.17%** | 22.18% | 23.28% | 20.84% | **39.06%** ⚠️ | 32.96% |
   | 漏字 | **67** | 90 | 87 | 67 | **218** ⚠️ | 60 |
   断崖成因：片段越短 → 出现越多「几乎没语音」的短段 → 模型把**简体引导语**念出来
   → `EchoCleaner` 判成纯回显**整段丢弃** ⇒ 内容直接丢失（不是模型没听清，是我们丢的）。
   ⇒ **6.0s 反而优于 8.0s**（切点更贴近语义边界），且比 5.0s 只多 1s 延迟。
5. 🔴 **顺带修掉一个架构级错误**：`_detect_device()` 原先只用 `torch.cuda.is_available()`
   探测 GPU，而 faster-whisper 底层是 **ctranslate2**（自己管 CUDA，不需要 torch）；
   本机装的是 `torch 2.14.1+cpu` ⇒ 永远 False ⇒ **RTX 5060 被判成 CPU，显卡全程闲置**。
   改为以 ctranslate2 为主、torch 为辅；并新增 `_cuda_runtime_ready()`
   （**枚举到 GPU ≠ 能推理**：缺 `cublas64_12.dll` 时模型构造成功、直到 encoder 阶段才炸）
   + 构造期 **CUDA→CPU 自动回落**兜底。缺运行时时安全走 CPU 并给出可执行提示。
6. 🔴 **又抓到一个真实缺陷**：模型在短片段上会吐出**截断的引导语**
   （实测「请使用简体中文转。」，比原文少一个「写」字），
   黑名单的派生方式（整句/子句/剥敬语前缀/**后缀**）一个都生成不出前缀形态 ⇒ 漏救
   ⇒ **虚假字幕直接留在屏幕上**。已修（`prompt_echo.py` 的 `_is_truncated_pure_echo`）。
   🔴 **修法刻意不把前缀塞进黑名单** —— 短前缀会误伤正常课文
   （实测「请使用简体的字形」会被删成「的字形」）。前缀**只用于整段丢弃**，
   局部剔除行为**完全不变**。
7. **提交**：`9b28265`（VAD + CUDA 修复）→ 推 CodeArts 主线 + GitHub 双库。
   新增测试：`test_vad_segmentation.py`(6) + `test_whisper_device_detection.py`(9)
   + `test_prompt_echo.py` 增补(5) ⇒ **门禁 579 passed / 1 skipped**（基线 559）。
8. 🆕 **新增 5 个诊断脚本**（都在 `scripts/`，可复现本文所有数据）：
   `bench_latency.py`（裸模型权衡基准）、`probe_vad_segments.py`（VAD 埋点）、
   `probe_maxsweep_e2e.py`（**生产引擎**扫档位，定参数靠它）、
   `probe_cliff_cause.py`（断崖归因）、`probe_context_production.py`（上下文对照）。
9. ⚠️ **方法论教训（接手方务必看）**：ctranslate2 在 CPU 上多线程归约的浮点顺序不确定，
   同一配置跑三次 CER差可差 **±6.5pp**（切出段数相同、识别结果不同）。
   ⇒ **单次对比差异 < ~7pp 一律不能当结论**，必须多轮复现。
   本轮另一个坑：我第一版诊断脚本的字符级对齐把插入数算成了 0（标记写进了 `a` 却在 `b` 里数），
   导致与另一指标矛盾。**两个指标打架时先怀疑自己的测量工具。**
10. 🟡 **已知副作用（要如实说）**：片段变短后「点词弹卡上的课上原句」上下文变短
    （原 12~15s → 现 ~6s）。点词查义本身不受影响（查的是单词），
    但「回看原句」体验变差 ⇒ 赛后改进项：点词弹卡展示**邻接片段合并**的上下文。
11. 📄 **完整归因文档**：`桌面/听课伴侣/延迟归因与优化_2026-11-11.md`
    （含所有实测表格、根因链、参数决策理由、答辩口径）。
    **答辩话术**：不要说「零延迟」，说「字幕延迟由片段长度决定，我们把片段上限
    从**实际 15 秒**修到 **6 秒**，端到端约 **8 秒**；更低的延迟需要真正的流式解码，
    属于赛后路线图」—— 能讲清「延迟 = 攒片段 + 推理 + 推送」三段拆解，
    比吹嘘「实时」更有说服力。

---

## 1. 项目是什么 / 为什么在做

**产品**：「听课伴侣」——国外大学公开课（无字幕、专业名词多）实时识别音频输出双语字幕、点击生词查释义、SM2 间隔重复进生词本的桌面软件。
**一句话定位**：不是"更准的字幕工具"，而是补全行为链「听到生词 → 点一下 → 进入复习系统」。三关键词：**实时查词 / SM2 闭环 / 桌面本地**。

**当前主线任务**：参加 **2026 华为开发者大赛・智能体创新赛**（学业辅助工具方向）。
- 提交 deadline **2026-11-24**；**2026-11-20 前必须完成绝大部分工作**（只收口不开新功能）。
- 评分五维：功能完备度 30 / 码道深度 25 / 专业贴合 25 / 技术架构 15 / 交付规范 5；团队自评目标 **93/100**（28/23/23/14/5）。
- 强制要求：用**华为云码道 CodeArts** 作为核心 AI 编程工具（占 25 分），需留痕（每日提交、CI、CodeCheck 门禁、Wiki、制品仓库）。
- 演示视频 **≤3 分钟**（赛题原文，别写成 5 分钟）。

**产品状态**：**功能已完工并封存**，不是"还在写代码"。v3.7.1 安装包已交付，v3.7.2 tag 为 V7 实验闭环里程碑。参赛阶段的工作主要是**码道留痕 + 材料（PPT/视频/答辩稿/proof 汇编）**，不是开发新功能。唯一可选新代码：朱鸿康的华为云语音转写接入（增强对比项，延期不影响主线演示）。
> ⚠️ **11/11 更新**：上段是 11/09 初版状态。参赛期实际又新增并合入了四项能力
> （课程资料上传 / 线下音频增强 / 字幕语言选择 / 章节管理），主线已见 §0.2。
> 即「主线功能已收口」，但**不再是纯材料阶段**——新增能力的演示镜头、CodeArts CI/CodeCheck 留痕需一并补齐。

---

## 2. 关键路径速查（绝对路径，Windows / Git Bash）

| 用途 | 路径 |
|---|---|
| **主仓库（工作副本）** | `C:\Users\Administrator\Desktop\听课伴侣\项目源代码\` |
| 交付/物料根目录 | `C:\Users\Administrator\Desktop\听课伴侣\` |
| Python venv（**必须用这个跑测试/后端**） | `C:\Users\Administrator\Desktop\听课伴侣\安装空间\venv\Scripts\python.exe` |
| 验证/探针工具目录 | `C:\Users\Administrator\Desktop\听课伴侣\安装空间\`（`_cdp_*.py`、`_minws.py`、日志） |
| 测试数据 | `项目源代码\testset\`（`english_lecture\en_lecture_mit6034_p4.wav` 英文；`chinese_lecture\real\real_zhlecture_origin_plot.wav` 中文 630s） |
| 运行时数据（DB/模型/日志） | `C:\Users\Administrator\.tingkebanlv\`（`data\tingkebanlv.db`、`whisper_models\`、`onboarding.json`） |
| 截图/输出 | `C:\Users\Administrator\WorkBuddy\2026-08-09-19-08-46\outputs\` |
| 备赛文档 | `项目源代码\docs\competition\` |
| 质量文档 | `项目源代码\docs\quality\` |
| 工作区记忆（历史日志，若同账号可读） | `C:\Users\Administrator\WorkBuddy\2026-08-09-19-08-46\.workbuddy\memory\` |
| 完整教训全文（编号稳定） | 记忆目录 `LESSONS.md` + 逐日 `2026-10-*.md` / `2026-11-*.md` |

**若换了账号导致路径不同**：先找含 `frontend/electron/main.cjs` 与 `backend/main.py` 的目录，即为仓库根；其余路径按相对关系推。记忆文件可能不可读，此时以本文为准。

---

## 3. 仓库状态（2026-11-09 17:15 实测）

### 3.1 分支 / 提交 / tag

```
当前分支：fix/codecheck-first-scan   ← HEAD 430af26
其他本地分支：2026-华为智能体创新 / feat/langchain-agent / main
远程：origin (GitHub: git@github.com:lizerankmpop-dev/tingke-banlv.git)
      codearts (https://codehub.devcloud.cn-north-4.huaweicloud.com/.../tingkebanlv.git)
tag：v3.5.0 v3.6.0 v3.7.0 v3.7.1 v3.7.2（v3.7.2 落在 2bde3f3）
```

最近提交（从新到旧）：

| commit | 内容 |
|---|---|
| `430af26` | fix(electron): 后端解释器探测链补 `backend/.venv`（新克隆机器跑不起后端的坑）+ 复刻手册 §4.3 同步 |
| `0dccfcc` | docs(quality): CodeCheck 首扫 47 项修复说明（42 修复 + 5 误报依据） |
| `612082f` | fix(codecheck): 47 项严重问题修复（20 文件 +52 −70） |
| `eee717c` | docs(competition): 备赛材料首次入库（复刻手册/分工/定位/评分表/5 人任务书+台账） |
| `2bde3f3` | V7 闭环（tag v3.7.2） |
| `798526ee` | 张春阳在 CodeArts 的 CI 首绿提交（分支 `fix/node20-ci-test-runner`，**不在本地**，在 CodeArts） |

`fix/codecheck-first-scan` 三笔提交**已推 GitHub**；CodeArts 侧由张春阳合入（他的协作分支 `work/codearts-replication`）。

### 3.2 ✅ 历史「未提交改动」记录（已闭环，勿再照做）

| 文件 | 状态 | 内容 |
|---|---|---|
| `backend/asr/text_norm.py` | **新增 untracked** | CJK 空白归一化 `normalize_cjk_spacing()`：只删两个 CJK 字符之间的空格，保留拉丁/数字旁空格 |
| `backend/asr/whisper_engine.py` | 已改 | 顶部 import 该函数；`raw_text = normalize_cjk_spacing(segment.text.strip())`（L712 附近，在回显清洗之前） |
| `frontend/src/pages/SubtitleOverlay.tsx` | 已改 | 新增 `wordSeparator(cur, next)`；历史字幕 L395 / 实时字幕 L410 两处 `{' '}` 改为条件空格 |
| `backend/tests/test_text_norm.py` | **新增 untracked** | 12 条单测（中文合并 / 拉丁保留 / 安全性） |
| `docs/competition/任务书/查收台账_2026-11-06.md` | 已改 | 11/10 CodeCheck 行勾 ☑ + 备注修复闭环 |

**起因**：张春阳 11/08 22:21 反馈中文课程（宋浩高数 2.0）跑通但精度差、且「每个字之间都有空格」。根因两条：
1. 前端逐词渲染时**每个 token 后强制补空格**（`{w.word}{' '}`）——英文正常，中文变成每字一空格；
2. 默认 `faster-whisper base`（141MB）对中文弱，专名错误（"宋浩"→"松号"、"待改善"→"代改善"）。

> 注：仓库根 `项目源代码/HANDOFF.md` 是本文同内容副本（untracked），接手时按需一并提交或忽略；它不属于上述代码改动。

**已验证**：
- `pytest backend/tests -q` → **524 passed / 1 skipped**（基线 512 + 新增 12），前端 `npm test` **0 fail**，`npx tsc --noEmit` 通过。
- `base vs medium` 中文实测（真实中文授课 wav 前 60s，CER 字错率）：**base 0.3129 / medium 0.2778**；medium 对英文专名（Origin/Book）明显更好，但 CPU int8 下 60s 音频要 307s（≈5× 实时），**不适合 CPU 实时字幕**。
- **中文端到端 E2E 尚未跑通**：`安装空间/_cdp_flow_zh.py` 两次都因假阳性误报成功（一次匹配到课程名中文、一次匹配到配置页帮助文案；且「开始识别」按钮 y=1153 在视口外，点击前未 `scrollIntoView`）。代码修复本身有单测保证，但**缺一次真·端到端验证**。

**接手建议（第一步）**：
```bash
cd "/c/Users/Administrator/Desktop/听课伴侣/项目源代码"
git add backend/asr/text_norm.py backend/asr/whisper_engine.py \
        backend/tests/test_text_norm.py frontend/src/pages/SubtitleOverlay.tsx \
        "docs/competition/任务书/查收台账_2026-11-06.md"
git commit -m "fix(subtitle): 中文词间空格归一化 + 条件渲染（张春阳反馈）"
git push origin fix/codecheck-first-scan
```
然后按 §3.3 补一次中文 E2E（脚本改法见 §10.9）。

### 3.3 中文 E2E 待办（脚本现状）

`安装空间/_cdp_flow_zh.py` 当前是 **"GfgSla 版"**：有「新建中文课 → 点课程卡片 → 开始识别」流程，但缺陷：
- `find()` 只取 `getBoundingClientRect()`、**没滚动**（配置页长，按钮在 y≈1153 > 视口 1019，点不到）；
- 字幕判据含课程名里的"绘图"、帮助文案里的"老师/新手"，导致 0s 假阳性；
- `TINGKEBANLV_PORT` 已修为 8765 且 vite 与 electron **同端口**（关键，见 §10.8）。

修法（重写要点）：① 两段式点击（先 `scrollIntoView` → sleep 0.7s → 再取 rect → 检查坐标在 1707×1019 内 → 再点）；② 判据只用授课内容词 `项目管理器/工作区/菜单栏/工具栏/默认创建`；③ 点「开始识别」后先等页面出现「识别中 / 监听 / 收音」再播音频；④ 断言：字幕区文本不存在 `[\u4e00-\u9fff] [\u4e00-\u9fff]`。
参考可跑通的英文版脚本：`安装空间/_cdp_flow_dev.py`（2026-11-08 跑通：vite 5s → 后端 /api/ping → 课程列表 → 开始识别 → 15s 出字幕，截图 `outputs/_devflow_3_subtitle.png`）。

---

## 4. 技术栈与运行方式

### 4.1 版本（锁定，复刻时别改）

| 层 | 组件 | 版本 |
|---|---|---|
| 前端 | Node / Electron / Vite / React / TS | 20.x / ^31.3.1 / ^5.3.5 / ^18.3.1 / ^5.5.4 |
| 前端 | react-router-dom / zustand / dexie / electron-builder | ^6.26.0 / ^4.5.4 / ^4.0.4 / ^24.13.3 |
| 后端 | Python / FastAPI / faster-whisper / silero-vad / SQLite | 3.11 / 见 requirements / **base 模型默认 141MB** |
| AI | LangChain 1.x（RunnableChain / checkpointer / RAG）、本地 Ollama（查词讲解） | — |

通信：前端 **HTTP 500ms 轮询**（`POST /api/asr/start` + `GET /api/asr/poll`），**不是 WebSocket**。

### 4.2 三种启动姿势

```bash
# A. 开发（队员 11/10 验收用；两句必须同一个 cmd 窗口）
cd frontend
set VITE_DEV_SERVER_URL=http://localhost:5173
npm run electron:dev

# B. CDP 自动化验证（本仓库实测可跑通；不要经 npx，直接调 exe）
#    见 安装空间/_cdp_flow_dev.py
TINGKEBANLV_DEBUG_PORT=9222 TINGKEBANLV_PORT=8765 VITE_DEV_SERVER_URL=http://localhost:5173 \
  node_modules/electron/dist/electron.exe . \
  --disable-renderer-backgrounding --disable-background-timer-throttling \
  --disable-backgrounding-occluded-windows --disable-features=CalculateNativeWinOcclusion

# C. 免安装成品（L4 被 NSIS 环境卡住时的等价物）
项目源代码/frontend/release/win-unpacked/听课伴侣.exe   # 双击即用，含 whisper_models+backend
```

**解释器解析顺序**（`frontend/electron/main.cjs:404` 起，11/08 刚修）：
`TINGKEBANLV_PYTHON` → `backend/.venv` → `安装空间/venv`（开发机特有）→ 系统 `py -3`/`python`（须能 `import fastapi, uvicorn`）。

**模型**：`TINGKEBANLV_WHISPER_MODEL`（默认 `base`，可选 small/medium/large），模型目录 `~/.tingkebanlv/whisper_models/`；HF 下载需 `HF_ENDPOINT=https://hf-mirror.com` 且**清掉 http_proxy**（见 §10.1）。medium 已下载到应用目录（2026-11-09 为对比实验下的）。

### 4.3 门禁基线（改动后必须复核）

> **11/12 22:50 更新（最新，以此为准）：后端 586 / 1 skipped、前端 243 / 0、tsc 0；主线 HEAD `207e930`。**
> 11/12 增量：L1 色值定版 + L2 组件移植（后端 +7 老库升级结构守卫，前端 +20 UI 组件守卫）。
> 11/11 的「579 / 1、223 / 0」已作废（那是延迟修复后的数字，尚未含界面定版的测试增量）。
> 下表是 11/09–11/10 的历史基线，保留供追溯，别用它核对现在的 CI。

| 项 | 历史基线 | 11/10 历史值 |
|---|---|---|
| 后端 pytest | `512 passed / 1 skipped`（HEAD） | `524 passed / 1 skipped`（含 12 条新单测） |
| 前端 | `205 pass / 0 fail` | `0 fail` |
| 双副本对拍 | 260/260 diff=0 | 保持（每次改 HANDOFF 后重新对拍） |
| 前端 205 + 后端 512 挂 CI | 11/07 22:51 首绿（张春阳） | 保持 |
| CodeCheck | 首扫 47 项严重 → 42 修 + 5 误报待平台标记 | 待张春阳复扫（合入后需再扫一次） |

⚠️ **评估脚本必须加 `--realtime`**：离线一次性喂 630s ⇒ Whisper 只认最后 30s ⇒ WER 飙到 0.67（正常基线 0.1130）。
`--realtime-speed`（默认 1.0）可压缩墙钟，2x/3x/4x 已验证 WER 不变；4x 偶发背压丢片，用 2x 重跑。

---

## 5. 参赛材料索引（全在仓库/交付目录）

### 5.1 `项目源代码/docs/competition/`

| 文件 | 用途 |
|---|---|
| `CODEARTS_REPLICATION.md` | **13 章架构师复刻手册**（版本清单/目录/环境/API/DB 迁移纪律/ASR-LLM 流水线/Electron 多窗口/码道四阶段留痕+proof 10 项/华为云增强/五文件夹/验收清单/落地顺序）——全员先读 |
| `TEAM_TASK_PLAN.md` | W1–W4 倒排、逐人目标/deadline/交付物、协作矩阵、风险兜底、里程碑签字 |
| `PROJECT_POSITIONING.md` | 定位叙事（PPT/申报材料底稿） |
| `华为智能体创新赛_评分标准与目标对照表.xlsx` | v2 冲顶版 3 sheet（评分对照含"多数高校队伍常见水平"列、团队目标、里程碑 10 节点）；生成脚本在 `docs/competition/.华为...ref/build.py` |
| `任务书/任务书_{李泽然,张春阳,李梓萌,郭宜平,朱鸿康}_2026-11-06.md` | 5 份自包含私人任务书 |
| `任务书/查收台账_2026-11-06.md` | **队长验收主表**（送达登记 + 11/8→11/24 共 22 查收行 + 逾期三规则），状态见 §8 |

### 5.2 `项目源代码/docs/quality/`

- `codecheck_first_scan_fix_2026-11-08.md` —— CodeCheck 首扫 47 项修复说明：42 项逐条清单 + **5 项 G.TYP.08 误报标记依据**（`type(x) is str` 是有意的精确类型检查，isinstance 会放宽 LangChain 输出契约）+ 合入复查指引。

### 5.3 `桌面/听课伴侣/`（交付物料，可直接转发）

| 文件 | 说明 |
|---|---|
| `tingkebanlv-full.bundle` | 6.2MB 全量仓库（17 refs，3 分支 + 全 tag），CodeArts 初始化用 |
| `tingkebanlv-codecheck-fix.bundle` | **6.4MB，含 `612082f`/`0dccfcc`/`430af26` 全 refs**（11/09 重打） |
| `给张春阳_仓库初始化操作包.md` | clone bundle → 换 remote → push → CI 首绿 |
| `给张春阳_CodeCheck修复合入操作包_2026-11-08.md` | 三笔提交 + 合入/复查/标记误报四步 |
| `给张春阳_eval夹具包.zip` + `eval夹具包_README.md` | 5 份 JSON 夹具，修 CI 485/28 → 512/1 |
| `本地跑通操作卡_2026-11-08.md` | **11/10 全员照做**（含 11/09 新增的 `VITE_DEV_SERVER_URL` 与两条翻车条目） |
| `调研数据/问卷明细_113份.xlsx` + `调研分析报告_113份_2026-11-08.md` | 问卷分析（PPT 三数字：痛点 92.9% / 高频遗忘 48.7%+课上打断查词 47.8% / 用过工具仍痛 56.3%；中英对照 71.7% 断层第一，隐私仅 5.3%；样本红线 2/5 达标，定向补捞研究生 10~15 份） |
| `CodeCheck/CodeCheck明细_首次扫描.xlsx` | 47 项缺陷明细（从微信缓存复制，需 `chmod +w` 才能被表格工具打开） |
| `安装空间/` | venv + CDP 工具 + 日志 + `_cdp_flow_dev.py` / `_cdp_flow_zh.py` |

---

## 6. 团队、账号与协作链路

| 成员 | 角色 | 主要交付 |
|---|---|---|
| 李泽然 | 队长（用户本人） | 统筹、材料、ADR、排名/台账 |
| 张春阳 | 副队长 | CI / CodeCheck / proof 01–05 / 答疑 |
| 李梓萌 | 美术 | 版式样张、PPT 全稿、图标、演示视频（≤3 分钟） |
| 郭宜平 | 答辩 | 调研报告、大纲、答辩稿 8–10 分钟、评分映射表 |
| 朱鸿康 | 技术 | 华为云语音转写接入、长稳测试、proof 汇编、五文件夹终装 |

**协作通道**：
- **CodeArts 项目**：华北-北京四，仓库 `tingkebanlv`（Repository ID `3118021`），5 人已入组。
  - git HTTPS 用户名是 IAM 用户 **`lizerankmpop`**，不是主账号 `hid_cqwnzebvycs1og6`。
  - 队长账号被「repo not found / CH.01100058」卡住（仓库级成员权限缺失），**周一（11/09 之后的第一个工作日）找华为云客服**；期间用 bundle + GitHub 双通道保底。
  - ⚠️ 主账号密码曾在聊天记录中暴露，**必须修改**（本文不记录密码）。
  - 待确认：`hw030029912956086` 究竟是朱鸿康还是张春阳（CI 执行人是它，但用户说是张春阳做的）。
- **GitHub 备份**：`git@github.com:lizerankmpop-dev/tingke-banlv.git`（SSH key `id_ed25519` 可用），3 分支 + 全 tag 已推。
- **离线传输**：`git bundle create <file> <branch> --all` → 微信发 → 对方 `git bundle verify` + `git fetch <bundle> <branch>:<branch>`。

---

## 7. 当前会话（11/08–11/09）已完成的工作

| # | 事项 | 结果 |
|---|---|---|
| 1 | CodeCheck 首扫 47 项 | 42 修复 + 5 误报（依据已写文档）；`612082f`；pytest 512/1 不变；**门禁阈值未动** |
| 2 | 修复说明 + 合入操作包 + bundle | `0dccfcc` + 交付文件；待张春阳在 CodeArts 合入并平台标记 5 项误报 |
| 3 | 源码 dev 模式全链路实跑 | `_cdp_flow_dev.py` 跑通：15s 出英文字幕（截图 `outputs/_devflow_3_subtitle.png`） |
| 4 | 两个新机必踩坑 | ① electron 后端探测链补 `backend/.venv`（`430af26`）；② 操作卡加 `set VITE_DEV_SERVER_URL=http://localhost:5173`（否则新克隆无 dist → 白屏） |
| 5 | 张春阳中文反馈分诊 | 空格 = 前端逐词渲染强制补空格；错字 = base 模型中文弱；量化 base 0.3129 / medium 0.2778 |
| 6 | 中文空格修复（**未提交**） | 见 §3.2；单测 12/12、全量 524/1、tsc 通过 |
| 7 | 台账 | 11/10 CodeCheck 行已勾 ☑ |

---

## 8. 时间线与台账当前状态（摘自查收台账，接手后每天更新）

| 日期 | 节点 | 状态 |
|---|---|---|
| 11/08 | 张春阳 CI 首绿 | ☑ 提前达成 |
| 11/10 | **全员 4 人本地跑通截图** | ☐☐☐☐ **最容易集体拖延，今晚/明早群里催** |
| 11/10 | 张春阳 CodeCheck 接入截图 | ☑（修复侧已交付，待合入 + 标记误报 + 复扫清零） |
| 11/12 | 郭宜平调研报告（问卷已有，访谈 ≥5 人待做） | ☐ |
| 11/13–11/18 | 李梓萌样张/PPT/视频、郭宜平大纲/答辩稿、朱鸿康联调/长稳/proof | ☐ |
| 11/20 | **绝大部分工作完成 · 五文件夹齐活** | ☐ 硬性节点 |
| 11/23 | 提交包 zip 上传 | ☐ |
| 11/24 | 兜底 deadline | ☐ |

**接手后要盯的两件最紧的事**：① 张春阳合入 `fix/codecheck-first-scan`（含 `430af26`，11/10 全员跑通依赖它）→ 群里通知"可以开始了"；② 11/10 收 4 张跑通截图（要求：字幕文字 + 右下角任务栏时间）。

---

## 9. 待办清单（按优先级）

### P0（立刻）
1. 处理 §3.2 的 4 个未提交文件：先跑门禁 → commit → 推 GitHub → 重打 bundle → 微信发张春阳（说明这是「中文空格修复」，与 CodeCheck 修复同分支）。
2. 催张春阳合入 + CodeCheck 复扫 + 5 项误报平台标记（依据 `docs/quality/codecheck_first_scan_fix_2026-11-08.md` §3，一句话：**LangChain 链输出契约要求精确 str，isinstance 会放进 str 子类，属行为变更**）。
3. 群里推进 11/10 全员跑通截图；发 `本地跑通操作卡_2026-11-08.md`（v2，含 VITE_DEV_SERVER_URL）。

### P1（本周）
4. 补一次中文 E2E 真验证（§3.3 修法），把「无每字空格」的截图发张春阳闭环他的反馈。
5. 精度答复口径（写进群话术 / 答辩预案）：默认 base 面向英文（英文 WER 0.1130）；中文课程建议 ① 课程配置里**填自定义术语**（人名/专名如「宋浩、高等数学、专升本、考研」，V7 实验证明术语层显著降错，P4 WER 0.1092 历史最佳）；② 有独显可换 `TINGKEBANLV_WHISPER_MODEL=medium`（专名明显改善，但 CPU 上 5× 实时不可行，small 是 CPU 折中）；③ LLM 讲解质量取决于本地 Ollama 模型，属独立配置。
6. 周一：华为云客服解决 `lizerankmpop` 仓库权限；**改主账号密码**；确认 `hw030029912956086` 归属。
7. 11/12 郭宜平调研报告（可补捞 10~15 份研究生问卷洗样本红线）。

### P2（11/13–11/23）
8. 按台账逐节点查收；11/20 五文件夹齐活；11/18 预打包演练；11/19 演示视频 ≤3 分钟；proof 10 项汇编。
9. 可选：`.gitattributes` 是否固化换行纪律（记在 `docs/m3/plan_v6.md §7`）；图标 §8.5 人工目检 5 项（用户 10-26 决定推迟，不阻塞发布）。

---

## 10. 地雷清单（全部实测踩过，别再踩）

### 10.1 `http_proxy` 劫持 curl / urllib / HuggingFace
验证本机后端**必须** `urllib.request.build_opener(ProxyHandler({}))`，否则看到的是代理转发的响应。下载模型时 `env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY HF_ENDPOINT=https://hf-mirror.com`；HF 缓存若是空壳，`HF_HUB_OFFLINE=1` 也没用，得真下载或把 model.bin 路径直接传给 `WhisperModel`。

### 10.2 僵尸端口
Windows 强杀后端口 LISTEN 但无进程，新进程 bind 失败 `[Errno 10048]`，而「端口能否连上」的健康检查**依然成功** ⇒ 只看端口会误判服务还活着。重跑前 `netstat -ano | grep -E ":8765|:9222|:5173"` 确认没有 LISTEN。

### 10.3 NSIS 静默安装 no-op
`setup.exe /S` 退出码 0 但啥都不装（本沙箱安装上下文问题，与包无关）。**唯一判据**：目标目录是否出现 `听课伴侣.exe`。等价验证用 `frontend/release/win-unpacked/听课伴侣.exe` 便携版（含 whisper_models + backend/dist），CDP 已跑通全链路。

### 10.4 GUI 验证必须用 CDP，不是截图+模拟鼠标
`electron/main.cjs:126` 读 `TINGKEBANLV_DEBUG_PORT` → `http://127.0.0.1:9222/json/list` → WebSocket → CDP。
- 窗口不可见 ⇒ renderer 被冻结 ⇒ CDP 全无响应。启动 EXE 时**必须**带 4 个 Chromium 开关（见 §4.2 B），再 `Page.setWebLifecycleState('active')` + `Emulation.setFocusEmulationEnabled` + `Page.bringToFront`。
- **不要经 `npx` 中转**（flag 会丢/兼容重启后丢），直接 `node_modules/electron/dist/electron.exe`。
- `json/list` 目标要**排除 `devtools://` 页面**（否则 attach 到 DevTools UI 白忙），并优先 `5173`。
- 点元素必须**两段式**：`scrollIntoView` → sleep → 再取 `getBoundingClientRect` → 检查坐标在视口内 → 再 `Input.dispatchMouseEvent`。配置页很长，按钮常在折叠线以下（实测 y=1153 > 1019）。
- 判据不要用界面/课程名/帮助文案里会出现的词（两次假阳性的教训）。

### 10.5 CRLF 例外只有 3 个
`scripts/verify_install.py`、`scripts/eval_asr_quality.py`、`testset/chinese_lecture/metadata.json` ⇒ **不要归一化它们**。`git ls-files --eol` 可查；`git diff` 出现 "LF will be replaced by CRLF" 警告是正常的（core.autocrlf），不是损坏。

### 10.6 `npm run backend:build` 的 python 解析到 PATH 第一个
那个 python 没装 PyInstaller ⇒ 必须用 venv 的 python。electron-builder 下载被代理挡 ⇒ 设 `ELECTRON_CACHE` + `ELECTRON_SKIP_BINARY_DOWNLOAD=1` + `ELECTRON_BUILDER_CACHE`。

### 10.7 gitignore 白名单语法 + 微信缓存 xlsx
- 目录整体排除（`packaging/eval/`）时 `!` 不生效，必须改成 `packaging/eval/*`。这是 CI 曾出现 `485/28` 而非 `512/1` 的根因（夹具被忽略 → skipif）。
- 微信缓存里的 xlsx 只读 ⇒ `cp` 到普通目录 + `chmod u+w` 才能被表格工具打开。
- 本机无 LibreOffice/formulas 引擎 ⇒ 含公式的 xlsx **禁止交付**，改静态值；AIGC 标识脚本最后写入。

### 10.8 vite proxy 端口必须与后端一致
`frontend/vite.config.ts` 的 proxy 目标是 `TINGKEBANLV_PORT || 8765`。若给 electron 传了自定义端口却没给 vite 传，页面加载 5173 后所有 `/api` 打到 8765 空端口 ⇒ **课程列表显示「还没有课程」**（DB 里其实有课）。两边同时设同一个端口，或都用默认 8765。

### 10.9 PyAV 版本不匹配
`faster_whisper` 用文件路径转写时会调 `av.open(..., metadata_errors=...)`，本机 PyAV 不认这个参数 ⇒ **TypeError**。应用内部本来就喂 numpy 数组（`np.frombuffer(wav, int16).astype(float32)/32768`），自己解码即可绕开。评估中文/英文音频走数组路径。

### 10.10 其他
- `_row_to_dict` 把 `created_at` 重命名成 `createdAt` ⇒ 直接读原始列名会**静默拿到 0**（不是报错，是错值）。
- CodeArts 个人设置首次使用需初始化昵称，否则报 "the project is not exist" 且按钮点不动。
- `finally` 内 `return` 会吞掉 try 块异常 —— 8 个 probe 脚本已在 `612082f` 重构（校验移出 finally，还原留在内）。
- 本沙箱后台进程在命令/轮次结束时被回收 ⇒ 长任务用后台任务且**在命令内跑完整流程**，别依赖跨命令存活的进程。

---

## 11. 常用命令速查

```bash
cd "/c/Users/Administrator/Desktop/听课伴侣/项目源代码"

# 门禁
"/c/Users/Administrator/Desktop/听课伴侣/安装空间/venv/Scripts/python.exe" -m pytest backend/tests -q
cd frontend && npx tsc --noEmit && npm test
node --check frontend/electron/main.cjs

# 提交 / 推送 / bundle
git add <files> && git commit -m "..." && git push origin fix/codecheck-first-scan
git bundle create "/c/Users/Administrator/Desktop/听课伴侣/tingkebanlv-codecheck-fix.bundle" fix/codecheck-first-scan --all
git bundle verify <bundle>

# 中文 base vs medium 对比脚本（本机临时文件，可重建）
# C:\Users\Administrator\AppData\Local\Temp\zh_base_vs_medium.py
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  HF_ENDPOINT=https://hf-mirror.com \
  "/c/Users/Administrator/Desktop/听课伴侣/安装空间/venv/Scripts/python.exe" zh_base_vs_medium.py

# CDP 全流程（英文已跑通 / 中文待修）
python "/c/Users/Administrator/Desktop/听课伴侣/安装空间/_cdp_flow_dev.py"
python "/c/Users/Administrator/Desktop/听课伴侣/安装空间/_cdp_flow_zh.py"   # 见 §3.3
```

---

## 12. 术语 / 缩写

| 词 | 含义 |
|---|---|
| 码道 | 华为云 CodeArts（代码托管 / CI / CodeCheck / Wiki / 制品仓库），占评分 25 分 |
| proof 01–10 | 码道四阶段留痕证据截图/日志（详见复刻手册 §9） |
| 五文件夹 | 赛题要求的提交包结构（复刻手册 §11） |
| V7 / P1–P20 | 术语层效果量化实验（已全部闭环，结论见 `docs/m3/plan_v7.md` 与记忆索引 v20） |
| WER / CER | 词错率 / 字错率；英文基线 0.1130，中文实测 base 0.3129 / medium 0.2778 |
| SM2 | 间隔重复算法（生词复习闭环） |
| CDP | Chrome DevTools Protocol（本项目 GUI 自动化唯一可靠手段） |
| L4 | 安装包端到端验收层（本机被 NSIS 环境卡住，用便携版等价验证） |
| win-unpacked | `frontend/release/win-unpacked/听课伴侣.exe`，完整便携版 |

---

## 13. 历史记忆索引（同账号可读时）

- **总索引**：`memory/MEMORY.md` 里的「听课伴侣 记忆索引（v20）」——V7 全闭环状态、三个静默失败坑、CDP 解法、门禁基线、待办。
- **教训全文**：`memory/LESSONS.md`（编号稳定）+ 逐日 `2026-10-*.md` / `2026-11-06.md` / `2026-11-07.md` / `2026-11-08.md`（11/08 含 CodeCheck 修复闭环 + dev 模式验证 + 中文空格修复）。
- **V7 实验**：`项目源代码/docs/m3/plan_v7.md`；网页用户测试 `docs/m3/web_user_test_findings.md`。
- **旧版快照**：`memory/MEMORY_2026-10-28_snapshot.md`。

> 交接完成判据：接手 agent 能 ① 说清 4 个未提交文件是什么、② 跑出 524/1 与前端 0 fail、③ 独立把 `fix/codecheck-first-scan` 推到 GitHub 并打 bundle、④ 按台账推进 11/10 验收。
