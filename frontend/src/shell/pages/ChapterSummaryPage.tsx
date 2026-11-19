import { useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowLeft, Check, CircleAlert, FileText, Sparkles } from 'lucide-react'
import { IconButton } from '../../components/ui/IconButton'
import {
  createChapter, listChapters, saveChapterSummary,
  type ChapterRow, type SummaryDraft,
} from '../../lib/chapterApi'
import type { Course } from '../../types'

/**
 * D7「结束并总结」跳转落点：章节总结保存页。
 *
 * 原行为只是把总结塞进识别页侧栏卡片，关掉页面就没了，用户也找不到「存在哪一讲」。
 * 现在总结草稿先落在这里，用户可选已有章节或顺手新建一章，确认后写 chapters.summary；
 * 下次进该课，识别页会读章节目录并问是否继续新建章节。
 */
interface ChapterSummaryPageProps {
  course: Course
  draft: SummaryDraft | null
  onBack: () => void
  onSaved: (chapterId: string, chapterName: string) => void
  onToast: (message: string) => void
}

export function ChapterSummaryPage({ course, draft, onBack, onSaved, onToast }: ChapterSummaryPageProps) {
  const [chapters, setChapters] = useState<ChapterRow[]>([])
  const [loadingChapters, setLoadingChapters] = useState(true)
  const [mode, setMode] = useState<'new' | 'existing'>('new')
  const [chapterId, setChapterId] = useState('')
  const [newName, setNewName] = useState('')
  const [summary, setSummary] = useState(() => draft?.summary ?? '')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    listChapters(course.id, controller.signal)
      .then((rows) => {
        if (controller.signal.aborted) return
        setChapters(rows)
        setNewName(`第 ${rows.length + 1} 讲`)
        setChapterId(rows[rows.length - 1]?.id ?? '')
        setMode(rows.length > 0 ? 'existing' : 'new')
      })
      .catch(() => {
        if (controller.signal.aborted) return
        setNewName('第 1 讲')
      })
      .finally(() => { if (!controller.signal.aborted) setLoadingChapters(false) })
    return () => controller.abort()
  }, [course.id])

  useEffect(() => { if (draft?.summary) setSummary(draft.summary) }, [draft?.summary])

  const preview = useMemo(() => {
    const points = draft?.key_points ?? []
    const words = draft?.review_words ?? []
    return { points, words }
  }, [draft])

  const canSave = summary.trim().length > 0 && !saving
    && (mode === 'existing' ? Boolean(chapterId) : newName.trim().length > 0)

  const handleSave = useCallback(async () => {
    if (!canSave) return
    setSaving(true)
    try {
      let targetId = chapterId
      let targetName = chapters.find(c => c.id === chapterId)?.name ?? ''
      if (mode === 'new') {
        const created = await createChapter(course.id, newName.trim())
        targetId = created.id
        targetName = created.name
      }
      await saveChapterSummary(course.id, targetId, summary.trim())
      onToast(`已保存到章节「${targetName}」`)
      onSaved(targetId, targetName)
    } catch (error) {
      onToast(error instanceof Error ? `保存失败：${error.message}` : '保存失败，请重试')
    } finally {
      setSaving(false)
    }
  }, [canSave, chapterId, chapters, course.id, mode, newName, onSaved, onToast, summary])

  return (
    <div className="page">
      <div className="page-head">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <IconButton label="返回课堂" onClick={onBack}><ArrowLeft size={18} /></IconButton>
          <div>
            <h1 className="page-title">章节总结保存</h1>
            <p className="page-sub">{course.name} · 确认后写入章节目录，下次进课可直接回看</p>
          </div>
        </div>
      </div>

      {draft && !draft.available ? (
        <div className="summary-save-warning" role="status">
          <CircleAlert size={16} />
          <span>{draft.message || 'AI 摘要暂不可用，你可以手写总结后照样保存'}</span>
        </div>
      ) : null}

      <section className="summary-save-card">
        <div className="summary-save-section">
          <div className="summary-save-label">
            <Sparkles size={15} /> <strong>本节课总结</strong>
            {draft?.available && draft.subtitleCount ? (
              <span className="summary-save-meta">依据 {draft.subtitleCount} 条字幕生成</span>
            ) : null}
          </div>
          <textarea
            className="summary-save-textarea"
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
            placeholder="AI 摘要不可用时，可在这里手写本节课的总结…"
            rows={8}
            aria-label="章节总结内容"
          />
          {preview.points.length > 0 ? (
            <div className="summary-save-points">
              <strong>知识点</strong>
              <ul>{preview.points.map((point) => <li key={point}>{point}</li>)}</ul>
            </div>
          ) : null}
          {preview.words.length > 0 ? (
            <div className="summary-save-words">
              <strong>建议复习</strong>
              <span>{preview.words.join('、')}</span>
            </div>
          ) : null}
        </div>

        <div className="summary-save-section">
          <div className="summary-save-label"><FileText size={15} /> <strong>保存到哪一讲</strong></div>
          {loadingChapters ? (
            <p className="page-sub">正在读取章节目录…</p>
          ) : (
            <>
              <div className="summary-save-mode">
                <button type="button" className={mode === 'new' ? 'active' : ''}
                  onClick={() => setMode('new')}>新建章节</button>
                <button type="button" className={mode === 'existing' ? 'active' : ''}
                  onClick={() => setMode('existing')} disabled={chapters.length === 0}>
                  已有章节{chapters.length ? `（${chapters.length}）` : ''}
                </button>
              </div>
              {mode === 'new' ? (
                <label className="summary-save-field">
                  <span>章节名称</span>
                  <input value={newName} onChange={(e) => setNewName(e.target.value)}
                    placeholder="例如：第三讲" maxLength={60} />
                </label>
              ) : (
                <label className="summary-save-field">
                  <span>选择章节</span>
                  <select value={chapterId} onChange={(e) => setChapterId(e.target.value)}>
                    {chapters.map((chapter) => (
                      <option key={chapter.id} value={chapter.id}>
                        {chapter.name}{chapter.summary ? ' · 已有总结' : ''}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </>
          )}
        </div>

        <div className="summary-save-actions">
          <button type="button" className="btn btn-ghost" onClick={onBack} disabled={saving}>稍后再说</button>
          <button type="button" className="btn btn-primary" onClick={() => void handleSave()}
            disabled={!canSave}>
            <Check size={15} /> {saving ? '保存中…' : '保存总结'}
          </button>
        </div>
      </section>
    </div>
  )
}
