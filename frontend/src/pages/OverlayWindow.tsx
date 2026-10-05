import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { HostStatusPayload, OverlaySubtitlePayload } from '../types'
import { hostBridge, overlayBridge } from '../lib/overlayBridge'
import { useOverlayStore } from '../stores/useOverlayStore'
import { Loader2, AlertCircle, Move, Unplug, Crosshair } from 'lucide-react'

/**
 * 桌面字幕悬浮窗（独立窗口入口：#/overlay）
 *
 * 与页面内 SubtitleOverlay 的分工：
 *   SubtitleOverlay  = 应用内工作台（控制栏 + AI 摘要/答疑 + 单词面板 + 点词查词）
 *   OverlayWindow    = 纯字幕展示面，没有控制栏、不拦截鼠标，只负责「看得见」
 * 用户切到浏览器看网课时，能看到的就是这一层。
 *
 * 数据来源：**不自己轮询**。后端 /api/asr/poll 是「取出即清空」的队列，
 * 两个窗口同时轮询会互相抢消息、字幕被腰斩。因此由主窗口（唯一轮询者）
 * 经 IPC 把快照推过来。
 *
 * 断连提示：
 *   悬浮窗是独立窗口，主窗口渲染进程崩溃时它不会消失，只会永远停在最后一句
 *   字幕上 —— 看起来像「字幕卡住了」。主进程用心跳 + render-process-gone
 *   判定后会推 overlay:host-status，这里据此显式告诉用户该怎么办。
 *
 * 样式：
 *   - 整层 pointer-events: none —— 字幕绝不能挡住用户点网课页面上的按钮
 *   - 鼠标穿透由主进程 setIgnoreMouseEvents(true, {forward:true}) 实现
 *   - 需要拖动/调参时从主应用（或 Alt+Shift+D 热键）切到「调整模式」：
 *     此时主进程会把 forward 参数摘掉，本页面随之显示顶部拖拽条
 */
export default function OverlayWindow() {
  const settings = useOverlayStore((s) => s.settings)
  const init = useOverlayStore((s) => s.init)
  const [payload, setPayload] = useState<OverlaySubtitlePayload | null>(null)
  const [host, setHost] = useState<HostStatusPayload | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const toastTimer = useRef<number | null>(null)

  // 订阅主进程推来的字幕快照
  useEffect(() => overlayBridge.onSubtitle(setPayload), [])

  // 订阅设置（字号/颜色/行数/透明度的唯一真理源在主进程）
  useEffect(() => {
    void init()
  }, [init])

  // 宿主（主窗口）存活状态
  useEffect(() => hostBridge.onHostStatus(setHost), [])

  /** 轻提示：3 秒后自动消失，不需要用户点掉（穿透窗口也点不到） */
  const showToast = useCallback((text: string) => {
    setToast(text)
    if (toastTimer.current !== null) window.clearTimeout(toastTimer.current)
    toastTimer.current = window.setTimeout(() => setToast(null), 3000)
  }, [])

  useEffect(() => () => {
    if (toastTimer.current !== null) window.clearTimeout(toastTimer.current)
  }, [])

  // 显示器插拔导致位置被主进程自动校正 → 明确告知，否则用户只会觉得「窗口自己跳了」
  useEffect(() => {
    return hostBridge.onPositionCorrected((p) => {
      showToast(`显示器变化（${p.reason}），已自动调整位置`)
    })
  }, [showToast])

  // 快捷键调字号 / 重置位置的反馈
  useEffect(() => hostBridge.onToast((p) => showToast(p.text)), [showToast])

  // 让 html/body 背景透明 —— 否则 transparent 窗口会被页面底色覆盖
  useEffect(() => {
    document.documentElement.classList.add('overlay-transparent')
    return () => document.documentElement.classList.remove('overlay-transparent')
  }, [])

  const { fontSize, color, lineCount, textBgOpacity, clickThrough } = settings

  // 历史行 + 当前行，总数不超过 lineCount
  const historical = useMemo(() => {
    const lines = payload?.lines ?? []
    const keep = Math.max(0, lineCount - 1)
    return keep > 0 ? lines.slice(-keep) : []
  }, [payload?.lines, lineCount])

  const currentText = payload?.currentText ?? ''
  const hasContent = historical.length > 0 || !!currentText

  const isListening = payload?.status === 'listening' || payload?.status === 'processing'
  const isLoading = payload?.status === 'loading'
  const hasError = payload?.status === 'error'
  const hostDown = host?.alive === false

  return (
    <div style={styles.root}>
      {/* 主窗口已断开：这比「字幕不动」有意义得多 —— 用户知道该重启了 */}
      {hostDown && (
        <div style={styles.hostDown}>
          <Unplug size={12} />
          <span style={styles.hostDownText}>
            主窗口已断开（{host?.reason}）· 字幕已停止更新，请重启听课伴侣
          </span>
        </div>
      )}

      {/* 调整模式：给用户一个明确的可拖动区域与退出提示 */}
      {!clickThrough && (
        <div style={styles.dragBar}>
          <Move size={12} />
          <span style={styles.dragText}>拖动调整位置 · Alt+Shift+D 退出调整模式</span>
        </div>
      )}

      <div style={styles.lines}>
        {historical.map((text, i) => (
          <div
            key={`${i}-${text.slice(0, 12)}`}
            style={{
              ...styles.historicalLine,
              fontSize: Math.max(12, Math.round(fontSize * 0.62)),
              color,
              // 越靠上越淡，形成自然的「正在过去」层次
              opacity: 0.28 + (0.22 * i) / Math.max(1, historical.length),
            }}
          >
            {text}
          </div>
        ))}

        {currentText ? (
          <div
            style={{
              ...styles.currentLine,
              fontSize,
              color,
              background: `rgba(0,0,0,${textBgOpacity})`,
            }}
          >
            {currentText}
          </div>
        ) : (
          !hasContent && (
            <div style={{ ...styles.hint, color }}>
              {isLoading ? (
                <>
                  <Loader2 size={16} style={styles.spin} />
                  {payload?.message || '模型加载中，请稍候...'}
                </>
              ) : isListening ? (
                <>
                  <Loader2 size={16} style={styles.spin} />
                  正在监听音频...
                </>
              ) : hasError ? (
                <>
                  <AlertCircle size={16} color="#e17055" />
                  {payload?.message || '连接错误'}
                </>
              ) : (
                <>等待音频输入...</>
              )}
            </div>
          )
        )}
      </div>

      {/* 轻提示：快捷键反馈 / 位置自动校正 */}
      {toast && (
        <div style={styles.toast}>
          <Crosshair size={11} />
          <span>{toast}</span>
        </div>
      )}
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  root: {
    width: '100vw',
    height: '100vh',
    display: 'flex',
    flexDirection: 'column',
    background: 'transparent',
    overflow: 'hidden',
    // 整层不接收鼠标事件：字幕悬浮在网课页面之上时绝不能挡住点击。
    // 主进程层面的 setIgnoreMouseEvents 才是真正的穿透，这一层是双保险
    // （也覆盖「主进程尚未切换 forward 标志」的短暂窗口）。
    pointerEvents: 'none',
  },
  hostDown: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    padding: '4px 10px',
    background: 'rgba(225, 112, 85, 0.92)',
    color: '#fff',
    fontSize: 11,
    flexShrink: 0,
  },
  hostDownText: { fontSize: 11 },
  dragBar: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    padding: '3px 0',
    background: 'rgba(108, 92, 231, 0.85)',
    color: '#fff',
    fontSize: 11,
    flexShrink: 0,
    // 调整模式下唯一可拖动区域；缺失此项会导致整窗无法移动
    WebkitAppRegion: 'drag',
    pointerEvents: 'auto',
  } as React.CSSProperties,
  dragText: { fontSize: 11 },
  lines: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    justifyContent: 'flex-end',
    alignItems: 'center',
    gap: 4,
    padding: '8px 16px 12px',
    overflow: 'hidden',
  },
  historicalLine: {
    textAlign: 'center',
    lineHeight: 1.5,
    textShadow: '0 1px 6px rgba(0,0,0,0.9), 0 0 2px rgba(0,0,0,0.9)',
    maxWidth: '100%',
    wordBreak: 'break-word',
  },
  currentLine: {
    textAlign: 'center',
    fontWeight: 600,
    lineHeight: 1.55,
    padding: '6px 18px',
    borderRadius: 10,
    maxWidth: '100%',
    wordBreak: 'break-word',
    textShadow: '0 2px 8px rgba(0,0,0,0.95), 0 0 2px rgba(0,0,0,0.9)',
  },
  hint: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    fontSize: 14,
    opacity: 0.7,
    padding: '6px 14px',
    textShadow: '0 1px 6px rgba(0,0,0,0.9)',
  },
  toast: {
    position: 'absolute',
    left: '50%',
    transform: 'translateX(-50%)',
    bottom: 6,
    display: 'flex',
    alignItems: 'center',
    gap: 5,
    padding: '3px 10px',
    borderRadius: 10,
    background: 'rgba(0,0,0,0.72)',
    color: '#fff',
    fontSize: 11,
    whiteSpace: 'nowrap',
  },
  spin: { animation: 'spin 1s linear infinite' },
}
