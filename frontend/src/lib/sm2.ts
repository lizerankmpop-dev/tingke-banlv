/**
 * SM2 间隔重复排程（Anki 风格四档）
 *
 * 为什么放在前端：复习是一个纯交互动作，排程要立刻反馈给用户
 * （「下次 3 天后」），放在前端可以零往返地更新 UI；后端只落库 + 兜底夹取
 * （见 backend/database/manager.py 的 review_vocabulary）。
 *
 * 与教科书 SM2 的差异（都是有意的）：
 *   1. 打分从 0~5 收敛成 4 档：Again / Hard / Good / Easy —— 6 档对普通学生
 *      没有可操作性，四档是 Anki 验证过的取舍
 *   2. 新词（repetitions=0）走固定阶梯 1 天 → 3 天，而不是直接 interval*EF
 *      （否则 efactor=2.5 时新词第一次「good」就会跳到 2.5 天，偏长）
 *   3. `interval` 单位是**天**，允许小数（0.0069 ≈ 10 分钟），
 *      这样「忘了」的重学间隔也能存在同一个字段里，不必再引入一个单位字段
 */

export type ReviewRating = 'again' | 'hard' | 'good' | 'easy'

/** 排程状态（对应 vocabulary 表的 efactor / review_interval / repetitions 三列） */
export interface ReviewState {
  efactor: number
  /** 上次排程的间隔，单位：天 */
  interval: number
  /** 连续答对次数；「忘了」会清零 */
  repetitions: number
}

export interface ReviewSchedule extends ReviewState {
  /** 下次到期时间（毫秒时间戳） */
  nextReviewAt: number
}

export const DEFAULT_EFACTOR = 2.5
export const MIN_EFACTOR = 1.3
export const MAX_EFACTOR = 5.0
/** 单次排程上限：10 年。超过这个数的间隔没有意义，只会掩盖算法 bug */
export const MAX_INTERVAL_DAYS = 3650
/** 「忘了」之后的重学间隔 */
export const RELEARN_MINUTES = 10
/** 新词首次答对/答「简单」的固定阶梯（天） */
export const FIRST_GOOD_DAYS = 1
export const SECOND_GOOD_DAYS = 3
export const FIRST_EASY_DAYS = 3

const DAY_MS = 86_400_000
const MINUTE_MS = 60_000

export const RATING_LABELS: Record<ReviewRating, { label: string; hint: string }> = {
  again: { label: '忘了', hint: '重新学（10 分钟后）' },
  hard: { label: '困难', hint: '间隔只小幅增加' },
  good: { label: '一般', hint: '正常推进' },
  easy: { label: '简单', hint: '间隔大幅增加' },
}

export const RATING_ORDER: ReviewRating[] = ['again', 'hard', 'good', 'easy']

function clamp(value: number, lo: number, hi: number): number {
  if (!Number.isFinite(value)) return lo
  return Math.min(hi, Math.max(lo, value))
}

function round2(value: number): number {
  return Math.round(value * 100) / 100
}

/** 容错读取排程字段：脏数据（null / 空串 / NaN）一律回退到默认值 */
export function normalizeReviewState(
  state: Partial<ReviewState> | null | undefined
): ReviewState {
  const raw = state ?? {}
  const efactor = Number(raw.efactor)
  const interval = Number(raw.interval)
  const repetitions = Number(raw.repetitions)
  return {
    efactor: Number.isFinite(efactor) && efactor > 0
      ? clamp(efactor, MIN_EFACTOR, MAX_EFACTOR)
      : DEFAULT_EFACTOR,
    interval: Number.isFinite(interval) && interval > 0
      ? clamp(interval, 0, MAX_INTERVAL_DAYS)
      : 0,
    repetitions: Number.isFinite(repetitions) && repetitions > 0
      ? Math.min(1000, Math.floor(repetitions))
      : 0,
  }
}

/**
 * 应用一次评分，返回新的排程
 *
 * @param state 当前排程（可直接传后端返回的生词对象，多余字段会被忽略）
 * @param rating 用户打分
 * @param now 当前时间戳（毫秒）—— 显式传入而不是内部取 Date.now()，这样可测
 */
export function applyRating(
  state: Partial<ReviewState> | null | undefined,
  rating: ReviewRating,
  now: number
): ReviewSchedule {
  const { efactor, interval, repetitions } = normalizeReviewState(state)

  let ef = efactor
  let iv = interval
  let reps = repetitions

  switch (rating) {
    case 'again':
      // 难度上调（下次更容易判为困难），连续次数清零
      ef = clamp(ef - 0.2, MIN_EFACTOR, MAX_EFACTOR)
      reps = 0
      iv = 0
      return {
        efactor: round2(ef),
        interval: 0,
        repetitions: 0,
        nextReviewAt: now + RELEARN_MINUTES * MINUTE_MS,
      }

    case 'hard': {
      ef = clamp(ef - 0.15, MIN_EFACTOR, MAX_EFACTOR)
      // 间隔小幅增长；新词给 1 天（10 分钟太短，学生退出复习后就再也想不起）
      iv = iv < 1 ? FIRST_GOOD_DAYS : clamp(round2(iv * 1.2), 1, MAX_INTERVAL_DAYS)
      reps = Math.max(1, reps)
      return { efactor: round2(ef), interval: iv, repetitions: reps, nextReviewAt: now + Math.round(iv * DAY_MS) }
    }

    case 'good': {
      reps += 1
      if (reps === 1) iv = FIRST_GOOD_DAYS
      else if (reps === 2) iv = SECOND_GOOD_DAYS
      else iv = clamp(round2(iv * ef), 1, MAX_INTERVAL_DAYS)
      return { efactor: round2(ef), interval: iv, repetitions: reps, nextReviewAt: now + Math.round(iv * DAY_MS) }
    }

    case 'easy': {
      ef = clamp(ef + 0.15, MIN_EFACTOR, MAX_EFACTOR)
      reps += 1
      iv = reps === 1
        ? FIRST_EASY_DAYS
        : clamp(round2(iv * ef * 1.3), 2, MAX_INTERVAL_DAYS)
      return { efactor: round2(ef), interval: iv, repetitions: reps, nextReviewAt: now + Math.round(iv * DAY_MS) }
    }

    default: {
      // 理论上不可达；用穷尽性检查兜底，避免将来加档位时漏改这里
      const exhaustive: never = rating
      throw new Error(`未知评分档位: ${String(exhaustive)}`)
    }
  }
}

/** 是否已到复习时间 */
export function isDue(nextReviewAt: number | null | undefined, now: number): boolean {
  const at = Number(nextReviewAt)
  if (!Number.isFinite(at)) return true // 脏数据 → 当作待复习，别把词藏起来
  return at <= now
}

/** 「下次 3 天后」这类人类可读描述 */
export function formatNextReview(nextReviewAt: number | null | undefined, now: number): string {
  const at = Number(nextReviewAt)
  if (!Number.isFinite(at) || at <= 0) return '待复习'
  const diff = at - now
  if (diff <= 0) return '待复习'
  const minutes = diff / MINUTE_MS
  if (minutes < 60) return `${Math.max(1, Math.round(minutes))} 分钟后`
  const hours = diff / 3_600_000
  if (hours < 24) return `${Math.round(hours)} 小时后`
  const days = diff / DAY_MS
  if (days < 30) return `${Math.round(days)} 天后`
  const months = days / 30
  if (months < 12) return `${Math.round(months)} 个月后`
  return `${(days / 365).toFixed(1)} 年后`
}

/** 复习会话完成后展示的总结用 */
export function summarizeSession(
  ratings: ReviewRating[]
): { total: number; again: number; hard: number; good: number; easy: number } {
  return {
    total: ratings.length,
    again: ratings.filter((r) => r === 'again').length,
    hard: ratings.filter((r) => r === 'hard').length,
    good: ratings.filter((r) => r === 'good').length,
    easy: ratings.filter((r) => r === 'easy').length,
  }
}
