import { useEffect, useState } from 'react'
import { useAppStore } from '../stores/useAppStore'
import { useCourseStore } from '../stores/useCourseStore'
import { Plus, Trash2, BookOpen, Hash, Clock, ChevronRight, Play, Monitor, School, FileText, Languages } from 'lucide-react'

const DOMAINS = ['计算机科学', '医学', '生物学', '物理学', '数学', '化学', '法律', '经济学', '哲学', '历史', '文学', '其他']
const LANGUAGES = [
  { value: 'auto', label: '自动检测' },
  { value: 'en', label: '英语' },
  { value: 'zh', label: '中文' },
  { value: 'ja', label: '日语' },
  { value: 'ko', label: '韩语' },
  { value: 'fr', label: '法语' },
  { value: 'de', label: '德语' },
  { value: 'es', label: '西班牙语' },
]

export default function CourseList() {
  const { courses, isLoading, loadCourses, createCourse, deleteCourse } = useCourseStore()
  const { enterCourse } = useAppStore()
  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState({
    name: '', domain: '计算机科学', description: '', customTerms: '', courseType: 'online' as 'online' | 'offline', language: 'auto'
  })
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; name: string } | null>(null)

  useEffect(() => { loadCourses() }, [])

  const handleCreate = async () => {
    if (!form.name.trim()) return
    await createCourse({
      name: form.name.trim(),
      domain: form.domain,
      description: form.description.trim(),
      customTerms: form.customTerms.split(/[,，\n]/).map(t => t.trim()).filter(Boolean),
      courseType: form.courseType,
      language: form.language,
    })
    setShowCreate(false)
    setForm({ name: '', domain: '计算机科学', description: '', customTerms: '', courseType: 'online', language: 'auto' })
  }

  const handleDelete = async (e: React.MouseEvent, id: string, name: string) => {
    e.stopPropagation()
    setDeleteTarget({ id, name })
  }

  const confirmDelete = async () => {
    if (deleteTarget) {
      await deleteCourse(deleteTarget.id)
      setDeleteTarget(null)
    }
  }

  return (
    <div style={styles.window}>
      {/* 标题栏 */}
      <div style={styles.titleBar}>
        <BookOpen size={20} color="var(--accent-light)" />
        <span style={styles.title}>听课伴侣</span>
        <span style={styles.subtitle}>我的课程</span>
      </div>

      {/* 课程列表 */}
      <div style={styles.listArea}>
        {isLoading && <p style={styles.empty}>加载中...</p>}

        {!isLoading && courses.length === 0 && (
          <div style={styles.emptyState}>
            <BookOpen size={56} color="var(--text-muted)" />
            <p style={styles.emptyTitle}>还没有课程</p>
            <p style={styles.emptyHint}>创建课程，选择线上网课或线下课堂模式</p>
            <button onClick={() => setShowCreate(true)} className="btn btn-primary" style={{ marginTop: 16 }}>
              <Plus size={16} /> 创建第一个课程
            </button>
          </div>
        )}

        {courses.map((course) => {
          const isOffline = course.courseType === 'offline'
          return (
            <div key={course.id} style={styles.card} onClick={() => enterCourse(course.id)}>
              <div style={styles.cardLeft}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                  <h3 style={styles.cardName}>{course.name}</h3>
                  <span style={{
                    ...styles.typeTag,
                    background: isOffline ? 'rgba(0,206,201,0.15)' : 'var(--accent-glow)',
                    color: isOffline ? '#00cec9' : 'var(--accent-light)',
                  }}>
                    {isOffline ? <School size={11} /> : <Monitor size={11} />}
                    {isOffline ? ' 线下课堂' : ' 在线网课'}
                  </span>
                </div>
                <div style={styles.cardMeta}>
                  <span style={styles.domainTag}>{course.domain}</span>
                  <span style={styles.meta}><Hash size={12} /> {course.videoCount ?? 0} 视频</span>
                  <span style={styles.meta}><FileText size={12} /> {course.wordCount ?? 0} 词汇</span>
                  <span style={styles.meta}><Clock size={12} /> {formatDate(course.updatedAt)}</span>
                </div>
                {course.description && <p style={styles.cardDesc}>{course.description}</p>}
              </div>
              <div style={styles.cardRight}>
                <Play size={20} color={isOffline ? '#00cec9' : 'var(--accent-light)'} style={{ marginRight: 8 }} />
                <button onClick={(e) => handleDelete(e, course.id, course.name)} style={styles.delBtn}>
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
          )
        })}
      </div>

      {/* 底部新建按钮 */}
      {courses.length > 0 && (
        <div style={styles.bottomBar}>
          <button onClick={() => setShowCreate(true)} className="btn btn-primary" style={{ width: '100%' }}>
            <Plus size={16} /> 新建课程
          </button>
        </div>
      )}

      {/* 创建课程弹窗 */}
      {showCreate && (
        <div style={styles.overlay} onClick={() => setShowCreate(false)}>
          <div style={styles.modal} onClick={(e) => e.stopPropagation()}>
            <h2 style={styles.modalTitle}>创建新课程</h2>

            {/* 课程类型选择 */}
            <label style={styles.label}>课程类型 *</label>
            <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
              <div
                onClick={() => setForm({ ...form, courseType: 'online' })}
                style={{
                  ...styles.modeCard,
                  borderColor: form.courseType === 'online' ? 'var(--accent)' : 'var(--border)',
                  background: form.courseType === 'online' ? 'var(--bg-card)' : undefined,
                }}
              >
                <Monitor size={28} color="var(--accent-light)" />
                <div style={styles.modeLabel}>在线网课</div>
                <div style={styles.modeHint}>捕获系统音频<br />适用于 Coursera / B站 / YouTube</div>
              </div>
              <div
                onClick={() => setForm({ ...form, courseType: 'offline' })}
                style={{
                  ...styles.modeCard,
                  borderColor: form.courseType === 'offline' ? '#00cec9' : 'var(--border)',
                  background: form.courseType === 'offline' ? 'rgba(0,206,201,0.06)' : undefined,
                }}
              >
                <School size={28} color="#00cec9" />
                <div style={styles.modeLabel}>线下课堂</div>
                <div style={styles.modeHint}>使用麦克风收音<br />适用于教室 / 讲座 / 会议室</div>
              </div>
            </div>

            <label style={styles.label}>课程名称 *</label>
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="例如：CS229 机器学习 / 高等数学 第三章" style={styles.input} autoFocus />
            <label style={styles.label}>所属领域</label>
            <select value={form.domain} onChange={(e) => setForm({ ...form, domain: e.target.value })} style={styles.input}>
              {DOMAINS.map(d => <option key={d} value={d}>{d}</option>)}
            </select>
            <label style={styles.label}><Languages size={13} style={{ verticalAlign: -2, marginRight: 4 }} />识别语言</label>
            <select value={form.language} onChange={(e) => setForm({ ...form, language: e.target.value })} style={styles.input}>
              {LANGUAGES.map(l => <option key={l.value} value={l.value}>{l.label}</option>)}
            </select>
            <label style={styles.label}>课程描述</label>
            <textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="描述课程内容，帮助 AI 更好理解上下文..." style={{ ...styles.input, minHeight: 56, resize: 'vertical' }} rows={2} />
            <label style={styles.label}>自定义术语（可选）<span style={styles.labelHint}>中英文均可，逗号分隔</span></label>
            <textarea value={form.customTerms} onChange={(e) => setForm({ ...form, customTerms: e.target.value })}
              placeholder="gradient descent, 反向传播, 特征向量..." style={{ ...styles.input, minHeight: 56, resize: 'vertical' }} rows={2} />
            <div style={styles.modalBtns}>
              <button onClick={() => setShowCreate(false)} className="btn btn-secondary">取消</button>
              <button onClick={handleCreate} className="btn btn-primary" disabled={!form.name.trim()}>
                <Plus size={16} /> 创建
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 删除确认对话框 */}
      {deleteTarget && (
        <div style={styles.overlay} onClick={() => setDeleteTarget(null)}>
          <div style={styles.deleteModal} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
              <Trash2 size={18} color="var(--danger)" />
              <h2 style={styles.modalTitleNoMargin}>删除课程</h2>
            </div>
            <p style={styles.deleteText}>
              确定要删除课程 <strong>{deleteTarget.name}</strong> 吗？<br />
              该课程的全部字幕记录、识别词汇和配置将被永久删除，此操作不可恢复。
            </p>
            <div style={styles.modalBtns}>
              <button onClick={() => setDeleteTarget(null)} className="btn btn-secondary">取消</button>
              <button onClick={confirmDelete} className="btn" style={{
                background: 'var(--danger)',
                color: 'white',
                border: 'none',
                borderRadius: 'var(--radius-sm)',
                padding: '8px 18px',
                fontSize: 13,
                fontWeight: 500,
                cursor: 'pointer',
              }}>
                <Trash2 size={14} style={{ verticalAlign: -2, marginRight: 4 }} /> 确认删除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function formatDate(ts: number) {
  const d = new Date(ts)
  return `${d.getMonth() + 1}/${d.getDate()}`
}

const styles: Record<string, React.CSSProperties> = {
  window: {
    display: 'flex', flexDirection: 'column',
    height: '100vh', maxWidth: 520, margin: '0 auto',
    background: 'var(--bg-primary)',
  },
  titleBar: {
    display: 'flex', alignItems: 'center', gap: 10,
    padding: '24px 24px 20px', flexShrink: 0,
    // @ts-ignore
    WebkitAppRegion: 'drag',
  },
  title: { fontSize: 22, fontWeight: 700, color: 'var(--accent-light)' },
  subtitle: { fontSize: 13, color: 'var(--text-muted)', marginLeft: 'auto' },
  listArea: { flex: 1, overflow: 'auto', padding: '0 20px' },
  empty: { textAlign: 'center', color: 'var(--text-muted)', marginTop: 60, fontSize: 14 },
  emptyState: { textAlign: 'center', marginTop: 80, display: 'flex', flexDirection: 'column', alignItems: 'center' },
  emptyTitle: { fontSize: 16, fontWeight: 600, marginTop: 16, color: 'var(--text-secondary)' },
  emptyHint: { fontSize: 13, color: 'var(--text-muted)', marginTop: 6 },
  card: {
    display: 'flex', alignItems: 'center',
    padding: '16px 18px', marginBottom: 8,
    background: 'var(--bg-card)', borderRadius: 'var(--radius)',
    cursor: 'pointer', border: '1px solid transparent',
    transition: 'all 0.15s',
  },
  cardLeft: { flex: 1, minWidth: 0 },
  cardName: { fontSize: 15, fontWeight: 600 },
  typeTag: {
    display: 'inline-flex', alignItems: 'center', gap: 3,
    padding: '1px 8px', borderRadius: 10, fontSize: 11, fontWeight: 500,
    whiteSpace: 'nowrap',
  },
  cardMeta: { display: 'flex', flexWrap: 'wrap', gap: 10, fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 },
  domainTag: { background: 'var(--accent-glow)', color: 'var(--accent-light)', padding: '1px 8px', borderRadius: 10, fontSize: 11, fontWeight: 500 },
  meta: { display: 'flex', alignItems: 'center', gap: 3 },
  cardDesc: { fontSize: 12, color: 'var(--text-secondary)', marginTop: 4, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  cardRight: { display: 'flex', alignItems: 'center', flexShrink: 0 },
  delBtn: { opacity: 0, padding: 4, background: 'none', color: 'var(--danger)', borderRadius: 4 },
  bottomBar: { padding: '16px 20px', borderTop: '1px solid var(--border)', flexShrink: 0 },
  overlay: { position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100 },
  modal: { background: 'var(--bg-secondary)', borderRadius: 'var(--radius)', padding: '28px 32px', width: 480, maxHeight: '90vh', overflow: 'auto', border: '1px solid var(--border)' },
  modalTitle: { fontSize: 18, fontWeight: 600, marginBottom: 20 },
  modalTitleNoMargin: { fontSize: 18, fontWeight: 600 },
  deleteModal: { background: 'var(--bg-secondary)', borderRadius: 'var(--radius)', padding: '24px 28px', width: 400, border: '1px solid var(--border)' },
  deleteText: { fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.7, margin: 0 },
  modeCard: {
    flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6,
    padding: 16, borderRadius: 'var(--radius)', border: '2px solid var(--border)',
    cursor: 'pointer', textAlign: 'center', transition: 'all 0.2s',
  },
  modeLabel: { fontSize: 14, fontWeight: 600 },
  modeHint: { fontSize: 11, color: 'var(--text-muted)', lineHeight: 1.4 },
  label: { display: 'block', fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)', marginBottom: 6, marginTop: 12 },
  labelHint: { fontSize: 11, color: 'var(--text-muted)', marginLeft: 8, fontWeight: 400 },
  input: { width: '100%', padding: '10px 14px', borderRadius: 'var(--radius-sm)', fontSize: 14 },
  modalBtns: { display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 24 },
}
