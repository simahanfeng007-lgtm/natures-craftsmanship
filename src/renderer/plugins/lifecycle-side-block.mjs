const FREQUENCY_OPTIONS = {
  manual: "手动触发",
  hourly: "每小时",
  daily: "每天",
  weekly: "每周"
};

const SCOPE_OPTIONS = {
  workspace: "当前工作区",
  knowledge: "文件知识库",
  runtime: "运行经验",
  all_safe: "安全范围内全部"
};

const STATUS_NAMES = {
  empty: "暂无经验",
  pending: "自动学习中",
  synced: "已同步",
  needs_attention: "需处理",
  candidate_ready: "已生成候选",
  learned: "已学习",
  learned_no_asset: "已学习，无新增资产",
  pending_learning: "待自动学习",
  pending_approval: "待自动学习",
  skipped_by_user: "已跳过",
  skipped_by_judge: "判定无需学习",
  failed: "失败",
  not_configured: "未启用",
  ready: "就绪",
  queue_ready: "队列就绪",
  loading: "读取中"
};

const SOURCE_NAMES = {
  chat: "聊天",
  code: "代码任务",
  file: "文件任务",
  runtime_task: "运行任务",
  free_will: "自由意志",
  manual_approval: "手动触发",
  xintiao_p5: "自由意志心跳"
};

function option(value, label) {
  return `<option value="${value}">${label}</option>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function numberValue(value) {
  const next = Number(value || 0);
  return Number.isFinite(next) ? next : 0;
}

function statusText(value) {
  const text = String(value || "");
  return STATUS_NAMES[text] || text || "未知";
}

function sourceText(value) {
  const text = String(value || "");
  return SOURCE_NAMES[text] || text || "未知来源";
}

function pillClass(status, pending, failed) {
  if (failed > 0 || status === "needs_attention" || status === "failed") return "mini-pill failed";
  if (pending > 0 || status === "pending" || status === "pending_learning" || status === "pending_approval") return "mini-pill warn";
  if (status === "synced" || status === "ready" || status === "queue_ready" || status === "learned" || status === "candidate_ready") return "mini-pill ok";
  return "mini-pill";
}

function metric(label, value, hint = "") {
  return `
    <div class="side-learning-metric">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      ${hint ? `<small>${escapeHtml(hint)}</small>` : ""}
    </div>
  `;
}

function renderLearningStatus(runtimeStatus, refs) {
  const payload = runtimeStatus?.payload || {};
  const learning = payload.learning || {};
  const pool = learning.learning_cards || {};
  const skillQueue = learning.skill_queue || {};
  const toolRequests = learning.tool_requests || {};
  const latest = Array.isArray(pool.latest) ? pool.latest : [];
  const pending = numberValue(pool.pending_learning);
  const failed = numberValue(pool.failed);
  const status = runtimeStatus?.loading ? "loading" : (learning.status || "empty");

  refs.pill.textContent = runtimeStatus?.loading ? "读取中" : statusText(status);
  refs.pill.className = pillClass(status, pending, failed);
  refs.grid.innerHTML = [
    metric("学习卡", numberValue(pool.total), "已接入"),
    metric("待学习", pending),
    metric("已生成候选", numberValue(pool.candidate_ready)),
    metric("已学习", numberValue(pool.learned) + numberValue(pool.learned_no_asset)),
    metric("技能候选队列", numberValue(skillQueue.draft_versions), statusText(skillQueue.status)),
    metric("工具生产请求", numberValue(toolRequests.production_requests), statusText(toolRequests.status))
  ].join("");

  refs.list.innerHTML = "";
  if (!runtimeStatus?.ok) {
    const empty = document.createElement("div");
    empty.className = "history-empty";
    empty.textContent = runtimeStatus?.text || "运行内核状态未连接。";
    refs.list.appendChild(empty);
    return;
  }
  if (!latest.length) {
    const empty = document.createElement("div");
    empty.className = "history-empty";
    empty.textContent = "暂无学习卡记录。";
    refs.list.appendChild(empty);
    return;
  }

  for (const item of latest.slice(0, 3)) {
    const row = document.createElement("div");
    row.className = "side-learning-row";
    const summary = item.summary || item.task_preview || item.learning_result || "暂无学习摘要";
    const labels = [
      item.priority ? `优先级：${item.priority}${Number.isFinite(Number(item.priority_score)) ? ` / ${item.priority_score}` : ""}` : "",
      sourceText(item.source),
      item.has_skill ? `技能：${item.skill_name || "已生成"}` : "",
      item.has_tool ? `工具：${item.tool_name || "已生成"}` : "",
      item.last_error ? `错误：${item.last_error}` : ""
    ].filter(Boolean);
    row.innerHTML = `
      <strong>${escapeHtml(summary)}</strong>
      <p>${escapeHtml(labels.join(" · ") || "等待自动学习链处理")}</p>
      <span class="${pillClass(item.status, item.status === "pending_learning" ? 1 : 0, item.status === "failed" ? 1 : 0)}">${escapeHtml(statusText(item.status))}</span>
    `;
    refs.list.appendChild(row);
  }
}

export const lifecycleSideBlockPlugin = {
  id: "lifecycle-side-block",
  slot: "context",
  order: 111,
  mount({ slot, state, actions }) {
    slot.insertAdjacentHTML(
      "beforeend",
      `
        <section id="lifecycleSidePanel" class="side-section lifecycle-side-panel" hidden>
          <div class="section-heading">
            <span>生命配置</span>
            <span id="lifecycleSideSaveState" class="mini-pill">未修改</span>
          </div>

          <div class="life-side-card">
            <div class="life-side-title">
              <span>自由意志设置</span>
              <button id="lifecycleSideSave" class="small-command" type="button">保存</button>
            </div>
            <div class="settings-form life-side-form">
              <label class="field-row">
                <span>频率</span>
                <select id="sideFreeWillFrequency">
                  ${Object.entries(FREQUENCY_OPTIONS).map(([value, label]) => option(value, label)).join("")}
                </select>
              </label>
              <label class="field-row">
                <span>范围</span>
                <select id="sideLearningScope">
                  ${Object.entries(SCOPE_OPTIONS).map(([value, label]) => option(value, label)).join("")}
                </select>
              </label>
            </div>
          </div>

          <div class="life-side-card">
            <div class="life-side-title">
              <span>自主学习运行状态</span>
              <span id="sideLearningStatusPill" class="mini-pill">未读取</span>
            </div>
            <div id="sideLearningStatusGrid" class="side-learning-grid"></div>
            <div class="life-side-title compact">
              <span>最近学习内容</span>
              <button id="sideLearningRefresh" class="small-command" type="button">刷新</button>
            </div>
            <div id="sideLearningCandidateList" class="side-learning-list"></div>
          </div>
        </section>
      `
    );

    const panel = slot.querySelector("#lifecycleSidePanel");
    const saveState = panel.querySelector("#lifecycleSideSaveState");
    const saveButton = panel.querySelector("#lifecycleSideSave");
    const frequencyInput = panel.querySelector("#sideFreeWillFrequency");
    const scopeInput = panel.querySelector("#sideLearningScope");
    const refreshButton = panel.querySelector("#sideLearningRefresh");
    const refs = {
      pill: panel.querySelector("#sideLearningStatusPill"),
      grid: panel.querySelector("#sideLearningStatusGrid"),
      list: panel.querySelector("#sideLearningCandidateList")
    };

    function markDirty() {
      saveState.textContent = "待保存";
      saveState.className = "mini-pill warn";
    }

    function renderPage(page) {
      panel.hidden = page !== "lifecycle";
      if (page === "lifecycle") actions.refreshStatus?.();
    }

    function renderSettings(settings) {
      frequencyInput.value = settings.lifecycleFreeWillFrequency || "manual";
      scopeInput.value = settings.lifecycleLearningScope || "workspace";
      saveState.textContent = "未修改";
      saveState.className = "mini-pill";
    }

    saveButton.addEventListener("click", async () => {
      saveState.textContent = "保存中";
      saveState.className = "mini-pill";
      await actions.saveSettings({
        lifecycleFreeWillFrequency: frequencyInput.value,
        lifecycleLearningScope: scopeInput.value
      });
      saveState.textContent = "已保存";
      saveState.className = "mini-pill ok";
    });
    refreshButton.addEventListener("click", () => actions.refreshStatus?.());
    for (const input of [frequencyInput, scopeInput]) {
      input.addEventListener("change", markDirty);
    }

    state.on("page", renderPage);
    state.on("settings", renderSettings);
    state.on("runtimeStatus", (runtimeStatus) => renderLearningStatus(runtimeStatus, refs));

    const snap = state.snapshot();
    renderPage(snap.activePage);
    renderSettings(snap.settings);
    renderLearningStatus(snap.runtimeStatus, refs);
  }
};
