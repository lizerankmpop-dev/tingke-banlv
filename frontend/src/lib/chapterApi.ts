/**
 * D7 章节与课后总结 API 封装。
 *
 * 「结束并总结」不再是原页面里自生自灭的侧栏卡片：
 * 1) POST /api/courses/{id}/summary 产出摘要草稿（AI 不可用时也返回 available=false + message）
 * 2) 用户确认后可保存到既有章节，或顺手新建一章（PUT .../summary）
 * 3) 下次进课时读取章节目录，问是否要接着新建章节
 */
export interface ChapterRow {
  id: string
  courseId: string
  name: string
  sortOrder: number
  createdAt: number
  summary?: string
  summaryUpdatedAt?: number
}

export interface SummaryDraft {
  available: boolean
  summary: string
  key_points: string[]
  review_words: string[]
  subtitleCount?: number
  wordCount?: number
  message?: string
}

function asChapter(value: unknown): ChapterRow | null {
  if (!value || typeof value !== 'object') return null
  const row = value as Record<string, unknown>
  if (typeof row.id !== 'string' || typeof row.name !== 'string') return null
  return {
    id: row.id,
    courseId: typeof row.courseId === 'string' ? row.courseId : '',
    name: row.name,
    sortOrder: typeof row.sortOrder === 'number' ? row.sortOrder : 0,
    createdAt: typeof row.createdAt === 'number' ? row.createdAt : 0,
    summary: typeof row.summary === 'string' ? row.summary : '',
    summaryUpdatedAt: typeof row.summaryUpdatedAt === 'number' ? row.summaryUpdatedAt : 0,
  }
}

export function asChapterList(value: unknown): ChapterRow[] {
  if (!Array.isArray(value)) return []
  return value.map(asChapter).filter((row): row is ChapterRow => row !== null)
}

export function asSummaryDraft(value: unknown): SummaryDraft {
  const body = (value && typeof value === 'object' ? value : {}) as Record<string, unknown>
  const list = (key: string): string[] =>
    Array.isArray(body[key]) ? (body[key] as unknown[]).filter((x): x is string => typeof x === 'string') : []
  return {
    available: body.available === true,
    summary: typeof body.summary === 'string' ? body.summary : '',
    key_points: list('key_points'),
    review_words: list('review_words'),
    subtitleCount: typeof body.subtitleCount === 'number' ? body.subtitleCount : undefined,
    wordCount: typeof body.wordCount === 'number' ? body.wordCount : undefined,
    message: typeof body.message === 'string' ? body.message : undefined,
  }
}

export async function listChapters(courseId: string, signal?: AbortSignal): Promise<ChapterRow[]> {
  const response = await fetch(`/api/courses/${courseId}/chapters`, { signal })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return asChapterList(await response.json())
}

export async function createChapter(
  courseId: string,
  name: string,
  signal?: AbortSignal,
): Promise<ChapterRow> {
  const response = await fetch(`/api/courses/${courseId}/chapters`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
    signal,
  })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  const row = asChapter(await response.json())
  if (!row) throw new Error('返回章节数据不完整')
  return row
}

/** 生成课后摘要草稿；AI 不可用时返回 available=false，不抛（走手动填写兜底）。 */
export async function generateSummary(courseId: string, signal?: AbortSignal): Promise<SummaryDraft> {
  const response = await fetch(`/api/courses/${courseId}/summary`, { method: 'POST', signal })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    // 后端 400 时给的是 {"detail": "暂无字幕记录…"}，统一成前端 message 口径
    const detail = (body as { detail?: unknown }).detail
    return asSummaryDraft({
      ...body,
      available: false,
      message: typeof detail === 'string' ? detail : `摘要生成失败（HTTP ${response.status}）`,
    })
  }
  return asSummaryDraft(body)
}

export async function saveChapterSummary(
  courseId: string,
  chapterId: string,
  summary: string,
): Promise<void> {
  const response = await fetch(`/api/courses/${courseId}/chapters/${chapterId}/summary`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ summary }),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error((body as { detail?: string }).detail || `HTTP ${response.status}`)
  }
}
