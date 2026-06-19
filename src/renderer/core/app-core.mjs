import { createActions } from "./actions.mjs";
import { createBus } from "./bus.mjs";
import { createState } from "./state.mjs";

const THEME_STYLES = new Set(["ink_teal", "bronze_gear", "jade_light"]);

function normalizeThemeStyle(value) {
  const theme = String(value || "").trim();
  return THEME_STYLES.has(theme) ? theme : "ink_teal";
}

function applyTheme(documentRef, settings) {
  const root = documentRef?.documentElement;
  if (!root) return;
  root.dataset.theme = normalizeThemeStyle(settings?.themeStyle);
}

export function createAppCore({ runtime, documentRef = document } = {}) {
  const state = createState();
  const bus = createBus();
  const plugins = [];
  const slotCache = new Map();
  const core = {
    runtime,
    state,
    bus,
    actions: null,
    registerPlugin,
    getSlot,
    boot
  };

  core.actions = createActions({ runtime, state });
  applyTheme(documentRef, state.snapshot().settings);
  state.on("settings", (settings) => applyTheme(documentRef, settings));

  function registerPlugin(plugin) {
    if (!plugin?.id || !plugin?.slot || typeof plugin.mount !== "function") {
      throw new Error("Plugin must provide id, slot, and mount(core).");
    }
    if (plugins.some((item) => item.id === plugin.id)) {
      throw new Error(`Plugin already registered: ${plugin.id}`);
    }
    plugins.push(plugin);
    return core;
  }

  function getSlot(name) {
    if (slotCache.has(name)) return slotCache.get(name);
    const slot = documentRef.querySelector(`[data-slot="${name}"]`);
    if (!slot) throw new Error(`Missing plugin slot: ${name}`);
    slotCache.set(name, slot);
    return slot;
  }

  async function boot() {
    for (const plugin of plugins.sort((a, b) => (a.order || 0) - (b.order || 0))) {
      try {
        await plugin.mount({ ...core, slot: getSlot(plugin.slot) });
      } catch (error) {
        console.error(`Plugin mount failed: ${plugin.id}`, error);
      }
    }
    bus.emit("app:mounted");
    await core.actions.loadSettings().catch((error) => console.error("loadSettings failed", error));
    await core.actions.refreshStatus().catch((error) => console.error("refreshStatus failed", error));
    await core.actions.refreshConfig().catch((error) => console.error("refreshConfig failed", error));
  }

  return core;
}
