/**
 * SM2 排程算法单元测试
 *
 * 排程算错是「静默失败」：界面照常显示，只是某些词再也不出现（排到几十年后）
 * 或者每次打开都出现（排到过去）。所以这里把每条分支都钉住。
 *
 * 运行：npm run test（Node 内置 test runner，无需额外依赖）
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  applyRating,
  formatNextReview,
  isDue,
  normalizeReviewState,
  summarizeSession,
  DEFAULT_EFACTOR,
  FIRST_EASY_DAYS,
  FIRST_GOOD_DAYS,
  MAX_EFACTOR,
  MAX_INTERVAL_DAYS,
  MIN_EFACTOR,
  RELEARN_MINUTES,
  SECOND_GOOD_DAYS,
} from '../src/lib/sm2.ts'

const DAY = 86_400_000
const MIN = 60_000
const NOW = 1_700_000_000_000

const newWord = { efactor: DEFAULT_EFACTOR, interval: 0, repetitions: 0 }

test('新词 good：走固定阶梯 1 天', () => {
  const s = applyRating(newWord, 'good', NOW)
  assert.equal(s.interval, FIRST_GOOD_DAYS)
  assert.equal(s.repetitions, 1)
  assert.equal(s.efactor, DEFAULT_EFACTOR, 'good 不应改变 efactor')
  assert.equal(s.nextReviewAt, NOW + FIRST_GOOD_DAYS * DAY)
})

test('第二次 good：阶梯 3 天', () => {
  const s = applyRating({ efactor: DEFAULT_EFACTOR, interval: 1, repetitions: 1 }, 'good', NOW)
  assert.equal(s.interval, SECOND_GOOD_DAYS)
  assert.equal(s.repetitions, 2)
})

test('第三次及以后 good：interval * efactor', () => {
  const s = applyRating({ efactor: 2.5, interval: 3, repetitions: 2 }, 'good', NOW)
  assert.equal(s.interval, 7.5)
  assert.equal(s.repetitions, 3)
  // 连续推进必须单调递增，否则词会越复习越频繁
  const next = applyRating(s, 'good', NOW)
  assert.ok(next.interval > s.interval, '间隔必须递增')
  assert.ok(next.nextReviewAt > s.nextReviewAt)
})

test('again：清零连对次数、拉低难度因子、10 分钟后重来', () => {
  const s = applyRating({ efactor: 2.5, interval: 60, repetitions: 8 }, 'again', NOW)
  assert.equal(s.repetitions, 0)
  assert.equal(s.interval, 0)
  assert.equal(s.efactor, 2.3)
  assert.equal(s.nextReviewAt, NOW + RELEARN_MINUTES * MIN)
})

test('again 反复使用不会把 efactor 压到 1.3 以下', () => {
  let state = { efactor: 1.4, interval: 0, repetitions: 0 }
  for (let i = 0; i < 20; i++) state = applyRating(state, 'again', NOW)
  assert.equal(state.efactor, MIN_EFACTOR)
})

test('hard：难度因子下降，间隔只小幅增加；新词给 1 天', () => {
  const fresh = applyRating(newWord, 'hard', NOW)
  assert.equal(fresh.interval, FIRST_GOOD_DAYS, '新词用 hard 不应退化成 10 分钟')
  assert.equal(fresh.efactor, 2.35)
  assert.ok(fresh.repetitions >= 1)

  const mature = applyRating({ efactor: 2.5, interval: 10, repetitions: 4 }, 'hard', NOW)
  assert.equal(mature.interval, 12)
  assert.ok(mature.interval < 10 * 2.5, 'hard 的增幅必须明显小于 good')
})

test('easy：难度因子上升，间隔增幅大于 good', () => {
  const fresh = applyRating(newWord, 'easy', NOW)
  assert.equal(fresh.interval, FIRST_EASY_DAYS)
  assert.equal(fresh.efactor, 2.65)

  const mature = applyRating({ efactor: 2.5, interval: 10, repetitions: 3 }, 'easy', NOW)
  const good = applyRating({ efactor: 2.5, interval: 10, repetitions: 3 }, 'good', NOW)
  assert.ok(mature.interval > good.interval, 'easy 的间隔必须大于 good')
})

test('四档排序：同样状态下 again < hard < good < easy', () => {
  const base = { efactor: 2.5, interval: 10, repetitions: 3 }
  const at = (r: 'again' | 'hard' | 'good' | 'easy') => applyRating(base, r, NOW).nextReviewAt
  assert.ok(at('again') < at('hard'))
  assert.ok(at('hard') < at('good'))
  assert.ok(at('good') < at('easy'))
})

test('间隔不会超过上限', () => {
  const s = applyRating({ efactor: MAX_EFACTOR, interval: MAX_INTERVAL_DAYS, repetitions: 20 }, 'easy', NOW)
  assert.equal(s.interval, MAX_INTERVAL_DAYS)
  assert.equal(s.efactor, MAX_EFACTOR)
})

test('normalizeReviewState 容错：脏数据回退默认值', () => {
  assert.deepEqual(
    normalizeReviewState(null),
    { efactor: DEFAULT_EFACTOR, interval: 0, repetitions: 0 }
  )
  assert.deepEqual(
    normalizeReviewState({ efactor: NaN, interval: -5, repetitions: -1 }),
    { efactor: DEFAULT_EFACTOR, interval: 0, repetitions: 0 }
  )
  assert.deepEqual(
    normalizeReviewState({ efactor: 0, interval: 0, repetitions: 0 }),
    { efactor: DEFAULT_EFACTOR, interval: 0, repetitions: 0 }
  )
  // 越界值被夹取
  assert.equal(normalizeReviewState({ efactor: 99 }).efactor, MAX_EFACTOR)
  assert.equal(normalizeReviewState({ efactor: 0.1 }).efactor, MIN_EFACTOR)
  // 字符串数字（后端 JSON 里出现过）也能识别
  assert.equal(normalizeReviewState({ efactor: '2.8' as unknown as number }).efactor, 2.8)
})

test('applyRating 能直接吃后端返回的生词对象（多余字段被忽略）', () => {
  const entry = {
    efactor: 2.6, interval: 3, repetitions: 2, word: 'gradient',
    nextReviewAt: NOW, contextSentence: 'x', isMemorized: false,
  }
  const s = applyRating(entry, 'good', NOW)
  assert.equal(s.interval, 7.8)
})

test('isDue：脏数据当作待复习，不把词藏起来', () => {
  assert.equal(isDue(NOW - 1, NOW), true)
  assert.equal(isDue(NOW, NOW), true)
  assert.equal(isDue(NOW + 1, NOW), false)
  assert.equal(isDue(0, NOW), true)
  assert.equal(isDue(null, NOW), true)
  assert.equal(isDue(undefined, NOW), true)
  assert.equal(isDue(NaN, NOW), true)
})

test('formatNextReview 人类可读', () => {
  assert.equal(formatNextReview(NOW - 1000, NOW), '待复习')
  assert.equal(formatNextReview(0, NOW), '待复习')
  assert.equal(formatNextReview(NOW + 5 * MIN, NOW), '5 分钟后')
  assert.equal(formatNextReview(NOW + 5 * 3_600_000, NOW), '5 小时后')
  assert.equal(formatNextReview(NOW + 3 * DAY, NOW), '3 天后')
  assert.equal(formatNextReview(NOW + 60 * DAY, NOW), '2 个月后')
  assert.equal(formatNextReview(NOW + 800 * DAY, NOW), '2.2 年后')
})

test('summarizeSession 统计各档次数', () => {
  const s = summarizeSession(['again', 'good', 'good', 'easy', 'hard', 'again'])
  assert.deepEqual(s, { total: 6, again: 2, hard: 1, good: 2, easy: 1 })
  assert.deepEqual(summarizeSession([]), { total: 0, again: 0, hard: 0, good: 0, easy: 0 })
})

test('排程结果永远落在 now 之后（不会立刻再次到期）', () => {
  const states = [
    null, undefined, {}, { efactor: 5, interval: 3650, repetitions: 999 },
    { efactor: 1.3, interval: 0.0069, repetitions: 0 },
  ]
  for (const st of states) {
    for (const r of ['again', 'hard', 'good', 'easy'] as const) {
      const s = applyRating(st, r, NOW)
      assert.ok(s.nextReviewAt > NOW, `${r} 的 nextReviewAt 必须 > now`)
      assert.ok(s.efactor >= MIN_EFACTOR && s.efactor <= MAX_EFACTOR)
      assert.ok(s.interval >= 0 && s.interval <= MAX_INTERVAL_DAYS)
    }
  }
})
