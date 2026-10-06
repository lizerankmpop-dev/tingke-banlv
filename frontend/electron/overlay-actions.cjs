/**
 * 悬浮窗动作的**副作用决策** —— 纯逻辑（不碰 Electron / 不碰真实 fs）
 *
 * 为什么要有这个文件
 * ------------------
 * 同一个动作（重置位置 / 开关悬浮窗）有**多个入口**：
 * 控制面板的 UI、全局热键、启动时自动恢复、显示器变化自愈、主窗口关闭连带销毁。
 *
 * 这些入口原先各自实现一遍「要不要弹 toast、要不要打日志」，于是行为不一致 ——
 * 两处真实缺陷（Backlog B1 / B3）都源于此：
 *
 *   - `Alt+Shift+R`（热键重置）**有** toast，因为它写在热键回调里；
 *     控制面板的「重置位置」按钮直接调 `resetOverlayPosition()`，绕过了那一行，
 *     于是**没有** toast。用户点了按钮却看不到任何反馈（B1）。
 *   - `Alt+Shift+O`（热键开关）走的 `open/closeOverlayWindow()` 成功路径
 *     **完全不打日志**；而同文件的 `resetOverlayPosition()` /
 *     `toggleOverlayInteractive()` 都有日志。用户按了没反应、去导出的日志里
 *     查，只能看到「热键注册成功」，看不出「到底按没按到」（B3）。
 *
 * 根因是同一个：**反馈与日志的职责被放在调用点，而不是被调用的函数里**。
 * 只要还有第二、第三个调用点，就一定会再漏一次。
 *
 * 解决办法：把「该产生什么副作用」收拢到这里，main.cjs 只负责「执行 + 输出」，
 * 各入口只负责声明自己是谁（triggerSource）。行为一致由**结构**保证，不靠人记。
 *
 * 不 require('electron')，所以能被 `node --test` 直接 require ——
 * 与 overlay-settings.cjs 是同一套思路（那个文件当初也是为了可测才从 main.cjs 抽出来）。
 *
 * 运行测试：cd frontend && npm run test
 */

/**
 * 触发来源词表。
 *
 * 新增入口时**必须**在这里登记；未登记的取值会被归一成 `unknown`，
 * 而不是被原样拼进日志 —— 否则日志里会出现 `触发来源=undefined`，
 * 比没有这个字段更误导人。
 */
const TRIGGER_SOURCES = Object.freeze({
  /** 控制面板上的按钮 / 开关 */
  ui: 'ui',
  /** 全局快捷键 */
  hotkey: 'hotkey',
  /** 启动时按上次的 enabled 状态自动恢复 */
  startup: 'startup',
  /** 显示器插拔 / 改 DPI / 投屏切换导致的位置自愈 */
  display: 'display-change',
  /** 主窗口关闭时的连带销毁 */
  host: 'host-closed',
  /** 兜底：未登记的来源 */
  unknown: 'unknown',
});

/**
 * 把任意输入归一成词表内的来源标记。
 *
 * 容错是必须的：调用点可能忘记传参（undefined）、传了错误的大小写、
 * 或者传了对象。这些情况都应落成 'unknown' 而不是污染日志。
 */
function normalizeTrigger(raw) {
  const key = typeof raw === 'string' ? raw.trim() : '';
  return TRIGGER_SOURCES[key] || TRIGGER_SOURCES.unknown;
}

/**
 * 重置位置后给用户看的文案 —— **唯一**来源。
 *
 * 不要在 main.cjs 的任何调用点再写一遍这个字面量：那正是 B1 的成因
 * （一处有、一处没有）。同理也不要在这里区分触发来源 ——
 * 验收标准明确要求「UI 点击与热键重置的 toast 文案一致」。
 */
const RESET_TOAST_TEXT = '位置已重置到屏幕底部居中';

/**
 * 数字兜底：坐标异常时不要把 `NaN` / `undefined` / `0` 打进日志和 toast。
 *
 * ⚠️ 必须先挡 `null` / `undefined`，不能直接 `Number(v)` ——
 * `Number(null) === 0` 且 `Number.isFinite(0) === true`，于是「没有坐标」
 * 会被静默转成坐标 0。这个坑本项目已经踩过一次（见 overlay-settings.cjs 顶部注释：
 * 「还没存过位置」的 null 变成坐标 0，默认位置永远算不出来）。
 */
function fmt(v) {
  if (v === null || v === undefined || v === '') return '?';
  const n = Number(v);
  return Number.isFinite(n) ? String(Math.round(n)) : '?';
}

/** 打开悬浮窗：日志。返回 `{ log }` 供 main.cjs 直接交给 logLine。 */
function describeOpen(trigger) {
  return { log: `[overlay] 已打开（触发来源=${normalizeTrigger(trigger)}）` };
}

/** 关闭悬浮窗：日志。 */
function describeClose(trigger) {
  return { log: `[overlay] 已关闭（触发来源=${normalizeTrigger(trigger)}）` };
}

/**
 * 销毁窗口（不改 enabled）：主窗口关闭 / 应用退出时用。
 *
 * 成功路径也要打日志：`destroyOverlayWindow()` 原先只在**失败**时 logLine，
 * 于是「悬浮窗怎么自己没了」在日志里同样查不出来（与 B3 同类）。
 */
function describeDestroy(trigger) {
  return { log: `[overlay] 已销毁（触发来源=${normalizeTrigger(trigger)}）` };
}

/**
 * 重置位置：日志 + toast。
 *
 * Args:
 *   state: `{ opened, x, y }` —— `opened` 表示悬浮窗当前是否真的开着
 *   trigger: 触发来源
 *
 * Returns:
 *   `{ log, toast: { text } }`
 *
 * 两点刻意的设计：
 *
 *   1. **toast 一定产出，且文案与来源无关。** B1 的验收标准就是
 *      「UI 点击与热键重置文案一致」，所以在描述层就统一掉，
 *      main.cjs 无法为某一条路径单独改文案。
 *   2. **悬浮窗没开着的情况要写进日志。** 此时 `sendToOverlay()` 会静默失败
 *      （它内部 `if (!isOverlayOpen()) return false`），用户看不到任何反馈。
 *      日志里必须留下「只更新了存储位置、没有实际显示反馈」这个事实，
 *      否则事后排查会以为 toast 发过了。
 *      （渲染层据此改在控制面板内联提示，见 OverlayControlPanel 的下方说明。）
 */
function describeReset(state, trigger) {
  const t = normalizeTrigger(trigger);
  const opened = !!(state && state.opened);
  const x = fmt(state && state.x);
  const y = fmt(state && state.y);
  const tail = opened ? '' : '；悬浮窗未打开，仅更新了存储位置';

  return {
    log: `[overlay] 位置已重置: ${x},${y}（触发来源=${t}${tail}）`,
    toast: { text: RESET_TOAST_TEXT },
  };
}

module.exports = {
  TRIGGER_SOURCES,
  RESET_TOAST_TEXT,
  normalizeTrigger,
  describeOpen,
  describeClose,
  describeDestroy,
  describeReset,
};
