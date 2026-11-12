import test from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { InputMeter } from '../src/components/ClassroomMicrophone'
import { readMicrophoneMeter } from '../src/lib/classroomAudio'

test('没有采集数据时不把未知电平当作静音', () => {
  assert.equal(readMicrophoneMeter(null, {}, 0), null)
})

test('同一麦克风静音满十秒才出现提示，讲话后恢复', () => {
  const data = { inputLevel: 0, micDeviceIndex: 2 }
  const first = readMicrophoneMeter(null, data, 100)
  assert.equal(readMicrophoneMeter(first, data, 10099)?.silent, false)
  const silent = readMicrophoneMeter(first, data, 10100)
  assert.equal(silent?.silent, true)
  const active = readMicrophoneMeter(silent, { ...data, inputLevel: 0.3 }, 10200)
  assert.equal(active?.silent, false)
  assert.equal(active?.quietSince, null)
})

test('切换麦克风后重新计时', () => {
  const first = readMicrophoneMeter(null, { inputLevel: 0, micDeviceIndex: 2 }, 0)
  const switched = readMicrophoneMeter(first, { inputLevel: 0, micDeviceIndex: 3 }, 10500)
  assert.equal(switched?.quietSince, 10500)
  assert.equal(switched?.silent, false)
})

test('电平组件显示当前音量、增益和静音提示', () => {
  const meter = { level: 0.01, gainDb: 4.2, quietSince: 0, silent: true, activeDevice: 1 }
  const html = renderToStaticMarkup(createElement(InputMeter, { meter }))
  // 2026-11-12：原生 <meter> 已换成移植自界面原型的 26 段电平柱（ui/LevelMeter）。
  // 断言「电平柱存在」而不是「原生 meter 的 value 属性」—— 后者是实现细节，
  // 而「电平被显示出来」才是这个组件该保证的事。
  assert.match(html, /class="level-meter/)
  assert.match(html, /4.2 dB/)
  assert.match(html, /没检测到声音/)
})

test('电平组件给出 good / low / silent 三态判定（ADR-003 §0 验收项）', () => {
  const base = { gainDb: null, quietSince: null, silent: false, activeDevice: 1 }
  const good = renderToStaticMarkup(
    createElement(InputMeter, { meter: { ...base, level: 0.6 } }),
  )
  assert.match(good, /收音正常/, 'level 0.6 应判 good')
  assert.match(good, /status-dot green/, 'good 应是绿点')

  const low = renderToStaticMarkup(
    createElement(InputMeter, { meter: { ...base, level: 0.05 } }),
  )
  assert.match(low, /声音偏弱/, 'level 0.05 应判 low')
  assert.match(low, /status-dot purple/, 'low 应是紫点')

  const silent = renderToStaticMarkup(
    createElement(InputMeter, { meter: { ...base, level: 0.01, silent: true } }),
  )
  assert.match(silent, /没检测到声音/, 'silent 应由后端判定直接透出')
  assert.match(silent, /status-dot cyan/, 'silent 应是青点')
})
