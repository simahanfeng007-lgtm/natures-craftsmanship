from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

from . import transaction_ops
from .common import ToolInputError, coerce_bool, json_output, resolve_workspace_path, safe_rel, workspace_root


FILE_OPS_SCHEMA = "tiangong.codex.file_ops.v1"
PROTECTED_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".linyuanzhe",
    ".codex",
    "node_modules",
    "backend_runtime",
    "site-packages",
    "__pycache__",
    "dist",
    "build",
    ".venv",
    "venv",
}
PROTECTED_TARGET_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".linyuanzhe",
    ".codex",
    "node_modules",
    "backend_runtime",
    "site-packages",
    ".venv",
    "venv",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _entry(path: Path, root: Path) -> dict[str, Any]:
    exists = path.exists()
    data: dict[str, Any] = {
        "path": safe_rel(path, root),
        "exists": exists,
        "type": "missing",
    }
    if not exists:
        return data
    if path.is_dir():
        try:
            child_count = sum(1 for _ in path.iterdir())
        except Exception:
            child_count = None
        data.update({
            "type": "directory",
            "child_count": child_count,
        })
        return data
    stat = path.stat()
    data.update({
        "type": "file",
        "size": stat.st_size,
        "sha256": _sha256(path),
    })
    return data


def _protected_reason(path: Path, root: Path) -> str:
    resolved = path.resolve(strict=False)
    if resolved == root:
        return "workspace root is protected"
    parts = set(resolved.relative_to(root).parts)
    blocked = sorted(parts & PROTECTED_NAMES)
    if blocked:
        return f"protected path segment: {blocked[0]}"
    return ""


def _protected_target_reason(path: Path, root: Path) -> str:
    resolved = path.resolve(strict=False)
    if resolved == root:
        return "workspace root is protected"
    parts = set(resolved.relative_to(root).parts)
    blocked = sorted(parts & PROTECTED_TARGET_NAMES)
    if blocked:
        return f"protected target segment: {blocked[0]}"
    return ""


def _resolve_target(root: Path, raw: Any, *, must_exist: bool = False) -> Path:
    if raw in (None, ""):
        raise ToolInputError("[BAD_ARGS] missing path")
    path = resolve_workspace_path(root, raw, must_exist=must_exist)
    return path


def _copy_path(source: Path, target: Path, *, overwrite: bool) -> None:
    if source.is_dir():
        if target.exists():
            if not overwrite:
                raise ToolInputError(f"[TARGET_EXISTS] {target}")
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        shutil.copytree(source, target)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not overwrite:
        raise ToolInputError(f"[TARGET_EXISTS] {target}")
    shutil.copy2(source, target)


def _transaction_fields(transaction: dict[str, Any] | None) -> dict[str, Any]:
    if not transaction:
        return {}
    tx_id = transaction.get("transaction_id")
    ref = transaction_ops.rollback_ref(transaction)
    return {
        "transaction_id": tx_id,
        "rollback_ref": ref,
        "transaction_status": transaction.get("status"),
        "llm_brief": f"file_ops change committed; transaction_id={tx_id}; rollback_ref={ref}",
        "next_actions": ["Read back affected paths, use readback_verifier for expected/forbidden checks when useful, then run impact_analyzer/test_selector before final validation."],
    }


def _rollback_prepared(root: Path, transaction: dict[str, Any] | None) -> None:
    if not transaction:
        return
    transaction_ops.rollback(root, transaction_id=str(transaction.get("transaction_id") or ""), force=True)


def run(workspace: str | Path, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    root = workspace_root(workspace)
    action = str(args.get("action") or "").strip().lower()
    if action not in {"stat", "mkdir", "copy", "move", "delete"}:
        raise ToolInputError("[BAD_ARGS] file_ops action must be stat, mkdir, copy, move, or delete")
    dry_run = coerce_bool(args.get("dry_run"))
    overwrite = coerce_bool(args.get("overwrite"))
    recursive = coerce_bool(args.get("recursive"))

    if action in {"copy", "move"}:
        source = _resolve_target(root, args.get("source"), must_exist=True)
        target = _resolve_target(root, args.get("target"), must_exist=False)
        if source == target:
            raise ToolInputError("[SAME_PATH] source and target are the same")
        if action == "move" and _protected_reason(source, root):
            raise ToolInputError(f"[PROTECTED_SOURCE] {_protected_reason(source, root)}")
        target_reason = _protected_target_reason(target, root)
        if target_reason:
            raise ToolInputError(f"[PROTECTED_TARGET] {target_reason}")
        before = {"source": _entry(source, root), "target": _entry(target, root)}
        transaction: dict[str, Any] | None = None
        if not dry_run:
            transaction = transaction_ops.prepare(
                root,
                f"file_ops.{action}",
                [target] if action == "copy" else [source, target],
                metadata={
                    "source": safe_rel(source, root),
                    "target": safe_rel(target, root),
                    "overwrite": overwrite,
                },
            )
            try:
                if action == "copy":
                    _copy_path(source, target, overwrite=overwrite)
                else:
                    if target.exists():
                        if not overwrite:
                            raise ToolInputError(f"[TARGET_EXISTS] {target}")
                        if target.is_dir():
                            shutil.rmtree(target)
                        else:
                            target.unlink()
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(source), str(target))
                transaction = transaction_ops.commit(root, transaction, paths=[target] if action == "copy" else [source, target])
            except Exception:
                _rollback_prepared(root, transaction)
                raise
        return {
            "schema": FILE_OPS_SCHEMA,
            "ok": True,
            "action": action,
            "dry_run": dry_run,
            "before": before,
            "after": {"source": _entry(source, root), "target": _entry(target, root)},
            **_transaction_fields(transaction),
        }

    path = _resolve_target(root, args.get("path") or args.get("target"), must_exist=(action in {"stat", "delete"}))
    if action == "stat":
        return {"schema": FILE_OPS_SCHEMA, "ok": True, "action": action, "entry": _entry(path, root)}

    if action == "mkdir":
        before = _entry(path, root)
        if path.exists() and not path.is_dir():
            raise ToolInputError(f"[TARGET_IS_FILE] {safe_rel(path, root)}")
        transaction: dict[str, Any] | None = None
        if not dry_run:
            transaction = transaction_ops.prepare(
                root,
                "file_ops.mkdir",
                [path],
                metadata={"path": safe_rel(path, root)},
            )
            try:
                path.mkdir(parents=True, exist_ok=True)
                transaction = transaction_ops.commit(root, transaction, paths=[path])
            except Exception:
                _rollback_prepared(root, transaction)
                raise
        return {
            "schema": FILE_OPS_SCHEMA,
            "ok": True,
            "action": action,
            "dry_run": dry_run,
            "before": before,
            "after": _entry(path, root),
            **_transaction_fields(transaction),
        }

    reason = _protected_reason(path, root)
    if reason:
        raise ToolInputError(f"[PROTECTED_PATH] {reason}")
    if path.is_dir() and not recursive:
        raise ToolInputError("[RECURSIVE_REQUIRED] directory deletion requires recursive=true")
    before = _entry(path, root)
    transaction: dict[str, Any] | None = None
    if not dry_run:
        transaction = transaction_ops.prepare(
            root,
            "file_ops.delete",
            [path],
            metadata={"path": safe_rel(path, root), "recursive": recursive},
        )
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            transaction = transaction_ops.commit(root, transaction, paths=[path])
        except Exception:
            _rollback_prepared(root, transaction)
            raise
    return {
        "schema": FILE_OPS_SCHEMA,
        "ok": True,
        "action": action,
        "dry_run": dry_run,
        "before": before,
        "after": _entry(path, root),
        **_transaction_fields(transaction),
    }


def run_text(workspace: str | Path, args: dict[str, Any] | None = None) -> str:
    return json_output(run(workspace, args), limit=16000)
