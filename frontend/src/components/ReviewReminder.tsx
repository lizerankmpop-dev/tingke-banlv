import { useCallback, useEffect, useState } from 'react'
import { vocabularyApi, type DueSummary } from '../lib/vocabularyApi'
import { useAppStore } from '../stores/useAppStore'
import { AlarmClock, X } from 'lucide-react'

/**
 * 启动复习提醒
 *
 * 为什么需要它：生词复习闭环最大的敌人不是「排程算法不准」，而是
 * **用户忘记回来复习**。没有提醒的话，next_review_at 排得再对也没人看。
 *
 * 打扰控制（遵循三条硬规则，否则提醒会变成骚扰）：
 *   1. 每次启动只查一次，不在切视图时反复请求
 *   2. 每天最多提示一次 —— 关掉后写 localStorage 标记，当天不再出现
 *   3. 只在确实有到期生词时出现，且明确写出数字，不写「你可能有词要复习」
 */

/** 本地日期戳（不能用 toISOString —— 那是 UTC，跨时区会提前/延后一天） */
function localDayKey(d = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

const DISMISS_KEY = 'tingkebanlv.dueReminder.dismissedOn'

export default function ReviewReminder() {
  const [summary, setSummary] = useState<DueSummary | null>(null)
  const [dismissed, setDismissed] = useState(true)
  const { setView, setCourseId } = useAppStore()

  useEffect(() => {
    let cancelled = false
    try {
      // 已经关过一次就不再查，省一次请求，也避免「关掉又冒出来」
      if (localStorage.getItem(DISMISS_KEY) === localDayKey()) {
        setDismissed(true)
        return
      }
    } catch {
      /* localStorage 不可用（隐私模式）：按未关闭处理，宁可提示一次 */
    }
    setDismissed(false)

    vocabularyApi
      .dueSummary()
      .then((data) => {
        if (!cancelled) setSummary(data)
      })
      .catch(() => {
        // 后端未就绪 / 请求失败时静默：提醒是附加功能，不能因此报错干扰用户
        if (!cancelled) setSummary(null)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const dismiss = useCallback(() => {
    setDismissed(true)
    try {
      localStorage.setItem(DISMISS_KEY, localDayKey())
    } catch {
      /* 存不进也无妨 */
    }
  }, [])

  if (dismissed || !summary || summary.due <= 0) return null

  const goReview = () => {
    if (summary.topCourseId) setCourseId(summary.topCourseId)
    setView('review')
    dismiss()
  }

  return (
    <div style={styles.wrap}>
      <div style={styles.iconBox}>
        <AlarmClock size={16} color="var(--accent-light)" />
      </div>
      <div style={styles.body}>
        <div style={styles.title}>今天有 {summary.due} 个词待复习</div>
        <div style={styles.sub}>
          间隔重复的关键是按时复习。已复习 {summary.reviewedToday} 次 · 生词本共{' '}
          {summary.total} 词
        </div>
      </div>
      <button onClick={goReview} style={styles.action}>
        去复习
      </button>
      <button onClick={dismiss} style={styles.close} title="今天不再提醒">
        <X size={14} />
      </button>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  wrap: {
    position: 'fixed',
    right: 18,
    bottom: 18,
    zIndex: 900,
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    maxWidth: 420,
    padding: '12px 14px',
    background: 'var(--bg-card)',
    border: '1px solid var(--border)',
    borderLeft: '3px solid var(--accent)',
    borderRadius: 'var(--radius)',
    boxShadow: 'var(--shadow)',
  },
  iconBox: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    width: 30,
    height: 30,
    borderRadius: 8,
    background: 'var(--accent-glow)',
    flexShrink: 0,
  },
  body: { flex: 1, minWidth: 0 },
  title: { fontSize: 13, fontWeight: 600 },
  sub: { fontSize: 11, color: 'var(--text-muted)', marginTop: 3, lineHeight: 1.5 },
  action: {
    padding: '7px 14px',
    background: 'var(--accent)',
    color: '#fff',
    borderRadius: 'var(--radius-sm)',
    fontSize: 12,
    fontWeight: 600,
    flexShrink: 0,
  },
  close: {
    background: 'none',
    color: 'var(--text-muted)',
    padding: 3,
    cursor: 'pointer',
    flexShrink: 0,
  },
}
