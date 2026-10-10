/**
 * 首启向导状态机单元测试（`electron/onboarding-state.cjs`）
 *
 * 这个模块决定「要不要弹向导」「走到第几步」「完成后还弹不弹」，
 * 三件事都只能靠重启应用人工验证 —— 所以决策全部抽进这个不 require
 * electron 的纯模块，在这里用单测覆盖**全部分支**。main.cjs 只负责注入
 * fs 与真实路径，分支逻辑一行都不该写在那里。
 *
 * 两个重点：
 *   1. `Number(null) === 0` 陷阱（本项目已在三个模块踩过三次）：
 *      「缺字段」必须回到 0，而不是被 Number 静默转成 0 后混过 isFinite
 *   2. `shouldShowWizard` 的判断**顺序**：force 必须最前 —— 顺序错了
 *      「重新查看引导」入口会在兼容模式下静默失效，且无任何报错
 *
 * 运行：npm run test
 */

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const state = require('../electron/onboarding-state.cjs');

// ---------------------------------------------------------------- 工具

/** 建一个临时目录跑真实读写（writeOnboarding 复用 overlay-settings 的原子写） */
function withTmpDir(fn) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'onboarding-test-'));
  try {
    return fn(dir);
  } finally {
    try { fs.rmSync(dir, { recursive: true, force: true }); } catch (_) { /* 尽力清理 */ }
  }
}

const T0 = '2026-10-06T12:00:00.000Z';

// ---------------------------------------------------------------- normalizeOnboarding

test('normalizeOnboarding · 出厂默认是「从未见过向导」', () => {
  const s = state.normalizeOnboarding(null);
  assert.equal(s.completed, false);
  assert.equal(s.dismissedAt, null);
  assert.equal(s.startedAt, null);
  assert.equal(s.stepReached, 0);
  assert.equal(s.schemaVersion, state.SCHEMA_VERSION);
  assert.deepEqual(s.seenHotkeys, {});
});

test('normalizeOnboarding · 垃圾输入永不抛异常', () => {
  for (const garbage of [undefined, null, 42, 'str', [], { nested: 'ok' }]) {
    const s = state.normalizeOnboarding(garbage);
    assert.equal(s.completed, false, `输入 ${JSON.stringify(garbage)} 应回到默认`);
    assert.equal(s.stepReached, 0);
  }
});

test('normalizeOnboarding · completed 严格只认 true（"true"/1 一律按未完成）', () => {
  // 宁可多弹一次，也不能因为手改坏了的文件让向导永远消失
  assert.equal(state.normalizeOnboarding({ completed: true }).completed, true);
  assert.equal(state.normalizeOnboarding({ completed: 'true' }).completed, false);
  assert.equal(state.normalizeOnboarding({ completed: 1 }).completed, false);
});

test('normalizeOnboarding · stepReached：Number(null)===0 陷阱与越界夹取', () => {
  // ⚠️ 本项目踩过三次的坑：Number(null)===0 且 Number.isFinite(0) 为真。
  // 这一组里任何一条写错，缺失字段都会被静默当成合法数字。
  for (const bad of [null, undefined, '', 'abc', NaN, -1, -3.2, Infinity]) {
    assert.equal(state.normalizeOnboarding({ stepReached: bad }).stepReached, 0,
      `stepReached=${String(bad)} 应回到 0`);
  }
  assert.equal(state.normalizeOnboarding({ stepReached: 4 }).stepReached, 4);
  assert.equal(state.normalizeOnboarding({ stepReached: 4.7 }).stepReached, 4); // 向下取整
  assert.equal(state.normalizeOnboarding({ stepReached: '3' }).stepReached, 3); // 字符串数字可救
  assert.equal(state.normalizeOnboarding({ stepReached: 99 }).stepReached, state.TOTAL_STEPS); // 上夹
});

test('normalizeOnboarding · durationMs / seenHotkeys / 时间字段的兜底', () => {
  assert.equal(state.normalizeOnboarding({ durationMs: -5 }).durationMs, null);
  assert.equal(state.normalizeOnboarding({ durationMs: 'x' }).durationMs, null);
  assert.equal(state.normalizeOnboarding({ durationMs: 1234.6 }).durationMs, 1235);
  assert.equal(state.normalizeOnboarding({ durationMs: null }).durationMs, null);
  // 数组形式的 seenHotkeys 会污染 Object 键遍历，必须归一成 {}
  assert.deepEqual(state.normalizeOnboarding({ seenHotkeys: ['a'] }).seenHotkeys, {});
  assert.deepEqual(state.normalizeOnboarding({ seenHotkeys: { O: true } }).seenHotkeys, { O: true });
  assert.equal(state.normalizeOnboarding({ startedAt: 'not-a-date' }).startedAt, null);
  assert.equal(state.normalizeOnboarding({ startedAt: T0 }).startedAt, T0);
});

// ---------------------------------------------------------------- readOnboarding / writeOnboarding

test('readOnboarding · 文件不存在 → absent（reason 必须区分，主进程据此打日志）', () => {
  withTmpDir((dir) => {
    const res = state.readOnboarding(fs, path.join(dir, 'none.json'));
    assert.equal(res.reason, 'absent');
    assert.equal(res.state.completed, false);
  });
});

test('readOnboarding · 损坏 JSON → corrupt + 完整默认状态（永不抛）', () => {
  withTmpDir((dir) => {
    const p = path.join(dir, 'onboarding.json');
    fs.writeFileSync(p, '{"completed": tru', 'utf8'); // 截断
    const res = state.readOnboarding(fs, p);
    assert.equal(res.reason, 'corrupt');
    assert.ok(res.error, '必须带 error 信息进日志');
    assert.equal(res.state.completed, false);
  });
});

test('readOnboarding · 顶层不是对象（数组/字符串）→ corrupt', () => {
  withTmpDir((dir) => {
    const p = path.join(dir, 'onboarding.json');
    for (const bad of ['[1,2,3]', '"str"', '42', 'null']) {
      fs.writeFileSync(p, bad, 'utf8');
      const res = state.readOnboarding(fs, p);
      assert.equal(res.reason, 'corrupt', `内容 ${bad} 应判损坏`);
    }
  });
});

test('writeOnboarding → readOnboarding 往返一致（原子写不落半个文件）', () => {
  withTmpDir((dir) => {
    const p = path.join(dir, 'onboarding.json');
    const full = state.markCompleted(state.markStep(state.markStarted(state.DEFAULT_ONBOARDING, T0), 6), T0);
    state.writeOnboarding(fs, p, full);
    // 原子写的标志：目录里不应有 .tmp 残留
    assert.deepEqual(fs.readdirSync(dir).filter((f) => f.endsWith('.tmp')), []);
    const res = state.readOnboarding(fs, p);
    assert.equal(res.reason, 'ok');
    assert.equal(res.state.completed, true);
    assert.equal(res.state.stepReached, 6);
    assert.ok(res.state.durationMs !== null && res.state.durationMs >= 0);
  });
});

// ---------------------------------------------------------------- shouldShowWizard（判断顺序是全部价值）

const FRESH = state.DEFAULT_ONBOARDING;

test('shouldShowWizard · 全新安装（absent）→ 弹', () => {
  assert.deepEqual(state.shouldShowWizard(FRESH, {}), { show: true, reason: 'first-run' });
});

test('shouldShowWizard · force 压过一切（含兼容模式与已完成）', () => {
  // 「重新查看引导」的语义是「用户此刻想看」。若 force 排在后面，
  // 兼容模式下手动入口会静默失效且无报错 —— 这是设计稿 §2.3 的本意：
  // 推迟的是自动弹出，不是禁止。
  const done = state.markCompleted(FRESH, T0);
  for (const ctx of [{}, { compatMode: true }, { backendReady: false }, { compatMode: true, backendReady: false }]) {
    assert.deepEqual(state.shouldShowWizard(done, { ...ctx, force: true }),
      { show: true, reason: 'forced' }, `ctx=${JSON.stringify(ctx)}`);
  }
});

test('shouldShowWizard · 兼容模式 / 后端未就绪 → 推迟（不写任何标记）', () => {
  assert.deepEqual(state.shouldShowWizard(FRESH, { compatMode: true }),
    { show: false, reason: 'compat-mode' });
  assert.deepEqual(state.shouldShowWizard(FRESH, { backendReady: false }),
    { show: false, reason: 'backend-not-ready' });
  // 推迟 ≠ 拒绝：下一次启动条件满足时仍要弹
  assert.equal(state.shouldShowWizard(FRESH, {}).show, true);
});

test('shouldShowWizard · completed / dismissed → 永不再自动弹（升级也不重置）', () => {
  assert.deepEqual(state.shouldShowWizard(state.markCompleted(FRESH, T0), {}),
    { show: false, reason: 'completed' });
  assert.deepEqual(state.shouldShowWizard(state.markDismissed(FRESH, T0), {}),
    { show: false, reason: 'dismissed' });
  // completed 优先于 dismissed 报告（顺序决定日志可读性），但结论一致
  const both = state.markDismissed(state.markCompleted(FRESH, T0), T0);
  assert.deepEqual(state.shouldShowWizard(both, {}), { show: false, reason: 'completed' });
});

test('shouldShowWizard · 判断顺序：compatMode 报告先于 backendReady（修回归时能一眼看出哪个条件拦的）', () => {
  assert.equal(state.shouldShowWizard(FRESH, { compatMode: true, backendReady: false }).reason,
    'compat-mode');
});

// ---------------------------------------------------------------- mark* / humanDuration

test('markStarted · 每次调用都刷新「本次会话」起点；stepReached 至少到 1', () => {
  // 语义变更（2026-10-10）：`startedAt` 是「本次会话开始时间」，不是「首开时间」。
  // 向导窗口每被打开一次就是一次新会话（自动弹出 / onboarding:reopen / macOS 激活），
  // 旧实现「只写一次」会让第二次之后的 durationMs 累加成天文数字（实测 12962m0s）。
  const a = state.markStarted(FRESH, T0);
  assert.equal(a.startedAt, T0);

  const T1 = '2026-10-06T23:59:00.000Z';
  const b = state.markStarted(a, T1);
  assert.equal(b.startedAt, T1, '再次打开向导＝新会话，起点必须刷新 —— 否则耗时记成跨会话总时长');
  assert.equal(b.stepReached, 1);

  // 不变量：刷新起点不得抹掉「走到过的最深一步」
  assert.equal(state.markStarted(state.markStep(a, 4), T1).stepReached, 4, 'stepReached 只增不减');
});

test('会话耗时 · 四个场景的 durationMs 都等于「本次会话」时长（旧实现全部污染）', () => {
  // 把真实缺陷固化成回归：把 markStarted 改回 `s.startedAt || nowIso(at)` 会全部变红。
  const T = (s) => new Date(`2026-10-01T${s}Z`).toISOString();
  const day = (d, s) => new Date(`2026-10-${d}T${s}Z`).toISOString();
  const start = (st, at) => state.describeEvent(st, 'start', { at }).state;
  const complete = (st, at) => state.describeEvent(st, 'complete', { at }).state;
  const dismiss = (st, at) => state.describeEvent(st, 'dismiss', { at }).state;

  // ③ 正常首次走完（对照组，任何修法都不能改坏它）
  const first = complete(start(FRESH, T('00:00:00')), T('00:05:00'));
  assert.equal(first.durationMs, 300000);

  // ① completed → 重新查看向导 → 走完（会话 2 分钟）。旧实现记成 12962m0s
  const reopened = complete(start(first, day('10', '00:00:00')), day('10', '00:02:00'));
  assert.equal(reopened.durationMs, 120000, '重开走完必须只算本次会话');

  // ② 跳过 → 一周后重新查看向导 → 走完（会话 3 分钟）。旧实现记成 10083m0s
  const afterDismiss = complete(
    start(dismiss(start(FRESH, T('00:00:00')), T('00:00:30')), day('08', '00:00:00')),
    day('08', '00:03:00'),
  );
  assert.equal(afterDismiss.durationMs, 180000);

  // ④ 点右上角 ×（不写标记）→ 重启自动弹出 → 走完（会话 40 秒）。
  //    这条**不依赖任何新入口**，是改动前就存在的线上缺陷：close 刻意不写标记，
  //    所以下次启动仍会弹出，而旧实现把会话起点留在了上一次启动。
  const closedByX = start(FRESH, T('00:00:00'));
  assert.equal(state.shouldShowWizard(closedByX, { backendReady: true }).show, true,
    '点 × 不等于拒绝，下次启动仍应弹出');
  const secondRun = complete(start(closedByX, T('00:00:20')), T('00:01:00'));
  assert.equal(secondRun.durationMs, 40000);
});

test('markStep · 只增不减（它衡量「卡在哪」，不是「当前在哪」）', () => {
  const s = state.markStep(state.markStep(FRESH, 4), 2);
  assert.equal(s.stepReached, 4);
  assert.equal(state.markStep(FRESH, 99).stepReached, state.TOTAL_STEPS);
  assert.equal(state.markStep(FRESH, null).stepReached, 0);
});

test('markCompleted · 耗时 = 完成 - 开始；开始时间缺失时保留旧值', () => {
  const started = state.markStarted(FRESH, T0);
  const done = state.markCompleted(started, '2026-10-06T12:00:41.200Z');
  assert.equal(done.durationMs, 41200);
  assert.equal(done.completed, true);
  // 没有 startedAt（理论上不可能，但文件可能被手改）→ 不该算出 NaN
  const weird = state.markCompleted(FRESH, T0);
  assert.equal(weird.durationMs, null);
});

test('humanDuration · 秒/分格式与边界', () => {
  assert.equal(state.humanDuration(41200), '41.2s');
  assert.equal(state.humanDuration(72000), '1m12s');
  assert.equal(state.humanDuration(60000), '1m0s');
  assert.equal(state.humanDuration(119500), '2m0s', '59.5s 四舍五入到 60s 时应进位，不能输出 1m60s');
  assert.equal(state.humanDuration(-1), '?');
  assert.equal(state.humanDuration(null), '?');
  assert.equal(state.humanDuration('x'), '?');
});

// ---------------------------------------------------------------- describeEvent（副作用决策集中点）

test('describeEvent · 五类事件的日志文案与状态迁移', () => {
  const run = (ev, payload, st) => state.describeEvent(st === undefined ? FRESH : st, ev, payload);

  const open = run('start', { at: T0 });
  assert.equal(open.log, '[onboarding] 向导已打开');
  assert.equal(open.event, 'start');
  assert.equal(open.state.startedAt, T0);

  const step = run('step', { step: 3 }, open.state);
  assert.equal(step.log, '[onboarding] 步骤=3/6');
  assert.equal(step.state.stepReached, 3);

  const done = run('complete', { at: '2026-10-06T12:00:41.200Z' }, step.state);
  assert.equal(done.log, '[onboarding] 完成 步骤=3/6 耗时=41.2s');
  assert.equal(done.state.completed, true);

  const dismiss = run('dismiss', { at: T0 }, step.state);
  assert.equal(dismiss.log, '[onboarding] 跳过 步骤=3/6');
  assert.ok(dismiss.state.dismissedAt);

  const unknown = run('nonsense', {});
  assert.equal(unknown.event, 'unknown');
  assert.match(unknown.log, /未知事件/);
  // 未知事件不得改状态（手滑传错事件名不能弄脏用户数据）
  assert.deepEqual(unknown.state, state.normalizeOnboarding(FRESH));
});

test('describeEvent · dismiss 时 stepReached 保留（跳过发生在第几步是核心埋点）', () => {
  const at4 = state.markStep(state.markStarted(FRESH, T0), 4);
  const d = state.describeEvent(at4, 'dismiss', { at: T0 });
  assert.equal(d.state.stepReached, 4, '跳过步号不能被清零，否则跳过率分析失真');
});
