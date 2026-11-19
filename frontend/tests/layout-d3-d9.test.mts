import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8')
const config = read('src/shell/pages/ConfigPage.tsx')
const panel = read('src/components/OverlayControlPanel.tsx')
const limeng = read('src/shell/limeng.css')
const globalCss = read('src/styles/global.css')

test('D3 配置页有优先级提示且次级设置分组两列', () => {
  assert.match(config, /config-priority-note/)
  assert.match(config, /<strong>配置顺序<\/strong>/)
  assert.match(config, /config-secondary-grid/)
  assert.match(limeng, /\.config-secondary-grid \{[\s\S]*grid-template-columns: repeat\(2/)
})

test('D3 文字密集处字号放大、字段名与结论加粗', () => {
  assert.match(limeng, /\.config-card \.field > span \{[\s\S]*font-weight: 600 !important/)
  assert.match(limeng, /\.config-card \.card-head-copy h2 \{[\s\S]*font-size: var\(--fs-body\) !important/)
  assert.match(limeng, /\.config-card \.fineprint[\s\S]*font-size: var\(--fs-caption\) !important/)
})

test('D9 说明文字收进每行右上角问号，点击展开', () => {
  assert.match(panel, /function HelpRow/)
  assert.match(panel, /className="row-help"/)
  assert.match(panel, /aria-expanded=\{open\}/)
  assert.match(panel, /aria-label=\{`\$\{label\}说明`\}/)
  assert.match(panel, /className="row-help-detail"/)
  assert.doesNotMatch(panel, /style=\{styles\.hint\}/)
  assert.match(globalCss, /\.row-help \{[\s\S]*position: absolute/)
})
