/**
 * 课程配置页提示条的守卫（V6-1）
 *
 * ## 这份测试盯的是三类**用户无法自救**的错误
 *
 * 1. **文案教了一个不存在的默认值。**
 *    提示条说「保持默认『自动检测』」，而页面的 `useState` 默认值其实是别的 ——
 *    用户照着做却得到相反的结果，且没有任何报错。与「向导教了一个不存在的
 *    快捷键」同类（`onboarding-copy.test.mts` 已为该类事故立过守卫）。
 *    → 所以默认值只定义一次（`COURSE_CONFIG_DEFAULTS`），页面与文案共用，
 *      并用源守卫把页面上**实际写的**默认值逐字对拍。
 *
 * 2. **文案里出现实现术语 / 含糊说法。**
 *    禁词表与审计实现在 `_copy-audit.mts`（与向导守卫**同一份**，
 *    避免两处禁词表各自漂移成假守卫）。
 *
 * 3. **同一句话存两份。**
 *    组件里再写一遍中文 = 改一处必漂移（本项目复发性缺陷）。有反向断言钉住。
 *
 * 另外把「关闭状态」的纯函数全边界覆盖：读不到存储、读写抛异常都不能把
 * 页面搞崩，也不能让提示反复冒出来。
 *
 * 运行：npm run test（.mts 必须走 npm test，系统 node 18 没有类型擦除）
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  COURSE_CONFIG_DEFAULTS,
  DEFAULT_LABELS,
  CONFIG_HINT_COPY,
  HINT_DISMISS_KEY,
  isHintDismissed,
  dismissHint,
  type HintStorage,
} from '../src/lib/courseConfigCopy.ts';

import { auditCopy, type CopyTree } from './_copy-audit.mts';

const HERE = dirname(fileURLToPath(import.meta.url));

const PAGE_SRC = readFileSync(join(HERE, '..', 'src', 'pages', 'CourseConfig.tsx'), 'utf8');
const HINT_SRC = readFileSync(
  join(HERE, '..', 'src', 'components', 'CourseConfigHint.tsx'),
  'utf8',
);

/**
 * 剥掉块注释后再扫源码
 *
 * 必须剥：组件与页面的注释里会**引用**被禁止的写法（例如解释「为什么不用
 * `role="status"`」），不剥就会因为说明性注释而永远失败，然后被「顺手」放宽成
 * 一条更弱的断言 —— 那才是真正的失败（`panel-copy.test.mts` 已踩过这个坑）。
 *
 * 只剥块注释、不剥 `//` 行注释：源码里有 URL（`http://…`），按 `//` 切会把
 * 后半句当成注释吃掉。本项目的说明性注释一律用块注释或整行 `//`。
 */
function stripBlockComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, '');
}

/** 内存版存储：单测不需要 jsdom，也顺便能构造「读写抛异常」的存储 */
function memStorage(init: Record<string, string> = {}): HintStorage & { data: Record<string, string> } {
  const data = { ...init };
  return {
    data,
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => {
      data[k] = v;
    },
  };
}

/** 每次访问都抛的存储（隐私模式 / 存储被禁用） */
const throwingStorage: HintStorage = {
  getItem() {
    throw new Error('SecurityError');
  },
  setItem() {
    throw new Error('SecurityError');
  },
};

// ---------------------------------------------------------------- 文案红线

test('提示条文案 · 无实现术语、无含糊说法', () => {
  assert.deepEqual(auditCopy(CONFIG_HINT_COPY as unknown as CopyTree), []);
});

test('三项齐全，且每项都同时给出「作用」与「推荐值」', () => {
  const items = CONFIG_HINT_COPY.items;
  assert.equal(items.length, 3, '提示条应覆盖三项配置（语言 / 领域 / 术语）');
  assert.deepEqual(items.map((i) => i.label), ['识别语言', '课程领域', '自定义术语']);
  for (const item of items) {
    assert.ok(item.what.trim().length > 0, `${item.label} 缺「作用」说明`);
    assert.ok(item.recommend.trim().length > 0, `${item.label} 缺「推荐值」说明`);
    // 两句承担不同信息：一句讲「它是什么」，一句讲「怎么填」。
    // 写成同一句就等于只说了其中一半，用户仍然不知道该选什么。
    assert.notEqual(item.what, item.recommend, `${item.label} 的「作用」与「推荐值」不能是同一句`);
  }
  // 每个 label 唯一：组件用它当 React key
  assert.equal(new Set(items.map((i) => i.label)).size, items.length, 'label 必须唯一（被当作 key）');
});

test('无障碍名与关闭按钮文案都存在且互不相同', () => {
  assert.ok(CONFIG_HINT_COPY.ariaLabel.trim().length > 0);
  assert.ok(CONFIG_HINT_COPY.dismissLabel.trim().length > 0);
  assert.ok(CONFIG_HINT_COPY.title.trim().length > 0);
  assert.ok(CONFIG_HINT_COPY.footNote.trim().length > 0);
  assert.notEqual(CONFIG_HINT_COPY.ariaLabel, CONFIG_HINT_COPY.title);
});

// ------------------------------------------------ 推荐值 ⇄ 页面默认值 对拍

test('页面默认值与 COURSE_CONFIG_DEFAULTS 逐字一致（源守卫）', () => {
  const grab = (field: string): string => {
    const m = new RegExp(`useState\\(\\s*course\\?\\.${field}\\s*\\|\\|\\s*COURSE_CONFIG_DEFAULTS\\.(\\w+)\\s*\\)`).exec(PAGE_SRC);
    assert.ok(m, `未找到 ${field} 的默认值（应写成 COURSE_CONFIG_DEFAULTS.xxx，不能是字面量）`);
    return m[1];
  };
  assert.equal(grab('domain'), 'domain');
  assert.equal(grab('language'), 'language');
  assert.equal(grab('simpMode'), 'simpMode');

  // 反向：**任何** `course.<字段> || '<字面量>'` 都是第二份事实。
  // 这条必须覆盖到「同步 effect」那几行（课程记录字段缺失时写回默认值的地方）——
  // 第一版守卫只查了 useState，实测漏掉了 effect 里的两处，是它自己抓出来的。
  const strayFallbacks = [
    ...PAGE_SRC.matchAll(/course\??\.(domain|language|simpMode)\s*\|\|\s*'([^']*)'/g),
  ].map((m) => `course.${m[1]} || '${m[2]}'`);
  assert.deepEqual(
    strayFallbacks,
    [],
    '默认值又写回字面量了 —— 必须统一走 COURSE_CONFIG_DEFAULTS（否则与提示条的推荐值成为两份事实）',
  );
});

test('提示条说的项名，必须与页面上的控件标签逐字一致（源守卫）', () => {
  // 提示条里叫「识别语言」、页面上写着「语言」—— 用户会怀疑说的是不是同一项。
  // 这里要求三项的项名都能在页面源码里原样找到（即真的用了同一个说法）。
  for (const item of CONFIG_HINT_COPY.items) {
    assert.ok(
      PAGE_SRC.includes(item.label),
      `页面上找不到「${item.label}」这个说法 —— 提示条与控件标签必须同一个说法`,
    );
  }
});

test('推荐值的显示名与页面下拉框标签一致（源守卫）', () => {
  // 值与标签的映射表在下拉框里，提示条只引用标签；改标签漏改文案会让提示条说错话。
  const lang = /value:\s*'auto',\s*label:\s*'([^']+)'/.exec(PAGE_SRC);
  assert.ok(lang, "LANGUAGES 里未找到 value: 'auto' 的选项");
  assert.equal(lang[1], DEFAULT_LABELS.language, 'LANGUAGES 的「auto」标签与提示条写的不一致');

  const domainOpt = /const DOMAINS = \[([^\]]*)\]/.exec(PAGE_SRC);
  assert.ok(domainOpt, '未找到 DOMAINS');
  assert.ok(
    domainOpt[1].includes(`'${DEFAULT_LABELS.domain}'`),
    `DOMAINS 里没有「${DEFAULT_LABELS.domain}」—— 提示条推荐了一个不存在的领域选项`,
  );
});

test('字形模式的取值必须与后端常量逐字一致（不一致会被静默收敛）', () => {
  // CourseConfig.tsx 的 SIMP_MODES 上方就写着这条约束：取值与后端
  // `SIMP_MODE_AUTO_SIMPLIFIED` / `SIMP_MODE_RAW` 不一致时，后端
  // `normalize_simp_mode()` 会**静默**把它收敛成默认值 —— 用户看到的现象是
  // 「开关点了没用」，且没有任何日志。所以这里直接读后端源码对拍。
  const backendSrc = readFileSync(
    join(HERE, '..', '..', 'backend', 'asr', 'whisper_engine.py'),
    'utf8',
  );
  const backendConst = (name: string): string => {
    const m = new RegExp(`^${name}\\s*=\\s*"([^"]+)"`, 'm').exec(backendSrc);
    assert.ok(m, `后端 whisper_engine.py 未找到常量 ${name}`);
    return m[1];
  };

  assert.equal(
    COURSE_CONFIG_DEFAULTS.simpMode,
    backendConst('SIMP_MODE_AUTO_SIMPLIFIED'),
    '前端默认字形模式与后端默认值不是同一个取值',
  );

  const front = [...PAGE_SRC.matchAll(/value:\s*'(auto-simplified|raw)'/g)].map((m) => m[1]);
  const backendAll = [backendConst('SIMP_MODE_AUTO_SIMPLIFIED'), backendConst('SIMP_MODE_RAW')];
  assert.deepEqual(
    [...new Set(front)].sort(),
    [...backendAll].sort(),
    '前端字形开关的取值集合与后端合法集合不一致（会被 normalize_simp_mode 静默收敛）',
  );
});

test('推荐值文案里必须引用 DEFAULT_LABELS，不得把值写死', () => {
  // 构造上就不可能漂移：文案是拼出来的，不是抄出来的。
  const libSrc = readFileSync(join(HERE, '..', 'src', 'lib', 'courseConfigCopy.ts'), 'utf8');
  const langItem = CONFIG_HINT_COPY.items.find((i) => i.label === '识别语言');
  const domainItem = CONFIG_HINT_COPY.items.find((i) => i.label === '课程领域');
  assert.ok(langItem && domainItem);
  assert.ok(langItem.recommend.includes(DEFAULT_LABELS.language), '语言推荐值里应出现默认标签');
  assert.ok(domainItem.recommend.includes(DEFAULT_LABELS.domain), '领域推荐值里应出现默认标签');
  assert.match(libSrc, /DEFAULT_LABELS\.language/, '文案必须由 DEFAULT_LABELS 拼出');
  assert.match(libSrc, /DEFAULT_LABELS\.domain/, '文案必须由 DEFAULT_LABELS 拼出');
});

// ---------------------------------------------------------------- 源守卫

test('提示条必须在课程配置页里真的渲染出来', () => {
  assert.match(PAGE_SRC, /import CourseConfigHint from '\.\.\/components\/CourseConfigHint'/, '必须引入提示条组件');
  assert.match(PAGE_SRC, /<CourseConfigHint\s*\/>/, '必须真的渲染 —— 只 import 不渲染等于没做');
});

test('无障碍：role=note + aria-label + 关闭按钮有可访问名（源守卫）', () => {
  const code = stripBlockComments(HINT_SRC);
  // role 用 note 而不是 status：status 是 live region，每次挂载都会被读屏朗读，
  // 等于把一条静态说明当成动态更新播报
  assert.match(code, /role="note"/, '提示条容器必须带 role="note"');
  assert.ok(!/role="status"/.test(code), '不该用 role="status"（live region 会朗读静态说明）');
  assert.match(
    code,
    /aria-label=\{CONFIG_HINT_COPY\.ariaLabel\}/,
    'role=note 需要可访问名，且必须指向 CONFIG_HINT_COPY',
  );
  assert.match(
    code,
    /aria-label=\{CONFIG_HINT_COPY\.dismissLabel\}/,
    '关闭按钮里只有图标、没有可访问名，必须显式 aria-label',
  );
  assert.match(code, /data-testid="course-config-hint"/, '必须有稳定选择器（冒烟脚本按它断言）');
  // 关闭按钮必须真的接上关闭逻辑，否则只是个装饰
  assert.match(code, /onClick=\{close\}/, '关闭按钮必须调用 close');
  assert.match(code, /dismissHint\(\)/, 'close 必须真的写入关闭标记');
});

test('组件里不得出现第二份文案字面量（同一句话只存一份）', () => {
  // 注意**不含 item.label**：项名刻意与页面控件标签同名（上一条守卫要求一致），
  // 它出现在页面上是「同一个说法」，不是重复的文案。其余每一个字都必须只有一份来源。
  const literals: string[] = [
    CONFIG_HINT_COPY.ariaLabel,
    CONFIG_HINT_COPY.title,
    CONFIG_HINT_COPY.lead,
    CONFIG_HINT_COPY.footNote,
    CONFIG_HINT_COPY.dismissLabel,
    ...CONFIG_HINT_COPY.items.flatMap((i) => [i.what, i.recommend]),
  ];
  for (const src of [PAGE_SRC, HINT_SRC]) {
    for (const lit of literals) {
      assert.ok(!src.includes(lit), `组件里出现了文案字面量：${JSON.stringify(lit)} —— 必须走 CONFIG_HINT_COPY`);
    }
  }
});

// ---------------------------------------------------------------- 关闭状态

test('关闭状态 · 键名稳定（改 key 会让已关闭的用户重新看到提示）', () => {
  assert.equal(HINT_DISMISS_KEY, 'tingkebanlv.courseConfigHint.dismissed');
});

test('关闭状态 · 未关闭 / 已关闭', () => {
  const s = memStorage();
  assert.equal(isHintDismissed(s), false, '全新用户应看到提示');
  assert.equal(dismissHint(s), true, '写入应成功');
  assert.equal(isHintDismissed(s), true, '关掉后不应再出现（刷新 + 重启都读同一个键）');
  assert.equal(s.data[HINT_DISMISS_KEY], '1');
});

test('关闭状态 · 存储缺失或抛异常时按「未关闭」处理，且绝不抛给页面', () => {
  assert.equal(isHintDismissed(null), false, '拿不到存储 → 宁可多提示一次');
  assert.equal(dismissHint(null), false, '写不进要如实返回 false');
  assert.equal(isHintDismissed(throwingStorage), false, '读抛异常不能把页面搞崩');
  assert.equal(dismissHint(throwingStorage), false, '写抛异常不能把页面搞崩');
});

test('关闭状态 · 其它值不算已关闭（只有明确写过的 "1" 才算）', () => {
  assert.equal(isHintDismissed(memStorage({ [HINT_DISMISS_KEY]: '' })), false);
  assert.equal(isHintDismissed(memStorage({ [HINT_DISMISS_KEY]: 'true' })), false);
  assert.equal(isHintDismissed(memStorage({ [HINT_DISMISS_KEY]: '0' })), false);
});

// ---------------------------------------------------------------- 变异验证

test('变异 · 审计能抓住被塞进提示条文案的每一类禁词（守卫失效 = 白守）', () => {
  const cases: [string, string][] = [
    ['实现术语 token', '超出 token 预算'],
    ['实现术语 预算', '按预算分配'],
    ['实现术语 截断', '超出后会被截断'],
    ['实现术语 提示词', '提示词长度受限'],
    ['含糊说法 智能降级', '系统会智能降级'],
  ];
  for (const [label, poison] of cases) {
    const mutated = { ...CONFIG_HINT_COPY, footNote: poison } as unknown as CopyTree;
    const problems = auditCopy(mutated);
    assert.ok(problems.length > 0, `变异「${label}」必须被审计抓住`);
    assert.match(problems.join('\n'), /footNote/, `变异「${label}」要能定位到字段`);
  }
});
