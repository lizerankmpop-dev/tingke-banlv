import { useEffect, useState } from 'react'
import { useAppStore } from '../stores/useAppStore'
import { useCourseStore } from '../stores/useCourseStore'
import { useSubtitleStore } from '../stores/useSubtitleStore'
import { ArrowLeft, Play, Settings, BookOpen, Mic, Monitor, School, AlertTriangle, Languages, Sparkles, ChevronDown, ChevronUp, ListVideo, Plus, Trash2 } from 'lucide-react'
import CourseConfigHint from '../components/CourseConfigHint'
import MaterialsPanel from '../components/MaterialsPanel'
import { appendTerms } from '../lib/materials'
import { generateTerms, mergeTerms, TERM_GENERATE_TIMEOUT_MS } from '../lib/termApi'
import ClassroomMicrophone from '../components/ClassroomMicrophone'
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

// 字幕显示语言（feat/lang-chapters）。取值与后端 routes.py 的
// normalize_subtitle_lang() 一致：both / orig / zh，非法值后端回落 both。
const SUBTITLE_LANGS = [
  { value: 'both', label: '双语对照（原文 + 中文译文）' },
  { value: 'orig', label: '仅原文' },
  { value: 'zh', label: '仅中文译文' },
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
          <Sparkles size={13} color="var(--accent-offline)" />
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
    background: 'var(--accent-offline-wash)',
    border: '1px solid var(--accent-offline-border)',
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
    borderColor: 'var(--accent-offline-border-strong)',
    background: 'var(--accent-offline-soft)',
  },
  chipTrans: { color: 'var(--text-secondary)', fontSize: 10 },
  chipNoTrans: { color: 'var(--accent-offline)', fontSize: 9, fontWeight: 700 },
  toggle: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 4,
    marginTop: 8,
    padding: 0,
    background: 'none',
    border: 'none',
    color: 'var(--accent-offline)',
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

/**
 * 章节管理面板（多集长课程）
 *
 * 「课程 → 章节 → 字幕」两级结构：每次「开始识别」选定一个章节，
 * 该次会话产出的字幕全部挂到它名下 —— 面向的是录播系列课 / 分集实验课，
 * 不分章节的老用法完全不受影响（默认「不分章节」）。
 *
 * 删除章节不会删字幕：字幕保留、仅解除归属（后端 delete_chapter 语义）。
 */
function ChapterPanel({
  courseId, chapters, selectedId, onSelect, reload,
  offline,
}: {
  courseId: string
  chapters: { id: string; name: string; sortOrder: number }[]
  selectedId: string
  onSelect: (id: string) => void
  reload: () => void
  offline: boolean
}) {
  const [newName, setNewName] = useState('')
  const [busy, setBusy] = useState(false)
  const accent = offline ? 'var(--accent-offline)' : 'var(--accent-light)'

  const addChapter = async () => {
    const name = newName.trim()
    if (!name || busy) return
    setBusy(true)
    try {
      const res = await fetch(`/api/courses/${courseId}/chapters`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      })
      if (res.ok) {
        setNewName('')
        reload()
      }
    } catch { /* 列表不变，用户重试即可 */ }
    setBusy(false)
  }

  const removeChapter = async (id: string, name: string) => {
    if (!window.confirm(`删除章节「${name}」？\n该章节下的字幕会保留（转为「未分章节」），不会被删除。`)) return
    try {
      const res = await fetch(`/api/courses/${courseId}/chapters/${id}`, { method: 'DELETE' })
      if (res.ok) {
        // 删的是当前选中章节 → 归位到「不分章节」
        if (selectedId === id) onSelect('')
        reload()
      }
    } catch { /* 同上 */ }
  }

  return (
    <div style={{
      marginTop: 16,
      padding: '12px 14px',
      borderRadius: 8,
      background: 'var(--bg-card)',
      border: `1px solid ${offline ? 'var(--accent-offline-border)' : 'var(--border)'}`,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
        <ListVideo size={13} color={accent} />
        <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>
          章节（多集长课程）
        </span>
        <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
          {chapters.length > 0 ? `${chapters.length} 个章节` : '未创建章节'}
        </span>
      </div>

      {/* 本次识别归属 —— 开始识别前选好，字幕会挂到该章节名下 */}
      <label style={{ fontSize: 11, color: 'var(--text-secondary)', display: 'block', marginBottom: 4 }}>
        本次识别归属
      </label>
      <select
        value={selectedId}
        onChange={(e) => onSelect(e.target.value)}
        style={{ ...styles.input, marginBottom: 10 }}
      >
        <option value="">不分章节</option>
        {chapters.map((c) => (
          <option key={c.id} value={c.id}>第 {c.sortOrder} 章 · {c.name}</option>
        ))}
      </select>

      {/* 章节列表 + 删除 */}
      {chapters.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: 10 }}>
          {chapters.map((c) => (
            <div key={c.id} style={{
              display: 'flex', alignItems: 'center', gap: 6,
              padding: '5px 8px', borderRadius: 6,
              background: selectedId === c.id ? 'var(--bg-tertiary)' : 'transparent',
              border: selectedId === c.id ? `1px solid ${accent}` : '1px solid transparent',
            }}>
              <span style={{ fontSize: 11, color: 'var(--text-muted)', minWidth: 20 }}>
                {c.sortOrder}.
              </span>
              <span style={{ flex: 1, fontSize: 12, color: 'var(--text-primary)' }}>{c.name}</span>
              <button
                onClick={() => removeChapter(c.id, c.name)}
                style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', padding: 2 }}
                title="删除章节（字幕保留）"
              >
                <Trash2 size={12} />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* 新增章节 */}
      <div style={{ display: 'flex', gap: 6 }}>
        <input
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') addChapter() }}
          placeholder="新章节名，如：第 3 讲 卷积神经网络"
          maxLength={60}
          style={{
            flex: 1, padding: '7px 10px', borderRadius: 6,
            border: '1px solid var(--border)', background: 'var(--bg-primary)',
            color: 'var(--text-primary)', fontSize: 12, minWidth: 0,
          }}
        />
        <button
          onClick={addChapter}
          disabled={!newName.trim() || busy}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 4,
            padding: '0 10px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
            border: 'none', background: accent, color: 'var(--on-accent)',
            opacity: !newName.trim() || busy ? 0.5 : 1,
          }}
        >
          <Plus size={13} /> 添加
        </button>
      </div>
    </div>
  )
}

export default function CourseConfig({ embedded = false }: { embedded?: boolean }) {
  const { courseId, setView, startListening } = useAppStore()
  const { courses, loadCourses, patchCourseLocal } = useCourseStore()
  const course = courses.find(c => c.id === courseId)
  const isOffline = course?.courseType === 'offline'
  // 课程名沿用页面现有的 course 数据（页面没有单独的 courseName state）
  const courseName = (course?.name || '').trim()

  // 三个默认值统一取自 `lib/courseConfigCopy`，与提示条里写的「推荐值」同源。
  // 直接写字面量的后果：改了这里、忘了改提示条，提示条就会教用户一个不存在的
  // 默认值（有源守卫逐字对拍，见 tests/course-config-hint.test.mts）。
  const [domain, setDomain] = useState(course?.domain || COURSE_CONFIG_DEFAULTS.domain)
  const [description, setDescription] = useState(course?.description || '')
  const [language, setLanguage] = useState(course?.language || COURSE_CONFIG_DEFAULTS.language)
  const [simpMode, setSimpMode] = useState(course?.simpMode || COURSE_CONFIG_DEFAULTS.simpMode)
  // 字幕显示语言（both/orig/zh）。老课程没有该字段 → 回落 both（历史行为）
  const [subtitleLang, setSubtitleLang] = useState(course?.subtitleLang || 'both')
  const [customTerms, setCustomTerms] = useState('')
  const [generatingTerms, setGeneratingTerms] = useState(false)
  const [termGenNotice, setTermGenNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
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
      setSubtitleLang(course.subtitleLang || 'both')
      setCustomTerms((course.customTerms || []).join(', '))
    }
  }, [course])

  // ============ 章节（多集长课程）============
  const [chapters, setChapters] = useState<{ id: string; name: string; sortOrder: number }[]>([])
  const [selectedChapterId, setSelectedChapterId] = useState('')
  const reloadChapters = () => {
    if (!courseId) return
    fetch(`/api/courses/${courseId}/chapters`)
      .then(r => (r.ok ? r.json() : []))
      .then(list => { setChapters(Array.isArray(list) ? list : []) })
      .catch(() => setChapters([]))
  }
  useEffect(() => { reloadChapters() }, [courseId])

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
  const canGenerateTerms = courseName.length > 0 && !generatingTerms

  /**
   * AI 生成专业术语。
   *
   * 只把结果合并进输入框，不调用 PUT —— 是否保存仍由「开始识别」决定。
   * 8.5 秒超时比后端 8 秒总预算多 0.5 秒，避免网络往返把成功误判成超时。
   */
  const handleGenerateTerms = async () => {
    if (!canGenerateTerms) return
    setGeneratingTerms(true)
    setTermGenNotice(null)
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), TERM_GENERATE_TIMEOUT_MS)
    try {
      const result = await generateTerms(
        { courseName, domain, language },
        controller.signal,
      )
      if (result.terms.length === 0) {
        setTermGenNotice({
          tone: 'error',
          text: 'AI 没有返回可用术语，已保留原有内容，可稍后重试或手动填写。',
        })
        return
      }
      // 只合并、不覆盖；原术语框内容原样保留
      setCustomTerms(prev => mergeTerms(prev, result.terms))
      setTermGenNotice({
        tone: 'ok',
        text: `已追加 ${result.terms.length} 个候选术语，可手动删改；点「开始识别」后才会保存。`,
      })
    } catch (err) {
      const timedOut = controller.signal.aborted
      const detail = err instanceof Error ? err.message : ''
      setTermGenNotice({
        tone: 'error',
        text: timedOut
          ? 'AI 生成超时，已保留原有术语，可稍后重试或手动填写；不影响开始识别。'
          : `AI 生成失败：${detail || '服务暂不可用'}。已保留原有术语，可手动填写；不影响开始识别。`,
      })
    } finally {
      clearTimeout(timer)
      setGeneratingTerms(false)
    }
  }

  const handleStart = () => {
    if (!canStart) return
    if (courseId) {
      // 🔴 乐观更新本地 store：PUT 是 fire-and-forget，但识别页**立刻**就要
      //    读 course.subtitleLang 决定字幕渲染模式 —— 不先改本地，
      //    「仅中文」点开始后头几屏还是原文（PUT 往返完成前）。
      patchCourseLocal(courseId, { subtitleLang })
      fetch(`/api/courses/${courseId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          domain, description, language, simpMode, subtitleLang,
          customTerms: customTerms.split(/[,，]/).map(t => t.trim()).filter(Boolean),
        }),
      }).catch(() => {})
    }
    // 章节归属随本次识别走：选了章节，这批字幕就挂它名下
    const chapter = chapters.find(c => c.id === selectedChapterId)
    startListening(chapter ? { id: chapter.id, name: chapter.name } : undefined)
  }

  return (
    <div style={embedded ? { ...styles.window, height: '100%' } : styles.window}>
      {/* 标题栏 */}
      <div style={styles.titleBar}>
        <button onClick={() => setView('course-list')} style={styles.backBtn}>
          <ArrowLeft size={18} />
        </button>
        <span style={styles.title}>{course?.name || '课程配置'}</span>
        <span style={{
          ...styles.typeTag,
          background: isOffline ? 'var(--accent-offline-glow)' : 'var(--accent-glow)',
          color: isOffline ? 'var(--accent-offline)' : 'var(--accent-light)',
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
          background: isOffline ? 'var(--accent-offline-wash)' : 'var(--bg-card)',
          border: isOffline ? '1px solid var(--accent-offline-glow)' : '1px solid transparent',
        }}>
          {isOffline ? (
            <School size={40} color="var(--accent-offline)" />
          ) : (
            <Mic size={40} color="var(--accent-light)" />
          )}
          <p style={styles.hintTitle}>准备开始识别</p>
          <p style={styles.hintText}>
            {embedded ? '可在右侧打开课程网页，点击「开始识别」后在同一窗口查看字幕。' : '点击「开始识别」后，本窗口将缩小为悬浮字幕条。'}<br />
            {isOffline ? (
              <>
                请确保麦克风已连接，靠近讲台效果更佳。<br />
                <span style={{ color: 'var(--accent-offline)', fontSize: 12 }}>💡 教室环境建议使用降噪麦克风</span>
              </>
            ) : (
              '请确保您的网课视频正在播放，系统将自动捕获音频。'
            )}
          </p>
        </div>

        {isOffline && courseId && <ClassroomMicrophone key={courseId} courseId={courseId} />}

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

          {/* 字幕显示语言：译文由本地 AI（Ollama）异步生成，不可用时自动回退原文 */}
          <label style={styles.label}>
            <Languages size={14} /> 字幕显示
          </label>
          <select value={subtitleLang} onChange={e => setSubtitleLang(e.target.value)} style={styles.input}>
            {SUBTITLE_LANGS.map(l => <option key={l.value} value={l.value}>{l.label}</option>)}
          </select>
          <p style={styles.labelHint}>
            译文由本地 AI 异步生成（先出原文、稍后补译文）；AI 未启动时自动只显示原文，不影响识别
          </p>

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

          <div style={styles.termLabelRow}>
            <label style={{ ...styles.label, margin: 0, flex: 1 }}>
              <Settings size={14} /> 自定义术语（支持中英文，优先识别）
            </label>
            <button
              type="button"
              onClick={() => void handleGenerateTerms()}
              disabled={!canGenerateTerms}
              aria-busy={generatingTerms}
              title={!courseName ? '请先填写课程名' : '根据课程名生成 15 个候选术语'}
              style={{
                ...styles.aiTermBtn,
                opacity: canGenerateTerms ? 1 : 0.5,
                cursor: canGenerateTerms ? 'pointer' : 'not-allowed',
              }}
            >
              <Sparkles size={12} />
              {generatingTerms ? '生成中…' : 'AI 生成'}
            </button>
          </div>
          <textarea
            value={customTerms}
            onChange={e => setCustomTerms(e.target.value)}
            placeholder="输入本课程的专业术语，用逗号分隔，如：gradient descent, 反向传播, 特征向量"
            style={{ ...styles.input, minHeight: 72, resize: 'vertical' }}
            rows={3}
          />
          {termGenNotice && (
            <p
              role={termGenNotice.tone === 'error' ? 'alert' : 'status'}
              style={{
                ...styles.termGenHint,
                color: termGenNotice.tone === 'error' ? 'var(--danger)' : 'var(--text-secondary)',
              }}
            >
              {termGenNotice.text}
            </p>
          )}

          {/* ADR-004 课程资料窗口：两种课型通用，边框色随场景（线上紫/线下青） */}
          {courseId && (
            <MaterialsPanel
              courseId={courseId}
              offline={isOffline}
              onAppendTerms={terms => setCustomTerms(prev => appendTerms(prev, terms))}
            />
          )}

          {/* 章节管理（多集长课程）：本次识别归属 + 增删章节 */}
          {courseId && (
            <ChapterPanel
              courseId={courseId}
              chapters={chapters}
              selectedId={selectedChapterId}
              onSelect={setSelectedChapterId}
              reload={reloadChapters}
              offline={isOffline}
            />
          )}

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
  termLabelRow: {
    display: 'flex', alignItems: 'center', gap: 8,
    marginBottom: 8, marginTop: 16,
  },
  aiTermBtn: {
    display: 'inline-flex', alignItems: 'center', gap: 4,
    padding: '5px 10px', borderRadius: 6,
    border: '1px solid var(--accent)',
    background: 'var(--accent-glow)',
    color: 'var(--accent-light)',
    fontSize: 12, fontWeight: 500,
    whiteSpace: 'nowrap',
  },
  termGenHint: { fontSize: 11, lineHeight: 1.5, marginTop: 4, marginBottom: 0 },
  startBtn: {
    display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
    width: '100%', padding: '14px',
    background: 'var(--accent)', color: 'white',
    borderRadius: 'var(--radius)', fontSize: 16, fontWeight: 600,
    marginTop: 20,
    border: 'none',
  },
  warningBar: {
    background: 'var(--danger-soft)',
    border: '1px solid var(--danger-border)',
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
