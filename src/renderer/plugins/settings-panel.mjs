import {
  humanizeBackendError,
  safeJsonParse,
  translateLabel,
  translateValue
} from "../core/formatters.mjs";
import { applyProviderPreset, providerOptions, providerPresets, providerThinkingCapability } from "./provider-presets.mjs";

function setRows(container, rows) {
  container.innerHTML = "";
  for (const [label, value] of rows) {
    const row = document.createElement("div");
    row.className = "kv-row";
    const key = document.createElement("span");
    key.className = "kv-key";
    key.textContent = translateLabel(label);
    const val = document.createElement("span");
    val.className = "kv-value";
    val.textContent = translateValue(value);
    val.title = String(value || "");
    row.appendChild(key);
    row.appendChild(val);
    container.appendChild(row);
  }
}

export const settingsPanelPlugin = {
  id: "settings-panel",
  slot: "conversation",
  order: 220,
  mount({ slot, state, actions }) {
    slot.insertAdjacentHTML(
      "beforeend",
      `
        <section class="page-panel settings-page" data-page-panel="settings">
          <header class="page-header">
            <div class="title-group">
              <span class="caption">设置</span>
              <h2>系统设置</h2>
            </div>
          </header>

          <section class="page-body settings-body">
            <section class="panel-card">
              <div class="panel-title">
                <span>模型服务配置</span>
                <div class="panel-actions">
                  <span id="modelSaveState" class="mini-pill">未修改</span>
                  <button id="settingsSaveModel" class="small-command" type="button">保存模型配置</button>
                  <button id="settingsRefreshConfig" class="small-command" type="button">刷新</button>
                </div>
              </div>
              <div class="settings-form">
                <label class="field-row">
                  <span>模型服务</span>
                  <select id="settingsModelService">
                    ${providerOptions()}
                  </select>
                </label>
                <label class="field-row">
                  <span>服务商标识</span>
                  <input id="settingsModelProvider" autocomplete="off" spellcheck="false" />
                </label>
                <label class="field-row">
                  <span>接口地址</span>
                  <input id="settingsModelBaseUrl" autocomplete="off" spellcheck="false" />
                </label>
                <label class="field-row">
                  <span>模型名</span>
                  <input id="settingsModelName" autocomplete="off" spellcheck="false" />
                </label>
                <label class="field-row">
                  <span>接口密钥</span>
                  <input id="settingsModelApiKey" type="password" autocomplete="off" spellcheck="false" placeholder="只保存在本机设置中" />
                </label>
                <div id="settingsThinkingRow" class="field-row">
                  <span>思考模式</span>
                  <label class="inline-check">
                    <input id="settingsModelThinkingEnabled" type="checkbox" />
                    <span>开启</span>
                  </label>
                </div>
                <label id="settingsThinkingDepthRow" class="field-row">
                  <span>思考深度</span>
                  <select id="settingsModelThinkingDepth"></select>
                </label>
              </div>
            </section>

            <section class="panel-card">
              <div class="panel-title">
                <span>后端脱敏状态</span>
                <span class="mini-pill">只读校验</span>
              </div>
              <div id="backendConfigRows" class="kv-list"></div>
            </section>
          </section>
        </section>
      `
    );

    const panel = slot.querySelector('[data-page-panel="settings"]');
    const modelSaveState = panel.querySelector("#modelSaveState");
    const modelSaveButton = panel.querySelector("#settingsSaveModel");
    const modelServiceInput = panel.querySelector("#settingsModelService");
    const modelProviderInput = panel.querySelector("#settingsModelProvider");
    const modelBaseUrlInput = panel.querySelector("#settingsModelBaseUrl");
    const modelNameInput = panel.querySelector("#settingsModelName");
    const modelApiKeyInput = panel.querySelector("#settingsModelApiKey");
    const thinkingRow = panel.querySelector("#settingsThinkingRow");
    const thinkingDepthRow = panel.querySelector("#settingsThinkingDepthRow");
    const modelThinkingEnabledInput = panel.querySelector("#settingsModelThinkingEnabled");
    const modelThinkingDepthInput = panel.querySelector("#settingsModelThinkingDepth");
    const refreshConfig = panel.querySelector("#settingsRefreshConfig");
    const backendConfigRows = panel.querySelector("#backendConfigRows");

    function markModelDirty() {
      modelSaveState.textContent = "待保存";
      modelSaveState.className = "mini-pill warn";
    }

    function renderPage(page) {
      panel.classList.toggle("active", page === "settings");
    }

    function renderThinkingControls(settings) {
      const capability = providerThinkingCapability(
        modelServiceInput.value || settings.modelService,
        modelProviderInput.value || settings.modelProvider
      );
      const supported = Boolean(capability.supported);
      const modes = Array.isArray(capability.modes) && capability.modes.length
        ? capability.modes
        : [{ value: "", label: "不可用" }];
      const selectedDepth = String(settings.modelThinkingDepth || capability.defaultDepth || modes[0]?.value || "");

      modelThinkingDepthInput.innerHTML = modes
        .map((item) => `<option value="${item.value}" ${item.value === selectedDepth ? "selected" : ""}>${item.label}</option>`)
        .join("");
      if (!modes.some((item) => item.value === selectedDepth) && modes[0]) {
        modelThinkingDepthInput.value = modes[0].value;
      }

      modelThinkingEnabledInput.checked = supported && settings.modelThinkingEnabled === true;
      modelThinkingEnabledInput.disabled = !supported;
      modelThinkingDepthInput.disabled = !supported || !modelThinkingEnabledInput.checked || modes.length <= 1;
      thinkingRow.classList.toggle("is-disabled", !supported);
      thinkingDepthRow.classList.toggle("is-disabled", !supported || !modelThinkingEnabledInput.checked || modes.length <= 1);
    }

    function draftThinkingSettings() {
      return {
        ...state.snapshot().settings,
        modelThinkingEnabled: modelThinkingEnabledInput.checked,
        modelThinkingDepth: modelThinkingDepthInput.value
      };
    }

    function renderSettings(settings) {
      modelServiceInput.value = settings.modelService || "openai_compatible";
      modelProviderInput.value = settings.modelProvider || providerPresets.openai_compatible.provider;
      modelBaseUrlInput.value = settings.modelBaseUrl || "";
      modelNameInput.value = settings.modelName || "";
      modelApiKeyInput.value = settings.modelApiKey || "";
      renderThinkingControls(settings);
    }

    function renderBackendConfig(configResult) {
      if (configResult.loading) {
        setRows(backendConfigRows, [
          ["HealthState", "loading"],
          ["SignalKind", "resource"]
        ]);
        return;
      }
      if (!configResult.ok) {
        setRows(backendConfigRows, [
          ["HealthState", "failed"],
          ["SignalKind", "resource"],
          ["错误", humanizeBackendError(configResult.stderr) || "未知错误"]
        ]);
        return;
      }
      const config = safeJsonParse(configResult.stdout);
      if (!config) {
        setRows(backendConfigRows, [
          ["HealthState", "parse_error"],
          ["SignalKind", "resource"],
          ["原始输出", "内容无法解析"]
        ]);
        return;
      }
      setRows(backendConfigRows, [
        ["CoreResult", "ok"],
        ["HealthState", "ok"],
        ["SignalKind", "resource"],
        ["provider", config.provider],
        ["model", config.model || "未配置"],
        ["base_url", config.base_url],
        ["api_key", config.api_key],
        ["thinking_enabled", config.thinking_enabled ? "enabled" : "disabled"],
        ["thinking_depth", config.thinking_depth || "未配置"],
        ["timeout", String(config.timeout ?? "")]
      ]);
    }

    modelServiceInput.addEventListener("change", () => {
      const next = applyProviderPreset(state.snapshot().settings, modelServiceInput.value);
      modelProviderInput.value = next.modelProvider;
      modelBaseUrlInput.value = next.modelBaseUrl;
      modelNameInput.value = next.modelName;
      modelThinkingEnabledInput.checked = false;
      modelThinkingDepthInput.value = next.modelThinkingDepth || "";
      renderThinkingControls(next);
      markModelDirty();
    });

    async function saveModelSettings() {
      modelSaveState.textContent = "热切换中";
      modelSaveState.className = "mini-pill";
      modelSaveButton.disabled = true;
      try {
        const result = await actions.hotSwitchSettings({
          modelService: modelServiceInput.value,
          modelProvider: modelProviderInput.value.trim(),
          modelBaseUrl: modelBaseUrlInput.value.trim(),
          modelName: modelNameInput.value.trim(),
          modelApiKey: modelApiKeyInput.value,
          modelThinkingEnabled: modelThinkingEnabledInput.checked && !modelThinkingEnabledInput.disabled,
          modelThinkingDepth: modelThinkingDepthInput.disabled ? "" : modelThinkingDepthInput.value
        });
        modelSaveState.textContent = result.ok ? "已热切换" : "已保存，后端校验失败";
        modelSaveState.className = `mini-pill ${result.ok ? "ok" : "failed"}`;
      } finally {
        modelSaveButton.disabled = false;
      }
    }

    modelSaveButton.addEventListener("click", saveModelSettings);

    refreshConfig.addEventListener("click", () => actions.refreshConfig());

    modelThinkingEnabledInput.addEventListener("change", () => {
      renderThinkingControls(draftThinkingSettings());
      markModelDirty();
    });
    modelProviderInput.addEventListener("input", () => renderThinkingControls(draftThinkingSettings()));
    modelProviderInput.addEventListener("change", () => renderThinkingControls(draftThinkingSettings()));

    for (const input of [modelProviderInput, modelBaseUrlInput, modelNameInput, modelApiKeyInput, modelThinkingDepthInput].filter(Boolean)) {
      input.addEventListener("change", markModelDirty);
      input.addEventListener("input", markModelDirty);
    }

    state.on("page", renderPage);
    state.on("settings", renderSettings);
    state.on("backendConfig", renderBackendConfig);
    const snap = state.snapshot();
    renderPage(snap.activePage);
    renderSettings(snap.settings);
    renderBackendConfig(snap.backendConfig);
  }
};
