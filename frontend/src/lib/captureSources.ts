/**
 * 窗口画面捕获的纯逻辑
 *
 * 设计约束：
 * · 这里只做「源列表清洗 / 默认源选择 / 错误文案」，不碰 Electron IPC 与 DOM；
 *   这样主进程、渲染进程和 node:test 可以共用同一套规则。
 * · 用户要的是「课程网页 / 课程 App 全屏后等比放在应用中间」，所以默认优先选
 *   非本应用的窗口；本应用窗口通常只是操作面板，捕获它只会得到套娃画面。
 */

export interface CaptureSource {
  id: string
  name: string
  type: 'window' | 'screen'
  thumbnail?: string
}

/** 主进程返回的源可能带任意字段，渲染层只认这四类安全字段 */
export function normalizeCaptureSource(input: unknown): CaptureSource | null {
  if (!input || typeof input !== 'object') return null
  const raw = input as Record<string, unknown>
  const id = typeof raw.id === 'string' ? raw.id : ''
  // v4.3.1：课程 App / UWP 窗口的 source.name 有时是空串。原来直接判空丢弃，
  // 结果就是「课程窗口明明开着，列表里找不到」。用 id 尾部兜底成可读名。
  const rawName = typeof raw.name === 'string' ? raw.name.trim() : ''
  const name = rawName || `课程窗口 ${String(id).split(':').pop()?.slice(0, 8) || '未命名'}`
  const type = raw.type === 'screen' ? 'screen' : raw.type === 'window' ? 'window' : null
  if (!id || !type) return null
  return {
    id,
    name,
    type,
    thumbnail: typeof raw.thumbnail === 'string' ? raw.thumbnail : undefined,
  }
}

/**
 * 选中源优先；否则挑一个最可能是课程画面的源。
 * 优先非本应用窗口；若只剩本应用窗口，宁可用整屏兜底，也不要默认捕获自己。
 */
export function preferCourseSource(sources: CaptureSource[], selectedId = ''): CaptureSource | null {
  const selected = sources.find(source => source.id === selectedId)
  if (selected) return selected

  const isSelf = (name: string) => /听课伴侣|tingkebanlv/i.test(name)
  const courseWindows = sources.filter(source => source.type === 'window' && !isSelf(source.name))
  return (
    courseWindows[0]
    ?? sources.find(source => source.type === 'screen')
    ?? sources.find(source => source.type === 'window')
    ?? null
  )
}

/** 把浏览器/Electron 的捕获异常翻成用户能操作的提示 */
export function captureErrorMessage(error: unknown): string {
  const err = error instanceof Error ? error : null
  const name = err?.name ?? ''
  const message = err?.message ?? String(error ?? '')

  if (name === 'NotAllowedError' || /permission|denied|reject/i.test(message)) {
    return '系统拒绝了画面捕获权限。请允许本机屏幕录制权限后重试。'
  }
  if (name === 'NotFoundError' || /no source|not found/i.test(message)) {
    return '没有找到可捕获的课程窗口。请先把课程网页或课程 App 打开，再刷新列表。'
  }
  if (name === 'NotReadableError' || /could not start|in use/i.test(message)) {
    return '画面源暂时不可读取。请关闭其它录屏软件后重试，或改用「整个屏幕」。'
  }
  return `画面捕获失败：${message || '未知错误'}`
}
