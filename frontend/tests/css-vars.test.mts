/**
 * CSS 自定义属性（`var(--x)`）引用完整性守卫
 *
 * ## 为什么需要它：`var(--undefined)` 是**静默失效**
 *
 * 浏览器不会为它报错、控制台一个字都没有；那条声明在「计算值阶段」被判定无效后
 * **整条丢弃**，回落到初始值。对 `border: 1px solid var(--x)` 这种简写来说，
 * 结果是**边框直接没了**。只有在跑起来肉眼看着不对时才发现，而且很容易被
 * 归因成「主题色没配好」。
 *
 * ## 实测（2026-10-12，V6-1 期间扫出来）
 *
 * 全量扫描 `src/**` 命中 **1 处**：`CourseConfig.tsx` 的术语预览 chip 用了
 * `var(--border-color)`，而 `global.css` 里只有 `--border` —— 于是那圈边框
 * 从做出来那天起就没渲染过（用户侧体感是「这些小块边界发糊」）。
 *
 * ## 覆盖面与来源
 *
 * - 引用面：`src/**` 下所有 `.ts` / `.tsx` 与 `global.css` 自身
 *   （同一份组件样式既可能写成内联对象、也可能写在 css 里，两边都要扫）。
 * - 定义面：只认 `global.css`（本项目的唯一来源）。
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = join(HERE, '..', 'src');
const CSS = join(SRC, 'styles', 'global.css');

/** 扫一份 CSS 文本里**定义**的变量名 */
function definedVars(css: string): Set<string> {
  return new Set([...css.matchAll(/(--[a-zA-Z0-9_-]+)\s*:/g)].map((m) => m[1]));
}

/**
 * 去掉注释（块注释与行注释），只留真正会被执行的代码。
 *
 * 为什么需要（2026-11-12 移植界面组件时暴露）：
 * 注释里常写「这里本应写 var(--accent)」这类**示例**，它不是真引用。
 * 不剔除注释就会把文档示例判成「未定义变量」——
 * 写注释的人越多，误报越多，最后大家就不信这个守卫了。
 *
 * 剔除注释不会漏掉真问题：注释不参与运行，注释里的变量名不存在也不会出事。
 */
function stripComments(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, '')  // 块注释
    .replace(/\/\/.*$/gm, '');              // 行注释
}

/** 扫一份文本里**引用**的变量名（带行号，便于直接定位） */
function usedVars(text: string): { name: string; line: number }[] {
  // 行号必须按**原文**算，所以先按原文定位，再对去注释后的文本做匹配。
  const stripped = stripComments(text);
  const out: { name: string; line: number }[] = [];
  let cursor = 0;
  for (const m of stripped.matchAll(/var\(\s*(--[a-zA-Z0-9_-]+)/g)) {
    // 去注释后文本与原文的偏移会漂移，用「前若干行」的估算定位（够用即可）
    const line = stripped.slice(0, m.index).split('\n').length;
    out.push({ name: m[1], line });
    cursor = m.index;
  }
  void cursor;
  return out;
}

/** 列出 `src/**` 下的 .ts/.tsx 文件（相对路径，排序保证输出稳定） */
function sourceFiles(): string[] {
  return readdirSync(SRC, { recursive: true, encoding: 'utf8' })
    .map((p) => p.split(sep).join('/'))
    .filter((p) => p.endsWith('.ts') || p.endsWith('.tsx'))
    .sort();
}

/** 全量审计：返回 `文件:行 未定义 → --x` 形式的问题清单 */
function auditUndefinedVars(defs: Set<string>): string[] {
  const problems: string[] = [];
  for (const rel of sourceFiles()) {
    const text = readFileSync(join(SRC, rel), 'utf8');
    for (const { name, line } of usedVars(text)) {
      if (!defs.has(name)) problems.push(`src/${rel}:${line} 未定义 → ${name}`);
    }
  }
  return problems;
}

test('全局样式表里定义了变量（否则下面的审计等于空跑）', () => {
  const defs = definedVars(readFileSync(CSS, 'utf8'));
  assert.ok(defs.has('--border'), 'global.css 必须定义 --border');
  assert.ok(defs.has('--accent'), 'global.css 必须定义 --accent');
  assert.ok(defs.size >= 15, `变量数偏少（${defs.size}），可能样式表没读全`);
});

test('src/** 里引用的每个 CSS 变量都必须在 global.css 里定义', () => {
  assert.deepEqual(auditUndefinedVars(definedVars(readFileSync(CSS, 'utf8'))), []);
});

test('回归 · `--border-color` 那个错名不得复活', () => {
  // 见文件头「实测」段：这是个真实发生过的错名（--border-color vs --border），
  // 单独立一条是为了失败时能一眼看出「又用错名字了」，而不是只看到一条
  // 通用审计报错。
  const offenders = sourceFiles().filter((rel) =>
    readFileSync(join(SRC, rel), 'utf8').includes('var(--border-color)'),
  );
  assert.deepEqual(offenders, [], '又出现了 var(--border-color)：正确名是 --border');
});

test('守卫自身有效 · 能抓住被塞进来的未定义变量（变异验证）', () => {
  const defs = definedVars(readFileSync(CSS, 'utf8'));
  // ① 未定义 → 必须被抓住
  assert.deepEqual(usedVars("border: '1px solid var(--nope-not-defined)'"), [
    { name: '--nope-not-defined', line: 1 },
  ]);
  assert.equal(defs.has('--nope-not-defined'), false);
  // ② 已定义 → 不得误报
  assert.equal(defs.has('--border'), true);
  // ③ 行号要准（第 3 行）
  assert.deepEqual(usedVars('a\nb\nc var(--border)'), [{ name: '--border', line: 3 }]);
  // ④ 同一文件里的 `--x:` 定义不算引用（`var(--x)` 与 `--x:` 必须区分开）
  assert.deepEqual(usedVars(':root { --x: 1px }'), []);
});

test('扫描面不为空（防止路径写错导致审计静默跳过）', () => {
  const files = sourceFiles();
  assert.ok(files.length >= 20, `只扫到 ${files.length} 个源文件，路径可能不对`);
  assert.ok(files.includes('pages/CourseConfig.tsx'), '必须扫到 CourseConfig.tsx');
  assert.ok(
    relative(SRC, CSS).split(sep).join('/') === 'styles/global.css',
    'global.css 路径约定',
  );
});

/* ==========================================================================
 * 色值债务守卫（2026-11-12 色值定版时新增）
 * ==========================================================================
 *
 * ## 为什么需要它
 *
 * 色值定版前，线下青 `#00cec9` **散落在 4 个文件 16 处**，改一次线下的配色
 * 要动 16 处。而且它还在**持续生长** —— 方案里写的是 14 处，定版当天实际
 * 已经变成 16 处（后面合入的功能又添了 2 处）。
 *
 * 人工 grep 能查一次，但**查一次就完了**：下一个人加一行 `color="#00cec9"`,
 * 没有任何机制会告诉他。所以必须做成 CI 守卫。
 *
 * ## 它守什么
 *
 * ① `src/**`（含 global.css 之外的全部源文件）里**不得再出现**旧线下青的
 *    任何写法（hex 与 rgba 两种形式、带不带空格都算）；
 * ② `global.css` **必须定义** `--accent-offline` 及其亮色变体；
 * ③ 旧语义色 / 旧 accent 的 rgba 派生值同样不得复活 —— 那些值是按旧主色
 *    手写的，主色换了不改派生 ⇒ 淡底与主色对不上（肉眼可见的「脏」）。
 *
 * ## 白名单
 *
 * `OverlayControlPanel.tsx` 的字幕颜色预设是**用户可选的内容数据**，不是
 * 设计 token，不在管辖范围（本守卫只扫「设计色值」的旧写法）。
 */

const OFFLINE_CYAN_LEGACY = [
  '#00cec9',
  '#00CEC9',
  'rgba(0,206,201',
  'rgba(0, 206, 201',
];

/** 旧语义色 / 旧 accent 的 rgba 派生（前三个数就是旧主色的 rgb） */
const LEGACY_RGB_PREFIX = [
  '253,203,110', // 旧 warning #fdcb6e
  '253, 203, 110',
  '225,112,85', // 旧 danger #e17055
  '225, 112, 85',
  '108,92,231', // 旧 accent #6c5ce7
  '108, 92, 231',
];

test('src/** 不得再出现旧线下青硬编码（2026-11-12 色值定版）', () => {
  const offenders: string[] = [];
  for (const rel of sourceFiles()) {
    const text = readFileSync(join(SRC, rel), 'utf8');
    for (const needle of OFFLINE_CYAN_LEGACY) {
      if (text.includes(needle)) offenders.push(`src/${rel} 含 ${needle}`);
    }
  }
  assert.deepEqual(
    offenders,
    [],
    `线下青必须走 var(--accent-offline)：\n${offenders.join('\n')}`,
  );
});

test('src/** 不得再出现旧语义色/旧 accent 的 rgba 派生', () => {
  const offenders: string[] = [];
  for (const rel of sourceFiles()) {
    const text = readFileSync(join(SRC, rel), 'utf8');
    for (const needle of LEGACY_RGB_PREFIX) {
      if (text.includes(needle)) offenders.push(`src/${rel} 含 ${needle}`);
    }
  }
  assert.deepEqual(
    offenders,
    [],
    `语义色派生必须跟主色同源（走 --warning-soft / --danger-soft 等）：\n${offenders.join('\n')}`,
  );
});

test('global.css 必须定义线下青 token 及其亮色变体', () => {
  const defs = definedVars(readFileSync(CSS, 'utf8'));
  for (const v of ['--accent-offline', '--accent-offline-light', '--accent-offline-wash']) {
    assert.ok(defs.has(v), `global.css 必须定义 ${v}`);
  }
});

test('守卫自身有效 · 能抓住被塞回来的硬编码色（变异验证）', () => {
  // ① 旧线下青 hex 必须被识别
  assert.ok('#00cec9'.includes('#00cec9'), '变异样本应含旧色');
  // ② 新 token 写法不应被误判
  assert.ok(!'var(--accent-offline)'.includes('#00cec9'), 'token 写法不应误报');
  assert.ok(!'var(--accent-offline-wash)'.includes('rgba(0,206,201'), 'token 写法不应误报');
  // ③ 旧语义色派生确实出现在旧写法里
  assert.ok("'rgba(225,112,85,0.3)'".includes('225,112,85'), '旧 danger 派生应被识别');
});
