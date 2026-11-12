/**
 * Toast · 轻提示
 *
 * 来源：李梓萌界面原型 v1.1（`components.jsx` 的 Toast），2026-11-12 人工转写。
 *
 * 原型用 `Check` 图标 + 绿色文字表达「成功」。这里保持不变，但：
 *   · `role="status"` 保留（读屏会播报，不打断用户）；
 *   · 图标颜色改走 `var(--success)`，不写死；
 *   · message 为空时**不渲染**（原型已如此）。
 */

import { Check } from 'lucide-react';

export interface ToastProps {
  message?: string | null;
}

export function Toast({ message }: ToastProps) {
  if (!message) return null;
  return (
    <div className="toast" role="status">
      <Check size={15} />
      <span>{message}</span>
    </div>
  );
}

export default Toast;
