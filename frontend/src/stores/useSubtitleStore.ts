import { create } from 'zustand'
import type { SubtitleLine, VocabularyEntry, ASRStatus } from '../types'
import { useCourseStore } from './useCourseStore'
import { overlayBridge } from '../lib/overlayBridge'

interface SubtitleState {
  subtitleLines: SubtitleLine[]
  currentWords: { word: string; start: number; end: number; confidence: number }[]
  currentText: string
  asrStatus: ASRStatus
  asrMessage: string
  selectedWord: string | null
  wordEntry: VocabularyEntry | null
  wordNotFound: boolean
  wordCache: Map<string, VocabularyEntry | { notFound: true }>
  isWordPanelOpen: boolean
  pollInterval: number | null
  reconnectAttempts: number
  maxReconnects: number
  reconnectTimer: ReturnType<typeof setTimeout> | null
  intentionalClose: boolean
  notRunningCount: number

  connectASR: (courseId: string) => void
  disconnectASR: () => void
  retryConnect: () => void
  _startBackendASR: (courseId: string) => void
  _tryReconnect: (courseId: string) => void
  addSubtitleLine: (line: SubtitleLine) => void
  updateInterimResult: (text: string, words: SubtitleLine['words']) => void
  setASRStatus: (status: ASRStatus, message?: string) => void
  selectWord: (word: string) => Promise<void>
  closeWordPanel: () => void
  clearSubtitles: () => void
}

/**
 * 把当前字幕快照推给桌面悬浮窗
 *
 * 悬浮窗刻意不自己轮询 /api/asr/poll —— 该端点会清空队列（popleft），
 * 两个窗口同时拉会互相抢消息，字幕被拦腰截断。所以轮询者只有主窗口一个，
 * 悬浮窗纯靠这里推过去的快照渲染。
 *
 * 快照只带文本，不带 words 数组：悬浮窗是鼠标穿透的，点不了词，
 * 传 words 只会白白放大 IPC 载荷（每 500ms 一次）。
 */
function pushOverlay(get: any) {
  const s = get()
  overlayBridge.pushSubtitle({
    lines: s.subtitleLines.slice(-4).map((l: SubtitleLine) => l.text),
    currentText: s.currentText,
    status: s.asrStatus,
    message: s.asrMessage,
    courseName: useCourseStore.getState().getActiveCourse()?.name || '',
  })
}

function handleMessage(msg: any, get: any, set: any) {
  switch (msg.type) {
    case 'subtitle':
      get().addSubtitleLine(msg.data)
      break
    case 'interim':
      get().updateInterimResult(msg.data.text, msg.data.words)
      break
    case 'asr_status':
      get().setASRStatus(msg.data.status, msg.data.message)
      break
    case 'error':
      get().setASRStatus('error', msg.data.message)
      break
  }
}

export const useSubtitleStore = create<SubtitleState>((set, get) => ({
  subtitleLines: [],
  currentWords: [],
  currentText: '',
  asrStatus: 'idle',
  asrMessage: '',
  selectedWord: null,
  wordEntry: null,
  wordNotFound: false,
  wordCache: new Map(),
  isWordPanelOpen: false,
  pollInterval: null,
  reconnectAttempts: 0,
  maxReconnects: 5,
  reconnectTimer: null,
  intentionalClose: false,
  notRunningCount: 0,

  connectASR: (courseId: string) => {
    // 先断开旧连接（会设置 intentionalClose = true）
    get().disconnectASR()

    // 重置状态，标记为非主动关闭
    set({
      subtitleLines: [],
      currentText: '',
      currentWords: [],
      intentionalClose: false,
      reconnectAttempts: 0,
      notRunningCount: 0,
    })
    // 让悬浮窗同步清空上一节课的残留字幕
    pushOverlay(get)

    // 启动后端 ASR
    get()._startBackendASR(courseId)

    // 轮询
    const interval = window.setInterval(async () => {
      if (get().intentionalClose) return

      try {
        const res = await fetch(`/api/asr/poll?course_id=${courseId}`)
        if (!res.ok) {
          get()._tryReconnect(courseId)
          return
        }
        const data = await res.json()

        if (data.status === 'not_running') {
          const count = get().notRunningCount + 1
          set({ notRunningCount: count })
          // 连续 3 次 not_running，尝试重启
          if (count >= 3) {
            set({ notRunningCount: 0 })
            get()._tryReconnect(courseId)
          }
          return
        }

        // 正常运行，重置重连计数
        if (get().reconnectAttempts > 0) {
          set({ reconnectAttempts: 0 })
        }

        for (const msg of data.messages || []) {
          handleMessage(msg, get, set)
        }
      } catch {
        // 网络错误，尝试重连
        get()._tryReconnect(courseId)
      }
    }, 500)

    set({ pollInterval: interval })
  },

  _startBackendASR: (courseId: string) => {
    get().setASRStatus('listening', '正在启动识别...')
    fetch(`/api/asr/start?course_id=${courseId}`, { method: 'POST' })
      .then(() => {
        if (!get().intentionalClose) {
          set({ reconnectAttempts: 0 })
          get().setASRStatus('listening', '识别中...')
        }
      })
      .catch(() => {
        if (!get().intentionalClose) {
          get()._tryReconnect(courseId)
        }
      })
  },

  _tryReconnect: (courseId: string) => {
    if (get().intentionalClose) return
    // 已有重连定时器在等待，不重复设置
    if (get().reconnectTimer) return

    const attempts = get().reconnectAttempts
    const maxReconnects = get().maxReconnects

    if (attempts >= maxReconnects) {
      get().setASRStatus('error', '连接失败，已达最大重试次数')
      return
    }

    const delay = Math.min(1000 * Math.pow(2, attempts), 30000)
    set({ reconnectAttempts: attempts + 1 })
    get().setASRStatus('error', `连接断开，${Math.round(delay / 1000)}秒后重连 (${attempts + 1}/${maxReconnects})`)

    const timer = setTimeout(() => {
      set({ reconnectTimer: null })
      if (!get().intentionalClose) {
        get()._startBackendASR(courseId)
      }
    }, delay)
    set({ reconnectTimer: timer })
  },

  disconnectASR: () => {
    const { pollInterval, reconnectTimer } = get()
    if (pollInterval) {
      window.clearInterval(pollInterval)
    }
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
    }
    // 标记为主动关闭，阻止重连
    set({
      intentionalClose: true,
      pollInterval: null,
      reconnectTimer: null,
      asrStatus: 'idle',
      asrMessage: '',
      reconnectAttempts: 0,
      notRunningCount: 0,
    })
    pushOverlay(get)
    const courseId = useCourseStore.getState().activeCourseId
    if (courseId) {
      fetch(`/api/asr/stop?course_id=${courseId}`, { method: 'POST' }).catch(() => {})
    }
  },

  retryConnect: () => {
    const courseId = useCourseStore.getState().activeCourseId
    if (courseId) {
      get().connectASR(courseId)
    }
  },

  addSubtitleLine: (line) => {
    set((s) => ({
      subtitleLines: [...s.subtitleLines, line],
      currentText: '',
      currentWords: [],
    }))
    pushOverlay(get)
  },

  updateInterimResult: (text, words) => {
    set({ currentText: text, currentWords: words })
    pushOverlay(get)
  },

  setASRStatus: (status, message) => {
    set({ asrStatus: status, asrMessage: message || '' })
    pushOverlay(get)
  },

  selectWord: async (word) => {
    const cleanWord = word.replace(/[^a-zA-Z0-9\u4e00-\u9fff-]/g, '').toLowerCase()
    if (!cleanWord) return

    // 缓存命中
    const cached = get().wordCache.get(cleanWord)
    if (cached) {
      set({
        selectedWord: cleanWord,
        isWordPanelOpen: true,
        wordEntry: 'notFound' in cached ? null : cached,
        wordNotFound: 'notFound' in cached,
      })
      return
    }

    // 未命中：查询中
    set({ selectedWord: cleanWord, isWordPanelOpen: true, wordEntry: null, wordNotFound: false })

    try {
      const courseId = useCourseStore.getState().activeCourseId
      const res = await fetch(`/api/courses/${courseId}/vocabulary/${cleanWord}`)
      const cache = new Map(get().wordCache)
      if (res.ok) {
        const entry = await res.json()
        cache.set(cleanWord, entry)
        set({ wordEntry: entry, wordNotFound: false, wordCache: cache })
      } else {
        cache.set(cleanWord, { notFound: true } as any)
        set({ wordNotFound: true, wordCache: cache })
      }
    } catch {
      // 静默处理
    }
  },

  closeWordPanel: () => {
    set({ isWordPanelOpen: false, selectedWord: null, wordEntry: null })
  },

  clearSubtitles: () => {
    set({ subtitleLines: [], currentText: '', currentWords: [] })
    pushOverlay(get)
  },
}))
