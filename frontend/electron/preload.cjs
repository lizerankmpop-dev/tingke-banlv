const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  getAppVersion: () => ipcRenderer.invoke('get-app-version'),
  // 后端实际监听端口（自动分配），调试时可在控制台查看
  getBackendPort: () => ipcRenderer.invoke('get-backend-port'),
});
