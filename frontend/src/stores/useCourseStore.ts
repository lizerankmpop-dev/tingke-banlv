import { create } from 'zustand'
import type { Course, CourseConfig } from '../types'

interface CourseState {
  courses: Course[]
  activeCourseId: string | null
  isLoading: boolean

  // Actions
  loadCourses: () => Promise<void>
  createCourse: (data: Omit<Course, 'id' | 'createdAt' | 'updatedAt' | 'videoCount' | 'wordCount'>) => Promise<Course>
  updateCourse: (id: string, data: Partial<Course>) => Promise<void>
  deleteCourse: (id: string) => Promise<void>
  setActiveCourse: (id: string | null) => void
  getActiveCourse: () => Course | undefined
}

export const useCourseStore = create<CourseState>((set, get) => ({
  courses: [],
  activeCourseId: null,
  isLoading: false,

  loadCourses: async () => {
    set({ isLoading: true })
    try {
      const res = await fetch('/api/courses')
      const courses = await res.json()
      set({ courses, isLoading: false })
    } catch (err) {
      console.error('Failed to load courses:', err)
      set({ isLoading: false })
    }
  },

  createCourse: async (data) => {
    const res = await fetch('/api/courses', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    const course = await res.json()
    set((s) => ({ courses: [...s.courses, course] }))
    return course
  },

  updateCourse: async (id, data) => {
    await fetch(`/api/courses/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    set((s) => ({
      courses: s.courses.map((c) => (c.id === id ? { ...c, ...data, updatedAt: Date.now() } : c)),
    }))
  },

  deleteCourse: async (id) => {
    await fetch(`/api/courses/${id}`, { method: 'DELETE' })
    set((s) => ({
      courses: s.courses.filter((c) => c.id !== id),
      activeCourseId: s.activeCourseId === id ? null : s.activeCourseId,
    }))
  },

  setActiveCourse: (id) => set({ activeCourseId: id }),

  getActiveCourse: () => {
    const { courses, activeCourseId } = get()
    return courses.find((c) => c.id === activeCourseId)
  },
}))
