import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8')
const page = read('src/shell/pages/RecognitionPage.tsx')
const store = read('src/stores/useSubtitleStore.ts')
const capture = read('src/shell/components/CourseScreenCapture.tsx')

test('暂停识别时清当前句并收起悬浮窗，不再停在上一句', () => {
  assert.match(store, /clearCurrentUtterance/)
  assert.match(store, /set\(\{ currentText: '', currentWords: \[\] \}\)/)
  assert.match(page, /clearCurrentUtterance\(\)/)
  assert.match(page, /overlayToggle\(\)/)
  assert.match(page, /识别已暂停，历史字幕已保留/)
})

test('备用网址与窗口捕获互斥：切网址时卸载捕获组件并清源', () => {
  assert.match(page, /!showLegacyWeb \? \([\s\S]*CourseScreenCapture/)
  assert.match(page, /<WebFrame[\s\S]*\/>/)
  assert.match(page, /capture\?\.clearSource\?\.\(\)/)
})

test('捕获黑屏连续多次自动停止，不留空边框', () => {
  assert.match(capture, /blackCountRef/)
  assert.match(capture, /blackCountRef\.current >= 3/)
  assert.match(capture, /已自动停止捕获/)
  assert.match(capture, /setInterval\(\(\) => \{ void detectBlackFrame\(\) \}, 2500\)/)
  assert.match(capture, /window\.clearInterval/)
})
