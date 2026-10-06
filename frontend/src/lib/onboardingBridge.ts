import type { OnboardingState, OnboardingStatePayload } from '../types'

/**
 * 首启向导桥接层
 *
 * 与 `overlayBridge` 同一套路：把「可能不存在的 Electron IPC」包成永远可调用的
 * 安全接口。用浏览器直接打开 Vite 页面调试时 `window.electronAPI.onboarding`
 * 是 undefined，所有方法退化为 null / false，页面不会崩（只会一直停在第一步）。
 *
 * 另外把两个后端探测也收在这里：它们是「环境 I/O」，与 UI 无关，
 * 且都不该让调用方处理异常。
 */

function api() {
  if (typeof window === 'undefined') return undefined
  return window.electronAPI?.onboarding
}

export const onboardingBridge = {
  /** 是否运行在 Electron 中 */
  isAvailable(): boolean {
    return !!api()
  },

  async getState(): Promise<OnboardingStatePayload | null> {
    try {
      return (await api()?.getState()) ?? null
    } catch {
      return null
    }
  },

  /**
   * 上报「已进入第 N 步」
   *
   * 埋点式调用：失败静默。它只影响事后分析，绝不能让一个上报失败
   * 把用户卡在向导里 —— 这类「辅助能力拖垮主流程」是很容易犯的错。
   */
  async reportStep(step: number): Promise<OnboardingState | null> {
    try {
      return (await api()?.reportStep(step)) ?? null
    } catch {
      return null
    }
  },

  async complete(): Promise<OnboardingState | null> {
    try {
      return (await api()?.complete()) ?? null
    } catch {
      return null
    }
  },

  async dismiss(): Promise<OnboardingState | null> {
    try {
      return (await api()?.dismiss()) ?? null
    } catch {
      return null
    }
  },

  /** 关闭窗口但不写标记（下次启动仍会提示） */
  async close(): Promise<boolean> {
    try {
      return !!(await api()?.close())
    } catch {
      return false
    }
  },

  async reopen(): Promise<boolean> {
    try {
      return !!(await api()?.reopen())
    } catch {
      return false
    }
  },
}

/** 后端 + 模型探测结果（两个都是「真实信号」，用于第 3 步，见 lib/onboarding.ts） */
export interface BackendProbe {
  pingOk: boolean
  modelOk: boolean | null
}

/**
 * 探测后端与模型状态
 *
 * 为什么两个请求而不是一个：`/api/ping` 只说明「进程起来了」，
 * 模型可能仍在准备。向导要区分「正在启动本地服务」与「正在准备识别模型」，
 * 这是两句不同的话 —— 合成一个布尔就说不清了。
 *
 * 判据只看 `ok` 字段，**不解析任何错误文本**（接口刻意不返回它们，
 * 防止有人按中文字符串分支）。
 */
export async function probeBackend(): Promise<BackendProbe> {
  const result: BackendProbe = { pingOk: false, modelOk: null }

  try {
    const ping = await fetch('/api/ping', { cache: 'no-store' })
    result.pingOk = ping.ok
  } catch {
    return result // 后端还没起来，不必再问模型
  }

  try {
    const res = await fetch('/api/asr/model-status', { cache: 'no-store' })
    if (res.ok) {
      const body = await res.json()
      result.modelOk = body?.ok === true
    }
  } catch {
    /* 保持 null = 尚未知，由纯函数判成「准备中」 */
  }

  return result
}
