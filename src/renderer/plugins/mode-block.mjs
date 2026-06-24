import { permissionNames } from "../core/formatters.mjs";

const permissionModes = ["readonly", "workspace_write", "workspace_full"];

export const modeBlockPlugin = {
  id: "mode-block",
  slot: "context",
  order: 120,
  mount({ slot, state, actions }) {
    slot.insertAdjacentHTML(
      "beforeend",
      `
        <section class="side-section default-context-section">
          <div class="section-heading">
            <span>权限</span>
          </div>
          <div class="segmented" role="tablist" aria-label="权限选择">
            ${permissionModes.map((mode) => `<button class="segment" data-permission-mode="${mode}" type="button">${permissionNames[mode]}</button>`).join("")}
          </div>
        </section>
      `
    );

    const buttons = [...slot.querySelectorAll("[data-permission-mode]")];
    const section = buttons[0]?.closest(".side-section");

    function render(settings) {
      const active = settings.permissionMode || "workspace_full";
      for (const button of buttons) {
        button.classList.toggle("active", button.dataset.permissionMode === active);
      }
    }

    function renderPage(page) {
      if (section) section.hidden = page === "lifecycle" || page === "persona" || page === "skills";
    }

    for (const button of buttons) {
      button.addEventListener("click", () => actions.setPermissionMode(button.dataset.permissionMode));
    }

    state.on("page", renderPage);
    state.on("settings", render);
    renderPage(state.snapshot().activePage);
    render(state.snapshot().settings);
  }
};
