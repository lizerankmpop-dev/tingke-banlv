/**
 * 首启向导的**全部可见文案**与纯逻辑（唯一来源）
 *
 * 为什么文案要单独成一个模块，而不是直接写在 `OnboardingWizard.tsx` 里：
 *
 * 1. **禁令要能自动检查。** 第 6 步讲的是「术语自动降级」，设计稿给它划了三条红线
 *    （不许出现「提示词/token/预算/截断」、不许说「智能降级」这种不承诺行为的话、
 *    必须说明「重新开始听课后会恢复完整术语表」）。这类禁令靠人 review 一定会漏，
 *    而写在组件里的 JSX 文案**没法被 node --test 读出来**（JSX 不在 Node 的类型
 *    擦除范围内）。抽成纯 TS 后，测试可以直接 import 本模块并遍历所有字符串——
 *    这比「正则扫源码」强一个量级（见 `tests/onboarding-copy.test.mts`）。
 * 2. **文案漂移是本项目的复发型缺陷。** B1 的 toast、B4 的「缩减到 N 字符」、
 *    B2 的 `aria-label`，病因都是同一句文案存了两份。所以这里刻意做成
 *    「屏幕上的字只有一份来源」，组件只负责排版。
 *
 * 本模块**不得** import react 或任何浏览器 API：它要能被 node --test 直接加载。
 */

/** 向导共 6 步（与 `electron/onboarding-state.cjs` 的 TOTAL_STEPS 必须一致） */
export const TOTAL_STEPS = 6

/**
 * 热键表（第 5 步渲染用）
 *
 * `accelerator` 必须与 `electron/main.cjs` 的 `registerOverlayShortcuts()` 逐字一致。
 * 这一条**有自动化守卫**：`tests/onboarding-copy.test.mts` 会去扫 main.cjs 里的
 * `bind('Alt+Shift+…')` 并与本表对拍 —— 因为「文档/引导里教了一个不存在的快捷键」
 * 是用户绝对无法自救的错误。
 */
export const HOTKEYS: { accelerator: string; action: string; needsOverlay: boolean }[] = [
  { accelerator: 'Alt + Shift + O', action: '显示 / 隐藏悬浮字幕条', needsOverlay: false },
  { accelerator: 'Alt + Shift + D', action: '进入 / 退出调整模式（临时恢复鼠标操作）', needsOverlay: true },
  { accelerator: 'Alt + Shift + R', action: '把字幕条挪回屏幕底部居中', needsOverlay: true },
  { accelerator: 'Alt + Shift + ]', action: '字幕字号 +2 px', needsOverlay: true },
  { accelerator: 'Alt + Shift + [', action: '字幕字号 −2 px', needsOverlay: true },
]

/**
 * 全部可见文案。
 *
 * 约定：组件里**只允许**通过 `WIZARD_COPY.xxx` 取字，不得再写第二份中文字面量。
 * （例外：`<kbd>` 里的组合键取自 `HOTKEYS`，数字/符号不在此列。）
 */
export const WIZARD_COPY = {
  // ---- 通用 ----
  appName: '听课伴侣',
  next: '下一步',
  prev: '上一步',
  start: '开始设置',
  skip: '跳过，直接使用',
  skipAll: '跳过',
  done: '开始使用',
  close: '关闭',
  // 找回入口（挂在课程列表页标题栏）：跳过后向导不再自动弹出，需留一条回看路径
  reopen: '重新查看新手引导',
  reopenHint: '随时回看这 6 步引导',

  // ---- 第 1 步 · 欢迎 ----
  step1Title: '欢迎使用听课伴侣',
  step1Lead: '这是一款默认全程本地运行的听课助手。',
  step1CanDoTitle: '它能做到：',
  step1CanDo: [
    '把老师说的话实时转成字幕',
    '点字幕里的任意单词，立刻看到中文释义',
    '自动整理这节课的专业词汇',
  ],
  step1Privacy:
    '默认全程本地运行、不联网。只有当你主动配置了云端 AI 服务时才会联网，'
    + '这个功能默认关闭。你的课程内容只留在这台电脑上。',

  // ---- 第 2 步 · 使用场景 ----
  step2Title: '你主要怎么听课？',
  step2OnlineTitle: '在线网课',
  step2OnlineSource: 'Coursera / B 站 / YouTube',
  step2OnlineHow: '直接捕获电脑播放的声音',
  step2OnlineLang: '识别语言以英文为主',
  step2OnlineSetup: '不需要任何额外设置',
  step2ClassroomTitle: '线下课堂',
  step2ClassroomSource: '教室 / 讲座 / 会议室',
  step2ClassroomHow: '用麦克风收音',
  step2ClassroomLang: '中英文混合都能识别',
  step2ClassroomSetup: '建议使用降噪麦克风',
  step2TipTitle: '小贴士',
  step2Tip:
    '电脑外放的声音可以直接采集，不需要装虚拟声卡，也不用改系统默认播放设备。',
  step2Note: '这个选择只作为你新建课程时的默认值，随时可以在课程里改。',

  // ---- 第 3 步 · 模型准备 ----
  step3Title: '正在准备语音模型',
  step3Lead: '首次使用需要准备好识别模型（已随安装包内置，约 141 MB）。',
  step3Once: '这一步只在第一次启动时需要，之后就是秒开。',
  step3NoNetwork: '无需联网下载',
  step3NoManual: '无需你手动放置任何文件',
  step3PhaseStarting: '正在启动本地服务…',
  step3PhasePreparing: '正在准备识别模型…',
  // 播种中：说清楚「在复制什么」而不是笼统的「准备中」，用户才知道这几秒是在等磁盘
  step3PhaseSeeding: '正在复制模型种子，请稍候',
  step3PhaseReady: '模型已就绪',
  step3PhaseReadyNote: '马上进入下一步。',
  step3FailedTitle: '模型准备失败。',
  step3FailedHowtoTitle: '请尝试：',
  step3FailedHowto: [
    '关闭应用后重新打开（会自动重试）',
    '若反复失败，点击下方按钮导出日志，反馈给开发者',
  ],
  step3Retry: '重试',
  step3ExportLogs: '导出日志',
  step3Later: '先跳过，稍后再说',
  step3Exported: '日志已导出：',
  step3ExportFailed: '日志导出失败：',

  // ---- 第 4 步 · 悬浮字幕条（最关键的一步）----
  step4Title: '字幕会变成一个「悬浮条」',
  step4Lead: '开始识别后，主界面会收起来，变成屏幕底部一条细长的字幕条。',
  step4Intro: '这个字幕条有三种状态，先认一下：',
  step4State1Title: '平时：鼠标穿透',
  step4State1Body:
    '点它、拖它都没反应 —— 这是故意的。这样它就不会挡住网课页面上的按钮。',
  step4State2Title: '要挪位置：调整模式',
  step4State2Body: '临时恢复鼠标操作，拖顶部的紫色条即可。再按一次退出调整模式。',
  step4State3Title: '拖丢了：重置位置',
  step4State3Body: '一键挪回屏幕底部居中，一定会出现。',
  step4Footer: '随时可以显示 / 隐藏它。',

  // ---- 第 5 步 · 热键 ----
  step5Title: '这五个快捷键，建议记一下',
  step5Lead: '它们在任何窗口里都能用 —— 包括你正在看网课的全屏浏览器。',
  step5ColKey: '快捷键',
  step5ColAction: '作用',
  step5ColNeeds: '需要先打开字幕条',
  step5NeedsYes: '是',
  step5NeedsNo: '—',
  step5Trouble: '如果某个快捷键没反应，通常是被别的软件占用了。应用不会报错，但可以在「导出日志」里查到是哪一条注册失败。',
  step5NoPermission:
    'Windows 下注册这些快捷键不需要申请任何系统权限，也不会弹权限确认框 —— 不用去系统设置里改什么。',
  step5ImeHint:
    '提示：如果你常用输入法，「Alt + Shift」可能被系统占用。遇到这种情况请导出日志反馈，我们会更换默认组合。',

  // ---- 第 6 步 · 术语与降级开关 ----
  step6Title: '让专业词也能被听懂',
  step6Lead: '老师讲的专业词，语音识别经常听错 ——',
  step6Examples: ['convolution 被听成 convalution', 'eigenvalue 被切成 eigen value'],
  step6Why: '你看到的字幕本身就是错的，查词自然查不到。',
  step6LayersTitle: '听课伴侣用「三层术语」来修正这个问题：',
  step6Layers: [
    '你手动填的术语 ← 最重要，识别优先级最高',
    '这节课已经识别出的词 ← 自动积累',
    '内置领域术语表 ← 覆盖计算机 / 医学 / 物理 / 经济等 15 个领域',
  ],
  step6LayersNote: '你可以在创建课程时直接填核心词，也可以稍后在课程配置里补充。',
  step6ToggleSeparator: '关于下面这个开关',
  step6ToggleLabel: '参与识别的术语自动防循环',
  step6ToggleDefault: '（默认开启，建议保持）',
  step6QgTitle: '它解决什么问题？',
  step6QgBody: '术语表如果太长，识别引擎会「刹不住车」—— 开始重复输出同一句话，字幕越滚越多，识别也会变慢。',
  step6WhatTitle: '它做了什么？',
  step6WhatBody: '一旦发现这种重复，自动把发给引擎的术语范围缩小一些，让识别恢复正常。你已经积累的课程词汇不会丢。',
  step6CostTitle: '代价是什么？',
  step6CostBody: '生效后，这一节课剩余时间里参与识别的术语会变少，可能有个别术语识别不出来。',
  step6RecoverTitle: '怎么恢复？',
  step6RecoverBody: '重新开始听课（停止识别再开始）就会恢复完整术语表 —— 不用改任何设置。',
  step6OffTitle: '那要关掉它吗？',
  step6OffBody:
    '一般情况下不用。如果你是术语密集型课程（比如整节课都在讲专业名词），'
    + '并且宁可偶尔看到重复字幕，也要保住完整的术语识别，可以关掉它。',
} as const

/** 第 4 步在正文里引用的两个快捷键（从 HOTKEYS 取，避免第二份字面量） */
export const STEP4_KEYS = {
  adjustMode: HOTKEYS[1].accelerator,
  resetPosition: HOTKEYS[2].accelerator,
  toggleOverlay: HOTKEYS[0].accelerator,
}

export const STEP_TITLES: string[] = [
  WIZARD_COPY.step1Title,
  WIZARD_COPY.step2Title,
  WIZARD_COPY.step3Title,
  WIZARD_COPY.step4Title,
  WIZARD_COPY.step5Title,
  WIZARD_COPY.step6Title,
]

/** `第 3 / 6 步`（步号 1-based，越界会被夹取） */
export function stepLabel(step: number, total: number = TOTAL_STEPS): string {
  return `第 ${clampStep(step)} / ${total} 步`
}

/** 把任意输入夹到 [1, TOTAL_STEPS]；非法值一律回到第 1 步 */
export function clampStep(step: number): number {
  const n = Number(step)
  if (!Number.isFinite(n)) return 1
  const i = Math.floor(n)
  if (i < 1) return 1
  return i > TOTAL_STEPS ? TOTAL_STEPS : i
}

export function nextStep(step: number): number {
  return clampStep(clampStep(step) + 1)
}

export function prevStep(step: number): number {
  return clampStep(clampStep(step) - 1)
}

export function isFirstStep(step: number): boolean {
  return clampStep(step) === 1
}

export function isLastStep(step: number): boolean {
  return clampStep(step) === TOTAL_STEPS
}

// ---- 第 3 步的状态机 ----

/**
 * 第 3 步的五种状态。
 *
 * `seeding` 这一档是修复首启缺陷时新增的：后端会在应用启动时**后台播种**模型种子
 * （141MB 从安装目录拷到可写目录）。这段时间里模型确实「不可用」，但它是**正常过程**
 * 而不是失败 —— 旧实现只有 ok 二分，于是全新安装的用户首启必然看到「模型准备失败」，
 * 还被引导去点一个不会触发播种的「重试」。
 */
export type ModelPhase = 'backend-starting' | 'preparing' | 'seeding' | 'ready' | 'failed'

/**
 * 播种的最长等待探测次数（1 次 = 1 秒，与 `OnboardingWizard` 的 1000ms 轮询一致）
 *
 * 超过就判失败、给出「重试 / 跳过」—— 兜底「播种卡死」这类没法自愈的情况。
 *
 * 实测（#149，本机 NVMe）：种子净载荷 141.0MiB，但每次播种实际 I/O 是 **423.1MiB**
 * —— 读源 141 + 写目标 141 + deep sha256 校验重读 141，**3 倍放大**。
 * `seed_model()` 端到端中位 0.317s、后端冷启到就绪 1.1~1.2s，所以 90s 余量 >98%。
 * 反推门槛：要在 90s 内完成需有效吞吐 ≥ **4.70MiB/s**；低于该量级（USB2.0 劣质 U 盘 /
 * 老 SD 卡 / SMB over VPN）才会误判失败 —— 代价只是给「重试 / 跳过」，不是白屏，可接受。
 *
 * ⚠️ `attemptsRef` 从**第 3 步挂载**起清零、每 tick **无条件 +1**（含 `backend-starting`
 * 与 `preparing` 阶段），所以这 90 秒**含后端启动耗时** —— 播种净预算 = 90 − 启动耗时。
 * ⚠️ 门槛与慢盘曲线是由实测字节数解析推演，**不是慢盘实测**（本机只有一块 NVMe）。
 * 证据：`packaging/verify/seed_timeout_slowdisk_measure.md`
 */
export const SEED_TIMEOUT_ATTEMPTS = 90

/** 后端探针信号（字段名与 `/api/asr/model-status` 的响应对齐） */
export interface ModelPhaseInput {
  /** `/api/ping` 是否通（后端进程起来了没） */
  pingOk: boolean
  /** 模型状态接口的 `ok`；null = 还没问到 */
  modelOk: boolean | null
  /** 后端是否正在后台播种（接口的 `seeding`） */
  seeding?: boolean
  /** 内置种子是否可用于播种（接口的 `seed_exists`）；false = 再等也不会好 */
  seedExists?: boolean
}

/**
 * 由后端真实信号推出第 3 步该显示什么。判定顺序即优先级。
 *
 * **不要用假进度条**（设计稿明确要求）：所以这里没有「百分比」这个概念，
 * 只表达「出现了不确定的等待」，界面显示不定长动画。所有信号都来自后端真实现状。
 *
 * `seedExists` 那一支是刻意的：接口在探测到不可用时**顺手触发**播种，而「触发」与
 * 下一次探测之间有一瞬 `ok=false` 且 `seeding` 还没置位 —— 若直接判 failed，
 * 界面会闪一下「准备失败」。有种子就说明这事能成，按「正在准备」处理。
 */
export function modelPhase(input: ModelPhaseInput, attempts = 0): ModelPhase {
  const { pingOk, modelOk, seeding = false, seedExists = false } = input
  if (!pingOk) {
    // 后端长时间起不来，与「模型不可用」一样属于要给出可执行动作的失败，
    // 而不是无限转圈 —— 无限转圈会让用户一直等下去。
    return attempts >= 60 ? 'failed' : 'backend-starting'
  }
  if (modelOk === true) return 'ready'
  if (seeding) return attempts >= SEED_TIMEOUT_ATTEMPTS ? 'failed' : 'seeding'
  if (modelOk === false) return seedExists ? 'seeding' : 'failed'
  return 'preparing'
}

/** 模型状态是否已经不需要再轮询（ready / failed 是终态；seeding 仍要继续等） */
export function phaseIsFinal(phase: ModelPhase): boolean {
  return phase === 'ready' || phase === 'failed'
}
