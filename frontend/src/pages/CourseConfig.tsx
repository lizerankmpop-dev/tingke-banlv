import { useEffect, useState } from 'react'
import { useAppStore } from '../stores/useAppStore'
import { useCourseStore } from '../stores/useCourseStore'
import { useSubtitleStore } from '../stores/useSubtitleStore'
import { ArrowLeft, Play, Settings, BookOpen, Mic, Monitor, School, AlertTriangle, Languages } from 'lucide-react'

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

type LoopbackStatus = 'checking' | 'ok' | 'missing'

interface LoopbackGuide {
  name: string
  url: string
  steps: string[]
}

export default function CourseConfig() {
  const { courseId, setView, startListening } = useAppStore()
  const { courses, loadCourses } = useCourseStore()
  const course = courses.find(c => c.id === courseId)
  const isOffline = course?.courseType === 'offline'

  const [domain, setDomain] = useState(course?.domain || '计算机科学')
  const [description, setDescription] = useState(course?.description || '')
  const [language, setLanguage] = useState(course?.language || 'auto')
  const [customTerms, setCustomTerms] = useState('')
  const [loopbackStatus, setLoopbackStatus] = useState<LoopbackStatus>('checking')
  const [loopbackGuide, setLoopbackGuide] = useState<LoopbackGuide | null>(null)
  const [showGuide, setShowGuide] = useState(false)

  useEffect(() => {
    if (courses.length === 0) loadCourses()
  }, [])

  useEffect(() => {
    if (course) {
      setDomain(course.domain)
      setDescription(course.description)
      setLanguage(course.language || 'auto')
      setCustomTerms((course.customTerms || []).join(', '))
    }
  }, [course])

  // 在线网课模式检测 Loopback 设备
  useEffect(() => {
    if (!isOffline) {
      setLoopbackStatus('checking')
      fetch('/api/audio/check-loopback')
        .then(r => r.json())
        .then(data => {
          setLoopbackGuide(data.guide)
          setLoopbackStatus(data.hasLoopback ? 'ok' : 'missing')
        })
        .catch(() => setLoopbackStatus('missing'))
    } else {
      setLoopbackStatus('ok')
    }
  }, [isOffline])

  const canStart = isOffline || loopbackStatus === 'ok'

  const handleStart = () => {
    if (!canStart) return
    if (courseId) {
      fetch(`/api/courses/${courseId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ domain, description, language, customTerms: customTerms.split(/[,，]/).map(t => t.trim()).filter(Boolean) }),
      }).catch(() => {})
    }
    startListening()
  }

  return (
    <div style={styles.window}>
      {/* 标题栏 */}
      <div style={styles.titleBar}>
        <button onClick={() => setView('course-list')} style={styles.backBtn}>
          <ArrowLeft size={18} />
        </button>
        <span style={styles.title}>{course?.name || '课程配置'}</span>
        <span style={{
          ...styles.typeTag,
          background: isOffline ? 'rgba(0,206,201,0.15)' : 'var(--accent-glow)',
          color: isOffline ? '#00cec9' : 'var(--accent-light)',
        }}>
          {isOffline ? <School size={11} /> : <Monitor size={11} />}
          {isOffline ? ' 线下课堂' : ' 在线网课'}
        </span>
      </div>

      <div style={styles.content}>
        {/* 顶部提示 */}
        <div style={{
          ...styles.hint,
          background: isOffline ? 'rgba(0,206,201,0.06)' : 'var(--bg-card)',
          border: isOffline ? '1px solid rgba(0,206,201,0.15)' : '1px solid transparent',
        }}>
          {isOffline ? (
            <School size={40} color="#00cec9" />
          ) : (
            <Mic size={40} color="var(--accent-light)" />
          )}
          <p style={styles.hintTitle}>准备开始识别</p>
          <p style={styles.hintText}>
            点击「开始识别」后，本窗口将缩小为悬浮字幕条。<br />
            {isOffline ? (
              <>
                请确保麦克风已连接，靠近讲台效果更佳。<br />
                <span style={{ color: '#00cec9', fontSize: 12 }}>💡 教室环境建议使用降噪麦克风</span>
              </>
            ) : (
              '请确保您的网课视频正在播放，系统将自动捕获音频。'
            )}
          </p>
        </div>

        {/* 配置项 */}
        <div style={styles.configSection}>
          <label style={styles.label}>
            <Settings size={14} /> 课程领域
          </label>
          <select value={domain} onChange={e => setDomain(e.target.value)} style={styles.input}>
            {DOMAINS.map(d => <option key={d} value={d}>{d}</option>)}
          </select>

          <label style={styles.label}>
            <Languages size={14} /> 识别语言
          </label>
          <select value={language} onChange={e => setLanguage(e.target.value)} style={styles.input}>
            {LANGUAGES.map(l => <option key={l.value} value={l.value}>{l.label}</option>)}
          </select>
          <p style={styles.labelHint}>自动检测适合英文课程；多语混读或识别不准时请手动指定</p>

          <label style={styles.label}>
            <Settings size={14} /> 自定义术语（支持中英文，优先识别）
          </label>
          <textarea
            value={customTerms}
            onChange={e => setCustomTerms(e.target.value)}
            placeholder="输入本课程的专业术语，用逗号分隔，如：gradient descent, 反向传播, 特征向量"
            style={{ ...styles.input, minHeight: 72, resize: 'vertical' }}
            rows={3}
          />
        </div>

        {/* Loopback 设备检测警告 */}
        {!isOffline && loopbackStatus === 'missing' && (
          <div style={styles.warningBar}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <AlertTriangle size={16} color="var(--danger)" />
              <span style={{ fontSize: 13, color: 'var(--danger)', fontWeight: 500 }}>
                未检测到音频 Loopback 设备
              </span>
            </div>
            <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 6, lineHeight: 1.5 }}>
              在线网课模式需要虚拟音频设备来捕获系统声音。请安装 {loopbackGuide?.name || '虚拟音频设备'} 后重启软件。
            </p>
            <button
              onClick={() => setShowGuide(!showGuide)}
              style={styles.guideLink}
            >
              {showGuide ? '收起安装指引' : '查看安装指引'}
            </button>
            {showGuide && loopbackGuide && (
              <div style={styles.guideSteps}>
                {loopbackGuide.url && (
                  <a href={loopbackGuide.url} target="_blank" rel="noreferrer" style={styles.guideUrl}>
                    下载地址：{loopbackGuide.url}
                  </a>
                )}
                <ol style={{ margin: '8px 0 0', paddingLeft: 20 }}>
                  {loopbackGuide.steps.map((step, i) => (
                    <li key={i} style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>
                      {step}
                    </li>
                  ))}
                </ol>
              </div>
            )}
          </div>
        )}

        {/* 启动按钮 */}
        <button
          onClick={handleStart}
          disabled={!canStart}
          style={{
            ...styles.startBtn,
            opacity: canStart ? 1 : 0.5,
            cursor: canStart ? 'pointer' : 'not-allowed',
          }}
        >
          <Play size={20} /> 开始识别
        </button>

        <p style={styles.footerHint}>
          启动后窗口将缩小为悬浮字幕条，您可以随时拖拽调整位置
        </p>
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
    display: 'flex', alignItems: 'center', gap: 12,
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
  typeTag: {
    display: 'inline-flex', alignItems: 'center', gap: 3,
    padding: '2px 8px', borderRadius: 10, fontSize: 11, fontWeight: 500,
    whiteSpace: 'nowrap',
  },
  content: {
    flex: 1, overflow: 'auto', padding: '0 24px 24px',
    display: 'flex', flexDirection: 'column',
  },
  hint: {
    textAlign: 'center', padding: '24px 16px',
    borderRadius: 'var(--radius)',
    marginBottom: 20,
  },
  hintTitle: { fontSize: 16, fontWeight: 600, marginTop: 10, marginBottom: 6 },
  hintText: { fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.7 },
  configSection: { flex: 1 },
  label: {
    display: 'flex', alignItems: 'center', gap: 6,
    fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)',
    marginBottom: 8, marginTop: 16,
  },
  input: { width: '100%', padding: '10px 14px', borderRadius: 'var(--radius-sm)', fontSize: 14 },
  labelHint: { fontSize: 11, color: 'var(--text-muted)', marginTop: 4, marginBottom: 0 },
  startBtn: {
    display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
    width: '100%', padding: '14px',
    background: 'var(--accent)', color: 'white',
    borderRadius: 'var(--radius)', fontSize: 16, fontWeight: 600,
    marginTop: 20,
    border: 'none',
  },
  warningBar: {
    background: 'rgba(225,112,85,0.08)',
    border: '1px solid rgba(225,112,85,0.3)',
    borderRadius: 'var(--radius-sm)',
    padding: 12,
    marginTop: 16,
  },
  guideLink: {
    background: 'none', border: 'none', cursor: 'pointer',
    fontSize: 12, color: 'var(--accent-light)',
    padding: '4px 0', marginTop: 4,
  },
  guideSteps: {
    marginTop: 8, padding: '8px 12px',
    background: 'var(--bg-card)',
    borderRadius: 'var(--radius-sm)',
  },
  guideUrl: {
    fontSize: 12, color: 'var(--accent-light)',
    wordBreak: 'break-all',
  },
  footerHint: {
    textAlign: 'center', fontSize: 12, color: 'var(--text-muted)',
    marginTop: 12,
  },
}
