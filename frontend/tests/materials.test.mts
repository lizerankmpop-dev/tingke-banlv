import test from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import MaterialsPanel from '../src/components/MaterialsPanel'
import { appendTerms, formatSize } from '../src/lib/materials'

test('appendTerms 保留已有术语顺序并按大小写去重', () => {
  const merged = appendTerms('gradient descent, 反向传播', ['Gradient Descent', ' sterile technique ', '反向传播'])
  assert.equal(merged, 'gradient descent，反向传播，sterile technique')
})

test('appendTerms 空输入安全', () => {
  assert.equal(appendTerms('', []), '')
  assert.equal(appendTerms('', ['静脉注射']), '静脉注射')
  assert.equal(appendTerms('已有', []), '已有')
})

test('formatSize 人类可读', () => {
  assert.equal(formatSize(0), '0 B')
  assert.equal(formatSize(512), '512 B')
  assert.equal(formatSize(2048), '2.0 KB')
  assert.equal(formatSize(2.5 * 1024 * 1024), '2.5 MB')
})

test('MaterialsPanel 空态渲染上传提示与隐私说明（不触发 fetch —— SSR 不跑 effect）', () => {
  const html = renderToStaticMarkup(
    createElement(MaterialsPanel, { courseId: 'c1', offline: false, onAppendTerms: () => {} }),
  )
  assert.ok(html.includes('课程资料'), '应有窗口标题')
  assert.ok(html.includes('拖入课件 / 讲义'), '应有空态引导')
  assert.ok(html.includes('只存本机，不上传云端'), '应有隐私说明')
  assert.ok(html.includes('从资料追加候选术语'), '应有追加术语按钮')
})

test('MaterialsPanel 线下课边框用青色主题', () => {
  const online = renderToStaticMarkup(
    createElement(MaterialsPanel, { courseId: 'c1', offline: false, onAppendTerms: () => {} }),
  )
  const offline = renderToStaticMarkup(
    createElement(MaterialsPanel, { courseId: 'c1', offline: true, onAppendTerms: () => {} }),
  )
  assert.ok(online.includes('var(--accent)'), '线上应使用紫色主题变量')
  // 2026-11-12 色值定版：线下青已从散落的硬编码 #00cec9 收敛为 token。
  // 这里断言的是**主题变量**而不是色值字面量 —— 断言字面量等于把实现细节
  // 钉死，改一次主题色就要改一次测试，而那种测试不会拦住「又写死一个色」。
  // 防复发由 css-vars.test.mts 的「不得再出现硬编码色值」守卫负责。
  assert.ok(offline.includes('var(--accent-offline)'), '线下应使用青色主题变量')
})
