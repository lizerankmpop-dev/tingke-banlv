import ReviewSession from '../components/ReviewSession'
import { useAppStore } from '../stores/useAppStore'
import { useCourseStore } from '../stores/useCourseStore'

/**
 * 复习页（应用内视图 'review'）
 *
 * 单独包一层而不是把 state 塞进 ReviewSession：复习会话要求
 * 「换课程 = 换一套队列」，而课程 id 的唯一来源是 useAppStore。
 * 让会话组件只接收 courseId 这种纯参数，它才能在测试里被单独渲染。
 */
export default function ReviewPage() {
  const { courseId, setView } = useAppStore()
  const courses = useCourseStore((s) => s.courses)
  const course = courses.find((c) => c.id === courseId)

  // 没有选课程时不应该进得来（入口都在课程上下文里），兜底回课程列表
  if (!courseId) {
    return (
      <div style={{ padding: 40, color: 'var(--text-muted)', fontSize: 14 }}>
        请先选择一门课程。
        <button
          onClick={() => setView('course-list')}
          style={{
            marginLeft: 10,
            padding: '6px 14px',
            background: 'var(--accent)',
            color: '#fff',
            borderRadius: 6,
          }}
        >
          返回课程列表
        </button>
      </div>
    )
  }

  return (
    <ReviewSession
      courseId={courseId}
      courseName={course?.name}
      onExit={() => setView('dictionary')}
    />
  )
}
