/**
 * 兼容模式自我重启「顺序不变量」单元测试（V6-7 / D6 修复）
 *
 * ## 这份测试存在的唯一理由
 *
 * D6 是一个**静默故障**：不报错、不崩溃、日志里一个字都没有，
 * 用户只看到「双击图标闪一下就没了」。它的根因不是「少写了一段代码」，
 * 而是**两行代码的先后顺序反了**（先 spawn 再让锁）—— 而顺序错误是
 * 「看起来完全正常、代码评审也过」的那一类改动。
 *
 * 所以这里不测「功能对不对」，只钉死两件事：
 *   1. `releaseLock()` 严格早于 `spawn()`（用调用记录断言，不是看代码有没有写）
 *   2. 传给子进程的 `env` **不得**被注入 `TINGKEBANLV_ALLOW_MULTI`
 *      —— 那是「让子进程跳过抢锁」的错修法，会让锁无人持有、用户再双击就出双实例
 *
 * 外加一条源码守卫：`main.cjs` 必须走 `compat-relaunch.cjs`，
 * 不允许自己另开一处 spawn（否则上述不变量被绕开，测试全绿也没意义）。
 *
 * 运行：npm run test
 */

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const {
  COMPAT_RELAUNCH_ARGS,
  buildCompatRelaunchSpec,
  performCompatRelaunch,
} = require('../electron/compat-relaunch.cjs');

/** 记录副作用发生顺序的工具 */
function makeRecorder() {
  const calls = [];
  return {
    calls,
    mark: (name) => calls.push(name),
    /** 断言 a 出现在 b 之前 */
    assertOrder(a, b) {
      const ia = calls.indexOf(a);
      const ib = calls.indexOf(b);
      assert.notEqual(ia, -1, `未发生：${a}`);
      assert.notEqual(ib, -1, `未发生：${b}`);
      assert.ok(ia < ib, `顺序错误：${a} 应在 ${b} 之前，实际 calls=${JSON.stringify(calls)}`);
    },
  };
}

/** 假 child：可选 pid；可选预先触发 'error' */
function makeFakeChild({ pid = 4321 } = {}) {
  const handlers = {};
  const state = { unrefed: false };
  return {
    pid,
    state,
    on(ev, fn) {
      handlers[ev] = fn;
      return this;
    },
    unref() {
      state.unrefed = true;
    },
    emit(ev, arg) {
      if (handlers[ev]) handlers[ev](arg);
    },
  };
}

const SILENT = { logLine: () => {}, logError: () => {} };

// ---------------------------------------------------------------- 顺序不变量

test('让锁必须早于 spawn（D6 根因：顺序反了就是复现）', () => {
  const rec = makeRecorder();
  const child = makeFakeChild();

  const result = performCompatRelaunch({
    spec: { execPath: 'C:/app/x.exe', args: ['--no-sandbox'], cwd: 'C:/app' },
    env: { PATH: 'x' },
    spawn: () => {
      rec.mark('spawn');
      return child;
    },
    releaseLock: () => {
      rec.mark('releaseLock');
      return true;
    },
    ...SILENT,
  });

  rec.assertOrder('releaseLock', 'spawn');
  assert.equal(result.ok, true);
  assert.equal(result.pid, 4321);
  assert.equal(result.releasedLock, true);
});

test('spawn 抛异常时，让锁仍然发生过（不能因为 spawn 失败就跳过让锁）', () => {
  const rec = makeRecorder();
  const errors = [];

  const result = performCompatRelaunch({
    spec: { execPath: 'x', args: [], cwd: '.' },
    env: {},
    spawn: () => {
      rec.mark('spawn');
      throw new Error('ENOENT');
    },
    releaseLock: () => {
      rec.mark('releaseLock');
      return true;
    },
    logLine: () => {},
    logError: (m) => errors.push(m),
  });

  rec.assertOrder('releaseLock', 'spawn');
  assert.equal(result.ok, false);
  assert.equal(result.pid, null);
  assert.ok(errors.some((m) => m.includes('拉起兼容模式实例失败')), '应记录失败原因');
});

test('拿不到 pid 判为拉起失败，且不得调用 unref', () => {
  const child = makeFakeChild({ pid: null });
  const errors = [];

  const result = performCompatRelaunch({
    spec: { execPath: 'x', args: [], cwd: '.' },
    env: {},
    spawn: () => child,
    releaseLock: () => true,
    logLine: () => {},
    logError: (m) => errors.push(m),
  });

  assert.equal(result.ok, false);
  assert.equal(child.state.unrefed, false);
  assert.ok(errors.some((m) => m.includes('未取到 pid')));
});

// ------------------------------------------------------- 「不许用错修法」断言

test('传给子进程的 env 不得被注入 TINGKEBANLV_ALLOW_MULTI', () => {
  const child = makeFakeChild();
  let seenEnv = null;

  performCompatRelaunch({
    spec: { execPath: 'x', args: [], cwd: '.' },
    env: { PATH: 'keep-me' },
    spawn: (_exe, _args, opts) => {
      seenEnv = opts.env;
      return child;
    },
    releaseLock: () => true,
    ...SILENT,
  });

  assert.ok(seenEnv, 'spawn 必须收到 env');
  assert.equal(
    Object.prototype.hasOwnProperty.call(seenEnv, 'TINGKEBANLV_ALLOW_MULTI'),
    false,
    '不许给子进程塞 ALLOW_MULTI —— 那会让单实例锁无人持有，用户再双击就出双实例'
  );
  assert.equal(seenEnv.PATH, 'keep-me', 'env 必须原样透传，不得被改写');
});

test('spawn 的 detached / stdio 语义不变（父进程退出后子进程要能活）', () => {
  const child = makeFakeChild();
  let seenOpts = null;

  performCompatRelaunch({
    spec: { execPath: 'x', args: [], cwd: 'C:/dir' },
    env: {},
    spawn: (_exe, _args, opts) => {
      seenOpts = opts;
      return child;
    },
    releaseLock: () => true,
    ...SILENT,
  });

  assert.equal(seenOpts.detached, true);
  assert.equal(seenOpts.stdio, 'ignore');
  assert.equal(seenOpts.cwd, 'C:/dir');
  assert.equal(child.state.unrefed, true);
});

// ------------------------------------------------------------ 让锁本身的边界

test('父进程未持有锁时不调用释放，但重启照做', () => {
  let releaseCalled = false;
  const child = makeFakeChild();

  const result = performCompatRelaunch({
    spec: { execPath: 'x', args: [], cwd: '.' },
    env: {},
    spawn: () => child,
    releaseLock: () => {
      releaseCalled = true;
      return false; // 未持有
    },
    ...SILENT,
  });

  assert.equal(releaseCalled, true, '应由调用方决定是否真的释放');
  assert.equal(result.releasedLock, false);
  assert.equal(result.ok, true, '没锁可让不应阻断重启');
});

test('释放锁抛异常不中断重启（回退到旧表现，但不比不重启更差）', () => {
  const errors = [];
  const child = makeFakeChild();

  const result = performCompatRelaunch({
    spec: { execPath: 'x', args: [], cwd: '.' },
    env: {},
    spawn: () => child,
    releaseLock: () => {
      throw new Error('锁释放失败');
    },
    logLine: () => {},
    logError: (m) => errors.push(m),
  });

  assert.equal(result.releasedLock, false);
  assert.equal(result.ok, true);
  assert.ok(errors.some((m) => m.includes('释放单实例锁异常')));
});

// ------------------------------------------------------------ 目标规格（cwd 坑）

test('打包态：cwd 取 exe 所在目录（app.asar 是文件，当 cwd 会 ENOENT）', () => {
  const spec = buildCompatRelaunchSpec({
    isPackaged: true,
    execPath: 'C:/Program Files/tingke-banlv/听课伴侣.exe',
    appPath: 'C:/Program Files/tingke-banlv/resources/app.asar',
  });

  assert.equal(spec.cwd, path.dirname('C:/Program Files/tingke-banlv/听课伴侣.exe'));
  assert.equal(spec.cwd.includes('app.asar'), false);
  assert.deepEqual(spec.args, [...COMPAT_RELAUNCH_ARGS]);
});

test('源码态：cwd 取 appPath，并额外带上 appPath 作为位置参数', () => {
  const spec = buildCompatRelaunchSpec({
    isPackaged: false,
    execPath: 'C:/node/electron.exe',
    appPath: 'C:/repo/frontend',
  });

  assert.equal(spec.cwd, 'C:/repo/frontend');
  assert.deepEqual(spec.args, [...COMPAT_RELAUNCH_ARGS, 'C:/repo/frontend']);
  assert.deepEqual(spec.args.slice(0, 2), ['--no-sandbox', '--tingkebanlv-compat']);
});

// ---------------------------------------------------------------- 源码守卫

const MAIN_SRC = fs.readFileSync(path.join(__dirname, '..', 'electron', 'main.cjs'), 'utf8');
/** 剥掉注释后的源码：注释里会**引用**被禁止的写法，不剥会假失败（本项目踩过） */
const MAIN_CODE = MAIN_SRC.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

test('main.cjs 不得自己 spawn 兼容实例（必须走 compat-relaunch.cjs）', () => {
  assert.ok(
    MAIN_CODE.includes("require('./compat-relaunch.cjs')"),
    'main.cjs 必须引用 compat-relaunch.cjs'
  );
  assert.ok(
    MAIN_CODE.includes('performCompatRelaunch('),
    'relaunchInCompatMode 必须通过 performCompatRelaunch 执行重启'
  );
  // main.cjs 里不应再出现裸的 relaunch spawn 调用（其余 spawn 都是后端进程）
  const relaunchBlock = MAIN_CODE.slice(
    MAIN_CODE.indexOf('function relaunchInCompatMode()'),
    MAIN_CODE.indexOf('function armRenderWatchdog()')
  );
  assert.ok(relaunchBlock.length > 0, '未找到 relaunchInCompatMode');
  assert.equal(
    /\bspawn\s*\(/.test(relaunchBlock),
    false,
    'relaunchInCompatMode 内不得直接调用 spawn —— 顺序不变量会失去守卫'
  );
});

test('main.cjs 不得向重启子进程注入 ALLOW_MULTI（错修法守卫）', () => {
  const relaunchBlock = MAIN_CODE.slice(
    MAIN_CODE.indexOf('function relaunchInCompatMode()'),
    MAIN_CODE.indexOf('function armRenderWatchdog()')
  );
  assert.equal(
    /TINGKEBANLV_ALLOW_MULTI\s*:\s*['"]1['"]/.test(relaunchBlock),
    false,
    '不得用「子进程跳过抢锁」来绕开竞态：锁会无人持有，用户再双击就出双实例'
  );
  assert.ok(
    relaunchBlock.includes('app.releaseSingleInstanceLock()'),
    '必须显式释放单实例锁后再重启'
  );
});
