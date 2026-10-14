/**
 * 帮助 / 关于页的**全部可见文案**与纯逻辑（唯一来源，V6-2）
 *
 * ## 为什么文案要外提
 *
 * 与向导（`lib/onboarding.ts`）同一个理由：组件里的 JSX 文案**没法被
 * `node --test` 读出来**（JSX 不在 Node 的类型擦除范围内），写在里面就无法
 * 自动审计禁词。而这一页的风险最高的恰恰是文案 —— 「常见问题」最容易顺手
 * 写出「提示词太长会被截断」「token 预算」这类**实现细节**，用户看了只会
 * 以为自己配错了什么（与控制面板 B4 修复后的口径一致）。
 *
 * ## 快捷键为什么引用 HOTKEYS
 *
 * 「悬浮条找不到了」这条答疑要教用户按重置位置的热键。如果在这里再写一遍
 * 组合键字面量，就成了第二份来源 —— 而 `main.cjs` 改注册键时，
 * 这一页会继续教一个**已经不存在**的快捷键（用户按了没反应、且无法自救）。
 * 所以取 `HOTKEYS`，并让向导那份守卫（已有）覆盖到同一份值。
 *
 * 本模块**不得** import react 或任何浏览器 API：它要能被 node --test 直接加载。
 */

// ⚠️ 这个 import 刻意带 `.ts` 扩展名（全项目唯一一处）：
// 本模块要被 `node --test` 直接加载以审计文案，而 Node 的类型擦除**不重写
// 导入说明符** —— 写成 `'./onboarding'` 会直接 `ERR_MODULE_NOT_FOUND`。
// `tsconfig` 已开 `allowImportingTsExtensions`，Vite 也能解析，故无副作用。
import { HOTKEYS } from './onboarding.ts'

/**
 * 答疑里要引用的热键（**按位置**取，与 `STEP4_KEYS` 同一套路）
 *
 * 为什么不用「按动作文本模糊查找」：那样改一个字就静默返回空串，
 * 于是页面上出现「按 &#xa0; 可以把它挪回屏幕底部居中」—— 一个残缺的句子，
 * 没有任何测试会红。按位置取则相反：顺序若被改动，测试会**明确失败**并说清
 * 是哪一条（见 `tests/help-about.test.mts` 对 `action` 关键字的断言）。
 */
export const HELP_KEYS = {
  /** 「把字幕条挪回屏幕底部居中」——「字幕条找不到了」这条答疑要引用的就是它 */
  resetPosition: HOTKEYS[2],
} as const

/** 版本号取不到时的占位。写「未知」而不是空串：空白会让人以为是渲染坏了 */
export const VERSION_FALLBACK = '未知'

/** 数据目录取不到时的占位 */
export const PATH_FALLBACK = '（暂不可用）'

export const HELP_COPY = {
  /** 课程列表标题栏入口按钮的可访问名 */
  openLabel: '帮助与关于',
  openHint: '查看版本号、常见问题与日志位置',

  /** 面板（模态）的无障碍：`role="dialog"` 需要 aria-labelledby 指向标题 */
  dialogTitleId: 'help-about-title',
  dialogTitle: '帮助与关于',
  closeLabel: '关闭帮助与关于',
  closeText: '关闭',

  // ---- 版本与路径 ----
  versionLabel: '版本',
  logDirLabel: '日志目录',
  openLogDir: '打开日志目录',
  openLogDirOk: '已在文件管理器中打开日志目录。',
  /**
   * 失败文案**必须带上原因**：`shell.openPath` 失败时是静默的（把错误当字符串
   * 返回、不抛异常），只写「打开失败」会让用户和开发者都无从下手。
   */
  openLogDirFailed: '打不开日志目录：',
  exportLogs: '导出诊断日志',
  exportLogsOk: '日志已导出到：',
  exportLogsCanceled: '已取消导出。',
  exportLogsFailed: '导出失败：',
  infoLoadFailed: '诊断信息读取失败，以下内容可能不完整。',

  // ---- 常见问题 ----
  faqTitle: '常见问题',
  faq: [
    {
      q: '字幕一直不出来',
      a: '首次启动需要先准备识别模型（安装包里自带，不需要联网下载），界面会提示「正在准备识别模型」。'
        + '如果这一步卡住或提示失败，关掉应用重新打开即可重试；反复失败请导出诊断日志反馈。',
    },
    {
      q: '字幕条找不到了',
      a: `按 ${HELP_KEYS.resetPosition.accelerator} 可以把它挪回屏幕底部居中，一定会出现`
        + `（${HELP_KEYS.resetPosition.needsOverlay ? '需要先打开字幕条' : '任何窗口下都能用'}）。`,
    },
    {
      q: '快捷键按了没反应',
      a: '多半是被别的软件占用了（输入法、截图工具都常见）。应用不会弹错误框，'
        + '但导出诊断日志里能看到是哪一条注册失败 —— 把日志发过来即可更换默认组合。',
    },
    {
      q: '专业词老是识别错',
      a: '在课程配置里把这门课的专业术语填进「自定义术语」，它们会被优先识别，'
        + '并在后续识别中自动积累。领域选对也很重要 —— 选错不会报错，但会引入无关的词。',
    },
    {
      q: '在线网课听不到声音',
      a: '在线网课模式需要虚拟音频设备来捕获系统播放的声音。'
        + '没装的话，课程配置页会给出安装提示与下载地址。',
    },
    {
      q: '点字幕里的单词查不到释义',
      a: '词库里没有的词会转由 AI 讲解（默认关闭，需要你自己配置云端服务后才会联网）。'
        + '如果整个查词面板都不弹，先看日志目录里最新的日志，再导出反馈。',
    },
  ],

  /** 面板底部一句话，交代隐私口径（与向导第 1 步一致） */
  footNote: '听课伴侣默认全程本地运行、不联网；只有你主动配置了云端 AI 服务时才会联网。',
} as const

/**
 * 版本号的展示串
 *
 * 刻意做成「标签 + 值」两段而不是拼好的一句话：组件里若写成
 * `` `版本 ${v}` ``，那串中文就又成了第二份文案。
 */
export function versionLine(version: string): { label: string; value: string } {
  return {
    label: HELP_COPY.versionLabel,
    value: version.trim() || VERSION_FALLBACK,
  }
}

/**
 * 判定一次「打开日志目录」的结果该给用户看什么
 *
 * 纯函数，便于单测覆盖「成功 / 失败 / 异常值」三分支 ——
 * 尤其是 `error` 为空串这种**看起来像成功、其实是失败**的输入
 * （`shell.openPath` 成功时返回空串，失败时返回错误信息；若哪天判断写反，
 * 用户会看到「已在文件管理器中打开」但什么都没发生）。
 */
export function openLogDirMessage(result: { ok?: boolean; error?: string } | null | undefined): {
  tone: 'ok' | 'error'
  text: string
} {
  if (result && result.ok === true) {
    return { tone: 'ok', text: HELP_COPY.openLogDirOk }
  }
  const reason = result && typeof result.error === 'string' && result.error.trim()
    ? result.error.trim()
    : '原因未知'
  return { tone: 'error', text: HELP_COPY.openLogDirFailed + reason }
}
