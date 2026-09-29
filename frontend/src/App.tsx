import { useState, useEffect } from 'react'
import CourseList from './pages/CourseList'
import CourseConfig from './pages/CourseConfig'
import SubtitleOverlay from './pages/SubtitleOverlay'
import DictionaryWindow from './pages/DictionaryWindow'
import { useAppStore } from './stores/useAppStore'

export default function App() {
  const { view, courseId } = useAppStore()

  switch (view) {
    case 'course-list':
      return <CourseList />
    case 'course-config':
      return <CourseConfig />
    case 'subtitle-overlay':
      return <SubtitleOverlay />
    case 'dictionary':
      return <DictionaryWindow />
    default:
      return <CourseList />
  }
}
