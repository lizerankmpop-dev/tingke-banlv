import { useCallback, useEffect, useRef, useState } from 'react'
import { createElement } from 'react'
import type { DragEvent } from 'react'
import {
  ArrowLeft, ArrowRight, Brain, Check, CheckCircle2, ChevronDown, Download,
  Globe, GripVertical, Lock, Notebook, Pause, Play, Plus, RefreshCw, Sparkles,
} from 'lucide-react'
import { IconButton } from '../../components/ui/IconButton'
import { StatusDot } from '../../components/ui/StatusDot'
import { HighlightedText } from '../components/HighlightedText'
import { readCourseURL } from '../../lib/courseDrop'
import { useSubtitleStore } from '../../stores/useSubtitleStore'
import { useCourseStore } from '../../stores/useCourseStore'
import { useOverlayStore } from '../../stores/useOverlayStore'
import { vocabularyApi } from '../../lib/vocabularyApi'
import type { Course, VocabularyEntry } from '../../types'
import { highlightTerms, isOnline, toDisplayLine, sm2Badge, relativeTime, type DisplayLine } from '../lib/mapping'

/**
 * 识别页（自李梓萌原型 App.jsx 的 Recognition / WebFrame / CurrentSubtitle /
 * TranscriptPanel / SidePanel / WordList / NotebookPanel 原样转写）
 *
 * 原型假数据 → 真实数据：
 * · transcript / currentLine → /api/asr/poll 轮询（useSubtitleStore，500ms）
 * · listening 开关 → connectASR / disconnectASR（真启停后端管道）
 * · 生字侧栏 → GET /api/courses/{id}/vocabulary
 * · 课堂总结 → POST /api/courses/{id}/summary（key_points → 她的 points）
 * · 小笔记本 → GET/PUT /api/courses/{id}/note（本次后端新增）
 * · MockCoursePage → 真实 <webview>（拖拽/粘贴链接，ADR-002 内嵌方案）
 * · 「悬浮条」→ 桌面置顶悬浮窗（useOverlayStore.toggle）
 * · 导出 → GET /api/courses/{id}/export-srt
 */

interface RecognitionPageProps {
  course: Course
  onBack: () => void
  onOpenConfig: () => void
  onGoReview: () => void
  onSelectWord: (word: string) => void
  onToast: (message: string) => void
}

export function RecognitionPage({
  course,
  onBack,
  onOpenConfig,
  onGoReview,
  onSelectWord,
  onToast,
}: RecognitionPageProps) {
  const online = isOnline(course)
  const setActiveCourse = useCourseStore((s) => s.setActiveCourse)

  const {
    subtitleLines, currentText, asrStatus, asrMessage,
    connectASR, disconnectASR, retryConnect,
  } = useSubtitleStore()

  const overlayOpen = useOverlayStore((s) => s.open)
  const overlayInit = useOverlayStore((s) => s.init)
  const overlayToggle = useOverlayStore((s) => s.toggle)
  useEffect(() => { void overlayInit() }, [overlayInit])

  const [vocab, setVocab] = useState<VocabularyEntry[]>([])
  const loadVocab = useCallback(() => {
    vocabularyApi.list(course.id).then(setVocab).catch(() => {})
  }, [course.id])
  useEffect(() => { loadVocab() }, [loadVocab])

  // 生字侧栏随字幕增长刷新（每 15 条刷一次，平衡实时性与请求数）
  useEffect(() => {
    if (subtitleLines.length > 0 && subtitleLines.length % 15 === 0) loadVocab()
  }, [subtitleLines.length, loadVocab])

  // 进入识别页即启动后端 ASR；离开时停（她的交互：点课程卡 = 开始听课）
  useEffect(() => {
    setActiveCourse(course.id)
    connectASR(course.id, '')
    return () => { disconnectASR() }
  }, [course.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const listening = asrStatus === 'listening' || asrStatus === 'processing'
  const hasError = asrStatus === 'error'

  function toggleListening() {
    if (listening) {
      disconnectASR()
      onToast('识别已暂停，字幕会保留在当前句')
    } else {
      retryConnect()
      onToast('识别已继续，字幕会实时更新')
    }
  }

  // 字幕行：原型 { time, en, zh } → 真实 { time, orig, trans }
  const lines: DisplayLine[] = subtitleLines.map(toDisplayLine)
  const lastFinal = lines[lines.length - 1] ?? null
  const currentLine: DisplayLine = lastFinal
    ? { ...lastFinal, orig: currentText || lastFinal.orig }
    : { id: 'interim', time: '--:--', orig: currentText, trans: '', isFinal: false }
  const historyLines = lines.slice(0, -1)

  const terms = highlightTerms(course, vocab)

  async function exportSrt() {
    try {
      const res = await fetch(`/api/courses/${course.id}/export-srt?offsetMs=0`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      const count = data?.count ?? 0
      onToast(count > 0 ? `已导出 ${count} 条字幕（SRT，最新一次识别）` : '还没有字幕可导出')
    } catch {
      onToast('导出失败：后端服务未运行')
    }
  }

  return (
    <div className="page page-wide recognition-page">
      <div className="topbar">
        <div className="topbar-left">
          <IconButton label="返回课程列表" onClick={onBack}>
            <ArrowLeft size={18} />
          </IconButton>
          <div className="topbar-title-wrap">
            <div className="topbar-title">{course.name}</div>
            <div className="topbar-sub">
              {online ? '线上网课 · 系统音频' : '线下课堂 · 麦克风'} ·{' '}
              {listening ? '识别中' : asrStatus === 'error' ? '连接异常' : '已暂停'}
            </div>
          </div>
        </div>

        <div className="topbar-actions">
          <button type="button" className="btn btn-ghost btn-sm" onClick={onOpenConfig}>
            配置
          </button>
          <button
            type="button"
            className={`btn btn-ghost btn-sm ${overlayOpen ? 'active' : ''}`}
            onClick={() => void overlayToggle()}
            title="桌面置顶悬浮字幕条（跟随课程场景色）"
          >
            悬浮条
          </button>
          <button
            type="button"
            className={`btn btn-sm ${listening ? 'btn-secondary' : 'btn-primary'}`}
            onClick={toggleListening}
          >
            {listening ? (
              <>
                <Pause size={14} /> 暂停
              </>
            ) : (
              <>
                <Play size={14} /> 开始
              </>
            )}
          </button>
        </div>
      </div>

      {hasError && asrMessage ? (
        <div className="asr-error-bar" role="alert">
          <RefreshCw size={13} />
          <span style={{ flex: 1 }}>{asrMessage}</span>
          <button type="button" className="text-btn" onClick={retryConnect}>重试</button>
        </div>
      ) : null}

      {online ? (
        <div className="recognition-grid">
          <div className="recognition-main">
            <WebFrame courseName={course.name} onToast={onToast} />
            <CurrentSubtitle
              label="实时字幕"
              courseTypeHint={course.language === 'zh' ? '中文识别 · 系统音频' : '双语 · 原文识别'}
              line={currentLine}
              listening={listening}
              terms={terms}
              onTerm={onSelectWord}
              onExport={() => void exportSrt()}
            />
          </div>

          <aside className="recognition-side">
            <SidePanel
              title="生字"
              count={vocab.length}
              action={
                <button type="button" className="side-link" onClick={onGoReview}>
                  复习
                </button>
              }
            >
              <WordList entries={vocab} onSelectWord={onSelectWord} />
            </SidePanel>

            <SidePanel title="历史字幕" count={historyLines.length}>
              <div className="history-list">
                {historyLines.slice(-6).map((line) => (
                  <div className="history-row" key={line.id}>
                    <span className="history-time">{line.time}</span>
                    <div className="history-copy">
                      <div className="history-row-en">{line.orig}</div>
                      {line.trans ? <div className="history-row-zh">{line.trans}</div> : null}
                    </div>
                  </div>
                ))}
                {historyLines.length === 0 ? (
                  <div className="side-empty">字幕会随着讲课实时出现在这里</div>
                ) : null}
              </div>
            </SidePanel>
          </aside>
        </div>
      ) : (
        <div className="recognition-grid">
          <div className="recognition-main">
            <TranscriptPanel
              lines={lines}
              interimText={currentText}
              listening={listening}
              terms={terms}
              onTerm={onSelectWord}
              onExport={() => void exportSrt()}
            />
            <CurrentSubtitle
              label="当前句"
              courseTypeHint="中文识别 · 麦克风收音"
              line={currentLine}
              listening={listening}
              terms={terms}
              onTerm={onSelectWord}
              onExport={() => void exportSrt()}
            />
          </div>

          <aside className="recognition-side">
            <SidePanel title="词汇" count={vocab.length}>
              <WordList entries={vocab} onSelectWord={onSelectWord} />
            </SidePanel>

            <SummaryPanel courseId={course.id} />

            <NotebookPanel courseId={course.id} onToast={onToast} />
          </aside>
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------
   线上：网页框（真实 <webview>，替换原型的 MockCoursePage）
   ------------------------------------------------------------ */

const browserHelp = '该站点不支持内嵌播放时，请在系统浏览器打开，仍可识别字幕。'

function CourseGuest({ url, onStatus }: { url: string; onStatus: (text: string) => void }) {
  const guestRef = useRef<HTMLElement>(null)

  useEffect(() => {
    const guest = guestRef.current
    if (!guest) return
    const timeout = window.setTimeout(() => {
      onStatus('网页加载时间较长。' + browserHelp)
    }, 10000)
    const finish = (message: string) => {
      window.clearTimeout(timeout)
      onStatus(message)
    }
    const didLoad = () => finish('')
    const didFail = (event: Event) => {
      const { isMainFrame, errorCode } = event as Event & { isMainFrame: boolean; errorCode: number }
      if (isMainFrame && errorCode !== -3) finish('网页加载失败。' + browserHelp)
    }
    const didCrash = () => finish('网页进程已退出，请重新拖入链接。' + browserHelp)
    guest.addEventListener('did-finish-load', didLoad)
    guest.addEventListener('did-fail-load', didFail)
    guest.addEventListener('render-process-gone', didCrash)
    guest.setAttribute('src', url)
    return () => {
      window.clearTimeout(timeout)
      guest.removeEventListener('did-finish-load', didLoad)
      guest.removeEventListener('did-fail-load', didFail)
      guest.removeEventListener('render-process-gone', didCrash)
    }
  }, [url, onStatus])

  return createElement('webview', {
    ref: guestRef,
    partition: 'persist:course',
    className: 'web-view-guest',
    title: '课程网页',
  })
}

function WebFrame({ courseName, onToast }: { courseName: string; onToast: (msg: string) => void }) {
  const [url, setUrl] = useState('')
  const [draft, setDraft] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [dragOver, setDragOver] = useState(false)
  const [status, setStatus] = useState('')

  function loadPage(next?: string) {
    const target = (next ?? draft).trim()
    if (!target) return
    if (!/^https?:\/\//i.test(target)) {
      onToast('请粘贴 http(s) 课程链接')
      return
    }
    setUrl(target)
    setAttempt((n) => n + 1)
    setStatus('正在加载课程网页…')
    onToast('网页已嵌入到听课伴侣中间')
  }

  function onDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault()
    setDragOver(false)
    const text = readCourseURL(event.dataTransfer)
    if (!text) {
      onToast('请拖入有效的课程网页链接')
      return
    }
    setDraft(text)
    loadPage(text)
  }

  return (
    <section className="web-frame">
      <div className="web-tabs">
        <div
          className="web-tab"
          draggable
          onDragStart={(event) => {
            event.dataTransfer.setData('text/plain', url)
            event.dataTransfer.effectAllowed = 'copy'
          }}
          title="按住并拖到下方网页区域，即可嵌入"
        >
          <GripVertical size={12} />
          <Globe size={12} />
          <span>{courseName}</span>
        </div>
        <button type="button" className="web-tab-add" title="新建标签页">
          <Plus size={13} />
        </button>
        <div className="web-tab-spacer" />
        {url ? (
          <button type="button" className="text-btn" onClick={() => { setUrl(''); setStatus(''); setDraft('') }}>
            移除网页
          </button>
        ) : null}
      </div>

      <div className="web-toolbar">
        <button type="button" className="web-nav" title="后退" onClick={() => onToast('内嵌网页暂不支持后退，可直接输入新链接')}>
          <ArrowLeft size={14} />
        </button>
        <button type="button" className="web-nav" title="前进" onClick={() => onToast('内嵌网页暂不支持前进')}>
          <ArrowRight size={14} />
        </button>
        <button type="button" className="web-nav" title="刷新" onClick={() => url && loadPage(url)}>
          <RefreshCw size={14} />
        </button>
        <div className="web-address">
          <Globe size={13} />
          <input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') loadPage()
            }}
            placeholder="粘贴课程网页链接"
          />
        </div>
        <button type="button" className="web-open" onClick={() => loadPage()}>
          打开
        </button>
      </div>

      {status ? <div className="web-status" role="status">{status}</div> : null}

      <div
        className={`web-content ${dragOver ? 'drag-over' : ''}`}
        onDragOver={(event) => {
          event.preventDefault()
          setDragOver(true)
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
      >
        {url ? (
          <CourseGuest key={attempt} url={url} onStatus={setStatus} />
        ) : (
          <div className="web-empty">
            <div className="web-empty-icon">
              <Globe size={22} />
            </div>
            <strong>把课程网页链接拖到这里</strong>
            <span>或在地址栏粘贴课程链接，再点「打开」</span>
            <span className="web-empty-hint">{browserHelp}</span>
          </div>
        )}
      </div>
    </section>
  )
}

/* ------------------------------------------------------------
   当前句 / 转写面板 / 侧栏（原样转写）
   ------------------------------------------------------------ */

function CurrentSubtitle({
  label,
  courseTypeHint,
  line,
  listening,
  terms,
  onTerm,
  onExport,
}: {
  label: string
  courseTypeHint: string
  line: DisplayLine
  listening: boolean
  terms: string[]
  onTerm: (term: string) => void
  onExport: () => void
}) {
  return (
    <section className="current-subtitle">
      <div className="current-subtitle-head">
        <span className="live-label">
          <StatusDot tone={listening ? 'green' : 'muted'} />
          {label}
        </span>
        <span className="current-subtitle-meta">{courseTypeHint}</span>
        <button type="button" className="text-btn" onClick={onExport}>
          <Download size={13} /> 导出
        </button>
      </div>
      <div className="current-subtitle-body">
        {line.orig ? (
          <div className="current-en">
            <HighlightedText text={line.orig} terms={terms} onTerm={onTerm} />
          </div>
        ) : null}
        {line.trans && line.trans !== line.orig ? <div className="current-zh">{line.trans}</div> : null}
        {!line.orig && !line.trans ? (
          <div className="current-zh current-zh-muted">等待老师开始讲课…</div>
        ) : null}
      </div>
    </section>
  )
}

function TranscriptPanel({
  lines,
  interimText,
  listening,
  terms,
  onTerm,
  onExport,
}: {
  lines: DisplayLine[]
  interimText: string
  listening: boolean
  terms: string[]
  onTerm: (term: string) => void
  onExport: () => void
}) {
  const bodyRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight
    }
  }, [lines.length, interimText])

  return (
    <section className="transcript-panel">
      <div className="panel-head">
        <div className="panel-title">
          <span className="live-label">
            <StatusDot tone={listening ? 'green' : 'muted'} />
            课堂字幕
          </span>
          <span className="panel-meta">{listening ? '识别中' : '已暂停'}</span>
        </div>
        <button type="button" className="text-btn" onClick={onExport}>
          <Download size={13} /> 导出
        </button>
      </div>

      <div className="transcript-body" ref={bodyRef}>
        {lines.map((line, index) => {
          const isCurrent = index === lines.length - 1
          return (
            <div className={`transcript-line ${isCurrent ? 'current' : ''}`} key={line.id}>
              <span className="transcript-time">{line.time}</span>
              <div className="transcript-text">
                {line.orig ? (
                  <HighlightedText text={line.orig} terms={terms} onTerm={onTerm} />
                ) : null}
                {line.trans && line.trans !== line.orig ? (
                  <div className="transcript-en">{line.trans}</div>
                ) : null}
              </div>
              {isCurrent ? <span className="transcript-current-label">当前</span> : null}
            </div>
          )
        })}
        {interimText ? (
          <div className="transcript-line current interim">
            <span className="transcript-time">···</span>
            <div className="transcript-text">
              <HighlightedText text={interimText} terms={terms} onTerm={onTerm} />
            </div>
          </div>
        ) : null}
        {lines.length === 0 && !interimText ? (
          <div className="side-empty" style={{ padding: 24 }}>等待老师开始讲课…</div>
        ) : null}
      </div>
    </section>
  )
}

function SidePanel({
  title,
  count,
  action,
  children,
}: {
  title: string
  count?: number
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="side-panel">
      <div className="side-panel-head">
        <h3>{title}</h3>
        {typeof count === 'number' ? <span className="side-panel-count">{count}</span> : null}
        {action ? <div className="side-panel-action">{action}</div> : null}
      </div>
      <div className="side-panel-body">{children}</div>
    </section>
  )
}

function WordList({ entries, onSelectWord }: { entries: VocabularyEntry[]; onSelectWord: (word: string) => void }) {
  if (entries.length === 0) {
    return <div className="side-empty">还没有生字，点字幕里的高亮词试试</div>
  }
  return (
    <div className="word-list">
      {entries.map((entry) => (
        <button
          type="button"
          className="word-row"
          key={entry.id}
          onClick={() => onSelectWord(entry.word)}
        >
          <div className="word-row-copy">
            <span className="word-row-word">{entry.word}</span>
            <span className="word-row-trans">{entry.translation}</span>
          </div>
          <span className="word-row-badge">{sm2Badge(entry)}</span>
        </button>
      ))}
    </div>
  )
}

/* ------------------------------------------------------------
   课堂总结（POST /summary，LLM 生成，key_points → 原型 points）
   ------------------------------------------------------------ */

function SummaryPanel({ courseId }: { courseId: string }) {
  const [loading, setLoading] = useState(false)
  const [unavailable, setUnavailable] = useState(false)
  const [data, setData] = useState<{
    summary: string
    key_points: string[]
    review_words: string[]
    updatedAt: number
  } | null>(null)

  const fetchSummary = async () => {
    if (loading) return
    setLoading(true)
    setUnavailable(false)
    setData(null)
    try {
      const res = await fetch(`/api/courses/${courseId}/summary`, { method: 'POST' })
      const body = await res.json()
      if (body.available) {
        setData({ ...body, updatedAt: Date.now() })
      } else {
        setUnavailable(true)
      }
    } catch {
      setUnavailable(true)
    }
    setLoading(false)
  }

  return (
    <SidePanel
      title="课堂总结"
      action={
        <button type="button" className="side-link" onClick={() => void fetchSummary()} disabled={loading}>
          {loading ? '生成中…' : data ? '重新生成' : '生成'}
        </button>
      }
    >
      {loading ? (
        <div className="side-empty">AI 正在阅读最近的字幕，约需十几秒…</div>
      ) : unavailable ? (
        <div className="side-empty">
          AI 摘要服务不可用：请确认 Ollama 已启动并拉取了模型
        </div>
      ) : data ? (
        <div className="summary-block">
          <h4>{data.summary?.slice(0, 40) || '本节课摘要'}</h4>
          <ul>
            {(data.key_points ?? []).map((point) => (
              <li key={point}>{point}</li>
            ))}
          </ul>
          {data.review_words?.length ? (
            <div className="summary-words">
              <Sparkles size={12} /> 生词复习：{data.review_words.slice(0, 6).join('、')}
            </div>
          ) : null}
          <div className="summary-updated">{relativeTime(data.updatedAt)}更新</div>
        </div>
      ) : (
        <div className="side-empty">点击「生成」，AI 总结本节课的知识点与生词</div>
      )}
    </SidePanel>
  )
}

/* ------------------------------------------------------------
   小笔记本（GET/PUT note，本次后端新增；原样的收放交互）
   ------------------------------------------------------------ */

function NotebookPanel({ courseId, onToast }: { courseId: string; onToast: (msg: string) => void }) {
  const [open, setOpen] = useState(true)
  const [text, setText] = useState('')
  const [state, setState] = useState<'clean' | 'dirty' | 'saving' | 'saved'>('clean')
  const loadedRef = useRef('')

  useEffect(() => {
    if (loadedRef.current === courseId) return
    loadedRef.current = courseId
    fetch(`/api/courses/${courseId}/note`)
      .then((r) => (r.ok ? r.json() : { content: '' }))
      .then((body) => { setText(body.content || ''); setState('clean') })
      .catch(() => {})
  }, [courseId])

  async function save() {
    setState('saving')
    try {
      const res = await fetch(`/api/courses/${courseId}/note`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: text }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setState('saved')
      onToast('笔记已保存（仅存本机）')
    } catch {
      setState('dirty')
      onToast('笔记保存失败：后端服务未运行')
    }
  }

  return (
    <section className={`side-panel notebook ${open ? 'open' : ''}`}>
      <button type="button" className="notebook-head" onClick={() => setOpen((value) => !value)}>
        <span>
          <Notebook size={14} /> 小笔记本
        </span>
        <ChevronDown size={14} className={open ? 'open' : ''} />
      </button>
      {open ? (
        <div className="notebook-body">
          <textarea
            value={text}
            onChange={(event) => {
              setText(event.target.value)
              setState('dirty')
            }}
            placeholder="随手记下没听懂的地方…"
          />
          <div className="notebook-foot">
            <span>
              <Lock size={11} /> 仅存本机
            </span>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => void save()}
              disabled={state === 'saving' || state === 'clean'}
            >
              {state === 'saving' ? '保存中…' : state === 'saved' ? '已保存' : state === 'dirty' ? '保存' : '已保存'}
            </button>
          </div>
        </div>
      ) : null}
    </section>
  )
}
