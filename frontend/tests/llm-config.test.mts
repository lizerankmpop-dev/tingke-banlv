import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  mergeLLMConfig, cloudReady, defaultLLMConfig,
  loadLocalLLMConfig, type LLMConfig,
} from '../src/lib/llmConfigApi'

test('Node 环境无 localStorage 时读偏好返回 null，不抛', () => {
  assert.equal(loadLocalLLMConfig(), null)
})

test('服务端脱敏配置与本地明文密钥合并', () => {
  const server = { provider: 'cloud', cloudApiBase: 'https://api.x/v1', cloudApiKeySet: true, cloudModel: 'm' }
  const local = { cloudApiKey: 'sk-123' }
  const merged = mergeLLMConfig(server, local)
  assert.equal(merged.provider, 'cloud')
  assert.equal(merged.cloudApiKey, 'sk-123', '密钥必须从本地补回（服务端不回传）')
  assert.equal(merged.cloudApiKeySet, true)
})

test('cloudReady 只在三要素齐全时为真', () => {
  const base: LLMConfig = { ...defaultLLMConfig, provider: 'cloud' }
  assert.equal(cloudReady(base), false)
  assert.equal(cloudReady({ ...base, cloudApiBase: 'https://x', cloudApiKeySet: true, cloudModel: 'm' }), true)
  // 已有 keySet（服务端已保存）时，本地 key 留空也算就绪
  assert.equal(cloudReady({ ...base, cloudApiBase: 'https://x', cloudApiKeySet: true, cloudModel: 'm', cloudApiKey: '' }), true)
})

test('D4 前端已接线配置卡与启动恢复', () => {
  const configPage = readFileSync(new URL('../src/shell/pages/ConfigPage.tsx', import.meta.url), 'utf8')
  const shell = readFileSync(new URL('../src/shell/Shell.tsx', import.meta.url), 'utf8')
  assert.match(configPage, /<LLMConfigCard/)
  assert.match(shell, /putLLMConfig\(merged\)/, '启动时必须把本地配置恢复到后端运行时')
  assert.match(shell, /if \(!local\) return/, '没有本地偏好时不能 PUT，避免覆盖 env 配置')
})
