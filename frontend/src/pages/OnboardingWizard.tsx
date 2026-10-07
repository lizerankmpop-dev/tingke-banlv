import { useCallback, useEffect, useRef, useState } from 'react'
import {
  PartyPopper, MonitorPlay, School, Cpu, MousePointerClick, Move, RotateCcw,
  Keyboard, BookOpenCheck, ShieldCheck, X, Loader2, CheckCircle2, AlertCircle, FileDown,
} from 'lucide-react'
import {
  HOTKEYS, STEP4_KEYS, STEP_TITLES, TOTAL_STEPS, WIZARD_COPY,
  clampStep, isFirstStep, isLastStep, modelPhase, nextStep, phaseIsFinal, prevStep, stepLabel,
  type ModelPhase,
} from '../lib/onboarding'
import { onboardingBridge, probeBackend } from '../lib/onboardingBridge'
import { hostBridge, overlayBridge } from '../lib/overlayBridge'

/**
 * 首启向导（独立窗口，路由 `#/onboarding`）
 *
 * 三条设计约束值得先说明，否则后面的代码看着像绕路：
 *
 * 1. **文案全部来自 `lib/onboarding.ts`**，本文件不出现第二份用户可见的中文句子。
 *    理由是那类「同一句话存两份然后漂移」的缺陷在本项目已经复发三次
 *    （B1 的 toast / B4 的「缩减到 N 字符」/ B2 的 aria-label），而且这里还多一层：
 *    第 6 步有三条**文案红线**（不得出现实现术语、不得说空话、必须说明怎么恢复），
 *    只有把字抽到纯 TS 模块里，测试才能 import 进来逐条断言。
 *
 * 2. **第 3 步是真实信号驱动，不造假进度条。** 后端 `/api/ping` 与
 *    `/api/asr/model-status` 各查一次，「进程没起来」和「模型没准备好」是两句
 *    不同的话，合成一个布尔就说不清了。
 *
 * 3. **第 4 步是本向导存在的理由**（设计稿原话：其他步骤都可以砍，这一步不能砍）。
 *    它讲的是最反直觉的交互——字幕条默认鼠标穿透、点不动也拖不动。
 *    用户如果没有被提前告知，第一反应一定是「这软件坏了」。
 */
export default function OnboardingWizard() {
  const [step, setStep] = useState(1)
  const [phase, setPhase] = useState<ModelPhase>('backend-starting')
  const [retryToken, setRetryToken] = useState(0)
  const [notice, setNotice] = useState<string | null>(null)
  /** null = 还没读到（IPC 未返回）；true/false = 真实值 */
  const [termAutoDegrade, setTermAutoDegrade] = useState<boolean | null>(null)
  const attemptsRef = useRef(0)

  /**
   * 埋点：每进入一步上报一次
   *
   * `stepReached` 只增不减，记录的是**走到过的最深一步**而不是当前步 ——
   * 用户在哪一步大量退出，才是判断「哪一步写太长了」的依据。
   * 上报失败静默：埋点绝不能把用户卡在向导里。
   */
  useEffect(() => {
    void onboardingBridge.reportStep(step)
  }, [step])

  // ---- 第 3 步：真实状态轮询 ----
  useEffect(() => {
    if (step !== 3) return
    let alive = true
    let timer: ReturnType<typeof setInterval> | null = null
    let done = false
    const stop = () => {
      if (timer !== null) {
        clearInterval(timer)
        timer = null
      }
    }

    attemptsRef.current = 0
    setPhase('backend-starting')

    const tick = async () => {
      if (done) return
      const probe = await probeBackend()
      if (!alive) return
      attemptsRef.current += 1
      const next = modelPhase(probe, attemptsRef.current)
      setPhase(next)
      if (phaseIsFinal(next)) {
        done = true
        stop()
      }
    }

    void tick()
    timer = setInterval(() => void tick(), 1000)
    // 首次探测可能比 setInterval 还快返回：那时 done 已经为真，
    // 但定时器是之后才赋值的，stop() 当时是空操作 —— 这里补一刀，
    // 否则会留下一个每秒空转、永远不停的定时器。
    if (done) stop()

    return () => {
      alive = false
      stop()
    }
  }, [step, retryToken])

  // 模型就绪后自动进入下一步（设计稿要求；留一点停顿让用户看到「已就绪」）
  useEffect(() => {
    if (step !== 3 || phase !== 'ready') return
    const t = setTimeout(() => setStep((s) => (s === 3 ? 4 : s)), 900)
    return () => clearTimeout(t)
  }, [step, phase])

  // ---- 第 6 步：读当前的开关真值 ----
  useEffect(() => {
    if (step !== 6 || termAutoDegrade !== null) return
    void (async () => {
      const st = await overlayBridge.getState()
      // 与面板同一口径：缺省即开启（`!== false`），不能写成 `=== true`
      setTermAutoDegrade(st ? st.settings.termAutoDegrade !== false : true)
    })()
  }, [step, termAutoDegrade])

  const goTo = useCallback((n: number) => {
    setNotice(null)
    setStep(clampStep(n))
  }, [])

  const handleSkip = useCallback(async () => {
    const state = await onboardingBridge.dismiss()
    // 主进程会销毁本窗口；若 IPC 不可用（浏览器调试）则只记一行，不假装成功
    if (!state) setNotice('当前环境不支持保存向导状态')
  }, [])

  const handleNext = useCallback(async () => {
    if (isLastStep(step)) {
      const state = await onboardingBridge.complete()
      if (!state) setNotice('当前环境不支持保存向导状态')
      return
    }
    goTo(nextStep(step))
  }, [step, goTo])

  const handleExportLogs = useCallback(async () => {
    const res = await hostBridge.exportLogs()
    if (res.ok && res.filePath) setNotice(`${WIZARD_COPY.step3Exported}${res.filePath}`)
    else if (!res.canceled) setNotice(`${WIZARD_COPY.step3ExportFailed}${res.error || '未知原因'}`)
    else setNotice(null)
  }, [])

  const toggleTermDegrade = useCallback(async () => {
    const next = !(termAutoDegrade !== false)
    setTermAutoDegrade(next) // 乐观更新：开关要跟手
    // 失败时主进程会回推真实值（与非 Electron 环境下的 null 一起由上面的 effect 兜住）
    await overlayBridge.setSettings({ termAutoDegrade: next })
  }, [termAutoDegrade])

  const step3Blocked = step === 3 && phase !== 'ready' && phase !== 'failed'
  const showNavPrev = !isFirstStep(step)

  return (
    <div style={styles.window}>
      <div style={styles.card}>
        <header style={styles.header}>
          <div style={styles.stepCounter} role="group" aria-label={stepLabel(step)}>
            <div style={styles.dots} aria-hidden="true">
              {Array.from({ length: TOTAL_STEPS }, (_, i) => (
                <span
                  key={i}
                  style={{
                    ...styles.dot,
                    ...(i + 1 === step ? styles.dotActive : null),
                    ...(i + 1 < step ? styles.dotDone : null),
                  }}
                />
              ))}
            </div>
            <span style={styles.stepText}>{stepLabel(step)}</span>
          </div>
          <button
            onClick={() => void onboardingBridge.close()}
            style={styles.close}
            aria-label={WIZARD_COPY.close}
          >
            <X size={14} />
          </button>
        </header>

        <div style={styles.body}>
          <h1 style={styles.title}>{STEP_TITLES[step - 1]}</h1>
          {step === 1 && <Step1 />}
          {step === 2 && <Step2 />}
          {step === 3 && (
            <Step3
              phase={phase}
              notice={notice}
              onRetry={() => setRetryToken((n) => n + 1)}
              onExportLogs={() => void handleExportLogs()}
              onLater={() => goTo(4)}
            />
          )}
          {step === 4 && <Step4 />}
          {step === 5 && <Step5 />}
          {step === 6 && (
            <Step6
              termAutoDegrade={termAutoDegrade !== false}
              onToggle={() => void toggleTermDegrade()}
            />
          )}
          {step !== 3 && notice && <div style={styles.notice} role="status">{notice}</div>}
        </div>

        <footer style={styles.footer}>
          <button style={styles.ghostBtn} onClick={() => void handleSkip()}>
            {isFirstStep(step) ? WIZARD_COPY.skip : WIZARD_COPY.skipAll}
          </button>
          <div style={styles.navRight}>
            {showNavPrev && (
              <button style={styles.secondaryBtn} onClick={() => goTo(prevStep(step))}>
                {WIZARD_COPY.prev}
              </button>
            )}
            <button
              style={{ ...styles.primaryBtn, ...(step3Blocked ? styles.primaryBtnDisabled : null) }}
              disabled={step3Blocked}
              onClick={() => void handleNext()}
            >
              {isFirstStep(step)
                ? WIZARD_COPY.start
                : (isLastStep(step) ? WIZARD_COPY.done : WIZARD_COPY.next)}
            </button>
          </div>
        </footer>
      </div>
    </div>
  )
}

// ============ 各步内容 ============

function Step1() {
  return (
    <>
      <p style={styles.lead}>{WIZARD_COPY.step1Lead}</p>
      <div style={styles.blockTitle}>{WIZARD_COPY.step1CanDoTitle}</div>
      <ul style={styles.list}>
        {WIZARD_COPY.step1CanDo.map((t) => <li key={t} style={styles.listItem}>{t}</li>)}
      </ul>
      <div style={styles.trust}>
        <ShieldCheck size={14} color="var(--success)" />
        <span>{WIZARD_COPY.step1Privacy}</span>
      </div>
      <div style={styles.iconHint}><PartyPopper size={13} /> {WIZARD_COPY.appName}</div>
    </>
  )
}

function Step2() {
  const cards = [
    {
      icon: <MonitorPlay size={18} color="var(--accent-light)" />,
      title: WIZARD_COPY.step2OnlineTitle,
      rows: [WIZARD_COPY.step2OnlineSource, WIZARD_COPY.step2OnlineHow, WIZARD_COPY.step2OnlineLang, WIZARD_COPY.step2OnlineSetup],
    },
    {
      icon: <School size={18} color="var(--accent-light)" />,
      title: WIZARD_COPY.step2ClassroomTitle,
      rows: [WIZARD_COPY.step2ClassroomSource, WIZARD_COPY.step2ClassroomHow, WIZARD_COPY.step2ClassroomLang, WIZARD_COPY.step2ClassroomSetup],
    },
  ]
  return (
    <>
      <div style={styles.twoCol}>
        {cards.map((c) => (
          <div key={c.title} style={styles.sceneCard}>
            <div style={styles.sceneHead}>{c.icon}<strong>{c.title}</strong></div>
            {c.rows.map((r) => <div key={r} style={styles.sceneRow}>{r}</div>)}
          </div>
        ))}
      </div>
      <div style={styles.tip}>
        <div style={styles.blockTitle}>{WIZARD_COPY.step2TipTitle}</div>
        {WIZARD_COPY.step2Tip}
      </div>
      <p style={styles.hint}>{WIZARD_COPY.step2Note}</p>
    </>
  )
}

/**
 * 第 3 步：phase → 进度文字
 *
 * 写成映射表而不是嵌套三元：`seeding` 这一档就是修复首启缺陷时加进来的，
 * 当时那串三元已经深到看不出哪个分支对应哪句话了。
 * 文案一律取自 WIZARD_COPY（组件里不写第二份中文字面量）。
 */
const STEP3_PHASE_TEXT: Record<ModelPhase, string> = {
  'backend-starting': WIZARD_COPY.step3PhaseStarting,
  preparing: WIZARD_COPY.step3PhasePreparing,
  seeding: WIZARD_COPY.step3PhaseSeeding,
  ready: WIZARD_COPY.step3PhaseReady,
  failed: WIZARD_COPY.step3FailedTitle,
}

function Step3({
  phase, notice, onRetry, onExportLogs, onLater,
}: {
  phase: ModelPhase
  notice: string | null
  onRetry: () => void
  onExportLogs: () => void
  onLater: () => void
}) {
  // phase → 进度文字的唯一映射：新增状态时只改这里 + WIZARD_COPY（别再写嵌套三元）
  const phaseText = STEP3_PHASE_TEXT[phase]

  return (
    <>
      {phase === 'failed' ? (
        <div style={styles.failBox}>
          <div style={styles.failHead}>
            <AlertCircle size={16} color="var(--danger)" />
            <strong>{WIZARD_COPY.step3FailedTitle}</strong>
          </div>
          <div style={styles.blockTitle}>{WIZARD_COPY.step3FailedHowtoTitle}</div>
          <ol style={styles.list}>
            {WIZARD_COPY.step3FailedHowto.map((t) => <li key={t} style={styles.listItem}>{t}</li>)}
          </ol>
          <div style={styles.btnRow}>
            <button style={styles.secondaryBtn} onClick={onRetry}>{WIZARD_COPY.step3Retry}</button>
            <button style={styles.secondaryBtn} onClick={onExportLogs}>
              <FileDown size={13} />{WIZARD_COPY.step3ExportLogs}
            </button>
            <button style={styles.secondaryBtn} onClick={onLater}>{WIZARD_COPY.step3Later}</button>
          </div>
        </div>
      ) : (
        <>
          <p style={styles.lead}>{WIZARD_COPY.step3Lead}</p>
          <p style={styles.hint}>{WIZARD_COPY.step3Once}</p>
          {/*
            进度条是**不定长**的：模型准备的真实进度后端没有暴露，画一个百分比
            就是编数字。设计稿明确禁止假进度条，所以这里只表达「在动」。
            aria-hidden 掉，状态由下面那行 role="status" 的文字承担。
          */}
          <div style={styles.progressTrack} aria-hidden="true">
            <span style={{ ...styles.progressBar, animation: 'onboarding-slide 1.2s ease-in-out infinite' }} />
          </div>
          <div style={styles.phaseRow} role="status">
            {phase === 'ready'
              ? <CheckCircle2 size={14} color="var(--success)" />
              : <Loader2 size={14} color="var(--accent-light)" style={{ animation: 'spin 1s linear infinite' }} />}
            <span>{phaseText}</span>
          </div>
          <div style={styles.checks}>
            <span style={styles.checkItem}><CheckCircle2 size={12} color="var(--success)" />{WIZARD_COPY.step3NoNetwork}</span>
            <span style={styles.checkItem}><CheckCircle2 size={12} color="var(--success)" />{WIZARD_COPY.step3NoManual}</span>
          </div>
        </>
      )}
      {notice && <div style={styles.notice} role="status">{notice}</div>}
    </>
  )
}

function Step4() {
  const rows = [
    {
      icon: <MousePointerClick size={15} color="var(--accent-light)" />,
      title: WIZARD_COPY.step4State1Title,
      body: WIZARD_COPY.step4State1Body,
      keys: null as string | null,
    },
    {
      icon: <Move size={15} color="var(--accent-light)" />,
      title: WIZARD_COPY.step4State2Title,
      body: WIZARD_COPY.step4State2Body,
      keys: STEP4_KEYS.adjustMode,
    },
    {
      icon: <RotateCcw size={15} color="var(--accent-light)" />,
      title: WIZARD_COPY.step4State3Title,
      body: WIZARD_COPY.step4State3Body,
      keys: STEP4_KEYS.resetPosition,
    },
  ]
  return (
    <>
      <p style={styles.lead}>{WIZARD_COPY.step4Lead}</p>
      <p style={styles.hint}>{WIZARD_COPY.step4Intro}</p>
      {rows.map((r, i) => (
        <div key={r.title} style={styles.stateCard}>
          <div style={styles.stateHead}>
            <span style={styles.stateIndex}>{i + 1}</span>
            {r.icon}
            <strong>{r.title}</strong>
          </div>
          <div style={styles.stateBody}>{r.body}</div>
          {r.keys && <div style={styles.kbdRow}><Kbd text={r.keys} /></div>}
        </div>
      ))}
      <p style={styles.hint}>
        {WIZARD_COPY.step4Footer} <Kbd text={STEP4_KEYS.toggleOverlay} />
      </p>
    </>
  )
}

function Step5() {
  return (
    <>
      <p style={styles.lead}>{WIZARD_COPY.step5Lead}</p>
      <table style={styles.table}>
        <thead>
          <tr>
            <th style={styles.th}>{WIZARD_COPY.step5ColKey}</th>
            <th style={styles.th}>{WIZARD_COPY.step5ColAction}</th>
            <th style={styles.th}>{WIZARD_COPY.step5ColNeeds}</th>
          </tr>
        </thead>
        <tbody>
          {HOTKEYS.map((h) => (
            <tr key={h.accelerator}>
              <td style={styles.td}><Kbd text={h.accelerator} /></td>
              <td style={styles.td}>{h.action}</td>
              <td style={{ ...styles.td, ...styles.tdCenter }}>
                {h.needsOverlay ? WIZARD_COPY.step5NeedsYes : WIZARD_COPY.step5NeedsNo}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p style={styles.hint}>
        <Keyboard size={12} /> {WIZARD_COPY.step5NoPermission}
      </p>
      <p style={styles.hint}>{WIZARD_COPY.step5Trouble}</p>
      <p style={styles.faint}>{WIZARD_COPY.step5ImeHint}</p>
    </>
  )
}

function Step6({
  termAutoDegrade, onToggle,
}: {
  termAutoDegrade: boolean
  onToggle: () => void
}) {
  const labelId = 'onboarding-term-toggle-label'
  return (
    <>
      <p style={styles.lead}>{WIZARD_COPY.step6Lead}</p>
      <div style={styles.wrongList}>
        {WIZARD_COPY.step6Examples.map((t) => <div key={t} style={styles.wrongItem}>{t}</div>)}
      </div>
      <p style={styles.hint}>{WIZARD_COPY.step6Why}</p>

      <div style={styles.blockTitle}>{WIZARD_COPY.step6LayersTitle}</div>
      <ol style={styles.list}>
        {WIZARD_COPY.step6Layers.map((t) => <li key={t} style={styles.listItem}>{t}</li>)}
      </ol>
      <p style={styles.hint}>{WIZARD_COPY.step6LayersNote}</p>

      <div style={styles.separator}>{WIZARD_COPY.step6ToggleSeparator}</div>

      {/*
        这个开关是真实生效的（写 overlay.json → 主进程再推给后端），不是示意图。
        可访问名用 aria-labelledby 指向可见文案，而不是再抄一份 aria-label ——
        那正是 Backlog B2 修过的坑（同一句话存两份必然漂移）。
      */}
      <div style={styles.toggleRow}>
        <div style={styles.toggleLabel}>
          <BookOpenCheck size={15} color={termAutoDegrade ? 'var(--accent-light)' : 'var(--text-muted)'} />
          <span id={labelId}>{WIZARD_COPY.step6ToggleLabel}</span>
          <span style={styles.toggleDefault}>{WIZARD_COPY.step6ToggleDefault}</span>
        </div>
        <button
          onClick={onToggle}
          role="switch"
          aria-checked={termAutoDegrade}
          aria-labelledby={labelId}
          style={{
            ...styles.toggle,
            background: termAutoDegrade ? 'var(--accent)' : 'var(--bg-tertiary)',
          }}
        >
          <span
            style={{
              ...styles.toggleKnob,
              transform: termAutoDegrade ? 'translateX(14px)' : 'translateX(0)',
            }}
          />
        </button>
      </div>

      <FaqItem title={WIZARD_COPY.step6QgTitle} body={WIZARD_COPY.step6QgBody} />
      <FaqItem title={WIZARD_COPY.step6WhatTitle} body={WIZARD_COPY.step6WhatBody} />
      <FaqItem title={WIZARD_COPY.step6CostTitle} body={WIZARD_COPY.step6CostBody} highlight />
      <FaqItem title={WIZARD_COPY.step6RecoverTitle} body={WIZARD_COPY.step6RecoverBody} />
      <FaqItem title={WIZARD_COPY.step6OffTitle} body={WIZARD_COPY.step6OffBody} />
    </>
  )
}

function FaqItem({ title, body, highlight }: { title: string; body: string; highlight?: boolean }) {
  return (
    <div style={highlight ? styles.faqWarn : styles.faq}>
      <div style={styles.faqTitle}>{title}</div>
      <div style={styles.faqBody}>{body}</div>
    </div>
  )
}

/** 快捷键标签：用语义化 <kbd>，便于读屏与后续无障碍处理 */
function Kbd({ text }: { text: string }) {
  return <kbd style={styles.kbd}>{text}</kbd>
}

// ============ 样式（对齐设计稿 §四：卡片 400px / 正文 13px / 辅助 11px）============

const styles: Record<string, React.CSSProperties> = {
  window: {
    height: '100%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: 'var(--bg-primary)',
    padding: 16,
  },
  card: {
    background: 'var(--bg-secondary)',
    borderRadius: 'var(--radius)',
    border: '1px solid var(--border)',
    boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
    width: 400,
    maxWidth: '92vw',
    maxHeight: '86vh',
    display: 'flex',
    flexDirection: 'column',
  },
  header: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    padding: '12px 16px', borderBottom: '1px solid var(--border)', flexShrink: 0,
  },
  stepCounter: { display: 'flex', alignItems: 'center', gap: 8 },
  dots: { display: 'flex', gap: 4 },
  dot: { width: 6, height: 6, borderRadius: 3, background: 'var(--border)', display: 'inline-block' },
  dotActive: { background: 'var(--accent-light)', width: 14 },
  dotDone: { background: 'var(--accent)' },
  stepText: { fontSize: 11, color: 'var(--text-muted)' },
  close: { background: 'none', color: 'var(--text-muted)', padding: 2 },
  body: { flex: 1, overflow: 'auto', padding: '16px 16px' },
  title: { fontSize: 16, fontWeight: 600, margin: '0 0 10px', lineHeight: 1.4 },
  lead: { fontSize: 13, lineHeight: 1.6, color: 'var(--text-primary)', margin: '0 0 10px' },
  hint: { fontSize: 11, lineHeight: 1.6, color: 'var(--text-muted)', margin: '8px 0' },
  faint: { fontSize: 11, lineHeight: 1.6, color: 'var(--text-muted)', margin: '8px 0 0', opacity: 0.8 },
  blockTitle: { fontSize: 13, fontWeight: 600, margin: '12px 0 6px', color: 'var(--text-secondary)' },
  list: { margin: '0 0 4px', paddingLeft: 18 },
  listItem: { fontSize: 13, lineHeight: 1.7, color: 'var(--text-primary)' },
  trust: {
    display: 'flex', gap: 8, alignItems: 'flex-start',
    fontSize: 12, lineHeight: 1.6, color: 'var(--text-secondary)',
    background: 'rgba(0,184,148,0.08)', border: '1px solid rgba(0,184,148,0.25)',
    borderRadius: 'var(--radius-sm)', padding: '8px 10px', marginTop: 12,
  },
  iconHint: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--text-muted)', marginTop: 12 },
  twoCol: { display: 'flex', gap: 10, marginBottom: 10 },
  sceneCard: {
    flex: 1, border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
    padding: '10px 10px', background: 'var(--bg-tertiary)',
  },
  sceneHead: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, marginBottom: 8 },
  sceneRow: { fontSize: 11, lineHeight: 1.6, color: 'var(--text-secondary)' },
  tip: {
    fontSize: 12, lineHeight: 1.6, color: 'var(--text-secondary)',
    background: 'var(--bg-tertiary)', borderRadius: 'var(--radius-sm)', padding: '8px 10px',
  },
  progressTrack: {
    height: 6, borderRadius: 3, background: 'var(--bg-tertiary)', overflow: 'hidden', margin: '14px 0 8px',
  },
  progressBar: {
    display: 'block', height: '100%', width: '38%', borderRadius: 3, background: 'var(--accent)',
  },
  phaseRow: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--text-primary)' },
  checks: { display: 'flex', flexDirection: 'column', gap: 4, marginTop: 12 },
  checkItem: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--text-muted)' },
  failBox: {
    border: '1px solid rgba(225,112,85,0.4)', background: 'rgba(225,112,85,0.08)',
    borderRadius: 'var(--radius-sm)', padding: '10px 12px',
  },
  failHead: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, marginBottom: 2 },
  btnRow: { display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 },
  stateCard: {
    border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
    padding: '9px 10px', marginBottom: 8, background: 'var(--bg-tertiary)',
  },
  stateHead: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, marginBottom: 4 },
  stateIndex: {
    display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
    width: 16, height: 16, borderRadius: 8, fontSize: 10,
    background: 'var(--accent)', color: '#fff', flexShrink: 0,
  },
  stateBody: { fontSize: 12, lineHeight: 1.6, color: 'var(--text-secondary)' },
  kbdRow: { marginTop: 6 },
  kbd: {
    display: 'inline-block', fontSize: 11, padding: '1px 6px', borderRadius: 4,
    background: 'var(--bg-primary)', border: '1px solid var(--border)',
    color: 'var(--text-primary)', fontFamily: 'inherit', whiteSpace: 'nowrap',
  },
  table: { width: '100%', borderCollapse: 'collapse', marginTop: 4 },
  th: {
    fontSize: 11, color: 'var(--text-muted)', textAlign: 'left',
    padding: '4px 4px', borderBottom: '1px solid var(--border)', fontWeight: 500,
  },
  td: { fontSize: 12, color: 'var(--text-secondary)', padding: '5px 4px', verticalAlign: 'top' },
  tdCenter: { textAlign: 'center', color: 'var(--text-muted)' },
  wrongList: { display: 'flex', flexDirection: 'column', gap: 3, margin: '0 0 6px' },
  wrongItem: {
    fontSize: 12, color: 'var(--text-secondary)', fontStyle: 'italic',
    paddingLeft: 10, borderLeft: '2px solid var(--border)',
  },
  separator: {
    display: 'flex', alignItems: 'center', gap: 8,
    fontSize: 11, color: 'var(--text-muted)', margin: '16px 0 10px',
    borderTop: '1px dashed var(--border)', paddingTop: 10,
  },
  toggleRow: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, marginBottom: 10,
  },
  toggleLabel: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--text-primary)' },
  toggleDefault: { fontSize: 10, color: 'var(--text-muted)' },
  toggle: {
    width: 30, height: 16, borderRadius: 9, padding: 2, display: 'flex',
    alignItems: 'center', border: 'none', flexShrink: 0, transition: 'background 0.15s',
  },
  toggleKnob: { width: 12, height: 12, borderRadius: '50%', background: '#fff', transition: 'transform 0.15s' },
  faq: {
    border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
    padding: '8px 10px', marginBottom: 6,
  },
  faqWarn: {
    border: '1px solid rgba(253,203,110,0.35)', background: 'rgba(253,203,110,0.08)',
    borderRadius: 'var(--radius-sm)', padding: '8px 10px', marginBottom: 6,
  },
  faqTitle: { fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 3 },
  faqBody: { fontSize: 12, lineHeight: 1.6, color: 'var(--text-primary)' },
  notice: { fontSize: 11, color: 'var(--accent-light)', marginTop: 10, lineHeight: 1.5, wordBreak: 'break-all' },
  footer: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '12px 16px', borderTop: '1px solid var(--border)', flexShrink: 0, gap: 8,
  },
  navRight: { display: 'flex', gap: 8 },
  ghostBtn: {
    background: 'none', color: 'var(--text-muted)', fontSize: 12, padding: '6px 4px',
  },
  secondaryBtn: {
    display: 'inline-flex', alignItems: 'center', gap: 6,
    background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
    border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
    fontSize: 12, fontWeight: 500, padding: '7px 12px',
  },
  primaryBtn: {
    background: 'var(--accent)', color: '#fff', border: 'none',
    borderRadius: 'var(--radius-sm)', fontSize: 12, fontWeight: 500, padding: '8px 16px',
  },
  primaryBtnDisabled: { opacity: 0.45, cursor: 'default' },
}
