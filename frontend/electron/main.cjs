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
//
// **记忆化**：一台机器一旦确认需要兼容模式，就把标记写进数据目录，之后直接以
// 兼容模式启动。否则每次开应用都要「先崩一次渲染进程 → 白等 12 秒 → 再重启一遍」，
// 而在部分机器上那次崩溃还会连带触发崩溃上报（实测最坏情况整个会话被拖住
// 20 分钟），代价远高于读一个标记文件。
// 恢复默认（重新启用渲染器沙箱）：设置 TINGKEBANLV_RENDER_SANDBOX=1，并删掉标记文件。
function dataHome() {
  return process.env.TINGKEBANLV_HOME || path.join(os.homedir(), '.tingkebanlv');
}

/** 兼容模式记忆标记；任何读写失败都当作「没有」，绝不因此影响启动 */
const COMPAT_MARKER = path.join(dataHome(), '.compat-mode');

function compatMarkerExists() {
  if (process.env.TINGKEBANLV_RENDER_SANDBOX === '1') return false;
  try {
    return fs.existsSync(COMPAT_MARKER);
  } catch (_) {
    return false;
  }
}

function markCompatModeNeeded() {
  try {
    fs.mkdirSync(path.dirname(COMPAT_MARKER), { recursive: true });
    fs.writeFileSync(
      COMPAT_MARKER,
      JSON.stringify(
        { at: new Date().toISOString(), reason: '渲染器沙箱被系统策略拦截，自动降级' },
        null,
        2
      ),
      'utf8'
    );
  } catch (_) {
    /* 写不进去只会退化成「每次先试标准模式」，功能不受影响 */
  }
}

const FORCED_COMPAT = compatMarkerExists();
const COMPAT_MODE = process.argv.includes('--tingkebanlv-compat') || FORCED_COMPAT;
const COMPAT_RELAUNCH_ARGS = ['--no-sandbox', '--tingkebanlv-compat'];

// 命令行走的是 --no-sandbox 参数；从标记恢复时没有这个参数，必须自己补上，
// 否则「这台机器需要兼容模式」这个判断只改了超时阈值、沙箱照样拦，白白再崩一次。
if (COMPAT_MODE) {
  app.commandLine.appendSwitch('no-sandbox');
}

// (3) 远程调试端口（排障用，默认关闭）
// 悬浮窗是独立渲染进程，出问题时必须单独挂 DevTools 才能看到它的控制台；
// 自动化验证也要靠 CDP 才能回读它的真实 DOM。
// 注意：开放该端口等于允许本机任意进程接管页面，因此只在显式设置
// TINGKEBANLV_DEBUG_PORT 时开启，默认一个端口都不监听。
if (process.env.TINGKEBANLV_DEBUG_PORT) {
  app.commandLine.appendSwitch('remote-debugging-port', String(process.env.TINGKEBANLV_DEBUG_PORT));
}

// (4) 单实例
// 为什么必须挡：一个实例已经拉起后端、占住动态端口、并且在写同一份 overlay.json
// 与日志。允许第二份实例的后果是「界面开了但拿不到端口 / 两个窗口来回覆盖设置」，
// 表现为随机的空白页与设置不生效，排查成本极高。
// 第二次启动时把已有窗口提到前台 —— 这符合用户「我再点一次图标」的预期。
// 自动化验证需要同时跑多个实例时用 TINGKEBANLV_ALLOW_MULTI=1 放开。
let singleInstanceOwner = true;
if (process.env.TINGKEBANLV_ALLOW_MULTI !== '1') {
  singleInstanceOwner = app.requestSingleInstanceLock();
  if (!singleInstanceOwner) {
    // 此刻日志文件还没初始化（initLogFile 在 whenReady 之前），只能走 stdout
    console.log('[single-instance] 检测到已有实例在运行，本次启动直接退出');
    app.quit();
  }
}

let mainWindow = null;
let overlayWindow = null; // 桌面字幕悬浮窗（独立 BrowserWindow）
// 首启向导（独立 BrowserWindow）
//
// 为什么不复用悬浮窗：悬浮窗是 frame:false + transparent + 鼠标穿透的置顶窗口，
// 它的存在意义就是「点不到」。向导恰恰相反，必须可点、可聚焦、可关。
// 两者共用一个窗口会立刻在 setIgnoreMouseEvents 上打架。
// 也不做成主窗口内的浮层：向导要能在主窗口被最小化时依然存在（首次启动时
// 主窗口可能还在白屏），独立窗口的生命周期更好推理。
let onboardingWindow = null;
let backendProcess = null;
let backendPort = null;
let isQuitting = false;
let backendLogTail = [];

/**
 * 主进程 → 悬浮窗的轻提示频道。
 *
 * 提成常量是因为它有**多个**发射点（重置位置、快捷键调字号），
 * 而接收端在 preload.cjs 里按字符串订阅 —— 任一处写错都不会报错，
 * 只会「什么提示都不出现」。这类静默失败值得用常量挡一下。
 */
const TOAST_CHANNEL = 'overlay:toast';
/** 主窗口当前是否处于「后台节流」状态；null = 还没应用过 */
let mainThrottled = null;
/** 主窗口渲染进程是否健在（悬浮窗据此显示断连提示） */
let hostAlive = true;

const HEALTH_TIMEOUT_MS = 90000; // 首次启动需加载模型/初始化数据库，给足时间
const HEALTH_INTERVAL_MS = 500;

// ============ 日志（分级 + 按日期滚动 + 限大小） ============
// 打包后的 Electron 在 Windows 上是 GUI 子系统程序，没有可用的 stdout，
// console.log 会全部丢失 —— 出错时用户只看到弹窗，拿不到任何细节。
// 因此关键事件同时写入 <TINGKEBANLV_HOME>/logs/electron.log，便于事后定位。
//
// 分级理由：排障时最需要的是「哪一行是异常」，而不是在几千行 info 里翻。
// 级别由 TINGKEBANLV_LOG_LEVEL 控制（debug|info|warn|error，默认 info）。
// 按日期滚动理由：单文件无限增长会让「导出日志发给我」这件事毫无用处 ——
// 收上来的报告里 99% 是几个月前的旧内容。每天一个文件，只留 7 天。

const LOG_LEVELS = { debug: 10, info: 20, warn: 30, error: 40 };
const LOG_LEVEL = LOG_LEVELS[String(process.env.TINGKEBANLV_LOG_LEVEL || 'info').toLowerCase()]
  || LOG_LEVELS.info;
const LOG_MAX_BYTES = 2 * 1024 * 1024;
const LOG_KEEP_DAYS = 7;

let logFilePath = null;

function logDir() {
  const home = process.env.TINGKEBANLV_HOME || path.join(os.homedir(), '.tingkebanlv');
  return path.join(home, 'logs');
}

function dayStamp(d) {
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** 清掉超过保留期的归档日志（不碰 electron.log 本身） */
function pruneOldLogs(dir) {
  try {
    const cutoff = Date.now() - LOG_KEEP_DAYS * 86400000;
    for (const name of fs.readdirSync(dir)) {
      if (!/^electron-.+\.log$/.test(name) && !/^crash-.+\.json$/.test(name)) continue;
      const full = path.join(dir, name);
      try {
        if (fs.statSync(full).mtimeMs < cutoff) fs.unlinkSync(full);
      } catch (_) {
        /* 单个文件删不掉不影响其它 */
      }
    }
  } catch (_) {
    /* 目录读不到就算了 */
  }
}

function initLogFile() {
  try {
    const dir = logDir();
    fs.mkdirSync(dir, { recursive: true });
    logFilePath = path.join(dir, 'electron.log');
    // 跨天：昨天的日志改名归档，保证 electron.log 永远是「最近一次运行」
    if (fs.existsSync(logFilePath)) {
      const st = fs.statSync(logFilePath);
      if (dayStamp(new Date(st.mtimeMs)) !== dayStamp(new Date())) {
        try {
          fs.renameSync(
            logFilePath,
            path.join(dir, `electron-${dayStamp(new Date(st.mtimeMs))}.log`)
          );
        } catch (_) {
          /* 改名失败就继续往原文件追加，不影响功能 */
        }
      }
    }
    // 限大小：同一天也可能连续跑很久
    if (fs.existsSync(logFilePath) && fs.statSync(logFilePath).size > LOG_MAX_BYTES) {
      try {
        fs.renameSync(logFilePath, path.join(dir, `electron-${dayStamp(new Date())}.1.log`));
      } catch (_) {
        /* 同上 */
      }
    }
    pruneOldLogs(dir);
  } catch (_) {
    logFilePath = null; // 落盘不可用不应影响应用运行
  }
}

/**
 * @param {string} message
 * @param {'debug'|'info'|'warn'|'error'} [level='info'] 默认 info，历史调用点无需改动
 */
function logLine(message, level = 'info') {
  const lv = LOG_LEVELS[level] ? level : 'info';
  if (LOG_LEVELS[lv] < LOG_LEVEL) return;
  const line = `[${new Date().toISOString()}] [${lv.toUpperCase()}] ${message}`;
  if (lv === 'error') console.error(line);
  else console.log(line);
  if (!logFilePath) return;
  try {
    fs.appendFileSync(logFilePath, line + '\n');
  } catch (_) {
    // 忽略：日志失败不能阻断主流程
  }
}

const logDebug = (msg) => logLine(msg, 'debug');
const logWarn = (msg) => logLine(msg, 'warn');
const logError = (msg) => logLine(msg, 'error');

/**
 * 崩溃现场留档
 * 进程崩溃时日志可能只写到一半，甚至日志文件本身没来得及 flush。
 * 单独落一个小 JSON（版本 / 平台 / 堆栈 / 当时的日志路径），
 * 「导出日志」会一并打包，用户不必知道去哪找。
 */
function writeCrashReport(kind, err) {
  try {
    const dir = logDir();
    fs.mkdirSync(dir, { recursive: true });
    const at = new Date();
    const payload = {
      kind,
      at: at.toISOString(),
      appVersion: app.getVersion(),
      electron: process.versions.electron,
      chrome: process.versions.chrome,
      node: process.versions.node,
      platform: process.platform,
      osRelease: os.release(),
      arch: process.arch,
      compatMode: COMPAT_MODE,
      message: err && err.message ? err.message : String(err),
      stack: err && err.stack ? err.stack : null,
      logFile: logFilePath,
    };
    fs.writeFileSync(
      path.join(dir, `crash-${dayStamp(at)}-${at.getTime()}.json`),
      JSON.stringify(payload, null, 2),
      'utf8'
    );
    return true;
  } catch (_) {
    return false;
  }
}

// ============ 性能埋点（本地，绝不上传） ============
// 只需要回答一个问题：这个应用在后台是不是真的还在干活。
// 因此记的都是「事件计数 + 状态切换次数」，不含任何用户内容。
const perfStats = {
  startedAt: Date.now(),
  subtitleForwards: 0,
  settingsBroadcasts: 0,
  throttleFlips: 0,
  heartbeats: 0,
  heartbeatMisses: 0,
  displayCorrections: 0,
  overlayOpens: 0,
};

function perfSnapshot() {
  return {
    ...perfStats,
    uptimeMs: Date.now() - perfStats.startedAt,
    throttled: typeof mainThrottled === 'boolean' ? mainThrottled : null,
    overlayOpen: isOverlayOpen(),
    hostAlive: typeof hostAlive === 'boolean' ? hostAlive : null,
  };
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

// 配置的解析 / 夹取 / 迁移 / 原子落盘全部收在 electron/overlay-settings.cjs 里 ——
// 那段逻辑承载「用户设置不能丢」，历史上出过一次不报错的 bug
// （Number(null)===0 把「未存位置」变成坐标 0）。放在这里既跑不了单测也没人看。
// 纯逻辑、fs 由调用方注入，因此 frontend/tests/overlay-settings.test.cjs 能覆盖
// 损坏文件 / 旧版本 / 越界值 / 写到一半崩溃 / 锁被占用等全部分支。
const {
  SCHEMA_VERSION,
  DEFAULT_OVERLAY_SETTINGS,
  normalizeOverlaySettings,
  sameBounds,
  isOnSomeDisplay,
  computeAnchor,
  resolveBoundsForDisplay,
  writeConfigAtomic,
  readConfig,
} = require('./overlay-settings.cjs');

// 悬浮窗动作的「副作用决策」（日志文案 / toast 载荷 / 触发来源归一）
//
// 同一个动作有多个入口（控制面板 / 热键 / 启动恢复 / 显示器自愈），
// 原先各自在调用点实现「要不要弹 toast、要不要打日志」，于是漏了两处
// （Backlog B1：按钮无 toast；B3：热键开关无日志）。
// 把决策收拢到该模块后，各入口只需声明自己是谁，行为一致由结构保证 ——
// 且因为该模块不 require('electron')，可以被 node --test 直接覆盖。
const overlayActions = require('./overlay-actions.cjs');

// 首启向导状态（onboarding.json）
//
// 与上面两个模块同一套路：决定「要不要弹向导 / 走到第几步 / 完成后还弹不弹」
// 的判断全在这个不 require('electron') 的纯模块里，于是能被 node --test 覆盖。
// 这段逻辑一旦写错（比如判断顺序反了），表现是「用户每次开机都被弹一遍」，
// 而它只能靠重启应用来人工验证 —— 不值得赌。
const onboardingState = require('./onboarding-state.cjs');

let overlaySettings = null;

function overlaySettingsPath() {
  return path.join(dataHome(), 'overlay.json');
}

/**
 * 读设置（首次调用时加载并缓存）
 *
 * 三种来源都要能起来：文件正常 / 文件不存在 / 文件损坏。
 * 损坏时把原文件改名保留为 .bad 再退默认 —— 直接删掉会让用户彻底无法自救，
 * 留着至少还能人工改回。
 */
function loadOverlaySettings() {
  if (overlaySettings) return overlaySettings;
  const file = overlaySettingsPath();
  const res = readConfig(fs, file);
  if (res.reason === 'corrupt') {
    logWarn(`[overlay] 配置文件损坏（${res.error}），已回退出厂默认；原文件另存为 ${path.basename(file)}.bad`);
    try {
      fs.renameSync(file, `${file}.bad`);
    } catch (_) {
      /* 改名失败也必须继续启动 */
    }
  } else if (res.reason === 'absent') {
    logDebug('[overlay] 尚无配置文件，使用出厂默认');
  }
  overlaySettings = res.settings;
  if (res.migrated) {
    logLine(`[overlay] 配置已从旧版本迁移到 schemaVersion=${SCHEMA_VERSION}`);
    saveOverlaySettings(); // 顺手落盘，避免每次启动都再迁一遍
  }
  return overlaySettings;
}

function saveOverlaySettings() {
  if (!overlaySettings) return;
  try {
    fs.mkdirSync(path.dirname(overlaySettingsPath()), { recursive: true });
    writeConfigAtomic(fs, overlaySettingsPath(), overlaySettings);
  } catch (err) {
    logError(`[overlay] 保存设置失败: ${err.message}`);
  }
}

/**
 * 把「术语提示词自动降级」开关推给后端
 *
 * 为什么要多这一跳：开关的真理源在 overlay.json（主进程），而真正执行降级的
 * 是后端 Python 里的 WhisperEngine。渲染进程不直接调后端 —— 它只在 Electron 里
 * 才有 IPC，浏览器里调试时 window.electronAPI 是 undefined，那时开关就静默失效了。
 * 统一从主进程推，路径只有一条。
 *
 * 幂等：值没变就不重复 POST。这个函数会在「后端就绪」和「每次设置变更」时被调，
 * 不缓存的话用户每拖一次滑块就打一次后端。
 */
let pushedTermAutoDegrade = null;

function pushTermGuards(force) {
  if (!backendPort) return false;
  if (!overlaySettings) return false;
  const want = overlaySettings.termAutoDegrade !== false;
  if (!force && pushedTermAutoDegrade === want) return false;
  pushedTermAutoDegrade = want;
  // fetch 是 Node 20+ 的内置实现（Electron 31），不需要额外依赖。
  // 走 127.0.0.1 不经系统代理：undici 默认不读 HTTP_PROXY，这里天然安全。
  fetch(`http://127.0.0.1:${backendPort}/api/asr/config`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ termAutoDegrade: want }),
  })
    .then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      logLine(`[overlay] 术语提示词自动降级已同步到后端: ${want ? '开启' : '关闭'}`);
    })
    .catch((err) => {
      // 推送失败不能影响任何交互：后端没起来时本来就会失败，下次设置变更会再推
      pushedTermAutoDegrade = null;
      logWarn(`[overlay] 同步术语降级开关失败: ${err.message}`);
    });
  return true;
}

function isOverlayOpen() {
  return !!overlayWindow && !overlayWindow.isDestroyed();
}

/** 向悬浮窗推消息；窗口不在时静默返回 false（不抛，调用方无需层层判断） */
function sendToOverlay(channel, payload) {
  if (!isOverlayOpen()) return false;
  try {
    if (overlayWindow.webContents.isDestroyed()) return false;
    overlayWindow.webContents.send(channel, payload);
    return true;
  } catch (err) {
    logWarn(`[overlay] 推送 ${channel} 失败: ${err.message}`);
    return false;
  }
}

function sendToMain(channel, payload) {
  if (!mainWindow || mainWindow.isDestroyed()) return false;
  // 主窗口渲染进程挂掉后，BrowserWindow 对象还在、帧却已经没了。此时
  // webContents.send() 不会抛错（我们的 try/catch 拦不住），而是由 Electron
  // 内部打一行
  //   Error sending from webFrameMain: Error: Render frame was disposed ...
  // 直接刷在用户 stdout 上，看起来像应用报错。实测就是这么来的：
  // render-process-gone → markHostAlive(false) → sendToMain 打到已销毁的帧。
  // 所以这里以「主窗口是否健在」为准直接不发 —— 主窗口重新加载后，
  // 设置与状态会被整体重播一遍，不会漏。
  if (!hostAlive) return false;
  try {
    mainWindow.webContents.send(channel, payload);
    return true;
  } catch (err) {
    logWarn(`[window] 推送 ${channel} 失败: ${err.message}`);
    return false;
  }
}

/** 悬浮窗入口地址 */
function overlayPageUrl() {
  const dev = process.env.VITE_DEV_SERVER_URL;
  if (dev) return `${dev.replace(/#.*$/, '')}#/overlay`;
  return `http://127.0.0.1:${backendPort}#/overlay`;
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
  perfStats.settingsBroadcasts++;
  sendToOverlay('overlay:settings', s);
  sendToMain('overlay:settings', s);
}

function notifyOverlayState() {
  sendToMain('overlay:state', { open: isOverlayOpen() });
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

  const s = loadOverlaySettings();
  // 位置修正走「绝对位置 → 相对锚点 → 默认底部居中」三级兜底（详见 overlay-settings.cjs）
  const b = resolveBoundsForDisplay(
    s.bounds,
    screen.getPrimaryDisplay().workArea,
    screen.getAllDisplays(),
    s.anchor
  );
  // 把修正后的位置与锚点落盘：否则下次启动还要再算一遍，且 overlay.json 里的位置长期是坏的
  overlaySettings.bounds = b;
  overlaySettings.anchor = computeAnchor(b, screen.getDisplayMatching(b));
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
      // 悬浮窗自己绝不节流：它就是给「切到浏览器看网课」这个场景用的，
      // 一旦节流，字幕会在用户最需要的时候停住。
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
    perfStats.overlayOpens++;
    logLine(
      `[overlay] 悬浮窗已显示（穿透=${loadOverlaySettings().clickThrough ? '开' : '关'}）`
    );
    applyMainWindowThrottling(); // 出口出现了，主窗口可以不用省电了
  });

  overlayWindow.webContents.once('did-finish-load', () => {
    logLine('[overlay] 页面加载完成');
    broadcastOverlaySettings();
    // 主动推一次宿主状态：悬浮窗重启后不该停在「主窗口已断开」的陈旧提示上
    sendToOverlay('overlay:host-status', { alive: hostAlive, reason: 'init', at: Date.now() });
    runOverlayDiagnostics();
  });

  overlayWindow.webContents.on('did-fail-load', (_e, errorCode, errorDesc, validatedURL) => {
    logError(`[overlay] 页面加载失败 code=${errorCode} desc=${errorDesc} url=${validatedURL}`);
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
      // 同步刷新相对锚点：下次显示器插拔/换分辨率就靠它把窗口搬回来
      overlaySettings.anchor = computeAnchor(nb, screen.getDisplayMatching(nb));
      saveOverlaySettings();
      logLine(
        `[overlay] 位置/尺寸已保存: ${nb.x},${nb.y} ${nb.width}x${nb.height} ` +
        `(锚点 屏${overlaySettings.anchor.displayId} ${overlaySettings.anchor.relX},${overlaySettings.anchor.relY})`
      );
      broadcastOverlaySettings();
    }, 400);
  };
  overlayWindow.on('moved', persistBounds);
  overlayWindow.on('resized', persistBounds);

  overlayWindow.on('closed', () => {
    overlayWindow = null;
    notifyOverlayState();
    applyMainWindowThrottling(); // 唯一出口没了 → 允许主窗口省电
  });

  const url = overlayPageUrl();
  const wa = screen.getPrimaryDisplay().workArea;
  logLine(
    `[overlay] 窗口位置: ${b.x},${b.y} ${b.width}x${b.height} ` +
    `（主屏工作区 ${wa.x},${wa.y} ${wa.width}x${wa.height}，显示器数 ${screen.getAllDisplays().length}）`
  );
  logLine(`[overlay] 加载入口: ${url}`);
  overlayWindow.loadURL(url);
  return overlayWindow;
}

/**
 * 仅销毁窗口，不改动 enabled（用于主窗口关闭 / 应用退出）
 *
 * 成功路径也打日志：这里原先只在**失败**时 logLine，于是「悬浮窗怎么自己没了」
 * 在日志里查不出来（与 B3 同一类问题 —— 只记了失败，没记成功）。
 */
function destroyOverlayWindow(triggerSource) {
  if (!isOverlayOpen()) return;
  const win = overlayWindow;
  overlayWindow = null; // 先置空：closed 回调据此判断是否还需广播状态
  try {
    win.destroy();
    logLine(overlayActions.describeDestroy(triggerSource).log);
  } catch (err) {
    logLine(`[overlay] 销毁悬浮窗失败: ${err.message}`);
  }
}

/**
 * 用户主动关闭：同时记住「不要再自动打开」
 *
 * 日志放在函数内部而不是调用点 —— 这是 B3 的修法。原先 `Alt+Shift+O`
 * 的开关动作成功时一行日志都没有，用户按了没反应时，日志里
 * 「热键注册成功」之后就是空的，无法区分「按键没生效」和「窗口没显示出来」。
 */
function closeOverlayWindow(triggerSource) {
  loadOverlaySettings().enabled = false;
  saveOverlaySettings();
  destroyOverlayWindow(triggerSource);
  notifyOverlayState();
  applyMainWindowThrottling();
  logLine(overlayActions.describeClose(triggerSource).log);
}

function openOverlayWindow(triggerSource) {
  loadOverlaySettings().enabled = true;
  saveOverlaySettings();
  createOverlayWindow();
  notifyOverlayState();
  // 立刻取消节流：窗口 ready-to-show 还要一小会儿，这期间字幕不该丢
  applyMainWindowThrottling();
  logLine(overlayActions.describeOpen(triggerSource).log);
}

/**
 * 开关悬浮窗 —— 热键与其它入口共用的**唯一**决策点。
 *
 * 「当前开着吗 → 该开还是该关」这段判断放在这里，而不是散在每个入口里。
 * 各入口只需要声明自己是谁（triggerSource），日志与文案由
 * open/closeOverlayWindow 内部统一产出，结构上不可能再出现
 * 「一条路径有日志、另一条没有」。
 */
function toggleOverlayWindow(triggerSource) {
  if (isOverlayOpen()) closeOverlayWindow(triggerSource);
  else openOverlayWindow(triggerSource);
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

/**
 * 悬浮窗挪回主屏底部居中（穿透窗口跑出屏幕后唯一的自救入口）
 *
 * ⚠️ toast 必须**在函数内部**发，不能在调用点发 —— 这是 B1 的修法。
 * 原先热键回调里写了 `sendToOverlay('overlay:toast', ...)`，而控制面板的
 * 「重置位置」按钮直接调本函数、绕过了那一行，于是按钮点了没有任何反馈。
 * 只要还有第二个调用点，放在调用点就一定会再漏一次。
 *
 * Returns:
 *   `{ settings, notice, toastShown }`
 *   - `notice`：给用户看的文案，来自 overlay-actions 的**唯一**常量。
 *     渲染层在 `toastShown === false` 时用它做面板内联提示 ——
 *     这样文案不会在渲染层再写一遍（重复字面量正是 B1/B4 的共同成因）。
 *   - `toastShown`：toast 是否真的送到了悬浮窗。悬浮窗没开着时
 *     `sendToOverlay` 会静默返回 false，此时用户看不到任何反馈。
 */
function resetOverlayPosition(triggerSource) {
  const s = loadOverlaySettings();
  const wa = screen.getPrimaryDisplay().workArea;
  const target = {
    x: Math.round(wa.x + (wa.width - s.bounds.width) / 2),
    y: Math.round(wa.y + wa.height - s.bounds.height - 80),
    width: s.bounds.width,
    height: s.bounds.height,
  };
  s.bounds = target;
  s.anchor = computeAnchor(target, screen.getPrimaryDisplay());
  overlaySettings = s;
  saveOverlaySettings();

  const opened = isOverlayOpen();
  if (opened) {
    try {
      overlayWindow.setBounds(target);
    } catch (err) {
      logWarn(`[overlay] 重置位置失败: ${err.message}`);
    }
  }
  broadcastOverlaySettings();

  const fx = overlayActions.describeReset({ opened, x: target.x, y: target.y }, triggerSource);
  logLine(fx.log);
  // 穿透状态下用户看不到任何反馈，必须主动提示一次
  const toastShown = !!sendToOverlay(TOAST_CHANNEL, { text: fx.toast.text, at: Date.now() });

  return { settings: s, notice: fx.toast.text, toastShown };
}

// ============ 后台节流策略 ============
//
// 背景：字幕靠主窗口 500ms 轮询 /api/asr/poll 驱动，而学生一开始听课就会切到
// 浏览器看网课 —— 主窗口随即进入后台。Chromium 默认把后台窗口的定时器降到
// 分钟级，字幕会在切走的一瞬间整体停住。所以历史上这里是硬编码
// `backgroundThrottling: false`。
//
// 但「永远不节流」的代价是主窗口在完全没人看的时候也在跑轮询（笔记本耗电、
// 风扇转）。折中成可配置：
//   1. 用户在设置里关掉「后台持续更新」→ 一律节流（明确选择，尊重）
//   2. 开着（默认）→ 只有当确实没有任何窗口在展示字幕时才节流省电
//      「出口」= 悬浮窗开着（它在屏幕上）或主窗口可见且未最小化
// 状态切换会写日志（[perf]），方便事后解释「为什么 CPU 占用不同」。

/** 当前是否存在能看到字幕的窗口 */
function hasSubtitleOutlet() {
  if (isOverlayOpen()) return true;
  if (!mainWindow || mainWindow.isDestroyed()) return false;
  return mainWindow.isVisible() && !mainWindow.isMinimized();
}

function applyMainWindowThrottling() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  const s = loadOverlaySettings();
  const want = !(s.backgroundUpdate && hasSubtitleOutlet());
  if (want === mainThrottled) return;
  try {
    // 立刻生效，不需要重建窗口
    mainWindow.webContents.setBackgroundThrottling(want);
  } catch (err) {
    logWarn(`[perf] 设置后台节流失败: ${err.message}`);
    return;
  }
  mainThrottled = want;
  perfStats.throttleFlips++;
  logLine(
    `[perf] 后台节流=${want ? '开（省电）' : '关（字幕持续更新）'}` +
    ` 出口=${isOverlayOpen() ? '悬浮窗' : (hasSubtitleOutlet() ? '主窗口' : '无')}` +
    ` 用户开关 backgroundUpdate=${s.backgroundUpdate}`
  );
}

// ============ 主窗口存活探测 ============
//
// 悬浮窗是独立渲染进程 + 独立窗口，主窗口崩溃时它**不会**跟着消失：
// 用户看到的是一块永远停在最后一句字幕、也永远不更新的浮层，看起来像卡死。
// 明确的「主窗口已断开，请重启应用」比沉默的静止要好得多。
//
// 判定策略：渲染进程的 render-process-gone / unresponsive 是权威信号；
// 心跳是兜底（覆盖 JS 死循环这类事件不触发的情况）。

const HEARTBEAT_STALE_MS = 8000;
let lastHeartbeatAt = 0;
let heartbeatTimer = null;

function markHostAlive(alive, reason) {
  if (alive === hostAlive) return;
  hostAlive = alive;
  const payload = { alive, reason, at: Date.now() };
  if (alive) logLine(`[overlay] 主窗口已恢复（${reason}）`);
  else logError(`[overlay] 主窗口失联（${reason}）→ 悬浮窗将提示用户`);
  sendToOverlay('overlay:host-status', payload);
  sendToMain('overlay:host-status', payload);
}

function startHeartbeatWatch() {
  if (heartbeatTimer) return;
  lastHeartbeatAt = Date.now();
  heartbeatTimer = setInterval(() => {
    // 只在悬浮窗开着时才判失联：悬浮窗开着 → 不节流 → 心跳必然按 2s 到达；
    // 悬浮窗关着时主窗口本来就可能被节流，心跳变慢属于预期，报警只会是误报。
    if (!isOverlayOpen()) {
      lastHeartbeatAt = Date.now();
      markHostAlive(true, '悬浮窗未开，跳过探测');
      return;
    }
    // 宽限期：一次心跳都还没收到过 → 只能说明渲染进程**还没启动完**，不能判失联。
    // 心跳是「活着」的证据；在观察到活之前推不出死。
    //
    // 这个窗口必然存在：whenReady 里的顺序是 createWindow() 紧接着
    // restoreOverlayIfEnabled()，悬浮窗几秒内就显示出来了，而主窗口的 React
    // 要等页面加载完成才挂载（本机踩渲染器沙箱缺陷时还要多一次兼容模式重启，
    // 20s 量级）。没有这条宽限，**每次启动**都会闪一条
    // 「主窗口已断开，请重启应用」的红色横幅 —— 用户学会无视它之后，
    // 这条提示就彻底失去意义了（这正是要做失联提示的那类场景）。
    //
    // 代价：渲染进程从未发出过心跳的死法不会被这里发现。但那类故障由
    // render-process-gone / unresponsive 与启动 watchdog 覆盖，不靠心跳。
    if (perfStats.heartbeats === 0) {
      lastHeartbeatAt = Date.now();
      return;
    }
    if (Date.now() - lastHeartbeatAt > HEARTBEAT_STALE_MS) {
      perfStats.heartbeatMisses++;
      markHostAlive(false, `超过 ${HEARTBEAT_STALE_MS}ms 未收到渲染进程心跳`);
    }
  }, 2000);
}

// ============ 显示器变化自愈 ============
//
// 外接屏拔掉 / 分辨率从 100% 改成 150% / 投屏切换 —— 这些都会让落盘的绝对坐标
// 落到「不存在的那块空间」里。悬浮窗是鼠标穿透的，用户既点不到也拖不回来，
// 只能手工改 overlay.json。所以每次显示器集合或度量变化都必须重新校验一次。

function handleDisplayChange(reason) {
  if (!isOverlayOpen()) return;
  const displays = screen.getAllDisplays();
  if (isOnSomeDisplay(overlaySettings.bounds, displays)) return; // 位置还有效，不打扰用户
  const fixed = resolveBoundsForDisplay(
    overlaySettings.bounds,
    screen.getPrimaryDisplay().workArea,
    displays,
    overlaySettings.anchor
  );
  overlaySettings.bounds = fixed;
  overlaySettings.anchor = computeAnchor(fixed, screen.getDisplayMatching(fixed));
  saveOverlaySettings();
  try {
    overlayWindow.setBounds(fixed);
  } catch (err) {
    logWarn(`[overlay] 显示器变化后设置位置失败: ${err.message}`);
  }
  perfStats.displayCorrections++;
  logWarn(
    `[overlay] 显示器变化(${reason})，位置已自动校正到 ${fixed.x},${fixed.y} ` +
    `（当前显示器数 ${displays.length}）`
  );
  broadcastOverlaySettings();
  sendToOverlay('overlay:position-corrected', {
    x: fixed.x,
    y: fixed.y,
    reason,
    at: Date.now(),
  });
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
      // 后端刚起来时 overlay.json 可能还没读过（读配置在 restoreOverlayIfEnabled 里），
      // 缺了就先读一次再推。已经读过就绝不再读 —— 重复读会把内存里刚改的设置
      // 用磁盘上的旧值覆盖掉（用户在启动过程中改了开关就会丢）。
      if (!overlaySettings) loadOverlaySettings();
      pushTermGuards(true);
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
  // cwd 必须是一个真实存在的**目录**。踩过的坑：打包后 app.getAppPath() 指向
  // `<安装目录>\resources\app.asar` —— 那是一个**文件**，把它当 cwd 传给 spawn，
  // 底层直接以 ENOENT 失败，而错误信息里出现的是 exe 路径，
  // 看起来像「找不到程序」，极容易误判。
  // 这个 bug 只在实装后暴露：源码模式下 getAppPath() 是仓库目录，一切正常，
  // 所以 CDP 冒烟全绿也照样带着它出门。
  const cwd = app.isPackaged ? path.dirname(process.execPath) : app.getAppPath();
  const args = app.isPackaged
    ? [...COMPAT_RELAUNCH_ARGS]
    : [...COMPAT_RELAUNCH_ARGS, app.getAppPath()];

  // 先把标记落下：即便下面 spawn 失败，用户下次手动双击也会直接进兼容模式，
  // 不至于反复卡在同一个坑里
  markCompatModeNeeded();

  try {
    const child = spawn(process.execPath, args, {
      detached: true, // 独立进程组，父进程退出后仍存活
      stdio: 'ignore',
      cwd,
      env: process.env,
    });
    // spawn 的失败是**异步**的（走 'error' 事件）。不挂监听的话它会一路冒到
    // uncaughtException —— 实测就是这样：日志里只剩一行堆栈，用户什么提示都
    // 看不到，而调用方以为已经成功拉起了新实例。挂上监听后，是否成功改由
    // 返回值（pid）决定，调用方才能正确地弹出错误框。
    child.on('error', (err) => {
      logError(`[watchdog] 兼容模式实例启动失败: ${err.message}`);
    });
    // pid 取不到 = 底层 spawn 没成功，此时绝不能报「已拉起」
    if (!child.pid) {
      logError('[watchdog] 兼容模式实例未取到 pid，判定为拉起失败');
      return false;
    }
    child.unref();
    logLine(`[watchdog] 已拉起兼容模式实例 pid=${child.pid} cwd=${cwd} args=${args.join(' ')}`);
    return true;
  } catch (err) {
    logError(`[watchdog] 拉起兼容模式实例失败: ${err.message}`);
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
      // 初始不节流：窗口创建到 ready-to-show 之间页面还在加载，
      // 这期间把定时器降级只会拖慢首屏。真正的策略在 ready-to-show 之后由
      // applyMainWindowThrottling() 按「用户开关 + 有没有窗口在看字幕」决定。
      backgroundThrottling: false,
    },
    // 开发模式下让任务栏/Alt+Tab 也显示真实图标（打包后由 exe 自带，APP_ICON 为
    // undefined → 该选项等于没写）。悬浮窗不设 icon：它 frame:false 且不进任务栏。
    icon: APP_ICON,
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
  });

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
    applyMainWindowThrottling();
  });

  // 主窗口可见性变化会改变「还有没有出口在看字幕」，需要重算节流策略
  for (const evt of ['show', 'hide', 'minimize', 'restore', 'maximize', 'unmaximize']) {
    mainWindow.on(evt, () => applyMainWindowThrottling());
  }

  // 渲染进程崩溃 / 卡死：这是比心跳更权威的信号（心跳只覆盖 JS 逻辑卡住）
  mainWindow.webContents.on('render-process-gone', (_e, details) => {
    const info = details || {};
    logError(
      `[fatal] 主窗口渲染进程退出 reason=${info.reason} exitCode=${info.exitCode}`
    );
    writeCrashReport('render-process-gone', new Error(`reason=${info.reason} exitCode=${info.exitCode}`));
    markHostAlive(false, `渲染进程退出（${info.reason}）`);
  });
  mainWindow.webContents.on('unresponsive', () => {
    logWarn('[perf] 主窗口渲染进程无响应');
    markHostAlive(false, '渲染进程无响应');
  });
  mainWindow.webContents.on('responsive', () => {
    logLine('[perf] 主窗口渲染进程恢复响应');
    markHostAlive(true, '渲染进程恢复响应');
  });
  // 每次页面重载都重置心跳基准，避免「刚 reload 完就被判失联」
  mainWindow.webContents.on('did-finish-load', () => {
    lastHeartbeatAt = Date.now();
    markHostAlive(true, '页面加载完成');
  });

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
    destroyOverlayWindow(overlayActions.TRIGGER_SOURCES.host);
    // 向导同理：它 parent 挂在主窗口上，主窗口关了还留着就是一个无主的模态孤儿。
    // 此时不应该写「跳过」标记 —— 用户只是关了主窗口，不是拒绝了引导。
    destroyOnboardingWindow('主窗口已关闭');
  });
}

// ============ 应用图标与 Windows 任务栏标识（M3）============

/**
 * 应用用户模型 id（AppUserModelID）
 *
 * **必须与 `frontend/package.json` 的 `build.appId` 逐字一致。**
 * electron-builder 会把 appId 写进快捷方式，而运行时的进程 AUMID 由代码决定；
 * 两者不一致时 Windows 认为「这是两个不同的应用」，表现为：
 *   · 任务栏出现两个图标（一个钉住的、一个运行中的）
 *   · 从钉住的图标启动，任务栏却在旁边新开一个
 *   · 将来做原生通知时，通知来源显示错误
 * 这类不一致没有任何报错，只会让用户觉得「这软件有点怪」。
 * 因此 `tests/onboarding-aumid` 那组用例会把它和 package.json 对拍（见测试文件）。
 */
const APP_USER_MODEL_ID = 'com.tingkebanlv.app';

/**
 * 开发模式下传给 BrowserWindow 的图标路径
 *
 * 两个坑都必须处理，且**两个都不报错**（只是图标不出现，很难发现）：
 *
 * 1. `build/` 是 electron-builder 的 buildResources 目录，**不会**被打进
 *    app.asar —— 它只在构建期被用来给 exe 内嵌图标。因此打包后
 *    `__dirname/../build/icon.ico` 必然不存在，传进去毫无意义：
 *    打包版的图标由 exe 自带，不需要也不应该再传 icon。
 * 2. 开发模式下美术资源可能尚未产出（`build/icon.ico` 不存在），
 *    传一个不存在的路径同样静默失效。所以这里显式区分「有」和「没有」，
 *    并在启动日志里写明 —— 否则「图标为什么没生效」只能靠猜。
 *
 * 返回 undefined 时 Electron 使用自身默认图标，不会崩。
 */
function resolveAppIconPath() {
  if (app.isPackaged) return undefined; // 打包版由 exe 内嵌图标决定
  const p = path.join(__dirname, '..', 'build', 'icon.ico');
  try {
    return fs.existsSync(p) ? p : undefined;
  } catch (_) {
    return undefined;
  }
}

const APP_ICON = resolveAppIconPath();

// ============ 首启向导（M3）============

/**
 * 向导状态文件
 *
 * 放在 `dataHome()`（与 `overlay.json` / `logs/` 同级），**不是**
 * `app.getPath('userData')`。设计稿 §2.1 把这两者当成同一个位置写，
 * 实际上 `userData` 在打包后是 `%APPDATA%/听课伴侣/`，而本项目所有状态
 * 一直都在 `dataHome()`（默认 `~/.tingkebanlv`，可用 `TINGKEBANLV_HOME` 覆盖）。
 *
 * 选 `dataHome()` 的决定性理由：冒烟脚本与实装验证都靠 `TINGKEBANLV_HOME`
 * 把状态隔离到临时目录（`smoke-overlay.cjs` 里那句「临时数据目录」）。
 * 若改用 `userData`，跑一次冒烟就会读到**开发者本机真实的** onboarding 状态
 * （向导不会弹，用例假通过），并把 `completed: true` 写进真实用户配置里。
 */
function onboardingPath() {
  return path.join(dataHome(), 'onboarding.json');
}

function loadOnboardingState() {
  const res = onboardingState.readOnboarding(fs, onboardingPath());
  if (res.reason === 'corrupt') {
    logWarn(
      `[onboarding] 状态文件损坏（${res.error}）→ 按「从未引导过」处理；` +
      '最坏后果只是向导再弹一次，不够格像 overlay.json 那样另存 .bad'
    );
  }
  return res.state;
}

function saveOnboardingState(state) {
  try {
    fs.mkdirSync(path.dirname(onboardingPath()), { recursive: true });
    onboardingState.writeOnboarding(fs, onboardingPath(), state);
  } catch (err) {
    logError(`[onboarding] 保存状态失败: ${err.message}`);
  }
}

/** 记录一次向导事件：算新状态 → 落盘 → 打日志。调用点只声明「发生了什么」 */
function recordOnboardingEvent(event, payload) {
  const fx = onboardingState.describeEvent(loadOnboardingState(), event, payload);
  saveOnboardingState(fx.state);
  logLine(fx.log);
  return fx.state;
}

function isOnboardingOpen() {
  return !!onboardingWindow && !onboardingWindow.isDestroyed();
}

function onboardingPageUrl() {
  const dev = process.env.VITE_DEV_SERVER_URL;
  if (dev) return `${dev.replace(/#.*$/, '')}#/onboarding`;
  return `http://127.0.0.1:${backendPort}#/onboarding`;
}

/**
 * 是否应当自动弹出
 *
 * 判据（含判断顺序）全在 `onboarding-state.cjs`，这里只负责把运行时事实喂进去。
 * 之所以不在这里写 if，是因为「顺序写错」的后果是「用户每次开机都被弹一遍」，
 * 而那种 bug 只能靠重启应用来发现。
 */
function shouldAutoShowOnboarding(force) {
  return onboardingState.shouldShowWizard(loadOnboardingState(), {
    force: force === true,
    compatMode: COMPAT_MODE,
    backendReady: !!backendPort,
  });
}

function createOnboardingWindow() {
  if (isOnboardingOpen()) {
    onboardingWindow.show();
    onboardingWindow.focus();
    return onboardingWindow;
  }

  onboardingWindow = new BrowserWindow({
    width: 560,
    height: 660,
    minWidth: 460,
    minHeight: 520,
    title: '欢迎使用听课伴侣',
    show: false, // 与主窗口一致：等渲染完再显示，避免白屏闪一下
    autoHideMenuBar: true,
    // 与页面底色一致（--bg-primary），否则显示瞬间会闪一块别的颜色
    backgroundColor: '#0f0f0f',
    // parent 但**不加 modal**：modal 在 Windows 上会把主窗口的关闭按钮一起卡住，
    // 一旦向导页面渲染失败就变成「打不开也关不掉」的死锁。向导不该锁死主窗口。
    parent: mainWindow && !mainWindow.isDestroyed() ? mainWindow : undefined,
    modal: false,
    // 向导不是悬浮窗：不置顶、不穿透、不隐藏边框 —— 它必须可点、可聚焦、可关
    alwaysOnTop: false,
    resizable: true,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.cjs'),
      // 第 3 步要轮询后端状态；窗口在后台时不能把定时器降级，否则会停在「正在启动」
      backgroundThrottling: false,
    },
  });

  onboardingWindow.once('ready-to-show', () => {
    if (isOnboardingOpen()) onboardingWindow.show();
  });

  onboardingWindow.webContents.on('did-fail-load', (_e, code, desc, url) => {
    logWarn(`[onboarding] 页面加载失败 code=${code} desc=${desc} url=${url}`);
  });

  onboardingWindow.on('closed', () => {
    onboardingWindow = null;
  });

  onboardingWindow.loadURL(onboardingPageUrl());
  logLine(`[onboarding] 向导窗口已创建（${onboardingPageUrl()}）`);
  return onboardingWindow;
}

function destroyOnboardingWindow(reason) {
  if (!isOnboardingOpen()) return;
  try {
    onboardingWindow.destroy();
  } catch (err) {
    logWarn(`[onboarding] 关闭向导窗口失败: ${err.message}`);
  }
  onboardingWindow = null;
  logLine(`[onboarding] 向导窗口已关闭（${reason}）`);
}

/**
 * 首次启动时按需弹出
 *
 * 时机很关键：必须在**后端就绪之后**调用。第 3 步要读真实的模型状态，
 * 后端没起来就只能显示一个假的进度条 —— 而「不要用假进度条」是设计稿里
 * 明确写下的要求（宁可不显示，也不骗人）。
 */
function maybeShowOnboarding(force) {
  const decision = shouldAutoShowOnboarding(force);
  if (!decision.show) {
    logLine(`[onboarding] 本次不弹出（原因：${decision.reason}）`);
    return false;
  }
  recordOnboardingEvent('start', {});
  createOnboardingWindow();
  return true;
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
logLine(
  `[env] 运行模式=${COMPAT_MODE ? '兼容模式（Chromium 沙箱已关闭）' : '标准模式'}` +
  (FORCED_COMPAT ? `（来自记忆标记 ${path.basename(COMPAT_MARKER)}，跳过标准模式重试）` : '')
);

// Windows 任务栏的窗口归组与图标归属由 AppUserModelID 决定，且必须在
// **创建任何窗口之前**设置（因而放在 whenReady 之前、紧邻日志初始化）。
// 打包后由 electron-builder 写进快捷方式的是同一个值；这里不设的话，
// 运行时进程与快捷方式对不上，就是「钉一个、跑起来又多一个」的成因。
if (process.platform === 'win32') {
  app.setAppUserModelId(APP_USER_MODEL_ID);
}
logLine(
  `[env] AppUserModelId=${process.platform === 'win32' ? APP_USER_MODEL_ID : '(非 Windows，未设置)'}`
);
logLine(
  `[env] 应用图标=${APP_ICON || '未找到 build/icon.ico（开发模式下用 Electron 默认图标；打包版由 exe 自带）'}`
);

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
      if (ok) logLine(`[overlay] 热键 ${accelerator}（${label}）注册成功`);
      // 注册失败（被其它软件占用）不能影响主流程，但必须让用户知道
      // 「这个快捷键没生效」—— 否则他会反复按、以为应用坏了。
      else logWarn(`[overlay] 热键 ${accelerator}（${label}）注册失败：已被其它程序占用`);
    } catch (err) {
      logWarn(`[overlay] 热键 ${accelerator}（${label}）注册异常: ${err.message}`);
    }
  };
  // 各入口只声明「我是谁」；日志与文案由 open/close/reset 内部统一产出。
  // 这样热键路径与 UI 路径的副作用由**结构**保证一致，不再靠人记得写。
  bind('Alt+Shift+O', () => toggleOverlayWindow(overlayActions.TRIGGER_SOURCES.hotkey), '开关悬浮窗');
  bind('Alt+Shift+D', toggleOverlayInteractive, '切换调整模式');
  bind('Alt+Shift+R',
    () => resetOverlayPosition(overlayActions.TRIGGER_SOURCES.hotkey),
    '重置悬浮窗位置');
  bind('Alt+Shift+]', () => nudgeFontSize(2), '字幕字号 +2');
  bind('Alt+Shift+[', () => nudgeFontSize(-2), '字幕字号 -2');
}

/** 快捷键调字号：夹取由 normalizeOverlaySettings 负责，这里只管发意图 */
function nudgeFontSize(delta) {
  const s = loadOverlaySettings();
  const next = normalizeOverlaySettings({ ...s, fontSize: s.fontSize + delta });
  overlaySettings = next;
  saveOverlaySettings();
  broadcastOverlaySettings();
  logLine(`[overlay] 字号 = ${next.fontSize}（快捷键 ${delta > 0 ? '+' : ''}${delta}）`);
  sendToOverlay(TOAST_CHANNEL, { text: `字号 ${next.fontSize}`, at: Date.now() });
}

/** 上次退出时悬浮窗是开着的 → 本次启动自动恢复 */
function restoreOverlayIfEnabled() {
  if (!loadOverlaySettings().enabled) return;
  try {
    createOverlayWindow();
    // 只补日志、不改行为：这里刻意不改成调 openOverlayWindow()，
    // 免得顺带引入 notifyOverlayState / 写配置这些启动期副作用。
    logLine(overlayActions.describeOpen(overlayActions.TRIGGER_SOURCES.startup).log);
  } catch (err) {
    logError(`[overlay] 自动恢复悬浮窗失败: ${err.message}`);
  }
}

/** 显示器集合 / 度量变化时重新校验悬浮窗位置（外接屏拔掉、改 DPI、投屏切换） */
function registerDisplayWatchers() {
  const onChange = (reason) => () => {
    try {
      handleDisplayChange(reason);
    } catch (err) {
      logWarn(`[overlay] 处理显示器变化(${reason})异常: ${err.message}`);
    }
  };
  screen.on('display-added', onChange('显示器接入'));
  screen.on('display-removed', onChange('显示器移除'));
  screen.on('display-metrics-changed', onChange('显示器参数变化（分辨率/缩放）'));
}

if (singleInstanceOwner) {
  app.on('second-instance', () => {
    logLine('[single-instance] 检测到第二次启动，将已有窗口提到前台');
    if (mainWindow && !mainWindow.isDestroyed()) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();
    }
  });

  app.whenReady().then(async () => {
    try {
      const port = await launchBackend();
      createWindow(port);
      restoreOverlayIfEnabled();
      registerOverlayShortcuts();
      registerDisplayWatchers();
      startHeartbeatWatch();
      applyMainWindowThrottling();
      // 首启向导放在最后：此时后端已就绪（第 3 步要读真实模型状态），
      // 主窗口也已开始渲染 —— 先让用户看到主界面，再弹出引导，不会出现
      // 「向导盖着一块白屏」的观感。
      maybeShowOnboarding();
    } catch (err) {
      logError(`[fatal] 启动失败: ${err.message}`);
      writeCrashReport('startup-failure', err);
      dialog.showErrorBox(
        '听课伴侣启动失败',
        `${err.message}\n\n详细日志：${logFilePath || '(未能写入日志文件)'}`
      );
      app.quit();
      return;
    }

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0 && backendPort) {
        mainThrottled = null; // 新窗口要重新决策，否则会跳过 setBackgroundThrottling
        createWindow(backendPort);
        // macOS 关掉主窗口不退出应用，重新激活时按上次的 enabled 把悬浮窗一并还原
        restoreOverlayIfEnabled();
        applyMainWindowThrottling();
        // 首次激活时如果没有渲染进程崩溃过，上面那次 maybeShowOnboarding 已经弹过；
        // 这里的判断是幂等的（completed/dismissed 会直接返回 false）。
        maybeShowOnboarding();
      }
    });
  });
} else {
  // 非首实例：不启动后端、不建窗口。留一条日志便于事后确认「为什么没反应」。
  app.whenReady().then(() => {
    console.log('[single-instance] 非首实例，不做任何初始化');
  });
}

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  isQuitting = true;
  destroyOverlayWindow(overlayActions.TRIGGER_SOURCES.host);
  destroyOnboardingWindow('应用退出');
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
  try {
    globalShortcut.unregisterAll();
  } catch (_) {
    /* 忽略 */
  }
  killBackend();
});

// 兜底：异常退出路径也要清理，避免残留 uvicorn 占端口
// 同时留一份崩溃现场（版本/平台/堆栈），「导出日志」会一并打包 ——
// 用户不需要知道去哪找，也说不清「刚才报了什么错」。
process.on('uncaughtException', (err) => {
  logError(`[fatal] 未捕获异常: ${err && err.stack ? err.stack : err}`);
  writeCrashReport('uncaughtException', err);
});
process.on('unhandledRejection', (reason) => {
  const err = reason instanceof Error ? reason : new Error(String(reason));
  logError(`[fatal] 未处理的 Promise 拒绝: ${err.stack || err.message}`);
  writeCrashReport('unhandledRejection', err);
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
  // backgroundUpdate 可能刚被改过，节流策略要跟着走
  applyMainWindowThrottling();
  // termAutoDegrade 归后端执行，本地存下来后必须同步过去（值没变时内部会跳过）
  pushTermGuards(false);
  return merged;
});

ipcMain.handle('overlay:open', () => {
  openOverlayWindow(overlayActions.TRIGGER_SOURCES.ui);
  return { open: isOverlayOpen() };
});

ipcMain.handle('overlay:close', () => {
  closeOverlayWindow(overlayActions.TRIGGER_SOURCES.ui);
  return { open: isOverlayOpen() };
});

/**
 * 把悬浮窗挪回默认位置（贴主屏工作区底部居中）
 *
 * 为什么需要单独的通道：设置里的 bounds.x/y 允许为 null（表示「没存过位置」），
 * 于是 normalize 之后再回传 null 也无从区分「用户想把窗口放回默认位」。
 * 用户把窗口拖到屏幕外或拖到只露出一角时，这是唯一的自救入口。
 *
 * 返回 `{ settings, notice, toastShown }`：toast 已经在
 * `resetOverlayPosition()` 内部发过（所以热键与按钮两条路径必然一致），
 * 这里额外把 `notice` 回给渲染层 —— 悬浮窗没开着时 toast 无处显示
 * （`toastShown === false`），控制面板据此改用自己的内联提示，
 * 文案直接复用主进程这一份，避免渲染层再写一遍字面量。
 */
ipcMain.handle('overlay:reset-position', () =>
  resetOverlayPosition(overlayActions.TRIGGER_SOURCES.ui));

// 字幕快照转发：主窗口（唯一轮询者）→ 主进程 → 悬浮窗
ipcMain.on('overlay:subtitle', (_event, payload) => {
  perfStats.subtitleForwards++;
  sendToOverlay('overlay:subtitle', payload);
});

// ---- 首启向导 ----

/**
 * 向导的 IPC 全部走主进程
 *
 * 真理源是 `<数据目录>/onboarding.json`，而渲染进程没有 fs。
 * 渲染层只负责「现在第几步、用户点了什么」，落盘与判断都在主进程 ——
 * 否则向导窗口与主窗口各记一份状态必然打架。
 */
ipcMain.handle('onboarding:get-state', () => {
  const decision = shouldAutoShowOnboarding(false);
  return { state: loadOnboardingState(), autoShow: decision.show, reason: decision.reason, totalSteps: onboardingState.TOTAL_STEPS };
});

/** 每进入一步上报一次：stepReached 只增不减，用于定位「用户卡在第几步」 */
ipcMain.handle('onboarding:step', (_event, step) => recordOnboardingEvent('step', { step }));

ipcMain.handle('onboarding:complete', () => {
  const state = recordOnboardingEvent('complete', {});
  destroyOnboardingWindow('走完全部步骤');
  return state;
});

ipcMain.handle('onboarding:dismiss', () => {
  const state = recordOnboardingEvent('dismiss', {});
  destroyOnboardingWindow('用户跳过');
  return state;
});

/**
 * 关闭向导窗口但**不写任何标记**
 *
 * 右上角的 × 与「跳过」语义不同：跳过是「别再来烦我」（写 dismissedAt），
 * × 是「现在不想看」（什么都不写）→ 下次启动还会弹出一次。
 * 这是刻意的：用户并没有表达拒绝，替他记成拒绝就没有回头路了。
 */
ipcMain.handle('onboarding:close', () => {
  destroyOnboardingWindow('用户关闭窗口（未表态，下次启动仍会弹出）');
  return { open: isOnboardingOpen() };
});

/**
 * 「重新查看新手引导」
 *
 * 用户跳过后必须留一条找回路径，否则那个按钮等于一次性 —— 点了就再也见不到
 * 向导（设计稿 §7）。force 会越过 completed/dismissed，也会越过兼容模式的
 * 「推迟」判断：推迟针对的是**自动**弹出，不是禁止用户主动打开。
 */
ipcMain.handle('onboarding:reopen', () => {
  recordOnboardingEvent('start', {});
  createOnboardingWindow();
  return { open: isOnboardingOpen() };
});

// ---- 存活心跳 / 诊断 ----

/**
 * 渲染进程心跳（主窗口每 2s 一次）
 * 用 ipcMain.on 而不是 handle：心跳是高频单向通知，走 handle 会让渲染侧
 * 每 2 秒产生一个无用的 Promise 与 resolver，纯属浪费。
 */
ipcMain.on('app:heartbeat', () => {
  lastHeartbeatAt = Date.now();
  perfStats.heartbeats++;
  // 首个心跳打一行 info：把「整段时间都没出现失联告警」这种**否定式**证据
  // 变成「链路确实通过」的**肯定式**证据。每次启动只此一行，成本可忽略。
  // （实装验证脚本 verify_install.py 依赖这行判定心跳链路是否真的通了）
  if (perfStats.heartbeats === 1) logLine('[overlay] 心跳链路已通（收到首个渲染进程心跳）');
  if (!hostAlive) markHostAlive(true, '心跳恢复');
});

ipcMain.handle('app:get-host-status', () => ({
  alive: hostAlive,
  throttled: mainThrottled,
  overlayOpen: isOverlayOpen(),
}));

/** 性能埋点快照（本地展示用，不联网） */
ipcMain.handle('app:get-perf-stats', () => perfSnapshot());

/**
 * 导出诊断日志
 *
 * 目的只有一个：让用户能把「出了什么问题」变成一份可以直接发过来的文本。
 * 因此不写 zip（多一个依赖、多一层会失败的环节），直接把 logs 目录里的
 * 日志与崩溃快照拼成一个 txt，附上环境头信息。
 */
ipcMain.handle('app:export-logs', async () => {
  const dir = logDir();
  const stamp = dayStamp(new Date());
  const defaultPath = path.join(app.getPath('desktop'), `听课伴侣日志-${stamp}.txt`);
  let saveTarget = null;
  try {
    const win = mainWindow && !mainWindow.isDestroyed() ? mainWindow : undefined;
    const res = win
      ? await dialog.showSaveDialog(win, {
        title: '导出诊断日志',
        defaultPath,
        filters: [{ name: '文本文件', extensions: ['txt'] }],
      })
      : await dialog.showSaveDialog({
        title: '导出诊断日志',
        defaultPath,
        filters: [{ name: '文本文件', extensions: ['txt'] }],
      });
    if (res.canceled || !res.filePath) return { ok: false, canceled: true };
    saveTarget = res.filePath;
  } catch (err) {
    logError(`[diag] 选择导出路径失败: ${err.message}`);
    return { ok: false, error: err.message };
  }

  try {
    const parts = [
      '听课伴侣 诊断日志',
      `导出时间: ${new Date().toISOString()}`,
      `应用版本: ${app.getVersion()}`,
      `Electron: ${process.versions.electron}  Chromium: ${process.versions.chrome}  Node: ${process.versions.node}`,
      `平台: ${process.platform} ${os.release()} (${process.arch})`,
      `打包模式: ${app.isPackaged}  兼容模式: ${COMPAT_MODE}`,
      `数据目录: ${process.env.TINGKEBANLV_HOME || path.join(os.homedir(), '.tingkebanlv')}`,
      `运行时长: ${Math.round((Date.now() - perfStats.startedAt) / 1000)}s`,
      '',
      '---- 性能埋点 ----',
      JSON.stringify(perfSnapshot(), null, 2),
      '',
    ];
    let names = [];
    try {
      names = fs.readdirSync(dir).filter((n) => /\.(log|json)$/.test(n)).sort();
    } catch (_) {
      parts.push('(日志目录不可读)');
    }
    for (const name of names) {
      parts.push(`===== ${name} =====`);
      try {
        parts.push(fs.readFileSync(path.join(dir, name), 'utf8'));
      } catch (err) {
        parts.push(`(读取失败: ${err.message})`);
      }
      parts.push('');
    }
    fs.writeFileSync(saveTarget, parts.join('\n'), 'utf8');
    logLine(`[diag] 日志已导出: ${saveTarget}（含 ${names.length} 个文件）`);
    return { ok: true, filePath: saveTarget, files: names.length };
  } catch (err) {
    logError(`[diag] 导出日志失败: ${err.message}`);
    return { ok: false, error: err.message };
  }
});
