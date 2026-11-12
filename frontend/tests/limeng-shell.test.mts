/**
 * 李梓萌界面骨架（shell/）测试 —— 2026-11-12「后端和功能向她的前端靠」重构
 *
 * 本次重构的边界约定（见 shell/limeng.css 头注释）：
 *   · 她的 JSX 结构原样转写为 TSX，样式 limeng.css 原样搬入（token 别名桥）；
 *   · 所有假数据（data.js）换成真实后端 API；
 *   · 旧主视图不再挂载，但文件保留（独立窗口与既有测试仍引用）。
 *
 * 本组测试守卫三条红线：
 *   ① 水印「界面初稿」不得进成品（原型红线，验收 = 全量 grep 为 0）；
 *   ② App.tsx 主视图必须挂 Shell（旧视图不得回流）；
 *   ③ shell 页面组件 SSR 渲染不炸（结构冒烟）。
 */

import test from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { readFileSync, existsSync, readdirSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const SRC = join(HERE, '..', 'src')

/** 递归收集目录下全部文件（跳过 node_modules） */
function walk(dir: string): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) out.push(...walk(p))
    else out.push(p)
  }
  return out
}

function stripComments(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1')
}

test('红线：shell/ 与打包入口不得出现「界面初稿」水印', () => {
  const targets = [
    ...walk(join(SRC, 'shell')),
    join(SRC, 'App.tsx'),
    join(SRC, 'main.tsx'),
  ]
  for (const file of targets) {
    const text = readFileSync(file, 'utf-8')
    const code = stripComments(text)
    assert.equal(
      code.includes('界面初稿'),
      false,
      `${file} 出现「界面初稿」水印 —— 成品红线（样张可标设计稿，成品界面不得带水印）`,
    )
  }
})

test('主视图挂载 Shell（旧视图不得回流到 App.tsx）', () => {
  const app = readFileSync(join(SRC, 'App.tsx'), 'utf-8')
  assert.ok(app.includes("from './shell/Shell'"), 'App.tsx 必须挂载 shell/Shell')
  // 旧主视图从入口移除：这些 import 若回流说明旧界面又接管了主窗口
  for (const old of ["from './pages/CourseList'", "from './pages/CourseConfig'", "from './pages/SubtitleOverlay'"]) {
    assert.equal(app.includes(old), false, `App.tsx 不应再直接挂载旧页面（${old}）`)
  }
})

test('limeng.css 存在且 token 走别名桥（不引入第二套色值）', () => {
  const css = readFileSync(join(SRC, 'shell', 'limeng.css'), 'utf-8')
  const rootBlock = css.slice(0, css.indexOf('}') + 1)
  // 别名桥：她的 token 名必须映射到主线 token（var(--bg-primary) 等），
  // 而不是重新定义一份色值 —— 色值唯一真理源在 global.css
  assert.ok(rootBlock.includes('--bg: var(--bg-primary)'), '她的 --bg 必须别名到主线 --bg-primary')
  assert.ok(rootBlock.includes('--purple: var(--accent)'), '她的 --purple 必须别名到主线 --accent')
  assert.ok(rootBlock.includes('--cyan: var(--accent-offline)'), '她的 --cyan 必须别名到主线 --accent-offline')
  // 红线：root 块里不得出现任何硬编码 hex（那是第二套色值）
  assert.equal(/#[0-9a-fA-F]{3,8}\b/.test(rootBlock), false, 'token 别名桥里不得出现硬编码 hex')
})

test('页面组件 SSR 渲染冒烟（不炸 + 关键结构存在）', async () => {
  // 课程列表页（空 store 初始态：fetch 不执行 —— renderToStaticMarkup 不跑副作用）
  const { CoursesPage } = await import('../src/shell/pages/CoursesPage')
  const coursesHtml = renderToStaticMarkup(
    createElement(CoursesPage, {
      filter: 'all',
      onOpenCourse: () => {},
      onOpenConfig: () => {},
      onNewCourse: () => {},
      dueCount: 0,
      onToast: () => {},
    }),
  )
  assert.ok(coursesHtml.includes('新建课程'), '课程页应含「新建课程」按钮')

  // 新建课程弹窗（她的两模式卡）
  const { NewCourseModal } = await import('../src/shell/pages/CoursesPage')
  const modalHtml = renderToStaticMarkup(
    createElement(NewCourseModal, {
      initialType: 'online',
      onClose: () => {},
      onCreate: async () => {},
    }),
  )
  assert.ok(modalHtml.includes('线上网课') && modalHtml.includes('线下课堂'), '新建弹窗应有两张模式卡')

  // 侧栏
  const { Sidebar } = await import('../src/shell/components/Sidebar')
  const sidebarHtml = renderToStaticMarkup(
    createElement(Sidebar, {
      screen: 'courses',
      activeFilter: 'all',
      counts: { all: 2, online: 1, offline: 1 },
      dueCount: 3,
      wordCount: 8,
      version: '3.9.0',
      collapsed: false,
      onToggleCollapse: () => {},
      coursesExpanded: true,
      onToggleCourses: () => {},
      onNavigate: () => {},
      onNewCourse: () => {},
      onExport: () => {},
      onSettings: () => {},
      onHelp: () => {},
    }),
  )
  assert.ok(sidebarHtml.includes('听课伴侣'), '侧栏应有品牌名')
  assert.ok(sidebarHtml.includes('v3.9.0'), '侧栏应显示版本号（替代原型水印）')

  // 点词抽屉（关态不渲染）
  const { WordPanelDrawer } = await import('../src/shell/components/WordPanelDrawer')
  const closed = renderToStaticMarkup(
    createElement(WordPanelDrawer, {
      word: null,
      course: null,
      onClose: () => {},
      onCollected: () => {},
      onGoReview: () => {},
      onToast: () => {},
    }),
  )
  assert.equal(closed, '', 'word 为 null 时抽屉不渲染')
})

test('识别页双场景分流的代码级守卫（线上 web-frame / 线下 transcript）', async () => {
  const { RecognitionPage } = await import('../src/shell/pages/RecognitionPage')
  const source = readFileSync(join(SRC, 'shell', 'pages', 'RecognitionPage.tsx'), 'utf-8')
  // 识别启停必须走真实管道（connectASR / disconnectASR），不许再是 setTimeout 假试采
  assert.ok(source.includes('connectASR'), '识别页必须调用 connectASR 启动真实管道')
  assert.ok(source.includes('disconnectASR'), '识别页必须调用 disconnectASR 停管道')
  // 禁的是「3 秒后假装试采完成」的原型假交互；WebFrame 的 10s 加载超时提示是真实功能，不在禁止之列
  assert.ok(!/setTimeout\((\s|\S)*?,\s*3000\)/.test(source), '识别页不得残留原型的 3 秒 setTimeout 假试采')
  // 课堂总结与笔记本必须走真实端点
  assert.ok(source.includes('/summary'), '课堂总结必须调 POST /summary')
  assert.ok(source.includes('/note'), '笔记本必须调 note 端点')
  assert.ok(existsSync(join(SRC, 'shell', 'pages', 'RecognitionPage.tsx')), 'RecognitionPage 存在')
})

test('配置页：试采必须走真实端点（不得再用 3 秒 setTimeout 假交互）', async () => {
  const source = readFileSync(join(SRC, 'shell', 'pages', 'ConfigPage.tsx'), 'utf-8')
  assert.ok(source.includes('/api/audio/mic-test'), '试采必须调 POST /api/audio/mic-test')
  assert.ok(!/setMicVerdict\('电平良好'\)/.test(source), '不得写死「电平良好」判定')
})
