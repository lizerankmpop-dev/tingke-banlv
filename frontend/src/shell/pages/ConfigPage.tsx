import { useCallback, useEffect, useRef, useState } from 'react'
import type { DragEvent } from 'react'
import {
  ArrowLeft, Check, CheckCircle2, FileText, Layers, Lock, Mic, Monitor,
  Play, RefreshCw, Sparkles, Trash2, Upload, Volume2, Wifi, Plus, X,
} from 'lucide-react'
import { IconButton } from '../../components/ui/IconButton'
import { LevelMeter } from '../../components/ui/LevelMeter'
import { useCourseStore } from '../../stores/useCourseStore'
import { useClassroomAudioStore } from '../../stores/useClassroomAudioStore'
import { formatSize } from '../../lib/materials'
import { generateTerms, mergeTerms, TERM_GENERATE_TIMEOUT_MS } from '../../lib/termApi'
import type { Course } from '../../types'
import { isOnline, languageLabel } from '../lib/mapping'

/**
 * 课程配置页（自李梓萌原型 App.jsx 的 CourseConfig 原样转写）
 *
 * 原型 → 真实数据：
 * · 麦克风设备下拉 → GET /api/audio/devices（真实设备）
 * · 「试采 3 秒」→ POST /audio/mic-test（本次后端新增：测 3 秒 RMS，不识别不入库）
 * · 「回听录音」→ 刻意不提供：试采不留文件（界面上承诺的「三不」要兑现）
 * · 电平条 → 识别中读轮询的 inputLevel；试采时显示试采结果
 * · 资料 → POST/GET/DELETE /courses/{id}/materials；候选术语 → POST .../materials/terms
 * · 系统音频状态 → GET /api/audio/check-loopback
 * 保留的主线能力（功能不退化，样式按她的卡片体系呈现）：
 * · 字幕语言（both/orig/zh）与识别语言、简繁输出
 * · 章节管理（多集长课程，字幕按章节归档）
 */

const SUBTITLE_LANGS = [
  { value: 'both', label: '双语对照' },
  { value: 'orig', label: '仅原文' },
  { value: 'zh', label: '仅中文' },
]
const LANGUAGES = [
  { value: 'auto', label: '自动检测' }, { value: 'en', label: '英文' },
  { value: 'zh', label: '中文' }, { value: 'ja', label: '日语' },
  { value: 'ko', label: '韩语' }, { value: 'fr', label: '法语' },
]
const DOMAINS = ['计算机科学', '数学', '经济学', '医学', '法学', '英语', '其他']

interface ConfigPageProps {
  course: Course
  onBack: () => void
  onStartRecognition: () => void
  onToast: (message: string) => void
}

export function ConfigPage({ course, onBack, onStartRecognition, onToast }: ConfigPageProps) {
  const online = isOnline(course)
  const { courses, updateCourse, loadCourses } = useCourseStore()
  const { devices, loadDevices, settings, setSettings } = useClassroomAudioStore()

  const [domain, setDomain] = useState(course.domain)
  const [description, setDescription] = useState(course.description)
  const [language, setLanguage] = useState(course.language || 'auto')
  const [subtitleLang, setSubtitleLang] = useState(course.subtitleLang || 'both')
  const [customTerms, setCustomTerms] = useState((course.customTerms || []).join(', '))
  // ADR-001 AI 术语生成（v4.0.0 从旧 CourseConfig 移植到新骨架，否则按钮在主路由里看不见）
  const [generatingTerms, setGeneratingTerms] = useState(false)
  const [termGenNotice, setTermGenNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)

  /** 只把结果合并进输入框，不落库；保存仍由「保存配置 / 开始识别」的 PUT 决定。 */
  const handleGenerateTerms = async () => {
    if (generatingTerms) return
    setGeneratingTerms(true)
    setTermGenNotice(null)
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), TERM_GENERATE_TIMEOUT_MS)
    try {
      const result = await generateTerms(
        { courseName: course.name, domain, language },
        controller.signal,
      )
      if (result.terms.length === 0) {
        setTermGenNotice({ tone: 'error', text: 'AI 没有返回可用术语，已保留原有内容，可稍后重试或手动填写。' })
        return
      }
      setCustomTerms(prev => mergeTerms(prev, result.terms))
      setTermGenNotice({
        tone: 'ok',
        text: `已追加 ${result.terms.length} 个候选术语（${result.provider === 'ollama' ? '本地 Ollama' : '云端'}），可手动删改；点「保存配置」或「开始识别」后才会保存。`,
      })
    } catch (err) {
      const timedOut = controller.signal.aborted
      const detail = err instanceof Error ? err.message : ''
      setTermGenNotice({
        tone: 'error',
        text: timedOut
          ? 'AI 生成超时，已保留原有术语；不影响开始识别。'
          : `AI 生成失败：${detail || '服务暂不可用'}。已保留原有术语；不影响开始识别。`,
      })
    } finally {
      clearTimeout(timer)
      setGeneratingTerms(false)
    }
  }

  // 章节管理（主线 feat/lang-chapters 能力，样式按她的卡片）
  const [chapters, setChapters] = useState<{ id: string; name: string; sortOrder: number }[]>([])
  const [newChapter, setNewChapter] = useState('')
  const reloadChapters = useCallback(() => {
    fetch(`/api/courses/${course.id}/chapters`)
      .then((r) => (r.ok ? r.json() : []))
      .then((list) => setChapters(Array.isArray(list) ? list : []))
      .catch(() => setChapters([]))
  }, [course.id])
  useEffect(() => { reloadChapters() }, [reloadChapters])

  const addChapter = async () => {
    const name = newChapter.trim()
    if (!name) return
    const res = await fetch(`/api/courses/${course.id}/chapters`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    })
    if (res.ok) {
      setNewChapter('')
      reloadChapters()
      onToast(`已添加章节「${name}」，识别页字幕将归档到该章节`)
    } else {
      onToast('章节添加失败')
    }
  }

  const removeChapter = async (chapterId: string, name: string) => {
    const res = await fetch(`/api/courses/${course.id}/chapters/${chapterId}`, { method: 'DELETE' })
    if (res.ok) {
      reloadChapters()
      onToast(`已删除章节「${name}」`)
    }
  }

  // 系统音频（线上）loopback 状态
  const [loopbackStatus, setLoopbackStatus] = useState<'checking' | 'ok' | 'missing' | 'unreachable'>('checking')
  useEffect(() => {
    if (online) {
      setLoopbackStatus('checking')
      fetch('/api/audio/check-loopback')
        .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() })
        .then((data) => setLoopbackStatus(data.hasLoopback ? 'ok' : 'missing'))
        .catch((err) => {
          const unreachable = (err?.message || '').startsWith('HTTP') || err instanceof TypeError
          setLoopbackStatus(unreachable ? 'unreachable' : 'missing')
        })
    }
  }, [online])

  // 线下：设备列表 + 试采
  useEffect(() => {
    if (!online && devices.length === 0) void loadDevices()
  }, [online, devices.length, loadDevices])

  const [micTesting, setMicTesting] = useState(false)
  const [micResult, setMicResult] = useState<{ ok: boolean; level: number; message: string } | null>(null)

  const runMicTest = async () => {
    if (micTesting) return
    setMicTesting(true)
    setMicResult(null)
    try {
      const res = await fetch('/api/audio/mic-test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ deviceIndex: settings.micDeviceIndex, seconds: 3 }),
      })
      const data = await res.json()
      setMicResult(data)
      onToast(data.ok ? `试采完成：${data.message}` : data.message)
    } catch {
      setMicResult({ ok: false, level: 0, message: '试采失败：后端服务未运行' })
      onToast('试采失败：后端服务未运行')
    }
    setMicTesting(false)
  }

  // 保存配置（domain/description/language/subtitleLang/customTerms → PUT）
  const saveConfig = async () => {
    await updateCourse(course.id, {
      domain,
      description,
      language,
      subtitleLang,
      customTerms: customTerms.split(/[,，、]/).map((t) => t.trim()).filter(Boolean),
    })
    void loadCourses()
    onToast('课程配置已保存')
  }

  const startRecognition = async () => {
    // 开始识别前先把配置落库（主线 handleStart 同款：fire-and-forget PUT）
    await updateCourse(course.id, {
      domain, description, language, subtitleLang,
      customTerms: customTerms.split(/[,，、]/).map((t) => t.trim()).filter(Boolean),
    })
    onStartRecognition()
  }

  return (
    <div className="page page-wide">
      <div className="topbar">
        <div className="topbar-left">
          <IconButton label="返回课程列表" onClick={onBack}>
            <ArrowLeft size={18} />
          </IconButton>
          <div className="topbar-title-wrap">
            <div className="topbar-title">课程配置</div>
            <div className="topbar-sub">{course.name} · {online ? '线上网课' : '线下课堂'}</div>
          </div>
        </div>
        <div className="topbar-actions">
          <button type="button" className={`btn ${online ? 'btn-primary' : 'btn-cyan'} btn-sm`} onClick={() => void startRecognition()}>
            <Play size={15} /> 开始识别
          </button>
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => void saveConfig()}>
            <Check size={15} /> 保存配置
          </button>
        </div>
      </div>

      <div className="config-grid">
        <section className="config-card">
          <div className="card-head">
            <div className={`card-icon ${online ? 'purple' : 'cyan'}`}>
              {online ? <Monitor size={18} /> : <Mic size={18} />}
            </div>
            <div className="card-head-copy">
              <h2>{online ? '系统音频捕获' : '麦克风测试'}</h2>
              <p>{online ? '线上专属 · 直接抓电脑播放的声音' : '线下专属 · 试采 3 秒确认电平'}</p>
            </div>
            <span className={`badge ${online ? 'purple' : 'cyan'}`}>{online ? '线上' : '线下'}</span>
          </div>

          {online ? (
            <>
              <div className="audio-source">
                <div className="audio-source-icon">
                  <Volume2 size={18} />
                </div>
                <div>
                  <div className="audio-source-name">扬声器（系统音频 Loopback）</div>
                  <div className="audio-source-sub">WASAPI Loopback · 无需虚拟声卡</div>
                </div>
                <span className={`badge ${loopbackStatus === 'ok' ? 'green' : loopbackStatus === 'missing' ? 'red' : 'amber'}`}>
                  {loopbackStatus === 'ok' ? '已就绪' : loopbackStatus === 'missing' ? '未检测到' : loopbackStatus === 'unreachable' ? '后端未运行' : '检测中…'}
                </span>
              </div>
              <div className="config-actions">
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => {
                  setLoopbackStatus('checking')
                  fetch('/api/audio/check-loopback')
                    .then((r) => (r.ok ? r.json() : null))
                    .then((data) => { setLoopbackStatus(data?.hasLoopback ? 'ok' : 'missing'); onToast(data?.hasLoopback ? '已重新检测系统音频：正常' : '未检测到 Loopback 设备') })
                    .catch(() => { setLoopbackStatus('unreachable'); onToast('后端服务未运行') })
                }}>
                  <RefreshCw size={14} /> 重新检测
                </button>
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => onToast('开始识别后会自动捕获系统音频，播放网课即可看到字幕')}>
                  <Wifi size={14} /> 捕获说明
                </button>
              </div>
              {loopbackStatus === 'missing' ? (
                <div className="form-error">未检测到 Loopback 设备：请安装 PyAudioWPatch（打包版已内置），或参考帮助页说明。</div>
              ) : null}
              <p className="fineprint">
                <Lock size={12} /> 无需安装虚拟声卡；开始识别后，播放网课即可看到字幕。
              </p>
            </>
          ) : (
            <>
              <label className="field field-tight">
                <span>麦克风设备</span>
                <select
                  value={settings.micDeviceIndex === null ? '' : String(settings.micDeviceIndex)}
                  onChange={(event) => {
                    const v = event.target.value
                    setSettings({ micDeviceIndex: v === '' ? null : Number(v) })
                  }}
                >
                  <option value="">系统默认麦克风</option>
                  {devices.map((device) => (
                    <option key={device.index} value={device.index}>{device.name}</option>
                  ))}
                </select>
              </label>

              <div className="field">
                <span>收音档位</span>
                <div className="segmented">
                  <button type="button" className={settings.vadProfile === 'near' ? 'active' : ''} onClick={() => setSettings({ vadProfile: 'near' })}>近讲</button>
                  <button type="button" className={settings.vadProfile === 'far-field' ? 'active' : ''} onClick={() => setSettings({ vadProfile: 'far-field' })}>远场</button>
                </div>
              </div>

              <div className="meter-block">
                <div className="meter-head">
                  <span>实时电平</span>
                  <span className={`meter-value ${micTesting ? 'testing' : ''}`}>
                    {micTesting ? '采集中…' : micResult ? micResult.message : '待试采'}
                  </span>
                </div>
                <LevelMeter active={micTesting} level={micResult?.level ?? 0.5} tone="cyan" bars={30} />
                <div className="meter-scale">
                  <span>-40 dB</span>
                  <span>-20 dB</span>
                  <span>0 dB</span>
                </div>
              </div>

              <div className="config-actions">
                <button type="button" className="btn btn-cyan btn-sm" onClick={() => void runMicTest()} disabled={micTesting}>
                  {micTesting ? (
                    <>
                      <RefreshCw size={14} className="spin" /> 正在试采 3 秒…
                    </>
                  ) : (
                    <>
                      <Mic size={14} /> 试采 3 秒
                    </>
                  )}
                </button>
              </div>

              {micResult ? (
                <div className={`verdict ${micResult.ok ? 'good' : 'checking'}`}>
                  <CheckCircle2 size={16} />
                  <div>
                    <strong>{micResult.message}</strong>
                    <span>峰值 {Math.round((micResult.level || 0) * 100)}% · {settings.vadProfile === 'far-field' ? '远场' : '近讲'}档 · 降噪已开</span>
                  </div>
                </div>
              ) : (
                <div className="verdict checking">
                  <Mic size={16} />
                  <div>
                    <strong>先试采确认电平</strong>
                    <span>教室远场建议坐前排或靠近讲台</span>
                  </div>
                </div>
              )}

              <p className="fineprint">
                <Lock size={12} /> 试采不写入课程、不启动识别、不留录音文件。
              </p>
            </>
          )}
        </section>

        <MaterialsCard courseId={course.id} courseType={course.courseType} onToast={onToast} onAppendTerms={(terms) => {
          setCustomTerms((prev) => {
            const existing = prev.split(/[,，、]/).map((t) => t.trim()).filter(Boolean)
            const merged = Array.from(new Set([...existing, ...terms]))
            return merged.join(', ')
          })
          onToast(`已追加候选术语到术语框，可手动删改`)
        }} />
      </div>

      {/* 识别设置（主线能力：字幕语言 / 识别语言 / 领域 / 描述） */}
      <section className="config-card term-panel">
        <div className="card-head">
          <div className="card-icon purple">
            <Layers size={18} />
          </div>
          <div className="card-head-copy">
            <h2>识别与字幕</h2>
            <p>字幕语言、识别语言与课程信息</p>
          </div>
          <span className="badge green">{languageLabel(language)}</span>
        </div>
        <div className="form-grid">
          <label className="field">
            <span>字幕显示</span>
            <div className="segmented">
              {SUBTITLE_LANGS.map((item) => (
                <button key={item.value} type="button" className={subtitleLang === item.value ? 'active' : ''} onClick={() => setSubtitleLang(item.value)}>
                  {item.label}
                </button>
              ))}
            </div>
          </label>
          <label className="field">
            <span>识别语言</span>
            <select value={language} onChange={(event) => setLanguage(event.target.value)}>
              {LANGUAGES.map((item) => (
                <option key={item.value} value={item.value}>{item.label}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>所属领域</span>
            <select value={domain} onChange={(event) => setDomain(event.target.value)}>
              {DOMAINS.map((item) => <option key={item}>{item}</option>)}
            </select>
          </label>
          <label className="field field-full">
            <span>课程描述</span>
            <textarea rows={2} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="描述课程内容，帮助 AI 更好理解上下文" />
          </label>
        </div>
      </section>

      {/* 课程术语（原样：三层术语 chips） */}
      <section className="config-card term-panel">
        <div className="card-head">
          <div className="card-icon purple">
            <Layers size={18} />
          </div>
          <div className="card-head-copy">
            <h2>课程术语</h2>
            <p>优先参与识别，减少专业词被听错</p>
          </div>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => void handleGenerateTerms()}
            disabled={generatingTerms}
            aria-busy={generatingTerms}
            title="云端优先，失败自动降级本地 Ollama；不会覆盖已填术语"
          >
            <Sparkles size={14} /> {generatingTerms ? '生成中…' : 'AI 生成术语'}
          </button>
          <span className="badge green">已自动积累 {customTerms.split(/[,，、]/).map((t) => t.trim()).filter(Boolean).length} 个</span>
        </div>
        <label className="field field-full">
          <span>术语列表（逗号分隔）</span>
          <textarea rows={2} value={customTerms} onChange={(event) => setCustomTerms(event.target.value)} placeholder="gradient descent, 反向传播, 特征向量…" />
        </label>
        {termGenNotice ? (
          <p className="fineprint" role={termGenNotice.tone === 'error' ? 'alert' : 'status'}>
            {termGenNotice.text}
          </p>
        ) : null}
        <div className="term-note">
          <CheckCircle2 size={14} />
          <span>三层术语：内置领域词表 + 你填的自定义术语 + 识别过程中自动积累。</span>
        </div>
      </section>

      {/* 章节管理（主线 feat/lang-chapters 能力） */}
      <section className="config-card term-panel">
        <div className="card-head">
          <div className="card-icon purple">
            <FileText size={18} />
          </div>
          <div className="card-head-copy">
            <h2>章节</h2>
            <p>多集长课程按章节归档字幕；识别时在识别页顶部选择</p>
          </div>
          <span className="badge">{chapters.length} 个</span>
        </div>
        <div className="chips">
          {chapters.map((chapter) => (
            <span className="chip chapter-chip" key={chapter.id}>
              {chapter.name}
              <button type="button" className="chip-remove" onClick={() => void removeChapter(chapter.id, chapter.name)} aria-label={`删除章节 ${chapter.name}`}>
                <X size={11} />
              </button>
            </span>
          ))}
          {chapters.length === 0 ? <span className="side-empty">还没有章节；不分章节也完全可以正常识别</span> : null}
        </div>
        <div className="chapter-add">
          <input
            value={newChapter}
            onChange={(event) => setNewChapter(event.target.value)}
            onKeyDown={(event) => { if (event.key === 'Enter') void addChapter() }}
            placeholder="新章节名，例如「第 3 讲 · 梯度下降」"
          />
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => void addChapter()} disabled={!newChapter.trim()}>
            <Plus size={13} /> 添加
          </button>
        </div>
      </section>
    </div>
  )
}

/* ------------------------------------------------------------
   课程资料卡（原型原样布局 + 真实上传/列表/候选术语）
   ------------------------------------------------------------ */

interface MaterialRecord {
  id: string
  name: string
  ext: string
  size: number
  indexed: boolean
}

function MaterialsCard({
  courseId,
  courseType,
  onToast,
  onAppendTerms,
}: {
  courseId: string
  courseType: string
  onToast: (message: string) => void
  onAppendTerms: (terms: string[]) => void
}) {
  const [materials, setMaterials] = useState<MaterialRecord[]>([])
  const [uploading, setUploading] = useState(0)
  const [dragOver, setDragOver] = useState(false)
  const [candidateTerms, setCandidateTerms] = useState<string[]>([])
  const fileInputRef = useRef<HTMLInputElement>(null)

  const reload = useCallback(() => {
    fetch(`/api/courses/${courseId}/materials`)
      .then((r) => (r.ok ? r.json() : []))
      .then((list: MaterialRecord[]) => setMaterials(list))
      .catch(() => {})
  }, [courseId])
  useEffect(() => { reload() }, [reload])

  async function uploadFiles(files: File[]) {
    if (files.length === 0) return
    setUploading((n) => n + files.length)
    for (const file of files) {
      try {
        const res = await fetch(`/api/courses/${courseId}/materials`, {
          method: 'POST',
          headers: { 'X-Filename': encodeURIComponent(file.name) },
          body: file,
        })
        if (!res.ok) {
          const detail = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
          onToast(`${file.name}：${detail.detail || '上传失败'}`)
        }
      } catch {
        onToast('后端服务未运行，上传失败')
      } finally {
        setUploading((n) => Math.max(0, n - 1))
      }
    }
    if (fileInputRef.current) fileInputRef.current.value = ''
    reload()
  }

  function onDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault()
    setDragOver(false)
    const files = Array.from(event.dataTransfer.files || [])
    if (files.length > 0) void uploadFiles(files)
  }

  async function removeMaterial(id: string) {
    setMaterials((prev) => prev.filter((m) => m.id !== id))
    try {
      await fetch(`/api/courses/${courseId}/materials/${id}`, { method: 'DELETE' })
      onToast('已从本课资料列表移除（文件已删）')
    } catch { /* 乐观删除失败就靠下次刷新纠正 */ }
    reload()
  }

  async function appendCandidates() {
    try {
      const res = await fetch(`/api/courses/${courseId}/materials/terms`, { method: 'POST' })
      const data = await res.json()
      const terms: string[] = data.terms || []
      if (terms.length === 0) {
        onToast(data.fromFiles > 0 ? '资料里没找到重复出现的候选术语' : '还没有可解析的文本资料（TXT / MD）')
        return
      }
      setCandidateTerms(terms)
      onAppendTerms(terms)
    } catch {
      onToast('后端服务未运行')
    }
  }

  return (
    <section className={`config-card material-card ${courseType}`}>
      <div className="card-head">
        <div className="card-icon purple">
          <FileText size={18} />
        </div>
        <div className="card-head-copy">
          <h2>课程资料</h2>
          <p>上传讲义、课件，自动提取候选术语</p>
        </div>
        <span className="badge">仅存本机</span>
      </div>

      <button
        type="button"
        className={`dropzone ${dragOver ? 'drag-over' : ''}`}
        onClick={() => fileInputRef.current?.click()}
        onDragOver={(event) => { event.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
      >
        <div className="dropzone-icon">
          <Upload size={22} />
        </div>
        <strong>拖入 PDF / PPT / DOCX / TXT / MD</strong>
        <span>或点击选择文件 · 单个文件不超过 20 MB</span>
        {uploading > 0 ? <span className="dropzone-busy">正在上传 {uploading} 个文件…</span> : null}
      </button>
      <input
        ref={fileInputRef}
        type="file"
        multiple
        hidden
        onChange={(event) => void uploadFiles(Array.from(event.target.files || []))}
      />

      <div className="file-list">
        {materials.map((file) => (
          <div className="file-row" key={file.id}>
            <div className="file-icon">
              <FileText size={15} />
            </div>
            <div className="file-meta">
              <div className="file-name">{file.name}</div>
              <div className="file-size">{formatSize(file.size)} · {file.indexed ? '已解析' : '已登记'}</div>
            </div>
            <span className={`file-status ${file.indexed ? 'done' : ''}`}>
              {file.indexed ? '已解析' : '已登记'}
            </span>
            <IconButton label="移除资料" onClick={() => void removeMaterial(file.id)}>
              <Trash2 size={14} />
            </IconButton>
          </div>
        ))}
        {materials.length === 0 ? (
          <div className="side-empty">还没有资料；TXT / MD 会被解析进 AI 上下文</div>
        ) : null}
      </div>

      <div className="candidate-terms">
        <div className="candidate-head">
          <span>候选术语</span>
          <span className="badge purple">{candidateTerms.length > 0 ? `${candidateTerms.length} 个` : '待提取'}</span>
        </div>
        <div className="chips">
          {candidateTerms.slice(0, 8).map((term) => (
            <span className="chip" key={term}>{term}</span>
          ))}
          {candidateTerms.length > 8 ? <span className="chip more">+{candidateTerms.length - 8}</span> : null}
        </div>
      </div>

      <button type="button" className="btn btn-secondary btn-sm btn-block" onClick={() => void appendCandidates()}>
        <Sparkles size={14} /> 追加候选术语到术语框
      </button>

      <p className="fineprint">
        <Lock size={12} /> 文件只存本机课程目录，不上传云端。
      </p>
    </section>
  )
}
