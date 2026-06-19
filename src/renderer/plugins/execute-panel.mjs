import {
  concisePath,
  formatElapsedMs,
  humanizeBackendError,
  humanizeBackendText,
  permissionNames,
  translateValue
} from "../core/formatters.mjs";

const PERMISSION_LABELS = permissionNames;

const VALUE_LABELS = {
  ready: "就绪",
  ok: "正常",
  failed: "失败",
  true: "是",
  false: "否",
  empty: "暂无",
  not_enabled: "未启用",
};

function option(value, label) {
  return `<option value="${value}">${label}</option>`;
}

function displayValue(value) {
  const text = String(value ?? "").trim();
  if (!text) return "暂无";
  return VALUE_LABELS[text] || translateValue(text);
}

function setRows(container, rows) {
  container.innerHTML = "";
  for (const [label, value, title] of rows) {
    const row = document.createElement("div");
    row.className = "kv-row";
    const key = document.createElement("span");
    key.className = "kv-key";
    key.textContent = label;
    const val = document.createElement("span");
    val.className = "kv-value";
    val.textContent = displayValue(value);
    val.title = String(title || value || "");
    row.appendChild(key);
    row.appendChild(val);
    container.appendChild(row);
  }
}

function backendOutput(run) {
  const stdout = humanizeBackendText(run.stdout) || String(run.stdout || "").trim();
  const stderr = humanizeBackendError(run.stderr) || String(run.stderr || "").trim();
  if (stderr) return `${stdout || "暂无后端输出。"}\n\n[错误输出]\n${stderr}`;
  return stdout || "暂无后端输出。";
}

export const executePanelPlugin = {
  id: "execute-panel",
  slot: "conversation",
  order: 210,
  mount({ slot, state, actions }) {
    slot.insertAdjacentHTML(
      "beforeend",
      `
        <section class="page-panel execute-page" data-page-panel="execute">
          <header class="page-header">
            <div class="title-group">
              <span class="caption">执行</span>
              <h2>执行界面</h2>
            </div>
            <div class="commandbar-meta">
              <span id="executeStatusPill" class="run-pill">空闲</span>
              <button id="executeRefresh" class="small-command" type="button">刷新后端</button>
            </div>
          </header>

          <section class="page-body execute-body">
            <div class="dashboard-grid">
              <section class="panel-card">
                <div class="panel-title">
                  <span>后端状态</span>
                  <span id="backendHealth" class="mini-pill">读取中</span>
                </div>
                <div id="backendStatusRows" class="kv-list"></div>
              </section>

              <section class="panel-card">
                <div class="panel-title">
                  <span>记忆系统状态</span>
                  <span id="memoryHealth" class="mini-pill">读取中</span>
                </div>
                <div id="memoryStatusRows" class="kv-list"></div>
              </section>

              <section class="panel-card">
                <div class="panel-title">
                  <span>执行参数</span>
                  <span id="executeSettingsState" class="mini-pill">未修改</span>
                </div>
                <div class="settings-form">
                  <label class="field-row">
                    <span>权限</span>
                    <select id="executePermissionMode">
                      ${Object.entries(PERMISSION_LABELS).map(([value, label]) => option(value, label)).join("")}
                    </select>
                  </label>
                  <label class="field-row">
                    <span>最大步数</span>
                    <input id="executeMaxSteps" type="number" min="1" max="180" step="1" />
                  </label>
                  <div class="execute-save-row">
                    <button id="executeSaveSettings" class="block-command" type="button">保存并热切换</button>
                  </div>
                </div>
              </section>

              <section class="panel-card">
                <div class="panel-title">
                  <span>最近一次运行</span>
                  <span id="lastRunPill" class="mini-pill">空闲</span>
                </div>
                <div id="lastRunRows" class="kv-list"></div>
              </section>
            </div>

            <section class="panel-card wide-card">
              <div class="panel-title">
                <span>后端输出摘要</span>
                <span class="mini-pill">运行结果</span>
              </div>
              <pre id="executeOutput" class="compact-pre"></pre>
            </section>
          </section>
        </section>
      `
    );

    const panel = slot.querySelector('[data-page-panel="execute"]');
    const executeStatusPill = panel.querySelector("#executeStatusPill");
    const backendHealth = panel.querySelector("#backendHealth");
    const backendStatusRows = panel.querySelector("#backendStatusRows");
    const memoryHealth = panel.querySelector("#memoryHealth");
    const memoryStatusRows = panel.querySelector("#memoryStatusRows");
    const settingsState = panel.querySelector("#executeSettingsState");
    const permissionInput = panel.querySelector("#executePermissionMode");
    const maxStepsInput = panel.querySelector("#executeMaxSteps");
    const saveSettings = panel.querySelector("#executeSaveSettings");
    const lastRunPill = panel.querySelector("#lastRunPill");
    const lastRunRows = panel.querySelector("#lastRunRows");
    const output = panel.querySelector("#executeOutput");
    const refreshButton = panel.querySelector("#executeRefresh");

    function markDirty() {
      settingsState.textContent = "待保存";
      settingsState.className = "mini-pill warn";
    }

    function renderPage(page) {
      panel.classList.toggle("active", page === "execute");
    }

    function renderRuntimeStatus(status) {
      backendHealth.textContent = status.loading ? "刷新中" : status.ok ? "可用" : "异常";
      backendHealth.className = `mini-pill ${status.loading ? "" : status.ok ? "ok" : "failed"}`;
      const data = status.payload || {};
      const coreResult = data.core_result || {};
      const learning = data.learning || {};
      const pool = learning.learning_cards || {};
      setRows(backendStatusRows, [
        ["内核导入", data.kernel_importable === undefined ? "未知" : data.kernel_importable],
        ["工作区", concisePath(data.workspace), data.workspace],
        ["模型服务", data.provider],
        ["模型", data.model || "未配置"],
        ["SignalKind", coreResult.signal_kind || "resource"],
        ["HealthState", coreResult.health_state || "healthy"],
        ["最大步数", data.max_steps]
      ]);
      memoryHealth.textContent = status.loading ? "刷新中" : "就绪";
      memoryHealth.className = `mini-pill ${status.loading ? "" : "ok"}`;
      setRows(memoryStatusRows, [
        ["经验池记录", pool.total ?? "0"],
        ["待自动学习", pool.pending_learning ?? "0"],
        ["已学习", Number(pool.learned || 0) + Number(pool.learned_no_asset || 0)],
        ["技能候选", learning.skill_queue?.draft_versions ?? "0"],
        ["工具请求", learning.tool_requests?.production_requests ?? "0"]
      ]);
    }

    function renderSettings(settings) {
      permissionInput.value = settings.permissionMode || "workspace_full";
      maxStepsInput.value = String(settings.maxSteps || 20);
    }

    function renderRun(run) {
      const running = run.phase === "running";
      const idle = run.phase === "idle";
      executeStatusPill.textContent = running ? "运行中" : idle ? "空闲" : run.ok ? "完成" : "失败";
      executeStatusPill.className = `run-pill ${running ? "running" : idle ? "" : run.ok ? "ok" : "failed"}`;
      lastRunPill.textContent = executeStatusPill.textContent;
      lastRunPill.className = `mini-pill ${running ? "" : idle ? "" : run.ok ? "ok" : "failed"}`;
      setRows(lastRunRows, [
        ["退出码", run.code === "" || run.code === undefined ? "未运行" : String(run.code)],
        ["耗时", formatElapsedMs(run.elapsedMs)],
        ["权限", PERMISSION_LABELS[run.permissionMode] || translateValue(run.permissionMode || state.snapshot().settings.permissionMode || "workspace_full")],
        ["工作区", concisePath(run.workspace), run.workspace]
      ]);
      output.textContent = backendOutput(run);
    }

    refreshButton.addEventListener("click", async () => {
      await actions.refreshStatus();
      await actions.refreshConfig();
    });
    saveSettings.addEventListener("click", async () => {
      settingsState.textContent = "热切换中";
      settingsState.className = "mini-pill";
      saveSettings.disabled = true;
      try {
        const result = await actions.hotSwitchSettings({
          mode: "auto",
          permissionMode: permissionInput.value,
          maxSteps: Number(maxStepsInput.value || 20)
        }, { refreshConfig: false });
        settingsState.textContent = result.ok ? "已热切换" : "后端切换失败";
        settingsState.className = `mini-pill ${result.ok ? "ok" : "failed"}`;
      } finally {
        saveSettings.disabled = false;
      }
    });
    for (const input of [permissionInput, maxStepsInput]) {
      input.addEventListener("change", markDirty);
      input.addEventListener("input", markDirty);
    }

    state.on("page", renderPage);
    state.on("runtimeStatus", renderRuntimeStatus);
    state.on("settings", renderSettings);
    state.on("run", renderRun);
    const snap = state.snapshot();
    renderPage(snap.activePage);
    renderRuntimeStatus(snap.runtimeStatus);
    renderSettings(snap.settings);
    renderRun(snap.lastRun);
  }
};
