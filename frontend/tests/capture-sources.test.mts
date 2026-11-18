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
