import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { readDefaultCourseType, writeDefaultCourseType } from '../src/lib/defaultCourseType'

const wizard = readFileSync(new URL('../src/pages/OnboardingWizard.tsx', import.meta.url), 'utf8')
const shell = readFileSync(new URL('../src/shell/Shell.tsx', import.meta.url), 'utf8')

test('Node 环境（无 window）读偏好返回 null，不抛异常', () => {
  // 测试进程没有 window：这正是 SSR / 冒烟早期会遇到的环境
  assert.equal(readDefaultCourseType(), null)
  assert.doesNotThrow(() => writeDefaultCourseType('offline'))
})

test('默认课程类型只认 online / offline，脏值一律按没选处理', () => {
  // 用注入假 window 的方式验证归一逻辑（模块内部每次现读 window，无需 mock 模块）
  const store = new Map<string, string>()
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: { localStorage: {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => { store.set(k, v) },
    } },
  })
  try {
    assert.equal(readDefaultCourseType(), null)
    writeDefaultCourseType('offline')
    assert.equal(readDefaultCourseType(), 'offline')
    writeDefaultCourseType('online')
    assert.equal(readDefaultCourseType(), 'online')
    // 人为塞脏值
    store.set('tingkebanlv.defaultCourseType', 'garbage')
    assert.equal(readDefaultCourseType(), null)
  } finally {
    // 还原环境，避免污染同进程其它测试
    // @ts-expect-error 测试专用清理
    delete globalThis.window
  }
})

test('向导第 2 步卡片是可选按钮，带 radio 语义与选中态（防回到静态 div）', () => {
  assert.match(wizard, /role="radio"/, '场景卡必须可被辅助技术识别为单选项')
  assert.match(wizard, /aria-checked=\{active\}/, '必须有选中态')
  assert.match(wizard, /onClick=\{\(\) => onSelect\(c\.type\)\}/, '卡片必须可点击')
  assert.match(wizard, /<button[\s\S]{0,200}role="radio"/, '场景卡应渲染为 button 而非 div')
  assert.match(wizard, /sceneCardActive/, '必须有选中高亮样式')
  assert.match(wizard, /writeDefaultCourseType\(type\)/, '选择后必须写偏好')
})

test('主窗口新建课程会读取向导保存的默认类型', () => {
  assert.match(shell, /readDefaultCourseType\(\)/)
  assert.match(shell, /type \?\? readDefaultCourseType\(\) \?\? 'online'/)
})
