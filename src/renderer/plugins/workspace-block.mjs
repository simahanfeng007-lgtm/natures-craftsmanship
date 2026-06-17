import { concisePath } from "../core/formatters.mjs";

export const workspaceBlockPlugin = {
  id: "workspace-block",
  slot: "context",
  order: 110,
  mount({ slot, state, actions }) {
    slot.insertAdjacentHTML(
      "beforeend",
      `
        <section class="side-section default-context-section">
          <div class="section-heading">
            <span>工作区</span>
            <button id="workspaceButton" class="small-command" type="button">选择</button>
          </div>
          <div id="workspaceText" class="path-display">读取中</div>
        </section>
      `
    );

    const button = slot.querySelector("#workspaceButton");
    const text = slot.querySelector("#workspaceText");
    const section = button.closest(".side-section");

    function render(settings) {
      text.textContent = concisePath(settings.workspace);
      text.title = settings.workspace || "";
    }

    function renderPage(page) {
      section.hidden = page === "lifecycle" || page === "persona" || page === "skills";
    }

    button.addEventListener("click", () => actions.chooseWorkspace());
    state.on("page", renderPage);
    state.on("settings", render);
    renderPage(state.snapshot().activePage);
    render(state.snapshot().settings);
  }
};
