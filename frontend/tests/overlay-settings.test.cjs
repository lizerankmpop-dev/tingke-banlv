/**
 * 悬浮窗配置解析 / 持久化 单元测试
 *
 * 这些用例的存在理由都是真实事故或高风险场景：
 *   - Number(null) === 0 把「未存位置」变成坐标 0（真发生过）
 *   - 配置写到一半崩溃 → 下次启动所有设置归零
 *   - 老版本配置缺新字段 → 升级后行为突变
 *   - 外接屏拔掉 → 悬浮窗跑到看不见的地方（鼠标穿透，用户救不回来）
 *
 * 运行：npm run test
 */

const { test } = require('node:test');
const assert = require('node:assert/strict');

const {
  SCHEMA_VERSION,
  DEFAULT_OVERLAY_SETTINGS,
  clampNumber,
  intOrNull,
  isFiniteNumber,
  migrateConfig,
  normalizeOverlaySettings,
  defaultSettings,
  sameBounds,
  isOnDisplay,
  isOnSomeDisplay,
  clampIntoWorkArea,
  computeAnchor,
  boundsFromAnchor,
  resolveBoundsForDisplay,
  writeConfigAtomic,
  readConfig,
} = require('../electron/overlay-settings.cjs');

const PRIMARY = { x: 0, y: 0, width: 1707, height: 1019 };
const SECONDARY = { x: 1707, y: 0, width: 1920, height: 1080 };
/** Electron 真实形态：带 id 的显示器对象 */
const D_PRIMARY = { id: 1, workArea: PRIMARY };
const D_SECOND = { id: 2, workArea: SECONDARY };

/** 内存 fs 桩：实现这套模块用到的方法（含文件锁所需的 fd 系列） */
function makeFs(initial = {}) {
  const files = new Map(Object.entries(initial));
  const mtimes = new Map();
  const fds = new Map();
  let nextFd = 1;
  return {
    files,
    mtimes,
    existsSync: (p) => files.has(p),
    readFileSync: (p) => {
      if (!files.has(p)) throw new Error(`ENOENT: ${p}`);
      return files.get(p);
    },
    writeFileSync: (p, text) => files.set(p, text),
    renameSync: (from, to) => {
      if (!files.has(from)) throw new Error(`ENOENT: ${from}`);
      files.set(to, files.get(from));
      files.delete(from);
    },
    openSync: (p, flags) => {
      if (flags === 'wx' && files.has(p)) {
        const err = new Error(`EEXIST: ${p}`);
        err.code = 'EEXIST';
        throw err;
      }
      if (!files.has(p)) files.set(p, '');
      const fd = nextFd++;
      fds.set(fd, p);
      return fd;
    },
    writeSync: (fd, text) => {
      const p = fds.get(fd);
      if (!p) throw new Error(`EBADF: ${fd}`);
      files.set(p, `${files.get(p)}${text}`);
      return String(text).length;
    },
    closeSync: (fd) => {
      fds.delete(fd);
    },
    statSync: (p) => {
      if (!files.has(p)) throw new Error(`ENOENT: ${p}`);
      return { mtimeMs: mtimes.has(p) ? mtimes.get(p) : Date.now() };
    },
    unlinkSync: (p) => {
      if (!files.has(p)) throw new Error(`ENOENT: ${p}`);
      files.delete(p);
    },
    mkdirSync: () => {},
  };
}

// ============ 空值处理（历史 bug 的回归护栏） ============

test('未存过位置：null 必须保持 null，不能被当成 0', () => {
  const s = normalizeOverlaySettings(null);
  assert.equal(s.bounds.x, null);
  assert.equal(s.bounds.y, null);
  // 反例：如果实现里写成 Number.isFinite(Number(v))，这里会得到 0
  assert.notEqual(s.bounds.x, 0);
});

test('字符串空值同样回退到默认值而不是 0', () => {
  assert.equal(intOrNull(''), null);
  assert.equal(intOrNull(undefined), null);
  assert.equal(clampNumber('', 12, 72, 24), 24);
  assert.equal(clampNumber(null, 12, 72, 24), 24);
  assert.equal(clampNumber(undefined, 0, 1, 0.55), 0.55);
});

test('合法的 0 仍然要保留（0 是有效坐标）', () => {
  assert.equal(intOrNull(0), 0);
  assert.equal(clampNumber(0, 0, 1, 0.55), 0);
});

// ============ 越界夹取 ============

test('越界值被夹到安全区间', () => {
  const s = normalizeOverlaySettings({
    fontSize: 500,
    lineCount: 99,
    textBgOpacity: 5,
    windowOpacity: 0,
    bounds: { x: 10, y: 20, width: 1, height: 99999 },
  });
  assert.equal(s.fontSize, 72);
  assert.equal(s.lineCount, 6);
  assert.equal(s.textBgOpacity, 1);
  // 透明度下界 0.2：再低用户就完全看不见悬浮窗了，等于丢失功能
  assert.equal(s.windowOpacity, 0.2);
  assert.equal(s.bounds.width, 320);
  assert.equal(s.bounds.height, 2000);
  assert.equal(s.bounds.x, 10);
});

test('非法颜色回退默认，合法颜色保留', () => {
  assert.equal(normalizeOverlaySettings({ color: 'white' }).color, '#ffffff');
  assert.equal(normalizeOverlaySettings({ color: '#FFF' }).color, '#ffffff');
  assert.equal(normalizeOverlaySettings({ color: '#ffcc00' }).color, '#ffcc00');
});

test('脏数据整体不抛异常', () => {
  for (const bad of [undefined, null, 'string', 42, [], { bounds: 'x' }, { bounds: [] }]) {
    const s = normalizeOverlaySettings(bad);
    assert.equal(typeof s.fontSize, 'number');
    assert.equal(typeof s.bounds, 'object');
  }
});

// ============ 版本迁移 ============

test('v1 配置迁移到 v2：补上 backgroundUpdate 且保持原有行为', () => {
  const v1 = { enabled: true, fontSize: 30, bounds: { x: null, y: null, width: 900, height: 220 } };
  assert.equal(v1.schemaVersion, undefined);
  const migrated = migrateConfig(v1);
  assert.equal(migrated.schemaVersion, SCHEMA_VERSION);
  // v1 时期代码硬编码 backgroundThrottling:false，等价于「开着」→ 老用户行为不变
  assert.equal(migrated.backgroundUpdate, true);

  const s = normalizeOverlaySettings(v1);
  assert.equal(s.enabled, true);
  assert.equal(s.fontSize, 30);
  assert.equal(s.backgroundUpdate, true);
  assert.equal(s.schemaVersion, SCHEMA_VERSION);
});

test('v2 配置里显式关闭 backgroundUpdate 必须被尊重（不能被迁移覆盖）', () => {
  const s = normalizeOverlaySettings({ schemaVersion: 2, backgroundUpdate: false });
  assert.equal(s.backgroundUpdate, false);
  assert.equal(migrateConfig({ schemaVersion: 2, backgroundUpdate: false }).backgroundUpdate, false);
});

test('v2 配置迁移到 v3：补上空锚点，已有设置不受影响', () => {
  const s = normalizeOverlaySettings({
    schemaVersion: 2,
    enabled: true,
    fontSize: 30,
    backgroundUpdate: false,
  });
  assert.equal(s.schemaVersion, SCHEMA_VERSION);
  assert.equal(s.enabled, true);
  assert.equal(s.fontSize, 30);
  // 迁移不能把用户显式关掉的后台更新打开
  assert.equal(s.backgroundUpdate, false);
  assert.deepEqual(s.anchor, { displayId: null, relX: null, relY: null });
  assert.equal(migrateConfig({ schemaVersion: 2 }).anchor, null);
});

test('默认设置即当前版本，字段齐全', () => {
  const s = defaultSettings();
  assert.equal(s.schemaVersion, SCHEMA_VERSION);
  for (const key of Object.keys(DEFAULT_OVERLAY_SETTINGS)) {
    assert.ok(key in s, `默认设置缺字段 ${key}`);
  }
});

test('默认锚点为空（首次运行没有可还原的相对位置）', () => {
  assert.deepEqual(defaultSettings().anchor, { displayId: null, relX: null, relY: null });
});

// ============ 显示器 / 位置自愈 ============

test('isOnDisplay 判断与工作区的重叠', () => {
  assert.equal(isOnDisplay({ x: 100, y: 100, width: 900, height: 220 }, PRIMARY), true);
  // 完全在屏幕外
  assert.equal(isOnDisplay({ x: 5000, y: 100, width: 900, height: 220 }, PRIMARY), false);
  assert.equal(isOnDisplay({ x: -2000, y: 100, width: 900, height: 220 }, PRIMARY), false);
  // 只露出一点边（几乎看不见）→ 视为不可用
  assert.equal(isOnDisplay({ x: -880, y: 100, width: 900, height: 220 }, PRIMARY), false);
  // y 方向跑到屏幕下方
  assert.equal(isOnDisplay({ x: 100, y: 1010, width: 900, height: 220 }, PRIMARY), false);
});

test('首次运行没有位置 → 底部居中', () => {
  const b = resolveBoundsForDisplay({ x: null, y: null, width: 900, height: 220 }, PRIMARY, [PRIMARY]);
  assert.equal(b.x, Math.round((1707 - 900) / 2));
  assert.equal(b.y, 1019 - 220 - 80);
});

test('位置落在第二显示器上必须保留（不能被强行拉回主屏）', () => {
  const saved = { x: 2000, y: 300, width: 900, height: 220 };
  const b = resolveBoundsForDisplay(saved, PRIMARY, [PRIMARY, SECONDARY]);
  assert.deepEqual(b, saved);
});

test('第二显示器被拔掉 → 自动回到主屏（否则用户永远看不到悬浮窗）', () => {
  const saved = { x: 2000, y: 300, width: 900, height: 220 };
  const b = resolveBoundsForDisplay(saved, PRIMARY, [PRIMARY]);
  assert.ok(isOnDisplay(b, PRIMARY), '修正后必须落在可见工作区');
  assert.notEqual(b.x, 2000);
});

test('没有显示器信息时退化为按主屏判断', () => {
  const b = resolveBoundsForDisplay({ x: 5000, y: 5000, width: 900, height: 220 }, PRIMARY, []);
  assert.ok(isOnDisplay(b, PRIMARY));
});

test('isOnSomeDisplay 空数组 / 缺字段都返回 false', () => {
  assert.equal(isOnSomeDisplay({ x: 1, y: 1, width: 10, height: 10 }, []), false);
  assert.equal(isOnSomeDisplay({ x: null, y: null, width: 10, height: 10 }, [PRIMARY]), false);
  assert.equal(isOnSomeDisplay({ x: 1, y: 1, width: 10, height: 10 }, undefined), false);
});

// ============ 原子读写 ============

test('原子写：读回来内容一致，且不残留 .tmp', () => {
  const fs = makeFs();
  const path = 'overlay.json';
  const settings = normalizeOverlaySettings({ fontSize: 34, color: '#ffcc00' });
  writeConfigAtomic(fs, path, settings);

  assert.deepEqual(JSON.parse(fs.files.get(path)), settings);
  assert.equal(fs.files.has(`${path}.tmp`), false, '临时文件必须被 rename 掉');

  const read = readConfig(fs, path);
  assert.equal(read.reason, 'ok');
  assert.equal(read.settings.fontSize, 34);
  assert.equal(read.settings.color, '#ffcc00');
});

test('写入失败时不能把原文件写坏（rename 前崩溃）', () => {
  const path = 'overlay.json';
  const good = JSON.stringify(normalizeOverlaySettings({ fontSize: 30 }));
  const fs = makeFs({ [path]: good });
  // 模拟「写到临时文件后进程被杀」：rename 不执行
  fs.renameSync = () => {
    throw new Error('EPERM: 进程被终止');
  };
  assert.throws(() => writeConfigAtomic(fs, path, normalizeOverlaySettings({ fontSize: 60 })));
  const read = readConfig(fs, path);
  assert.equal(read.reason, 'ok', '原文件必须仍然可解析');
  assert.equal(read.settings.fontSize, 30, '原设置不能被破坏');
});

test('配置文件损坏 → 回退出厂默认并给出原因', () => {
  const path = 'overlay.json';
  for (const broken of ['{ 截断的 JSON', '', 'null', '[1,2,3]', '不是 JSON']) {
    const fs = makeFs({ [path]: broken });
    const read = readConfig(fs, path);
    assert.equal(read.reason, 'corrupt', `${JSON.stringify(broken)} 应判为 corrupt`);
    assert.ok(read.error, '必须带上原因供日志记录');
    assert.deepEqual(read.settings, defaultSettings());
  }
});

test('配置文件不存在 → reason=absent（不是 corrupt，不该报错）', () => {
  const read = readConfig(makeFs(), 'overlay.json');
  assert.equal(read.reason, 'absent');
  assert.equal(read.migrated, false);
  assert.deepEqual(read.settings, defaultSettings());
});

test('读取时报告是否需要迁移（便于顺手回写新版本）', () => {
  const path = 'overlay.json';
  const v1 = JSON.stringify({ enabled: true, fontSize: 30 });
  const read = readConfig(makeFs({ [path]: v1 }), path);
  assert.equal(read.reason, 'ok');
  assert.equal(read.migrated, true);

  const v2 = JSON.stringify(normalizeOverlaySettings(null));
  const read2 = readConfig(makeFs({ [path]: v2 }), path);
  assert.equal(read2.migrated, false);
});

test('readFileSync 抛异常（权限/被占用）也不能让应用起不来', () => {
  const fs = makeFs({ 'overlay.json': '{}' });
  fs.readFileSync = () => {
    throw new Error('EBUSY: 文件被占用');
  };
  const read = readConfig(fs, 'overlay.json');
  assert.equal(read.reason, 'corrupt');
  assert.equal(read.settings.fontSize, DEFAULT_OVERLAY_SETTINGS.fontSize);
});

test('isFiniteNumber 不接受 null / "" / 数字字符串', () => {
  assert.equal(isFiniteNumber(0), true);
  assert.equal(isFiniteNumber(-1.5), true);
  assert.equal(isFiniteNumber(null), false);
  assert.equal(isFiniteNumber(''), false);
  assert.equal(isFiniteNumber('12'), false);
  assert.equal(isFiniteNumber(NaN), false);
  assert.equal(isFiniteNumber(Infinity), false);
});

test('sameBounds：任一侧为空都算不同，避免空值把好位置覆盖掉', () => {
  const b = { x: 1, y: 2, width: 3, height: 4 };
  assert.equal(sameBounds(b, { ...b }), true);
  assert.equal(sameBounds(b, { ...b, x: 9 }), false);
  assert.equal(sameBounds(b, null), false);
  assert.equal(sameBounds(null, b), false);
});

// ============ 相对锚点（多显示器 / 高 DPI） ============

test('computeAnchor：由绝对位置算出相对比例', () => {
  const a = computeAnchor({ x: 2000, y: 540, width: 900, height: 220 }, D_SECOND);
  assert.equal(a.displayId, 2);
  assert.equal(a.relX, Number(((2000 - 1707) / 1920).toFixed(6)));
  assert.equal(a.relY, Number((540 / 1080).toFixed(6)));
});

test('computeAnchor：位置无效 / 无显示器信息时返回全 null（不可用的锚点）', () => {
  const empty = { displayId: null, relX: null, relY: null };
  // 首次运行：x/y 是 null，不能因为 Number(null)===0 就给出 relX=0
  assert.deepEqual(computeAnchor({ x: null, y: null, width: 900, height: 220 }, D_PRIMARY), empty);
  assert.deepEqual(computeAnchor({ x: 10, y: 10, width: 900, height: 220 }, null), empty);
  assert.deepEqual(computeAnchor(null, D_PRIMARY), empty);
});

test('computeAnchor：比例被夹到 0~1（窗口被拖出屏时不会存出 1.8 这种值）', () => {
  const a = computeAnchor({ x: 99999, y: -99999, width: 900, height: 220 }, D_PRIMARY);
  assert.equal(a.relX, 1);
  assert.equal(a.relY, 0);
});

test('锚点比例 0 是合法值（贴工作区左上边），不能被当成「未设置」', () => {
  const s = normalizeOverlaySettings({ anchor: { displayId: 1, relX: 0, relY: 0 } });
  assert.equal(s.anchor.relX, 0);
  assert.equal(s.anchor.relY, 0);
  assert.equal(s.anchor.displayId, 1);
  // 与「完全没锚点」必须可区分
  assert.notDeepEqual(s.anchor, { displayId: null, relX: null, relY: null });
});

test('boundsFromAnchor：原屏还在 → 按比例还原到同一块屏', () => {
  const anchor = { displayId: 2, relX: 0.5, relY: 0.5 };
  const pos = boundsFromAnchor(anchor, [D_PRIMARY, D_SECOND], PRIMARY);
  assert.equal(pos.x, Math.round(1707 + 0.5 * 1920));
  assert.equal(pos.y, Math.round(0.5 * 1080));
});

test('boundsFromAnchor：原屏被拔掉 → 退到主屏按同样比例摆放', () => {
  const anchor = { displayId: 2, relX: 0.5, relY: 0.2 };
  const pos = boundsFromAnchor(anchor, [D_PRIMARY], PRIMARY);
  assert.equal(pos.x, Math.round(0.5 * 1707));
  assert.equal(pos.y, Math.round(0.2 * 1019));
  assert.ok(isOnDisplay({ ...pos, width: 900, height: 220 }, PRIMARY), '必须落在主屏可见区');
});

test('boundsFromAnchor：锚点无效 / 无显示器信息 → null（交给调用方兜底）', () => {
  assert.equal(boundsFromAnchor({ displayId: null, relX: null, relY: null }, [D_PRIMARY], PRIMARY), null);
  assert.equal(boundsFromAnchor(null, [D_PRIMARY], PRIMARY), null);
  assert.equal(boundsFromAnchor({ relX: 0.5, relY: 0.5 }, [], null), null);
});

test('副屏被拔掉：有锚点时按相对比例回到主屏，而不是直接拍到底部居中', () => {
  const saved = { x: 2500, y: 400, width: 900, height: 220 };
  const anchor = computeAnchor(saved, D_SECOND);
  const b = resolveBoundsForDisplay(saved, PRIMARY, [D_PRIMARY], anchor);
  assert.ok(isOnDisplay(b, PRIMARY), '修正后必须可见');
  // 关键：位置由「相对比例 × 主屏尺寸」决定，保留用户的空间记忆
  assert.equal(b.x, Math.round(anchor.relX * PRIMARY.width));
  assert.equal(b.y, Math.round(anchor.relY * PRIMARY.height));
  // 反例：如果实现里忽略锚点直接走默认位，就会得到底部居中
  const fallback = resolveBoundsForDisplay(saved, PRIMARY, [D_PRIMARY], null);
  assert.equal(fallback.x, Math.round((PRIMARY.width - 900) / 2));
  assert.notEqual(b.y, fallback.y, '不能与「无锚点兜底」的结果相同');
});

test('位置仍有效时，锚点不得覆盖用户手动摆放的结果', () => {
  const saved = { x: 2000, y: 300, width: 900, height: 220 };
  const anchor = { displayId: 1, relX: 0, relY: 0 };
  const b = resolveBoundsForDisplay(saved, PRIMARY, [D_PRIMARY, D_SECOND], anchor);
  assert.deepEqual(b, saved);
});

test('分辨率变小导致窗口超出工作区：锚点还原后被夹回可见范围', () => {
  // 用户原来在 1920 宽的屏上把窗口放在最右边
  const anchor = { displayId: 1, relX: 1, relY: 1 };
  const small = { x: 0, y: 0, width: 1024, height: 768 };
  const b = resolveBoundsForDisplay({ x: 3000, y: 3000, width: 900, height: 220 }, small, [{ id: 1, workArea: small }], anchor);
  assert.ok(isOnDisplay(b, small), '夹取后必须可见');
  assert.equal(b.x, 1024 - 900);
  assert.equal(b.y, 768 - 220);
});

test('clampIntoWorkArea：窗口比屏还宽时贴左边，不产生负坐标', () => {
  const b = clampIntoWorkArea({ x: 9999, y: 9999, width: 3000, height: 3000 }, PRIMARY);
  assert.equal(b.x, PRIMARY.x);
  assert.equal(b.y, PRIMARY.y);
});

test('clampIntoWorkArea：没有工作区信息时原样返回', () => {
  const src = { x: 5, y: 6, width: 7, height: 8 };
  assert.deepEqual(clampIntoWorkArea(src, null), src);
});

test('裸工作区对象也能当显示器用（兼容单屏调用点）', () => {
  assert.equal(isOnSomeDisplay({ x: 100, y: 100, width: 900, height: 220 }, [PRIMARY]), true);
  assert.equal(isOnSomeDisplay({ x: 5000, y: 100, width: 900, height: 220 }, [PRIMARY]), false);
});

// ============ 写锁（尽力而为，不是正确性依赖） ============

test('正常写入不残留 .lock 与 .tmp', () => {
  const fs = makeFs();
  writeConfigAtomic(fs, 'overlay.json', normalizeOverlaySettings({ fontSize: 30 }));
  assert.equal(fs.files.has('overlay.json.lock'), false);
  assert.equal(fs.files.has('overlay.json.tmp'), false);
  assert.ok(fs.files.has('overlay.json'));
});

test('陈旧锁（持有进程已崩溃）会被回收，写入照常完成', () => {
  const fs = makeFs({ 'overlay.json': '{}' });
  fs.files.set('overlay.json.lock', '99999');
  fs.mtimes.set('overlay.json.lock', Date.now() - 60_000);
  writeConfigAtomic(fs, 'overlay.json', normalizeOverlaySettings({ fontSize: 40 }), { staleMs: 5000 });
  assert.equal(fs.files.has('overlay.json.lock'), false, '陈旧锁必须被回收');
  assert.equal(readConfig(fs, 'overlay.json').settings.fontSize, 40);
});

test('锁被活跃进程持有时：写入仍然完成，且不能删掉别人的锁', () => {
  const fs = makeFs({ 'overlay.json': '{}' });
  fs.files.set('overlay.json.lock', '1');
  fs.mtimes.set('overlay.json.lock', Date.now()); // 新鲜锁
  writeConfigAtomic(fs, 'overlay.json', normalizeOverlaySettings({ fontSize: 44 }), {
    retries: 2,
    waitMs: 1,
  });
  assert.equal(readConfig(fs, 'overlay.json').settings.fontSize, 44, '拿不到锁也必须写完');
  assert.equal(fs.files.has('overlay.json.lock'), true, '别人的锁不能被自己删掉');
});

test('fs 不支持文件锁（方法缺失）时不得阻断写入', () => {
  const fs = makeFs({ 'overlay.json': '{}' });
  delete fs.openSync;
  writeConfigAtomic(fs, 'overlay.json', normalizeOverlaySettings({ fontSize: 50 }));
  assert.equal(readConfig(fs, 'overlay.json').settings.fontSize, 50);
});

test('写入抛错时锁与 .tmp 都要被清理（否则下次启动会被自己的陈旧锁挡住）', () => {
  const fs = makeFs({ 'overlay.json': '{}' });
  fs.renameSync = () => {
    throw new Error('EPERM: 进程被终止');
  };
  assert.throws(() => writeConfigAtomic(fs, 'overlay.json', normalizeOverlaySettings({ fontSize: 60 })));
  assert.equal(fs.files.has('overlay.json.lock'), false, '锁必须释放');
  assert.equal(fs.files.has('overlay.json.tmp'), false, '临时文件必须清理');
  assert.equal(readConfig(fs, 'overlay.json').reason, 'ok');
});

test('锚点参与往返持久化：写盘再读回来保持一致', () => {
  const fs = makeFs();
  const path = 'overlay.json';
  const settings = normalizeOverlaySettings({
    enabled: true,
    anchor: computeAnchor({ x: 2200, y: 300, width: 900, height: 220 }, D_SECOND),
    bounds: { x: 2200, y: 300, width: 900, height: 220 },
  });
  writeConfigAtomic(fs, path, settings);
  const read = readConfig(fs, path);
  assert.equal(read.reason, 'ok');
  assert.deepEqual(read.settings.anchor, settings.anchor);
  assert.deepEqual(read.settings.bounds, settings.bounds);
  assert.equal(read.migrated, false);
});
