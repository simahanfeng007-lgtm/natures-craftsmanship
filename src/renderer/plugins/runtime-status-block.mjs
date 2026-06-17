import { translateLabel, translateValue } from "../core/formatters.mjs";

export const runtimeStatusBlockPlugin = {
  id: "runtime-status-block",
  slot: "context",
  order: 130,
  mount({ slot, state, actions }) {
    slot.insertAdjacentHTML(
      "beforeend",
      `
        <section class="side-section default-context-section">
          <div class="section-heading">
            <span>运行内核</span>
            <button id="refreshStatus" class="small-command" type="button">刷新</button>
          </div>
          <div id="runtimeState" class="runtime-state"></div>
        </section>
      `
    );

    const button = slot.querySelector("#refreshStatus");
    const status = slot.querySelector("#runtimeState");
    const section = button.closest(".side-section");

    function appendStatusValue(value) {
      const row = document.createElement("div");
      row.className = "rs-line";
      const valueNode = document.createElement("span");
      valueNode.className = "rs-value";
      valueNode.textContent = translateValue(value);
      row.appendChild(valueNode);
      status.appendChild(row);
    }

    function renderStatusText(raw) {
      const text = String(raw || "待连接").trim();
      status.innerHTML = "";
      if (!text || text === "待连接") {
        appendStatusValue("待连接");
        return;
      }
      const lines = text.split("\n").map((l) => l.replace(/^-\s*/, "").trim()).filter(Boolean);
      for (const line of lines) {
        const idx = line.indexOf(":");
        if (idx > 0) {
          const label = translateLabel(line.slice(0, idx).trim());
          const value = translateValue(line.slice(idx + 1).trim());
          const row = document.createElement("div");
          row.className = "rs-line";
          const labelNode = document.createElement("span");
          labelNode.className = "rs-label";
          labelNode.textContent = `${label}:`;
          const valueNode = document.createElement("span");
          valueNode.className = "rs-value";
          valueNode.textContent = value;
          row.appendChild(labelNode);
          row.appendChild(valueNode);
          status.appendChild(row);
        } else {
          appendStatusValue(line);
        }
      }
    }

    function render(next) {
      renderStatusText(next.text);
    }

    function renderPage(page) {
      section.hidden = page === "lifecycle" || page === "persona" || page === "skills";
    }

    button.addEventListener("click", () => actions.refreshStatus());
    state.on("page", renderPage);
    state.on("runtimeStatus", render);
    renderPage(state.snapshot().activePage);
    render(state.snapshot().runtimeStatus);
  },
};
