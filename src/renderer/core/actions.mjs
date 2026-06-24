import { backendReply, extractStatusPayload, humanizeBackendError, parseBackendSteps, runtimeStatusText, spokenBackendText } from "./formatters.mjs";

const THEME_STYLES = new Set(["ink_teal", "bronze_gear", "jade_light"]);

function makeRequestId() {
  return `req_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

const WORK_INTENT_MARKERS = [
  "来修", "修复", "修一下", "改一下", "改掉", "处理", "开始干活", "干活", "工作啊", "工作",
  "继续", "别停", "不要停", "执行", "运行", "测试", "扫描", "读取", "写入", "保存",
  "下载", "上网", "搜索", "学习", "安装", "打包", "排查", "诊断", "审查", "迁移",
  "fix", "repair", "work", "continue", "run", "test", "scan", "read", "write",
  "download", "search", "learn", "install", "package", "diagnose"
];

function normalizeModeValue(value) {
  const mode = String(value || "").trim().toLowerCase();
  return mode === "chat" || mode === "work" || mode === "auto" ? mode : "";
}

function looksLikeWorkIntent(text, selectedSkills = []) {
  if (Array.isArray(selectedSkills) && selectedSkills.length) return true;
  const compact = String(text || "").replace(/\s+/g, "").toLowerCase();
  if (!compact) return false;
  if (/https?:\/\//i.test(text) && /下载|保存|download|save/i.test(text)) return true;
  return WORK_INTENT_MARKERS.some((marker) => compact.includes(String(marker).replace(/\s+/g, "").toLowerCase()));
}

function inferSendMode(message, settings = {}, selectedSkills = [], runOptions = {}) {
  const explicit = normalizeModeValue(runOptions.mode || runOptions.workMode);
  if (explicit) return explicit;
  const configured = normalizeModeValue(settings.mode);
  if (configured === "work" || configured === "chat") return configured;
  if (looksLikeWorkIntent(message, selectedSkills)) return "work";
  return "auto";
}

export function createActions({ runtime, state }) {
  if (runtime?.onRunStep) {
    runtime.onRunStep((event) => state.applyRunProgress(event));
  }
  if (runtime?.onLearningMessage) {
    runtime.onLearningMessage((event) => {
      const role = event?.role === "user" ? "user" : "assistant";
      const content = String(event?.content || "").trim();
      if (!content) return;
      state.addMessage(role, content, Boolean(event?.error));
      refreshStatus().catch(() => {});
    });
  }

  async function loadSettings() {
    if (!runtime?.getSettings) return;
    const next = await runtime.getSettings();
    state.setSettings(next);
  }

  function currentThemeStyle() {
    const theme = String(state.snapshot().settings.themeStyle || "").trim();
    return THEME_STYLES.has(theme) ? theme : "ink_teal";
  }

  function normalizeSettingsPatch(next) {
    const patch = { ...(next || {}) };
    if (Object.prototype.hasOwnProperty.call(patch, "themeStyle")) {
      const theme = String(patch.themeStyle || "").trim();
      patch.themeStyle = THEME_STYLES.has(theme) ? theme : currentThemeStyle();
    }
    return patch;
  }

  async function setMode(mode) {
    try {
      await hotSwitchSettings({ mode }, { refreshConfig: false });
    } catch {
      state.setSettings({ mode });
    }
  }

  async function setPermissionMode(permissionMode) {
    try {
      await hotSwitchSettings({ permissionMode }, { refreshConfig: false });
    } catch {
      state.setSettings({ permissionMode });
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

  function toggleSelectedSkill(skill) {
    return state.toggleSelectedSkill(skill);
  }

  function clearSelectedSkills() {
    state.clearSelectedSkills();
  }

  async function saveSettings(next) {
    const normalized = normalizeSettingsPatch(next);
    const explicitTheme = Object.prototype.hasOwnProperty.call(normalized, "themeStyle");
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
      const merged = saved
        ? (explicitTheme ? { ...saved, themeStyle: normalized.themeStyle } : saved)
        : state.snapshot().settings;
      if (saved) state.setSettings(merged);
      return merged;
    } catch {
      return state.snapshot().settings;
    }
  }

  async function chooseWorkspace() {
    if (!runtime?.chooseWorkspace) return;
    const next = await runtime.chooseWorkspace();
    if (next) state.setSettings(next);
    return state.snapshot().settings;
  }

  async function chooseWorkspaceRoot(root) {
    if (!runtime?.chooseWorkspaceRoot) return state.snapshot().settings;
    const next = await runtime.chooseWorkspaceRoot(root);
    if (next) state.setSettings(next);
    return state.snapshot().settings;
  }

  async function choosePersonaAvatar() {
    if (!runtime?.choosePersonaAvatar) return state.snapshot().settings;
    const next = await runtime.choosePersonaAvatar();
    if (next) state.setSettings(next);
    return state.snapshot().settings;
  }

  async function chooseUserAvatar() {
    if (!runtime?.chooseUserAvatar) return state.snapshot().settings;
    const next = await runtime.chooseUserAvatar();
    if (next) state.setSettings(next);
    return state.snapshot().settings;
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

  async function pasteChatFiles(payload = {}) {
    if (!runtime?.pasteChatFiles) return { ok: false, error: "粘贴文件上传不可用", attachments: [] };
    return runtime.pasteChatFiles(knowledgePayload(payload));
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

  async function deleteLearningExperience(experienceId) {
    if (!runtime?.deleteLearningExperience) return { ok: false, error: "学习池删除桥接不可用" };
    const result = await runtime.deleteLearningExperience(lifecyclePayload({ id: experienceId, experienceId }));
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

  async function messageChannelStatus() {
    if (!runtime?.messageChannelStatus) {
      return { ok: false, error: "消息通道桥接不可用", channels: {} };
    }
    return runtime.messageChannelStatus();
  }

  async function connectMessageChannel(payload = {}) {
    if (!runtime?.connectMessageChannel) {
      return { ok: false, error: "消息通道桥接不可用" };
    }
    return runtime.connectMessageChannel(payload);
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

  async function cancelRun() {
    const snap = state.snapshot();
    const requestId = snap.runProgress.requestId || snap.lastRun.requestId || "";
    if (!requestId || !runtime?.cancel) return { ok: false, error: "cancel_unavailable" };
    const result = await runtime.cancel({ requestId });
    if (result?.ok || result?.interrupted || result?.canceled) {
      const summary = result.summary || "已中断。本次进度和上下文已保留，后续可以继续。";
      state.interruptRunProgress(requestId, summary);
      state.setLastRun({
        ...snap.lastRun,
        ...result,
        requestId,
        phase: "interrupted",
        ok: null,
        finishedAt: Date.now()
      });
      state.setBusy(false);
    }
    return result;
  }

  async function guideRun(text) {
    const message = String(text || "").trim();
    const snap = state.snapshot();
    const requestId = snap.runProgress.requestId || snap.lastRun.requestId || "";
    if (!message) return { ok: false, error: "empty_guidance" };
    if (!requestId || !runtime?.guide) return { ok: false, error: "guide_unavailable" };
    state.addMessage("user", `【运行中纠偏】${message}`, false);
    const result = await runtime.guide({ requestId, message });
    if (!result?.ok) {
      state.addMessage("assistant", result?.error || "纠偏发送失败。", true);
    }
    return result;
  }

  function recentBackendAttachments(attachments) {
    return Array.isArray(attachments)
      ? attachments.map((item) => ({
          name: String(item?.name || item?.file_name || ""),
          path: String(item?.path || ""),
          ext: String(item?.ext || "").toLowerCase(),
          size: Number(item?.size || 0),
          documentId: String(item?.documentId || item?.document_id || ""),
          status: String(item?.status || ""),
          summary: String(item?.summary || ""),
          citationCount: Number(item?.citationCount || item?.citation_count || 0),
          error: String(item?.error || "")
        })).filter((item) => item.name || item.path).slice(0, 5)
      : [];
  }

  function recentBackendMessages(messages) {
    return (Array.isArray(messages) ? messages : [])
      .filter((item) => item?.role === "user" || item?.role === "assistant")
      .map((item) => {
        const attachments = recentBackendAttachments(item?.attachments);
        return {
          role: item.role,
          content: String(item.content || "").slice(0, 4000),
          attachments,
          error: Boolean(item.error),
          at: Number(item.at || 0)
        };
      })
      .filter((item) => item.content || item.attachments.length)
      .slice(-20);
  }

  function compactWorkText(value, limit = 1200) {
    const text = String(value || "").replace(/\u0000/g, "").trim();
    if (text.length <= limit) return text;
    return text.slice(-limit);
  }

  function recentWorkContext(snapshot) {
    const run = snapshot?.lastRun || {};
    const progress = snapshot?.runProgress || {};
    const stdout = compactWorkText(run.stdout, 5000);
    const stderr = compactWorkText(run.stderr, 3000);
    const parsedSteps = parseBackendSteps(run.stdout || "").map((step) => ({
      tool: compactWorkText(step.tool, 80),
      status: compactWorkText(step.status, 40),
      summary: compactWorkText(step.summary, 500)
    }));
    const progressSteps = Array.isArray(progress.steps) ? progress.steps.map((step) => ({
      id: compactWorkText(step.id || step.stepId, 80),
      title: compactWorkText(step.title, 120),
      status: compactWorkText(step.status, 40),
      summary: compactWorkText(step.summary, 500),
      toolName: compactWorkText(step.toolName, 80),
      ts: Number(step.ts || 0)
    })) : [];
    const steps = [...parsedSteps, ...progressSteps]
      .filter((step) => step.tool || step.title || step.summary)
      .slice(-12);
    const hasRun = Boolean(run.requestId || stdout || stderr || run.phase === "finished" || run.phase === "running");
    const hasProgress = Boolean(progress.requestId || steps.length);
    if (!hasRun && !hasProgress) return null;
    return {
      schema: "tiangong.frontend.work_context.v1",
      capturedAt: Date.now(),
      lastRun: {
        requestId: compactWorkText(run.requestId, 80),
        phase: compactWorkText(run.phase, 40),
        ok: run.ok === null || typeof run.ok === "undefined" ? null : Boolean(run.ok),
        code: typeof run.code === "undefined" ? "" : String(run.code),
        mode: compactWorkText(run.mode, 40),
        workspace: compactWorkText(run.workspace, 900),
        elapsedMs: Number(run.elapsedMs || 0),
        startedAt: Number(run.startedAt || 0),
        finishedAt: Number(run.finishedAt || 0),
        stdout,
        stderr
      },
      runProgress: {
        requestId: compactWorkText(progress.requestId, 80),
        phase: compactWorkText(progress.phase, 40),
        ok: progress.ok === null || typeof progress.ok === "undefined" ? null : Boolean(progress.ok),
        startedAt: Number(progress.startedAt || 0),
        finishedAt: Number(progress.finishedAt || 0),
        anchorAt: Number(progress.anchorAt || 0)
      },
      steps
    };
  }

  async function sendMessage(text, attachments = [], runOptions = {}) {
    const cleanAttachments = Array.isArray(attachments) ? attachments.slice(0, 5) : [];
    const message = String(text || "").trim() || (cleanAttachments.length ? "请阅读我上传的文件。" : "");
    if ((!message && !cleanAttachments.length) || !runtime?.send) return;

    const beforeSend = state.snapshot();
    const { settings } = beforeSend;
    const selectedSkills = Array.isArray(beforeSend.selectedSkills) ? beforeSend.selectedSkills : [];
    const sendMode = inferSendMode(message, settings, selectedSkills, runOptions);
    const requestId = makeRequestId();
    const userMessage = state.addMessage("user", message, false, { attachments: cleanAttachments });
    const currentSnapshot = state.snapshot();
    const recentMessages = recentBackendMessages(currentSnapshot.messages);
    const workContext = recentWorkContext({
      ...currentSnapshot,
      lastRun: beforeSend.lastRun,
      runProgress: beforeSend.runProgress
    });
    const showRunProgress = true;
    if (showRunProgress) {
      state.startRunProgress(requestId, { anchorAt: userMessage?.at || Date.now() });
    }
    state.setBusy(true);
    state.setLastRun({
      phase: "running",
      ok: null,
      mode: sendMode,
      permissionMode: settings.permissionMode || "workspace_full",
      workspace: settings.workspace
    });

    try {
      const result = await runtime.send({
        requestId,
        message,
        workspace: settings.workspace,
        mode: sendMode,
        permissionMode: settings.permissionMode || "workspace_full",
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
        modelMultimodalInput: settings.modelMultimodalInput,
        modelImageInput: settings.modelImageInput,
        modelVideoInput: settings.modelVideoInput,
        modelAudioInput: settings.modelAudioInput,
        webSearchProvider: settings.webSearchProvider,
        imageGenerationMode: settings.imageGenerationMode,
        attachments: cleanAttachments,
        recentMessages,
        selectedSkills,
        selectedSkillNames: selectedSkills.map((item) => item.name || item.id).filter(Boolean),
        workContext,
        learningAction: runOptions.learningAction || "",
        learningId: runOptions.learningId || ""
      });
      if (result?.interrupted || result?.canceled) {
        if (showRunProgress) state.interruptRunProgress(requestId, result.stderr || result.summary || "");
        state.setLastRun({ ...result, phase: "interrupted", ok: null, finishedAt: Date.now() });
        state.addMessage("assistant", "已中断。本次进度和上下文已保留，后续输入“继续”即可接着处理。", false);
        return;
      }
      if (showRunProgress) state.finishRunProgress(requestId, Boolean(result.ok));
      state.setLastRun({ ...result, phase: "finished", finishedAt: Date.now() });
      const reply = backendReply(result);
      const displayText = spokenBackendText(reply.text) || (reply.error ? reply.text : "已完成。");
      if (showRunProgress) state.clearRunProgress();
      state.addMessage("assistant", displayText, reply.error);
    } catch (error) {
      const message = error.message || String(error);
      if (showRunProgress) state.finishRunProgress(requestId, false);
      state.setLastRun({ phase: "finished", ok: false, stderr: message, finishedAt: Date.now() });
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

  async function learnLearningExperience(experienceId, item = {}) {
    const id = String(experienceId || "").trim();
    if (!id) return { ok: false, error: "缺少学习卡编号" };
    const summary = String(item?.summary || item?.task_preview || "").replace(/\s+/g, " ").trim().slice(0, 240);
    const message = [
      summary ? `前台主动学习卡 ${id}：${summary}` : `前台主动学习卡 ${id}`,
      "执行要求：按学习卡 SOP 完成筛选、去重、优先级、学习、质检和归类；不得随机选择学习内容。"
    ].join("\n");
    await sendMessage(message, [], { learningAction: "learn", learningId: id });
    return { ok: true, id };
  }

  function clearConversation() {
    const { settings } = state.snapshot();
    state.clearMessages();
    state.setLastRun({
      phase: "idle",
      ok: true,
      mode: "auto",
      permissionMode: settings.permissionMode || "workspace_full",
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
    toggleSelectedSkill,
    clearSelectedSkills,
    setMode,
    setPermissionMode,
    saveSettings,
    chooseWorkspace,
    chooseWorkspaceRoot,
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
    messageChannelStatus,
    connectMessageChannel,
    listKnowledge,
    importKnowledgeFiles,
    chooseChatFiles,
    pasteChatFiles,
    queryKnowledge,
    exportKnowledge,
    removeKnowledge,
    confirmLifecycleUpdate,
    denyLifecycleUpdate,
    learnLearningExperience,
    deleteLearningExperience,
    cancelRun,
    guideRun,
    sendMessage,
    clearConversation
  };
}
