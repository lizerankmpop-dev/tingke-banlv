import { useCallback, useEffect, useState } from 'react'
import { ArrowLeft, Download, FileSpreadsheet, FileText, Trash2 } from 'lucide-react'
import { IconButton } from '../../components/ui/IconButton'
import { deleteExportRecord, listExportRecords, type ExportRecord } from '../../lib/exportRecords'

/** D10 导出记录页：空的时候照常进入并显示「没有记录」。 */
interface ExportsPageProps {
  onBack: () => void
  onGoCourses: () => void
  onToast: (message: string) => void
}

const FORMAT_LABEL: Record<string, string> = {
  md: 'Markdown', srt: 'SRT 字幕', csv: 'CSV', json: 'JSON',
}

export function ExportsPage({ onBack, onGoCourses, onToast }: ExportsPageProps) {
  const [records, setRecords] = useState<ExportRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setRecords(await listExportRecords())
    } catch {
      setError('读取导出记录失败，请检查本地服务后重试')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  async function remove(id: string) {
    try {
      await deleteExportRecord(id)
      setRecords(rows => rows.filter(row => row.id !== id))
      onToast('已删除该条记录（文件本身不受影响）')
    } catch {
      onToast('删除失败，请重试')
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <IconButton label="返回" onClick={onBack}><ArrowLeft size={18} /></IconButton>
          <div>
            <h1 className="page-title">导出记录</h1>
            <p className="page-sub">这里只记录下载动作，已保存的文件仍在你的下载目录</p>
          </div>
        </div>
      </div>

      {error ? <div className="summary-save-warning" role="alert">{error}</div> : null}

      {loading ? (
        <p className="page-sub">正在读取记录…</p>
      ) : records.length === 0 && !error ? (
        <div className="export-empty" role="status">
          <Download size={26} />
          <strong>没有记录</strong>
          <span>还没有导出过字幕或生词。进入课程点「导出字幕」，或在生词本点「导出 CSV」后，记录会出现在这里。</span>
          <button type="button" className="btn btn-primary btn-sm" onClick={onGoCourses}>去课程列表</button>
        </div>
      ) : (
        <div className="export-record-list">
          {records.map((record) => (
            <div className="export-record-row" key={record.id}>
              <span className="export-record-icon">
                {record.kind === 'vocabulary' ? <FileSpreadsheet size={17} /> : <FileText size={17} />}
              </span>
              <div className="export-record-copy">
                <strong>{record.fileName || `${record.kind}.${record.format}`}</strong>
                <span>
                  {record.courseName || '未命名课程'} · {FORMAT_LABEL[record.format] ?? record.format} · {record.rowCount} 条
                </span>
              </div>
              <time>{new Date(record.createdAt).toLocaleString()}</time>
              <button type="button" className="btn btn-ghost btn-sm" aria-label="删除记录"
                onClick={() => void remove(record.id)}><Trash2 size={14} /></button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
