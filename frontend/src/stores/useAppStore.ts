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
  setView: (view: AppView) => void
  setCourseId: (id: string | null) => void
  enterCourse: (id: string) => void
  startListening: () => void
  stopListening: () => void
}

export const useAppStore = create<AppState>((set, get) => ({
  view: 'course-list',
  courseId: null,
  setView: (view) => set({ view }),
  setCourseId: (id) => set({ courseId: id }),
  enterCourse: (id) => {
    set({ courseId: id, view: 'course-config' })
  },
  startListening: () => {
    set({ view: 'subtitle-overlay' })
  },
  stopListening: () => {
    set({ view: 'course-config' })
  },
}))
