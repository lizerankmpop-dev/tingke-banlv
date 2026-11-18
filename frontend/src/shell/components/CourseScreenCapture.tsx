/**
 * 线上课「窗口画面」组件
 *
 * 用户要的不是把网址塞进 webview，而是：课程网页 / 课程 App 全屏后，
 * 把它的画面按正常比例放到听课伴侣正中间。音频继续走系统音频（loopback），
 * 本组件只负责画面，不参与识别，因此全屏 / DRM 黑屏不会拖垮字幕主链路。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertCircle, MonitorPlay, RefreshCw, Square, Video } from 'lucide-react'
import {
  captureErrorMessage,
  normalizeCaptureSource,
  preferCourseSource,
  type CaptureSource,
} from '../../lib/captureSources'

interface CourseScreenCaptureProps {
  courseName: string
  onToast: (message: string) => void
}

export function CourseScreenCapture({ courseName, onToast }: CourseScreenCaptureProps) {
  const [sources, setSources] = useState<CaptureSource[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [capturing, setCapturing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [blackFrameWarning, setBlackFrameWarning] = useState(false)

  const videoRef = useRef<HTMLVideoElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const blackTimerRef = useRef<number | null>(null)

  const clearBlackTimer = useCallback(() => {
    if (blackTimerRef.current != null) {
      window.clearTimeout(blackTimerRef.current)
      blackTimerRef.current = null
    }
  }, [])

  const stopCapture = useCallback(() => {
    clearBlackTimer()
    const stream = streamRef.current
    if (stream) stream.getTracks().forEach(track => track.stop())
    streamRef.current = null
    if (videoRef.current) videoRef.current.srcObject = null
    setCapturing(false)
    setBlackFrameWarning(false)
    void window.electronAPI?.capture?.clearSource?.()
  }, [clearBlackTimer])

  useEffect(() => () => {
    clearBlackTimer()
    const stream = streamRef.current
    if (stream) stream.getTracks().forEach(track => track.stop())
    streamRef.current = null
    void window.electronAPI?.capture?.clearSource?.()
  }, [clearBlackTimer])

  const refreshSources = useCallback(async () => {
    setBusy(true)
    try {
      const raw = await window.electronAPI?.capture?.listSources?.() ?? []
      const list = raw.map(normalizeCaptureSource).filter((item): item is CaptureSource => item != null)
      setSources(list)
      const preferred = preferCourseSource(list, selectedId)
      if (preferred) setSelectedId(preferred.id)
      setMessage(list.length ? '' : '没有找到可捕获的窗口。请先打开课程网页或课程 App。')
    } catch (error) {
      const text = captureErrorMessage(error)
      setMessage(text)
      onToast(text)
    } finally {
      setBusy(false)
    }
  }, [onToast, selectedId])

  useEffect(() => { void refreshSources() }, [refreshSources])

  const detectBlackFrame = useCallback(() => {
    const video = videoRef.current
    if (!video || !streamRef.current || video.readyState < 2) return
    try {
      const canvas = document.createElement('canvas')
      canvas.width = 32
      canvas.height = 18
      const ctx = canvas.getContext('2d')
      if (!ctx) return
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
      const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height)
      let dark = 0
      for (let i = 0; i < data.length; i += 4) {
        if (data[i] + data[i + 1] + data[i + 2] < 36) dark += 1
      }
      const ratio = dark / (data.length / 4)
      setBlackFrameWarning(ratio > 0.92)
    } catch {
      // 黑屏检测失败（例如受保护画面禁止采样）不影响捕获本身
    }
  }, [])

  const startCapture = useCallback(async () => {
    const candidate = preferCourseSource(sources, selectedId)
    if (!candidate) {
      onToast('请先打开课程网页或课程 App，再刷新窗口列表')
      return
    }
    if (!navigator.mediaDevices?.getDisplayMedia) {
      onToast('当前环境不支持窗口画面捕获，请使用打包后的桌面版')
      return
    }

    setBusy(true)
    try {
      await window.electronAPI?.capture?.selectSource?.(candidate.id)
      const stream = await navigator.mediaDevices.getDisplayMedia({
        video: { frameRate: 30 },
        audio: false,
      })
      streamRef.current = stream
      const video = videoRef.current
      if (!video) throw new Error('画面容器尚未就绪')
      video.srcObject = stream
      await video.play()

      const handleEnded = () => stopCapture()
      stream.getVideoTracks().forEach(track => track.addEventListener('ended', handleEnded, { once: true }))

      setSelectedId(candidate.id)
      setCapturing(true)
      setMessage('')
      setBlackFrameWarning(false)
      clearBlackTimer()
      blackTimerRef.current = window.setTimeout(detectBlackFrame, 2500)
    } catch (error) {
      stopCapture()
      const text = captureErrorMessage(error)
      setMessage(text)
      onToast(text)
    } finally {
      setBusy(false)
    }
  }, [clearBlackTimer, detectBlackFrame, onToast, selectedId, sources, stopCapture])

  const selected = sources.find(source => source.id === selectedId) ?? null

  return (
    <section className="web-frame capture-frame">
      <div className="web-tabs">
        <div className="web-tab" title="当前课程">
          <MonitorPlay size={12} />
          <span>{courseName}</span>
        </div>
        <div className="web-tab-spacer" />
        <span className="capture-mode-tag">窗口画面</span>
      </div>

      <div className="web-toolbar capture-toolbar">
        <select
          className="capture-source-select"
          value={selectedId}
          onChange={event => setSelectedId(event.target.value)}
          disabled={capturing || busy}
          aria-label="选择课程窗口"
        >
          {sources.length === 0 ? <option value="">未发现窗口</option> : null}
          {sources.map(source => (
            <option key={source.id} value={source.id}>
              {source.type === 'screen' ? '整个屏幕 · ' : '窗口 · '}{source.name}
            </option>
          ))}
        </select>
        {selected?.thumbnail ? (
          <img className="capture-source-thumb" src={selected.thumbnail} alt="所选窗口预览" />
        ) : null}
        <button type="button" className="web-nav" title="刷新窗口列表" onClick={() => void refreshSources()} disabled={busy}>
          <RefreshCw size={14} />
        </button>
        {capturing ? (
          <button type="button" className="btn btn-ghost btn-sm" onClick={stopCapture}>
            <Square size={13} /> 停止捕获
          </button>
        ) : (
          <button type="button" className="btn btn-primary btn-sm" onClick={() => void startCapture()} disabled={busy || !sources.length}>
            <Video size={13} /> 开始捕获
          </button>
        )}
        <span className="capture-audio-hint">音频仍走系统音频，不受画面影响</span>
      </div>

      {message ? (
        <div className="web-status" role="status">{message}</div>
      ) : null}
      {blackFrameWarning ? (
        <div className="web-status capture-warning" role="alert">
          <AlertCircle size={13} />
          <span>画面几乎全黑：该窗口可能受全屏 / DRM 保护。请改用「整个屏幕」源，或退出全屏后重试。</span>
        </div>
      ) : null}

      <div className="web-content capture-stage">
        <video
          ref={videoRef}
          className="capture-video"
          autoPlay
          muted
          playsInline
          style={{ display: capturing ? 'block' : 'none' }}
        />
        {!capturing ? (
          <div className="web-empty">
            <div className="web-empty-icon"><MonitorPlay size={22} /></div>
            <strong>选择课程网页 / 课程 App 窗口</strong>
            <span>把它全屏后点「开始捕获」，画面会等比放在这里</span>
            <span className="web-empty-hint">识别继续用系统音频，不需要把画面交给识别引擎</span>
          </div>
        ) : null}
      </div>
    </section>
  )
}
