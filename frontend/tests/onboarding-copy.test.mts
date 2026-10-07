/**
 * 首启向导「文案红线」守卫（`src/lib/onboarding.ts`）
 *
 * 设计稿给第 6 步（术语降级开关）划了三条红线，第 1 步隐私说明也有硬要求：
 *   1. **禁止实现术语**：「提示词 / token / 预算 / 截断」这类词一旦出现在
 *      界面上，用户就会以为自己配置错了什么。和 B4 修复后的控制面板口径一致。
 *   2. **不许说「智能降级」**——不承诺具体行为的话等于没说，用户无法据此
 *      判断「关掉它我会失去什么」。
 *   3. **必须说明恢复方式**：重新开始听课后自动恢复，不用改任何设置。
 *
 * 为什么直接 import 模块而不是扫组件源码：文案已经外提到 `onboarding.ts`
 * （组件里的 JSX 没法被 node --test 读出来，见项目既有结论），所以这里能
 * **遍历真实文案对象本身**，比正则扫源码强一个量级 —— 扫源码会漏掉拼接
 * 出来的字符串，也会被注释误伤。
 *
 * 热键对拍：第 5 步教用户的快捷键必须与 `main.cjs` 实际注册的逐字一致
 *（去空格后比较）——「引导里教了一个不存在的快捷键」是用户无法自救的错误。
 *
 * 变异验证：`auditCopy` 是纯函数，所以变异**在进程内构造**（把禁词塞进
 * 文案再跑一遍审计），不需要临时改文件 —— 覆盖「审计逻辑失效」与
 * 「禁词漏检」两类事故。
 *
 * 运行：npm run test（.mts 必须走 npm test，系统 node 18 没有类型擦除）
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  TOTAL_STEPS,
  HOTKEYS,
  WIZARD_COPY,
  STEP_TITLES,
  STEP4_KEYS,
  stepLabel,
  clampStep,
  nextStep,
  prevStep,
  isFirstStep,
  isLastStep,
  modelPhase,
  phaseIsFinal,
  SEED_TIMEOUT_ATTEMPTS,
} from '../src/lib/onboarding.ts';

const HERE = dirname(fileURLToPath(import.meta.url));

// ---------------------------------------------------------------- 审计

/** 界面可见文案里禁止出现的实现术语（与控制面板 B4 修复后的口径一致） */
const FORBIDDEN_TERMS = /token|Token|预算|截断|提示词|initial_prompt/;

/** 不承诺具体行为、用户无法据此做决策的含糊说法 */
const VAGUE_TERMS = /智能降级|智能地|自动优化/;

type CopyNode = string | readonly string[] | readonly Record<string, unknown>;
type CopyTree = Record<string, CopyNode>;

/**
 * 遍历文案树，返回问题清单（空数组 = 通过）。
 * `forbidden` 允许注入：变异用例要塞进**任意**禁词来验证审计没有漏检。
 */
function auditCopy(copy: CopyTree, forbidden: RegExp = FORBIDDEN_TERMS): string[] {
  const problems: string[] = [];
  const walk = (path: string, node: unknown): void => {
    if (typeof node === 'string') {
      if (forbidden.test(node)) {
        problems.push(`文案 ${path} 含实现术语：${JSON.stringify(node)}`);
      }
      if (VAGUE_TERMS.test(node)) {
        problems.push(`文案 ${path} 含不承诺行为的说法：${JSON.stringify(node)}`);
      }
      return;
    }
    if (Array.isArray(node)) {
      node.forEach((item, i) => walk(`${path}[${i}]`, item));
      return;
    }
    if (node && typeof node === 'object') {
      for (const [k, v] of Object.entries(node)) walk(path ? `${path}.${k}` : k, v);
    }
  };
  walk('', copy);
  return problems;
}

// ---------------------------------------------------------------- 文案红线

test('全部文案 · 无实现术语、无含糊说法', () => {
  assert.deepEqual(auditCopy(WIZARD_COPY as unknown as CopyTree), []);
});

test('第 6 步 · 三条红线逐条核对', () => {
  const c = WIZARD_COPY as unknown as CopyTree;
  // 红线 3：必须说明恢复方式，且 recovery 说明本身也要干净
  const recover = `${c.step6RecoverTitle} ${c.step6RecoverBody}`;
  assert.match(recover, /恢复/);
  assert.match(recover, /重新开始听课/);
  assert.match(recover, /不用改任何设置/);
  assert.deepEqual(auditCopy({ recover } as unknown as CopyTree), [],
    '恢复说明自身不得夹带实现术语');
  // 「它做了什么」讲的是行为（缩小范围 + 不丢词），不是机制
  const what = `${c.step6WhatBody}`;
  assert.match(what, /术语范围缩小/);
  assert.match(what, /不会丢/);
  // 开关标签必须与控制面板口径一致（B4 后面板文案无「提示词」「降级」）
  assert.doesNotMatch(String(c.step6ToggleLabel), /提示词|降级|token|预算|截断/);
});

test('第 1 步 · 隐私说明必须交代「本地默认、云端例外、默认关闭」', () => {
  const privacy = WIZARD_COPY.step1Privacy;
  assert.match(privacy, /本地运行/);
  assert.match(privacy, /不联网/);
  assert.match(privacy, /云端 AI 服务/);   // 例外要交代（设计稿 §6）
  assert.match(privacy, /默认关闭/);
});

test('第 4 步 · 穿透三态说明齐备（这是用户最反直觉的一步）', () => {
  const c = WIZARD_COPY as unknown as CopyTree;
  assert.match(String(c.step4State1Title), /穿透/);
  assert.match(String(c.step4State1Body), /故意的/);
  assert.match(String(c.step4State2Title), /调整模式/);
  assert.match(String(c.step4State3Title), /重置位置/);
});

// ---------------------------------------------------------------- 结构

test('步骤数三处一致：TOTAL_STEPS / STEP_TITLES / state 模块', () => {
  // electron/onboarding-state.cjs 也有一份 TOTAL_STEPS，两处必须一致
  const stateMod = createRequire(import.meta.url)('../electron/onboarding-state.cjs');
  assert.equal(TOTAL_STEPS, 6);
  assert.equal(stateMod.TOTAL_STEPS, TOTAL_STEPS);
  assert.equal(STEP_TITLES.length, TOTAL_STEPS);
  for (const t of STEP_TITLES) assert.ok(t.length > 0, '步骤标题不得为空串');
});

test('热键表 · 5 条，与 main.cjs 实际注册的逐字一致（去空格后对拍）', () => {
  const mainSrc = readFileSync(join(HERE, '..', 'electron', 'main.cjs'), 'utf8')
    // 先剥注释：main.cjs 的注释里会举例说明热键格式
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');
  const registered = [...mainSrc.matchAll(/bind\('([^']+)'/g)].map((m) => m[1]);
  assert.equal(registered.length, 5, 'main.cjs 应注册 5 条热键');

  const taught = HOTKEYS.map((h) => h.accelerator);
  const norm = (s: string) => s.replace(/\s+/g, '');
  assert.deepEqual(
    taught.map(norm),
    registered.map(norm),
    '向导教的快捷键与实际注册的不一致 —— 用户照着按没反应，且无法自救',
  );
  assert.deepEqual(
    HOTKEYS.map((h) => h.needsOverlay),
    [false, true, true, true, true],
    '只有开关悬浮窗那条不依赖字幕条已打开',
  );
});

test('第 4 步引用的快捷键取自 HOTKEYS（同一句文案不存两份）', () => {
  assert.equal(STEP4_KEYS.toggleOverlay, HOTKEYS[0].accelerator);
  assert.equal(STEP4_KEYS.adjustMode, HOTKEYS[1].accelerator);
  assert.equal(STEP4_KEYS.resetPosition, HOTKEYS[2].accelerator);
});

// ---------------------------------------------------------------- 纯逻辑

test('stepLabel / clampStep / next / prev / isFirst / isLast', () => {
  assert.equal(stepLabel(3), '第 3 / 6 步');
  assert.equal(stepLabel(0), '第 1 / 6 步');
  assert.equal(stepLabel(99), '第 6 / 6 步');
  assert.equal(clampStep(null as unknown as number), 1);
  assert.equal(clampStep('x' as unknown as number), 1);
  assert.equal(clampStep(2.9), 2);
  assert.equal(nextStep(6), 6);
  assert.equal(prevStep(1), 1);
  assert.equal(nextStep(2), 3);
  assert.equal(prevStep(3), 2);
  assert.ok(isFirstStep(1) && !isFirstStep(2));
  assert.ok(isLastStep(6) && !isLastStep(5));
});

test('modelPhase · 五态状态机全分支（含播种档）', () => {
  // ping 不通：短时间 = 还在启动；问满 60 次 = 失败（不能无限转圈）
  assert.equal(modelPhase({ pingOk: false, modelOk: null }, 0), 'backend-starting');
  assert.equal(modelPhase({ pingOk: false, modelOk: null }, 59), 'backend-starting');
  assert.equal(modelPhase({ pingOk: false, modelOk: null }, 60), 'failed');

  // ping 通了：模型信号三态
  assert.equal(modelPhase({ pingOk: true, modelOk: true }), 'ready');
  assert.equal(modelPhase({ pingOk: true, modelOk: false }), 'failed');
  assert.equal(modelPhase({ pingOk: true, modelOk: null }), 'preparing');

  // 播种中 → seeding，**不是 failed**。这是首启缺陷修复的核心：
  // 旧实现只有 ok 二分，把「正在复制种子」判成「准备失败」，用户被卡在第 3 步。
  assert.equal(modelPhase({ pingOk: true, modelOk: false, seeding: true }), 'seeding');
  // 播种超时 → 失败分支（界面给「重试 / 跳过」，不能无限等）
  assert.equal(
    modelPhase({ pingOk: true, modelOk: false, seeding: true }, SEED_TIMEOUT_ATTEMPTS),
    'failed',
  );
  assert.equal(
    modelPhase({ pingOk: true, modelOk: false, seeding: true }, SEED_TIMEOUT_ATTEMPTS - 1),
    'seeding',
  );
  // 种子可用但播种还没起来（接口触发与下次探测之间的那一瞬）→ 也算「正在准备」，
  // 否则界面会闪一下「准备失败」
  assert.equal(modelPhase({ pingOk: true, modelOk: false, seedExists: true }), 'seeding');
  // 连种子都没有 → 失败得干脆，不给用户无限等待
  assert.equal(modelPhase({ pingOk: true, modelOk: false, seedExists: false }), 'failed');
  assert.equal(modelPhase({ pingOk: true, modelOk: false }), 'failed');

  // 状态机不认任何本地计数器伪造进度
  assert.equal(modelPhase({ pingOk: true, modelOk: null }, 1000), 'preparing', 'preparing 不因时间自动变 ready');
  // 后端若是旧版本（不返回 seeding / seed_exists），退回按 ok 二分的旧行为
  assert.equal(modelPhase({ pingOk: true, modelOk: false, seeding: false, seedExists: false }), 'failed');
});

test('phaseIsFinal · 只有 ready / failed 是终态（seeding 要继续轮询）', () => {
  assert.ok(phaseIsFinal('ready'));
  assert.ok(phaseIsFinal('failed'));
  assert.ok(!phaseIsFinal('preparing'));
  assert.ok(!phaseIsFinal('backend-starting'));
  assert.ok(!phaseIsFinal('seeding'), 'seeding 必须继续轮询，否则播种完成也进不了下一步');
});

// ---------------------------------------------------------------- 变异验证

test('变异 · 审计能抓住被塞进文案的每一类禁词（守卫失效 = 白守）', () => {
  const cases: [string, string][] = [
    ['实现术语 token', '超出 token 预算'],
    ['实现术语 预算', '按预算分配'],
    ['实现术语 截断', '超出后会被截断'],
    ['实现术语 提示词', '提示词长度受限'],
    ['含糊说法 智能降级', '系统会智能降级'],
  ];
  for (const [label, poison] of cases) {
    const mutated = { ...WIZARD_COPY, step6WhatBody: poison } as unknown as CopyTree;
    const problems = auditCopy(mutated);
    assert.ok(problems.length > 0, `变异「${label}」必须被审计抓住`);
    assert.match(problems.join('\n'), /step6WhatBody/, `变异「${label}」要能定位到字段`);
  }
});
