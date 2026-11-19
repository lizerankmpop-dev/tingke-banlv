import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const page = readFileSync(
  new URL('../src/shell/pages/RecognitionPage.tsx', import.meta.url), 'utf8',
)
const css = readFileSync(new URL('../src/shell/limeng.css', import.meta.url), 'utf8')

test('D6 历史面板默认展开，会话/章节初次进课即可见', () => {
  assert.match(page, /useState\(true\)/, 'expanded 必须默认 true')
  assert.match(page, /history-group-title">会话筛选/, '会话要有独立分组标题')
  assert.match(page, /history-group-title">章节管理/, '章节要有独立分组标题')
})

test('D6 会话/章节拉取不再依赖展开状态', () => {
  // sessions 拉取的 effect 依赖里不应再有 expanded
  const effectBlock = page.slice(page.indexOf('fetch(`/api/courses/${courseId}/sessions`'))
  const deps = effectBlock.slice(0, effectBlock.indexOf('}, ['))
  assert.doesNotMatch(deps, /if \(!expanded\) return/, '拉取不能因收起而跳过')
})

test('D6 历史列表拉长且分区样式存在', () => {
  assert.match(css, /\.history-controls/)
  assert.match(css, /\.history-group-title/)
  assert.match(css, /\.history-list \{ max-height: 420px; \}/)
})
