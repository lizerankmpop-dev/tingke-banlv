/**
 * 悬浮窗配置 —— 纯逻辑（不碰 Electron / 不碰真实 fs）
 *
 * 抽出来的原因：这段逻辑承载了「用户设置不能丢」这一硬要求，而它历史上
 * 出过一次不报错的 bug —— `Number.isFinite(Number(null))` 为 true，把
 * 「还没存过位置」的 null 静默转成了坐标 0，默认位置永远算不出来。
 * 放在 main.cjs 里既跑不了单测，也没人会去看。
 *
 * fs 由调用方注入（main.cjs 传真实 fs，测试传内存桩），因此这里的分支
 * 全部可测：损坏文件、缺字段、旧版本、越界值、写到一半崩溃、锁被占用。
 *
 * 运行测试：cd frontend && npm run test
 */

/** 配置结构版本：新增/变更字段时 +1，并在 migrateConfig 里写清迁移规则 */
const SCHEMA_VERSION = 4;

const DEFAULT_OVERLAY_SETTINGS = {
  schemaVersion: SCHEMA_VERSION,
  enabled: false,
  fontSize: 24,
  color: '#ffffff',
  lineCount: 3,
  textBgOpacity: 0.55,
  windowOpacity: 1,
  clickThrough: true,
  /**
   * 后台持续字幕更新
   *
   * true（默认）= 悬浮窗打开时关掉 Chromium 后台节流 → 切到浏览器看网课字幕照常更新，
   * 代价是主窗口在后台仍会跑 500ms 轮询，CPU / 耗电略升。
   * 这是产品权衡，必须让用户自己选（设置面板里带功耗提示）。
   */
  backgroundUpdate: true,
  /**
   * 术语提示词自动降级（防重复循环兜底）
   *
   * 背景：术语热词是作为 Whisper 的 initial_prompt 注入的，长列表会被模型当成
   * 「上文」继续往下编 —— 实测会出现重复循环，单次识别 RTF 从 0.42 涨到 1.18
   * （突破实时）。P0-3 前的观测中，794 字符的术语表就足以诱发这一现象。
   *
   * true（默认）= 检出「本段转写文本出现长距离重复」时
   *   （判据见 backend/asr/repetition.py，默认 min_repeat_chars=35），
   *   把术语提示词的 **token 预算** 从 PROMPT_TOKEN_BUDGET 降到
   *   DEGRADE_TOKEN_BUDGET，超出预算的低优先级术语被丢弃，并打 WARN。
   * 降级是**事后反应**：它挡不住首次发生，只能让后续片段恢复正常。
   * 关掉它 = 完全保留术语表的原貌（识别质量优先，接受偶发重复）。
   *
   * ⚠️ 两个曾经写在这里、现已失效的说法，勿再照抄：
   *   1. 「自动把提示词截断到 200 字符」—— 那是 P0-3 之前的做法。按字符砍尾巴
   *      等于砍掉优先级最高的术语，正是 P0-3 修掉的缺陷。现在提示词字符串
   *      原样保留，变的只是组装时的 token 预算（见 backend/asr/prompt_budget.py）。
   *   2. 「检测到『输出字幕数 > 输入片段数』」—— 那是 P0-2 之前的判据。
   *      旧判据用的是单调结构信号，真实复读会被漏掉；现判据直接看文本里
   *      有没有长距离重复，见 backend/asr/repetition.py。
   *
   * 另注：缺省值 true 是**三重对齐**的（本文件 DEFAULT_OVERLAY_SETTINGS、
   * 后端 `_auto_degrade`、前端 types 里的 termAutoDegrade），
   * 改这里要同步改另外两处。UI 判断请用 `!== false`，不要用真值判断。
   */
  termAutoDegrade: true,
  bounds: { x: null, y: null, width: 900, height: 220 },
  /**
   * 相对坐标锚点（多显示器 / 高 DPI 自愈用）
   *
   * 绝对坐标在多屏场景下必然失效：外接屏拔掉、分辨率从 100% 改到 150%、
   * 笔记本外接投影仪 —— 同一组 x/y 会落到「不存在的那块空间」里。
   * 而悬浮窗是鼠标穿透的，跑到屏幕外用户既点不到也拖不回来。
   * relX / relY 是相对所在显示器工作区的比例（0~1），显示器没了就照比例
   * 搬到还在的屏幕上，位置感基本一致。
   */
  anchor: { displayId: null, relX: null, relY: null },
};

/**
 * 空值必须显式判 null/undefined/''，不能只靠 Number.isFinite(Number(v))
 * 因为 Number(null) === 0、Number('') === 0 —— 会把「没配」误判成「配了 0」
 */
function clampNumber(value, lo, hi, fallback) {
  if (value === null || value === undefined || value === '') return fallback;
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(hi, Math.max(lo, n));
}

function intOrNull(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? Math.round(n) : null;
}

/** 只认真正的 number 类型：`Number(null)` 是 0，判空不能走隐式转换 */
function isFiniteNumber(v) {
  return typeof v === 'number' && Number.isFinite(v);
}

/**
 * 保护性开关的布尔归一：默认 true，只有**显式**的假值才算关闭
 *
 * 与 backgroundUpdate 的 `=== undefined ? true : !!v` 刻意不同，原因是失败方向：
 * 这个开关是防「字幕重复循环」的兜底，配置被写坏（值变成 null / ''）时
 * 宁可多截一次提示词，也不能静默失去保护能力 —— 用户看到的是「字幕开始重复」
 * 且没有任何提示。反过来多开一次兜底，最坏只是术语表短了一点。
 */
function boolOrTrue(value) {
  if (value === undefined || value === null || value === '') return true;
  return !!value;
}

/**
 * 版本迁移
 *
 * v1（无 schemaVersion）：没有 backgroundUpdate 概念，当时代码里是硬编码
 *   `backgroundThrottling: false`，行为等价于「开着」。所以老配置补 true，
 *   用户升级后行为不变。
 * v2 → v3：新增 anchor（多显示器相对坐标）。老配置没有它 → 留空，
 *   由 normalizeOverlaySettings 补成 { displayId: null, relX: null, relY: null }，
 *   下次拖窗口时自然写上新值，无需回填历史位置。
 * v3 → v4：新增 termAutoDegrade（术语提示词自动降级）。引擎里的默认值一直是
 *   开启（`AUTO_DEGRADE_ENABLED`），所以老配置补 true，升级后行为不变。
 */
function migrateConfig(raw) {
  const src = raw && typeof raw === 'object' ? { ...raw } : {};
  const version = Number.isInteger(src.schemaVersion) ? src.schemaVersion : 1;
  if (version < 2 && src.backgroundUpdate === undefined) {
    src.backgroundUpdate = true;
  }
  if (version < 3 && src.anchor === undefined) {
    src.anchor = null;
  }
  if (version < 4 && (src.termAutoDegrade === undefined || src.termAutoDegrade === null)) {
    src.termAutoDegrade = true;
  }
  src.schemaVersion = SCHEMA_VERSION;
  return src;
}

function normalizeOverlaySettings(raw) {
  const src = migrateConfig(raw);
  const b = src.bounds && typeof src.bounds === 'object' && !Array.isArray(src.bounds) ? src.bounds : {};
  const a = src.anchor && typeof src.anchor === 'object' && !Array.isArray(src.anchor) ? src.anchor : {};
  return {
    schemaVersion: SCHEMA_VERSION,
    enabled: !!src.enabled,
    fontSize: Math.round(clampNumber(src.fontSize, 12, 72, DEFAULT_OVERLAY_SETTINGS.fontSize)),
    color:
      typeof src.color === 'string' && /^#[0-9a-fA-F]{6}$/.test(src.color)
        ? src.color
        : DEFAULT_OVERLAY_SETTINGS.color,
    lineCount: Math.round(clampNumber(src.lineCount, 1, 6, DEFAULT_OVERLAY_SETTINGS.lineCount)),
    textBgOpacity: clampNumber(src.textBgOpacity, 0, 1, DEFAULT_OVERLAY_SETTINGS.textBgOpacity),
    windowOpacity: clampNumber(src.windowOpacity, 0.2, 1, DEFAULT_OVERLAY_SETTINGS.windowOpacity),
    clickThrough: src.clickThrough === undefined ? true : !!src.clickThrough,
    backgroundUpdate:
      src.backgroundUpdate === undefined ? true : !!src.backgroundUpdate,
    termAutoDegrade: boolOrTrue(src.termAutoDegrade),
    bounds: {
      x: intOrNull(b.x),
      y: intOrNull(b.y),
      width: Math.round(clampNumber(b.width, 320, 4000, DEFAULT_OVERLAY_SETTINGS.bounds.width)),
      height: Math.round(clampNumber(b.height, 100, 2000, DEFAULT_OVERLAY_SETTINGS.bounds.height)),
    },
    anchor: {
      displayId: intOrNull(a.displayId),
      // 比例允许恰好 0（贴工作区左/上边），因此不能用 || 兜底
      relX: clampNumber(a.relX, 0, 1, null),
      relY: clampNumber(a.relY, 0, 1, null),
    },
  };
}

function defaultSettings() {
  return normalizeOverlaySettings(null);
}

function sameBounds(a, b) {
  if (!a || !b) return false;
  return a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height;
}

/**
 * 兼容两种「显示器」入参
 *   - Electron 真实形态：{ id, workArea: { x, y, width, height } }
 *   - 裸工作区：{ x, y, width, height }（单测、以及只有工作区可用的调用点）
 * 统一成 { id, workArea }，上层逻辑就不必到处判形状。
 */
function displayWorkArea(display) {
  if (!display) return null;
  if (display.workArea && isFiniteNumber(display.workArea.width)) return display.workArea;
  if (isFiniteNumber(display.width) && isFiniteNumber(display.height)) return display;
  return null;
}

function normalizeDisplays(displays) {
  if (!Array.isArray(displays)) return [];
  const out = [];
  for (const d of displays) {
    const wa = displayWorkArea(d);
    if (!wa) continue;
    out.push({ id: isFiniteNumber(d && d.id) ? d.id : null, workArea: wa });
  }
  return out;
}

/** 与某个显示器工作区是否有足够重叠（否则判定为「跑到屏幕外了」） */
function isOnDisplay(bounds, workArea) {
  const b = bounds || {};
  if (b.x === null || b.y === null || b.x === undefined || b.y === undefined) return false;
  if (!isFiniteNumber(Number(b.x)) || !isFiniteNumber(Number(b.y))) return false;
  if (!workArea) return false;
  return (
    b.x + 80 < workArea.x + workArea.width &&
    b.x + b.width - 80 > workArea.x &&
    b.y + 20 < workArea.y + workArea.height &&
    b.y + b.height - 20 > workArea.y
  );
}

function isOnSomeDisplay(bounds, displays) {
  const list = normalizeDisplays(displays);
  if (!list.length) return false;
  const b = bounds || {};
  if (b.x === null || b.y === null || b.x === undefined || b.y === undefined) return false;
  return list.some((d) => isOnDisplay(b, d.workArea));
}

/** 把窗口夹进工作区，保证整窗可见（穿透窗口只露一角等于丢失功能） */
function clampIntoWorkArea(bounds, workArea) {
  const b = { ...bounds };
  if (!workArea) return b;
  const maxX = workArea.x + workArea.width - b.width;
  const maxY = workArea.y + workArea.height - b.height;
  b.x = Math.round(Math.min(Math.max(b.x, workArea.x), Math.max(workArea.x, maxX)));
  b.y = Math.round(Math.min(Math.max(b.y, workArea.y), Math.max(workArea.y, maxY)));
  return b;
}

/** 默认位置：主屏工作区底部居中，留 80px 让开任务栏 */
function defaultBoundsIn(workArea, bounds) {
  const wa = workArea || { x: 0, y: 0, width: 1920, height: 1080 };
  const width = Number(bounds && bounds.width) || DEFAULT_OVERLAY_SETTINGS.bounds.width;
  const height = Number(bounds && bounds.height) || DEFAULT_OVERLAY_SETTINGS.bounds.height;
  return {
    ...(bounds || {}),
    x: Math.round(wa.x + (wa.width - width) / 2),
    y: Math.round(wa.y + wa.height - height - 80),
  };
}

/**
 * 由绝对位置算出相对锚点
 * 找不到对应显示器时返回全 null，调用方据此判断「这个锚点不可用」。
 */
function computeAnchor(bounds, display) {
  const b = bounds || {};
  const wa = displayWorkArea(display);
  const empty = { displayId: null, relX: null, relY: null };
  if (!wa || !wa.width || !wa.height) return empty;
  if (!isFiniteNumber(b.x) || !isFiniteNumber(b.y)) return empty;
  const relX = (b.x - wa.x) / wa.width;
  const relY = (b.y - wa.y) / wa.height;
  const round = (v) => Number(Math.min(1, Math.max(0, v)).toFixed(6));
  return {
    displayId: isFiniteNumber(display && display.id) ? display.id : null,
    relX: round(relX),
    relY: round(relY),
  };
}

/**
 * 由相对锚点还原绝对位置
 *
 * 优先找回原来的那块屏（按 displayId 匹配）；原屏不在了（拔掉了）就退到主屏，
 * 按同样比例摆放 —— 用户对「右半边、靠下」这种相对关系有记忆，比直接拍到底部居中更符合预期。
 */
function boundsFromAnchor(anchor, displays, primaryWorkArea) {
  const a = anchor || {};
  if (!isFiniteNumber(a.relX) || !isFiniteNumber(a.relY)) return null;
  const list = normalizeDisplays(displays);
  let wa = null;
  if (isFiniteNumber(a.displayId)) {
    const hit = list.find((d) => isFiniteNumber(d.id) && d.id === a.displayId);
    if (hit) wa = hit.workArea;
  }
  if (!wa) {
    wa = primaryWorkArea || (list[0] ? list[0].workArea : null);
  }
  if (!wa || !wa.width || !wa.height) return null;
  return {
    x: Math.round(wa.x + a.relX * wa.width),
    y: Math.round(wa.y + a.relY * wa.height),
  };
}

/**
 * 修正落盘的窗口位置
 *
 * 优先级：
 *   1. 位置在某块屏上 → 原样保留（用户自己摆的位置不能动）
 *   2. 位置失效（外接屏拔了 / 分辨率变了 / 拖出屏幕）→ 按相对锚点搬到还存在的屏
 *   3. 连锚点都没有（首次运行）→ 主屏底部居中
 *
 * 窗口是鼠标穿透的，跑到屏幕外用户既点不到也拖不回来，只能手工改配置文件 ——
 * 这三档必须自愈，不能把「不可见」这个状态留给用户。
 */
function resolveBoundsForDisplay(bounds, workArea, displays, anchor) {
  const b = { ...(bounds || {}) };
  const list = normalizeDisplays(displays);
  const onScreen = list.length ? isOnSomeDisplay(b, list) : isOnDisplay(b, workArea);
  if (onScreen) return b;

  const fromAnchor = boundsFromAnchor(anchor, list, workArea);
  if (fromAnchor) {
    const placed = clampIntoWorkArea({ ...b, x: fromAnchor.x, y: fromAnchor.y }, workArea);
    const ok = list.length ? isOnSomeDisplay(placed, list) : isOnDisplay(placed, workArea);
    if (ok) return placed;
    // 锚点还原后仍在屏外（例如显示器尺寸缩到比窗口还小）→ 落到最后兜底
  }

  return defaultBoundsIn(workArea, b);
}

// ============ 原子写 + 跨进程锁 ============

/**
 * 同步 sleep：整个写入路径是同步的，不能为等锁把主进程让出去
 * （让出去就有可能在「拿到锁之前」被别的事件插进来改设置，锁就没意义了）
 */
function sleepSync(ms) {
  if (!(ms > 0)) return;
  try {
    const buf = new Int32Array(new SharedArrayBuffer(4));
    Atomics.wait(buf, 0, 0, ms);
  } catch (_) {
    /* Atomics.wait 在极少数环境不可用：退化为忙等，代价可接受 */
    const end = Date.now() + ms;
    while (Date.now() < end) { /* spin */ }
  }
}

/**
 * 申请写锁（best-effort）
 *
 * 需要的理由：单实例锁只挡住本应用的第二份进程；用户手工编辑 overlay.json、
 * 或另一个 `--user-data-dir` 的实例、或杀软扫描临时持有文件句柄，都会撞上。
 * 锁不是正确性的最后防线（原子写才是），但能把「读到别人写一半的内容」
 * 这种偶发问题压下去。
 *
 * 任何异常都返回 false —— 拿不到锁就让调用方照常写，绝不能因为锁把设置读挂。
 */
function acquireLock(fs, lockPath, opts) {
  const staleMs = (opts && opts.staleMs) || 5000;
  const retries = (opts && opts.retries) || 4;
  const waitMs = (opts && opts.waitMs) || 25;
  for (let i = 0; i < retries; i++) {
    try {
      const fd = fs.openSync(lockPath, 'wx');
      try {
        fs.writeSync(fd, String(process.pid));
      } finally {
        fs.closeSync(fd);
      }
      return true;
    } catch (err) {
      if (!err || err.code !== 'EEXIST') return false;
      // 锁文件存在：可能是上个进程崩在写操作里没删掉，超过 staleMs 视为陈旧
      try {
        const st = fs.statSync(lockPath);
        if (st && Date.now() - st.mtimeMs > staleMs) {
          try { fs.unlinkSync(lockPath); } catch (_) { /* 别人先删了 */ }
          continue;
        }
      } catch (_) { /* statSync 失败（文件刚被删）→ 直接重试 */ }
      sleepSync(waitMs);
    }
  }
  return false;
}

function releaseLock(fs, lockPath) {
  try {
    fs.unlinkSync(lockPath);
    return true;
  } catch (_) {
    return false;
  }
}

/**
 * 原子写：加锁 → 临时文件 → 自校验 → rename 覆盖
 *
 * 直接 writeFileSync 覆盖原文件时，若进程在写一半被杀（关机、崩溃、任务管理器
 * 结束进程），用户留下的是一个被截断的 JSON —— 下次启动解析失败，所有悬浮窗
 * 设置归零。rename 在同一分区上是原子的，要么旧文件、要么新文件，不存在中间态。
 */
function writeConfigAtomic(fs, filePath, settings, opts) {
  const text = JSON.stringify(settings, null, 2);
  JSON.parse(text); // 自校验：序列化结果必须是合法 JSON 才允许落盘
  const tmpPath = `${filePath}.tmp`;
  const lockPath = `${filePath}.lock`;
  const locked = acquireLock(fs, lockPath, opts);
  try {
    fs.writeFileSync(tmpPath, text, 'utf8');
    fs.renameSync(tmpPath, filePath);
  } finally {
    if (locked) releaseLock(fs, lockPath);
    // 失败路径上把 .tmp 也清掉，避免下次启动看到一堆残留文件
    try {
      if (fs.existsSync(tmpPath)) fs.unlinkSync(tmpPath);
    } catch (_) { /* 清不掉不影响正确性 */ }
  }
  return settings;
}

/**
 * 读取配置（永不抛异常）
 *
 * @returns {{ settings: object, reason: 'ok'|'absent'|'corrupt', migrated: boolean, error?: string }}
 *   reason='corrupt' 时返回出厂默认值并附上原因，调用方负责写警告日志 ——
 *   决不能让一个坏掉的配置文件把应用卡在启动阶段。
 */
function readConfig(fs, filePath) {
  try {
    if (!fs.existsSync(filePath)) {
      return { settings: defaultSettings(), reason: 'absent', migrated: false };
    }
    const text = fs.readFileSync(filePath, 'utf8');
    const raw = JSON.parse(text);
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
      return { settings: defaultSettings(), reason: 'corrupt', migrated: false, error: '顶层不是对象' };
    }
    const version = Number.isInteger(raw.schemaVersion) ? raw.schemaVersion : 1;
    return {
      settings: normalizeOverlaySettings(raw),
      reason: 'ok',
      migrated: version < SCHEMA_VERSION,
    };
  } catch (err) {
    return {
      settings: defaultSettings(),
      reason: 'corrupt',
      migrated: false,
      error: err && err.message ? err.message : String(err),
    };
  }
}

module.exports = {
  SCHEMA_VERSION,
  DEFAULT_OVERLAY_SETTINGS,
  clampNumber,
  intOrNull,
  isFiniteNumber,
  boolOrTrue,
  migrateConfig,
  normalizeOverlaySettings,
  defaultSettings,
  sameBounds,
  displayWorkArea,
  normalizeDisplays,
  isOnDisplay,
  isOnSomeDisplay,
  clampIntoWorkArea,
  defaultBoundsIn,
  computeAnchor,
  boundsFromAnchor,
  resolveBoundsForDisplay,
  acquireLock,
  releaseLock,
  writeConfigAtomic,
  readConfig,
};
