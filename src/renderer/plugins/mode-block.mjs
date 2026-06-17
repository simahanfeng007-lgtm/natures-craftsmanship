import { modeNames } from "../core/formatters.mjs";

const modes = ["auto", "chat", "work"];

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
            <span>模式</span>
          </div>
          <div class="segmented" role="tablist" aria-label="运行模式">
            ${modes.map((mode) => `<button class="segment" data-mode="${mode}" type="button">${modeNames[mode]}</button>`).join("")}
          </div>
        </section>
      `
    );

    const buttons = [...slot.querySelectorAll("[data-mode]")];
    const section = buttons[0]?.closest(".side-section");

    function render(settings) {
      for (const button of buttons) {
        button.classList.toggle("active", button.dataset.mode === settings.mode);
      }
    }

    function renderPage(page) {
      if (section) section.hidden = page === "lifecycle" || page === "persona" || page === "skills";
    }

    for (const button of buttons) {
      button.addEventListener("click", () => actions.setMode(button.dataset.mode));
    }

    state.on("page", renderPage);
    state.on("settings", render);
    renderPage(state.snapshot().activePage);
    render(state.snapshot().settings);
  }
};
