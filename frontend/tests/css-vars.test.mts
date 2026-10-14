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

/** 扫一份文本里**引用**的变量名（带行号，便于直接定位） */
function usedVars(text: string): { name: string; line: number }[] {
  const out: { name: string; line: number }[] = [];
  for (const m of text.matchAll(/var\(\s*(--[a-zA-Z0-9_-]+)/g)) {
    out.push({ name: m[1], line: text.slice(0, m.index).split('\n').length });
  }
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
