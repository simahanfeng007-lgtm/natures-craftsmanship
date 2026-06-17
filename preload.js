const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("tiangongRuntime", {
  getSettings: () => ipcRenderer.invoke("runtime:getSettings"),
  setSettings: (settings) => ipcRenderer.invoke("runtime:setSettings", settings),
  chooseWorkspace: () => ipcRenderer.invoke("runtime:chooseWorkspace"),
  choosePersonaAvatar: () => ipcRenderer.invoke("runtime:choosePersonaAvatar"),
  chooseUserAvatar: () => ipcRenderer.invoke("runtime:chooseUserAvatar"),
  chooseChatFiles: (payload) => ipcRenderer.invoke("runtime:chooseChatFiles", payload),
  send: (payload) => ipcRenderer.invoke("runtime:send", payload),
  onRunStep: (handler) => {
    if (typeof handler !== "function") return () => {};
    const listener = (_event, payload) => handler(payload);
    ipcRenderer.on("runtime:run-step", listener);
    return () => ipcRenderer.removeListener("runtime:run-step", listener);
  },
  status: () => ipcRenderer.invoke("runtime:status"),
  config: () => ipcRenderer.invoke("runtime:config"),
  listDailyLogs: () => ipcRenderer.invoke("runtime:listDailyLogs"),
  openDailyLog: (payload) => ipcRenderer.invoke("runtime:openDailyLog", payload),
  deleteDailyLog: (payload) => ipcRenderer.invoke("runtime:deleteDailyLog", payload),
  skillsList: () => ipcRenderer.invoke("skills:list"),
  openPath: (targetPath) => ipcRenderer.invoke("runtime:openPath", targetPath),
  confirmLifecycleUpdate: (payload) => ipcRenderer.invoke("lifecycle:confirm", payload),
  denyLifecycleUpdate: (payload) => ipcRenderer.invoke("lifecycle:deny", payload),
  knowledgeList: (payload) => ipcRenderer.invoke("knowledge:list", payload),
  chooseKnowledgeFiles: (payload) => ipcRenderer.invoke("knowledge:chooseFiles", payload),
  knowledgeQuery: (payload) => ipcRenderer.invoke("knowledge:query", payload),
  knowledgeExport: (payload) => ipcRenderer.invoke("knowledge:export", payload),
  knowledgeRemove: (payload) => ipcRenderer.invoke("knowledge:remove", payload)
});
