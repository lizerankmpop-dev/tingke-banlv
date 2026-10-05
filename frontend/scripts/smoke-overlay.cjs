/**
 * 悬浮窗端到端冒烟测试（CDP）
 *
 * 为什么必须进仓库、而不是留一个临时脚本：
 *   悬浮窗的失败模式**几乎都不会报错**。hash 路由写错时 index.html 照样加载成功，
 *   只是渲染成了主应用 —— 表现为「悬浮窗是黑的、不透明、永远不动」；
 *   设置面板里勾上了「已开启」，窗口却根本没出现。这些都无法靠单元测试发现，
 *   只能真的把应用拉起来、回读两个窗口的真实 DOM。
 *
 * 它验证的 7 件事（每一项都对应一次真实踩坑）：
 *   1. 悬浮窗目标真的存在于 CDP target 列表里（窗口被创建出来了）
 *   2. 它加载的是 #/overlay（而不是退回主应用）
 *   3. html 上挂了 overlay-transparent（页面底色不会盖住透明窗口）
 *   4. body 计算背景是透明的（全局样式生效）
 *   5. #root 下真的渲染了节点（React 挂载成功，不是白屏）
 *   6. 主窗口也在，且不在 /overlay（两个入口没有互相串）
 *   7. 悬浮窗页面里没有出现错误提示文案
 *
 * 用法：
 *   node scripts/smoke-overlay.cjs                # 用已构建的 dist
 *   node scripts/smoke-overlay.cjs --dev          # 用 Vite 开发服务器（需先 npm run dev）
 *   node scripts/smoke-overlay.cjs --timeout 200  # 放宽后端就绪等待（秒）
 *   node scripts/smoke-overlay.cjs --keep         # 结束时保留进程与临时数据目录（排查用）
 *
 * 前置条件：frontend/dist 已构建（npm run build），或提供 VITE_DEV_SERVER_URL。
 * 退出码：0 = 全部通过；1 = 有断言失败；2 = 环境不满足（无法启动/后端未就绪）。
 */

const { spawn } = require('child_process')
const fs = require('fs')
const http = require('http')
const os = require('os')
const path = require('path')

const ROOT = path.resolve(__dirname, '..')
const DEBUG_PORT = Number(process.env.TINGKEBANLV_DEBUG_PORT || 9222)
const USE_DEV = process.argv.includes('--dev')
const KEEP = process.argv.includes('--keep')
const TIMEOUT_SEC = (() => {
  const i = process.argv.indexOf('--timeout')
  return i >= 0 && process.argv[i + 1] ? Number(process.argv[i + 1]) : 180
})()

const checks = []
function check(name, ok, detail = '') {
  checks.push({ name, ok: !!ok, detail })
  console.log(`${ok ? '  ok  ' : ' FAIL '} ${name}${detail ? ` — ${detail}` : ''}`)
}

function log(msg) {
  console.log(`[smoke] ${msg}`)
}

function fatal(msg) {
  console.error(`[smoke] 环境不满足：${msg}`)
  process.exit(2)
}

// ============ 启动准备 ============

function electronBinary() {
  try {
    // 在 Electron 之外 require('electron') 返回可执行文件路径
    const p = require('electron')
    if (typeof p === 'string' && fs.existsSync(p)) return p
  } catch {
    /* 继续尝试兜底路径 */
  }
  const guess = path.join(ROOT, 'node_modules', 'electron', 'dist', 'electron.exe')
  return fs.existsSync(guess) ? guess : null
}

function prepareHome() {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'tkl-smoke-'))
  // 预置「悬浮窗已开启」：这就是要验证的场景，不能靠手点
  fs.writeFileSync(
    path.join(home, 'overlay.json'),
    JSON.stringify(
      {
        schemaVersion: 3,
        enabled: true,
        fontSize: 28,
        color: '#ffffff',
        lineCount: 3,
        textBgOpacity: 0.55,
        windowOpacity: 1,
        clickThrough: true,
        backgroundUpdate: true,
        bounds: { x: null, y: null, width: 900, height: 220 },
        anchor: { displayId: null, relX: null, relY: null },
      },
      null,
      2
    ),
    'utf8'
  )
  return home
}

function cleanEnv(home) {
  const env = { ...process.env }
  // 本机 Bash 默认带 ELECTRON_RUN_AS_NODE=1：不剥掉的话 Electron 会当纯 Node 跑，
  // 应用「活着但什么都不做」，日志一行不写 —— 整轮结论作废（真的踩过）
  delete env.ELECTRON_RUN_AS_NODE
  delete env.VITE_DEV_SERVER_URL
  env.TINGKEBANLV_HOME = home
  env.TINGKEBANLV_DEBUG_PORT = String(DEBUG_PORT)
  // 允许并存：本机可能已经开着一个开发实例在跑
  env.TINGKEBANLV_ALLOW_MULTI = '1'
  env.TINGKEBANLV_OVERLAY_DIAG = '1'
  // 强制走已构建产物（源码模式下 dist 不存在时后端无法同源托管前端）
  delete env.TINGKEBANLV_DIST_DIR

  const py = pickPython()
  if (py) {
    env.TINGKEBANLV_PYTHON = py
    log(`后端解释器：${py}`)
  } else {
    log('未找到项目 venv，回退到主进程自己的解释器探测链（可能因缺 fastapi 而启动失败）')
  }
  return env
}

/**
 * 挑一个能跑后端的 Python
 *
 * 本机 `python` 指向托管解释器（没有 fastapi），所以项目自带的 venv 必须显式指定 ——
 * 否则应用会起来、窗口会开、后端永远不就绪，冒烟测试只能看到一个空壳界面。
 */
function pickPython() {
  if (process.env.TINGKEBANLV_PYTHON && fs.existsSync(process.env.TINGKEBANLV_PYTHON)) {
    return process.env.TINGKEBANLV_PYTHON
  }
  const candidates = [
    // 本仓常见的 venv 位置（第二项是「项目源代码」的同级目录）
    path.resolve(ROOT, '..', '..', '安装空间', 'venv', 'Scripts', 'python.exe'),
    path.resolve(ROOT, '..', '..', '.venv', 'Scripts', 'python.exe'),
    path.resolve(ROOT, '..', '安装空间', 'venv', 'Scripts', 'python.exe'),
    path.resolve(ROOT, '..', '.venv', 'Scripts', 'python.exe'),
    path.resolve(ROOT, '.venv', 'Scripts', 'python.exe'),
    path.resolve(ROOT, '..', 'venv', 'Scripts', 'python.exe'),
  ]
  return candidates.find((p) => fs.existsSync(p)) || null
}

// ============ CDP ============

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

async function waitForTargets(timeoutSec) {
  const deadline = Date.now() + timeoutSec * 1000
  let lastErr = null
  while (Date.now() < deadline) {
    try {
      const list = await getJson(`http://127.0.0.1:${DEBUG_PORT}/json/list`)
      // page 类型才是有 DOM 的窗口；worker / iframe 忽略
      const pages = (Array.isArray(list) ? list : []).filter((t) => t.type === 'page')
      if (pages.length >= 2) return pages
      lastErr = new Error(`当前只有 ${pages.length} 个页面目标`)
    } catch (err) {
      lastErr = err
    }
    await new Promise((r) => setTimeout(r, 1000))
  }
  throw new Error(`等待窗口超时：${lastErr ? lastErr.message : '未知'}`)
}

/**
 * 在目标页面里求值
 *
 * 用 Node 22 内置的全局 WebSocket（undici），不额外引 ws 依赖 ——
 * 这是唯一一处需要 WebSocket 的地方，为它加一个包不值得。
 */
function evaluate(target, expression, timeoutMs = 8000) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(target.webSocketDebuggerUrl)
    const timer = setTimeout(() => {
      try {
        ws.close()
      } catch {
        /* 忽略 */
      }
      reject(new Error('CDP 求值超时（页面可能卡死）'))
    }, timeoutMs)
    const done = (fn) => {
      clearTimeout(timer)
      try {
        ws.close()
      } catch {
        /* 忽略 */
      }
      fn()
    }
    ws.addEventListener('open', () => {
      ws.send(
        JSON.stringify({
          id: 1,
          method: 'Runtime.evaluate',
          params: { expression, returnByValue: true, awaitPromise: true },
        })
      )
    })
    ws.addEventListener('message', (ev) => {
      let msg
      try {
        msg = JSON.parse(ev.data)
      } catch {
        return
      }
      if (msg.id !== 1) return
      if (msg.error) {
        done(() => reject(new Error(msg.error.message || 'CDP 错误')))
        return
      }
      const res = msg.result || {}
      if (res.exceptionDetails) {
        done(() => reject(new Error(res.exceptionDetails.text || '页面内异常')))
        return
      }
      done(() => resolve(res.result ? res.result.value : undefined))
    })
    ws.addEventListener('error', () => {
      done(() => reject(new Error('CDP 连接失败')))
    })
  })
}

const PROBE = `(() => {
  const root = document.getElementById('root')
  return {
    hash: location.hash,
    transparentClass: document.documentElement.classList.contains('overlay-transparent'),
    bodyBg: getComputedStyle(document.body).backgroundColor,
    rootChildren: root ? root.childElementCount : -1,
    text: (root && root.innerText ? root.innerText : '').slice(0, 200),
  }
})()`

/**
 * 轮询等待页面进入预期状态
 *
 * 为什么必须等：CDP 的 target 列表在**窗口被创建**时就有了目标，
 * 此时页面往往还在加载（React 尚未挂载、#root 还是空的）。
 * 直接断言会得到「窗口存在但什么都没有」的假失败 —— 第一版就是这么误报的。
 */
async function waitForProbe(target, predicate, timeoutSec, label) {
  const deadline = Date.now() + timeoutSec * 1000
  let last = null
  let lastErr = null
  while (Date.now() < deadline) {
    try {
      last = await evaluate(target, PROBE)
      if (predicate(last)) return last
    } catch (err) {
      lastErr = err
    }
    await new Promise((r) => setTimeout(r, 500))
  }
  if (last) return last
  throw new Error(`等待「${label}」失败：${lastErr ? lastErr.message : '始终取不到页面状态'}`)
}

// ============ 主流程 ============

async function main() {
  if (typeof WebSocket === 'undefined') {
    fatal('需要 Node 22+（依赖内置 WebSocket）。当前 ' + process.version)
  }
  const bin = electronBinary()
  if (!bin) fatal('找不到 Electron 可执行文件，请先在 frontend 下执行 npm install')

  if (!USE_DEV && !fs.existsSync(path.join(ROOT, 'dist', 'index.html'))) {
    fatal('缺少 frontend/dist/index.html。先执行 npm run build，或加 --dev 配合 npm run dev')
  }

  const home = prepareHome()
  log(`临时数据目录：${home}`)
  log(`启动应用：${bin} .`)

  const child = spawn(bin, ['.', '--no-sandbox'], {
    cwd: ROOT,
    env: cleanEnv(home),
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  })
  let stdout = ''
  child.stdout.on('data', (d) => (stdout += d.toString()))
  child.stderr.on('data', (d) => (stdout += d.toString()))

  const cleanup = () => {
    if (KEEP) {
      log('--keep：保留进程与数据目录')
      return
    }
    try {
      if (process.platform === 'win32') {
        spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true })
      } else {
        child.kill('SIGTERM')
      }
    } catch {
      /* 忽略 */
    }
  }

  try {
    let pages
    try {
      pages = await waitForTargets(TIMEOUT_SEC)
    } catch (err) {
      log(`等待窗口失败：${err.message}`)
      log('---- 应用输出（最后 40 行）----')
      console.log(stdout.split('\n').slice(-40).join('\n'))
      fatal('应用未能在超时内打开两个窗口（后端可能没起来，见上面的输出）')
    }

    const overlay = pages.find((p) => (p.url || '').includes('#/overlay'))
    const main = pages.find((p) => p !== overlay)

    check('悬浮窗目标存在', !!overlay, overlay ? overlay.url : `页面数 ${pages.length}，无 #/overlay`)
    check('主窗口目标存在', !!main, main ? main.url : '缺失')

    if (!overlay) {
      log('---- 现有页面 ----')
      pages.forEach((p) => log(`  ${p.type} ${p.url}`))
    } else {
      // 先等真正渲染出来（见 waitForProbe 的注释）
      let info
      try {
        info = await waitForProbe(
          overlay,
          (r) => r.rootChildren > 0 && r.transparentClass,
          Math.min(90, TIMEOUT_SEC),
          '悬浮窗完成渲染'
        )
      } catch (err) {
        log(`等待渲染失败：${err.message}`)
        info = await evaluate(overlay, PROBE).catch(() => ({}))
      }

      check('hash 路由落在 #/overlay', info.hash === '#/overlay', `实际 ${info.hash}`)
      check(
        'html.overlay-transparent 已挂上',
        info.transparentClass === true,
        '未挂上说明 global.css 的透明规则没生效，窗口会是一块不透明黑板'
      )
      check(
        'body 背景透明',
        /rgba\(0,\s*0,\s*0,\s*0\)|transparent/.test(info.bodyBg || ''),
        `实际 ${info.bodyBg}`
      )
      check('React 已挂载（#root 有子节点）', info.rootChildren > 0, `子节点数 ${info.rootChildren}`)
      check(
        '页面内无错误提示',
        !/连接错误|加载失败|Cannot|undefined is not/.test(info.text || ''),
        `正文片段：${String(info.text || '').replace(/\s+/g, ' ').slice(0, 60)}`
      )
    }

    if (main) {
      const mainInfo = await waitForProbe(main, (r) => r.rootChildren > 0, 60, '主窗口完成渲染').catch(
        () => evaluate(main, PROBE).catch(() => ({}))
      )
      check('主窗口不在 /overlay（两个入口未串）', mainInfo.hash !== '#/overlay', `实际 ${mainInfo.hash || '(空)'}`)
      check('主窗口 React 已挂载', mainInfo.rootChildren > 0, `子节点数 ${mainInfo.rootChildren}`)
    }
  } finally {
    cleanup()
  }

  const failed = checks.filter((c) => !c.ok)
  console.log('')
  log(`结果：${checks.length - failed.length}/${checks.length} 通过`)
  if (failed.length) {
    failed.forEach((f) => console.error(`  ✗ ${f.name}${f.detail ? ` — ${f.detail}` : ''}`))
    process.exit(1)
  }
  process.exit(0)
}

main().catch((err) => {
  console.error(`[smoke] 未预期的失败：${err && err.stack ? err.stack : err}`)
  process.exit(1)
})
