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

function option(value, label) {
  return `<option value="${value}">${label}</option>`;
}

function card(title, status, body) {
  return `
    <section class="panel-card lifecycle-card">
      <div class="panel-title">
        <span>${title}</span>
        <span class="mini-pill">${status}</span>
      </div>
      <p class="lifecycle-copy">${body}</p>
    </section>
  `;
}

function pendingTitle(item) {
  return item.title || item.tool_name || item.kind || "自主更新候选";
}

function pendingSummary(item) {
  return item.summary || item.message || item.reason || item.neirong_zhaiyao || "等待用户确认后进入 Planner/P4 自我迭代入口。";
}

function pendingId(item) {
  return String(item.id || item.ticket_id || item.source_id || item.item_id || "").trim();
}

function pendingMeta(item) {
  return [
    item.risk_level ? `风险：${item.risk_level}` : "",
    item.source ? `来源：${item.source}` : "",
    item.reason ? `判定：${item.reason}` : "",
    item.confirmation_effect ? `确认后：${item.confirmation_effect}` : ""
  ].filter(Boolean).join(" · ");
}

function pendingUpdatesFromRuntime(runtimeStatus, settings = {}) {
  const lifecycle = runtimeStatus?.payload?.lifecycle || {};
  const direct = Array.isArray(lifecycle.pending_updates) ? lifecycle.pending_updates : [];
  const fromPool = Array.isArray(lifecycle.iteration_pool?.pending) ? lifecycle.iteration_pool.pending : [];
  const fromSettings = Array.isArray(settings.lifecyclePendingUpdates) ? settings.lifecyclePendingUpdates : [];
  if (direct.length) return direct;
  if (fromPool.length) return fromPool;
  return fromSettings;
}

function renderPending(container, pending = [], busyId = "") {
  container.innerHTML = "";
  if (!pending.length) {
    const empty = document.createElement("div");
    empty.className = "empty-detail";
    empty.textContent = "暂无需要确认的自主更新。";
    container.appendChild(empty);
    return;
  }
  for (const item of pending) {
    const id = pendingId(item);
    const busy = id && id === busyId;
    const meta = pendingMeta(item);
    const node = document.createElement("div");
    node.className = "lifecycle-candidate";
    node.innerHTML = `
      <div>
        <strong>${escapeHtml(pendingTitle(item))}</strong>
        <p>${escapeHtml(pendingSummary(item))}</p>
        ${meta ? `<p class="lifecycle-meta">${escapeHtml(meta)}</p>` : ""}
      </div>
      <div class="lifecycle-actions">
        <button class="small-command" type="button" data-lifecycle-action="confirm" data-update-id="${escapeHtml(id)}" ${busy ? "disabled" : ""}>确认</button>
        <button class="small-command subtle-command" type="button" data-lifecycle-action="deny" data-update-id="${escapeHtml(id)}" ${busy ? "disabled" : ""}>拒绝</button>
      </div>
    `;
    container.appendChild(node);
  }
}

const LEARNING_STATUS_NAMES = {
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
  not_configured: "未配置",
  ready: "就绪",
  queue_ready: "队列就绪",
  loading: "读取中"
};

const LEARNING_SOURCE_NAMES = {
  chat: "聊天",
  code: "代码任务",
  file: "文件任务",
  runtime_task: "运行任务",
  free_will: "自由意志",
  manual_approval: "手动触发",
  xintiao_p5: "自由意志心跳"
};

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
  return LEARNING_STATUS_NAMES[text] || text || "未知";
}

function sourceText(value) {
  const text = String(value || "");
  return LEARNING_SOURCE_NAMES[text] || text || "未知来源";
}

function pillClass(status, pending, failed) {
  if (failed > 0 || status === "needs_attention" || status === "failed") return "mini-pill failed";
  if (pending > 0 || status === "pending" || status === "pending_learning" || status === "pending_approval") return "mini-pill warn";
  if (status === "synced" || status === "ready" || status === "queue_ready" || status === "learned" || status === "candidate_ready") return "mini-pill ok";
  return "mini-pill";
}

function metric(label, value, hint = "") {
  return `
    <div class="learning-status-row">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      ${hint ? `<small>${escapeHtml(hint)}</small>` : ""}
    </div>
  `;
}

function renderLearningStatus(runtimeStatus, refs) {
  const payload = runtimeStatus?.payload || {};
  const learning = payload.learning || {};
  const pool = learning.jingyan_chi || {};
  const skillQueue = learning.skill_queue || {};
  const toolRequests = learning.tool_requests || {};
  const latest = Array.isArray(pool.latest) ? pool.latest : [];
  const pending = numberValue(pool.pending_learning);
  const failed = numberValue(pool.failed);
  const status = runtimeStatus?.loading ? "loading" : (learning.status || "empty");

  refs.pill.textContent = runtimeStatus?.loading ? "读取中" : statusText(status);
  refs.pill.className = pillClass(status, pending, failed);
  refs.grid.innerHTML = [
    metric("经验池", numberValue(pool.total), pool.path ? "已接入" : "未定位"),
    metric("待学习", pending),
    metric("已生成候选", numberValue(pool.candidate_ready)),
    metric("已学习", numberValue(pool.learned) + numberValue(pool.learned_no_asset)),
    metric("技能候选队列", numberValue(skillQueue.draft_versions), statusText(skillQueue.status)),
    metric("工具生产请求", numberValue(toolRequests.production_requests), statusText(toolRequests.status))
  ].join("");

  refs.list.innerHTML = "";
  if (!runtimeStatus?.ok) {
    const empty = document.createElement("div");
    empty.className = "empty-detail";
    empty.textContent = runtimeStatus?.text || "运行内核状态未连接。";
    refs.list.appendChild(empty);
    return;
  }
  if (!latest.length) {
    const empty = document.createElement("div");
    empty.className = "empty-detail";
    empty.textContent = "暂无经验池记录。";
    refs.list.appendChild(empty);
    return;
  }

  for (const item of latest) {
    const row = document.createElement("div");
    row.className = "learning-candidate-row";
    const summary = item.summary || item.task_preview || item.learning_result || "暂无学习摘要";
    const labels = [
      `来源：${sourceText(item.source)}`,
      item.task_preview ? `原任务：${item.task_preview}` : "",
      item.judge_reason ? `判定理由：${item.judge_reason}` : "",
      item.learning_result ? `学习结果：${item.learning_result}` : "",
      item.has_skill ? `技能候选：${item.skill_name || "已生成"}` : "",
      item.has_tool ? `工具请求：${item.tool_name || "已生成"}` : "",
      item.last_error ? `错误：${item.last_error}` : ""
    ].filter(Boolean);
    row.innerHTML = `
      <div>
        <strong>学习内容：${escapeHtml(summary)}</strong>
        <p>${escapeHtml(labels.join(" · ") || "等待自动学习链处理")}</p>
      </div>
      <span class="${pillClass(item.status, item.status === "pending_learning" ? 1 : 0, item.status === "failed" ? 1 : 0)}">${escapeHtml(statusText(item.status))}</span>
    `;
    refs.list.appendChild(row);
  }
}

export const lifecyclePanelPlugin = {
  id: "lifecycle-panel",
  slot: "conversation",
  order: 218,
  mount({ slot, state, actions }) {
    slot.insertAdjacentHTML(
      "beforeend",
      `
        <section class="page-panel lifecycle-page" data-page-panel="lifecycle">
          <header class="page-header">
            <div class="title-group">
              <span class="caption">生命周期</span>
              <h2>生命周期系统插件</h2>
            </div>
            <div class="commandbar-meta">
              <span class="mini-pill">生命链路</span>
            </div>
          </header>

          <section class="page-body lifecycle-body">
            <div class="lifecycle-grid">
              ${card("自我学习", "自动运行", "从任务结束后的执行证据、失败恢复、用户修正中提炼经验；能学习的内容自动进入学习链，并沉淀为经验、技能候选或工具请求。")}
              ${card("自主迭代", "需确认", "涉及改系统、改核心逻辑、合入新能力的动作只生成候选，不自动生效，必须经过用户确认和质量门。")}
              ${card("自由意志", "自动心跳", "只在没有用户任务时做心跳检查，生成观察、学习或自愈候选，不抢占当前任务；知识技能类内容可以自动学习。")}
              ${card("自我愈合", "只提建议", "发现失败链、配置缺口、工具缺失或上下文污染时，进入恢复计划和用户确认流程。")}
            </div>

            <section class="panel-card wide-card">
              <div class="panel-title">
                <span>待确认自主更新</span>
                <span id="lifecyclePendingState" class="mini-pill">等待刷新</span>
              </div>
              <div id="lifecyclePending" class="lifecycle-pending"></div>
            </section>
          </section>
        </section>
      `
    );

    const panel = slot.querySelector('[data-page-panel="lifecycle"]');
    const pending = panel.querySelector("#lifecyclePending");
    const pendingState = panel.querySelector("#lifecyclePendingState");
    let pendingBusyId = "";

    function renderPage(page) {
      panel.classList.toggle("active", page === "lifecycle");
      if (page === "lifecycle") {
        actions.refreshStatus?.();
      }
    }

    function renderSettings(settings) {
      renderLifecyclePending(state.snapshot().runtimeStatus);
    }

    function renderLifecyclePending(runtimeStatus) {
      const updates = pendingUpdatesFromRuntime(runtimeStatus, state.snapshot().settings);
      renderPending(pending, updates, pendingBusyId);
      if (runtimeStatus?.loading) {
        pendingState.textContent = "读取中";
        pendingState.className = "mini-pill";
      } else if (updates.length) {
        pendingState.textContent = `${updates.length} 项待确认`;
        pendingState.className = "mini-pill warn";
      } else if (runtimeStatus?.ok === false) {
        pendingState.textContent = "读取失败";
        pendingState.className = "mini-pill failed";
      } else {
        pendingState.textContent = "无待确认";
        pendingState.className = "mini-pill ok";
      }
    }

    pending.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-lifecycle-action]");
      if (!button) return;
      const updateId = button.dataset.updateId || "";
      const action = button.dataset.lifecycleAction || "confirm";
      if (!updateId) return;
      pendingBusyId = updateId;
      pendingState.textContent = action === "deny" ? "拒绝中" : "确认中";
      pendingState.className = "mini-pill";
      renderLifecyclePending(state.snapshot().runtimeStatus);
      try {
        const result = action === "deny"
          ? await actions.denyLifecycleUpdate?.(updateId)
          : await actions.confirmLifecycleUpdate?.(updateId);
        pendingState.textContent = result?.ok ? (action === "deny" ? "已拒绝" : "已确认") : (result?.error || "处理失败");
        pendingState.className = `mini-pill ${result?.ok ? "ok" : "failed"}`;
      } catch (error) {
        pendingState.textContent = error?.message || "处理失败";
        pendingState.className = "mini-pill failed";
      } finally {
        pendingBusyId = "";
        renderLifecyclePending(state.snapshot().runtimeStatus);
      }
    });

    state.on("page", renderPage);
    state.on("settings", renderSettings);
    state.on("runtimeStatus", renderLifecyclePending);
    const snap = state.snapshot();
    renderPage(snap.activePage);
    renderSettings(snap.settings);
    renderLifecyclePending(snap.runtimeStatus);
  }
};
