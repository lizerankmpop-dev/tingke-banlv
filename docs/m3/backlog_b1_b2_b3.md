# M3 · UI 体验缺陷 Backlog（B1–B5 · 已全部清空）

> 状态：**B1 / B2 / B3 / B4 / B5 全部已修复** —— Backlog 清空，可进入 M3 主体开发
> 目标版本：M3
> 相关文档：[`onboarding_wizard.md`](./onboarding_wizard.md)、[`icon_asset_spec.md`](./icon_asset_spec.md)
>
> **B2 已修复**（最后一轮）—— 范围比原始需求大：原需求只说「显示悬浮窗」开关缺 `role`，
> 实际把面板里**3 个开关 + 4 个滑杆 + 全部按钮/色板/颜色输入**一次补齐，
> 并加了「无障碍语义源码守卫 + 变异验证」防再犯。详见 §二开头的修复记录。
>
> **B1+B3 已修复**（本次提交）—— 共用一个根因：**反馈与日志写在调用点，而不是被调用的函数里**。
> 修法是把「该产生什么副作用」抽成纯模块 `electron/overlay-actions.cjs`，
> 各入口只声明自己是谁（`triggerSource`）。详见 §1.7 / §3.7。
>
> **本轮新增**（更早一轮）：核查过程中额外发现 2 项（**B4** / **B5**），均为 P0-3 修复后遗留的
> 文案与实现漂移。性质与 B1–B3 相同（不影响功能正确性，但错误地描述了系统行为），
> 已优先修复。
>
> **修复 B5 时又发现 2 处同类漂移**（属 B5 同源，一并修掉，见 §5.5）：
> `frontend/src/types/index.ts` 的 `termAutoDegrade` 注释、
> `frontend/src/components/OverlayControlPanel.tsx` 的开关说明注释 ——
> 都还写着「截断到 200 字符」，且前者还写着已被 P0-2 废弃的旧判据。

---

## 零、概览

| ID | 标题 | 类型 | 严重度 | 影响面 | 修复成本 | 状态 |
|---|---|---|---|---|---|---|
| **B1** | 「重置位置」按钮路径无任何反馈 | 交互反馈缺失 | 中 | 所有用户 | 极低 | ✅ **已修复** |
| **B2** | 悬浮面板控件缺无障碍语义（原报告只点名 1 个开关） | 无障碍 | 中 | 屏幕阅读器用户 | 低（3 开关 + 4 滑杆 + 守卫） | ✅ **已修复** |
| **B3** | `Alt+Shift+O` 成功路径无日志 | 可观测性 | 中 | 排障 / 用户自查 | 极低 | ✅ **已修复** |
| **B4** | 控制面板「提示词已缩减到 N 字符」文案已失效 | 文案与实现漂移 | **高** | 所有开启降级的用户 | 低 | ✅ **已修复** |
| **B5** | 注释仍描述旧的 200 字符截断 + 旧判据 | 注释过时 | 低 | 开发者 | 极低 | ✅ **已修复** |

> **B4 严重度标为「高」的原因**：它不只是文案不好看，而是**在降级真实发生时，
> 界面会给出一个错误的数字**，让用户无法判断软件是否真的采取了保护措施。
> 详见 §4。

---

## 一、B1 · 「重置位置」按钮路径无反馈

> ✅ **已修复**（与 B3 同一提交）。落地方式与下方 §1.5 的方案**基本一致但更进一步**：
>
> §1.5 写的是「把 toast 下沉到 `resetOverlayPosition()` 内部」——
> 这一步照做了，但那只解决了「这次的两条路径」。为了让它**结构上不可能再漏**，
> 把「该产生什么副作用」也抽成了纯模块：
>
> | 文件 | 改动 |
> |---|---|
> | `frontend/electron/overlay-actions.cjs` | **新建**。`describeReset(state, trigger)` 产出 `{ log, toast }`；`RESET_TOAST_TEXT` 是文案**唯一**来源 |
> | `frontend/electron/main.cjs` | `resetOverlayPosition(triggerSource)` 内部统一 `logLine` + `sendToOverlay`；热键与面板按钮的调用点**只声明来源** |
> | `frontend/tests/overlay-actions.test.cjs` | **新建**，16 项（含 5 条结构守卫） |
>
> **额外补掉一个原方案没覆盖的场景**：悬浮窗**关着**时点「重置位置」。
> 此时 `sendToOverlay()` 会静默返回 false（内部 `if (!isOverlayOpen()) return false`），
> toast 无处显示 —— 按原方案修完，按钮在这个场景下**仍然没有反馈**。
>
> 处理方式：`overlay:reset-position` 改为返回 `{ settings, notice, toastShown }`，
> 渲染层在 `toastShown === false` 时用 `notice` 做面板内联提示。
> 文案直接复用主进程那一份，**不在渲染层再写一遍字面量** ——
> 重复字面量正是 B1（和 B4）的共同成因。
>
> 顺带把 toast 频道提成常量 `TOAST_CHANNEL`：它有多个发射点，而接收端在
> preload.cjs 里按字符串订阅，写错不会报错、只会「什么提示都不出现」。
>
> **一处刻意的「不改」**：`restoreOverlayIfEnabled()`（启动自动恢复）只补了日志，
> 没有改成调 `openOverlayWindow()`。后者会顺带引入 `notifyOverlayState()`、
> 写配置文件等启动期副作用 —— 本次只收拢入口，不动底层行为。

### 1.1 现象

悬浮窗控制面板里的「重置位置」按钮**点下去没有任何视觉反馈**。

窗口位置被挪丢了 → 用户打开控制面板 → 点「重置位置」→ 界面毫无变化 →
用户不知道到底生效了没有，只能反复点。

**为什么「看不到变化」是常态**：如果窗口本来就在默认位置附近（或用户只是点着玩），
重置后位置几乎没有位移，肉眼无法分辨。此时「无反馈」和「功能坏了」在用户眼里
是完全一样的。

### 1.2 影响范围

- 触发路径：**主窗口 → 字幕视图 → PiP 按钮 → 控制面板 → 「重置位置」**
- 影响用户：**全部**（这是窗口挪丢后的主要自救手段，而它恰恰最需要反馈）
- 不影响：热键路径（`Alt+Shift+R`）—— **它已经有 Toast**

### 1.3 源码证据（已核实）

**按钮路径（无反馈）** —— `frontend/src/components/OverlayControlPanel.tsx:284`

```tsx
<button onClick={() => void resetPosition()} style={styles.actionBtn}>
  <RotateCcw size={13} />
  重置位置
</button>
```

调用链：`OverlayControlPanel` → `useOverlayStore.resetPosition()`
（`useOverlayStore.ts:86`）→ `overlayBridge.resetPosition()`
（`overlayBridge.ts:96`）→ IPC → 主进程 `resetOverlayPosition()`（`main.cjs:826`）

整条链**没有任何一处向用户回传可见结果**。

**热键路径（有反馈）** —— `frontend/electron/main.cjs:1450-1454`

```js
bind('Alt+Shift+R', () => {
  resetOverlayPosition();
  // 穿透状态下用户看不到任何反馈，必须主动提示一次
  sendToOverlay('overlay:toast', { text: '位置已重置到屏幕底部居中', at: Date.now() });
}, '重置悬浮窗位置');
```

**Toast 基础设施已经存在且已验证可用**：

- `OverlayWindow.tsx:35-53`：维护 `toast` state，3 秒后自动消失
- `OverlayWindow.tsx:167-171`：渲染 toast 层

**同面板内的对照**：「导出日志」按钮有反馈（`setLogNotice`，见
`OverlayControlPanel.tsx:41-46, 293`）。所以问题不是「面板不支持反馈」，
而是「这一个按钮漏了」。

### 1.4 根因

Toast 的发送职责被放在了**调用点**（热键 handler 里），而不是**被调用的函数**里。
按钮路径直接调 `resetOverlayPosition()`，绕过了调用点那一行，于是没有 toast。

这是一个典型的「反馈逻辑与业务逻辑分离」导致的不一致。

### 1.5 修复方案

**推荐做法：把 toast 下沉到 `resetOverlayPosition()` 内部，并删掉热键 handler 里的重复发送。**

`frontend/electron/main.cjs`：

```js
function resetOverlayPosition() {
  // ...现有重置逻辑（计算 target / setBounds / 保存配置）...

  logLine(`[overlay] 位置已重置: ${target.x},${target.y}`);

  // 反馈下沉到函数内部：热键路径与按钮路径共用同一实现，
  // 不会再出现「一条路径有提示、另一条没有」的分裂。
  // 穿透状态下用户看不到任何反馈，必须主动提示一次。
  sendToOverlay('overlay:toast', {
    text: '位置已重置到屏幕底部居中',
    at: Date.now(),
  });
}
```

同时把热键 handler 简化为：

```js
bind('Alt+Shift+R', () => resetOverlayPosition(), '重置悬浮窗位置');
```

> **为什么必须下沉而不是在按钮里补一行**：如果只在按钮里补，
> 下次再新增第三条调用路径（比如设置页、托盘菜单）时会再次漏掉。
> 下沉后**结构上不可能漏**。

**补充（可选）：主窗口侧也加一行本地提示。**
若悬浮窗处于关闭状态，用户点「重置位置」不会看到任何 toast（因为没有窗口承载它）。
此时可在面板内复用「导出日志」的 `logNotice` 模式补一行文字：

```tsx
const [resetNotice, setResetNotice] = useState<string | null>(null)

<button onClick={async () => {
  await resetPosition()
  setResetNotice('位置已重置到屏幕底部居中')
}} style={styles.actionBtn}>
```

> 判断依据：`sendToOverlay()` 在悬浮窗不存在时是**静默无效**的
> （需要确认实现；若它内部会 `logWarn`，则此补充可选）。

### 1.6 验收标准

| # | 验收项 | 验证方式 |
|---|---|---|
| 1 | 悬浮窗开着时，热键 `Alt+Shift+R` 弹出 toast | 手动按热键 |
| 2 | 悬浮窗开着时，点面板「重置位置」**同样**弹出 toast | 手动点击 |
| 3 | 两条路径的 toast 文案完全一致 | 对比 |
| 4 | 窗口位置被拖到屏幕外后，两条路径都能拉回底部居中 | 先拖丢再重置 |
| 5 | 悬浮窗关闭时点击按钮，应用不报错 | 关掉悬浮窗后点击 |
| 6 | toast 只弹一次（无重复叠加） | 观察是否有两条 toast 同时出现 |

> 第 6 项是「toast 从 handler 移到函数内」是否清理干净的检查点 ——
> 如果忘了删 handler 里那行，会出现两次 `sendToOverlay`。

---

## 二、B2 · 悬浮面板控件缺无障碍语义

> ✅ **已修复**。原始报告只点名「显示悬浮窗」开关缺 `role`，但**同一个根因贯穿整个面板**：
> 所有控件的语义都只存在于视觉里（文字标签是兄弟节点、开关状态只体现在背景色、
> 色块只有颜色、滑杆连名字和单位都没有）。所以这次一次性补齐，而不是只补被点名的那一个。
>
> | 文件 | 改动 |
> |---|---|
> | `frontend/src/components/OverlayControlPanel.tsx` | 3 个开关补 `role="switch"` + `aria-checked` + `aria-labelledby`；4 个滑杆补 `role="slider"` + `aria-valuemin/max/now/valuetext` + 可访问名；关闭按钮 / 6 个色块 / 颜色输入补可访问名；色板容器加 `role="group"`；异步提示区 `role="status"`、错误行 `role="alert"` |
> | `frontend/tests/overlay-a11y.test.cjs` | **新建**，12 项（2 项现状校验 + 1 项扫描自检 + **9 项变异验证**） |
>
> **落地方式与 §2.5 的方案不同，是升级**：方案里写的 `aria-label="显示悬浮窗"`（字面量）
> 没有采纳，改用 `aria-labelledby` 指向那个**已经显示在屏幕上的** `<span>`。
> 理由不是「更规范」而是**本项目自己的教训**：B1 的 toast 文案、B4 的「缩减到 N 字符」，
> 病因都是**同一句话存了两份**，然后其中一份改了、另一份没改。
> `aria-label="显示悬浮窗"` 恰好就是同一个形状 —— 屏幕上写着「显示悬浮窗」，
> 属性里再抄一份，将来改文案漏改属性，读屏就会念出和屏幕不一样的字。
> 用 `aria-labelledby` 后，这句话**只有一份**，不可能漂移。
> （§2.5 末尾其实已经写明「严格来说正确解法是 `aria-labelledby`」，
> 当时判断为「更大的重构」，实际做下来只是给 `<span>` 加个 `id` 而已。）
>
> **id 串收进常量表 `A11Y_ID`**（不是散落的字符串字面量），引用时写 `A11Y_ID.showOverlay`，
> 于是「改名漏改一处」在语法层面就不成立；反向的「写了 `aria-labelledby` 却忘了给标签挂 `id`」
> （悬空引用 —— 属性在、名字空，界面毫无异常）由守卫的**引用完整性检查**兜住。
>
> **滑杆用的是 `aria-label={label}` 而不是 `aria-labelledby`**：滑杆的可见文案来自
> `SliderRow` 的 `label` 属性，本来就是一个变量、只有一份来源，直接用同一个变量命名即可，
> 不必再绕一层 id。**共同原则是：可访问名与可见文案必须同源，不写第二份。**
>
> **三处刻意的判断，值得单独记**：
>
> 1. **「调整模式 / 结束调整」按钮不加 `aria-pressed`。** 它的标签随状态在变
>    （写的是「点下去会发生什么」），属于**命令式**按钮；再挂 `aria-pressed`
>    会让读屏念出「结束调整，已按下」这种自相矛盾的话。`aria-pressed` 只用于
>    「标签不变、只有状态在变」的按钮（本面板里是色板）。
> 2. **`<input type="range">` 上显式写 `role="slider"` + `aria-valuemin/max/now`
>    是冗余的**（原生元素本来就暴露这些），保留它是为了让守卫有一个统一的可校验形态，
>    以及让「值域来自哪」在源码里可读。真正带来信息量的是 `aria-valuetext` ——
>    没有它，读屏只会念「48」，单位（px / 行 / %）会丢。
> 3. **`aria-valuetext` 与屏幕上的数值共用同一个 `valueText` 变量**，
>    且 `suffix` 里禁止出现实现术语（`token` / `预算` / `截断`），由守卫的
>    术语口径检查挡住 —— 这是方案 §四.2 的风险点 2。
>
> **一项刻意没做：没有按 §2.6 的建议去 `smoke-overlay.cjs` 里加 DOM 断言。**
> 两个原因：①控制面板要先「开始识别」进入字幕视图才能打开，冒烟环境里没有音频输入，
> 够不到那个界面；②模块是 `.tsx`，`node --test` 无法导入（JSX 不在 Node 的类型擦除范围内），
> 想渲染就得引入 vitest + jsdom 一整套新依赖。所以改为**源码守卫**，
> 并**用变异验证证明它不是空转**（见下）。M3 首启向导落地后可以再考虑补一轮
> CDP 无障碍树冒烟。
>
> **变异验证做了两层**：
>
> - **套内（9 项，常驻）**：每条规则都配一个「把源码改坏」的用例，断言 `audit()` 必须报错。
>   永久生效，防止将来有人把守卫改成了永远返回通过。
> - **手工（一次性）**：按方案要求，临时删掉 `SliderRow` 里那一行 `aria-label={label}`，
>   跑守卫 —— 确实失败，报出 3 条：`L346 滑杆没有可访问名` /
>   `SliderRow 的 aria-label 不再是 {label}` / `L346 输入控件没有可访问名`，
>   且带精确行号。随后从备份逐字节还原（`cmp -s` 一致）。
>
> **回归基线**：tsc 0 错；前端 `npm test` **103 通过**（91 → 103，新增 12）；
> 后端 pytest **114 通过 / 1 跳过**（未触碰后端）；CDP 冒烟 **9/9**。

### 2.1 现象

> 以下行号均为**修复前**的源码状态，保留作原始证据；修复后这些行号已整体下移。

控制面板里三个视觉上完全一致的开关，**只有两个**带无障碍语义：

| 开关 | 位置 | `role="switch"` | `aria-checked` |
|---|---|---|---|
| 显示悬浮窗 | `OverlayControlPanel.tsx:76-86` | ❌ **缺失** | ❌ **缺失** |
| 后台持续更新字幕 | `OverlayControlPanel.tsx:174-189` | ✅ 有 | ✅ 有 |
| 术语提示词自动防循环降级 | `OverlayControlPanel.tsx:220-235` | ✅ 有 | ✅ 有 |

屏幕阅读器读到「显示悬浮窗」时，会把它当成一个**普通按钮**，
既播报不出「这是一个开关」，也播报不出「当前是开还是关」。

### 2.2 影响范围

- 影响用户：使用屏幕阅读器 / 键盘导航 / 语音控制的用户
- 严重性说明：这是三个开关里**唯一一个控制核心功能**（悬浮窗显示）的开关，
  恰恰是最需要被正确播报的那个
- 附带影响：键盘 `Tab` 导航时，开关状态无法通过 AT 读出

### 2.3 源码证据（已核实）

**有问题的**（`OverlayControlPanel.tsx:76-86`）：

```tsx
<button
  onClick={handleToggle}
  disabled={busy || loading}
  style={{
    ...styles.switch,
    background: open ? 'var(--accent)' : 'var(--bg-tertiary)',
    opacity: busy || loading ? 0.5 : 1,
  }}
>
  <span style={{ ...styles.knob, transform: open ? 'translateX(16px)' : 'translateX(0)' }} />
</button>
```

**正确的对照组**（`OverlayControlPanel.tsx:174-189`）：

```tsx
<button
  onClick={() => set({ backgroundUpdate: !settings.backgroundUpdate })}
  style={{ ... }}
  role="switch"                                // ← 有
  aria-checked={!!settings.backgroundUpdate}    // ← 有
>
```

### 2.4 根因

「显示悬浮窗」开关是**先写的**（它是面板里第一个控件），当时该模式还没成型；
后来加的两个开关用了正确的模式，但**没有人回头补第一个**。

### 2.5 修复方案

```tsx
<button
  onClick={handleToggle}
  disabled={busy || loading}
  role="switch"                       // ← 新增
  aria-checked={!!open}               // ← 新增
  aria-label="显示悬浮窗"              // ← 新增（见下方说明）
  style={{ ... }}
>
```

**关于 `aria-label` 的必要性**：按钮内部的可见文字「显示悬浮窗」在
`styles.rowLabel` 里，是这个 `<button>` 的**兄弟节点**，不是它的子节点。
因此按钮本身对无障碍树来说是**没有名字**的。`role="switch"` 只声明了「这是什么」，
`aria-checked` 声明了「什么状态」，但**没有声明「是哪一个开关」**。

> ⚠️ **上面这段方案没有采纳，已按「正确解法」实现**（实际工作量比预想的小得多）。
> 给 `<div style={styles.row}>` 加 id 那一步不需要 —— 直接把 `id` 挂到
> `rowLabel` 里那个已经存在的 `<span>` 上就够了。采纳它的理由见 §2 开头的修复记录：
> `aria-label` 字面量等于把屏幕上的文案再抄一份，正是 B1 / B4 的病因。
>
> 同样的问题**也存在于另外两个开关**（它们同样没有可访问名），
> 以及 `SliderRow`（`OverlayControlPanel.tsx:313-343`）里的 `<input type="range">`
> —— 没有名字，读不出「这是字号还是行数」。本次**三个开关 + 四个滑杆一起改**了。

### 2.6 验收标准

| # | 验收项 | 验证方式 | 落地情况 |
|---|---|---|---|
| 1 | 「显示悬浮窗」按钮在无障碍树中 `role=switch` | 浏览器 DevTools → Accessibility 面板 | ✅ 已补 |
| 2 | `aria-checked` 随开关状态正确变化 | 切换后复查 | ✅ 已补（绑 `open`，主进程回推时同步） |
| 3 | 使用屏幕阅读器能读出「显示悬浮窗，开关，已开启」 | 手动用 NVDA / Narrator | ⚠️ **待人工复验**（本机未装读屏软件，见下） |
| 4 | 该按钮有可访问名（不是空名） | 无障碍树中 `name` 字段非空 | ✅ `aria-labelledby` 指向可见 `<span>` |
| 5 | **（若统一修）** 三个开关都有 `role` / `aria-checked` / 可访问名 | 逐个检查 | ✅ 守卫逐项断言 |
| 6 | **（若统一修）** 四个滑杆都有可访问名 | 逐个检查 | ✅ 守卫逐项断言 |

> **第 3 项为什么只能「待人工复验」**：自动化能证明「属性写对了、引用不悬空」，
> 但证明不了「读屏念出来的顺序和用词真的能听懂」—— 那需要真人开着 NVDA/Narrator 走一遍。
> 已经用守卫把「属性缺失/悬空」这类机器能判的部分全部挡在提交时，
> 剩下的主观部分留给 M3 的实装验证轮次。
>
> **原建议「在 `smoke-overlay.cjs` 里遍历 `button[role="switch"]`」没有采纳**：
> 控制面板要先「开始识别」进字幕视图才能打开，冒烟环境里没有音频输入够不到；
> 且该模块是 `.tsx`，`node --test` 无法导入。改为 `tests/overlay-a11y.test.cjs`
> 的**源码守卫**（同样覆盖全部开关/滑杆，且已在提交时生效），
> 并用**套内 9 项变异验证 + 1 次手工变异**证明它不是空转。

---

## 三、B3 · `Alt+Shift+O` 成功路径无日志

> ✅ **已修复**（与 B1 同一提交，共用同一个纯模块）。
>
> 落地内容：
>
> | 文件 | 改动 |
> |---|---|
> | `frontend/electron/overlay-actions.cjs` | **新建**。触发来源词表 `TRIGGER_SOURCES`（ui / hotkey / startup / display-change / host-closed / unknown）+ `normalizeTrigger` + `describeOpen` / `describeClose` / `describeDestroy` |
> | `frontend/electron/main.cjs` | `openOverlayWindow` / `closeOverlayWindow` / `destroyOverlayWindow` 全部接受 `triggerSource` 并在**函数内部**打日志；新增 `toggleOverlayWindow(triggerSource)` 作为「开着吗→开还是关」的唯一决策点 |
>
> **日志字段的落地方式与方案略有出入**：方案 §四.2 写的是「日志字段增加
> `triggerSource: "ui" | "hotkey"`」。实际**没有**引入结构化字段，而是拼进消息文本：
>
> ```
> [2026-10-06T14:22:31.004Z] [INFO] [overlay] 已打开（触发来源=hotkey）
> ```
>
> 原因就是方案 §五自己提的那条风险：「确保日志字段和现有日志体系保持统一，
> 不要新增不兼容字段」。现有 `logLine(message, level)` 的输出是
> `[ISO] [LEVEL] message` 纯文本，全部 60+ 处调用点都是这个形状；
> 引入一个只有这里有的结构化字段，会破坏「一行一条、可直接 grep」的既有约定。
>
> **新增的 `startup` / `display-change` / `host-closed` 三个来源**超出了原方案范围：
> `destroyOverlayWindow` 原先连**成功**都不打日志（只在失败时打），
> 于是「悬浮窗怎么自己没了」同样查不出来 —— 与 B3 完全同类，
> 顺手一并补上。
>
> **`normalizeTrigger` 的容错是刻意的**：调用点漏传时落成 `unknown`，
> 而不是把 `undefined` 拼进日志。日志里出现 `触发来源=undefined`
> 比没有这个字段更误导人。

### 3.1 现象

五个全局热键里，**只有 `Alt+Shift+O`（开关悬浮窗）在成功执行后不打日志**。

用户按了 `Alt+Shift+O` 但悬浮窗没出现 → 去导出的 `electron.log` 里查 →
**只看得到「热键注册成功」，看不到「用户确实按过」**。

于是无法区分两种完全不同的故障：

- **A：热键根本没触发**（被别的软件抢占了注册）
- **B：热键触发了，但窗口没显示出来**（比如被别的窗口挡住、坐标在屏幕外、创建失败）

这两者的修复方向完全不同（A 要换快捷键，B 要修窗口逻辑），
但日志上完全一样 —— 都是一个「注册成功」然后什么都没有。

### 3.2 影响范围

- 影响用户：**所有**（`Alt+Shift+O` 是最高频的热键，也是开启悬浮窗的唯一键盘入口）
- 影响场景：**排障**。而导出的日志正是目前让用户自助排障的唯一手段
  （控制面板里就有「导出日志」按钮）
- 严重性说明：这类缺陷不会让功能出错，但会让**所有**相关故障都变成
  「无法定位」，而用户手里明明有日志工具却查不出东西，体验更差

### 3.3 源码证据（已核实）

**热键绑定**（`main.cjs:1435-1438`）：

```js
bind('Alt+Shift+O', () => {
  if (isOverlayOpen()) closeOverlayWindow();
  else openOverlayWindow();
}, '开关悬浮窗');
```

`bind()` 内部只在**注册时**打日志（`main.cjs:1420-1426`）：

> `[overlay] 热键 Alt+Shift+O（开关悬浮窗）注册成功`
> `[overlay] 热键 Alt+Shift+O（开关悬浮窗）注册失败：已被其它程序占用`

**但 handler 触发的动作本身不打日志**。

**各函数日志情况对照**（已逐一核实）：

| 函数 | 行号 | 成功路径是否打日志 |
|---|---|---|
| `openOverlayWindow()` | `main.cjs:805` | ❌ **无** |
| `closeOverlayWindow()` | `main.cjs:797` | ❌ **无** |
| `destroyOverlayWindow()` | `main.cjs:785` | ❌ 无（仅失败时 `logLine`） |
| `toggleOverlayInteractive()` | `main.cjs:815` | ✅ `logLine('[overlay] 鼠标穿透=…')` |
| `resetOverlayPosition()` | `main.cjs:826` | ✅ `logLine('[overlay] 位置已重置: x,y')` |

**结论**：这不是「所有热键都没日志」，而是 **`Alt+Shift+O` 恰好走的两个函数
是唯一没打日志的**。同一份文件里另外三个热键都有日志，说明日志规范本身是有的，
只是 `open` / `close` 这两个函数漏了。

### 3.4 根因

`openOverlayWindow()` / `closeOverlayWindow()` 是**底层原语**（除了热键，
创建/销毁流程、启动时自动恢复、显示器变化自愈也都会调它们）。
函数作者可能认为「调用方会打日志」—— 但热键 handler 作为调用方并没有打。

这与 **B1** 是**同一类结构性问题的两个面**：
「该在底层打的日志/反馈，被期望由调用方负责，结果某个调用方没做」。

### 3.5 修复方案

**同样建议下沉到函数内部**，而不是在热键 handler 里补：

```js
function openOverlayWindow() {
  // ...现有创建逻辑...
  logLine(`[overlay] 已打开（触发来源=${_overlayTrigger ?? '未知'}）`);
}

function closeOverlayWindow() {
  // ...现有销毁逻辑...
  logLine(`[overlay] 已关闭（触发来源=${_overlayTrigger ?? '未知'}）`);
}
```

**关于「触发来源」字段**：只记「打开了」价值有限 —— 用户报告的往往是
「我按了热键它自己又关了」。加一个轻量的来源标记能直接定位：

```js
let _overlayTrigger = null;

// 热键
bind('Alt+Shift+O', () => {
  _overlayTrigger = 'hotkey';
  if (isOverlayOpen()) closeOverlayWindow();
  else openOverlayWindow();
}, '开关悬浮窗');

// 面板按钮（IPC handler 内）
_overlayTrigger = 'panel';

// 启动自动恢复
_overlayTrigger = 'startup-restore';

// 显示器变化自愈
_overlayTrigger = 'display-change';
```

日志形如：

```
[overlay] 已打开（触发来源=hotkey）
[overlay] 已关闭（触发来源=startup-restore）   ← 一眼看出是启动逻辑误关
```

> **不要为这个字段引入复杂的状态机。** 一个模块级 `let` 变量在单线程主进程里足够；
> 它是诊断辅助，不需要并发安全。

### 3.6 验收标准

| # | 验收项 | 验证方式 |
|---|---|---|
| 1 | 按 `Alt+Shift+O` 打开悬浮窗后有日志 | 查看 `electron.log` |
| 2 | 再按一次关闭后有日志 | 同上 |
| 3 | 日志含触发来源 | 同上 |
| 4 | 启动时自动恢复悬浮窗也打日志，来源为 `startup-restore` | 关应用时保持悬浮窗开启，重启后查日志 |
| 5 | 控制面板开关悬浮窗的日志来源为 `panel` | 点击后查日志 |
| 6 | 五个热键**全部**有动作日志 | 逐个按一遍 |
| 7 | `openOverlayWindow` 创建失败时有明确错误日志 | 注入异常（临时改代码） |

---

## 四、B4 · 控制面板「提示词已缩减到 N 字符」文案已失效 ⚠️

> ✅ **已修复**（本次提交）。实际落地方式与下方 §4.4 的方案有一处关键差异：
>
> **后端无需改动。** 方案里写的「透传 promptTokens / promptBudget / droppedTerms」
> 在 P0-3 就已完成了 —— `Pipeline.get_stats()` 是 `self._whisper.get_stats()`
> 的全量透传，`/api/asr/poll` 直接把整个 stats 返回。所以 B4 实际是
> **纯前端 + 测试**的改动。
>
> 落地内容：
>
> | 文件 | 改动 |
> |---|---|
> | `frontend/src/lib/termGuard.ts` | **新建**。文案抽为纯函数 `buildTermGuardRows(guard, autoDegrade)`，可被 `node --test` 直接 import |
> | `frontend/tests/term-guard.test.mts` | **新建**，13 项用例（含 2 条「文案不得出现固定数值/实现术语」的回归守卫） |
> | `frontend/src/components/OverlayControlPanel.tsx` | 删掉两处 `promptChars` 数值展示，改渲染纯函数结果；合并原先分离的「已关闭兜底」行 |
> | `frontend/src/types/index.ts` | `TermGuardStats` 补 `promptTokens`/`promptBudget`/`droppedTermCount`；`promptChars` 标 `@deprecated` 并写明失效原因 |
> | `frontend/src/stores/useSubtitleStore.ts` | 映射新字段（含 `numOrNull` 兜底，避免 `NaN` 漏进 UI） |
> | `backend/tests/test_asr_pipeline_e2e.py` | 新增 2 项：钉住「`termPromptChars` 不是降级指示器」+「面板依赖字段齐备」 |
>
> **修复过程中额外发现并处理了一个未记录的状态**（原 §4.3 只列了 3 态，实际有 5 态）：
>
> | autoDegrade | degraded | repetitionDetected | 真实含义 | 修复前显示 | 修复后显示 |
> |---|---|---|---|---|---|
> | on | ✅ | ✅ | 已降级 | ✅ 正确 | 已触发降级…（含丢弃条数） |
> | on | ❌ | ✅ | **术语本来就少，降无可降**（引擎里 `cost <= DEGRADE_TOKEN_BUDGET` 分支） | ❌ **「未检测到重复输出」** | 检测到重复输出，但术语范围已很小 |
> | on | ❌ | ❌ | 正常 | ✅ 正确 | 未检测到重复输出 |
> | off | ❌ | ✅ | 检出了但保护关闭 | ❌ 会同时渲染两行互相打架的提示 | 已检出…但自动降级已关闭 |
> | off | ❌ | ❌ | 正常，保护关闭 | ✅ 正确 | 已关闭兜底… |
>
> 第 2 行是**同一类错误的第二处**：`degraded` 与 `repetitionDetected` 被当成一个布尔用，
> 于是「检出了但没降级」的两个不同原因（保护被关 / 术语本就不多）都被显示成
> 「未检测到重复输出」。
>
> 另外把开关的「缺省即开启」语义显式归一为 `settings.termAutoDegrade !== false`：
> 该字段在类型上可选，原先用真值判断，会把「字段缺失」误显示成「已关闭」，
> 导致开关外观与状态行互相矛盾（也让 `tsc` 报错）。

> **本项为核查过程中新发现，严重度高于 B1–B3。**

### 4.1 现象

悬浮窗控制面板在降级触发后会显示：

> **已触发降级：检测到字幕重复输出，提示词已缩减到 `N` 字符。重新开始听课后会恢复完整术语表。**

**P0-3 之后，这里的 `N` 不再变化 —— 它会一直显示完整术语表的原始字符数，
降级前后完全一样。**

用户看到的是自相矛盾的一句话：「已缩减到 794 字符」（就是原始长度，
根本没有缩减），这会让他怀疑保护措施到底有没有生效。

### 4.2 影响范围

- 影响用户：**所有触发过降级的用户**（即所有使用长术语表的专业课场景）
- 严重性：**高**。这不是「文案不好看」，而是**在关键时刻给出错误信息**：
  - 降级是**事后补救**机制，用户此刻正看到字幕在重复输出，本就处于
    「软件是不是坏了」的焦虑中
  - 唯一能证明「软件已经采取措施」的就是这一行字
  - 而它给出的数字是错的

### 4.3 源码证据（已核实）

**前端展示**（`OverlayControlPanel.tsx:240-260`）：

```tsx
{termGuard.degraded ? (
  <>
    <ShieldAlert size={12} />
    <span>
      已触发降级：检测到字幕重复输出，提示词已缩减到
      {termGuard.promptChars !== null ? ` ${termGuard.promptChars} 字符` : '较短长度'}。
      重新开始听课后会恢复完整术语表。
    </span>
  </>
) : ( /* ... */ )}
```

**数据来源**（`useSubtitleStore.ts:154-157`）：

```ts
termGuard: {
  autoDegrade: whisper.termAutoDegrade !== false,
  degraded: !!whisper.termPromptDegraded,
  promptChars: typeof whisper.termPromptChars === 'number' ? whisper.termPromptChars : null,
  repetitionDetected: !!whisper.repetitionDetected,
},
```

**后端计算**（`backend/asr/whisper_engine.py:951`）：

```python
"termPromptChars": len(self.initial_prompt or ""),
```

**根因**：P0-3 把降级机制从「**截断提示词字符串**」改成了「**改用更小的 token 预算**」：

| | P0-3 之前 | P0-3 之后 |
|---|---|---|
| 降级做了什么 | `initial_prompt = _last_text[-200:]`（**真的截短了字符串**） | `budget = DEGRADE_TOKEN_BUDGET (70)`（**字符串没变，只是组装时给更少预算**） |
| `len(self.initial_prompt)` | 变小（如 794 → 200） | **不变**（始终是完整长度） |

于是 `termPromptChars` 从「当前生效长度」变成了「原始长度」，
而前端文案仍然按旧的语义解释它。

> **接口没坏，语义变了** —— 这类漂移最难被发现，因为所有字段名都对得上、
> 所有测试都过（`test_asr_pipeline_e2e.py` 确实在断言
> `stats["termPromptDegraded"] is True`，但它没断言字符数变化）。

### 4.4 修复方案

P0-3 已经提供了正确的埋点字段（`whisper_engine.py:966-977`）：

```python
"promptTokens": b.used_tokens if b else 0,          # 真正送进模型的分量
"promptBudget": b.budget if b else PROMPT_TOKEN_BUDGET,  # 本次预算（降级后变小）
"promptRemaining": b.remaining_tokens if b else 0,
"droppedTerms": list(b.dropped) if b else [],       # 被预算丢掉的术语
"promptSections": {k: len(v) for k, v in b.kept.items()} if b else {},  # 各层保留数
```

**推荐改法：前端改读 token 字段，文案改说「术语」，不绑定单位。**

`frontend/src/types/index.ts:193-202` → 扩展 `TermGuardStats`：

```ts
export interface TermGuardStats {
  autoDegrade: boolean
  degraded: boolean
  /** @deprecated P0-3 起降级不再改变提示词字符串长度，此字段恒为原始长度。
   *  展示请改用 promptTokens / promptBudget / droppedTerms。 */
  promptChars: number | null
  repetitionDetected: boolean
  /** 本次实际送入模型的提示词 token 数 */
  promptTokens?: number | null
  /** 本次预算上限（降级后为较小的值） */
  promptBudget?: number | null
  /** 因预算不足被丢弃的术语条数 */
  droppedTermCount?: number
}
```

`useSubtitleStore.ts` 同步映射：

```ts
promptTokens: typeof whisper.promptTokens === 'number' ? whisper.promptTokens : null,
promptBudget: typeof whisper.promptBudget === 'number' ? whisper.promptBudget : null,
droppedTermCount: Array.isArray(whisper.droppedTerms) ? whisper.droppedTerms.length : 0,
```

`OverlayControlPanel.tsx` 文案改为（**两个分支都要改**）：

```tsx
{termGuard.degraded ? (
  <>
    <ShieldAlert size={12} />
    <span>
      已触发降级：检测到字幕重复输出，已自动缩小参与识别的术语范围
      {termGuard.droppedTermCount > 0 ? `（本次有 ${termGuard.droppedTermCount} 个低优先级术语未参与）` : ''}
      。重新开始听课后会恢复完整术语表。
    </span>
  </>
) : (
  <>
    <ShieldCheck size={12} />
    <span>未检测到重复输出。</span>
  </>
)}
```

**三条设计原则（必须遵守）**：

1. **不向用户暴露 `token` / `预算` 这类实现概念。** 用户不需要知道
   `initial_prompt` 有 223 token 窗口。措辞应该是「参与识别的术语范围」。
2. **不绑定具体数值。** 不要再出现「缩减到 N 字符 / N 个 token」——
   预算参数（`PROMPT_TOKEN_BUDGET` / `DEGRADE_TOKEN_BUDGET`）是可配置的，
   文案写死数值就会再次漂移（这正是 B4 产生的原因）。
   如果一定要给量化信息，用「本次有 N 个低优先级术语未参与」——
   N 来自 `droppedTerms.length`，是**运行时真实值**，不会漂移。
3. **保留「重新开始听课后会恢复」这句话。** 这是降级唯一的解除方式，
   必须说清楚，否则用户会以为软件「一直恢复不了」。

**同时**：`termGuard.promptChars` **不要直接删除**，标记 `@deprecated` 即可
（后端 `get_stats()` 里的 `termPromptChars` 也保留，避免破坏其他消费方）。

### 4.5 验收标准

| # | 验收项 | 验证方式 |
|---|---|---|
| 1 | 构造降级场景（长术语表 + 复读音频），面板文案不出现「缩减到 N 字符」 | 手动验证 |
| 2 | 降级后 `promptBudget` 确实变小（220 → 70） | 查 `/api/health` 或 stats |
| 3 | 文案中的数字随时间不再漂移（不引用固定预算值） | 代码审查 |
| 4 | 未降级分支也改掉「当前提示词 N 字符」的表述 | 代码审查 |
| 5 | `promptChars` 保留且标 `@deprecated`，无消费方崩溃 | `tsc` 0 错 + 前端测试通过 |
| 6 | 文案不含 `token` / `预算` / `截断` 等实现词 | 全文搜索面板文案 |

> **本项应与首启向导的第 6 步文案保持一致** —— 两处都在解释同一个开关，
> 用词分裂会让用户更加困惑。向导文案见
> [`onboarding_wizard.md`](./onboarding_wizard.md) §3 第 6 步。

---

## 五、B5 · 注释仍描述旧的 200 字符截断

> ✅ **已修复**（与 B4 同一提交）。除 `overlay-settings.cjs` 外，
> 又扫出 **3 处**同类漂移，一并修掉，见 §5.5。

### 5.1 现象

`frontend/electron/overlay-settings.cjs:36-46` 对 `termAutoDegrade` 的说明注释中写着：

> `true（默认）= 检测到「输出字幕数 > 输入片段数」且累计比值超阈值时，`
> **`自动把提示词截断到 200 字符并打 WARN`**`，把损伤范围限制住。`

**这句话有两处已过时**：

1. **「截断到 200 字符」** —— P0-3 后改为「改用 70 token 预算」，
   不再截断字符串（即 B4 的同一根因）。
2. **「检测到『输出字幕数 > 输入片段数』且累计比值超阈值」** ——
   P0-2 已经把判据从「单调结构信号（字幕数 > 片段数）」改成
   **基于文本自身重复的检测**（`asr/repetition.py`，阈值
   `min_repeat_chars=35` 等）。旧判据的特征是「字幕数 > 片段数」，
   新判据看的是**文本里有没有真实的长重复**。

第 2 点尤其误导：一个开发者照着这段注释去理解系统行为，会得到**完全相反**的结论
（比如以为「只要字幕数超过片段数就会降级」）。

### 5.2 影响范围

- 影响用户：无（仅注释）
- 影响开发者：**中**。这段注释出现在「配置结构定义」这个高被读位置，
  而且它解释的是整个 P0-2/P0-3 改动中最容易误解的机制
- 连带风险：后续新增配置项的人会以这段注释为模板，把过时描述继续扩散

### 5.3 修复方案

更新为该机制**当前**的真实描述：

```js
  /**
   * 术语提示词自动降级（防重复循环兜底）
   *
   * 背景：术语热词是作为 Whisper 的 initial_prompt 注入的，长列表会被模型当成
   * 「上文」继续往下编 —— 实测会出现字幕重复循环，单次识别 RTF 从 0.42 涨到 1.18
   * （突破实时）。
   *
   * true（默认）= 检测到「本段转写文本出现长距离重复」（判据见 asr/repetition.py，
   * 默认 min_repeat_chars=35）时，把术语提示词的 token 预算从 PROMPT_TOKEN_BUDGET
   * 降到 DEGRADE_TOKEN_BUDGET，并打 WARN，把损失范围限制在最低优先级的术语上。
   *
   * 降级是**事后反应**：它挡不住首次发生，只能让后续片段恢复正常。
   * 关掉它 = 完全保留术语表的原貌（识别质量优先，接受偶发重复）。
   *
   * 注意：降级**不再是**「把提示词截断到 200 字符」——
   * P0-3 起改为调整 token 预算，提示词字符串本身不变。
   */
  termAutoDegrade: true,
```

同文件 `:39` 提到的「794 字符的术语表」是**历史实测数据**，
保留即可，但建议补一句标注它是 P0-3 前的观测值：

```js
   * 实测 P0-3 前，794 字符的术语表会让字幕数超过音频片段数，出现重复循环
```

### 5.4 验收标准

| # | 验收项 | 验证方式 |
|---|---|---|
| 1 | 注释不再出现「截断到 200 字符」 | 全文搜索 |
| 2 | 注释描述的判据与 `asr/repetition.py` 实际一致 | 交叉阅读两份代码 |
| 3 | 注释描述的降级行为与 `_note_produced_text()` 实际一致 | 交叉阅读 |
| 4 | 注释中引用的常量名（`PROMPT_TOKEN_BUDGET` / `DEGRADE_TOKEN_BUDGET` / `min_repeat_chars`）真实存在 | 全局搜索确认 |

> **同类检查建议**：P0-2/P0-3 改动较大，建议全局搜索
> `200 字符` / `截断` / `字幕数` 三个关键词，
> 排查是否还有其他注释/文档残留旧描述。已知 `README.md` 与
> `ASR_Pipeline_Validation_Report.md` 已在 P0-3 期间同步更新，
> 但**代码内注释**这一轮没有系统性排查过。

### 5.5 按上述建议做全局搜索后，又扫出 3 处（已一并修复）

执行 `grep -rn "200 字符\|截断到 200\|字幕数 > 片段数" frontend backend scripts` 后，
逐条判定「是残留」还是「正当引用」，结果如下：

| 位置 | 判定 | 处理 |
|---|---|---|
| `frontend/src/types/index.ts` → `termAutoDegrade` 注释 | ❌ **残留**（且同时写着旧判据） | 已改写，并补上「缺省值是 true、UI 判断用 `!== false`」的告警 |
| `frontend/src/components/OverlayControlPanel.tsx` → 开关上方说明注释 | ❌ **残留** | 已改写为「调 token 预算、丢低优先级术语」，并显式注明「不再是截断到 200 字符」 |
| `backend/dictionary/terminology.py` → `DEFAULT_PROMPT_MAX_CHARS` 注释 | ❌ **误导**（见下） | 已改写 |
| `backend/asr/repetition.py` | ✅ 正当引用 | 保留 —— 此处正是在解释「旧判据为何失效」 |
| `backend/tests/test_repetition.py` | ✅ 正当引用 | 保留 —— 用例名就是「字幕数 > 片段数**不**是重复循环」 |
| `scripts/analyze_zh_ab.py`、`scripts/eval_asr_quality.py` | ✅ 正当引用 | 保留 —— 这是**离线评估**的观测指标，不是引擎判据 |

**`terminology.py` 那条为什么算「误导」而不是「过时」**：

原注释说 `DEFAULT_PROMPT_MAX_CHARS = 800` 是
「收益与代价的平衡点」，暗示它是**生效**的长度上限。但 P0-3 之后：

- 真正的约束是 **token 预算**（`PROMPT_TOKEN_BUDGET = 220`）
- 中文 token 密度约 1.8 token/字 → **800 字符 ≈ 1480 token**
- 也就是说，对中文课程这个字符上限**永远不会生效**，调它没有任何效果

留着一句「800 是平衡点」会让人去 tuning 一个死参数。已改写为明确说明
「这只是粗筛，真正的约束在 `TINGKEBANLV_TERM_PROMPT_TOKENS`」，
并保留它在英文场景下的实际作用（800 字符 ≈ 208 token，与预算同量级，能挡掉明显超长的列表）。

**顺带澄清一个我一度怀疑是真 bug、实测证伪的点**：

`terminology.py` 里 `DOMAIN_TERMS` 的注释说「越靠前的词越优先」，
而 P0-3 把拼接顺序改成了「高优先级贴尾」。看起来像是「靠前的词会被丢」的反向 bug。
追进 `prompt_budget.assemble()` 后确认**不是**：术语是按**列表顺序贪心填预算**的
（`for term in terms: if cost > remaining: drop; else: remaining -= cost`），
所以靠前的词优先拿到配额 —— 结论仍然成立。位置靠前不危险，因为组装总量
被压在 220 token、低于库保留的 223，库不会再截。
这个区分（「优先」vs「位置」）很容易被后来的维护者搞混，
已在 `terminology.py` 的注释里写清楚。

---

## 六、附：本轮核查方法论（为什么能发现 B4 / B5）

B1–B3 是已知项，B4/B5 是本轮**新发现**。发现路径值得记录，因为它是可复用的：

**发现 B4 的路径**：
1. 为写首启向导文档，需要准确描述「术语自动降级」这个开关 → 读
   `OverlayControlPanel.tsx` 里它的 UI 文案
2. 看到「提示词已缩减到 N 字符」→ 需要确认 `termGuard.promptChars` 怎么算的
3. 追到 `useSubtitleStore.ts` → 追到后端 `whisper_engine.py:951`
4. 看到 `len(self.initial_prompt or "")` → **违反预期**：
   P0-3 明明改成了预算制，字符串不该变
5. 反向验证：确认 P0-3 的降级实现只改 `budget` 不改 `initial_prompt` → 疑点成立

**发现 B5 的路径**：
1. 为写图标文档，需要确认悬浮窗配置的默认值 → 读 `overlay-settings.cjs`
2. 读到 `termAutoDegrade` 的注释 → 发现里面写的是「截断到 200 字符」
3. 与刚在 B4 里确认的事实矛盾 → 疑点成立

**共性**：两次都是「**为了写文档而被迫逐层追实现**」时发现的。
如果只是做代码 review，很可能因为「字段名都对得上」而跳过。

**可复用结论**：
> **改动一个机制时，必须主动搜索所有描述该机制的文案与注释。**
> 代码改了、测试改了、报告改了，并不意味着这个改动已经「传播完成」——
> UI 文案和代码注释是两个最容易漏掉的落点，而它们恰恰是用户和
> 后续开发者最直接看到的东西。

这一条建议补进 `测试策略与验收清单.md` 的修复检查清单。

---

## 七、优先级建议

| 顺序 | 项目 | 理由 | 状态 |
|---|---|---|---|
| 1 | **B4** | 唯一会在运行时向用户显示**错误信息**的一项 | ✅ 已完成 |
| 2 | **B5** | 与 B4 同源（P0-3 文档漂移），合并一次提交 | ✅ 已完成 |
| 3 | **B1** | 最高频自救路径无反馈 | ✅ 已完成 |
| 4 | **B3** | 与 B1 同源（反馈/日志未下沉），合并一次提交 | ✅ 已完成 |
| 5 | **B2** | 独立，但建议与其他两个开关+四个滑杆**统一修**，顺带加自动化检查 | ✅ 已完成 |

**合并建议**：

- ~~**提交 2**：B4 + B5~~ ✅ —— 已完成，见 §四 / §五的修复记录
- ~~**提交 3**：B1 + B3~~ ✅ —— 已完成，见 §一 / §三的修复记录
- ~~**提交 4**：B2~~ ✅ —— 已完成，见 §二开头的修复记录

**Backlog 已清空**（B1–B5 全部关闭），下一步进入 M3 主体开发：
首启向导（`docs/m3/onboarding_wizard.md`）+ 应用图标（`docs/m3/icon_asset_spec.md`）
+ `app.setAppUserModelId`。原建议的「在 `smoke-overlay.cjs` 里补 3–4 条一致性断言」
已由 `tests/overlay-a11y.test.cjs` 以源码守卫的形式落地（含变异验证），
不需再往冒烟脚本里堆断言。

### 7.1 B4 + B5 这一段留下的可复用做法

1. **把文案抽成纯函数**（`src/lib/termGuard.ts`）而不是留在组件里 ——
   组件里的 JSX 逻辑跑不了单测，抽出来才有 13 项用例。这与项目已有的
   `sm2.ts` / `overlay-settings.cjs` 是同一套思路。
2. **给文案加「不得出现固定数值/实现术语」的回归守卫**（`term-guard.test.mts`）——
   这类漂移靠人工 review 一定会再犯，必须让 CI 挡住。
3. **后端测试钉住「字段语义」而不只是「字段存在」** ——
   `test_term_prompt_chars_is_not_a_degradation_indicator` 断言的是
   「这个字段**不会**随降级变化」。写这种「反向断言」看起来怪，
   但它正是防止别人把语义悄悄改回去的唯一手段。

### 7.2 B1 + B3 这一段留下的可复用做法

1. **「多入口一致性」不能靠人记，要靠结构。** 只要还有第二个调用点，
   写在调用点的反馈/日志就一定会漏。修法是两件事同时做：
   **①副作用下沉到被调函数 ②各入口只声明自己是谁**。
2. **`main.cjs` 不能被 `node --test` 导入（require electron），
   所以「决策」必须抽成不依赖 electron 的纯模块。** 这是本项目第二次
   做这种抽取（第一次是 `overlay-settings.cjs`），已成为固定套路。
3. **给不可导入的主进程代码加「源码结构守卫」测试。** 本次 5 条：
   文案无第二份字面量 / 每个调用点都声明来源 / 热键区不得自带 toast /
   热键区不得自己 if-else 判开闭 / 频道名必须是常量。
   看起来不常规，但它是唯一能守住 main.cjs 的手段 ——
   **且已做变异验证**：故意破坏调用点与故意在热键区塞 toast，
   两条守卫都确实失败（不是空转）。
4. **同一个陷阱会重复出现。** 抽 `fmt()` 时又撞上
   `Number(null) === 0` → 「没有坐标」被静默转成坐标 0。
   `overlay-settings.cjs` 顶部注释早就记录过同一个坑（那次导致默认位置永远算不出来）。
   → **遇到「把未知值转成数字」时，先挡 null/undefined，再判 isFinite。**

### 7.3 B2 这一段留下的可复用做法

1. **「可访问名」是一份文案，因此适用同一条铁律：不得存两份。**
   `aria-label="显示悬浮窗"` 看起来无害，但它就是 B1 的 toast 文案、
   B4 的「缩减到 N 字符」的同一个形状 —— 屏幕上写一份、属性里抄一份，
   将来改一处漏一处。默认用 `aria-labelledby` / 复用同一个变量；
   只有当控件**没有可见文案可引用**时（纯图标按钮），字面量才是唯一来源。
2. **「审计函数 + 变异用例」比「断言」更适合守这类规则。**
   把检查写成返回问题清单的纯函数 `audit(src)`，而不是直接 assert，
   于是「把源码改坏后 audit 必须报错」本身也成了可测的用例（本次 9 条）。
   → **一个永远返回「通过」的守卫比没有守卫更糟**，因为它会让所有人
   以为这件事已经有人看着了。
3. **`sliceFunction` 这类小工具在「带解构参数的函数」上会静默失效。**
   `function SliderRow({ … }: { … }) {` 的第一个 `{` 是参数解构，
   按「找第一个 `{` 再配平」的老写法会在参数闭合处提前收尾，
   函数体整个取不到 —— 表现是「守卫报了三处问题」而代码其实是对的。
   → 正确做法是先跳过参数表（含类型注解里的 `(v: number) => void`），再取函数体的 `{`。
4. **「一个组件元素」与「一个控件」不是一回事。**
   面板里有 4 个滑杆，但源码里只有 **1 个** `<input type="range">`（`SliderRow` 模板）
   + **4 处** `<SliderRow>` 调用。第一版守卫按「数 range 元素」判定滑杆个数，
   直接扫出 1 ≠ 4 的假失败。
   → 数**调用点**判个数，用**模板**判属性齐备性。
5. **能自动判的和只能人判的，要在报告里分开写。**
   守卫能证明「属性写对、引用不悬空」，证明不了「读屏念出来是否真能听懂」。
   后者明确标为「待人工复验」，不留一句「已完成」把主观项也盖过去。


