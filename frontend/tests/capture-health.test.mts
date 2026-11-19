import test from 'node:test'
import assert from 'node:assert/strict'
import { readCaptureHealth } from '../src/lib/classroomAudio'
import { readFileSync } from 'node:fs'

test('采集健康：只有明确的布尔 false 判为不健康，缺字段向后兼容', () => {
  assert.equal(readCaptureHealth({ captureAlive: false }).captureAlive, false)
  assert.equal(readCaptureHealth({ captureAlive: true }).captureAlive, true)
  assert.equal(readCaptureHealth({}).captureAlive, null, '旧后端没有该字段，不能制造误报')
  assert.equal(readCaptureHealth({ captureAlive: 'false' }).captureAlive, null)
  assert.equal(readCaptureHealth({ audioChunks: 12 }).audioChunks, 12)
  assert.equal(readCaptureHealth({ audioChunks: -1 }).audioChunks, -1) // 数字即收，语义由后端保证
})

test('轮询里拦截采集线程死亡（防止回到假识别）', () => {
  const store = readFileSync(
    new URL('../src/stores/useSubtitleStore.ts', import.meta.url), 'utf8',
  )
  assert.match(store, /readCaptureHealth\(data\)/)
  assert.match(store, /health\.captureAlive === false/)
  assert.match(store, /音频采集已中断/)
})
