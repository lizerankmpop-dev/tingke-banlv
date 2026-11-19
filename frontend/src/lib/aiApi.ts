/**
 * AI 功能 API 封装（v4.4.0）
 *
 * 主骨架此前只接了「点词讲解 / 课堂总结 / AI 生成术语」，
 * `/api/ai/status` 与 `/api/ai/ask` 两个端点前端从未调用 —— 后端能力被浪费。
 * 这里统一封装，供 AI 答疑面板与可用性提示复用。
 *
 * 所有调用都带明确失败语义：网络异常返回 available=false + message，
 * 不向上抛，UI 层可以无脑渲染 message。
 */

export interface AIStatus {
  available: boolean
  reason: string
  provider: string
  coreUnaffected: boolean
}

export interface AskResult {
  available: boolean
  answer?: string
  message?: string
}

/** 查询 AI 能力可用性（含真实原因：依赖缺失 / 服务未启动 / 模型未拉取） */
export async function getAIStatus(signal?: AbortSignal): Promise<AIStatus> {
  const response = await fetch('/api/ai/status', { signal })
  if (!response.ok) {
    return {
      available: false,
      reason: `AI 状态探测失败（HTTP ${response.status}）`,
      provider: 'unknown',
      coreUnaffected: true,
    }
  }
  const body = (await response.json()) as Partial<AIStatus>
  return {
    available: Boolean(body.available),
    reason: typeof body.reason === 'string' ? body.reason : '',
    provider: typeof body.provider === 'string' ? body.provider : 'unknown',
    coreUnaffected: body.coreUnaffected !== false,
  }
}

/**
 * 向 AI 提问（基于课程字幕 + 已索引资料 + 术语表）。
 * sessionId 相同则复用后端多轮记忆，实现连续追问。
 */
export async function askAI(
  question: string,
  courseId: string,
  sessionId = '',
): Promise<AskResult> {
  try {
    const response = await fetch('/api/ai/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, courseId, sessionId }),
    })
    const body = (await response.json()) as Partial<AskResult>
    if (!response.ok) {
      const detail = (body as { detail?: unknown }).detail
      return {
        available: false,
        message: typeof detail === 'string' ? detail : `AI 答疑失败（HTTP ${response.status}）`,
      }
    }
    if (!body.available) {
      return {
        available: false,
        message: body.message || 'AI 答疑服务不可用，请检查模型配置',
      }
    }
    return { available: true, answer: body.answer || '' }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    return { available: false, message: `AI 答疑连接失败：${message}` }
  }
}
