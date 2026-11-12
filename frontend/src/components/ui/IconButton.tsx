/**
 * IconButton · 纯图标按钮
 *
 * 来源：李梓萌界面原型 v1.1（`components.jsx` 的 IconButton），2026-11-12 人工转写。
 *
 * ## 移植时的两处改动（都是补原型的缺口，不是偏离设计）
 *
 * 1. 原型的 `IconButton` 给 `label` 同时用作 `title` 与 `aria-label`，
 *    但**没有**处理「label 为空」的情况 —— 空 label 会渲染出 `aria-label=""`，
 *    对读屏软件等于「有个按钮但没名字」。这里 label 为空时**不设** aria-label，
 *    并在开发态提示（见测试）。
 * 2. 原型的 `.active` 态**没有 CSS**（传了 active 但样式表里没定义），
 *    这里补上：用主题色底 + 亮色前景，语义是「这个功能当前开着」。
 */

import type { ReactNode } from 'react';

export interface IconButtonProps {
  /** 可访问名 —— 同时用作 title 与 aria-label */
  label: string;
  onClick?: () => void;
  /** 激活态（当前开启） */
  active?: boolean;
  className?: string;
  children: ReactNode;
  disabled?: boolean;
}

export function IconButton({
  label,
  onClick,
  active = false,
  className = '',
  children,
  disabled = false,
}: IconButtonProps) {
  const hasLabel = Boolean(label && label.trim());
  return (
    <button
      type="button"
      className={`icon-btn${active ? ' active' : ''}${className ? ` ${className}` : ''}`}
      onClick={onClick}
      disabled={disabled}
      title={hasLabel ? label : undefined}
      aria-label={hasLabel ? label : undefined}
    >
      {children}
    </button>
  );
}

export default IconButton;
