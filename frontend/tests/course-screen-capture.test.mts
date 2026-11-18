import test from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { CourseScreenCapture } from '../src/shell/components/CourseScreenCapture'

test('线上课默认展示窗口画面捕获，不再把粘贴网址作为主路径', () => {
  const html = renderToStaticMarkup(createElement(CourseScreenCapture, {
    courseName: '生成式人工智能（二）',
    onToast: () => {},
  }))
  assert.match(html, /窗口画面/)
  assert.match(html, /选择课程网页/)
  assert.match(html, /开始捕获/)
  assert.match(html, /系统音频/)
  assert.doesNotMatch(html, /<webview\b/)
  assert.doesNotMatch(html, /粘贴课程网页链接/)
})
