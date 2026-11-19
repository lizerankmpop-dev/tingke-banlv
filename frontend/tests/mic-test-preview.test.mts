import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = (p: string) => readFileSync(new URL(p, import.meta.url), 'utf8')

const config = read('../src/shell/pages/ConfigPage.tsx')
const main = read('../electron/main.cjs')
const css = read('../src/shell/limeng.css')

test('配置页试音请求带 preview 与课程识别语言', () => {
  assert.match(config, /preview: true, language/, '试音请求必须把预览开关和识别语言送到后端')
  assert.match(config, /\/api\/audio\/mic-test/)
  assert.match(config, /正在试音并生成字幕…/)
})

test('试音字幕卡片展示转写结果或降级提示', () => {
  assert.match(config, /试音字幕/)
  assert.match(config, /micResult\.transcript \|\| micResult\.previewMessage/)
  assert.match(config, /试音只显示字幕预览，不写入课程，也不保存录音。/)
  assert.match(css, /\.field\.mic-transcript/, '试音字幕卡片必须有样式，不能裸奔')
})

test('合并张春阳增量后仍保留 v4.1.4 窗口捕获修复', () => {
  // 他的包基于旧版、整包覆盖会丢掉窗口捕获；这条守卫专门防这次合并事故。
  assert.match(main, /capture:list-sources/)
  assert.match(main, /setDisplayMediaRequestHandler/)
  assert.match(main, /startsWith\('screen:'\)/, 'source.type 缺失时按 id 前缀推导的修复不能丢')
  assert.doesNotMatch(main, /type:\s*source\.type/, '不能退回透传 Electron 31 缺失的 type')
})

test('版本号四处同步（升版时不能漏改出口）', () => {
  const pkg = JSON.parse(read('../package.json'))
  const lock = JSON.parse(read('../package-lock.json'))
  const app = read('../../backend/main.py')
  const shell = read('../src/shell/Shell.tsx')
  const v = pkg.version

  assert.equal(lock.version, v)
  assert.equal(lock.packages[''].version, v)
  assert.match(app, new RegExp(`APP_VERSION\\s*=\\s*"${v}"`))
  assert.match(shell, new RegExp(`version \\|\\| '${v}'`))
})
