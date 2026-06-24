export const brandBlockPlugin = {
  id: "brand-block",
  slot: "context",
  order: 100,
  mount({ slot, state }) {
    slot.insertAdjacentHTML(
      "beforeend",
      `
        <header class="brand-block">
          <div class="brand-text">
            <h1>天工造物</h1>
            <p id="brandPersona">临渊者 · 运行宿主</p>
          </div>
        </header>
      `
    );

    const persona = slot.querySelector("#brandPersona");
    function render(settings) {
      const name = String(settings?.personaName || "临渊者").trim() || "临渊者";
      persona.textContent = `${name} · 运行宿主`;
    }

    state.on("settings", render);
    render(state.snapshot().settings);
  },
};
