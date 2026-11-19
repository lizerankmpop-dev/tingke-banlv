import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const overlay = readFileSync(new URL('../src/pages/OverlayWindow.tsx', import.meta.url), 'utf8')

test('D8 悬浮窗自适应鼠标穿透：整层 pointerEvents 随 clickThrough 变化', () => {
  assert.match(overlay, /pointerEvents: clickThrough \? 'none' : 'auto'/,
    '调整模式必须让整窗可交互，不能写死 none（否则退出按钮点不到）')
  assert.doesNotMatch(overlay, /^\s*pointerEvents: 'none',\s*$/m,
    'root 层不能再有写死的 pointerEvents none')
})

test('D8 悬浮窗自带退出调整按钮，鼠标即可完成全流程', () => {
  assert.match(overlay, /结束调整模式/, '退出按钮必须有 aria-label')
  assert.match(overlay, /patch\(\{ clickThrough: true \}\)/,
    '退出按钮必须走 store.patch 设置 clickThrough=true')
  assert.match(overlay, /WebkitAppRegion: 'no-drag'/,
    'drag 区里的按钮必须 no-drag，否则点击被当拖动')
  assert.match(overlay, /e\.stopPropagation\(\)/,
    '按钮点击要阻止冒泡，避免触发 drag')
})
