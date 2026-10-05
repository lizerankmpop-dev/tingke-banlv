import { useCallback, useEffect, useRef, useState } from 'react'
import { useAppStore } from '../stores/useAppStore'
import { useCourseStore } from '../stores/useCourseStore'
import type { VocabularyEntry, VocabularyStats } from '../types'
import { downloadUrl, exportUrl, vocabularyApi } from '../lib/vocabularyApi'
import { formatNextReview, isDue } from '../lib/sm2'
import {
  ArrowLeft,
  BookOpen,
  BookmarkCheck,
  Loader2,
  ExternalLink,
  Search,
  GraduationCap,
  Download,
  Upload,
  Trash2,
} from 'lucide-react'

export default function DictionaryWindow() {
  const { setView, courseId } = useAppStore()
  const { courses } = useCourseStore()
  const course = courses.find(c => c.id === courseId)
  const [entries, setEntries] = useState<VocabularyEntry[]>([])
  const [stats, setStats] = useState<VocabularyStats | null>(null)
  const [loading, setLoading] = useState(false)
  const [filter, setFilter] = useState<'all' | 'memorized' | 'unmemorized' | 'due'>('all')
  const [query, setQuery] = useState('')
  const [notice, setNotice] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const loadEntries = useCallback(async () => {
    if (!courseId) return
    setLoading(true)
    try {
      const [list, st] = await Promise.all([
        vocabularyApi.list(courseId),
        vocabularyApi.stats(courseId).catch(() => null),
      ])
      setEntries(list)
      if (st) setStats(st)
    } catch {
      /* 忽略：列表页的失败不需要弹窗，空态本身已经说明问题 */
    }
    setLoading(false)
  }, [courseId])

  useEffect(() => {
    void loadEntries()
  }, [loadEntries])

  const toggleMemorized = async (entry: VocabularyEntry) => {
    const next = !entry.isMemorized
    // 乐观更新：勾选必须跟手，失败时再由 loadEntries 纠正
    setEntries(prev => prev.map(e => (e.id === entry.id ? { ...e, isMemorized: next } : e)))
    try {
      await vocabularyApi.setMemorized(courseId!, entry.id, next)
      setStats(s => (s ? { ...s, memorized: s.memorized + (next ? 1 : -1) } : s))
    } catch {
      setEntries(prev => prev.map(e => (e.id === entry.id ? { ...e, isMemorized: !next } : e)))
      flash('标记失败，请重试')
    }
  }

  const remove = async (entry: VocabularyEntry) => {
    const ok = window.confirm(`确定从生词本删除「${entry.word}」？复习进度会一并丢失。`)
    if (!ok) return
    try {
      await vocabularyApi.remove(courseId!, entry.word)
      setEntries(prev => prev.filter(e => e.id !== entry.id))
      flash(`已删除 ${entry.word}`)
    } catch {
      flash('删除失败，请重试')
    }
  }

  const flash = (text: string) => {
    setNotice(text)
    window.setTimeout(() => setNotice(null), 2600)
  }

  const exportCsv = () => {
    downloadUrl(exportUrl(courseId!, 'csv'))
    flash('已开始下载 CSV（Excel 可直接打开）')
  }

  const exportBackup = () => {
    downloadUrl(exportUrl(courseId!, 'json'))
    flash('已开始下载 JSON 备份（含复习进度，换机可恢复）')
  }

  /** 导入 JSON 备份：保留复习进度，不删旧词 */
  const importBackup = async (file: File) => {
    try {
      const text = await file.text()
      const parsed = JSON.parse(text)
      const items = Array.isArray(parsed) ? parsed : parsed?.items
      if (!Array.isArray(items) || items.length === 0) {
        flash('这个文件里没有可导入的生词')
        return
      }
      const res = await vocabularyApi.importJson(courseId!, items)
      flash(`导入完成：新增/更新 ${res.imported} 条，跳过 ${res.skipped} 条`)
      await loadEntries()
    } catch (err) {
      flash(`导入失败：${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const now = Date.now()
  const dueCount = entries.filter(e => !e.isMemorized && isDue(e.nextReviewAt, now)).length

  const q = query.trim().toLowerCase()
  const filtered = entries.filter(e => {
    if (filter === 'memorized' && !e.isMemorized) return false
    if (filter === 'unmemorized' && e.isMemorized) return false
    if (filter === 'due' && (e.isMemorized || !isDue(e.nextReviewAt, now))) return false
    if (q) {
      return (
        (e.word || '').toLowerCase().includes(q) ||
        (e.translation || '').toLowerCase().includes(q)
      )
    }
    return true
  })

  return (
    <div style={styles.window}>
      {/* 标题栏 */}
      <div style={styles.titleBar}>
        <button onClick={() => setView('subtitle-overlay')} style={styles.backBtn}>
          <ArrowLeft size={18} />
        </button>
        <span style={styles.title}>{course?.name || '课程词典'}</span>
        <span style={styles.count}>{entries.length} 词</span>
      </div>

      {/* 统计卡片：把「有多少要复习」直接摆在最显眼的位置，
          否则学生根本不知道间隔重复在替他排程 */}
      <div style={styles.statsRow}>
        <div style={styles.statBox}>
          <div style={{ ...styles.statNum, color: dueCount > 0 ? 'var(--warning)' : 'var(--text-secondary)' }}>
            {dueCount}
          </div>
          <div style={styles.statLabel}>待复习</div>
        </div>
        <div style={styles.statBox}>
          <div style={styles.statNum}>{stats?.reviewedToday ?? 0}</div>
          <div style={styles.statLabel}>今日已复习</div>
        </div>
        <div style={styles.statBox}>
          <div style={{ ...styles.statNum, color: 'var(--success)' }}>{stats?.memorized ?? 0}</div>
          <div style={styles.statLabel}>已掌握</div>
        </div>
        <div style={styles.statBox}>
          <div style={styles.statNum}>
            {stats ? `${Math.round((stats.masteryRate ?? 0) * 100)}%` : '—'}
          </div>
          <div style={styles.statLabel}>掌握率</div>
        </div>
      </div>

      {/* 主要动作：复习 / 导出 / 导入 */}
      <div style={styles.actionRow}>
        <button
          onClick={() => setView('review')}
          disabled={dueCount === 0}
          style={{ ...styles.reviewBtn, opacity: dueCount === 0 ? 0.45 : 1 }}
        >
          <GraduationCap size={14} />
          {dueCount > 0 ? `开始复习（${dueCount}）` : '暂无待复习'}
        </button>
        <button onClick={exportCsv} style={styles.iconAction} title="导出 CSV（Excel 可打开）">
          <Download size={14} /> CSV
        </button>
        <button onClick={exportBackup} style={styles.iconAction} title="导出 JSON 备份（含复习进度）">
          <Download size={14} /> 备份
        </button>
        <button
          onClick={() => fileInputRef.current?.click()}
          style={styles.iconAction}
          title="从 JSON 备份导入（保留复习进度）"
        >
          <Upload size={14} /> 导入
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept="application/json,.json"
          style={{ display: 'none' }}
          onChange={e => {
            const file = e.target.files?.[0]
            if (file) void importBackup(file)
            e.target.value = '' // 允许连续导入同一个文件
          }}
        />
      </div>

      {notice && <div style={styles.notice}>{notice}</div>}

      {/* 筛选 */}
      <div style={styles.filterBar}>
        {(['all', 'due', 'unmemorized', 'memorized'] as const).map(f => (
          <button key={f} onClick={() => setFilter(f)}
            style={{
              ...styles.filterBtn,
              background: filter === f ? 'var(--accent)' : 'transparent',
              color: filter === f ? 'white' : 'var(--text-muted)',
            }}>
            {{ all: '全部', due: `待复习${dueCount ? ` ${dueCount}` : ''}`, unmemorized: '未掌握', memorized: '已掌握' }[f]}
          </button>
        ))}
      </div>

      {/* 搜索框 */}
      <div style={styles.searchBox}>
        <Search size={14} color="var(--text-muted)" />
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="搜索单词或释义..."
          style={styles.searchInput}
        />
        {query && (
          <button onClick={() => setQuery('')} style={styles.clearBtn}>✕</button>
        )}
      </div>

      {/* 词汇列表 */}
      <div style={styles.list}>
        {loading ? (
          <div style={styles.empty}><Loader2 size={18} style={{ animation: 'spin 1s linear infinite' }} /> 加载中...</div>
        ) : filtered.length === 0 ? (
          <div style={styles.empty}>
            <BookOpen size={40} color="var(--text-muted)" />
            <p style={{ marginTop: 12, color: 'var(--text-muted)', fontSize: 14 }}>暂无词汇</p>
            <p style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>AI 将在识别过程中自动提取术语</p>
          </div>
        ) : (
          filtered.map(entry => (
            <div key={entry.id} style={styles.entry}>
              <button onClick={() => void toggleMemorized(entry)} style={styles.checkBtn}>
                <BookmarkCheck size={16} color={entry.isMemorized ? 'var(--success)' : 'var(--text-muted)'} />
              </button>
              <div style={styles.entryInfo}>
                <div style={styles.entryWord}>
                  {entry.word}
                  {entry.isAutoGenerated && <span style={styles.aiTag}>AI</span>}
                  {!entry.isMemorized && isDue(entry.nextReviewAt, now) && (
                    <span style={styles.dueTag}>待复习</span>
                  )}
                </div>
                <div style={styles.entryTrans}>{entry.translation}</div>
                {entry.contextSentence && (
                  <div style={styles.entryContext}>"{entry.contextSentence}"</div>
                )}
                <div style={styles.entryMeta}>
                  复习 {entry.reviewCount ?? 0} 次 · 下次 {formatNextReview(entry.nextReviewAt, now)}
                </div>
              </div>
              <button
                onClick={() => void remove(entry)}
                style={styles.externalBtn}
                title="从生词本删除"
              >
                <Trash2 size={13} />
              </button>
              <button onClick={() => window.open(`https://dict.youdao.com/search?q=${entry.word}`, '_blank')}
                style={styles.externalBtn}>
                <ExternalLink size={12} />
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  window: {
    display: 'flex', flexDirection: 'column',
    height: '100vh', maxWidth: 520, margin: '0 auto',
    background: 'var(--bg-primary)',
  },
  titleBar: {
    display: 'flex', alignItems: 'center', gap: 10,
    padding: '20px 20px 16px', flexShrink: 0,
    // @ts-ignore
    WebkitAppRegion: 'drag',
  },
  backBtn: {
    background: 'none', color: 'var(--text-secondary)',
    padding: 4, borderRadius: 4, // @ts-ignore
    WebkitAppRegion: 'no-drag',
  },
  title: { fontSize: 18, fontWeight: 600, flex: 1 },
  count: { fontSize: 13, color: 'var(--text-muted)' },
  statsRow: {
    display: 'flex', gap: 8, padding: '0 20px 12px', flexShrink: 0,
  },
  statBox: {
    flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2,
    padding: '8px 4px', background: 'var(--bg-card)',
    borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)',
  },
  statNum: { fontSize: 17, fontWeight: 700, color: 'var(--text-primary)' },
  statLabel: { fontSize: 10, color: 'var(--text-muted)', whiteSpace: 'nowrap' },
  actionRow: {
    display: 'flex', gap: 6, padding: '0 20px 12px', flexShrink: 0, alignItems: 'center',
    flexWrap: 'wrap',
  },
  reviewBtn: {
    display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
    flex: 1, minWidth: 130, padding: '9px 14px',
    background: 'var(--accent)', color: '#fff',
    borderRadius: 'var(--radius-sm)', fontSize: 13, fontWeight: 600,
  },
  iconAction: {
    display: 'flex', alignItems: 'center', gap: 4,
    padding: '9px 10px', background: 'var(--bg-card)',
    border: '1px solid var(--border)', color: 'var(--text-secondary)',
    borderRadius: 'var(--radius-sm)', fontSize: 11,
  },
  notice: {
    margin: '0 20px 10px', padding: '7px 12px',
    background: 'var(--accent-glow)', color: 'var(--accent-light)',
    borderRadius: 'var(--radius-sm)', fontSize: 12,
  },
  filterBar: {
    display: 'flex', gap: 6, padding: '0 20px 12px', flexShrink: 0,
  },
  filterBtn: {
    padding: '4px 12px', borderRadius: 14, fontSize: 12,
    border: '1px solid var(--border)',
    transition: 'all 0.15s',
  },
  searchBox: {
    display: 'flex', alignItems: 'center', gap: 8,
    margin: '0 20px 12px', padding: '8px 12px',
    background: 'var(--bg-card)', borderRadius: 'var(--radius-sm)',
    border: '1px solid var(--border)', flexShrink: 0,
  },
  searchInput: {
    flex: 1, background: 'none', border: 'none', outline: 'none',
    fontSize: 13, color: 'var(--text-primary)', minWidth: 0,
  },
  clearBtn: { background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', padding: 2, fontSize: 12 },
  list: { flex: 1, overflow: 'auto', padding: '0 16px 16px' },
  empty: {
    display: 'flex', flexDirection: 'column', alignItems: 'center',
    marginTop: 60, color: 'var(--text-muted)',
  },
  entry: {
    display: 'flex', alignItems: 'flex-start', gap: 10,
    padding: '12px 14px', marginBottom: 4,
    background: 'var(--bg-card)', borderRadius: 'var(--radius-sm)',
  },
  checkBtn: { background: 'none', padding: 2, flexShrink: 0, marginTop: 2 },
  entryInfo: { flex: 1, minWidth: 0 },
  entryWord: {
    fontSize: 15, fontWeight: 600, marginBottom: 2,
    display: 'flex', alignItems: 'center', gap: 6,
  },
  entryTrans: { fontSize: 13, color: 'var(--accent-light)' },
  entryMeta: { fontSize: 10, color: 'var(--text-muted)', marginTop: 4 },
  dueTag: {
    background: 'rgba(253, 203, 110, 0.18)', color: 'var(--warning)',
    padding: '0px 5px', borderRadius: 4, fontSize: 9, fontWeight: 700,
  },
  entryContext: {
    fontSize: 11, color: 'var(--text-muted)', fontStyle: 'italic',
    marginTop: 4, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
  },
  aiTag: {
    background: 'var(--accent-glow)', color: 'var(--accent-light)',
    padding: '0px 5px', borderRadius: 4, fontSize: 9, fontWeight: 700,
  },
  externalBtn: { background: 'none', color: 'var(--text-muted)', padding: 4, flexShrink: 0, marginTop: 2 },
}
