import { HashRouter, Routes, Route } from 'react-router-dom'
import CourseList from './pages/CourseList'
import CourseConfig from './pages/CourseConfig'
import SubtitleOverlay from './pages/SubtitleOverlay'
import DictionaryWindow from './pages/DictionaryWindow'
import OverlayWindow from './pages/OverlayWindow'
import ReviewPage from './pages/ReviewPage'
import OnboardingWizard from './pages/OnboardingWizard'
import ReviewReminder from './components/ReviewReminder'
import { useAppStore } from './stores/useAppStore'
import { useHostHeartbeat } from './hooks/useHostHeartbeat'

/**
 * 主应用视图切换
 *
 * 保留原有的 store 驱动方式（useAppStore().view），不改成路由跳转：
 * 4 个页面之间大量依赖「带着 courseId 切视图」的隐式状态传递，
 * 一次性改写成路由参数会牵动全部页面且难以回归验证。
 * 路由只用来区分「两个独立入口」，不承担应用内导航职责。
 */
function MainApp() {
  const { view } = useAppStore()

  // 存活心跳：主窗口崩溃时悬浮窗要能明确提示，而不是永远停在最后一句字幕
  useHostHeartbeat()

  switch (view) {
    case 'course-list':
      return <CourseList />
    case 'course-config':
      return <CourseConfig />
    case 'subtitle-overlay':
      return <SubtitleOverlay />
    case 'dictionary':
      return <DictionaryWindow />
    case 'review':
      return <ReviewPage />
    default:
      return <CourseList />
  }
}

/** 主应用外壳：所有视图共用（复习提醒与视图无关） */
function MainShell() {
  return (
    <>
      <MainApp />
      <ReviewReminder />
    </>
  )
}

/**
 * 应用入口：Hash 路由
 *
 * 为什么必须是 hash 而不是 BrowserRouter：
 *   打包后页面由后端同源托管（http://127.0.0.1:<动态端口>），直接打开
 *   /overlay 这种路径既没有对应的静态文件、也没配 history 回退，
 *   只有 `#/overlay` 能在不改后端路由的前提下稳定直达 —— 悬浮窗
 *   正是要靠 `loadURL('http://127.0.0.1:PORT/#/overlay')` 打开。
 * 配合 vite.config.ts 的 base:'./'，相对资源路径在任意 hash 下都能解析。
 */
export default function App() {
  return (
    <HashRouter>
      <Routes>
        {/* 桌面悬浮窗：纯字幕展示，无控制栏 */}
        <Route path="/overlay" element={<OverlayWindow />} />
        {/* 首启向导（M3）：独立 BrowserWindow 打开，必须走自己的路由 ——
            否则向导窗口里渲染的是整个主应用（一块不透明的完整界面），
            表现为「窗口打开了、加载也成功了，但内容完全不对」。 */}
        <Route path="/onboarding" element={<OnboardingWizard />} />
        {/* 其余一切路径（含 /）都进主应用 */}
        <Route path="*" element={<MainShell />} />
      </Routes>
    </HashRouter>
  )
}
