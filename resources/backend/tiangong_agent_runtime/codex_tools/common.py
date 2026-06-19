from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


CODEX_TOOL_DIR = Path(".linyuanzhe") / "codex_tools"
SKIP_DIRS = {
    ".git", ".hg", ".svn", ".linyuanzhe", "__pycache__", "node_modules",
    ".venv", "venv", "dist", "build", "backend_runtime", "site-packages",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
}
TEXT_SUFFIXES = {
    ".py", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".html", ".css",
    ".json", ".yaml", ".yml", ".toml", ".md", ".txt", ".ini", ".cfg", ".env",
    ".ps1", ".bat", ".cmd", ".sh", ".go", ".rs", ".java", ".kt", ".cs", ".cpp",
    ".c", ".h", ".hpp", ".sql", ".xml", ".vue", ".svelte",
}
DANGEROUS_COMMAND_PATTERNS = (
    r"\brm\s+(-rf|-fr|/s|/q)\b",
    r"\bdel\s+(/s|/q)\b",
    r"\brmdir\s+(/s|/q)\b",
    r"\bremove-item\b.*(?:^|\s)(?:-recurse|-r)\b",
    r"\bformat\b",
    r"\bmkfs\b",
    r"\breg\s+delete\b",
    r"\bpowershell(?:\.exe)?\s+-enc(?:odedcommand)?\b",
    r"\b(?:curl|wget|iwr|irm)\b.*\|\s*(?:sh|bash|powershell|pwsh)\b",
)


class ToolInputError(ValueError):
    pass


def bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except Exception:
        number = default
    return max(minimum, min(maximum, number))


def coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled", "enable"}


def workspace_root(workspace: str | Path) -> Path:
    root = Path(workspace).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_rel(path: str | Path, root: str | Path) -> str:
    path_obj = Path(path).resolve(strict=False)
    root_obj = Path(root).resolve(strict=False)
    try:
        return path_obj.relative_to(root_obj).as_posix()
    except ValueError:
        return str(path_obj)


def resolve_workspace_path(workspace: str | Path, raw_path: Any, *, must_exist: bool = True) -> Path:
    root = workspace_root(workspace)
    text = str(raw_path if raw_path not in (None, "") else ".").strip()
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ToolInputError(f"[PATH_OUTSIDE_WORKSPACE] {text}") from exc
    if must_exist and not resolved.exists():
        raise ToolInputError(f"[PATH_NOT_FOUND] {text}")
    return resolved


def artifact_dir(workspace: str | Path, name: str) -> Path:
    root = workspace_root(workspace)
    path = root / CODEX_TOOL_DIR / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def json_output(payload: dict[str, Any], *, limit: int = 12000) -> str:
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    return text[:limit] + ("\n...[truncated]" if len(text) > limit else "")


def command_danger_reason(command: str) -> str:
    lowered = str(command or "").strip().lower()
    for pattern in DANGEROUS_COMMAND_PATTERNS:
        if re.search(pattern, lowered):
            return f"dangerous command pattern matched: {pattern}"
    return ""


def command_exists(name: str) -> str:
    return shutil.which(name) or ""


def run_command(
    argv: list[str],
    *,
    cwd: str | Path,
    timeout: int = 20,
    input_text: str | None = None,
) -> tuple[bool, str, int]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    try:
        result = subprocess.run(
            argv,
            cwd=str(cwd),
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return False, f"[TIMEOUT] {' '.join(argv[:4])} > {timeout}s", 124
    except Exception as exc:
        return False, f"[RUN_ERROR] {' '.join(argv[:4])}: {type(exc).__name__}: {exc}", 1
    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part and part.strip())
    return result.returncode == 0, output, result.returncode


def read_package_json(start: str | Path) -> tuple[Path | None, dict[str, Any]]:
    current = Path(start).resolve()
    if current.is_file():
        current = current.parent
    while True:
        package_path = current / "package.json"
        if package_path.exists():
            try:
                return package_path, json.loads(package_path.read_text(encoding="utf-8"))
            except Exception as exc:
                return package_path, {"_parse_error": f"{type(exc).__name__}: {exc}"}
        if current.parent == current:
            return None, {}
        current = current.parent


def script_command(package: dict[str, Any], name: str) -> str:
    scripts = package.get("scripts")
    if not isinstance(scripts, dict):
        return ""
    return str(scripts.get(name) or "")


def find_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def wait_for_url(url: str, *, timeout: int = 30) -> tuple[bool, str]:
    deadline = time.time() + max(1, timeout)
    last_error = ""
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Tiangong-CodeX/1.0"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                return True, f"http_status={resp.status}"
        except urllib.error.HTTPError as exc:
            if 200 <= int(exc.code) < 500:
                return True, f"http_status={exc.code}"
            last_error = f"HTTPError: {exc.code}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.5)
    return False, last_error or "timeout"
