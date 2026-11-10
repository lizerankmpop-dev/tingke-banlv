/**
 * 课程资料（ADR-004）前端工具函数 —— 与 UI 解耦，纯函数可直接单测。
 */

/**
 * 把资料抽出的术语候选追加进自定义术语输入框的现有文本。
 *
 * 规则：
 * - 现有内容按中英文逗号切分、去空白、去空项，**保持原顺序**；
 * - 新词去重（与已有词重复的不追加；同一批内重复也只留一个，比较前先 trim）；
 * - 连接符统一用中文逗号「，」—— 后端 split(/[,，]/) 两种都认，但输入框里
 *   混用会显得脏，统一成一种。
 */
export function appendTerms(current: string, newTerms: string[]): string {
  const existing = current.split(/[,，]/).map(t => t.trim()).filter(Boolean)
  const seen = new Set(existing.map(t => t.toLowerCase()))
  for (const term of newTerms) {
    const trimmed = (term || '').trim()
    if (!trimmed) continue
    const key = trimmed.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    existing.push(trimmed)
  }
  return existing.join('，')
}

/** 文件大小人类可读（资料列表用）。四舍五入到一位小数，1024 进制。 */
export function formatSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  if (bytes < 1024) return `${Math.round(bytes)} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
