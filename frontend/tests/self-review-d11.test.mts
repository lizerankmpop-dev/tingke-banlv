import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const review = readFileSync(
  new URL('../src/shell/pages/ReviewPage.tsx', import.meta.url), 'utf8',
)
const css = readFileSync(new URL('../src/shell/limeng.css', import.meta.url), 'utf8')

test('D11 复习页有到期复习 / 自主复习双模式', () => {
  assert.match(review, /useState<'due' \| 'self'>\('due'\)/)
  assert.match(review, /role="tab"/)
  assert.match(review, /自主复习/)
  assert.match(css, /\.review-mode-switch/)
})

test('D11 自主复习按课程与关键词挑词，并复用 SM2 评分', () => {
  assert.match(review, /vocabularyApi\.list\(course\.id\)/)
  assert.match(review, /filteredPool\.map/)
  assert.match(review, /togglePick\(card\)/)
  assert.match(review, /setQueue\(chosen\)/)
  assert.match(review, /await vocabularyApi\.review\(/)
})

test('D11 无到期词时可一键切自主复习', () => {
  assert.match(review, /今天没有到期的词/)
  assert.match(review, /<button type="button" className="btn btn-primary btn-sm" onClick=\{\(\) => setMode\('self'\)\}/)
})
