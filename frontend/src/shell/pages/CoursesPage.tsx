import { useEffect, useState } from 'react'
import { Plus, Search, Settings, Trash2, X, Monitor, Mic, Check, Link2, Lock } from 'lucide-react'
import { IconButton } from '../../components/ui/IconButton'
import { EmptyState } from '../../components/ui/EmptyState'
import { LevelMeter } from '../../components/ui/LevelMeter'
import { useCourseStore } from '../../stores/useCourseStore'
import { useClassroomAudioStore } from '../../stores/useClassroomAudioStore'
import type { Course } from '../../types'
import { isOnline, languageLabel, relativeTime } from '../lib/mapping'

/**
 * 课程列表页（自李梓萌原型 App.jsx 的 CourseList / CourseCard / NewCourseModal 原样转写）
 *
 * 原型 → 真实数据的映射：
 * · COURSES 假数据 → GET /api/courses（useCourseStore）
 * · updatedAt「今天 14:20」→ relativeTime(updatedAt)
 * · videoCount / wordCount 真实字段同名
 * · 「第 N 节」（线下）→ 主线无节次字段，显示领域（诚实优先，不编数字）
 * · mic 选择 → /api/audio/devices 真实设备列表
 * · 收音档位（近讲/远场）→ vadProfile（near / far-field），写进 classroomAudioStore
 * · 新建 → POST /api/courses
 * 原型没有删除课程功能 —— 按她的样式体系（modal / icon-btn）补了删除入口，
 * 这是主线既有能力（DELETE /api/courses/{id}），砍掉属于功能退化。
 */

const DOMAINS = ['计算机科学', '数学', '经济学', '医学', '法学', '英语', '其他']
/** 原型的识别语言选项 → 主线 language code（「中英混合」交给 auto 检测） */
const LANGUAGES = [
  { value: 'auto', label: '自动检测' },
  { value: 'en', label: '英文' },
  { value: 'zh', label: '中文' },
]

function splitTerms(value: string): string[] {
  return value
    .split(/[,，、;；\n]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

interface CoursesPageProps {
  filter: 'all' | 'online' | 'offline'
  onOpenCourse: (course: Course) => void
  onOpenConfig: (course: Course) => void
  onNewCourse: () => void
  dueCount: number
  onToast: (message: string) => void
}

export function CoursesPage({ filter, onOpenCourse, onOpenConfig, onNewCourse, dueCount, onToast }: CoursesPageProps) {
  const { courses, isLoading, loadCourses, deleteCourse } = useCourseStore()
  const [query, setQuery] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<Course | null>(null)

  useEffect(() => {
    void loadCourses()
  }, [loadCourses])

  const matches = (course: Course) => {
    const haystack = [course.name, course.domain, course.description, ...(course.customTerms || [])]
      .join(' ')
      .toLowerCase()
    return haystack.includes(query.trim().toLowerCase())
  }

  const filtered = courses.filter(
    (course) => (filter === 'all' || (isOnline(course) ? 'online' : 'offline') === filter) && matches(course),
  )
  const online = filtered.filter((course) => isOnline(course))
  const offline = filtered.filter((course) => !isOnline(course))
  const counts = {
    all: courses.length,
    online: courses.filter((c) => isOnline(c)).length,
    offline: courses.filter((c) => !isOnline(c)).length,
  }

  const confirmDelete = async () => {
    if (!deleteTarget) return
    await deleteCourse(deleteTarget.id)
    onToast(`已删除「${deleteTarget.name}」及其全部字幕与生词`)
    setDeleteTarget(null)
  }

  if (isLoading && courses.length === 0) {
    return (
      <div className="page">
        <p className="page-sub" style={{ padding: 40 }}>课程加载中…</p>
      </div>
    )
  }

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1 className="page-title">课程</h1>
          <p className="page-sub">
            {counts.all} 门课程 · {counts.online} 门线上 · {counts.offline} 门线下 ·{' '}
            {dueCount} 个今日到期
          </p>
        </div>
        <div className="head-actions">
          <label className="search-box">
            <Search size={15} />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索课程或术语"
            />
          </label>
          <button type="button" className="btn btn-primary btn-sm" onClick={onNewCourse}>
            <Plus size={15} /> 新建课程
          </button>
        </div>
      </header>

      {filter !== 'offline' && online.length > 0 && (
        <section className="course-section">
          <div className="section-head">
            <div className="section-title">
              <span className="dot purple" />
              <h2>线上网课</h2>
              <span className="section-count">{online.length}</span>
            </div>
            <span className="section-hint">系统音频 · Coursera / B站 / YouTube</span>
          </div>
          <div className="course-grid">
            {online.map((course) => (
              <CourseCard
                key={course.id}
                course={course}
                onOpen={onOpenCourse}
                onConfig={onOpenConfig}
                onDelete={setDeleteTarget}
              />
            ))}
          </div>
        </section>
      )}

      {filter !== 'online' && offline.length > 0 && (
        <section className="course-section">
          <div className="section-head">
            <div className="section-title">
              <span className="dot cyan" />
              <h2>线下课堂</h2>
              <span className="section-count">{offline.length}</span>
            </div>
            <span className="section-hint">麦克风收音 · 教室 / 讲座 / 会议室</span>
          </div>
          <div className="course-grid">
            {offline.map((course) => (
              <CourseCard
                key={course.id}
                course={course}
                onOpen={onOpenCourse}
                onConfig={onOpenConfig}
                onDelete={setDeleteTarget}
              />
            ))}
          </div>
        </section>
      )}

      {courses.length === 0 && (
        <EmptyState
          title="还没有课程"
          description="新建一门线上网课或线下课堂，开始实时字幕学习。"
          action={
            <button type="button" className="btn btn-primary btn-sm" onClick={onNewCourse}>
              <Plus size={15} /> 新建第一门课程
            </button>
          }
        />
      )}

      {courses.length > 0 && filtered.length === 0 && (
        <EmptyState
          title="没有找到匹配的课程"
          description="换个关键词，或新建一门线上网课 / 线下课堂。"
          action={
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => setQuery('')}>
              清空搜索
            </button>
          }
        />
      )}

      {deleteTarget && (
        <div className="modal-backdrop" onClick={() => setDeleteTarget(null)}>
          <div className="modal modal-delete" onClick={(event) => event.stopPropagation()}>
            <div className="modal-head">
              <div>
                <h2 className="modal-title">删除课程</h2>
                <p className="modal-sub">
                  「{deleteTarget.name}」的全部字幕记录、生词与配置将被永久删除，此操作不可恢复。
                </p>
              </div>
              <IconButton label="关闭" onClick={() => setDeleteTarget(null)}>
                <X size={18} />
              </IconButton>
            </div>
            <div className="modal-foot">
              <span className="modal-hint">
                <Lock size={12} /> 数据只在本机，删除后无法找回
              </span>
              <div className="modal-actions">
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => setDeleteTarget(null)}>
                  取消
                </button>
                <button type="button" className="btn btn-danger btn-sm" onClick={() => void confirmDelete()}>
                  <Trash2 size={14} /> 确认删除
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function CourseCard({
  course,
  onOpen,
  onConfig,
  onDelete,
}: {
  course: Course
  onOpen: (course: Course) => void
  onConfig: (course: Course) => void
  onDelete: (course: Course) => void
}) {
  const online = isOnline(course)
  return (
    <article
      className={`course-card ${online ? 'online' : 'offline'}`}
      onClick={() => onOpen(course)}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === 'Enter') onOpen(course)
      }}
    >
      <div className="course-card-top">
        <span className="course-type">
          <span className={`dot ${online ? 'purple' : 'cyan'}`} />
          {online ? '线上' : '线下'}
        </span>
        <div className="course-card-actions">
          <IconButton
            label="课程配置"
            className="course-config-btn"
            onClick={(event) => {
              event.stopPropagation()
              onConfig(course)
            }}
          >
            <Settings size={14} />
          </IconButton>
          <IconButton
            label="删除课程"
            className="course-config-btn"
            onClick={(event) => {
              event.stopPropagation()
              onDelete(course)
            }}
          >
            <Trash2 size={14} />
          </IconButton>
        </div>
      </div>
      <h3 className="course-name">{course.name}</h3>
      {course.description ? <p className="course-desc">{course.description}</p> : null}
      <div className="course-card-meta">
        <span>{course.domain}</span>
        <span className="sep">·</span>
        <span>{languageLabel(course.language)}</span>
        <span className="sep">·</span>
        <span>{course.wordCount ?? 0} 词汇</span>
        <span className="sep">·</span>
        <span>{relativeTime(course.updatedAt)}</span>
      </div>
    </article>
  )
}

/* ------------------------------------------------------------
   新建课程（她的两模式卡 + 分场景表单）
   ------------------------------------------------------------ */

export interface NewCoursePayload {
  name: string
  courseType: 'online' | 'offline'
  domain: string
  language: string
  customTerms: string[]
  description: string
}

export function NewCourseModal({
  initialType,
  onClose,
  onCreate,
}: {
  initialType: 'online' | 'offline'
  onClose: () => void
  onCreate: (payload: NewCoursePayload) => Promise<void>
}) {
  const [type, setType] = useState<'online' | 'offline'>(initialType)
  const [form, setForm] = useState({
    name: '',
    domain: '计算机科学',
    language: 'auto',
    terms: '',
    description: '',
  })
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  // 线下：真实麦克风设备列表 + 收音档位（vadProfile）
  const { devices, loadDevices, settings, setSettings } = useClassroomAudioStore()
  useEffect(() => {
    if (type === 'offline' && devices.length === 0) void loadDevices()
  }, [type, devices.length, loadDevices])

  function update(field: keyof typeof form, value: string) {
    setForm((prev) => ({ ...prev, [field]: value }))
    if (error) setError('')
  }

  async function submit() {
    if (!form.name.trim()) {
      setError('先给课程起个名字，例如「CS229 机器学习」。')
      return
    }
    setSubmitting(true)
    try {
      await onCreate({
        name: form.name.trim(),
        courseType: type,
        domain: form.domain,
        language: form.language,
        customTerms: splitTerms(form.terms),
        description: form.description.trim(),
      })
    } finally {
      setSubmitting(false)
    }
  }

  const online = type === 'online'

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-head">
          <div>
            <h2 className="modal-title">新建课程</h2>
            <p className="modal-sub">线上抓系统音频，线下用麦克风。选中的场景会贯穿导航、卡片和识别页。</p>
          </div>
          <IconButton label="关闭" onClick={onClose}>
            <X size={18} />
          </IconButton>
        </div>

        <div className="mode-grid">
          <button
            type="button"
            className={`mode-card online ${online ? 'active' : ''}`}
            onClick={() => setType('online')}
          >
            <div className="mode-icon">
              <Monitor size={22} />
            </div>
            <div className="mode-copy">
              <h3>线上网课</h3>
              <p>捕获系统音频</p>
              <span>Coursera / B站 / YouTube</span>
            </div>
            {online ? (
              <span className="mode-check">
                <Check size={14} />
              </span>
            ) : null}
          </button>

          <button
            type="button"
            className={`mode-card offline ${!online ? 'active' : ''}`}
            onClick={() => setType('offline')}
          >
            <div className="mode-icon">
              <Mic size={22} />
            </div>
            <div className="mode-copy">
              <h3>线下课堂</h3>
              <p>麦克风收音</p>
              <span>教室 / 讲座 / 会议室</span>
            </div>
            {!online ? (
              <span className="mode-check">
                <Check size={14} />
              </span>
            ) : null}
          </button>
        </div>

        <div className="form-grid">
          <label className="field field-name">
            <span>课程名称 *</span>
            <input
              value={form.name}
              onChange={(event) => update('name', event.target.value)}
              placeholder={online ? '例如：CS229 机器学习' : '例如：高等数学 第三章'}
              autoFocus
            />
          </label>

          <label className="field">
            <span>所属领域</span>
            <select value={form.domain} onChange={(event) => update('domain', event.target.value)}>
              {DOMAINS.map((item) => (
                <option key={item}>{item}</option>
              ))}
            </select>
          </label>

          <label className="field">
            <span>识别语言</span>
            <select value={form.language} onChange={(event) => update('language', event.target.value)}>
              {LANGUAGES.map((item) => (
                <option key={item.value} value={item.value}>{item.label}</option>
              ))}
            </select>
          </label>

          <label className="field field-full">
            <span>自定义术语（可选）</span>
            <textarea
              rows={2}
              value={form.terms}
              onChange={(event) => update('terms', event.target.value)}
              placeholder="用逗号分隔，例如 gradient descent, overfitting, 正则化"
            />
            <small>你填的词会被优先识别；人名、缩写、课程特有的说法最值得填。</small>
          </label>

          {online ? (
            <div className="field field-full field-hint-block">
              <span>课程链接</span>
              <div className="input-with-icon">
                <Link2 size={14} />
                <input placeholder="开始识别后在识别页地址栏粘贴，或把链接拖进网页区" disabled />
              </div>
              <small>开始识别后，在中间网页区域打开课程页面即可。</small>
            </div>
          ) : (
            <>
              <label className="field">
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
                    <option key={device.index} value={device.index}>
                      {device.name}
                    </option>
                  ))}
                </select>
              </label>

              <div className="field">
                <span>收音档位</span>
                <div className="segmented">
                  <button
                    type="button"
                    className={settings.vadProfile === 'near' ? 'active' : ''}
                    onClick={() => setSettings({ vadProfile: 'near' })}
                  >
                    近讲
                  </button>
                  <button
                    type="button"
                    className={settings.vadProfile === 'far-field' ? 'active' : ''}
                    onClick={() => setSettings({ vadProfile: 'far-field' })}
                  >
                    远场
                  </button>
                </div>
              </div>

              <div className="field field-full">
                <span>收音示意</span>
                <div className="mini-level">
                  <LevelMeter active={false} level={0.5} tone="cyan" bars={34} />
                  <span className="mini-level-label">建课后可在课程配置页试采 3 秒实测电平</span>
                </div>
              </div>
            </>
          )}

          <label className="field field-full">
            <span>课程描述（可选）</span>
            <textarea
              rows={2}
              value={form.description}
              onChange={(event) => update('description', event.target.value)}
              placeholder="描述课程内容，帮助 AI 更好理解上下文"
            />
          </label>
        </div>

        {error ? <div className="form-error">{error}</div> : null}

        <div className="modal-foot">
          <span className="modal-hint">
            <Lock size={12} /> 所有配置只保存在本机，可随时修改
          </span>
          <div className="modal-actions">
            <button type="button" className="btn btn-secondary btn-sm" onClick={onClose}>
              取消
            </button>
            <button
              type="button"
              className={`btn btn-sm ${online ? 'btn-primary' : 'btn-cyan'}`}
              onClick={() => void submit()}
              disabled={submitting || !form.name.trim()}
            >
              <Plus size={14} /> {submitting ? '创建中…' : `创建${online ? '线上课程' : '线下课程'}`}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
