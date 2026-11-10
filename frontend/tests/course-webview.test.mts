import test from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createRequire } from 'node:module'
import { EventEmitter } from 'node:events'
import CourseWebview from '../src/components/CourseWebview'
import { readCourseURL } from '../src/lib/courseDrop'

const require = createRequire(import.meta.url)
const { configureCourseWebview, isCourseURL } = require('../electron/course-webview.cjs')

const bilibili = 'https://www.bilibili.com/video/BV1xx411c7mD'
const drag = (uriList: string, plain = '') => ({
  getData: (format: string) => format === 'text/uri-list' ? uriList : plain,
})

test('课程网页方框空态提供拖放提示和系统浏览器兜底，未创建远程视图', () => {
  const html = renderToStaticMarkup(createElement(CourseWebview))
  assert.match(html, /拖入课程网页链接/)
  assert.match(html, /系统浏览器打开/)
  assert.doesNotMatch(html, /<webview\b/)
})

test('识别 Chrome 链接格式和纯文本网址，忽略 URI 列表注释', () => {
  assert.equal(readCourseURL(drag('# link\r\n' + bilibili + '\r\n')), bilibili)
  assert.equal(readCourseURL(drag('', bilibili)), bilibili)
})

test('文本、文件、脚本、明文 HTTP 和本地服务无法进入课程网页', () => {
  for (const value of [
    '课程链接 ' + bilibili, 'file:///C:/course.html', 'javascript:alert(1)',
    'http://example.com', 'https://127.0.0.1:8765/api', 'https://[::1]/',
    'https://0x7f000001/', 'https://foo.localhost/', 'https://home.arpa/', 'https://foo.home.arpa/',
    'https://user:password@example.com/',
  ]) {
    assert.equal(readCourseURL(drag(value)), null, value)
    assert.equal(isCourseURL(value), false, value)
  }
})

test('主进程检查课程分区、移除 guest preload，并阻止权限和本地请求', () => {
  const host = new EventEmitter()
  let requestHandler
  let permissionCheck
  const session = new EventEmitter()
  Object.assign(session, {
    setPermissionRequestHandler: () => {},
    setPermissionCheckHandler: handler => { permissionCheck = handler },
    webRequest: { onBeforeRequest: (_filter, handler) => { requestHandler = handler } },
  })
  configureCourseWebview(host, {
    fromPartition: partition => {
      assert.equal(partition, 'persist:course')
      return session
    },
  })
  const preferences = { preload: 'bad.cjs', nodeIntegration: true, webSecurity: false }
  host.emit('will-attach-webview', {}, preferences, { src: bilibili, partition: 'persist:course' })
  assert.equal(preferences.preload, undefined)
  assert.equal(preferences.nodeIntegration, false)
  assert.equal(preferences.webSecurity, true)
  assert.equal(preferences.sandbox, true)
  assert.equal(permissionCheck(), false)

  let rejected = false
  host.emit('will-attach-webview', { preventDefault: () => { rejected = true } },
    {}, { src: bilibili, partition: 'persist:other' })
  assert.equal(rejected, true)
  requestHandler({ url: 'http://127.0.0.1:8765/api/ping' }, result => {
    assert.deepEqual(result, { cancel: true })
  })
  requestHandler({ url: 'https://upos-sz-mirrorcos.bilivideo.com/course.m4s' }, result => {
    assert.deepEqual(result, { cancel: false })
  })
})
