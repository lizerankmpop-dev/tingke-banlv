import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { VocabularyEntry } from '../types'
import { vocabularyApi } from '../lib/vocabularyApi'
import {
  RATING_LABELS,
  RATING_ORDER,
  applyRating,
  formatNextReview,
  summarizeSession,
  type ReviewRating,
} from '../lib/sm2'
import { AlertCircle, ArrowLeft, BookOpen, Check, Loader2, RotateCcw } from 'lucide-react'

/**
 * 生词复习会话（SM2 四档）
 *
 * 设计取舍：
 *   1. **每评一档立刻落库**，不在会话结束时批量提交。学生会中途关窗、切去上课，
 *      批量提交意味着「按了 40 次，一次都没保存」。代价是每个词一次请求 ——
 *      复习本来就不是高频操作，完全可接受。
 *   2. 先出词、后出答案。看到中文释义再打分等于抄答案，间隔重复的核心机制就废了。
 *   3. 排程在前端算（applyRating），提交给后端只做夹取兜底。
 *      这样按钮上显示的「3 天后」与真正落库的间隔必然一致，不会对不上。
 *   4. 打「忘了」的词在本次会话末尾再出现一次（最多一次）——
 *      10 分钟后才再到期，学生那时早退出复习了，等于没复习。
 */

interface Props {
  courseId: string
  courseName?: string
  onExit: () => void
}

interface Card {
  entry: VocabularyEntry
  /** 本会话内是否已重排过一次（防止「忘了」把同一个词无限刷） */
  requeued: boolean
}

const SESSION_SIZE = 30

export default function ReviewSession({ courseId, courseName, onExit }: Props) {
  const [queue, setQueue] = useState<Card[]>([])
  const [loading, setLoading] = useState(true)
  const [revealed, setRevealed] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [ratings, setRatings] = useState<ReviewRating[]>([])
  /** 本会话已提交的累计次数 —— 中途退出时用来说明「已保存多少」 */
  const submittedRef = useRef(0)
  const [now, setNow] = useState(() => Date.now())

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const items = await vocabularyApi.reviewQueue(courseId, SESSION_SIZE)
      setQueue(items.map((entry) => ({ entry, requeued: false })))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [courseId])

  useEffect(() => {
    void load()
  }, [load])

  // 卡片换一张就重新取一次「现在」，让「下次 3 天后」是按眼前这张算的
  const current = queue[0] ?? null
  useEffect(() => {
    setRevealed(false)
    setNow(Date.now())
  }, [current?.entry.id])

  /** 当前卡片的四档预览（按钮上直接写「3 天后」，用户一眼知道选了会发生什么） */
  const previews = useMemo(() => {
    if (!current) return null
    const out = {} as Record<ReviewRating, string>
    for (const rating of RATING_ORDER) {
      out[rating] = formatNextReview(applyRating(current.entry, rating, now).nextReviewAt, now)
    }
    return out
  }, [current, now])

  const submit = useCallback(
    async (rating: ReviewRating) => {
      if (!current || submitting) return
      setSubmitting(true)
      setError(null)
      const entry = current.entry
      const schedule = applyRating(entry, rating, Date.now())
      try {
        // 立刻落库：见文件头「设计取舍 1」
        await vocabularyApi.review(courseId, entry.word, { ...schedule, rating })
        submittedRef.current += 1
        setRatings((prev) => [...prev, rating])
        setQueue((prev) => {
          const [head, ...rest] = prev
          if (!head) return prev
          const updated: Card = {
            ...head,
            entry: {
              ...head.entry,
              efactor: schedule.efactor,
              interval: schedule.interval,
              repetitions: schedule.repetitions,
              nextReviewAt: schedule.nextReviewAt,
            },
          }
          // 「忘了」的词在本次会话末尾再出现一次（每词最多一次）
          if (rating === 'again' && !head.requeued) {
            return [...rest, { ...updated, requeued: true }]
          }
          return rest
        })
      } catch (err) {
        setError(
          `保存失败（本次评分未生效，可重试）：${err instanceof Error ? err.message : String(err)}`
        )
      } finally {
        setSubmitting(false)
      }
    },
    [courseId, current, submitting]
  )

  // 键盘操作：复习是高频重复动作，只用鼠标会很难受
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      if (e.key === ' ' || e.key === 'Enter') {
        e.preventDefault()
        if (!revealed) setRevealed(true)
        return
      }
      const idx = ['1', '2', '3', '4'].indexOf(e.key)
      if (idx >= 0) {
        e.preventDefault()
        if (!revealed) setRevealed(true)
        else void submit(RATING_ORDER[idx])
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [revealed, submit])

  const done = !loading && queue.length === 0
  const summary = summarizeSession(ratings)

  return (
    <div style={styles.page}>
      <div style={styles.header}>
        <button onClick={onExit} style={styles.backBtn} title="退出复习">
          <ArrowLeft size={18} />
        </button>
        <div style={styles.headerText}>
          <div style={styles.title}>生词复习</div>
          <div style={styles.subtitle}>
            {courseName || '当前课程'}
            {!done && ` · 剩余 ${queue.length}`}
          </div>
        </div>
        <div style={styles.progressPill}>
          {submittedRef.current > 0 ? `已复习 ${submittedRef.current}` : '未开始'}
        </div>
      </div>

      {error && (
        <div style={styles.errorBar}>
          <AlertCircle size={14} />
          <span style={{ flex: 1 }}>{error}</span>
          <button onClick={() => setError(null)} style={styles.errorClose}>
            ✕
          </button>
        </div>
      )}

      {loading ? (
        <div style={styles.center}>
          <Loader2 size={22} style={styles.spin} />
          <p style={styles.centerText}>正在取待复习的词...</p>
        </div>
      ) : done ? (
        <div style={styles.center}>
          {summary.total === 0 ? (
            <>
              <BookOpen size={44} color="var(--text-muted)" />
              <p style={styles.centerTitle}>当前没有待复习的词</p>
              <p style={styles.centerText}>
                生词会在合适的间隔后自动回到这里。先去上课，识别出的术语会自动进入复习队列。
              </p>
            </>
          ) : (
            <>
              <Check size={44} color="var(--success)" />
              <p style={styles.centerTitle}>本轮复习完成</p>
              <p style={styles.centerText}>
                共 {summary.total} 次评价：忘了 {summary.again} · 困难 {summary.hard} · 一般{' '}
                {summary.good} · 简单 {summary.easy}
              </p>
              <p style={styles.centerHint}>
                这些进度已经逐条保存，随时关掉页面都不会丢。
              </p>
            </>
          )}
          <div style={styles.doneActions}>
            <button onClick={() => void load()} style={styles.secondaryBtn}>
              <RotateCcw size={14} /> 重新检查队列
            </button>
            <button onClick={onExit} style={styles.primaryBtn}>
              返回
            </button>
          </div>
        </div>
      ) : (
        current && (
          <div style={styles.cardArea}>
            <div style={styles.card}>
              <div style={styles.word}>{current.entry.word}</div>
              {current.entry.contextSentence && (
                <div style={styles.context}>“{current.entry.contextSentence}”</div>
              )}
              {revealed ? (
                <>
                  <div style={styles.divider} />
                  <div style={styles.translation}>{current.entry.translation || '（无释义）'}</div>
                  {current.entry.definition && (
                    <div style={styles.definition}>{current.entry.definition}</div>
                  )}
                  <div style={styles.metaRow}>
                    <span style={styles.meta}>已复习 {current.entry.reviewCount ?? 0} 次</span>
                    <span style={styles.meta}>
                      难度因子 {(current.entry.efactor ?? 2.5).toFixed(2)}
                    </span>
                    <span style={styles.meta}>
                      上次间隔{' '}
                      {(current.entry.interval ?? 0) > 0
                        ? `${current.entry.interval} 天`
                        : '—'}
                    </span>
                  </div>
                </>
              ) : (
                <button onClick={() => setRevealed(true)} style={styles.revealBtn}>
                  显示释义（空格）
                </button>
              )}
            </div>

            <div style={styles.ratingRow}>
              {RATING_ORDER.map((rating, i) => (
                <button
                  key={rating}
                  disabled={!revealed || submitting}
                  onClick={() => void submit(rating)}
                  style={{
                    ...styles.ratingBtn,
                    borderColor: RATING_COLORS[rating],
                    color: revealed ? RATING_COLORS[rating] : 'var(--text-muted)',
                    opacity: revealed ? 1 : 0.45,
                  }}
                >
                  <span style={styles.ratingLabel}>
                    {RATING_LABELS[rating].label}
                    <kbd style={styles.kbd}>{i + 1}</kbd>
                  </span>
                  <span style={styles.ratingHint}>
                    {revealed && previews ? previews[rating] : RATING_LABELS[rating].hint}
                  </span>
                </button>
              ))}
            </div>

            {submitting && (
              <div style={styles.savingRow}>
                <Loader2 size={12} style={styles.spin} /> 正在保存...
              </div>
            )}
          </div>
        )
      )}
    </div>
  )
}

const RATING_COLORS: Record<ReviewRating, string> = {
  again: 'var(--danger)',
  hard: 'var(--warning)',
  good: 'var(--accent-light)',
  easy: 'var(--success)',
}

const styles: Record<string, React.CSSProperties> = {
  page: {
    display: 'flex',
    flexDirection: 'column',
    height: '100vh',
    maxWidth: 560,
    margin: '0 auto',
    background: 'var(--bg-primary)',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '20px 20px 14px',
    flexShrink: 0,
    // @ts-ignore
    WebkitAppRegion: 'drag',
  },
  backBtn: {
    background: 'none',
    color: 'var(--text-secondary)',
    padding: 4,
    borderRadius: 4,
    // @ts-ignore
    WebkitAppRegion: 'no-drag',
  },
  headerText: { flex: 1, minWidth: 0 },
  title: { fontSize: 17, fontWeight: 600 },
  subtitle: { fontSize: 12, color: 'var(--text-muted)', marginTop: 2 },
  progressPill: {
    fontSize: 11,
    color: 'var(--accent-light)',
    background: 'var(--accent-glow)',
    borderRadius: 10,
    padding: '3px 10px',
    // @ts-ignore
    WebkitAppRegion: 'no-drag',
  },
  errorBar: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    margin: '0 20px 10px',
    padding: '8px 12px',
    background: 'rgba(225, 112, 85, 0.12)',
    border: '1px solid var(--danger)',
    borderRadius: 'var(--radius-sm)',
    color: 'var(--danger)',
    fontSize: 12,
  },
  errorClose: { background: 'none', color: 'var(--danger)', padding: 2, cursor: 'pointer' },
  center: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 10,
    padding: '0 40px',
    textAlign: 'center',
  },
  centerTitle: { fontSize: 16, fontWeight: 600, marginTop: 8 },
  centerText: { fontSize: 13, color: 'var(--text-muted)', lineHeight: 1.7, maxWidth: 380 },
  centerHint: { fontSize: 12, color: 'var(--text-muted)', opacity: 0.75 },
  doneActions: { display: 'flex', gap: 10, marginTop: 18 },
  cardArea: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    padding: '0 20px 24px',
    gap: 14,
    minHeight: 0,
  },
  card: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    justifyContent: 'center',
    alignItems: 'center',
    gap: 12,
    padding: '28px 24px',
    background: 'var(--bg-card)',
    borderRadius: 'var(--radius)',
    border: '1px solid var(--border)',
    overflow: 'auto',
  },
  word: {
    fontSize: 32,
    fontWeight: 700,
    textAlign: 'center',
    wordBreak: 'break-word',
  },
  context: {
    fontSize: 13,
    color: 'var(--text-muted)',
    fontStyle: 'italic',
    textAlign: 'center',
    lineHeight: 1.6,
    maxWidth: 420,
  },
  divider: { width: 60, height: 1, background: 'var(--border)', margin: '4px 0' },
  translation: { fontSize: 19, fontWeight: 600, color: 'var(--accent-light)', textAlign: 'center' },
  definition: {
    fontSize: 13,
    color: 'var(--text-secondary)',
    textAlign: 'center',
    lineHeight: 1.7,
    maxWidth: 420,
  },
  metaRow: { display: 'flex', gap: 14, marginTop: 6, flexWrap: 'wrap', justifyContent: 'center' },
  meta: { fontSize: 11, color: 'var(--text-muted)' },
  revealBtn: {
    marginTop: 6,
    padding: '8px 20px',
    background: 'var(--bg-hover)',
    color: 'var(--text-primary)',
    borderRadius: 'var(--radius-sm)',
    fontSize: 13,
  },
  ratingRow: { display: 'flex', gap: 8, flexShrink: 0 },
  ratingBtn: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 3,
    padding: '10px 6px',
    background: 'var(--bg-card)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius-sm)',
    transition: 'all 0.15s',
  },
  ratingLabel: { fontSize: 13, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 5 },
  ratingHint: { fontSize: 10, color: 'var(--text-muted)' },
  kbd: {
    fontSize: 9,
    color: 'var(--text-muted)',
    border: '1px solid var(--border)',
    borderRadius: 3,
    padding: '0 3px',
    lineHeight: 1.4,
  },
  savingRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    fontSize: 11,
    color: 'var(--text-muted)',
    flexShrink: 0,
  },
  primaryBtn: {
    padding: '10px 26px',
    background: 'var(--accent)',
    color: '#fff',
    borderRadius: 'var(--radius-sm)',
    fontSize: 13,
    fontWeight: 600,
  },
  secondaryBtn: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    padding: '10px 18px',
    background: 'var(--bg-card)',
    border: '1px solid var(--border)',
    color: 'var(--text-secondary)',
    borderRadius: 'var(--radius-sm)',
    fontSize: 13,
  },
  spin: { animation: 'spin 1s linear infinite' },
}
