/**
 * 听课伴侣 - Electron 主进程
 *
 * 职责：
 *   1. 动态分配空闲端口，拉起 Python 后端（开发用系统 Python，打包用 PyInstaller 产物）
 *   2. 轮询 /api/ping 等待后端就绪，避免前端在服务未起时开始请求
 *      （刻意不用 /api/health：它带外部探测，受限网络下单次可达 2 秒以上）
 *   3. 加载界面（开发走 Vite dev server，打包走 http://127.0.0.1:PORT —— 由后端同源托管前端）
 *   4. 退出时清理整棵后端进程树（Windows 下 uvicorn 子进程必须用 taskkill /T）
 *   5. 管理桌面字幕悬浮窗（独立 BrowserWindow，见「桌面字幕悬浮窗」一节）
 *
 * 全局热键（悬浮窗默认鼠标穿透，键盘是唯一可靠的操作通道）：
 *   Alt+Shift+O  开关悬浮窗
 *   Alt+Shift+D  切换调整模式（临时关闭鼠标穿透以便拖动窗口）
 *
 * 环境变量：
 *   TINGKEBANLV_PYTHON          指定 Python 解释器（覆盖自动探测）
 *   TINGKEBANLV_BACKEND_DIR     指定后端目录
 *   TINGKEBANLV_PORT            强制指定端口（覆盖自动分配，调试用）
 *   TINGKEBANLV_HOME            数据目录（决定日志与 overlay.json 落点，默认 ~/.tingkebanlv）
 *   TINGKEBANLV_GPU_SANDBOX=1   恢复 Chromium GPU 沙箱默认行为（排障用）
 *   VITE_DEV_SERVER_URL         存在则进入开发模式加载该地址

 *
 * 随包分发的资源（均在 process.resourcesPath 下，不进 asar）：
 *   backend/          后端源码（源码模式回退用）
 *   backend-server/   冻结好的独立后端 exe（真实用户机唯一可靠入口）
 *   whisper_models/   内置 base 模型种子，首启播种到 TINGKEBANLV_HOME
 *
 * 兼容模式（自动触发，无需配置）：
 *   若界面在 12 秒内未渲染完成，判定为 Chromium 沙箱被系统安全策略拦截，
 *   应用会自行以 --no-sandbox --tingkebanlv-compat 重启一次。
 *
 * 日志：本进程与后端输出统一落盘到 <TINGKEBANLV_HOME>/logs/electron.log
 */

const { app, BrowserWindow, ipcMain, dialog, screen, globalShortcut } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');
const net = require('net');
const http = require('http');
const { spawn } = require('child_process');

// ============ 启动参数 ============
//
// (1) GPU 沙箱
// 部分环境（无可用 GPU 的虚拟机 / 远程桌面 / 企业安全策略拦截进程沙箱）下，
// Chromium 的 GPU 进程会以 0xC0000005（访问冲突）反复崩溃，最终抛出：
//   FATAL:gpu_data_manager_impl_private.cc(449) GPU process isn't usable. Goodbye.
// 该 FATAL 致命且不可恢复：应用会在后端尚未初始化完成时直接退出，
// 用户看到的现象就是「双击图标闪一下就没了」。实测 --disable-gpu 无法规避
// （Electron 仍会拉起 GPU 子进程），必须关闭 GPU 沙箱。
// 代价仅是失去 GPU 进程这一层隔离（界面为本地 React 页面，不加载远程内容）。
// 如确需恢复默认行为：设置 TINGKEBANLV_GPU_SANDBOX=1
if (process.env.TINGKEBANLV_GPU_SANDBOX !== '1') {
  app.commandLine.appendSwitch('disable-gpu-sandbox');
}

// (2) 兼容模式（渲染器沙箱）
// 在同样的受限环境里，渲染器沙箱也会失效 —— 表现与 GPU 不同，非常隐蔽：
// 应用与后端都正常启动，HTML 也能取到，但渲染器进程直接卡死，
// JS/CSS 永不加载，页面停留在白屏（CDP 中 Runtime.evaluate 会超时）。
// 这无法在启动前预判，因此采用「先正常启动，超时未渲染则自动降级重启」策略：
// 见 createWindow() 中的 firstPaintWatchdog。COMPAT_MODE 即降级重启后的标记。
const COMPAT_MODE = process.argv.includes('--tingkebanlv-compat');
const COMPAT_RELAUNCH_ARGS = ['--no-sandbox', '--tingkebanlv-compat'];

// (3) 远程调试端口（排障用，默认关闭）
// 悬浮窗是独立渲染进程，出问题时必须单独挂 DevTools 才能看到它的控制台；
// 自动化验证也要靠 CDP 才能回读它的真实 DOM。
// 注意：开放该端口等于允许本机任意进程接管页面，因此只在显式设置
// TINGKEBANLV_DEBUG_PORT 时开启，默认一个端口都不监听。
if (process.env.TINGKEBANLV_DEBUG_PORT) {
  app.commandLine.appendSwitch('remote-debugging-port', String(process.env.TINGKEBANLV_DEBUG_PORT));
}

let mainWindow = null;
let overlayWindow = null; // 桌面字幕悬浮窗（独立 BrowserWindow）
let backendProcess = null;
let backendPort = null;
let isQuitting = false;
let backendLogTail = [];

const HEALTH_TIMEOUT_MS = 90000; // 首次启动需加载模型/初始化数据库，给足时间
const HEALTH_INTERVAL_MS = 500;

// ============ 日志落盘 ============
// 打包后的 Electron 在 Windows 上是 GUI 子系统程序，没有可用的 stdout，
// console.log 会全部丢失 —— 出错时用户只看到弹窗，拿不到任何细节。
// 因此关键事件同时写入 <TINGKEBANLV_HOME>/logs/electron.log，便于事后定位。

let logFilePath = null;

function initLogFile() {
  try {
    const home = process.env.TINGKEBANLV_HOME || path.join(os.homedir(), '.tingkebanlv');
    const dir = path.join(home, 'logs');
    fs.mkdirSync(dir, { recursive: true });
    logFilePath = path.join(dir, 'electron.log');
    // 简易轮转：超过 1MB 就转存为 .1，避免长期运行无限增长
    if (fs.existsSync(logFilePath) && fs.statSync(logFilePath).size > 1024 * 1024) {
      fs.renameSync(logFilePath, logFilePath + '.1');
    }
  } catch (_) {
    logFilePath = null; // 落盘不可用不应影响应用运行
  }
}

function logLine(message) {
  const line = `[${new Date().toISOString()}] ${message}`;
  console.log(line);
  if (!logFilePath) return;
  try {
    fs.appendFileSync(logFilePath, line + '\n');
  } catch (_) {
    // 忽略：日志失败不能阻断主流程
  }
}

// ============ 路径解析 ============

/**
 * 后端目录
 * 打包后 __dirname 位于 app.asar 内，相对路径 ..\..\backend 不存在，
 * 必须改从 process.resourcesPath 读取（extraResources 落点）。
 */
function getBackendDir() {
  if (process.env.TINGKEBANLV_BACKEND_DIR) {
    return process.env.TINGKEBANLV_BACKEND_DIR;
  }
  if (app.isPackaged) {
    const packaged = path.join(process.resourcesPath, 'backend');
    if (fs.existsSync(packaged)) return packaged;
  }
  return path.resolve(__dirname, '..', '..', 'backend');
}

/**
 * 前端构建产物目录
 *
 * 打包后由后端同源托管，后端需要知道 dist 在哪。源码模式下后端能自己从
 * `__file__` 推出来，但冻结成 exe 后 `__file__` 指向 _internal 内部，
 * 推不出 resourcesPath —— 由主进程显式告知最稳妥。
 */
function resolveDistDir() {
  if (process.env.TINGKEBANLV_DIST_DIR) return process.env.TINGKEBANLV_DIST_DIR;
  if (app.isPackaged) {
    const packaged = path.join(process.resourcesPath, 'dist');
    if (fs.existsSync(path.join(packaged, 'index.html'))) return packaged;
  }
  return null;
}

/**
 * 内置模型种子目录
 *
 * 安装包把 base 模型放在 resources/whisper_models（extraResources，不进 asar），
 * 后端首次启动时把它播种到 TINGKEBANLV_HOME/whisper_models —— 即「程序本体只读、
 * 模型数据可写」，这样后续单独更新模型不必让用户重装整个应用。
 *
 * 后端自己也能从 exe 位置反推出该目录（见 asr/model_bootstrap.py），
 * 但显式传递更稳妥：冻结后 sys.executable 的推导依赖目录层级未被改动。
 */
function resolveSeedModelDir() {
  if (process.env.TINGKEBANLV_SEED_MODEL_DIR) return process.env.TINGKEBANLV_SEED_MODEL_DIR;
  if (app.isPackaged) {
    const packaged = path.join(process.resourcesPath, 'whisper_models');
    if (fs.existsSync(path.join(packaged, 'manifest.json'))) return packaged;
  }
  return null;
}

/**
 * 解析可用的后端启动方式
 *
 * 关键：Windows 上不存在 python3 命令，原代码 spawn('python3') 会直接 ENOENT。
 * 这里不仅探测命令是否存在，还验证它真的能 import fastapi，
 * 避免选到一个空壳解释器（如未装依赖的全局 Python）。
 *
 * 返回对象的 frozen 字段表示「已冻结的独立后端」，它不接受脚本路径参数，
 * 启动方式与解释器模式不同（见 startBackendProcess）。
 */
function resolvePythonCommand() {
  const backendDir = getBackendDir();

  // 1) 打包后的独立可执行文件优先
  //    这是安装包在真实用户机器上**唯一**可靠的路径：目标机器没有 Python，
  //    也没有 fastapi/uvicorn，下面第 4 步的探测必然全部失败。
  if (app.isPackaged) {
    const candidates = [
      path.join(process.resourcesPath, 'backend-server', 'tingkebanlv-server.exe'),
      path.join(process.resourcesPath, 'backend-server', 'tingkebanlv-server'),
    ];
    for (const bundled of candidates) {
      if (fs.existsSync(bundled)) {
        return { command: bundled, args: [], display: bundled, frozen: true };
      }
    }
    logLine('[backend] 未找到冻结后端（resources/backend-server），回退到 Python 探测');
  }

  // 2) 环境变量指定
  if (process.env.TINGKEBANLV_PYTHON) {
    return { command: process.env.TINGKEBANLV_PYTHON, args: [], display: process.env.TINGKEBANLV_PYTHON, frozen: false };
  }

  // 3) 项目自带 venv
  const venvPython = process.platform === 'win32'
    ? path.join(backendDir, '..', '..', '安装空间', 'venv', 'Scripts', 'python.exe')
    : path.join(backendDir, '..', '..', '安装空间', 'venv', 'bin', 'python');
  if (fs.existsSync(venvPython)) {
    return { command: venvPython, args: [], display: venvPython, frozen: false };
  }

  // 4) 系统解释器候选（顺序即优先级）
  const candidates = process.platform === 'win32'
    ? [
        { command: 'py', args: ['-3'], display: 'py -3' },
        { command: 'python', args: [], display: 'python' },
      ]
    : [
        { command: 'python3', args: [], display: 'python3' },
        { command: 'python', args: [], display: 'python' },
      ];

  // 逐个探测：能跑通 import fastapi 的才算可用
  const { spawnSync } = require('child_process');
  for (const candidate of candidates) {
    try {
      const probe = spawnSync(
        candidate.command,
        [...candidate.args, '-c', 'import fastapi, uvicorn'],
        { stdio: 'ignore', timeout: 15000 }
      );
      if (probe.status === 0) return { ...candidate, frozen: false };
    } catch (_) {
      // 该候选不可用，继续探测下一个
    }
  }

  return null;
}

// ============ 桌面字幕悬浮窗 ============
//
// 为什么必须做成独立窗口：应用内的字幕页只在主窗口里可见，学生一切到浏览器
// 看网课就看不到字幕了 —— 而「切到浏览器看课」恰恰是这个产品的核心使用姿势。
//
// 设计要点：
//   1. 设置由主进程持有并落盘到 <TINGKEBANLV_HOME>/overlay.json。位置/不透明度
//      这类属性只能作用于 BrowserWindow 本身，真理源放渲染进程必然错位。
//   2. 鼠标穿透用 setIgnoreMouseEvents(true, { forward: true })。穿透时若不传
//      forward，下层窗口连 hover 都收不到，网课页面上的按钮高亮会失效。
//   3. 穿透状态下窗口不可点也不可拖，因此提供「调整模式」（Alt+Shift+D）：
//      临时关掉穿透并显示拖拽条，调完再切回。
//   4. 生命周期必须与主窗口绑定：主窗口一关就销毁悬浮窗，否则会留下一个
//      没有入口、永远置顶、只能靠任务管理器杀掉的孤儿窗口。
//
// 注意：悬浮窗**不自己轮询** /api/asr/poll（该端点取出即清空，两个窗口同时拉
// 会互相抢消息）。字幕由主窗口经 IPC 转发，见 src/stores/useSubtitleStore.ts。

/** 与 src/lib/overlayBridge.ts 的 DEFAULT_OVERLAY_SETTINGS 保持一致 */
const DEFAULT_OVERLAY_SETTINGS = {
  enabled: false,
  fontSize: 24,
  color: '#ffffff',
  lineCount: 3,
  textBgOpacity: 0.55,
  windowOpacity: 1,
  clickThrough: true,
  bounds: { x: null, y: null, width: 900, height: 220 },
};

let overlaySettings = null;

function overlaySettingsPath() {
  const home = process.env.TINGKEBANLV_HOME || path.join(os.homedir(), '.tingkebanlv');
  return path.join(home, 'overlay.json');
}

function clampNumber(value, lo, hi, fallback) {
  // 空值一律走 fallback：Number(null) === 0 会把「没配」误判成「配了 0」，
  // 结果是被夹到下界（例如字号变成 12）而不是默认值，用户无从理解。
  if (value === null || value === undefined || value === '') return fallback;
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(hi, Math.max(lo, n));
}

/**
 * 归一化设置
 * 落盘文件可能被手工改坏（例如 color 写成 "white" 或 fontSize 给成 200），
 * 这里统一夹到安全区间 —— 越界值会让窗口变成不可见或铺满屏幕，很难靠用户自己恢复。
 */
function normalizeOverlaySettings(raw) {
  const src = raw && typeof raw === 'object' ? raw : {};
  const b = src.bounds && typeof src.bounds === 'object' ? src.bounds : {};
  // 注意：不能写成 Number.isFinite(Number(v)) —— Number(null) === 0，
  // 会把「首次运行、还没存过位置」的 null 静默转成 0，于是窗口永远被判定为
  // 「已经在屏幕上（左上角）」，默认的底部居中位置一次都算不出来。
  const intOrNull = (v) => {
    if (v === null || v === undefined || v === '') return null;
    const n = Number(v);
    return Number.isFinite(n) ? Math.round(n) : null;
  };
  return {
    enabled: !!src.enabled,
    fontSize: Math.round(clampNumber(src.fontSize, 12, 72, DEFAULT_OVERLAY_SETTINGS.fontSize)),
    color: typeof src.color === 'string' && /^#[0-9a-fA-F]{6}$/.test(src.color)
      ? src.color
      : DEFAULT_OVERLAY_SETTINGS.color,
    lineCount: Math.round(clampNumber(src.lineCount, 1, 6, DEFAULT_OVERLAY_SETTINGS.lineCount)),
    textBgOpacity: clampNumber(src.textBgOpacity, 0, 1, DEFAULT_OVERLAY_SETTINGS.textBgOpacity),
    windowOpacity: clampNumber(src.windowOpacity, 0.2, 1, DEFAULT_OVERLAY_SETTINGS.windowOpacity),
    clickThrough: src.clickThrough === undefined ? true : !!src.clickThrough,
    bounds: {
      x: intOrNull(b.x),
      y: intOrNull(b.y),
      width: Math.round(clampNumber(b.width, 320, 4000, DEFAULT_OVERLAY_SETTINGS.bounds.width)),
      height: Math.round(clampNumber(b.height, 100, 2000, DEFAULT_OVERLAY_SETTINGS.bounds.height)),
    },
  };
}

function loadOverlaySettings() {
  if (overlaySettings) return overlaySettings;
  try {
    const file = overlaySettingsPath();
    overlaySettings = fs.existsSync(file)
      ? normalizeOverlaySettings(JSON.parse(fs.readFileSync(file, 'utf8')))
      : normalizeOverlaySettings(null);
  } catch (err) {
    logLine(`[overlay] 读取设置失败，回退默认值: ${err.message}`);
    overlaySettings = normalizeOverlaySettings(null);
  }
  return overlaySettings;
}

function saveOverlaySettings() {
  if (!overlaySettings) return;
  try {
    const file = overlaySettingsPath();
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify(overlaySettings, null, 2), 'utf8');
  } catch (err) {
    logLine(`[overlay] 保存设置失败: ${err.message}`);
  }
}

function isOverlayOpen() {
  return !!overlayWindow && !overlayWindow.isDestroyed();
}

function sameBounds(a, b) {
  return a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height;
}

/** 悬浮窗尺寸是否与某个显示器工作区有足够重叠（否则就是「跑到屏幕外了」） */
function isOnSomeDisplay(b) {
  if (b.x === null || b.y === null) return false;
  return screen.getAllDisplays().some((d) => {
    const wa = d.workArea;
    return (
      b.x + 80 < wa.x + wa.width &&
      b.x + b.width - 80 > wa.x &&
      b.y + 20 < wa.y + wa.height &&
      b.y + b.height - 20 > wa.y
    );
  });
}

/**
 * 修正落盘的窗口位置
 *
 * 两种必须兜底的情况：
 *   1. 首次运行（x/y 为 null）→ 贴主屏工作区底部居中
 *   2. 上次保存的位置已经不在任何显示器上（外接屏拔了、分辨率变了、用户拖出了屏幕）
 *      → 同样回到默认位。否则窗口会永久不可见，而它是鼠标穿透的，
 *        用户既点不到也没法拖回来，只能手工改 overlay.json。
 */
function resolveBoundsForDisplay(bounds) {
  const b = { ...bounds };
  if (!isOnSomeDisplay(b)) {
    const wa = screen.getPrimaryDisplay().workArea;
    b.x = Math.round(wa.x + (wa.width - b.width) / 2);
    b.y = Math.round(wa.y + wa.height - b.height - 80);
  }
  return b;
}

/** 把设置里与窗口属性相关的部分真正作用到 BrowserWindow 上 */
function applyOverlaySettingsToWindow() {
  if (!isOverlayOpen()) return;
  const s = loadOverlaySettings();
  try {
    overlayWindow.setOpacity(s.windowOpacity);
  } catch (err) {
    logLine(`[overlay] 设置窗口透明度失败: ${err.message}`);
  }
  try {
    if (s.clickThrough) {
      // forward: true —— 让 hover / 光标形状仍能穿透到下层窗口
      overlayWindow.setIgnoreMouseEvents(true, { forward: true });
      overlayWindow.setFocusable(false);
    } else {
      overlayWindow.setIgnoreMouseEvents(false);
      overlayWindow.setFocusable(true);
    }
  } catch (err) {
    logLine(`[overlay] 应用鼠标穿透失败: ${err.message}`);
  }
}

/** 把最新设置广播给两个渲染进程（悬浮窗用它渲染样式，主窗口用它同步 UI） */
function broadcastOverlaySettings() {
  const s = loadOverlaySettings();
  if (isOverlayOpen() && !overlayWindow.webContents.isDestroyed()) {
    overlayWindow.webContents.send('overlay:settings', s);
  }
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('overlay:settings', s);
  }
}

function notifyOverlayState() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('overlay:state', { open: isOverlayOpen() });
  }
}

/** 悬浮窗入口地址：hash 路由 /overlay（见 src/App.tsx） */
function overlayPageUrl() {
  const dev = process.env.VITE_DEV_SERVER_URL;
  if (dev) return `${dev.replace(/#.*$/, '')}#/overlay`;
  return `http://127.0.0.1:${backendPort}#/overlay`;
}

/**
 * 悬浮窗关键路径自检（排障 / 自动化验证用，默认关闭）
 *
 * 为什么需要它：`did-finish-load` 并不能证明 hash 路由真的匹配上了 ——
 * 路由写错时 index.html 照样加载成功，只是渲染成主应用（一块不透明的 CourseList），
 * 表现为「悬浮窗是黑的、不透明、也永远不动」。这类失败在日志里完全看不出来。
 * 这里在加载完成后回读页面真实状态（透明类是否挂上、body 背景是否透明、
 * root 下渲染了几个节点），把「看起来加载成功」和「真的渲染正确」区分开。
 *
 * 开启方式：TINGKEBANLV_OVERLAY_DIAG=1
 */
function runOverlayDiagnostics() {
  if (process.env.TINGKEBANLV_OVERLAY_DIAG !== '1') return;
  if (!isOverlayOpen()) return;
  overlayWindow.webContents
    .executeJavaScript(
      `(() => {
         const root = document.getElementById('root');
         return {
           hash: location.hash,
           transparentClass: document.documentElement.classList.contains('overlay-transparent'),
           bodyBg: getComputedStyle(document.body).backgroundColor,
           rootChildren: root ? root.childElementCount : -1,
           text: (root && root.innerText ? root.innerText : '').slice(0, 60),
         };
       })()`
    )
    .then((info) => logLine(`[overlay][diag] ${JSON.stringify(info)}`))
    .catch((err) => logLine(`[overlay][diag] 回读失败: ${err.message}`));
}

function createOverlayWindow() {
  if (isOverlayOpen()) return overlayWindow;

  const b = resolveBoundsForDisplay(loadOverlaySettings().bounds);
  // 把修正后的位置落盘：否则下次启动还要再算一遍，且 overlay.json 里的位置长期是坏的
  overlaySettings.bounds = b;
  saveOverlaySettings();

  overlayWindow = new BrowserWindow({
    x: b.x,
    y: b.y,
    width: b.width,
    height: b.height,
    minWidth: 320,
    minHeight: 100,
    frame: false,
    transparent: true,
    backgroundColor: '#00000000',
    hasShadow: false,
    resizable: true,
    movable: true,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    show: false,
    title: '听课伴侣字幕',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.cjs'),
      // 学生切到浏览器看课时主窗口在后台，默认节流会把 500ms 轮询降到分钟级
      backgroundThrottling: false,
    },
  });

  // 'screen-saver' 是最高层级：不设的话会被浏览器全屏（F11）压在下面
  overlayWindow.setAlwaysOnTop(true, 'screen-saver');
  if (process.platform === 'darwin') {
    overlayWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  }

  overlayWindow.once('ready-to-show', () => {
    if (!isOverlayOpen()) return;
    overlayWindow.showInactive(); // 不抢焦点，用户正在浏览器里打字时不该被打断
    applyOverlaySettingsToWindow();
    logLine(`[overlay] 悬浮窗已显示（穿透=${loadOverlaySettings().clickThrough ? '开' : '关'}）`);
  });

  overlayWindow.webContents.once('did-finish-load', () => {
    logLine('[overlay] 页面加载完成');
    broadcastOverlaySettings();
    runOverlayDiagnostics();
  });

  overlayWindow.webContents.on('did-fail-load', (_e, errorCode, errorDesc, validatedURL) => {
    logLine(`[overlay] 页面加载失败 code=${errorCode} desc=${errorDesc} url=${validatedURL}`);
  });

  // 用户拖动 / 缩放后落盘，下次启动还原到同一位置
  let persistTimer = null;
  const persistBounds = () => {
    if (persistTimer) clearTimeout(persistTimer);
    persistTimer = setTimeout(() => {
      // 窗口创建/映射过程中也会触发 moved，此时窗口尚未显示、getBounds() 还是 0,0；
      // 不挡掉的话会把「贴屏幕底部居中」的默认位置覆盖成左上角（实测踩过）。
      if (!isOverlayOpen() || !overlayWindow.isVisible()) return;
      const nb = overlayWindow.getBounds();
      if (nb.width < 100 || nb.height < 50) return;
      if (sameBounds(overlaySettings.bounds, nb)) return;
      overlaySettings.bounds = { x: nb.x, y: nb.y, width: nb.width, height: nb.height };
      saveOverlaySettings();
      logLine(`[overlay] 位置/尺寸已保存: ${nb.x},${nb.y} ${nb.width}x${nb.height}`);
      broadcastOverlaySettings();
    }, 400);
  };
  overlayWindow.on('moved', persistBounds);
  overlayWindow.on('resized', persistBounds);

  overlayWindow.on('closed', () => {
    overlayWindow = null;
    notifyOverlayState();
  });

  const url = overlayPageUrl();
  const wa = screen.getPrimaryDisplay().workArea;
  logLine(
    `[overlay] 窗口位置: ${b.x},${b.y} ${b.width}x${b.height} ` +
    `（主屏工作区 ${wa.x},${wa.y} ${wa.width}x${wa.height}）`
  );
  logLine(`[overlay] 加载入口: ${url}`);
  overlayWindow.loadURL(url);
  return overlayWindow;
}

/** 仅销毁窗口，不改动 enabled（用于主窗口关闭 / 应用退出） */
function destroyOverlayWindow() {
  if (!isOverlayOpen()) return;
  const win = overlayWindow;
  overlayWindow = null; // 先置空：closed 回调据此判断是否还需广播状态
  try {
    win.destroy();
  } catch (err) {
    logLine(`[overlay] 销毁悬浮窗失败: ${err.message}`);
  }
}

/** 用户主动关闭：同时记住「不要再自动打开」 */
function closeOverlayWindow() {
  loadOverlaySettings().enabled = false;
  saveOverlaySettings();
  destroyOverlayWindow();
  notifyOverlayState();
}

function openOverlayWindow() {
  loadOverlaySettings().enabled = true;
  saveOverlaySettings();
  createOverlayWindow();
  notifyOverlayState();
}

/** 调整模式：临时关掉鼠标穿透，让用户可以拖动窗口 */
function toggleOverlayInteractive() {
  if (!isOverlayOpen()) return;
  const s = loadOverlaySettings();
  s.clickThrough = !s.clickThrough;
  saveOverlaySettings();
  applyOverlaySettingsToWindow();
  broadcastOverlaySettings();
  logLine(`[overlay] 鼠标穿透=${s.clickThrough ? '开' : '关（调整模式）'}`);
}

// ============ 端口分配 ============

/**
 * 申请一个空闲端口
 * 注意存在「关闭探测 socket 到子进程绑定」之间的竞态窗口，
 * 因此调用方在启动失败时会重试。
 */
function findFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

// ============ 后端健康检查 ============

/**
 * 探测后端是否已能接受请求
 *
 * 刻意用 /api/ping 而不是 /api/health：
 *   /api/health 会做真实探测（曾包含一次外部 LLM 请求），在受限网络下
 *   单次耗时可达 2 秒以上，超过本函数的超时阈值 —— 于是每次探测都判定失败，
 *   界面永远等不到就绪，90 秒后弹「后端启动超时」。
 *   /api/ping 不碰数据库、不碰外部服务，恒定毫秒级返回。
 *
 * 超时也从 2s 放宽到 5s：后端首次导入 ctranslate2/onnxruntime 时偶有卡顿，
 * 没必要把「慢」误判成「没起来」。
 */
const READY_PATH = '/api/ping';
const READY_TIMEOUT_MS = 5000;

function probeHealth(port) {
  return new Promise((resolve) => {
    const req = http.get(
      { host: '127.0.0.1', port, path: READY_PATH, timeout: READY_TIMEOUT_MS },
      (res) => {
        res.resume();
        resolve(res.statusCode === 200);
      }
    );
    req.on('error', () => resolve(false));
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });
  });
}

/** 轮询等待后端就绪；后端起不来时抛错，由调用方弹窗告知用户 */
async function waitForBackend(port, timeoutMs = HEALTH_TIMEOUT_MS) {
  const started = Date.now();
  const deadline = started + timeoutMs;
  let attempts = 0;
  let nextNoticeAt = 15000;

  while (Date.now() < deadline) {
    if (isQuitting) throw new Error('应用正在退出');
    if (!backendProcess || backendProcess.exitCode !== null) {
      throw new Error('后端进程已退出');
    }
    if (await probeHealth(port)) return;

    attempts += 1;
    // 长时间等不到就绪时定期留痕：否则用户只能看到「启动超时」，
    // 完全不知道卡在哪一步（实践中正是这条日志定位到 /api/health 超时问题）
    const elapsed = Date.now() - started;
    if (elapsed >= nextNoticeAt) {
      logLine(
        `[backend] 等待就绪 ${Math.round(elapsed / 1000)}s：` +
        `${READY_PATH} 已探测 ${attempts} 次仍未返回 200`
      );
      nextNoticeAt += 15000;
    }
    await new Promise((r) => setTimeout(r, HEALTH_INTERVAL_MS));
  }
  throw new Error(`后端启动超时（${Math.round(timeoutMs / 1000)} 秒）`);
}

// ============ 后端进程管理 ============

function startBackendProcess(port, python) {
  const backendDir = getBackendDir();
  const distDir = resolveDistDir();
  const seedModelDir = resolveSeedModelDir();
  if (seedModelDir) {
    logLine(`[backend] 内置模型种子: ${seedModelDir}`);
  } else if (app.isPackaged) {
    logLine('[backend] 未找到内置模型种子（resources/whisper_models），首启将需要联网下载模型');
  }

  // 冻结后的 exe 把入口脚本编译进自身，**不能再传 main.py**：
  // 那会被当成参数交给 PyInstaller 引导器，行为未定义。
  // 工作目录也改为 exe 所在目录，避免依赖 resources/backend 是否存在。
  const args = python.frozen ? [...python.args] : [...python.args, 'main.py'];
  const cwd = python.frozen ? path.dirname(python.command) : backendDir;

  backendProcess = spawn(python.command, args, {
    cwd,
    env: {
      ...process.env,
      PYTHONUNBUFFERED: '1',
      PYTHONIOENCODING: 'utf-8', // 冻结后日志含中文，显式指定避免 GBK 编码错误
      TINGKEBANLV_PORT: String(port),
      TINGKEBANLV_HOST: '127.0.0.1',
      TINGKEBANLV_RELOAD: '0', // 打包/桌面运行一律关闭热重载
      ...(distDir ? { TINGKEBANLV_DIST_DIR: distDir } : {}),
      ...(seedModelDir ? { TINGKEBANLV_SEED_MODEL_DIR: seedModelDir } : {}),
    },
    // 非 Windows 建独立进程组，退出时才能整组信号终止
    detached: process.platform !== 'win32',
    windowsHide: true,
  });

  backendLogTail = [];
  const record = (line) => {
    backendLogTail.push(line);
    if (backendLogTail.length > 40) backendLogTail.shift();
  };

  // 后端输出按行分流：既进内存尾部缓冲（用于失败弹窗），也落盘到日志文件
  const onBackendData = (stream, chunk) => {
    const text = chunk.toString();
    stream.write(`[Backend] ${text}`);
    for (const raw of text.split(/\r?\n/)) {
      const line = raw.trim();
      if (!line) continue;
      record(line);
      logLine(`[backend] ${line}`);
    }
  };

  backendProcess.stdout.on('data', (d) => onBackendData(process.stdout, d));
  backendProcess.stderr.on('data', (d) => onBackendData(process.stderr, d));
  backendProcess.on('error', (err) => {
    logLine(`[backend] 进程启动失败: ${err.message}`);
    record(`启动失败: ${err.message}`);
  });
  backendProcess.on('close', (code) => {
    logLine(`[backend] 进程退出，code=${code}`);
    backendProcess = null;
  });
}

/**
 * 终止整棵后端进程树
 *
 * Windows 上 child.kill() 只杀直接子进程，uvicorn 派生的子进程会残留并占住端口，
 * 导致下次启动端口冲突 —— 必须用 taskkill /T 递归结束。
 */
function killBackend() {
  if (!backendProcess) return;
  const pid = backendProcess.pid;
  backendProcess = null;

  try {
    if (process.platform === 'win32') {
      spawn('taskkill', ['/PID', String(pid), '/T', '/F'], {
        stdio: 'ignore',
        windowsHide: true,
      });
    } else {
      try {
        process.kill(-pid, 'SIGTERM'); // 负号 = 整个进程组
      } catch (_) {
        process.kill(pid, 'SIGTERM');
      }
    }
  } catch (err) {
    logLine(`[backend] 终止进程失败: ${err.message}`);
  }
}

async function launchBackend() {
  const python = resolvePythonCommand();
  if (!python) {
    throw new Error(
      '未找到可用的后端。\n\n' +
      '【安装版】请确认安装目录 resources\\backend-server 是否存在且完整\n' +
      '（部分杀毒软件会隔离其中的原生库 .dll）。\n\n' +
      '【开发模式】请安装 Python 3.10+ 并执行：\n' +
      'pip install -r backend/requirements.txt\n' +
      '或设置环境变量 TINGKEBANLV_PYTHON 指定解释器路径。'
    );
  }
  logLine(`[backend] 后端目录: ${getBackendDir()}`);
  logLine(`[backend] 启动方式: ${python.frozen ? '冻结 exe' : 'Python 解释器'} -> ${python.display}`);

  // 端口竞态最多重试 3 次
  let lastError = null;
  for (let attempt = 1; attempt <= 3; attempt++) {
    const port = process.env.TINGKEBANLV_PORT
      ? Number(process.env.TINGKEBANLV_PORT)
      : await findFreePort();

    logLine(`[backend] 尝试启动 (第 ${attempt} 次, port=${port})`);
    startBackendProcess(port, python);

    try {
      await waitForBackend(port);
      backendPort = port;
      logLine(`[backend] 就绪: http://127.0.0.1:${port}`);
      return port;
    } catch (err) {
      lastError = err;
      logLine(`[backend] 第 ${attempt} 次启动失败: ${err.message}`);
      killBackend();
      await new Promise((r) => setTimeout(r, 800));
    }
  }

  throw new Error(
    `后端服务启动失败：${lastError ? lastError.message : '未知原因'}\n\n` +
    (backendLogTail.length ? `最后日志：\n${backendLogTail.slice(-12).join('\n')}` : '')
  );
}

// ============ 窗口 ============

// 后端此时已确认就绪，正常环境下页面 1 秒内即可渲染完成，
// 因此该阈值只用于识别「渲染器卡死」这一异常，不会误伤慢机器。
const FIRST_PAINT_TIMEOUT_MS = 12000;
// 兼容模式下仍失败说明不是沙箱问题，再等下去没有意义
const COMPAT_REPAINT_TIMEOUT_MS = 20000;

/**
 * 以兼容模式拉起一个新实例
 *
 * 这里刻意不用 app.relaunch()：它只是登记重启意图，真正拉起新进程依赖
 * Electron 的正常退出流程，实测在本环境下会静默失效 —— 老进程退出了、
 * 新实例从未出现，且不产生任何报错，极难排查。
 * 直接 spawn 自身可执行文件，行为完全可控、可验证。
 *
 * 注意 appPath：打包后 process.execPath 本身就是应用（不需要路径参数），
 * 但源码模式下它是 node_modules/electron/dist/electron.exe —— 不带应用路径时
 * Electron 会退回到默认 app，找不到 main.cjs 便直接退出，且**不留任何日志**，
 * 表现为「应用闪一下消失、日志停在 watchdog 那一行」。开发模式排查兼容问题时必看此处。
 */
function relaunchInCompatMode() {
  try {
    const args = app.isPackaged
      ? [...COMPAT_RELAUNCH_ARGS]
      : [...COMPAT_RELAUNCH_ARGS, app.getAppPath()];
    const child = spawn(process.execPath, args, {
      detached: true, // 独立进程组，父进程退出后仍存活
      stdio: 'ignore',
      cwd: app.getAppPath(),
      env: process.env,
    });
    child.unref();
    logLine(`[watchdog] 已拉起兼容模式实例 pid=${child.pid} args=${args.join(' ')}`);
    return true;
  } catch (err) {
    logLine(`[watchdog] 拉起兼容模式实例失败: ${err.message}`);
    return false;
  }
}

function armRenderWatchdog() {
  const timeout = COMPAT_MODE ? COMPAT_REPAINT_TIMEOUT_MS : FIRST_PAINT_TIMEOUT_MS;

  const watchdog = setTimeout(() => {
    if (COMPAT_MODE) {
      // 已经关掉沙箱还是渲染不出来，只能把控制权交回用户
      logLine('[watchdog] 兼容模式下界面仍未渲染完成，停止自动恢复');
      dialog.showErrorBox(
        '听课伴侣界面加载失败',
        '界面无法渲染，自动兼容模式也未能恢复。\n\n' +
        `请把日志文件发给开发者：\n${logFilePath || '(日志未落盘)'}`
      );
      app.quit();
      return;
    }
    logLine(
      `[watchdog] 页面 ${timeout / 1000}s 内未完成加载，判定为 Chromium 沙箱被系统策略拦截，` +
      '以兼容模式（--no-sandbox）重启'
    );
    if (!relaunchInCompatMode()) {
      dialog.showErrorBox(
        '听课伴侣启动失败',
        '界面无法渲染，且自动重启兼容模式失败。\n\n' +
        `请把日志文件发给开发者：\n${logFilePath || '(日志未落盘)'}`
      );
    }
    app.quit();
  }, timeout);

  return () => clearTimeout(watchdog);
}

function createWindow(port) {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1100,
    minHeight: 700,
    title: '听课伴侣 - 实时字幕 · 交互学词',
    show: false, // 等页面渲染完再显示，避免白屏闪烁
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.cjs'),
      // 关键：学生开始识别后就会切到浏览器看网课，主窗口随即进入后台。
      // Chromium 默认会把后台窗口的定时器降到分钟级，而 ASR 字幕靠 500ms
      // 轮询驱动 —— 不关掉节流，字幕会在切走的一瞬间整体停住。
      backgroundThrottling: false,
    },
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
  });

  mainWindow.once('ready-to-show', () => mainWindow.show());

  const devServerUrl = process.env.VITE_DEV_SERVER_URL;
  let cancelWatchdog = () => {};

  if (devServerUrl) {
    // 开发模式：Vite 提供页面并代理 /api
    // 不挂看门狗 —— 开发服务器未启动属于正常情况，不应触发兼容模式重启
    mainWindow.loadURL(devServerUrl);
    mainWindow.webContents.openDevTools();
  } else {
    // 生产模式：由后端同源托管前端，fetch('/api/...') 可直接工作
    mainWindow.loadURL(`http://127.0.0.1:${port}`);
    cancelWatchdog = armRenderWatchdog();
  }

  mainWindow.webContents.once('did-finish-load', () => {
    cancelWatchdog();
    logLine(`[window] 界面加载完成${COMPAT_MODE ? '（兼容模式：已关闭 Chromium 沙箱）' : ''}`);
  });

  mainWindow.webContents.on('did-fail-load', (_e, errorCode, errorDesc, validatedURL) => {
    logLine(`[window] 页面加载失败 code=${errorCode} desc=${errorDesc} url=${validatedURL}`);
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
    // 主窗口是悬浮窗的唯一入口与生命周期锚点：主窗口没了就必须销毁悬浮窗，
    // 否则会留下一个永远置顶、点不掉、也没有 UI 能关掉它的孤儿窗口。
    destroyOverlayWindow();
  });
}

// ============ 生命周期 ============

// 尽早初始化日志：连「GPU 规避开关是否生效」也要能在日志里回查
initLogFile();
logLine(`===== 听课伴侣启动 v${app.getVersion()} =====`);
logLine(`[env] 打包模式=${app.isPackaged} 平台=${process.platform} Electron=${process.versions.electron}`);
logLine(
  `[env] disable-gpu-sandbox=` +
  (process.env.TINGKEBANLV_GPU_SANDBOX === '1' ? '未启用（用户显式恢复默认）' : '已启用')
);
logLine(`[env] 运行模式=${COMPAT_MODE ? '兼容模式（Chromium 沙箱已关闭）' : '标准模式'}`);

/**
 * 注册悬浮窗全局热键
 *
 * 悬浮窗默认鼠标穿透，用户点不到它也拖不动它 —— 必须留一条键盘通道，
 * 否则「想挪一下位置」这件事在 UI 上无解。
 * 注册失败（热键被别的软件占用）不应影响主流程，只记日志。
 */
function registerOverlayShortcuts() {
  const bind = (accelerator, handler, label) => {
    try {
      const ok = globalShortcut.register(accelerator, handler);
      logLine(`[overlay] 热键 ${accelerator}（${label}）注册${ok ? '成功' : '失败（可能被占用）'}`);
    } catch (err) {
      logLine(`[overlay] 热键 ${accelerator}（${label}）注册异常: ${err.message}`);
    }
  };
  bind('Alt+Shift+O', () => {
    if (isOverlayOpen()) closeOverlayWindow();
    else openOverlayWindow();
  }, '开关悬浮窗');
  bind('Alt+Shift+D', toggleOverlayInteractive, '切换调整模式');
}

/** 上次退出时悬浮窗是开着的 → 本次启动自动恢复 */
function restoreOverlayIfEnabled() {
  if (!loadOverlaySettings().enabled) return;
  try {
    createOverlayWindow();
  } catch (err) {
    logLine(`[overlay] 自动恢复悬浮窗失败: ${err.message}`);
  }
}

app.whenReady().then(async () => {
  try {
    const port = await launchBackend();
    createWindow(port);
    restoreOverlayIfEnabled();
    registerOverlayShortcuts();
  } catch (err) {
    logLine(`[fatal] 启动失败: ${err.message}`);
    dialog.showErrorBox(
      '听课伴侣启动失败',
      `${err.message}\n\n详细日志：${logFilePath || '(未能写入日志文件)'}`
    );
    app.quit();
    return;
  }

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0 && backendPort) {
      createWindow(backendPort);
      // macOS 关掉主窗口不退出应用，重新激活时按上次的 enabled 把悬浮窗一并还原
      restoreOverlayIfEnabled();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  isQuitting = true;
  destroyOverlayWindow();
  try {
    globalShortcut.unregisterAll();
  } catch (_) {
    /* 忽略 */
  }
  killBackend();
});

// 兜底：异常退出路径也要清理，避免残留 uvicorn 占端口
process.on('uncaughtException', (err) => {
  logLine(`[fatal] 未捕获异常: ${err && err.stack ? err.stack : err}`);
});
process.on('exit', killBackend);
process.on('SIGINT', () => {
  killBackend();
  process.exit(0);
});
process.on('SIGTERM', () => {
  killBackend();
  process.exit(0);
});

// ============ IPC ============

ipcMain.handle('get-app-version', () => app.getVersion());
ipcMain.handle('get-backend-port', () => backendPort);

// ---- 悬浮窗 ----

ipcMain.handle('overlay:get-settings', () => loadOverlaySettings());

ipcMain.handle('overlay:get-state', () => ({
  open: isOverlayOpen(),
  settings: loadOverlaySettings(),
}));

ipcMain.handle('overlay:set-settings', (_event, patch) => {
  const current = loadOverlaySettings();
  const incoming = patch && typeof patch === 'object' ? patch : {};
  const merged = normalizeOverlaySettings({
    ...current,
    ...incoming,
    // bounds 单独合并：渲染进程只改字号时不应把已有位置冲成 null
    bounds: { ...current.bounds, ...(incoming.bounds || {}) },
  });
  overlaySettings = merged;
  saveOverlaySettings();

  if (isOverlayOpen()) {
    const nb = overlayWindow.getBounds();
    const want = merged.bounds;
    const target = {
      x: want.x === null ? nb.x : want.x,
      y: want.y === null ? nb.y : want.y,
      width: want.width,
      height: want.height,
    };
    if (!sameBounds(nb, target)) {
      try {
        overlayWindow.setBounds(target);
      } catch (err) {
        logLine(`[overlay] 应用窗口尺寸失败: ${err.message}`);
      }
    }
    applyOverlaySettingsToWindow();
  }

  broadcastOverlaySettings();
  return merged;
});

ipcMain.handle('overlay:open', () => {
  openOverlayWindow();
  return { open: isOverlayOpen() };
});

ipcMain.handle('overlay:close', () => {
  closeOverlayWindow();
  return { open: isOverlayOpen() };
});

/**
 * 把悬浮窗挪回默认位置（贴主屏工作区底部居中）
 *
 * 为什么需要单独的通道：设置里的 bounds.x/y 允许为 null（表示「没存过位置」），
 * 于是 normalize 之后再回传 null 也无从区分「用户想把窗口放回默认位」。
 * 用户把窗口拖到屏幕外或拖到只露出一角时，这是唯一的自救入口。
 */
ipcMain.handle('overlay:reset-position', () => {
  const s = loadOverlaySettings();
  const wa = screen.getPrimaryDisplay().workArea;
  const target = {
    x: Math.round(wa.x + (wa.width - s.bounds.width) / 2),
    y: Math.round(wa.y + wa.height - s.bounds.height - 80),
    width: s.bounds.width,
    height: s.bounds.height,
  };
  s.bounds = target;
  overlaySettings = s;
  saveOverlaySettings();
  if (isOverlayOpen()) {
    try {
      overlayWindow.setBounds(target);
    } catch (err) {
      logLine(`[overlay] 重置位置失败: ${err.message}`);
    }
  }
  broadcastOverlaySettings();
  return s;
});

// 字幕快照转发：主窗口（唯一轮询者）→ 主进程 → 悬浮窗
ipcMain.on('overlay:subtitle', (_event, payload) => {
  if (!isOverlayOpen() || overlayWindow.webContents.isDestroyed()) return;
  overlayWindow.webContents.send('overlay:subtitle', payload);
});
