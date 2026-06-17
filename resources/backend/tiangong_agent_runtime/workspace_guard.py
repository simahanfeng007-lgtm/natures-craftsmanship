"""工作区边界保护。"""

from __future__ import annotations

import json
import os
from pathlib import Path


SENSITIVE_NAMES = {
    ".env",
    ".env.local",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".crt", ".cer"}
SENSITIVE_PARTS = {".ssh", ".gnupg", "credentials", "secrets", "secret", "tokens"}
_UPLOAD_FILE_MAX_COUNT = 5
_UPLOAD_FILE_MAX_TOTAL_BYTES = 200 * 1024 * 1024


class WorkspaceViolation(ValueError):
    pass


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.expanduser().resolve()))


def _uploaded_file_allowlist() -> set[str]:
    raw = os.getenv("TIANGONG_UPLOAD_FILES_JSON", "").strip()
    if not raw:
        return set()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return set()
    if not isinstance(data, list):
        return set()
    allowed: set[str] = set()
    total_size = 0
    for item in data:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "imported").strip()
        if status and status not in {"imported", "selected"}:
            continue
        raw_path = str(item.get("path") or "").strip()
        if not raw_path:
            continue
        try:
            target = Path(raw_path).expanduser().resolve()
        except OSError:
            continue
        if not target.is_file():
            continue
        size = int(item.get("size") or item.get("size_bytes") or 0)
        if size < 0:
            size = 0
        total_size += size or target.stat().st_size
        if total_size > _UPLOAD_FILE_MAX_TOTAL_BYTES:
            break
        allowed.add(_path_key(target))
        if len(allowed) >= _UPLOAD_FILE_MAX_COUNT:
            break
    return allowed


class WorkspaceGuard:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)

    def resolve_for_read(self, path: str | Path) -> Path:
        resolved = self._resolve(path)
        self._ensure_inside_workspace(resolved)
        self._ensure_not_sensitive(resolved)
        return resolved

    def resolve_for_write(self, path: str | Path) -> Path:
        resolved = self._resolve(path)
        self._ensure_inside_workspace(resolved)
        self._ensure_not_sensitive(resolved)
        return resolved

    def resolve_for_artifact(self, path: str | Path) -> Path:
        resolved = self._resolve(path)
        self._ensure_inside_workspace(resolved)
        self._ensure_not_sensitive(resolved)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return resolved

    def _resolve(self, path: str | Path) -> Path:
        raw = Path(path)
        if not raw.is_absolute():
            raw = self.workspace / raw
        return raw.expanduser().resolve()

    def _ensure_inside_workspace(self, resolved: Path) -> None:
        if _path_key(resolved) in _uploaded_file_allowlist():
            return
        try:
            resolved.relative_to(self.workspace)
        except ValueError as exc:
            raise WorkspaceViolation(f"路径越出工作区：{resolved}") from exc

    def _ensure_not_sensitive(self, resolved: Path) -> None:
        lowered_parts = {part.lower() for part in resolved.parts}
        if resolved.name.lower() in SENSITIVE_NAMES:
            raise WorkspaceViolation(f"禁止访问敏感文件：{resolved.name}")
        if resolved.suffix.lower() in SENSITIVE_SUFFIXES:
            raise WorkspaceViolation(f"禁止访问敏感后缀：{resolved.suffix}")
        if lowered_parts.intersection(SENSITIVE_PARTS):
            raise WorkspaceViolation("禁止访问敏感目录或凭证路径。")
