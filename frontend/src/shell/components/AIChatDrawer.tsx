import { useEffect, useRef, useState } from 'react'
import { AlertCircle, MessageCircle, Send, Sparkles, X } from 'lucide-react'
import { askAI, getAIStatus, type AIStatus } from '../../lib/aiApi'
import type { Course } from '../../types'

/**
 * AI 答疑抽屉（v4.4.0）
 *
 * 后端 /api/ai/ask 早已就绪（RAG + Agent + 多轮记忆），但主骨架 shell
 * 从来没有入口 —— 唯一 UI 在未挂载的旧 SubtitleOverlay.tsx 里，形同虚设。
 * 本组件把它接回主界面：基于当前课程的字幕 + 已索引课件 + 术语表回答。
 *
 * · 打开时拉 /api/ai/status，把真实原因（没装 Ollama / 没拉模型 / 依赖缺失）显示出来
 * · 同一 sessionId 连续追问接上多轮记忆
 * · AI 不可用时保留输入框但禁用，给可操作提示，不误导用户
 */

interface QAMessage {
  q: string
  a: string
}

interface AIChatDrawerProps {
  course: Course | null
  onClose: () => void
}

export function AIChatDrawer({ course, onClose }: AIChatDrawerProps) {
  const [status, setStatus] = useState<AIStatus | null>(null)
  const [messages, setMessages] = useState<QAMessage[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)
  // 每次打开抽屉生成一个 sessionId，保证多轮记忆只在本次答疑会话内串联
  const sessionIdRef = useRef(`qa-${Date.now()}`)

  useEffect(() => {
    let alive = true
    void getAIStatus().then((s) => { if (alive) setStatus(s) })
    return () => { alive = false }
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  async function submit() {
    const question = input.trim()
    if (!question || loading || !course) return
    setInput('')
    setLoading(true)
    setError('')
    const result = await askAI(question, course.id, sessionIdRef.current)
    if (result.available) {
      setMessages((prev) => [...prev.slice(-8), { q: question, a: result.answer || '' }])
    } else {
      setError(result.message || 'AI 答疑服务不可用')
    }
    setLoading(false)
  }

  const unavailable = status !== null && !status.available

  return (
    <div className="ai-chat-overlay" role="presentation" onClick={onClose}>
      <aside
        className="ai-chat-drawer"
        role="dialog"
        aria-label="AI 答疑"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="ai-chat-head">
          <div className="ai-chat-title">
            <MessageCircle size={17} />
            <strong>AI 答疑</strong>
            <span className="ai-chat-hint">基于本节字幕与课件</span>
          </div>
          <button type="button" className="ai-chat-close" onClick={onClose} aria-label="关闭 AI 答疑">
            <X size={16} />
          </button>
        </div>

        {unavailable ? (
          <div className="ai-chat-status" role="status">
            <AlertCircle size={16} />
            <div>
              <strong>AI 尚未就绪</strong>
              <p>{status?.reason || '模型服务不可用'}</p>
              <p className="ai-chat-status-note">字幕、生词等核心功能不受影响，可正常使用。</p>
            </div>
          </div>
        ) : null}

        <div className="ai-chat-body">
          {messages.length === 0 && !loading ? (
            <div className="ai-chat-empty">
              <Sparkles size={30} />
              <p>问问刚才课上没听懂的内容</p>
              <span>例如：刚才讲的过拟合是什么意思？</span>
            </div>
          ) : null}
          {messages.map((m, i) => (
            <div className="ai-chat-msg" key={i}>
              <div className="ai-chat-q">{m.q}</div>
              <div className="ai-chat-a">{m.a}</div>
            </div>
          ))}
          {loading ? <div className="ai-chat-thinking">AI 正在思考…</div> : null}
          {error ? <div className="ai-chat-error">{error}</div> : null}
          <div ref={bottomRef} />
        </div>

        <div className="ai-chat-input-row">
          <input
            type="text"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => { if (event.key === 'Enter') void submit() }}
            placeholder={unavailable ? 'AI 未就绪，请先完成上方配置' : '输入你的问题…'}
            disabled={!course || unavailable || loading}
            aria-label="输入问题"
          />
          <button
            type="button"
            onClick={() => void submit()}
            disabled={!input.trim() || !course || unavailable || loading}
            aria-label="发送问题"
          >
            <Send size={16} />
          </button>
        </div>
      </aside>
    </div>
  )
}
