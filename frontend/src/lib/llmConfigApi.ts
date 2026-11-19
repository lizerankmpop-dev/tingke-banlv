/**
 * LLM 运行时配置 API 与本地持久化
 *
 * 云端模型配置分两层：
 * 1. 后端运行时（`PUT /api/llm/config`）—— 立即生效，无需重启；
 * 2. 本地偏好（localStorage）—— 主窗口与向导同源共享，启动时读出来重新 PUT
 *    到后端，实现重启后自动恢复（后端运行时配置本身不落盘）。
 *
 * API key 存在 localStorage（浏览器同源隔离），不走 overlay.json 明文 JSON。
 * 仍建议用户不要在公用电脑上保存密钥 —— UI 会明确提示。
 */

const STORAGE_KEY = 'tingkebanlv.llmConfig'

export interface LLMConfig {
  /** ollama（本地）| cloud（云端 OpenAI 兼容） */
  provider: 'ollama' | 'cloud'
  cloudApiBase: string
  /** 密钥本身只写本地、只在 PUT 时发出；GET 回显只有 cloudApiKeySet */
  cloudApiKey: string
  cloudApiKeySet: boolean
  cloudModel: string
  ollamaModel: string
}

export const defaultLLMConfig: LLMConfig = {
  provider: 'ollama',
  cloudApiBase: '',
  cloudApiKey: '',
  cloudApiKeySet: false,
  cloudModel: '',
  ollamaModel: 'qwen2.5:7b',
}

/** 从 GET 响应（脱敏）合并成本地表单结构：key 明文从本地偏好补回 */
export function mergeLLMConfig(
  server: Partial<LLMConfig> | null | undefined,
  local: Partial<LLMConfig> | null | undefined,
): LLMConfig {
  const s = server || {}
  const l = local || {}
  return {
    provider: s.provider === 'cloud' || s.provider === 'ollama' ? s.provider : (l.provider === 'cloud' ? 'cloud' : 'ollama'),
    cloudApiBase: s.cloudApiBase ?? l.cloudApiBase ?? '',
    cloudApiKey: l.cloudApiKey ?? '', // 服务端不回传明文，只能用本地的
    cloudApiKeySet: s.cloudApiKeySet === true || !!l.cloudApiKey,
    cloudModel: s.cloudModel ?? l.cloudModel ?? '',
    ollamaModel: s.ollamaModel ?? l.ollamaModel ?? 'qwen2.5:7b',
  }
}

/** 该配置能否连通云端（纯判断，供 UI 禁用「测试连接」） */
export function cloudReady(c: LLMConfig): boolean {
  return !!(c.cloudApiBase.trim() && (c.cloudApiKey.trim() || c.cloudApiKeySet) && c.cloudModel.trim())
}

// ---- 本地持久化 ----

export function loadLocalLLMConfig(): Partial<LLMConfig> | null {
  if (typeof window === 'undefined' || !window.localStorage) return null
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const obj = JSON.parse(raw)
    return obj && typeof obj === 'object' ? obj : null
  } catch {
    return null
  }
}

export function saveLocalLLMConfig(config: LLMConfig): void {
  if (typeof window === 'undefined' || !window.localStorage) return
  try {
    // 只存必要字段；cloudApiKeySet 由服务端回显决定，不必本地记
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({
      provider: config.provider,
      cloudApiBase: config.cloudApiBase,
      cloudApiKey: config.cloudApiKey,
      cloudModel: config.cloudModel,
      ollamaModel: config.ollamaModel,
    }))
  } catch {
    /* 存储被禁用时静默降级：配置仍能本次生效，只是不跨重启 */
  }
}

// ---- HTTP ----

export async function getLLMConfig(): Promise<Partial<LLMConfig> | null> {
  try {
    const res = await fetch('/api/llm/config', { cache: 'no-store' })
    if (!res.ok) return null
    const body = await res.json()
    return body?.config ?? null
  } catch {
    return null
  }
}

export async function putLLMConfig(config: LLMConfig): Promise<{ ok: boolean; message: string }> {
  try {
    const res = await fetch('/api/llm/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        provider: config.provider,
        cloudApiBase: config.cloudApiBase,
        cloudApiKey: config.cloudApiKey,
        cloudModel: config.cloudModel,
        ollamaModel: config.ollamaModel,
      }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      return { ok: false, message: body?.detail || `HTTP ${res.status}` }
    }
    saveLocalLLMConfig(config)
    return { ok: true, message: '模型配置已保存并立即生效' }
  } catch {
    return { ok: false, message: '无法连接本地服务' }
  }
}

export async function probeLLM(): Promise<{ ok: boolean; message: string }> {
  try {
    const res = await fetch('/api/llm/probe', { method: 'POST' })
    const body = await res.json().catch(() => ({}))
    if (!res.ok) return { ok: false, message: body?.detail || `HTTP ${res.status}` }
    return { ok: body?.available !== false, message: body?.message || (body?.available === false ? '当前模型不可用' : '连接正常') }
  } catch {
    return { ok: false, message: '无法连接本地服务' }
  }
}
