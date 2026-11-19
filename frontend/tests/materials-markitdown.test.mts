import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const config = readFileSync(
  new URL('../src/shell/pages/ConfigPage.tsx', import.meta.url), 'utf8',
)

test('D2 资料上传文案覆盖 Office/图片格式', () => {
  assert.match(config, /PDF \/ PPT \/ DOCX \/ XLSX \/ TXT \/ MD \/ 图片/)
  assert.match(config, /自动转成文字进入 AI 上下文/)
})

test('D2 图片资料给出联网提示', () => {
  assert.match(config, /IMAGE_EXTS/)
  assert.match(config, /\.png.*\.jpg.*\.jpeg.*\.gif.*\.webp/)
  assert.match(config, /图片需联网解析/)
})

test('D2 白名单扩展已同步到后端', () => {
  const service = readFileSync(
    new URL('../../backend/materials/service.py', import.meta.url), 'utf8',
  )
  assert.match(service, /markitdown/)
  assert.match(service, /MARKITDOWN_EXTS/)
  assert.match(service, /result\.markdown|'markdown'/)
})
