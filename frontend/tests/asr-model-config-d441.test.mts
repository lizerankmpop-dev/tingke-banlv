import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8')
const page = read('src/shell/pages/ConfigPage.tsx')
const types = read('src/types/index.ts')
const css = read('src/shell/limeng.css')

test('v4.4.1 课程配置页提供识别模型选择', () => {
  assert.match(page, /\/api\/asr\/config/)
  assert.match(page, /识别模型/)
  assert.match(page, /changeModel/)
  assert.match(page, /modelSize/)
  assert.match(page, /medium/)
})

test('模型选择展示本地就绪状态与切换生效提示', () => {
  assert.match(page, /installed/)
  assert.match(page, /更准/)
  assert.match(page, /modelTakesEffectOnNextStart/)
  assert.match(page, /asr-model-hint/)
})

test('Course 类型与样式 token 已落地', () => {
  assert.match(types, /modelSize\?/)
  assert.match(css, /\.asr-model-hint/)
  assert.match(css, /--accent/)
})
