/**
 * 首启向导人工验收驱动（CDP）—— 一次性工具，服务于 verify_install 生成的人工清单
 *
 * 为什么需要它：`packaging/verify/onboarding-manual-checklist.md` 里的 5 条属于
 * 「重启级 / 视觉级」场景，verify_install 跑完就卸载了，脚本里断言不了。这里用 CDP
 * 把**打包后**的应用拉起来，读真实 DOM、驱动 React 按钮、逐帧截图，把「人工点一遍」
 * 落成可复核的证据文件。
 *
 * 与 frontend/scripts/smoke-overlay.cjs 的关系：CDP 连接方式（Node 22 内置 WebSocket）
 * 与「React 受控组件优先走 __reactProps 上的 onClick」这两个坑完全一致，直接复用同一套做法。
 *
 * 为什么不用 frontend/scripts/launch-ui.cjs：那个面向开发态 electron.exe（app 传目录），
 * 本脚本面向**安装后**的 exe，才能验「安装包 + 首启向导」这条真实链路。
 *
 * 前置：安装包已 `/S` 静默安装（本机静默安装必须用 Python subprocess 或 --keep-installed 流程，
 * 经 Git Bash 直接跑会把 `/S` 当路径转掉，安装器会弹交互界面挂住）。
 *
 * 用法：
 *   node drive-onboarding.cjs <home> run12 [port]   # ①全新自动弹出 + ②完成后重启不弹
 *   node drive-onboarding.cjs <home> run3  [port]   # ③跳过 → 重启不弹
 *   node drive-onboarding.cjs <home> run4  [port]   # ④已有 completed=true（老用户升级）不弹
 *   node drive-onboarding.cjs <home> run5  [port]   # ⑤第 4/6 步文案截图（人工对照控制面板）
 *   node drive-onboarding.cjs <home> step3 [port]   # ⑥第 3 步「正在复制模型种子」+ 就绪后自动跳步
 *
 * 退出码：0 全部符合预期；1 有断言不符；2 环境不满足（起不来 / 找不到向导窗口）
 */
const { spawn } = require('child_process')
const fs = require('fs')
const http = require('http')
const path = require('path')

const APP_NAME = '听课伴侣'
const APP_EXE = path.join(process.env.LOCALAPPDATA || '', 'Programs', 'tingke-banlv', `${APP_NAME}.exe`)
const HOME = process.argv[2]
const MODE = process.argv[3]
const PORT = Number(process.argv[4] || 9333)
// 本脚本放 scripts/（与 verify_install.py 同级、入库），但产出是**证据产物**，
// 统一落在 packaging/verify/ —— 那个目录被 .gitignore 忽略，正合适。
const OUT_DIR = path.resolve(__dirname, '..', 'packaging', 'verify')
const SHOTS = path.join(OUT_DIR, 'screenshots')
const OB_JSON = path.join(HOME || '', 'onboarding.json')
const LOG_FILE = path.join(HOME || '', 'logs', 'electron.log')

if (!HOME || !MODE) {
  console.error('用法: node drive-onboarding.cjs <home> <mode> [port]')
  process.exit(2)
}
if (!fs.existsSync(APP_EXE)) {
  console.error('[drive] 环境不满足：未找到', APP_EXE, '（先静默安装）')
  process.exit(2)
}
fs.mkdirSync(SHOTS, { recursive: true })

// 向导按钮文案 —— 与 frontend/src/lib/onboarding.ts 的 WIZARD_COPY 一一对应，
// 改那边必须同步改这里（就像主进程日志锚点那样）。
const BTN = {
  start: '开始设置',
  next: '下一步',
  done: '开始使用',
  skip: '跳过，直接使用',
  skipAll: '跳过',
  step3Later: '先跳过，稍后再说',
}

// 第 3 步的相位文案（同样与 WIZARD_COPY 一一对应）。
// 播种那句是缺陷 B 修复后新增的可见文案，step3 模式专门验它真的会渲染。
const COPY_STEP3_SEEDING = '正在复制模型种子，请稍候'
const COPY_STEP3_FAILED = '模型准备失败'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const results = []
function check(name, ok, detail = '') {
  results.push({ name, ok: !!ok, detail })
  console.log(`${ok ? '  ok  ' : ' FAIL '} ${name}${detail ? ` — ${detail}` : ''}`)
}

function getJson(url) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, (res) => {
      let b = ''
      res.on('data', (c) => (b += c))
      res.on('end', () => {
        try { resolve(JSON.parse(b)) } catch (e) { reject(e) }
      })
    })
    req.on('error', reject)
    req.setTimeout(3000, () => req.destroy(new Error('timeout')))
  })
}

async function listTargets() {
  try { return await getJson(`http://127.0.0.1:${PORT}/json/list`) } catch { return [] }
}

/** 轮询直到出现满足 pred 的 CDP target */
async function waitTarget(pred, timeoutMs, label) {
  const t0 = Date.now()
  while (Date.now() - t0 < timeoutMs) {
    const hit = (await listTargets()).find(pred)
    if (hit) return hit
    await sleep(1000)
  }
  console.error(`[drive] 等待「${label}」超时（${timeoutMs / 1000}s）`)
  return null
}

const isWizard = (t) => typeof t.url === 'string' && t.url.includes('#/onboarding')
/**
 * 主窗口判定用**排除法**，不要校验 url 里有没有 `#/`。
 *
 * 两个窗口的 `document.title` 都被 `<title>` 覆盖成同一个（见 docs 里的记录），
 * url 也不能假定带 hash —— 第一次跑 run12 就是卡在 `url.includes('#/')` 上：
 * 主窗口其实起来了（日志有「界面加载完成」），target 判定却一直不匹配。
 */
const isMain = (t) => {
  const u = typeof t.url === 'string' ? t.url : ''
  if (u.startsWith('devtools://')) return false
  return t.type === 'page' && !isWizard(t) && !u.includes('#/overlay')
}

function connect(target) {
  const ws = new WebSocket(target.webSocketDebuggerUrl)
  let id = 0
  const pending = new Map()
  ws.addEventListener('message', (ev) => {
    // 这里必须 try/catch：CDP 的截图响应是**二进制帧**，ev.data 可能是 Buffer/Blob，
    // JSON.parse 会抛 SyntaxError —— 异常发生在事件回调里，外层 try 包不住，
    // 会直接把整个 Node 进程带崩（第一次跑 run12 输出停在第一张截图就是这么没的）。
    try {
      const text = typeof ev.data === 'string' ? ev.data : Buffer.from(ev.data).toString('utf8')
      const msg = JSON.parse(text)
      if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id) }
    } catch (e) {
      console.error('[drive] CDP 消息解析失败:', e && e.message)
    }
  })
  const ready = new Promise((res, rej) => {
    ws.addEventListener('open', res)
    ws.addEventListener('error', () => rej(new Error('CDP WebSocket 连接失败')))
  })
  const send = (method, params, timeoutMs) => new Promise((res) => {
    const myId = ++id
    // 必须带超时：点「开始使用」会让主进程**立刻关闭向导窗口**，
    // 那条 CDP 响应永远回不来，Promise 悬挂后事件循环归零、进程静默退出
    // （现象：脚本提前结束、输出缺尾、退出码还是 0）。
    const limit = timeoutMs || (method === 'Page.captureScreenshot' ? 25000 : 8000)
    const timer = setTimeout(() => {
      pending.delete(myId)
      console.error(`[drive] CDP ${method} 超时（${limit}ms）—— 目标窗口可能已关闭`)
      res({ __timeout: true })
    }, limit)
    pending.set(myId, (m) => { clearTimeout(timer); res(m) })
    try {
      ws.send(JSON.stringify({ id: myId, method, params }))
    } catch (e) {
      clearTimeout(timer)
      pending.delete(myId)
      res({ __error: String(e && e.message) })
    }
  })
  return { ws, ready, send }
}

async function evalOn(conn, expression) {
  const r = await conn.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
  const res = r.result && r.result.result
  if (r.result && r.result.exceptionDetails) {
    console.error('[drive] evaluate 异常:', JSON.stringify(r.result.exceptionDetails).slice(0, 300))
  }
  return res ? res.value : undefined
}
/** 当前所有按钮的文案与 disabled 状态（推进算法的输入） */
async function buttons(conn) {
  const r = await evalOn(conn, `(() => Array.from(document.querySelectorAll('button')).map((b) => ({ text: (b.innerText || '').trim(), disabled: !!b.disabled })))()`)
  return Array.isArray(r) ? r : []
}

/** 当前步数指示（stepLabel 渲染的「第 N / 6 步」），抓不到就返回 null */
async function stepCounter(conn) {
  const r = await evalOn(conn, `(() => { const m = (document.body.innerText || '').match(/第\\s*(\\d)\\s*\\/\\s*6\\s*步/); return m ? Number(m[1]) : null })()`)
  return typeof r === 'number' ? r : null
}

/**
 * 按可见文案点按钮。
 *
 * 两个坑，都踩过：
 *   1. React 受控组件在 CDP 里 dispatched 原生 click 时偶发不触发（合成事件委托时序），
 *      因此优先直接调 `__reactProps$*` 上的 onClick —— smoke-overlay.cjs 同款做法。
 *   2. **disabled 的按钮必须拒绝点击**。onClick 直调会绕过 DOM 的 disabled 门控，
 *      把「第 3 步模型未就绪、主按钮不可点」误判成「用户点了下一步」—— 第一次跑
 *      run12 就是这么造出 step3→4 的假前进的（日志里那一步没有对应点击动作）。
 */
async function clickText(conn, text) {
  const expr = `
    (function(text) {
      const norm = (s) => (s || '').replace(/\\s+/g, '');
      const want = norm(text);
      const all = Array.from(document.querySelectorAll('button, [role="button"], a'));
      const el = all.find((e) => norm(e.innerText).includes(want));
      if (!el) {
        return { found: false, labels: all.map((e) => (e.innerText || '').trim()).filter(Boolean).slice(0, 24) };
      }
      if (el.disabled || el.getAttribute('aria-disabled') === 'true') {
        return { found: true, blocked: true, label: (el.innerText || '').trim() };
      }
      const key = Object.keys(el).find((k) => k.startsWith('__reactProps$'));
      const props = key ? el[key] : null;
      if (props && typeof props.onClick === 'function') {
        props.onClick({ preventDefault() {}, stopPropagation() {} });
        return { found: true, via: 'reactProps', label: (el.innerText || '').trim() };
      }
      el.click();
      return { found: true, via: 'click', label: (el.innerText || '').trim() };
    })(${JSON.stringify(text)})
  `
  const r = await evalOn(conn, expr)
  if (r && r.blocked) {
    console.log(`[drive] 按钮「${r.label}」当前不可点（disabled）—— 等待其就绪`)
  } else if (!r || !r.found) {
    console.error(`[drive] 未找到按钮「${text}」，当前可见按钮:`, r ? r.labels : '(无返回)')
  }
  return r
}

/**
 * 推进向导直到「开始使用」出现并点掉它。
 *
 * 为什么不按固定 6 步点：**第 3 步在模型就绪后会自己跳到第 4 步**（设计稿要求，
 * 组件里有 `phase === 'ready' → 900ms 后 setStep(4)`）。固定步数的脚本必然错位。
 * 这里改成「看当前有什么按钮决定下一步」，顺带记录每轮快照，错位时可直接看 trace。
 */
async function advanceToComplete(conn, maxRound = 40, waitMs = 60000, onBeforeDone = null) {
  const trace = []
  let step3WaitUntil = null
  for (let i = 0; i < maxRound; i++) {
    const bs = await buttons(conn)
    const labels = bs.map((b) => `${b.text}${b.disabled ? '(禁)' : ''}`)
    const step = await stepCounter(conn)
    trace.push({ step, labels })
    console.log(`[drive] 推进轮 ${i + 1}：步=${step === null ? '?' : step} 按钮=${labels.join(' / ') || '(无)'}`)

    const done = bs.find((b) => b.text === BTN.done && !b.disabled)
    if (done) {
      // 「开始使用」一点，主进程就关闭向导窗口 —— 之后一切 CDP 调用都会超时。
      // 所以第 6 步的样子必须在点之前拍。
      if (onBeforeDone) await onBeforeDone()
      await clickText(conn, BTN.done)
      await sleep(1800)
      return { ok: true, trace }
    }
    // 第 3 步：模型就绪前主按钮禁用；若走了 failed 分支会多出「先跳过，稍后再说」。
    // **不要一见到它就点**：首启时模型正在从安装目录异步播种（141MB），
    // 播种未完成时 model-status 返回 ok:false → 前端判 failed。等一会儿往往自己就 ready 了。
    // 只有等满 waitMs 仍是 failed，才按失败分支跳过 —— 这也是真实用户该点的按钮。
    const later = bs.find((b) => b.text === BTN.step3Later && !b.disabled)
    if (later) {
      if (step3WaitUntil === null) {
        step3WaitUntil = Date.now() + waitMs
        console.log(`[drive] 第 3 步处于 failed 分支 —— 先等 ${Math.round(waitMs / 1000)}s 看模型是否播种完成`)
      }
      if (Date.now() < step3WaitUntil) { await sleep(3000); continue }
      console.log('[drive] 等待超时，按失败分支点「先跳过，稍后再说」继续')
      await clickText(conn, BTN.step3Later)
      await sleep(1000)
      continue
    }
    // 主按钮恢复可点 → 模型已就绪，清掉等待计时
    if (step3WaitUntil !== null && bs.some((b) => b.text === BTN.next && !b.disabled)) {
      console.log('[drive] 模型已就绪，第 3 步可正常前进')
      step3WaitUntil = null
    }

    const next = bs.find((b) => b.text === BTN.next && !b.disabled)
    if (next) { await clickText(conn, BTN.next); await sleep(1000); continue }

    // 主按钮被禁（第 3 步准备中）→ 等自动就绪
    await sleep(1500)
  }
  return { ok: false, trace }
}

async function shot(conn, file) {
  const r = await conn.send('Page.captureScreenshot', { format: 'png' })
  const data = r.result && r.result.data
  if (!data) { console.error('[drive] 截图失败:', JSON.stringify(r).slice(0, 200)); return null }
  const p = path.join(SHOTS, file)
  fs.writeFileSync(p, Buffer.from(data, 'base64'))
  console.log('[drive] 截图 ->', p)
  return p
}

function launch() {
  const env = { ...process.env }
  // 这两条在本机 Bash 环境里默认存在，会让 Electron 退化成纯 Node 进程
  // （不开窗、不建日志、不起后端），必须剥掉 —— 与 verify_install 的 clean_env 一致。
  delete env.ELECTRON_RUN_AS_NODE
  delete env.NODE_OPTIONS
  env.TINGKEBANLV_HOME = HOME
  env.TINGKEBANLV_DEBUG_PORT = String(PORT)
  const child = spawn(APP_EXE, [], { detached: true, stdio: 'ignore', env })
  child.unref()
  console.log(`[drive] 启动 ${APP_NAME} pid=${child.pid} home=${HOME} port=${PORT}`)
  return child
}

async function killApp() {
  await new Promise((resolve) => {
    const p = spawn('taskkill', ['/IM', `${APP_NAME}.exe`, '/T', '/F'], { stdio: 'ignore' })
    p.on('exit', () => resolve())
    p.on('error', () => resolve())
  })
  await waitNoApp()
}

/**
 * 等进程真正退干净。
 *
 * 单实例锁：若旧实例还没走，新启动的进程会被转交（激活旧窗口）然后自己退出，
 * 于是「重启轮」拿到的还是上一轮的 target/日志，结论全假。
 */
async function waitNoApp(timeoutSec = 20) {
  for (let i = 0; i < timeoutSec * 2; i++) {
    const alive = await new Promise((resolve) => {
      const p = spawn('tasklist', ['/FI', `IMAGENAME eq ${APP_NAME}.exe`, '/FO', 'CSV', '/NH'], { stdio: ['ignore', 'pipe', 'ignore'] })
      let out = ''
      p.stdout.on('data', (d) => (out += d))
      p.on('exit', () => resolve(out.includes(`${APP_NAME}.exe`)))
      p.on('error', () => resolve(false))
    })
    if (!alive) return true
    await sleep(500)
  }
  console.error('[drive] 进程未在 20s 内退干净，继续执行可能拿到旧实例')
  return false
}

function readLog() {
  try { return fs.readFileSync(LOG_FILE, 'utf8') } catch { return '' }
}
function readOb() {
  try { return JSON.parse(fs.readFileSync(OB_JSON, 'utf8')) } catch { return null }
}
function writeOb(obj) {
  fs.mkdirSync(HOME, { recursive: true })
  fs.writeFileSync(OB_JSON, JSON.stringify(obj, null, 2), 'utf8')
}

/** 归档本轮日志，便于 run-2 覆盖前留证 */
function archiveLog(tag) {
  const text = readLog()
  if (!text) return null
  const p = path.join(OUT_DIR, `onboarding-${tag}.log`)
  fs.writeFileSync(p, text, 'utf8')
  console.log('[drive] 日志留档 ->', p)
  return p
}

/** 启动后等向导窗口出现，返回 {target, conn} */
async function openWizard(timeoutSec) {
  const t = await waitTarget(isWizard, timeoutSec * 1000, '向导窗口 #/onboarding')
  if (!t) {
    const ts = await listTargets()
    console.error('[drive] 当前 targets:', ts.map((x) => x.url).join(' | ') || '(空)')
    console.error('[drive] 日志尾部:', readLog().split('\n').slice(-12).join('\n'))
    return null
  }
  const conn = connect(t)
  await conn.ready
  await sleep(1500) // 等首帧渲染稳定再截图
  return { target: t, conn }
}

/** 推进到指定步（第 4 / 第 6 步截图用）。stepCounter 抓不到步数时不判定，只按按钮推。 */
async function advanceToStep(conn, target, maxRound = 40, waitMs = 60000) {
  let step3WaitUntil = null
  for (let i = 0; i < maxRound; i++) {
    const cur = await stepCounter(conn)
    if (cur === target) return { ok: true, step: cur }
    const bs = await buttons(conn)
    const later = bs.find((b) => b.text === BTN.step3Later && !b.disabled)
    if (later) {
      if (step3WaitUntil === null) step3WaitUntil = Date.now() + waitMs
      if (Date.now() < step3WaitUntil) { await sleep(3000); continue }
      await clickText(conn, BTN.step3Later)
      await sleep(1000)
      continue
    }
    const next = bs.find((b) => b.text === BTN.next && !b.disabled)
    if (next) { await clickText(conn, BTN.next); await sleep(1000); continue }
    await sleep(1500) // 主按钮被禁：第 3 步正在准备模型，等它就绪
  }
  return { ok: false, step: await stepCounter(conn) }
}

// ============ 模式实现 ============

/** ① + ②：全新 home 自动弹出 → 走完 6 步 → 重启不弹 */
async function run12() {
  await killApp()
  if (fs.existsSync(OB_JSON)) fs.unlinkSync(OB_JSON) // 保证「全新」
  if (fs.existsSync(LOG_FILE)) fs.unlinkSync(LOG_FILE)

  launch()
  const w = await openWizard(90)
  if (!w) return 2

  const ob0 = readOb()
  check('① 向导自动弹出（窗口存在）', true, `url=${w.target.url}`)
  check('① 状态为未完成', ob0 && ob0.completed === false, `onboarding.json=${JSON.stringify(ob0)}`)
  check('① 停在 1/6 步', ob0 && ob0.stepReached === 1, `stepReached=${ob0 && ob0.stepReached}`)
  await shot(w.conn, 'setup-01-向导自动弹出.png')

  // 点「开始设置」进入第 2 步，之后交给推进算法（第 3 步模型就绪会自动跳，不能按固定步数点）
  await clickText(w.conn, BTN.start)
  await sleep(900)
  const adv = await advanceToComplete(w.conn, 40, 60000, async () => {
    await shot(w.conn, 'setup-02-第6步.png')   // 必须在点「开始使用」之前 —— 点完窗口就关了
  })
  const ob1 = readOb()
  check('② 走完 6 步并点掉「开始使用」', adv.ok,
    adv.ok ? '已走到最后一步' : `未走到最后一步，轨迹=${JSON.stringify(adv.trace).slice(0, 600)}`)
  check('② 点「开始使用」后 completed=true', !!(ob1 && ob1.completed === true), `onboarding.json=${JSON.stringify(ob1)}`)
  check('② stepReached 记录到 6', !!(ob1 && ob1.stepReached === 6), `stepReached=${ob1 && ob1.stepReached}`)
  check('② durationMs 已写入', !!(ob1 && typeof ob1.durationMs === 'number'), `durationMs=${ob1 && ob1.durationMs}`)

  archiveLog('run1')
  const wizardOpensRun1 = readLog().split('[onboarding] 向导已打开').length - 1
  await killApp()
  await sleep(4000)
  if (fs.existsSync(LOG_FILE)) fs.unlinkSync(LOG_FILE) // 清掉，便于统计 run-2

  // ---- 重启轮 ----
  launch()
  const main = await waitTarget(isMain, 60 * 1000, '主窗口')
  check('② 重启后主窗口正常', !!main, main ? `url=${main.url}` : '未出现主窗口')
  let wizardAgain = false
  for (let i = 0; i < 25; i++) {
    if ((await listTargets()).some(isWizard)) { wizardAgain = true; break }
    await sleep(1000)
  }
  const log2 = readLog()
  check('② 重启后向导不再弹出', !wizardAgain, wizardAgain ? '重启后仍出现 #/onboarding 窗口' : '25s 内无向导窗口')
  check('② 重启轮日志无「向导已打开」', !log2.includes('[onboarding] 向导已打开'),
    log2.includes('[onboarding] 向导已打开') ? '重启轮日志仍出现向导打开' : '干净')
  check('② 首轮向导只弹一次', wizardOpensRun1 === 1, `run-1 命中 ${wizardOpensRun1} 次`)
  if (main) {
    const conn = connect(main)
    await conn.ready
    await sleep(1500)
    await shot(conn, 'setup-04-重启不弹（主窗口）.png')
  }
  archiveLog('run2')
  await killApp()
  return results.every((r) => r.ok) ? 0 : 1
}

/** ③ 点「跳过」→ 重启不弹 */
async function run3() {
  await killApp()
  // 重置为「未走过」状态，但仍保留其它字段结构
  writeOb({
    schemaVersion: 1,
    startedAt: new Date().toISOString(),
    stepReached: 1,
    completed: false,
    dismissedAt: null,
    durationMs: null,
    lastShownVersion: null,
    seenHotkeys: {},
  })
  if (fs.existsSync(LOG_FILE)) fs.unlinkSync(LOG_FILE)

  launch()
  const w = await openWizard(90)
  if (!w) return 2
  await shot(w.conn, 'setup-05-跳过前.png')

  const r = await clickText(w.conn, BTN.skip)
  await sleep(1800)
  const ob = readOb()
  check('③ 第 1 步跳过按钮可达', !!(r && r.found), r ? `via=${r.via} label=${r.label}` : '未找到')
  check('③ 跳过后 dismissedAt 已写入', !!(ob && ob.dismissedAt), `dismissedAt=${ob && ob.dismissedAt}`)
  check('③ 跳过后 completed 仍为 false', !!(ob && ob.completed === false), `completed=${ob && ob.completed}`)
  // 跳过后向导窗口立即关闭，这里不再截图（setup-05 已记录跳过前的界面）

  archiveLog('skip-run1')
  await killApp()
  await sleep(4000)
  if (fs.existsSync(LOG_FILE)) fs.unlinkSync(LOG_FILE)

  launch()
  const main = await waitTarget(isMain, 60 * 1000, '主窗口')
  let wizardAgain = false
  for (let i = 0; i < 25; i++) {
    if ((await listTargets()).some(isWizard)) { wizardAgain = true; break }
    await sleep(1000)
  }
  check('③ 跳过后重启不再弹出', !wizardAgain, wizardAgain ? '重启后仍出现向导' : '25s 内无向导窗口')
  check('③ 重启轮日志无「向导已打开」', !readLog().includes('[onboarding] 向导已打开'), '日志检查')
  if (main) {
    const conn = connect(main)
    await conn.ready
    await sleep(1500)
    await shot(conn, 'setup-07-跳过后重启（主窗口）.png')
  }
  archiveLog('skip-run2')
  await killApp()
  return results.every((r) => r.ok) ? 0 : 1
}

/** ④ 老用户升级：已有 completed=true，不应重置、不应重复弹 */
async function run4() {
  await killApp()
  const before = {
    schemaVersion: 1,
    startedAt: '2026-09-01T10:00:00.000Z',
    stepReached: 6,
    completed: true,
    dismissedAt: null,
    durationMs: 95000,
    lastShownVersion: null,
    seenHotkeys: {},
  }
  writeOb(before)
  if (fs.existsSync(LOG_FILE)) fs.unlinkSync(LOG_FILE)

  launch()
  const main = await waitTarget(isMain, 60 * 1000, '主窗口')
  check('④ 升级后主窗口正常', !!main, main ? `url=${main.url}` : '未出现主窗口')
  let wizardShown = false
  for (let i = 0; i < 25; i++) {
    if ((await listTargets()).some(isWizard)) { wizardShown = true; break }
    await sleep(1000)
  }
  check('④ 已完成用户升级后不再弹向导', !wizardShown, wizardShown ? '仍弹出向导' : '25s 内无向导窗口')
  check('④ 日志无「向导已打开」', !readLog().includes('[onboarding] 向导已打开'), '日志检查')

  const after = readOb()
  const same = JSON.stringify(before) === JSON.stringify(after)
  check('④ onboarding.json 未被升级重置', same,
    same ? '逐字段一致' : `before=${JSON.stringify(before)} after=${JSON.stringify(after)}`)
  if (main) {
    const conn = connect(main)
    await conn.ready
    await sleep(1500)
    await shot(conn, 'setup-08-升级不弹（主窗口）.png')
  }
  archiveLog('upgrade')
  await killApp()
  return results.every((r) => r.ok) ? 0 : 1
}

/** ⑤ 第 4 步穿透提示 / 第 6 步降级文案截图（人工与控制面板对照） */
async function run5() {
  await killApp()
  writeOb({
    schemaVersion: 1,
    startedAt: new Date().toISOString(),
    stepReached: 1,
    completed: false,
    dismissedAt: null,
    durationMs: null,
    lastShownVersion: null,
    seenHotkeys: {},
  })
  if (fs.existsSync(LOG_FILE)) fs.unlinkSync(LOG_FILE)

  launch()
  const w = await openWizard(90)
  if (!w) return 2
  await clickText(w.conn, BTN.start)
  await sleep(900)
  const a4 = await advanceToStep(w.conn, 4)
  await sleep(800)
  const step4 = await evalOn(w.conn, `document.body.innerText`)
  await shot(w.conn, 'setup-09-第4步-穿越提示.png')
  check('⑤ 已到达第 4 步', a4.ok, `当前步=${a4.step}`)
  check('⑤ 第 4 步文案已抓取', typeof step4 === 'string' && step4.includes('鼠标穿透'), String(step4).replace(/\s+/g, ' ').slice(0, 200))
  fs.writeFileSync(path.join(OUT_DIR, 'copy-step4.txt'), String(step4 || ''), 'utf8')

  const a6 = await advanceToStep(w.conn, 6)
  await sleep(800)
  const step6 = await evalOn(w.conn, `document.body.innerText`)
  await shot(w.conn, 'setup-10-第6步-术语降级.png')
  check('⑤ 已到达第 6 步', a6.ok, `当前步=${a6.step}`)
  check('⑤ 第 6 步文案已抓取', typeof step6 === 'string' && step6.length > 30, String(step6).replace(/\s+/g, ' ').slice(0, 200))
  fs.writeFileSync(path.join(OUT_DIR, 'copy-step6.txt'), String(step6 || ''), 'utf8')

  const banned = /token|Token|预算|截断|提示词|initial_prompt|智能降级/
  check('⑤ 两处均无禁词', !banned.test(String(step4 || '') + String(step6 || '')),
    banned.test(String(step4 || '') + String(step6 || '')) ? '命中禁词' : '未命中')
  check('⑤ 第 6 步含「重新开始听课」口径', String(step6 || '').includes('重新开始听课'),
    String(step6 || '').replace(/\s+/g, ' ').slice(0, 300))

  archiveLog('copy')
  await killApp()
  return results.every((r) => r.ok) ? 0 : 1
}

/**
 * ⑥ 第 3 步「正在复制模型种子」——缺陷 B 修复的正面验收
 *
 * 为什么必须「构造」这个状态：本机把 141MB 种子从安装目录拷到数据目录只要
 * **0.1~0.4s**（NVMe + 页缓存），这个中间态真实存在，但肉眼与 CDP 都抓不到，
 * 直接跑一遍只能看到「第 3 步一闪而过」—— 证明不了那句文案真的会渲染。
 * 所以这里用 CDP 的 Fetch 域把 `/api/asr/model-status` 的响应换成
 * `{ok:false, seed_exists:true, seeding:true}`，把界面**钉在**播种态上读真实 DOM；
 * 再解除拦截，验证它会自己走到第 4 步。
 *
 * 这不是编造数据：被替换的响应正是后端**播种期间的返回值形状**
 * （契约见 backend/api/routes.py 的 `/asr/model-status`，
 *   由 backend/tests/test_onboarding_model_status.py 钉住五个字段）。
 * 换句话说，这里验的是「后端给出播种态时，界面会不会说人话」——
 * 而「后端在播种时确实会给出这个状态」由上面那个后端测试与 verify_install 的
 * 「应用启动后自行播种模型」断言分别覆盖，两段合起来才是完整链路。
 */
async function step3() {
  await killApp()
  if (fs.existsSync(OB_JSON)) fs.unlinkSync(OB_JSON) // 保证「全新」
  if (fs.existsSync(LOG_FILE)) fs.unlinkSync(LOG_FILE)

  launch()
  const w = await openWizard(90)
  if (!w) return 2
  const { conn } = w

  // 拦截必须在**到达第 3 步之前**启用：后端此刻已经播种好了，一旦放行真实响应，
  // 第 3 步会在 900ms 后自己跳到第 4 步，根本没机会观察。
  const stubbed = Buffer.from(
    JSON.stringify({ ok: false, model: 'base', seed_exists: true, seeding: true, error: null }),
    'utf8',
  ).toString('base64')
  // 每条被暂停的请求都必须 fulfill，否则页面会一直等（1s 轮询会越积越多）
  conn.ws.addEventListener('message', (ev) => {
    try {
      const text = typeof ev.data === 'string' ? ev.data : Buffer.from(ev.data).toString('utf8')
      const msg = JSON.parse(text)
      if (msg.method === 'Fetch.requestPaused') {
        conn.send('Fetch.fulfillRequest', {
          requestId: msg.params.requestId,
          responseCode: 200,
          responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
          body: stubbed,
        }).catch(() => {})
      }
    } catch { /* 二进制帧（截图）等非 JSON 消息，忽略 */ }
  })
  await conn.send('Fetch.enable', {
    patterns: [{ urlPattern: '*model-status*', requestStage: 'Request' }],
  })

  await clickText(conn, BTN.start)
  for (let i = 0; i < 8; i++) {
    await sleep(900)
    if ((await stepCounter(conn)) === 3) break
    await clickText(conn, BTN.next)
  }

  let text = ''
  for (let i = 0; i < 24; i++) {
    await sleep(500)
    text = String(await evalOn(conn, `document.body.innerText`))
    if (text.includes(COPY_STEP3_SEEDING)) break
  }
  const stepAtSeed = await stepCounter(conn)
  await shot(conn, 'setup-11-第3步-正在复制模型种子.png')
  const flat = text.replace(/\s+/g, ' ')
  fs.writeFileSync(path.join(OUT_DIR, 'copy-step3-seeding.txt'), text, 'utf8')
  check('⑥ 第 3 步显示「正在复制模型种子，请稍候」', text.includes(COPY_STEP3_SEEDING), flat.slice(0, 200))
  check('⑥ 播种期间不停在失败态', !text.includes(COPY_STEP3_FAILED), `当前步=${stepAtSeed}`)

  // 解除拦截 → 真实响应 ok:true → 应自动跳到第 4 步
  await conn.send('Fetch.disable')
  let advanced = false
  for (let i = 0; i < 24; i++) {
    await sleep(1000)
    const s = await stepCounter(conn)
    if (typeof s === 'number' && s >= 4) { advanced = true; break }
  }
  await shot(conn, 'setup-12-第3步解除拦截后自动跳转.png')
  check('⑥ 模型就绪后自动跳到第 4 步（无需用户点击）', advanced, `当前步=${await stepCounter(conn)}`)

  archiveLog('step3')
  await killApp()
  return results.every((r) => r.ok) ? 0 : 1
}

// ============ 入口 ============
// 全局兜底：异常若发生在事件回调里（CDP 消息、WebSocket 事件），外层 try 包不住，
// 不加这个就会静默退出、只留下半截输出 —— 排查时最难的一类现象。
process.on('uncaughtException', (e) => {
  console.error('[drive] uncaughtException:', e && e.stack ? e.stack : e)
  process.exitCode = 3
})
process.on('unhandledRejection', (e) => {
  console.error('[drive] unhandledRejection:', e && e.stack ? e.stack : e)
  process.exitCode = 4
})

;(async () => {
  if (typeof WebSocket === 'undefined') {
    console.error('[drive] 需要 Node 22+（依赖内置 WebSocket），当前 ' + process.version)
    process.exit(2)
  }
  const table = { run12, run3, run4, run5, step3 }
  const fn = table[MODE]
  if (!fn) {
    console.error(`[drive] 未知 mode: ${MODE}（可选 ${Object.keys(table).join(' / ')}）`)
    process.exit(2)
  }
  let code = 2
  try {
    code = await fn()
  } catch (e) {
    console.error('[drive] 异常:', e && e.stack ? e.stack : e)
    code = 2
  } finally {
    await killApp()
  }
  console.log(`\n[drive] ${MODE} 结束：${results.filter((r) => r.ok).length}/${results.length} 项符合预期`)
  // 用 exitCode 而不是 process.exit()：后者会**截断还没 flush 的 stdout**，
  // 「跑完了但只剩前几行输出」就是这么来的。
  process.exitCode = code
})()
