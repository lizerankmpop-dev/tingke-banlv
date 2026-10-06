import { useEffect, useMemo, useState } from 'react'
import {
  X, Eye, Move, RotateCcw, AlertCircle, PictureInPicture2, Type, Zap, FileDown,
  ShieldCheck, ShieldAlert,
} from 'lucide-react'
import { useOverlayStore } from '../stores/useOverlayStore'
import { useSubtitleStore } from '../stores/useSubtitleStore'
import { hostBridge } from '../lib/overlayBridge'
import { buildTermGuardRows } from '../lib/termGuard'

/**
 * 悬浮窗控制面板
 *
 * 注意这里**没有**「拖动位置」这种直接操作：悬浮窗鼠标穿透，本身就点不到，
 * 自然也没法在主应用里模拟拖拽。位置调整走两条路 ——
 *   1. 「调整模式」按钮（等价 Alt+Shift+D）临时关掉穿透，用户在悬浮窗上直接拖
 *   2. 「重置位置」一键挪回屏幕底部居中（窗口被拖丢时的自救手段）
 *      （另有 Alt+Shift+R 热键、Alt+Shift+] / [ 快速调字号）
 */
/**
 * 色板。
 *
 * `name` 不是写给读代码的人看的注释，而是**唯一的可访问名来源**：色块在屏幕上
 * 只是一个 18px 的小圆点，读屏拿不到任何信息（hex 值念出来也没意义），
 * 必须由数据提供一个可读名，再让 `aria-label` 与鼠标 `title` 共用同一份。
 */
const PRESET_COLORS = [
  { value: '#ffffff', name: '白色' },
  { value: '#ffeaa7', name: '米黄' },
  { value: '#a29bfe', name: '淡紫' },
  { value: '#74b9ff', name: '天蓝' },
  { value: '#55efc4', name: '薄荷绿' },
  { value: '#fab1a0', name: '珊瑚粉' },
]

/** 自定义颜色输入的可访问名（`title` 与 `aria-label` 共用同一份，不写第二遍） */
const CUSTOM_COLOR_LABEL = '自定义颜色'

/**
 * 无障碍标签的 id（可访问名的**引用目标**）。
 *
 * 开关的可见文案（「显示悬浮窗」等）本来就写在 JSX 里，若再加一份
 * `aria-label="显示悬浮窗"` 就等于同一句话存两份 —— 那正是 Backlog B1/B4 的
 * 共同成因（B1 的 toast 文案、B4 的「缩减到 N 字符」都是这么漂移的）。
 * 所以这里用 `aria-labelledby` 指过去：可访问名与屏幕上的字**永远同源**。
 *
 * id 串只写在这里、引用时用 `A11Y_ID.xxx`，于是「改名漏改一处」不可能发生；
 * 反过来「写了 labelledby 却忘了给标签挂 id」的悬空引用，
 * 由 `tests/overlay-a11y.test.cjs` 静态兜住。
 */
const A11Y_ID = {
  showOverlay: 'ov-label-show-overlay',
  backgroundUpdate: 'ov-label-bg-update',
  termAutoDegrade: 'ov-label-term-degrade',
  colorGroup: 'ov-label-color',
} as const

export default function OverlayControlPanel({ onClose }: { onClose: () => void }) {
  const { available, open, settings, loading, error, init, patch, toggle, resetPosition } = useOverlayStore()
  // 引擎侧的实时降级状态由主窗口的 500ms 轮询带回来（面板自己不发请求）
  const termGuard = useSubtitleStore((s) => s.termGuard)
  const [busy, setBusy] = useState(false)
  const [logNotice, setLogNotice] = useState<string | null>(null)
  const [resetNotice, setResetNotice] = useState<string | null>(null)

  /**
   * 重置位置。
   *
   * 反馈文案由主进程给：悬浮窗开着时它已经弹了 toast，这里拿到 null（不重复提示）；
   * 悬浮窗关着时 toast 没地方显示，主进程会回一个文案，改在面板内联展示 ——
   * 否则按钮点下去毫无反应（Backlog B1）。
   *
   * 文案**不在渲染层写第二遍**：那是 B1/B4 的共同成因（同一句话在两个地方各写一份）。
   */
  const handleResetPosition = async () => {
    setResetNotice(await resetPosition())
  }

  /**
   * 防循环降级开关的**有效值**。
   *
   * `termAutoDegrade` 在类型上是可选字段，但缺省语义是「开启」
   * （后端 `_auto_degrade` 与 overlay-settings 的 DEFAULT 都是 true）。
   * 统一用 `!== false` 归一，避免「字段缺失」被真值判断误显示成「已关闭」——
   * 那会让状态行与开关外观互相矛盾。
   */
  const termAutoDegrade = settings.termAutoDegrade !== false

  /**
   * 防循环降级的状态行（0~2 行）。
   *
   * 文案由 lib/termGuard.ts 的纯函数生成 —— 那里有单测，且明确禁止把
   * 「缩减到 N 字符」这类会随实现漂移的固定数值写进 UI。
   * 历史上这里直接渲染 `termGuard.promptChars`，P0-3 改成 token 预算后
   * 就变成了「已缩减到 794 字符」这种自相矛盾的展示（Backlog B4）。
   */
  const termGuardRows = useMemo(
    () => buildTermGuardRows(termGuard, termAutoDegrade),
    [termGuard, termAutoDegrade],
  )

  useEffect(() => {
    void init()
  }, [init])

  const set = (p: Parameters<typeof patch>[0]) => void patch(p)

  const handleToggle = async () => {
    setBusy(true)
    await toggle()
    setBusy(false)
  }

  /** 导出诊断日志：出问题时不要求用户自己去 %USERPROFILE% 找文件 */
  const exportLogs = async () => {
    const res = await hostBridge.exportLogs()
    if (res.ok) setLogNotice(`已导出：${res.filePath}`)
    else if (res.canceled) setLogNotice(null)
    else setLogNotice(`导出失败：${res.error || '未知原因'}`)
  }

  return (
    <div style={styles.backdrop} onClick={onClose}>
      <div style={styles.card} onClick={(e) => e.stopPropagation()}>
        <div style={styles.header}>
          <div style={styles.headerLeft}>
            <PictureInPicture2 size={16} color="var(--accent-light)" />
            <span style={styles.title}>桌面字幕悬浮窗</span>
          </div>
          {/* 纯图标按钮没有可见文字，必须自带可访问名，否则读屏只会念「按钮」 */}
          <button onClick={onClose} style={styles.close} aria-label="关闭控制面板">
            <X size={14} />
          </button>
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
                  {/* 这个 span 同时是「可见文案」与「可访问名来源」，两件事共用一份文字 */}
                  <span id={A11Y_ID.showOverlay}>显示悬浮窗</span>
                </div>
                <button
                  onClick={handleToggle}
                  disabled={busy || loading}
                  role="switch"
                  aria-checked={open}
                  aria-labelledby={A11Y_ID.showOverlay}
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

              {error && <div style={styles.error} role="alert">{error}</div>}

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
              {/*
                色板是**一组互斥选项**，用 role="group" + aria-labelledby 说明
                「这一排小圆点是干什么的」：否则读屏用户只会听到一串
                「白色 切换按钮 / 米黄 切换按钮 …」，不知道它们属于哪个设置。
                每个色块的名称来自 PRESET_COLORS 的 name 字段（唯一来源），
                选中状态用 aria-pressed 暴露 —— 屏幕上那个高亮边框对读屏是看不见的。
              */}
              <div style={styles.row}>
                <span id={A11Y_ID.colorGroup} style={styles.label}>字幕颜色</span>
                <div style={styles.swatches} role="group" aria-labelledby={A11Y_ID.colorGroup}>
                  {PRESET_COLORS.map((c) => {
                    const selected = settings.color.toLowerCase() === c.value
                    return (
                      <button
                        key={c.value}
                        onClick={() => set({ color: c.value })}
                        title={`${c.name} ${c.value}`}
                        aria-label={c.name}
                        aria-pressed={selected}
                        style={{
                          ...styles.swatch,
                          background: c.value,
                          borderColor: selected ? 'var(--accent-light)' : 'transparent',
                        }}
                      />
                    )
                  })}
                  <input
                    type="color"
                    value={settings.color}
                    onChange={(e) => set({ color: e.target.value })}
                    style={styles.colorInput}
                    title={CUSTOM_COLOR_LABEL}
                    aria-label={CUSTOM_COLOR_LABEL}
                  />
                </div>
              </div>

              <Divider />

              {/*
                后台持续更新开关
                这不是一个「性能选项」，而是产品权衡：
                  开（默认）= 切到浏览器看网课时字幕照常更新，代价是主窗口在后台
                               仍跑 500ms 轮询，笔记本耗电/风扇略增
                  关        = 交给 Chromium 节流，省电，但字幕会在切走后停住
                必须让用户看见代价后再选，而不是我们替他硬编码一个值。
              */}
              <div style={styles.row}>
                <div style={styles.rowLabel}>
                  <Zap size={14} color={settings.backgroundUpdate ? 'var(--accent-light)' : 'var(--text-muted)'} />
                  <span id={A11Y_ID.backgroundUpdate}>后台持续更新字幕</span>
                </div>
                <button
                  onClick={() => set({ backgroundUpdate: !settings.backgroundUpdate })}
                  style={{
                    ...styles.toggle,
                    background: settings.backgroundUpdate ? 'var(--accent)' : 'var(--bg-tertiary)',
                  }}
                  role="switch"
                  aria-checked={!!settings.backgroundUpdate}
                  aria-labelledby={A11Y_ID.backgroundUpdate}
                >
                  <span
                    style={{
                      ...styles.toggleKnob,
                      transform: settings.backgroundUpdate ? 'translateX(14px)' : 'translateX(0)',
                    }}
                  />
                </button>
              </div>
              <div style={styles.hint}>
                {settings.backgroundUpdate ? (
                  <>开启后，切到浏览器看网课字幕仍会持续更新（推荐）。代价是后台会持续识别，耗电略高。</>
                ) : (
                  <>已关闭：主窗口切到后台后字幕会暂停更新，省电。想边看网课边看字幕请打开它。</>
                )}
              </div>

              <Divider />

              {/*
                术语提示词自动降级（防重复循环兜底）

                为什么必须有这个开关：术语热词是作为 Whisper 的 initial_prompt 注入的，
                长列表会被模型当成「上文」继续往下编，实测会出现字幕重复循环
                （单次识别 RTF 从 0.42 冲到 1.18，突破实时）。

                兜底策略：检出「本段转写文本出现长距离重复」时（判据见
                backend/asr/repetition.py，默认 min_repeat_chars=35），把术语提示词的
                **token 预算**从 PROMPT_TOKEN_BUDGET 降到 DEGRADE_TOKEN_BUDGET，超出预算的
                低优先级术语被丢弃。

                注意：**不再**是「把提示词截断到 200 字符」—— 那是 P0-3 之前的做法，
                按字符砍尾巴等于砍掉优先级最高的术语。提示词字符串现在原样保留，
                变的只是组装时的预算。详见 backend/asr/prompt_budget.py。

                默认开启，但必须可关：这是个「事后反应」的补救措施，它挡不住首次发生。
                用户在专业术语密集的课上可能宁可接受偶发重复，也要保住完整术语表的识别收益。
              */}
              <div style={styles.row}>
                <div style={styles.rowLabel}>
                  <ShieldCheck
                    size={14}
                    color={termAutoDegrade ? 'var(--accent-light)' : 'var(--text-muted)'}
                  />
                  <span id={A11Y_ID.termAutoDegrade}>术语提示词自动防循环降级</span>
                </div>
                <button
                  onClick={() => set({ termAutoDegrade: !termAutoDegrade })}
                  style={{
                    ...styles.toggle,
                    background: termAutoDegrade ? 'var(--accent)' : 'var(--bg-tertiary)',
                  }}
                  role="switch"
                  aria-checked={termAutoDegrade}
                  aria-labelledby={A11Y_ID.termAutoDegrade}
                >
                  <span
                    style={{
                      ...styles.toggleKnob,
                      transform: termAutoDegrade ? 'translateX(14px)' : 'translateX(0)',
                    }}
                  />
                </button>
              </div>
              <div style={styles.hint}>
                当检测到字幕重复输出时，自动缩减参与识别的术语范围，防止识别变慢、重复字幕。
              </div>
              {termGuardRows.map((row) => (
                <div
                  key={row.text}
                  style={row.tone === 'warn' ? styles.guardWarn : styles.guardOk}
                >
                  {row.tone === 'warn' ? <ShieldAlert size={12} /> : <ShieldCheck size={12} />}
                  <span>{row.text}</span>
                </div>
              ))}

              <Divider />

              {/* 调整模式 / 重置位置 / 导出日志 */}
              <div style={styles.btnRow}>
                {/*
                  「调整模式 / 结束调整」是**命令式**按钮：标签本身随状态变化
                  （写的是「点下去会发生什么」）。这类按钮不该再加 aria-pressed ——
                  读屏会念成「结束调整，已按下」，自相矛盾。
                  aria-pressed 只用于「标签不变、只有状态在变」的按钮（如上方色板）。
                */}
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
                <button onClick={() => void handleResetPosition()} style={styles.actionBtn}>
                  <RotateCcw size={13} />
                  重置位置
                </button>
                <button onClick={() => void exportLogs()} style={styles.actionBtn}>
                  <FileDown size={13} />
                  导出日志
                </button>
              </div>
              {/*
                悬浮窗关着时 toast 无处显示 → 用同一份文案在面板内兜底。
                role="status" 让读屏在文案出现时主动念一遍：这两个 div 是异步
                塞进来的（导出要等文件对话框），没有它就只有视觉用户看得到反馈。
              */}
              {resetNotice && <div style={styles.logNotice} role="status">{resetNotice}</div>}
              {logNotice && <div style={styles.logNotice} role="status">{logNotice}</div>}
              <div style={styles.hint}>
                {settings.clickThrough ? (
                  <>字幕默认<b>鼠标穿透</b>，不会挡住网课页面的按钮。点「调整模式」（或热键 <b>Alt+Shift+D</b>）可拖动窗口。热键 <b>Alt+Shift+R</b> 可一键把窗口挪回屏幕底部居中。</>
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
  /**
   * 可见数值与可访问数值同源：屏幕上显示 `48 px`，读屏也念 `48 px`。
   *
   * 不写 `aria-valuetext={`${value}${suffix}`}` 这种第二份表达式 —— 一旦有人
   * 改了显示格式（比如加个「字体」前缀），两份就会漂移（B1/B4 的同一类问题）。
   */
  const valueText = `${value}${suffix}`
  return (
    <div style={styles.row}>
      <div style={styles.rowLabel}>
        {icon}
        <span>{label}</span>
      </div>
      <div style={styles.sliderWrap}>
        {/*
          原生 input[type=range] 的隐式 role 就是 slider、隐式值域来自 min/max，
          这里仍显式写全：一是给守卫一个可静态校验的统一形态（滑杆必须有这几个
          属性），二是 aria-valuetext 能把「48」念成「48 px」—— 单位在读屏里会丢。
          可访问名直接用 label 变量，**不再抄一份中文字面量**。
        */}
        <input
          type="range"
          min={min} max={max} step={step}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          role="slider"
          aria-label={label}
          aria-valuemin={min}
          aria-valuemax={max}
          aria-valuenow={value}
          aria-valuetext={valueText}
          style={styles.slider}
        />
        <span style={styles.sliderValue}>{valueText}</span>
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
  /**
   * 防循环状态条
   *
   * 降级已经发生时必须显眼（用户看到字幕重复会以为软件坏了），
   * 正常时只用极低对比度的一行，不抢视线。
   */
  guardOk: {
    display: 'flex', gap: 6, alignItems: 'flex-start',
    fontSize: 11, color: 'var(--text-muted)', lineHeight: 1.5,
    marginBottom: 8, opacity: 0.75,
  },
  guardWarn: {
    display: 'flex', gap: 6, alignItems: 'flex-start',
    fontSize: 11, color: 'var(--warning)', lineHeight: 1.5,
    marginBottom: 8, padding: '6px 8px', borderRadius: 6,
    background: 'rgba(253,203,110,0.10)',
    border: '1px solid rgba(253,203,110,0.35)',
  },
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
  btnRow: { display: 'flex', gap: 8, flexWrap: 'wrap' },
  toggle: {
    width: 30,
    height: 16,
    borderRadius: 9,
    padding: 2,
    display: 'flex',
    alignItems: 'center',
    transition: 'background 0.15s',
    cursor: 'pointer',
    flexShrink: 0,
  },
  toggleKnob: {
    width: 12,
    height: 12,
    borderRadius: '50%',
    background: '#fff',
    transition: 'transform 0.15s',
  },
  logNotice: {
    fontSize: 10,
    color: 'var(--accent-light)',
    marginBottom: 6,
    wordBreak: 'break-all',
    lineHeight: 1.5,
  },
  actionBtn: {
    display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 6,
    flex: 1, padding: '8px 0',
    background: 'var(--bg-tertiary)', color: 'var(--text-secondary)',
    border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
    fontSize: 12, fontWeight: 500, cursor: 'pointer',
  },
}
