import { createElement, useEffect, useRef, useState } from 'react'
import type { DragEvent } from 'react'
import { readCourseURL } from '../lib/courseDrop'

const browserHelp = '该站点不支持内嵌播放时，请在系统浏览器打开，仍可识别字幕。'

function CourseGuest({ url, onStatus }: { url: string; onStatus: (text: string) => void }) {
  const guestRef = useRef<HTMLElement>(null)

  useEffect(() => {
    const guest = guestRef.current
    if (!guest) return

    const timeout = window.setTimeout(() => {
      onStatus('网页加载时间较长。' + browserHelp)
    }, 10000)
    const finish = (message: string) => {
      window.clearTimeout(timeout)
      onStatus(message)
    }
    const didLoad = () => finish('')
    const didFail = (event: Event) => {
      const { isMainFrame, errorCode } = event as Event & { isMainFrame: boolean; errorCode: number }
      if (isMainFrame && errorCode !== -3) finish('网页加载失败。' + browserHelp)
    }
    const didCrash = () => finish('网页进程已退出，请重新拖入链接。' + browserHelp)
    guest.addEventListener('did-finish-load', didLoad)
    guest.addEventListener('did-fail-load', didFail)
    guest.addEventListener('render-process-gone', didCrash)
    guest.setAttribute('src', url)

    return () => {
      window.clearTimeout(timeout)
      guest.removeEventListener('did-finish-load', didLoad)
      guest.removeEventListener('did-fail-load', didFail)
      guest.removeEventListener('render-process-gone', didCrash)
    }
  }, [url, onStatus])

  return createElement('webview', {
    ref: guestRef,
    partition: 'persist:course',
    className: 'course-webview__guest',
    title: '课程网页',
  })
}

export default function CourseWebview() {
  const [page, setPage] = useState<{ url: string; attempt: number } | null>(null)
  const [status, setStatus] = useState('')
  const [dropError, setDropError] = useState('')

  function onDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault()
    const url = readCourseURL(event.dataTransfer)
    if (!url) {
      setDropError('请拖入有效的 HTTPS 课程网页链接。')
      return
    }
    setDropError('')
    setStatus('正在加载课程网页…')
    setPage(previous => ({ url, attempt: (previous?.attempt ?? 0) + 1 }))
  }

  return (
    <section className="course-webview" aria-label="课程网页"
      onDragOver={event => { event.preventDefault(); event.dataTransfer.dropEffect = 'link' }}
      onDrop={onDrop}>
      <div className="course-webview__bar">
        <strong title="可将新链接拖到此处">课程网页</strong>
        {page && (
          <>
            <span className="course-webview__address" title={page.url}>{page.url}</span>
            <button type="button" onClick={() => {
              setPage(null)
              setStatus('')
              setDropError('')
            }}>关闭网页</button>
          </>
        )}
      </div>
      {dropError && <div className="course-webview__message" role="alert">{dropError}</div>}
      {status && <div className="course-webview__message" role="status">{status}</div>}
      {page
        ? <CourseGuest key={page.attempt} url={page.url} onStatus={setStatus} />
        : <div className="course-webview__empty">
            <strong>拖入课程网页链接</strong>
            <p>可以从浏览器地址栏的站点图标或书签栏拖入。</p>
            <p>{browserHelp}</p>
          </div>}
    </section>
  )
}
