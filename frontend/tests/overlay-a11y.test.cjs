/**
 * 悬浮窗控制面板「无障碍语义」源码守卫（Backlog B2）
 *
 * 面板里的开关与滑杆，是**视觉上自解释、语义上完全空白**的一类控件：
 *   - 开关只是一个 `<button>` —— 读屏念「按钮」，既不知道它是开关，
 *     也不知道当前是开还是关（开/关只体现在背景色和滑块位置上）
 *   - 滑杆只是一个原生 range —— 没有名字，单位（px / 行 / %）在读屏里会丢
 *   - 图标按钮（关闭、6 个色块）连一个字都没有
 * 屏幕上看得见的东西（文字标签、高亮边框、滑块位置）读屏一个都拿不到。
 *
 * B2 的修法本身很简单，难点在**守住**：这批属性属于「肉眼看不出来少了」的那类
 * —— 漏了不报错、不崩溃、界面上毫无变化，只会在无障碍体验上悄悄退化。
 * 所以这里做两层：
 *   1. 结构层：扫 OverlayControlPanel.tsx 源码，逐个控件核对属性是否齐备
 *   2. 变异层：把源码**人为改坏**再跑一次 audit，确认守卫真的会失败
 *
 * 第 2 层是本文件存在的主要理由：一个永远返回「通过」的守卫比没有守卫更糟，
 * 它会让所有人以为这件事已经有人看着了。所以每条规则都配一个「改坏它」用例。
 *
 * 为什么扫源码而不是测渲染后的 DOM：这个面板要 Electron + IPC + zustand 才渲染得
 * 起来（组件内 JSX 跑不了 node --test，见 tests/ 里其它文件的同类说明），
 * 目前唯一可行的自动化位置就是源码。等 M3 的首启向导落地后，可以另加一轮
 * CDP 无障碍树冒烟，但源码守卫仍然要留 —— 它拦在提交时，成本最低。
 *
 * 附：`main.cjs` 与 `overlay-actions.cjs` 的两层分工见 overlay-actions.test.cjs，
 * 那份守的是「多入口行为一致」，这份守的是「控件语义齐备」，用途不同。
 *
 * 运行：npm run test
 */

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const PANEL = path.join(__dirname, '..', 'src', 'components', 'OverlayControlPanel.tsx');
const SRC = fs.readFileSync(PANEL, 'utf8');

/**
 * 去掉注释后的源码 —— 所有扫描都在这份文本上跑。
 *
 * 理由同 overlay-actions.test.cjs：说明性注释里会**引用**被禁止的写法
 * （本项目已经踩过一次假失败的坑）。这里更密集，例如
 * 「不写 `aria-valuetext={`${value}${suffix}`}` 这种第二份表达式」这句注释，
 * 不剥掉就会被当成一份真的 aria-valuetext 声明。
 * JSX 注释 `{/* … *\/}` 剥完后剩一对空花括号，对扫描无影响。
 */
const CODE = SRC
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '');

/** 可访问名里不允许出现的实现术语：读屏文案要和面板文案同口径，不暴露内部机制 */
const FORBIDDEN_TERMS = /token|Token|预算|截断|initial_prompt/;

function lineOf(src, idx) {
  return src.slice(0, idx).split('\n').length;
}

/**
 * 从 `start` 处的 `<tag` 起，找到标签头的结束 `>`。
 *
 * 需要跳过 `{}` 表达式（里面会有 `=>`、`? :`）与引号内的内容，
 * 否则 `onClick={() => …}` 里的箭头会被当成标签结束。
 */
function tagHeaderEnd(src, start) {
  let brace = 0;
  let quote = null;
  for (let i = start; i < src.length; i++) {
    const ch = src[i];
    if (quote) {
      if (ch === '\\') { i++; continue; }
      if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === '`') { quote = ch; continue; }
    if (ch === '{') brace++;
    else if (ch === '}') brace--;
    else if (ch === '>' && brace === 0) return i;
  }
  return -1;
}

/**
 * 扫描某个标签的所有**元素**（含子节点文本），返回标签头、内部文本与行号。
 *
 * 只做够用的解析：面板里的控件都是扁平的（没有 button 套 button），
 * 不值得为此引一个 JSX 解析器。扫不到任何元素时 audit 会报「扫描逻辑可能已失效」，
 * 所以这里解析不精确也不会静默放过。
 */
function scanElements(src, tag) {
  const out = [];
  const openRe = new RegExp(`<${tag}[\\s>]`, 'g');
  let m;
  while ((m = openRe.exec(src)) !== null) {
    const start = m.index;
    const headEnd = tagHeaderEnd(src, start);
    if (headEnd < 0) throw new Error(`L${lineOf(src, start)} 的 <${tag}> 找不到结束符`);
    const header = src.slice(start, headEnd + 1);
    const selfClosing = /\/\s*>$/.test(header);

    let end = headEnd + 1;
    let inner = '';
    if (!selfClosing) {
      const close = `</${tag}>`;
      const closeIdx = src.indexOf(close, end);
      if (closeIdx < 0) throw new Error(`L${lineOf(src, start)} 的 <${tag}> 未闭合`);
      inner = src.slice(end, closeIdx);
      end = closeIdx + close.length;
    }
    out.push({ line: lineOf(src, start), start, header, inner, selfClosing });
    openRe.lastIndex = end; // 跳过子节点，不让同名的子标签重复计入
  }
  return out;
}

/** 元素内部「肉眼能看到的文字」（剥掉子标签与表达式） */
function visibleText(inner) {
  return inner.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
}

/** 解析 `A11Y_ID` 常量表：key -> id 串 */
function parseIdMap(src) {
  const map = new Map();
  const block = /const A11Y_ID\s*=\s*\{([\s\S]*?)\}/.exec(src);
  if (!block) return map;
  for (const m of block[1].matchAll(/(\w+)\s*:\s*'([^']+)'/g)) map.set(m[1], m[2]);
  return map;
}

/** 把一个 aria 引用值（`"id"` 或 `{A11Y_ID.x}`）解析成真正的 id 串 */
function resolveIdRef(raw, idMap) {
  const t = raw.trim();
  const lit = /^"([^"]*)"$/.exec(t) || /^'([^']*)'$/.exec(t);
  if (lit) return lit[1];
  const expr = /^\{A11Y_ID\.(\w+)\}$/.exec(t);
  if (expr) return idMap.get(expr[1]) || null;
  return null;
}

/**
 * 找到某个 id 挂在哪个标签上，返回该标签内的可见文案。
 * 返回 null 表示这个 id 根本没被定义（悬空引用）。
 */
function labelTextOfId(src, id, idMap) {
  const re = /\bid=("[^"]*"|\{[^}]*\})/g;
  let m;
  while ((m = re.exec(src)) !== null) {
    if (resolveIdRef(m[1], idMap) !== id) continue;
    const gt = src.indexOf('>', m.index);
    if (gt < 0) return '';
    const rest = src.slice(gt + 1);
    const stop = rest.search(/[<{]/);
    return (stop < 0 ? rest : rest.slice(0, stop)).trim();
  }
  return null;
}

/**
 * 提取元素的可访问名来源。
 *
 * 分类很重要：`aria-label={label}`（表达式，指向屏幕上那句话）是**对的**，
 * `aria-label="字号"`（字面量，把屏幕上那句话又抄了一遍）是**错的**。
 * 字面量本身不是罪 —— 「关闭控制面板」这种纯图标按钮没有可见文案可引用，
 * 字面量就是唯一来源；问题只在「同一句话存了两份」。
 */
function nameOf(header) {
  const by = /\baria-labelledby=("[^"]*"|\{[^}]*\})/.exec(header);
  if (by) return { kind: 'labelledby', text: by[1] };

  const expr = /\baria-label=\{([^}]*)\}/.exec(header);
  if (expr) return { kind: 'expr', text: expr[1].trim() };

  const lit = /\baria-label="([^"]*)"/.exec(header);
  if (lit) return { kind: 'literal', text: lit[1] };

  const tpl = /\baria-label=`([^`]*)`/.exec(header);
  if (tpl) return { kind: 'template', text: tpl[1] };

  return null;
}

/**
 * 取出某个顶层函数的函数体（用于校验 SliderRow 内部的可访问名来源）。
 *
 * 不能「找到 `function X(` 后取第一个 `{`」—— SliderRow 的参数是解构写法
 * `function SliderRow({ icon, label, … }: { … }) {`，第一个 `{` 是参数解构，
 * 会在它闭合时提前收尾，函数体整个取不到（这个坑在本文件第一版里踩过一次，
 * 表现是「守卫报了三处 SliderRow 有问题」而代码其实是对的）。
 * 正确做法是先跳过参数表（含类型注解里的 `onChange: (v: number) => void`），
 * 再取紧随其后的 `{`。
 */
function sliceFunction(src, name) {
  const start = src.indexOf(`function ${name}(`);
  if (start < 0) return null;

  let i = src.indexOf('(', start);
  let paren = 0;
  for (; i < src.length; i++) {
    if (src[i] === '(') paren++;
    else if (src[i] === ')') {
      paren--;
      if (paren === 0) { i++; break; }
    }
  }

  const open = src.indexOf('{', i);
  if (open < 0) return null;
  let depth = 0;
  for (let j = open; j < src.length; j++) {
    if (src[j] === '{') depth++;
    else if (src[j] === '}') {
      depth--;
      if (depth === 0) return src.slice(start, j + 1);
    }
  }
  return null;
}

/**
 * 核心：审计一份源码，返回「问题清单」（空数组 = 全过）。
 *
 * 返回清单而不是直接 assert，是为了让变异用例能复用它 ——
 * 「把源码改坏后 audit 必须报出问题」这件事本身是可测的。
 */
function audit(src) {
  const problems = [];
  const idMap = parseIdMap(src);

  // ===== 1. aria-labelledby 的引用完整性 =====
  // 悬空引用是最隐蔽的失败：属性写在那里，读屏却读不出名字，界面上毫无异常。
  for (const m of src.matchAll(/\baria-labelledby=("[^"]*"|\{[^}]*\})/g)) {
    const line = lineOf(src, m.index);
    const id = resolveIdRef(m[1], idMap);
    if (!id) {
      problems.push(`L${line} aria-labelledby 的值 ${m[1]} 无法静态解析（只支持字面量或 A11Y_ID.xxx）`);
      continue;
    }
    const text = labelTextOfId(src, id, idMap);
    if (text === null) {
      problems.push(`L${line} aria-labelledby 指向的 id「${id}」在本文件里没有定义 → 悬空引用，可访问名会是空的`);
    } else if (!/[\u4e00-\u9fa5A-Za-z0-9]/.test(text)) {
      problems.push(`L${line} aria-labelledby 指向的标签「${id}」没有可见文案 → 可访问名会是空的`);
    }
  }

  const buttons = scanElements(src, 'button');
  const inputs = scanElements(src, 'input');
  const switches = buttons.filter((b) => /\brole="switch"/.test(b.header));
  // 四个滑杆共用 SliderRow 里的同一个 <input type="range">：
  // 「滑杆个数」要数调用点，属性齐备性则只需校验那一个模板。
  const ranges = inputs.filter((i) => /\btype="range"/.test(i.header));
  const sliderCalls = (src.match(/<SliderRow\b/g) || []).length;

  // ===== 2. 扫描自检 =====
  // 数量对不上说明扫描逻辑坏了（比漏检更危险：会安静地全票通过）
  if (buttons.length < 8) {
    problems.push(`只扫到 ${buttons.length} 个 <button>（应 ≥ 8），扫描逻辑可能已失效`);
  }
  if (switches.length < 3) {
    problems.push(`只扫到 ${switches.length} 个 role="switch"（应 ≥ 3）：有开关丢了 role，读屏只会念「按钮」`);
  }
  if (sliderCalls < 4) {
    problems.push(`只扫到 ${sliderCalls} 处 <SliderRow>（应 ≥ 4），扫描逻辑可能已失效`);
  }
  if (ranges.length < 1) {
    problems.push('找不到 <input type="range">，滑杆属性无从校验，扫描逻辑可能已失效');
  }

  // ===== 3. 开关：role + 状态 + 可访问名 =====
  for (const sw of switches) {
    if (!/\baria-checked=/.test(sw.header)) {
      problems.push(`L${sw.line} 开关缺 aria-checked：读屏说不出当前是开还是关`);
    }
    const name = nameOf(sw.header);
    if (!name) {
      problems.push(`L${sw.line} 开关没有可访问名：读屏只会念「按钮」`);
    } else if (name.kind === 'literal' || name.kind === 'template') {
      problems.push(
        `L${sw.line} 开关的可访问名是硬编码文案（${name.text}）：` +
        '可见标签已经写着同一句话，应改用 aria-labelledby 指向它 —— 存两份必然漂移',
      );
    }
  }

  // ===== 4. 滑杆：role + 值域 + 单位 + 可访问名 =====
  for (const sl of ranges) {
    for (const attr of ['aria-valuemin', 'aria-valuemax', 'aria-valuenow', 'aria-valuetext']) {
      if (!new RegExp(`\\b${attr}=`).test(sl.header)) {
        problems.push(`L${sl.line} 滑杆缺 ${attr}`);
      }
    }
    if (!/\brole="slider"/.test(sl.header)) {
      problems.push(`L${sl.line} 滑杆缺 role="slider"`);
    }
    const name = nameOf(sl.header);
    if (!name) {
      problems.push(`L${sl.line} 滑杆没有可访问名：读屏念不出这是「字号」还是「整体透明度」`);
    } else if (name.kind === 'literal' || name.kind === 'template') {
      problems.push(
        `L${sl.line} 滑杆的可访问名是硬编码文案（${name.text}）：` +
        'label 变量已经渲染在屏幕上了，直接复用它',
      );
    }
  }

  // ===== 5. 滑杆的可访问名必须与可见文案同源 =====
  const sliderFn = sliceFunction(src, 'SliderRow');
  if (!sliderFn) {
    problems.push('找不到 SliderRow 函数，守卫无法校验滑杆');
  } else {
    if (!/aria-label=\{label\}/.test(sliderFn)) {
      problems.push('SliderRow 的 aria-label 不再是 {label}：可访问名应与屏幕上的 label 同源');
    }
    if (!/<span>\{label\}<\/span>/.test(sliderFn)) {
      problems.push('SliderRow 里找不到可见的 <span>{label}</span>：可见文案与可访问名可能已解耦');
    }
    if (!/const valueText = `\$\{value\}\$\{suffix\}`/.test(sliderFn)) {
      problems.push('SliderRow 的 valueText 不再是「value + suffix」：aria-valuetext 可能引入了实现术语或第二份格式表达式');
    }
  }

  // ===== 6. 每个按钮都要有可访问名 =====
  // 纯图标按钮（关闭、色块）是最容易漏的一类：视觉上「一眼就懂」，读屏上是空的。
  for (const b of buttons) {
    if (!nameOf(b.header) && visibleText(b.inner) === '') {
      problems.push(`L${b.line} 按钮既没有可见文字也没有可访问名：读屏只会念「按钮」`);
    }
  }

  // ===== 7. 每个输入控件都要有可访问名 =====
  for (const i of inputs) {
    if (!nameOf(i.header)) {
      problems.push(`L${i.line} 输入控件没有可访问名`);
    }
  }

  // ===== 8. aria 文案不得出现实现术语 =====
  // 风险点 2：aria-valuetext 是从 suffix 拼出来的，所以两处都要扫。
  for (const m of src.matchAll(/\baria-[a-z]+=("[^"]*"|\{[^}]*\}|`[^`]*`)/g)) {
    if (FORBIDDEN_TERMS.test(m[1])) {
      problems.push(`L${lineOf(src, m.index)} aria 属性里出现了实现术语（${m[1]}）：读屏文案要和面板文案同口径，不暴露内部机制`);
    }
  }
  for (const m of src.matchAll(/\bsuffix=("[^"]*"|\{[^}]*\})/g)) {
    if (FORBIDDEN_TERMS.test(m[1])) {
      problems.push(`L${lineOf(src, m.index)} 滑杆的 suffix 里出现了实现术语（${m[1]}）：它会被拼进 aria-valuetext 念给用户听`);
    }
  }

  return problems;
}

/** 人为改坏源码，返回改动后的审计结果 —— 用来证明守卫不是空转 */
function auditMutated(from, to) {
  assert.ok(CODE.includes(from), `变异失败：源码里找不到 ${from}，请更新本用例`);
  const broken = CODE.replace(from, to);
  assert.notEqual(broken, CODE, '变异失败：替换后源码没有变化');
  return audit(broken);
}

/** 断言「问题清单里出现了某类问题」 */
function expectDetected(problems, needle, label) {
  assert.ok(
    problems.some((p) => p.includes(needle)),
    `${label}\n守卫没有报出含「${needle}」的问题，实际报出：\n${JSON.stringify(problems, null, 2)}`,
  );
}

// ============ 当前源码 ============

test('无障碍语义检查 · 当前源码全部通过', () => {
  assert.deepEqual(
    audit(CODE),
    [],
    'OverlayControlPanel.tsx 的无障碍语义有问题（见上方清单）—— ' +
    '多数是「屏幕上看不出来」的那种，改完记得跑 npm run test 复验',
  );
});

test('面板里的开关/滑杆/按钮数量与预期一致（扫描逻辑没退化）', () => {
  const buttons = scanElements(CODE, 'button');
  const switches = buttons.filter((b) => /\brole="switch"/.test(b.header));
  const ranges = scanElements(CODE, 'input').filter((i) => /\btype="range"/.test(i.header));
  const sliderCalls = (CODE.match(/<SliderRow\b/g) || []).length;

  assert.equal(switches.length, 3, '开关：显示悬浮窗 / 后台持续更新字幕 / 术语提示词自动防循环降级');
  assert.equal(sliderCalls, 4, '滑杆：字号 / 显示行数 / 当前句底色 / 整体透明度');
  assert.equal(ranges.length, 1, '四个滑杆共用 SliderRow 里的同一个 <input type="range">');
  assert.equal(buttons.length, 8, '按钮：关闭 + 3 开关 + 1 色板模板 + 调整模式 / 重置位置 / 导出日志');
});

// ============ 变异验证：证明守卫真的会失败 ============

test('变异 · 滑杆丢掉 aria-label 必须被检出', () => {
  const problems = auditMutated('aria-label={label}', '');
  expectDetected(problems, '滑杆没有可访问名', '滑杆可访问名规则');
  expectDetected(problems, 'aria-label 不再是 {label}', '可访问名同源规则');
});

test('变异 · 滑杆用「再抄一份中文文案」的方式糊过去，必须被检出', () => {
  // 这是最可能出现的错误修法：属性是补上了，但文案从此有两份
  const problems = auditMutated('aria-label={label}', 'aria-label="字号"');
  expectDetected(problems, '滑杆的可访问名是硬编码文案', '禁止第二份文案的规则');
});

test('变异 · 滑杆丢掉 aria-valuetext 必须被检出', () => {
  const problems = auditMutated('aria-valuetext={valueText}', '');
  expectDetected(problems, '滑杆缺 aria-valuetext', '滑杆单位规则');
});

test('变异 · 某个开关丢掉 role="switch" 必须被检出', () => {
  // 只删一处：count 检查 + 该元素不再进入开关校验，两条路都该有反应
  const problems = auditMutated('role="switch"', '');
  expectDetected(problems, '只扫到 2 个 role="switch"', '开关 role 规则');
});

test('变异 · 开关丢掉 aria-checked 必须被检出', () => {
  const problems = auditMutated('aria-checked={open}', '');
  expectDetected(problems, '开关缺 aria-checked', '开关状态规则');
});

test('变异 · aria-labelledby 指向不存在的 id 必须被检出', () => {
  // 悬空引用：属性在、名字空，界面上完全看不出来
  const problems = auditMutated('aria-labelledby={A11Y_ID.showOverlay}', 'aria-labelledby="ov-label-not-exist"');
  expectDetected(problems, '悬空引用', '引用完整性规则');
});

test('变异 · 可见标签被删掉（只剩 labelledby 引用）必须被检出', () => {
  const problems = auditMutated('id={A11Y_ID.showOverlay}', '');
  expectDetected(problems, '没有定义', '引用完整性规则');
});

test('变异 · 色块丢掉可访问名必须被检出', () => {
  // 色块是纯视觉控件，丢了名字读屏只会念一串「按钮」
  const problems = auditMutated('aria-label={c.name}', '');
  expectDetected(problems, '既没有可见文字也没有可访问名', '按钮可访问名规则');
});

test('变异 · 图标按钮（关闭）丢掉可访问名必须被检出', () => {
  const problems = auditMutated('aria-label="关闭控制面板"', '');
  expectDetected(problems, '既没有可见文字也没有可访问名', '按钮可访问名规则');
});

test('变异 · aria-valuetext 的文案里混进实现术语必须被检出', () => {
  // 风险点 2：读屏文案不能出现 token / 预算 这类内部说法
  const problems = auditMutated('suffix=" px"', 'suffix=" token"');
  expectDetected(problems, 'suffix 里出现了实现术语', '术语口径规则');
});
