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
  assert.match(html, /<meter[^>]+value="0.01"/)
  assert.match(html, /4.2 dB/)
  assert.match(html, /没检测到声音/)
})
