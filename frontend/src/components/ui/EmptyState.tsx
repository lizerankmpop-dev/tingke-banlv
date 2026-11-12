/**
 * EmptyState · 空状态
 *
 * 来源：李梓萌界面原型 v1.1（`components.jsx` 的 EmptyState），2026-11-12 人工转写。
 *
 * 原型用 `Square`（一个空方块）当图标 —— 语义上有点弱，但**它是一个占位符**，
 * 不是"没有图标"。这里改成接受 `icon` prop 传入真实图标，
 * 默认仍退回 Square（保持原型观感，不擅自引入新图标）。
 */

import type { ReactNode } from 'react';
import { Square } from 'lucide-react';

export interface EmptyStateProps {
  title: string;
  description?: string;
  /** 可选的操作区（按钮 / 链接） */
  action?: ReactNode;
  /** 自定义图标 —— 默认用 Square，与原型一致 */
  icon?: ReactNode;
}

export function EmptyState({ title, description, action, icon }: EmptyStateProps) {
  return (
    <div className="empty-state">
      <div className="empty-icon">{icon ?? <Square size={20} />}</div>
      <h3>{title}</h3>
      {description && <p>{description}</p>}
      {action}
    </div>
  );
}

export default EmptyState;
