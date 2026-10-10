/**
 * 版本号一致性守卫：`frontend/package.json` ↔ `backend/main.py` 的 APP_VERSION
 *
 * 为什么需要：本项目一个版本号有**三个出口**，说的却是同一件事 ——
 *   1. `frontend/package.json` 的 version → electron-builder 用它拼安装包文件名
 *      （`tingkebanlv-<version>-setup.exe`），也是主进程 `app.getVersion()` 的来源；
 *   2. `backend/main.py` 的 APP_VERSION → `/api/ping` 返回给前端（main.py:211），
 *      启动横幅里也打它；
 *   3. git tag / Release Note / L4 报告里的包名。
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

const pkgVersion = JSON.parse(fs.readFileSync(PKG, 'utf8')).version;

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
