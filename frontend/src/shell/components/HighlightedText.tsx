import React, { useMemo } from 'react'

/**
 * 字幕术语高亮（自李梓萌原型 components.jsx 原样转写）
 *
 * 与原型的唯一差异：命中词点击回调改为必传 —— 真实版点词要打开
 * 词典抽屉（查询 + AI 讲解），原型里 onTerm 只是切换选中的假词。
 */
export function HighlightedText({
  text,
  terms = [],
  onTerm,
}: {
  text: string
  terms?: string[]
  onTerm: (term: string) => void
}) {
  const cleanTerms = useMemo(() => terms.filter(Boolean), [terms])
  if (!text) return null
  if (!cleanTerms.length) return <>{text}</>

  const escaped = cleanTerms
    .map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    .sort((a, b) => b.length - a.length)
  const matcher = new RegExp(`(${escaped.join('|')})`, 'gi')
  const parts = text.split(matcher)

  return (
    <>
      {parts.map((part, index) => {
        const isTerm = cleanTerms.some((term) => term.toLowerCase() === part.toLowerCase())
        if (!isTerm) return <React.Fragment key={index}>{part}</React.Fragment>
        return (
          <button
            key={index}
            type="button"
            className="term"
            onClick={(event) => {
              event.stopPropagation()
              onTerm(part)
            }}
          >
            {part}
          </button>
        )
      })}
    </>
  )
}
