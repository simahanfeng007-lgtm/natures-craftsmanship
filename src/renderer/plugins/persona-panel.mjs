const THEME_OPTIONS = [
  { value: "ink_teal", label: "玄墨青绿", hint: "当前深色执行台，克制清爽。" },
  { value: "bronze_gear", label: "青铜机关", hint: "更沉稳的铜色机关感。" },
  { value: "jade_light", label: "玉简浅色", hint: "浅色文牍感，适合长时间阅读。" }
];

const DEFAULT_LOGO_SRC = "./assets/tiangong-logo.png";

function option(value, label) {
  return `<option value="${value}">${label}</option>`;
}

function personaName(settings) {
  return String(settings?.personaName || "临渊者").trim() || "临渊者";
}

function personaInitial(settings) {
  return personaName(settings).slice(0, 1) || "临";
}

function applyTheme(settings) {
  const value = THEME_OPTIONS.some((item) => item.value === settings?.themeStyle)
    ? settings.themeStyle
    : "ink_teal";
  document.documentElement.dataset.theme = value;
}

function renderAvatar(target, settings) {
  target.innerHTML = "";
  const img = document.createElement("img");
  img.src = String(settings?.personaAvatarDataUrl || "") || DEFAULT_LOGO_SRC;
  img.alt = "人物头像";
  target.appendChild(img);
}

export const personaPanelPlugin = {
  id: "persona-panel",
  slot: "conversation",
  order: 215,
  mount({ slot, state, actions }) {
    slot.insertAdjacentHTML(
      "beforeend",
      `
        <section class="page-panel persona-page" data-page-panel="persona">
          <header class="page-header">
            <div class="title-group">
              <span class="caption">人设</span>
              <h2>人物设定</h2>
            </div>
            <div class="commandbar-meta">
              <span id="personaSaveState" class="mini-pill">未修改</span>
              <button id="personaSave" class="small-command" type="button">保存</button>
            </div>
          </header>

          <section class="page-body persona-body">
            <section class="panel-card persona-identity-card">
              <div class="panel-title">
                <span>身份与头像</span>
                <span class="mini-pill">前端显示</span>
              </div>

              <div class="persona-avatar-editor">
                <div id="personaAvatarPreview" class="persona-avatar-preview"></div>
                <div class="avatar-actions">
                  <button id="personaChooseAvatar" class="small-command" type="button">选择头像</button>
                  <button id="personaClearAvatar" class="small-command muted-command" type="button">清除</button>
                </div>
              </div>

              <div class="settings-form">
                <label class="field-row">
                  <span>人物姓名</span>
                  <input id="personaName" type="text" maxlength="32" placeholder="临渊者" />
                </label>
              </div>
            </section>

            <section class="panel-card persona-theme-card">
              <div class="panel-title">
                <span>界面主体风格</span>
                <span class="mini-pill">即时预览</span>
              </div>
              <label class="field-row">
                <span>主体风格</span>
                <select id="personaTheme">
                  ${THEME_OPTIONS.map((item) => option(item.value, item.label)).join("")}
                </select>
              </label>
              <div id="themeHint" class="theme-hint"></div>
              <div class="theme-swatch-row" aria-hidden="true">
                <span class="theme-swatch accent"></span>
                <span class="theme-swatch surface"></span>
                <span class="theme-swatch line"></span>
              </div>
            </section>

            <section class="panel-card wide-card persona-soul-card">
              <div class="panel-title">
                <span>灵魂提示词</span>
                <span class="mini-pill">写入执行链</span>
              </div>
              <textarea id="personaSoulPrompt" class="persona-textarea" maxlength="6000" placeholder="留空时使用后端默认 Soul。这里可以写人物底色、说话方式、边界、执行偏好。"></textarea>
              <div class="textarea-meta">
                <span id="soulPromptCount">0 / 6000</span>
                <button id="personaClearSoul" class="small-command muted-command" type="button">清空</button>
              </div>
            </section>
          </section>
        </section>
      `
    );

    const panel = slot.querySelector('[data-page-panel="persona"]');
    const saveState = panel.querySelector("#personaSaveState");
    const saveButton = panel.querySelector("#personaSave");
    const nameInput = panel.querySelector("#personaName");
    const avatarPreview = panel.querySelector("#personaAvatarPreview");
    const chooseAvatar = panel.querySelector("#personaChooseAvatar");
    const clearAvatar = panel.querySelector("#personaClearAvatar");
    const themeInput = panel.querySelector("#personaTheme");
    const themeHint = panel.querySelector("#themeHint");
    const soulInput = panel.querySelector("#personaSoulPrompt");
    const soulCount = panel.querySelector("#soulPromptCount");
    const clearSoul = panel.querySelector("#personaClearSoul");
    let dirty = false;
    let avatarDataUrl = "";

    function setSaveState(label, className = "") {
      saveState.textContent = label;
      saveState.className = `mini-pill ${className}`.trim();
    }

    function markDirty() {
      dirty = true;
      setSaveState("待保存", "warn");
      applyTheme({ themeStyle: themeInput.value });
      state.setSettings({ themeStyle: themeInput.value });
      updateThemeHint();
      updateCount();
    }

    function updateCount() {
      soulCount.textContent = `${soulInput.value.length} / 6000`;
    }

    function updateThemeHint() {
      const selected = THEME_OPTIONS.find((item) => item.value === themeInput.value) || THEME_OPTIONS[0];
      themeHint.textContent = selected.hint;
    }

    function renderPage(page) {
      panel.classList.toggle("active", page === "persona");
    }

    function renderSettings(settings) {
      applyTheme(settings);
      if (dirty) return;
      nameInput.value = personaName(settings);
      avatarDataUrl = String(settings.personaAvatarDataUrl || "");
      themeInput.value = THEME_OPTIONS.some((item) => item.value === settings.themeStyle)
        ? settings.themeStyle
        : "ink_teal";
      soulInput.value = String(settings.soulPrompt || "");
      renderAvatar(avatarPreview, settings);
      updateThemeHint();
      updateCount();
      setSaveState("未修改");
    }

    function collectSettingsPayload() {
      return {
        personaName: nameInput.value.trim() || "临渊者",
        personaAvatarDataUrl: avatarDataUrl,
        soulPrompt: soulInput.value.trim(),
        themeStyle: themeInput.value
      };
    }

    saveButton.addEventListener("click", async () => {
      setSaveState("保存中");
      const saved = await actions.saveSettings(collectSettingsPayload());
      dirty = false;
      renderSettings(saved);
      setSaveState("已保存", "ok");
    });

    chooseAvatar.addEventListener("click", async () => {
      const previousAvatar = avatarDataUrl;
      const next = await actions.choosePersonaAvatar();
      const selectedAvatar = String(next?.personaAvatarDataUrl || "");
      if (selectedAvatar === previousAvatar) return;
      avatarDataUrl = selectedAvatar;
      const saved = await actions.saveSettings(collectSettingsPayload());
      dirty = false;
      renderSettings(saved);
      setSaveState("已保存", "ok");
    });

    clearAvatar.addEventListener("click", () => {
      avatarDataUrl = "";
      renderAvatar(avatarPreview, { personaName: nameInput.value, personaAvatarDataUrl: "" });
      markDirty();
    });

    clearSoul.addEventListener("click", () => {
      soulInput.value = "";
      markDirty();
    });

    for (const input of [nameInput, themeInput, soulInput]) {
      input.addEventListener("change", markDirty);
      input.addEventListener("input", markDirty);
    }

    state.on("page", renderPage);
    state.on("settings", renderSettings);
    const snap = state.snapshot();
    renderPage(snap.activePage);
    renderSettings(snap.settings);
  }
};
