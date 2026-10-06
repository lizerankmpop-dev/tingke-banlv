import { create } from 'zustand'
import type { OverlaySettings } from '../types'
import { overlayBridge, DEFAULT_OVERLAY_SETTINGS } from '../lib/overlayBridge'

/**
 * 悬浮窗设置（渲染进程侧镜像）
 *
 * 真理源在主进程（持久化到 <TINGKEBANLV_HOME>/overlay.json）：
 * 窗口位置/不透明度这类属性只能由主进程作用到 BrowserWindow 上，
 * 若在此处也存一份必然出现「UI 显示 A、窗口实际是 B」的错位。
 * 因此这里只缓存一份供 UI 渲染，一切写入都经 IPC 回主进程确认后再落状态。
 */
interface OverlayState {
  available: boolean
  open: boolean
  settings: OverlaySettings
  /** 首次同步完成前为 true，避免控制面板闪一下默认值 */
  loading: boolean
  error: string | null

  init: () => Promise<void>
  refresh: () => Promise<void>
  patch: (patch: Partial<OverlaySettings>) => Promise<void>
  toggle: () => Promise<void>
  /**
   * 重置悬浮窗位置。
   *
   * 返回需要给用户看的反馈文案（来自主进程的**唯一**来源），
   * 或 null 表示调用失败。悬浮窗关着时 toast 无处显示，
   * 调用方（控制面板）应当用这个文案做内联提示 —— 否则按钮点了没反应。
   */
  resetPosition: () => Promise<string | null>
}

// StrictMode 下 effect 会执行两次；订阅常驻到窗口关闭为止，无需取消，
// 用模块级标记保证只注册一次，避免重复回调把状态刷成两份。
let initialized = false

export const useOverlayStore = create<OverlayState>((set, get) => ({
  available: overlayBridge.isAvailable(),
  open: false,
  settings: DEFAULT_OVERLAY_SETTINGS,
  loading: true,
  error: null,

  init: async () => {
    if (initialized) return
    initialized = true

    if (!overlayBridge.isAvailable()) {
      set({ loading: false, available: false })
      return
    }

    const state = await overlayBridge.getState()
    if (state) {
      set({ open: state.open, settings: state.settings, loading: false })
    } else {
      set({ loading: false, error: '悬浮窗状态获取失败' })
    }

    // 主进程是唯一真理源：热键开关、窗口拖动都会回推到这里
    overlayBridge.onSettings((settings) => set({ settings }))
    overlayBridge.onStateChanged(({ open }) => set({ open }))
  },

  refresh: async () => {
    const state = await overlayBridge.getState()
    if (state) set({ open: state.open, settings: state.settings, loading: false })
  },

  patch: async (patch) => {
    // 乐观更新：滑块拖动要跟手，失败时再由主进程回推的真实值纠正
    set({ settings: { ...get().settings, ...patch } })
    const applied = await overlayBridge.setSettings(patch)
    if (applied) set({ settings: applied, error: null })
    else set({ error: '设置未生效（悬浮窗不可用）' })
  },

  toggle: async () => {
    const next = !get().open
    const ok = next ? await overlayBridge.open() : (await overlayBridge.close(), true)
    if (!ok && next) {
      set({ error: '悬浮窗打开失败，请把日志发给开发者' })
      return
    }
    set({ open: next, error: null })
    // 打开后设置面板要显示真实生效值（例如主进程校正过的字号边界）
    const applied = await overlayBridge.setSettings({ enabled: next })
    if (applied) set({ settings: applied })
  },

  resetPosition: async () => {
    const res = await overlayBridge.resetPosition()
    if (!res) {
      set({ error: '重置位置失败（悬浮窗不可用）' })
      return null
    }
    set({ settings: res.settings, error: null })
    // toastShown=false 表示悬浮窗当时是关着的、toast 没送到，
    // 交给调用方在面板内联提示兜底。文案两边共用主进程那一份。
    return res.toastShown ? null : res.notice
  },
}))
