/**
 * Pill · 小标签
 *
 * 来源：李梓萌界面原型 v1.1（`components.jsx` 的 Pill），2026-11-12 人工转写。
 *
 * 原型的 `tone` prop 有默认值 'purple'，但**样式表里一个 tone 变体都没实现**
 * （只有 `.pill` 基础样式）—— 也就是说原型里这个组件其实只有一种外观。
 * 这里补齐四档，全部映射到主线 token。
 */

import type { ReactNode } from 'react';

export type PillTone = 'purple' | 'cyan' | 'green' | 'muted';

export interface PillProps {
  tone?: PillTone;
  children: ReactNode;
}

export function Pill({ tone = 'purple', children }: PillProps) {
  return <span className={`pill ${tone}`}>{children}</span>;
}

export default Pill;
