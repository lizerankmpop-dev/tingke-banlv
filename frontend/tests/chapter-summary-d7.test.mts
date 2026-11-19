import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8')
const page = read('src/shell/pages/RecognitionPage.tsx')
const shell = read('src/shell/Shell.tsx')
const savePage = read('src/shell/pages/ChapterSummaryPage.tsx')
const api = read('src/lib/chapterApi.ts')
const routes = read('../backend/api/routes.py')
const css = read('src/shell/limeng.css')

test('D7 后端有章节总结保存路由且做归属校验', () => {
  assert.match(routes, /@router\.put\("\/courses\/\{course_id\}\/chapters\/\{chapter_id\}\/summary"\)/)
  assert.match(routes, /update_chapter_summary\(chapter_id, course_id, summary/)
})

test('D7 结束并总结会生成草稿并跳转，不再只刷侧栏', () => {
  assert.match(page, /await generateSummary\(course\.id\)/)
  assert.match(page, /onOpenChapterSummary\(draft\)/)
  assert.doesNotMatch(page, /setSummaryRequest/)
  assert.match(page, /setHistoryRefresh\(value => value \+ 1\)/, '历史刷新要单独触发')
})

test('D7 主壳层注册 chapter-summary 屏并保存草稿', () => {
  assert.match(shell, /'chapter-summary'/)
  assert.match(shell, /setSummaryDraft\(draft\)/)
  assert.match(shell, /<ChapterSummaryPage/)
})

test('D7 保存页支持新建章节 / 已有章节与手写兜底', () => {
  assert.match(savePage, /createChapter\(course\.id, newName\.trim\(\)\)/)
  assert.match(savePage, /saveChapterSummary\(course\.id, targetId, summary\.trim\(\)\)/)
  assert.match(savePage, /AI 摘要暂不可用/)
  assert.match(savePage, /章节总结内容/)
})

test('D7 下次进课显示章节目录并询问新建章节', () => {
  assert.match(page, /listChapters\(course\.id, controller\.signal\)/)
  assert.match(page, /resumeChapters\.map/)
  assert.match(page, /为这节课新建章节/)
  assert.match(page, /createResumeChapter/)
  assert.match(css, /\.resume-chapter-banner/)
})
