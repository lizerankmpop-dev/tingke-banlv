/**
 * 帮助 / 关于（V6-2）的守卫
 *
 * ## 这一页为什么值得单独守卫
 *
 * 它是**用户排障时唯一会打开的地方**，所以它自己坏掉的代价特别高：
 * 用户已经遇到问题，再看到一个显示旧版本号 / 打开目录点了没反应 / 术语看不懂的
 * 帮助页，就彻底没有自助途径了。三条具体红线：
 *
 * 1. **版本号不能写死。** 写死等于多了个不会被任何构建步骤改写的「版本号出口」，
 *    而用户恰恰是为了报版本号才打开这一页。整链守卫在
 *    `version-consistency.test.cjs` 的「界面显示链」一节（package.json →
 *    app.getVersion() → IPC → preload → bridge → 组件）。
 * 2. **打开日志目录失败必须说出来。** `shell.openPath` **不抛异常**，
 *    失败时把错误当字符串返回、成功时返回空串 —— 不检查返回值就变成
 *    「点了没反应」，用户以为按钮坏了。
 * 3. **文案里不许出现实现术语。** 「常见问题」最容易顺手写出「提示词 / token /
 *    预算 / 截断」——用户看了只会以为自己配错了什么。审计与禁词表与向导守卫
 *    共用同一份（`_copy-audit.mts`）。
 *
 * 运行：npm run test（.mts 必须走 npm test，系统 node 18 没有类型擦除）
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  HELP_COPY,
  HELP_KEYS,
  VERSION_FALLBACK,
  PATH_FALLBACK,
  openLogDirMessage,
  versionLine,
} from '../src/lib/helpCopy.ts';
import { HOTKEYS } from '../src/lib/onboarding.ts';

import { auditCopy, type CopyTree } from './_copy-audit.mts';

const HERE = dirname(fileURLToPath(import.meta.url));

const LIST_SRC = readFileSync(join(HERE, '..', 'src', 'pages', 'CourseList.tsx'), 'utf8');
const PANEL_SRC = readFileSync(join(HERE, '..', 'src', 'components', 'HelpAboutPanel.tsx'), 'utf8');
const BRIDGE_SRC = readFileSync(join(HERE, '..', 'src', 'lib', 'diagBridge.ts'), 'utf8');
const MAIN_SRC = readFileSync(join(HERE, '..', 'electron', 'main.cjs'), 'utf8');

/**
 * 剥掉块注释后再扫源码
 *
 * 必须剥：注释里会**引用**被禁止的写法（例如解释「为什么不用 shell.openPath
 * 的返回值当判据」时提到「点了没反应」）。不剥就会因说明性注释永远红，
 * 然后被顺手放宽成更弱的断言 —— 那才是真正的失败（`panel-copy.test.mts` 踩过）。
 */
function stripBlockComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, '');
}

// ---------------------------------------------------------------- 文案红线

test('帮助页文案 · 无实现术语、无含糊说法', () => {
  assert.deepEqual(auditCopy(HELP_COPY as unknown as CopyTree), []);
});

test('常见问题：条数、字段齐备、问答不重复', () => {
  const faq = HELP_COPY.faq;
  assert.ok(faq.length >= 5, `常见问题应至少 5 条，当前 ${faq.length} 条`);
  const questions = faq.map((f) => f.q);
  assert.equal(new Set(questions).size, questions.length, '问题不得重复（被当作 React key）');
  for (const item of faq) {
    assert.ok(item.q.trim().length > 0 && item.a.trim().length > 0, `「${item.q}」缺问题或答案`);
    // 答案是给人看的：太短说明只写了个标题，等于没答
    assert.ok(item.a.trim().length >= 20, `「${item.q}」的答案过短（${item.a.length} 字），等于没答`);
  }
});

test('版本号与路径的占位符不是空串（空白会让人以为是渲染坏了）', () => {
  assert.ok(VERSION_FALLBACK.trim().length > 0);
  assert.ok(PATH_FALLBACK.trim().length > 0);
  assert.notEqual(VERSION_FALLBACK, PATH_FALLBACK, '两种不可用状态应能区分开');
});

test('关键文案齐备（入口 / 关闭 / 打开目录 / 导出日志 / 加载失败）', () => {
  for (const key of [
    'openLabel',
    'openHint',
    'dialogTitle',
    'dialogTitleId',
    'closeLabel',
    'versionLabel',
    'logDirLabel',
    'openLogDir',
    'openLogDirOk',
    'openLogDirFailed',
    'exportLogs',
    'exportLogsOk',
    'exportLogsCanceled',
    'exportLogsFailed',
    'infoLoadFailed',
    'faqTitle',
    'footNote',
  ] as const) {
    assert.ok(String(HELP_COPY[key]).trim().length > 0, `缺少文案 HELP_COPY.${key}`);
  }
});

// -------------------------------------------------- 热键引用（不许第二份字面量）

test('答疑里引用的热键取自 HOTKEYS，且真的是「重置位置」那一条', () => {
  // 按位置取（与 STEP4_KEYS 同套路）。必须断言**语义**也还对得上：
  // 若有人重排 HOTKEYS，位置取法会静默指向另一条热键，页面就会教错快捷键。
  assert.equal(HELP_KEYS.resetPosition.accelerator, HOTKEYS[2].accelerator);
  assert.ok(
    HELP_KEYS.resetPosition.action.includes('挪回'),
    `HELP_KEYS.resetPosition 指向的不是「挪回屏幕底部居中」那条，而是「${HELP_KEYS.resetPosition.action}」`,
  );
  assert.equal(HELP_KEYS.resetPosition.needsOverlay, true, '该热键需要先打开字幕条，答疑要交代');

  const answer = HELP_COPY.faq.find((f) => f.q.includes('字幕条找不到'));
  assert.ok(answer, '缺少「字幕条找不到了」这条答疑');
  assert.ok(
    answer.a.includes(HELP_KEYS.resetPosition.accelerator),
    '答疑里必须出现真实的热键组合（用户按不出来就没意义）',
  );
  assert.ok(answer.a.includes('需要先打开字幕条'), '该热键依赖字幕条已打开，必须交代');

  // 反向：答案里不得出现**任何**已经不在 HOTKEYS 里的组合键写法
  // （改注册键时最容易漏的就是这一页）
  const allKeys = HOTKEYS.map((h) => h.accelerator);
  const mentioned = [...answer.a.matchAll(/Alt\s*\+\s*Shift\s*\+\s*(\S)/g)].map((m) => m[0]);
  for (const one of mentioned) {
    const normalized = (s: string) => s.replace(/\s+/g, '');
    assert.ok(
      allKeys.map(normalized).includes(normalized(one)),
      `答疑里出现了不存在的快捷键「${one}」—— 用户照着按没反应，且无法自救`,
    );
  }
});

// ------------------------------------------------ 打开目录结果：三分支纯函数

test('打开日志目录的结果必须能被区分成「成功 / 失败」三分支', () => {
  // ① 成功
  assert.deepEqual(openLogDirMessage({ ok: true }), { tone: 'ok', text: HELP_COPY.openLogDirOk });
  // ② 失败**必须带原因**（否则用户和开发者都无从下手）
  const failed = openLogDirMessage({ ok: false, error: 'Access is denied.' });
  assert.equal(failed.tone, 'error');
  assert.ok(failed.text.includes('Access is denied.'), '失败提示里必须带上真实原因');
  // ③ 失败但没有原因 / 传了 null —— 也必须判成失败，**不能**因为「没有 error」就当好结果
  for (const weird of [null, undefined, {}, { ok: false }, { ok: false, error: '   ' }]) {
    const r = openLogDirMessage(weird as { ok?: boolean; error?: string });
    assert.equal(r.tone, 'error', `异常输入 ${JSON.stringify(weird)} 被判成了成功`);
    assert.ok(r.text.includes('原因未知'), '没有原因时要明说「原因未知」，不能留空');
  }
});

test('版本展示串：正常值直接用，空值走占位', () => {
  assert.deepEqual(versionLine('3.6.0'), { label: HELP_COPY.versionLabel, value: '3.6.0' });
  for (const bad of ['', '   ', '\n']) {
    assert.equal(versionLine(bad).value, VERSION_FALLBACK, `空值 ${JSON.stringify(bad)} 应走占位`);
  }
});

// ---------------------------------------------------------------- 源守卫

test('课程列表标题栏必须有入口，且文案走 HELP_COPY', () => {
  assert.match(LIST_SRC, /data-testid="help-about-open"/, '入口必须有稳定选择器（冒烟脚本按它断言）');
  assert.match(LIST_SRC, /aria-label=\{HELP_COPY\.openLabel\}/, '可访问名必须指向 HELP_COPY.openLabel');
  assert.match(LIST_SRC, /title=\{HELP_COPY\.openHint\}/, 'hover 提示必须指向 HELP_COPY.openHint');
  assert.match(LIST_SRC, /<HelpAboutPanel\s/, '必须真的渲染面板 —— 只 import 不渲染等于没做');
  assert.match(LIST_SRC, /onClick=\{\(\)\s*=>\s*setShowHelp\(true\)\}/, '入口必须真的能打开面板');

  // 反向：组件里不得出现第二份中文（同一句话存两份 = 改一处必漂移）
  for (const lit of [HELP_COPY.openLabel, HELP_COPY.openHint]) {
    assert.ok(!LIST_SRC.includes(lit), `CourseList 里出现了文案字面量「${lit}」—— 必须走 HELP_COPY`);
  }
});

test('面板无障碍：role=dialog + aria-modal + aria-labelledby + 关闭按钮可访问名', () => {
  const code = stripBlockComments(PANEL_SRC);
  assert.match(code, /role="dialog"/, '面板必须是 dialog');
  assert.match(code, /aria-modal="true"/, '模态必须标 aria-modal');
  assert.match(code, /aria-labelledby=\{HELP_COPY\.dialogTitleId\}/, '必须用标题做可访问名');
  assert.match(code, /id=\{HELP_COPY\.dialogTitleId\}/, '标题元素必须挂上那个 id，否则 aria-labelledby 指向空');
  assert.match(code, /aria-label=\{HELP_COPY\.closeLabel\}/, '关闭按钮只有图标，必须显式 aria-label');
  assert.match(code, /data-testid="help-about-panel"/, '面板需要稳定选择器');
  assert.match(code, /data-testid="help-about-version"/, '版本号需要稳定选择器（e2e 要断言它非占位值）');
  assert.match(code, /data-testid="help-about-logdir"/, '日志目录需要稳定选择器');
});

test('面板：版本号来自主进程、失败有提示、Esc 可关', () => {
  const code = stripBlockComments(PANEL_SRC);
  assert.match(code, /getDiagInfo\(\)/, '必须去主进程取诊断信息');
  assert.match(code, /info\?\.version/, '版本号必须来自返回值，不能写死');
  assert.ok(
    !/['"`]\d+\.\d+\.\d+['"`]/.test(code),
    '面板里出现了版本号字面量 —— 升版时它会继续显示旧值',
  );
  // 取不到也要能用：帮助页最不该「因为拿不到版本号就打不开」
  assert.match(code, /HELP_COPY\.infoLoadFailed/, '取不到诊断信息时要有提示，不能静默');
  assert.match(code, /key.*===.*'Escape'|'Escape'/, 'Esc 要能关闭（模态的基本预期）');
  // 打开目录之后必须把结果**显示出来**（`shell.openPath` 不抛异常，静默 = 用户以为坏了）
  assert.match(code, /openLogDirMessage\(/, '打开目录的结果必须过判定函数再显示');
  assert.match(code, /data-testid="help-about-notice"/, '结果提示需要稳定选择器（e2e 要断言失败可见）');
});

test('桥接层永不抛：每一处都兜底（帮助页在排障场景下失败很正常，不能因此打不开）', () => {
  const src = stripBlockComments(BRIDGE_SRC);
  // 三个导出函数各自必须有 try/catch 或显式兜底
  for (const fn of ['getDiagInfo', 'openLogDir', 'exportLogs']) {
    assert.match(src, new RegExp(`export async function ${fn}\\(\\)`), `缺少桥接函数 ${fn}`);
  }
  assert.equal((src.match(/catch/g) || []).length >= 3, true, '三个桥接函数都要有兜底，不能有裸 await');
  assert.match(src, /'当前环境不支持打开日志目录'/, '浏览器调试环境要如实报告不可用，不假装成功');
});

test('主进程：shell.openPath 的返回值必须被检查（它不抛异常）', () => {
  const src = stripBlockComments(MAIN_SRC);
  const block = src.slice(
    src.indexOf("ipcMain.handle('diag:open-log-dir'"),
    src.indexOf("ipcMain.handle('diag:open-log-dir'") + 1200,
  );
  assert.ok(block.length > 0, '未找到 diag:open-log-dir 处理器');
  assert.match(block, /const errMsg = await shell\.openPath\(dir\)/, '必须接住 openPath 的返回值');
  assert.match(block, /if\s*\(errMsg\)/, '返回值非空 = 失败，必须分支出来');
  assert.match(block, /ok:\s*false[\s\S]*error/, '失败分支必须带 error 字段');
  // 反向：不许写成「调完就当成功」
  assert.ok(
    !/await shell\.openPath\(dir\);\s*\n\s*return \{ ok: true/.test(block),
    '直接把 openPath 的调用结果当成功 = 失败时静默，用户会以为按钮坏了',
  );
});

// ---------------------------------------------------------------- 变异验证

test('变异 · 审计能抓住被塞进帮助文案的每一类禁词（守卫失效 = 白守）', () => {
  const cases: [string, string][] = [
    ['实现术语 token', '超出 token 预算'],
    ['实现术语 预算', '按预算分配'],
    ['实现术语 截断', '过长的会被截断'],
    ['实现术语 提示词', '提示词过长'],
    ['含糊说法 智能降级', '系统会智能降级'],
  ];
  for (const [label, poison] of cases) {
    const mutated = { ...HELP_COPY, footNote: poison } as unknown as CopyTree;
    const problems = auditCopy(mutated);
    assert.ok(problems.length > 0, `变异「${label}」必须被审计抓住`);
    assert.match(problems.join('\n'), /footNote/, `变异「${label}」要能定位到字段`);
  }
  // FAQ 是嵌套结构，审计必须能走进去（只审顶层就等于漏掉了这一页的主体）
  const mutatedFaq = {
    ...HELP_COPY,
    faq: [{ q: '测试', a: '这里藏着 提示词' }],
  } as unknown as CopyTree;
  assert.match(auditCopy(mutatedFaq).join('\n'), /faq/, '审计必须能递归进 faq 数组');
});
