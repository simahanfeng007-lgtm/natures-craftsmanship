import { permissionNames, spokenBackendText } from "../core/formatters.mjs";
import { renderMediaAttachment, renderMessageContent } from "../core/message-renderer.mjs";

const DEFAULT_LOGO_SRC = "./assets/tiangong-logo.png";

function formatTime(ts) {
  if (!ts) return "";
  try {
    return new Date(ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

function escHtml(text) {
  const el = document.createElement("span");
  el.textContent = text;
  return el.innerHTML;
}

function formatBytes(value) {
  const size = Number(value || 0);
  if (!Number.isFinite(size) || size <= 0) return "";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function attachmentKey(item) {
  return String(item?.documentId || item?.document_id || item?.path || item?.name || "");
}

function attachmentStatusText(item) {
  const status = String(item?.status || "");
  if (status === "imported") return "已入库";
  if (status === "attached") return "已附加";
  if (status === "failed") return "导入失败";
  return "待发送";
}

function attachmentLooksLikeMedia(item) {
  const value = String(item?.path || item?.name || "");
  return /\.(png|jpe?g|gif|webp|bmp|ico|avif|svg|tiff?|mp4|webm|ogv|mov|mkv|avi|m4v|wmv|flv|mpe?g|3gp|m2?ts|mp3|wav|ogg|m4a|flac|aac|opus|wma)$/i.test(value);
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("read failed"));
    reader.readAsDataURL(file);
  });
}

function personaName(settings) {
  return String(settings?.personaName || "临渊者").trim() || "临渊者";
}

function personaInitial(settings) {
  return personaName(settings).slice(0, 1) || "临";
}

function renderPersonaAvatar(container, settings) {
  container.innerHTML = "";
  const img = document.createElement("img");
  img.src = String(settings?.personaAvatarDataUrl || "") || DEFAULT_LOGO_SRC;
  img.alt = personaName(settings);
  img.className = "persona-avatar-img";
  container.appendChild(img);
}

function renderEmptyState(container, settings) {
  container.innerHTML = `
    <div class="empty-state">
      <div class="empty-avatar" data-persona-avatar></div>
      <h3>天工造物 · ${escHtml(personaName(settings))}</h3>
      <p>输入任务或对话，后端会按自动流程进入执行链条。</p>
    </div>
  `;
  renderPersonaAvatar(container.querySelector("[data-persona-avatar]"), settings);
}

function progressStatusText(status) {
  const value = String(status || "pending");
  if (["done", "ok", "success", "completed"].includes(value)) return "完成";
  if (["failed", "blocked", "timeout"].includes(value)) return "异常";
  if (["interrupted", "canceled", "cancelled"].includes(value)) return "已中断";
  if (["running", "loading"].includes(value)) return "进行中";
  return "等待";
}

function progressClass(status) {
  const value = String(status || "pending");
  if (["done", "ok", "success", "completed"].includes(value)) return "done";
  if (["failed", "blocked", "timeout"].includes(value)) return "failed";
  if (["interrupted", "canceled", "cancelled"].includes(value)) return "interrupted";
  if (["running", "loading"].includes(value)) return "running";
  return "pending";
}

function percent(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return 0;
  return Math.max(0, Math.min(100, Math.round(number * 100)));
}

function visibleProgress(progress, activeSessionId) {
  return progress
    && progress.phase !== "idle"
    && progress.sessionId === activeSessionId
    && Array.isArray(progress.steps)
    && progress.steps.length;
}

let progressStepsExpanded = false;
let progressStepsRunId = "";
let thinkingStepsExpanded = false;
let thinkingStepsRunId = "";

const THINKING_STEP_IDS = new Set([
  "backend_wait",
  "request",
  "provider",
  "collect_context",
  "attachments",
  "route"
]);

function progressRunId(progress) {
  return String(progress?.requestId || progress?.startedAt || progress?.anchorAt || "");
}

function currentProgressStepsExpanded(progress) {
  const runId = progressRunId(progress);
  if (runId && runId !== progressStepsRunId) {
    progressStepsRunId = runId;
    progressStepsExpanded = false;
  }
  return progressStepsExpanded;
}

function currentThinkingStepsExpanded(progress) {
  const runId = progressRunId(progress);
  if (runId && runId !== thinkingStepsRunId) {
    thinkingStepsRunId = runId;
    thinkingStepsExpanded = false;
  }
  return thinkingStepsExpanded;
}

function isThinkingStep(step) {
  const id = String(step?.id || step?.step_id || "");
  return THINKING_STEP_IDS.has(id);
}

function thinkingGroupStatus(steps) {
  if (steps.some((step) => ["failed", "blocked", "timeout"].includes(String(step.status || "")))) return "failed";
  if (steps.some((step) => ["interrupted", "canceled", "cancelled"].includes(String(step.status || "")))) return "interrupted";
  if (steps.some((step) => ["running", "loading"].includes(String(step.status || "")))) return "running";
  if (steps.every((step) => ["done", "ok", "success", "completed"].includes(String(step.status || "")))) return "done";
  return "pending";
}

function thinkingGroupStep(steps) {
  const latest = steps[steps.length - 1] || {};
  const status = thinkingGroupStatus(steps);
  return {
    id: "thinking_group",
    title: "思考中",
    status,
    summary: status === "running" ? "正在理解请求，点开查看具体步骤" : "已完成前置思考，点开查看具体步骤",
    children: steps.map((step) => ({ ...step })),
    latestTitle: latest.title || "",
    isThinkingGroup: true
  };
}

function displayProgressSteps(rawSteps) {
  const output = [];
  let thinking = [];
  const flushThinking = () => {
    if (!thinking.length) return;
    output.push(thinkingGroupStep(thinking));
    thinking = [];
  };
  for (const step of rawSteps) {
    if (isThinkingStep(step)) {
      thinking.push(step);
      continue;
    }
    flushThinking();
    output.push(step);
  }
  flushThinking();
  return output;
}

function progressSignature(progress) {
  const codexProgress = progress?.codexProgress || {};
  const codexPlan = progress?.codexPlan || {};
  return [
    progress?.phase || "",
    progress?.ok ?? "",
    currentProgressStepsExpanded(progress) ? "steps-expanded" : "steps-collapsed",
    currentThinkingStepsExpanded(progress) ? "thinking-expanded" : "thinking-collapsed",
    codexPlan.plan_ref || "",
    codexProgress.status || "",
    codexProgress.active_step_id || "",
    codexProgress.total_progress ?? "",
    codexProgress.confidence ?? "",
    codexProgress.risk_score ?? "",
    ...(progress?.steps || []).map((step) => [
      step.id || "",
      step.title || "",
      step.status || "",
      step.summary || ""
    ].join(":"))
  ].join("|");
}

function renderProgressContent(container, progress, animate = true) {
  const nextSignature = progressSignature(progress);
  if (container.dataset.progressSignature === nextSignature) return;
  container.dataset.progressSignature = nextSignature;
  if (animate) container.classList.add("progress-content-updating");
  container.innerHTML = "";
  const header = document.createElement("div");
  header.className = "progress-header";
  const title = document.createElement("span");
  title.className = "progress-title";
  const isCodeX = Boolean(progress.codexPlan || progress.codexProgress);
  const headerSteps = Array.isArray(progress.steps) ? progress.steps : [];
  const isThinkingOnly = !isCodeX && progress.phase !== "finished" && headerSteps.length > 0 && headerSteps.every(isThinkingStep);
  title.textContent = isCodeX ? "Code-X 代码系统" : progress.phase === "finished" ? "本轮运行步骤" : isThinkingOnly ? "思考中" : "正在运行";
  const state = document.createElement("span");
  const isInterrupted = progress.phase === "interrupted";
  state.className = `progress-state ${isInterrupted ? "interrupted" : progress.ok === false ? "failed" : progress.phase === "finished" ? "done" : "running"}`;
  state.textContent = isInterrupted ? "已中断" : progress.ok === false ? "异常" : progress.phase === "finished" ? "完成" : isThinkingOnly ? "思考中" : "进行中";
  header.appendChild(title);
  header.appendChild(state);
  container.appendChild(header);

  const codexProgress = progress.codexProgress;
  if (codexProgress) {
    const total = percent(codexProgress.total_progress);
    const metrics = document.createElement("div");
    metrics.className = "codex-progress-metrics";
    metrics.innerHTML = `
      <div class="codex-progress-meter" aria-label="Code-X 总进度">
        <span class="codex-progress-fill" style="width:${total}%"></span>
      </div>
      <div class="codex-progress-stats">
        <span>进度 ${total}%</span>
        <span>置信 ${percent(codexProgress.confidence)}%</span>
        <span>风险 ${percent(codexProgress.risk_score)}%</span>
        <span>健康 ${percent(codexProgress.health_score)}%</span>
      </div>
    `;
    container.appendChild(metrics);
  }

  const planSteps = Array.isArray(progress.codexPlan?.steps) ? progress.codexPlan.steps : [];
  if (planSteps.length) {
    const evals = new Map((codexProgress?.steps || []).map((item) => [String(item.step_id || ""), item]));
    const tree = document.createElement("div");
    tree.className = "codex-plan-tree";
    for (const planStep of planSteps) {
      const stepId = String(planStep?.step_id || "");
      const evalStep = evals.get(stepId) || {};
      const statusValue = evalStep.status || (codexProgress?.active_step_id === stepId ? "running" : "pending");
      const row = document.createElement("div");
      row.className = `codex-plan-step ${progressClass(statusValue)}`;
      const name = document.createElement("span");
      name.className = "codex-plan-name";
      name.textContent = `${stepId || "-"} ${planStep?.title || "执行步骤"}`;
      const score = document.createElement("span");
      score.className = "codex-plan-score";
      score.textContent = `${progressStatusText(statusValue)} · ${percent(evalStep.score)}%`;
      row.appendChild(name);
      row.appendChild(score);
      tree.appendChild(row);
    }
    container.appendChild(tree);
  }

  const rawSteps = Array.isArray(progress.steps) ? progress.steps : [];
  const displaySteps = displayProgressSteps(rawSteps);
  const stepsExpanded = currentProgressStepsExpanded(progress);
  const visibleSteps = stepsExpanded ? displaySteps : displaySteps.slice(-1);
  const stepsSection = document.createElement("div");
  stepsSection.className = `progress-steps-section ${stepsExpanded ? "expanded" : "collapsed"}`;
  if (rawSteps.length > 1) {
    const controls = document.createElement("div");
    controls.className = "progress-steps-controls";
    const label = document.createElement("span");
    label.className = "progress-steps-label";
    label.textContent = stepsExpanded ? `运行步骤 ${rawSteps.length}` : "当前步骤";
    const toggle = document.createElement("button");
    toggle.className = "progress-steps-toggle";
    toggle.type = "button";
    toggle.title = stepsExpanded ? "收起历史步骤" : "展开历史步骤";
    toggle.setAttribute("aria-label", toggle.title);
    toggle.setAttribute("aria-expanded", stepsExpanded ? "true" : "false");
    toggle.innerHTML = `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M6 9l6 6 6-6"></path>
      </svg>
    `;
    toggle.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      progressStepsRunId = progressRunId(progress);
      progressStepsExpanded = !stepsExpanded;
      container.dataset.progressSignature = "";
      renderProgressContent(container, progress, false);
    });
    controls.appendChild(label);
    controls.appendChild(toggle);
    stepsSection.appendChild(controls);
  }

  const list = document.createElement("div");
  list.className = "progress-steps";
  for (const step of visibleSteps) {
    const isThinkingGroup = Boolean(step.isThinkingGroup);
    const row = document.createElement("div");
    row.className = `progress-step ${progressClass(step.status)}${isThinkingGroup ? " thinking-group" : ""}`;
    if (isThinkingGroup) {
      const expanded = currentThinkingStepsExpanded(progress);
      row.setAttribute("role", "button");
      row.setAttribute("tabindex", "0");
      row.setAttribute("aria-expanded", expanded ? "true" : "false");
      row.title = expanded ? "收起具体步骤" : "展开具体步骤";
      const toggleThinking = (event) => {
        event.preventDefault();
        event.stopPropagation();
        thinkingStepsRunId = progressRunId(progress);
        thinkingStepsExpanded = !currentThinkingStepsExpanded(progress);
        container.dataset.progressSignature = "";
        renderProgressContent(container, progress, false);
      };
      row.addEventListener("click", toggleThinking);
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") toggleThinking(event);
      });
    }
    const dot = document.createElement("span");
    dot.className = "progress-dot";
    const body = document.createElement("div");
    body.className = "progress-step-body";
    const line = document.createElement("div");
    line.className = "progress-step-line";
    const name = document.createElement("span");
    name.className = "progress-step-title";
    name.textContent = step.title || "运行步骤";
    if (isThinkingGroup) {
      const chevron = document.createElement("span");
      chevron.className = "thinking-group-chevron";
      chevron.textContent = currentThinkingStepsExpanded(progress) ? "⌃" : "⌄";
      name.appendChild(chevron);
    }
    const status = document.createElement("span");
    status.className = "progress-step-status";
    status.textContent = progressStatusText(step.status);
    line.appendChild(name);
    line.appendChild(status);
    body.appendChild(line);
    if (step.summary) {
      const summary = document.createElement("div");
      summary.className = "progress-step-summary";
      summary.textContent = step.summary;
      body.appendChild(summary);
    }
    if (isThinkingGroup && currentThinkingStepsExpanded(progress)) {
      const details = document.createElement("div");
      details.className = "thinking-step-details";
      for (const child of step.children || []) {
        const childRow = document.createElement("div");
        childRow.className = `thinking-step-detail ${progressClass(child.status)}`;
        const childTitle = document.createElement("div");
        childTitle.className = "thinking-step-detail-title";
        childTitle.textContent = child.title || "运行步骤";
        const childStatus = document.createElement("span");
        childStatus.className = "thinking-step-detail-status";
        childStatus.textContent = progressStatusText(child.status);
        childTitle.appendChild(childStatus);
        childRow.appendChild(childTitle);
        if (child.summary) {
          const childSummary = document.createElement("div");
          childSummary.className = "thinking-step-detail-summary";
          childSummary.textContent = child.summary;
          childRow.appendChild(childSummary);
        }
        details.appendChild(childRow);
      }
      body.appendChild(details);
    }
    row.appendChild(dot);
    row.appendChild(body);
    list.appendChild(row);
  }
  stepsSection.appendChild(list);
  container.appendChild(stepsSection);
  if (animate) {
    requestAnimationFrame(() => container.classList.remove("progress-content-updating"));
  }
}

export const conversationPanelPlugin = {
  id: "conversation-panel",
  slot: "conversation",
  order: 200,
  mount({ slot, state, actions, bus }) {
    slot.insertAdjacentHTML(
      "beforeend",
      `
        <section class="page-panel chat-page active" data-page-panel="chat">
          <header class="commandbar page-header">
            <div class="title-group">
              <h2>临渊者</h2>
              <span id="modeLabel" class="meta-chip">完全权限</span>
            </div>
            <div class="commandbar-meta">
              <button id="newChat" class="icon-button" type="button" title="新对话" aria-label="新对话">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h7"/><line x1="19" y1="3" x2="19" y2="11"/><line x1="15" y1="7" x2="23" y2="7"/></svg>
              </button>
              <button id="clearChat" class="icon-button" type="button" title="清空对话" aria-label="清空对话">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
              </button>
            </div>
          </header>

          <section id="messages" class="messages" aria-live="polite"></section>

          <form id="composer" class="composer">
            <div id="attachmentTray" class="attachment-tray" hidden></div>
            <div class="composer-extras">
              <button id="uploadFiles" class="icon-button" type="button" title="上传文件" aria-label="上传文件">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.44 11.05 12.25 20.24a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 1 1-2.83-2.83l8.49-8.48"/></svg>
              </button>
            </div>
            <textarea id="messageInput" rows="1" placeholder="输入任务或对话..."></textarea>
            <div class="composer-run-controls">
              <button id="interruptRun" class="interrupt-button" type="button" title="中断当前执行" aria-label="中断当前执行" hidden>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="7" y="7" width="10" height="10" rx="1.5"/></svg>
              </button>
              <button id="sendButton" class="send-button" type="submit" title="回车发送，Shift+回车换行" aria-label="发送">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
              </button>
            </div>
          </form>

          <div class="status-row">
            <span id="chatStatus">就绪</span>
            <span>后端入口：单轮任务执行</span>
          </div>
        </section>
      `
    );

    const panel = slot.querySelector('[data-page-panel="chat"]');
    const modeLabel = panel.querySelector("#modeLabel");
    const messagesEl = panel.querySelector("#messages");
    const form = panel.querySelector("#composer");
    const input = panel.querySelector("#messageInput");
    const sendButton = panel.querySelector("#sendButton");
    const interruptButton = panel.querySelector("#interruptRun");
    const newChatButton = panel.querySelector("#newChat");
    const clearButton = panel.querySelector("#clearChat");
    const uploadButton = panel.querySelector("#uploadFiles");
    const attachmentTray = panel.querySelector("#attachmentTray");
    const chatStatus = panel.querySelector("#chatStatus");
    const title = panel.querySelector(".title-group h2");
    let currentSettings = state.snapshot().settings;
    let pendingAttachments = [];

    function resizeInput() {
      input.style.height = "auto";
      input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
    }

    function renderPage(page) {
      panel.classList.toggle("active", page === "chat");
    }

    function shouldStickToBottom() {
      return messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 96;
    }

    function renderAttachmentChip(item, options = {}) {
      const chip = document.createElement("span");
      chip.className = `attachment-chip ${String(item?.status || "")}`;
      chip.title = [item?.path, item?.documentId].filter(Boolean).join("\n");
      const label = document.createElement(item?.path ? "button" : "span");
      label.className = "attachment-label";
      if (item?.path) {
        label.type = "button";
        label.dataset.openPath = item.path;
      }
      const name = document.createElement("span");
      name.className = "attachment-name";
      name.textContent = item?.name || item?.path || "file";
      const meta = document.createElement("span");
      meta.className = "attachment-meta";
      meta.textContent = [attachmentStatusText(item), formatBytes(item?.size)].filter(Boolean).join(" · ");
      label.appendChild(name);
      label.appendChild(meta);
      chip.appendChild(label);
      if (options.removable) {
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "attachment-remove";
        remove.dataset.removeAttachment = attachmentKey(item);
        remove.title = "移除";
        remove.setAttribute("aria-label", "移除文件");
        remove.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>`;
        chip.appendChild(remove);
      }
      return chip;
    }

    function renderAttachmentTray() {
      attachmentTray.innerHTML = "";
      attachmentTray.hidden = pendingAttachments.length === 0;
      for (const item of pendingAttachments) {
        attachmentTray.appendChild(renderAttachmentChip(item, { removable: true }));
      }
    }

    function renderMessageAttachments(container, attachments) {
      const items = Array.isArray(attachments) ? attachments.filter((item) => item?.name || item?.path) : [];
      if (!items.length) return;
      const mediaItems = items.filter((item) => item?.path && attachmentLooksLikeMedia(item));
      if (mediaItems.length) {
        const mediaWrap = document.createElement("div");
        mediaWrap.className = "message-attachment-media";
        for (const item of mediaItems) {
          renderMediaAttachment(mediaWrap, item.path, { caption: item.name || item.path });
        }
        container.appendChild(mediaWrap);
      }
      const wrap = document.createElement("div");
      wrap.className = "message-attachments";
      for (const item of items) {
        wrap.appendChild(renderAttachmentChip(item));
      }
      container.appendChild(wrap);
    }

    function applyAttachmentResult(result, actionLabel = "文件") {
      const items = Array.isArray(result?.attachments) ? result.attachments : [];
      const accepted = items.filter((item) => ["imported", "attached", "selected"].includes(String(item?.status || "")));
      const failedCount = items.filter((item) => String(item?.status || "") === "failed").length;
      const byKey = new Map(pendingAttachments.map((item) => [attachmentKey(item), item]));
      for (const item of accepted) {
        const key = attachmentKey(item);
        if (!key) continue;
        if (byKey.size >= 5 && !byKey.has(key)) break;
        byKey.set(key, item);
      }
      pendingAttachments = [...byKey.values()].slice(0, 5);
      renderAttachmentTray();

      const importedCount = accepted.filter((item) => String(item?.status || "") === "imported").length;
      const attachedCount = accepted.length - importedCount;
      if (accepted.length) {
        const parts = [];
        if (importedCount) parts.push(`入库 ${importedCount} 个`);
        if (attachedCount) parts.push(`附加 ${attachedCount} 个`);
        chatStatus.textContent = `${actionLabel}${parts.join("，")}${failedCount ? `，${failedCount} 个失败` : ""}`;
      } else if (result?.error) {
        chatStatus.textContent = result.error;
      } else if (failedCount) {
        chatStatus.textContent = `${failedCount} 个文件导入失败`;
      }
    }

    function createMessageNode(item) {
      const node = document.createElement("article");
      node.className = `message ${item.role}${item.error ? " error" : ""}${item.progress ? " progress" : ""}`;
      node.dataset.messageRole = item.role || "";
      node.dataset.messageAt = String(item.at || "");
      if (item.progress) node.dataset.progressBubble = "1";

      const avatar = document.createElement("div");
      avatar.className = "message-avatar";
      if (item.role === "user") {
        avatar.textContent = "你";
      } else {
        renderPersonaAvatar(avatar, currentSettings);
      }

      const body = document.createElement("div");
      body.className = "message-body";

      const meta = document.createElement("div");
      meta.className = "message-meta";
      const name = document.createElement("span");
      name.className = "message-name";
      name.textContent = item.role === "user" ? "你" : personaName(currentSettings);
      const time = document.createElement("span");
      time.className = "message-time";
      time.textContent = formatTime(item.at);
      meta.appendChild(name);
      meta.appendChild(time);

      const content = document.createElement("div");
      content.className = "message-content";
      if (item.progress) {
        renderProgressContent(content, item.progressData, false);
      } else {
        renderMessageContent(content, item.role === "assistant"
          ? spokenBackendText(item.content) || item.content
          : item.content);
        renderMessageAttachments(content, item.attachments);
      }

      body.appendChild(meta);
      body.appendChild(content);
      node.appendChild(avatar);
      node.appendChild(body);
      return node;
    }

    function progressAnchor(progress) {
      const anchorAt = Number(progress?.anchorAt || progress?.startedAt || 0);
      const users = [...messagesEl.querySelectorAll('.message.user[data-message-at]')];
      let anchor = null;
      for (const node of users) {
        if (Number(node.dataset.messageAt || 0) <= anchorAt + 1000) anchor = node;
      }
      return anchor;
    }

    function placeProgressNode(node, progress) {
      const anchor = progressAnchor(progress);
      if (anchor?.nextSibling !== node) {
        anchor?.after(node) || messagesEl.appendChild(node);
      }
    }

    function renderProgress(progress = state.snapshot().runProgress, activeSessionId = state.snapshot().activeSessionId) {
      const existing = messagesEl.querySelector('[data-progress-bubble="1"]');
      if (!visibleProgress(progress, activeSessionId)) {
        if (existing) {
          existing.classList.add("is-leaving");
          existing._progressLeaveTimer = window.setTimeout(() => {
            if (existing.classList.contains("is-leaving")) existing.remove();
          }, 180);
        }
        return;
      }

      const wasNearBottom = shouldStickToBottom();
      let node = existing;
      if (!node) {
        node = createMessageNode({
          role: "assistant",
          progress: true,
          progressData: progress,
          at: progress.startedAt || Date.now()
        });
        node.classList.add("progress-enter");
      }
      if (node._progressLeaveTimer) {
        window.clearTimeout(node._progressLeaveTimer);
        node._progressLeaveTimer = null;
      }
      node.classList.remove("is-leaving");
      placeProgressNode(node, progress);
      const content = node.querySelector(".message-content");
      renderProgressContent(content, progress, true);
      requestAnimationFrame(() => node.classList.remove("progress-enter"));
      if (wasNearBottom || !existing) messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function renderMessages(messages) {
      messagesEl.innerHTML = "";
      messagesEl.classList.toggle("is-empty", messages.length === 0);

      if (!messages.length) {
        renderEmptyState(messagesEl, currentSettings);
        return;
      }

      for (const item of messages) {
        messagesEl.appendChild(createMessageNode(item));
      }

      const snap = state.snapshot();
      renderProgress(snap.runProgress, snap.activeSessionId);
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function renderSettings(settings) {
      currentSettings = settings;
      title.textContent = personaName(settings);
      modeLabel.textContent = permissionNames[settings.permissionMode || "workspace_full"] || settings.permissionMode || "完全权限";
      renderMessages(state.snapshot().messages);
    }

    function renderBusy(busy) {
      sendButton.disabled = false;
      sendButton.hidden = false;
      sendButton.title = busy ? "发送运行中纠偏，Shift+回车换行" : "回车发送，Shift+回车换行";
      sendButton.setAttribute("aria-label", busy ? "发送运行中纠偏" : "发送");
      interruptButton.hidden = !busy;
      interruptButton.disabled = !busy;
      uploadButton.disabled = busy;
      input.placeholder = busy ? "运行中可直接输入纠偏：比如“方向错了，回到大框架改成...”" : "输入任务或对话...";
      chatStatus.textContent = busy ? "后端执行中，可直接发送纠偏" : "就绪";
    }

    function collectDropFiles(dataTransfer) {
      const allFiles = Array.from(dataTransfer?.files || []);
      const itemFiles = Array.from(dataTransfer?.items || [])
        .filter((item) => item.kind === "file")
        .map((item) => item.getAsFile())
        .filter(Boolean);
      for (const file of itemFiles) {
        if (!allFiles.some((item) => item === file || (item.name === file.name && item.size === file.size && item.type === file.type))) {
          allFiles.push(file);
        }
      }
      return allFiles;
    }

    function dropHasFiles(dataTransfer) {
      return Array.from(dataTransfer?.types || []).includes("Files") || (dataTransfer?.files?.length || 0) > 0;
    }

    async function attachDroppedFiles(dataTransfer) {
      const allFiles = collectDropFiles(dataTransfer);
      if (!allFiles.length) return false;
      if (state.snapshot().busy) {
        chatStatus.textContent = "后台执行中，稍后再添加附件";
        return true;
      }
      chatStatus.textContent = "拖入附件处理中";
      try {
        const paths = [];
        const items = [];
        for (const file of allFiles.slice(0, 5)) {
          const filePath = String(file?.path || "");
          if (filePath) {
            paths.push(filePath);
            continue;
          }
          const dataUrl = await readFileAsDataUrl(file);
          items.push({
            name: file.name || "",
            type: file.type || "",
            size: Number(file.size || 0),
            dataUrl
          });
        }
        const result = await actions.pasteChatFiles({ paths, items });
        applyAttachmentResult(result, "已拖入：");
      } catch (error) {
        chatStatus.textContent = error?.message || String(error);
      }
      return true;
    }

    let composerDragDepth = 0;
    function setComposerDragActive(active) {
      form.classList.toggle("is-drag-over", Boolean(active));
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (sendButton.disabled) return;
      const text = input.value.trim();
      const attachments = pendingAttachments.slice(0, 5);
      if (!text && !attachments.length) return;
      input.value = "";
      resizeInput();
      if (state.snapshot().busy) {
        const result = await actions.guideRun?.(text);
        chatStatus.textContent = result?.ok ? "纠偏已送达，Code-X 将回到大框架调整" : (result?.error || "纠偏发送失败");
      } else {
        pendingAttachments = [];
        renderAttachmentTray();
        await actions.sendMessage(text, attachments);
      }
      input.focus();
    });

    interruptButton.addEventListener("click", async () => {
      if (interruptButton.disabled) return;
      interruptButton.disabled = true;
      chatStatus.textContent = "正在中断";
      try {
        const result = await actions.cancelRun?.();
        if (result?.ok || result?.interrupted || result?.canceled) {
          chatStatus.textContent = "已中断，可继续";
        } else {
          chatStatus.textContent = result?.error || "没有正在运行的任务";
        }
      } catch (error) {
        chatStatus.textContent = error?.message || String(error);
      } finally {
        interruptButton.disabled = !state.snapshot().busy;
      }
    });

    input.addEventListener("input", resizeInput);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
        event.preventDefault();
        form.requestSubmit();
      }
    });

    input.addEventListener("paste", async (event) => {
      const files = Array.from(event.clipboardData?.files || []);
      const itemFiles = Array.from(event.clipboardData?.items || [])
        .filter((item) => item.kind === "file")
        .map((item) => item.getAsFile())
        .filter(Boolean);
      const allFiles = [...files];
      for (const file of itemFiles) {
        if (!allFiles.some((item) => item === file || (item.name === file.name && item.size === file.size && item.type === file.type))) {
          allFiles.push(file);
        }
      }
      if (!allFiles.length) return;
      event.preventDefault();
      if (state.snapshot().busy) {
        chatStatus.textContent = "后端执行中，稍后再粘贴文件";
        return;
      }
      chatStatus.textContent = "粘贴文件处理中";
      try {
        const paths = [];
        const items = [];
        for (const file of allFiles.slice(0, 5)) {
          const filePath = String(file?.path || "");
          if (filePath) {
            paths.push(filePath);
            continue;
          }
          const dataUrl = await readFileAsDataUrl(file);
          items.push({
            name: file.name || "",
            type: file.type || "",
            size: Number(file.size || 0),
            dataUrl
          });
        }
        const result = await actions.pasteChatFiles({ paths, items });
        applyAttachmentResult(result, "已粘贴：");
      } catch (error) {
        chatStatus.textContent = error?.message || String(error);
      }
    });

    form.addEventListener("dragenter", (event) => {
      if (!dropHasFiles(event.dataTransfer)) return;
      event.preventDefault();
      composerDragDepth += 1;
      setComposerDragActive(true);
      event.dataTransfer.dropEffect = "copy";
    });

    form.addEventListener("dragover", (event) => {
      if (!dropHasFiles(event.dataTransfer)) return;
      event.preventDefault();
      setComposerDragActive(true);
      event.dataTransfer.dropEffect = "copy";
    });

    form.addEventListener("dragleave", (event) => {
      if (!dropHasFiles(event.dataTransfer)) return;
      event.preventDefault();
      composerDragDepth = Math.max(0, composerDragDepth - 1);
      if (composerDragDepth === 0) setComposerDragActive(false);
    });

    form.addEventListener("drop", async (event) => {
      if (!dropHasFiles(event.dataTransfer)) return;
      event.preventDefault();
      composerDragDepth = 0;
      setComposerDragActive(false);
      await attachDroppedFiles(event.dataTransfer);
    });

    messagesEl.addEventListener("click", (event) => {
      const localLink = event.target.closest("[data-open-path]");
      if (!localLink) return;
      event.preventDefault();
      actions.openPath(localLink.dataset.openPath);
    });

    attachmentTray.addEventListener("click", (event) => {
      const remove = event.target.closest("[data-remove-attachment]");
      if (remove) {
        pendingAttachments = pendingAttachments.filter((item) => attachmentKey(item) !== remove.dataset.removeAttachment);
        renderAttachmentTray();
        return;
      }
      const localLink = event.target.closest("[data-open-path]");
      if (!localLink) return;
      event.preventDefault();
      actions.openPath(localLink.dataset.openPath);
    });

    newChatButton.addEventListener("click", () => {
      actions.startNewConversation();
      input.value = "";
      pendingAttachments = [];
      renderAttachmentTray();
      resizeInput();
      input.focus();
    });
    clearButton.addEventListener("click", () => {
      pendingAttachments = [];
      renderAttachmentTray();
      actions.clearConversation();
    });
    uploadButton.addEventListener("click", async () => {
      if (uploadButton.disabled) return;
      uploadButton.disabled = true;
      chatStatus.textContent = "附件处理中";
      try {
        const result = await actions.chooseChatFiles();
        if (result?.canceled) {
          chatStatus.textContent = state.snapshot().busy ? "后端执行中" : "就绪";
          return;
        }
        applyAttachmentResult(result, "已处理：");
      } catch (error) {
        chatStatus.textContent = error?.message || String(error);
      } finally {
        uploadButton.disabled = state.snapshot().busy;
      }
    });

    bus.on("composer:set-text", (text) => {
      actions.setActivePage("chat");
      input.value = String(text || "");
      resizeInput();
      input.focus();
    });

    state.on("page", renderPage);
    state.on("messages", renderMessages);
    state.on("runProgress", (progress) => renderProgress(progress, state.snapshot().activeSessionId));
    state.on("settings", renderSettings);
    state.on("busy", renderBusy);
    renderPage(state.snapshot().activePage);
    renderMessages(state.snapshot().messages);
    renderSettings(state.snapshot().settings);
    renderBusy(state.snapshot().busy);
    renderAttachmentTray();
  }
};
