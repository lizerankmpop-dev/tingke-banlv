import { create } from 'zustand'

export type AppView =
  | 'course-list'
  | 'course-config'
  | 'subtitle-overlay'
  | 'dictionary'
  | 'review'

interface AppState {
  view: AppView
  courseId: string | null
  /** 本次识别归属的章节（'' = 不分章节）。由课程配置页在「开始识别」前设置 */
  activeChapterId: string
  /** 章节显示名（识别页控制栏标签用；id 空串时它也无意义） */
  activeChapterName: string
  setView: (view: AppView) => void
  setCourseId: (id: string | null) => void
  enterCourse: (id: string) => void
  startListening: (chapter?: { id: string; name: string }) => void
  stopListening: () => void
}

export const useAppStore = create<AppState>((set, get) => ({
  view: 'course-list',
  courseId: null,
  activeChapterId: '',
  activeChapterName: '',
  setView: (view) => set({ view }),
  setCourseId: (id) => set({ courseId: id }),
  enterCourse: (id) => {
    set({ courseId: id, view: 'course-config' })
  },
  startListening: (chapter) => {
    set({
      view: 'subtitle-overlay',
      activeChapterId: chapter?.id || '',
      activeChapterName: chapter?.name || '',
    })
  },
  stopListening: () => {
    set({ view: 'course-config' })
  },
}))
