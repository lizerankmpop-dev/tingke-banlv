import type {
  HostStatusPayload,
  OverlaySettings,
  OverlaySubtitlePayload,
  OverlayToastPayload,
  PerfStats,
  PositionCorrectedPayload,
  ResetPositionResult,
} from '../types'

/**
 * 悬浮窗桥接层
 *
 * 唯一职责：把「可能不存在的 Electron IPC」包装成永远可调用的安全接口。
 * 浏览器里直接打开 Vite 页面时 window.electronAPI.overlay 为 undefined，
 * 所有方法退化为 no-op / null，业务代码无需到处写 if。
 */

/**
 * 与主进程 electron/overlay-settings.cjs 的 DEFAULT_OVERLAY_SETTINGS 保持一致。
 * 仅作为「IPC 还没回来时」的渲染兜底 —— 真理源始终在主进程。
 * 两边字段一致性由 frontend/tests/overlay-settings.test.cjs 与
 * tsc 的类型检查共同约束（此处的类型是 OverlaySettings，缺字段会报错）。
 */
export const DEFAULT_OVERLAY_SETTINGS: OverlaySettings = {
  schemaVersion: 4,
  enabled: false,
  fontSize: 24,
  color: '#ffffff',
  lineCount: 3,
  textBgOpacity: 0.55,
  windowOpacity: 1,
  clickThrough: true,
  backgroundUpdate: true,
  // 与 overlay-settings.cjs 的默认值一致：引擎侧的默认一直是开启，这里不能改成 false
  termAutoDegrade: true,
  bounds: { x: null, y: null, width: 900, height: 220 },
  anchor: { displayId: null, relX: null, relY: null },
}

function overlayApi() {
  if (typeof window === 'undefined') return undefined
  return window.electronAPI?.overlay
}

function hostApi() {
  if (typeof window === 'undefined') return undefined
  return window.electronAPI
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

  async resetPosition(): Promise<ResetPositionResult | null> {
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

// ============ 宿主存活 / 诊断桥接 ============
//
// 与 overlayBridge 分开的理由：这一组通道主窗口与悬浮窗都会用（心跳是主窗口发、
// 断连提示是悬浮窗收），不属于「悬浮窗设置」这一个职责。

/** 订阅辅助：统一吞掉「非 Electron 环境」与「订阅失败」，调用方拿到的永远是可调用函数 */
function safeSubscribe<T>(pick: (() => ((cb: (p: T) => void) => () => void) | undefined), cb: (p: T) => void): () => void {
  try {
    return pick()?.(cb) ?? (() => {})
  } catch {
    return () => {}
  }
}

export const hostBridge = {
  isAvailable(): boolean {
    return !!hostApi()?.heartbeat
  },

  /**
   * 上报一次心跳（单向、失败静默）
   *
   * 悬浮窗靠它判断主窗口是否还活着 —— 主窗口渲染进程崩溃时悬浮窗不会消失，
   * 只会永远停在最后一句字幕上，看起来像「字幕卡住了」。
   */
  heartbeat(): void {
    try {
      hostApi()?.heartbeat()
    } catch {
      /* 忽略 */
    }
  },

  onHostStatus(cb: (p: HostStatusPayload) => void): () => void {
    return safeSubscribe(() => hostApi()?.onHostStatus, cb)
  },

  onPositionCorrected(cb: (p: PositionCorrectedPayload) => void): () => void {
    return safeSubscribe(() => hostApi()?.onPositionCorrected, cb)
  },

  onToast(cb: (p: OverlayToastPayload) => void): () => void {
    return safeSubscribe(() => hostApi()?.onToast, cb)
  },

  async getHostStatus() {
    try {
      return (await hostApi()?.getHostStatus()) ?? null
    } catch {
      return null
    }
  },

  async getPerfStats(): Promise<PerfStats | null> {
    try {
      return (await hostApi()?.getPerfStats()) ?? null
    } catch {
      return null
    }
  },

  async exportLogs() {
    try {
      return (await hostApi()?.exportLogs()) ?? { ok: false, error: '当前环境不支持导出（仅桌面版可用）' }
    } catch (err) {
      return { ok: false, error: err instanceof Error ? err.message : String(err) }
    }
  },
}

/**
 * 启动心跳循环（只在主窗口里调用一次）
 *
 * 2 秒一次：足够快（8 秒判定失联，最多漏 1~2 次不误报），
 * 又足够轻（一次 IPC 消息，相比 500ms 一次的 ASR 轮询可以忽略）。
 */
export function startHostHeartbeat(intervalMs = 2000): () => void {
  if (!hostBridge.isAvailable()) return () => {}
  hostBridge.heartbeat() // 立刻发一次，避免刚启动的 2 秒空窗被判失联
  const timer = setInterval(() => hostBridge.heartbeat(), intervalMs)
  return () => clearInterval(timer)
}
