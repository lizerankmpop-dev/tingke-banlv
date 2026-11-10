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
}

export const defaultClassroomSettings: ClassroomSettings = {
  micDeviceIndex: null,
  agc: false,
  vadProfile: 'far-field',
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
