function escHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function safeArray(value) {
  return Array.isArray(value) ? value : [];
}

function numberValue(value) {
  const next = Number(value || 0);
  return Number.isFinite(next) ? next : 0;
}

function formatDate(value) {
  const timestamp = Number(value || 0);
  if (!timestamp) return "未记录";
  try {
    return new Date(timestamp * (timestamp < 100000000000 ? 1000 : 1)).toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit"
    });
  } catch {
    return "未记录";
  }
}

function statusLabel(value) {
  const text = String(value || "");
  const names = {
    active: "可用",
    candidate: "候选",
    review_ready: "待审阅",
    draft: "草稿",
    disabled: "停用",
    pending: "待处理",
    failed: "异常"
  };
  return names[text] || text || "未标记";
}

function statusClass(ability) {
  if (!ability?.runtimeUsable || ability?.status === "disabled") return "warn";
  if (ability?.status === "failed") return "failed";
  if (ability?.status === "active") return "ok";
  return "";
}

function chipList(values, emptyText = "未配置") {
  const items = safeArray(values).filter(Boolean).slice(0, 8);
  if (!items.length) return `<span class="skill-chip muted">${escHtml(emptyText)}</span>`;
  return items.map((item) => `<span class="skill-chip">${escHtml(item)}</span>`).join("");
}

export const skillsPanelPlugin = {
  id: "skills-panel",
  slot: "conversation",
  order: 216,
  mount({ slot, state, actions }) {
    slot.insertAdjacentHTML(
      "beforeend",
      `
        <section class="page-panel skills-page" data-page-panel="skills">
          <header class="page-header">
            <div class="title-group">
              <span class="caption">技能</span>
              <h2>技能与能力包</h2>
            </div>
            <div class="commandbar-meta">
              <span id="skillsPanelState" class="mini-pill">未读取</span>
              <button id="skillsPanelRefresh" class="small-command" type="button">刷新</button>
            </div>
          </header>

          <section class="page-body skills-body">
            <section class="panel-card skills-list-card">
              <div class="panel-title">
                <span id="skillsListTitle">能力包列表</span>
                <span id="skillsListCount" class="mini-pill">0 个</span>
              </div>
              <div id="skillsList" class="skills-list"></div>
            </section>
          </section>
        </section>
      `
    );

    const panel = slot.querySelector('[data-page-panel="skills"]');
    const statePill = panel.querySelector("#skillsPanelState");
    const refreshButton = panel.querySelector("#skillsPanelRefresh");
    const listTitle = panel.querySelector("#skillsListTitle");
    const listCount = panel.querySelector("#skillsListCount");
    const listEl = panel.querySelector("#skillsList");
    let catalog = { ok: null, categories: [], abilities: [], summary: {} };
    let loaded = false;
    let loading = false;

    function setState(text, ok = null) {
      statePill.textContent = text;
      statePill.className = `mini-pill ${ok === true ? "ok" : ok === false ? "failed" : ""}`;
    }

    function activeCategory() {
      return state.snapshot().activeSkillCategory || "all";
    }

    function categoryLabel(id) {
      if (id === "all") return "全部分类";
      const category = safeArray(catalog.categories).find((item) => item.id === id);
      return category?.label || id || "未分类";
    }

    function visibleAbilities() {
      const category = activeCategory();
      const abilities = safeArray(catalog.abilities);
      return category === "all" ? abilities : abilities.filter((item) => item.category === category);
    }

    function abilityKey(ability) {
      return String(ability?.id || ability?.name || "").trim();
    }

    function selectedSkillIds() {
      return new Set(safeArray(state.snapshot().selectedSkills).map((item) => String(item.id || item.name || "").toLowerCase()));
    }

    function renderAbility(ability) {
      const tone = statusClass(ability);
      const id = abilityKey(ability);
      const selected = selectedSkillIds().has(id.toLowerCase());
      const toolRefs = safeArray(ability.toolPackageRefs);
      const toolNames = safeArray(ability.toolNames);
      const toolSummary = toolNames.length
        ? toolNames.slice(0, 5).join(" / ")
        : toolRefs.length
          ? toolRefs.slice(0, 5).join(" / ")
          : "未绑定工具引用";
      const risk = ability.riskLevel || ability.maxDangerLevel || (ability.requiresConfirmation ? "需确认" : "常规");
      const toolRefCount = Math.max(toolRefs.length, toolNames.length);
      return `
        <article class="skill-card ${escHtml(tone)}${selected ? " selected" : ""}" data-skill-id="${escHtml(id)}">
          <div class="skill-card-head">
            <div>
              <strong>${escHtml(ability.name || ability.id || "未命名能力包")}</strong>
              <span>${escHtml(ability.id || "未记录 ID")}</span>
            </div>
            <span class="mini-pill ${escHtml(tone)}">${escHtml(statusLabel(ability.status))}</span>
            <label class="skill-select-control" title="启用此技能">
              <input type="checkbox" data-select-skill="${escHtml(id)}" ${selected ? "checked" : ""} />
              <span>启用</span>
            </label>
          </div>
          <p class="skill-desc">${escHtml(ability.description || "暂无能力说明")}</p>
          <div class="skill-meta-grid">
            <div><span>分类</span><strong>${escHtml(categoryLabel(ability.category))}</strong></div>
            <div><span>等级</span><strong>${escHtml(ability.level || "未标记")}</strong></div>
            <div><span>风险</span><strong>${escHtml(risk)}</strong></div>
            <div><span>更新</span><strong>${escHtml(formatDate(ability.updatedAt))}</strong></div>
          </div>
          <div class="skill-chip-row">
            ${chipList(ability.taskIntents, "未配置意图")}
          </div>
          <div class="skill-tool-row">
            <span>工具引用</span>
            <strong title="${escHtml(toolSummary)}">${escHtml(toolSummary)}</strong>
            <em>${numberValue(toolRefCount)} 项</em>
          </div>
        </article>
      `;
    }

    function render() {
      const category = activeCategory();
      const abilities = visibleAbilities();
      listTitle.textContent = categoryLabel(category);
      listCount.textContent = `${abilities.length} 个`;
      if (loading && !loaded) {
        listEl.innerHTML = `<div class="knowledge-empty">正在读取技能资产</div>`;
        return;
      }
      if (catalog.ok === false) {
        listEl.innerHTML = `<div class="knowledge-empty failed">${escHtml(catalog.error || "技能资产读取失败")}</div>`;
        return;
      }
      if (!abilities.length) {
        listEl.innerHTML = `<div class="knowledge-empty">这个分类下暂时没有技能包</div>`;
        return;
      }
      listEl.innerHTML = abilities.map(renderAbility).join("");
    }

    async function refreshSkills(quiet = false) {
      if (loading) return;
      loading = true;
      refreshButton.disabled = true;
      if (!quiet) setState("读取中");
      render();
      try {
        const result = await actions.listSkills?.();
        catalog = {
          ok: result?.ok !== false,
          categories: safeArray(result?.categories),
          abilities: safeArray(result?.abilities),
          summary: result?.summary || {},
          error: result?.error || ""
        };
        loaded = true;
        setState(catalog.ok ? "已同步" : (catalog.error || "读取失败"), catalog.ok);
      } catch (error) {
        catalog = { ok: false, categories: [], abilities: [], summary: {}, error: error?.message || String(error) };
        loaded = true;
        setState(catalog.error || "读取失败", false);
      } finally {
        loading = false;
        refreshButton.disabled = false;
        render();
      }
    }

    function renderPage(page) {
      const active = page === "skills";
      panel.classList.toggle("active", active);
      if (active && !loaded) void refreshSkills();
    }

    refreshButton.addEventListener("click", () => refreshSkills());
    listEl.addEventListener("change", (event) => {
      const input = event.target.closest("[data-select-skill]");
      if (!input) return;
      const id = input.dataset.selectSkill || "";
      const ability = safeArray(catalog.abilities).find((item) => abilityKey(item) === id);
      if (!ability) return;
      actions.toggleSelectedSkill?.(ability);
      render();
    });
    state.on("page", renderPage);
    state.on("skillCategory", render);
    state.on("selectedSkills", render);

    renderPage(state.snapshot().activePage);
    render();
  }
};
