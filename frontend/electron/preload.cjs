/**
 * preload —— 主世界与渲染世界之间唯一的通道
 *
 * 渲染进程一律以 nodeIntegration:false + contextIsolation:true 运行，
 * 因此所有能力都必须在这里经 contextBridge 显式暴露，不在页面上放任何 Node 能力。
 *
 * 两个消费方：
 *   主应用窗口  —— getAppVersion / getBackendPort / overlay.*（控制面板 + 推送字幕）
 *   悬浮窗      —— overlay.getSettings / onSettings / onSubtitle（纯展示）
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
});
