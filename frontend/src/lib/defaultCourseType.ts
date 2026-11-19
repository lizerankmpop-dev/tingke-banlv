/**
 * 新建课程默认类型（首启向导第 2 步的选择结果）
 *
 * 为什么放 localStorage：向导是独立窗口，但与主窗口同源（都是
 * `http://127.0.0.1:<port>`），localStorage 天然共享；用它存一个「默认课程类型」
 * 不需要动 Electron 的 onboarding.json 状态机、IPC 与那一整套重启判断测试。
 * 这里只存一个「偏好」，丢了的最坏后果只是新建课程回到默认 online，可接受。
 *
 * 存的值只有 'online' | 'offline' 两种，任何其它内容（脏数据、旧版本残留）
 * 一律按「没有选择」处理 —— 与项目里其它归一函数同一口径。
 */

export type CourseType = 'online' | 'offline'

const STORAGE_KEY = 'tingkebanlv.defaultCourseType'

/** 从偏好里读默认课程类型；读不到 / 环境不支持时返回 null */
export function readDefaultCourseType(): CourseType | null {
  if (typeof window === 'undefined' || !window.localStorage) return null
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    return raw === 'online' || raw === 'offline' ? raw : null
  } catch {
    // 隐私模式 / 存储被禁用：静默降级为「没有选择」，绝不让它拖垮向导
    return null
  }
}

/** 写入默认课程类型 */
export function writeDefaultCourseType(type: CourseType): void {
  if (typeof window === 'undefined' || !window.localStorage) return
  try {
    window.localStorage.setItem(STORAGE_KEY, type)
  } catch {
    /* 同上：写失败不影响主流程 */
  }
}
