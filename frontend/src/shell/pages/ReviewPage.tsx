import { useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowLeft, ArrowRight, Brain, Check, CheckCircle2, Clock, RotateCcw, Search } from 'lucide-react'
import { IconButton } from '../../components/ui/IconButton'
import { vocabularyApi } from '../../lib/vocabularyApi'
import {
  RATING_LABELS, RATING_ORDER, applyRating, formatNextReview, summarizeSession,
} from '../../lib/sm2'
import type { ReviewRating } from '../../lib/sm2'
import type { VocabularyEntry } from '../../types'
import { sm2Badge } from '../lib/mapping'
import { useCourseStore } from '../../stores/useCourseStore'

/**
 * 今日复习页（自李梓萌原型 App.jsx 的 Review 原样转写）
 *
 * 原型 → 真实数据：
 * · REVIEW_WORDS 假队列 → 跨课程到期队列（GET review-queue，全部课程合并，
 *   她的左侧「今日到期」列表 + 右侧卡片原样保留）
 * · 四档评分文案「10分钟/1天/3天/7天」→ SM2 真实排程预览
 *   （applyRating 在前端算，按钮上显示的就是真实落库间隔 —— 主线既定设计）
 * · 完成/再复习一轮/进度 → 原样
 * 保留的主线机制：先出词、空格/回车翻面、1-4 键评分（不砍已有功能）
 * D11 自主复习：到期队列之外，可按课程勾选任意生词临时组成一轮复习，
 * 评分照常走 SM2 落库 —— 不等到期也能主动巩固。
 */

interface ReviewPageProps {
  onBack: () => void
  onSelectWord: (word: string) => void
  onToast: (message: string) => void
}

interface Card {
  entry: VocabularyEntry
  courseId: string
  courseName: string
  requeued: boolean
}

const SESSION_SIZE = 30

export function ReviewPage({ onBack, onSelectWord, onToast }: ReviewPageProps) {
  const { courses, loadCourses } = useCourseStore()
  const [queue, setQueue] = useState<Card[]>([])
  const [loading, setLoading] = useState(true)
  const [revealed, setRevealed] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [ratings, setRatings] = useState<ReviewRating[]>([])
  const [now, setNow] = useState(() => Date.now())

  // D11 自主复习
  const [mode, setMode] = useState<'due' | 'self'>('due')
  const [pool, setPool] = useState<Card[]>([])
  const [poolLoading, setPoolLoading] = useState(false)
  const [selectedCourse, setSelectedCourse] = useState('')
  const [wordQuery, setWordQuery] = useState('')
  const [picked, setPicked] = useState<Set<string>>(new Set())

  useEffect(() => { void loadCourses() }, [loadCourses])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      // 跨课程合并到期队列（她的复习页是全局入口，不分课程）
      const batches = await Promise.all(
        courses.map((course) => vocabularyApi.reviewQueue(course.id, SESSION_SIZE).catch(() => [])),
      )
      const merged: Card[] = []
      batches.forEach((items, index) => {
        const course = courses[index]
        for (const entry of items) {
          merged.push({ entry, courseId: course.id, courseName: course.name, requeued: false })
        }
      })
      // 全局按到期时间升序
      merged.sort((a, b) => (a.entry.nextReviewAt ?? 0) - (b.entry.nextReviewAt ?? 0))
      setQueue(merged.slice(0, SESSION_SIZE))
    } finally {
      setLoading(false)
    }
  }, [courses])

  useEffect(() => { if (courses.length > 0 && mode === 'due') void load() }, [load, mode]) // eslint-disable-line react-hooks/exhaustive-deps

  const loadPool = useCallback(async () => {
    setPoolLoading(true)
    try {
      const targets = selectedCourse ? courses.filter(c => c.id === selectedCourse) : courses
      const batches = await Promise.all(
        targets.map((course) => vocabularyApi.list(course.id).catch(() => [])),
      )
      const all: Card[] = []
      batches.forEach((items, index) => {
        const course = targets[index]
        for (const entry of items) {
          all.push({ entry, courseId: course.id, courseName: course.name, requeued: false })
        }
      })
      setPool(all)
      setPicked(new Set())
    } finally {
      setPoolLoading(false)
    }
  }, [courses, selectedCourse])

  useEffect(() => {
    if (mode === 'self' && courses.length > 0) void loadPool()
  }, [mode, loadPool, courses.length]) // eslint-disable-line react-hooks/exhaustive-deps

  const cardKey = (card: Card) => `${card.courseId}::${card.entry.id}`
  const filteredPool = useMemo(() => {
    const q = wordQuery.trim().toLowerCase()
    if (!q) return pool
    return pool.filter(card => [card.entry.word, card.entry.translation, card.entry.definition]
      .join(' ').toLowerCase().includes(q))
  }, [pool, wordQuery])

  function togglePick(card: Card) {
    setPicked((prev) => {
      const next = new Set(prev)
      const key = cardKey(card)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  function startSelfReview() {
    const chosen = pool.filter(card => picked.has(cardKey(card)))
    if (chosen.length === 0) {
      onToast('请先勾选要复习的生词')
      return
    }
    setRatings([])
    setQueue(chosen)
    setRevealed(false)
  }

  const current = queue[0] ?? null
  useEffect(() => {
    setRevealed(false)
    setNow(Date.now())
  }, [current?.entry.id])

  /** 四档预览：按钮上直接写真实间隔（主线设计：按钮与落库必然一致） */
  const previews = useMemo(() => {
    if (!current) return null
    const out = {} as Record<ReviewRating, string>
    for (const rating of RATING_ORDER) {
      out[rating] = formatNextReview(applyRating(current.entry, rating, now).nextReviewAt, now)
    }
    return out
  }, [current, now])

  const submit = useCallback(async (rating: ReviewRating) => {
    if (!current || submitting) return
    setSubmitting(true)
    const entry = current.entry
    const schedule = applyRating(entry, rating, Date.now())
    try {
      await vocabularyApi.review(current.courseId, entry.word, { ...schedule, rating })
      setRatings((prev) => [...prev, rating])
      setQueue((prev) => {
        const [head, ...rest] = prev
        if (!head) return prev
        const updated: Card = {
          ...head,
          entry: {
            ...head.entry,
            efactor: schedule.efactor, interval: schedule.interval,
            repetitions: schedule.repetitions, nextReviewAt: schedule.nextReviewAt,
          },
        }
        if (rating === 'again' && !head.requeued) {
          return [...rest, { ...updated, requeued: true }]
        }
        return rest
      })
    } catch (err) {
      onToast(`保存失败（本次评分未生效，可重试）：${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setSubmitting(false)
    }
  }, [current, submitting, onToast])

  // 键盘：空格/回车翻面，1-4 评分（主线已有，不砍）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      if (selfPicking) return
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

  const selfPicking = mode === 'self' && queue.length === 0 && ratings.length === 0
  const done = !loading && queue.length === 0 && ratings.length > 0
  const empty = !loading && queue.length === 0 && ratings.length === 0 && !selfPicking
  const total = ratings.length + queue.length
  const progress = done ? 100 : total > 0 ? Math.round((ratings.length / total) * 100) : 0
  const summary = summarizeSession(ratings)

  const restart = () => {
    setRatings([])
    setQueue([])
    setRevealed(false)
    if (mode === 'due') void load()
    else void loadPool()
  }

  return (
    <div className="page page-wide">
      <div className="topbar">
        <div className="topbar-left">
          <IconButton label="返回课程列表" onClick={onBack}>
            <ArrowLeft size={18} />
          </IconButton>
          <div className="topbar-title-wrap">
            <div className="topbar-title">今日复习</div>
            <div className="topbar-sub">
              SM2 间隔重复 · {mode === 'due' ? `今日到期 ${total} 个词` : '自主复习 · 自己挑词'}
            </div>
          </div>
        </div>
        <div className="review-mode-switch" role="tablist" aria-label="复习方式">
          <button type="button" role="tab" aria-selected={mode === 'due'}
            className={mode === 'due' ? 'active' : ''} onClick={() => { setMode('due'); setQueue([]); setRatings([]) }}>
            到期复习
          </button>
          <button type="button" role="tab" aria-selected={mode === 'self'}
            className={mode === 'self' ? 'active' : ''} onClick={() => { setMode('self'); setQueue([]); setRatings([]) }}>
            自主复习
          </button>
        </div>
        <div className="review-top-progress">
          <span>{ratings.length} / {total}</span>
          <div className="progress-track">
            <span style={{ width: `${progress}%` }} />
          </div>
        </div>
      </div>

      {selfPicking ? (
        <section className="self-review-picker panel">
          <div className="panel-head">
            <div>
              <h2 className="panel-title">自选生词</h2>
              <p className="page-sub">按课程筛选、勾选要复习的词；评分同样会更新 SM2 排程。</p>
            </div>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => void loadPool()}
              disabled={poolLoading}>刷新词库</button>
          </div>
          <div className="self-review-filters">
            <select value={selectedCourse} onChange={(e) => setSelectedCourse(e.target.value)}
              aria-label="筛选课程">
              <option value="">全部课程</option>
              {courses.map(course => <option key={course.id} value={course.id}>{course.name}</option>)}
            </select>
            <label className="search-box">
              <Search size={15} />
              <input value={wordQuery} onChange={(e) => setWordQuery(e.target.value)}
                placeholder="搜索单词或释义" aria-label="搜索生词" />
            </label>
            <span className="self-review-count">已选 {picked.size} / {pool.length}</span>
          </div>
          {poolLoading ? (
            <p className="page-sub">正在读取生词本…</p>
          ) : filteredPool.length === 0 ? (
            <p className="page-sub">没有匹配的生词。先在字幕里收藏几个词，再来这里复习。</p>
          ) : (
            <div className="self-review-grid">
              {filteredPool.map(card => {
                const key = cardKey(card)
                return (
                  <label key={key} className={`self-review-item ${picked.has(key) ? 'picked' : ''}`}>
                    <input type="checkbox" checked={picked.has(key)} onChange={() => togglePick(card)} />
                    <strong>{card.entry.word}</strong>
                    <span>{card.entry.translation || card.courseName}</span>
                  </label>
                )
              })}
            </div>
          )}
          <div className="self-review-actions">
            <button type="button" className="btn btn-primary btn-sm" onClick={startSelfReview}
              disabled={picked.size === 0}>开始复习（{picked.size}）</button>
          </div>
        </section>
      ) : loading ? (
        <div className="review-card" style={{ margin: '40px auto', maxWidth: 640 }}>
          <p className="review-definition" style={{ textAlign: 'center' }}>正在加载今日到期词…</p>
        </div>
      ) : empty ? (
        <div className="review-card review-done" style={{ margin: '40px auto', maxWidth: 640 }}>
          <div className="done-icon">
            <CheckCircle2 size={32} />
          </div>
          <h2>今天没有到期的词</h2>
          <p>上课时点字幕里的生词收藏进来，SM2 会自动安排复习；也可以切到自主复习自己挑词。</p>
          <div className="done-actions">
            <button type="button" className="btn btn-primary btn-sm" onClick={() => setMode('self')}>
              自主复习
            </button>
            <button type="button" className="btn btn-secondary btn-sm" onClick={onBack}>
              返回课程列表
            </button>
          </div>
        </div>
      ) : (
        <div className="review-layout">
          <aside className="due-list panel">
            <div className="due-head">
              <span>今日到期</span>
              <span>{total}</span>
            </div>
            {/* 左侧列表：按当前队列原样展示（已答过的不再列出，与主线会话机制一致） */}
            {queue.map((card, itemIndex) => (
              <div
                className={`due-item ${itemIndex === 0 && !done ? 'active' : ''}`}
                key={card.entry.id}
              >
                <span className="due-word">{card.entry.word}</span>
                <span className="due-badge">{sm2Badge(card.entry)}</span>
              </div>
            ))}
            <div className="due-note">
              <Clock size={13} />
              <span>按记忆曲线安排，答错会缩短间隔。</span>
            </div>
          </aside>

          {done ? (
            <div className="review-card review-done">
              <div className="done-icon">
                <CheckCircle2 size={32} />
              </div>
              <h2>本轮复习完成</h2>
              <p>你已完成 {ratings.length} 张卡片，SM2 已根据评分安排下一次复习。</p>
              <div className="done-stats">
                <div>
                  <strong>{ratings.length}</strong>
                  <span>今日复习</span>
                </div>
                <div>
                  <strong>{summary.again}</strong>
                  <span>答错重排</span>
                </div>
                <div>
                  <strong>100%</strong>
                  <span>完成度</span>
                </div>
              </div>
              <div className="done-actions">
                <button type="button" className="btn btn-primary btn-sm" onClick={restart}>
                  <RotateCcw size={15} /> 再复习一轮
                </button>
                <button type="button" className="btn btn-secondary btn-sm" onClick={onBack}>
                  返回课程列表
                </button>
              </div>
            </div>
          ) : current ? (
            <div className="review-card">
              <div className="review-top">
                <span className="badge purple">{sm2Badge(current.entry)}</span>
                <span className="review-count">第 {ratings.length + 1} / {total} 张 · {current.courseName}</span>
              </div>

              <div className="review-word">{current.entry.word}</div>

              {revealed ? (
                <>
                  <div className="review-translation">{current.entry.translation}</div>
                  <div className="review-divider" />
                  {current.entry.contextSentence ? (
                    <div className="review-context">“{current.entry.contextSentence}”</div>
                  ) : null}
                  {current.entry.definition ? (
                    <p className="review-definition">{current.entry.definition}</p>
                  ) : null}
                  <button type="button" className="text-btn" style={{ marginBottom: 10 }} onClick={() => onSelectWord(current.entry.word)}>
                    查看完整释义与 AI 讲解 <ArrowRight size={12} />
                  </button>

                  <div className="rating-label">这次记得怎么样？</div>
                  <div className="rating-grid">
                    {RATING_ORDER.map((rating) => (
                      <button
                        key={rating}
                        type="button"
                        className={`rating ${rating}`}
                        onClick={() => void submit(rating)}
                        disabled={submitting}
                      >
                        <span>{RATING_LABELS[rating].label}</span>
                        <small>{previews ? previews[rating] : ''}</small>
                      </button>
                    ))}
                  </div>
                </>
              ) : (
                <>
                  <div className="review-divider" />
                  <div className="rating-label" style={{ marginTop: 24 }}>
                    想想这个词的意思，然后按空格看答案
                  </div>
                  <div className="rating-grid">
                    <button type="button" className="rating good" onClick={() => setRevealed(true)}>
                      <span>看答案</span>
                      <small>空格 / 回车</small>
                    </button>
                  </div>
                </>
              )}

              <div className="review-foot">
                <Brain size={14} />
                <span>评分会更新下次到期时间，词卡始终保留它出现时的原句。</span>
              </div>
            </div>
          ) : null}
        </div>
      )}

      <div className="review-insight">
        <div className="insight-title">
          <Brain size={14} /> SM2 是怎么安排复习的
        </div>
        <div className="insight-steps">
          <div>
            <span>01</span>
            <strong>听到生词</strong>
            <p>字幕里点一下，自动进生词本</p>
          </div>
          <div>
            <span>02</span>
            <strong>到期待复习</strong>
            <p>间隔从 1 天开始逐步拉长</p>
          </div>
          <div>
            <span>03</span>
            <strong>评分后调整</strong>
            <p>忘了缩短，简单拉长</p>
          </div>
        </div>
      </div>
    </div>
  )
}

