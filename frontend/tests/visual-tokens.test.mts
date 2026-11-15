/**
 * 视觉改造 v1（李梓萌 2026-11-15）守卫
 *
 * 合并她交付的三张表 + 4 个 CSS 后，锁住五条不能回流的红线：
 *   ① 新主色 token（墨靛 / 黛青）与字号 scale 必须在 global.css 定义；
 *   ② 三个 CSS 不得残留旧紫 #8b84e8 家族 / 旧青 #2dd4cf 家族的硬编码；
 *   ③ 不得再出现 < 11px 的裸 font-size（原稿有 8 / 8.5 / 9.5 / 10.5px）；
 *   ④ 字幕核心类必须映射到 >= 16px 的 token；
 *   ⑤ 高对比度开关与视频填满规则已经落地，limeng.css 的 :root 仍只做别名桥。
 *
 * 注意：global.css 的 :root 是 token 定义处，允许出现新 hex；
 * 本守卫只禁止「旧色」和「组件里写死色值」。
 */

import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const SRC = join(HERE, '..', 'src')
const GLOBAL = join(SRC, 'styles', 'global.css')
const LIMENG = join(SRC, 'shell', 'limeng.css')
const UI = join(SRC, 'components', 'ui', 'ui.css')

const globalCss = readFileSync(GLOBAL, 'utf8')
const limengCss = readFileSync(LIMENG, 'utf8')
const uiCss = readFileSync(UI, 'utf8')
const allCss = [['global.css', globalCss], ['limeng.css', limengCss], ['ui.css', uiCss]] as const

const OLD_COLORS = [
  '#8b84e8', '#7b74db', '#bdb8f2', '#afa9ec', '#3c3489',
  '#2dd4cf', '#25c5c0', '#8ce9e5', '#7ff5f1', '#00cec9', '#006b68',
  'rgba(139,132,232,', 'rgba(139, 132, 232,',
  'rgba(45,212,207,', 'rgba(45, 212, 207,',
]

const FS_TOKENS = [
  '--fs-micro', '--fs-caption', '--fs-ui', '--fs-body',
  '--fs-subtitle', '--fs-subtitle-lg', '--fs-title', '--fs-display',
]

test('新主色 token 已在 global.css 定义（墨靛 / 黛青）', () => {
  assert.match(globalCss, /--accent\s*:\s*#5f6f96/i, '线上主色应为墨靛 #5F6F96')
  assert.match(globalCss, /--accent-offline\s*:\s*#5f8f84/i, '线下主色应为黛青 #5F8F84')
})

test('字号 scale 八个 token 全部定义', () => {
  for (const token of FS_TOKENS) {
    assert.match(globalCss, new RegExp(`${token}\\s*:`, 'i'), `缺少 ${token}`)
  }
  assert.match(globalCss, /--fs-subtitle\s*:\s*16px/i, '字幕正文下限必须是 16px')
  assert.match(globalCss, /--fs-micro\s*:\s*11px/i, '装饰性最小字号必须是 11px')
})

test('三个 CSS 不得残留旧紫 / 旧青硬编码', () => {
  const offenders: string[] = []
  for (const [name, css] of allCss) {
    const lower = css.toLowerCase()
    for (const needle of OLD_COLORS) {
      if (lower.includes(needle.toLowerCase())) offenders.push(`${name} 含 ${needle}`)
    }
  }
  assert.deepEqual(offenders, [], `旧色值回流：\n${offenders.join('\n')}`)
})

test('三个 CSS 不得出现 < 11px 的裸 font-size', () => {
  const offenders: string[] = []
  for (const [name, css] of allCss) {
    for (const m of css.matchAll(/font-size\s*:\s*(\d+(?:\.\d+)?)px/gi)) {
      if (Number(m[1]) < 11) offenders.push(`${name}: font-size: ${m[1]}px`)
    }
  }
  assert.deepEqual(offenders, [], `小字号回流：\n${offenders.join('\n')}`)
})

test('字幕核心类映射到 >= 16px 的字号 token', () => {
  assert.match(limengCss, /\.transcript-text\s*\{[^}]*--fs-subtitle/, '.transcript-text 应用 --fs-subtitle')
  assert.match(limengCss, /\.current-en\s*\{[^}]*--fs-subtitle-lg/, '.current-en 应用 --fs-subtitle-lg')
  assert.match(limengCss, /\.overlay-en\s*\{[^}]*--fs-subtitle/, '.overlay-en 应用 --fs-subtitle')
})

test('高对比度与视频填满规则已落地', () => {
  assert.match(limengCss, /html\[data-contrast="high"\]/, '缺高对比度触发选择器')
  assert.match(limengCss, /\.web-view-guest/, '缺视频填满的 guest 样式')
  assert.match(limengCss, /\.recognition-page\.web-fill/, '缺 web-fill 布局类')
})

test('limeng.css :root 仍只做 token 别名桥，不写死 hex', () => {
  const m = limengCss.match(/:root\s*\{([\s\S]*?)\}/)
  assert.ok(m, 'limeng.css 应有 :root')
  assert.match(m[1], /--purple\s*:\s*var\(--accent\)/)
  assert.match(m[1], /--cyan\s*:\s*var\(--accent-offline\)/)
  assert.doesNotMatch(m[1], /#[0-9a-f]{3,8}|rgba?\(/i, ':root 不得写死 hex/rgba')
})

test('ui.css 覆盖只引用字号 token，不新增硬编码色值', () => {
  assert.match(uiCss, /TINGKE_VISUAL_REFACTOR_V1_UI/, '缺视觉改造标记')
  assert.doesNotMatch(uiCss, /#[0-9a-f]{3,8}\b/i, 'ui.css 覆盖块不应写死 hex')
})
