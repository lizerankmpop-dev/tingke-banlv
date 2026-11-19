/**
 * GUI 用户测试（真实 Electron 窗口 + CDP）
 *
 * 与 verify-all-features.py 的区别：
 *   - 那个脚本打 HTTP API，证明「后端业务链路」没坏；
 *   - 这个脚本连到**已经打开的听课伴侣窗口**，用 DOM click / input 走真实页面，
 *     证明侧栏、弹窗、页面跳转、模型下拉、章节输入、AI 抽屉这些 GUI 入口真的可用。
 *
 * 用法：
 *   node scripts/gui-user-test.cjs --port 9333
 *   TINGKEBANLV_GUI_PORT=9333 node scripts/gui-user-test.cjs
 *
 * 前置：应用已启动且带 TINGKEBANLV_DEBUG_PORT=<port>。脚本不会关闭应用。
 * 退出码：0 = 全部通过；1 = 有断言失败；2 = 连不上主窗口。
 */

const http = require('http')
const fs = require('fs')
const path = require('path')

// 版本号必须与被测应用同源：写死字面量会在升版后假红（本项目踩过多次）
const APP_VERSION = JSON.parse(
  fs.readFileSync(path.join(__dirname, '..', 'package.json'), 'utf8')
).version

const PORT = (() => {
  const i = process.argv.indexOf('--port')
  if (i >= 0 && process.argv[i + 1]) return Number(process.argv[i + 1])
  return Number(process.env.TINGKEBANLV_GUI_PORT || 9222)
})()
const COURSE_NAME = 'GUI 用户测试课程'
const checks = []

function check(name, ok, detail = '') {
  checks.push({ name, ok: !!ok, detail })
  console.log(`${ok ? '  ok  ' : ' FAIL '} ${name}${detail ? ` — ${detail}` : ''}`)
}

function q(value) {
  return JSON.stringify(String(value))
}

function getJson(url) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, (res) => {
      let body = ''
      res.setEncoding('utf8')
      res.on('data', (c) => (body += c))
      res.on('end', () => {
        try {
          resolve(JSON.parse(body))
        } catch (err) {
          reject(new Error(`响应不是 JSON: ${body.slice(0, 120)}`))
        }
      })
    })
    req.on('error', reject)
    req.setTimeout(4000, () => req.destroy(new Error('timeout')))
  })
}

function evaluate(target, expression, timeoutMs = 12000) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(target.webSocketDebuggerUrl)
    const timer = setTimeout(() => {
      try { ws.close() } catch (_) { /* 忽略 */ }
      reject(new Error('CDP 求值超时'))
    }, timeoutMs)
    const done = (fn) => {
      clearTimeout(timer)
      try { ws.close() } catch (_) { /* 忽略 */ }
      fn()
    }
    ws.addEventListener('open', () => {
      ws.send(JSON.stringify({
        id: 1,
        method: 'Runtime.evaluate',
        params: { expression, returnByValue: true, awaitPromise: true },
      }))
    })
    ws.addEventListener('message', (ev) => {
      let msg
      try { msg = JSON.parse(ev.data) } catch (_) { return }
      if (msg.id !== 1) return
      if (msg.error) return done(() => reject(new Error(msg.error.message || 'CDP 错误')))
      const res = msg.result || {}
      if (res.exceptionDetails) return done(() => reject(new Error(res.exceptionDetails.text || '页面内异常')))
      done(() => resolve(res.result ? res.result.value : undefined))
    })
    ws.addEventListener('error', () => done(() => reject(new Error('CDP 连接失败'))))
  })
}

function send(target, method, params = {}, timeoutMs = 8000) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(target.webSocketDebuggerUrl)
    const timer = setTimeout(() => {
      try { ws.close() } catch (_) { /* 忽略 */ }
      reject(new Error('CDP 命令超时：' + method))
    }, timeoutMs)
    const done = (fn) => {
      clearTimeout(timer)
      try { ws.close() } catch (_) { /* 忽略 */ }
      fn()
    }
    ws.addEventListener('open', () => ws.send(JSON.stringify({ id: 1, method, params })))
    ws.addEventListener('message', (ev) => {
      let msg
      try { msg = JSON.parse(ev.data) } catch (_) { return }
      if (msg.id !== 1) return
      if (msg.error) return done(() => reject(new Error(msg.error.message || 'CDP 错误')))
      done(() => resolve(msg.result || {}))
    })
    ws.addEventListener('error', () => done(() => reject(new Error('CDP 连接失败'))))
  })
}

async function waitFor(fn, timeoutMs = 12000, label = '条件') {
  const deadline = Date.now() + timeoutMs
  let last
  while (Date.now() < deadline) {
    try {
      last = await fn()
      if (last) return last
    } catch (_) { /* 下一轮重试 */ }
    await new Promise((r) => setTimeout(r, 200))
  }
  return last
}

// ===== 以下 helper 全部在真实页面 DOM 上操作，不是 API 调用 =====

function bodyText(target) {
  return evaluate(target, 'document.body ? (document.body.innerText || "") : ""')
}

function pageTitle(target) {
  return evaluate(
    target,
    'document.querySelector(".page-title")?.textContent || document.querySelector(".topbar-title")?.textContent || ""'
  )
}

function clickSelector(target, selector) {
  return evaluate(target, `(() => { const el = document.querySelector(${q(selector)}); if (!el) return false; el.click(); return true })()`)
}

function clickText(target, text, selector = 'button') {
  return evaluate(
    target,
    `(() => {
      const all = [...document.querySelectorAll(${q(selector)})]
      const el = all.find((e) => (e.textContent || '').replace(/\\s+/g, ' ').includes(${q(text)}))
      if (!el) return false
      el.click()
      return true
    })()`
  )
}

function setValue(target, selector, value) {
  return evaluate(
    target,
    `(() => {
      const el = document.querySelector(${q(selector)})
      if (!el) return false
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype
        : el.tagName === 'SELECT' ? HTMLSelectElement.prototype
        : HTMLInputElement.prototype
      const setter = Object.getOwnPropertyDescriptor(proto, 'value').set
      setter.call(el, ${q(value)})
      el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }))
      return true
    })()`
  )
}

function setPlaceholder(target, placeholder, value) {
  return evaluate(
    target,
    `(() => {
      const el = [...document.querySelectorAll('input,textarea')].find((e) => e.placeholder === ${q(placeholder)})
      if (!el) return false
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
      Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, ${q(value)})
      el.dispatchEvent(new Event('input', { bubbles: true }))
      return true
    })()`
  )
}

async function waitForText(target, text, timeoutMs = 12000) {
  return waitFor(async () => (await bodyText(target)).includes(text), timeoutMs, text)
}

async function waitForPageTitle(target, title, timeoutMs = 12000) {
  return waitFor(async () => (await pageTitle(target)) === title, timeoutMs, title)
}

async function main() {
  if (typeof WebSocket === 'undefined') {
    console.error('[gui] 需要 Node 22+（依赖内置 WebSocket）。当前 ' + process.version)
    process.exit(2)
  }

  let list
  try {
    list = await getJson(`http://127.0.0.1:${PORT}/json/list`)
  } catch (err) {
    console.error(`[gui] 连不上 CDP ${PORT}: ${err.message}`)
    process.exit(2)
  }
  const pages = (Array.isArray(list) ? list : []).filter((t) => t.type === 'page')
  const target = pages.find((p) => /^http:\/\/127\.0\.0\.1:\d+\/?$/.test(p.url || ''))
  check('CDP 可连接且有页面目标', pages.length > 0, `页面数 ${pages.length}`)
  check('主窗口目标存在（非 #/overlay / #/onboarding）', !!target, target ? target.url : '缺失')
  if (!target) return finish()

  if (process.argv.includes('--front')) {
    await send(target, 'Page.bringToFront')
    console.log('[gui] 已将主窗口置前')
    process.exit(0)
  }

  // A. 课程列表 / 新建课程
  check('课程页已渲染', await waitForText(target, '课程', 10000), await pageTitle(target))
  check('点「新建课程」打开弹窗',
    (await clickText(target, '新建课程', 'button')) && !!(await waitFor(
      async () => (await evaluate(target, 'document.querySelector(".modal-title")?.textContent || ""')) === '新建课程',
      8000
    )),
    '真实点击侧栏/页头按钮'
  )
  check('选择线上网课场景', await clickText(target, '线上网课', '.mode-card'))
  check('填写课程名称', await setPlaceholder(target, '例如：CS229 机器学习', COURSE_NAME))
  check('填写自定义术语', await setPlaceholder(
    target,
    '用逗号分隔，例如 gradient descent, overfitting, 正则化',
    'transformer, 注意力'
  ))
  check('填写课程描述', await setPlaceholder(
    target,
    '描述课程内容，帮助 AI 更好理解上下文',
    '端到端 GUI 用户测试'
  ))
  check('提交创建课程', await clickText(target, '创建线上课程', 'button'))
  check('创建后跳到课程配置页', await waitForText(target, '课程配置', 15000), await pageTitle(target))

  // B. 课程配置页：识别模型 + 手动章节
  check('配置页显示「识别与字幕」', await waitForText(target, '识别与字幕'))
  check('配置页显示「识别模型」', (await bodyText(target)).includes('识别模型'))
  const hasMedium = await evaluate(target, `(() => [...document.querySelectorAll('select')]
    .some((s) => [...s.options].some((o) => o.value === 'medium')))()`)
  check('识别模型下拉包含 medium（更准）', hasMedium)
  if (hasMedium) {
    const switched = await evaluate(target, `(() => {
      const s = [...document.querySelectorAll('select')].find((s) => [...s.options].some((o) => o.value === 'medium'))
      if (!s) return false
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(s, 'medium')
      s.dispatchEvent(new Event('change', { bubbles: true }))
      return true
    })()`)
    check('可切换到 medium 模型', switched)
  }
  check('填写新章节名', await setPlaceholder(target, '新章节名，例如「第 3 讲 · 梯度下降」', '第 1 讲 · 测试章节'))
  check('点击添加章节', await clickText(target, '添加', 'button'))
  check('章节出现在配置页', await waitForText(target, '第 1 讲 · 测试章节'), '手动章节已落库并回显')

  // C. 今日复习 / 生词本 / 导出记录
  check('侧栏可跳「今日复习」', (await clickSelector(target, 'button[title="今日复习"]')) && await waitForText(target, '自主复习'))
  check('今日复习页显示自主复习入口', (await bodyText(target)).includes('自主复习'))

  check('侧栏可跳「生词本」', (await clickSelector(target, 'button[title="生词本"]')) && await waitForPageTitle(target, '生词本'))
  check('生词本页渲染', (await pageTitle(target)) === '生词本')

  check('侧栏可跳「导出记录」', (await clickSelector(target, 'button[title="导出记录"]')) && await waitForPageTitle(target, '导出记录'))
  check('导出记录空态正确', (await bodyText(target)).includes('还没有导出过字幕或生词'))

  // D. 设置 / 帮助面板
  check('侧栏打开设置面板', (await clickSelector(target, 'button[title="设置"]')) && await waitForText(target, '桌面字幕悬浮窗'))
  check('设置面板可关闭', await clickSelector(target, 'button[aria-label="关闭控制面板"]'))

  check('侧栏打开帮助面板', (await clickSelector(target, 'button[title="帮助与关于"]')) && !!(await waitFor(
    async () => !!(await evaluate(target, '!!document.querySelector("[data-testid=\\"help-about-panel\\"]")')),
    8000
  )))
  const versionText = await evaluate(target, 'document.querySelector("[data-testid=\\"help-about-version\\"]")?.textContent || ""')
  check(`帮助面板显示版本号 ${APP_VERSION}`, versionText.includes(APP_VERSION), versionText)
  check('帮助面板可关闭', await clickSelector(target, '[data-testid="help-about-close"]'))

  // E. 回到课程并打开识别页
  check('侧栏回到全部课程', (await clickSelector(target, '[title="全部课程"]')) && await waitForPageTitle(target, '课程'))
  check('新建课程出现在列表', await waitForText(target, COURSE_NAME), COURSE_NAME)
  check('打开课程进入识别页',
    (await clickText(target, COURSE_NAME, '[role="button"]')) && !!(await waitFor(
      async () => (await pageTitle(target)) === COURSE_NAME,
      10000
    )),
    await pageTitle(target)
  )
  const recogText = await bodyText(target)
  check('识别页显示开始/暂停控制', /开始|暂停/.test(recogText))
  check('识别页显示结束并总结', recogText.includes('结束并总结'))

  // F. AI 答疑抽屉（真实 Ollama 链路）
  check('识别页打开 AI 答疑', (await clickText(target, 'AI 答疑', 'button')) && !!(await waitFor(
    async () => !!(await evaluate(target, '!!document.querySelector("[aria-label=\\"输入问题\\"]")')),
    8000
  )))
  check('AI 服务在抽屉中显示可用', !(await bodyText(target)).includes('AI 尚未就绪'))
  check('输入 AI 问题', await setValue(target, '[aria-label="输入问题"]', '请只回答数字：1+1等于几？'))
  check('发送 AI 问题', await clickSelector(target, '[aria-label="发送问题"]'))
  const answered = await waitFor(async () => {
    const text = await bodyText(target)
    return text.includes('1+1等于几') && (/\b2\b|等于\s*2/.test(text) || text.includes('ai-chat-error'))
  }, 60000)
  const chatBody = await evaluate(target, 'document.querySelector(".ai-chat-body")?.innerText || ""')
  check('AI 答疑返回答案', !!answered && !chatBody.includes('AI 答疑服务不可用'), chatBody.replace(/\s+/g, ' ').slice(0, 100))
  check('AI 答疑抽屉可关闭', await clickSelector(target, 'button[aria-label="关闭 AI 答疑"]'))

  return finish()
}

function finish() {
  const failed = checks.filter((c) => !c.ok)
  console.log('')
  console.log(`[gui] 结果：${checks.length - failed.length}/${checks.length} 通过`)
  if (failed.length) {
    failed.forEach((f) => console.error(`  ✗ ${f.name}${f.detail ? ` — ${f.detail}` : ''}`))
    process.exit(1)
  }
  process.exit(0)
}

main().catch((err) => {
  console.error(`[gui] 未预期的失败：${err && err.stack ? err.stack : err}`)
  process.exit(1)
})
