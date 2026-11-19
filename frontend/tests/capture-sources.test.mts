import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  captureErrorMessage,
  normalizeCaptureSource,
  preferCourseSource,
} from '../src/lib/captureSources'

const sources = [
  { id: 'self', name: '听课伴侣', type: 'window' as const },
  { id: 'ppt', name: '生成式人工智能（二） - 腾讯会议', type: 'window' as const },
  { id: 'screen', name: '整个屏幕', type: 'screen' as const },
]

test('只保留合法的窗口/屏幕源，脏值不会进入下拉列表', () => {
  assert.deepEqual(normalizeCaptureSource(null), null)
  assert.deepEqual(normalizeCaptureSource({ id: '', name: 'x', type: 'window' }), null)
  assert.deepEqual(normalizeCaptureSource({ id: 'a', name: 'x', type: 'tab' }), null)
  assert.deepEqual(normalizeCaptureSource({ id: 'a', name: 'x', type: 'screen', thumbnail: 'data:,' }), {
    id: 'a', name: 'x', type: 'screen', thumbnail: 'data:,',
  })
})

test('D-捕获：空 name 的课程窗口用 id 兜底，不再被丢弃', () => {
  // UWP / 课程 App 的 source.name 有时是空串，原来直接判空丢掉 → 列表里找不到。
  const normalized = normalizeCaptureSource({ id: 'window:787434:0', name: '', type: 'window' })
  assert.ok(normalized, '空 name 的窗口必须保留')
  assert.match(normalized!.name, /课程窗口/)
  assert.equal(normalized!.id, 'window:787434:0')
})

test('D-捕获：窗口列表持续刷新（定时 + 焦点），切回应用即得新列表', () => {
  const ui = readFileSync(
    new URL('../src/shell/components/CourseScreenCapture.tsx', import.meta.url), 'utf8',
  )
  assert.match(ui, /setInterval\(\(\) => \{ void refreshSources\(true\) \}, 2000\)/)
  assert.match(ui, /addEventListener\('focus'/)
  assert.match(ui, /const refreshSources = useCallback\(async \(silent = false\)/, '后台静默刷新不能打断用户')
  assert.match(ui, /个源/, '窗口数要可见，让用户知道列表在更新')
})

test('默认优先非本应用窗口，选中项始终压过默认策略', () => {
  assert.equal(preferCourseSource(sources)?.id, 'ppt')
  assert.equal(preferCourseSource(sources, 'screen')?.id, 'screen')
  assert.equal(preferCourseSource([sources[0], sources[2]])?.id, 'screen')
  assert.equal(preferCourseSource([]), null)
})

test('捕获异常给出可操作提示，不把英文异常直接甩给用户', () => {
  assert.match(captureErrorMessage(Object.assign(new Error('denied'), { name: 'NotAllowedError' })), /权限/)
  assert.match(captureErrorMessage(new Error('No source')), /课程网页或课程 App 打开/)
  assert.match(captureErrorMessage(new Error('boom')), /画面捕获失败：boom/)
})

test('主进程按 id 前缀推导源类型，不依赖 Electron 缺失的 source.type', () => {
  const main = readFileSync(new URL('../electron/main.cjs', import.meta.url), 'utf8')
  assert.match(main, /startsWith\('screen:'\)/)
  assert.doesNotMatch(main, /type:\s*source\.type/)
  // 真实数据：Electron 31 返回的 source.type 是 undefined，只有 id 带前缀
  const inferType = (id) => String(id).startsWith('screen:') ? 'screen' : 'window'
  assert.equal(inferType('screen:0:0'), 'screen')
  assert.equal(inferType('window:787434:0'), 'window')
  assert.deepEqual(normalizeCaptureSource({ id: 'window:787434:0', name: '百度网盘', type: inferType('window:787434:0') })?.type, 'window')
})

test('主进程安装 display-capture 处理器，preload 只暴露受控通道', () => {
  const main = readFileSync(new URL('../electron/main.cjs', import.meta.url), 'utf8')
  const preload = readFileSync(new URL('../electron/preload.cjs', import.meta.url), 'utf8')
  assert.match(main, /setDisplayMediaRequestHandler/)
  assert.match(main, /ipcMain\.handle\('capture:list-sources'/)
  assert.match(main, /ipcMain\.handle\('capture:set-source'/)
  assert.match(preload, /capture:list-sources/)
  assert.match(preload, /capture:set-source/)
  assert.match(preload, /capture:clear-source/)
})
