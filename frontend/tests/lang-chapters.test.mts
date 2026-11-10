import test from 'node:test'
import assert from 'node:assert/strict'

/**
 * 字幕语言选择 + 章节管理（feat/lang-chapters）前端单测。
 *
 * 不渲染组件（CourseConfig 内含多处 fetch effect 与全局 store，SSR 快照
 * 噪音大），聚焦三块纯逻辑：
 * 1. SUBTITLE_LANGS 取值与后端 normalize_subtitle_lang 的契约（both/orig/zh）；
 * 2. useSubtitleStore 的 applyTranslation 合并语义（id 命中/不命中/重复译文）；
 * 3. useAppStore 的 startListening 章节传参语义。
 */

// ============ 契约：字幕语言取值 ============
// 从源码文本提取 SUBTITLE_LANGS 的 value（避免为了常量把它 export 出去，
// 契约测试的意义就在于「后端改了合法值集合，这里立刻红」）

test('SUBTITLE_LANGS 取值与后端 both/orig/zh 契约一致', async () => {
  const { readFile } = await import('node:fs/promises')
  const src = await readFile(new URL('../src/pages/CourseConfig.tsx', import.meta.url), 'utf-8')
  const m = src.match(/const SUBTITLE_LANGS[^[]*\[([\s\S]*?)\]/)
  assert.ok(m, 'CourseConfig.tsx 应定义 SUBTITLE_LANGS')
  const values = [...m[1].matchAll(/value:\s*'([^']+)'/g)].map(x => x[1])
  assert.deepEqual([...values].sort(), ['both', 'orig', 'zh'])
  // 默认值必须是 both（历史行为：老课程无此字段回落 both）
  const def = src.match(/useState\(course\?\.subtitleLang \|\| '([^']+)'\)/)
  assert.ok(def, '应以 course.subtitleLang 初始化，老课程回落默认')
  assert.equal(def[1], 'both')
})

test('类型定义：SubtitleLine.translation 可选 + Course.subtitleLang 存在', async () => {
  const { readFile } = await import('node:fs/promises')
  const src = await readFile(new URL('../src/types/index.ts', import.meta.url), 'utf-8')
  assert.ok(/translation\?:\s*string/.test(src), 'SubtitleLine 应有可选 translation')
  assert.ok(/subtitleLang:\s*string/.test(src), 'Course 应有 subtitleLang')
  assert.ok(/subtitle_translation/.test(src), 'WSMessage 应声明 subtitle_translation 消息')
})

// ============ store：译文合并语义 ============

test('applyTranslation 按 id 合并译文；不命中与重复调用无害', async () => {
  // 动态 import store（zustand create 在 import 时执行，无需 DOM）
  const storeUrl = new URL('../src/stores/useSubtitleStore.ts', import.meta.url).href
  const { useSubtitleStore } = await import(storeUrl)

  const s0 = useSubtitleStore.getState()
  s0.addSubtitleLine({ id: 'sub_1', text: 'hello', words: [], start: 0, end: 10, isFinal: true })
  s0.addSubtitleLine({ id: 'sub_2', text: 'world', words: [], start: 10, end: 20, isFinal: true })

  // 命中：sub_1 得到译文
  useSubtitleStore.getState().applyTranslation('sub_1', '你好')
  let lines = useSubtitleStore.getState().subtitleLines
  assert.equal(lines.find((l: any) => l.id === 'sub_1')?.translation, '你好')

  // 重复同样的译文：不产生新数组语义问题（幂等）
  useSubtitleStore.getState().applyTranslation('sub_1', '你好')
  lines = useSubtitleStore.getState().subtitleLines
  assert.equal(lines.find((l: any) => l.id === 'sub_1')?.translation, '你好')
  assert.equal(lines.find((l: any) => l.id === 'sub_2')?.translation, undefined)

  // 不命中的 id（字幕已滚出/清空）：静默忽略
  useSubtitleStore.getState().applyTranslation('sub_99', '幽灵译文')
  lines = useSubtitleStore.getState().subtitleLines
  assert.equal(lines.length, 2)

  // 空参数防御
  useSubtitleStore.getState().applyTranslation('', 'x')
  useSubtitleStore.getState().applyTranslation('sub_1', '')
  lines = useSubtitleStore.getState().subtitleLines
  assert.equal(lines.find((l: any) => l.id === 'sub_1')?.translation, '你好')
})

// ============ app store：章节传参 ============

test('startListening 章节传参写入 activeChapterId/Name；无章节归空串', async () => {
  const { useAppStore } = await import(new URL('../src/stores/useAppStore.ts', import.meta.url).href)

  useAppStore.getState().startListening({ id: 'ch1', name: '第 3 讲' })
  let s = useAppStore.getState()
  assert.equal(s.view, 'subtitle-overlay')
  assert.equal(s.activeChapterId, 'ch1')
  assert.equal(s.activeChapterName, '第 3 讲')

  // 不传章节 = 不分章节（空串，不是 undefined/null）
  useAppStore.getState().startListening()
  s = useAppStore.getState()
  assert.equal(s.activeChapterId, '')
  assert.equal(s.activeChapterName, '')
})
