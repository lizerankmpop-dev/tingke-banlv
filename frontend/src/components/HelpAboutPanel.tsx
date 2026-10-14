import { useEffect, useState } from 'react'
import { X, FolderOpen, FileDown, Info } from 'lucide-react'
import {
  HELP_COPY,
  PATH_FALLBACK,
  VERSION_FALLBACK,
  openLogDirMessage,
} from '../lib/helpCopy'
import { exportLogs, getDiagInfo, openLogDir } from '../lib/diagBridge'
import type { DiagInfo } from '../types'

/**
 * 帮助 / 关于面板（V6-2）
 *
 * ## 为什么做成模态而不是新页面
 *
 * 应用内的视图切换由 `useAppStore().view` 驱动（见 `App.tsx` 的说明：路由只负责
 * 区分「三个独立入口」，不承担应用内导航）。为一个帮助页加一个 view 值，就要动
 * 类型联合、`App.tsx` 的 switch，还要给它留一条「从哪儿进来、往哪儿回去」的路径。
 * 而帮助页的需求只是「随时能查一下」—— 模态不占据导航位置，也就不需要回退路径。
 *
 * ## 三处不能出错的地方（都有守卫）
 *
 * 1. **版本号必须来自主进程**。硬编码一个 `3.6.0` 就等于多一个版本号出口，
 *    且是唯一不会被任何构建步骤改写的那个（`version-consistency.test.cjs` 有守卫）。
 * 2. **打开日志目录失败必须说出来**。`shell.openPath` 失败时不抛异常、
 *    只返回错误串；不检查返回值就会变成「点了没反应」。
 * 3. **文案全部外提** 到 `lib/helpCopy.ts`，避免「常见问题」顺手写出
 *    「提示词 / token / 预算」这类实现术语（那份文案有禁词审计）。
 */
export default function HelpAboutPanel({ onClose }: { onClose: () => void }) {
  const [info, setInfo] = useState<DiagInfo | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  // 一次操作的反馈（打开目录 / 导出日志），空串 = 不显示
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)

  useEffect(() => {
    let cancelled = false
    void getDiagInfo().then((res) => {
      if (cancelled) return
      if (res) setInfo(res)
      // 取不到也要照常显示常见问题 —— 帮助页最不该「因为拿不到版本号就打不开」
      else setLoadFailed(true)
    })
    return () => {
      cancelled = true
    }
  }, [])

  // Esc 关闭：模态的基本预期，缺了用户会觉得「卡住了」
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const handleOpenLogDir = async () => {
    setNotice(openLogDirMessage(await openLogDir()))
  }

  const handleExport = async () => {
    const res = await exportLogs()
    if (res.canceled) setNotice({ tone: 'ok', text: HELP_COPY.exportLogsCanceled })
    else if (res.ok) setNotice({ tone: 'ok', text: HELP_COPY.exportLogsOk + (res.filePath || '') })
    else setNotice({ tone: 'error', text: HELP_COPY.exportLogsFailed + (res.error || '原因未知') })
  }

  return (
    <div style={styles.backdrop} onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={HELP_COPY.dialogTitleId}
        data-testid="help-about-panel"
        style={styles.panel}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={styles.head}>
          <Info size={16} color="var(--accent-light)" />
          <span id={HELP_COPY.dialogTitleId} style={styles.title}>
            {HELP_COPY.dialogTitle}
          </span>
          <button
            type="button"
            onClick={onClose}
            style={styles.closeBtn}
            aria-label={HELP_COPY.closeLabel}
            title={HELP_COPY.closeLabel}
            data-testid="help-about-close"
          >
            <X size={15} />
          </button>
        </div>

        <div style={styles.body}>
          {loadFailed && <p style={styles.warn}>{HELP_COPY.infoLoadFailed}</p>}

          <div style={styles.kv}>
            <span style={styles.kvLabel}>{HELP_COPY.versionLabel}</span>
            <span style={styles.kvValue} data-testid="help-about-version">
              {(info?.version || '').trim() || VERSION_FALLBACK}
            </span>
          </div>

          <div style={styles.kv}>
            <span style={styles.kvLabel}>{HELP_COPY.logDirLabel}</span>
            <span style={{ ...styles.kvValue, ...styles.kvPath }} data-testid="help-about-logdir">
              {info?.logDir || PATH_FALLBACK}
            </span>
          </div>

          <div style={styles.actions}>
            <button type="button" onClick={() => void handleOpenLogDir()} style={styles.actionBtn}>
              <FolderOpen size={13} /> {HELP_COPY.openLogDir}
            </button>
            <button type="button" onClick={() => void handleExport()} style={styles.actionBtn}>
              <FileDown size={13} /> {HELP_COPY.exportLogs}
            </button>
          </div>

          {notice && (
            <p
              style={notice.tone === 'ok' ? styles.noticeOk : styles.noticeError}
              data-testid="help-about-notice"
            >
              {notice.text}
            </p>
          )}

          <p style={styles.sectionTitle}>{HELP_COPY.faqTitle}</p>
          <ul style={styles.faqList}>
            {HELP_COPY.faq.map((item) => (
              <li key={item.q} style={styles.faqItem}>
                <p style={styles.faqQ}>{item.q}</p>
                <p style={styles.faqA}>{item.a}</p>
              </li>
            ))}
          </ul>
        </div>

        <p style={styles.foot}>{HELP_COPY.footNote}</p>
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  backdrop: {
    position: 'fixed',
    inset: 0,
    background: 'rgba(0,0,0,0.55)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 20,
    zIndex: 50,
  },
  panel: {
    display: 'flex',
    flexDirection: 'column',
    width: '100%',
    maxWidth: 520,
    maxHeight: '100%',
    background: 'var(--bg-primary)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius)',
    boxShadow: 'var(--shadow)',
    overflow: 'hidden',
  },
  head: {
    display: 'flex',
    alignItems: 'center',
    gap: 7,
    padding: '14px 16px 10px',
    flexShrink: 0,
  },
  title: { fontSize: 15, fontWeight: 600, flex: 1 },
  closeBtn: {
    background: 'none',
    border: 'none',
    padding: 3,
    cursor: 'pointer',
    color: 'var(--text-secondary)',
    lineHeight: 0,
  },
  body: {
    flex: 1,
    overflow: 'auto',
    padding: '0 16px 12px',
  },
  warn: {
    fontSize: 12,
    color: 'var(--warning)',
    background: 'rgba(253,203,110,0.10)',
    border: '1px solid rgba(253,203,110,0.30)',
    borderRadius: 'var(--radius-sm)',
    padding: '7px 10px',
    margin: '0 0 10px',
    lineHeight: 1.55,
  },
  kv: { display: 'flex', gap: 8, alignItems: 'flex-start', marginBottom: 6 },
  kvLabel: {
    fontSize: 12,
    color: 'var(--text-secondary)',
    width: 64,
    flexShrink: 0,
    paddingTop: 1,
  },
  kvValue: { fontSize: 12, color: 'var(--text-primary)', wordBreak: 'break-all' },
  kvPath: { fontFamily: 'Consolas, Menlo, monospace', fontSize: 11, color: 'var(--text-secondary)' },
  actions: { display: 'flex', gap: 8, margin: '10px 0 0' },
  actionBtn: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 5,
    padding: '6px 11px',
    fontSize: 12,
    background: 'var(--bg-card)',
    color: 'var(--text-primary)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius-sm)',
    cursor: 'pointer',
  },
  noticeOk: { fontSize: 11, color: 'var(--success)', margin: '8px 0 0', lineHeight: 1.5 },
  noticeError: { fontSize: 11, color: 'var(--danger)', margin: '8px 0 0', lineHeight: 1.5 },
  sectionTitle: {
    fontSize: 12,
    fontWeight: 600,
    color: 'var(--text-primary)',
    margin: '16px 0 6px',
  },
  faqList: { margin: 0, padding: 0, listStyle: 'none' },
  faqItem: { padding: '7px 0', borderTop: '1px solid var(--border)' },
  faqQ: { fontSize: 12, fontWeight: 600, color: 'var(--accent-light)', margin: '0 0 3px' },
  faqA: { fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.65, margin: 0 },
  foot: {
    fontSize: 10,
    color: 'var(--text-muted)',
    padding: '8px 16px 12px',
    margin: 0,
    lineHeight: 1.6,
    flexShrink: 0,
    borderTop: '1px solid var(--border)',
  },
}
