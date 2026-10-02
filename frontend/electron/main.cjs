/**
 * 听课伴侣 - Electron 主进程
 *
 * 职责：
 *   1. 动态分配空闲端口，拉起 Python 后端（开发用系统 Python，打包用 PyInstaller 产物）
 *   2. 轮询 /api/ping 等待后端就绪，避免前端在服务未起时开始请求
 *      （刻意不用 /api/health：它带外部探测，受限网络下单次可达 2 秒以上）
 *   3. 加载界面（开发走 Vite dev server，打包走 http://127.0.0.1:PORT —— 由后端同源托管前端）
 *   4. 退出时清理整棵后端进程树（Windows 下 uvicorn 子进程必须用 taskkill /T）
 *
 * 环境变量：
 *   TINGKEBANLV_PYTHON          指定 Python 解释器（覆盖自动探测）
 *   TINGKEBANLV_BACKEND_DIR     指定后端目录
 *   TINGKEBANLV_PORT            强制指定端口（覆盖自动分配，调试用）
 *   TINGKEBANLV_HOME            数据目录（决定日志落点，默认 ~/.tingkebanlv）
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

const { app, BrowserWindow, ipcMain, dialog } = require('electron');
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

let mainWindow = null;
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
 */
function relaunchInCompatMode() {
  try {
    const child = spawn(process.execPath, COMPAT_RELAUNCH_ARGS, {
      detached: true, // 独立进程组，父进程退出后仍存活
      stdio: 'ignore',
      env: process.env,
    });
    child.unref();
    logLine(`[watchdog] 已拉起兼容模式实例 pid=${child.pid}`);
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

app.whenReady().then(async () => {
  try {
    const port = await launchBackend();
    createWindow(port);
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
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  isQuitting = true;
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
