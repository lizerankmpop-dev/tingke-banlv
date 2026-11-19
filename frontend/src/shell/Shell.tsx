import { lazy, Suspense, useCallback, useEffect, useState } from 'react'
import { useCourseStore } from '../stores/useCourseStore'
import { useSubtitleStore } from '../stores/useSubtitleStore'
import { useHostHeartbeat } from '../hooks/useHostHeartbeat'
import { useOverlayStore } from '../stores/useOverlayStore'
import { vocabularyApi, type DueSummary } from '../lib/vocabularyApi'
import { getDiagInfo } from '../lib/diagBridge'
import { onboardingBridge } from '../lib/onboardingBridge'
import { readDefaultCourseType } from '../lib/defaultCourseType'
import { loadLocalLLMConfig, mergeLLMConfig, getLLMConfig, putLLMConfig } from '../lib/llmConfigApi'
import { Toast } from '../components/ui/Toast'
import { Sidebar, type Screen } from './components/Sidebar'
import { CoursesPage, NewCourseModal, type NewCoursePayload } from './pages/CoursesPage'
import { RecognitionPage } from './pages/RecognitionPage'
import { ChapterSummaryPage } from './pages/ChapterSummaryPage'
import { ExportsPage } from './pages/ExportsPage'
import { ConfigPage } from './pages/ConfigPage'
import { ReviewPage } from './pages/ReviewPage'
import { VocabPage } from './pages/VocabPage'
import type { SummaryDraft } from '../lib/chapterApi'
import { WordPanelDrawer } from './components/WordPanelDrawer'
import './limeng.css'

/** 帮助/关于：主线既有面板（版本、FAQ、日志目录），惰性加载避免拖慢首屏 */
const HelpAboutPanel = lazy(() => import('../components/HelpAboutPanel'))
const OverlayControlPanel = lazy(() => import('../components/OverlayControlPanel'))

/**
 * 主应用骨架（自李梓萌原型 App.jsx 的 App() 原样转写）
 *
 * 结构与原型一致：Sidebar + 五个 screen（courses / recognition / config /
 * review / vocabulary）+ 新建课程弹窗 + 点词抽屉 + Toast。
 *
 * 与原型的差异只在数据层（全部来自真实后端）：
 * · courses / counts → GET /api/courses
 * · dueCount / wordCount → GET /api/vocabulary/due-summary（跨课程一次拿）
 * · createCourse → POST /api/courses（她的「新建后进配置页」流程保留）
 * · 点词 → useSubtitleStore.selectWord（词典 + AI 讲解抽屉）
 * 「设置/帮助」打开主线 HelpAboutPanel；「导出记录」跳生词本（导出入口都在那）
 */

export default function Shell() {
  const { courses, isLoading, loadCourses, createCourse } = useCourseStore()
  const selectWord = useSubtitleStore((s) => s.selectWord)
  const selectedWord = useSubtitleStore((s) => s.selectedWord)
  const closeWordPanel = useSubtitleStore((s) => s.closeWordPanel)

  // 存活心跳：主窗口崩溃时悬浮窗要能明确提示，而不是永远停在最后一句字幕
  useHostHeartbeat()

  const [screen, setScreen] = useState<Screen>('courses')
  const [filter, setFilter] = useState<'all' | 'online' | 'offline'>('all')
  const [selectedCourseId, setSelectedCourseId] = useState<string | null>(null)
  const [showNewCourse, setShowNewCourse] = useState(false)
  const [newCourseType, setNewCourseType] = useState<'online' | 'offline'>('online')
  const [toast, setToast] = useState<string | null>(null)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [coursesExpanded, setCoursesExpanded] = useState(true)
  const [dueSummary, setDueSummary] = useState<DueSummary | null>(null)
  const [version, setVersion] = useState('')
  const [showHelp, setShowHelp] = useState(false)
  const [showOverlayPanel, setShowOverlayPanel] = useState(false)
  const [summaryDraft, setSummaryDraft] = useState<SummaryDraft | null>(null)
  const initializeOverlay = useOverlayStore(state => state.init)
  const highContrast = useOverlayStore(state => state.settings.highContrast)

  useEffect(() => { void initializeOverlay() }, [initializeOverlay])
  useEffect(() => {
    const root = document.documentElement
    if (highContrast) root.dataset.contrast = 'high'
    else delete root.dataset.contrast
    return () => { delete root.dataset.contrast }
  }, [highContrast])

  useEffect(() => {
    void loadCourses()
    vocabularyApi.dueSummary().then(setDueSummary).catch(() => {})
    void getDiagInfo().then((info) => { if (info?.version) setVersion(info.version) }).catch(() => {})
    // 恢复上次保存的 LLM 配置到后端运行时（后端配置不落盘，重启会丢）。
    // 只有本地真的存过配置才推送，避免覆盖由环境变量注入的部署配置。
    void (async () => {
      const local = loadLocalLLMConfig()
      if (!local) return
      const server = await getLLMConfig()
      const merged = mergeLLMConfig(server, local)
      await putLLMConfig(merged)
    })()
  }, [loadCourses])

  // 收藏成功后刷新到期概览（侧栏计数）
  const refreshDue = useCallback(() => {
    vocabularyApi.dueSummary().then(setDueSummary).catch(() => {})
  }, [])

  useEffect(() => {
    if (!toast) return undefined
    const timer = window.setTimeout(() => setToast(null), 2800)
    return () => window.clearTimeout(timer)
  }, [toast])

  const selectedCourse = courses.find((c) => c.id === selectedCourseId) ?? courses[0] ?? null

  const counts = {
    all: courses.length,
    online: courses.filter((c) => c.courseType !== 'offline').length,
    offline: courses.filter((c) => c.courseType === 'offline').length,
  }

  function navigate(nextScreen: Screen, nextFilter?: 'all' | 'online' | 'offline') {
    setScreen(nextScreen)
    if (nextFilter) setFilter(nextFilter)
    closeWordPanel()
  }

  function openCourse(courseId: string) {
    setSelectedCourseId(courseId)
    closeWordPanel()
    setScreen('recognition')
  }

  function openConfig(courseId: string) {
    setSelectedCourseId(courseId)
    closeWordPanel()
    setScreen('config')
  }

  // D7：「结束并总结」后带着摘要草稿跳到章节保存页；保存完回识别页。
  // 草稿存 state 而不是 URL，避免刷新后重复触发生成（一次摘要要十几秒）。
  function openChapterSummary(courseId: string, draft: SummaryDraft) {
    setSelectedCourseId(courseId)
    setSummaryDraft(draft)
    closeWordPanel()
    setScreen('chapter-summary')
  }

  function backFromChapterSummary() {
    setSummaryDraft(null)
    setScreen('recognition')
  }

  function openNewCourse(type?: 'online' | 'offline') {
    // 没显式指定时，用首启向导第 2 步保存的使用场景作为默认类型
    setNewCourseType(type ?? readDefaultCourseType() ?? 'online')
    setShowNewCourse(true)
  }

  async function handleCreate(payload: NewCoursePayload) {
    const course = await createCourse({
      name: payload.name,
      domain: payload.domain,
      description: payload.description,
      customTerms: payload.customTerms,
      courseType: payload.courseType,
      language: payload.language,
      simpMode: 'auto-simplified',
      subtitleLang: 'both',
    })
    setSelectedCourseId(course.id)
    setShowNewCourse(false)
    setScreen('config')
    refreshDue()
    setToast(
      payload.courseType === 'offline'
        ? '线下课程已创建，请先做麦克风测试'
        : '线上课程已创建，配置完成即可开始识别',
    )
  }

  const wordCount = dueSummary?.total ?? 0
  const dueCount = dueSummary?.due ?? 0

  return (
    <div className="app">
      <Sidebar
        screen={screen}
        activeFilter={screen === 'courses' ? filter : null}
        counts={counts}
        dueCount={dueCount}
        wordCount={wordCount}
        version={version || '4.3.0'}
        collapsed={sidebarCollapsed}
        onToggleCollapse={() => setSidebarCollapsed((v) => !v)}
        coursesExpanded={coursesExpanded}
        onToggleCourses={() => setCoursesExpanded((v) => !v)}
        onNavigate={navigate}
        onNewCourse={() => openNewCourse()}
        onExport={() => navigate('exports')}
        onSettings={() => setShowOverlayPanel(true)}
        onOpenGuide={() => void onboardingBridge.reopen()}
        onHelp={() => setShowHelp(true)}
      />

      <main className="main">
        {screen === 'courses' && (
          <CoursesPage
            filter={filter}
            dueCount={dueCount}
            onOpenCourse={(course) => openCourse(course.id)}
            onOpenConfig={(course) => openConfig(course.id)}
            onNewCourse={() => openNewCourse(filter === 'offline' ? 'offline' : 'online')}
            onOpenGuide={() => void onboardingBridge.reopen()}
            onToast={setToast}
          />
        )}

        {screen === 'recognition' && selectedCourse && (
          <RecognitionPage
            key={selectedCourse.id}
            course={selectedCourse}
            onBack={() => navigate('courses', 'all')}
            onOpenConfig={() => openConfig(selectedCourse.id)}
            onOpenOverlaySettings={() => setShowOverlayPanel(true)}
            onGoReview={() => navigate('review')}
            onOpenChapterSummary={(draft) => openChapterSummary(selectedCourse.id, draft)}
            onSelectWord={(word) => void selectWord(word)}
            onToast={setToast}
          />
        )}

        {screen === 'chapter-summary' && selectedCourse && summaryDraft && (
          <ChapterSummaryPage
            key={selectedCourse.id}
            course={selectedCourse}
            draft={summaryDraft}
            onBack={backFromChapterSummary}
            onSaved={(_chapterId, chapterName) => {
              setSummaryDraft(null)
              setScreen('recognition')
              setToast(`总结已保存到「${chapterName}」`)
            }}
            onToast={setToast}
          />
        )}

        {screen === 'config' && selectedCourse && (
          <ConfigPage
            course={selectedCourse}
            onBack={() => {
              if (screen === 'config') setScreen('recognition')
              else navigate('courses', 'all')
            }}
            onStartRecognition={() => setScreen('recognition')}
            onToast={setToast}
          />
        )}

        {screen === 'review' && (
          <ReviewPage
            onBack={() => navigate('courses', 'all')}
            onSelectWord={(word) => void selectWord(word)}
            onToast={setToast}
          />
        )}

        {screen === 'vocabulary' && (
          <VocabPage
            onSelectWord={(word) => void selectWord(word)}
            onToast={setToast}
          />
        )}

        {screen === 'exports' && (
          <ExportsPage
            onBack={() => navigate('courses', 'all')}
            onGoCourses={() => navigate('courses', 'all')}
            onToast={setToast}
          />
        )}

        {isLoading && courses.length === 0 && (
          <p className="page-sub" style={{ padding: 40 }}>连接本地服务中…</p>
        )}
      </main>

      {showNewCourse && (
        <NewCourseModal
          initialType={newCourseType}
          onClose={() => setShowNewCourse(false)}
          onCreate={handleCreate}
        />
      )}

      <WordPanelDrawer
        word={selectedWord}
        course={selectedCourse}
        onClose={closeWordPanel}
        onCollected={refreshDue}
        onGoReview={() => {
          closeWordPanel()
          navigate('review')
        }}
        onToast={setToast}
      />

      <Toast message={toast} />

      {showOverlayPanel && (
        <Suspense fallback={null}>
          <OverlayControlPanel
            onClose={() => setShowOverlayPanel(false)}
            showHighContrast
          />
        </Suspense>
      )}

      {showHelp && (
        <Suspense fallback={null}>
          <HelpAboutPanel onClose={() => setShowHelp(false)} />
        </Suspense>
      )}
    </div>
  )
}
