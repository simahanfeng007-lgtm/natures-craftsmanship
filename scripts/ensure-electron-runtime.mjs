import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const isWindows = process.platform === "win32";
const python = path.join(root, "resources", "backend_runtime", "python", isWindows ? "python.exe" : "bin/python3");
const runAgent = path.join(root, "resources", "backend", "run_agent.py");

const required = [
  "main.js",
  "preload.js",
  "package.json",
  "src/index.html",
  "src/assets/tiangong-logo.png",
  "build/icon.ico",
  "resources/backend/run_agent.py",
  "resources/backend_runtime/runtime_manifest.json",
  "resources/backend_runtime/python/python312.zip",
  "resources/backend_runtime/python/python312._pth"
];

const missing = required.filter((item) => !existsSync(path.join(root, item)));
if (missing.length) {
  console.error("[ensure] missing required packaging files:");
  for (const item of missing) console.error(` - ${item}`);
  process.exit(1);
}

if (!existsSync(python)) {
  console.error(`[ensure] bundled Python not found: ${python}`);
  process.exit(1);
}

const env = {
  ...process.env,
  PYTHONUTF8: "1",
  PYTHONIOENCODING: "utf-8:replace",
  PYTHONNOUSERSITE: "1",
  PYTHONDONTWRITEBYTECODE: "1"
};

const importCheck = spawnSync(
  python,
  ["-c", "import encodings, ssl, sqlite3, openai, pytest; print('python_runtime_ok')"],
  { cwd: root, encoding: "utf8", env, windowsHide: true, timeout: 20000 }
);
if (importCheck.status !== 0) {
  console.error("[ensure] bundled Python import check failed");
  console.error(importCheck.stderr || importCheck.stdout);
  process.exit(importCheck.status || 1);
}

const status = spawnSync(
  python,
  [runAgent, "--status", "--workspace", root, "--max-steps", "1"],
  { cwd: root, encoding: "utf8", env: { ...env, TIANGONG_SOUL_BASELINE_PERSIST: "0" }, windowsHide: true, timeout: 30000 }
);
if (status.status !== 0 || !String(status.stdout || "").includes("health_state=ok")) {
  console.error("[ensure] backend status check failed");
  console.error(status.stderr || status.stdout);
  process.exit(status.status || 1);
}

console.log("[ensure] packaging prerequisites OK");
