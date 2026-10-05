import { useEffect, useRef, useState } from 'react'
import { useAppStore } from '../stores/useAppStore'
import { useCourseStore } from '../stores/useCourseStore'
import { useSubtitleStore } from '../stores/useSubtitleStore'
import { useOverlayStore } from '../stores/useOverlayStore'
import OverlayControlPanel from '../components/OverlayControlPanel'
import { X, MinusCircle, BookOpen, Mic, MicOff, Settings, Loader2, AlertCircle, School, Monitor, Sparkles, FileText, ListChecks, GraduationCap, MessageCircle, Send, PictureInPicture2 } from 'lucide-react'

export default function SubtitleOverlay() {
  const { courseId, stopListening, setView } = useAppStore()
  const { courses } = useCourseStore()
  const course = courses.find(c => c.id === courseId)
  const isOffline = course?.courseType === 'offline'

  const {
    subtitleLines, currentText, currentWords,
    asrStatus, asrMessage, connectASR, disconnectASR, retryConnect,
    selectedWord, isWordPanelOpen, closeWordPanel, selectWord,
  } = useSubtitleStore()

  const [collapsed, setCollapsed] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  // 桌面悬浮窗控制面板
  const [overlayPanelOpen, setOverlayPanelOpen] = useState(false)
  const overlayOpen = useOverlayStore((s) => s.open)
  const overlayInit = useOverlayStore((s) => s.init)

  useEffect(() => {
    void overlayInit()
  }, [overlayInit])

  // AI 课摘要状态
  const [summaryOpen, setSummaryOpen] = useState(false)
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [summaryData, setSummaryData] = useState<{
    summary: string
    key_points: string[]
    review_words: string[]
    subtitleCount: number
    wordCount: number
  } | null>(null)
  const [summaryUnavailable, setSummaryUnavailable] = useState(false)

  const fetchSummary = async () => {
    if (!courseId || summaryLoading) return
    setSummaryOpen(true)
    setSummaryLoading(true)
    setSummaryData(null)
    setSummaryUnavailable(false)
    try {
      const res = await fetch(`/api/courses/${courseId}/summary`, { method: 'POST' })
      const data = await res.json()
      if (data.available) {
        setSummaryData(data)
      } else {
        setSummaryUnavailable(true)
      }
    } catch {
      setSummaryUnavailable(true)
    }
    setSummaryLoading(false)
  }

  // AI 答疑状态
  const [qaOpen, setQaOpen] = useState(false)
  const [qaInput, setQaInput] = useState('')
  const [qaMessages, setQaMessages] = useState<{ q: string; a: string }[]>([])
  const [qaLoading, setQaLoading] = useState(false)
  const [qaUnavailable, setQaUnavailable] = useState(false)
  const qaBottomRef = useRef<HTMLDivElement>(null)

  // 问答消息更新时滚动到底部
  useEffect(() => {
    qaBottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [qaMessages, qaLoading])

  const submitQuestion = async () => {
    const question = qaInput.trim()
    if (!question || qaLoading || !courseId) return
    setQaInput('')
    setQaLoading(true)
    setQaUnavailable(false)
    try {
      const res = await fetch('/api/ai/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, courseId }),
      })
      const data = await res.json()
      if (data.available) {
        setQaMessages((prev) => [...prev.slice(-4), { q: question, a: data.answer }])
      } else {
        setQaUnavailable(true)
        setQaMessages((prev) => [...prev.slice(-4), { q: question, a: '' }])
      }
    } catch {
      setQaUnavailable(true)
    }
    setQaLoading(false)
  }

  // 自动连接 ASR
  useEffect(() => {
    if (courseId) {
      connectASR(courseId)
    }
    return () => { disconnectASR() }
  }, [courseId])

  // 字幕更新时自动滚动到底部
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [subtitleLines.length, currentText])

  const isListening = asrStatus === 'listening' || asrStatus === 'processing'
  const isLoading = asrStatus === 'loading'
  const hasError = asrStatus === 'error'

  // 取最近的字幕
  const recentLines = subtitleLines.slice(-3)
  const hasContent = recentLines.length > 0 || !!currentText

  const handleWordClick = (word: string) => {
    const clean = word.replace(/[^a-zA-Z0-9\u4e00-\u9fff-]/g, '')
    if (clean.length > 1) selectWord(word)
  }

  if (collapsed) {
    return (
      <div style={{
        ...styles.collapsed,
        borderColor: isOffline ? 'rgba(0,206,201,0.3)' : 'var(--border)',
      }} onClick={() => setCollapsed(false)}>
        {isOffline ? <School size={14} color="#00cec9" /> : <Mic size={14} color="var(--accent-light)" />}
        <span style={styles.collapsedText}>听课伴侣</span>
        <span style={{
          width: 6, height: 6, borderRadius: 3,
          background: isListening ? 'var(--success)' : 'var(--text-muted)',
          animation: isListening ? 'pulse 1.5s infinite' : undefined,
        }} />
      </div>
    )
  }

  return (
    <div ref={containerRef} style={styles.overlay}>
      {/* 顶部控制栏 */}
      <div style={styles.controlBar}>
        <div style={styles.controlLeft}>
          <span style={styles.courseLabel}>{course?.name || '字幕'}</span>
          {/* 模式标签 */}
          <span style={{
            ...styles.modeTag,
            background: isOffline ? 'rgba(0,206,201,0.15)' : 'var(--accent-glow)',
            color: isOffline ? '#00cec9' : 'var(--accent-light)',
          }}>
            {isOffline ? <School size={10} /> : <Monitor size={10} />}
            {isOffline ? ' 线下' : ' 网课'}
          </span>
          <span style={{
            ...styles.statusDot,
            background: isListening ? 'var(--success)' : hasError ? 'var(--danger)' : 'var(--text-muted)',
          }} />
          <span style={styles.statusText}>
            {isListening ? '识别中' : hasError ? '错误' : '就绪'}
          </span>
        </div>
        <div style={styles.controlRight}>
          {/* 桌面悬浮窗：开启后可切到浏览器看课，字幕始终浮在最上层 */}
          <button
            onClick={() => setOverlayPanelOpen(true)}
            style={{
              ...styles.ctrlBtn,
              color: overlayOpen ? 'var(--accent-light)' : 'var(--text-muted)',
            }}
            title={overlayOpen ? '桌面悬浮窗已开启' : '开启桌面悬浮窗'}
          >
            <PictureInPicture2 size={15} />
          </button>
          <button onClick={() => setQaOpen(true)} style={styles.ctrlBtn} title="AI 答疑">
            <MessageCircle size={15} />
          </button>
          <button onClick={fetchSummary} style={styles.ctrlBtn} title="AI 课摘要">
            <FileText size={15} />
          </button>
          <button onClick={() => setView('dictionary')} style={styles.ctrlBtn} title="课程词典">
            <BookOpen size={15} />
          </button>
          <button onClick={() => setView('course-config')} style={styles.ctrlBtn} title="设置">
            <Settings size={15} />
          </button>
          <button onClick={() => setCollapsed(true)} style={styles.ctrlBtn} title="最小化">
            <MinusCircle size={15} />
          </button>
          <button onClick={() => { disconnectASR(); stopListening() }} style={styles.ctrlBtn} title="关闭">
            <X size={15} />
          </button>
        </div>
      </div>

      {/* 字幕内容区 */}
      <div style={styles.subtitleArea}>
        {!hasContent && (
          <div style={styles.waiting}>
            {isLoading ? (
              <>
                <Loader2 size={18} style={{ animation: 'spin 1s linear infinite' }} color="var(--accent-light)" />
                <span>{asrMessage || '模型加载中，请稍候...'}</span>
              </>
            ) : isListening ? (
              <>
                <Loader2 size={18} style={{ animation: 'spin 1s linear infinite' }} color="var(--accent-light)" />
                <span>{isOffline ? '🎙 正在通过麦克风收音...' : '正在监听系统音频...'}</span>
              </>
            ) : hasError ? (
              <>
                <AlertCircle size={18} color="var(--danger)" />
                <span>{asrMessage || '连接错误'}</span>
                <button onClick={() => retryConnect()} style={styles.retryBtn}>
                  重试连接
                </button>
              </>
            ) : (
              <span>等待音频输入...</span>
            )}
          </div>
        )}

        {/* 历史字幕（半透明） */}
        {recentLines.map((line) => (
          <div key={line.id} style={styles.historicalLine}>
            {line.words?.length > 0 ? (
              line.words.map((w, i) => (
                <span key={i} onClick={() => handleWordClick(w.word)} style={styles.wordLink}>
                  {w.word}{' '}
                </span>
              ))
            ) : (
              <span>{line.text}</span>
            )}
          </div>
        ))}

        {/* 当前实时字幕 */}
        {currentText && (
          <div style={styles.currentLine}>
            {currentWords.length > 0 ? (
              currentWords.map((w, i) => (
                <span key={i} onClick={() => handleWordClick(w.word)} style={styles.activeWord}>
                  {w.word}{' '}
                </span>
              ))
            ) : (
              <span style={styles.activeWord}>{currentText}</span>
            )}
          </div>
        )}

        {/* 自动滚动锚点 */}
        <div ref={bottomRef} style={{ height: 1 }} />
      </div>

      {/* 单词释义弹出卡片 */}
      {isWordPanelOpen && selectedWord && (
        <div style={styles.wordCard}>
          <WordPanelInline word={selectedWord} onClose={closeWordPanel} />
        </div>
      )}

      {/* AI 课摘要模态 */}
      {summaryOpen && (
        <div style={styles.sumOverlay} onClick={() => setSummaryOpen(false)}>
          <div style={styles.sumModal} onClick={(e) => e.stopPropagation()}>
            <div style={styles.sumHeader}>
              <div style={styles.sumHeaderLeft}>
                <GraduationCap size={16} color="var(--accent-light)" />
                <span style={styles.sumTitle}>AI 课摘要</span>
              </div>
              <button onClick={() => setSummaryOpen(false)} style={styles.wcClose}><X size={14} /></button>
            </div>

            <div style={styles.sumBody}>
              {summaryLoading ? (
                <div style={styles.sumLoading}>
                  <Loader2 size={20} style={{ animation: 'spin 1s linear infinite' }} color="var(--accent-light)" />
                  <span>AI 正在梳理本节课内容，约需 10-30 秒...</span>
                </div>
              ) : summaryUnavailable ? (
                <div style={styles.sumUnavailable}>
                  <AlertCircle size={18} color="var(--danger)" />
                  <span>AI 摘要服务不可用，请确认 Ollama 已启动并拉取了模型</span>
                </div>
              ) : summaryData ? (
                <>
                  <div style={styles.sumSection}>
                    <div style={styles.sumLabel}>本节课摘要</div>
                    <div style={styles.sumText}>{summaryData.summary}</div>
                  </div>
                  {summaryData.key_points?.length > 0 && (
                    <div style={styles.sumSection}>
                      <div style={styles.sumLabel}><ListChecks size={11} style={{ verticalAlign: -1, marginRight: 4 }} />知识点</div>
                      <ul style={styles.sumList}>
                        {summaryData.key_points.map((p, i) => <li key={i} style={styles.sumListItem}>{p}</li>)}
                      </ul>
                    </div>
                  )}
                  {summaryData.review_words?.length > 0 && (
                    <div style={styles.sumSection}>
                      <div style={styles.sumLabel}>建议复习生词</div>
                      <div style={styles.sumWordsRow}>
                        {summaryData.review_words.map((w, i) => (
                          <span key={i} style={styles.sumWordTag}>{w}</span>
                        ))}
                      </div>
                    </div>
                  )}
                  <div style={styles.sumMeta}>
                    基于 {summaryData.subtitleCount} 条字幕 · 课程词典 {summaryData.wordCount} 词
                  </div>
                </>
              ) : null}
            </div>
          </div>
        </div>
      )}

      {/* AI 答疑模态 */}
      {qaOpen && (
        <div style={styles.sumOverlay} onClick={() => setQaOpen(false)}>
          <div style={styles.qaModal} onClick={(e) => e.stopPropagation()}>
            <div style={styles.sumHeader}>
              <div style={styles.sumHeaderLeft}>
                <MessageCircle size={16} color="var(--accent-light)" />
                <span style={styles.sumTitle}>AI 答疑</span>
                <span style={styles.qaHint}>基于最近字幕回答</span>
              </div>
              <button onClick={() => setQaOpen(false)} style={styles.wcClose}><X size={14} /></button>
            </div>

            <div style={styles.qaBody}>
              {qaMessages.length === 0 && !qaLoading && !qaUnavailable && (
                <div style={styles.qaEmpty}>
                  <MessageCircle size={28} color="var(--text-muted)" />
                  <p style={styles.qaEmptyText}>问问刚才课上没听懂的内容</p>
                  <p style={styles.qaEmptySub}>例如：刚才讲的 overfitting 是什么意思？</p>
                </div>
              )}
              {qaMessages.map((m, i) => (
                <div key={i} style={styles.qaMsgBlock}>
                  <div style={styles.qaQuestion}>🙋 {m.q}</div>
                  {m.a ? (
                    <div style={styles.qaAnswer}>{m.a}</div>
                  ) : qaUnavailable ? (
                    <div style={styles.qaAnswerError}>AI 服务不可用（Ollama 未启动或云端 API 未配置）</div>
                  ) : null}
                </div>
              ))}
              {qaLoading && (
                <div style={styles.qaLoading}>
                  <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} color="var(--accent-light)" />
                  AI 正在结合字幕思考...
                </div>
              )}
              <div ref={qaBottomRef} style={{ height: 1 }} />
            </div>

            <div style={styles.qaInputBar}>
              <input
                value={qaInput}
                onChange={(e) => setQaInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') submitQuestion() }}
                placeholder="输入问题，回车发送..."
                style={styles.qaInput}
                autoFocus
              />
              <button onClick={submitQuestion} disabled={!qaInput.trim() || qaLoading} style={{
                ...styles.qaSendBtn,
                opacity: !qaInput.trim() || qaLoading ? 0.4 : 1,
              }}>
                <Send size={14} />
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 桌面悬浮窗控制面板 */}
      {overlayPanelOpen && <OverlayControlPanel onClose={() => setOverlayPanelOpen(false)} />}
    </div>
  )
}

// 内联单词面板（在悬浮窗内弹出）—— 只消费 store 的查询结果，不重复请求
function WordPanelInline({ word, onClose }: { word: string; onClose: () => void }) {
  const { wordEntry, wordNotFound } = useSubtitleStore()
  const courseId = useCourseStore((s) => s.activeCourseId)
  const [aiState, setAiState] = useState<'idle' | 'loading' | 'done' | 'unavailable'>('idle')
  const [aiData, setAiData] = useState<{ explanation: { translation: string; meaning_in_context: string; example: string; related: string[] } } | null>(null)

  // 切换单词时重置 AI 讲解
  useEffect(() => {
    setAiState('idle')
    setAiData(null)
  }, [word])

  const fetchAIExplain = async () => {
    if (!courseId || aiState === 'loading') return
    setAiState('loading')
    try {
      const res = await fetch('/api/ai/explain', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ word, courseId }),
      })
      const data = await res.json()
      if (data.available) {
        setAiData(data)
        setAiState('done')
      } else {
        setAiState('unavailable')
      }
    } catch {
      setAiState('unavailable')
    }
  }

  return (
    <div style={styles.wcContainer}>
      <div style={styles.wcHeader}>
        <span style={styles.wcWord}>{word}</span>
        <button onClick={onClose} style={styles.wcClose}><X size={14} /></button>
      </div>
      {wordEntry ? (
        <div>
          <div style={styles.wcTrans}>{wordEntry.translation}</div>
          {wordEntry.definition && <div style={styles.wcDef}>{wordEntry.definition}</div>}
          {wordEntry.contextSentence && <div style={styles.wcContext}>"{wordEntry.contextSentence}"</div>}
        </div>
      ) : wordNotFound ? (
        <div style={styles.wcNotFound}>
          未收录到课程词典
          <button onClick={() => window.open(`https://dict.youdao.com/search?q=${encodeURIComponent(word)}`, '_blank')}
            style={styles.wcExternal}>在有道词典中查看</button>
        </div>
      ) : (
        <div style={styles.wcLoading}><Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} /> 查询中...</div>
      )}

      {/* AI 深度讲解 */}
      {aiState === 'idle' && (
        <button onClick={fetchAIExplain} style={styles.aiBtn}>
          <Sparkles size={13} /> AI 深度讲解
        </button>
      )}
      {aiState === 'loading' && (
        <div style={styles.aiLoading}>
          <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} /> AI 正在结合课程语境生成讲解...
        </div>
      )}
      {aiState === 'unavailable' && (
        <div style={styles.aiUnavailable}>
          AI 讲解服务不可用（Ollama 未启动）
        </div>
      )}
      {aiState === 'done' && aiData && (
        <div style={styles.aiResult}>
          <div style={styles.aiLabel}><Sparkles size={11} /> 本课语境含义</div>
          <div style={styles.aiText}>{aiData.explanation.meaning_in_context}</div>
          {aiData.explanation.example && (
            <>
              <div style={styles.aiLabel}>例句</div>
              <div style={styles.aiText}>{aiData.explanation.example}</div>
            </>
          )}
          {aiData.explanation.related?.length > 0 && (
            <>
              <div style={styles.aiLabel}>相关词</div>
              <div style={styles.aiRelatedRow}>
                {aiData.explanation.related.map((r, i) => (
                  <span key={i} style={styles.aiRelatedTag}>{r}</span>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  overlay: {
    width: '100vw', height: '100vh',
    display: 'flex', flexDirection: 'column',
    background: 'transparent',
    overflow: 'hidden',
  },
  collapsed: {
    position: 'fixed', bottom: 20, right: 20,
    display: 'flex', alignItems: 'center', gap: 6,
    padding: '8px 14px',
    background: 'rgba(26, 26, 46, 0.9)',
    borderRadius: 20, cursor: 'pointer',
    border: '1px solid var(--border)',
    boxShadow: '0 2px 12px rgba(0,0,0,0.4)',
    zIndex: 9999,
  },
  collapsedText: { fontSize: 12, color: 'var(--text-secondary)' },
  controlBar: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    padding: '6px 12px',
    background: 'rgba(15, 15, 15, 0.85)',
    backdropFilter: 'blur(12px)',
    borderBottom: '1px solid var(--border)',
    flexShrink: 0,
  },
  controlLeft: { display: 'flex', alignItems: 'center', gap: 8 },
  courseLabel: { fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' },
  modeTag: {
    display: 'inline-flex', alignItems: 'center', gap: 2,
    padding: '1px 6px', borderRadius: 8, fontSize: 10, fontWeight: 500,
    whiteSpace: 'nowrap',
  },
  statusDot: { width: 6, height: 6, borderRadius: 3 },
  statusText: { fontSize: 11, color: 'var(--text-muted)' },
  controlRight: { display: 'flex', alignItems: 'center', gap: 2 },
  ctrlBtn: {
    background: 'none', color: 'var(--text-muted)',
    padding: '4px 6px', borderRadius: 4,
  },
  subtitleArea: {
    flex: 1,
    display: 'flex', flexDirection: 'column', justifyContent: 'flex-end',
    padding: '16px 20px 30px',
    overflow: 'auto',
    pointerEvents: 'none',
  },
  waiting: {
    display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
    color: 'var(--text-muted)', fontSize: 13,
    padding: 20,
  },
  retryBtn: {
    background: 'var(--accent)', color: 'white',
    border: 'none', borderRadius: 6,
    padding: '4px 12px', fontSize: 12, fontWeight: 500,
    cursor: 'pointer',
  },
  historicalLine: {
    fontSize: 14,
    color: 'rgba(255,255,255,0.35)',
    textAlign: 'center',
    lineHeight: 1.7,
    marginBottom: 2,
    textShadow: '0 1px 4px rgba(0,0,0,0.7)',
    pointerEvents: 'auto',
  },
  wordLink: { cursor: 'pointer', transition: 'color 0.15s' },
  currentLine: {
    fontSize: 18,
    fontWeight: 600,
    textAlign: 'center',
    lineHeight: 1.8,
    padding: '8px 20px',
    background: 'rgba(0,0,0,0.6)',
    borderRadius: 10,
    backdropFilter: 'blur(8px)',
    pointerEvents: 'auto',
    textShadow: '0 2px 8px rgba(0,0,0,0.9)',
  },
  activeWord: {
    cursor: 'pointer',
    textDecoration: 'underline',
    textDecorationColor: 'rgba(255,255,255,0.25)',
    textUnderlineOffset: 4,
    transition: 'color 0.15s',
  },
  // Word card
  wordCard: {
    position: 'absolute', bottom: 120, left: '50%', transform: 'translateX(-50%)',
    maxWidth: 360, width: '90%',
    pointerEvents: 'auto',
    zIndex: 100,
  },
  wcContainer: {
    background: 'var(--bg-secondary)',
    borderRadius: 'var(--radius)',
    padding: '14px 18px',
    border: '1px solid var(--border)',
    boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
  },
  wcHeader: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    marginBottom: 8,
  },
  wcWord: { fontSize: 18, fontWeight: 700, color: 'var(--accent-light)' },
  wcClose: { background: 'none', color: 'var(--text-muted)', padding: 2 },
  wcLoading: { display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--text-muted)' },
  wcTrans: { fontSize: 15, color: 'var(--text-primary)', marginBottom: 6 },
  wcDef: { fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.5, marginBottom: 4 },
  wcContext: { fontSize: 12, color: 'var(--text-muted)', fontStyle: 'italic', lineHeight: 1.5 },
  wcNotFound: { fontSize: 13, color: 'var(--text-muted)', display: 'flex', flexDirection: 'column', gap: 6 },
  wcExternal: {
    background: 'none', color: 'var(--accent-light)', fontSize: 12,
    textAlign: 'left', padding: 0,
  },
  // AI 深度讲解
  aiBtn: {
    display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
    width: '100%', marginTop: 10, padding: '7px 0',
    background: 'var(--accent-glow)', color: 'var(--accent-light)',
    border: '1px solid rgba(99,102,241,0.3)', borderRadius: 'var(--radius-sm)',
    fontSize: 12, fontWeight: 500, cursor: 'pointer',
  },
  aiLoading: {
    display: 'flex', alignItems: 'center', gap: 6,
    marginTop: 10, fontSize: 12, color: 'var(--text-muted)',
  },
  aiUnavailable: { marginTop: 10, fontSize: 12, color: 'var(--text-muted)', textAlign: 'center' },
  aiResult: {
    marginTop: 10, paddingTop: 10,
    borderTop: '1px dashed var(--border)',
  },
  aiLabel: {
    display: 'flex', alignItems: 'center', gap: 4,
    fontSize: 11, fontWeight: 600, color: 'var(--accent-light)',
    marginBottom: 4, marginTop: 6,
  },
  aiText: { fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6 },
  aiRelatedRow: { display: 'flex', flexWrap: 'wrap', gap: 6 },
  aiRelatedTag: {
    background: 'var(--bg-tertiary)', color: 'var(--text-secondary)',
    padding: '2px 8px', borderRadius: 8, fontSize: 11,
  },
  // AI 课摘要模态
  sumOverlay: {
    position: 'fixed', inset: 0,
    background: 'rgba(0,0,0,0.55)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    zIndex: 200,
  },
  sumModal: {
    background: 'var(--bg-secondary)',
    borderRadius: 'var(--radius)',
    border: '1px solid var(--border)',
    boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
    width: 420, maxWidth: '92vw', maxHeight: '80vh',
    display: 'flex', flexDirection: 'column',
  },
  sumHeader: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    padding: '12px 16px', borderBottom: '1px solid var(--border)', flexShrink: 0,
  },
  sumHeaderLeft: { display: 'flex', alignItems: 'center', gap: 8 },
  sumTitle: { fontSize: 14, fontWeight: 600 },
  sumBody: { flex: 1, overflow: 'auto', padding: 16 },
  sumLoading: {
    display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10,
    color: 'var(--text-muted)', fontSize: 13, padding: '36px 0',
  },
  sumUnavailable: {
    display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10,
    color: 'var(--text-muted)', fontSize: 13, padding: '36px 0', textAlign: 'center',
  },
  sumSection: { marginBottom: 14 },
  sumLabel: {
    fontSize: 11, fontWeight: 600, color: 'var(--accent-light)',
    textTransform: 'uppercase', marginBottom: 6,
  },
  sumText: { fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.7 },
  sumList: { margin: 0, paddingLeft: 18 },
  sumListItem: { fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.7, marginBottom: 4 },
  sumWordsRow: { display: 'flex', flexWrap: 'wrap', gap: 6 },
  sumWordTag: {
    background: 'var(--accent-glow)', color: 'var(--accent-light)',
    padding: '3px 10px', borderRadius: 10, fontSize: 12,
  },
  sumMeta: { fontSize: 11, color: 'var(--text-muted)', textAlign: 'center', marginTop: 8 },
  // AI 答疑模态
  qaModal: {
    background: 'var(--bg-secondary)',
    borderRadius: 'var(--radius)',
    border: '1px solid var(--border)',
    boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
    width: 440, maxWidth: '92vw', maxHeight: '80vh',
    display: 'flex', flexDirection: 'column',
  },
  qaHint: { fontSize: 11, color: 'var(--text-muted)', marginLeft: 4 },
  qaBody: { flex: 1, overflow: 'auto', padding: '12px 16px', minHeight: 160 },
  qaEmpty: {
    display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6,
    padding: '32px 0', color: 'var(--text-muted)',
  },
  qaEmptyText: { fontSize: 14, color: 'var(--text-secondary)', margin: 0, marginTop: 8 },
  qaEmptySub: { fontSize: 12, color: 'var(--text-muted)', margin: 0 },
  qaMsgBlock: { marginBottom: 12 },
  qaQuestion: { fontSize: 13, fontWeight: 500, color: 'var(--text-primary)', marginBottom: 6 },
  qaAnswer: {
    fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.7,
    background: 'var(--bg-card)', borderRadius: 'var(--radius-sm)',
    padding: '10px 12px', whiteSpace: 'pre-wrap',
  },
  qaAnswerError: {
    fontSize: 12, color: 'var(--danger)', lineHeight: 1.6,
    background: 'rgba(225,112,85,0.08)', borderRadius: 'var(--radius-sm)',
    padding: '8px 12px',
  },
  qaLoading: {
    display: 'flex', alignItems: 'center', gap: 8,
    fontSize: 12, color: 'var(--text-muted)', padding: '8px 0',
  },
  qaInputBar: {
    display: 'flex', gap: 8, padding: '10px 12px',
    borderTop: '1px solid var(--border)', flexShrink: 0,
  },
  qaInput: {
    flex: 1, padding: '9px 12px', borderRadius: 'var(--radius-sm)',
    fontSize: 13, background: 'var(--bg-card)',
    border: '1px solid var(--border)', color: 'var(--text-primary)', outline: 'none', minWidth: 0,
  },
  qaSendBtn: {
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    width: 38, borderRadius: 'var(--radius-sm)',
    background: 'var(--accent)', color: 'white',
    border: 'none', cursor: 'pointer', flexShrink: 0,
  },
}
