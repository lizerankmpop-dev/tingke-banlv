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
import { SubtitleExportPanel } from '../components/SubtitleExportPanel'
import { readCourseURL } from '../../lib/courseDrop'
import { useSubtitleStore } from '../../stores/useSubtitleStore'
import { useCourseStore } from '../../stores/useCourseStore'
import { useOverlayStore } from '../../stores/useOverlayStore'
import { vocabularyApi } from '../../lib/vocabularyApi'
import type { Course, VocabularyEntry } from '../../types'
import { fromBackendRow, highlightTerms, isOnline, toDisplayLine, sm2Badge, relativeTime, type DisplayLine, type SubtitleRow } from '../lib/mapping'

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
  onOpenOverlaySettings: () => void
  onGoReview: () => void
  onSelectWord: (word: string) => void
  onToast: (message: string) => void
}

export function RecognitionPage({
  course,
  onBack,
  onOpenConfig,
  onOpenOverlaySettings,
  onGoReview,
  onSelectWord,
  onToast,
}: RecognitionPageProps) {
  const online = isOnline(course)
  const setActiveCourse = useCourseStore((s) => s.setActiveCourse)

  const {
    subtitleLines, currentText, asrStatus, asrMessage,
    connectASR, disconnectASR, hydrateSubtitles,
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

  const historyRequest = useRef<AbortController | null>(null)
  const startRecognition = useCallback(() => {
    historyRequest.current?.abort()
    const request = new AbortController()
    historyRequest.current = request
    // 只回填启动前已保存的字幕。新会话的行会由实时轮询添加。
    // 🔴 limit=5000（2026-11-16，C6 修复后契约）：后端单次上限已放宽到
    // SUBTITLE_MAX_ROWS=5000，一节 45 分钟课约 1500 条 —— 原来取 200 会把
    // 大半节课的历史静默截掉（真实课《生成式人工智能（二）》有 715 条）。
    // 一次性拉全量比分页简单，~1500 行 JSON 在本机回环上毫秒级。
    const startedAt = Date.now()
    connectASR(course.id, '')
    fetch(`/api/courses/${course.id}/subtitles?limit=5000`, { signal: request.signal })
      .then(response => response.ok ? response.json() : [])
      .then((rows: SubtitleRow[]) => {
        if (!request.signal.aborted && Array.isArray(rows)) {
          hydrateSubtitles(rows.filter(row => row.createdAt == null || row.createdAt < startedAt))
        }
      })
      .catch(() => { /* 实时识别仍可继续 */ })
  }, [connectASR, course.id, hydrateSubtitles])

  useEffect(() => {
    setActiveCourse(course.id)
    startRecognition()
    return () => {
      historyRequest.current?.abort()
      disconnectASR()
    }
  }, [course.id, disconnectASR, setActiveCourse, startRecognition])

  const [showExport, setShowExport] = useState(false)
  const [webFill, setWebFill] = useState(false)
  const [ending, setEnding] = useState(false)
  const [summaryRequest, setSummaryRequest] = useState(0)
  const pageActive = useRef(false)
  useEffect(() => {
    pageActive.current = true
    return () => { pageActive.current = false }
  }, [])

  async function endAndSummarize() {
    if (ending) return
    setEnding(true)
    const stopped = await disconnectASR()
    if (!pageActive.current) return
    setEnding(false)
    if (!stopped) {
      onToast('未能确认识别已结束，请检查连接后重试')
      return
    }
    setSummaryRequest(request => request + 1)
  }

  const listening = asrStatus === 'listening' || asrStatus === 'processing'
  const hasError = asrStatus === 'error'

  function toggleListening() {
    if (listening) {
      disconnectASR()
      onToast('识别已暂停，字幕会保留在当前句')
    } else {
      startRecognition()
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

  return (
    <div className={`page page-wide recognition-page${online && webFill ? ' web-fill' : ''}`}>
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
          <button type="button" className="btn btn-ghost btn-sm" onClick={onOpenOverlaySettings}>
            悬浮设置
          </button>
          {online && (
            <button type="button" className="btn btn-ghost btn-sm" aria-pressed={webFill}
              onClick={() => setWebFill(value => !value)}>
              {webFill ? '分栏' : '填满视频'}
            </button>
          )}
          <button
            type="button"
            className={`btn btn-sm ${listening ? 'btn-secondary' : 'btn-primary'}`}
            onClick={toggleListening}
            disabled={ending}
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
          <button type="button" className="btn btn-secondary btn-sm" disabled={ending}
            onClick={() => void endAndSummarize()}>
            {ending ? '正在结束…' : '结束并总结'}
          </button>
        </div>
      </div>

      {hasError && asrMessage ? (
        <div className="asr-error-bar" role="alert">
          <RefreshCw size={13} />
          <span style={{ flex: 1 }}>{asrMessage}</span>
          <button type="button" className="text-btn" onClick={startRecognition}>重试</button>
        </div>
      ) : null}

      {online ? (
        <div className="recognition-grid">
          <div className="recognition-main">
            <WebFrame key={course.id} courseId={course.id} courseName={course.name} onToast={onToast} />
            <CurrentSubtitle
              label="实时字幕"
              courseTypeHint={course.language === 'zh' ? '中文识别 · 系统音频' : '双语 · 原文识别'}
              line={currentLine}
              listening={listening}
              terms={terms}
              onTerm={onSelectWord}
              onExport={() => setShowExport(true)}
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

            <HistoryPanel key={course.id} courseId={course.id} recent={historyLines}
              refresh={summaryRequest} />
            {summaryRequest > 0 && <SummaryPanel courseId={course.id} request={summaryRequest} />}
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
              onExport={() => setShowExport(true)}
            />
            <CurrentSubtitle
              label="当前句"
              courseTypeHint="中文识别 · 麦克风收音"
              line={currentLine}
              listening={listening}
              terms={terms}
              onTerm={onSelectWord}
              onExport={() => setShowExport(true)}
            />
          </div>

          <aside className="recognition-side">
            <SidePanel title="词汇" count={vocab.length}>
              <WordList entries={vocab} onSelectWord={onSelectWord} />
            </SidePanel>

            <HistoryPanel courseId={course.id} recent={historyLines} refresh={summaryRequest} />
            <SummaryPanel courseId={course.id} request={summaryRequest} />

            <NotebookPanel courseId={course.id} onToast={onToast} />
          </aside>
        </div>
      )}
      {showExport && <SubtitleExportPanel course={course} onToast={onToast} onClose={() => setShowExport(false)} />}
    </div>
  )
}

interface HistorySession {
  sessionId: string
  startedAt: number
  isLatest: boolean
}

function HistoryPanel({ courseId, recent, refresh }: {
  courseId: string
  recent: DisplayLine[]
  refresh: number
}) {
  const [expanded, setExpanded] = useState(false)
  const [sessionId, setSessionId] = useState('')
  const [chapterId, setChapterId] = useState<string | null>(null)
  const [sessions, setSessions] = useState<HistorySession[]>([])
  const [chapters, setChapters] = useState<{ id: string; name: string }[]>([])
  const [rows, setRows] = useState<SubtitleRow[]>([])
  const [state, setState] = useState<'loading' | 'error' | 'ready'>('loading')

  useEffect(() => {
    if (!expanded) return
    const request = new AbortController()
    const options = { signal: request.signal }

    fetch(`/api/courses/${courseId}/sessions`, options)
      .then(response => response.ok ? response.json() : { sessions: [] })
      .then(data => {
        if (request.signal.aborted) return
        const list: HistorySession[] = data.sessions ?? []
        setSessions(list)
        if (!refresh) setSessionId(list.find(session => session.isLatest)?.sessionId ?? '')
      })
      .catch(() => {})
    fetch(`/api/courses/${courseId}/chapters`, options)
      .then(response => response.ok ? response.json() : [])
      .then(data => {
        if (!request.signal.aborted) setChapters(Array.isArray(data) ? data : [])
      })
      .catch(() => {})

    return () => { request.abort() }
  }, [courseId, expanded, refresh])

  useEffect(() => {
    if (refresh) {
      setExpanded(true)
      setSessionId('')
      setChapterId(null)
    }
  }, [refresh])

  useEffect(() => {
    if (!expanded) return
    const request = new AbortController()
    const query = new URLSearchParams({ limit: '200' })
    if (chapterId !== null) query.set('chapterId', chapterId)
    setState('loading')

    fetch(`/api/courses/${courseId}/subtitles?${query}`, { signal: request.signal })
      .then(response => {
        if (!response.ok) throw new Error('历史字幕请求失败')
        return response.json()
      })
      .then((data: SubtitleRow[]) => {
        if (request.signal.aborted) return
        setRows(data)
        setState('ready')
      })
      .catch(() => {
        if (!request.signal.aborted) setState('error')
      })

    return () => { request.abort() }
  }, [chapterId, courseId, expanded, refresh])

  const lines = expanded
    ? state === 'ready'
      ? rows.filter(row => !sessionId || row.session_id === sessionId).reverse().map(row => toDisplayLine(fromBackendRow(row)))
      : []
    : recent.slice(-6)

  return (
    <SidePanel
      title="历史字幕"
      count={expanded ? lines.length : recent.length}
      action={
        <button type="button" className="side-link" aria-expanded={expanded}
          onClick={() => setExpanded(value => !value)}>
          {expanded ? '收起' : '查看与筛选'}
        </button>
      }
    >
      {expanded && (
        <div>
          <label>会话{' '}
            <select value={sessionId} onChange={event => setSessionId(event.target.value)}>
              <option value="">全部会话</option>
              {sessions.map(session => (
                <option key={session.sessionId} value={session.sessionId}>
                  {session.startedAt ? new Date(session.startedAt).toLocaleString() : '历史会话'}
                  {session.isLatest ? '（最近一次）' : ''}
                </option>
              ))}
            </select>
          </label>
          <label>章节{' '}
            <select value={chapterId ?? '__all__'}
              onChange={event => setChapterId(event.target.value === '__all__' ? null : event.target.value)}>
              <option value="__all__">全部章节</option>
              <option value="">未分章</option>
              {chapters.map(chapter => <option key={chapter.id} value={chapter.id}>{chapter.name}</option>)}
            </select>
          </label>
          {state === 'loading' && <div className="side-empty" role="status">正在加载历史字幕…</div>}
          {state === 'error' && <div className="side-empty" role="alert">历史字幕加载失败，请收起后重试。</div>}
        </div>
      )}
      <div className="history-list">
        {lines.map(line => (
          <div className="history-row" key={line.id}>
            <span className="history-time">{line.time}</span>
            <div className="history-copy">
              <div className="history-row-en">{line.orig}</div>
              {line.trans ? <div className="history-row-zh">{line.trans}</div> : null}
            </div>
          </div>
        ))}
        {lines.length === 0 && (!expanded || state === 'ready') && (
          <div className="side-empty">{expanded ? '没有符合条件的字幕' : '字幕会随着讲课实时出现在这里'}</div>
        )}
      </div>
    </SidePanel>
  )
}

/* ------------------------------------------------------------
   线上：网页框（真实 <webview>，替换原型的 MockCoursePage）
   ------------------------------------------------------------ */

const LOGIN_HOSTS = new Set(['passport.bilibili.com'])
const loginHelp = '该站点需要登录。可在系统浏览器登录并播放课程，听课伴侣仍可识别系统声音。内嵌网页使用独立会话，如在应用内登录，请登录后刷新。'
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
    let mainFrameFailed = false
    const didStart = (event: Event) => {
      if ((event as Event & { isMainFrame?: boolean }).isMainFrame) mainFrameFailed = false
    }
    const didLoad = () => {
      if (mainFrameFailed) return
      const currentUrl = (guest as HTMLElement & { getURL: () => string }).getURL()
      const host = URL.canParse(currentUrl) ? new URL(currentUrl).hostname : ''
      finish(LOGIN_HOSTS.has(host) ? loginHelp : '')
    }
    const didFail = (event: Event) => {
      const { isMainFrame, errorCode } = event as Event & { isMainFrame: boolean; errorCode: number }
      if (isMainFrame && errorCode !== -3) {
        mainFrameFailed = true
        finish('网页加载失败。' + browserHelp)
      }
    }
    const didCrash = () => finish('网页进程已退出，请重新拖入链接。' + browserHelp)
    guest.addEventListener('did-start-navigation', didStart)
    guest.addEventListener('did-finish-load', didLoad)
    guest.addEventListener('did-fail-load', didFail)
    guest.addEventListener('render-process-gone', didCrash)
    guest.setAttribute('src', url)
    return () => {
      window.clearTimeout(timeout)
      guest.removeEventListener('did-start-navigation', didStart)
    guest.removeEventListener('did-finish-load', didLoad)
      guest.removeEventListener('did-fail-load', didFail)
      guest.removeEventListener('render-process-gone', didCrash)
    }
  }, [url, onStatus])

  return createElement('webview', {
    ref: guestRef,
    partition: 'persist:course',
    className: 'web-view-guest',
    style: { width: '100%', height: '100%' },
    title: '课程网页',
  })
}

function WebFrame({ courseId, courseName, onToast }: {
  courseId: string
  courseName: string
  onToast: (msg: string) => void
}) {
  const [url, setUrl] = useState('')
  const [draft, setDraft] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [dragOver, setDragOver] = useState(false)
  const [status, setStatus] = useState('')

  const editedLink = useRef(false)

  useEffect(() => {
    let active = true
    window.electronAPI?.getCourseWebLink?.(courseId).then(saved => {
      if (!active || editedLink.current || !saved) return
      setUrl(saved)
      setDraft(saved)
      setStatus('正在加载课程网页…')
    }).catch(() => {})
    return () => { active = false }
  }, [courseId])

  async function saveLink(next: string) {
    const save = window.electronAPI?.setCourseWebLink
    if (!save) return
    try {
      if (!await save(courseId, next)) onToast('课程网页链接未能保存')
    } catch {
      onToast('课程网页链接未能保存')
    }
  }

  function loadPage(next?: string) {
    const text = (next ?? draft).trim()
    if (!text) return
    const target = readCourseURL({ getData: type => type === 'text/plain' ? text : '' })
    if (!target) {
      onToast('请粘贴有效的 HTTPS 课程链接')
      return
    }
    editedLink.current = true
    setUrl(target)
    setDraft(target)
    void saveLink(target)
    setAttempt((n) => n + 1)
    setStatus('正在加载课程网页…')
  }

  function onDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault()
    setDragOver(false)
    const text = readCourseURL(event.dataTransfer)
    if (!text) {
      onToast('请拖入有效的课程网页链接')
      return
    }
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
          <button type="button" className="text-btn" onClick={() => {
            editedLink.current = true
            setUrl('')
            setStatus('')
            setDraft('')
            void saveLink('')
          }}>
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
            onChange={(event) => {
              editedLink.current = true
              setDraft(event.target.value)
            }}
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

      {url && (
        <button type="button" className="text-btn" onClick={async () => {
          try {
            if (await window.electronAPI?.openCourseExternal?.(url)) return
            await navigator.clipboard.writeText(url)
            onToast('链接已复制，可在系统浏览器打开课程并继续识别')
          } catch {
            onToast('请复制地址栏中的课程链接，在系统浏览器打开')
          }
        }}>
          在系统浏览器打开
        </button>
      )}

      {status ? (
        <div className="web-status" role="status">
          {status}
          {url && !status.startsWith('正在加载') && (
            <button type="button" className="text-btn" onClick={() => loadPage(url)}>
              重新加载
            </button>
          )}
        </div>
      ) : null}

      <div
        className={`web-content${url ? ' has-course-guest' : ''}${dragOver ? ' drag-over' : ''}`}
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

function SummaryPanel({ courseId, request }: { courseId: string; request: number }) {
  const [loading, setLoading] = useState(false)
  const [unavailable, setUnavailable] = useState('')
  const inFlight = useRef<AbortController | null>(null)
  const [data, setData] = useState<{
    summary: string
    key_points: string[]
    review_words: string[]
    updatedAt: number
    subtitleCount?: number
  } | null>(null)

  const fetchSummary = useCallback(async () => {
    inFlight.current?.abort()
    const controller = new AbortController()
    inFlight.current = controller
    setLoading(true)
    setUnavailable('')
    setData(null)
    try {
      const response = await fetch(`/api/courses/${courseId}/summary`, {
        method: 'POST',
        signal: controller.signal,
      })
      const body = await response.json()
      if (controller.signal.aborted) return
      if (response.ok && body.available) {
        setData({ ...body, updatedAt: Date.now() })
      } else {
        setUnavailable(body.detail || body.message || 'AI 摘要服务不可用')
      }
    } catch {
      if (!controller.signal.aborted) setUnavailable('无法连接摘要服务，请稍后重试')
    } finally {
      if (!controller.signal.aborted) setLoading(false)
    }
  }, [courseId])

  useEffect(() => {
    if (request) void fetchSummary()
    return () => { inFlight.current?.abort() }
  }, [fetchSummary, request])

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
          {unavailable}
        </div>
      ) : data ? (
        <div className="summary-block">
          <h4>本节课摘要</h4>
          <p>{data.summary}</p>
          {data.subtitleCount != null && <p>根据 {data.subtitleCount} 条字幕生成</p>}
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
