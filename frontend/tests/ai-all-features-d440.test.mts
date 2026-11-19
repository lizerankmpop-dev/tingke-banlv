import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8')
const shell = read('src/shell/Shell.tsx')
const page = read('src/shell/pages/RecognitionPage.tsx')
const drawer = read('src/shell/components/AIChatDrawer.tsx')
const aiApi = read('src/lib/aiApi.ts')
const wordPanel = read('src/shell/components/WordPanelDrawer.tsx')
const css = read('src/shell/limeng.css')

test('主骨架接入 AI 答疑抽屉（后端 /api/ai/ask 不再是无入口的死端点）', () => {
  assert.match(shell, /import \{ AIChatDrawer \}/)
  assert.match(shell, /showAIChat/)
  assert.match(shell, /<AIChatDrawer/)
  assert.match(page, /onOpenAIChat/)
  assert.match(page, /AI 答疑/)
})

test('AI 答疑封装调用真实状态与问答端点，并带多轮 sessionId', () => {
  assert.match(aiApi, /\/api\/ai\/status/)
  assert.match(aiApi, /\/api\/ai\/ask/)
  assert.match(aiApi, /sessionId/)
  assert.match(aiApi, /coreUnaffected/)
})

test('答疑抽屉把真实不可用原因显示给用户，不硬编码误导文案', () => {
  assert.match(drawer, /getAIStatus/)
  assert.match(drawer, /status\?\.reason/)
  assert.match(drawer, /核心功能不受影响|不受影响/)
})

test('点词 AI 讲解改用后端返回 message，不再写死 Ollama 未启动', () => {
  assert.match(wordPanel, /data\.message/)
  assert.match(wordPanel, /aiMessage/)
  assert.doesNotMatch(wordPanel, /AI 讲解服务不可用：请确认 Ollama 已启动/)
})

test('AI 答疑抽屉有配套样式且用主题 token', () => {
  assert.match(css, /\.ai-chat-drawer/)
  assert.match(css, /\.ai-chat-input-row/)
  assert.match(css, /var\(--accent\)/)
})
