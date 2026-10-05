import type {
  HostStatusPayload,
  OverlaySettings,
  OverlaySubtitlePayload,
  OverlayToastPayload,
  PerfStats,
  PositionCorrectedPayload,
} from './index'

/**
 * preload.cjs 通过 contextBridge 暴露的全部能力
 *
 * 只在 Electron 环境下存在；用浏览器直接打开 Vite 页面时为 undefined，
 * 因此所有调用点都必须做存在性判断（统一收口在 lib/overlayBridge.ts 与 lib/hostBridge.ts）。
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

/** 存活心跳 / 诊断能力（主窗口与悬浮窗都会用到一部分） */
export interface ElectronHostAPI {
  /** 渲染进程 → 主进程心跳（每 2s 一次，单向） */
  heartbeat: () => void
  onHostStatus: (cb: (payload: HostStatusPayload) => void) => () => void
  getHostStatus: () => Promise<{ alive: boolean; throttled: boolean | null; overlayOpen: boolean }>
  onPositionCorrected: (cb: (payload: PositionCorrectedPayload) => void) => () => void
  onToast: (cb: (payload: OverlayToastPayload) => void) => () => void
  getPerfStats: () => Promise<PerfStats>
  exportLogs: () => Promise<{ ok: boolean; filePath?: string; files?: number; canceled?: boolean; error?: string }>
}

export type ElectronAPI = ElectronHostAPI & {
  getAppVersion: () => Promise<string>
  getBackendPort: () => Promise<number | null>
  overlay?: ElectronOverlayAPI
}

declare global {
  interface Window {
    electronAPI?: ElectronAPI
  }
}

export {}
