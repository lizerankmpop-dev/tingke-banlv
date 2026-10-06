/**
 * 首启向导状态（`<数据目录>/onboarding.json`）
 *
 * 为什么单独成一个模块：这段代码决定「要不要弹向导」「走到第几步」「完成后还弹不弹」，
 * 三件事都**只能靠重启应用来人工验证**，而它们又恰恰是最容易写错的部分
 * （判断顺序错一个分支，用户就会每次开机都被弹一遍）。
 * 因此按项目既定做法，把决策抽成**不 require('electron') 的纯模块**，
 * 由 `frontend/tests/onboarding-state.test.cjs` 覆盖全部分支；main.cjs 只负责
 * 把 fs 与真实路径注入进来。
 *
 * 存储位置与 `overlay.json` / `logs/` 同级（都在 electron/main.cjs 的 `dataHome()`），
 * 这一点与设计稿里写的「`<userData>` = %APPDATA%/听课伴侣」不同，原因见
 * `docs/m3/onboarding_wizard.md` §2.1 的实现说明：`dataHome()` 支持
 * `TINGKEBANLV_HOME` 覆盖，冒烟脚本与实装验证都靠它把状态隔离到临时目录；
 * 若改用 `app.getPath('userData')`，跑一次冒烟就会读写真机上的用户配置。
 */

const { writeConfigAtomic } = require('./overlay-settings.cjs');

const SCHEMA_VERSION = 1;

/** 向导共 6 步（步号 1-based；`stepReached: 0` 表示「还没开始」） */
const TOTAL_STEPS = 6;

/**
 * 出厂默认。
 *
 * `completed` 与 `dismissedAt` **都算「不再自动弹出」** —— 用户点了跳过就必须
 * 尊重他，否则这个按钮等于没用。但两者分开记：跳过率与完成率是两个完全不同的
 * 指标，合并成一个布尔就再也算不出来了。
 */
const DEFAULT_ONBOARDING = Object.freeze({
  schemaVersion: SCHEMA_VERSION,
  startedAt: null,
  stepReached: 0,
  completed: false,
  dismissedAt: null,
  durationMs: null,
  lastShownVersion: null,
  seenHotkeys: {},
});

function strOrNull(v) {
  return typeof v === 'string' && v.trim() !== '' ? v : null;
}

/**
 * 步号归一。
 *
 * ⚠️ 本项目已经踩过两次的坑：`Number(null) === 0` 且 `Number.isFinite(0)` 为真，
 * 于是「缺字段」会被静默当成合法的 0。所以必须**先**挡掉 null/undefined/''
 * 再判 isFinite —— 否则一个只写了 `{"completed": true}` 的残缺文件会被读成
 * 「走到第 0 步就完成了」，日志与埋点全部失真。
 */
function stepOrZero(v) {
  if (v === null || v === undefined || v === '') return 0;
  const n = Number(v);
  if (!Number.isFinite(n)) return 0;
  const i = Math.floor(n);
  if (i < 0) return 0;
  return i > TOTAL_STEPS ? TOTAL_STEPS : i;
}

function timeOrNull(v) {
  if (typeof v !== 'string' || v.trim() === '') return null;
  const t = Date.parse(v);
  return Number.isFinite(t) ? v : null;
}

/** 把任意输入（含残缺 / 垃圾 / 旧版本）归一成完整状态；永不抛异常 */
function normalizeOnboarding(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return { ...DEFAULT_ONBOARDING, seenHotkeys: {} };
  }
  const seen = raw.seenHotkeys && typeof raw.seenHotkeys === 'object' && !Array.isArray(raw.seenHotkeys)
    ? { ...raw.seenHotkeys }
    : {};
  const duration = Number(raw.durationMs);

  return {
    schemaVersion: SCHEMA_VERSION,
    startedAt: timeOrNull(raw.startedAt),
    stepReached: stepOrZero(raw.stepReached),
    // 严格只认 true：写成 "true" / 1 的一律按未完成处理 —— 宁可多弹一次，
    // 也不能因为一个手改坏了的文件让向导永远消失（那是不可逆的用户侧损失）。
    completed: raw.completed === true,
    dismissedAt: timeOrNull(raw.dismissedAt),
    durationMs: raw.durationMs === null || raw.durationMs === undefined
      ? null
      : (Number.isFinite(duration) && duration >= 0 ? Math.round(duration) : null),
    lastShownVersion: strOrNull(raw.lastShownVersion),
    seenHotkeys: seen,
  };
}

/**
 * 读状态（永不抛异常）
 *
 * 与 `overlay.json` 的处理有一处**刻意的不同**：损坏时不做 `.bad` 备份改名。
 * overlay.json 承载的是用户调过的字号/位置，丢了要能人工找回；
 * 而 onboarding.json 的最坏后果只是「向导再弹一次」，属于完全可接受的降级。
 * 为它多留一个垃圾文件不划算。
 *
 * @returns {{ state: object, reason: 'ok'|'absent'|'corrupt', error?: string }}
 */
function readOnboarding(fs, filePath) {
  try {
    if (!fs.existsSync(filePath)) {
      return { state: normalizeOnboarding(null), reason: 'absent' };
    }
    const raw = JSON.parse(fs.readFileSync(filePath, 'utf8'));
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
      return { state: normalizeOnboarding(null), reason: 'corrupt', error: '顶层不是对象' };
    }
    return { state: normalizeOnboarding(raw), reason: 'ok' };
  } catch (err) {
    return {
      state: normalizeOnboarding(null),
      reason: 'corrupt',
      error: err && err.message ? err.message : String(err),
    };
  }
}

/** 原子落盘（复用 overlay-settings 里已单测覆盖的写盘实现） */
function writeOnboarding(fs, filePath, state) {
  return writeConfigAtomic(fs, filePath, normalizeOnboarding(state));
}

/**
 * 是否应当自动弹出向导（判断顺序是这段逻辑的全部价值）
 *
 * `force` 必须排在**最前面**：它是「重新查看新手引导」入口，语义是
 * 「用户此刻想看」，优先级高于任何自动判断 —— 包括兼容模式与「已完成」。
 * 设计稿 §2.3 写的「兼容模式下推迟到用户手动触发」正是这个意思：
 * 推迟的是**自动**弹出，不是禁止。把 force 放到后面写，手动入口在兼容模式下
 * 就会静默失效（而且不会有任何报错）。
 *
 * @returns {{ show: boolean, reason: string }}
 */
function shouldShowWizard(state, ctx) {
  const s = normalizeOnboarding(state);
  const c = ctx && typeof ctx === 'object' ? ctx : {};

  if (c.force === true) return { show: true, reason: 'forced' };
  // 兼容模式下渲染环境本身不稳定，再叠一个模态向导只会放大故障面
  if (c.compatMode) return { show: false, reason: 'compat-mode' };
  // 后端未就绪时第 3 步读不到模型状态，只能显示假进度 —— 等就绪再弹
  if (c.backendReady === false) return { show: false, reason: 'backend-not-ready' };
  if (s.completed) return { show: false, reason: 'completed' };
  if (s.dismissedAt) return { show: false, reason: 'dismissed' };
  return { show: true, reason: 'first-run' };
}

/** 人类可读耗时：`41.2s` / `1m12s`（用于日志与埋点，不参与判断） */
function humanDuration(ms) {
  // ⚠️ Number(null) === 0（本项目第四次踩到同一形状的坑）：不先挡掉，
  // 「未知耗时」会被记成 0.0s —— 埋点看起来一切正常，数据全是假的。
  if (ms === null || ms === undefined || ms === '') return '?';
  const n = Number(ms);
  if (!Number.isFinite(n) || n < 0) return '?';
  const total = n / 1000;
  if (total < 60) return `${total.toFixed(1)}s`;
  const m = Math.floor(total / 60);
  const s = Math.round(total - m * 60);
  return s === 60 ? `${m + 1}m0s` : `${m}m${s}s`;
}

function nowIso(at) {
  if (typeof at === 'string' && Number.isFinite(Date.parse(at))) return at;
  if (at instanceof Date) return at.toISOString();
  return new Date().toISOString();
}

/** 记为已开始（写 startedAt 只写一次，重复调用不覆盖） */
function markStarted(state, at) {
  const s = normalizeOnboarding(state);
  return { ...s, startedAt: s.startedAt || nowIso(at), stepReached: Math.max(s.stepReached, 1) };
}

/** 记录「走到过的最深一步」—— 只增不减，它衡量的是用户卡在哪，不是当前在哪 */
function markStep(state, step) {
  const s = normalizeOnboarding(state);
  return { ...s, stepReached: Math.max(s.stepReached, stepOrZero(step)) };
}

function markCompleted(state, at) {
  const s = normalizeOnboarding(state);
  const end = nowIso(at);
  const started = s.startedAt ? Date.parse(s.startedAt) : NaN;
  const endMs = Date.parse(end);
  const duration = Number.isFinite(started) && Number.isFinite(endMs) && endMs >= started
    ? endMs - started
    : s.durationMs;
  return { ...s, completed: true, durationMs: duration };
}

function markDismissed(state, at) {
  const s = normalizeOnboarding(state);
  return { ...s, dismissedAt: nowIso(at) };
}

/**
 * 把一次事件翻译成「新状态 + 日志」（副作用决策集中在这里，调用点只声明发生了什么）
 *
 * 与 `overlay-actions.cjs` 是同一套做法：日志文案只有这一份来源，
 * 主进程与测试读到的是同一个字符串。
 *
 * @returns {{ state: object, log: string, event: string }}
 */
function describeEvent(state, event, payload) {
  const s = normalizeOnboarding(state);
  const p = payload && typeof payload === 'object' ? payload : {};
  const at = p.at;

  switch (event) {
    case 'start':
      return { state: markStarted(s, at), log: '[onboarding] 向导已打开', event };
    case 'step': {
      const next = markStep(s, p.step);
      return { state: next, log: `[onboarding] 步骤=${next.stepReached}/${TOTAL_STEPS}`, event };
    }
    case 'complete': {
      const next = markCompleted(s, at);
      return {
        state: next,
        log: `[onboarding] 完成 步骤=${next.stepReached}/${TOTAL_STEPS} 耗时=${humanDuration(next.durationMs)}`,
        event,
      };
    }
    case 'dismiss': {
      const next = markDismissed(s, at);
      return {
        state: next,
        log: `[onboarding] 跳过 步骤=${next.stepReached}/${TOTAL_STEPS}`,
        event,
      };
    }
    default:
      return { state: s, log: `[onboarding] 未知事件 ${String(event)}（已忽略）`, event: 'unknown' };
  }
}

module.exports = {
  SCHEMA_VERSION,
  TOTAL_STEPS,
  DEFAULT_ONBOARDING,
  normalizeOnboarding,
  readOnboarding,
  writeOnboarding,
  shouldShowWizard,
  humanDuration,
  markStarted,
  markStep,
  markCompleted,
  markDismissed,
  describeEvent,
};
