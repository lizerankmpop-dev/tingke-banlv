import type { OverlaySettings, OverlaySubtitlePayload } from './index'

/**
 * preload.cjs 通过 contextBridge 暴露的悬浮窗 API
 *
 * 只在 Electron 环境下存在；用浏览器直接打开 Vite 页面时为 undefined，
 * 因此所有调用点都必须做存在性判断（统一收口在 lib/overlayBridge.ts）。
 */
export interface ElectronOverlayAPI {
  getSettings: () => Promise<OverlaySettings>
  setSettings: (patch: Partial<OverlaySettings>) => Promise<OverlaySettings>
  getState: () => Promise<{ open: boolean; settings: OverlaySettings }>
  open: () => Promise<{ open: boolean }>
  close: () => Promise<{ open: boolean }>
  /** 把悬浮窗挪回默认位置（拖到屏幕外时的自救入口） */
  resetPosition: () => Promise<OverlaySettings>
  /** 主渲染进程 → 悬浮窗 的字幕推送（单向，不等待） */
  pushSubtitle: (payload: OverlaySubtitlePayload) => void
  /** 订阅字幕（悬浮窗侧使用），返回取消订阅函数 */
  onSubtitle: (cb: (payload: OverlaySubtitlePayload) => void) => () => void
  /** 订阅设置变更（悬浮窗侧使用），返回取消订阅函数 */
  onSettings: (cb: (settings: OverlaySettings) => void) => () => void
  /** 订阅悬浮窗开关状态（主应用侧使用），返回取消订阅函数 */
  onStateChanged: (cb: (state: { open: boolean }) => void) => () => void
}

declare global {
  interface Window {
    electronAPI?: {
      getAppVersion: () => Promise<string>
      getBackendPort: () => Promise<number | null>
      overlay?: ElectronOverlayAPI
    }
  }
}

export {}
