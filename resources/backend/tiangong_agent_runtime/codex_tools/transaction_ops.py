from __future__ import annotations

import hashlib
import json
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .common import ToolInputError, bounded_int, coerce_bool, json_output, resolve_workspace_path, safe_rel, workspace_root


TRANSACTION_SCHEMA = "tiangong.codex.transaction.v1"
ROLLBACK_SCHEMA = "tiangong.codex.transaction_rollback.v1"
TRANSACTION_ROOT = Path(".linyuanzhe") / "codex_transactions"
DEFAULT_MAX_BACKUP_FILES = 2000
DEFAULT_MAX_BACKUP_BYTES = 50 * 1024 * 1024


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_id(value: Any) -> str:
    text = str(value or "").strip()
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in text)
    return cleaned[:120]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _transaction_root(root: Path) -> Path:
    base = root / TRANSACTION_ROOT
    (base / "manifests").mkdir(parents=True, exist_ok=True)
    (base / "backups").mkdir(parents=True, exist_ok=True)
    return base


def _manifest_path(root: Path, transaction_id: str) -> Path:
    safe = _safe_id(transaction_id)
    if not safe:
        raise ToolInputError("[BAD_ARGS] missing transaction_id")
    return _transaction_root(root) / "manifests" / f"{safe}.json"


def rollback_ref(manifest: dict[str, Any]) -> str:
    return str(manifest.get("manifest_path") or "")


def _manifest_paths(manifest: dict[str, Any]) -> list[str]:
    return [str(entry.get("path") or "") for entry in (manifest.get("entries") or []) if entry.get("path")]


def _manifest_brief(manifest: dict[str, Any]) -> str:
    paths = _manifest_paths(manifest)
    return (
        f"transaction={manifest.get('transaction_id') or ''}; "
        f"action={manifest.get('action') or ''}; status={manifest.get('status') or ''}; "
        f"paths={len(paths)}; rollback_ref={manifest.get('manifest_path') or ''}"
    )


def _manifest_next_actions(manifest: dict[str, Any]) -> list[str]:
    status = str(manifest.get("status") or "")
    actions = ["Use rollback_preview before rollback if later edits may have touched the same files."]
    if status == "committed":
        actions.append("Call rollback_ops action=rollback with transaction_id or manifest_path to undo this committed change.")
    elif status == "rolled_back":
        actions.append("Inspect files or run validation before applying a new fix.")
    else:
        actions.append("This transaction is not committed; prefer inspecting the manifest before relying on it.")
    return actions


def _make_transaction_id(action: str, rels: list[str]) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    basis = json.dumps({"action": action, "paths": rels, "time": time.time_ns()}, sort_keys=True)
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]
    return f"{stamp}_{_safe_id(action) or 'transaction'}_{digest}"


def _remove_path(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _dir_stats(path: Path, *, max_files: int = DEFAULT_MAX_BACKUP_FILES, max_bytes: int = DEFAULT_MAX_BACKUP_BYTES) -> dict[str, Any]:
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    for item in sorted(path.rglob("*"), key=lambda candidate: candidate.as_posix()):
        if item.is_dir() and not item.is_symlink():
            continue
        if not item.exists() and not item.is_symlink():
            continue
        file_count += 1
        if file_count > max_files:
            raise ToolInputError(f"[BACKUP_TOO_LARGE] directory exceeds {max_files} files: {path}")
        size = item.stat().st_size if item.exists() and not item.is_dir() else 0
        total_bytes += size
        if total_bytes > max_bytes:
            raise ToolInputError(f"[BACKUP_TOO_LARGE] directory exceeds {max_bytes} bytes: {path}")
        rel = item.relative_to(path).as_posix()
        digest.update(rel.encode("utf-8", errors="replace"))
        digest.update(str(size).encode("ascii"))
        if item.is_file():
            digest.update(_sha256_file(item).encode("ascii"))
    return {
        "file_count": file_count,
        "total_bytes": total_bytes,
        "tree_sha256": digest.hexdigest(),
    }


def _entry(path: Path, root: Path) -> dict[str, Any]:
    rel = safe_rel(path, root)
    payload: dict[str, Any] = {"path": rel, "exists": path.exists() or path.is_symlink()}
    if not payload["exists"]:
        payload["type"] = "missing"
        return payload
    if path.is_dir() and not path.is_symlink():
        stats = _dir_stats(path)
        payload.update({
            "type": "dir",
            "file_count": stats["file_count"],
            "total_bytes": stats["total_bytes"],
            "tree_sha256": stats["tree_sha256"],
        })
        return payload
    stat = path.stat()
    payload.update({
        "type": "file",
        "size": stat.st_size,
        "sha256": _sha256_file(path),
    })
    return payload


def _matches_entry(path: Path, root: Path, expected: dict[str, Any]) -> bool:
    if not expected:
        return True
    current_exists = path.exists() or path.is_symlink()
    if bool(expected.get("exists")) != current_exists:
        return False
    if not current_exists:
        return True
    expected_type = str(expected.get("type") or "")
    if expected_type == "file":
        return path.is_file() and _sha256_file(path) == str(expected.get("sha256") or "")
    if expected_type == "dir":
        if not path.is_dir() or path.is_symlink():
            return False
        current = _entry(path, root)
        return str(current.get("tree_sha256") or "") == str(expected.get("tree_sha256") or "")
    return True


def _copy_to_backup(source: Path, backup: Path) -> None:
    if source.is_dir() and not source.is_symlink():
        _dir_stats(source)
        if backup.exists():
            _remove_path(backup)
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, backup, symlinks=True)
        return
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, backup)


def _restore_backup(backup: Path, target: Path) -> None:
    if not backup.exists() and not backup.is_symlink():
        raise ToolInputError(f"[ROLLBACK_BACKUP_NOT_FOUND] {backup}")
    _remove_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if backup.is_dir() and not backup.is_symlink():
        shutil.copytree(backup, target, symlinks=True)
    else:
        shutil.copy2(backup, target)


def _read_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != TRANSACTION_SCHEMA:
        raise ToolInputError("[TRANSACTION_SCHEMA_MISMATCH]")
    return payload


def _write_manifest(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    transaction_id = _safe_id(payload.get("transaction_id"))
    path = _manifest_path(root, transaction_id)
    payload["manifest_path"] = safe_rel(path, root)
    payload["llm_brief"] = _manifest_brief(payload)
    payload["next_actions"] = _manifest_next_actions(payload)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def load_manifest(workspace: str | Path, *, transaction_id: str = "", manifest_path: str = "") -> dict[str, Any]:
    root = workspace_root(workspace)
    if manifest_path:
        path = resolve_workspace_path(root, manifest_path, must_exist=True)
    else:
        path = _manifest_path(root, transaction_id)
    return _read_manifest(path)


def prepare(
    workspace: str | Path,
    action: str,
    paths: list[str | Path],
    *,
    metadata: dict[str, Any] | None = None,
    transaction_id: str = "",
) -> dict[str, Any]:
    root = workspace_root(workspace)
    resolved: list[Path] = []
    seen: set[str] = set()
    for raw_path in paths:
        path = Path(raw_path).resolve(strict=False)
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ToolInputError(f"[PATH_OUTSIDE_WORKSPACE] {raw_path}") from exc
        rel = safe_rel(path, root)
        if rel not in seen:
            seen.add(rel)
            resolved.append(path)
    rels = [safe_rel(path, root) for path in resolved]
    tx_id = _safe_id(transaction_id) or _make_transaction_id(action, rels)
    backup_base = _transaction_root(root) / "backups" / tx_id
    entries: list[dict[str, Any]] = []
    for path in resolved:
        before = _entry(path, root)
        backup_rel = ""
        if before.get("exists"):
            backup = backup_base / safe_rel(path, root)
            _copy_to_backup(path, backup)
            backup_rel = safe_rel(backup, root)
        entries.append({
            "path": safe_rel(path, root),
            "before": before,
            "after": {},
            "backup_path": backup_rel,
        })
    payload = {
        "schema": TRANSACTION_SCHEMA,
        "transaction_id": tx_id,
        "action": str(action or "transaction"),
        "status": "prepared",
        "created_at": _now(),
        "entries": entries,
        "metadata": metadata or {},
        "raw_bytes_hidden": True,
    }
    return _write_manifest(root, payload)


def commit(
    workspace: str | Path,
    manifest_or_id: dict[str, Any] | str,
    *,
    paths: list[str | Path] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = workspace_root(workspace)
    if isinstance(manifest_or_id, dict):
        manifest = dict(manifest_or_id)
    else:
        manifest = load_manifest(root, transaction_id=str(manifest_or_id))
    entries = list(manifest.get("entries") or [])
    path_map: dict[str, Path] = {}
    if paths:
        for raw_path in paths:
            path = Path(raw_path).resolve(strict=False)
            path_map[safe_rel(path, root)] = path
    for entry in entries:
        rel = str(entry.get("path") or "")
        path = path_map.get(rel) or resolve_workspace_path(root, rel, must_exist=False)
        entry["after"] = _entry(path, root)
    manifest["entries"] = entries
    manifest["status"] = "committed"
    manifest["committed_at"] = _now()
    if metadata:
        merged = dict(manifest.get("metadata") or {})
        merged.update(metadata)
        manifest["metadata"] = merged
    return _write_manifest(root, manifest)


def rollback(
    workspace: str | Path,
    *,
    transaction_id: str = "",
    manifest_path: str = "",
    force: bool = False,
) -> dict[str, Any]:
    root = workspace_root(workspace)
    manifest = load_manifest(root, transaction_id=transaction_id, manifest_path=manifest_path)
    tx_id = str(manifest.get("transaction_id") or "")
    entries = list(manifest.get("entries") or [])
    for entry in entries:
        path = resolve_workspace_path(root, entry.get("path"), must_exist=False)
        expected_after = entry.get("after") or {}
        if expected_after and not force and not _matches_entry(path, root, expected_after):
            raise ToolInputError(f"[ROLLBACK_TARGET_CHANGED] {entry.get('path')}")
    actions: list[dict[str, Any]] = []
    for entry in reversed(entries):
        rel = str(entry.get("path") or "")
        path = resolve_workspace_path(root, rel, must_exist=False)
        before = entry.get("before") or {}
        backup_rel = str(entry.get("backup_path") or "")
        if before.get("exists"):
            if not backup_rel:
                raise ToolInputError(f"[ROLLBACK_BACKUP_MISSING] {rel}")
            backup = resolve_workspace_path(root, backup_rel, must_exist=True)
            _restore_backup(backup, path)
            action = "restore_backup"
        else:
            if path.exists() or path.is_symlink():
                _remove_path(path)
            action = "delete_created"
        actions.append({"path": rel, "action": action})
    rollback_id = f"{tx_id}_rollback_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    rollback_path = _manifest_path(root, rollback_id)
    rollback_payload = {
        "schema": ROLLBACK_SCHEMA,
        "transaction_id": tx_id,
        "rollback_id": rollback_id,
        "rolled_back_at": _now(),
        "force": force,
        "actions": actions,
        "source_manifest": str(manifest.get("manifest_path") or ""),
        "raw_bytes_hidden": True,
    }
    rollback_path.write_text(json.dumps(rollback_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["status"] = "rolled_back"
    manifest["rolled_back_at"] = rollback_payload["rolled_back_at"]
    manifest["rollback_manifest"] = safe_rel(rollback_path, root)
    _write_manifest(root, manifest)
    return {
        "schema": ROLLBACK_SCHEMA,
        "ok": True,
        "transaction_id": tx_id,
        "rollback_manifest": safe_rel(rollback_path, root),
        "actions": actions,
        "force": force,
        "llm_brief": f"rolled_back transaction={tx_id}; actions={len(actions)}; rollback_manifest={safe_rel(rollback_path, root)}",
        "next_actions": ["Read back restored files and run the smallest relevant validation before continuing."],
    }


def list_transactions(workspace: str | Path, *, limit: int = 20) -> dict[str, Any]:
    root = workspace_root(workspace)
    manifest_dir = _transaction_root(root) / "manifests"
    items: list[dict[str, Any]] = []
    for path in sorted(manifest_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if payload.get("schema") != TRANSACTION_SCHEMA:
            continue
        items.append({
            "transaction_id": payload.get("transaction_id"),
            "action": payload.get("action"),
            "status": payload.get("status"),
            "created_at": payload.get("created_at"),
            "committed_at": payload.get("committed_at"),
            "manifest_path": safe_rel(path, root),
            "paths": [entry.get("path") for entry in (payload.get("entries") or [])],
        })
        if len(items) >= limit:
            break
    return {
        "schema": TRANSACTION_SCHEMA,
        "ok": True,
        "transactions": items,
        "count": len(items),
        "llm_brief": f"{len(items)} recent committed/prepared Code-X transaction(s) found",
        "next_actions": ["Use rollback_preview on a transaction before rollback when file freshness matters."],
    }


def run(workspace: str | Path, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    action = str(args.get("action") or "list").strip().lower()
    if action == "list":
        return list_transactions(workspace, limit=bounded_int(args.get("limit"), 20, 1, 100))
    if action == "show":
        manifest = load_manifest(
            workspace,
            transaction_id=str(args.get("transaction_id") or ""),
            manifest_path=str(args.get("manifest") or args.get("manifest_path") or ""),
        )
        return {
            "schema": TRANSACTION_SCHEMA,
            "ok": True,
            "llm_brief": _manifest_brief(manifest),
            "next_actions": _manifest_next_actions(manifest),
            "transaction": manifest,
        }
    if action == "rollback":
        return rollback(
            workspace,
            transaction_id=str(args.get("transaction_id") or ""),
            manifest_path=str(args.get("manifest") or args.get("manifest_path") or ""),
            force=coerce_bool(args.get("force")),
        )
    raise ToolInputError("[BAD_ARGS] rollback_ops action must be list, show, or rollback")


def run_text(workspace: str | Path, args: dict[str, Any] | None = None) -> str:
    return json_output(run(workspace, args), limit=20000)
