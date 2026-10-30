import { useEffect, useState } from 'react'
import { useAppStore } from '../stores/useAppStore'
import { useCourseStore } from '../stores/useCourseStore'
import { useSubtitleStore } from '../stores/useSubtitleStore'
import { ArrowLeft, Play, Settings, BookOpen, Mic, Monitor, School, AlertTriangle, Languages, Sparkles, ChevronDown, ChevronUp } from 'lucide-react'
import CourseConfigHint from '../components/CourseConfigHint'
import { COURSE_CONFIG_DEFAULTS } from '../lib/courseConfigCopy'

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

// V5-4 简繁输出开关。取值必须与后端 asr/whisper_engine.py 的
// SIMP_MODE_AUTO_SIMPLIFIED / SIMP_MODE_RAW 一致 —— 不一致会被后端
// normalize_simp_mode() 静默收敛成默认值，表现为「开关点了没用」。
const SIMP_MODES = [
  { value: 'auto-simplified', label: '简体优先' },
  { value: 'raw', label: '保留原始字形' },
]

// 🔴 `'unreachable'` = **后端服务没运行**（连接被拒），它与 `'missing'`
//    （后端在跑，但确实没有 Loopback 设备）是**两件完全不同的事**：
//    前者要「启动服务」，后者要「装虚拟声卡」。混为一谈会把人引向错误的修复方向。
type LoopbackStatus = 'checking' | 'ok' | 'missing' | 'unreachable'

interface LoopbackGuide {
  name: string
  url: string
  steps: string[]
}

interface TermEntry {
  term: string
  source: 'custom' | 'course' | 'domain'
  inVocab: boolean
  translation: string
  definition: string
}

interface TermStats {
  total: number
  inVocab: number
  custom: number
  domain: number
  coverage: number
}

/**
 * 术语库预览
 *
 * 让用户在启动监听前就看到"这节课会重点识别哪些词"，
 * 并实时反映自定义术语的增删 —— 把识别优化从"看不见的黑盒"变成可见的反馈。
 *
 * `language` 必须一起送后端：中文课的「词表短语层」全是英文多词短语、与课程无关，
 * 后端要靠语种才能关掉它。漏传时中文课会显示 41 条英文短语（缺陷 b 的现场）。
 */
function TerminologyPreview({ domain, language, customTerms }: { domain: string; language: string; customTerms: string[] }) {
  const [terms, setTerms] = useState<TermEntry[]>([])
  const [stats, setStats] = useState<TermStats | null>(null)
  const [promptChars, setPromptChars] = useState(0)
  const [expanded, setExpanded] = useState(false)
  const [loading, setLoading] = useState(false)

  // 自定义术语变化后防抖 400ms 再请求，避免逐字符打字时频繁调用
  useEffect(() => {
    const timer = setTimeout(() => {
      setLoading(true)
      fetch('/api/terminology/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ domain, language, customTerms }),
      })
        .then(r => r.json())
        .then(data => {
          setTerms(data.terms || [])
          setStats(data.stats || null)
          setPromptChars(data.promptChars || 0)
        })
        .catch(() => {})
        .finally(() => setLoading(false))
    }, 400)
    return () => clearTimeout(timer)
  }, [domain, language, customTerms.join(',')])

  if (!stats || stats.total === 0) return null

  const customCount = stats.custom
  const display = expanded ? terms : terms.slice(0, 8)

  return (
    <div style={termStyles.panel}>
      <div style={termStyles.header}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <Sparkles size={13} color="#00cec9" />
          <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>
            识别优化已启用
          </span>
          {
            loading &&
            <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>更新中…</span>
          }
        </div>
        <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
          {stats.total} 个术语 · 词库覆盖 {Math.round(stats.coverage * 100)}%
        </span>
      </div>

      <p style={termStyles.hint}>
        {customCount > 0 && `${customCount} 个自定义术语优先识别；`}
        系统已载入该领域术语表，将从字幕中自动匹配并加入生词本
      </p>

      <div style={termStyles.chips}>
        {display.map(t => (
          <span
            key={t.term}
            title={t.translation || t.definition || '点击字幕时由 AI 讲解'}
            style={{
              ...termStyles.chip,
              ...(t.source === 'custom' ? termStyles.chipCustom : {}),
            }}
          >
            {t.term}
            {t.inVocab && t.translation
              ? <span style={termStyles.chipTrans}>{t.translation}</span>
              : <span style={termStyles.chipNoTrans}>AI</span>}
          </span>
        ))}
      </div>

      {terms.length > 8 && (
        <button onClick={() => setExpanded(!expanded)} style={termStyles.toggle}>
          {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          {expanded ? '收起' : `展开全部 ${terms.length} 个`}
        </button>
      )}

      <p style={termStyles.footer}>
        术语先验 {promptChars} 字符 · 用于提升 Whisper 对专业词的识别准确率
      </p>
    </div>
  )
}

const termStyles: Record<string, React.CSSProperties> = {
  panel: {
    marginTop: 14,
    padding: '12px 14px',
    borderRadius: 8,
    background: 'rgba(0, 206, 201, 0.06)',
    border: '1px solid rgba(0, 206, 201, 0.22)',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 6,
  },
  hint: {
    fontSize: 11,
    color: 'var(--text-secondary)',
    lineHeight: 1.5,
    margin: '0 0 8px',
  },
  chips: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 6,
  },
  chip: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 4,
    padding: '3px 8px',
    borderRadius: 4,
    fontSize: 11,
    background: 'var(--bg-tertiary)',
    color: 'var(--text-primary)',
    border: '1px solid var(--border)',
  },
  chipCustom: {
    borderColor: 'rgba(0, 206, 201, 0.55)',
    background: 'rgba(0, 206, 201, 0.12)',
  },
  chipTrans: { color: 'var(--text-secondary)', fontSize: 10 },
  chipNoTrans: { color: '#00cec9', fontSize: 9, fontWeight: 700 },
  toggle: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 4,
    marginTop: 8,
    padding: 0,
    background: 'none',
    border: 'none',
    color: '#00cec9',
    fontSize: 11,
    cursor: 'pointer',
  },
  footer: {
    fontSize: 10,
    color: 'var(--text-secondary)',
    margin: '8px 0 0',
    opacity: 0.75,
  },
}

export default function CourseConfig() {
  const { courseId, setView, startListening } = useAppStore()
  const { courses, loadCourses } = useCourseStore()
  const course = courses.find(c => c.id === courseId)
  const isOffline = course?.courseType === 'offline'

  // 三个默认值统一取自 `lib/courseConfigCopy`，与提示条里写的「推荐值」同源。
  // 直接写字面量的后果：改了这里、忘了改提示条，提示条就会教用户一个不存在的
  // 默认值（有源守卫逐字对拍，见 tests/course-config-hint.test.mts）。
  const [domain, setDomain] = useState(course?.domain || COURSE_CONFIG_DEFAULTS.domain)
  const [description, setDescription] = useState(course?.description || '')
  const [language, setLanguage] = useState(course?.language || COURSE_CONFIG_DEFAULTS.language)
  const [simpMode, setSimpMode] = useState(course?.simpMode || COURSE_CONFIG_DEFAULTS.simpMode)
  const [customTerms, setCustomTerms] = useState('')
  const [loopbackStatus, setLoopbackStatus] = useState<LoopbackStatus>('checking')
  // 🔴 **次要实例提示**（2026-10-30）—— 同一台机器跑两个后端实例时，
  //    后启动的那个**识别不出字幕**（设备被抢），但用户完全不知道原因
  const [instanceHint, setInstanceHint] = useState('')
  const [loopbackGuide, setLoopbackGuide] = useState<LoopbackGuide | null>(null)
  const [showGuide, setShowGuide] = useState(false)

  useEffect(() => {
    if (courses.length === 0) loadCourses()
  }, [])

  useEffect(() => {
    if (course) {
      setDomain(course.domain)
      setDescription(course.description)
      // 这里的兜底值也必须走同一份常量：课程记录里字段可能缺失（老库 / 导入的课程），
      // 这时页面显示的值同样是「默认值」，写死字面量就和提示条里说的推荐值成了两份事实。
      setLanguage(course.language || COURSE_CONFIG_DEFAULTS.language)
      setSimpMode(course.simpMode || COURSE_CONFIG_DEFAULTS.simpMode)
      setCustomTerms((course.customTerms || []).join(', '))
    }
  }, [course])

  // 🔴 检测本机是否已有另一个后端实例（设备独占 ⇒ 后启动的那个会没字幕）
  useEffect(() => {
    fetch('/api/instance/status')
      .then(r => (r.ok ? r.json() : null))
      .then(d => { if (d && d.anotherInstanceRunning) setInstanceHint(d.hint || '') })
      .catch(() => { /* 后端不可达时不必提示这个 */ })
  }, [])

  // 在线网课模式检测 Loopback 设备
  useEffect(() => {
    if (!isOffline) {
      setLoopbackStatus('checking')
      fetch('/api/audio/check-loopback')
        .then(r => {
          // 🔴 **先看 HTTP 状态，再看 body**（2026-10-29 修）
          //
          // 原来只 catch 网络异常 ⇒ **后端整个进程没起来时**（连接被拒）
          // 也走 catch ⇒ 状态被设成 'missing' ⇒ 页面显示
          // 「**未检测到音频 Loopback 设备**」⇒ **把人引去装虚拟声卡**，
          // 而真实原因是「后端服务没运行」。两者要修的方向完全不同。
          if (!r.ok) throw new Error(`HTTP ${r.status}`)
          return r.json()
        })
        .then(data => {
          setLoopbackGuide(data.guide)
          setLoopbackStatus(data.hasLoopback ? 'ok' : 'missing')
        })
        .catch((err) => {
          // 「后端不可达」与「设备不存在」是**两件事**，必须分开说
          const unreachable = (err?.message || '').startsWith('HTTP')
            || err instanceof TypeError
          setLoopbackStatus(unreachable ? 'unreachable' : 'missing')
        })
    } else {
      setLoopbackStatus('ok')
    }
  }, [isOffline])

  // ⚠️ `'unreachable'`（后端没起）时**不禁用**「开始识别」——
  //    否则用户连试都试不了，只能干看着。真正该做的是让用户去启动服务，
  //    而不是把按钮锁死（点了之后后端会给出真实原因）。
  const canStart = isOffline || loopbackStatus === 'ok' || loopbackStatus === 'unreachable'

  const handleStart = () => {
    if (!canStart) return
    if (courseId) {
      fetch(`/api/courses/${courseId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ domain, description, language, simpMode, customTerms: customTerms.split(/[,，]/).map(t => t.trim()).filter(Boolean) }),
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
        {/* 一次性填写提示（V6-1）：只在首次进入时出现，关掉后写 localStorage 标记 */}
        <CourseConfigHint />

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

          {/* V5-4：只在「自动检测」下露出 —— 指定了语种时这个开关不起作用，
              一直显示一个点了没反应的控件比不显示更容易误导用户。 */}
          {language === 'auto' && (
            <>
              <label style={styles.label}>
                <Languages size={14} /> 中文输出字形
              </label>
              <select value={simpMode} onChange={e => setSimpMode(e.target.value)} style={styles.input}>
                {SIMP_MODES.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
              </select>
              <p style={styles.labelHint}>
                自动检测判定为中文时，优先输出简体（推荐）。课题涉及古文、繁体教材时可改为「保留原始字形」。
              </p>
            </>
          )}

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

          <TerminologyPreview
            domain={domain}
            language={language}
            customTerms={customTerms.split(/[,，]/).map(t => t.trim()).filter(Boolean)}
          />
        </div>

        {/* 🔴 后端服务不可达（**不是**设备问题）—— 2026-10-29 加 */}
        {!isOffline && loopbackStatus === 'unreachable' && (
          <div style={styles.warningBar}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <AlertTriangle size={16} color="var(--danger)" />
              <span style={{ fontSize: 13, color: 'var(--danger)', fontWeight: 500 }}>
                后端服务未运行
              </span>
            </div>
            <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 6, lineHeight: 1.5 }}>
              连不上识别服务，**这不是音频设备的问题**。请先启动后端再试
              （桌面版由应用自动启动；网页版请运行 <code>启动网页测试.bat</code>）。
            </p>
            <button
              onClick={() => {
                setLoopbackStatus('checking')
                window.location.reload()
              }}
              style={styles.guideLink}
            >
              重新检测
            </button>
          </div>
        )}

        {/* 🔴 次要实例提示：两个实例抢音频设备 ⇒ 本实例可能识别不出字幕 */}
        {instanceHint && (
          <div style={styles.warningBar}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <AlertTriangle size={16} color="var(--danger)" />
              <span style={{ fontSize: 13, color: 'var(--danger)', fontWeight: 500 }}>
                检测到另一个实例在运行
              </span>
            </div>
            <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 6, lineHeight: 1.5 }}>
              {instanceHint}
            </p>
          </div>
        )}

        {/* 🔴 次要实例提示：两个实例抢音频设备 ⇒ 本实例可能识别不出字幕 */}
        {instanceHint && (
          <div style={styles.warningBar}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <AlertTriangle size={16} color="var(--danger)" />
              <span style={{ fontSize: 13, color: 'var(--danger)', fontWeight: 500 }}>
                检测到另一个实例在运行
              </span>
            </div>
            <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 6, lineHeight: 1.5 }}>
              {instanceHint}
            </p>
          </div>
        )}

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
