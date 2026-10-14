/**
 * 可见文案审计的**共享实现**（`onboarding-copy` 与 `course-config-hint` 两处守卫共用）
 *
 * 为什么抽出来（而不是各写一份）：禁词表一旦存两份，加词时必然只改一处，
 * 另一处守卫就变成了**假守卫** —— 它仍然全绿，但守的不是同一套规则。
 * 这正是本项目复发性缺陷的病因（同一句话存两份 → 改一处必漂移），
 * 所以连测试里的禁词表也走唯一来源。
 *
 * 刻意**不 import react / 浏览器 API**：本模块要能被 `node --test` 直接加载。
 */

/** 界面可见文案里禁止出现的实现术语（与控制面板 B4 修复后的口径一致） */
export const FORBIDDEN_TERMS = /token|Token|预算|截断|提示词|initial_prompt/

/** 不承诺具体行为、用户无法据此做决策的含糊说法 */
export const VAGUE_TERMS = /智能降级|智能地|自动优化/

export type CopyNode = string | readonly string[] | readonly Record<string, unknown>
export type CopyTree = Record<string, CopyNode>

/**
 * 遍历文案树，返回问题清单（空数组 = 通过）。
 *
 * `forbidden` 允许注入：变异用例要塞进**任意**禁词来验证审计没有漏检。
 * 注意 VAGUE_TERMS 是固定的 —— 含糊说法只有一类，不需要注入。
 */
export function auditCopy(copy: CopyTree, forbidden: RegExp = FORBIDDEN_TERMS): string[] {
  const problems: string[] = []
  const walk = (path: string, node: unknown): void => {
    if (typeof node === 'string') {
      if (forbidden.test(node)) {
        problems.push(`文案 ${path} 含实现术语：${JSON.stringify(node)}`)
      }
      if (VAGUE_TERMS.test(node)) {
        problems.push(`文案 ${path} 含不承诺行为的说法：${JSON.stringify(node)}`)
      }
      return
    }
    if (Array.isArray(node)) {
      node.forEach((item, i) => walk(`${path}[${i}]`, item))
      return
    }
    if (node && typeof node === 'object') {
      for (const [k, v] of Object.entries(node)) walk(path ? `${path}.${k}` : k, v)
    }
  }
  walk('', copy)
  return problems
}
