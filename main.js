const { app, BrowserWindow, clipboard, dialog, ipcMain, nativeImage, nativeTheme, shell, Menu } = require("electron");
const path = require("path");
const fs = require("fs");
const os = require("os");
const crypto = require("crypto");
const http = require("http");
const { spawn, spawnSync } = require("child_process");
const { fileURLToPath } = require("url");

const APP_DIR = __dirname;
const DEV_ROOT = path.resolve(APP_DIR, "..", "..");
const LOCAL_RESOURCE_ROOT = path.join(APP_DIR, "resources");
const USE_LOCAL_RESOURCES = !app.isPackaged
  && fs.existsSync(path.join(LOCAL_RESOURCE_ROOT, "backend"))
  && fs.existsSync(path.join(LOCAL_RESOURCE_ROOT, "backend_runtime"));
const RESOURCE_ROOT = app.isPackaged ? process.resourcesPath : (USE_LOCAL_RESOURCES ? LOCAL_RESOURCE_ROOT : DEV_ROOT);
const BACKEND = (app.isPackaged || USE_LOCAL_RESOURCES) ? path.join(RESOURCE_ROOT, "backend") : DEV_ROOT;
const RUNTIME_ROOT = (app.isPackaged || USE_LOCAL_RESOURCES)
  ? path.join(RESOURCE_ROOT, "backend_runtime")
  : path.join(DEV_ROOT, "installer", "runtime", "backend_runtime");
const ROOT = BACKEND;
const RUN_AGENT = path.join(BACKEND, "run_agent.py");
const KNOWLEDGE_BRIDGE = path.join(BACKEND, "knowledge_bridge.py");
const PRELOAD = path.join(APP_DIR, "preload.js");
const INDEX = path.join(APP_DIR, "src", "index.html");
const APP_ICON = path.join(APP_DIR, "src", "assets", "tiangong-logo.png");
const STREAM_EVENT_PREFIX = "__TIANGONG_STREAM_EVENT__ ";
const STREAM_EVENT_CHANNEL = "runtime:run-step";
const LEARNING_MESSAGE_CHANNEL = "runtime:learning-message";
const CHAT_UPLOAD_MAX_FILES = 5;
const CHAT_UPLOAD_MAX_BYTES = 200 * 1024 * 1024;
const ACTIVE_BACKEND_RUNS = new Map();
const MESSAGE_GATEWAY_HOST = "127.0.0.1";
const MESSAGE_GATEWAY_DEFAULT_PORT = 17775;
const MESSAGE_GATEWAY_HISTORY_LIMIT = 40;
let MESSAGE_GATEWAY_SERVER = null;
let MESSAGE_GATEWAY_PORT = 0;
let MESSAGE_GATEWAY_STARTING = null;
let MESSAGE_GATEWAY_QUEUE = Promise.resolve();
const MESSAGE_GATEWAY_STATS = {
  startedAt: 0,
  lastInboundAt: 0,
  lastFinishAt: 0,
  lastError: "",
  inboundCount: 0,
  activeRuns: 0
};
let LEARNING_SCHEDULER_TIMER = null;
let LEARNING_SCHEDULER_STARTUP_TIMER = null;
let LAST_LEARNING_SCHEDULER_AT = 0;
const LEARNING_SCHEDULER_STATE = {
  startedAt: 0,
  lastTickAt: 0,
  lastRunAt: 0,
  lastFinishAt: 0,
  lastSkipAt: 0,
  lastSkipReason: "",
  lastError: "",
  lastResultOk: null,
  tickCount: 0,
  runCount: 0,
  skipCount: 0
};
let MEMORY_HEARTBEAT_TIMER = null;
let MEMORY_HEARTBEAT_STARTUP_TIMER = null;
let LAST_MEMORY_HEARTBEAT_AT = 0;
const MEMORY_HEARTBEAT_STATE = {
  startedAt: 0,
  lastTickAt: 0,
  lastRunAt: 0,
  lastFinishAt: 0,
  lastSkipAt: 0,
  lastSkipReason: "",
  lastError: "",
  lastResultOk: null,
  running: false,
  tickCount: 0,
  runCount: 0,
  skipCount: 0,
  lastDurationMs: 0,
  lastDelta: null
};
const SELECTED_SKILL_LIMIT = 5;
const CHAT_DOCUMENT_EXTENSIONS = ["txt", "md", "markdown", "csv", "json", "jsonl", "html", "htm", "xml", "yaml", "yml", "docx", "xlsx", "pptx", "pdf", "py", "js", "mjs", "ts", "tsx", "jsx", "css", "log"];
const CHAT_IMAGE_EXTENSIONS = ["png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "avif", "svg", "tif", "tiff"];
const CHAT_VIDEO_EXTENSIONS = ["mp4", "webm", "ogv", "mov", "mkv", "avi", "m4v", "wmv", "flv", "mpeg", "mpg", "3gp", "ts", "m2ts"];
const CHAT_AUDIO_EXTENSIONS = ["mp3", "wav", "ogg", "m4a", "flac", "aac", "opus", "wma"];
const THEME_STYLES = new Set(["ink_teal", "bronze_gear", "jade_light"]);
const CHAT_FILE_FILTERS = [
  { name: "所有支持的附件", extensions: [...CHAT_DOCUMENT_EXTENSIONS, ...CHAT_IMAGE_EXTENSIONS, ...CHAT_VIDEO_EXTENSIONS, ...CHAT_AUDIO_EXTENSIONS] },
  { name: "文档和代码", extensions: CHAT_DOCUMENT_EXTENSIONS },
  { name: "图片", extensions: CHAT_IMAGE_EXTENSIONS },
  { name: "视频", extensions: CHAT_VIDEO_EXTENSIONS },
  { name: "音频", extensions: CHAT_AUDIO_EXTENSIONS },
  { name: "全部文件", extensions: ["*"] }
];
const FRONTEND_HISTORY_MAX_MESSAGES = 20;
const FRONTEND_HISTORY_MAX_CONTENT = 4000;
const FRONTEND_WORK_MAX_STEPS = 12;
const FRONTEND_WORK_MAX_TEXT = 5000;
const TOOL_CATEGORY_DEFINITIONS = [
  {
    id: "writing_content",
    label: "写作生产",
    description: "网文、脚本、文案、案例、选题和转化内容生产。",
    toolCount: 0,
    sampleTools: ["wangwen_novel_factory_run", "short_video_script_generate", "case_study_generate"]
  },
  {
    id: "image_creation",
    label: "图片制作",
    description: "图片生成、编辑、海报、封面、产品图和视觉素材。",
    toolCount: 0,
    sampleTools: ["image_generate", "image_edit", "image_text_poster_generate"]
  },
  {
    id: "video_creation",
    label: "视频制作",
    description: "短视频、分镜、口播、剪辑、字幕、配音和导出。",
    toolCount: 0,
    sampleTools: ["video_generate_from_text", "storyboard_generate", "video_render"]
  },
  {
    id: "audio_creation",
    label: "音频制作",
    description: "配音、TTS、背景音乐、降噪、混音和音频导出。",
    toolCount: 0,
    sampleTools: ["tts_generate", "audio_clone_voice", "bgm_generate"]
  },
  {
    id: "media_analysis",
    label: "多媒体解析",
    description: "图片、视频、音频识别、OCR、关键帧、转写和结构化抽取。",
    toolCount: 0,
    sampleTools: ["image_inspect", "video_keyframe_extract", "audio_transcribe"]
  },
  {
    id: "business_ops",
    label: "商业运营",
    description: "市场洞察、ICP、渠道、活动、内容运营和增长实验。",
    toolCount: 0,
    sampleTools: ["market_segment_analyze", "campaign_plan_build", "growth_experiment_design"]
  },
  {
    id: "sales_growth",
    label: "销售增长",
    description: "线索评分、客户画像、触达、需求诊断、方案报价和 RevOps。",
    toolCount: 0,
    sampleTools: ["lead_score", "sales_call_brief", "proposal_outline_build"]
  },
  {
    id: "data_integration",
    label: "数据集成",
    description: "表格、数据库、SQL、API、Webhook 和结构化数据处理。",
    toolCount: 0,
    sampleTools: ["table_profile", "db_query_readonly", "api_request_spec"]
  },
  {
    id: "automation_apps",
    label: "自动化应用",
    description: "浏览器自动化、桌面 RPA、低代码应用和系统预览打包。",
    toolCount: 0,
    sampleTools: ["browser_open", "desktop_click_plan", "app_scaffold_plan"]
  },
  {
    id: "knowledge_research",
    label: "知识研究",
    description: "联网检索、科研情报、企业知识库和来源追踪。",
    toolCount: 0,
    sampleTools: ["web_search", "paper_search_plan", "kb_search_local"]
  },
  {
    id: "document",
    label: "文档处理",
    description: "文档解析、追问、改写、导出与回滚。",
    toolCount: 0,
    sampleTools: ["document_parse", "document_query", "document_export"]
  },
  {
    id: "files_delivery",
    label: "文件交付",
    description: "文件读写、校验、压缩包与交付物生成。",
    toolCount: 0,
    sampleTools: ["list_dir", "read_file", "write_workspace_file", "create_zip_package"]
  },
  {
    id: "project_quality",
    label: "编程项目",
    description: "代码诊断、项目扫描、质量检查、测试和交付校验。",
    toolCount: 0,
    sampleTools: ["scan_project", "run_python_quality_check", "diagnose_project"]
  },
  {
    id: "system_governance",
    label: "系统治理",
    description: "系统构建、运行时对齐、质量门、评测、审计和治理报告。",
    toolCount: 0,
    sampleTools: ["runtime_tool_alignment_check", "evaluate_quality_gate", "eval_report"]
  },
  {
    id: "learning_assets",
    label: "学习资产",
    description: "仅用于学习资产契约、沙箱、候选包、发布门和激活流程。",
    toolCount: 0,
    sampleTools: ["learning_asset_contract_validate", "learning_asset_release_gate_check", "learning_asset_activation_apply"]
  },
  {
    id: "memory_experience",
    label: "记忆经验",
    description: "记忆召回、经验搜索、任务模式匹配和经验沉淀。",
    toolCount: 0,
    sampleTools: ["conversation_history_search", "experience_mentor_search", "learning_master_plan"]
  },
  {
    id: "runtime_reports",
    label: "运行报告",
    description: "状态、诊断、分析、只读报告与结果回传。",
    toolCount: 0,
    sampleTools: ["return_analysis", "return_code", "diagnose_project"]
  },
  {
    id: "other",
    label: "其他能力",
    description: "暂未归入明确业务板块的运行时工具和兼容能力。",
    toolCount: 0,
    sampleTools: ["runtime_misc", "compat_adapter"]
  }
];

let mainWindow = null;

function normalizeThemeStyle(value) {
  const theme = String(value || "").trim();
  return THEME_STYLES.has(theme) ? theme : "ink_teal";
}

function isLightThemeStyle(value) {
  return normalizeThemeStyle(value) === "jade_light";
}

function applyNativeThemeStyle(value) {
  const light = isLightThemeStyle(value);
  try {
    nativeTheme.themeSource = light ? "light" : "dark";
  } catch {}
  if (mainWindow && !mainWindow.isDestroyed()) {
    try {
      mainWindow.setBackgroundColor(light ? "#F4EFE3" : "#0C0E11");
    } catch {}
  }
}
let cachedPythonSitePaths = null;
let cachedBundledPythonHealth = null;
let cachedRuntimeToolCatalog = null;

function writeDebugLog(message, extra = "") {
  try {
    const line = `[${new Date().toISOString()}] ${message}${extra ? ` ${extra}` : ""}\n`;
    const logDir = app.isReady() ? app.getPath("userData") : APP_DIR;
    fs.mkdirSync(logDir, { recursive: true });
    fs.appendFileSync(path.join(logDir, "electron-render.log"), line, "utf8");
  } catch {
    // Debug logging must never block startup.
  }
}

function redactRuntimeText(value) {
  let text = String(value || "");
  const settings = app.isReady() ? readSettings() : {};
  for (const secret of [
    settings.modelApiKey,
    process.env.TIANGONG_API_KEY,
    process.env.OPENAI_API_KEY,
    process.env.DEEPSEEK_API_KEY
  ]) {
    const token = String(secret || "").trim();
    if (token) text = text.split(token).join("<redacted>");
  }
  return text.length > 200000 ? `${text.slice(0, 200000)}\n[truncated]` : text;
}

function runtimeLogRoot() {
  return app.isReady() ? app.getPath("userData") : APP_DIR;
}

function packageStateRoot() {
  const packageRoot = app.isPackaged ? path.dirname(process.resourcesPath) : APP_DIR;
  const root = path.join(packageRoot, ".linyuanzhe");
  try {
    fs.mkdirSync(root, { recursive: true });
  } catch {
    // State creation failures are surfaced by the backend when it tries to persist.
  }
  return root;
}

function runtimeStateEnv() {
  const root = packageStateRoot();
  return {
    TIANGONG_STATE_DIR: root,
    LINYUANZHE_STATE_DIR: root,
    TIANGONG_JIA: root,
    HERMES_HOME: root,
    TIANGONG_PACKAGE_STATE_DIR: root,
    TIANGONG_SOUL_BASELINE_PATH: path.join(root, "soul", "soul_emotion_baseline.json")
  };
}

function backendRunLogPath() {
  return path.join(runtimeLogRoot(), "backend-run.log");
}

function writeBackendRunLog(event, payload = {}) {
  try {
    const logPath = backendRunLogPath();
    fs.mkdirSync(path.dirname(logPath), { recursive: true });
    const line = JSON.stringify({
      ts: new Date().toISOString(),
      event,
      ...payload,
      stdout: redactRuntimeText(payload.stdout || ""),
      stderr: redactRuntimeText(payload.stderr || "")
    }) + "\n";
    fs.appendFileSync(logPath, line, "utf8");
  } catch {
    // Backend logging must not affect execution.
  }
}

function learningSchedulerIntervalMs() {
  return Math.max(60000, Number(process.env.TIANGONG_LEARNING_CRON_MS || 0) || 60 * 60 * 1000);
}

function learningSchedulerCheckEveryMs() {
  return Math.min(learningSchedulerIntervalMs(), 5 * 60 * 1000);
}

function memoryHeartbeatIntervalMs() {
  return Math.max(5 * 60 * 1000, Number(process.env.TIANGONG_MEMORY_HEARTBEAT_MS || 0) || 30 * 60 * 1000);
}

function memoryHeartbeatCheckEveryMs() {
  return Math.min(memoryHeartbeatIntervalMs(), 5 * 60 * 1000);
}

function isoOrEmpty(value) {
  const ts = Number(value || 0);
  return ts > 0 ? new Date(ts).toISOString() : "";
}

function learningSchedulerSnapshot(settingsOverride = null) {
  const settings = settingsOverride || readSettings();
  const intervalMs = learningSchedulerIntervalMs();
  const checkEveryMs = learningSchedulerCheckEveryMs();
  const frequency = String(settings.lifecycleFreeWillFrequency || "manual").trim().toLowerCase();
  const nextEligibleAt = LAST_LEARNING_SCHEDULER_AT ? LAST_LEARNING_SCHEDULER_AT + intervalMs : 0;
  return {
    schema: "tiangong.desktop.learning_scheduler.v1",
    timer_active: Boolean(LEARNING_SCHEDULER_TIMER),
    startup_timer_active: Boolean(LEARNING_SCHEDULER_STARTUP_TIMER),
    frequency,
    learning_scope: String(settings.lifecycleLearningScope || "workspace"),
    interval_ms: intervalMs,
    check_every_ms: checkEveryMs,
    active_backend_runs: ACTIVE_BACKEND_RUNS.size,
    started_at: isoOrEmpty(LEARNING_SCHEDULER_STATE.startedAt),
    last_tick_at: isoOrEmpty(LEARNING_SCHEDULER_STATE.lastTickAt),
    last_run_at: isoOrEmpty(LEARNING_SCHEDULER_STATE.lastRunAt),
    last_finish_at: isoOrEmpty(LEARNING_SCHEDULER_STATE.lastFinishAt),
    last_skip_at: isoOrEmpty(LEARNING_SCHEDULER_STATE.lastSkipAt),
    last_skip_reason: LEARNING_SCHEDULER_STATE.lastSkipReason,
    last_error: LEARNING_SCHEDULER_STATE.lastError,
    last_result_ok: LEARNING_SCHEDULER_STATE.lastResultOk,
    next_eligible_at: isoOrEmpty(nextEligibleAt),
    tick_count: LEARNING_SCHEDULER_STATE.tickCount,
    run_count: LEARNING_SCHEDULER_STATE.runCount,
    skip_count: LEARNING_SCHEDULER_STATE.skipCount
  };
}

function markLearningSchedulerSkip(reason, trigger, extra = {}) {
  LEARNING_SCHEDULER_STATE.lastSkipAt = Date.now();
  LEARNING_SCHEDULER_STATE.lastSkipReason = String(reason || "skipped");
  LEARNING_SCHEDULER_STATE.skipCount += 1;
  writeBackendRunLog("learning_tick_skip", {
    trigger,
    reason: LEARNING_SCHEDULER_STATE.lastSkipReason,
    scheduler: learningSchedulerSnapshot(),
    ...extra
  });
  return { ok: true, skipped: LEARNING_SCHEDULER_STATE.lastSkipReason, scheduler: learningSchedulerSnapshot() };
}

function memoryHeartbeatSnapshot(settingsOverride = null) {
  const settings = settingsOverride || readSettings();
  const intervalMs = memoryHeartbeatIntervalMs();
  const checkEveryMs = memoryHeartbeatCheckEveryMs();
  const nextEligibleAt = LAST_MEMORY_HEARTBEAT_AT ? LAST_MEMORY_HEARTBEAT_AT + intervalMs : 0;
  return {
    schema: "tiangong.desktop.memory_heartbeat_scheduler.v1",
    timer_active: Boolean(MEMORY_HEARTBEAT_TIMER),
    startup_timer_active: Boolean(MEMORY_HEARTBEAT_STARTUP_TIMER),
    interval_ms: intervalMs,
    check_every_ms: checkEveryMs,
    startup_delay_ms: Math.min(2 * 60 * 1000, checkEveryMs),
    active_backend_runs: ACTIVE_BACKEND_RUNS.size,
    running: Boolean(MEMORY_HEARTBEAT_STATE.running),
    workspace: String(settings.workspace || defaultWorkspace()),
    started_at: isoOrEmpty(MEMORY_HEARTBEAT_STATE.startedAt),
    last_tick_at: isoOrEmpty(MEMORY_HEARTBEAT_STATE.lastTickAt),
    last_run_at: isoOrEmpty(MEMORY_HEARTBEAT_STATE.lastRunAt),
    last_finish_at: isoOrEmpty(MEMORY_HEARTBEAT_STATE.lastFinishAt),
    last_skip_at: isoOrEmpty(MEMORY_HEARTBEAT_STATE.lastSkipAt),
    last_skip_reason: MEMORY_HEARTBEAT_STATE.lastSkipReason,
    last_error: MEMORY_HEARTBEAT_STATE.lastError,
    last_result_ok: MEMORY_HEARTBEAT_STATE.lastResultOk,
    next_eligible_at: isoOrEmpty(nextEligibleAt),
    tick_count: MEMORY_HEARTBEAT_STATE.tickCount,
    run_count: MEMORY_HEARTBEAT_STATE.runCount,
    skip_count: MEMORY_HEARTBEAT_STATE.skipCount,
    last_duration_ms: MEMORY_HEARTBEAT_STATE.lastDurationMs,
    last_delta: MEMORY_HEARTBEAT_STATE.lastDelta
  };
}

function markMemoryHeartbeatSkip(reason, trigger, extra = {}) {
  MEMORY_HEARTBEAT_STATE.lastSkipAt = Date.now();
  MEMORY_HEARTBEAT_STATE.lastSkipReason = String(reason || "skipped");
  MEMORY_HEARTBEAT_STATE.skipCount += 1;
  writeBackendRunLog("memory_heartbeat_skip", {
    trigger,
    reason: MEMORY_HEARTBEAT_STATE.lastSkipReason,
    scheduler: memoryHeartbeatSnapshot(),
    ...extra
  });
  return { ok: true, skipped: MEMORY_HEARTBEAT_STATE.lastSkipReason, scheduler: memoryHeartbeatSnapshot() };
}

function injectDesktopStatusPayload(stdout, extraPayload = {}) {
  const raw = String(stdout || "");
  const startMarker = "TIANGONG_STATUS_JSON_START";
  const endMarker = "TIANGONG_STATUS_JSON_END";
  const start = raw.indexOf(startMarker);
  if (start < 0) return raw;
  const bodyStart = start + startMarker.length;
  const end = raw.indexOf(endMarker, bodyStart);
  if (end < 0) return raw;
  try {
    const payload = JSON.parse(raw.slice(bodyStart, end).trim() || "{}");
    const merged = {
      ...payload,
      desktop_runtime: {
        ...(payload.desktop_runtime || {}),
        ...extraPayload
      }
    };
    if (merged.learning && typeof merged.learning === "object") {
      merged.learning = {
        ...merged.learning,
        scheduler: extraPayload.learning_scheduler || merged.learning.scheduler
      };
    }
    return `${raw.slice(0, bodyStart)}\n${JSON.stringify(merged, null, 2)}\n${raw.slice(end)}`;
  } catch {
    return raw;
  }
}

function logDayFromLine(line, fallbackDay) {
  try {
    const parsed = JSON.parse(line);
    const day = String(parsed?.ts || "").slice(0, 10);
    if (/^\d{4}-\d{2}-\d{2}$/.test(day)) return day;
  } catch {
    // Older diagnostic lines may not be JSON.
  }
  const match = String(line || "").match(/\b(\d{4}-\d{2}-\d{2})\b/);
  return match?.[1] || fallbackDay;
}

function latestTimestampFromLines(lines, fallbackDate) {
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    try {
      const parsed = JSON.parse(lines[index]);
      if (parsed?.ts) return String(parsed.ts);
    } catch {
      // Keep scanning.
    }
  }
  return `${fallbackDate}T00:00:00.000Z`;
}

function materializeDailyBackendLogs() {
  const sourcePath = backendRunLogPath();
  const sourceExists = fs.existsSync(sourcePath);
  if (!sourceExists) return [];

  const fallbackDay = fs.statSync(sourcePath).mtime.toISOString().slice(0, 10);
  const groups = new Map();
  const lines = fs.readFileSync(sourcePath, "utf8").split(/\r?\n/).filter((line) => line.trim());
  for (const line of lines) {
    const day = logDayFromLine(line, fallbackDay);
    if (!groups.has(day)) groups.set(day, []);
    groups.get(day).push(line);
  }

  const dailyDir = path.join(runtimeLogRoot(), "daily-logs");
  fs.mkdirSync(dailyDir, { recursive: true });
  const logs = [];
  for (const [date, dayLines] of groups.entries()) {
    const safeDate = /^\d{4}-\d{2}-\d{2}$/.test(date) ? date : "unknown";
    const targetPath = path.join(dailyDir, `backend-run-${safeDate}.log`);
    fs.writeFileSync(targetPath, `${dayLines.join("\n")}\n`, "utf8");
    const stats = fs.statSync(targetPath);
    logs.push({
      date,
      path: targetPath,
      count: dayLines.length,
      sizeBytes: stats.size,
      lastTs: latestTimestampFromLines(dayLines, date)
    });
  }

  logs.sort((a, b) => String(b.date).localeCompare(String(a.date)));
  return logs;
}

function listDailyBackendLogs() {
  try {
    return { ok: true, logs: materializeDailyBackendLogs() };
  } catch (error) {
    return { ok: false, logs: [], error: error?.message || String(error) };
  }
}

async function openDailyBackendLog(payload = {}) {
  try {
    const date = String(payload?.date || "").trim();
    const logs = materializeDailyBackendLogs();
    const target = logs.find((item) => item.date === date) || logs[0];
    if (!target?.path) return { ok: false, error: "暂无可打开的日志。" };
    const code = await shell.openPath(target.path);
    return { ok: !code, code, path: target.path };
  } catch (error) {
    return { ok: false, error: error?.message || String(error) };
  }
}

function deleteDailyBackendLog(payload = {}) {
  try {
    const date = String(payload?.date || "").trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
      return { ok: false, error: "请选择要删除的日志日期。", logs: materializeDailyBackendLogs() };
    }

    const sourcePath = backendRunLogPath();
    if (!fs.existsSync(sourcePath)) return { ok: true, logs: [] };

    const fallbackDay = fs.statSync(sourcePath).mtime.toISOString().slice(0, 10);
    const lines = fs.readFileSync(sourcePath, "utf8").split(/\r?\n/);
    const kept = [];
    let removed = 0;
    for (const line of lines) {
      if (!line.trim()) continue;
      if (logDayFromLine(line, fallbackDay) === date) {
        removed += 1;
      } else {
        kept.push(line);
      }
    }
    fs.writeFileSync(sourcePath, kept.length ? `${kept.join("\n")}\n` : "", "utf8");

    const dailyPath = path.join(runtimeLogRoot(), "daily-logs", `backend-run-${date}.log`);
    if (fs.existsSync(dailyPath)) fs.rmSync(dailyPath, { force: true });
    return { ok: true, removed, logs: materializeDailyBackendLogs() };
  } catch (error) {
    return { ok: false, error: error?.message || String(error), logs: [] };
  }
}

function userDataFile() {
  return path.join(app.getPath("userData"), "runtime-settings.json");
}

function defaultWorkspace() {
  return path.join(os.homedir(), "Desktop");
}

const DEFAULT_SOUL_PROMPT = [
  "You are SOUL, the default working identity of Tiangong Zaowu.",
  "Default posture: understand the user's goal first, gather only necessary context, and prefer real runtime/file verification when available.",
  "Work method: break tasks down, choose the smallest effective tool path, keep closing the loop, and diagnose the root cause before retrying once.",
  "Delivery standard: answer clearly and briefly with what was done, the result, validation, and remaining risk. Never invent tool results or hide failure.",
  "Boundary: user goals, safety policy, A5 hard blocks, workspace permissions, Runtime evidence, and QualityGate always outrank expression style."
].join("\n");

function normalizeWorkspaceRoot(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  let candidate = /^[a-z]:$/i.test(raw) ? `${raw}\\` : raw;
  try {
    candidate = path.parse(path.resolve(candidate)).root || candidate;
  } catch {
    return "";
  }
  if (!/^[a-z]:\\$/i.test(candidate)) return "";
  return fs.existsSync(candidate) ? candidate : "";
}

const MODEL_PROVIDER_PROFILE_FIELDS = [
  "modelService",
  "modelProvider",
  "modelBaseUrl",
  "modelName",
  "modelApiKey",
  "modelThinkingEnabled",
  "modelThinkingDepth",
  "modelMultimodalInput",
  "modelImageInput",
  "modelVideoInput",
  "modelAudioInput",
  "webSearchProvider",
  "imageGenerationMode",
  "updatedAt"
];

function normalizeModelProviderProfiles(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const profiles = {};
  for (const [key, rawProfile] of Object.entries(value)) {
    if (!key || !rawProfile || typeof rawProfile !== "object" || Array.isArray(rawProfile)) continue;
    const profile = {};
    for (const field of MODEL_PROVIDER_PROFILE_FIELDS) {
      if (Object.prototype.hasOwnProperty.call(rawProfile, field)) {
        profile[field] = rawProfile[field];
      }
    }
    profiles[String(key)] = profile;
  }
  return profiles;
}

function currentModelProviderProfile(settings) {
  return {
    modelService: String(settings.modelService || "").trim(),
    modelProvider: String(settings.modelProvider || "").trim(),
    modelBaseUrl: String(settings.modelBaseUrl || "").trim(),
    modelName: String(settings.modelName || "").trim(),
    modelApiKey: String(settings.modelApiKey || ""),
    modelThinkingEnabled: settings.modelThinkingEnabled === true,
    modelThinkingDepth: String(settings.modelThinkingDepth || "").trim(),
    modelMultimodalInput: String(settings.modelMultimodalInput || "auto").trim(),
    modelImageInput: String(settings.modelImageInput || "auto").trim(),
    modelVideoInput: String(settings.modelVideoInput || "auto").trim(),
    modelAudioInput: String(settings.modelAudioInput || "auto").trim(),
    webSearchProvider: String(settings.webSearchProvider || "auto").trim(),
    imageGenerationMode: String(settings.imageGenerationMode || "auto").trim(),
    updatedAt: new Date().toISOString()
  };
}

function mergeModelProviderProfiles(settings) {
  const profiles = normalizeModelProviderProfiles(settings.modelProviderProfiles);
  const service = String(settings.modelService || "").trim();
  if (!service) return profiles;
  profiles[service] = {
    ...(profiles[service] || {}),
    ...currentModelProviderProfile({ ...settings, modelService: service })
  };
  return profiles;
}

function readSettings() {
  const fallback = {
    workspace: defaultWorkspace(),
    maxSteps: 20,
    mode: "auto",
    permissionMode: "workspace_full",
    personaName: "临渊者",
    soulPrompt: DEFAULT_SOUL_PROMPT,
    personaAvatarDataUrl: "",
    userDisplayName: "",
    userCallsign: "",
    userWork: "",
    userAvatarDataUrl: "",
    userProfileSummary: "",
    userContextEnabled: true,
    themeStyle: "ink_teal",
    modelService: "minimax",
    modelProvider: "minimax",
    modelBaseUrl: "https://api.minimaxi.com/v1",
    modelName: "MiniMax-M3",
    modelApiKey: "",
    modelThinkingEnabled: false,
    modelThinkingDepth: "",
    modelMultimodalInput: "auto",
    modelImageInput: "auto",
    modelVideoInput: "auto",
    modelAudioInput: "auto",
    webSearchProvider: "auto",
    imageGenerationMode: "auto",
    modelProviderProfiles: {},
    lifecycleFreeWillFrequency: "manual",
    lifecycleLearningScope: "workspace"
  };
  try {
    const raw = fs.readFileSync(userDataFile(), "utf8");
    const merged = { ...fallback, ...JSON.parse(raw) };
    merged.mode = normalizeFrontendMode(merged.mode);
    merged.permissionMode = normalizePermissionMode(merged.permissionMode);
    merged.themeStyle = normalizeThemeStyle(merged.themeStyle);
    if (!String(merged.soulPrompt || "").trim()) merged.soulPrompt = DEFAULT_SOUL_PROMPT;
    merged.modelProviderProfiles = mergeModelProviderProfiles(merged);
    return merged;
  } catch {
    fallback.modelProviderProfiles = mergeModelProviderProfiles(fallback);
    return fallback;
  }
}

function writeSettings(next) {
  const current = readSettings();
  const merged = { ...current, ...next };
  merged.mode = normalizeFrontendMode(merged.mode);
  merged.permissionMode = normalizePermissionMode(merged.permissionMode);
  merged.themeStyle = normalizeThemeStyle(merged.themeStyle);
  if (!String(merged.soulPrompt || "").trim()) merged.soulPrompt = DEFAULT_SOUL_PROMPT;
  merged.modelProviderProfiles = mergeModelProviderProfiles(merged);
  fs.mkdirSync(path.dirname(userDataFile()), { recursive: true });
  fs.writeFileSync(userDataFile(), JSON.stringify(merged, null, 2), "utf8");
  applyNativeThemeStyle(merged.themeStyle);
  return merged;
}

function imageMimeType(filePath) {
  const ext = path.extname(String(filePath || "")).toLowerCase();
  if (ext === ".jpg" || ext === ".jpeg") return "image/jpeg";
  if (ext === ".webp") return "image/webp";
  if (ext === ".gif") return "image/gif";
  if (ext === ".svg") return "image/svg+xml";
  return "image/png";
}

const IMAGE_MEDIA_EXTENSIONS = new Set(CHAT_IMAGE_EXTENSIONS.map((ext) => `.${ext}`));
const VIDEO_MEDIA_EXTENSIONS = new Set(CHAT_VIDEO_EXTENSIONS.map((ext) => `.${ext}`));
const AUDIO_MEDIA_EXTENSIONS = new Set(CHAT_AUDIO_EXTENSIONS.map((ext) => `.${ext}`));

function mediaKindFromPath(filePath) {
  const ext = path.extname(String(filePath || "")).toLowerCase();
  if (IMAGE_MEDIA_EXTENSIONS.has(ext)) return "image";
  if (VIDEO_MEDIA_EXTENSIONS.has(ext)) return "video";
  if (AUDIO_MEDIA_EXTENSIONS.has(ext)) return "audio";
  return "file";
}

function shouldImportChatAttachment(filePath) {
  return mediaKindFromPath(filePath) === "file";
}

function normalizeClipboardTarget(value) {
  const raw = String(value || "").trim();
  if (!raw) return { raw: "", filePath: "", isUrl: false };
  if (/^file:\/\/\/?/i.test(raw)) {
    try {
      const filePath = fileURLToPath(raw);
      return { raw, filePath, isUrl: false };
    } catch {
      return { raw, filePath: "", isUrl: false };
    }
  }
  if (/^https?:\/\//i.test(raw) || /^data:/i.test(raw)) {
    return { raw, filePath: "", isUrl: true };
  }
  return { raw, filePath: path.resolve(raw), isUrl: false };
}

function copyFileToSystemClipboard(filePath) {
  clipboard.writeText(filePath);
  return { ok: true, copiedAs: "path" };
}

function copyMediaToClipboard(payload = {}) {
  const target = normalizeClipboardTarget(payload.target || payload.path || payload.url);
  const copyAs = String(payload.copyAs || "media").trim().toLowerCase();
  if (!target.raw) return { ok: false, error: "empty_target" };
  if (target.isUrl) {
    clipboard.writeText(target.raw);
    return { ok: true, copiedAs: "url" };
  }
  if (!target.filePath || !fs.existsSync(target.filePath)) {
    clipboard.writeText(target.raw);
    return { ok: false, copiedAs: "path", error: "file_not_found" };
  }
  if (copyAs === "path") {
    clipboard.writeText(target.filePath);
    return { ok: true, copiedAs: "path" };
  }
  const kind = String(payload.kind || mediaKindFromPath(target.filePath)).toLowerCase();
  if (kind === "image") {
    const image = nativeImage.createFromPath(target.filePath);
    if (!image.isEmpty()) {
      clipboard.writeImage(image);
      return { ok: true, copiedAs: "image" };
    }
  }
  return copyFileToSystemClipboard(target.filePath);
}

async function chooseAvatarForSetting(settingKey, title) {
  const current = readSettings();
  const result = await dialog.showOpenDialog(mainWindow, {
    title,
    properties: ["openFile"],
    filters: [
      { name: "图片", extensions: ["png", "jpg", "jpeg", "webp", "gif", "svg"] }
    ]
  });
  if (result.canceled || !result.filePaths[0]) return current;
  const filePath = result.filePaths[0];
  const stat = fs.statSync(filePath);
  if (stat.size > 8 * 1024 * 1024) {
    return current;
  }
  const data = fs.readFileSync(filePath);
  const avatarDataUrl = `data:${imageMimeType(filePath)};base64,${data.toString("base64")}`;
  return writeSettings({ [settingKey]: avatarDataUrl });
}

async function choosePersonaAvatar() {
  return chooseAvatarForSetting("personaAvatarDataUrl", "选择人物头像");
}

async function chooseUserAvatar() {
  return chooseAvatarForSetting("userAvatarDataUrl", "选择用户头像");
}

function pythonCommand() {
  const bundled = bundledPythonCommand();
  if (bundled) {
    const health = bundledPythonHealth(bundled);
    if (health.ok) return bundled;
    return {
      ...bundled,
      missing: true,
      message: health.message || "内置 Python 运行时不完整，请重新安装完整安装包。"
    };
  }
  if (app.isPackaged) {
    return {
      command: process.platform === "win32"
        ? path.join(RUNTIME_ROOT, "python", "python.exe")
        : path.join(RUNTIME_ROOT, "python", "bin", "python3"),
      prefix: [],
      bundled: true,
      missing: true,
      message: "未找到内置 Python 运行时，请重新安装完整安装包。"
    };
  }
  if (process.env.LINYUANZHE_PYTHON) {
    return { command: process.env.LINYUANZHE_PYTHON, prefix: [], bundled: false };
  }
  if (process.env.PYTHON) {
    return { command: process.env.PYTHON, prefix: [], bundled: false };
  }
  return { command: process.platform === "win32" ? "python" : "python3", prefix: [], bundled: false };
}

function bundledPythonCommand() {
  const candidates = process.platform === "win32"
    ? [
        path.join(RUNTIME_ROOT, "python", "python.exe"),
        path.join(RUNTIME_ROOT, "python.exe")
      ]
    : [
        path.join(RUNTIME_ROOT, "python", "bin", "python3"),
        path.join(RUNTIME_ROOT, "python", "bin", "python"),
        path.join(RUNTIME_ROOT, "python3"),
        path.join(RUNTIME_ROOT, "python")
      ];
  const command = candidates.find((item) => fs.existsSync(item));
  return command ? { command, prefix: [], bundled: true, pythonHome: path.dirname(command) } : null;
}

function existingDirs(items) {
  return items.filter((item) => item && fs.existsSync(item) && fs.statSync(item).isDirectory());
}

function bundledSitePaths() {
  const paths = [
    path.join(RUNTIME_ROOT, "site-packages"),
    path.join(RUNTIME_ROOT, "python", "site-packages"),
    path.join(RUNTIME_ROOT, "python", "Lib", "site-packages")
  ];
  const libDir = path.join(RUNTIME_ROOT, "python", "lib");
  try {
    for (const name of fs.readdirSync(libDir)) {
      if (/^python\d+(\.\d+)?$/i.test(name)) {
        paths.push(path.join(libDir, name, "site-packages"));
        paths.push(path.join(libDir, name, "dist-packages"));
      }
    }
  } catch {
    // The Windows embeddable layout has no lib/pythonX.Y directory.
  }
  return existingDirs(paths);
}

function pythonBaseEnv(py, extra = {}) {
  const env = { ...process.env };
  for (const key of [
    "PYTHONHOME",
    "PYTHONEXECUTABLE",
    "PYTHONUSERBASE",
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "CONDA_DEFAULT_ENV",
    "__PYVENV_LAUNCHER__"
  ]) {
    delete env[key];
  }
  if (py?.bundled && py.pythonHome) {
    env.PYTHONHOME = py.pythonHome;
  }
  env.PYTHONUTF8 = "1";
  env.PYTHONIOENCODING = "utf-8:replace";
  env.PYTHONNOUSERSITE = "1";
  env.PYTHONDONTWRITEBYTECODE = "1";
  env.PATH = pythonNativePathValue(py);
  return { ...env, ...extra };
}

function bundledPythonHealth(py) {
  if (!py?.bundled) return { ok: true };
  if (cachedBundledPythonHealth?.command === py.command) return cachedBundledPythonHealth;
  try {
    const result = spawnSync(py.command, [
      "-S",
      "-c",
      "import encodings, runpy, sys; print(sys.executable)"
    ], {
      cwd: RUNTIME_ROOT,
      encoding: "utf8",
      env: pythonBaseEnv(py, {
        PYTHONPATH: [BACKEND, ...bundledSitePaths()].join(path.delimiter)
      }),
      windowsHide: true,
      timeout: 10000
    });
    const ok = result.status === 0;
    cachedBundledPythonHealth = {
      command: py.command,
      ok,
      message: ok ? "" : `内置 Python 运行时启动失败：${String(result.stderr || result.stdout || "").slice(0, 1200)}`
    };
  } catch (error) {
    cachedBundledPythonHealth = {
      command: py.command,
      ok: false,
      message: `内置 Python 运行时启动失败：${error?.message || String(error)}`
    };
  }
  return cachedBundledPythonHealth;
}

function pythonSitePaths(py) {
  if (Array.isArray(cachedPythonSitePaths)) return cachedPythonSitePaths;
  const bundledPaths = bundledSitePaths();
  try {
    const script = [
      "import json, site",
      "paths = []",
      "paths.extend(getattr(site, 'getsitepackages', lambda: [])())",
      "paths.append(site.getusersitepackages())",
      "print(json.dumps(paths, ensure_ascii=False))"
    ].join("; ");
    const result = spawnSync(py.command, [...py.prefix, "-c", script], {
      cwd: ROOT,
      encoding: "utf8",
      env: pythonBaseEnv(py, {
        PYTHONPATH: [BACKEND, ...bundledPaths].join(path.delimiter)
      }),
      windowsHide: true
    });
    const raw = String(result.stdout || "").trim().split(/\r?\n/).pop();
    const values = JSON.parse(raw || "[]");
    const probed = Array.isArray(values)
      ? values.map((item) => String(item || "")).filter((item) => item && fs.existsSync(item))
      : [];
    cachedPythonSitePaths = [...new Set([...bundledPaths, ...probed])];
  } catch {
    cachedPythonSitePaths = bundledPaths;
  }
  return cachedPythonSitePaths;
}

function pythonPathValue(py) {
  const parts = [BACKEND];
  parts.push(...pythonSitePaths(py));
  if (!py?.bundled && process.env.PYTHONPATH) parts.push(process.env.PYTHONPATH);
  return parts.join(path.delimiter);
}

function pythonNativePathValue(py) {
  const parts = existingDirs([
    path.dirname(py.command || ""),
    path.join(RUNTIME_ROOT, "python"),
    path.join(RUNTIME_ROOT, "python", "DLLs"),
    path.join(RUNTIME_ROOT, "python", "bin"),
    path.join(RUNTIME_ROOT, "lib")
  ]);
  if (process.env.PATH) parts.push(process.env.PATH);
  return parts.join(path.delimiter);
}

function backendPythonEnv(py, extra = {}) {
  return pythonBaseEnv(py, {
    PYTHONPATH: pythonPathValue(py),
    TIANGONG_BACKEND_ROOT: BACKEND,
    TIANGONG_BACKEND_RUNTIME: RUNTIME_ROOT,
    TIANGONG_ENTRY_CHANNEL: "desktop_gui",
    ...extra
  });
}

function staticRuntimeToolCount() {
  return TOOL_CATEGORY_DEFINITIONS.reduce((sum, item) => sum + Number(item.toolCount || 0), 0);
}

function runtimeToolCatalogCacheKey() {
  const files = [
    path.join(BACKEND, "tiangong_agent_runtime", "runtime_entry.py"),
    path.join(BACKEND, "tiangong_agent_runtime", "tool_schemas.py"),
    path.join(BACKEND, "tiangong_agent_runtime", "tool_schemas_multimedia_additions.py"),
    path.join(BACKEND, "tiangong_agent_runtime", "enterprise_tool_schemas.py"),
    path.join(BACKEND, "tiangong_agent_runtime", "ops_tool_schemas.py"),
    path.join(BACKEND, "tiangong_agent_runtime", "wangwen_unified_pipeline_tool_schemas.py"),
    path.join(BACKEND, "tiangong_agent_runtime", "v1_clean_import_adapters.py")
  ];
  return files.map((filePath) => `${filePath}:${safeMtimeMs(filePath)}`).join("|");
}

function collectRuntimeToolCatalog() {
  const fallbackCount = staticRuntimeToolCount();
  const cacheKey = runtimeToolCatalogCacheKey();
  if (cachedRuntimeToolCatalog?.cacheKey === cacheKey) return cachedRuntimeToolCatalog;
  const py = pythonCommand();
  if (py?.missing) {
    cachedRuntimeToolCatalog = {
      ok: false,
      source: "static_category_fallback",
      toolCount: fallbackCount,
      toolNames: [],
      error: py.message || "Python runtime unavailable.",
      cacheKey
    };
    return cachedRuntimeToolCatalog;
  }
  const script = [
    "import json",
    "from tiangong_agent_runtime.runtime_entry import build_default_registry",
    "registry = build_default_registry()",
    "items = registry.describe()",
    "def _name(item):",
    "    if isinstance(item, dict):",
    "        return item.get('name', '')",
    "    return getattr(item, 'name', '')",
    "names = sorted({str(_name(item)).strip() for item in items if str(_name(item)).strip()})",
    "print(json.dumps({'toolCount': len(names), 'toolNames': names}, ensure_ascii=False))"
  ].join("\n");
  try {
    const result = spawnSync(py.command, [...py.prefix, "-c", script], {
      cwd: ROOT,
      encoding: "utf8",
      env: backendPythonEnv(py, runtimeStateEnv()),
      windowsHide: true,
      timeout: 20000
    });
    if (result.status !== 0) {
      throw new Error(String(result.stderr || result.stdout || `exit ${result.status}`).slice(0, 1200));
    }
    const raw = String(result.stdout || "").trim().split(/\r?\n/).pop();
    const payload = JSON.parse(raw || "{}");
    const toolNames = uniqStrings(payload.toolNames);
    cachedRuntimeToolCatalog = {
      ok: true,
      source: "runtime_registry",
      toolCount: Number(payload.toolCount || toolNames.length || fallbackCount),
      toolNames,
      error: "",
      cacheKey
    };
  } catch (error) {
    writeDebugLog("runtime-tool-catalog-failed", error?.message || String(error));
    cachedRuntimeToolCatalog = {
      ok: false,
      source: "static_category_fallback",
      toolCount: fallbackCount,
      toolNames: [],
      error: error?.message || String(error),
      cacheKey
    };
  }
  return cachedRuntimeToolCatalog;
}

function pythonUnavailableResult(py) {
  return {
    ok: false,
    code: 1,
    stdout: "",
    stderr: py.message || "Python 运行时不可用。",
    elapsedMs: 0,
    workspace: "",
    mode: "",
    maxSteps: ""
  };
}

function pythonScriptArgs(py, scriptPath, scriptArgs = []) {
  const argv = [scriptPath, ...scriptArgs.map((item) => String(item))];
  if (!py.bundled) {
    return [...py.prefix, "-S", scriptPath, ...scriptArgs.map((item) => String(item))];
  }
  const code = [
    "import runpy, sys",
    `sys.path.insert(0, ${JSON.stringify(BACKEND)})`,
    `sys.argv = ${JSON.stringify(argv)}`,
    `runpy.run_path(${JSON.stringify(scriptPath)}, run_name='__main__')`
  ].join("; ");
  return [...py.prefix, "-S", "-c", code];
}

function sessionScope(settings = {}, workspaceValue = "") {
  const workspace = String(workspaceValue || settings.workspace || defaultWorkspace());
  const persona = String(settings.personaName || "临渊者").slice(0, 64);
  const digest = crypto.createHash("sha256").update(`${workspace}\n${persona}`).digest("hex").slice(0, 16);
  return `electron_web_v2_${digest}`;
}

function normalizeFrontendMode(value) {
  const mode = String(value || "auto").trim().toLowerCase();
  if (mode === "chat" || mode === "work" || mode === "auto") return mode;
  if (mode === "task" || mode === "execute") return "work";
  if (mode === "talk" || mode === "conversation") return "chat";
  return "auto";
}

function normalizePermissionMode(value) {
  const mode = String(value || "workspace_full").trim().toLowerCase();
  if (mode === "readonly" || mode === "workspace_write" || mode === "workspace_full") return mode;
  if (mode === "full" || mode === "full_access" || mode === "unrestricted") return "workspace_full";
  if (mode === "write" || mode === "standard") return "workspace_write";
  return "workspace_full";
}

function normalizeMaxSteps(value, fallback = 20) {
  const parsed = Number(value || fallback);
  const safe = Number.isFinite(parsed) ? parsed : fallback;
  return Math.max(1, Math.min(180, Math.round(safe)));
}

function modelArgsFromSettings(settings) {
  const service = String(settings.modelService || "").trim();
  const provider = String(settings.modelProvider || "openai_compatible").trim();
  const baseUrl = String(settings.modelBaseUrl || "").trim();
  const model = String(settings.modelName || "").trim();
  const toolMode = String(settings.toolMode || "").trim();
  const plannerMode = String(settings.plannerMode || "").trim();
  const hasExplicitModelSettings = Boolean(baseUrl || model || (service && service !== "openai_compatible") || (provider && provider !== "openai_compatible"));
  const args = [];
  if (hasExplicitModelSettings) {
    if (provider) args.push("--provider", provider);
    if (baseUrl) args.push("--base-url", baseUrl);
    if (model) args.push("--model", model);
  }
  if (toolMode) args.push("--tool-mode", toolMode);
  if (plannerMode) args.push("--planner-mode", plannerMode);
  return args;
}

function modelEnvFromSettings(settings) {
  const env = {};
  const service = String(settings.modelService || "").trim();
  const provider = String(settings.modelProvider || "").trim();
  const baseUrl = String(settings.modelBaseUrl || "").trim();
  const apiKey = String(settings.modelApiKey || "").trim();
  const model = String(settings.modelName || "").trim();
  const thinkingEnabled = settings.modelThinkingEnabled === true || String(settings.modelThinkingEnabled || "").toLowerCase() === "true" || String(settings.modelThinkingEnabled || "") === "1";
  const thinkingDepth = String(settings.modelThinkingDepth || "").trim();
  const multimodalInput = String(settings.modelMultimodalInput || "auto").trim();
  const imageInput = String(settings.modelImageInput || "auto").trim();
  const videoInput = String(settings.modelVideoInput || "auto").trim();
  const audioInput = String(settings.modelAudioInput || "auto").trim();
  const webSearchProvider = String(settings.webSearchProvider || "auto").trim();
  const imageGenerationMode = String(settings.imageGenerationMode || "auto").trim();
  const toolMode = String(settings.toolMode || "").trim();
  const plannerMode = String(settings.plannerMode || "").trim();
  const freeWillFrequency = String(settings.lifecycleFreeWillFrequency || "manual").trim();
  const learningScope = String(settings.lifecycleLearningScope || "workspace").trim();
  const hasExplicitModelSettings = Boolean(baseUrl || apiKey || model || (service && service !== "openai_compatible") || (provider && provider !== "openai_compatible"));
  if (hasExplicitModelSettings) {
    if (provider) env.TIANGONG_PROVIDER = provider;
    if (baseUrl) env.TIANGONG_BASE_URL = baseUrl;
    if (apiKey) env.TIANGONG_API_KEY = apiKey;
    if (model) env.TIANGONG_MODEL = model;
  }
  if (toolMode) env.TIANGONG_TOOL_MODE = toolMode;
  if (plannerMode) env.TIANGONG_PLANNER_MODE = plannerMode;
  env.TIANGONG_THINKING_ENABLED = thinkingEnabled ? "1" : "0";
  if (thinkingDepth) env.TIANGONG_THINKING_DEPTH = thinkingDepth;
  if (multimodalInput && multimodalInput !== "auto") env.TIANGONG_MULTIMODAL_INPUT = multimodalInput;
  if (imageInput && imageInput !== "auto") env.TIANGONG_IMAGE_INPUT = imageInput;
  if (videoInput && videoInput !== "auto") env.TIANGONG_VIDEO_INPUT = videoInput;
  if (audioInput && audioInput !== "auto") env.TIANGONG_AUDIO_INPUT = audioInput;
  if (webSearchProvider) env.TIANGONG_WEB_SEARCH_PROVIDER = webSearchProvider;
  if (imageGenerationMode) env.TIANGONG_IMAGE_PROVIDER_MODE = imageGenerationMode;
  if (freeWillFrequency) {
    env.TIANGONG_FREE_WILL_FREQUENCY = freeWillFrequency;
    env.LINYUANZHE_FREE_WILL_FREQUENCY = freeWillFrequency;
  }
  if (learningScope) {
    env.TIANGONG_LEARNING_SCOPE = learningScope;
    env.LINYUANZHE_LEARNING_SCOPE = learningScope;
  }
  return env;
}

function normalizeChatAttachments(value) {
  const items = Array.isArray(value) ? value : [];
  const result = [];
  let totalBytes = 0;
  for (const item of items.slice(0, CHAT_UPLOAD_MAX_FILES)) {
    const target = path.resolve(String(item?.path || ""));
    try {
      const stat = fs.statSync(target);
      if (!stat.isFile()) continue;
      const status = String(item?.status || "");
      if (status && !["imported", "selected", "attached"].includes(status)) continue;
      totalBytes += stat.size;
      if (totalBytes > CHAT_UPLOAD_MAX_BYTES) break;
      result.push({
        path: target,
        name: String(item?.name || path.basename(target)),
        ext: String(item?.ext || path.extname(target).replace(/^\./, "")).toLowerCase(),
        size: stat.size,
        documentId: String(item?.documentId || item?.document_id || ""),
        status: status || "imported",
        citationCount: Number(item?.citationCount || item?.citation_count || 0)
      });
    } catch {
      // Ignore disappeared attachment paths between selection and send.
    }
  }
  return result;
}

function normalizeFrontendMessageAttachments(value) {
  const items = Array.isArray(value) ? value : [];
  const result = [];
  let totalBytes = 0;
  for (const item of items.slice(0, CHAT_UPLOAD_MAX_FILES)) {
    const rawPath = String(item?.path || "").trim();
    let target = rawPath;
    let size = Number(item?.size || item?.size_bytes || 0);
    if (rawPath) {
      try {
        const resolved = path.resolve(rawPath);
        const stat = fs.statSync(resolved);
        if (!stat.isFile()) continue;
        target = resolved;
        size = stat.size || size;
      } catch {
        target = rawPath;
      }
    }
    const status = String(item?.status || "");
    if (status && !["imported", "selected", "attached"].includes(status)) continue;
    totalBytes += Math.max(0, size);
    if (totalBytes > CHAT_UPLOAD_MAX_BYTES) break;
    const name = String(item?.name || item?.file_name || (target ? path.basename(target) : ""));
    const ext = String(item?.ext || (target ? path.extname(target).replace(/^\./, "") : "")).toLowerCase();
    if (!target && !name) continue;
    result.push({
      path: target,
      name,
      ext,
      size: Math.max(0, size),
      documentId: String(item?.documentId || item?.document_id || ""),
      status: status || "imported",
      summary: String(item?.summary || "").slice(0, 1200),
      citationCount: Number(item?.citationCount || item?.citation_count || 0)
    });
  }
  return result;
}

function normalizeFrontendMessages(value) {
  const items = Array.isArray(value) ? value : [];
  return items
    .filter((item) => item?.role === "user" || item?.role === "assistant")
    .map((item) => {
      const attachments = normalizeFrontendMessageAttachments(item?.attachments);
      return {
        role: item.role,
        content: String(item.content || "").slice(0, FRONTEND_HISTORY_MAX_CONTENT),
        attachments,
        error: Boolean(item.error),
        at: Number(item.at || 0)
      };
    })
    .filter((item) => item.content || item.attachments.length)
    .slice(-FRONTEND_HISTORY_MAX_MESSAGES);
}

function compactFrontendWorkText(value, limit = FRONTEND_WORK_MAX_TEXT) {
  const text = String(value || "").replace(/\u0000/g, "").trim();
  const safeLimit = Math.max(80, Math.min(FRONTEND_WORK_MAX_TEXT, Number(limit) || FRONTEND_WORK_MAX_TEXT));
  if (text.length <= safeLimit) return text;
  return text.slice(-safeLimit);
}

function normalizeFrontendWorkStep(item) {
  if (!item || typeof item !== "object") return null;
  const step = {
    id: compactFrontendWorkText(item.id || item.stepId || item.step_id || "", 80),
    title: compactFrontendWorkText(item.title || item.tool || item.toolName || item.tool_name || "", 120),
    tool: compactFrontendWorkText(item.tool || item.toolName || item.tool_name || "", 80),
    status: compactFrontendWorkText(item.status || "", 40),
    summary: compactFrontendWorkText(item.summary || item.message || item.text || "", 600),
    ts: Number(item.ts || item.at || 0)
  };
  return step.id || step.title || step.tool || step.status || step.summary ? step : null;
}

function normalizeFrontendWorkContext(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const lastRun = value.lastRun && typeof value.lastRun === "object" ? value.lastRun : {};
  const runProgress = value.runProgress && typeof value.runProgress === "object" ? value.runProgress : {};
  const rawSteps = Array.isArray(value.steps)
    ? value.steps
    : (Array.isArray(runProgress.steps) ? runProgress.steps : []);
  const steps = rawSteps
    .map(normalizeFrontendWorkStep)
    .filter(Boolean)
    .slice(-FRONTEND_WORK_MAX_STEPS);
  const normalized = {
    schema: "tiangong.frontend.work_context.v1",
    capturedAt: Number(value.capturedAt || Date.now()),
    lastRun: {
      requestId: compactFrontendWorkText(lastRun.requestId || lastRun.request_id || "", 80),
      phase: compactFrontendWorkText(lastRun.phase || "", 40),
      ok: lastRun.ok === null || typeof lastRun.ok === "undefined" ? null : Boolean(lastRun.ok),
      code: typeof lastRun.code === "undefined" ? "" : String(lastRun.code),
      mode: compactFrontendWorkText(lastRun.mode || "", 40),
      workspace: compactFrontendWorkText(lastRun.workspace || "", 900),
      elapsedMs: Number(lastRun.elapsedMs || lastRun.elapsed_ms || 0),
      startedAt: Number(lastRun.startedAt || lastRun.started_at || 0),
      finishedAt: Number(lastRun.finishedAt || lastRun.finished_at || 0),
      stdout: compactFrontendWorkText(lastRun.stdout || "", FRONTEND_WORK_MAX_TEXT),
      stderr: compactFrontendWorkText(lastRun.stderr || "", 3000)
    },
    runProgress: {
      requestId: compactFrontendWorkText(runProgress.requestId || runProgress.request_id || "", 80),
      phase: compactFrontendWorkText(runProgress.phase || "", 40),
      ok: runProgress.ok === null || typeof runProgress.ok === "undefined" ? null : Boolean(runProgress.ok),
      startedAt: Number(runProgress.startedAt || runProgress.started_at || 0),
      finishedAt: Number(runProgress.finishedAt || runProgress.finished_at || 0),
      anchorAt: Number(runProgress.anchorAt || runProgress.anchor_at || 0)
    },
    steps
  };
  const run = normalized.lastRun;
  const progress = normalized.runProgress;
  const hasWork = Boolean(
    run.requestId || run.phase || run.stdout || run.stderr || run.code
    || progress.requestId || progress.phase || steps.length
  );
  return hasWork ? normalized : null;
}

function normalizeSelectedSkills(value, fallbackNames = []) {
  const rawItems = Array.isArray(value) ? value : [];
  const names = Array.isArray(fallbackNames) ? fallbackNames : [];
  const out = [];
  const seen = new Set();
  function pushSkill(item) {
    const source = item && typeof item === "object" ? item : { name: item };
    const name = String(source.name || source.abilityName || source.ability_name || source.title || source.id || "").trim();
    if (!name) return;
    const key = name.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    out.push({
      id: String(source.id || source.abilityId || source.ability_id || "").slice(0, 160),
      name: name.slice(0, 160),
      category: String(source.category || "").slice(0, 80),
      description: String(source.description || "").slice(0, 500),
      toolNames: Array.isArray(source.toolNames)
        ? source.toolNames.map((tool) => String(tool || "").slice(0, 80)).filter(Boolean).slice(0, 24)
        : []
    });
  }
  for (const item of rawItems) pushSkill(item);
  for (const name of names) pushSkill(name);
  return out.slice(0, SELECTED_SKILL_LIMIT);
}

function cancelBackendRun(payload = {}) {
  const requestId = String(payload.requestId || payload.request_id || "").trim();
  let run = requestId ? ACTIVE_BACKEND_RUNS.get(requestId) : null;
  if (!run && !requestId) {
    run = [...ACTIVE_BACKEND_RUNS.values()].sort((a, b) => Number(b.started || 0) - Number(a.started || 0))[0] || null;
  }
  if (!run || typeof run.cancel !== "function") {
    return { ok: false, canceled: false, interrupted: false, requestId, error: "no_active_backend_run" };
  }
  return run.cancel("user_interrupted");
}

function emitLearningMessage(role, content, error = false) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  try {
    mainWindow.webContents.send(LEARNING_MESSAGE_CHANNEL, {
      role: role || "assistant",
      content: String(content || ""),
      error: Boolean(error),
      at: Date.now()
    });
  } catch {
    // Learning notifications are best-effort and must not affect execution.
  }
}

function finalVisibleTextFromBackend(result) {
  const lines = String(result?.stdout || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith(STREAM_EVENT_PREFIX));
  const text = lines.slice(-12).join("\n").trim();
  if (text) return text;
  return String(result?.stderr || result?.error || "").trim();
}

async function runAutonomousLearningTick(trigger = "cron") {
  const settings = readSettings();
  LEARNING_SCHEDULER_STATE.lastTickAt = Date.now();
  LEARNING_SCHEDULER_STATE.tickCount += 1;
  LEARNING_SCHEDULER_STATE.lastError = "";
  const frequency = String(settings.lifecycleFreeWillFrequency || "manual").trim().toLowerCase();
  if (frequency !== "hourly" && trigger === "cron") {
    return markLearningSchedulerSkip("frequency_not_hourly", trigger, { frequency });
  }
  if (ACTIVE_BACKEND_RUNS.size > 0) {
    return markLearningSchedulerSkip("frontend_busy", trigger, { activeBackendRuns: ACTIVE_BACKEND_RUNS.size });
  }
  const now = Date.now();
  const intervalMs = learningSchedulerIntervalMs();
  if (trigger === "cron" && LAST_LEARNING_SCHEDULER_AT && now - LAST_LEARNING_SCHEDULER_AT < intervalMs) {
    return markLearningSchedulerSkip("interval_not_reached", trigger, {
      remainingMs: Math.max(0, intervalMs - (now - LAST_LEARNING_SCHEDULER_AT))
    });
  }
  LAST_LEARNING_SCHEDULER_AT = now;
  LEARNING_SCHEDULER_STATE.lastRunAt = now;
  LEARNING_SCHEDULER_STATE.runCount += 1;
  writeBackendRunLog("learning_tick_start", {
    trigger,
    scheduler: learningSchedulerSnapshot(settings)
  });
  emitLearningMessage("assistant", "我要开始学习啦：我会按优先级处理下一张学习卡。");
  try {
    const result = await runBackend("Background self-learning: process the next eligible learning card by priority.", {
      ...settings,
      requestId: `learn_${crypto.randomUUID()}`,
      mode: "work",
      learningAction: "learn",
      learningId: "__next__",
      workspace: settings.workspace || defaultWorkspace()
    });
    LEARNING_SCHEDULER_STATE.lastFinishAt = Date.now();
    LEARNING_SCHEDULER_STATE.lastResultOk = Boolean(result.ok);
    LEARNING_SCHEDULER_STATE.lastError = result.ok ? "" : String(result.stderr || result.error || "learning_failed").slice(0, 1000);
    writeBackendRunLog("learning_tick_finish", {
      trigger,
      ok: Boolean(result.ok),
      code: result.code ?? "",
      requestId: result.requestId || "",
      scheduler: learningSchedulerSnapshot(settings)
    });
    const finalText = finalVisibleTextFromBackend(result) || (result.ok ? "我学完了。" : "自主学习失败。");
    emitLearningMessage("assistant", finalText, !result.ok);
    return { ...result, scheduler: learningSchedulerSnapshot(settings) };
  } catch (error) {
    LEARNING_SCHEDULER_STATE.lastFinishAt = Date.now();
    LEARNING_SCHEDULER_STATE.lastResultOk = false;
    LEARNING_SCHEDULER_STATE.lastError = error?.message || String(error);
    writeBackendRunLog("learning_tick_error", {
      trigger,
      error: LEARNING_SCHEDULER_STATE.lastError,
      scheduler: learningSchedulerSnapshot(settings)
    });
    throw error;
  }
}

function startLearningScheduler() {
  if (LEARNING_SCHEDULER_TIMER) clearInterval(LEARNING_SCHEDULER_TIMER);
  if (LEARNING_SCHEDULER_STARTUP_TIMER) clearTimeout(LEARNING_SCHEDULER_STARTUP_TIMER);
  LEARNING_SCHEDULER_STATE.startedAt = Date.now();
  const checkEveryMs = learningSchedulerCheckEveryMs();
  writeBackendRunLog("learning_scheduler_start", {
    scheduler: learningSchedulerSnapshot()
  });
  LEARNING_SCHEDULER_STARTUP_TIMER = setTimeout(() => {
    LEARNING_SCHEDULER_STARTUP_TIMER = null;
    runAutonomousLearningTick("cron").catch((error) => {
      LEARNING_SCHEDULER_STATE.lastError = error?.message || String(error);
      emitLearningMessage("assistant", `Self-learning scheduler failed: ${error?.message || String(error)}`, true);
    });
  }, Math.min(30000, checkEveryMs));
  LEARNING_SCHEDULER_TIMER = setInterval(() => {
    runAutonomousLearningTick("cron").catch((error) => {
      LEARNING_SCHEDULER_STATE.lastError = error?.message || String(error);
      emitLearningMessage("assistant", `Self-learning scheduler failed: ${error?.message || String(error)}`, true);
    });
  }, checkEveryMs);
}

function parseMemoryHeartbeatStdout(stdout) {
  const text = String(stdout || "").trim();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

async function runMemoryHeartbeatTick(trigger = "cron") {
  const settings = readSettings();
  MEMORY_HEARTBEAT_STATE.lastTickAt = Date.now();
  MEMORY_HEARTBEAT_STATE.tickCount += 1;
  MEMORY_HEARTBEAT_STATE.lastError = "";
  if (MEMORY_HEARTBEAT_STATE.running) {
    return markMemoryHeartbeatSkip("already_running", trigger);
  }
  if (ACTIVE_BACKEND_RUNS.size > 0) {
    return markMemoryHeartbeatSkip("frontend_busy", trigger, { activeBackendRuns: ACTIVE_BACKEND_RUNS.size });
  }
  const now = Date.now();
  const intervalMs = memoryHeartbeatIntervalMs();
  if (trigger === "cron" && LAST_MEMORY_HEARTBEAT_AT && now - LAST_MEMORY_HEARTBEAT_AT < intervalMs) {
    return markMemoryHeartbeatSkip("interval_not_reached", trigger, {
      remainingMs: Math.max(0, intervalMs - (now - LAST_MEMORY_HEARTBEAT_AT))
    });
  }

  const py = pythonCommand();
  if (py.missing) {
    MEMORY_HEARTBEAT_STATE.lastFinishAt = Date.now();
    MEMORY_HEARTBEAT_STATE.lastResultOk = false;
    MEMORY_HEARTBEAT_STATE.lastError = py.message || "Python runtime unavailable.";
    writeBackendRunLog("memory_heartbeat_error", {
      trigger,
      error: MEMORY_HEARTBEAT_STATE.lastError,
      scheduler: memoryHeartbeatSnapshot(settings)
    });
    return { ok: false, error: MEMORY_HEARTBEAT_STATE.lastError, scheduler: memoryHeartbeatSnapshot(settings) };
  }

  LAST_MEMORY_HEARTBEAT_AT = now;
  MEMORY_HEARTBEAT_STATE.lastRunAt = now;
  MEMORY_HEARTBEAT_STATE.runCount += 1;
  MEMORY_HEARTBEAT_STATE.running = true;
  const workspace = String(settings.workspace || defaultWorkspace());
  writeBackendRunLog("memory_heartbeat_start", {
    trigger,
    scheduler: memoryHeartbeatSnapshot(settings)
  });

  return new Promise((resolve) => {
    const args = pythonScriptArgs(py, RUN_AGENT, [
      "--memory-heartbeat",
      "--workspace",
      workspace,
      "--max-steps",
      String(normalizeMaxSteps(settings.maxSteps || 20)),
      ...modelArgsFromSettings(settings)
    ]);
    const child = spawn(py.command, args, {
      cwd: ROOT,
      env: backendPythonEnv(py, {
        ...runtimeStateEnv(),
        ...modelEnvFromSettings(settings),
        TIANGONG_PERMISSION_MODE: normalizePermissionMode(settings.permissionMode || "workspace_full"),
        LINYUANZHE_PERMISSION_MODE: normalizePermissionMode(settings.permissionMode || "workspace_full"),
        TIANGONG_SESSION_SCOPE: sessionScope(settings, workspace)
      }),
      windowsHide: true
    });
    let stdout = "";
    let stderr = "";
    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      MEMORY_HEARTBEAT_STATE.running = false;
      const parsed = parseMemoryHeartbeatStdout(result.stdout);
      const ok = Boolean(result.ok && (!parsed || parsed.ok !== false));
      MEMORY_HEARTBEAT_STATE.lastFinishAt = Date.now();
      MEMORY_HEARTBEAT_STATE.lastResultOk = ok;
      MEMORY_HEARTBEAT_STATE.lastDurationMs = Number(parsed?.duration_ms || Math.max(0, MEMORY_HEARTBEAT_STATE.lastFinishAt - now));
      MEMORY_HEARTBEAT_STATE.lastDelta = parsed?.delta || null;
      MEMORY_HEARTBEAT_STATE.lastError = ok ? "" : String(result.stderr || parsed?.error || result.error || "memory_heartbeat_failed").slice(0, 1000);
      writeBackendRunLog(ok ? "memory_heartbeat_finish" : "memory_heartbeat_error", {
        trigger,
        ok,
        code: result.code ?? "",
        result: parsed || null,
        scheduler: memoryHeartbeatSnapshot(settings)
      });
      resolve({ ...result, ok, result: parsed, scheduler: memoryHeartbeatSnapshot(settings) });
    };
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
    });
    child.on("error", (error) => {
      finish({
        ok: false,
        code: 1,
        stdout,
        stderr,
        error: error?.message || String(error)
      });
    });
    child.on("close", (code) => {
      finish({
        ok: code === 0,
        code,
        stdout,
        stderr
      });
    });
  });
}

function startMemoryHeartbeatScheduler() {
  if (MEMORY_HEARTBEAT_TIMER) clearInterval(MEMORY_HEARTBEAT_TIMER);
  if (MEMORY_HEARTBEAT_STARTUP_TIMER) clearTimeout(MEMORY_HEARTBEAT_STARTUP_TIMER);
  MEMORY_HEARTBEAT_STATE.startedAt = Date.now();
  const checkEveryMs = memoryHeartbeatCheckEveryMs();
  writeBackendRunLog("memory_heartbeat_scheduler_start", {
    scheduler: memoryHeartbeatSnapshot()
  });
  MEMORY_HEARTBEAT_STARTUP_TIMER = setTimeout(() => {
    MEMORY_HEARTBEAT_STARTUP_TIMER = null;
    runMemoryHeartbeatTick("cron").catch((error) => {
      MEMORY_HEARTBEAT_STATE.running = false;
      MEMORY_HEARTBEAT_STATE.lastError = error?.message || String(error);
      writeBackendRunLog("memory_heartbeat_error", {
        trigger: "cron",
        error: MEMORY_HEARTBEAT_STATE.lastError,
        scheduler: memoryHeartbeatSnapshot()
      });
    });
  }, Math.min(2 * 60 * 1000, checkEveryMs));
  MEMORY_HEARTBEAT_TIMER = setInterval(() => {
    runMemoryHeartbeatTick("cron").catch((error) => {
      MEMORY_HEARTBEAT_STATE.running = false;
      MEMORY_HEARTBEAT_STATE.lastError = error?.message || String(error);
      writeBackendRunLog("memory_heartbeat_error", {
        trigger: "cron",
        error: MEMORY_HEARTBEAT_STATE.lastError,
        scheduler: memoryHeartbeatSnapshot()
      });
    });
  }, checkEveryMs);
}

function emitRunStep(requestId, payload) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  try {
    mainWindow.webContents.send(STREAM_EVENT_CHANNEL, {
      ...(payload || {}),
      requestId: String(payload?.requestId || payload?.request_id || requestId || ""),
      request_id: String(payload?.request_id || payload?.requestId || requestId || "")
    });
  } catch {
    // Progress events must not affect backend execution.
  }
}

function activeBackendRun(requestId = "") {
  const id = String(requestId || "").trim();
  if (id && ACTIVE_BACKEND_RUNS.has(id)) return ACTIVE_BACKEND_RUNS.get(id);
  const runs = [...ACTIVE_BACKEND_RUNS.values()].sort((left, right) => Number(right.started || 0) - Number(left.started || 0));
  return runs[0] || null;
}

function runGuidanceDir() {
  const root = path.join(packageStateRoot(), "runtime_guidance");
  fs.mkdirSync(root, { recursive: true });
  return root;
}

function safeRunId(value) {
  return String(value || "run")
    .trim()
    .replace(/[^a-zA-Z0-9._-]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 96) || "run";
}

function runGuidancePath(requestId) {
  return path.join(runGuidanceDir(), `${safeRunId(requestId)}.jsonl`);
}

function appendRuntimeGuidance(payload = {}) {
  const run = activeBackendRun(payload.requestId || payload.request_id);
  if (!run) return { ok: false, error: "no_active_backend_run" };
  const text = String(payload.message || payload.text || payload.content || "").trim();
  if (!text) return { ok: false, error: "empty_guidance", requestId: run.requestId };
  const record = {
    schema: "tiangong.codex.runtime_guidance.v1",
    id: crypto.randomUUID(),
    requestId: run.requestId,
    at: Date.now(),
    source: "frontend",
    text: text.slice(0, 4000)
  };
  const guidancePath = run.guidancePath || runGuidancePath(run.requestId);
  fs.mkdirSync(path.dirname(guidancePath), { recursive: true });
  fs.appendFileSync(guidancePath, JSON.stringify(record) + "\n", "utf8");
  emitRunStep(run.requestId, {
    schema: "tiangong.desktop.stream_event.v1",
    type: "step",
    step_id: "codex_runtime_guidance",
    title: "收到运行中纠偏",
    status: "done",
    summary: text.slice(0, 120),
    guidance: record
  });
  writeBackendRunLog("runtime_guidance", {
    requestId: run.requestId,
    guidancePath,
    messagePreview: text.slice(0, 240)
  });
  return { ok: true, requestId: run.requestId, guidancePath, guidance: record };
}

function consumeStreamEventLine(line, requestId) {
  const text = String(line || "").trim();
  if (!text.startsWith(STREAM_EVENT_PREFIX)) return false;
  try {
    const payload = JSON.parse(text.slice(STREAM_EVENT_PREFIX.length).trim());
    emitRunStep(requestId, payload);
    return payload;
  } catch (error) {
    emitRunStep(requestId, {
      schema: "tiangong.desktop.stream_event.v1",
      type: "step",
      step_id: "progress_parse",
      title: "运行步骤解析",
      status: "failed",
      summary: error?.message || String(error)
    });
  }
  return false;
}

function runBackend(message, options = {}) {
  return new Promise((resolve) => {
    if (!fs.existsSync(RUN_AGENT)) {
      resolve({
        ok: false,
        code: 1,
        stdout: "",
        stderr: "找不到后端运行代理",
        elapsedMs: 0
      });
      return;
    }

    const settings = { ...readSettings(), ...(options || {}) };
    const requestId = String(options.requestId || options.request_id || crypto.randomUUID());
    const guidancePath = runGuidancePath(requestId);
    try {
      fs.rmSync(guidancePath, { force: true });
    } catch {
      // Guidance is best-effort; stale files are ignored by per-run ids.
    }
    const workspace = String(options.workspace || settings.workspace || defaultWorkspace());
    const numericMaxSteps = normalizeMaxSteps(options.maxSteps || settings.maxSteps || 20);
    const maxSteps = String(numericMaxSteps);
    const hardTimeoutMs = Math.max(
      60000,
      Math.min(900000, Number(process.env.TIANGONG_BACKEND_TIMEOUT_MS || 0) || (60000 + numericMaxSteps * 45000))
    );
    const afterOutputGraceMs = Math.max(
      3000,
      Math.min(60000, Number(process.env.TIANGONG_BACKEND_AFTER_OUTPUT_GRACE_MS || 0) || 8000)
    );
    const mode = normalizeFrontendMode(options.mode || options.workMode || settings.mode || "auto");
    const taskMode = mode === "work" ? "work_task" : (mode === "chat" ? "ordinary_chat" : "auto");
    const toolsRequested = mode === "work" ? "1" : (mode === "chat" ? "0" : "");
    const plannerAllowed = mode === "work" ? "1" : (mode === "chat" ? "0" : "");
    const permissionMode = normalizePermissionMode(options.permissionMode || settings.permissionMode || "workspace_full");
    const personaName = String(options.personaName || settings.personaName || "临渊者").slice(0, 32);
    const soulPrompt = String(options.soulPrompt || settings.soulPrompt || "").slice(0, 6000);
    const chatAttachments = normalizeChatAttachments(options.attachments);
    const frontendMessages = normalizeFrontendMessages(options.recentMessages || options.messages || options.conversation);
    const frontendWorkContext = normalizeFrontendWorkContext(options.workContext || options.recentWorkContext || options.work);
    const selectedSkills = normalizeSelectedSkills(options.selectedSkills, options.selectedSkillNames || options.selected_skill_names);
    const manualLearningAction = String(options.learningAction || options.learning_action || "").trim().toLowerCase();
    const manualLearningId = String(options.learningId || options.learning_id || options.experienceId || "").trim();
    writeBackendRunLog("start", {
      requestId,
      workspace,
      mode,
      maxSteps,
      provider: settings.modelProvider || settings.modelService || "",
      model: settings.modelName || "",
      permissionMode,
      attachmentCount: chatAttachments.length,
      conversationCount: frontendMessages.length,
      workContextPresent: Boolean(frontendWorkContext),
      selectedSkills: selectedSkills.map((item) => item.name).join(", "),
      manualLearningAction,
      manualLearningId,
      messagePreview: String(message || "").slice(0, 240)
    });
    const py = pythonCommand();
    if (py.missing) {
      resolve(pythonUnavailableResult(py));
      return;
    }
    const args = pythonScriptArgs(py, RUN_AGENT, [
      "--once",
      String(message || ""),
      "--workspace",
      workspace,
      "--max-steps",
      maxSteps,
      ...modelArgsFromSettings(settings)
    ]);
    const env = backendPythonEnv(py, {
      ...runtimeStateEnv(),
      ...modelEnvFromSettings(settings),
      TIANGONG_SESSION_SCOPE: sessionScope(settings, workspace),
      TIANGONG_SOUL_NAME: personaName,
      LINYUANZHE_PERSONA_NAME: personaName,
      TIANGONG_SOUL_PROMPT: soulPrompt,
      LINYUANZHE_PERSONA_PROMPT: soulPrompt,
      LINYUANZHE_FRONTEND_WORK_MODE: mode,
      TIANGONG_PERMISSION_MODE: permissionMode,
      LINYUANZHE_PERMISSION_MODE: permissionMode,
      TIANGONG_TASK_MODE: taskMode,
      LINYUANZHE_TOOLS_REQUESTED: toolsRequested,
      LINYUANZHE_PLANNER_ALLOWED: plannerAllowed,
      TIANGONG_UPLOAD_FILES_JSON: JSON.stringify(chatAttachments),
      TIANGONG_FRONTEND_MESSAGES_JSON: JSON.stringify(frontendMessages),
      TIANGONG_FRONTEND_WORK_CONTEXT_JSON: JSON.stringify(frontendWorkContext || {}),
      TIANGONG_SELECTED_SKILLS_JSON: JSON.stringify(selectedSkills),
      TIANGONG_SELECTED_SKILL_NAMES: selectedSkills.map((item) => item.name).join("\n"),
      TIANGONG_MANUAL_LEARNING_ACTION: manualLearningAction && manualLearningId ? manualLearningAction : "",
      TIANGONG_MANUAL_LEARNING_ID: manualLearningAction && manualLearningId ? manualLearningId : "",
      TIANGONG_STREAM_EVENTS: "1",
      TIANGONG_REQUEST_ID: requestId,
      TIANGONG_RUNTIME_GUIDANCE_PATH: guidancePath,
      TIANGONG_CODEX_GUIDANCE_PATH: guidancePath
    });
    delete env.PYTHONSTARTUP;
    const syncLearningOverride = String(env.TIANGONG_DESKTOP_SYNC_LEARNING || "").trim().toLowerCase();
    const foregroundReturnEnabled = !["1", "true", "yes", "on"].includes(syncLearningOverride);
    const foregroundReturnGraceMs = Math.max(
      200,
      Math.min(3000, Number(process.env.TIANGONG_BACKEND_FOREGROUND_RETURN_GRACE_MS || 0) || 500)
    );

    const started = Date.now();
    const child = spawn(py.command, args, {
      cwd: ROOT,
      env,
      windowsHide: true
    });
    let stdout = "";
    let stderr = "";
    let settled = false;
    let lastStdoutAt = 0;
    let streamLineBuffer = "";
    let cancelRequested = false;
    let outputSessionDoneAt = 0;

    function isOutputSessionDoneEvent(payload) {
      if (!payload || typeof payload !== "object") return false;
      const stepId = String(payload.step_id || payload.stepId || payload.id || "");
      const status = String(payload.status || "").toLowerCase();
      return stepId === "output_session" && ["done", "ok", "success", "completed"].includes(status);
    }

    function consumeStreamChunk(text) {
      streamLineBuffer += String(text || "");
      const lines = streamLineBuffer.split(/\r?\n/);
      streamLineBuffer = lines.pop() || "";
      for (const line of lines) {
        const payload = consumeStreamEventLine(line, requestId);
        if (isOutputSessionDoneEvent(payload)) outputSessionDoneAt = Date.now();
      }
    }

    function flushStreamBuffer() {
      if (!streamLineBuffer) return;
      consumeStreamEventLine(streamLineBuffer, requestId);
      streamLineBuffer = "";
    }

    function stopChild() {
      if (!child.pid || child.killed) return;
      try {
        if (process.platform === "win32") {
          spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"], { windowsHide: true });
        } else {
          child.kill("SIGTERM");
        }
      } catch {
        try {
          child.kill();
        } catch {
          // Ignore shutdown races.
        }
      }
    }

    function cancelRun(reason = "user_interrupted") {
      if (settled) {
        return { ok: false, canceled: false, interrupted: false, requestId, error: "run_already_finished" };
      }
      cancelRequested = true;
      const summary = reason === "user_interrupted"
        ? "用户已中断，本次进度和上下文已保留，可继续。"
        : String(reason || "interrupted");
      emitRunStep(requestId, {
        schema: "tiangong.desktop.stream_event.v1",
        type: "step",
        step_id: "frontend_interrupt",
        title: "用户中断",
        status: "interrupted",
        summary
      });
      stderr = `${stderr}\n[HealthState] ${summary}`.trim();
      stopChild();
      finish({ ok: false, canceled: true, interrupted: true, code: 130, stdout, stderr });
      return { ok: true, canceled: true, interrupted: true, requestId };
    }

    ACTIVE_BACKEND_RUNS.set(requestId, {
      requestId,
      started,
      workspace,
      mode,
      childPid: child.pid,
      guidancePath,
      cancel: cancelRun
    });

    function finish(result) {
      if (settled) return;
      settled = true;
      ACTIVE_BACKEND_RUNS.delete(requestId);
      clearTimeout(hardTimer);
      clearInterval(afterOutputTimer);
      writeBackendRunLog("finish", {
        requestId,
        ok: Boolean(result.ok),
        interrupted: Boolean(result.interrupted || result.canceled || cancelRequested),
        code: result.code ?? "",
        elapsedMs: Date.now() - started,
        workspace,
        mode,
        maxSteps,
        stdout: result.stdout || "",
        stderr: result.stderr || ""
      });
      resolve({
        ...result,
        elapsedMs: Date.now() - started,
        workspace,
        mode,
        maxSteps,
        requestId
      });
    }

    function stdoutHasCompletionSignal() {
      const text = String(stdout || "");
      return text.includes("[CoreResult / direct-file 写回验真]")
        || text.includes("[CoreResult / pytest 验真]")
        || (text.includes("CoreResult.status=") && text.includes("HealthState="));
    }

    const hardTimer = setTimeout(() => {
      stderr = `${stderr}\n[HealthState] 后端执行超过 ${hardTimeoutMs}ms，已终止。`.trim();
      stopChild();
      finish({ ok: false, code: 124, stdout, stderr });
    }, hardTimeoutMs);

    const afterOutputTimer = setInterval(() => {
      if (!stdout.trim() || !lastStdoutAt) return;
      if (foregroundReturnEnabled && outputSessionDoneAt) {
        if (Date.now() - outputSessionDoneAt < foregroundReturnGraceMs) return;
        finish({ ok: true, code: 0, stdout, stderr, foregroundReturned: true, postprocessBackground: true });
        return;
      }
      if (!stdoutHasCompletionSignal()) return;
      if (Date.now() - lastStdoutAt < afterOutputGraceMs) return;
      stderr = `${stderr}\n[HealthState] 后端已输出结果但未退出，已结束同步后处理等待。`.trim();
      stopChild();
      finish({ ok: true, code: 0, stdout, stderr, postprocessTerminated: true });
    }, 1000);

    child.stdout.on("data", (chunk) => {
      const text = chunk.toString("utf8");
      stdout += text;
      lastStdoutAt = Date.now();
      consumeStreamChunk(text);
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
    });
    child.on("error", (error) => {
      finish({
        ok: false,
        code: 1,
        stdout,
        stderr: `${stderr}\n${error.message}`.trim()
      });
    });
    child.on("close", (code) => {
      flushStreamBuffer();
      finish({
        ok: code === 0,
        code,
        stdout,
        stderr
      });
    });
  });
}

function runStatus() {
  return new Promise((resolve) => {
    const settings = readSettings();
    const workspace = String(settings.workspace || defaultWorkspace());
    const py = pythonCommand();
    if (py.missing) {
      resolve({ ok: false, code: 1, stdout: "", stderr: py.message || "Python 运行时不可用。" });
      return;
    }
    const args = pythonScriptArgs(py, RUN_AGENT, [
      "--status",
      "--workspace",
      workspace,
      "--max-steps",
      String(normalizeMaxSteps(settings.maxSteps || 20)),
      ...modelArgsFromSettings(settings)
    ]);
    const child = spawn(py.command, args, {
      cwd: ROOT,
      env: backendPythonEnv(py, {
        ...runtimeStateEnv(),
        ...modelEnvFromSettings(settings),
        TIANGONG_PERMISSION_MODE: normalizePermissionMode(settings.permissionMode || "workspace_full"),
        LINYUANZHE_PERMISSION_MODE: normalizePermissionMode(settings.permissionMode || "workspace_full"),
        TIANGONG_SESSION_SCOPE: sessionScope(settings, workspace)
      }),
      windowsHide: true
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
    });
    child.on("error", (error) => resolve({ ok: false, stdout, stderr: error.message }));
    child.on("close", (code) => {
      const scheduler = learningSchedulerSnapshot(settings);
      const memoryHeartbeat = memoryHeartbeatSnapshot(settings);
      const augmentedStdout = injectDesktopStatusPayload(stdout, {
        learning_scheduler: scheduler,
        memory_heartbeat: memoryHeartbeat
      });
      resolve({ ok: code === 0, code, stdout: augmentedStdout, stderr });
    });
  });
}

function runConfig() {
  return new Promise((resolve) => {
    const settings = readSettings();
    const workspace = String(settings.workspace || defaultWorkspace());
    const py = pythonCommand();
    if (py.missing) {
      resolve({ ok: false, code: 1, stdout: "", stderr: py.message || "Python 运行时不可用。" });
      return;
    }
    const args = pythonScriptArgs(py, RUN_AGENT, [
      "--show-config",
      "--workspace",
      workspace,
      "--max-steps",
      String(normalizeMaxSteps(settings.maxSteps || 20)),
      ...modelArgsFromSettings(settings)
    ]);
    const child = spawn(py.command, args, {
      cwd: ROOT,
      env: backendPythonEnv(py, {
        ...runtimeStateEnv(),
        ...modelEnvFromSettings(settings),
        TIANGONG_PERMISSION_MODE: normalizePermissionMode(settings.permissionMode || "workspace_full"),
        LINYUANZHE_PERMISSION_MODE: normalizePermissionMode(settings.permissionMode || "workspace_full"),
        TIANGONG_SESSION_SCOPE: sessionScope(settings, workspace)
      }),
      windowsHide: true
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
    });
    child.on("error", (error) => resolve({ ok: false, stdout, stderr: error.message }));
    child.on("close", (code) => resolve({ ok: code === 0, code, stdout, stderr }));
  });
}

function parseBridgeJson(stdout, fallback = {}) {
  try {
    return JSON.parse(String(stdout || "").trim() || "{}");
  } catch {
    return { ok: false, error: "桌面桥接输出无法解析", stdout, ...fallback };
  }
}

function readJsonFileSafe(filePath, fallback = null) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return fallback;
  }
}

function readJsonLinesSafe(filePath) {
  try {
    return fs.readFileSync(filePath, "utf8")
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        try {
          return JSON.parse(line);
        } catch {
          return null;
        }
      })
      .filter(Boolean);
  } catch {
    return [];
  }
}

const TIANGONG_MESSAGE_CHANNELS = {
  desktop: {
    id: "desktop",
    label: "桌面",
    connectLabel: "桌面入口",
    transport: "electron"
  },
  weixin: {
    id: "weixin",
    label: "微信",
    connectLabel: "微信连接",
    transport: "adapter",
    adapterState: "pending"
  },
  feishu: {
    id: "feishu",
    label: "飞书",
    connectLabel: "飞书连接",
    transport: "websocket",
    adapterState: "pending"
  }
};

function messageGatewayDir() {
  const root = path.join(packageStateRoot(), "message_gateway");
  fs.mkdirSync(root, { recursive: true });
  return root;
}

function messageGatewayConfigPath() {
  return path.join(messageGatewayDir(), "config.json");
}

function defaultMessageGatewayConfig() {
  return {
    schema: "tiangong.message_gateway.config.v1",
    enabled: false,
    host: MESSAGE_GATEWAY_HOST,
    port: MESSAGE_GATEWAY_DEFAULT_PORT,
    sharedConversationId: "primary",
    identityMode: "shared_user",
    channels: {
      desktop: { enabled: true, adapter: "electron" },
      weixin: { enabled: false, adapter: "tiangong-weixin", adapterState: "pending", credentialed: false, qrUrl: "", authUrl: "" },
      feishu: { enabled: false, adapter: "tiangong-feishu", adapterState: "pending", credentialed: false, qrUrl: "", authUrl: "" }
    }
  };
}

function normalizeMessageGatewayConfig(config) {
  const fallback = defaultMessageGatewayConfig();
  const next = config && typeof config === "object" && !Array.isArray(config) ? config : {};
  const channels = next.channels && typeof next.channels === "object" && !Array.isArray(next.channels) ? next.channels : {};
  return {
    ...fallback,
    ...next,
    host: MESSAGE_GATEWAY_HOST,
    port: Number(next.port || fallback.port) || fallback.port,
    sharedConversationId: String(next.sharedConversationId || fallback.sharedConversationId || "primary"),
    identityMode: String(next.identityMode || fallback.identityMode || "shared_user"),
    channels: {
      desktop: { ...fallback.channels.desktop, ...(channels.desktop || {}), enabled: true },
      weixin: { ...fallback.channels.weixin, ...(channels.weixin || {}) },
      feishu: { ...fallback.channels.feishu, ...(channels.feishu || {}) }
    }
  };
}

function readMessageGatewayConfig() {
  return normalizeMessageGatewayConfig(readJsonFileSafe(messageGatewayConfigPath(), null));
}

function writeMessageGatewayConfig(patch = {}) {
  const current = readMessageGatewayConfig();
  const channelPatch = patch.channels && typeof patch.channels === "object" && !Array.isArray(patch.channels)
    ? patch.channels
    : {};
  const next = normalizeMessageGatewayConfig({
    ...current,
    ...patch,
    channels: {
      ...current.channels,
      ...Object.fromEntries(
        Object.entries(channelPatch).map(([key, value]) => [
          key,
          { ...(current.channels[key] || {}), ...(value || {}) }
        ])
      )
    }
  });
  fs.mkdirSync(path.dirname(messageGatewayConfigPath()), { recursive: true });
  fs.writeFileSync(messageGatewayConfigPath(), JSON.stringify(next, null, 2), "utf8");
  return next;
}

function safeGatewayId(value, fallback = "primary") {
  const text = String(value || fallback || "primary")
    .trim()
    .replace(/[^a-zA-Z0-9._-]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return text || fallback || "primary";
}

function messageGatewayConversationId(config = null) {
  const cfg = config || readMessageGatewayConfig();
  return safeGatewayId(cfg.sharedConversationId || "primary", "primary");
}

function messageGatewayHistoryDir() {
  const root = path.join(messageGatewayDir(), "conversations");
  fs.mkdirSync(root, { recursive: true });
  return root;
}

function messageGatewayHistoryPath(conversationId = "primary") {
  return path.join(messageGatewayHistoryDir(), `${safeGatewayId(conversationId)}.jsonl`);
}

function appendJsonLine(filePath, payload) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.appendFileSync(filePath, JSON.stringify(payload) + "\n", "utf8");
}

function normalizeGatewayRole(value) {
  const role = String(value || "").trim().toLowerCase();
  return role === "assistant" ? "assistant" : "user";
}

function normalizeGatewayChannel(value) {
  const raw = String(value || "").trim().toLowerCase();
  if (raw === "wechat" || raw === "wx") return "weixin";
  if (raw === "lark") return "feishu";
  if (raw === "desktop" || raw === "weixin" || raw === "feishu") return raw;
  return "desktop";
}

function appendGatewayMessage(record = {}) {
  const config = readMessageGatewayConfig();
  const conversationId = safeGatewayId(record.conversationId || record.conversation_id || messageGatewayConversationId(config));
  const item = {
    schema: "tiangong.message_gateway.message.v1",
    id: String(record.id || crypto.randomUUID()),
    at: Number(record.at || Date.now()),
    conversationId,
    role: normalizeGatewayRole(record.role),
    channel: normalizeGatewayChannel(record.channel || record.source),
    senderId: String(record.senderId || record.sender_id || record.userId || record.user_id || ""),
    content: String(record.content || record.text || record.message || "").slice(0, FRONTEND_HISTORY_MAX_CONTENT),
    error: Boolean(record.error),
    external: record.external && typeof record.external === "object" ? record.external : {}
  };
  if (!item.content) return null;
  appendJsonLine(messageGatewayHistoryPath(conversationId), item);
  return item;
}

function readGatewayRecentMessages(conversationId = "primary", limit = MESSAGE_GATEWAY_HISTORY_LIMIT) {
  const rows = readJsonLinesSafe(messageGatewayHistoryPath(conversationId));
  return rows
    .filter((item) => item?.role === "user" || item?.role === "assistant")
    .map((item) => ({
      role: item.role,
      content: String(item.content || "").slice(0, FRONTEND_HISTORY_MAX_CONTENT),
      error: Boolean(item.error),
      at: Number(item.at || 0)
    }))
    .filter((item) => item.content)
    .slice(-Math.max(1, Math.min(100, Number(limit) || MESSAGE_GATEWAY_HISTORY_LIMIT)));
}

function mergeRecentMessages(...groups) {
  const out = [];
  const seen = new Set();
  for (const group of groups) {
    for (const item of normalizeFrontendMessages(group || [])) {
      const key = `${item.role}\n${item.content}\n${item.at || ""}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(item);
    }
  }
  return out.slice(-FRONTEND_HISTORY_MAX_MESSAGES);
}

function backendVisibleReply(result) {
  const text = finalVisibleTextFromBackend(result);
  return String(text || (result?.ok ? "已完成。" : result?.stderr || result?.error || "执行失败。")).slice(0, FRONTEND_HISTORY_MAX_CONTENT);
}

function messageGatewayChannelStatus(channelId, config) {
  const channel = TIANGONG_MESSAGE_CHANNELS[channelId];
  const channelConfig = config.channels?.[channelId] || {};
  const running = Boolean(MESSAGE_GATEWAY_SERVER);
  const enabled = channelId === "desktop" || channelConfig.enabled === true;
  const credentialed = channelConfig.credentialed === true;
  const qrUrl = String(channelConfig.qrUrl || "").trim();
  const authUrl = String(channelConfig.authUrl || "").trim();
  let state = "not_configured";
  let detail = "未启用。";
  if (channelId === "desktop") {
    state = "ready";
    detail = "桌面入口已写入同一个天工消息会话。";
  } else if (credentialed && enabled) {
    state = "ready";
    detail = `${channel.label}适配器已登记，消息会写入同一个天工消息会话。`;
  } else if ((qrUrl || authUrl) && enabled && running) {
    state = "installed";
    detail = `${channel.label}适配器已给出连接码，请扫码或打开授权链接。`;
  } else if (enabled && running) {
    state = "adapter_pending";
    detail = channelId === "feishu"
      ? "天工消息网关已启动；下一步接入飞书 WebSocket 适配器。"
      : "天工消息网关已启动；下一步接入微信官方扫码与消息监听适配器。";
  } else if (enabled) {
    state = "gateway_stopped";
    detail = "天工消息通道已启用，网关未运行。";
  }
  return {
    ...channel,
    state,
    enabled,
    installed: running,
    credentialed,
    qrUrl,
    authUrl,
    detail
  };
}

function tiangongMessageGatewayStatus() {
  const config = readMessageGatewayConfig();
  const running = Boolean(MESSAGE_GATEWAY_SERVER);
  return {
    ok: true,
    mode: "tiangong_message_gateway",
    running,
    host: MESSAGE_GATEWAY_HOST,
    port: MESSAGE_GATEWAY_PORT || 0,
    endpoint: running ? `http://${MESSAGE_GATEWAY_HOST}:${MESSAGE_GATEWAY_PORT}` : "",
    stateDir: messageGatewayDir(),
    sharedConversationId: messageGatewayConversationId(config),
    stats: {
      ...MESSAGE_GATEWAY_STATS,
      startedAt: isoOrEmpty(MESSAGE_GATEWAY_STATS.startedAt),
      lastInboundAt: isoOrEmpty(MESSAGE_GATEWAY_STATS.lastInboundAt),
      lastFinishAt: isoOrEmpty(MESSAGE_GATEWAY_STATS.lastFinishAt)
    },
    channels: Object.fromEntries(
      Object.keys(TIANGONG_MESSAGE_CHANNELS).map((id) => [id, messageGatewayChannelStatus(id, config)])
    )
  };
}

function readRequestBody(req, maxBytes = 2 * 1024 * 1024) {
  return new Promise((resolve, reject) => {
    let total = 0;
    const chunks = [];
    req.on("data", (chunk) => {
      total += chunk.length;
      if (total > maxBytes) {
        reject(new Error("request_body_too_large"));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

function sendGatewayJson(res, statusCode, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(statusCode, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store"
  });
  res.end(body);
}

async function handleMessageGatewayRequest(req, res) {
  const url = new URL(req.url || "/", `http://${MESSAGE_GATEWAY_HOST}:${MESSAGE_GATEWAY_PORT || MESSAGE_GATEWAY_DEFAULT_PORT}`);
  if (req.method === "GET" && (url.pathname === "/health" || url.pathname === "/status")) {
    sendGatewayJson(res, 200, tiangongMessageGatewayStatus());
    return;
  }
  if (req.method === "POST" && ["/message", "/inbound", "/channels/inbound"].includes(url.pathname)) {
    const raw = await readRequestBody(req);
    const payload = raw.trim() ? JSON.parse(raw) : {};
    const result = await enqueueTiangongMessageGatewayDispatch({
      ...payload,
      channel: payload.channel || payload.source || url.searchParams.get("channel") || "desktop"
    });
    sendGatewayJson(res, result?.ok === false ? 500 : 200, result);
    return;
  }
  if (req.method === "POST" && (url.pathname === "/channels/register" || url.pathname === "/adapter/register")) {
    const raw = await readRequestBody(req);
    const payload = raw.trim() ? JSON.parse(raw) : {};
    const channel = normalizeGatewayChannel(payload.channel || payload.id || url.searchParams.get("channel") || "");
    if (!TIANGONG_MESSAGE_CHANNELS[channel] || channel === "desktop") {
      sendGatewayJson(res, 400, { ok: false, error: "unsupported_channel" });
      return;
    }
    writeMessageGatewayConfig({
      channels: {
        [channel]: {
          enabled: true,
          adapterState: String(payload.adapterState || payload.state || "registered"),
          credentialed: payload.credentialed === true,
          qrUrl: String(payload.qrUrl || payload.qr_url || ""),
          authUrl: String(payload.authUrl || payload.auth_url || ""),
          detail: String(payload.detail || payload.message || "")
        }
      }
    });
    sendGatewayJson(res, 200, tiangongMessageGatewayStatus());
    return;
  }
  sendGatewayJson(res, 404, { ok: false, error: "not_found" });
}

function startTiangongMessageGateway(patch = {}) {
  const nextConfig = writeMessageGatewayConfig({ ...patch, enabled: true });
  if (MESSAGE_GATEWAY_SERVER) return Promise.resolve(tiangongMessageGatewayStatus());
  if (MESSAGE_GATEWAY_STARTING) return MESSAGE_GATEWAY_STARTING;

  MESSAGE_GATEWAY_STARTING = new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      handleMessageGatewayRequest(req, res).catch((error) => {
        MESSAGE_GATEWAY_STATS.lastError = error?.message || String(error);
        sendGatewayJson(res, 500, { ok: false, error: MESSAGE_GATEWAY_STATS.lastError });
      });
    });
    const requestedPort = Number(nextConfig.port || MESSAGE_GATEWAY_DEFAULT_PORT) || MESSAGE_GATEWAY_DEFAULT_PORT;
    let triedFallback = false;

    function listen(port) {
      server.once("error", (error) => {
        if (!triedFallback && error?.code === "EADDRINUSE" && port !== 0) {
          triedFallback = true;
          listen(0);
          return;
        }
        MESSAGE_GATEWAY_STARTING = null;
        MESSAGE_GATEWAY_STATS.lastError = error?.message || String(error);
        resolve({ ok: false, error: MESSAGE_GATEWAY_STATS.lastError, ...tiangongMessageGatewayStatus() });
      });
      server.listen(port, MESSAGE_GATEWAY_HOST, () => {
        MESSAGE_GATEWAY_SERVER = server;
        MESSAGE_GATEWAY_PORT = server.address()?.port || port;
        MESSAGE_GATEWAY_STATS.startedAt = Date.now();
        MESSAGE_GATEWAY_STATS.lastError = "";
        MESSAGE_GATEWAY_STARTING = null;
        writeMessageGatewayConfig({ enabled: true, port: MESSAGE_GATEWAY_PORT });
        resolve(tiangongMessageGatewayStatus());
      });
    }

    listen(requestedPort);
  });
  return MESSAGE_GATEWAY_STARTING;
}

function stopTiangongMessageGateway() {
  if (!MESSAGE_GATEWAY_SERVER) return Promise.resolve(tiangongMessageGatewayStatus());
  return new Promise((resolve) => {
    const server = MESSAGE_GATEWAY_SERVER;
    MESSAGE_GATEWAY_SERVER = null;
    MESSAGE_GATEWAY_PORT = 0;
    server.close(() => resolve(tiangongMessageGatewayStatus()));
  });
}

async function connectTiangongMessageChannel(payload = {}) {
  const channelId = normalizeGatewayChannel(payload.channel || payload.id || "desktop");
  if (!TIANGONG_MESSAGE_CHANNELS[channelId]) return { ok: false, error: "unsupported_channel" };
  const channels = { [channelId]: { enabled: true } };
  const status = await startTiangongMessageGateway({ channels });
  const channelStatus = status.channels?.[channelId] || {};
  return {
    ...status,
    ok: Boolean(status.ok),
    channel: channelId,
    connection: {
      channel: channelId,
      label: TIANGONG_MESSAGE_CHANNELS[channelId].label,
      state: channelStatus.state || "",
      qrUrl: channelStatus.qrUrl || "",
      authUrl: channelStatus.authUrl || "",
      endpoint: status.endpoint || "",
      detail: channelStatus.detail || ""
    },
    message: `${TIANGONG_MESSAGE_CHANNELS[channelId].label}已接入天工消息通道核心`
  };
}

async function tiangongMessageGatewayDispatch(payload = {}) {
  const text = String(payload.text || payload.message || payload.content || "").trim();
  if (!text) return { ok: false, error: "empty_message" };
  const config = readMessageGatewayConfig();
  const channel = normalizeGatewayChannel(payload.channel || payload.source || "desktop");
  const conversationId = messageGatewayConversationId(config);
  const recentFromGateway = readGatewayRecentMessages(conversationId, MESSAGE_GATEWAY_HISTORY_LIMIT);
  const recentFromPayload = normalizeFrontendMessages(payload.recentMessages || payload.messages || payload.conversation);
  const recentMessages = mergeRecentMessages(recentFromGateway, recentFromPayload);
  const senderId = String(payload.senderId || payload.sender_id || payload.userId || payload.user_id || channel);
  const requestId = String(payload.requestId || payload.request_id || `msg_${Date.now().toString(36)}_${crypto.randomUUID().slice(0, 8)}`);

  MESSAGE_GATEWAY_STATS.lastInboundAt = Date.now();
  MESSAGE_GATEWAY_STATS.inboundCount += 1;
  MESSAGE_GATEWAY_STATS.activeRuns += 1;
  appendGatewayMessage({
    role: "user",
    channel,
    conversationId,
    senderId,
    content: text,
    at: Date.now(),
    external: {
      externalMessageId: payload.messageId || payload.message_id || "",
      externalConversationId: payload.externalConversationId || payload.external_conversation_id || ""
    }
  });

  try {
    const result = await runBackend(text, {
      ...payload,
      requestId,
      message: text,
      recentMessages,
      mode: payload.mode || "auto",
      selectedSkills: payload.selectedSkills || [],
      selectedSkillNames: payload.selectedSkillNames || [],
      channel,
      messageChannel: channel,
      gatewayConversationId: conversationId
    });
    const reply = backendVisibleReply(result);
    appendGatewayMessage({
      role: "assistant",
      channel,
      conversationId,
      senderId: "tiangong",
      content: reply,
      error: !result?.ok,
      at: Date.now()
    });
    MESSAGE_GATEWAY_STATS.lastFinishAt = Date.now();
    MESSAGE_GATEWAY_STATS.lastError = result?.ok ? "" : String(result?.stderr || result?.error || "backend_failed").slice(0, 1000);
    return {
      ...result,
      ok: Boolean(result?.ok),
      reply,
      messageGateway: {
        channel,
        conversationId,
        endpoint: MESSAGE_GATEWAY_PORT ? `http://${MESSAGE_GATEWAY_HOST}:${MESSAGE_GATEWAY_PORT}` : ""
      }
    };
  } catch (error) {
    MESSAGE_GATEWAY_STATS.lastError = error?.message || String(error);
    appendGatewayMessage({
      role: "assistant",
      channel,
      conversationId,
      senderId: "tiangong",
      content: MESSAGE_GATEWAY_STATS.lastError,
      error: true,
      at: Date.now()
    });
    return { ok: false, error: MESSAGE_GATEWAY_STATS.lastError };
  } finally {
    MESSAGE_GATEWAY_STATS.activeRuns = Math.max(0, MESSAGE_GATEWAY_STATS.activeRuns - 1);
  }
}

function enqueueTiangongMessageGatewayDispatch(payload = {}) {
  MESSAGE_GATEWAY_QUEUE = MESSAGE_GATEWAY_QUEUE
    .catch(() => {})
    .then(() => tiangongMessageGatewayDispatch(payload));
  return MESSAGE_GATEWAY_QUEUE;
}

function messageChannelStatus() {
  return tiangongMessageGatewayStatus();
}

function connectMessageChannel(payload = {}) {
  return connectTiangongMessageChannel(payload);
}

function uniqStrings(values) {
  return [...new Set((Array.isArray(values) ? values : [])
    .map((value) => String(value || "").trim())
    .filter(Boolean))];
}

function abilityStoreDir() {
  return path.join(packageStateRoot(), "artifacts", "nengli");
}

function generatedAssetIndexPath() {
  return path.join(abilityStoreDir(), "indexes", "generated_assets_index.json");
}

function safeMtimeMs(filePath) {
  try {
    return fs.statSync(filePath).mtimeMs;
  } catch {
    return 0;
  }
}

function uniqueExistingPaths(paths) {
  const seen = new Set();
  const result = [];
  for (const rawPath of paths) {
    const text = String(rawPath || "").trim();
    if (!text) continue;
    try {
      const full = path.resolve(text);
      if (!fs.existsSync(full) || seen.has(full.toLowerCase())) continue;
      seen.add(full.toLowerCase());
      result.push(full);
    } catch {
      // Ignore malformed external paths.
    }
  }
  return result;
}

function extractAbilityToolNames(ability) {
  const names = [];
  const fromCreated = Array.isArray(ability.created_from?.tool_packages) ? ability.created_from.tool_packages : [];
  for (const pack of fromCreated) {
    for (const ref of Array.isArray(pack.tool_refs) ? pack.tool_refs : []) {
      if (ref.tool_name) names.push(ref.tool_name);
      if (ref.tool_id) names.push(ref.tool_id);
    }
    for (const step of Array.isArray(pack.fixed_orchestration) ? pack.fixed_orchestration : []) {
      if (step.tool_name) names.push(step.tool_name);
      if (step.tool_id) names.push(step.tool_id);
    }
  }
  return uniqStrings(names);
}

function classifyAbilityCategory(ability, mapItem = {}) {
  const text = [
    ability.ability_id,
    ability.ability_name,
    ability.description,
    ability.metadata?.domain,
    ability.metadata?.capability,
    ...(Array.isArray(ability.task_intents) ? ability.task_intents : []),
    ...(Array.isArray(mapItem.tags) ? mapItem.tags : []),
    ...(Array.isArray(ability.capability_refs) ? ability.capability_refs : []),
    ...(Array.isArray(ability.tool_package_refs) ? ability.tool_package_refs : [])
  ].join(" ").toLowerCase();
  if (/(wps|paiban|bangong|办公套件|全格式)/i.test(text)) return "document";
  if (/(wangwen|novel|小说|写作|文案|case_study|content_calendar|content_topic|conversion_material|landing_page_copy|short_video_script|内容运营|选题|neirong_yunying)/i.test(text)) return "writing_content";
  if (/(image_generate|image_edit|image_inpaint|image_background|image_upscale|image_style|image_variation|image_text_poster|图片制作|海报|封面|产品图|视觉素材|tupian_zhizuo)/i.test(text)) return "image_creation";
  if (/(video_generate|storyboard|shot_plan_generate|video_avatar|voiceover|subtitle_burn|video_render|video_trim|video_concat|video_cut|video_add_|video_resize|video_export|视频制作|视频剪辑|短视频|分镜|口播|shipin_zhizuo|shipin_jianji)/i.test(text)) return "video_creation";
  if (/(tts_generate|audio_clone|bgm_generate|audio_mix|audio_denoise|audio_normalize|audio_export|音频制作|配音|背景音乐|混音|yinpin_zhizuo)/i.test(text)) return "audio_creation";
  if (/(image_inspect|image_ocr|image_layout|image_region|image_compare|image_table|image_chart|image_crop|video_inspect|video_keyframe|video_scene|video_ocr|video_audio|video_subtitle|video_event|audio_transcribe|audio_diarize|audio_summary|audio_keywords|audio_event|media_entity|media_kv|media_topic|media_risk|media_knowledge|图片解析|视频解析|音频解析|多媒体结构化|ocr|关键帧|转写)/i.test(text)) return "media_analysis";
  if (/(browser_|desktop_|terminal|zhongduan|app_spec|app_scaffold|app_preview|app_package|frontend_page|backend_api|db_schema_generate|rpa|自动化|低代码|桌面|浏览器|终端|liulanqi|zhuomian|didaima)/i.test(text)) return "automation_apps";
  if (/(table_|db_|api_|webhook|sql|database|csv|excel|表格|数据库|接口|数据集成|shuju_biaoge|shujuku|jiekou)/i.test(text)) return "data_integration";
  if (/(ops_|market_|icp_profile|buyer_persona|pain_point|competitor|value_proposition|channel_|campaign_|landing_page_audit|event_lead|community_operation|growth_experiment|ab_test|uplift|bandit|experiment_result|growth_retrospective|商业运营|市场洞察|获客|渠道|活动运营|增长实验|zonghe_yunying|shichang|huoke|siyu|zengzhang)/i.test(text)) return "business_ops";
  if (/(sales_|lead_|crm_|deal_|proposal|roi_|pricing|closing|contract_handoff|pipeline|revops|account_score|stakeholder|followup|objection|spin_|销售|线索|客户画像|触达|需求诊断|方案报价|成交|xiaoshou|xiansuo|fangan|revops)/i.test(text)) return "sales_growth";
  if (/(web_search|web_readability|paper_|tech_trend|kb_|knowledge|research|科研|论文|知识库|联网搜索|来源追踪|wangluo|keyan|zhishiku)/i.test(text)) return "knowledge_research";
  if (/(document_|文档|docx|pdf|rewrite|文件学习|wendang)/i.test(text)) return "document";
  if (/(file\.transfer|file\.write|wenjian|文件传输|文件写入|交付包|zip|delivery|chuanshu|xieru)/i.test(text)) return "files_delivery";
  if (/(learning_asset|xuexi_zichan|学习资产|候选包|发布门|资产契约|sandbox_alignment|candidate_sandbox|release_gate|activation_workflow|contract_workflow|tool_production|queue_skill_candidates)/i.test(text)) return "learning_assets";
  if (/(governance|audit|runtime_tool|quality_gate|benchmark|eval_|l6_|system|integration|planner|recovery|shell_system|build_|治理|审计|评测|系统构建|质量门|xitong|zhili|pingce|v1_clean_import)/i.test(text)) return "system_governance";
  if (/(project|scan_project|diagnose_project|代码|项目|compile|pytest|code_x|xiangmu|daima)/i.test(text)) return "project_quality";
  if (/(memory|experience|jiyi|jingyan|记忆|经验)/i.test(text)) return "memory_experience";
  if (/(report|analysis|runtime|诊断|报告|分析)/i.test(text)) return "runtime_reports";
  return "other";
}

function skillAssetSearchRoots(workspace) {
  const roots = [
    workspace,
    readSettings().workspace,
    defaultWorkspace(),
    process.env.TIANGONG_WORKSPACE,
    process.env.LINYUANZHE_WORKSPACE
  ];
  return uniqueExistingPaths(roots);
}

function activeRegistryPaths(workspace) {
  const roots = skillAssetSearchRoots(workspace);
  const paths = [];
  for (const root of roots) {
    paths.push(path.join(root, ".linyuanzhe", "active_assets", "r20", "active_assets_registry.json"));
  }
  for (const stateRoot of [packageStateRoot(), process.env.LINYUANZHE_STATE_DIR, process.env.TIANGONG_STATE_DIR]) {
    if (stateRoot) paths.push(path.join(stateRoot, "active_assets", "r20", "active_assets_registry.json"));
  }
  return uniqueExistingPaths(paths);
}

function candidateManifestPaths(workspace) {
  const roots = skillAssetSearchRoots(workspace);
  const manifests = [];
  const visit = (dir, depth = 0) => {
    if (depth > 8 || !fs.existsSync(dir)) return;
    let entries = [];
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        visit(full, depth + 1);
      } else if (entry.isFile() && entry.name === "manifest.json") {
        manifests.push(full);
      }
    }
  };
  for (const root of roots) {
    visit(path.join(root, ".linyuanzhe", "candidate_sandbox", "r18"));
  }
  visit(path.join(packageStateRoot(), "candidate_sandbox", "r18"));
  return uniqueExistingPaths(manifests);
}

function normalizeGeneratedTags(...values) {
  return uniqStrings(values.flatMap((value) => Array.isArray(value) ? value : [value]).filter(Boolean)).slice(0, 10);
}

function normalizeGeneratedAssetFromActive(record = {}, registryPath = "") {
  const manifestPath = String(record.active_manifest_path || record.active_manifest_relative || "");
  const activeManifest = path.isAbsolute(manifestPath)
    ? readJsonFileSafe(manifestPath, {})
    : {};
  const usageCard = activeManifest.usage_card || record.usage_card || {};
  const assetKind = String(record.asset_kind || activeManifest.asset_kind || "").toLowerCase();
  const toolName = String(record.tool_name || activeManifest.tool_name || "");
  const name = String(activeManifest.name || record.name || toolName || "未命名 learned asset");
  const purpose = String(activeManifest.purpose || record.purpose || usageCard.purpose || usageCard.when_to_use || "");
  const ability = {
    id: String(record.asset_ref || activeManifest.asset_ref || toolName || `${registryPath}:${name}`),
    name: assetKind === "tool" && toolName ? `${name} (${toolName})` : name,
    level: assetKind === "tool" ? "R20 Tool" : "R20 Skill",
    status: "active",
    version: String(activeManifest.version || record.version || ""),
    description: purpose || String(usageCard.when_to_use || "R20 已激活学习资产"),
    runtimeUsable: true,
    requiresConfirmation: false,
    riskLevel: String(activeManifest.descriptor_risk || record.descriptor_risk || "A3"),
    maxDangerLevel: String(activeManifest.descriptor_risk || record.descriptor_risk || "A3"),
    taskIntents: normalizeGeneratedTags(
      usageCard.when_to_use,
      usageCard.title,
      ...(Array.isArray(activeManifest.chain_recipe) ? activeManifest.chain_recipe : [])
    ).slice(0, 12),
    tags: normalizeGeneratedTags("新生成", "已激活", assetKind || "asset"),
    toolPackageRefs: normalizeGeneratedTags(record.source_package_ref, record.source_package_dir, record.active_dir, registryPath).slice(0, 24),
    toolNames: normalizeGeneratedTags(toolName).slice(0, 24),
    updatedAt: Number(activeManifest.activated_at || record.activated_at || 0) || Math.floor(safeMtimeMs(registryPath) / 1000),
    generated: true,
    generatedStage: "active",
    generatedKind: assetKind || "asset",
    metadata: {
      domain: assetKind === "tool" ? "learned_tool" : "learned_skill",
      source: "r20_active_assets"
    }
  };
  ability.category = classifyAbilityCategory({
    ability_id: ability.id,
    ability_name: ability.name,
    description: ability.description,
    task_intents: ability.taskIntents,
    tool_package_refs: ability.toolPackageRefs,
    metadata: ability.metadata
  }, { tags: ability.tags });
  return ability;
}

function normalizeGeneratedAssetFromCandidate(manifestPath) {
  const manifest = readJsonFileSafe(manifestPath, {});
  if (!manifest || !manifest.asset_ref) return null;
  const usageCard = manifest.usage_card || {};
  const assetKind = String(manifest.asset_kind || "").toLowerCase();
  const packageDir = path.dirname(manifestPath);
  const registrationReview = readJsonFileSafe(path.join(packageDir, "registration_review.json"), {});
  const staticScan = readJsonFileSafe(path.join(packageDir, "static_scan.json"), {});
  const smoke = readJsonFileSafe(path.join(packageDir, "smoke_result.json"), {});
  const isReady = String(registrationReview.review_status || "").includes("ready")
    || (staticScan.status === "pass" && smoke.status === "pass");
  const toolName = assetKind === "tool" ? String(manifest.name || "") : "";
  const ability = {
    id: String(manifest.asset_ref || manifest.package_ref || manifestPath),
    name: String(manifest.name || manifest.package_ref || "未命名候选资产"),
    level: assetKind === "tool" ? "R18 Tool" : "R18 Skill",
    status: isReady ? "review_ready" : "candidate",
    version: String(manifest.version || ""),
    description: String(manifest.purpose || usageCard.purpose || usageCard.when_to_use || "R18 候选学习资产"),
    runtimeUsable: false,
    requiresConfirmation: true,
    riskLevel: "候选",
    maxDangerLevel: "",
    taskIntents: normalizeGeneratedTags(
      usageCard.when_to_use,
      usageCard.title,
      manifest.adapter_template_id,
      ...(Array.isArray(manifest.chain_recipe) ? manifest.chain_recipe : [])
    ).slice(0, 12),
    tags: normalizeGeneratedTags("新生成", "候选", assetKind || "asset", manifest.adapter_template_id),
    toolPackageRefs: normalizeGeneratedTags(manifest.package_ref, packageDir, manifestPath).slice(0, 24),
    toolNames: normalizeGeneratedTags(toolName).slice(0, 24),
    updatedAt: Math.floor(safeMtimeMs(manifestPath) / 1000),
    generated: true,
    generatedStage: "candidate",
    generatedKind: assetKind || "asset",
    metadata: {
      domain: assetKind === "tool" ? "candidate_tool" : "candidate_skill",
      source: "r18_candidate_sandbox"
    }
  };
  ability.category = classifyAbilityCategory({
    ability_id: ability.id,
    ability_name: ability.name,
    description: ability.description,
    task_intents: ability.taskIntents,
    tool_package_refs: ability.toolPackageRefs,
    metadata: ability.metadata
  }, { tags: ability.tags });
  return ability;
}

function collectGeneratedSkillAssets(workspace) {
  const abilities = [];
  const sources = [];
  for (const registryPath of activeRegistryPaths(workspace)) {
    const payload = readJsonFileSafe(registryPath, {});
    const records = Array.isArray(payload.records) ? payload.records : [];
    for (const record of records) {
      if (!record || typeof record !== "object") continue;
      if (record.status && record.status !== "active") continue;
      abilities.push(normalizeGeneratedAssetFromActive(record, registryPath));
    }
    sources.push({ kind: "active_registry", path: registryPath, count: records.length });
  }
  for (const manifestPath of candidateManifestPaths(workspace)) {
    const ability = normalizeGeneratedAssetFromCandidate(manifestPath);
    if (ability) {
      abilities.push(ability);
      sources.push({ kind: "candidate_manifest", path: manifestPath, count: 1 });
    }
  }
  const seen = new Set();
  const uniqueAbilities = abilities.filter((ability) => {
    const key = String(ability?.id || ability?.name || "").toLowerCase();
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  return { abilities: uniqueAbilities, sources };
}

function writeGeneratedAssetIndex(payload) {
  const target = generatedAssetIndexPath();
  try {
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, JSON.stringify(payload, null, 2), "utf8");
  } catch (error) {
    writeDebugLog("generated-asset-index-write-failed", error?.message || String(error));
  }
  return target;
}

function backendSkillRoot() {
  return path.join(BACKEND, "tiangong_agent_runtime", "skills");
}

function firstMeaningfulSkillLine(lines) {
  for (const rawLine of lines) {
    const line = String(rawLine || "").trim();
    if (!line || line.startsWith("#") || line.startsWith("- `") || line.startsWith("* `")) continue;
    if (/^[-*]\s*$/.test(line)) continue;
    return line.replace(/^[-*]\s*/, "").trim();
  }
  return "";
}

function extractBackendSkillToolNames(text) {
  const lines = String(text || "").split(/\r?\n/);
  const sectionLines = [];
  let inToolSection = false;
  for (const rawLine of lines) {
    const line = String(rawLine || "");
    const heading = line.trim().match(/^##\s+(.+)$/);
    if (heading) {
      if (inToolSection) break;
      inToolSection = /tools?|工具/i.test(heading[1]);
      continue;
    }
    if (inToolSection) sectionLines.push(line);
  }
  const source = sectionLines.length ? sectionLines.join("\n") : text;
  return uniqStrings(
    Array.from(String(source || "").matchAll(/`([a-z][a-z0-9_]+)`/g)).map((match) => match[1])
  );
}

function parseBackendSkillMarkdown(skillDir) {
  const skillPath = path.join(skillDir, "SKILL.md");
  let text = "";
  try {
    text = fs.readFileSync(skillPath, "utf8");
  } catch {
    return null;
  }
  const lines = text.split(/\r?\n/);
  const titleLine = lines.find((line) => /^#\s+/.test(String(line || "").trim()));
  const title = titleLine ? titleLine.replace(/^#\s+/, "").trim() : path.basename(skillDir);
  const beforeFirstSection = [];
  for (const line of lines.slice(titleLine ? lines.indexOf(titleLine) + 1 : 0)) {
    if (/^##\s+/.test(String(line || "").trim())) break;
    beforeFirstSection.push(line);
  }
  const tools = extractBackendSkillToolNames(text);
  return {
    id: `backend_skill_${path.basename(skillDir).replace(/[^a-zA-Z0-9_]+/g, "_")}`,
    name: title || path.basename(skillDir),
    description: firstMeaningfulSkillLine(beforeFirstSection) || "后端内部技能",
    toolNames: tools,
    skillPath,
    updatedAt: Math.floor(safeMtimeMs(skillPath) / 1000)
  };
}

function normalizeBackendSkillAbility(skill) {
  const taskIntents = uniqStrings([
    skill.name,
    path.basename(path.dirname(skill.skillPath || "")),
    ...skill.toolNames
  ]).slice(0, 12);
  const pseudoAbility = {
    ability_id: skill.id,
    ability_name: skill.name,
    description: skill.description,
    task_intents: taskIntents,
    tool_package_refs: skill.toolNames,
    metadata: { domain: "backend_internal_skill", source: "backend_internal_skills" }
  };
  const tags = uniqStrings(["backend_internal", "skill", ...skill.toolNames]).slice(0, 10);
  return {
    id: skill.id,
    name: skill.name,
    category: classifyAbilityCategory(pseudoAbility, { tags }),
    level: "Backend Skill",
    status: "active",
    version: "",
    description: skill.description,
    runtimeUsable: true,
    requiresConfirmation: false,
    riskLevel: "runtime_governed",
    maxDangerLevel: "",
    taskIntents,
    tags,
    toolPackageRefs: skill.toolNames.slice(0, 24),
    toolNames: skill.toolNames.slice(0, 24),
    updatedAt: skill.updatedAt,
    metadata: pseudoAbility.metadata
  };
}

function backendVirtualSkillAbilities() {
  const virtualSkills = [
    {
      id: "backend_virtual_code_x_repair",
      name: "Code-X 代码系统",
      description: "进入 Code-X 代码系统，处理代码诊断、分析、补丁、验证与交付链路。",
      toolNames: ["scan_project", "read_file", "workspace_text_search", "run_python_quality_check", "write_workspace_file", "file_sha256"],
      updatedAt: Math.floor(Date.now() / 1000)
    }
  ];
  return virtualSkills.map((skill) => ({
    ...normalizeBackendSkillAbility(skill),
    level: "Virtual Skill",
    metadata: {
      domain: "backend_virtual_skill",
      source: "backend_internal_virtual_skill"
    }
  }));
}

function collectBackendInternalSkillAbilities() {
  const root = backendSkillRoot();
  const abilities = [];
  let entries = [];
  try {
    entries = fs.readdirSync(root, { withFileTypes: true });
  } catch (error) {
    return { root, abilities, error: error?.message || String(error) };
  }
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const parsed = parseBackendSkillMarkdown(path.join(root, entry.name));
    if (parsed) abilities.push(normalizeBackendSkillAbility(parsed));
  }
  abilities.sort((left, right) => String(left.id).localeCompare(String(right.id), "zh-Hans-CN"));
  const knownIds = new Set(abilities.map((item) => item.id));
  for (const virtualAbility of backendVirtualSkillAbilities()) {
    if (!knownIds.has(virtualAbility.id)) abilities.push(virtualAbility);
  }
  return { root, abilities, error: "" };
}

function listSkillAssets() {
  const settings = app.isReady() ? readSettings() : {};
  const workspace = String(settings.workspace || defaultWorkspace());
  const storePath = backendSkillRoot();
  const backendSkills = collectBackendInternalSkillAbilities();
  const runtimeCatalog = collectRuntimeToolCatalog();
  const abilities = backendSkills.abilities;
  const abilityToolNames = new Set();
  for (const ability of abilities) {
    for (const toolName of Array.isArray(ability.toolNames) ? ability.toolNames : []) {
      abilityToolNames.add(toolName);
    }
  }
  for (const toolName of Array.isArray(runtimeCatalog.toolNames) ? runtimeCatalog.toolNames : []) {
    abilityToolNames.add(toolName);
  }

  const categoryCounts = {};
  const statusCounts = {};
  const levelCounts = {};
  for (const ability of abilities) {
    categoryCounts[ability.category] = Number(categoryCounts[ability.category] || 0) + 1;
    statusCounts[ability.status || "unknown"] = Number(statusCounts[ability.status || "unknown"] || 0) + 1;
    levelCounts[ability.level || "unknown"] = Number(levelCounts[ability.level || "unknown"] || 0) + 1;
  }

  const categoryToolNames = {};
  for (const ability of abilities) {
    const category = ability.category || "other";
    if (!categoryToolNames[category]) categoryToolNames[category] = new Set();
    for (const toolName of Array.isArray(ability.toolNames) ? ability.toolNames : []) {
      categoryToolNames[category].add(toolName);
    }
  }

  const categories = TOOL_CATEGORY_DEFINITIONS.map((category) => ({
    ...category,
    toolCount: Number(categoryToolNames[category.id]?.size || category.toolCount || 0),
    abilityCount: Number(categoryCounts[category.id] || 0)
  }));

  return {
    ok: !backendSkills.error,
    storePath,
    generatedIndexPath: storePath,
    updatedAt: Math.max(0, ...abilities.map((item) => item.updatedAt || 0)),
    categories,
    abilities,
    error: backendSkills.error || "",
    summary: {
      abilityCount: abilities.length,
      activeCount: abilities.filter((item) => item.status === "active").length,
      runtimeUsableCount: abilities.filter((item) => item.runtimeUsable).length,
      toolCategoryCount: categories.length,
      runtimeToolCount: Number(runtimeCatalog.toolCount || staticRuntimeToolCount()),
      runtimeToolSource: runtimeCatalog.source,
      runtimeToolCatalogError: runtimeCatalog.error || "",
      abilityToolPackageCount: abilityToolNames.size,
      abilityToolNameCount: abilityToolNames.size,
      baseAbilityCount: abilities.length,
      generatedCount: 0,
      generatedActiveCount: 0,
      generatedCandidateCount: 0,
      backendInternalSkillCount: abilities.length,
      backendInternalSkillRoot: backendSkills.root,
      workspace,
      categoryCounts,
      statusCounts,
      levelCounts
    }
  };
}

function runLifecycleDecision(action, payload = {}) {
  return new Promise((resolve) => {
    if (!fs.existsSync(RUN_AGENT)) {
      resolve({ ok: false, error: "找不到后端运行代理" });
      return;
    }
    const settings = readSettings();
    const workspace = String(payload.workspace || settings.workspace || defaultWorkspace());
    const updateId = String(payload.id || payload.ticketId || payload.updateId || "").trim();
    if (!updateId) {
      resolve({ ok: false, error: "缺少待确认自主更新编号" });
      return;
    }
    const py = pythonCommand();
    if (py.missing) {
      resolve({ ok: false, error: py.message || "Python 运行时不可用。" });
      return;
    }
    const flag = action === "deny" ? "--lifecycle-deny" : "--lifecycle-confirm";
    const args = pythonScriptArgs(py, RUN_AGENT, [
      flag,
      updateId,
      "--workspace",
      workspace,
      "--max-steps",
      String(normalizeMaxSteps(settings.maxSteps || 20)),
      ...modelArgsFromSettings(settings)
    ]);
    const child = spawn(py.command, args, {
      cwd: ROOT,
      env: backendPythonEnv(py, {
        ...runtimeStateEnv(),
        ...modelEnvFromSettings(settings),
        TIANGONG_SESSION_SCOPE: sessionScope(settings, workspace)
      }),
      windowsHide: true
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
    });
    child.on("error", (error) => resolve({ ok: false, error: error.message, stderr }));
    child.on("close", (code) => {
      const parsed = parseBridgeJson(stdout, { code, stderr });
      resolve({ ...parsed, ok: Boolean(parsed.ok) && code === 0, code, stderr });
    });
  });
}

function runLearningDelete(payload = {}) {
  return new Promise((resolve) => {
    if (!fs.existsSync(RUN_AGENT)) {
      resolve({ ok: false, error: "找不到后端运行代理" });
      return;
    }
    const settings = readSettings();
    const experienceId = String(payload.id || payload.experienceId || payload.learningId || "").trim();
    if (!experienceId) {
      resolve({ ok: false, error: "缺少经验池条目编号" });
      return;
    }
    const py = pythonCommand();
    if (py.missing) {
      resolve({ ok: false, error: py.message || "Python 运行时不可用。" });
      return;
    }
    const args = pythonScriptArgs(py, RUN_AGENT, [
      "--learning-delete",
      experienceId,
      "--workspace",
      String(payload.workspace || settings.workspace || defaultWorkspace())
    ]);
    const child = spawn(py.command, args, {
      cwd: ROOT,
      env: backendPythonEnv(py, {
        ...runtimeStateEnv(),
        ...modelEnvFromSettings(settings),
        TIANGONG_SESSION_SCOPE: sessionScope(settings, String(payload.workspace || settings.workspace || defaultWorkspace()))
      }),
      windowsHide: true
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
    });
    child.on("error", (error) => resolve({ ok: false, error: error.message, stderr }));
    child.on("close", (code) => {
      const parsed = parseBridgeJson(stdout, { code, stderr });
      resolve({ ...parsed, ok: Boolean(parsed.ok) && code === 0, code, stderr });
    });
  });
}

function runKnowledgeBridge(action, payload = {}) {
  return new Promise((resolve) => {
    if (!fs.existsSync(KNOWLEDGE_BRIDGE)) {
      resolve({ ok: false, error: "找不到知识库桥接脚本" });
      return;
    }
    const settings = readSettings();
    const workspace = String(payload.workspace || settings.workspace || defaultWorkspace());
    const py = pythonCommand();
    if (py.missing) {
      resolve({ ok: false, error: py.message || "Python 运行时不可用。" });
      return;
    }
    const args = pythonScriptArgs(py, KNOWLEDGE_BRIDGE, [
      "--action",
      String(action || "list"),
      "--workspace",
      workspace
    ]);
    const child = spawn(py.command, args, {
      cwd: ROOT,
      env: backendPythonEnv(py, runtimeStateEnv()),
      windowsHide: true
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
    });
    child.on("error", (error) => resolve({ ok: false, error: error.message, stderr }));
    child.on("close", (code) => {
      const parsed = parseBridgeJson(stdout, { code, stderr });
      resolve({ ...parsed, ok: Boolean(parsed.ok) && code === 0, code, stderr });
    });
    child.stdin.end(JSON.stringify({ ...payload, workspace }));
  });
}

async function chooseKnowledgeFiles(payload = {}) {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "选择知识库文件",
    defaultPath: payload.workspace || readSettings().workspace || defaultWorkspace(),
    properties: ["openFile", "multiSelections"],
    filters: [
      { name: "文档", extensions: ["txt", "md", "markdown", "csv", "json", "jsonl", "html", "htm", "xml", "yaml", "yml", "docx", "xlsx", "pptx", "pdf"] },
      { name: "全部文件", extensions: ["*"] }
    ]
  });
  if (result.canceled || !result.filePaths.length) {
    return runKnowledgeBridge("list", payload);
  }
  return runKnowledgeBridge("import", { ...payload, paths: result.filePaths });
}

function publicChatAttachment(filePath, imported = null, failed = null) {
  const target = path.resolve(String(filePath || ""));
  let stat = null;
  try {
    stat = fs.statSync(target);
  } catch {
    stat = null;
  }
  return {
    path: target,
    name: path.basename(target),
    ext: path.extname(target).replace(/^\./, "").toLowerCase(),
    kind: mediaKindFromPath(target),
    size: stat?.size || 0,
    documentId: imported?.document_id || "",
    status: imported ? "imported" : (stat ? "attached" : "failed"),
    summary: imported?.summary || "",
    citationCount: Number(imported?.citation_count || 0),
    error: !stat ? (failed?.error || "文件不存在") : "",
    importError: !imported && failed?.error ? failed.error : ""
  };
}

function pastedFilesDir() {
  const dir = path.join(app.getPath("userData"), "pasted-files");
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

function extensionFromMime(mimeType, fallback = ".bin") {
  const mime = String(mimeType || "").toLowerCase();
  if (mime.includes("png")) return ".png";
  if (mime.includes("jpeg") || mime.includes("jpg")) return ".jpg";
  if (mime.includes("webp")) return ".webp";
  if (mime.includes("gif")) return ".gif";
  if (mime.includes("bmp")) return ".bmp";
  if (mime.includes("tiff") || mime.includes("tif")) return ".tiff";
  if (mime.includes("mp4")) return ".mp4";
  if (mime.includes("webm")) return ".webm";
  if (mime.includes("quicktime")) return ".mov";
  if (mime.includes("x-msvideo") || mime.includes("avi")) return ".avi";
  if (mime.includes("matroska") || mime.includes("mkv")) return ".mkv";
  if (mime.includes("x-ms-wmv") || mime.includes("wmv")) return ".wmv";
  if (mime.includes("mpeg")) return ".mp3";
  if (mime.includes("wav")) return ".wav";
  if (mime.includes("ogg")) return ".ogg";
  if (mime.includes("aac")) return ".aac";
  if (mime.includes("opus")) return ".opus";
  if (mime.includes("wma")) return ".wma";
  if (mime.includes("pdf")) return ".pdf";
  return fallback;
}

function safePastedFileName(name, mimeType) {
  const rawName = path.basename(String(name || "")).replace(/[<>:"/\\|?*\x00-\x1F]/g, "_").slice(0, 120);
  if (rawName && path.extname(rawName)) return rawName;
  const ext = extensionFromMime(mimeType, path.extname(rawName) || ".bin");
  const stem = rawName ? rawName.replace(/\.[^.]*$/, "") : "pasted_file";
  return `${stem}${ext}`;
}

function savePastedDataUrl(item) {
  const dataUrl = String(item?.dataUrl || "");
  const match = dataUrl.match(/^data:([^;,]+)?(?:;[^,]*)?;base64,(.+)$/);
  if (!match) return "";
  const mimeType = String(item?.type || match[1] || "application/octet-stream");
  const raw = Buffer.from(match[2], "base64");
  if (!raw.length) return "";
  const fileName = safePastedFileName(item?.name, mimeType);
  const ext = path.extname(fileName) || extensionFromMime(mimeType);
  const stem = path.basename(fileName, ext).slice(0, 80) || "pasted_file";
  const out = path.join(pastedFilesDir(), `${Date.now()}_${crypto.randomUUID().slice(0, 8)}_${stem}${ext}`);
  fs.writeFileSync(out, raw);
  return out;
}

async function pasteChatFiles(payload = {}) {
  const paths = [];
  for (const rawPath of Array.isArray(payload.paths) ? payload.paths : []) {
    const target = normalizeClipboardTarget(rawPath);
    if (target.filePath) paths.push(target.filePath);
  }
  for (const item of Array.isArray(payload.items) ? payload.items : []) {
    const saved = savePastedDataUrl(item);
    if (saved) paths.push(saved);
  }
  return attachChatFiles(paths, payload);
}

async function attachChatFiles(paths, payload = {}) {
  const settings = readSettings();
  const workspace = String(payload.workspace || settings.workspace || defaultWorkspace());
  const cleanPaths = Array.from(new Set((Array.isArray(paths) ? paths : [])
    .map((filePath) => path.resolve(String(filePath || "")))
    .filter(Boolean)));
  if (!cleanPaths.length) {
    return { ok: true, canceled: true, attachments: [], imported: [], failed: [] };
  }

  if (cleanPaths.length > CHAT_UPLOAD_MAX_FILES) {
    return {
      ok: false,
      canceled: false,
      error: `一次最多上传 ${CHAT_UPLOAD_MAX_FILES} 个文件。`,
      attachments: [],
      imported: [],
      failed: cleanPaths.map((filePath) => ({ path: filePath, error: "超过单次文件数量上限" }))
    };
  }
  const totalBytes = cleanPaths.reduce((sum, filePath) => {
    try {
      return sum + fs.statSync(filePath).size;
    } catch {
      return sum;
    }
  }, 0);
  if (totalBytes > CHAT_UPLOAD_MAX_BYTES) {
    return {
      ok: false,
      canceled: false,
      error: "单次上传文件总体大小不能超过 200MB。",
      attachments: cleanPaths.map((filePath) => ({ ...publicChatAttachment(filePath, null, { error: "超过 200MB 总大小上限" }), status: "failed", error: "超过 200MB 总大小上限" })),
      imported: [],
      failed: cleanPaths.map((filePath) => ({ path: filePath, error: "超过 200MB 总大小上限" }))
    };
  }
  const importPaths = cleanPaths.filter(shouldImportChatAttachment);
  const importResult = importPaths.length
    ? await runKnowledgeBridge("import", { workspace, paths: importPaths, max_chars: 12000 })
    : { ok: true, imported: [], failed: [] };
  const imported = Array.isArray(importResult.imported) ? importResult.imported : [];
  const failed = Array.isArray(importResult.failed) ? importResult.failed : [];
  const importedByPath = new Map();
  for (const item of imported) {
    const filePath = String(item?.file_path || item?.path || "");
    if (filePath) importedByPath.set(path.resolve(filePath).toLowerCase(), item);
  }
  const importedByName = new Map(imported.map((item) => [String(item.file_name || "").toLowerCase(), item]));
  const failedByPath = new Map(failed.map((item) => [path.resolve(String(item.path || "")).toLowerCase(), item]));
  const attachments = cleanPaths.map((filePath) => {
    const cleanPath = path.resolve(filePath);
    return publicChatAttachment(
      cleanPath,
      importedByPath.get(cleanPath.toLowerCase()) || importedByName.get(path.basename(cleanPath).toLowerCase()) || null,
      failedByPath.get(cleanPath.toLowerCase()) || null
    );
  });
  return {
    ...importResult,
    ok: Boolean(importResult.ok) || attachments.some((item) => ["imported", "attached"].includes(item.status)),
    canceled: false,
    attachments
  };
}

async function chooseChatFiles(payload = {}) {
  const settings = readSettings();
  const workspace = String(payload.workspace || settings.workspace || defaultWorkspace());
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "上传到本轮对话",
    defaultPath: workspace,
    properties: ["openFile", "multiSelections"],
    filters: CHAT_FILE_FILTERS
  });
  if (result.canceled || !result.filePaths.length) {
    return { ok: true, canceled: true, attachments: [], imported: [], failed: [] };
  }
  return attachChatFiles(result.filePaths, payload);
}

function createWindow() {
  const initialSettings = readSettings();
  const initialLightTheme = isLightThemeStyle(initialSettings.themeStyle);
  applyNativeThemeStyle(initialSettings.themeStyle);
  mainWindow = new BrowserWindow({
    width: 1380,
    height: 840,
    minWidth: 1040,
    minHeight: 680,
    title: "天工造物 - 临渊者",
    icon: APP_ICON,
    autoHideMenuBar: true,
    backgroundColor: initialLightTheme ? "#F4EFE3" : "#0C0E11",
    darkTheme: !initialLightTheme,
    webPreferences: {
      preload: PRELOAD,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  });
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
  mainWindow.webContents.on("did-finish-load", () => writeDebugLog("did-finish-load", INDEX));
  mainWindow.webContents.on("did-fail-load", (_event, code, desc, url) => writeDebugLog("did-fail-load", `${code} ${desc} ${url || ""}`));
  mainWindow.webContents.on("render-process-gone", (_event, details) => writeDebugLog("render-process-gone", JSON.stringify(details)));
  mainWindow.webContents.on("preload-error", (_event, preloadPath, error) => writeDebugLog("preload-error", `${preloadPath}: ${error.stack || error.message}`));
  mainWindow.webContents.on("console-message", (_event, levelOrDetails, message, line, sourceId) => {
    if (typeof levelOrDetails === "object" && levelOrDetails !== null) {
      writeDebugLog("console-message", JSON.stringify(levelOrDetails));
      return;
    }
    writeDebugLog("console-message", `${levelOrDetails} ${message || ""} ${sourceId || ""}:${line || ""}`);
  });
  mainWindow.setMenuBarVisibility(false);
  mainWindow.loadFile(INDEX);
}

app.whenReady().then(() => {
  Menu.setApplicationMenu(null);
  ipcMain.handle("runtime:getSettings", () => readSettings());
  ipcMain.handle("runtime:setSettings", (_event, next) => writeSettings(next || {}));
  ipcMain.handle("runtime:chooseWorkspace", async () => {
    const current = readSettings();
    const result = await dialog.showOpenDialog(mainWindow, {
      title: "选择工作区",
      defaultPath: current.workspace || defaultWorkspace(),
      properties: ["openDirectory", "createDirectory"]
    });
    if (result.canceled || !result.filePaths[0]) return current;
    return writeSettings({ workspace: result.filePaths[0] });
  });
  ipcMain.handle("runtime:chooseWorkspaceRoot", (_event, payload) => {
    const raw = typeof payload === "string" ? payload : (payload?.root || payload?.drive || "");
    const root = normalizeWorkspaceRoot(raw);
    if (!root) return { ...readSettings(), error: "workspace_root_unavailable" };
    return writeSettings({ workspace: root });
  });
  ipcMain.handle("runtime:choosePersonaAvatar", () => choosePersonaAvatar());
  ipcMain.handle("runtime:chooseUserAvatar", () => chooseUserAvatar());
  ipcMain.handle("runtime:chooseChatFiles", (_event, payload) => chooseChatFiles(payload || {}));
  ipcMain.handle("runtime:pasteChatFiles", (_event, payload) => pasteChatFiles(payload || {}));
  ipcMain.handle("runtime:send", (_event, payload = {}) => enqueueTiangongMessageGatewayDispatch({
    ...payload,
    channel: "desktop",
    source: "desktop",
    text: payload.message || payload.text || ""
  }));
  ipcMain.handle("runtime:cancel", (_event, payload) => cancelBackendRun(payload || {}));
  ipcMain.handle("runtime:guide", (_event, payload) => appendRuntimeGuidance(payload || {}));
  ipcMain.handle("learning:runNow", () => runAutonomousLearningTick("manual"));
  ipcMain.handle("runtime:status", () => runStatus());
  ipcMain.handle("runtime:config", () => runConfig());
  ipcMain.handle("messageChannel:status", () => messageChannelStatus());
  ipcMain.handle("messageChannel:connect", (_event, payload) => connectMessageChannel(payload || {}));
  ipcMain.handle("runtime:listDailyLogs", () => listDailyBackendLogs());
  ipcMain.handle("runtime:openDailyLog", (_event, payload) => openDailyBackendLog(payload || {}));
  ipcMain.handle("runtime:deleteDailyLog", (_event, payload) => deleteDailyBackendLog(payload || {}));
  ipcMain.handle("skills:list", () => listSkillAssets());
  ipcMain.handle("lifecycle:confirm", (_event, payload) => runLifecycleDecision("confirm", payload || {}));
  ipcMain.handle("lifecycle:deny", (_event, payload) => runLifecycleDecision("deny", payload || {}));
  ipcMain.handle("learning:delete", (_event, payload) => runLearningDelete(payload || {}));
  ipcMain.handle("knowledge:list", (_event, payload) => runKnowledgeBridge("list", payload || {}));
  ipcMain.handle("knowledge:chooseFiles", (_event, payload) => chooseKnowledgeFiles(payload || {}));
  ipcMain.handle("knowledge:query", (_event, payload) => runKnowledgeBridge("query", payload || {}));
  ipcMain.handle("knowledge:export", (_event, payload) => runKnowledgeBridge("export", payload || {}));
  ipcMain.handle("knowledge:remove", (_event, payload) => runKnowledgeBridge("remove", payload || {}));
  ipcMain.handle("runtime:openPath", async (_event, targetPath) => {
    const target = normalizeClipboardTarget(targetPath || ROOT);
    const code = await shell.openPath(target.filePath || target.raw || ROOT);
    return { ok: !code, code };
  });
  ipcMain.handle("runtime:copyMedia", (_event, payload) => copyMediaToClipboard(payload || {}));
  createWindow();
  startLearningScheduler();
  startMemoryHeartbeatScheduler();
}).catch((error) => console.error(error));

app.on("before-quit", () => {
  if (LEARNING_SCHEDULER_TIMER) clearInterval(LEARNING_SCHEDULER_TIMER);
  if (LEARNING_SCHEDULER_STARTUP_TIMER) clearTimeout(LEARNING_SCHEDULER_STARTUP_TIMER);
  if (MEMORY_HEARTBEAT_TIMER) clearInterval(MEMORY_HEARTBEAT_TIMER);
  if (MEMORY_HEARTBEAT_STARTUP_TIMER) clearTimeout(MEMORY_HEARTBEAT_STARTUP_TIMER);
  if (MESSAGE_GATEWAY_SERVER) {
    MESSAGE_GATEWAY_SERVER.close();
    MESSAGE_GATEWAY_SERVER = null;
    MESSAGE_GATEWAY_PORT = 0;
  }
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
