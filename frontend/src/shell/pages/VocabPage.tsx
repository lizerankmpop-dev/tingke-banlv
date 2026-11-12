import { useEffect, useMemo, useState } from 'react'
import { ChevronRight, Download, Search } from 'lucide-react'
import { EmptyState } from '../../components/ui/EmptyState'
import { vocabularyApi, exportUrl, downloadUrl } from '../../lib/vocabularyApi'
import { useCourseStore } from '../../stores/useCourseStore'
import type { Course, VocabularyEntry, VocabularyStats } from '../../types'
import { isOnline, sm2Badge } from '../lib/mapping'

/**
 * 生词本页（自李梓萌原型 App.jsx 的 Vocabulary 原样转写）
 *
 * 原型 → 真实数据：
 * · WORDS 假词表 → 全部课程词汇合并（GET /api/courses/{id}/vocabulary），
 *   「来源课程」列正是真实字段 courseId（原型自己就设计了这个概念）
 * · vocab-summary 四格 → GET /vocabulary/stats（跨课程汇总）
 * · 导出 CSV → GET /vocabulary/export.csv（真实下载，不再是演示 toast）
 */

interface VocabPageProps {
  onSelectWord: (word: string) => void
  onToast: (message: string) => void
}

export function VocabPage({ onSelectWord, onToast }: VocabPageProps) {
  const { courses, loadCourses } = useCourseStore()
  const [query, setQuery] = useState('')
  const [entries, setEntries] = useState<Array<{ entry: VocabularyEntry; course: Course }>>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => { void loadCourses() }, [loadCourses])

  useEffect(() => {
    if (courses.length === 0) return
    let cancelled = false
    setLoading(true)
    Promise.all(
      courses.map((course) => vocabularyApi.list(course.id).catch(() => [] as VocabularyEntry[])),
    )
      .then((batches) => {
        if (cancelled) return
        const merged: Array<{ entry: VocabularyEntry; course: Course }> = []
        batches.forEach((items, index) => {
          for (const entry of items) merged.push({ entry, course: courses[index] })
        })
        merged.sort((a, b) => b.entry.createdAt - a.entry.createdAt)
        setEntries(merged)
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [courses])

  const stats = useMemo(() => {
    const total = entries.length
    const memorized = entries.filter((e) => e.entry.isMemorized).length
    const due = entries.filter(
      (e) => !e.entry.isMemorized && (e.entry.nextReviewAt ?? 0) <= Date.now(),
    ).length
    return { total, memorized, due, learning: total - memorized - due }
  }, [entries])

  const filtered = entries.filter(({ entry }) =>
    [entry.word, entry.translation, entry.definition]
      .join(' ')
      .toLowerCase()
      .includes(query.trim().toLowerCase()),
  )

  const exportCsv = () => {
    if (entries.length === 0) {
      onToast('还没有可导出的生词')
      return
    }
    // 多课程时导出词最多的那门（后端按课程导出）；提示用户来源
    const counts = new Map<string, number>()
    for (const { course } of entries) counts.set(course.id, (counts.get(course.id) ?? 0) + 1)
    let best = ''
    let bestCount = -1
    counts.forEach((n, id) => { if (n > bestCount) { bestCount = n; best = id } })
    downloadUrl(exportUrl(best, 'csv'))
    onToast(`已导出 ${bestCount} 个生词（CSV，${courses.find((c) => c.id === best)?.name ?? ''}）`)
  }

  return (
    <div className="page page-wide">
      <header className="page-head">
        <div>
          <h1 className="page-title">生词本</h1>
          <p className="page-sub">从字幕里点过的词都在这里；到期后会出现在「今日复习」。</p>
        </div>
        <div className="head-actions">
          <label className="search-box">
            <Search size={15} />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索单词或释义"
            />
          </label>
          <button type="button" className="btn btn-secondary btn-sm" onClick={exportCsv}>
            <Download size={15} /> 导出 CSV
          </button>
        </div>
      </header>

      <div className="vocab-summary">
        <div className="vocab-stat">
          <strong>{stats.total}</strong>
          <span>已收录</span>
        </div>
        <div className="vocab-stat">
          <strong>{stats.due}</strong>
          <span>今日到期</span>
        </div>
        <div className="vocab-stat">
          <strong>{stats.memorized}</strong>
          <span>已掌握</span>
        </div>
        <div className="vocab-stat">
          <strong>{stats.learning}</strong>
          <span>学习中</span>
        </div>
      </div>

      {loading ? (
        <p className="page-sub" style={{ padding: 32 }}>加载中…</p>
      ) : entries.length === 0 ? (
        <EmptyState
          title="生词本还是空的"
          description="识别页里点字幕上的高亮词，收藏进生词本开始复习。"
        />
      ) : (
        <div className="vocab-table">
          <div className="vocab-head">
            <span>单词</span>
            <span>释义</span>
            <span>来源课程</span>
            <span>复习状态</span>
            <span />
          </div>
          {filtered.map(({ entry, course }) => (
            <button
              key={entry.id}
              type="button"
              className="vocab-row"
              onClick={() => onSelectWord(entry.word)}
            >
              <span className="vocab-word">
                <strong>{entry.word}</strong>
                <small>{entry.domain}</small>
              </span>
              <span className="vocab-trans">{entry.translation}</span>
              <span className="vocab-course">
                <span className={`dot ${isOnline(course) ? 'purple' : 'cyan'}`} />
                {course.name}
              </span>
              <span>
                <span className={`badge ${entry.isMemorized ? 'green' : 'purple'}`}>{sm2Badge(entry)}</span>
              </span>
              <span className="vocab-arrow">
                <ChevronRight size={15} />
              </span>
            </button>
          ))}
          {filtered.length === 0 ? (
            <div className="side-empty" style={{ padding: 24 }}>没有匹配「{query}」的词</div>
          ) : null}
        </div>
      )}
    </div>
  )
}
