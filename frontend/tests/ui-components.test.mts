/**
 * 通用 UI 层（ui/）单元测试
 *
 * 覆盖 2026-11-12 从李梓萌界面原型 v1.1 移植的 6 个组件：
 *   LevelMeter / IconButton / StatusDot / Pill / Toast / EmptyState
 *
 * ## 这组测试除了测行为，还测「移植纪律」
 *
 * 原型是 `.jsx` + 内存假数据 + 无路由的**另一条并行实现**，从未合进过主线。
 * 移植最大的风险不是「组件写错」，而是**把原型的假数据带进主线**、
 * 或者**把设计色值写死**（那样主题一改就散架）。所以这里有两条纪律守卫：
 *   ① 组件源码里不得出现原型假数据的字段名；
 *   ② 组件 CSS 里不得出现写死的 hex（必须走 var(--x)）。
 *
 * 判据用 SSR（`renderToStaticMarkup`）而不是 DOM 环境：这些组件都是纯展示，
 * SSR 能测出「渲染结构对不对」，且不需要 jsdom（本项目没有装）。
 */

import test from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { LevelMeter } from '../src/components/ui/LevelMeter'
import { IconButton } from '../src/components/ui/IconButton'
import { StatusDot } from '../src/components/ui/StatusDot'
import { Pill } from '../src/components/ui/Pill'
import { Toast } from '../src/components/ui/Toast'
import { EmptyState } from '../src/components/ui/EmptyState'

const HERE = dirname(fileURLToPath(import.meta.url))
const UI_DIR = join(HERE, '..', 'src', 'components', 'ui')

/**
 * 去掉注释（块注释与行注释），只留真正会被执行的代码。
 *
 * 与 css-vars.test.mts 里那份**同源**：注释里常写「这里本应写 var(--accent)」
 * 这类示例，它不是真引用。不剔除注释 ⇒ 文档示例被误判成「未定义变量」，
 * 写注释的人越多误报越多，最后大家就不信这个守卫了。
 */
function stripComments(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/\/\/.*$/gm, '');
}

/* ────────────────────────── 行为 ────────────────────────── */

test('LevelMeter 渲染指定段数的电平柱', () => {
  const html = renderToStaticMarkup(createElement(LevelMeter, { bars: 26 }))
  const bars = (html.match(/<span/g) || []).length
  assert.equal(bars, 26, '应有 26 段电平柱')
})

test('LevelMeter 段数可变，tone 落到 class 上', () => {
  const html = renderToStaticMarkup(createElement(LevelMeter, { bars: 8, tone: 'purple' }))
  assert.equal((html.match(/<span/g) || []).length, 8)
  assert.ok(html.includes('level-meter purple'), 'purple tone 应落到 class')
})

test('LevelMeter active 时加 active class（波形脉动）', () => {
  const on = renderToStaticMarkup(createElement(LevelMeter, { active: true }))
  const off = renderToStaticMarkup(createElement(LevelMeter, { active: false }))
  assert.ok(on.includes('level-meter cyan active'), 'active 应加 class')
  assert.ok(!off.includes('active'), '非 active 不应带 active')
})

test('IconButton 把 label 同时用作 title 与 aria-label', () => {
  const html = renderToStaticMarkup(
    createElement(IconButton, { label: '开始识别' }, createElement('span', null, '▶')),
  )
  assert.ok(html.includes('aria-label="开始识别"'))
  assert.ok(html.includes('title="开始识别"'))
  assert.ok(html.includes('▶'))
})

test('IconButton label 为空时不输出空 aria-label（读屏不能拿到空名字）', () => {
  const html = renderToStaticMarkup(
    createElement(IconButton, { label: '' }, createElement('span', null, '?')),
  )
  assert.ok(!html.includes('aria-label'), '不该有 aria-label')
  assert.ok(!html.includes('title='), '不该有 title')
})

test('IconButton active 与 disabled 落到 class / 属性', () => {
  const active = renderToStaticMarkup(createElement(IconButton, { label: 'x', active: true }))
  assert.ok(active.includes('icon-btn active'))
  const disabled = renderToStaticMarkup(createElement(IconButton, { label: 'x', disabled: true }))
  assert.ok(disabled.includes('disabled'))
})

test('StatusDot 默认 green，四档 tone 全部可渲染', () => {
  assert.ok(renderToStaticMarkup(createElement(StatusDot)).includes('status-dot green'))
  for (const tone of ['cyan', 'purple', 'muted'] as const) {
    assert.ok(
      renderToStaticMarkup(createElement(StatusDot, { tone })).includes(`status-dot ${tone}`),
      `${tone} tone 应渲染`,
    )
  }
})

test('Pill 默认 purple，内容与 tone 都进 DOM', () => {
  const html = renderToStaticMarkup(createElement(Pill, null, '线下'))
  assert.ok(html.includes('pill purple'))
  assert.ok(html.includes('线下'))
})

test('Toast 有 message 才渲染，且带 role=status', () => {
  const empty = renderToStaticMarkup(createElement(Toast, { message: '' }))
  assert.equal(empty, '', '空 message 不应渲染任何东西')
  const html = renderToStaticMarkup(createElement(Toast, { message: '已保存' }))
  assert.ok(html.includes('role="status"'))
  assert.ok(html.includes('已保存'))
})

test('EmptyState 渲染标题、描述与操作区', () => {
  const html = renderToStaticMarkup(
    createElement(EmptyState, {
      title: '还没有课程',
      description: '新建一门课开始上课',
      action: createElement('button', null, '新建'),
    }),
  )
  assert.ok(html.includes('empty-state'))
  assert.ok(html.includes('还没有课程'))
  assert.ok(html.includes('新建一门课开始上课'))
  assert.ok(html.includes('新建'))
})

/* ────────────────────── 移植纪律守卫 ────────────────────── */

/** 原型的内存假数据（data.js）里最容易被误搬进来的字段名 */
const PROTOTYPE_FAKE_DATA_FIELDS = ['courses:', 'lessons:', 'mockWords', 'fakeData', 'demoData']

test('移植纪律 · ui/ 组件不得引入原型的内存假数据', () => {
  for (const file of ['LevelMeter.tsx', 'IconButton.tsx', 'StatusDot.tsx', 'Pill.tsx', 'Toast.tsx', 'EmptyState.tsx']) {
    const text = readFileSync(join(UI_DIR, file), 'utf8')
    for (const field of PROTOTYPE_FAKE_DATA_FIELDS) {
      assert.ok(
        !text.includes(field),
        `${file} 出现了原型假数据特征「${field}」—— 移植只许带组件，不许带 data.js`,
      )
    }
  }
})

test('移植纪律 · ui/ 组件源码不得写死 hex 色值（必须走 var(--x)）', () => {
  for (const file of ['LevelMeter.tsx', 'IconButton.tsx', 'StatusDot.tsx', 'Pill.tsx', 'Toast.tsx', 'EmptyState.tsx']) {
    const text = readFileSync(join(UI_DIR, file), 'utf8')
    // 去掉注释再扫（注释里可以写「这里本应是 #fff」）
    const code = stripComments(text)
    const hex = code.match(/#[0-9a-fA-F]{3,8}\b/g)
    assert.equal(hex, null, `${file} 写死了色值 ${hex}，必须走 var(--x)`)
  }
})

test('移植纪律 · ui.css 不得写死 hex / rgb 色值（必须走 var(--x)）', () => {
  const css = readFileSync(join(UI_DIR, 'ui.css'), 'utf8')
  const code = stripComments(css)
  // rgba(0, 0, 0, 0.35) 是纯阴影，不属于「色值」；其余一律不许
  const offenders = code
    .replace(/rgba\(\s*0,\s*0,\s*0,\s*[\d.]+\s*\)/g, '')
    .match(/#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\)/g)
  assert.equal(offenders, null, `ui.css 写死了色值 ${offenders}，必须走 var(--x)`)
})

test('移植纪律 · ui.css 里引用的每个 var(--x) 都必须在 global.css 里定义', () => {
  const css = readFileSync(join(UI_DIR, 'ui.css'), 'utf8')
  const global = readFileSync(join(HERE, '..', 'src', 'styles', 'global.css'), 'utf8')
  const defined = new Set([...global.matchAll(/(--[a-zA-Z0-9_-]+)\s*:/g)].map(m => m[1]))
  const used = [...stripComments(css).matchAll(/var\(\s*(--[a-zA-Z0-9_-]+)/g)].map(m => m[1])
  const missing = [...new Set(used)].filter(v => !defined.has(v))
  assert.deepEqual(missing, [], `ui.css 引用了未定义的变量：${missing.join(', ')}`)
})

test('移植纪律 · 原型的水印字样不得进入成品', () => {
  for (const file of ['LevelMeter.tsx', 'IconButton.tsx', 'StatusDot.tsx', 'Pill.tsx', 'Toast.tsx', 'EmptyState.tsx', 'ui.css']) {
    const text = readFileSync(join(UI_DIR, file), 'utf8')
    assert.ok(!text.includes('界面初稿'), `${file} 出现了原型水印「界面初稿」`)
  }
})
