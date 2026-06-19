from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from .common import artifact_dir, bounded_int, command_exists, json_output, run_command, safe_rel, workspace_root


def run(workspace: str | Path, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    root = workspace_root(workspace)
    url = str(args.get("url") or "").strip()
    if not url:
        return {"schema": "tiangong.codex.browser_verify.v1", "ok": False, "error": "[BAD_ARGS] browser_verify requires url"}
    node = command_exists("node")
    if not node:
        return {"schema": "tiangong.codex.browser_verify.v1", "ok": False, "error": "[NODE_NOT_FOUND] node executable not found"}

    output_dir = artifact_dir(root, "browser_verify")
    stamp = str(int(time.time() * 1000))
    screenshot_path = ""
    if args.get("screenshot", True):
        screenshot_path = str(output_dir / f"browser-{stamp}.png")
    request = {
        "schema": "tiangong.codex.browser_verify.request.v1",
        "workspace": str(root),
        "url": url,
        "timeout": bounded_int(args.get("timeout"), 30, 3, 180) * 1000,
        "waitUntil": str(args.get("wait_until") or "domcontentloaded"),
        "viewport": args.get("viewport") or {"width": 1366, "height": 768},
        "actions": args.get("actions") if isinstance(args.get("actions"), list) else [],
        "screenshotPath": screenshot_path,
        "fullPage": bool(args.get("full_page", True)),
        "collectDom": bool(args.get("collect_dom", True)),
    }
    request_path = output_dir / f"request-{stamp}.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    runner = Path(__file__).with_name("browser_verify_runner.mjs")
    ok, output, code = run_command(
        [node, str(runner), str(request_path)],
        cwd=root,
        timeout=bounded_int(args.get("timeout"), 30, 3, 180) + 10,
    )
    try:
        payload = json.loads(output)
    except Exception:
        payload = {
            "schema": "tiangong.codex.browser_verify.v1",
            "ok": False,
            "status": "runner_output_parse_failed",
            "exit_code": code,
            "output": output[:4000],
        }
    payload.setdefault("request_path", safe_rel(request_path, root))
    if screenshot_path:
        payload.setdefault("screenshot_path", safe_rel(screenshot_path, root))
    payload.setdefault("runner_ok", ok)
    payload.setdefault("exit_code", code)
    return payload


def run_text(workspace: str | Path, args: dict[str, Any] | None = None) -> str:
    return json_output(run(workspace, args), limit=16000)


if __name__ == "__main__":
    workspace = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    payload = json.loads(sys.stdin.read() or "{}")
    print(run_text(workspace, payload))

