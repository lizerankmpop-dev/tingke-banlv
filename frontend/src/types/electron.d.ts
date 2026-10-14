import type {
  DiagInfo,
  HostStatusPayload,
  OnboardingState,
  OnboardingStatePayload,
  OpenLogDirResult,
  OverlaySettings,
  OverlaySubtitlePayload,
  OverlayToastPayload,
  PerfStats,
  PositionCorrectedPayload,
  ResetPositionResult,
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
  resetPosition: () => Promise<ResetPositionResult>
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

/** 首启向导能力（向导窗口与主窗口都会用） */
export interface ElectronOnboardingAPI {
  getState: () => Promise<OnboardingStatePayload>
  /** 上报「已进入第 N 步」；stepReached 只增不减 */
  reportStep: (step: number) => Promise<OnboardingState>
  complete: () => Promise<OnboardingState>
  dismiss: () => Promise<OnboardingState>
  /** 关闭窗口但不写标记（下次启动仍会提示） */
  close: () => Promise<{ open: boolean }>
  /** 「重新查看新手引导」：越过 completed/dismissed 与兼容模式的自动弹出限制 */
  reopen: () => Promise<{ open: boolean }>
}

/** 新增：诊断 / 帮助（V6-2）。**只读** —— 渲染层不做任何持久化 */
export interface ElectronDiagAPI {
  getInfo: () => Promise<DiagInfo>
  /** 打开日志目录；失败时 `error` 有值（`shell.openPath` 不抛异常，只返回错误串） */
  openLogDir: () => Promise<OpenLogDirResult>
}

export type ElectronAPI = ElectronHostAPI & {
  getAppVersion: () => Promise<string>
  getBackendPort: () => Promise<number | null>
  overlay?: ElectronOverlayAPI
  onboarding?: ElectronOnboardingAPI
  diag?: ElectronDiagAPI
}

declare global {
  interface Window {
    electronAPI?: ElectronAPI
  }
}

export {}
