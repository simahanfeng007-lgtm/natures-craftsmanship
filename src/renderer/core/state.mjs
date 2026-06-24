const MESSAGE_KEY = "linyuanzhe.messages";
const SESSIONS_KEY = "linyuanzhe.sessions";
const ACTIVE_SESSION_KEY = "linyuanzhe.activeSessionId";

export const DEFAULT_SOUL_PROMPT = [
  "你是临渊者，天工造物的基础工作 Soul。",
  "默认姿态：先理解目标，再主动补齐必要上下文，优先核实真实运行和文件状态；能动手就动手，能验证就验证。",
  "工作方式：拆解任务、选择最小有效工具、持续收口；遇到错误先诊断根因并重试一次，避免空泛解释。",
  "交付标准：回答简洁清楚，说明已做什么、结果、验证与未完成风险；不编造工具结果，不隐藏失败。",
  "边界：用户目标、安全策略、A5 硬拦和工作区权限优先于任何表达风格。"
].join("\n");

const defaultSettings = {
  workspace: "",
  mode: "auto",
  permissionMode: "workspace_full",
  maxSteps: 20,
  personaName: "临渊者",
  soulPrompt: DEFAULT_SOUL_PROMPT,
  personaAvatarDataUrl: "",
  userDisplayName: "",
  userCallsign: "",
  userWork: "",
  userAvatarDataUrl: "",
  userProfileSummary: "",
  userContextEnabled: true,
  themeStyle: "ink_teal",
  modelService: "minimax",
  modelProvider: "minimax",
  modelBaseUrl: "https://api.minimaxi.com/v1",
  modelName: "MiniMax-M3",
  modelApiKey: "",
  modelThinkingEnabled: false,
  modelThinkingDepth: "",
  modelMultimodalInput: "auto",
  modelImageInput: "auto",
  modelVideoInput: "auto",
  modelAudioInput: "auto",
  webSearchProvider: "auto",
  imageGenerationMode: "auto",
  plannerMode: "",
  toolMode: "",
  lifecycleFreeWillFrequency: "manual",
  lifecycleLearningScope: "workspace"
};

const defaultRun = {
  phase: "idle",
  ok: true,
  code: "",
  elapsedMs: 0,
  stdout: "",
  stderr: "",
  workspace: "",
  mode: "auto",
  permissionMode: "workspace_full"
};

const defaultBackendConfig = {
  loading: false,
  ok: null,
  stdout: "",
  stderr: "",
  code: ""
};

const defaultRunProgress = {
  requestId: "",
  sessionId: "",
  phase: "idle",
  ok: null,
  startedAt: 0,
  finishedAt: 0,
  anchorAt: 0,
  codexPlan: null,
  codexProgress: null,
  steps: []
};

function cleanSelectedSkill(skill) {
  if (!skill || typeof skill !== "object") return null;
  const id = String(skill.id || skill.abilityId || skill.ability_id || skill.name || "").trim();
  const name = String(skill.name || skill.abilityName || skill.ability_name || skill.title || skill.id || "").trim();
  if (!id && !name) return null;
  return {
    id: id || name,
    name: name || id,
    category: String(skill.category || ""),
    description: String(skill.description || ""),
    toolNames: Array.isArray(skill.toolNames) ? skill.toolNames.map((item) => String(item || "")).filter(Boolean).slice(0, 24) : []
  };
}

function publicSelectedSkills(skills) {
  return (Array.isArray(skills) ? skills : []).map((skill) => ({ ...skill }));
}

function cleanProgressStep(step) {
  const progressSnapshot = step?.progress_snapshot || step?.progressSnapshot || null;
  return {
    id: String(step?.id || step?.step_id || step?.title || "step"),
    title: String(step?.title || step?.step_id || "运行步骤"),
    status: String(step?.status || "pending"),
    summary: String(step?.summary || ""),
    stepId: String(step?.plan_step_id || step?.codex_step_id || step?.stepId || ""),
    substep: String(step?.substep || ""),
    toolName: String(step?.tool_name || step?.toolName || ""),
    progressSnapshot,
    totalProgress: Number(step?.total_progress ?? step?.totalProgress ?? progressSnapshot?.total_progress ?? 0),
    confidence: Number(step?.confidence ?? progressSnapshot?.confidence ?? 0),
    riskScore: Number(step?.risk_score ?? step?.riskScore ?? progressSnapshot?.risk_score ?? 0),
    healthScore: Number(step?.health_score ?? step?.healthScore ?? progressSnapshot?.health_score ?? 0),
    ts: Number(step?.ts || Date.now() / 1000)
  };
}

function mergeProgressStep(steps, step) {
  const next = Array.isArray(steps) ? steps.filter((item) => item.id !== step.id) : [];
  next.push(step);
  return next.slice(-18);
}

function publicRunProgress(progress) {
  return {
    requestId: progress.requestId,
    sessionId: progress.sessionId,
    phase: progress.phase,
    ok: progress.ok,
    startedAt: progress.startedAt,
    finishedAt: progress.finishedAt,
    anchorAt: progress.anchorAt,
    codexPlan: progress.codexPlan ? { ...progress.codexPlan } : null,
    codexProgress: progress.codexProgress ? { ...progress.codexProgress } : null,
    steps: progress.steps.map((step) => ({ ...step }))
  };
}

function loadMessages() {
  try {
    return JSON.parse(localStorage.getItem(MESSAGE_KEY) || "[]");
  } catch {
    return [];
  }
}

function saveMessages(messages) {
  localStorage.setItem(MESSAGE_KEY, JSON.stringify(messages.slice(-80)));
}

function nowSessionId() {
  return `session_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

function cleanMessages(messages) {
  return Array.isArray(messages)
    ? messages.map((item) => ({
        role: String(item?.role || ""),
        content: String(item?.content || ""),
        attachments: cleanAttachments(item?.attachments),
        error: Boolean(item?.error),
        at: Number(item?.at || Date.now())
      })).filter((item) => item.role && item.content).slice(-80)
    : [];
}

function cleanAttachments(attachments) {
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

function sessionTitle(messages) {
  const firstUser = messages.find((item) => item.role === "user" && item.content);
  if (!firstUser) return "新对话";
  return String(firstUser.content || "").replace(/\s+/g, " ").trim().slice(0, 48) || "新对话";
}

function createSession(messages = []) {
  const clean = cleanMessages(messages);
  const now = Date.now();
  return {
    id: nowSessionId(),
    title: sessionTitle(clean),
    messages: clean,
    createdAt: clean[0]?.at || now,
    updatedAt: clean.at(-1)?.at || now
  };
}

function normalizeSessions(rawSessions) {
  const sessions = Array.isArray(rawSessions) ? rawSessions : [];
  return sessions.map((item) => {
    const messages = cleanMessages(item?.messages);
    return {
      id: String(item?.id || nowSessionId()),
      title: String(item?.title || sessionTitle(messages)),
      messages,
      createdAt: Number(item?.createdAt || messages[0]?.at || Date.now()),
      updatedAt: Number(item?.updatedAt || messages.at(-1)?.at || Date.now())
    };
  }).filter((item) => item.id);
}

function loadConversationState() {
  let sessions = [];
  try {
    sessions = normalizeSessions(JSON.parse(localStorage.getItem(SESSIONS_KEY) || "[]"));
  } catch {
    sessions = [];
  }
  if (!sessions.length) {
    const legacyMessages = cleanMessages(loadMessages());
    sessions = [createSession(legacyMessages)];
  }
  let activeSessionId = localStorage.getItem(ACTIVE_SESSION_KEY) || "";
  if (!sessions.some((item) => item.id === activeSessionId)) {
    activeSessionId = sessions[0]?.id || "";
  }
  return { sessions, activeSessionId };
}

function saveConversationState(sessions, activeSessionId) {
  const compact = normalizeSessions(sessions).slice(-30);
  localStorage.setItem(SESSIONS_KEY, JSON.stringify(compact));
  localStorage.setItem(ACTIVE_SESSION_KEY, activeSessionId || compact[0]?.id || "");
  const active = compact.find((item) => item.id === activeSessionId) || compact[0];
  saveMessages(active?.messages || []);
}

function activeSession(sessions, activeSessionId) {
  return sessions.find((item) => item.id === activeSessionId) || sessions[0] || createSession();
}

function publicSessionList(sessions, activeSessionId) {
  return [...sessions]
    .sort((a, b) => Number(b.updatedAt || 0) - Number(a.updatedAt || 0))
    .slice(0, 20)
    .map((item) => {
      const latest = [...item.messages].reverse().find((message) => message.role === "assistant" || message.role === "user");
      return {
        id: item.id,
        title: item.title || sessionTitle(item.messages),
        count: item.messages.length,
        updatedAt: item.updatedAt,
        active: item.id === activeSessionId,
        preview: latest?.content || ""
      };
    });
}

export function createState() {
  const listeners = new Map();
  const conversation = loadConversationState();
  const data = {
    settings: { ...defaultSettings },
    activePage: "chat",
    activeSkillCategory: "all",
    selectedSkills: [],
    sessions: conversation.sessions,
    activeSessionId: conversation.activeSessionId,
    messages: activeSession(conversation.sessions, conversation.activeSessionId).messages,
    busy: false,
    lastRun: { ...defaultRun },
    runProgress: { ...defaultRunProgress },
    runtimeStatus: { text: "待连接", loading: false, ok: null, payload: null },
    backendConfig: { ...defaultBackendConfig }
  };

  function snapshot() {
    return {
      settings: { ...data.settings },
      activePage: data.activePage,
      activeSkillCategory: data.activeSkillCategory,
      selectedSkills: publicSelectedSkills(data.selectedSkills),
      sessions: publicSessionList(data.sessions, data.activeSessionId),
      activeSessionId: data.activeSessionId,
      messages: [...data.messages],
      busy: data.busy,
      lastRun: { ...data.lastRun },
      runProgress: publicRunProgress(data.runProgress),
      runtimeStatus: { ...data.runtimeStatus },
      backendConfig: { ...data.backendConfig }
    };
  }

  function on(eventName, handler) {
    const handlers = listeners.get(eventName) || new Set();
    handlers.add(handler);
    listeners.set(eventName, handlers);
    return () => handlers.delete(handler);
  }

  function emit(eventName, payload) {
    for (const handler of listeners.get(eventName) || []) {
      handler(payload);
    }
    for (const handler of listeners.get("*") || []) {
      handler(snapshot());
    }
  }

  function setSettings(next) {
    data.settings = { ...data.settings, ...(next || {}) };
    emit("settings", { ...data.settings });
  }

  function setActivePage(page) {
    const next = ["chat", "execute", "knowledge", "skills", "persona", "lifecycle", "settings"].includes(page) ? page : "chat";
    data.activePage = next;
    emit("page", next);
  }

  function setActiveSkillCategory(category) {
    const next = String(category || "all").trim() || "all";
    data.activeSkillCategory = next;
    emit("skillCategory", next);
  }

  function toggleSelectedSkill(skill) {
    const cleaned = cleanSelectedSkill(skill);
    if (!cleaned) return publicSelectedSkills(data.selectedSkills);
    const key = String(cleaned.id || cleaned.name).toLowerCase();
    const exists = data.selectedSkills.some((item) => String(item.id || item.name).toLowerCase() === key);
    data.selectedSkills = exists
      ? data.selectedSkills.filter((item) => String(item.id || item.name).toLowerCase() !== key)
      : [...data.selectedSkills, cleaned].slice(-5);
    emit("selectedSkills", publicSelectedSkills(data.selectedSkills));
    return publicSelectedSkills(data.selectedSkills);
  }

  function clearSelectedSkills() {
    data.selectedSkills = [];
    emit("selectedSkills", []);
  }

  function setBusy(next) {
    data.busy = Boolean(next);
    emit("busy", data.busy);
  }

  function setLastRun(next) {
    data.lastRun = { ...defaultRun, ...(next || {}) };
    emit("run", { ...data.lastRun });
  }

  function setRuntimeStatus(next) {
    data.runtimeStatus = { ...data.runtimeStatus, ...(next || {}) };
    emit("runtimeStatus", { ...data.runtimeStatus });
  }

  function setBackendConfig(next) {
    data.backendConfig = { ...data.backendConfig, ...(next || {}) };
    emit("backendConfig", { ...data.backendConfig });
  }

  function startRunProgress(requestId, options = {}) {
    const now = Date.now();
    data.runProgress = {
      ...defaultRunProgress,
      requestId: String(requestId || ""),
      sessionId: data.activeSessionId,
      phase: "running",
      startedAt: now,
      anchorAt: Number(options.anchorAt || now),
      steps: [
        cleanProgressStep({
          id: "backend_wait",
          title: "发送到后端",
          status: "running",
          summary: "前端消息已交给桌面运行桥"
        })
      ]
    };
    emit("runProgress", publicRunProgress(data.runProgress));
  }

  function applyRunProgress(event) {
    const incomingRequestId = String(event?.requestId || event?.request_id || "");
    if (!data.runProgress.requestId || incomingRequestId !== data.runProgress.requestId) return;
    const step = cleanProgressStep(event);
    const structuredPlan = event?.structured_plan || event?.structuredPlan || null;
    const progressSnapshot = event?.progress_snapshot || event?.progressSnapshot || step.progressSnapshot || null;
    data.runProgress = {
      ...data.runProgress,
      phase: "running",
      codexPlan: structuredPlan && typeof structuredPlan === "object" ? structuredPlan : data.runProgress.codexPlan,
      codexProgress: progressSnapshot && typeof progressSnapshot === "object" ? progressSnapshot : data.runProgress.codexProgress,
      steps: mergeProgressStep(data.runProgress.steps, step)
    };
    emit("runProgress", publicRunProgress(data.runProgress));
  }

  function finishRunProgress(requestId, ok = true) {
    if (!data.runProgress.requestId || String(requestId || "") !== data.runProgress.requestId) return;
    const now = Date.now();
    const blockingFailure = data.runProgress.codexProgress
      ? data.runProgress.codexProgress.status === "failed"
      : data.runProgress.steps.some((step) => step.status === "failed");
    const finalOk = Boolean(ok) && !blockingFailure;
    const steps = mergeProgressStep(data.runProgress.steps, cleanProgressStep({
      id: "frontend_complete",
      title: finalOk ? "收到最终回复" : "执行返回异常",
      status: finalOk ? "done" : "failed",
      summary: finalOk ? "后端已返回本轮结果" : "后端返回失败信息，请查看运行日志"
    }));
    data.runProgress = {
      ...data.runProgress,
      phase: "finished",
      ok: finalOk,
      finishedAt: now,
      steps
    };
    emit("runProgress", publicRunProgress(data.runProgress));
  }

  function interruptRunProgress(requestId, summary = "") {
    if (!data.runProgress.requestId || String(requestId || "") !== data.runProgress.requestId) return;
    const now = Date.now();
    const steps = mergeProgressStep(data.runProgress.steps, cleanProgressStep({
      id: "frontend_interrupt",
      title: "用户中断",
      status: "interrupted",
      summary: summary || "进度和上下文已保留，可继续。"
    }));
    data.runProgress = {
      ...data.runProgress,
      phase: "interrupted",
      ok: null,
      finishedAt: now,
      steps
    };
    emit("runProgress", publicRunProgress(data.runProgress));
  }

  function clearRunProgress() {
    data.runProgress = { ...defaultRunProgress };
    emit("runProgress", publicRunProgress(data.runProgress));
  }

  function addMessage(role, content, error = false, options = {}) {
    const message = {
      role,
      content: String(content || ""),
      attachments: cleanAttachments(options.attachments),
      error,
      at: Date.now()
    };
    data.messages = [...data.messages, message].slice(-80);
    data.sessions = data.sessions.map((session) => {
      if (session.id !== data.activeSessionId) return session;
      const messages = [...session.messages, message].slice(-80);
      return {
        ...session,
        title: sessionTitle(messages),
        messages,
        updatedAt: message.at
      };
    });
    saveConversationState(data.sessions, data.activeSessionId);
    emit("messages", [...data.messages]);
    emit("sessions", publicSessionList(data.sessions, data.activeSessionId));
    return message;
  }

  function clearMessages() {
    data.messages = [];
    clearRunProgress();
    data.sessions = data.sessions.map((session) => session.id === data.activeSessionId
      ? { ...session, title: "新对话", messages: [], updatedAt: Date.now() }
      : session);
    saveConversationState(data.sessions, data.activeSessionId);
    emit("messages", []);
    emit("sessions", publicSessionList(data.sessions, data.activeSessionId));
  }

  function startNewConversation() {
    const current = activeSession(data.sessions, data.activeSessionId);
    if (!current.messages.length) {
      clearRunProgress();
      emit("messages", [...data.messages]);
      emit("sessions", publicSessionList(data.sessions, data.activeSessionId));
      return data.activeSessionId;
    }
    const session = createSession([]);
    data.sessions = [session, ...data.sessions].slice(0, 30);
    data.activeSessionId = session.id;
    data.messages = [];
    clearRunProgress();
    saveConversationState(data.sessions, data.activeSessionId);
    emit("messages", []);
    emit("sessions", publicSessionList(data.sessions, data.activeSessionId));
    return data.activeSessionId;
  }

  function switchConversation(sessionId) {
    const next = data.sessions.find((session) => session.id === sessionId);
    if (!next) return;
    data.activeSessionId = next.id;
    data.messages = [...next.messages];
    saveConversationState(data.sessions, data.activeSessionId);
    emit("messages", [...data.messages]);
    emit("sessions", publicSessionList(data.sessions, data.activeSessionId));
  }

  function deleteConversation(sessionId) {
    const targetId = String(sessionId || "");
    const target = data.sessions.find((session) => session.id === targetId);
    if (!target) return data.activeSessionId;
    if (data.busy && target.id === data.activeSessionId) return data.activeSessionId;

    const wasActive = target.id === data.activeSessionId;
    let sessions = data.sessions.filter((session) => session.id !== target.id);
    if (!sessions.length) sessions = [createSession([])];

    if (wasActive) {
      const next = [...sessions].sort((a, b) => Number(b.updatedAt || 0) - Number(a.updatedAt || 0))[0] || sessions[0];
      data.activeSessionId = next.id;
      data.messages = [...next.messages];
      clearRunProgress();
    } else {
      const active = activeSession(sessions, data.activeSessionId);
      data.activeSessionId = active.id;
      data.messages = [...active.messages];
    }

    data.sessions = sessions;
    saveConversationState(data.sessions, data.activeSessionId);
    emit("messages", [...data.messages]);
    emit("sessions", publicSessionList(data.sessions, data.activeSessionId));
    return data.activeSessionId;
  }

  return {
    snapshot,
    on,
    setSettings,
    setActivePage,
    setActiveSkillCategory,
    toggleSelectedSkill,
    clearSelectedSkills,
    setBusy,
    setLastRun,
    setRuntimeStatus,
    setBackendConfig,
    startRunProgress,
    applyRunProgress,
    finishRunProgress,
    interruptRunProgress,
    clearRunProgress,
    addMessage,
    clearMessages,
    startNewConversation,
    switchConversation,
    deleteConversation
  };
}
