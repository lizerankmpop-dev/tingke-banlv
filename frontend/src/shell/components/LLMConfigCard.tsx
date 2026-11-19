import { useCallback, useEffect, useState } from 'react'
import { Cloud, Cpu, Loader2, CheckCircle2, AlertCircle, CircleHelp } from 'lucide-react'
import {
  cloudReady, getLLMConfig, loadLocalLLMConfig, mergeLLMConfig, probeLLM, putLLMConfig,
  type LLMConfig,
} from '../../lib/llmConfigApi'

/**
 * 云端 / 本地模型配置卡（课程配置页，v4.3.0）
 *
 * 配置立即生效（后端运行时），并存本地偏好供重启恢复。
 * 说明文字默认收进右上角问号（与 D9 的设置简洁化同一套信息架构）。
 */
export function LLMConfigCard({ onToast }: { onToast: (message: string) => void }) {
  const [config, setConfig] = useState<LLMConfig | null>(null)
  const [saving, setSaving] = useState(false)
  const [probing, setProbing] = useState(false)
  const [probeResult, setProbeResult] = useState<{ ok: boolean; message: string } | null>(null)
  const [showHelp, setShowHelp] = useState(false)

  useEffect(() => {
    let alive = true
    void (async () => {
      const [server, local] = await Promise.all([
        getLLMConfig(),
        Promise.resolve(loadLocalLLMConfig()),
      ])
      if (alive) setConfig(mergeLLMConfig(server, local))
    })()
    return () => { alive = false }
  }, [])

  const patch = useCallback((p: Partial<LLMConfig>) => {
    setConfig((c) => (c ? { ...c, ...p } : c))
    setProbeResult(null)
  }, [])

  const save = useCallback(async () => {
    if (!config || saving) return
    setSaving(true)
    const res = await putLLMConfig(config)
    setSaving(false)
    setProbeResult(null)
    onToast(res.message)
  }, [config, saving, onToast])

  const probe = useCallback(async () => {
    if (probing) return
    setProbing(true)
    const res = await probeLLM()
    setProbing(false)
    setProbeResult(res)
  }, [probing])

  if (!config) return null

  const isCloud = config.provider === 'cloud'

  return (
    <section className="config-card llm-config-card">
      <div className="card-head">
        <div className="card-icon purple">
          {isCloud ? <Cloud size={18} /> : <Cpu size={18} />}
        </div>
        <div className="card-head-copy">
          <h2>AI 模型</h2>
          <p>{isCloud ? '云端 OpenAI 兼容接口' : '本地 Ollama（默认，全程不联网）'}</p>
        </div>
        <div className="llm-help-wrap">
          <button
            type="button"
            className="icon-btn"
            aria-label="模型配置说明"
            aria-expanded={showHelp}
            onClick={() => setShowHelp((v) => !v)}
          >
            <CircleHelp size={16} />
          </button>
          {showHelp ? (
            <div className="llm-help-popover" role="tooltip">
              <p><strong>本地（Ollama）</strong>：默认方案，模型在本机运行，不联网。需先安装 Ollama 并拉取 qwen2.5:7b。</p>
              <p><strong>云端</strong>：任意 OpenAI 兼容接口（DeepSeek / Kimi / 智谱 / OpenAI）。填写接口地址、密钥与模型名即可，立即生效。</p>
              <p><strong>密钥安全</strong>：密钥保存在本机浏览器偏好、仅用于向接口发请求，不会上传课程数据。公用电脑请谨慎保存。</p>
            </div>
          ) : null}
        </div>
      </div>

      <div className="llm-mode-switch">
        <button
          type="button"
          className={!isCloud ? 'active' : ''}
          onClick={() => patch({ provider: 'ollama' })}
        >
          <Cpu size={14} /> 本地 Ollama
        </button>
        <button
          type="button"
          className={isCloud ? 'active' : ''}
          onClick={() => patch({ provider: 'cloud' })}
        >
          <Cloud size={14} /> 云端接口
        </button>
      </div>

      {!isCloud ? (
        <label className="field field-tight">
          <span>本地模型名</span>
          <input
            value={config.ollamaModel}
            onChange={(e) => patch({ ollamaModel: e.target.value })}
            placeholder="qwen2.5:7b"
          />
        </label>
      ) : (
        <>
          <label className="field field-tight">
            <span>接口地址（Base URL）</span>
            <input
              value={config.cloudApiBase}
              onChange={(e) => patch({ cloudApiBase: e.target.value })}
              placeholder="https://api.deepseek.com/v1"
            />
          </label>
          <label className="field field-tight">
            <span>API 密钥{config.cloudApiKeySet && !config.cloudApiKey ? '（已保存，留空则沿用）' : ''}</span>
            <input
              type="password"
              value={config.cloudApiKey}
              onChange={(e) => patch({ cloudApiKey: e.target.value })}
              placeholder={config.cloudApiKeySet ? '••••••••（已设置）' : 'sk-...'}
              autoComplete="off"
            />
          </label>
          <label className="field field-tight">
            <span>模型名</span>
            <input
              value={config.cloudModel}
              onChange={(e) => patch({ cloudModel: e.target.value })}
              placeholder="deepseek-chat"
            />
          </label>
        </>
      )}

      <div className="config-actions">
        <button type="button" className="btn btn-primary btn-sm" onClick={() => void save()} disabled={saving}>
          {saving ? <Loader2 size={14} className="spin" /> : <CheckCircle2 size={14} />} 保存配置
        </button>
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          onClick={() => void probe()}
          disabled={probing || (isCloud && !cloudReady(config))}
        >
          {probing ? <Loader2 size={14} className="spin" /> : <Cpu size={14} />} 测试连接
        </button>
      </div>

      {probeResult ? (
        <div className={`verdict ${probeResult.ok ? 'good' : 'checking'}`}>
          {probeResult.ok ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}
          <div><strong>{probeResult.message}</strong></div>
        </div>
      ) : null}

      <p className="fineprint">
        <Cloud size={12} /> 切换云端后 AI 功能会联网调用你的接口；本地模式不联网。
      </p>
    </section>
  )
}
