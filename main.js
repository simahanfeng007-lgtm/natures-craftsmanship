const { app, BrowserWindow, dialog, ipcMain, shell, Menu } = require("electron");
const path = require("path");
const fs = require("fs");
const os = require("os");
const crypto = require("crypto");
const { spawn, spawnSync } = require("child_process");

const APP_DIR = __dirname;
const DEV_ROOT = path.resolve(APP_DIR, "..", "..");
const RESOURCE_ROOT = app.isPackaged ? process.resourcesPath : DEV_ROOT;
const BACKEND = app.isPackaged ? path.join(RESOURCE_ROOT, "backend") : DEV_ROOT;
const RUNTIME_ROOT = app.isPackaged
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
const CHAT_UPLOAD_MAX_FILES = 5;
const CHAT_UPLOAD_MAX_BYTES = 200 * 1024 * 1024;
const FRONTEND_HISTORY_MAX_MESSAGES = 20;
const FRONTEND_HISTORY_MAX_CONTENT = 4000;
const TOOL_CATEGORY_DEFINITIONS = [
  {
    id: "runtime_reports",
    label: "运行报告",
    description: "状态、诊断、分析、只读报告与结果回传。",
    toolCount: 19,
    sampleTools: ["return_analysis", "return_code", "diagnose_project"]
  },
  {
    id: "learning_assets",
    label: "学习资产",
    description: "经验合成、技能候选、工具候选、学习资产化。",
    toolCount: 25,
    sampleTools: ["learning_asset_*", "experience_synthesis", "tool_skill_candidate"]
  },
  {
    id: "document",
    label: "文档处理",
    description: "文档解析、追问、改写、导出与回滚。",
    toolCount: 7,
    sampleTools: ["document_parse", "document_query", "document_export"]
  },
  {
    id: "files_delivery",
    label: "文件交付",
    description: "文件读写、校验、压缩包与交付物生成。",
    toolCount: 6,
    sampleTools: ["list_dir", "read_file", "write_workspace_file", "create_zip_package"]
  },
  {
    id: "project_quality",
    label: "编程功能",
    description: "项目扫描、质量检查、诊断与交付校验。",
    toolCount: 4,
    sampleTools: ["scan_project", "run_python_quality_check", "diagnose_project"]
  },
  {
    id: "model_or_external",
    label: "模型/外部",
    description: "模型调用、联网搜索、外部资料检索。",
    toolCount: 5,
    sampleTools: ["web_search", "model_request", "workspace_text_search"]
  },
  {
    id: "memory_experience",
    label: "记忆经验",
    description: "记忆召回、经验查询、上下文经验沉淀。",
    toolCount: 3,
    sampleTools: ["memory_recall", "experience_query", "memory_experience"]
  },
  {
    id: "other",
    label: "其他能力",
    description: "未归入固定家族的运行时工具和兼容能力。",
    toolCount: 8,
    sampleTools: ["runtime_misc", "compat_adapter"]
  }
];

let mainWindow = null;
let cachedPythonSitePaths = null;
let cachedBundledPythonHealth = null;

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

function readSettings() {
  const fallback = {
    workspace: defaultWorkspace(),
    maxSteps: 20,
    mode: "auto",
    personaName: "临渊者",
    soulPrompt: "",
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
    lifecycleFreeWillFrequency: "manual",
    lifecycleLearningScope: "workspace"
  };
  try {
    const raw = fs.readFileSync(userDataFile(), "utf8");
    return { ...fallback, ...JSON.parse(raw) };
  } catch {
    return fallback;
  }
}

function writeSettings(next) {
  const current = readSettings();
  const merged = { ...current, ...next };
  fs.mkdirSync(path.dirname(userDataFile()), { recursive: true });
  fs.writeFileSync(userDataFile(), JSON.stringify(merged, null, 2), "utf8");
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
  if (mode === "chat" || mode === "work") return mode;
  return "auto";
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
      if (status && status !== "imported") continue;
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

function normalizeFrontendMessages(value) {
  const items = Array.isArray(value) ? value : [];
  return items
    .filter((item) => item?.role === "user" || item?.role === "assistant")
    .map((item) => ({
      role: item.role,
      content: String(item.content || "").slice(0, FRONTEND_HISTORY_MAX_CONTENT),
      error: Boolean(item.error),
      at: Number(item.at || 0)
    }))
    .filter((item) => item.content)
    .slice(-FRONTEND_HISTORY_MAX_MESSAGES);
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

function consumeStreamEventLine(line, requestId) {
  const text = String(line || "").trim();
  if (!text.startsWith(STREAM_EVENT_PREFIX)) return false;
  try {
    const payload = JSON.parse(text.slice(STREAM_EVENT_PREFIX.length).trim());
    emitRunStep(requestId, payload);
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
  return true;
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
    const mode = normalizeFrontendMode(options.mode || settings.mode || "auto");
    const personaName = String(options.personaName || settings.personaName || "临渊者").slice(0, 32);
    const soulPrompt = String(options.soulPrompt || settings.soulPrompt || "").slice(0, 6000);
    const chatAttachments = normalizeChatAttachments(options.attachments);
    const frontendMessages = normalizeFrontendMessages(options.recentMessages || options.messages || options.conversation);
    writeBackendRunLog("start", {
      requestId,
      workspace,
      mode,
      maxSteps,
      provider: settings.modelProvider || settings.modelService || "",
      model: settings.modelName || "",
      attachmentCount: chatAttachments.length,
      conversationCount: frontendMessages.length,
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
      ...modelEnvFromSettings(settings),
      TIANGONG_SESSION_SCOPE: sessionScope(settings, workspace),
      TIANGONG_SOUL_NAME: personaName,
      LINYUANZHE_PERSONA_NAME: personaName,
      TIANGONG_SOUL_PROMPT: soulPrompt,
      LINYUANZHE_PERSONA_PROMPT: soulPrompt,
      LINYUANZHE_FRONTEND_WORK_MODE: mode,
      TIANGONG_TASK_MODE: mode === "work" ? "work_task" : "ordinary_chat",
      LINYUANZHE_TOOLS_REQUESTED: mode === "chat" ? "0" : "1",
      LINYUANZHE_PLANNER_ALLOWED: mode === "chat" ? "0" : "1",
      TIANGONG_UPLOAD_FILES_JSON: JSON.stringify(chatAttachments),
      TIANGONG_FRONTEND_MESSAGES_JSON: JSON.stringify(frontendMessages),
      TIANGONG_STREAM_EVENTS: "1",
      TIANGONG_REQUEST_ID: requestId
    });
    delete env.PYTHONSTARTUP;

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

    function consumeStreamChunk(text) {
      streamLineBuffer += String(text || "");
      const lines = streamLineBuffer.split(/\r?\n/);
      streamLineBuffer = lines.pop() || "";
      for (const line of lines) consumeStreamEventLine(line, requestId);
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

    function finish(result) {
      if (settled) return;
      settled = true;
      clearTimeout(hardTimer);
      clearInterval(afterOutputTimer);
      writeBackendRunLog("finish", {
        requestId,
        ok: Boolean(result.ok),
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
    child.on("error", (error) => resolve({ ok: false, stdout, stderr: error.message }));
    child.on("close", (code) => resolve({ ok: code === 0, code, stdout, stderr }));
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

function uniqStrings(values) {
  return [...new Set((Array.isArray(values) ? values : [])
    .map((value) => String(value || "").trim())
    .filter(Boolean))];
}

function abilityStoreDir() {
  return path.join(os.homedir(), ".tiangong", "artifacts", "nengli");
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
  if (/(learning|xuexi|学习|技能|工具候选|asset|candidate|jingpin|精品|practice|实践)/i.test(text)) return "learning_assets";
  if (/(document|文档|docx|pdf|rewrite|export|query|知识库|文件学习)/i.test(text)) return "document";
  if (/(file\.transfer|file\.write|wenjian|文件传输|文件写入|交付包|zip|delivery|chuanshu|xieru)/i.test(text)) return "files_delivery";
  if (/(project|quality|scan|diagnose|代码|项目|质量|compile|pytest)/i.test(text)) return "project_quality";
  if (/(web|search|external|model|搜索|联网|外部|cli|软件)/i.test(text)) return "model_or_external";
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
  for (const stateRoot of [process.env.LINYUANZHE_STATE_DIR, process.env.TIANGONG_STATE_DIR]) {
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
  const tools = uniqStrings(
    Array.from(text.matchAll(/`([a-z][a-z0-9_]+)`/g)).map((match) => match[1])
  );
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
  const abilities = backendSkills.abilities;
  const abilityToolNames = new Set();
  for (const ability of abilities) {
    for (const toolName of Array.isArray(ability.toolNames) ? ability.toolNames : []) {
      abilityToolNames.add(toolName);
    }
  }

  const categoryCounts = {};
  const statusCounts = {};
  const levelCounts = {};
  for (const ability of abilities) {
    categoryCounts[ability.category] = Number(categoryCounts[ability.category] || 0) + 1;
    statusCounts[ability.status || "unknown"] = Number(statusCounts[ability.status || "unknown"] || 0) + 1;
    levelCounts[ability.level || "unknown"] = Number(levelCounts[ability.level || "unknown"] || 0) + 1;
  }

  const categories = TOOL_CATEGORY_DEFINITIONS.map((category) => ({
    ...category,
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
      runtimeToolCount: categories.reduce((sum, item) => sum + Number(item.toolCount || 0), 0),
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
      env: backendPythonEnv(py),
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
    size: stat?.size || 0,
    documentId: imported?.document_id || "",
    status: failed ? "failed" : (imported ? "imported" : "selected"),
    summary: imported?.summary || "",
    citationCount: Number(imported?.citation_count || 0),
    error: failed?.error || ""
  };
}

async function chooseChatFiles(payload = {}) {
  const settings = readSettings();
  const workspace = String(payload.workspace || settings.workspace || defaultWorkspace());
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "上传到本轮对话",
    defaultPath: workspace,
    properties: ["openFile", "multiSelections"],
    filters: [
      { name: "文档与文本", extensions: ["txt", "md", "markdown", "csv", "json", "jsonl", "html", "htm", "xml", "yaml", "yml", "docx", "xlsx", "pptx", "pdf", "py", "js", "mjs", "ts", "tsx", "jsx", "css", "log"] },
      { name: "全部文件", extensions: ["*"] }
    ]
  });
  if (result.canceled || !result.filePaths.length) {
    return { ok: true, canceled: true, attachments: [], imported: [], failed: [] };
  }

  if (result.filePaths.length > CHAT_UPLOAD_MAX_FILES) {
    return {
      ok: false,
      canceled: false,
      error: `一次最多上传 ${CHAT_UPLOAD_MAX_FILES} 个文件。`,
      attachments: [],
      imported: [],
      failed: result.filePaths.map((filePath) => ({ path: filePath, error: "超过单次文件数量上限" }))
    };
  }
  const paths = result.filePaths;
  const totalBytes = paths.reduce((sum, filePath) => {
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
      attachments: paths.map((filePath) => publicChatAttachment(filePath, null, { error: "超过 200MB 总大小上限" })),
      imported: [],
      failed: paths.map((filePath) => ({ path: filePath, error: "超过 200MB 总大小上限" }))
    };
  }
  const importResult = await runKnowledgeBridge("import", { workspace, paths, max_chars: 12000 });
  const imported = Array.isArray(importResult.imported) ? importResult.imported : [];
  const failed = Array.isArray(importResult.failed) ? importResult.failed : [];
  const importedByPath = new Map();
  for (const item of imported) {
    const filePath = String(item?.file_path || item?.path || "");
    if (filePath) importedByPath.set(path.resolve(filePath).toLowerCase(), item);
  }
  const importedByName = new Map(imported.map((item) => [String(item.file_name || "").toLowerCase(), item]));
  const failedByPath = new Map(failed.map((item) => [path.resolve(String(item.path || "")).toLowerCase(), item]));
  const attachments = paths.map((filePath) => {
    const cleanPath = path.resolve(filePath);
    return publicChatAttachment(
      cleanPath,
      importedByPath.get(cleanPath.toLowerCase()) || importedByName.get(path.basename(cleanPath).toLowerCase()) || null,
      failedByPath.get(cleanPath.toLowerCase()) || null
    );
  });
  return {
    ...importResult,
    ok: Boolean(importResult.ok) || attachments.some((item) => item.status === "imported"),
    canceled: false,
    attachments
  };
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1380,
    height: 840,
    minWidth: 1040,
    minHeight: 680,
    title: "天工造物 - 临渊者",
    icon: APP_ICON,
    autoHideMenuBar: true,
    backgroundColor: "#0C0E11",
    darkTheme: true,
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
  ipcMain.handle("runtime:choosePersonaAvatar", () => choosePersonaAvatar());
  ipcMain.handle("runtime:chooseUserAvatar", () => chooseUserAvatar());
  ipcMain.handle("runtime:chooseChatFiles", (_event, payload) => chooseChatFiles(payload || {}));
  ipcMain.handle("runtime:send", (_event, payload) => runBackend(payload.message, payload));
  ipcMain.handle("runtime:status", () => runStatus());
  ipcMain.handle("runtime:config", () => runConfig());
  ipcMain.handle("runtime:listDailyLogs", () => listDailyBackendLogs());
  ipcMain.handle("runtime:openDailyLog", (_event, payload) => openDailyBackendLog(payload || {}));
  ipcMain.handle("runtime:deleteDailyLog", (_event, payload) => deleteDailyBackendLog(payload || {}));
  ipcMain.handle("skills:list", () => listSkillAssets());
  ipcMain.handle("lifecycle:confirm", (_event, payload) => runLifecycleDecision("confirm", payload || {}));
  ipcMain.handle("lifecycle:deny", (_event, payload) => runLifecycleDecision("deny", payload || {}));
  ipcMain.handle("knowledge:list", (_event, payload) => runKnowledgeBridge("list", payload || {}));
  ipcMain.handle("knowledge:chooseFiles", (_event, payload) => chooseKnowledgeFiles(payload || {}));
  ipcMain.handle("knowledge:query", (_event, payload) => runKnowledgeBridge("query", payload || {}));
  ipcMain.handle("knowledge:export", (_event, payload) => runKnowledgeBridge("export", payload || {}));
  ipcMain.handle("knowledge:remove", (_event, payload) => runKnowledgeBridge("remove", payload || {}));
  ipcMain.handle("runtime:openPath", async (_event, targetPath) => {
    const code = await shell.openPath(String(targetPath || ROOT));
    return { ok: !code, code };
  });
  createWindow();
}).catch((error) => console.error(error));

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
