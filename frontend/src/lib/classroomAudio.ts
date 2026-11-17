export interface MicrophoneDevice {
  index: number
  name: string
  isDefault: boolean
  channels: number
}

export interface ClassroomSettings {
  micDeviceIndex: number | null
  agc: boolean
  vadProfile: 'near' | 'far-field'
  latencyProfile: 'fast' | 'balanced'
}

export const defaultClassroomSettings: ClassroomSettings = {
  micDeviceIndex: null,
  // 2026-11-18 线下课实测：教室远场音量大、起伏大，AGC 默认开启可显著降低
  // 音量忽高忽低导致的漏字/误识；用户可在配置页关掉。
  agc: true,
  vadProfile: 'far-field',
  // fast 档按比例收紧 VAD 判定（静音 ×0.75、最长片段 ×0.7），出字更快。
  // 线下课对实时性最敏感，默认 fast；追求完整可在配置页切回 balanced。
  latencyProfile: 'fast',
}

export interface MicrophoneMeter {
  level: number
  gainDb: number | null
  activeDevice: number | null
  quietSince: number | null
  silent: boolean
}

export function readMicrophoneMeter(
  previous: MicrophoneMeter | null,
  data: { inputLevel?: unknown; agcGainDb?: unknown; micDeviceIndex?: unknown },
  now: number,
): MicrophoneMeter | null {
  if (typeof data.inputLevel !== 'number' || !Number.isFinite(data.inputLevel)) return null
  const level = Math.min(1, Math.max(0, data.inputLevel))
  const activeDevice = typeof data.micDeviceIndex === 'number' ? data.micDeviceIndex : null
  const sameDevice = previous?.activeDevice === activeDevice
  const quietSince = level < 0.02 ? (sameDevice ? previous?.quietSince ?? now : now) : null
  return {
    level,
    gainDb: typeof data.agcGainDb === 'number' && Number.isFinite(data.agcGainDb) ? data.agcGainDb : null,
    activeDevice,
    quietSince,
    silent: quietSince !== null && now - quietSince >= 10000,
  }
}
