const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("tiangongRuntime", {
  getSettings: () => ipcRenderer.invoke("runtime:getSettings"),
  setSettings: (settings) => ipcRenderer.invoke("runtime:setSettings", settings),
  chooseWorkspace: () => ipcRenderer.invoke("runtime:chooseWorkspace"),
  chooseWorkspaceRoot: (payload) => ipcRenderer.invoke("runtime:chooseWorkspaceRoot", payload),
  choosePersonaAvatar: () => ipcRenderer.invoke("runtime:choosePersonaAvatar"),
  chooseUserAvatar: () => ipcRenderer.invoke("runtime:chooseUserAvatar"),
  chooseChatFiles: (payload) => ipcRenderer.invoke("runtime:chooseChatFiles", payload),
  pasteChatFiles: (payload) => ipcRenderer.invoke("runtime:pasteChatFiles", payload),
  send: (payload) => ipcRenderer.invoke("runtime:send", payload),
  guide: (payload) => ipcRenderer.invoke("runtime:guide", payload),
  cancel: (payload) => ipcRenderer.invoke("runtime:cancel", payload),
  onRunStep: (handler) => {
    if (typeof handler !== "function") return () => {};
    const listener = (_event, payload) => handler(payload);
    ipcRenderer.on("runtime:run-step", listener);
    return () => ipcRenderer.removeListener("runtime:run-step", listener);
  },
  onLearningMessage: (handler) => {
    if (typeof handler !== "function") return () => {};
    const listener = (_event, payload) => handler(payload);
    ipcRenderer.on("runtime:learning-message", listener);
    return () => ipcRenderer.removeListener("runtime:learning-message", listener);
  },
  status: () => ipcRenderer.invoke("runtime:status"),
  config: () => ipcRenderer.invoke("runtime:config"),
  messageChannelStatus: () => ipcRenderer.invoke("messageChannel:status"),
  connectMessageChannel: (payload) => ipcRenderer.invoke("messageChannel:connect", payload),
  listDailyLogs: () => ipcRenderer.invoke("runtime:listDailyLogs"),
  openDailyLog: (payload) => ipcRenderer.invoke("runtime:openDailyLog", payload),
  deleteDailyLog: (payload) => ipcRenderer.invoke("runtime:deleteDailyLog", payload),
  skillsList: () => ipcRenderer.invoke("skills:list"),
  openPath: (targetPath) => ipcRenderer.invoke("runtime:openPath", targetPath),
  copyMedia: (payload) => ipcRenderer.invoke("runtime:copyMedia", payload),
  confirmLifecycleUpdate: (payload) => ipcRenderer.invoke("lifecycle:confirm", payload),
  denyLifecycleUpdate: (payload) => ipcRenderer.invoke("lifecycle:deny", payload),
  deleteLearningExperience: (payload) => ipcRenderer.invoke("learning:delete", payload),
  runLearningNow: () => ipcRenderer.invoke("learning:runNow"),
  knowledgeList: (payload) => ipcRenderer.invoke("knowledge:list", payload),
  chooseKnowledgeFiles: (payload) => ipcRenderer.invoke("knowledge:chooseFiles", payload),
  knowledgeQuery: (payload) => ipcRenderer.invoke("knowledge:query", payload),
  knowledgeExport: (payload) => ipcRenderer.invoke("knowledge:export", payload),
  knowledgeRemove: (payload) => ipcRenderer.invoke("knowledge:remove", payload)
});
