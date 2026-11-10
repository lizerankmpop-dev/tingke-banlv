import { create } from 'zustand'
import { defaultClassroomSettings } from '../lib/classroomAudio'
import type { ClassroomSettings, MicrophoneDevice } from '../lib/classroomAudio'

interface ClassroomAudioState {
  devices: MicrophoneDevice[]
  loading: boolean
  error: string
  settings: ClassroomSettings
  loadDevices: () => Promise<void>
  setSettings: (patch: Partial<ClassroomSettings>) => void
}

export const useClassroomAudioStore = create<ClassroomAudioState>((set, get) => ({
  devices: [],
  loading: false,
  error: '',
  settings: defaultClassroomSettings,
  loadDevices: async () => {
    if (get().loading) return
    set({ loading: true, error: '' })
    try {
      const response = await fetch('/api/audio/devices')
      if (!response.ok) throw new Error('无法读取麦克风')
      const { devices } = await response.json() as { devices: MicrophoneDevice[] }
      set(state => ({
        devices,
        settings: devices.some(device => device.index === state.settings.micDeviceIndex)
          ? state.settings
          : { ...state.settings, micDeviceIndex: null },
      }))
    } catch {
      set({ error: '无法读取麦克风列表，可尝试使用系统默认麦克风。' })
    } finally {
      set({ loading: false })
    }
  },
  setSettings: patch => set(state => ({ settings: { ...state.settings, ...patch } })),
}))
