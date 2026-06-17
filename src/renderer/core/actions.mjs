import { backendReply, extractStatusPayload, humanizeBackendError, runtimeStatusText, spokenBackendText } from "./formatters.mjs";

function makeRequestId() {
  return `req_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

export function createActions({ runtime, state }) {
  if (runtime?.onRunStep) {
    runtime.onRunStep((event) => state.applyRunProgress(event));
  }

  async function loadSettings() {
    if (!runtime?.getSettings) return;
    const next = await runtime.getSettings();
    state.setSettings(next);
  }

  async function setMode(mode) {
    try {
      await hotSwitchSettings({ mode }, { refreshConfig: false });
    } catch {
      state.setSettings({ mode });
    }
  }

  function setActivePage(page) {
    state.setActivePage(page);
  }

  function startNewConversation() {
    state.startNewConversation();
    state.setActivePage("chat");
  }

  function switchConversation(sessionId) {
    state.switchConversation(sessionId);
    state.setActivePage("chat");
  }

  function deleteConversation(sessionId) {
    state.deleteConversation(sessionId);
    state.setActivePage("chat");
  }

  function setActiveSkillCategory(category) {
    state.setActiveSkillCategory(category);
  }

  async function saveSettings(next) {
    const normalized = { ...(next || {}) };
    if (Object.prototype.hasOwnProperty.call(normalized, "maxSteps")) {
      const current = state.snapshot().settings.maxSteps || 20;
      const parsed = Number(normalized.maxSteps || current);
      normalized.maxSteps = Number.isFinite(parsed)
        ? Math.max(1, Math.min(180, Math.round(parsed)))
        : current;
    }
    state.setSettings(normalized);
    try {
      const saved = await runtime?.setSettings?.(normalized);
      if (saved) state.setSettings(saved);
      return saved || state.snapshot().settings;
    } catch {
      return state.snapshot().settings;
    }
  }

  async function chooseWorkspace() {
    if (!runtime?.chooseWorkspace) return;
    const next = await runtime.chooseWorkspace();
    state.setSettings(next);
  }

  async function choosePersonaAvatar() {
    if (!runtime?.choosePersonaAvatar) return state.snapshot().settings;
    const next = await runtime.choosePersonaAvatar();
    if (next) state.setSettings(next);
    return next || state.snapshot().settings;
  }

  async function chooseUserAvatar() {
    if (!runtime?.chooseUserAvatar) return state.snapshot().settings;
    const next = await runtime.chooseUserAvatar();
    if (next) state.setSettings(next);
    return next || state.snapshot().settings;
  }

  async function openWorkspace() {
    const { settings } = state.snapshot();
    if (runtime?.openPath && settings.workspace) {
      await runtime.openPath(settings.workspace);
    }
  }

  async function openPath(targetPath) {
    if (runtime?.openPath && targetPath) {
      await runtime.openPath(targetPath);
    }
  }

  async function listDailyLogs() {
    if (!runtime?.listDailyLogs) return { ok: false, error: "日志桥接不可用", logs: [] };
    return runtime.listDailyLogs();
  }

  async function listSkills() {
    if (!runtime?.skillsList) {
      return { ok: false, error: "技能桥接不可用", categories: [], abilities: [], summary: {} };
    }
    return runtime.skillsList();
  }

  async function openDailyLog(date) {
    if (!runtime?.openDailyLog) return { ok: false, error: "日志打开桥接不可用" };
    const payload = typeof date === "string" ? { date } : (date || {});
    return runtime.openDailyLog(payload);
  }

  async function deleteDailyLog(date) {
    if (!runtime?.deleteDailyLog) return { ok: false, error: "日志删除桥接不可用", logs: [] };
    const payload = typeof date === "string" ? { date } : (date || {});
    return runtime.deleteDailyLog(payload);
  }

  function knowledgePayload(extra = {}) {
    const { settings } = state.snapshot();
    return { workspace: settings.workspace, ...extra };
  }

  function lifecyclePayload(extra = {}) {
    const { settings } = state.snapshot();
    return { workspace: settings.workspace, ...extra };
  }

  async function listKnowledge() {
    if (!runtime?.knowledgeList) return { ok: false, error: "知识库桥接不可用", documents: [] };
    return runtime.knowledgeList(knowledgePayload());
  }

  async function importKnowledgeFiles() {
    if (!runtime?.chooseKnowledgeFiles) return { ok: false, error: "知识库导入不可用", documents: [] };
    return runtime.chooseKnowledgeFiles(knowledgePayload());
  }

  async function chooseChatFiles() {
    if (!runtime?.chooseChatFiles) return { ok: false, error: "会话文件上传不可用", attachments: [] };
    return runtime.chooseChatFiles(knowledgePayload());
  }

  async function queryKnowledge(documentId, query, topK = 6) {
    if (!runtime?.knowledgeQuery) return { ok: false, error: "知识库查询不可用" };
    return runtime.knowledgeQuery(knowledgePayload({ document_id: documentId, query, top_k: topK }));
  }

  async function exportKnowledge(documentId, format = "md") {
    if (!runtime?.knowledgeExport) return { ok: false, error: "知识库导出不可用" };
    return runtime.knowledgeExport(knowledgePayload({ document_id: documentId, format }));
  }

  async function removeKnowledge(documentId) {
    if (!runtime?.knowledgeRemove) return { ok: false, error: "知识库删除不可用", documents: [] };
    return runtime.knowledgeRemove(knowledgePayload({ document_id: documentId }));
  }

  async function confirmLifecycleUpdate(updateId) {
    if (!runtime?.confirmLifecycleUpdate) return { ok: false, error: "生命周期确认桥接不可用" };
    const result = await runtime.confirmLifecycleUpdate(lifecyclePayload({ id: updateId, ticketId: updateId }));
    await refreshStatus();
    return result;
  }

  async function denyLifecycleUpdate(updateId) {
    if (!runtime?.denyLifecycleUpdate) return { ok: false, error: "生命周期拒绝桥接不可用" };
    const result = await runtime.denyLifecycleUpdate(lifecyclePayload({ id: updateId, ticketId: updateId }));
    await refreshStatus();
    return result;
  }

  async function refreshStatus() {
    if (!runtime?.status) {
      state.setRuntimeStatus({ text: "桌面桥接不可用", loading: false, ok: false, payload: null });
      return { ok: false, stderr: "桌面桥接不可用" };
    }

    state.setRuntimeStatus({ text: "检查中", loading: true, ok: null });
    const result = await runtime.status();
    state.setRuntimeStatus({
      text: runtimeStatusText(result),
      loading: false,
      ok: Boolean(result.ok),
      stdout: result.stdout || "",
      stderr: result.stderr || "",
      code: result.code ?? "",
      payload: result.ok ? extractStatusPayload(result.stdout || "") : null
    });
    return result;
  }

  async function refreshConfig() {
    if (!runtime?.config) {
      state.setBackendConfig({ loading: false, ok: false, stderr: "桌面桥接不可用" });
      return { ok: false, stderr: "桌面桥接不可用" };
    }

    state.setBackendConfig({ loading: true, ok: null, stdout: "", stderr: "", code: "" });
    const result = await runtime.config();
    state.setBackendConfig({
      loading: false,
      ok: Boolean(result.ok),
      stdout: result.stdout || "",
      stderr: result.stderr || "",
      code: result.code ?? ""
    });
    return result;
  }

  async function hotSwitchSettings(next, options = {}) {
    const saved = await saveSettings(next);
    let statusResult;
    let configResult = null;
    try {
      statusResult = await refreshStatus();
    } catch (error) {
      statusResult = { ok: false, stderr: error?.message || String(error) };
      state.setRuntimeStatus({ text: statusResult.stderr, loading: false, ok: false, stderr: statusResult.stderr, payload: null });
    }
    const shouldRefreshConfig = options.refreshConfig !== false;
    if (shouldRefreshConfig) {
      try {
        configResult = await refreshConfig();
      } catch (error) {
        configResult = { ok: false, stderr: error?.message || String(error) };
        state.setBackendConfig({ loading: false, ok: false, stderr: configResult.stderr });
      }
    }
    return {
      saved,
      statusResult,
      configResult,
      ok: Boolean(statusResult?.ok) && (!shouldRefreshConfig || Boolean(configResult?.ok))
    };
  }

  function recentBackendMessages(messages) {
    return (Array.isArray(messages) ? messages : [])
      .filter((item) => item?.role === "user" || item?.role === "assistant")
      .map((item) => ({
        role: item.role,
        content: String(item.content || "").slice(0, 4000),
        error: Boolean(item.error),
        at: Number(item.at || 0)
      }))
      .filter((item) => item.content)
      .slice(-20);
  }

  async function sendMessage(text, attachments = []) {
    const cleanAttachments = Array.isArray(attachments) ? attachments.slice(0, 5) : [];
    const message = String(text || "").trim() || (cleanAttachments.length ? "请阅读我上传的文件。" : "");
    if ((!message && !cleanAttachments.length) || !runtime?.send) return;

    const { settings } = state.snapshot();
    const requestId = makeRequestId();
    const userMessage = state.addMessage("user", message, false, { attachments: cleanAttachments });
    const recentMessages = recentBackendMessages(state.snapshot().messages);
    const showRunProgress = settings.mode !== "chat";
    if (showRunProgress) {
      state.startRunProgress(requestId, { anchorAt: userMessage?.at || Date.now() });
    }
    state.setBusy(true);
    state.setLastRun({
      phase: "running",
      ok: null,
      mode: settings.mode,
      workspace: settings.workspace
    });

    try {
      const result = await runtime.send({
        requestId,
        message,
        workspace: settings.workspace,
        mode: settings.mode,
        maxSteps: settings.maxSteps,
        personaName: settings.personaName,
        soulPrompt: settings.soulPrompt,
        modelService: settings.modelService,
        modelProvider: settings.modelProvider,
        modelBaseUrl: settings.modelBaseUrl,
        modelName: settings.modelName,
        modelApiKey: settings.modelApiKey,
        modelThinkingEnabled: settings.modelThinkingEnabled,
        modelThinkingDepth: settings.modelThinkingDepth,
        attachments: cleanAttachments,
        recentMessages
      });
      if (showRunProgress) state.finishRunProgress(requestId, Boolean(result.ok));
      state.setLastRun({ ...result, phase: "finished" });
      const reply = backendReply(result);
      const displayText = spokenBackendText(reply.text) || (reply.error ? reply.text : "已完成。");
      if (showRunProgress) state.clearRunProgress();
      state.addMessage("assistant", displayText, reply.error);
    } catch (error) {
      const message = error.message || String(error);
      if (showRunProgress) state.finishRunProgress(requestId, false);
      state.setLastRun({ phase: "finished", ok: false, stderr: message });
      if (showRunProgress) state.clearRunProgress();
      state.addMessage("assistant", humanizeBackendError(message) || "执行失败。", true);
    } finally {
      state.setBusy(false);
      try {
        await refreshStatus();
      } catch {
        // Status refresh must not turn a completed response into a failed send.
      }
    }
  }

  function clearConversation() {
    const { settings } = state.snapshot();
    state.clearMessages();
    state.setLastRun({
      phase: "idle",
      ok: true,
      mode: settings.mode,
      workspace: settings.workspace
    });
  }

  return {
    loadSettings,
    setActivePage,
    startNewConversation,
    switchConversation,
    deleteConversation,
    setActiveSkillCategory,
    setMode,
    saveSettings,
    chooseWorkspace,
    choosePersonaAvatar,
    chooseUserAvatar,
    openWorkspace,
    openPath,
    listDailyLogs,
    openDailyLog,
    deleteDailyLog,
    listSkills,
    hotSwitchSettings,
    refreshStatus,
    refreshConfig,
    listKnowledge,
    importKnowledgeFiles,
    chooseChatFiles,
    queryKnowledge,
    exportKnowledge,
    removeKnowledge,
    confirmLifecycleUpdate,
    denyLifecycleUpdate,
    sendMessage,
    clearConversation
  };
}
