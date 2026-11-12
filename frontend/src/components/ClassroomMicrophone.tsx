import { useEffect, useState } from 'react'
import { useClassroomAudioStore } from '../stores/useClassroomAudioStore'
import { useSubtitleStore } from '../stores/useSubtitleStore'
import type { MicrophoneMeter } from '../lib/classroomAudio'
import { LevelMeter } from './ui/LevelMeter'
import { StatusDot } from './ui/StatusDot'
import type { StatusDotTone } from './ui/StatusDot'

/**
 * 电平条 + 三态判定（ADR-003 §0 验收要求「电平 / good·low·silent 判定」）
 *
 * 2026-11-12：原先用原生 `<meter>` 元素画电平 —— 那是浏览器默认样式，
 * 随平台变化且是横向的，表达不出「瞬时峰值」。
 * 现改为移植自界面原型的 26 段电平柱（`ui/LevelMeter`），并补上 good / low 两档
 * （此前只有后端给的 silent 一档，用户看到电平偏低时没有任何提示）。
 *
 * 阈值：silent 由后端判定（level < 0.02 持续一段时间）；low 取 0.15 —— 低于此值
 * 意味着「在收音但几乎收不到」，用户需要调麦克风位置或增益。
 */
const LOW_LEVEL_THRESHOLD = 0.15

export function InputMeter({ meter }: { meter: MicrophoneMeter | null }) {
  const level = meter?.level ?? 0
  const verdict = !meter ? 'idle' : meter.silent ? 'silent' : level < LOW_LEVEL_THRESHOLD ? 'low' : 'good'
  const verdictText = {
    idle: '等待麦克风输入…',
    silent: '没检测到声音，请检查麦克风。',
    low: '声音偏弱，建议靠近讲台或调高增益。',
    good: '收音正常。',
  }[verdict]
  const verdictTone = ({ idle: 'muted', silent: 'cyan', low: 'purple', good: 'green' } as const)[verdict]

  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 10, fontSize: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <LevelMeter level={level} active={verdict === 'good'} tone="cyan" />
        <span>麦克风电平</span>
      </div>
      {meter?.gainDb != null && <span>增益 {meter.gainDb.toFixed(1)} dB</span>}
      <span role="status" style={{ display: 'inline-flex', alignItems: 'center', gap: 5, color: verdict === 'good' ? 'var(--text-secondary)' : 'var(--warning)' }}>
        <StatusDot tone={verdictTone} />
        {verdictText}
      </span>
    </div>
  )
}

export default function ClassroomMicrophone({ courseId, live = false }: { courseId: string; live?: boolean }) {
  const { devices, settings, loading, error, setSettings, loadDevices } = useClassroomAudioStore()
  const meter = useSubtitleStore(state => state.microphoneMeter)
  const [switching, setSwitching] = useState(false)
  const [switchError, setSwitchError] = useState('')

  useEffect(() => {
    if (!live) void loadDevices()
  }, [live, loadDevices])

  async function changeDevice(value: string) {
    const micDeviceIndex = value === '' ? null : Number(value)
    if (!live) {
      setSettings({ micDeviceIndex })
      return
    }
    setSwitching(true)
    setSwitchError('')
    try {
      const response = await fetch('/api/asr/microphone?course_id=' + encodeURIComponent(courseId), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ micDeviceIndex }),
      })
      if (!response.ok) throw new Error('无法切换麦克风')
      setSettings({ micDeviceIndex })
      useSubtitleStore.setState({ microphoneMeter: null })
    } catch {
      setSwitchError('无法切换麦克风，请确认线下识别已启动后再试。')
    } finally {
      setSwitching(false)
    }
  }

  const awaitingDevice = settings.micDeviceIndex !== null && settings.micDeviceIndex !== meter?.activeDevice
  return (
    <section aria-label="线下课堂麦克风" style={{ padding: '8px 12px', display: 'grid', gap: 8 }}>
      <label>
        麦克风{' '}
        <select value={settings.micDeviceIndex ?? ''} disabled={loading || switching}
          onChange={event => { void changeDevice(event.target.value) }}>
          <option value="">系统默认麦克风</option>
          {devices.map(device => (
            <option key={device.index} value={device.index}>
              {device.name}{device.isDefault ? '（默认）' : ''}
            </option>
          ))}
        </select>
      </label>
      {!live && (
        <>
          {loading && <span role="status">正在读取麦克风…</span>}
          {error && <span role="status">{error}</span>}
          {!loading && !error && devices.length === 0 && <span>未列出可选麦克风，请检查连接和系统权限。</span>}
          <label>
            <input type="checkbox" checked={settings.agc}
              onChange={event => setSettings({ agc: event.target.checked })} />{' '}
            自动增益与轻量降噪
          </label>
          <label>
            收音档位{' '}
            <select value={settings.vadProfile}
              onChange={event => setSettings({ vadProfile: event.target.value === 'near' ? 'near' : 'far-field' })}>
              <option value="far-field">教室远场</option>
              <option value="near">近讲</option>
            </select>
          </label>
          <span style={{ fontSize: 12 }}>开始识别后可查看电平并切换麦克风。</span>
        </>
      )}
      {live && (
        <>
          {switchError && <span role="alert">{switchError}</span>}
          {awaitingDevice && !meter?.silent && <span role="status">正在等待所选麦克风输入…</span>}
          <InputMeter meter={meter} />
        </>
      )}
    </section>
  )
}
