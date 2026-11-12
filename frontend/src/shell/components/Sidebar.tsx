import {
  BookOpen,
  ChevronDown,
  Download,
  GraduationCap,
  Headphones,
  HelpCircle,
  ListChecks,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Settings,
} from 'lucide-react'
import { IconButton } from '../../components/ui/IconButton'

/**
 * 侧栏（自李梓萌原型 components.jsx 的 Sidebar 原样转写）
 *
 * 接真实数据的部分：
 * · counts / dueCount / wordCount 来自 store（原型是 data.js 计数）
 * · 「导出记录」→ 交给页面层（生词本导出 CSV / 字幕导出 SRT 已有真实端点）
 * · 「设置 / 帮助」→ 打开帮助面板（主线 HelpAboutPanel 已有）
 * · 底部水印「界面初稿 v1.1」→ 版本号（成品红线：水印不得进成品）
 */

function LogoMark({ size = 34 }: { size?: number }) {
  return (
    <div className="logo-mark" style={{ width: size, height: size }}>
      <Headphones size={size * 0.5} strokeWidth={2.1} />
    </div>
  )
}

export type Screen = 'courses' | 'recognition' | 'config' | 'review' | 'vocabulary'

interface SidebarProps {
  screen: Screen
  activeFilter: 'all' | 'online' | 'offline' | null
  counts: { all: number; online: number; offline: number }
  dueCount: number
  wordCount: number
  version: string
  collapsed: boolean
  onToggleCollapse: () => void
  coursesExpanded: boolean
  onToggleCourses: () => void
  onNavigate: (screen: Screen, filter?: 'all' | 'online' | 'offline') => void
  onNewCourse: () => void
  onExport: () => void
  onSettings: () => void
  onHelp: () => void
}

export function Sidebar({
  screen,
  activeFilter,
  counts,
  dueCount,
  wordCount,
  version,
  collapsed,
  onToggleCollapse,
  coursesExpanded,
  onToggleCourses,
  onNavigate,
  onNewCourse,
  onExport,
  onSettings,
  onHelp,
}: SidebarProps) {
  const isCourses = screen === 'courses'

  return (
    <aside className={`sidebar ${collapsed ? 'collapsed' : ''}`}>
      <div className="sidebar-top">
        <div className="brand">
          <LogoMark size={collapsed ? 30 : 34} />
          {!collapsed ? (
            <div className="brand-copy">
              <div className="brand-name">听课伴侣</div>
              <div className="brand-sub">实时字幕 · 交互学词</div>
            </div>
          ) : null}
        </div>
        <IconButton
          label={collapsed ? '展开导航' : '收起导航'}
          className="sidebar-toggle"
          onClick={onToggleCollapse}
        >
          {collapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
        </IconButton>
      </div>

      <nav className="nav">
        {!collapsed ? <div className="nav-label">课程</div> : null}

        <div
          className={`nav-item ${isCourses && activeFilter === 'all' ? 'active' : ''}`}
          role="button"
          tabIndex={0}
          title="全部课程"
          onClick={() => onNavigate('courses', 'all')}
          onKeyDown={(event) => {
            if (event.key === 'Enter') onNavigate('courses', 'all')
          }}
        >
          <BookOpen size={16} />
          {!collapsed ? <span className="nav-text">全部课程</span> : null}
          {!collapsed ? <span className="nav-count">{counts.all}</span> : null}
          {!collapsed ? (
            <button
              type="button"
              className="nav-chevron"
              aria-label={coursesExpanded ? '收起线上线下列表' : '展开线上线下列表'}
              onClick={(event) => {
                event.stopPropagation()
                onToggleCourses()
              }}
            >
              <ChevronDown size={14} className={coursesExpanded ? 'open' : ''} />
            </button>
          ) : null}
        </div>

        {!collapsed && coursesExpanded ? (
          <div className="nav-sub">
            <button
              type="button"
              className={`nav-sub-item ${isCourses && activeFilter === 'online' ? 'active' : ''}`}
              onClick={() => onNavigate('courses', 'online')}
            >
              <span className="dot purple" />
              <span>线上网课</span>
              <span className="nav-sub-count">{counts.online}</span>
            </button>
            <button
              type="button"
              className={`nav-sub-item ${isCourses && activeFilter === 'offline' ? 'active' : ''}`}
              onClick={() => onNavigate('courses', 'offline')}
            >
              <span className="dot cyan" />
              <span>线下课堂</span>
              <span className="nav-sub-count">{counts.offline}</span>
            </button>
          </div>
        ) : null}

        {!collapsed ? <div className="nav-label">学习</div> : null}
        <button
          type="button"
          className={`nav-item ${screen === 'review' ? 'active' : ''}`}
          onClick={() => onNavigate('review')}
          title="今日复习"
        >
          <ListChecks size={16} />
          {!collapsed ? <span className="nav-text">今日复习</span> : null}
          {!collapsed ? (
            <span className={`nav-count ${dueCount > 0 ? 'due' : ''}`}>{dueCount}</span>
          ) : null}
        </button>
        <button
          type="button"
          className={`nav-item ${screen === 'vocabulary' ? 'active' : ''}`}
          onClick={() => onNavigate('vocabulary')}
          title="生词本"
        >
          <GraduationCap size={16} />
          {!collapsed ? <span className="nav-text">生词本</span> : null}
          {!collapsed ? <span className="nav-count">{wordCount}</span> : null}
        </button>
        <button type="button" className="nav-item" onClick={onExport} title="导出记录">
          <Download size={16} />
          {!collapsed ? <span className="nav-text">导出记录</span> : null}
        </button>

        {!collapsed ? <div className="nav-label">系统</div> : null}
        <button type="button" className="nav-item" onClick={onSettings} title="设置">
          <Settings size={16} />
          {!collapsed ? <span className="nav-text">设置</span> : null}
        </button>
        <button type="button" className="nav-item" onClick={onHelp} title="帮助与关于">
          <HelpCircle size={16} />
          {!collapsed ? <span className="nav-text">帮助与关于</span> : null}
        </button>
      </nav>

      <div className="sidebar-bottom">
        <button type="button" className="new-course-btn" onClick={onNewCourse} title="新建课程">
          <Plus size={16} />
          {!collapsed ? <span>新建课程</span> : null}
        </button>
        {!collapsed ? (
          <div className="sidebar-meta">
            <span>听课伴侣 · 桌面版</span>
            <span>v{version}</span>
          </div>
        ) : null}
      </div>
    </aside>
  )
}
