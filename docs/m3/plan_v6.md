# V6 推进方案（目标版本 v3.6.0）

> **本文档是 v3.5.0 封存后的下一版执行方案**，不是需求清单。
> 每一条都写成「**现状证据 → 精确改动点 → 验收判据 → 风险与回滚**」四段，
> 保证换一个人也能照着做完，不需要重新考古。
>
> 上游文档：[`backlog_v350.md`](./backlog_v350.md) §八~§十与§十四（待办来源）、
> [`../asr_eval/known_defects.md`](../asr_eval/known_defects.md)（D3 / D4 / D6 全文）、
> [`../asr_eval/WER_REPORT_PLAN.md`](../asr_eval/WER_REPORT_PLAN.md) §16（v3.5.0 回归记录）。

---

## 0. 版本定位与四条硬约束

**v3.6.0 的定位**：**质量收口版**。不加新功能，只做三件事——
① 修掉唯一会让用户看到故障的缺陷（D6）；② 补齐前端体验缺口；③ 把评估方法学的两个已知短板填上。

### 四条硬约束（违反任何一条，本轮工作作废）

| # | 约束 | 原因 |
|---|---|---|
| **1** | **所有改动 `frontend/` 的项必须并进同一次打包** | 改前端 ⇒ 重新打包 ⇒ 作废既有交付物指纹与 L4 / 真机 e2e 结论。v3.5.0 就是为此外推到 V6 的 |
| **2** | **数字必须落仓库内的 md** | `packaging/eval/` 有意 gitignore（`.gitignore:57`），产物不进仓库；只有 md 是长期留存的 |
| **3** | **CRLF 例外文件保持 CRLF，其余全 LF** | 例外仅 3 个：`scripts/verify_install.py`、`scripts/eval_asr_quality.py`、`testset/chinese_lecture/metadata.json`。**不要「归一化」** |
| **4** | **改完文档立刻同步副本并对拍** | `安装空间/_mirror_sync.py` → `_mirror_check.py`；基准必须用 `git ls-files`，**不能用 `find`** |

### 回归对照基线（v3.5.0 实测，本轮所有验收的参照）

| 项 | v3.5.0 基线 |
|---|---|
| 单元/集成测试 | **445 passed / 1 skipped** |
| 变异探针 | **22 / 22** 全部捕获 |
| 七格 CER（`zh`，realtime 稳态） | clean **0.1504** ｜ noise 0.1821 ｜ lowvol 0.1715 ｜ fast 0.4248 ｜ multi 0.6675 ｜ long 0.2773 ｜ real 0.1985 |
| L4 安装包验收 | **61 步 / 58 OK / 0 阻断**（复跑口径） |
| 双副本对拍 | **179 / 179 diff=0 miss=0** |
| 交付物 | `tingkebanlv-3.5.0-setup.exe` 302,587,991 B ｜ sha256 `a1f2ff18…128904dd` |

🔴 **注意**：`clean` / `fast` / `multi` / `long` 四格**跨会话不可对拍**（D3），
本轮验收**只能走同会话对照**；只有 `noise` / `lowvol` / `real` 三格允许跨会话比对。

---

## 1. 范围、分批次与依赖

8 个待办按「是否需要改包」分成三批。**批次顺序即执行顺序**。

| 批次 | 项 | 是否改包 | 前置依赖 |
|---|---|---|---|
| **批 A（改包，一次并批）** | V6-7（D6 竞态）· V6-1（向导提示条）· V6-2（帮助/关于）· V6-3（图标 v4.1）· D1 埋点 | ✅ 是 | 无 |
| **批 B（评估侧，不改包）** | V6-5（基准加固）· V6-6（长样本恒等式） | ❌ 否 | 无（可并行于批 A） |
| **批 C（待外部输入）** | V6-4（`accent` 素材） | ❌ 否 | 🔴 **需用户提供 BV 号 + 起止时间** |

```
批 A ──► 打包 v3.6.0 ──► 发布门禁（C-1~C-4）──► 封存 + tag
           ▲
批 B ──────┘（结论写进 WER_REPORT_PLAN §7，不进包）
批 C ──────► 独立跑通即可，随时可插队（不产生新包）
```

**批内顺序（批 A）**：V6-7 → V6-1 → V6-2 → V6-3 → D1 埋点。
理由：V6-7 是唯一有行为风险的改动，先做先验证；三项前端体验彼此独立、可任意序；
D1 埋点最后做，因为可能要复用 V6-2 的「关于页」作为指标查看入口。

---

## 2. 批 A 执行卡（改包项）

### 2.1 V6-7 · D6 降级重启 × 单实例锁竞态

**现状证据**（`known_defects.md` D6 全文）

| 项 | 值 |
|---|---|
| 现象 | L4 首跑 4 个 critical 失败：界面 150s 内未加载 · 进程 0 个 · ping 0/35 |
| 关键日志 | `[single-instance] 检测到第二次启动，将已有窗口提到前台`（`main.cjs:1779` 触发） |
| 根因 | watchdog 在 **spawn 之后才 `app.quit()`**，而 `app.quit()` 是**异步**的 ⇒ 父进程仍持锁 ⇒ 子进程抢锁失败静默退出（`stdio:'ignore'`，日志里一个字都没有） |
| 用户感知 | 双击图标「闪一下就没了」，**零提示** |

**改动点（精确到行）**

```js
// frontend/electron/main.cjs:1321  relaunchInCompatMode()
try {
  const child = spawn(process.execPath, args, {
    detached: true,
    stdio: 'ignore',
    cwd,
    env: process.env,          // ← 🔴 这里
  });
```

| 候选 | 改法 | 语义 | 副作用 | 结论 |
|---|---|---|---|---|
| **a** | `env: { ...process.env, TINGKEBANLV_ALLOW_MULTI: '1' }` | 子进程跳过单实例检查（`main.cjs:131` 既有逃生口） | 🔴 **锁从此无人持有**：兼容实例存活期间用户再双击图标，新进程能成功抢锁 ⇒ **出现两个实例**（且新实例会再崩再降级，可能自激） | ⚠️ **仅作兜底** |
| **b** | spawn 前 `app.releaseSingleInstanceLock()`，env 保持 `process.env` | 父进程显式让锁，子进程正常抢锁 | 需确认释放后父进程的 `second-instance` 监听与退出行为不退化 | ✅ **主推** |
| c | 用 `app.exit(0)` 替换 `app.quit()` | 同步退出 | `exit()` 不走 `before-quit`，可能漏清理 | 备选组合 |
| d | 子进程带 `--tingkebanlv-compat` 时跳过单实例检查 | 同 a，改判据 | `--tingkebanlv-compat` 用户可误传 | 与 a 等价 |

> **本方案对台账的修正**：台账 §六 推荐 a（一行改动、已实测跑通）。
> 但 a 的副作用有真实语义漏洞——**锁无人持有**。因此主推 **b**：
> 「父进程显式让锁」才符合"这是自我重启"的语义，子进程保持正常单实例行为。
> **a 作为 b 不可行时的兜底**（b 依赖 Electron 的 `releaseSingleInstanceLock()` 在
> `before-quit` 之前调用的实际行为，需实机验证）。

**必须做的副作用实验（3 个，缺一不可）**

| # | 实验 | 通过判据 |
|---|---|---|
| ① | 触发降级后观察 150s | 应用**存活**，`/api/ping` 有响应，`进程 >= 1` |
| ② | ①之后**再双击一次图标** | **不出现两个实例**（这是 a 方案的失败点，也是本轮的核心验证） |
| ③ | **不触发降级**的机器上启动 | 单实例锁语义不变：第二次启动仍被提到前台（回归保护） |

**验收判据**

- L4 连跑 **3 次**，**3/3 全部通过**（不再出现 1 通过 1 阻断的竞态特征）；
- 实验 ①②③ 全过；
- `frontend/electron/` 单测新增（或扩展）：至少覆盖「环境变量接线」与「锁释放顺序」；
- 变异探针：新增 1~2 条，证明「把 `env` 改回去」或「删掉释放锁调用」会让测试变红。

**风险与回滚**

- 风险：`releaseSingleInstanceLock()` 若在打包环境下行为异常，可能导致**双实例正常化**。
  → 缓解：实验② 是判定闸门；不通过则回退到 a + 额外防自激判断（子进程记录一个「我是第几代降级」标记，超过 1 代不再重启）。
- 回滚：单文件单行改动，`git checkout -- frontend/electron/main.cjs` 即恢复。

**实施记录（2026-10-12，代码侧完成）**

按上面的**修法 b** 落地。没有直接改 `main.cjs` 里的两行，而是把重启编排抽成一个新模块 ——
理由是**这里的顺序本身就是不变量**，留在 1600 行的 `main.cjs` 里只能靠注释约束，
任何人重排两行都不会有任何测试变红。抽出来后才能用「调用记录」断言先后。

| 产物 | 内容 |
|---|---|
| `frontend/electron/compat-relaunch.cjs`（新增 126 行） | 导出 `COMPAT_RELAUNCH_ARGS` / `buildCompatRelaunchSpec()` / `performCompatRelaunch()`；所有副作用（`spawn`/`releaseLock`/日志）走参数注入 |
| `frontend/electron/main.cjs`（改 4 处） | ① 顶部 `require('./compat-relaunch.cjs')`；② 删掉局部 `COMPAT_RELAUNCH_ARGS` 定义；③ `relaunchInCompatMode()` 改走 `performCompatRelaunch()`；④ `releaseLock` 回调内调 `app.releaseSingleInstanceLock()` 并置 `singleInstanceOwner = false` |
| `frontend/tests/compat-relaunch.test.cjs`（新增 11 条） | 顺序不变量、spawn 抛异常仍让锁、拿不到 pid 判失败且不 `unref`、**env 不得被注入 `ALLOW_MULTI`**、`detached/stdio/cwd/unref` 语义、未持锁不阻断、释放抛异常不中断重启、打包态 `cwd` 不含 `app.asar`、源码态带 appPath 位置参数、main.cjs 两条源码守卫 |
| `packaging/verify/probe_compat_relaunch_mutations.py`（新增 7 条变异） | A 不再让锁 · B 顺序反转 · C 子进程 env 塞 `ALLOW_MULTI` · D 去掉 pid 检查 · E 释放异常即中断 · F main.cjs 注入 `ALLOW_MULTI` · G main.cjs 绕过模块自己 `spawn` |

实测（本机，2026-10-12）：

- `node --test tests/compat-relaunch.test.cjs` → `# pass 11 / # fail 0`
- `node --test "tests/**/*.test.*"`（全量）→ **168 通过 / 0 失败**
- 变异探针 → **7/7 条变异全部被抓住**（退出码 0）
- `node --check` 两文件语法通过；`COMPAT_RELAUNCH_ARGS` 在 `main.cjs` 无残留引用

补充两条**踩坑记录**（都是「看起来无关、实际必炸」的类型）：

1. **`cwd` 不能取 `app.getAppPath()`**：打包后它指向 `<安装目录>\resources\app.asar`，
   那是一个**文件**；当 `cwd` 传给 `spawn` 会以 `ENOENT` 失败，而错误信息里出现的是 exe 路径，
   看起来像「找不到程序」。源码模式下 `getAppPath()` 是仓库目录、一切正常 ⇒ **CDP 冒烟全绿也照样出门**。
   现由 `buildCompatRelaunchSpec()` 按 `isPackaged` 分流，并有单测钉死「打包态 `cwd` 不含 `app.asar`」。
2. **`spawn` 的失败是异步的**（走 `'error'` 事件，不抛异常）。不挂监听时它会一路冒到
   `uncaughtException`：日志里只剩一行堆栈、用户零提示，而调用方以为已经成功拉起。
   挂上监听后，成败改由**返回值（pid）**决定，调用方才能正确弹错误框。

> ⚠️ **`ALLOW_MULTI` 错修法现在有多重防线**：模块内不注入（变异 C）+ `main.cjs` 接线不注入（变异 F）+
> 两条单测断言。之所以这么严：它**只改一行、跑起来"像是好了"**（降级后应用确实活了），
> 但锁从此无人持有，用户再双击就出双实例 —— 这是最难在评审里发现的一类「修好了但修错了」。

**尚未完成**（属批 A 收尾，需重新打包 v3.6.0 后在真机执行）：3 个副作用实验 ①②③、L4 连跑 3 次见 §5 门禁 C-4。

---

### 2.2 V6-1 · 课程配置页「向导提示条」

| 项 | 内容 |
|---|---|
| 需求 | 课程配置页首次进入时，顶部显示可关闭提示条，说明「语言 / 术语表 / 领域」三项的作用与推荐值 |
| 改动点 | `frontend/src/pages/CourseConfig.tsx`（新增提示条组件）；关闭状态持久化（`localStorage`） |
| 无障碍 | `role="note"`（或 `role="status"`）+ 关闭按钮带 `aria-label`；复用既有 `panel-copy.test.mts` 的**文案审计 + 源守卫**机制 |
| 验收 | ① 新用户首次进入可见；② 关闭后**不再出现**（刷新 + 重启应用均不出现）；③ ARIA 语义完整；④ 文案不出现禁词表内的词（沿用既有静态守卫） |
| 单测 | `frontend/tests/` 新增 1 条：文案存在性 + 关闭状态持久化纯函数 |

**注意**：`localStorage` 在 Electron 渲染进程可用，但**不要**依赖它跨数据目录迁移——
若与 `overlay.json` 的持久化风格不一致，改为走既有配置存储更稳。

**实施记录（2026-10-12）**

缓存方案用了 `localStorage`（与 `ReviewReminder` 的「今天不再提醒」同套路），
键名 `tingkebanlv.courseConfigHint.dismissed`。**没有**改走 `overlay.json` 的原因：
那份配置是悬浮窗的**位置与外观**，语义是「设备相关、需要跨会话保持的偏好」；
而「一条提示看过没有」是一次性标记，`ReviewReminder` 已经确立了 localStorage
先例。已知代价：换数据目录 / 清理应用数据后提示会再出现一次（用户再点一下关闭），
可接受，因此不加迁移逻辑。

| 产物 | 内容 |
|---|---|
| `frontend/src/lib/courseConfigCopy.ts`（新增） | `COURSE_CONFIG_DEFAULTS`（三项默认值，唯一来源）、`DEFAULT_LABELS`、`CONFIG_HINT_COPY`（全部可见文案）、`HINT_DISMISS_KEY`、`isHintDismissed()` / `dismissHint()`（存储可注入） |
| `frontend/src/components/CourseConfigHint.tsx`（新增） | 提示条组件，`role="note"` + `aria-label` + `data-testid="course-config-hint"` |
| `frontend/src/pages/CourseConfig.tsx`（改 4 处） | ① import 常量与组件；② 三处 `useState` 默认值改走 `COURSE_CONFIG_DEFAULTS`；③ **同步 effect 里两处兜底值**（`course.language \|\| '…'`）同样改走常量；④ 修掉 `var(--border-color)` 错名 |
| `frontend/tests/course-config-hint.test.mts`（新增 16 条） | 文案审计、三项齐备、项名与页面控件标签对拍、默认值与页面源码逐字对拍、**字形模式取值与后端常量对拍**、无障碍源守卫、无重复文案字面量、关闭状态全边界、审计变异 |
| `frontend/tests/_copy-audit.mts`（新增） | 审计实现与禁词表的**共享模块**（`onboarding-copy.test.mts` 已改为引用它，两处守卫从此同一份禁词表） |
| `frontend/tests/css-vars.test.mts`（新增 5 条） | `src/**` 的 `var(--x)` 引用完整性（见下方「顺带修复」） |

实测（本机 2026-10-12）：`node --test "tests/**/*.test.*"` → **189 通过 / 0 失败**（168→189）；
`tsc --noEmit` 0 错；`npm run build` 成功（1587 modules）。

**守卫自己抓出来的两个真问题**（都不是事后补的说明，是断言先红）：

1. **同步 effect 里还留着第二份默认值。**
   `useEffect(() => { if (course) { ... setLanguage(course.language || 'auto') ... } })`
   —— 我只改了 `useState` 那一处，这条「课程记录字段缺失时写回默认值」的路径漏了。
   它是真实的漂移源：该路径下页面显示的同样是「默认值」，与提示条里说的推荐值同义。
   断言写法是「**任何** `course.<字段> || '<字面量>'` 都算违规」（正则扫全文件），
   所以第一版只查 `useState` 的写法当场就被否掉了。
2. **注释里的 `role="status"` 把无障碍守卫弄红了。**
   组件注释里写着「用 `role="note"` 而不是 `role="status"`」—— 不剥注释就会
   因说明性文字永远失败，然后被「顺手」放宽成一条更弱的断言。
   已按 `panel-copy.test.mts` 的既有做法先剥块注释再扫。

**顺带修复：一处静默失效的 CSS 变量引用（新发现）**

扫描过程中发现 `CourseConfig.tsx` 的术语预览 chip 用了 `var(--border-color)`，
而 `global.css` 只定义了 `--border`。`var(--undefined)` 是**静默失效**：浏览器不报错、
控制台无输出，那条声明在计算值阶段被判无效后**整条丢弃**，回落到初始值 ——
对 `border: 1px solid var(--x)` 这种简写来说就是**边框直接没有**。
已改为 `--border`，并新增 `tests/css-vars.test.mts` 把这一类彻底钉死
（全量扫描确认当时**只此一处**）。**这是本轮唯一一处**原计划外的代码改动。

**尚未完成**：提示条在**真机上的可见性**（渲染出来没有）尚无自动化覆盖 ——
`smoke-overlay.cjs` 只走主窗口 / 悬浮窗 / 向导三处，而课程配置页只能由
Zustand 的 `view` 驱动进入（`App.tsx` 明确让路由只区分三个独立入口、
不承担应用内导航），冒烟脚本按 hash 跳不进去。当前只由「静态守卫 + 纯函数单测」
兜住逻辑，**可见性验证放到批 A 收尾的 e2e**（需真机点进课程配置页）。

---

### 2.3 V6-2 · 前端「帮助 / 关于」入口

| 项 | 内容 |
|---|---|
| 需求 | 应用内入口，显示版本号、常见问题、日志导出位置 |
| 改动点 | 新增页面/抽屉 + 主进程 IPC（`shell.openPath(logDir)`）+ `preload.cjs` 暴露只读接口 |
| 版本号三源一致 | 页面显示值 ≡ `frontend/package.json` ≡ 安装包 `VersionInfo`；**已有** `frontend/tests/version-consistency.test.cjs` 把它扩成三源断言 |
| 日志目录 | `TINGKEBANLV_HOME`（默认 `~/.tingkebanlv`），与 `main.cjs` 现有 `logFilePath` 同源，**不要另算路径** |
| 验收 | ① 入口可见；② 版本号三处一致（机器断言，非人工核对）；③ 点击可打开日志目录（用 `shell.openPath`，失败要有提示而不是静默） |

**实施记录（2026-10-12）**

做成了**模态面板**（`HelpAboutPanel`）而不是新页面。理由：应用内的视图切换由
`useAppStore().view` 驱动（`App.tsx` 明确让路由只负责「三个独立入口」、不承担
应用内导航），为帮助页加一个 view 值要动类型联合 + `App.tsx` 的 switch +
一条「从哪儿进来往哪儿回去」的路径；而它只需要「随时能查一下」，模态不占导航位。

| 产物 | 内容 |
|---|---|
| `frontend/electron/main.cjs`（改 2 处） | destructure 补 `shell`；新增 `diag:get-info` 与 `diag:open-log-dir` 两个 handler |
| `frontend/electron/preload.cjs`（新增 `diag` 命名空间） | `getInfo()` / `openLogDir()`，**只读**，渲染层不做任何持久化 |
| `frontend/src/types/index.ts` + `types/electron.d.ts` | `DiagInfo` / `OpenLogDirResult` 与 `ElectronDiagAPI` |
| `frontend/src/lib/helpCopy.ts`（新增） | 全部可见文案 + `HELP_KEYS`（按位置引 `HOTKEYS[2]`）+ `versionLine()` / `openLogDirMessage()` 两个纯函数 |
| `frontend/src/lib/diagBridge.ts`（新增） | 永不抛的三层封装 |
| `frontend/src/components/HelpAboutPanel.tsx`（新增） | `role="dialog"` + `aria-modal` + `aria-labelledby`；Esc 关闭；失败提示 |
| `frontend/src/pages/CourseList.tsx`（改 2 处） | 标题栏新增入口（复用 `helpBtn` 以继承 `no-drag`）+ 条件渲染面板 |
| `frontend/tests/help-about.test.mts`（新增 16 条） | 文案审计、FAQ 结构、热键引用语义、`openLogDirMessage` 三分支、无障碍源守卫、桥接兜底、主进程返回值检查、审计变异 |
| `frontend/tests/version-consistency.test.cjs`（扩 3 条） | **界面显示链**（见下） |

**版本号「第五个出口」现在也被机器钉住了**：前四个出口都是文件之间的一致性；
而帮助页显示的值是「界面显示的值到底从哪来」。整链静态可验：

```
package.json → app.getVersion() → 'diag:get-info' → preload diag.getInfo
            → diagBridge.getDiagInfo → HelpAboutPanel [data-testid=help-about-version]
```

并额外断言两份界面源码里**不得出现任何 `x.y.z` 形态的字面量**
（写死就等于多了个不会被构建步骤改写的版本号出口，而用户恰恰是为了报版本号才打开这一页）。

**三条静默失败被显式处理**（都是「点了没反应」的类型）：

1. `shell.openPath()` **不抛异常**：失败时把错误信息当**字符串返回**、成功返回空串。
   写成 `await shell.openPath(dir)` 不检查返回值 = 失败时静默。现在映射成
   `{ ok, dir, error }`，界面把原因原样显示出来。
2. `openLogDirMessage()` 是纯函数且**把「没有 error」也判成失败**（`null` / `{}` /
   `error: '   '` 都走失败分支）。反向写法（「有 error 才算失败」）会让
   `{ok:false}` 显示成「已在文件管理器中打开」—— 比不报错更糟。
3. 路径**只由主进程算**。渲染层拿不到 `dataHome()`（它不是 Electron 的 `userData`），
   自己拼会指向一个空目录，用户照着找不到日志只会更困惑。

**又踩了一次「注释假失败」**：`version-consistency.test.cjs` 首次运行时，
新断言被 `HelpAboutPanel.tsx` 注释里**举例说明**的版本号写法弄红了。
已按既有做法**先剥块注释再扫**。这是本项目第三次踩同一个坑（`panel-copy`、
`course-config-hint`、这里），所以在两个测试文件里都留了同一条说明。

实测（本机 2026-10-12）：`node --test "tests/**/*.test.*"` → **205 通过 / 0 失败**
（189→205）；`tsc --noEmit` 0 错；`npm run build` 成功（1590 modules）；
`node --check` 三个文件通过。

---

### 2.4 V6-3 · 应用图标 v4.1

| 项 | 内容 |
|---|---|
| 需求 | 按 [`icon_asset_spec.md`](./icon_asset_spec.md) 用 Inkscape 绘制矢量图标并导出各尺寸 |
| 改动点 | `frontend/electron/`（图标资源）+ `scripts/icon_assets.py`（产物生成）+ `icon-draft/`（矢量源） |
| 既有守卫 | `backend/tests/test_icon_assets.py`（图标断言已存在，直接复用）；历史先例：`packaging/verify/report-icon-rehearsal-v4*.json` |
| 验收 | ① 各尺寸产物齐全且校验通过；② `test_icon_assets.py` 全绿；③ 安装后任务栏 / 开始菜单 / 卸载项三处图标一致（真机截图留档） |
| 风险 | **需要 Inkscape 手工绘制**，是本轮唯一无法纯代码完成的项。若时间紧，可**先只出 256/128/64/48/32/16 六档 PNG 覆盖打包需求**，矢量源补齐延后 |

---

### 2.5 D1 产品侧度量 · 繁体对术语高亮 / 生词本的影响

| 项 | 内容 |
|---|---|
| 现状 | D1 代码层已关闭（繁体 0/448），但**繁体字是否影响术语高亮命中与生词本收录，未量化** |
| 指标定义（需先定，否则不可测） | ① 命中率：字幕中「繁体形态的术语」被术语高亮命中的比例；② 入库率：用户点击繁体词后成功进入生词本的比例；③ 失败归因：未命中因为「词表是简体」还是「匹配算法不认」 |
| 改动点 | 前端埋点（只上报计数，不上报文本），或后端统计接口 |
| 验收 | 给出可复现的指标定义 + 一次基线测量（可用构造样本，不依赖真实用户）；结论写进 `known_defects.md` D1 的「关闭时对照」段 |
| 注意 | **不要**为了这个指标改识别链路——D1 已关闭，本项只是度量 |

**实施记录（2026-10-12，度量完成）**

落成 `backend/tests/test_d1_traditional_terms.py`（**6 条测试，无生产代码改动**）。文件头把
「为什么繁体已经归零了还要度量」写清楚了：简体引导语只保证**识别输出默认是简体**，
繁体仍从两条路径进来 —— ① 用户把「中文输出字形」切成 `simpMode=raw`（面向古文 / 繁体教材）；
② 用户在自定义术语里填繁体词（界面无简繁校验）。所以这不是「一个已修好的问题」。

指标定义（**先定死才可测**）：

| 指标 | 定义 |
|---|---|
| **M1 命中率** | 生词本里存简体形态时，「繁体形态的术语」在 `GET /vocabulary/{word}` 上返回 200 的比例 |
| **M2 入库率** | 「繁体形态的术语」经 `POST /vocabulary/{word}/collect` 成功入库的比例 |
| **M3 重复率** | 同一术语的简体与繁体形态并存时产生的额外条目比例 |
| **M4 失败归因** | 未命中是「词表里存的是简体」还是「匹配算法不认简繁」 |

样本 = 24 个原生术语（**从 `scripts/ab_terms_compare.py` 的 `COURSE_TERMS_ORIGIN` 动态加载，
不复制副本**，避免两份清单漂移后断言仍在自己那份过期副本上全绿）+ 2 条真实观测到的
「字级混入」形态（取自 `testset/chinese_lecture/real/*.corrections.tsv` 的 `图表繪制` / `橫坐标`）。
清单与映射表**双向对拍**：清单加词而映射表没跟上会直接红。

**实测基线（本机，2026-10-12）**：

```
[D1 度量] 样本 24 个 = 有简繁差异 22 个 + 简繁同形 2 个['厘米', '毫米']
  M1a 命中率（有差异的样本）= 0/22 = 0.0000
  M1b 命中率（同形样本，对照组）= 2/2  = 1.0000
  M2  入库率（全部样本）        = 24/24 = 1.0000
  M3  重复条目 = 生词本 46 条 vs 术语 24 个 → 额外 22 条（0.9167）
```

**归因（M4，这是整组里唯一能定位责任方的一条）**：同一份代码、同一个查询，
**只换**库里那条记录的字 —— 库里存简体则查简体命中 / 查繁体 404，库里存繁体则查繁体命中 /
查简体 404，两边**完全对称**。⇒ 查词算法既不认简体也不认繁体，它只做**逐字相等比较**；
未命中的责任方是**库里那个字符串的形态**，不是「匹配算法不支持简繁」。

三条结论：

1. **繁体形态从不命中**（22/22 未命中）——不是因为算法坏，是因为词表存的是简体，逐字比较必然不等；
2. **简繁同形的词全部命中**（2/2，对照组）⇒ 证明「不命中」的确由字形差异造成，
   而不是查词链路整体失灵（对照组失效则 M1 的结论不成立，所以它是断言而非装饰）；
3. **入库完全不受影响**（24/24），但会**产生重复条目**（同一术语的简繁两形态各占一条，
   本样本下 24 → 46 条）。

两条**踩坑记录**（都是第一版写错、被自己的断言抓出来的）：

1. **首版断言 `m1 == 0.0` 直接失败（实际 2/24）** —— 那 2 个命中正是 `厘米` / `毫米` 这两个
   **简繁同形**的词，本该命中。把两组混在一起算总命中率会掩盖整件事（8.3% 看起来像
   「有 8% 能命中」）。改为拆两组：`m1_diff == 0.0`（实验组）与 `m1_same == 1.0`（**对照组**）。
2. **M4 首版在同一课程里先收简体再收繁体** ⇒ 两条记录并存 ⇒ 两个方向都命中 ⇒
   把「对称性」这个**唯一结论**糊掉。改为**两门各自新开的课程**分别验证两个方向。

⚠️ `test_conclusion_premise_change_must_surface` 是**文档同步报警器**，不是缺陷锁定。
若将来有人补上简繁归一化（那是好事），这几条会红 —— 红的含义是「请更新
`known_defects.md` D1 与本文档 §2.5，并把度量改写成归一化生效后的版本」，
**而不是删断言让它变绿**。

---

## 3. 批 B 执行卡（评估侧，不改包）

### 3.1 V6-5 · 跨会话基准加固（D3）

**现状**：7 格中 **4 格跨会话不可复现**，实测最大相对变化 **+64.3%**。

| 可对拍 ✅ | 不可对拍 ❌ |
|---|---|
| noise 0.1821 · lowvol 0.1715 · real 0.1985 | clean 0.1662→0.1504 · fast 0.2586→0.4248 · multi 0.6834→0.6675 · long 0.2589→0.3053 |

**要做的**：对 7 格各跑**同代码同会话重复 N 次**，把 `WER_REPORT_PLAN.md` §7 的单元格从「单值」改成
「**中位数 [min, max]**」，并标注该格的**内禀散布**。

**N 的取值与成本（建议，需用户裁决）**

| 格 | 单次成本 | 建议 N | 小计 |
|---|---|---|---|
| clean / noise / lowvol / fast / multi | 短样本（秒级~分钟级） | **5** | 可接受 |
| long | ≈10.5 min | **3** | ≈32 min |
| real | ≈10.5 min | **3** | ≈32 min |

```bash
# 单格单次（示例：clean）
<venv>/Scripts/python.exe scripts/eval_asr_quality.py \
    --audio-dir testset/chinese_lecture/scenarios/short \
    --realtime --language zh --terms on \
    --report packaging/eval/v6_repeat_clean_<i>.json
# 七格命令同 §16.3 口径；跑完把中位数与极差抄进 WER_REPORT_PLAN.md §7
```

**验收**：§7 表能回答「这个数字的波动范围是多少」；N 次原始产物可溯源（文件名带序号）；
**跨会话仍然不设阈值**这条纪律写进 §7 表头。

**可选加强（择一）**：定位 `condition_on_previous_text=True` 的自强化是否为波动来源
（D3 §四 疑点，未证实）——做一组 `condition_on_previous_text` on/off 对照即可，成本低。

### 3.2 V6-6 · 长样本恒等式覆盖（D2 残留）

| 项 | 内容 |
|---|---|
| 现状 | 恒等式 `latency_新 − latency_旧 == audioEnd − audioWordsEnd ≥ 0` 只在 `lowvol` / `multi`（6→6）验过，各 6/6 残差 0；`long`（56→100）/ `real`（63→86）因 **1:N 结构原理上不可校验** |
| 要做的 | 建立「**whisper 段 → 所属 VAD 块**」的**时间包含映射**，让恒等式对 1:N 格也成立 |
| 必须先定义的规则 | 一个 VAD 块内含多段时，**块尾静音归给哪一段**；跨块边界的段如何归属 |
| 改动点 | `scripts/eval_asr_quality.py`（🔴 **CRLF 例外文件**，改它必须走补丁脚本 + 复验 `lone_lf=0`） |
| 验收 | `long` / `real` 也能给出**恒等式条数与最差残差**；映射规则有**单测 + 变异探针**（证明改坏会红） |

---

## 4. 批 C 执行卡（待外部输入）

### 4.1 V6-4 · `accent` 口音素材

**素材入口已全部就绪**（V5-7 交付）：`testset/chinese_lecture/accent/README.md` 写明了
采样率、时长、命名规则、参考稿要求、`metadata.json` 模板与运行命令。

**🔴 唯一卡点：需要用户提供**

| 需要的信息 | 示例（参照已有 `real` 格） |
|---|---|
| BV 号 | `BV1BA411i7PT` |
| 起止时间 | `00:40~11:10`（60~130 s 为宜，对齐短样本集） |
| 参考稿来源 | 官方字幕 / 讲稿优先；无则人工预校对成 `*.preproof.txt` |

**执行（三条命令，README §二~§三 已写明）**

```bash
# 1) 确认视频信息与时长（不下载）
<venv>/Scripts/python.exe scripts/fetch_bili_audio.py <BV号> --start 0 --end 90 --dry-run
# 2) 拉到素材目录，名字里带 accent
<venv>/Scripts/python.exe scripts/fetch_bili_audio.py <BV号> --start MM:SS --end MM:SS \
    --name accent_zhlecture_<bvid> --out-dir testset/chinese_lecture/accent
# 3) 校验素材就位 → 真跑（自动加 --realtime 与 --source-kind human-recording）
<venv>/Scripts/python.exe scripts/run_accent_baseline.py
<venv>/Scripts/python.exe scripts/run_accent_baseline.py --run
```

**验收**：`packaging/eval/p2_<n>_rt_accent.json` 产出；`WER_REPORT_PLAN.md` §7.1 / §7.2 的
`accent` 行填上真实数字；**不进安装包**（`testset/` 安全）。

**口径纪律**：`accent` 格的定位是**如实报告**——CER 预期**高于 clean，这不是缺陷**。
它要拦的是**变化（回归）**，不是绝对值。

> 文档小一致性问题：`accent/README.md` 第 6 行仍写「backlog_v350.md **V5-7**」，
> 编号已改为 **V6-4**，顺手更新。

---

## 5. 发布门禁（发布 v3.6.0 前必须全绿）

| # | 门禁 | 命令 / 口径 | 通过判据 |
|---|---|---|---|
| **C-1a** | 全量测试 | `pytest backend/tests/ -q` | 通过数 ≥ 445，**失败 0**，仅允许既有 1 skip |
| **C-1b** | 变异探针 | `packaging/verify/probe_*.py`（9 个探针） | **全部捕获**（基线 22 条 + 本轮新增） |
| **C-1c** | 前端 | `npm --prefix frontend run test` + `npm --prefix frontend run build` | `tsc` 无错、前端测试全绿 |
| **C-2** | 七格识别 | §16.3 口径，7 格 × {`zh`, `auto`}，`--realtime` | 七格全通过；`zh` 四格锚点**逐位复现**；`auto` 与 `zh` **提示词逐项同构** |
| **C-3** | 真机 e2e | 安装段 + 运行时 E1~E4 + 内容层 C1~C4 + **C5（V6-1 提示条可见性）** + **C6（V6-2 帮助面板）** | 全部 OK（基线 21 条 + C5 + C6）；繁体 **0**；低置信正确降级 |
| **C-4** | L4 安装包验收 | `scripts/verify_install.py --installer <setup.exe> --hold 150 --expect-sha256 <hex> --report <out>` | **连跑 3 次全通过**（D6 修好后不应再出现阻断；若 V6-7 未修则此门禁不适用） |
| **C-5** | 双副本对拍 | `_mirror_sync.py` → `_mirror_check.py` | `diff=0 miss=0`（基准 `git ls-files`，**不能用 `find`**） |
| **C-6** | 换行符 | 逐文件核对 | 3 个 CRLF 例外保持 CRLF，其余 **loneLF 正常 / CRLF 0** |

**C5 的定义（V6-1 引入，2026-10-12）**：真机点进课程配置页，断言
① `[data-testid="course-config-hint"]` 存在且可见；② 三项文案都在 DOM 里；
③ 点关闭后元素消失；④ 重启应用后再进本页，元素**不再出现**。
为什么必须真机做：`smoke-overlay.cjs` 只能按 hash 跳转，而课程配置页由
Zustand 的 `view` 驱动（`App.tsx` 有意让路由只区分三个独立入口），冒烟脚本
进不去这一页 —— 所以「渲染出来没有」目前**没有任何自动化覆盖**，只能靠 e2e。

**C6 的定义（V6-2 引入，2026-10-12）**：真机点开帮助面板，断言
① `[data-testid="help-about-panel"]` 出现；② `[data-testid="help-about-version"]`
的文本 **≡ 安装包版本**（`app.getVersion()` → 即 `package.json` / 安装包文件名里的版本，
这一条把「界面显示链」从静态断言升级成真机证据）；
③ `[data-testid="help-about-logdir"]` 非占位值、且路径**存在**；
④ 点「打开日志目录」后，成功则资源管理器打开该目录，失败则
`[data-testid="help-about-notice"]` 必须可见并带原因 —— **不允许两种都没有**。

### 打包与冻结流程（批 A 完成后执行）

| 步 | 动作 | 校验点 |
|---|---|---|
| 1 | 版本号 3.5.0 → 3.6.0 | **四个出口**：`frontend/package.json` `version`、`backend/main.py` `APP_VERSION`、`frontend/package-lock.json` **第 3、9 行**（⚠️ 第 340/341 行的 `@electron/asar@3.4.1` **绝不能改**） |
| 2 | 重建冻结后端 | PyInstaller；记下 `tingkebanlv-server.exe` 大小与 md5 |
| 3 | 重建前端 dist + 打包 | `npm run electron:build`；记下 exe 大小 / md5 / sha256 |
| 4 | 归档 v3.5.0 | 移入 `安装空间/_frozen_v350/`（保留 MANIFEST），确认 md5 `f96dc049…` **未变** |
| 5 | 归档 v3.6.0 | 新建 `_frozen_v360/` + `MANIFEST.json`，跑归档校验（`archiveVerify.allMatch` 应为 true） |
| 6 | 包内冒烟 | 引擎响应 / `version=3.6.0` / `preview` 不传 language ≡ 显式 `auto` / `zh` 繁体 0 |
| 7 | 跑 C-1 ~ C-4 | 全部通过才继续 |
| 8 | 文档 + 提交 + tag | `known_defects.md`（D6 关闭）+ `WER_REPORT_PLAN.md` §17 + `backlog_v350.md` §十四 + 本方案；commit 后 `git tag -a v3.6.0` |

🔴 **禁止事项**
- 不改 v3.4.1 / v3.5.0 的已冻结交付物（任何改动都会作废其 L4 与 e2e 结论）；
- 不把 `packaging/eval/` 转为跟踪；
- 不「归一化」换行符。

---

## 6. 风险登记

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | V6-7 修法 b 在打包环境行为异常 ⇒ 双实例正常化 | 高（比原缺陷更糟） | 实验② 作为闸门；不通过回退 a + 自激防护 |
| R2 | 改 `frontend/` 后 L4 出现**新的**非预期阻断 | 中 | 阻断项必须逐条归因；**不能**再用「既有缺陷」当挡箭牌 |
| R3 | D3 波动使 C-2 出现「假失败」 | 中 | 判据只用同会话对照 + 极差；不可对拍的格不记失败 |
| R4 | V6-3 图标手工绘制超期 | 低 | 先出 6 档 PNG 满足打包，矢量源延后 |
| R5 | V6-6 改 `eval_asr_quality.py` 破坏 CRLF 例外 | 中 | 走补丁脚本 + 复验 `lone_lf=0` |
| R6 | 历史口径混淆：把 v3.4.1 的「L4 0 阻断」当基准 | 中 | 该基线的降级路径**一次都没被走到**，与 v3.6.0 不同口径，**不可直接对比** |

---

## 7. 需要用户决策的 5 个点

| # | 决策 | 我的建议 |
|---|---|---|
| **1** | V6-7 修法选 **b**（先释放锁再 spawn）还是 **a**（子进程带 `ALLOW_MULTI`） | 选 **b**。a 会让锁无人持有，用户再双击就出双实例 |
| **2** | V6-3 图标本轮做还是延后 | 有时间就做，但**不阻塞打包**（可先出 PNG 六档） |
| **3** | V6-5 的 N：`long` / `real` 用 N=3 还是与短样本统一 N=5 | 短样本 N=5、`long`/`real` N=3（单次 ≈10.5 min，N=5 要 53 min/格） |
| **4** | V6-4 的 **BV 号 + 起止时间** | 🔴 只有你能给；给了就能立刻跑通 |
| **5** | 是否**顺手更正** v3.4.1 的 L4 口径记录 | 建议在 §16.6 加一行说明「历史基线是在未走到降级路径时取得的」，避免后人误引 |

---

## 8. 执行顺序建议（按依赖，不按日历）

1. **V6-7** 改 + 3 个副作用实验 + 单测/探针 → 单项验收
2. **V6-1 / V6-2 / V6-3 / D1 埋点** → 各自单测
3. **打包 v3.6.0** → 冒烟
4. **跑门禁 C-1 ~ C-4** → 全绿
5. **批 B**（V6-5 / V6-6）→ 结论写进 §7
6. **批 C**（V6-4）→ 等 BV 号，随时插队
7. **文档 + 副本对拍 + 提交 + tag `v3.6.0`**

> 批 B 与批 C **不产生新包**，因此可以在批 A 打包前后任意穿插，
> 唯一要求是：**结论必须落进仓库内的 md**。
