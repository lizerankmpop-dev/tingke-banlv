import { useState } from 'react'
import { Lightbulb, X } from 'lucide-react'
import { CONFIG_HINT_COPY, dismissHint, isHintDismissed } from '../lib/courseConfigCopy'

/**
 * 课程配置页顶部的一次性提示条（V6-1）
 *
 * ## 为什么需要它
 *
 * 课程配置页有四组控件（领域 / 语言 / 字形 / 术语），但页面本身**没有任何解释**
 * —— 新用户第一眼看到的是几个陌生下拉框，最常见的反应是「随便选一个先开始」，
 * 于是把「识别语言」选成英语去听中文课，然后在第一节课就得到一屏错字幕。
 * 提示条只在第一次进入时出现一次，读完关掉，不长期占地方。
 *
 * ## 文案为什么全部外提
 *
 * 组件里的 JSX 文案**没法被 `node --test` 读出来**（JSX 不在 Node 的类型擦除
 * 范围内），写在里面就无法自动审计禁词。所以文案统一放
 * `../lib/courseConfigCopy`，这里只负责排版 —— 且有源守卫钉住
 * 「本文件里不得出现第二份文案字面量」。
 *
 * ## 无障碍
 *
 * - 用 `role="note"` 而**不是** `role="status"`：`status` 是 live region，
 *   每次挂载都会被读屏朗读，等于把一条静态说明当成动态更新播报一遍。
 * - 关闭按钮里只有一个图标、没有可访问名，所以**必须** `aria-label`。
 * - `data-testid` 是给冒烟脚本用的稳定选择器：脚本按它断言，
 *   若改用文案文本，改一句话就会把脚本一起弄红。
 */
export default function CourseConfigHint() {
  // 惰性初始化：localStorage 只在首次挂载时读一次，不在每次渲染里查
  const [dismissed, setDismissed] = useState(() => isHintDismissed())
  if (dismissed) return null

  const close = () => {
    dismissHint()
    // 写入失败（隐私模式 / 存储被禁用）也要让它消失：本次会话内不再打扰。
    // 不因为「存不下」就把用户按在提示条前面。
    setDismissed(true)
  }

  return (
    <div
      role="note"
      data-testid="course-config-hint"
      aria-label={CONFIG_HINT_COPY.ariaLabel}
      style={styles.wrap}
    >
      <button
        onClick={close}
        style={styles.close}
        aria-label={CONFIG_HINT_COPY.dismissLabel}
        title={CONFIG_HINT_COPY.dismissLabel}
      >
        <X size={14} />
      </button>

      <div style={styles.head}>
        <Lightbulb size={15} color="var(--warning)" />
        <span style={styles.title}>{CONFIG_HINT_COPY.title}</span>
      </div>

      <p style={styles.lead}>{CONFIG_HINT_COPY.lead}</p>

      <ul style={styles.list}>
        {CONFIG_HINT_COPY.items.map(item => (
          <li key={item.label} style={styles.item}>
            <span style={styles.itemLabel}>{item.label}</span>
            <span style={styles.itemWhat}>{item.what}</span>
            <span style={styles.itemRec}>{item.recommend}</span>
          </li>
        ))}
      </ul>

      <p style={styles.foot}>{CONFIG_HINT_COPY.footNote}</p>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  wrap: {
    position: 'relative',
    padding: '12px 14px',
    borderRadius: 'var(--radius)',
    background: 'var(--bg-card)',
    border: '1px solid var(--border)',
    marginBottom: 16,
  },
  close: {
    position: 'absolute',
    top: 8,
    right: 8,
    background: 'none',
    border: 'none',
    padding: 2,
    cursor: 'pointer',
    color: 'var(--text-muted)',
    lineHeight: 0,
  },
  head: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    paddingRight: 20,
  },
  title: { fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' },
  lead: {
    fontSize: 11,
    color: 'var(--text-secondary)',
    lineHeight: 1.6,
    margin: '6px 0 8px',
  },
  list: { margin: 0, padding: 0, listStyle: 'none' },
  item: {
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
    padding: '7px 0 0',
  },
  itemLabel: { fontSize: 12, fontWeight: 600, color: 'var(--accent-light)' },
  itemWhat: { fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.55 },
  itemRec: { fontSize: 11, color: 'var(--text-primary)', lineHeight: 1.55 },
  foot: {
    fontSize: 10,
    color: 'var(--text-muted)',
    margin: '10px 0 0',
    opacity: 0.85,
  },
}
