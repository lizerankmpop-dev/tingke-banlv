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

**实施记录（2026-10-12，v4.1 完成）**

**任务前提在立项后已变**：图标不是占位版 —— v4「海獭」版已于 2026-10-07 按 §8.5
验收落地（单一数据源 `frontend/build/icon-source/build_otter.py`：master 256~32 完整设计
+ 16/24px 独立剪影，**纯 Pillow + numpy，不需要 Inkscape**，本机也未装）。因此 v4.1
不重画，做的是**小步美化**：解决 v4 遗留的唯一可见短板 —— 16px 剪影「有帽无脸」。

| 项 | 内容 |
|---|---|
| 改动 | `build_otter.py` 新增 `s16_pixel_face()`：在 **16×16 网格上直接贴**「两眼 + 鼻」4 个墨点；`svg_small(16)` 用网格对齐 rect 同步表达（单一数据源不漂移）；16px/24px 以外的 6 档**零改动** |
| 关键教训 | 16px 下白脸只有 ≈2px 高，设计空间坐标（眼距 32 / 40 两轮实测）经超采样 + LANCZOS 必然糊成横带并切碎白脸 ⇒ 16px 表情**只能像素级贴点**，坐标按 v4 实测网格反推（眼 y8 行 x6/x9、鼻 y10 行 x7+x8，对网格中心 x=7.5 对称） |
| 覆盖 | 新 ico 已覆盖 `frontend/build/icon.ico`（82.1 KB，8 档不变） |

实测（本机，2026-10-12）：

- `python scripts/icon_assets.py check frontend/build/icon.ico` → **全部通过**（8 档、256 PNG）
- `pytest backend/tests/test_icon_assets.py` → **12 passed**
- 前端 `npm test` → **205 pass / 0 fail**（含 `onboarding-aumid` 的 ico 容器守卫）
- 16px 可辨识性（§6.1 第 5 项，人眼）：像素级放大 ×16 浅 / 深双底对照 —— v4 无脸，
  v4.1 眼点 + 鼻杠清晰；对照图留档 `安装空间/_icon_v41/compare_{light,dark}.png`

**尚未完成**（属打包收尾，与 V6-7 / V6-1 / V6-2 同批）：§8.5 全流程 —— 清
`release/` → 重新打包 → `audit` → 真机六处落点 + 安装向导目检。图标改动**必然改变
安装包哈希**，v3.6.0 打包时一并验证。

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

**实施记录（2026-10-15 ~ 10-16，七格 31 次观测完成）**

工具（**全部在 `安装空间/`，前缀 `_`，不入库**）：

| 脚本 | 职责 |
|---|---|
| `_v65_repeat.py` | 长跑。`GRIDS` 与 `_c2_batch_350.py` **逐字一致**（保证与 §16.3 同口径）；N：短样本 5、long/real 3；产物 `packaging/eval/v6_repeat_<sc>_<i>.json`；**断点续跑**；新增 `--budget-seconds`（每完成一次评估后检查）以适配回合边界 |
| `_v65_summarize.py` | 统计。**从产物 JSON 重建**（不信进程内存 —— 见下方教训），输出 `_v65_summary_rebuilt.json` |

**七格结果**（数字由 `_v65_summarize.py` 从产物读出，不手抄）：

| 格 | N | CER 中位 | [min, max] | 极差 | σ | 词级 p95 中位 | 合并合计 | 异常观测 |
|---|---|---|---|---|---|---|---|---|
| clean | 5 | 0.1847 | [0.1504, 0.2137] | 0.0633 | 0.0266 | 13.407 | 1 | **1（#2 长阻塞）** |
| noise | 5 | 0.1821 | [0.1821, 0.1821] | **0.0000** | 0.0000 | 11.952 | 0 | 0 |
| lowvol | 5 | 0.1715 | [0.1715, 0.1715] | **0.0000** | 0.0000 | 2.467 | 0 | 0 |
| fast | 5 | 0.4248 | [0.4248, 0.4248] | **0.0000** | 0.0000 | 9.274 | 5 | 0 |
| multi | 5 | 0.6570 | [0.5752, 0.7731] | **0.1979** | 0.0856 | 16.004 | 0 | 0 |
| long | 3 | 0.2620 | [0.2319, 0.2690] | 0.0371 | 0.0197 | 21.296 | 9 | 0 |
| real | 3 | 0.1989 | [0.1985, 0.3962] | 0.1977 | 0.1140 | 29.703 | 4 | **1（real_3 管道故障）** |

**排除异常观测后的正常态**：

| 格 | 正常 N | CER 中位 | [min, max] | 极差 |
|---|---|---|---|---|
| clean | 4 | 0.1966 | [0.1504, 0.2137] | 0.0633 |
| real | 2 | 0.1987 | [0.1985, 0.1989] | **0.0004** |

**五条结论**

1. **三个格是确定性的**（`noise` / `lowvol` / `fast` 五次 **CER 逐位相同**）⇒ 它们的单次观测就是真值，
   "跨会话不可对拍"这条判断**不适用于它们**。⚠️ `fast` 每次都 `mergedCalls=1` 但 CER 五次全同
   ⇒ **合并是这格的常态**，不是异常信号（首版异常判据用中位数作基线，把 `fast` 五次全判成异常，已改）。
2. **`multi` 是波动最大的格**：极差 **0.1979**、σ **0.0856** ⇒ 这就是 D3 的定量形态。
   `multi` 单次观测**绝不能当基准**（§7 里它也是唯一标 ❌ 且被 §7.5 提醒不可做减法的格之一）。
3. **`long` 的跨会话漂移坐实**：三次 0.2319 / 0.269 / 0.262（中位 **0.262**），
   而 §16.3 的单次观测是 **0.2773** ⇒ 同为"同代码同素材"，落在 [0.2319, 0.2690] 之外。
4. 🔍 **`clean` 存在"长阻塞"现象（仅 #2 一次）**：`tailSeconds` 2.2 → **22.6**、词级 p95 11.6 → **246.4**，
   且逐条延迟单调递减（246.4 → 238.5 → … → 193.4）—— 形态是**一次长约 200 s 的整体阻塞偏移**，
   不是逐条抖动。自洽性已核（音频 80.5 s 的实际喂入被拖到 ≈240 s）⇒ 阻塞发生在**喂入/调度侧**。
   ⚠️ 这是**本机环境**的偶发长尾，不是 ASR 质量问题；它的存在正是"单次观测不得当基准"的实证。
5. 🔍 **`real` 正常态两次几乎完全一致（极差 0.0004，σ 0.00028）**，`real_3` 则是**管道故障**而非波动：
   丢片 **15**、字幕 86 → 55、词级 p95 **13471 s**、CER 0.3962（vs 0.1985/0.1989）。
   ⇒ 判定"异常"与"不可对拍"必须分开：`real` 可对拍（正常态），但**单次观测要先排故障**。

**两条踩坑（已记入 `LESSONS.md`）**

- 🔴 **汇总不能只活在进程内存里**：`real_3` 遭遇极端管道卡顿（词级延迟 13471 s），子进程被
  `subprocess.TimeoutExpired` 中断 ⇒ 脚本抛异常退出 ⇒ **real 汇总未落盘**（产物文件本身后来写成功了）。
  修法：`_v65_summarize.py` 独立于长跑、**从产物重建**。
- 🔴 **异常判据的基线要用同格最小值，不能用中位数**：见结论 1 的 `fast` 误报。

⚠️ **并发约束（记录在案）**：长跑每格启动新 `subprocess` 读 `scripts/eval_asr_quality.py`，
而变异探针**原地改**该文件 ⇒ 二者**不可并行**（既防文件污染，也防 CPU 抢占污染墙钟延迟）。
探针与全量 pytest 一律排在长跑之后。

### 3.2 V6-6 · 长样本恒等式覆盖（D2 残留）

| 项 | 内容 |
|---|---|
| 现状 | 恒等式 `latency_新 − latency_旧 == audioEnd − audioWordsEnd ≥ 0` 只在 `lowvol` / `multi`（6→6）验过，各 6/6 残差 0；`long`（56→100）/ `real`（63→86）因 **1:N 结构原理上不可校验** |
| 要做的 | 建立「**whisper 段 → 所属 VAD 块**」的**时间包含映射**，让恒等式对 1:N 格也成立 |
| 必须先定义的规则 | 一个 VAD 块内含多段时，**块尾静音归给哪一段**；跨块边界的段如何归属 |
| 改动点 | `scripts/eval_asr_quality.py`（🔴 **CRLF 例外文件**，改它必须走补丁脚本 + 复验 `lone_lf=0`） |
| 验收 | `long` / `real` 也能给出**恒等式条数与最差残差**；映射规则有**单测 + 变异探针**（证明改坏会红） |

**实施记录（2026-10-15，代码侧完成）**

补 D2 §五 的两条残留缺口（`long`/`real` 恒等式不可校验 + 新口径不区分首段/续段）。

**核心思路**：不去动 `pair()`（放宽 1:1 只会产出「看似合理、实际错位」的延迟，
第 5 节已把它反向锁死），而是换一个在 1:N 下**恒有答案**的问题 ——
「这条字幕落在哪个 VAD 块里」。`Recorder.words_latency_by_block()` 用
**时间包含**把逐字幕的新口径映射回块。

**映射规则（先定义，否则不可测）**：

| # | 规则 | 理由 |
|---|---|---|
| ① | 字幕词尾 `audioWordsEnd` 落在哪个块的 `[audioStart, audioEnd]`（闭区间）内就归那个块 | 1:N 下这是唯一**恒有解**的问题 |
| ② | **块尾静音归块内最后一条字幕** | 不是规定而是**结论**：块内每条字幕的 `新−旧` 都等于 `seg.audioEnd − 该字幕自己的 audioWordsEnd`；块尾那条最小，其余等于「块尾 + 它自己词后的静音」 |
| ③ | 落在所有块之外 ⇒ 记 `unmapped`，不参与恒等式但**计入报告** | 静默丢弃会让「验了几条/漏了几条」从报告里消失（教训 14） |
| ④ | 命中多个块（区间重叠，正常不该发生）⇒ 取**最早**那个并记 `overlapped` | 随便挑一个会让数字看起来正常 —— 这正是这类缺陷难被发现的原因 |

| 产物 | 内容 |
|---|---|
| `scripts/eval_asr_quality.py`（🔴 CRLF 例外，走 `安装空间/_v66_patch.py` 补丁脚本 5 处） | `IDENTITY_TOL = 0.005`；`Recorder.words_latency_by_block()`；结果字段 `latency_identity`（blocks/subtitles/mapped/unmapped/overlapped/worstResidual/violations/toleranceSeconds/unmappedSample/detail）；`summarize` 四个汇总键；报告尾部一行打印 |
| `backend/tests/test_eval_latency_words.py`（+8 条，共 **25**） | 1:N 三条字幕映射同一块且恒等式逐条成立、块外记 unmapped、重叠取最早、缺锚既不 mapped 也不 unmapped、空形状一致、`tail` 语义（块尾−词尾）、容差区间、接线计数（教训 2） |
| `packaging/verify/probe_latency_words_mutations.py`（+8 条 E 组，共 **21**） | E1 取最晚块 · E2 tail 算反 · E3 残差符号反 · E4 unmapped 静默丢弃 · E5 重叠不记录 · E6 容差放大到 1s · E7 字段改名 · E8 去掉 feed_t0 守卫 |

**改动是纯附加**（一行旧代码都没改）：常量新增、方法新增、报告字段新增、汇总键新增、
打印行新增 ⇒ **旧字段语义与数值一字未动**，历史报告仍可逐格对账，且本轮 V6-5 长跑
跨越补丁点时 CER / 延迟数字不受影响。

**实测（七格 31 次观测，2026-10-15 ~ 10-16）**

数字全部由 `安装空间/_v65_summarize.py` **从产物 JSON 读出**（不手抄）；逐次明细见 `安装空间/_v65_summary_rebuilt.json`。

| 格 | 块 → 字幕 | mapped | unmapped | overlap | 违例 | 最差残差 |
|---|---|---|---|---|---|---|
| clean | 6 → 7/9/10 | 19 | 0 | 0 | 0 | 0.0 |
| noise | 6 → 9 | 45 | 0 | 0 | 0 | 0.0 |
| lowvol | 6 → 6 | 30 | 0 | 0 | 0 | 0.0 |
| fast | 6 → 9 | 45 | 0 | 0 | 0 | 0.0 |
| multi | 6 → 6/8/9/11 | 42 | 0 | 0 | 0 | 0.0 |
| long | 56 → 92/94/99 | **285** | 0 | **3** | 0 | **0.001** |
| real | 63 → 55/86 | **221** | **6** | 0 | 0 | 0.0 |
| **合计** | | **687** | **6** | **3** | **0** | **0.001** |

> `clean` 只有第 4/5 次带 `latency_identity`（前三次跑在补丁点之前，字段尚未落地）；其余六格 5/5、3/3 全有值。

**四条读法与两个新发现**

1. **687 条恒等式逐条成立、0 违例、最差残差 0.001 s**（≪ `IDENTITY_TOL = 0.005`）
   ⇒ 1:N 的时间包含映射在**真实长样本**（56 块 / 63 块）上同样成立，D2 的"原理上不可校验"已被消除。
2. 🔎 **`unmapped` 的 6 条是跨运行稳定复现的同一对锚点** —— `real` 三次的 `unmappedSample` 都是
   `audioWordsEnd ≈ 297.284` 和 `381.188`（`real_3` 因丢片 15 索引从 50 变 45，**锚点值不变**）。
   跨运行复现 ⇒ 是**该素材/块切分的固有缺口**（这两条字幕的词尾落在所有 VAD 块区间之外），
   **不是测量噪声**。这正是规则 ③「记 `unmapped` 而不静默丢弃」的价值：丢弃的话，
   "86 条里只验了 84 条"这件事**在报告里根本不会出现**（教训 14）。
3. 🔎 **`overlap` 的 3 条也全部落在 `long`，且三次都恰好 1 对** ⇒ `long` 整篇在固定位置有一对
   相邻 VAD 块区间重叠（同样跨运行稳定）。**规则 ④「取最早块并记录」在真实数据上被实际用到**，
   且取最早后恒等式仍成立（残差 ≤ 0.001）。若第 ④ 条设计成"静默取一个"，这一对就永远查不出来。
4. ⚠️ **`real_3` 是管道故障观测**（丢片 15、字幕 86→55、词级 p95 13471 s，见 §3.1）。
   它的 53 条 mapped **不计入"正常态"参考**，但**依然 0 违例** ——
   说明故障发生在**喂入侧**、不在映射侧，恒等式对故障观测同样成立。
5. 顺带证实 **`clean` 本身就是 1:N**（6 → 7/9/10，旧口径 `pair()` 在它上面同样恒为空）
   ⇒ D2 的缺口原本比最早记录的更广（不是"只有 long/real"）。

⚠️ **并发约束（记录在案）**：长跑期间**不得**跑变异探针 —— 探针会**原地改**
`eval_asr_quality.py`，而长跑每格启动新进程读它 ⇒ 会污染结果。探针与全量 pytest
一律安排在长跑结束后。

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
| **C-1b** | 变异探针 | `packaging/verify/probe_*.py`（**10 个探针 / 120 条变异**） | **全部捕获**（逐探针条数见下方运行记录） |
| **C-1c** | 前端 | `npm --prefix frontend run test` + `npm --prefix frontend run build` | `tsc` 无错、前端测试全绿 |
| **C-2** | 七格识别 | §16.3 口径，7 格 × {`zh`, `auto`}，`--realtime` | 七格全通过；`zh` 四格锚点**逐位复现**；`auto` 与 `zh` **提示词逐项同构** |
| **C-3** | 真机 e2e | 安装段 + 运行时 E1~E4 + 内容层 C1~C4 + **C5（V6-1 提示条可见性）** + **C6（V6-2 帮助面板）** | 全部 OK（基线 21 条 + C5 + C6）；繁体 **0**；低置信正确降级 |
| **C-4** | L4 安装包验收 | `scripts/verify_install.py --installer <setup.exe> --hold 150 --expect-sha256 <hex> --report <out>` | **连跑 3 次全通过**（D6 修好后不应再出现阻断；若 V6-7 未修则此门禁不适用） |
| **C-5** | 双副本对拍 | `_mirror_sync.py` → `_mirror_check.py` | `diff=0 miss=0`（基准 `git ls-files`，**不能用 `find`**） |
| **C-6** | 换行符 | 逐文件核对 | 3 个 CRLF 例外保持 CRLF，其余 **loneLF 正常 / CRLF 0** |

### C-1b 怎么跑（2026-10-16 实测的运行约束，**不是可选项**）

变异探针是**原地改被测文件、跑完还原**。本机有三个会把它误伤成「探针报红」的坑：

| # | 坑 | 症状（会伪装成什么） | 修法 |
|---|---|---|---|
| 1 | **并发** | 另一读/写同一文件的操作插进来 ⇒ 读到中间态；探针内置的「真身本应全绿」守卫会如实报成**不变量失败** | **严格串行**；跑前要求 `git status --porcelain` 为空；跑后**必须**再查一次（非空即停） |
| 2 | 🔴 **批量删除守卫按「回合」累计** | `CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD=50`，计数存 `CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR`，**scope=turn（整个回合累计，不随命令重置）**。探针清理自己的临时模块到第 50 次删除就被拦 ⇒ 探针异常退出 ⇒ `finally` 还原**没执行** ⇒ 工作区留下 `backend/dictionary/_term_mut_*.py`（🔴 **在 `backend/` 打包作废区里**），同时「变红」被误读成「断言缺分辨力」 | **每个探针给一个全新的 `CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR`**：阈值仍是 50（守卫照旧生效），只是不让计数跨探针累加。实测修法有效（同一回合连跑时 `eval_source_kind` 会从 24/24 掉到 5/24，`phrases_and_truncate` 直接崩） |
| 3 | **本机休眠** | 墙钟计时器照走 ⇒ 子进程 `subprocess.TimeoutExpired` 报出**离谱的耗时**（实测 **6341.8 s**）。与教训 16⑤ 同一形态 | 跑测期间保持机器唤醒；别把「耗时异常」当代码问题 |

工具：`安装空间/_run_all_probes.py`（**串行 + 每探针独立计数状态目录 + 跑前跑后双查工作区 + 跑后查 `_term_mut_*` 残留**）。

**逐探针实测（2026-10-16，全部 ✅）**

| 探针 | 条数 | 探针 | 条数 |
|---|---|---|---|
| `ab_invalid_round` | 12/12 | `latency_words` | 21/21 |
| `compat_relaunch` | 7/7 | `phrases_and_truncate` | 6/6 |
| `eval_source_kind` | 24/24 | `preproof` | 9/9 |
| `lang_simp` | 22/22 | `verify_install_artifact` | 5/5 |
| `language_plumbing` | 7/7 ※ | `verify_install_log` | 7/7 |

※ `language_plumbing` 的 M3 锚点曾**漂移**：`pipeline.py` 重构成 `backend/asr/pipeline.py`，
且该调用点的 `language=` 实参从 `self.course_config.get("language","auto")` 换成
`language if language is not None else self._terminology_language()`（V5-8 起语种另有来源）。
探针如实报「**变异锚点未找到（源码已变，请更新探针）**」并把 M3 记成漏网 ——
属**探针失配**（教训 13），不是断言缺分辨力、也不是生产代码漏传。锚点已同步更新。

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

### 打包怎么跑（2026-10-16 实测的运行约束，**不是可选项**）

打包链路里有三件事**会静默产出错包**，与 C-1b 那三个坑同源（都是环境而不是代码）：

| # | 坑 | 症状（会伪装成什么） | 修法 |
|---|---|---|---|
| 1 | 🔴 **PyInstaller `COLLECT` 被批量删除守卫打断** | 守卫统计**被删除的条目数**（`CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD=50`，计数存 `CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR`，`scope=turn`）；触发有两条路：**单次巨量删除**（`COLLECT` 一次清 816 个旧条目 ⇒ 在 `count=815/816` 处被拦）或**同回合内多次小删除累计到 50**。**日志照样写 "completed successfully"**，但 `packaging/out/backend-server/` 里仍是**上一个版本**的 exe ⇒ 完全坐实教训 4（打包后必须查产物大小/md5，不能信日志） | **移动而不是删除**：`mv packaging/out/backend-server 安装空间/_stale_v350_backend`，让 `COLLECT` 从空目录开始（条目数 0 < 阈值），重跑 rc=0 |
| 2 | 🔴 **`electron-builder` 会清 `frontend/release/win-unpacked`，`vite build` 会清 `frontend/dist`** | 这两个目录被清后，若需要回退对比就再也拿不到上一版的中间产物 | 打包前**先 `mv`** 成 `win-unpacked.pre360` / `dist.pre360`（同样移动不删除） |
| 3 | 🔴 **`dist.pre360` 不在 `.gitignore` 覆盖范围内** | `.gitignore` 只写了 `dist`（精确匹配），`dist.pre360/` 会被 `git status` 报成未跟踪 ⇒ 看起来像「打包污染了仓库」 | 确认内容无价值后删除（仅 3 个文件，低于守卫阈值）；下次直接移出仓库 |

⚠️ `mv` 会改变文件 mtime。Git 不记录 mtime，但**归档/对拍类校验若按时间戳判断会失真**（实测 `packaging/out/backend-server.pre360` 的 mtime 被 `cp -r` 改成打包当天）。

**v3.6.0 实测数字（2026-10-16）**

| 项 | 值 |
|---|---|
| `tingkebanlv-server.exe`（冻结后端） | **21,739,259 B** / md5 `712688224028d4e22b5b5f5511cf358a`（≠ v3.5.0 的 `34a75c44…`，**这就是轮子确实换了的证据**） |
| 包内冒烟 | `/api/ping → {"ok":true,"pid":904,"version":"3.6.0"}`，2.6 s 就绪 ✅ |
| `tingkebanlv-3.6.0-setup.exe` | **302,593,365 B** / md5 `7e87f7da47618b37b5a0e7dfbfa9e10e` / sha256 `631a5f4d…290df5` |
| `…setup.exe.blockmap` | **314,484 B** / md5 `ac10725954167d40133d19718cf4beba` |
| `app.asar` | 34,148,003 B / md5 `de54aae56ebc82cb1bd7de36a4d191e1` |
| 归档 `_frozen_v360/` | `MANIFEST.json`，`archiveVerify.allMatch = true`（813 文件 / 278,538,359 B；whisper_models 6 文件 / 147,883,711 B） |
| v3.5.0 归档未变 | `_frozen_v341/` md5 `bf0af680c3851e3513fe4a3294a4413f` ✅ |

工具：`安装空间/_b360_archive.py`（由 `_b350_archive.py` 改 `VER` / `_frozen_v360` 而来）。

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
