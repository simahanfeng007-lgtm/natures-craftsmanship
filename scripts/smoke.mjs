import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: root,
    encoding: "utf8",
    windowsHide: true,
    timeout: options.timeout ?? 30000,
    env: {
      ...process.env,
      PYTHONUTF8: "1",
      PYTHONIOENCODING: "utf-8:replace",
      PYTHONDONTWRITEBYTECODE: "1",
      ...(options.env || {})
    }
  });
  if (result.status !== 0) {
    console.error(`[smoke] failed: ${command} ${args.join(" ")}`);
    console.error(result.stderr || result.stdout);
    process.exit(result.status || 1);
  }
  return result.stdout;
}

run("node", ["--check", "main.js"]);
run("node", ["--check", "src/renderer/app.mjs"]);

const python = path.join(root, "resources", "backend_runtime", "python", process.platform === "win32" ? "python.exe" : "bin/python3");
const runAgent = path.join(root, "resources", "backend", "run_agent.py");
run(python, [runAgent, "--once", "你好，简单回复一句", "--workspace", root, "--max-steps", "1"], {
  env: { TIANGONG_SOUL_BASELINE_PERSIST: "0" }
});

const html = readFileSync(path.join(root, "src", "index.html"), "utf8");
for (const rel of ["./assets/tiangong-logo.png", "./styles.css", "./renderer/app.mjs"]) {
  const target = path.resolve(root, "src", rel);
  if (!html.includes(rel) || !existsSync(target)) {
    console.error(`[smoke] missing frontend asset reference: ${rel}`);
    process.exit(1);
  }
}

console.log("[smoke] OK");
