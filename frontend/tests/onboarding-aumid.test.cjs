/**
 * AUMID 与应用图标「装配完整性」守卫（M3 · 任务栏双图标 / 图标缺失）
 *
 * 这两项的故障模式高度相似：**配置错了不报错、不崩溃，只在 Windows 的
 * 观感上悄悄退化** ——
 *   · AUMID 与 electron-builder 的 `appId` 不一致 → 任务栏「钉一个、
 *     跑起来又多一个」的双图标；用户只会觉得这软件有点怪
 *   · 图标路径写错 → 静默回落 Electron 默认图标；六处落点全跟着错
 * 所以必须在源码与配置层对拍，而不是等实装验收才发现。
 *
 * 对拍关系（任何一条断链都会造成上面描述的静默故障）：
 *   main.cjs 的 APP_USER_MODEL_ID  ←逐字一致→  package.json 的 build.appId
 *   main.cjs 的 setAppUserModelId  ←必须早于→  app.whenReady()（建窗之前）
 *   main.cjs 的 icon: APP_ICON     ←存在→     resolveAppIconPath() 的兜底
 *   package.json 的 win.icon       ←文件真实存在→  frontend/build/icon.ico
 *
 * 变异验证沿用项目既定套路：audit 是纯函数，把源码/配置**人为改坏**再跑，
 * 确认每条规则真的会失败 —— 一个永远通过的守卫比没有守卫更糟。
 *
 * 运行：npm run test
 */

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const PKG_PATH = path.join(__dirname, '..', 'package.json');
const MAIN_PATH = path.join(__dirname, '..', 'electron', 'main.cjs');
const ICON_PATH = path.join(__dirname, '..', 'build', 'icon.ico');

const PKG = JSON.parse(fs.readFileSync(PKG_PATH, 'utf8'));
const MAIN_SRC = fs.readFileSync(MAIN_PATH, 'utf8');

/** 剥掉块注释与整行注释后的源码 —— main.cjs 的注释里会引用这些字面量 */
const CODE = MAIN_SRC
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '');

// ---------------------------------------------------------------- 审计

/**
 * @param {string} code        剥过注释的 main.cjs 源码
 * @param {object} pkg         package.json 内容
 * @param {Buffer|null} icoBuf icon.ico 文件内容（null = 文件缺失）
 * @returns {string[]} 问题清单（空数组 = 通过）
 */
function audit(code, pkg, icoBuf) {
  const problems = [];
  const appId = pkg.build && pkg.build.appId;

  // ---- AUMID ----
  if (typeof appId !== 'string' || appId.trim() === '') {
    problems.push('package.json 缺少 build.appId：electron-builder 无从把 AUMID 写进快捷方式');
  }
  const m = code.match(/const\s+APP_USER_MODEL_ID\s*=\s*'([^']+)'/);
  if (!m) {
    problems.push('main.cjs 找不到 APP_USER_MODEL_ID 常量：运行时进程的 AUMID 将保持 Electron 默认');
  } else if (appId && m[1] !== appId) {
    problems.push(
      `AUMID 不一致：main.cjs='${m[1]}' vs package.json build.appId='${appId}'`
      + ' —— 两个值对不上就是任务栏双图标的直接成因',
    );
  }
  if (!/app\.setAppUserModelId\(\s*APP_USER_MODEL_ID\s*\)/.test(code)) {
    problems.push('main.cjs 未调用 app.setAppUserModelId(APP_USER_MODEL_ID)');
  }
  // 必须“未打包也要设”，但要有平台 guard（非 Windows 上该 API 无害，但保持口径统一）
  if (!/if\s*\(\s*process\.platform\s*===\s*'win32'\s*\)\s*\{\s*app\.setAppUserModelId/.test(code)) {
    problems.push("setAppUserModelId 应包在 process.platform === 'win32' 的 guard 里");
  }
  // 顺序：setAppUserModelId 必须出现在 app.whenReady() 之前（建窗之前）
  const setAt = code.search(/app\.setAppUserModelId\(/);
  const readyAt = code.search(/app\.whenReady\(\)/);
  if (setAt === -1 || readyAt === -1 || setAt > readyAt) {
    problems.push('app.setAppUserModelId 必须在 app.whenReady() 之前（即创建任何窗口之前）调用');
  }

  // ---- 窗口 icon + 缺失兜底 ----
  if (!/icon:\s*APP_ICON/.test(code)) {
    problems.push('BrowserWindow 配置里没有 icon: APP_ICON（开发模式下任务栏将显示 Electron 图标）');
  }
  if (!/function\s+resolveAppIconPath\(\)/.test(code)) {
    problems.push('找不到 resolveAppIconPath()：图标路径缺失时没有兜底逻辑');
  } else {
    const fnStart = code.indexOf('function resolveAppIconPath()');
    const fnBody = code.slice(fnStart, fnStart + 600);
    if (!fnBody.includes('app.isPackaged')) {
      problems.push('resolveAppIconPath 未区分打包模式：打包后 build/ 不在 asar 里，传不存在的路径属无效调用');
    }
    if (!fnBody.includes('fs.existsSync') && !fnBody.includes('existsSync')) {
      problems.push('resolveAppIconPath 未做存在性判断：ico 缺失时会把坏路径塞给 BrowserWindow');
    }
    if (!fnBody.includes('try')) {
      problems.push('resolveAppIconPath 没有 try/catch：existsSync 抛异常（权限等）会让主进程启动失败');
    }
  }

  // ---- 打包配置 ----
  const winIcon = pkg.build && pkg.build.win && pkg.build.win.icon;
  if (winIcon !== 'build/icon.ico') {
    problems.push(
      `package.json build.win.icon 应为 'build/icon.ico'，实际为 ${JSON.stringify(winIcon ?? null)}`
      + '（相对 frontend/，不是 ../build/icon.ico）',
    );
  }

  // ---- ico 文件本体（多尺寸容器）----
  if (!icoBuf) {
    problems.push('frontend/build/icon.ico 不存在：构建将回落 Electron 默认图标（六处落点全错）');
  } else {
    // ICONDIR: reserved(2) type(2) count(2)；随后 count × 16 字节目录项
    const reserved = icoBuf.readUInt16LE(0);
    const type = icoBuf.readUInt16LE(2);
    const count = icoBuf.readUInt16LE(4);
    if (reserved !== 0 || type !== 1) {
      problems.push(`icon.ico 头部非法（reserved=${reserved} type=${type}）—— 不是有效的 ico`);
    } else if (count < 7) {
      problems.push(`icon.ico 只有 ${count} 档尺寸，设计稿要求 16/24/32/48/64/128/256 七档`);
    } else {
      const sizes = new Set();
      for (let i = 0; i < count; i++) {
        const off = 6 + i * 16;
        const w = icoBuf.readUInt8(off) || 256;
        const h = icoBuf.readUInt8(off + 1) || 256;
        const dataOffset = icoBuf.readUInt32LE(off + 12);
        sizes.add(w);
        if (w !== h) {
          problems.push(`icon.ico 第 ${i + 1} 项宽高不一致（${w}x${h}）`);
        }
        // 256 档必须是 PNG 压缩（否则体积暴涨）；其余档 BMP
        const isPng = icoBuf.readUInt32BE(dataOffset) === 0x89504e47;
        if (w === 256 && !isPng) {
          problems.push('icon.ico 的 256 档未用 PNG 压缩（设计稿 §3.1），体积会暴涨到 MB 级');
        }
      }
      for (const need of [16, 24, 32, 48, 64, 128, 256]) {
        if (!sizes.has(need)) problems.push(`icon.ico 缺少 ${need}x${need} 档`);
      }
    }
  }
  return problems;
}

// ---------------------------------------------------------------- 当前源码

test('AUMID · main.cjs 与 package.json 逐字一致，且早于 whenReady', () => {
  assert.deepEqual(audit(CODE, PKG, fs.existsSync(ICON_PATH) ? fs.readFileSync(ICON_PATH) : null), []);
});

test('AUMID · 当前值就是既定的 com.tingkebanlv.app', () => {
  const m = CODE.match(/const\s+APP_USER_MODEL_ID\s*=\s*'([^']+)'/);
  assert.equal(m && m[1], 'com.tingkebanlv.app');
  assert.equal(PKG.build.appId, 'com.tingkebanlv.app');
});

// ---------------------------------------------------------------- 变异验证

/** 把一份源码/配置改坏再跑审计，断言问题清单里出现含 needle 的条目 */
function expectDetected(problems, needle, label) {
  assert.ok(
    problems.some((p) => p.includes(needle)),
    `变异「${label}」必须被守卫抓住；实际输出：\n${problems.join('\n') || '(空)'}`,
  );
}

test('变异 · AUMID 改成不一致的值 → 抓住', () => {
  const bad = CODE.replace(
    "const APP_USER_MODEL_ID = 'com.tingkebanlv.app';",
    "const APP_USER_MODEL_ID = 'com.example.other';",
  );
  expectDetected(audit(bad, PKG, null), 'AUMID 不一致', 'AUMID 漂移');
});

test('变异 · 删掉 setAppUserModelId 调用 → 抓住', () => {
  const bad = CODE.replace(/app\.setAppUserModelId\(APP_USER_MODEL_ID\);/, '');
  expectDetected(audit(bad, PKG, null), '未调用 app.setAppUserModelId', 'AUMID 调用缺失');
});

test('变异 · setAppUserModelId 挪到 whenReady 之后 → 抓住', () => {
  const bad = CODE
    .replace(/if\s*\(\s*process\.platform\s*===\s*'win32'\s*\)\s*\{\s*app\.setAppUserModelId\(APP_USER_MODEL_ID\);\s*\}/, '')
    .replace(/app\.whenReady\(\)/, "app.whenReady()\n  if (process.platform === 'win32') { app.setAppUserModelId(APP_USER_MODEL_ID); }");
  expectDetected(audit(bad, PKG, null), '之前', '调用时机过晚');
});

test('变异 · 删掉窗口 icon 参数 → 抓住', () => {
  const bad = CODE.replace(/icon:\s*APP_ICON,/, '');
  expectDetected(audit(bad, PKG, null), 'icon: APP_ICON', '窗口图标缺失');
});

test('变异 · win.icon 路径写错（../build/icon.ico）→ 抓住', () => {
  const badPkg = JSON.parse(JSON.stringify(PKG));
  badPkg.build.win.icon = '../build/icon.ico';
  expectDetected(audit(CODE, badPkg, null), 'build/icon.ico', '打包配置路径');
});

test('变异 · ico 文件缺失 → 抓住', () => {
  expectDetected(audit(CODE, PKG, null), '不存在', '图标文件缺失');
});

test('变异 · ico 被换成单尺寸假货 → 抓住', () => {
  // 构造一个只有 32x32 单档的假 ico（真实事故形态：美术只导了一档）
  const head = Buffer.alloc(6);
  head.writeUInt16LE(0, 0); head.writeUInt16LE(1, 2); head.writeUInt16LE(1, 4);
  const entry = Buffer.alloc(16);
  entry.writeUInt8(32, 0); entry.writeUInt8(32, 1);
  entry.writeUInt16LE(1, 6); entry.writeUInt16LE(32, 8);
  entry.writeUInt32LE(70, 12); // data offset
  const pngHead = Buffer.from([0x89, 0x50, 0x4e, 0x47]); // 让 32 档是 PNG（不触发 256 规则）
  const fake = Buffer.concat([head, entry, pngHead, Buffer.alloc(64)]);
  const problems = audit(CODE, PKG, fake);
  expectDetected(problems, '1 档尺寸', '尺寸档不足');
});

test('变异 · resolveAppIconPath 被改成不做存在性判断 → 抓住', () => {
  const bad = CODE.replace(/return fs\.existsSync\(p\) \? p : undefined;/, 'return p;');
  expectDetected(audit(bad, PKG, null), '存在性判断', '兜底被拆');
});
