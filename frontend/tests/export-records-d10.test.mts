import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8')
const shell = read('src/shell/Shell.tsx')
const sidebar = read('src/shell/components/Sidebar.tsx')
const page = read('src/shell/pages/ExportsPage.tsx')
const api = read('src/lib/exportRecords.ts')
const subtitlePanel = read('src/shell/components/SubtitleExportPanel.tsx')
const vocab = read('src/shell/pages/VocabPage.tsx')
const routes = read('../backend/api/routes.py')
const css = read('src/shell/limeng.css')

test('D10 侧栏导出记录可点进独立页面', () => {
  assert.match(shell, /onExport=\{\(\) => navigate\('exports'\)\}/)
  assert.match(shell, /screen === 'exports'/)
  assert.match(sidebar, /\| 'exports'/)
  assert.match(shell, /<ExportsPage/)
})

test('D10 空态明确显示没有记录', () => {
  assert.match(page, /records\.length === 0/)
  assert.match(page, /没有记录/)
  assert.match(css, /\.export-empty/)
})

test('D10 下载成功后才登记，且登记失败不影响下载', () => {
  assert.match(subtitlePanel, /void recordExport\([\s\S]*\.catch\(\(\) => \{\}\)/)
  assert.match(vocab, /void recordExport\([\s\S]*\.catch\(\(\) => \{\}\)/)
  assert.match(api, /export async function recordExport/)
  assert.match(routes, /@router\.post\("\/exports"\)/)
  assert.match(routes, /@router\.get\("\/exports"\)/)
})
