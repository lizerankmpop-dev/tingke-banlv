/**
 * 术语提示词防循环降级 —— 控制面板状态文案（纯逻辑）
 *
 * 为什么把这段文案抽出来：
 *
 * 它历史上直接读了 `promptChars`（提示词**字符串**长度）来当「降级后剩多少」，
 * 但 P0-3 起降级改的是 token 预算、**不再裁剪字符串**，于是界面上出现了
 * 「提示词已缩减到 794 字符」这种自相矛盾的展示 —— 794 正是完整长度，
 * 根本没有缩减。用户看到字幕在重复输出、正需要确认软件是否采取了措施，
 * 却拿到一个错误的数字。
 *
 * 由此得到两条设计约束（改动本文件时请遵守）：
 *
 *   1. **文案不绑定任何固定数值。** 一旦写死「缩减到 N 字符 / N token」，
 *      底层预算参数一变就会再次漂移。本模块只输出**描述性**文案。
 *   2. **唯一允许出现的数字是运行时真实统计出来的丢弃术语条数**，
 *      它来自 `droppedTerms.length`，是本次识别的真实结果，不可能与实现脱节。
 *
 * 另外：`degraded=false` 但 `repetitionDetected=true` 是真实存在的一态
 * （用户关掉了自动降级，或降级尚未对下一轮生效）。此时说「未检测到重复输出」
 * 是错的 —— 检出了，只是没处理。三个状态必须分开表达，不能合并成布尔。
 *
 * 放在 src/lib 而非组件内，是为了能被 node --test 直接 import
 * （见 tests/term-guard.test.mts）。组件里的 JSX 逻辑跑不了单测，
 * 这与 sm2.ts / overlay-settings.cjs 的做法一致。
 */

/** `warn` = 需要用户注意（已降级 / 已检出 / 保护关闭）；`ok` = 一切正常 */
export type TermGuardTone = 'ok' | 'warn'

export interface TermGuardRow {
  tone: TermGuardTone
  text: string
}

/**
 * 只声明本模块真正读取的字段。
 *
 * 这样测试可以构造最小桩对象，也避免与 `types/index.ts` 里的完整接口强耦合 ——
 * 后端多回字段时这里不需要跟着改。
 */
export interface TermGuardStatsLike {
  /** 是否**已经真的执行过**降级（关掉保护时恒为 false） */
  degraded?: boolean
  /** 本次运行是否**至少检出过一次**重复输出（与是否降级无关） */
  repetitionDetected?: boolean
  /** 因预算不足被丢弃的术语条数 */
  droppedTermCount?: number
}

const OFF_HINT =
  '已关闭兜底：术语表保持完整。若出现重复字幕或识别变慢，请重新打开它。'

const OFF_DETECTED =
  '已检出字幕重复输出，但自动降级已关闭，术语表保持完整。若持续出现请重新打开它。'

/**
 * 「检出了、保护也开着，但没降级」——
 *
 * 对应引擎里 `cost <= DEGRADE_TOKEN_BUDGET` 的分支：参与识别的术语本来就少，
 * 再降也不会更少。**这一态以前会被显示成「未检测到重复输出」**，同样是错的。
 */
const NOOP_DETECTED =
  '检测到字幕重复输出，但参与识别的术语范围已经很小，降级不再有额外帮助。'

const CLEAN = '未检测到重复输出。'

/** 降级文案的前半句。刻意不含任何数值。 */
const DEGRADED_HEAD =
  '已触发降级：检测到字幕重复输出，已自动缩小参与识别的术语范围'

/** 降级文案的后半句：这是降级**唯一**的解除方式，必须说清楚 */
const DEGRADED_TAIL = '重新开始听课后会恢复完整术语表。'

/**
 * 构造控制面板「防循环降级」区域要渲染的状态行。
 *
 * 返回空数组 = 引擎尚未上报统计且保护处于开启状态 → 不渲染任何状态行。
 * 返回一行 = 正常情况。
 *
 * 分支优先级（保护关闭的提示优先级最高，因为它解释了「为什么没降级」）：
 *
 *   保护关闭 + 已检出  → 「检出了但没处理」       （warn）
 *   保护关闭 + 未检出  → 「兜底已关闭」           （warn）
 *   保护开启 + 已降级  → 「已降级」+ 丢弃条数     （warn）
 *   保护开启 + 已检出  → 「检出但无需降级」       （warn）
 *   保护开启 + 未检出  → 「未检测到重复输出」     （ok）
 *   无统计   + 保护关闭 → 「兜底已关闭」           （warn）
 *   无统计   + 保护开启 → 不渲染                  （[]）
 *
 * 注意 `degraded` 与 `repetitionDetected` **不能合并成一个布尔**：
 * 四个真实状态里有两个是「检出了但没降级」，而它们的原因完全不同
 * （保护被关掉 vs 术语本来就不多），文案也必须不同。
 */
export function buildTermGuardRows(
  guard: TermGuardStatsLike | null | undefined,
  autoDegrade: boolean,
): TermGuardRow[] {
  // 引擎尚未上报统计。此时唯一还能说的确定事实就是「保护开关是关着的」。
  if (!guard) {
    return autoDegrade ? [] : [{ tone: 'warn', text: OFF_HINT }]
  }

  if (!autoDegrade) {
    // 保护关着：重点永远是「术语表完整」，检出与否只改前半句。
    // 不再额外渲染一行通用的「已关闭兜底」，否则会与这里语义重复。
    return [{ tone: 'warn', text: guard.repetitionDetected ? OFF_DETECTED : OFF_HINT }]
  }

  if (guard.degraded) {
    return [{ tone: 'warn', text: DEGRADED_HEAD + formatDropped(guard.droppedTermCount) + '。' + DEGRADED_TAIL }]
  }

  if (guard.repetitionDetected) {
    return [{ tone: 'warn', text: NOOP_DETECTED }]
  }

  return [{ tone: 'ok', text: CLEAN }]
}

/**
 * 把丢弃条数格式化成可选的括号从句。
 *
 * 刻意**不用**「缩减了 N%」这类相对量：分母（完整预算）会随配置变化，
 * 百分比迟早也会变成错误信息。绝对条数则永远是本次识别的真实结果。
 */
function formatDropped(count: number | undefined): string {
  const n = Number(count)
  if (!Number.isFinite(n) || n <= 0) return ''
  return `（本次有 ${Math.floor(n)} 个低优先级术语未参与）`
}
