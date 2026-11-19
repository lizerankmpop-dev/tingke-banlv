/** D10 导出记录：只有真的触发下载后才回调后端登记。 */
export type ExportKind = 'subtitles' | 'vocabulary'
export type ExportFormat = 'md' | 'srt' | 'csv' | 'json'

export interface ExportRecord {
  id: string
  courseId: string
  courseName: string
  kind: ExportKind
  format: ExportFormat
  fileName: string
  rowCount: number
  createdAt: number
}

export async function listExportRecords(): Promise<ExportRecord[]> {
  const response = await fetch('/api/exports')
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  const body = await response.json() as { records?: unknown }
  return Array.isArray(body.records) ? body.records as ExportRecord[] : []
}

/** 登记失败不能影响已下载的文件 —— 调用方一律 catch 忽略。 */
export async function recordExport(input: {
  courseId: string
  courseName: string
  kind: ExportKind
  format: ExportFormat
  fileName: string
  rowCount: number
}): Promise<void> {
  await fetch('/api/exports', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
}

export async function deleteExportRecord(id: string): Promise<void> {
  const response = await fetch(`/api/exports/${encodeURIComponent(id)}`, { method: 'DELETE' })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
}
