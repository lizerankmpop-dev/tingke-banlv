import { HashRouter, Routes, Route } from 'react-router-dom'
import Shell from './shell/Shell'
import DictionaryWindow from './pages/DictionaryWindow'
import OverlayWindow from './pages/OverlayWindow'
import OnboardingWizard from './pages/OnboardingWizard'
import ReviewReminder from './components/ReviewReminder'

/**
 * 主应用入口：Hash 路由
 *
 * 2026-11-12 起主界面 = 李梓萌界面骨架（shell/Shell.tsx）：
 * 课程列表 / 识别（线上网页框·线下转写）/ 配置 / 复习 / 生词本五屏 + 可收起侧栏。
 * 旧主视图（CourseList / CourseConfig / SubtitleOverlay / ReviewPage）不再挂载，
 * 文件保留供独立窗口（悬浮窗 / 向导 / 词典窗）与既有测试引用。
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
        {/* 其余一切路径（含 /）都进主应用（新界面骨架 + 复习提醒） */}
        <Route
          path="*"
          element={
            <>
              <Shell />
              <ReviewReminder />
            </>
          }
        />
      </Routes>
    </HashRouter>
  )
}
