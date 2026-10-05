import { useEffect } from 'react'
import { startHostHeartbeat } from '../lib/overlayBridge'

/**
 * 主窗口存活心跳
 *
 * 为什么需要：悬浮窗是独立窗口 + 独立渲染进程，主窗口渲染进程崩溃时它
 * **不会**跟着消失 —— 用户看到的是一块永远停在最后一句字幕、也永远不更新的
 * 浮层，看起来像「字幕卡住了」，根本想不到该重启应用。
 * 主进程据此判定并在悬浮窗上给出明确提示。
 *
 * 只在主窗口（MainApp）里调用一次；浏览器环境自动退化为 no-op。
 */
export function useHostHeartbeat(intervalMs = 2000): void {
  useEffect(() => startHostHeartbeat(intervalMs), [intervalMs])
}
