/**
 * 兼容模式「自我重启」的副作用编排
 *
 * ## 为什么单独提一个模块
 *
 * 这段逻辑的**调用顺序本身就是不变量**，而顺序错误产生的是**静默故障**：
 * 不报错、不崩溃、日志里一个字都没有，用户只看到「双击图标闪一下就没了」。
 * 把它留在 `main.cjs` 里靠注释约束太脆（谁都可以调整两行的先后），
 * 所以提出来做成**依赖注入 + 单测可断言调用顺序**的形式。
 *
 * ## 事故（D6 / V6-7，2026-10-12 首次暴露）
 *
 * 原实现是「先 spawn，再 `app.quit()`」。问题在于 `app.quit()` 是**异步**的：
 *
 *   父进程（渲染进程已崩，但**仍持有单实例锁**）
 *     → spawn 子进程
 *     → 子进程立刻 `requestSingleInstanceLock()` → **失败**
 *     → 子进程走「已有实例」分支直接退出（`stdio:'ignore'`，日志被吞）
 *     → 父进程随后 quit
 *   ⇒ 两个实例都没了。
 *
 * 现场证据：`packaging/verify/app-stdio.log` 里出现
 * `[single-instance] 检测到第二次启动，将已有窗口提到前台` —— 这行**只能**由
 * `app.on('second-instance')` 打出，触发条件是「另一个实例抢锁失败」，
 * 因此它确定性证明了「刚被拉起的子进程抢锁失败」。
 *
 * ## 修法：先让锁，再 spawn（严格顺序）
 *
 * `releaseLock()` **必须**早于 `spawn()`。父进程把锁显式让出去，
 * 子进程才能正常抢到锁、按正常单实例语义运行，`second-instance` 语义不被破坏。
 *
 * ## 为什么不采用「给子进程塞环境变量跳过单实例检查」
 *
 * 代码里已有逃生口 `TINGKEBANLV_ALLOW_MULTI=1`（`main.cjs:131`），加进去只要一行。
 * 但它有真实语义漏洞：**子进程跳过抢锁 ⇒ 锁从此无人持有** ⇒ 兼容实例存活期间
 * 用户再双击一次图标，新进程能成功抢锁 ⇒ **出现两个实例**（且新实例会再崩、
 * 再降级，可能自激）。
 *
 * 因此这里**刻意不注入任何环境变量**：`env` 原样透传。顺序修复才是正解。
 * 见 `frontend/tests/compat-relaunch.test.cjs` 里对「env 不被污染」的断言。
 *
 * 相关文档：`docs/asr_eval/known_defects.md` D6、`docs/m3/plan_v6.md` §2.1。
 */

const path = require('path');

/** 兼容模式重启时带上的参数（`--no-sandbox` 必须显式带，否则沙箱照样拦） */
const COMPAT_RELAUNCH_ARGS = ['--no-sandbox', '--tingkebanlv-compat'];

/**
 * 算出重启子进程需要的「目标」——纯函数，不碰任何副作用。
 *
 * cwd 必须是一个真实存在的**目录**。踩过的坑：打包后 `app.getAppPath()` 指向
 * `<安装目录>\resources\app.asar` —— 那是一个**文件**，把它当 cwd 传给 spawn，
 * 底层直接以 ENOENT 失败，而错误信息里出现的是 exe 路径，看起来像「找不到程序」，
 * 极容易误判。这个 bug 只在实装后暴露（源码模式下 `getAppPath()` 是仓库目录，
 * 一切正常），所以 CDP 冒烟全绿也照样带着它出门。
 */
function buildCompatRelaunchSpec({ isPackaged, execPath, appPath }) {
  const cwd = isPackaged ? path.dirname(execPath) : appPath;
  const args = isPackaged
    ? [...COMPAT_RELAUNCH_ARGS]
    : [...COMPAT_RELAUNCH_ARGS, appPath];
  return { execPath, args, cwd };
}

/**
 * 执行自我重启。所有副作用通过参数注入，便于单测断言调用顺序。
 *
 * @param {object}   o
 * @param {object}   o.spec        `buildCompatRelaunchSpec()` 的产物
 * @param {Function} o.spawn       `child_process.spawn`
 * @param {object}   o.env         透传给子进程的环境变量（**不得被本模块改写**）
 * @param {Function} o.releaseLock 释放单实例锁；返回 true 表示确实释放了
 * @param {Function} o.logLine     正常日志
 * @param {Function} o.logError    错误日志
 * @returns {{ok: boolean, pid: number|null, releasedLock: boolean}}
 */
function performCompatRelaunch({ spec, spawn, env, releaseLock, logLine, logError }) {
  // ① 先让锁 —— 顺序是不变量，反了就是 D6 复现（见文件头）。
  let releasedLock = false;
  try {
    releasedLock = !!(releaseLock && releaseLock());
  } catch (err) {
    // 释放失败不能中断重启：子进程抢不到锁会退回「父进程还活着」的旧表现，
    // 但不至于比不重启更差；真正的兜底是调用方检查返回值后弹错误框。
    logError(`[single-instance] 释放单实例锁异常: ${err.message}`);
    releasedLock = false;
  }

  // ② 再 spawn
  try {
    const child = spawn(spec.execPath, spec.args, {
      detached: true, // 独立进程组，父进程退出后仍存活
      stdio: 'ignore',
      cwd: spec.cwd,
      env,
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
      return { ok: false, pid: null, releasedLock };
    }
    child.unref();
    logLine(
      `[watchdog] 已拉起兼容模式实例 pid=${child.pid} cwd=${spec.cwd} args=${spec.args.join(' ')}`
    );
    return { ok: true, pid: child.pid, releasedLock };
  } catch (err) {
    logError(`[watchdog] 拉起兼容模式实例失败: ${err.message}`);
    return { ok: false, pid: null, releasedLock };
  }
}

module.exports = {
  COMPAT_RELAUNCH_ARGS,
  buildCompatRelaunchSpec,
  performCompatRelaunch,
};
