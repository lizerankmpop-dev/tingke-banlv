/**
 * AI 专业词汇生成 API 与术语合并规则
 *
 * 为什么单独拆成模块：
 * 1. `mergeTerms` 是纯函数，不依赖 React / DOM，可直接用 `node --test` 覆盖；
 * 2. HTTP 与超时细节留在这一层，课程配置页只关心「生成成功 / 失败」；
 * 3. 生成的术语只用于**填充输入框**，是否保存仍由「开始识别」的 PUT 决定 ——
 *    这里绝不主动落库，避免用户还没确认就被写进课程配置。
 */

export interface GenerateTermsPayload {
  courseName: string
  domain?: string
  language?: string
}

export type TermProvider = 'cloud' | 'ollama'

export interface GenerateTermsResult {
  terms: string[]
  provider: TermProvider
  elapsedMs: number
}

/**
 * 前端超时 8.5 秒。
 *
 * 后端总预算是 8 秒（云端 5 秒 + Ollama 剩余约 3 秒），前端多留 0.5 秒，
 * 避免「后端刚好在 8 秒返回、前端却先断开」把一次成功误判成超时。
 */
export const TERM_GENERATE_TIMEOUT_MS = 8500

/**
 * 请求后端生成专业术语。
 *
 * `signal` 由调用方提供：页面层用 AbortController 控制 8.5 秒超时，
 * 这样测试与复用方可以自行决定取消策略，API 层不偷偷持有定时器。
 */
export async function generateTerms(
  payload: GenerateTermsPayload,
  signal?: AbortSignal,
): Promise<GenerateTermsResult> {
  const res = await fetch('/api/terms/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  })

  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const body = await res.json()
      if (body && typeof body.detail === 'string' && body.detail.trim()) {
        detail = body.detail
      }
    } catch {
      /* 响应不是 JSON，沿用状态码 */
    }
    throw new Error(detail)
  }

  const data = (await res.json()) as Partial<GenerateTermsResult>
  const terms = Array.isArray(data.terms)
    ? data.terms.filter((term): term is string => typeof term === 'string')
    : []
  const elapsedMs = typeof data.elapsedMs === 'number' ? data.elapsedMs : 0
  return {
    terms,
    provider: data.provider === 'ollama' ? 'ollama' : 'cloud',
    elapsedMs: Number.isFinite(elapsedMs) ? elapsedMs : 0,
  }
}

/**
 * 把 AI 生成的术语合并进自定义术语输入框的现有文本。
 *
 * 规则（ADR 任务 2 约定）：
 * - 现有值按中英文逗号拆分、trim、去空，**保持原顺序**；
 * - 新词也 trim、去空；
 * - 大小写不敏感去重，已有词与同批新词都参与；
 * - 统一用英文逗号 + 空格 ", " 连接；
 * - **绝不清空用户已填内容** —— 生成失败或没有新词时，原文本原样保留。
 */
export function mergeTerms(current: string, generated: readonly string[]): string {
  const merged: string[] = []
  const seen = new Set<string>()

  const append = (raw: string | undefined | null) => {
    const term = (raw || '').trim()
    if (!term) return
    const key = term.toLowerCase()
    if (seen.has(key)) return
    seen.add(key)
    merged.push(term)
  }

  current.split(/[,，]/).forEach(append)
  generated.forEach(append)

  return merged.join(', ')
}