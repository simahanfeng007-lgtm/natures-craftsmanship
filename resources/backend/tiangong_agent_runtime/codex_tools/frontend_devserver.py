from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from .common import (
    artifact_dir,
    bounded_int,
    coerce_bool,
    command_danger_reason,
    command_exists,
    find_free_port,
    json_output,
    read_package_json,
    safe_rel,
    script_command,
    wait_for_url,
    workspace_root,
)


SERVER_SCHEMA = "tiangong.codex.frontend_devserver.v1"
SAFE_SCRIPT_NAMES = ("dev", "start", "serve", "preview")


def _registry_path(workspace: Path) -> Path:
    return artifact_dir(workspace, "devservers") / "registry.json"


def _read_registry(workspace: Path) -> dict[str, Any]:
    path = _registry_path(workspace)
    if not path.exists():
        return {"servers": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("servers"), list):
            return data
    except Exception:
        pass
    return {"servers": []}


def _write_registry(workspace: Path, data: dict[str, Any]) -> None:
    path = _registry_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True, timeout=5)
            return str(pid) in result.stdout
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _stop_pid(pid: int) -> tuple[bool, str]:
    if not _pid_running(pid):
        return True, "already stopped"
    try:
        if os.name == "nt":
            result = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, timeout=15)
            output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part and part.strip())
            return result.returncode == 0, output
        os.kill(pid, signal.SIGTERM)
        return True, "sent SIGTERM"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _candidate_scripts(package: dict[str, Any]) -> list[dict[str, str]]:
    scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
    rows: list[dict[str, str]] = []
    for name in SAFE_SCRIPT_NAMES:
        command = str(scripts.get(name) or "")
        if command:
            rows.append({"name": name, "command": command, "danger": command_danger_reason(command)})
    return rows


def _status_payload(workspace: Path) -> dict[str, Any]:
    registry = _read_registry(workspace)
    rows = []
    for item in registry.get("servers", []):
        pid = int(item.get("pid") or 0)
        rows.append({**item, "running": _pid_running(pid)})
    return {"schema": SERVER_SCHEMA, "action": "status", "servers": rows}


def run(workspace: str | Path, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    root = workspace_root(workspace)
    action = str(args.get("action") or "plan").strip().lower()
    package_path, package = read_package_json(root)
    npm = command_exists("npm")

    if action == "status":
        return _status_payload(root)

    if action == "stop":
        registry = _read_registry(root)
        target_pid = int(args.get("pid") or 0)
        stopped = []
        kept = []
        for item in registry.get("servers", []):
            pid = int(item.get("pid") or 0)
            if target_pid and pid != target_pid:
                kept.append(item)
                continue
            ok, output = _stop_pid(pid)
            stopped.append({"pid": pid, "ok": ok, "output": output[:1000]})
        registry["servers"] = kept if target_pid else []
        _write_registry(root, registry)
        return {"schema": SERVER_SCHEMA, "action": "stop", "stopped": stopped}

    candidates = _candidate_scripts(package if isinstance(package, dict) else {})
    base = {
        "schema": SERVER_SCHEMA,
        "action": action,
        "package_path": safe_rel(package_path, root) if package_path else "",
        "npm_available": bool(npm),
        "candidate_scripts": candidates,
    }

    if action == "plan":
        return base

    url = str(args.get("url") or "").strip()
    if action == "probe":
        if not url:
            return {**base, "ok": False, "error": "[BAD_ARGS] probe requires url"}
        timeout = bounded_int(args.get("timeout"), 15, 1, 120)
        ok, detail = wait_for_url(url, timeout=timeout)
        return {**base, "ok": ok, "url": url, "detail": detail}

    if action != "start":
        return {**base, "ok": False, "error": f"[BAD_ARGS] unknown action={action}"}

    if not npm:
        return {**base, "ok": False, "error": "[NPM_NOT_FOUND] npm executable not found"}
    script = str(args.get("script") or "").strip()
    if not script:
        script = candidates[0]["name"] if candidates else ""
    command = script_command(package if isinstance(package, dict) else {}, script)
    if not command:
        return {**base, "ok": False, "error": f"[SCRIPT_NOT_FOUND] package script not found: {script}"}
    danger = command_danger_reason(command)
    if danger:
        return {**base, "ok": False, "error": f"[A5_BLOCKED] {danger}"}

    port = bounded_int(args.get("port"), 0, 0, 65535) or find_free_port()
    host = str(args.get("host") or "127.0.0.1")
    url = url or f"http://{host}:{port}"
    timeout = bounded_int(args.get("timeout"), 30, 1, 180)
    server_dir = artifact_dir(root, "devservers")
    stamp = str(int(time.time()))
    stdout_path = server_dir / f"{script}-{port}-{stamp}.log"
    env = {**os.environ, "PORT": str(port), "HOST": host, "BROWSER": "none"}
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    stdout_file = stdout_path.open("w", encoding="utf-8", errors="replace")
    process = subprocess.Popen(
        [npm, "run", "-s", script, "--", "--host", host, "--port", str(port)],
        cwd=str(package_path.parent if package_path else root),
        stdout=stdout_file,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        env=env,
        creationflags=creationflags,
    )
    stdout_file.close()
    ok, detail = wait_for_url(url, timeout=timeout)
    record = {
        "script": script,
        "pid": process.pid,
        "url": url,
        "port": port,
        "host": host,
        "log_path": safe_rel(stdout_path, root),
        "started_at": time.time(),
    }
    registry = _read_registry(root)
    registry.setdefault("servers", []).append(record)
    _write_registry(root, registry)
    if not ok and coerce_bool(args.get("stop_on_fail", True)):
        stop_ok, stop_output = _stop_pid(process.pid)
        record["stopped_after_failed_probe"] = {"ok": stop_ok, "output": stop_output[:1000]}
    return {**base, "ok": ok, "detail": detail, "server": record}


def run_text(workspace: str | Path, args: dict[str, Any] | None = None) -> str:
    return json_output(run(workspace, args))

