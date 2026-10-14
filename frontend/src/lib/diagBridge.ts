import type { DiagInfo, OpenLogDirResult } from '../types'

/**
 * 诊断 / 帮助桥接层（V6-2）
 *
 * 与 `overlayBridge` / `onboardingBridge` 同一套路：把「可能不存在的 Electron IPC」
 * 包成永远可调用、**永不抛**的安全接口。用浏览器直接打开 Vite 页面调试时
 * `window.electronAPI.diag` 是 undefined，页面要能正常渲染出「暂不可用」而不是崩掉。
 *
 * ⚠️ 这里**不缓存**返回的路径：主进程是路径的唯一计算方，缓存一份就多一份会过期的
 * 事实（换 `TINGKEBANLV_HOME` 之后缓存还指着旧目录）。
 */

function api() {
  if (typeof window === 'undefined') return undefined
  return window.electronAPI?.diag
}

/** 是否运行在 Electron 中（浏览器调试时为 false，界面据此显示占位） */
export function diagAvailable(): boolean {
  return !!api()
}

/**
 * 取诊断信息；失败返回 null（调用方显示 `infoLoadFailed` 提示，不抛）
 *
 * 为什么返回 null 而不是抛：这一页是**排障用的**。它自己在排障场景下失败很正常
 * （例如后端/主进程刚崩过），此时最不该发生的就是「帮助页也打不开」。
 */
export async function getDiagInfo(): Promise<DiagInfo | null> {
  try {
    return (await api()?.getInfo()) ?? null
  } catch {
    return null
  }
}

/**
 * 打开日志目录。
 *
 * 返回统一的 `{ ok, dir, error? }`：**绝不**在失败时把 undefined 交给界面 ——
 * `shell.openPath` 本身就不抛异常、只把错误当字符串返回，若这一层再吞掉失败，
 * 界面会显示「什么都没有」或者更糟的「像是成功了」。
 */
export async function openLogDir(): Promise<OpenLogDirResult> {
  try {
    const res = await api()?.openLogDir()
    if (res) return res
  } catch (err) {
    return { ok: false, dir: '', error: err instanceof Error ? err.message : String(err) }
  }
  // 非 Electron 环境（浏览器调试）：如实报告不可用，不假装成功
  return { ok: false, dir: '', error: '当前环境不支持打开日志目录' }
}

/** 导出诊断日志（复用既有 `exportLogs`，这里只做「永不抛」封装） */
export async function exportLogs(): Promise<{
  ok: boolean
  filePath?: string
  files?: number
  canceled?: boolean
  error?: string
}> {
  try {
    return (await window.electronAPI?.exportLogs()) ?? { ok: false, error: '当前环境不支持导出日志' }
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : String(err) }
  }
}
