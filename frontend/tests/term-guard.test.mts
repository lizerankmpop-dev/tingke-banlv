/**
 * 术语防循环降级 · 控制面板状态文案 单元测试
 *
 * 这些用例的存在理由是一次真实的信息矛盾事故（Backlog B4）：
 *
 *   面板原先读 `promptChars`（**字符串**长度）当作「降级后剩多少」，
 *   而 P0-3 之后降级改的是 token 预算、不再裁剪字符串，于是界面显示
 *   「提示词已缩减到 794 字符」—— 794 正是完整长度，自相矛盾。
 *
 * 所以这里除了钉住各分支的正确文案，还专门加了一条**回归守卫**：
 * 任何分支的文案都不得出现固定数值或实现术语（字符 / token / 预算 / 截断）。
 * 只要有人再把数字写回文案，这条断言就会失败。
 *
 * 运行：npm run test（Node 内置 test runner，无需额外依赖）
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import { buildTermGuardRows } from '../src/lib/termGuard.ts'

// ============ 保护开启 ============

test('保护开启 · 未检出 → ok，明确说没有重复', () => {
  const rows = buildTermGuardRows({ degraded: false, repetitionDetected: false }, true)

  assert.equal(rows.length, 1)
  assert.equal(rows[0].tone, 'ok')
  assert.equal(rows[0].text, '未检测到重复输出。')
})

test('保护开启 · 已降级 → warn，含丢弃条数且说明如何恢复', () => {
  const rows = buildTermGuardRows(
    { degraded: true, repetitionDetected: true, droppedTermCount: 17 },
    true,
  )

  assert.equal(rows.length, 1)
  assert.equal(rows[0].tone, 'warn')
  assert.match(rows[0].text, /已触发降级/)
  assert.match(rows[0].text, /17 个低优先级术语未参与/)
  // 降级唯一的解除方式，必须说清楚，否则用户以为软件「一直恢复不了」
  assert.match(rows[0].text, /重新开始听课后会恢复完整术语表/)
})

test('保护开启 · 已降级但丢弃条数为 0 → 不出现 0 个的从句', () => {
  for (const dropped of [0, undefined]) {
    const rows = buildTermGuardRows({ degraded: true, droppedTermCount: dropped }, true)

    assert.equal(rows.length, 1)
    assert.equal(rows[0].tone, 'warn')
    assert.match(rows[0].text, /已触发降级/)
    // 「本次有 0 个低优先级术语未参与」是废话，必须省掉
    assert.doesNotMatch(rows[0].text, /低优先级术语未参与/)
    assert.match(rows[0].text, /重新开始听课后会恢复完整术语表/)
  }
})

test('保护开启 · 已检出但未降级 → 不能谎称「未检测到」', () => {
  // 对应引擎里 cost <= DEGRADE_TOKEN_BUDGET 的分支：术语本来就不多，降无可降。
  // 这一态以前会被显示成「未检测到重复输出」。
  const rows = buildTermGuardRows({ degraded: false, repetitionDetected: true }, true)

  assert.equal(rows.length, 1)
  assert.equal(rows[0].tone, 'warn')
  assert.match(rows[0].text, /检测到字幕重复输出/)
  assert.doesNotMatch(rows[0].text, /未检测到/)
})

// ============ 保护关闭 ============

test('保护关闭 · 未检出 → warn，提示可重新打开', () => {
  const rows = buildTermGuardRows({ degraded: false, repetitionDetected: false }, false)

  assert.equal(rows.length, 1)
  assert.equal(rows[0].tone, 'warn')
  assert.match(rows[0].text, /已关闭兜底/)
  assert.match(rows[0].text, /重新打开它/)
})

test('保护关闭 · 已检出 → 说明「检出了但没处理」，且与未检出文案不同', () => {
  const detected = buildTermGuardRows({ degraded: false, repetitionDetected: true }, false)
  const clean = buildTermGuardRows({ degraded: false, repetitionDetected: false }, false)

  assert.equal(detected.length, 1)
  assert.equal(detected[0].tone, 'warn')
  assert.match(detected[0].text, /已检出字幕重复输出/)
  assert.match(detected[0].text, /自动降级已关闭/)
  assert.notEqual(detected[0].text, clean[0].text)
})

test('保护关闭 · 即便引擎报了 degraded 也要以「已关闭」为准（开关是真理源）', () => {
  // 用户刚把开关关掉时，stats 里可能还残留上一轮的 degraded=true。
  // 此刻说「已触发降级」是错的 —— 保护已经关了。
  const rows = buildTermGuardRows({ degraded: true, repetitionDetected: true }, false)

  assert.equal(rows.length, 1)
  assert.doesNotMatch(rows[0].text, /已触发降级/)
  assert.match(rows[0].text, /自动降级已关闭/)
})

test('保护关闭 · 不再额外渲染一行通用提示（避免语义重复）', () => {
  // 重构前是两行：「状态行」+「已关闭兜底行」，内容互相打架。
  const rows = buildTermGuardRows({ degraded: false, repetitionDetected: true }, false)

  assert.equal(rows.length, 1, '保护关闭时应当只有一行')
})

// ============ 无统计（引擎尚未上报）============

test('无统计 · 保护开启 → 不渲染任何状态行', () => {
  for (const guard of [null, undefined]) {
    assert.deepEqual(buildTermGuardRows(guard, true), [])
  }
})

test('无统计 · 保护关闭 → 仍要说清兜底是关着的', () => {
  for (const guard of [null, undefined]) {
    const rows = buildTermGuardRows(guard, false)

    assert.equal(rows.length, 1)
    assert.equal(rows[0].tone, 'warn')
    assert.match(rows[0].text, /已关闭兜底/)
  }
})

// ============ 回归守卫（B4 的核心）============

test('回归守卫 · 任何分支都不得出现固定数值或实现术语', () => {
  // 这些词一旦出现在用户可见文案里，就说明又有人把实现细节泄漏到了 UI，
  // 或者把可配置的数值写死了 —— 那正是 B4 的成因。
  const FORBIDDEN = ['字符', 'token', 'Token', '预算', '截断', 'initial_prompt']

  const cases: Array<[Parameters<typeof buildTermGuardRows>[0], boolean]> = [
    [{ degraded: true, repetitionDetected: true, droppedTermCount: 3 }, true],
    [{ degraded: true }, true],
    [{ degraded: true, droppedTermCount: 0 }, true],
    [{ degraded: false, repetitionDetected: true }, true],
    [{ degraded: false }, true],
    [{ repetitionDetected: true }, false],
    [{}, false],
    [null, false],
  ]

  for (const [guard, autoDegrade] of cases) {
    for (const row of buildTermGuardRows(guard, autoDegrade)) {
      for (const word of FORBIDDEN) {
        assert.ok(
          !row.text.includes(word),
          `文案出现了实现术语「${word}」：${row.text}`,
        )
      }
      // 唯一允许出现的数字是丢弃术语条数，且必须带单位说明「个…术语」
      const digits = row.text.match(/\d+/g) ?? []
      assert.ok(digits.length <= 1, `文案出现了多个数字，可能绑定了固定数值：${row.text}`)
    }
  }
})

test('回归守卫 · 丢弃条数只作为绝对条数出现，不出现百分比', () => {
  // 分母（完整预算）随配置变化，百分比迟早变成错误信息。
  const rows = buildTermGuardRows({ degraded: true, droppedTermCount: 42 }, true)

  assert.match(rows[0].text, /42 个/)
  assert.doesNotMatch(rows[0].text, /%/)
})

test('回归守卫 · 丢弃条数为 NaN / 负数 / 小数时不产生奇怪文案', () => {
  const cases: Array<number> = [Number.NaN, -5, Number.POSITIVE_INFINITY]

  for (const dropped of cases) {
    const rows = buildTermGuardRows({ degraded: true, droppedTermCount: dropped }, true)

    assert.equal(rows.length, 1)
    assert.doesNotMatch(rows[0].text, /低优先级术语未参与/)
    // 且不能出现 NaN / Infinity 字面量
    assert.doesNotMatch(rows[0].text, /NaN|Infinity/)
  }

  // 小数向下取整，不出现「3.7 个」
  const frac = buildTermGuardRows({ degraded: true, droppedTermCount: 3.7 }, true)
  assert.match(frac[0].text, /3 个低优先级术语未参与/)
})
