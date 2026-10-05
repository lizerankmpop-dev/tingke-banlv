import { useEffect, useState } from 'react'
import { X, Eye, Move, RotateCcw, AlertCircle, PictureInPicture2, Type } from 'lucide-react'
import { useOverlayStore } from '../stores/useOverlayStore'

/**
 * 悬浮窗控制面板
 *
 * 注意这里**没有**「拖动位置」这种直接操作：悬浮窗鼠标穿透，本身就点不到，
 * 自然也没法在主应用里模拟拖拽。位置调整走两条路 ——
 *   1. 「调整模式」按钮（等价 Alt+Shift+D）临时关掉穿透，用户在悬浮窗上直接拖
 *   2. 「重置位置」一键挪回屏幕底部居中（窗口被拖丢时的自救手段）
 */
const PRESET_COLORS = ['#ffffff', '#ffeaa7', '#a29bfe', '#74b9ff', '#55efc4', '#fab1a0']

export default function OverlayControlPanel({ onClose }: { onClose: () => void }) {
  const { available, open, settings, loading, error, init, patch, toggle, resetPosition } = useOverlayStore()
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    void init()
  }, [init])

  const set = (p: Parameters<typeof patch>[0]) => void patch(p)

  const handleToggle = async () => {
    setBusy(true)
    await toggle()
    setBusy(false)
  }

  return (
    <div style={styles.backdrop} onClick={onClose}>
      <div style={styles.card} onClick={(e) => e.stopPropagation()}>
        <div style={styles.header}>
          <div style={styles.headerLeft}>
            <PictureInPicture2 size={16} color="var(--accent-light)" />
            <span style={styles.title}>桌面字幕悬浮窗</span>
          </div>
          <button onClick={onClose} style={styles.close}><X size={14} /></button>
        </div>

        <div style={styles.body}>
          {!available ? (
            <div style={styles.notice}>
              <AlertCircle size={16} color="var(--warning)" />
              <div>
                当前不在桌面客户端中运行，悬浮窗不可用。
                <div style={styles.noticeSub}>请通过「听课伴侣」应用启动后再试。</div>
              </div>
            </div>
          ) : (
            <>
              {/* 开关 */}
              <div style={styles.row}>
                <div style={styles.rowLabel}>
                  <Eye size={13} />
                  <span>显示悬浮窗</span>
                </div>
                <button
                  onClick={handleToggle}
                  disabled={busy || loading}
                  style={{
                    ...styles.switch,
                    background: open ? 'var(--accent)' : 'var(--bg-tertiary)',
                    opacity: busy || loading ? 0.5 : 1,
                  }}
                >
                  <span style={{ ...styles.knob, transform: open ? 'translateX(16px)' : 'translateX(0)' }} />
                </button>
              </div>
              <div style={styles.hint}>
                开启后可切到浏览器看网课，字幕会一直浮在最上层。热键 <b>Alt+Shift+O</b> 随时开关。
              </div>

              {error && <div style={styles.error}>{error}</div>}

              <Divider />

              {/* 字号 */}
              <SliderRow
                icon={<Type size={13} />}
                label="字号"
                value={settings.fontSize}
                min={12} max={72} step={1}
                suffix=" px"
                onChange={(v) => set({ fontSize: v })}
              />

              {/* 显示行数 */}
              <SliderRow
                label="显示行数"
                value={settings.lineCount}
                min={1} max={6} step={1}
                suffix=" 行"
                onChange={(v) => set({ lineCount: v })}
              />

              {/* 当前句底色 */}
              <SliderRow
                label="当前句底色"
                value={Math.round(settings.textBgOpacity * 100)}
                min={0} max={100} step={5}
                suffix=" %"
                onChange={(v) => set({ textBgOpacity: v / 100 })}
              />

              {/* 整体透明度 */}
              <SliderRow
                label="整体透明度"
                value={Math.round(settings.windowOpacity * 100)}
                min={20} max={100} step={5}
                suffix=" %"
                onChange={(v) => set({ windowOpacity: v / 100 })}
              />

              {/* 颜色 */}
              <div style={styles.row}>
                <span style={styles.label}>字幕颜色</span>
                <div style={styles.swatches}>
                  {PRESET_COLORS.map((c) => (
                    <button
                      key={c}
                      onClick={() => set({ color: c })}
                      title={c}
                      style={{
                        ...styles.swatch,
                        background: c,
                        borderColor: settings.color.toLowerCase() === c ? 'var(--accent-light)' : 'transparent',
                      }}
                    />
                  ))}
                  <input
                    type="color"
                    value={settings.color}
                    onChange={(e) => set({ color: e.target.value })}
                    style={styles.colorInput}
                    title="自定义颜色"
                  />
                </div>
              </div>

              <Divider />

              {/* 调整模式 / 重置位置 */}
              <div style={styles.btnRow}>
                <button
                  onClick={() => set({ clickThrough: !settings.clickThrough })}
                  style={{
                    ...styles.actionBtn,
                    background: settings.clickThrough ? 'var(--bg-tertiary)' : 'var(--accent-glow)',
                    color: settings.clickThrough ? 'var(--text-secondary)' : 'var(--accent-light)',
                    borderColor: settings.clickThrough ? 'var(--border)' : 'rgba(108,92,231,0.5)',
                  }}
                >
                  {settings.clickThrough ? <Eye size={13} /> : <Move size={13} />}
                  {settings.clickThrough ? '调整模式' : '结束调整'}
                </button>
                <button onClick={() => void resetPosition()} style={styles.actionBtn}>
                  <RotateCcw size={13} />
                  重置位置
                </button>
              </div>
              <div style={styles.hint}>
                {settings.clickThrough ? (
                  <>字幕默认<b>鼠标穿透</b>，不会挡住网课页面的按钮。点「调整模式」（或热键 <b>Alt+Shift+D</b>）可拖动窗口。</>
                ) : (
                  <>调整模式已开启：拖动悬浮窗顶部的紫色条移动位置，鼠标此时会恢复点击。</>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function Divider() {
  return <div style={{ height: 1, background: 'var(--border)', margin: '12px 0' }} />
}

function SliderRow({
  icon, label, value, min, max, step, suffix, onChange,
}: {
  icon?: React.ReactNode
  label: string
  value: number
  min: number
  max: number
  step: number
  suffix: string
  onChange: (v: number) => void
}) {
  return (
    <div style={styles.row}>
      <div style={styles.rowLabel}>
        {icon}
        <span>{label}</span>
      </div>
      <div style={styles.sliderWrap}>
        <input
          type="range"
          min={min} max={max} step={step}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          style={styles.slider}
        />
        <span style={styles.sliderValue}>{value}{suffix}</span>
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  backdrop: {
    position: 'fixed', inset: 0,
    background: 'rgba(0,0,0,0.55)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    zIndex: 300,
  },
  card: {
    background: 'var(--bg-secondary)',
    borderRadius: 'var(--radius)',
    border: '1px solid var(--border)',
    boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
    width: 400, maxWidth: '92vw', maxHeight: '86vh',
    display: 'flex', flexDirection: 'column',
  },
  header: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    padding: '12px 16px', borderBottom: '1px solid var(--border)', flexShrink: 0,
  },
  headerLeft: { display: 'flex', alignItems: 'center', gap: 8 },
  title: { fontSize: 14, fontWeight: 600 },
  close: { background: 'none', color: 'var(--text-muted)', padding: 2 },
  body: { flex: 1, overflow: 'auto', padding: '14px 16px' },
  row: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    gap: 12, marginBottom: 10,
  },
  rowLabel: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--text-secondary)' },
  label: { fontSize: 13, color: 'var(--text-secondary)' },
  switch: {
    position: 'relative',
    width: 38, height: 22, borderRadius: 11,
    border: 'none', padding: 0, flexShrink: 0,
    transition: 'background 0.2s',
  },
  knob: {
    position: 'absolute', top: 3, left: 3,
    width: 16, height: 16, borderRadius: 8,
    background: '#fff', transition: 'transform 0.2s',
  },
  hint: { fontSize: 11, color: 'var(--text-muted)', lineHeight: 1.6, marginBottom: 8 },
  error: { fontSize: 12, color: 'var(--danger)', marginTop: 4 },
  notice: {
    display: 'flex', gap: 10, alignItems: 'flex-start',
    fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6,
  },
  noticeSub: { fontSize: 11, color: 'var(--text-muted)' },
  sliderWrap: { display: 'flex', alignItems: 'center', gap: 8, flex: 1, maxWidth: 220 },
  slider: { flex: 1, accentColor: 'var(--accent)', minWidth: 0 },
  sliderValue: { fontSize: 11, color: 'var(--text-muted)', width: 46, textAlign: 'right', flexShrink: 0 },
  swatches: { display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' },
  swatch: { width: 18, height: 18, borderRadius: 9, border: '2px solid transparent', padding: 0 },
  colorInput: {
    width: 26, height: 22, padding: 0, border: '1px solid var(--border)',
    borderRadius: 4, background: 'transparent', cursor: 'pointer',
  },
  btnRow: { display: 'flex', gap: 8 },
  actionBtn: {
    display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 6,
    flex: 1, padding: '8px 0',
    background: 'var(--bg-tertiary)', color: 'var(--text-secondary)',
    border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
    fontSize: 12, fontWeight: 500, cursor: 'pointer',
  },
}
