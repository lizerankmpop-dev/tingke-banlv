import { useEffect, useState } from 'react'
import { ArrowRight, Bookmark, Check, Sparkles, X } from 'lucide-react'
import { IconButton } from '../../components/ui/IconButton'
import type { Course, VocabularyEntry } from '../../types'
import { sm2Badge } from '../lib/mapping'

/**
 * 点词面板抽屉（自李梓萌原型 components.jsx 的 WordPanel 原样转写）
 *
 * 原型 → 真实数据：
 * · entry（假 WORDS 表）→ GET /api/courses/{id}/vocabulary/{word}
 * · 「AI 讲解」→ POST /api/ai/explain（本地 Ollama；loading / 不可用两态真实呈现）
 * · 「收藏到生词本」→ POST /vocabulary/{word}/collect（SM2 从 D1 起算）
 * · badge → 真实 SM2 状态（sm2Badge）
 * · 音标：词典接口不提供音标，原型字段诚实留空，不编造
 */

interface AiExplain {
  translation: string
  meaning_in_context: string
  example: string
  related: string[]
}

interface WordPanelDrawerProps {
  word: string | null
  course: Course | null
  onClose: () => void
  onCollected: () => void
  onGoReview: () => void
  onToast: (message: string) => void
}

export function WordPanelDrawer({
  word, course, onClose, onCollected, onGoReview, onToast,
}: WordPanelDrawerProps) {
  const [entry, setEntry] = useState<VocabularyEntry | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [collecting, setCollecting] = useState(false)
  const [collected, setCollected] = useState(false)
  const [aiState, setAiState] = useState<'idle' | 'loading' | 'done' | 'unavailable'>('idle')
  const [aiData, setAiData] = useState<AiExplain | null>(null)

  useEffect(() => {
    if (!word || !course) return
    setEntry(null)
    setNotFound(false)
    setCollected(false)
    setAiState('idle')
    setAiData(null)

    let cancelled = false
    fetch(`/api/courses/${course.id}/vocabulary/${encodeURIComponent(word)}`)
      .then(async (res) => {
        if (!res.ok) {
          // 词没查到也可能是「还没收藏过」——不当作错误，卡片仍可收藏
          if (!cancelled) setNotFound(true)
          return null
        }
        return res.json()
      })
      .then((data) => { if (!cancelled && data) setEntry(data) })
      .catch(() => { if (!cancelled) setNotFound(true) })

    // AI 讲解（词未收藏也照讲：点词的动机就是「看不懂」）
    setAiState('loading')
    fetch('/api/ai/explain', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ word, courseId: course.id }),
    })
      .then((res) => res.json())
      .then((data) => {
        if (cancelled) return
        if (data.available) {
          setAiData(data.explanation)
          setAiState('done')
        } else {
          setAiState('unavailable')
        }
      })
      .catch(() => { if (!cancelled) setAiState('unavailable') })

    return () => { cancelled = true }
  }, [word, course])

  if (!word) return null

  async function collect() {
    if (!course || collecting || !word) return
    setCollecting(true)
    try {
      const res = await fetch(
        `/api/courses/${course.id}/vocabulary/${encodeURIComponent(word)}/collect`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            translation: entry?.translation || aiData?.translation || '',
            definition: entry?.definition || aiData?.meaning_in_context || '',
            contextSentence: entry?.contextSentence || '',
            domain: course.domain,
          }),
        },
      )
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const saved = await res.json()
      setEntry(saved)
      setCollected(true)
      onCollected()
      onToast(`「${word}」已加入生词本，SM2 将从第一次复习开始安排`)
    } catch {
      onToast('收藏失败：后端服务未运行')
    }
    setCollecting(false)
  }

  const translation = entry?.translation || aiData?.translation || '本课术语'
  const definition = entry?.definition || aiData?.meaning_in_context || null
  const context = entry?.contextSentence || aiData?.example || ''
  const isCollected = collected || !!entry

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="word-panel" onClick={(event) => event.stopPropagation()}>
        <div className="wp-head">
          <div>
            <div className="wp-word">{word}</div>
          </div>
          <IconButton label="关闭" onClick={onClose}>
            <X size={18} />
          </IconButton>
        </div>

        <div className="wp-translation">{translation}</div>

        {context ? (
          <div className="wp-section">
            <div className="wp-label">本课语境</div>
            <blockquote className="wp-context">“{context}”</blockquote>
          </div>
        ) : null}

        {definition ? (
          <div className="wp-section">
            <div className="wp-label">释义</div>
            <p className="wp-definition">{definition}</p>
          </div>
        ) : null}

        <div className="wp-ai">
          <div className="wp-ai-head">
            <Sparkles size={14} />
            AI 讲解
          </div>
          {aiState === 'loading' ? (
            <p className="wp-ai-status">AI 正在结合本课上下文讲解…</p>
          ) : aiState === 'unavailable' ? (
            <p className="wp-ai-status">AI 讲解服务不可用：请确认 Ollama 已启动并拉取了模型</p>
          ) : aiData ? (
            <>
              <p>{aiData.meaning_in_context || aiData.translation}</p>
              {aiData.example ? <p className="wp-ai-example">{aiData.example}</p> : null}
              {aiData.related?.length ? (
                <div className="wp-ai-related">
                  相关：{aiData.related.slice(0, 6).join('、')}
                </div>
              ) : null}
            </>
          ) : null}
        </div>

        <div className="wp-actions">
          <button
            type="button"
            className={`btn btn-block ${isCollected ? 'btn-collected' : 'btn-primary'}`}
            onClick={() => void collect()}
            disabled={isCollected || collecting}
          >
            {isCollected ? (
              <>
                <Check size={16} /> 已收藏到生词本
              </>
            ) : collecting ? (
              '收藏中…'
            ) : (
              <>
                <Bookmark size={16} /> 收藏到生词本
              </>
            )}
          </button>
          <button type="button" className="btn btn-secondary btn-block" onClick={onGoReview}>
            查看今日复习 <ArrowRight size={15} />
          </button>
        </div>

        <div className="wp-foot">
          <span className="badge">{entry ? sm2Badge(entry) : 'SM2 · 收藏后起算'}</span>
          <span>收藏后按 SM2 间隔重复安排复习</span>
        </div>
      </aside>
    </div>
  )
}
