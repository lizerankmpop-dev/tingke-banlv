import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import type { Course } from '../../types'

interface ExportSession {
  sessionId: string
  startedAt: number
  count: number
  firstText: string
  isLatest: boolean
}

interface Props {
  course: Course
  onClose: () => void
  onToast: (message: string) => void
}

export function SubtitleExportPanel({ course, onClose, onToast }: Props) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [sessions, setSessions] = useState<ExportSession[]>([])
  const [sessionId, setSessionId] = useState('')
  const [offset, setOffset] = useState('')
  const [loading, setLoading] = useState(true)
  const [exporting, setExporting] = useState(false)
  const [notice, setNotice] = useState('')
  const downloadRequest = useRef<AbortController | null>(null)

  useEffect(() => {
    const panel = dialog.current
    panel?.showModal()
    return () => { panel?.close() }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    fetch(`/api/courses/${course.id}/sessions`, { signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error('会话列表不可用')
        return response.json()
      })
      .then(data => {
        if (controller.signal.aborted) return
        const list: ExportSession[] = Array.isArray(data.sessions) ? data.sessions : []
        setSessions(list)
        setSessionId(list.find(session => session.isLatest)?.sessionId ?? '')
      })
      .catch(() => {
        if (!controller.signal.aborted) setNotice('会话列表读取失败，可以先导出最近一次识别。')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => {
      controller.abort()
      downloadRequest.current?.abort()
    }
  }, [course.id])

  async function exportSrt(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (downloadRequest.current) return
    const seconds = Number(offset)
    const offsetMs = Math.round(seconds * 1000)
    if (!Number.isSafeInteger(offsetMs) || offsetMs < 0) {
      setNotice('请填写有效的视频起点秒数。')
      return
    }

    const controller = new AbortController()
    downloadRequest.current = controller
    setExporting(true)
    setNotice('')
    const query = new URLSearchParams({ offsetMs: String(offsetMs) })
    if (sessionId) query.set('sessionId', sessionId)
    try {
      const response = await fetch(`/api/courses/${course.id}/export-srt?${query}`, {
        signal: controller.signal,
      })
      const data = await response.json()
      if (controller.signal.aborted) return
      if (!response.ok) {
        setNotice(typeof data.detail === 'string' ? data.detail : '字幕导出失败，请重试。')
        return
      }
      if (!data.count || typeof data.srt !== 'string' || !data.srt.trim()) {
        setNotice('所选会话没有字幕可以导出。')
        return
      }

      const blob = new Blob([data.srt], { type: 'application/x-subrip;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `${course.name.replace(/[\\/:*?"<>|]/g, '_') || 'course'}.srt`
      document.body.append(link)
      link.click()
      link.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
      onToast(`已请求下载 ${data.count} 条字幕`)
      onClose()
    } catch {
      if (!controller.signal.aborted) setNotice('字幕导出失败，请检查本地服务。')
    } finally {
      downloadRequest.current = null
      if (!controller.signal.aborted) setExporting(false)
    }
  }

  return (
    <dialog ref={dialog} aria-labelledby="subtitle-export-title" onCancel={onClose}>
      <form onSubmit={event => void exportSrt(event)}>
        <h2 id="subtitle-export-title">导出字幕</h2>
        <p>
          <label>识别会话{' '}
            <select disabled={loading || exporting} value={sessionId}
              onChange={event => setSessionId(event.target.value)}>
              <option value="">最近一次识别</option>
              {sessions.map(session => (
                <option key={session.sessionId} value={session.sessionId}>
                  {session.startedAt ? new Date(session.startedAt).toLocaleString() : '历史会话'}
                  {' · '}{session.count} 条{session.firstText ? ` · ${session.firstText}` : ''}
                </option>
              ))}
            </select>
          </label>
        </p>
        <p>
          <label>视频起点（秒）{' '}
            <input type="number" min="0" step="0.001" value={offset}
              onChange={event => setOffset(event.target.value)} placeholder="0" disabled={exporting} />
          </label>
        </p>
        {notice && <p role="status">{notice}</p>}
        <button type="button" className="btn btn-secondary" onClick={onClose}>关闭</button>
        <button type="submit" className="btn btn-primary" disabled={loading || exporting}>
          {exporting ? '正在导出…' : '下载 SRT'}
        </button>
      </form>
    </dialog>
  )
}
