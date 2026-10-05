import type {
  CollectVocabularyPayload,
  ReviewRating,
  VocabularyEntry,
  VocabularyStats,
} from '../types'

/** 跨课程待复习概览 */
export interface DueSummary {
  due: number
  total: number
  reviewedToday: number
  topCourseId: string | null
  topCourseDue: number
  now: number
}

/**
 * 生词本 API 层
 *
 * 为什么单独成文件：这套接口的路径语义有坑（静态段与 {word} 段混排），
 * 集中在一处更容易看出「哪个是静态路径、哪个是按词 upsert」。
 *
 * 后端路由顺序要求（见 backend/api/routes.py 的注释）：
 *   /vocabulary/stats 、/vocabulary/review-queue 、/vocabulary/export.csv
 *   /vocabulary/backup.json 这些**静态段**必须声明在 /vocabulary/{word} 之前，
 *   否则会被 {word} 吃掉。前端调用顺序无所谓，但不能把路径写错。
 */

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const body = await res.json()
      if (body && typeof body.detail === 'string') detail = body.detail
    } catch {
      /* 响应不是 JSON，沿用状态码 */
    }
    throw new Error(detail)
  }
  return (await res.json()) as T
}

const jsonHeaders = { 'Content-Type': 'application/json' }

export const vocabularyApi = {
  list: (courseId: string) =>
    requestJson<VocabularyEntry[]>(`/api/courses/${courseId}/vocabulary`),

  stats: (courseId: string) =>
    requestJson<VocabularyStats>(`/api/courses/${courseId}/vocabulary/stats`),

  /**
   * 跨课程待复习概览
   *
   * 单独一个端点而不是遍历所有课程的 stats：启动提醒发生在「还没选课程」时，
   * 逐课程查会变成 N 次请求，课程多了启动就变慢。
   */
  dueSummary: () =>
    requestJson<DueSummary>('/api/vocabulary/due-summary'),

  /** 待复习队列（next_review_at <= now，按到期时间升序） */
  reviewQueue: (courseId: string, limit = 20) =>
    requestJson<VocabularyEntry[]>(
      `/api/courses/${courseId}/vocabulary/review-queue?limit=${limit}`
    ),

  /**
   * 采集生词（按「单词」upsert）
   *
   * 与 update(entry.id) 的区别：这个入口是「在字幕里看到一个词，收进生词本」，
   * 此时还没有 entry.id。重复采集只更新释义/上下文，并保留既有复习进度。
   */
  collect: (courseId: string, word: string, payload: CollectVocabularyPayload = {}) =>
    requestJson<VocabularyEntry>(
      `/api/courses/${courseId}/vocabulary/${encodeURIComponent(word)}/collect`,
      { method: 'POST', headers: jsonHeaders, body: JSON.stringify(payload) }
    ),

  /**
   * 提交一次复习评价
   *
   * 排程（SM2）在前端算好再提交，后端只做夹取兜底 ——
   * 这样「评分按钮上的间隔天数」与「实际落库的间隔」必然一致，
   * 不会出现「界面写 3 天后，实际 1 天后」这种对不上的情况。
   */
  review: (
    courseId: string,
    word: string,
    schedule: {
      efactor: number
      interval: number
      repetitions: number
      nextReviewAt: number
      rating: ReviewRating
    }
  ) =>
    requestJson<VocabularyEntry>(
      `/api/courses/${courseId}/vocabulary/${encodeURIComponent(word)}/review`,
      { method: 'POST', headers: jsonHeaders, body: JSON.stringify(schedule) }
    ),

  remove: (courseId: string, word: string) =>
    requestJson<{ ok: boolean }>(
      `/api/courses/${courseId}/vocabulary/${encodeURIComponent(word)}`,
      { method: 'DELETE' }
    ),

  /** 标记已掌握 / 取消（沿用既有 PUT 接口） */
  setMemorized: (courseId: string, vocabId: string, isMemorized: boolean) =>
    requestJson<unknown>(`/api/courses/${courseId}/vocabulary/${vocabId}`, {
      method: 'PUT',
      headers: jsonHeaders,
      body: JSON.stringify({ isMemorized }),
    }),

  backupJson: (courseId: string) =>
    requestJson<{
      format: string
      exportedAt: number
      count: number
      items: VocabularyEntry[]
    }>(`/api/courses/${courseId}/vocabulary/backup.json`),

  importJson: (courseId: string, items: unknown[]) =>
    requestJson<{ imported: number; skipped: number; total: number }>(
      `/api/courses/${courseId}/vocabulary/backup.json`,
      { method: 'POST', headers: jsonHeaders, body: JSON.stringify({ items }) }
    ),
}

/**
 * 触发浏览器下载（CSV / JSON 备份）
 *
 * 为什么不用 fetch + blob 再手动保存：这两个端点都带 Content-Disposition，
 * 交给浏览器直接跳转最省事，也不会把整份数据读进内存。
 * 唯一代价是拿不到「失败」的回调 —— 因此导出按钮只做「点了就给反馈」，
 * 不假装知道成功与否。
 */
export function downloadUrl(url: string): void {
  const a = document.createElement('a')
  a.href = url
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  a.remove()
}

/** 导出下载用的文件名（后端已带 Content-Disposition，这里只做兜底可读性） */
export function exportUrl(courseId: string, kind: 'csv' | 'json'): string {
  const base = `/api/courses/${courseId}/vocabulary`
  return kind === 'csv' ? `${base}/export.csv` : `${base}/backup.json`
}
