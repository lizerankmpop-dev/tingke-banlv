/**
 * 控制面板可见文案守卫：与向导同口径，且不得出现实现术语
 *
 * 为什么需要它（两次真实教训）：
 *
 *   1. **同一功能两个名字**：面板里的开关曾叫「术语提示词自动防循环降级」，
 *      而向导第 6 步叫「参与识别的术语自动防循环」—— 用户会以为是两件事；
 *      「提示词」（Whisper 的 `initial_prompt`）更是纯实现细节，不该露给学生用户。
 *      M3 首启验收把面板改成与向导同源的说法，这里把它钉住。
 *
 *   2. **必须先剥注释再扫**：这两个文件的注释里大量出现「提示词」与
 *      `initial_prompt`（解释降级策略与 token 预算的来龙去脉，是必要的工程说明）。
 *      不剥注释，守卫会因为说明性注释而永远失败，然后被「顺手」放宽成一个
 *      更弱的断言 —— 那才是真正的失败。文件末尾的变异用例专门钉住这一点。
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { WIZARD_COPY } from '../src/lib/onboarding.ts';

const HERE = dirname(fileURLToPath(import.meta.url));

/** 面板开关名与向导开关名共用的核心短语（口径的唯一来源） */
const CORE = '参与识别的术语自动防循环';

/** 界面可见文案里禁止出现的实现术语 */
const FORBIDDEN = /提示词|initial_prompt/;

/**
 * 剥掉块注释（含 JSX 的 `{/* … *\/}`）
 *
 * 刻意**只剥块注释、不剥 `//` 行注释**：面板源码里有 URL 与正则字面量，
 * 按 `//` 切会把 `http://…` 的后半句当成注释吃掉，反而漏掉真文案。
 * 本项目的中文说明性注释一律用 `/* *\/` 或 `//` 单行开头整行，块注释足够覆盖。
 */
function stripBlockComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, '');
}

/** 审计一份源码的可见文案；返回违规项（导出给变异用例复用） */
function auditCopy(source: string): string[] {
  const visible = stripBlockComments(source);
  return FORBIDDEN.test(visible) ? ['禁词'] : [];
}

const PANEL_SRC = readFileSync(resolve(HERE, '../src/components/OverlayControlPanel.tsx'), 'utf8');
const TERM_GUARD_SRC = readFileSync(resolve(HERE, '../src/lib/termGuard.ts'), 'utf8');
const WIZARD_SRC = readFileSync(resolve(HERE, '../src/pages/OnboardingWizard.tsx'), 'utf8');

test('面板可见文案不含实现术语（提示词 / initial_prompt）', () => {
  assert.deepEqual(auditCopy(PANEL_SRC), [], 'OverlayControlPanel.tsx 的可见文案里出现了实现术语');
});

test('面板状态行文案（termGuard）不含实现术语', () => {
  assert.deepEqual(auditCopy(TERM_GUARD_SRC), [], 'termGuard.ts 的输出文案里出现了实现术语');
});

test('向导组件可见文案不含实现术语', () => {
  assert.deepEqual(auditCopy(WIZARD_SRC), [], 'OnboardingWizard.tsx 的可见文案里出现了实现术语');
});

test('向导第 6 步与控制面板的开关名同口径', () => {
  assert.ok(
    WIZARD_COPY.step6ToggleLabel.startsWith(CORE),
    `向导开关名应以「${CORE}」开头，当前是「${WIZARD_COPY.step6ToggleLabel}」`,
  );
  const visible = stripBlockComments(PANEL_SRC);
  assert.ok(
    visible.includes(`${CORE}降级`),
    `面板开关名应与向导同源（「${CORE}降级」）—— 两个名字会被当成两个功能`,
  );
});

test('面板旧名（术语提示词…）不得复活', () => {
  assert.ok(
    !stripBlockComments(PANEL_SRC).includes('术语提示词'),
    '旧的「术语提示词自动防循环降级」又回来了：含实现术语，且与向导口径不一致',
  );
});

test('守卫自身有效：先剥注释，再抓可见文案（变异验证）', () => {
  // ① 刻意塞进可见文案 → 必须被抓住（否则「测试全绿」毫无意义）
  const bad = '<span id={A11Y_ID.termAutoDegrade}>术语提示词自动防循环降级</span>';
  assert.notDeepEqual(auditCopy(bad), [], '守卫失效：可见文案里塞了「提示词」却没报');

  // ② 同样的词出现在注释里 → 合法（工程说明必须能解释 initial_prompt）
  const inComment =
    '{/* 术语提示词防循环降级：initial_prompt 注入与 token 预算的说明 */}\n'
    + '<span>参与识别的术语自动防循环降级</span>';
  assert.deepEqual(auditCopy(inComment), [], '注释里的说明性文字不该被判成 UI 文案违规');
});
