import { useMemo } from 'react'
import type { Course, SubtitleLine, VocabularyEntry } from '../../types'

/**
 * 她的原型数据模型（data.js）↔ 主线真实数据模型的映射层。
 *
 * 「不乱合并」的边界约定：她的 JSX 结构原样保留，所有假数据字段
 * （course.type / line.en / entry.badge ...）都在这里换算成真实字段，
 * 页面组件里不出现第二种字段命名。
 */

/** 线上/线下（原型叫 type，主线叫 courseType） */
export function isOnline(course: Course | { courseType?: string }): boolean {
  return (course as Course).courseType !== 'offline'
}

/** 识别语言的显示名（主线存 code：auto/en/zh/...） */
export function languageLabel(code: string): string {
  const map: Record<string, string> = {
    auto: '自动检测',
    en: '英文',
    zh: '中文',
    ja: '日语',
    ko: '韩语',
    fr: '法语',
    de: '德语',
    es: '西班牙语',
  }
  return map[code] || code
}

/** 「刚刚 / X 分钟前 / 昨天 / M月D日」——原型课程卡上的 updatedAt 文案形态 */
export function relativeTime(ts: number, now = Date.now()): string {
  const diff = now - ts
  if (diff < 60_000) return '刚刚'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
  if (diff < 172_800_000) return '昨天'
  const d = new Date(ts)
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日`
}

/** 字幕行时间戳（毫秒）→ mm:ss，对应原型 transcript 的 time 字段 */
export function lineTime(line: SubtitleLine): string {
  const total = Math.max(0, Math.floor(line.start / 1000))
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

/** SM2 状态徽章（原型写死 'SM2 · D1'，真实按间隔换算） */
export function sm2Badge(entry: Pick<VocabularyEntry, 'interval' | 'repetitions' | 'isMemorized'>): string {
  if (entry.isMemorized) return 'SM2 · 已掌握'
  const days = entry.interval ?? 0
  if (days <= 0) return 'SM2 · 新词'
  if (days < 1) return 'SM2 · 当天'
  return `SM2 · ${Math.round(days)}天`
}

/** 课程术语表 + 已收藏生词 → 字幕高亮词表（原型 line.terms 的真实来源） */
export function highlightTerms(course: Course | null | undefined, vocab: VocabularyEntry[]): string[] {
  const custom = course?.customTerms ?? []
  const collected = vocab.map((v) => v.word)
  return Array.from(new Set([...custom, ...collected]))
}

/**
 * 字幕行 → 原型行形态。
 * 原型字段是 en/zh，但真实场景里「原文/译文」不一定是英文/中文
 * （中文课原文就是中文），所以这里映射为 orig/trans 两个语义名，
 * 页面渲染逻辑与她的 JSX 结构保持一致（先原文行、再译文行）。
 */
export interface DisplayLine {
  id: string
  time: string
  orig: string
  trans: string
  isFinal: boolean
}

export function toDisplayLine(line: SubtitleLine): DisplayLine {
  return {
    id: line.id,
    time: lineTime(line),
    orig: line.text,
    trans: line.translation ?? '',
    isFinal: line.isFinal,
  }
}
