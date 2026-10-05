import type { OverlaySettings, OverlaySubtitlePayload } from '../types'

/**
 * 悬浮窗桥接层
 *
 * 唯一职责：把「可能不存在的 Electron IPC」包装成永远可调用的安全接口。
 * 浏览器里直接打开 Vite 页面时 window.electronAPI.overlay 为 undefined，
 * 所有方法退化为 no-op / null，业务代码无需到处写 if。
 */

/** 与主进程 electron/main.cjs 的 DEFAULT_OVERLAY_SETTINGS 保持一致（仅用于桥接层兜底） */
export const DEFAULT_OVERLAY_SETTINGS: OverlaySettings = {
  enabled: false,
  fontSize: 24,
  color: '#ffffff',
  lineCount: 3,
  textBgOpacity: 0.55,
  windowOpacity: 1,
  clickThrough: true,
  bounds: { x: null, y: null, width: 900, height: 220 },
}

function overlayApi() {
  if (typeof window === 'undefined') return undefined
  return window.electronAPI?.overlay
}

export const overlayBridge = {
  /** 是否运行在 Electron 中（决定控制面板显示「不可用」还是真实开关） */
  isAvailable(): boolean {
    return !!overlayApi()
  },

  async getState(): Promise<{ open: boolean; settings: OverlaySettings } | null> {
    const api = overlayApi()
    if (!api) return null
    try {
      return await api.getState()
    } catch {
      return null
    }
  },

  async setSettings(patch: Partial<OverlaySettings>): Promise<OverlaySettings | null> {
    const api = overlayApi()
    if (!api) return null
    try {
      return await api.setSettings(patch)
    } catch {
      return null
    }
  },

  async open(): Promise<boolean> {
    const api = overlayApi()
    if (!api) return false
    try {
      return (await api.open()).open
    } catch {
      return false
    }
  },

  async close(): Promise<boolean> {
    const api = overlayApi()
    if (!api) return false
    try {
      return (await api.close()).open
    } catch {
      return false
    }
  },

  async resetPosition(): Promise<OverlaySettings | null> {
    const api = overlayApi()
    if (!api) return null
    try {
      return await api.resetPosition()
    } catch {
      return null
    }
  },

  /**
   * 推送字幕快照
   *
   * 单向、不等待、失败静默 —— 悬浮窗只是「锦上添花」的展示面，
   * 它出问题绝不能影响主窗口的识别主流程。
   */
  pushSubtitle(payload: OverlaySubtitlePayload): void {
    try {
      overlayApi()?.pushSubtitle(payload)
    } catch {
      /* 忽略 */
    }
  },

  onSubtitle(cb: (payload: OverlaySubtitlePayload) => void): () => void {
    try {
      return overlayApi()?.onSubtitle(cb) ?? (() => {})
    } catch {
      return () => {}
    }
  },

  onSettings(cb: (settings: OverlaySettings) => void): () => void {
    try {
      return overlayApi()?.onSettings(cb) ?? (() => {})
    } catch {
      return () => {}
    }
  },

  onStateChanged(cb: (state: { open: boolean }) => void): () => void {
    try {
      return overlayApi()?.onStateChanged(cb) ?? (() => {})
    } catch {
      return () => {}
    }
  },
}
