/**
 * StatusDot · 状态点
 *
 * 来源：李梓萌界面原型 v1.1（`components.jsx` 的 StatusDot），2026-11-12 人工转写。
 *
 * 原型的 tone 只有 green / cyan / muted 三档且 CSS 只实现了 cyan 与 muted
 * （green 走的是基础样式）。这里补齐为四档，并全部映射到主线 token：
 *   green → --success（在线/正常）
 *   cyan  → --accent-offline（线下课）
 *   purple→ --accent（线上课）
 *   muted → --text-muted（未激活）
 */

export type StatusDotTone = 'green' | 'cyan' | 'purple' | 'muted';

export interface StatusDotProps {
  tone?: StatusDotTone;
}

export function StatusDot({ tone = 'green' }: StatusDotProps) {
  return <span className={`status-dot ${tone}`} />;
}

export default StatusDot;
