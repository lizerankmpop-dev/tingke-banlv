/**
 * 悬浮窗动作「副作用决策」单元测试 —— 锁定「多入口行为一致」这个不变量
 *
 * 这些用例的存在理由是两个真实缺陷（Backlog B1 / B3），共用一个根因：
 *
 *   B1：`Alt+Shift+R`（热键重置）有 toast，因为它写在**热键回调**里；
 *       控制面板的「重置位置」按钮直接调函数、绕过了那一行 → 点了没反应。
 *   B3：`Alt+Shift+O`（热键开关）走的 open/close **成功路径完全不写日志**，
 *       而同文件的 reset / toggleInteractive 都写 → 按了没反应时无从排查。
 *
 * 也就是说：**反馈与日志不能放在调用点，只要还有第二个入口，就一定会再漏一次。**
 *
 * 所以这里有两层断言：
 *   1. 决策层（overlay-actions.cjs）—— 同一动作在不同来源下产出**结构一致**的副作用
 *   2. 结构层（扫 main.cjs 源码）—— 每个调用点都必须声明来源、文案不得出现第二份字面量
 *
 * 第 2 层看起来不常规，但它正是「防止后续改动再次出现路径行为不一致」的唯一手段：
 * main.cjs require('electron')，无法被 node --test 导入，只能从源码层面守住。
 *
 * 运行：npm run test
 */

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const actions = require('../electron/overlay-actions.cjs');

const MAIN_CJS = path.join(__dirname, '..', 'electron', 'main.cjs');
const MAIN_SRC = fs.readFileSync(MAIN_CJS, 'utf8');

/**
 * 去掉块注释与整行注释后的源码。
 *
 * 「字面量守卫」必须在去注释的文本上跑：main.cjs 的说明性注释里会**引用**
 * 那些字面量（例如解释「原先这里写着 sendToOverlay('overlay:toast', ...)」），
 * 直接扫原文会把文档当成违规代码。
 *
 * 刻意不做行尾注释剥离 —— 那会误伤字符串里的 `//`（如 URL），
 * 而本文件的关键调用行都没有行尾注释。
 */
const MAIN_CODE = MAIN_SRC
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '');

/** 会被扫描「是否声明了触发来源」的函数名 */
const TRACKED_FNS = [
  'openOverlayWindow',
  'closeOverlayWindow',
  'destroyOverlayWindow',
  'resetOverlayPosition',
  'toggleOverlayWindow',
];

/**
 * 收集 main.cjs 里这些函数的**调用点**（排除函数声明行与注释）。
 *
 * 注释必须排除：文档里会引用 `openOverlayWindow()` 这类写法，
 * 不排除就会把说明文字当成调用点，产生假失败。
 */
function collectCallSites(src) {
  const sites = [];
  src.split('\n').forEach((raw, idx) => {
    const rawTrim = raw.trim();
    // 整行注释 / 块注释体
    if (!rawTrim || /^(\/\/|\*|\/\*)/.test(rawTrim)) return;

    // 去掉行尾注释（本文件的关键调用行都没有 URL 之类的 // 内容，安全）
    const code = raw.replace(/\s*\/\/.*$/, '');
    if (/^\s*(async\s+)?function\b/.test(code)) return; // 函数声明

    const re = new RegExp(`\\b(${TRACKED_FNS.join('|')})\\s*\\(([^)]*)\\)`, 'g');
    let m;
    while ((m = re.exec(code)) !== null) {
      sites.push({ line: idx + 1, name: m[1], args: m[2].trim(), text: rawTrim });
    }
  });
  return sites;
}

/** 取出 main.cjs 某个顶层函数的函数体文本 */
function sliceFunction(src, name) {
  const start = src.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `main.cjs 里找不到函数 ${name}`);
  const open = src.indexOf('{', start);
  let depth = 0;
  for (let i = open; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') {
      depth--;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  throw new Error(`函数 ${name} 的大括号不平衡`);
}

// ============ 触发来源归一 ============

test('已知来源原样返回', () => {
  assert.equal(actions.normalizeTrigger('ui'), 'ui');
  assert.equal(actions.normalizeTrigger('hotkey'), 'hotkey');
  assert.equal(actions.normalizeTrigger('startup'), 'startup');
  assert.equal(actions.normalizeTrigger('display'), 'display-change');
  assert.equal(actions.normalizeTrigger('host'), 'host-closed');
});

test('未登记 / 空 / 非字符串一律归一成 unknown，不把 undefined 拼进日志', () => {
  // 调用点漏传来源时，日志里出现 `触发来源=undefined` 比没有这个字段更误导人
  for (const bad of [undefined, null, '', '   ', 'UI', 'keyboard', 42, {}, []]) {
    assert.equal(actions.normalizeTrigger(bad), 'unknown', `输入 ${JSON.stringify(bad)}`);
  }
});

// ============ 开关悬浮窗（B3）============

test('打开/关闭都产出日志，且两条路径结构一致、只有来源不同', () => {
  const uiOpen = actions.describeOpen('ui');
  const hkOpen = actions.describeOpen('hotkey');
  const uiClose = actions.describeClose('ui');
  const hkClose = actions.describeClose('hotkey');

  // 结构（除来源外的部分）必须完全相同 —— 这就是 B3 的验收标准
  assert.equal(uiOpen.log.replace('ui', 'X'), hkOpen.log.replace('hotkey', 'X'));
  assert.equal(uiClose.log.replace('ui', 'X'), hkClose.log.replace('hotkey', 'X'));

  assert.match(uiOpen.log, /已打开/);
  assert.match(uiClose.log, /已关闭/);
  assert.match(uiOpen.log, /触发来源=ui/);
  assert.match(hkOpen.log, /触发来源=hotkey/);
});

test('打开与关闭的日志必须能区分开（否则等于没记）', () => {
  assert.notEqual(actions.describeOpen('ui').log, actions.describeClose('ui').log);
});

test('销毁窗口成功路径也有日志（原先只在失败时记，与 B3 同类）', () => {
  const a = actions.describeDestroy('host');
  const b = actions.describeDestroy('ui');

  assert.match(a.log, /已销毁/);
  assert.match(a.log, /触发来源=host-closed/);
  assert.equal(a.log.replace('host-closed', 'X'), b.log.replace('ui', 'X'));
});

// ============ 重置位置（B1）============

test('重置一定产出 toast，且文案与触发来源无关（B1 的核心验收）', () => {
  const ui = actions.describeReset({ opened: true, x: 100, y: 200 }, 'ui');
  const hk = actions.describeReset({ opened: true, x: 100, y: 200 }, 'hotkey');

  assert.equal(ui.toast.text, hk.toast.text, '两条路径的 toast 文案必须完全一致');
  assert.equal(ui.toast.text, actions.RESET_TOAST_TEXT);
  assert.ok(ui.toast.text.length > 0);
});

test('重置日志含来源与坐标', () => {
  const fx = actions.describeReset({ opened: true, x: 512, y: 688 }, 'hotkey');

  assert.match(fx.log, /位置已重置: 512,688/);
  assert.match(fx.log, /触发来源=hotkey/);
});

test('悬浮窗没开着时，日志必须写明「只更新了存储位置」', () => {
  // 此时 sendToOverlay 会静默失败，用户看不到 toast。
  // 日志若不写明，事后排查会以为提示发过了。
  const closed = actions.describeReset({ opened: false, x: 0, y: 0 }, 'ui');
  const opened = actions.describeReset({ opened: true, x: 0, y: 0 }, 'ui');

  assert.match(closed.log, /悬浮窗未打开/);
  assert.doesNotMatch(opened.log, /悬浮窗未打开/);
  assert.match(closed.log, /触发来源=ui/);
});

test('坐标异常时不把 NaN / undefined 打进日志', () => {
  for (const bad of [Number.NaN, undefined, null, 'abc', Number.POSITIVE_INFINITY]) {
    const fx = actions.describeReset({ opened: true, x: bad, y: bad }, 'ui');
    assert.doesNotMatch(fx.log, /NaN|undefined|Infinity/, `输入 ${String(bad)}`);
    assert.match(fx.log, /位置已重置: \?,?\?/);
  }
});

test('state 缺失也不崩（防御调用点漏传）', () => {
  for (const state of [undefined, null, {}]) {
    const fx = actions.describeReset(state, 'ui');
    assert.ok(fx.toast.text);
    assert.match(fx.log, /触发来源=ui/);
  }
});

// ============ 结构守卫：main.cjs ============

test('结构守卫 · 重置文案不在 main.cjs 出现第二份字面量', () => {
  // B1 的成因就是「同一句话在两个地方各写一份」。文案唯一来源是
  // overlay-actions.cjs 的 RESET_TOAST_TEXT。
  assert.ok(
    !MAIN_CODE.includes(actions.RESET_TOAST_TEXT),
    `main.cjs 里出现了重置文案字面量「${actions.RESET_TOAST_TEXT}」，` +
    '应当改用 overlayActions.describeReset().toast.text',
  );
});

test('结构守卫 · 每个开闭/重置/销毁调用点都声明了触发来源', () => {
  const sites = collectCallSites(MAIN_CODE);

  // 至少要能扫到调用点，否则说明扫描逻辑失效（比漏检更危险）
  assert.ok(sites.length >= 8, `只扫到 ${sites.length} 个调用点，扫描逻辑可能已失效`);

  // 实参只允许两种形态：
  //   triggerSource                        —— 把来源继续往下转发
  //   overlayActions.TRIGGER_SOURCES.xxx   —— 入口处声明自己是谁
  // 这样既挡住「漏传」，也挡住「传了个不相干的变量」。
  const VALID = /^(triggerSource|overlayActions\.TRIGGER_SOURCES\.\w+)$/;
  const bad = sites.filter((s) => !VALID.test(s.args));

  assert.deepEqual(
    bad.map((s) => `L${s.line} ${s.name}(${s.args}) —— ${s.text}`),
    [],
    '存在未正确声明触发来源的调用点 —— 这正是 B1/B3 的成因',
  );
});

test('结构守卫 · 热键注册区不再自带 toast（B1/B3 的直接修法）', () => {
  const body = sliceFunction(MAIN_SRC, 'registerOverlayShortcuts');

  // 热键回调里一旦出现 sendToOverlay，就说明副作用又回到调用点了
  assert.doesNotMatch(
    body,
    /sendToOverlay\s*\(/,
    'registerOverlayShortcuts 里又出现了 sendToOverlay —— ' +
    'toast 必须由 resetOverlayPosition() 内部统一发出，否则按钮路径会再次漏掉',
  );

  // 五个热键都必须显式声明来源
  const binds = body.match(/bind\(\s*'Alt\+Shift/g) || [];
  assert.equal(binds.length, 5, `热键数量应为 5，实际 ${binds.length}`);

  const withSource = body.match(/TRIGGER_SOURCES\.hotkey/g) || [];
  assert.equal(withSource.length, 2, '开关与重置两个热键必须各带一个 hotkey 来源标记');
});

test('结构守卫 · 开关悬浮窗的决策已收拢（热键不再自己 if/else）', () => {
  const body = sliceFunction(MAIN_SRC, 'registerOverlayShortcuts');

  // 原先这里是 `if (isOverlayOpen()) closeOverlayWindow(); else openOverlayWindow();`
  assert.doesNotMatch(body, /isOverlayOpen\s*\(\s*\)/, '热键回调里又出现了自己判断开闭');
  assert.match(body, /toggleOverlayWindow\s*\(/);
});

test('结构守卫 · main.cjs 已接入 overlay-actions 模块', () => {
  assert.match(MAIN_SRC, /require\(\s*'\.\/overlay-actions\.cjs'\s*\)/);
  assert.match(MAIN_SRC, /overlayActions\./);
});

test('结构守卫 · 日志与 toast 频道都是常量，不支持内联字面量', () => {
  // 频道名写错不会报错，只会「什么提示都不出现」，这类静默失败值得用常量挡
  assert.match(MAIN_CODE, /const TOAST_CHANNEL = 'overlay:toast'/);
  assert.equal(
    (MAIN_CODE.match(/sendToOverlay\(\s*'overlay:toast'/g) || []).length,
    0,
    '又出现了 sendToOverlay(\'overlay:toast\', ...) 的字面量调用',
  );
});
