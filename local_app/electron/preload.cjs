const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('telecollectLocal', {
  chooseFolder: () => ipcRenderer.invoke('telecollect:choose-folder'),
});
