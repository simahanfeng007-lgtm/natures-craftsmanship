import { existsSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dist = path.join(root, "dist");

if (!existsSync(dist)) {
  console.error(`[verify-dist] dist directory not found: ${dist}`);
  process.exit(1);
}

const files = readdirSync(dist, { withFileTypes: true })
  .filter((entry) => entry.isFile())
  .map((entry) => {
    const fullPath = path.join(dist, entry.name);
    return { name: entry.name, size: statSync(fullPath).size, fullPath };
  });

const setup = files.find((item) => /setup/i.test(item.name) && /\.exe$/i.test(item.name));
const portable = files.find((item) => /portable/i.test(item.name) && /\.exe$/i.test(item.name));
const blocklist = files.filter((item) => /\.(log|tmp|bak|map)$/i.test(item.name));
const unpacked = path.join(dist, "win-unpacked");
const requiredUnpackedEntries = [
  "resources/app.asar",
  "resources/backend/run_agent.py",
  "resources/backend/tiangong_agent_shell/cli_main.py",
  "resources/backend/tiangong_agent_runtime/runtime_entry.py",
  "resources/backend_runtime/runtime_manifest.json",
  "resources/backend_runtime/python/python.exe",
  "resources/backend_runtime/python/python312.zip",
  "resources/backend_runtime/python/python312._pth"
];

function walk(dir, prefix = "") {
  const rows = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      rows.push({ rel, full, isDirectory: true });
      rows.push(...walk(full, rel));
    } else {
      rows.push({ rel, full, isDirectory: false, size: statSync(full).size });
    }
  }
  return rows;
}

function isForbiddenUnpackedEntry(item) {
  const rel = item.rel.replaceAll("\\", "/");
  const lower = rel.toLowerCase();
  if (lower === "resources/backend_runtime/python/python312.zip") return false;
  if (item.isDirectory && /(^|\/)(__pycache__|\.pytest_cache|\.mypy_cache|\.ruff_cache)(\/|$)/i.test(rel)) return true;
  return /\.(pyc|pyo|log|tmp|bak)$/i.test(rel) || (lower.endsWith(".zip") && lower !== "resources/backend_runtime/python/python312.zip");
}

if (!setup || setup.size < 50 * 1024 * 1024) {
  console.error("[verify-dist] setup installer missing or unexpectedly small");
  process.exit(1);
}
if (!portable || portable.size < 50 * 1024 * 1024) {
  console.error("[verify-dist] portable executable missing or unexpectedly small");
  process.exit(1);
}
if (blocklist.length) {
  console.error("[verify-dist] forbidden loose artifacts:");
  for (const item of blocklist) console.error(` - ${item.name}`);
  process.exit(1);
}
if (!existsSync(unpacked)) {
  console.error(`[verify-dist] unpacked app missing: ${unpacked}`);
  process.exit(1);
}
const missingUnpacked = requiredUnpackedEntries.filter((item) => !existsSync(path.join(unpacked, item)));
if (missingUnpacked.length) {
  console.error("[verify-dist] required unpacked entries missing:");
  for (const item of missingUnpacked) console.error(` - ${item}`);
  process.exit(1);
}
const dirtyUnpacked = walk(unpacked).filter(isForbiddenUnpackedEntry);
if (dirtyUnpacked.length) {
  console.error("[verify-dist] forbidden unpacked artifacts:");
  for (const item of dirtyUnpacked.slice(0, 80)) console.error(` - ${item.rel}`);
  if (dirtyUnpacked.length > 80) console.error(` ... ${dirtyUnpacked.length - 80} more`);
  process.exit(1);
}

console.log("[verify-dist] Windows artifacts OK");
for (const item of files) {
  console.log(`${item.name}\t${item.size}`);
}
console.log(`[verify-dist] unpacked entries OK (${walk(unpacked).length})`);
