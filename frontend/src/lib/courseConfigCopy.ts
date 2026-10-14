/**
 * 课程配置页的**默认值**与**提示条文案**（唯一来源）
 *
 * ## 为什么默认值和文案要放同一个模块、还要互相引用
 *
 * 提示条要告诉用户「这三项推荐填什么」。如果推荐值是用中文字面量写死的，
 * 它就与页面上 `useState(course?.domain || '…')` 的默认值构成**两份事实**：
 * 改了页面默认值、忘了改提示条，提示条就会**教用户一个不存在的默认值** ——
 * 与「引导里教了一个不存在的快捷键」是同一类**用户无法自救**的错误。
 *
 * 所以：默认值只定义一次（`COURSE_CONFIG_DEFAULTS`），页面引用它、文案也引用它；
 * 再用源守卫把「页面里实际写的默认值」与这里逐字对拍
 * （`tests/course-config-hint.test.mts`）。
 *
 * ## 为什么不 import react / 浏览器 API
 *
 * 本模块要能被 `node --test` 直接加载（JSX 与浏览器 API 都不在 Node 的类型擦除
 * 范围内）。所以 `localStorage` 只在函数体内惰性取用，且**访问本身也可能抛**
 * （隐私模式 / 存储被禁用），必须包一层。
 */

/** 三项配置的默认值；同时也是提示条里的「推荐值」 */
export const COURSE_CONFIG_DEFAULTS = {
  domain: '计算机科学',
  language: 'auto',
  simpMode: 'auto-simplified',
} as const

/**
 * 推荐值在界面上的**显示名**
 *
 * 注意这是 `select` 里的**标签**、不是值 —— 值到标签的映射表在
 * `CourseConfig.tsx`（`LANGUAGES` / `DOMAINS`）。所以这里也有对拍守卫，
 * 防止「改了标签、没改提示条」。
 */
export const DEFAULT_LABELS = {
  language: '自动检测',
  domain: '计算机科学',
} as const

/**
 * 提示条的全部可见文案。
 *
 * 约定：组件里**只允许**通过 `CONFIG_HINT_COPY.xxx` 取字，
 * 不得再写第二份中文字面量（有源守卫钉住，见测试文件）。
 *
 * 推荐值刻意用模板串拼接 `DEFAULT_LABELS`，这样「文案里说的值」与
 * 「页面上实际的值」**构造上就不可能不一致**。
 */
export const CONFIG_HINT_COPY = {
  /** `role="note"` 的无障碍名；缺了它读屏只念一串散句，不知道这块是什么 */
  ariaLabel: '课程配置填写提示',

  title: '第一次填？这三项这样选最省事',
  lead: '它们都会影响字幕质量，但不用纠结 —— 下面写的就是绝大多数课程的推荐值。',

  items: [
    {
      label: '识别语言',
      what: '告诉识别引擎这节课主要讲什么语言。选错会让它按错误的发音习惯去猜，专有名词错得最多。',
      recommend:
        `保持默认「${DEFAULT_LABELS.language}」：英文课、中英混杂的课都适用；`
        + '只有整节课都是单一语种、且识别明显不准时，才手动指定。',
    },
    {
      label: '课程领域',
      what: '载入这个领域的专业术语表，字幕里出现该领域的专业词时会自动匹配、收进生词本。',
      recommend:
        `按课程实际内容选，默认「${DEFAULT_LABELS.domain}」。`
        + '选错不会报错，但会引入无关的词。',
    },
    {
      label: '自定义术语',
      what: '你在这里填的词会被优先识别。人名、缩写、课程特有的说法最值得填。',
      recommend: '第一节课先留空。听下来发现哪些词被听错了，再回来补上，效果最直接。',
    },
  ],

  footNote: '关掉这条提示后就不再自动出现，不影响你随时修改上面三项。',
  dismissLabel: '关闭课程配置提示',
} as const

// ---------------------------------------------------------------- 关闭状态

/**
 * 关闭标记的存储键
 *
 * 前缀与本项目其它 localStorage 键一致（`tingkebanlv.<模块>.<用途>`）。
 * **不要改这个字符串**：改了等于把所有已关闭提示的用户重置回「未读」，
 * 他们下次进课程配置页会再看到一次（有单测把键名钉死）。
 */
export const HINT_DISMISS_KEY = 'tingkebanlv.courseConfigHint.dismissed'

/**
 * 最小存储接口：只要求 `getItem` / `setItem`。
 *
 * 刻意不用 `Storage` 类型：单测里注入一个内存实现就够了，不需要起 jsdom；
 * 而 `Storage` 的完整接口（length / key / removeItem / clear）本模块一个都不用。
 */
export interface HintStorage {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
}

/**
 * 取默认存储。拿不到时返回 `null`，调用方按「未关闭」处理。
 *
 * 宁可把提示**多显示一次**（代价：用户点一下关闭），也不要让一条附加提示
 * 因为存储不可用而把整个页面搞崩 —— 这类「辅助能力拖垮主流程」本项目犯过。
 */
export function getDefaultHintStorage(): HintStorage | null {
  try {
    const g = globalThis as unknown as { localStorage?: HintStorage }
    return g.localStorage ?? null
  } catch {
    return null
  }
}

/** 是否已关闭过（读不到 / 读失败一律按「未关闭」） */
export function isHintDismissed(storage: HintStorage | null = getDefaultHintStorage()): boolean {
  if (!storage) return false
  try {
    return storage.getItem(HINT_DISMISS_KEY) === '1'
  } catch {
    return false
  }
}

/** 写入关闭标记；返回是否写入成功（失败不抛，调用方无需 try/catch） */
export function dismissHint(storage: HintStorage | null = getDefaultHintStorage()): boolean {
  if (!storage) return false
  try {
    storage.setItem(HINT_DISMISS_KEY, '1')
    return true
  } catch {
    return false
  }
}
