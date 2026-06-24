from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Any

from .common import command_exists, json_output, read_package_json, run_command, safe_rel, workspace_root


def _version(argv: list[str], cwd: Path) -> dict[str, Any]:
    exe = command_exists(argv[0])
    if not exe:
        return {"available": False, "path": "", "version": ""}
    ok, output, code = run_command([exe, *argv[1:]], cwd=cwd, timeout=10)
    return {"available": ok, "path": exe, "version": output.splitlines()[0] if output else "", "exit_code": code}


def _node_probe(script: str, cwd: Path) -> dict[str, Any]:
    node = command_exists("node")
    if not node:
        return {"available": False, "error": "node not found"}
    ok, output, code = run_command([node, "-e", script], cwd=cwd, timeout=20)
    return {"available": ok, "output": output, "exit_code": code}


def run(workspace: str | Path, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    root = workspace_root(workspace)
    package_path, package = read_package_json(root)
    scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}

    playwright_resolve = _node_probe(
        "try{console.log(require.resolve('playwright'))}catch(e){process.exitCode=2;console.error(e.message)}",
        root,
    )
    playwright_browsers = {"available": False, "output": "playwright module unavailable"}
    if playwright_resolve.get("available"):
        playwright_browsers = _node_probe(
            "const {chromium}=require('playwright');"
            "(async()=>{const b=await chromium.launch({headless:true});"
            "console.log(await b.version()); await b.close();})().catch(e=>{process.exitCode=3; console.error(e.message)})",
            root,
        )

    payload = {
        "schema": "tiangong.codex.dependency_probe.v1",
        "system": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "cwd": str(root),
        },
        "executables": {
            "node": _version(["node", "--version"], root),
            "npm": _version(["npm", "--version"], root),
            "git": _version(["git", "--version"], root),
            "python": {"available": True, "path": sys.executable, "version": sys.version.split()[0]},
        },
        "package": {
            "path": safe_rel(package_path, root) if package_path else "",
            "name": package.get("name") if isinstance(package, dict) else "",
            "scripts": sorted(scripts.keys()) if isinstance(scripts, dict) else [],
            "parse_error": package.get("_parse_error") if isinstance(package, dict) else "",
        },
        "playwright": {
            "module": playwright_resolve,
            "chromium_launch": playwright_browsers,
            "ready": bool(playwright_resolve.get("available") and playwright_browsers.get("available")),
            "install_hint": "npm install -D playwright && npx playwright install chromium",
        },
    }
    if args.get("json") is False:
        return payload
    return payload


def run_text(workspace: str | Path, args: dict[str, Any] | None = None) -> str:
    return json_output(run(workspace, args))

