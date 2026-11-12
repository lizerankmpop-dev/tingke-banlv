import { useEffect, useRef, useState } from 'react'
import type { DragEvent } from 'react'
import { FileText, ListChecks, Paperclip, Upload, X } from 'lucide-react'
import { formatSize } from '../lib/materials'

/**
 * 课程资料窗口（ADR-004 L1）—— 线上/线下课程通用。
 *
 * - 拖入或点击选择上传（raw body + X-Filename 头，见后端 materials/service.py）；
 * - TXT/MD 已索引（进答疑上下文 + 可抽术语候选）；PDF/PPT/DOCX 只登记；
 * - 「追加候选术语」把资料抽出的候选写进自定义术语框（由父组件回调完成，
 *   本组件不持有术语框状态）；
 * - 边框色跟随场景主题：线下青、线上紫（一色一场景，见界面区分设计方案 §0）。
 */

interface MaterialRecord {
  id: string
  name: string
  ext: string
  size: number
  indexed: boolean
}

interface Props {
  courseId: string
  offline: boolean
  onAppendTerms: (terms: string[]) => void
}

const MAX_SIZE_MB = 20

export default function MaterialsPanel({ courseId, offline, onAppendTerms }: Props) {
  const [materials, setMaterials] = useState<MaterialRecord[]>([])
  const [uploading, setUploading] = useState(0)
  const [message, setMessage] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const accent = offline ? 'var(--accent-offline)' : 'var(--accent)'
  const accentBg = offline ? 'var(--accent-offline-wash)' : 'var(--accent-glow)'
  const accentText = offline ? 'var(--accent-offline)' : 'var(--accent-light)'

  useEffect(() => {
    if (!courseId) return
    let cancelled = false
    fetch(`/api/courses/${courseId}/materials`)
      .then(r => (r.ok ? r.json() : []))
      .then((list: MaterialRecord[]) => { if (!cancelled) setMaterials(list) })
      .catch(() => { /* 后端未起：列表空着即可，上传时会再报错 */ })
    return () => { cancelled = true }
  }, [courseId])

  async function uploadFiles(files: File[]) {
    if (!courseId || files.length === 0) return
    setMessage('')
    const oversized = files.filter(f => f.size > MAX_SIZE_MB * 1024 * 1024)
    if (oversized.length > 0) {
      setMessage(`超过 ${MAX_SIZE_MB}MB 上限：${oversized.map(f => f.name).join('、')}`)
      return
    }
    setUploading(n => n + files.length)
    for (const file of files) {
      try {
        const res = await fetch(`/api/courses/${courseId}/materials`, {
          method: 'POST',
          headers: { 'X-Filename': encodeURIComponent(file.name) },
          body: file,
        })
        if (!res.ok) {
          const detail = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
          setMessage(`${file.name}：${detail.detail || '上传失败'}`)
        }
      } catch {
        setMessage('后端服务未运行，上传失败')
      } finally {
        setUploading(n => Math.max(0, n - 1))
      }
    }
    // 无论成败都刷新列表（部分成功的也要显示出来）
    if (fileInputRef.current) fileInputRef.current.value = ''
    fetch(`/api/courses/${courseId}/materials`)
      .then(r => (r.ok ? r.json() : []))
      .then((list: MaterialRecord[]) => setMaterials(list))
      .catch(() => {})
  }

  function onDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault()
    setDragOver(false)
    const files = Array.from(event.dataTransfer.files || [])
    if (files.length > 0) void uploadFiles(files)
  }

  async function removeMaterial(id: string) {
    if (!courseId) return
    setMaterials(prev => prev.filter(m => m.id !== id))
    try {
      await fetch(`/api/courses/${courseId}/materials/${id}`, { method: 'DELETE' })
    } catch { /* 乐观删除失败就靠下次刷新纠正 */ }
  }

  async function appendCandidates() {
    if (!courseId) return
    setMessage('')
    try {
      const res = await fetch(`/api/courses/${courseId}/materials/terms`, { method: 'POST' })
      const data = await res.json()
      const terms: string[] = data.terms || []
      if (terms.length === 0) {
        setMessage(data.fromFiles > 0
          ? '资料里没找到重复出现的候选术语'
          : '还没有可解析的文本资料（TXT / MD）')
        return
      }
      onAppendTerms(terms)
      setMessage(`已追加 ${terms.length} 个候选术语到术语框，可手动删改`)
    } catch {
      setMessage('后端服务未运行')
    }
  }

  const indexedCount = materials.filter(m => m.indexed).length

  return (
    <div style={{
      border: `1px solid ${dragOver ? accent : 'var(--border)'}`,
      borderRadius: 8,
      padding: '12px 14px',
      background: 'var(--bg-tertiary)',
      transition: 'border-color 0.15s',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <Paperclip size={14} color={accentText} />
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>课程资料</span>
        <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
          {materials.length > 0 ? `${materials.length} 份 · ${indexedCount} 份可解析` : '课件、讲义、术语表'}
        </span>
        <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--text-secondary)' }}>只存本机，不上传云端</span>
      </div>

      <div
        role="button"
        tabIndex={0}
        aria-label="上传课程资料"
        onClick={() => fileInputRef.current?.click()}
        onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') fileInputRef.current?.click() }}
        onDragOver={e => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        style={{
          border: `1px dashed ${accent}`,
          background: dragOver ? accentBg : 'transparent',
          borderRadius: 8,
          padding: '18px 12px',
          textAlign: 'center',
          cursor: 'pointer',
        }}
      >
        <Upload size={18} color={accentText} style={{ marginBottom: 6 }} />
        <div style={{ fontSize: 12, color: 'var(--text-primary)' }}>
          拖入课件 / 讲义，或点击选择
        </div>
        <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 4 }}>
          TXT · MD 可解析 · PDF / PPT / DOCX 暂只登记 · 单文件 ≤ {MAX_SIZE_MB}MB
        </div>
      </div>
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept=".txt,.md,.pdf,.ppt,.pptx,.docx"
        style={{ display: 'none' }}
        onChange={e => { void uploadFiles(Array.from(e.target.files || [])) }}
      />

      {uploading > 0 && (
        <div style={{ fontSize: 12, color: accentText, marginTop: 8 }}>正在上传 {uploading} 个文件…</div>
      )}

      {message && (
        <div role="status" style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 8 }}>{message}</div>
      )}

      {materials.length > 0 && (
        <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
          {materials.map(m => (
            <div key={m.id} style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '8px 10px', borderRadius: 6,
              background: 'var(--bg-secondary, rgba(255,255,255,0.03))',
              borderLeft: `3px solid ${accent}`,
            }}>
              <FileText size={14} color="var(--text-secondary)" />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 12, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={m.name}>
                  {m.name}
                </div>
                <div style={{ fontSize: 10, color: 'var(--text-secondary)' }}>
                  {formatSize(m.size)} · {m.indexed ? '已加入答疑上下文' : '暂不解析（PDF/PPT）'}
                </div>
              </div>
              <button
                type="button"
                aria-label={`移除 ${m.name}`}
                onClick={() => void removeMaterial(m.id)}
                style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-secondary)', padding: 4 }}
              >
                <X size={14} />
              </button>
            </div>
          ))}
        </div>
      )}

      <button
        type="button"
        onClick={() => void appendCandidates()}
        style={{
          marginTop: 10,
          display: 'inline-flex', alignItems: 'center', gap: 6,
          padding: '6px 12px', borderRadius: 6, fontSize: 12,
          color: accentText,
          background: 'transparent',
          border: `1px solid ${accent}`,
          cursor: 'pointer',
        }}
      >
        <ListChecks size={14} />
        从资料追加候选术语
      </button>
    </div>
  )
}
