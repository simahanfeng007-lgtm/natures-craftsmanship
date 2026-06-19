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
                <label class="field-row">
                  <span>多模态输入</span>
                  <select id="settingsModelMultimodalInput">
                    <option value="auto">自动判定</option>
                    <option value="1">支持</option>
                    <option value="0">不支持</option>
                  </select>
                </label>
                <label class="field-row">
                  <span>图片输入</span>
                  <select id="settingsModelImageInput">
                    <option value="auto">自动判定</option>
                    <option value="1">支持</option>
                    <option value="0">不支持</option>
                  </select>
                </label>
                <label class="field-row">
                  <span>视频输入</span>
                  <select id="settingsModelVideoInput">
                    <option value="auto">自动判定</option>
                    <option value="1">支持</option>
                    <option value="0">不支持</option>
                  </select>
                </label>
                <label class="field-row">
                  <span>音频输入</span>
                  <select id="settingsModelAudioInput">
                    <option value="auto">自动判定</option>
                    <option value="1">支持</option>
                    <option value="0">不支持</option>
                  </select>
                </label>
                <label class="field-row">
                  <span>联网搜索</span>
                  <select id="settingsWebSearchProvider">
                    <option value="auto">自动</option>
                    <option value="public_rss">内置检索</option>
                    <option value="provider_only">仅模型搜索</option>
                  </select>
                </label>
                <label class="field-row">
                  <span>图片生成</span>
                  <select id="settingsImageGenerationMode">
                    <option value="auto">模型优先</option>
                    <option value="provider_only">仅模型生成</option>
                    <option value="local">本地兜底</option>
                  </select>
                </label>
              </div>
            </section>

            <section class="panel-card">
              <div class="panel-title">
                <span>消息通道</span>
                <div class="panel-actions">
                  <span id="messageChannelState" class="mini-pill">未读取</span>
                  <button id="settingsRefreshMessageChannels" class="small-command" type="button">刷新</button>
                </div>
              </div>
              <div id="messageChannelRows" class="channel-list" aria-live="polite"></div>
              <div id="messageChannelConnectDialog" class="channel-connect-dialog" role="dialog" aria-modal="true" aria-labelledby="messageChannelConnectTitle" hidden>
                <div class="channel-connect-backdrop" data-channel-connect-close></div>
                <div class="channel-connect-sheet">
                  <div class="channel-connect-head">
                    <strong id="messageChannelConnectTitle">消息通道连接</strong>
                    <button class="icon-command" type="button" data-channel-connect-close aria-label="关闭">×</button>
                  </div>
                  <div class="channel-connect-body">
                    <div id="messageChannelQrBox" class="channel-qr-box" hidden>
                      <img id="messageChannelQrImage" alt="连接二维码" />
                    </div>
                    <div id="messageChannelNoQr" class="channel-no-qr" hidden>等待平台适配器提供二维码。</div>
                    <a id="messageChannelAuthLink" class="channel-auth-link" href="#" target="_blank" rel="noreferrer" hidden>打开授权链接</a>
                    <p id="messageChannelConnectDetail" class="channel-connect-detail"></p>
                    <code id="messageChannelConnectEndpoint" class="channel-connect-endpoint"></code>
                  </div>
                </div>
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
    const modelMultimodalInput = panel.querySelector("#settingsModelMultimodalInput");
    const modelImageInput = panel.querySelector("#settingsModelImageInput");
    const modelVideoInput = panel.querySelector("#settingsModelVideoInput");
    const modelAudioInput = panel.querySelector("#settingsModelAudioInput");
    const webSearchProviderInput = panel.querySelector("#settingsWebSearchProvider");
    const imageGenerationModeInput = panel.querySelector("#settingsImageGenerationMode");
    const refreshConfig = panel.querySelector("#settingsRefreshConfig");
    const messageChannelState = panel.querySelector("#messageChannelState");
    const refreshMessageChannels = panel.querySelector("#settingsRefreshMessageChannels");
    const messageChannelRows = panel.querySelector("#messageChannelRows");
    const messageChannelConnectDialog = panel.querySelector("#messageChannelConnectDialog");
    const messageChannelConnectTitle = panel.querySelector("#messageChannelConnectTitle");
    const messageChannelQrBox = panel.querySelector("#messageChannelQrBox");
    const messageChannelQrImage = panel.querySelector("#messageChannelQrImage");
    const messageChannelNoQr = panel.querySelector("#messageChannelNoQr");
    const messageChannelAuthLink = panel.querySelector("#messageChannelAuthLink");
    const messageChannelConnectDetail = panel.querySelector("#messageChannelConnectDetail");
    const messageChannelConnectEndpoint = panel.querySelector("#messageChannelConnectEndpoint");
    const backendConfigRows = panel.querySelector("#backendConfigRows");
    let activeModelService = "";
    let modelProviderProfiles = {};
    let messageChannels = { loading: true, channels: {} };
    let connectingChannel = "";

    function cloneModelProviderProfiles(value) {
      if (!value || typeof value !== "object" || Array.isArray(value)) return {};
      return Object.fromEntries(
        Object.entries(value)
          .filter(([key, profile]) => key && profile && typeof profile === "object" && !Array.isArray(profile))
          .map(([key, profile]) => [String(key), { ...profile }])
      );
    }

    function currentModelProfile(serviceId = modelServiceInput.value) {
      return {
        modelService: String(serviceId || "").trim(),
        modelProvider: modelProviderInput.value.trim(),
        modelBaseUrl: modelBaseUrlInput.value.trim(),
        modelName: modelNameInput.value.trim(),
        modelApiKey: modelApiKeyInput.value,
        modelThinkingEnabled: modelThinkingEnabledInput.checked && !modelThinkingEnabledInput.disabled,
        modelThinkingDepth: modelThinkingDepthInput.disabled ? "" : modelThinkingDepthInput.value,
        modelMultimodalInput: modelMultimodalInput.value,
        modelImageInput: modelImageInput.value,
        modelVideoInput: modelVideoInput.value,
        modelAudioInput: modelAudioInput.value,
        webSearchProvider: webSearchProviderInput.value,
        imageGenerationMode: imageGenerationModeInput.value,
        updatedAt: new Date().toISOString()
      };
    }

    function rememberCurrentModelProfile(serviceId = activeModelService || modelServiceInput.value) {
      const key = String(serviceId || "").trim();
      if (!key) return modelProviderProfiles;
      modelProviderProfiles = {
        ...modelProviderProfiles,
        [key]: currentModelProfile(key)
      };
      return modelProviderProfiles;
    }

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
      modelProviderProfiles = cloneModelProviderProfiles(settings.modelProviderProfiles);
      if (settings.modelService) {
        const service = String(settings.modelService);
        modelProviderProfiles[service] = {
          ...(modelProviderProfiles[service] || {}),
          modelService: service,
          modelProvider: settings.modelProvider || "",
          modelBaseUrl: settings.modelBaseUrl || "",
          modelName: settings.modelName || "",
          modelApiKey: settings.modelApiKey || "",
          modelThinkingEnabled: settings.modelThinkingEnabled === true,
          modelThinkingDepth: settings.modelThinkingDepth || "",
          modelMultimodalInput: settings.modelMultimodalInput || "auto",
          modelImageInput: settings.modelImageInput || "auto",
          modelVideoInput: settings.modelVideoInput || "auto",
          modelAudioInput: settings.modelAudioInput || "auto",
          webSearchProvider: settings.webSearchProvider || "auto",
          imageGenerationMode: settings.imageGenerationMode || "auto"
        };
      }
      modelServiceInput.value = settings.modelService || "openai_compatible";
      activeModelService = modelServiceInput.value;
      modelProviderInput.value = settings.modelProvider || providerPresets.openai_compatible.provider;
      modelBaseUrlInput.value = settings.modelBaseUrl || "";
      modelNameInput.value = settings.modelName || "";
      modelApiKeyInput.value = settings.modelApiKey || "";
      modelMultimodalInput.value = settings.modelMultimodalInput || "auto";
      modelImageInput.value = settings.modelImageInput || "auto";
      modelVideoInput.value = settings.modelVideoInput || "auto";
      modelAudioInput.value = settings.modelAudioInput || "auto";
      webSearchProviderInput.value = settings.webSearchProvider || "auto";
      imageGenerationModeInput.value = settings.imageGenerationMode || "auto";
      renderThinkingControls(settings);
    }

    function closeMessageChannelConnectDialog() {
      messageChannelConnectDialog.hidden = true;
      messageChannelQrImage.removeAttribute("src");
    }

    function openMessageChannelConnectDialog(connection = {}, fallback = {}) {
      const label = connection.label || fallback.label || "消息通道";
      const qrUrl = String(connection.qrUrl || "").trim();
      const authUrl = String(connection.authUrl || "").trim();
      const detail = String(connection.detail || "天工消息通道核心已启动，等待平台适配器提供连接二维码。");
      const endpoint = String(connection.endpoint || "").trim();

      messageChannelConnectTitle.textContent = `${label}连接`;
      messageChannelConnectDetail.textContent = detail;
      messageChannelConnectEndpoint.textContent = endpoint ? `天工入口 ${endpoint}` : "";
      messageChannelConnectEndpoint.hidden = !endpoint;

      if (qrUrl) {
        messageChannelQrImage.src = qrUrl;
        messageChannelQrBox.hidden = false;
        messageChannelNoQr.hidden = true;
      } else {
        messageChannelQrImage.removeAttribute("src");
        messageChannelQrBox.hidden = true;
        messageChannelNoQr.hidden = false;
      }

      if (authUrl) {
        messageChannelAuthLink.href = authUrl;
        messageChannelAuthLink.hidden = false;
      } else {
        messageChannelAuthLink.removeAttribute("href");
        messageChannelAuthLink.hidden = true;
      }

      messageChannelConnectDialog.hidden = false;
    }

    const channelDefaults = {
      weixin: {
        id: "weixin",
        label: "微信",
        connectLabel: "微信连接",
        description: "点击后启动天工自己的微信消息通道核心，后续扫码适配器接在这条通道上。"
      },
      feishu: {
        id: "feishu",
        label: "飞书",
        connectLabel: "飞书连接",
        description: "点击后启动天工自己的飞书消息通道核心，后续长连接或扫码适配器接在这条通道上。"
      }
    };

    function channelStateText(channel) {
      switch (channel?.state) {
        case "ready":
          return "已连接";
        case "credentialed_disabled":
          return "已登录未启用";
        case "installed":
          return "待扫码";
        case "host_upgrade_required":
          return "需要更新连接组件";
        case "gateway_required":
          return "待接入天工通道";
        case "adapter_pending":
          return "通道已就绪";
        case "gateway_stopped":
          return "通道未启动";
        case "connector_missing":
          return "连接组件缺失";
        case "not_configured":
          return "未连接";
        default:
          return "待连接";
      }
    }

    function channelStateClass(channel) {
      if (channel?.state === "ready") return "mini-pill ok";
      if (channel?.state === "host_upgrade_required" || channel?.state === "installed" || channel?.state === "credentialed_disabled" || channel?.state === "gateway_required" || channel?.state === "adapter_pending" || channel?.state === "gateway_stopped") {
        return "mini-pill warn";
      }
      if (channel?.state === "connector_missing") return "mini-pill failed";
      return "mini-pill";
    }

    function channelDetail(channel, fallback) {
      if (channel?.state === "host_upgrade_required") {
        return `当前连接组件版本 ${channel.hostVersion || "未知"}，点击连接会自动更新后进入登录。`;
      }
      if (channel?.state === "gateway_required") {
        return channel.detail || "需要先接入天工造物自己的消息网关。";
      }
      if (channel?.state === "adapter_pending") {
        return channel.detail || "天工消息通道核心已启动，下一层接入该平台的扫码或长连接适配器。";
      }
      if (channel?.state === "gateway_stopped") {
        return channel.detail || "天工消息通道核心未启动，点击连接后会在本机启动。";
      }
      if (channel?.state === "connector_missing") {
        return "没有找到消息连接组件；请先安装天工连接运行环境，再点击连接。";
      }
      return channel?.detail || fallback.description;
    }

    function renderMessageChannels(result = messageChannels) {
      messageChannelRows.innerHTML = "";
      if (result.loading) {
        messageChannelState.textContent = "读取中";
        messageChannelState.className = "mini-pill";
      } else if (!result.ok) {
        messageChannelState.textContent = "读取失败";
        messageChannelState.className = "mini-pill failed";
      } else {
        const readyCount = ["weixin", "feishu"]
          .filter((id) => result.channels?.[id]?.state === "ready")
          .length;
        messageChannelState.textContent = readyCount ? `已连接 ${readyCount}/2` : "待连接";
        messageChannelState.className = readyCount ? "mini-pill ok" : "mini-pill warn";
      }

      for (const id of ["weixin", "feishu"]) {
        const fallback = channelDefaults[id];
        const channel = { ...fallback, ...(result.channels?.[id] || {}) };
        const row = document.createElement("div");
        row.className = "channel-row";

        const main = document.createElement("div");
        main.className = "channel-main";
        const title = document.createElement("div");
        title.className = "channel-title";
        const name = document.createElement("strong");
        name.textContent = fallback.label;
        const pill = document.createElement("span");
        pill.className = channelStateClass(channel);
        pill.textContent = result.loading ? "读取中" : channelStateText(channel);
        title.appendChild(name);
        title.appendChild(pill);

        const desc = document.createElement("p");
        desc.className = "channel-desc";
        desc.textContent = result.loading ? fallback.description : channelDetail(channel, fallback);
        main.appendChild(title);
        main.appendChild(desc);

        const actionsWrap = document.createElement("div");
        actionsWrap.className = "channel-actions";
        const connect = document.createElement("button");
        connect.className = "small-command channel-link";
        connect.type = "button";
        connect.dataset.messageChannelConnect = id;
        connect.disabled = connectingChannel === id;
        connect.textContent = connectingChannel === id ? "打开中" : fallback.connectLabel;
        connect.title = `${fallback.connectLabel}，启动天工消息通道核心`;
        actionsWrap.appendChild(connect);

        row.appendChild(main);
        row.appendChild(actionsWrap);
        messageChannelRows.appendChild(row);
      }

      if (result.notice) {
        const notice = document.createElement("div");
        notice.className = result.noticeType === "error" ? "channel-notice failed" : "channel-notice";
        notice.textContent = result.notice;
        messageChannelRows.appendChild(notice);
      }
    }

    async function refreshMessageChannelStatus() {
      messageChannels = { ...messageChannels, loading: true, notice: "" };
      renderMessageChannels();
      try {
        messageChannels = await actions.messageChannelStatus();
      } catch (error) {
        messageChannels = { ok: false, error: error?.message || String(error), channels: {} };
      }
      renderMessageChannels();
      return messageChannels;
    }

    async function connectMessageChannel(channel) {
      const fallback = channelDefaults[channel];
      if (!fallback) return;
      connectingChannel = channel;
      renderMessageChannels();
      try {
        const result = await actions.connectMessageChannel({ channel });
        messageChannels = {
          ...messageChannels,
          notice: result?.ok
            ? (result?.message || `${fallback.connectLabel}已接入天工消息通道核心，下一步接入扫码或长连接适配器。`)
            : (result?.error || `${fallback.connectLabel}窗口打开失败`),
          noticeType: result?.ok ? "info" : "error"
        };
        if (result?.ok) {
          openMessageChannelConnectDialog(result.connection || {}, fallback);
        }
      } catch (error) {
        messageChannels = {
          ...messageChannels,
          notice: error?.message || `${fallback.connectLabel}窗口打开失败`,
          noticeType: "error"
        };
      } finally {
        connectingChannel = "";
        renderMessageChannels();
      }
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
        ["multimodal_input", config.multimodal_input ?? "auto"],
        ["image_input", config.image_input ?? "auto"],
        ["video_input", config.video_input ?? "auto"],
        ["audio_input", config.audio_input ?? "auto"],
        ["timeout", String(config.timeout ?? "")]
      ]);
    }

    modelServiceInput.addEventListener("change", () => {
      rememberCurrentModelProfile(activeModelService);
      const next = applyProviderPreset(
        { ...state.snapshot().settings, modelProviderProfiles },
        modelServiceInput.value
      );
      modelProviderInput.value = next.modelProvider;
      modelBaseUrlInput.value = next.modelBaseUrl;
      modelNameInput.value = next.modelName;
      modelApiKeyInput.value = next.modelApiKey || "";
      modelMultimodalInput.value = next.modelMultimodalInput || "auto";
      modelImageInput.value = next.modelImageInput || "auto";
      modelVideoInput.value = next.modelVideoInput || "auto";
      modelAudioInput.value = next.modelAudioInput || "auto";
      webSearchProviderInput.value = next.webSearchProvider || "auto";
      imageGenerationModeInput.value = next.imageGenerationMode || "auto";
      modelThinkingEnabledInput.checked = next.modelThinkingEnabled === true;
      modelThinkingDepthInput.value = next.modelThinkingDepth || "";
      activeModelService = modelServiceInput.value;
      renderThinkingControls(next);
      markModelDirty();
    });

    async function saveModelSettings() {
      modelSaveState.textContent = "热切换中";
      modelSaveState.className = "mini-pill";
      modelSaveButton.disabled = true;
      try {
        rememberCurrentModelProfile(modelServiceInput.value);
        const result = await actions.hotSwitchSettings({
          modelService: modelServiceInput.value,
          modelProvider: modelProviderInput.value.trim(),
          modelBaseUrl: modelBaseUrlInput.value.trim(),
          modelName: modelNameInput.value.trim(),
          modelApiKey: modelApiKeyInput.value,
          modelThinkingEnabled: modelThinkingEnabledInput.checked && !modelThinkingEnabledInput.disabled,
          modelThinkingDepth: modelThinkingDepthInput.disabled ? "" : modelThinkingDepthInput.value,
          modelMultimodalInput: modelMultimodalInput.value,
          modelImageInput: modelImageInput.value,
          modelVideoInput: modelVideoInput.value,
          modelAudioInput: modelAudioInput.value,
          webSearchProvider: webSearchProviderInput.value,
          imageGenerationMode: imageGenerationModeInput.value,
          modelProviderProfiles
        });
        modelSaveState.textContent = result.ok ? "已热切换" : "已保存，后端校验失败";
        if (result.saved?.modelProviderProfiles) {
          modelProviderProfiles = cloneModelProviderProfiles(result.saved.modelProviderProfiles);
        }
        modelSaveState.className = `mini-pill ${result.ok ? "ok" : "failed"}`;
      } finally {
        modelSaveButton.disabled = false;
      }
    }

    modelSaveButton.addEventListener("click", saveModelSettings);

    refreshConfig.addEventListener("click", () => actions.refreshConfig());
    refreshMessageChannels.addEventListener("click", () => refreshMessageChannelStatus());
    messageChannelRows.addEventListener("click", (event) => {
      const button = event.target.closest("[data-message-channel-connect]");
      if (!button) return;
      connectMessageChannel(button.dataset.messageChannelConnect);
    });
    messageChannelConnectDialog.addEventListener("click", (event) => {
      if (event.target.closest("[data-channel-connect-close]")) {
        closeMessageChannelConnectDialog();
      }
    });

    modelThinkingEnabledInput.addEventListener("change", () => {
      renderThinkingControls(draftThinkingSettings());
      rememberCurrentModelProfile();
      markModelDirty();
    });
    modelProviderInput.addEventListener("input", () => {
      renderThinkingControls(draftThinkingSettings());
      rememberCurrentModelProfile();
    });
    modelProviderInput.addEventListener("change", () => {
      renderThinkingControls(draftThinkingSettings());
      rememberCurrentModelProfile();
    });

    for (const input of [modelProviderInput, modelBaseUrlInput, modelNameInput, modelApiKeyInput, modelThinkingDepthInput, modelMultimodalInput, modelImageInput, modelVideoInput, modelAudioInput, webSearchProviderInput, imageGenerationModeInput].filter(Boolean)) {
      input.addEventListener("change", () => {
        rememberCurrentModelProfile();
        markModelDirty();
      });
      input.addEventListener("input", () => {
        rememberCurrentModelProfile();
        markModelDirty();
      });
    }

    state.on("page", renderPage);
    state.on("settings", renderSettings);
    state.on("backendConfig", renderBackendConfig);
    const snap = state.snapshot();
    renderPage(snap.activePage);
    renderSettings(snap.settings);
    renderMessageChannels();
    refreshMessageChannelStatus();
    renderBackendConfig(snap.backendConfig);
  }
};
