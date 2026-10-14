/**
 * preload —— 主世界与渲染世界之间唯一的通道
 *
 * 渲染进程一律以 nodeIntegration:false + contextIsolation:true 运行，
 * 因此所有能力都必须在这里经 contextBridge 显式暴露，不在页面上放任何 Node 能力。
 *
 * 两个消费方：
 *   主应用窗口  —— getAppVersion / getBackendPort / overlay.*（控制面板 + 推送字幕）
 *                 / heartbeat / host-status / 导出日志 / 性能快照
 *   悬浮窗      —— overlay.getSettings / onSettings / onSubtitle / onHostStatus
 */
const { contextBridge, ipcRenderer } = require('electron');

/** 订阅辅助：统一返回取消订阅函数，避免调用方直接碰 ipcRenderer 的内部事件对象 */
function subscribe(channel, cb) {
  const listener = (_event, payload) => cb(payload);
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.removeListener(channel, listener);
}

contextBridge.exposeInMainWorld('electronAPI', {
  getAppVersion: () => ipcRenderer.invoke('get-app-version'),
  // 后端实际监听端口（自动分配），调试时可在控制台查看
  getBackendPort: () => ipcRenderer.invoke('get-backend-port'),

  // ---- 存活心跳 / 诊断 ----
  //
  // 为什么要有心跳：悬浮窗是独立窗口，主窗口渲染进程崩掉时它并不会消失，
  // 只会永远停在最后一句字幕上 —— 看起来像「字幕不动了」，用户根本不知道
  // 该重启应用。心跳让悬浮窗能明确地说出「主窗口已断开」。
  /** 渲染进程 → 主进程（每 2s 一次，单向通知） */
  heartbeat: () => ipcRenderer.send('app:heartbeat'),
  /** 主窗口失联/恢复通知（两侧都会收到） */
  onHostStatus: (cb) => subscribe('overlay:host-status', cb),
  getHostStatus: () => ipcRenderer.invoke('app:get-host-status'),
  /** 显示器变化导致位置被自动校正时的通知（悬浮窗弹一行提示） */
  onPositionCorrected: (cb) => subscribe('overlay:position-corrected', cb),
  /** 悬浮窗轻提示（快捷键改字号 / 重置位置后的反馈） */
  onToast: (cb) => subscribe('overlay:toast', cb),
  /** 本地性能埋点快照（不上传） */
  getPerfStats: () => ipcRenderer.invoke('app:get-perf-stats'),
  /** 导出诊断日志（弹保存对话框，返回 { ok, filePath? , canceled? }） */
  exportLogs: () => ipcRenderer.invoke('app:export-logs'),

  // 桌面字幕悬浮窗
  overlay: {
    getSettings: () => ipcRenderer.invoke('overlay:get-settings'),
    setSettings: (patch) => ipcRenderer.invoke('overlay:set-settings', patch),
    getState: () => ipcRenderer.invoke('overlay:get-state'),
    open: () => ipcRenderer.invoke('overlay:open'),
    close: () => ipcRenderer.invoke('overlay:close'),
    resetPosition: () => ipcRenderer.invoke('overlay:reset-position'),

    /** 主窗口 → 悬浮窗的字幕快照（单向，不需要回执） */
    pushSubtitle: (payload) => ipcRenderer.send('overlay:subtitle', payload),

    /** 悬浮窗侧：订阅字幕快照 */
    onSubtitle: (cb) => subscribe('overlay:subtitle', cb),
    /** 两侧都用：订阅设置变更（主进程是唯一真理源） */
    onSettings: (cb) => subscribe('overlay:settings', cb),
    /** 主窗口侧：订阅悬浮窗开关状态（热键也能改，UI 必须跟着走） */
    onStateChanged: (cb) => subscribe('overlay:state', cb),
  },

  // 首启向导
  //
  // 向导是独立窗口，但状态（onboarding.json）的唯一读写点在主进程 ——
  // 渲染层只上报「用户做了什么」，不做任何持久化，避免两个窗口各记一份。
  onboarding: {
    getState: () => ipcRenderer.invoke('onboarding:get-state'),
    reportStep: (step) => ipcRenderer.invoke('onboarding:step', step),
    complete: () => ipcRenderer.invoke('onboarding:complete'),
    dismiss: () => ipcRenderer.invoke('onboarding:dismiss'),
    /** 关闭窗口但不写标记（下次启动仍会提示） */
    close: () => ipcRenderer.invoke('onboarding:close'),
    /** 「重新查看新手引导」入口 */
    reopen: () => ipcRenderer.invoke('onboarding:reopen'),
  },

  // 诊断 / 帮助（V6-2）
  //
  // 路径只由主进程算：渲染层拿不到 `dataHome()`（它不是 Electron 的 userData），
  // 自己拼会指向一个空目录。所以这里是**只读**接口，渲染层不做任何持久化。
  diag: {
    /** 版本号 / 数据目录 / 日志目录 / 兼容模式（全部主进程现算） */
    getInfo: () => ipcRenderer.invoke('diag:get-info'),
    /** 打开日志目录；返回 { ok, dir, error? }（失败带原因，界面不许静默） */
    openLogDir: () => ipcRenderer.invoke('diag:open-log-dir'),
  },
});
