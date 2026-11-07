import { readdirSync } from 'node:fs'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const TESTS_DIR = fileURLToPath(new URL('../tests', import.meta.url))
const TEST_FILE = /\.test\.(?:cjs|mjs|js|mts|ts)$/

function collect(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const fullPath = join(dir, entry.name)
    if (entry.isDirectory()) return collect(fullPath)
    return TEST_FILE.test(entry.name) ? [fullPath] : []
  })
}

const files = collect(TESTS_DIR).sort()

if (files.length === 0) {
  console.error(`[test] 未在 ${TESTS_DIR} 找到任何测试文件`)
  process.exit(1)
}

console.log(`[test] 共发现 ${files.length} 个测试文件：`)
for (const file of files) console.log(`[test]   ${file}`)

const nodeOptions = [process.env.NODE_OPTIONS, '--import tsx'].filter(Boolean).join(' ')
const result = spawnSync(process.execPath, ['--test', ...files], {
  stdio: 'inherit',
  env: { ...process.env, NODE_OPTIONS: nodeOptions },
})

if (result.error) {
  console.error(`[test] 启动测试进程失败：${result.error.message}`)
  process.exit(1)
}

process.exit(result.status ?? 1)