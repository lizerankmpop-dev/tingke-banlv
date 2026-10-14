/**
 * 版本号一致性守卫：`frontend/package.json` ↔ `backend/main.py` 的 APP_VERSION
 *
 * 为什么需要：本项目一个版本号有**四个出口**，说的却是同一件事 ——
 *   1. `frontend/package.json` 的 version → electron-builder 用它拼安装包文件名
 *      （`tingkebanlv-<version>-setup.exe`），也是主进程 `app.getVersion()` 的来源；
 *   2. `backend/main.py` 的 APP_VERSION → `/api/ping` 返回给前端（main.py:211），
 *      启动横幅里也打它；
 *   3. git tag / Release Note / L4 报告里的包名；
 *   4. `frontend/package-lock.json` 的根 name/version（见文末「锁文件」一节，
 *      它漂了 4 个版本才被发现，且它决定了进包的那棵 `node_modules`）。
 *
 * 实测踩到过：升版时改了 package.json、漏了 APP_VERSION，于是「装的是 3.4.1、
 * `/api/ping` 报 3.4.0」—— 排障时第一眼就会怀疑装错了包，而这类不一致**不会**
 * 让任何测试变红，只会让日志互相矛盾。
 *
 * 做法与 `onboarding-aumid.test.cjs` 守 `main.cjs AUMID ↔ build.appId` 同一套路：
 * 读两份真实文件、逐字比较，不做任何推断（值从源码里抠出来再比对，见项目教训 9）。
 *
 * 运行：npm run test
 */

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const PKG = path.join(__dirname, '..', 'package.json');
const MAIN = path.join(__dirname, '..', '..', 'backend', 'main.py');
const LOCK = path.join(__dirname, '..', 'package-lock.json');

const pkg = JSON.parse(fs.readFileSync(PKG, 'utf8'));
const pkgVersion = pkg.version;

/** 从真实源码里抠出 APP_VERSION（不照抄常量，否则守卫只会验证它自己） */
function readAppVersion(src) {
  // 允许行首缩进：常量将来若被挪进某个块里，正则不该跟着失效
  const m = /^[ \t]*APP_VERSION\s*=\s*"([^"]+)"/m.exec(src);
  return m ? m[1] : null;
}

test('package.json 的 version 与 backend/main.py 的 APP_VERSION 逐字一致', () => {
  const appVersion = readAppVersion(fs.readFileSync(MAIN, 'utf8'));
  assert.ok(appVersion, 'backend/main.py 里找不到 APP_VERSION = "..." —— 正则或实现已变，守卫失效');
  assert.equal(
    appVersion,
    pkgVersion,
    `版本号不一致：package.json=${pkgVersion} vs main.py APP_VERSION=${appVersion}。`
      + '升版必须同时改这两处（安装包名 / app.getVersion() / /api/ping 是同一件事的三个出口）',
  );
});

test('版本号形如 x.y.z（避免把 tag 名、日期或 pre 后缀误填进来）', () => {
  assert.match(pkgVersion, /^\d+\.\d+\.\d+$/, `package.json version 形状异常：${pkgVersion}`);
});

test('变异 · readAppVersion 真的从源码取值，而不是恒真或恒假', () => {
  // 反向自证：正则若写坏（恒返回 null，或回退成读 package.json），这几条会红。
  assert.equal(readAppVersion('APP_VERSION = "9.9.9"\n'), '9.9.9');
  assert.equal(readAppVersion('    APP_VERSION = "1.2.3"\n'), '1.2.3');
  assert.equal(readAppVersion('# 这一行没有版本号\n'), null);
  assert.equal(readAppVersion('APP_VERSION="x"\n'), 'x');
  assert.notEqual(readAppVersion('APP_VERSION = "0.0.0"\n'), pkgVersion);
});

// ---------------------------------------------------------------- 锁文件（第四个出口）

/**
 * 为什么锁文件也算「版本号的出口」：它一直漂到 2026-10-10 才被发现 ——
 * 连续 4 个版本（3.0.0→3.4.1）都停着 `tingke-banlv-frontend` / `3.0.0`，
 * 期间包名改过一次、版本升过三次，**锁文件一次都没跟上**，而没有任何测试变红。
 *
 * 它比看上去重要：`node_modules` 是**进安装包**的（实测 asar 顶层
 * `/electron` `/node_modules` `/package.json`，3688 个文件），
 * 而锁文件决定了那棵 `node_modules` 会长成什么样 ⇒ 它决定了安装包的哈希。
 *
 * 两条断言各自防一类事故：
 *   · 根 name/version 漂移 → 说明「改完 package.json 没重生成锁」（本次修的就是它）；
 *   · 依赖范围漂移 → `npm ci` 会直接判定「不同步」而失败（CI / 换机复现构建时炸）。
 *
 * 为什么不直接对 `package-lock.json` 跑 `npm install` 修：`node_modules` 进包，
 * 一旦解析器把 `^` 范围往上抬，asar 就变了、安装包哈希就变了，
 * 于是该版本已通过的 L4 与真机端到端证据**当场作废**。所以只做最小字段更正。
 */
test('package-lock.json 的根标识与依赖范围必须与 package.json 一致', () => {
  const lock = JSON.parse(fs.readFileSync(LOCK, 'utf8'));
  assert.equal(typeof lock.packages, 'object', 'package-lock.json 结构异常：没有 packages 字段');
  const root = lock.packages[''];
  assert.ok(root, 'package-lock.json 缺 packages[""] 根条目');

  assert.equal(lock.name, pkg.name, `锁文件顶层 name 陈旧：${lock.name} ≠ ${pkg.name}`);
  assert.equal(lock.version, pkgVersion, `锁文件顶层 version 陈旧：${lock.version} ≠ ${pkgVersion}`);
  assert.equal(root.name, pkg.name, `锁文件根包 name 陈旧：${root.name} ≠ ${pkg.name}`);
  assert.equal(root.version, pkgVersion, `锁文件根包 version 陈旧：${root.version} ≠ ${pkgVersion}`);

  for (const grp of ['dependencies', 'devDependencies']) {
    assert.deepEqual(
      root[grp] || {},
      pkg[grp] || {},
      `锁文件与 package.json 的 ${grp} 范围不一致 —— 加/删依赖后必须重生成锁，否则 npm ci 会拒绝安装`,
    );
  }
});

// ---------------------------------------------------------------- 界面显示链（V6-2）

/**
 * 帮助 / 关于页会把版本号**显示给用户看**，于是它成了「版本号的第五个出口」。
 *
 * 前四个出口（package.json / APP_VERSION / git tag / 锁文件）都是**文件之间**的一致性，
 * 这一条不同：它是「**界面显示的值到底从哪来**」。如果这一页把版本号写成一个字面量，
 * 那么每次升版它都会显示旧值 —— 而用户恰恰是**因为要报版本号**才会打开这一页。
 *
 * 这里把整条链静态钉住（不启动应用也能验）：
 *   package.json → app.getVersion() → 'diag:get-info' → preload diag.getInfo
 *                → diagBridge.getDiagInfo → HelpAboutPanel 显示
 * 任何一环被换成别的来源（写死、读配置、读 tag）都会被下面任一条断言拦下。
 */
const MAIN_CJS = path.join(__dirname, '..', 'electron', 'main.cjs');
const PRELOAD = path.join(__dirname, '..', 'electron', 'preload.cjs');
const PANEL = path.join(__dirname, '..', 'src', 'components', 'HelpAboutPanel.tsx');
const HELP_LIB = path.join(__dirname, '..', 'src', 'lib', 'helpCopy.ts');

test('界面版本号来自主进程 app.getVersion()（不得写死）', () => {
  const mainSrc = fs.readFileSync(MAIN_CJS, 'utf8');
  // 允许 ipcMain.handle(...) 里直接引用，或经过中间变量；只要求这一行没被换掉来源
  assert.match(
    mainSrc,
    /version:\s*app\.getVersion\(\)/,
    "'diag:get-info' 的 version 必须直接来自 app.getVersion()（electron 读 package.json）",
  );
  assert.match(
    mainSrc,
    /ipcMain\.handle\('diag:get-info'/,
    '缺少 diag:get-info 处理器 —— 帮助页拿不到版本号',
  );
});

test('preload 暴露 diag.getInfo，且指向同一个通道', () => {
  const src = fs.readFileSync(PRELOAD, 'utf8');
  assert.match(src, /diag:\s*\{/, 'preload 必须暴露 diag 命名空间');
  assert.match(
    src,
    /getInfo:\s*\(\)\s*=>\s*ipcRenderer\.invoke\('diag:get-info'\)/,
    'diag.getInfo 必须指向 diag:get-info（通道名不一致会静默 invoke 失败）',
  );
  assert.match(
    src,
    /openLogDir:\s*\(\)\s*=>\s*ipcRenderer\.invoke\('diag:open-log-dir'\)/,
    'diag.openLogDir 必须指向 diag:open-log-dir',
  );
});

test('帮助页显示的版本号取自主进程返回值，且全链无版本字面量', () => {
  const panel = fs.readFileSync(PANEL, 'utf8');
  assert.match(panel, /getDiagInfo\(\)/, '帮助页必须真的去取诊断信息');
  assert.match(panel, /info\?\.version/, '版本号必须来自 getInfo() 的返回值');

  // 界面源码里不得出现 x.y.z 形态的版本字面量 —— 有，就说明某一处把版本号写死了。
  //
  // ⚠️ 必须先剥块注释再扫：这两份文件的注释里**会**用例子说明「不要硬编码
  // 某个 `x.y.z` 这种写法」，不剥就会因说明性文字永远失败，然后被顺手放宽成
  // 一条更弱的断言 —— 那才是真正的失败（`panel-copy.test.mts` 已记过这个坑）。
  const stripBlockComments = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '');
  const VERSION_LITERAL = /['"`]\d+\.\d+\.\d+['"`]/;
  for (const [label, file] of [['HelpAboutPanel.tsx', PANEL], ['helpCopy.ts', HELP_LIB]]) {
    const m = VERSION_LITERAL.exec(stripBlockComments(fs.readFileSync(file, 'utf8')));
    assert.equal(
      m,
      null,
      `${label} 里出现了版本号字面量 ${m && m[0]} —— 升版时它会继续显示旧值，`
        + '而用户正是为了报版本号才打开这一页',
    );
  }
});
