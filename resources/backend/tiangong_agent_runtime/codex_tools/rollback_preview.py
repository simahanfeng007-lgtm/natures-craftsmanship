from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import transaction_ops
from .common import ToolInputError, json_output, resolve_workspace_path, safe_rel, workspace_root


ROLLBACK_PREVIEW_SCHEMA = "tiangong.codex.rollback_preview.v1"
TRANSACTION_ROOT = Path(".linyuanzhe") / "codex_transactions"


def _safe_id(value: Any) -> str:
    text = str(value or "").strip()
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in text)[:120]


def _read_manifest(root: Path, *, transaction_id: str = "", manifest_path: str = "") -> tuple[dict[str, Any], str]:
    if manifest_path:
        path = resolve_workspace_path(root, manifest_path, must_exist=True)
    else:
        safe = _safe_id(transaction_id)
        if not safe:
            raise ToolInputError("[BAD_ARGS] rollback_preview requires transaction_id or manifest_path")
        path = root / TRANSACTION_ROOT / "manifests" / f"{safe}.json"
        if not path.exists():
            raise ToolInputError(f"[TRANSACTION_NOT_FOUND] {safe}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != transaction_ops.TRANSACTION_SCHEMA:
        raise ToolInputError("[TRANSACTION_SCHEMA_MISMATCH]")
    return payload, safe_rel(path, root)


def _safe_entry(path: Path, root: Path) -> tuple[dict[str, Any], str]:
    try:
        return transaction_ops._entry(path, root), ""  # type: ignore[attr-defined]
    except Exception as exc:
        return {
            "path": safe_rel(path, root),
            "exists": path.exists() or path.is_symlink(),
            "type": "unknown",
            "error": f"{type(exc).__name__}: {exc}",
        }, f"{type(exc).__name__}: {exc}"


def _matches(path: Path, root: Path, expected: dict[str, Any]) -> tuple[bool, str]:
    if not expected:
        return True, ""
    try:
        return bool(transaction_ops._matches_entry(path, root, expected)), ""  # type: ignore[attr-defined]
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _state_subset(state: dict[str, Any]) -> dict[str, Any]:
    keys = ("exists", "type", "sha256", "tree_sha256", "file_count", "total_bytes", "error")
    return {key: state.get(key) for key in keys if key in state}


def _preview_entry(root: Path, entry: dict[str, Any], *, include_backup_state: bool) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    rel = str(entry.get("path") or "")
    path = resolve_workspace_path(root, rel, must_exist=False)
    before = dict(entry.get("before") or {})
    after = dict(entry.get("after") or {})
    backup_rel = str(entry.get("backup_path") or "")
    current, current_error = _safe_entry(path, root)
    current_matches_after, after_error = _matches(path, root, after)
    current_matches_before, before_error = _matches(path, root, before)
    planned_action = "restore_backup" if before.get("exists") else "delete_created"
    backup_state: dict[str, Any] = {"path": backup_rel, "exists": False}
    backup_available = True
    if before.get("exists"):
        backup_available = False
        if backup_rel:
            try:
                backup_path = resolve_workspace_path(root, backup_rel, must_exist=False)
                backup_available = backup_path.exists() or backup_path.is_symlink()
                backup_state = {"path": backup_rel, "exists": backup_available}
                if include_backup_state and backup_available:
                    backup_state, backup_error = _safe_entry(backup_path, root)
                    if backup_error:
                        backup_state["error"] = backup_error
            except ToolInputError as exc:
                backup_state = {"path": backup_rel, "exists": False, "error": str(exc)}

    target_changed = bool(after) and not current_matches_after
    findings: list[dict[str, Any]] = []
    reads: list[dict[str, Any]] = []
    if current_error:
        findings.append({"severity": "medium", "kind": "current_state_unverified", "file": rel, "line": 0, "message": current_error})
    if after_error:
        findings.append({"severity": "medium", "kind": "after_match_unverified", "file": rel, "line": 0, "message": after_error})
    if before_error:
        findings.append({"severity": "medium", "kind": "before_match_unverified", "file": rel, "line": 0, "message": before_error})
    if target_changed:
        findings.append({
            "severity": "high",
            "kind": "rollback_target_changed",
            "file": rel,
            "line": 0,
            "message": "Current target no longer matches the committed after-state; rollback_ops would block without force.",
        })
        reads.append({"file": rel, "line": 1, "read_hint": f"{rel}:1", "reason": "target changed since transaction commit"})
    if before.get("exists") and not backup_available:
        findings.append({
            "severity": "high",
            "kind": "rollback_backup_missing",
            "file": rel,
            "line": 0,
            "message": "Rollback would need a backup, but the backup path is missing.",
        })
    if current_matches_before and before.get("exists"):
        findings.append({
            "severity": "medium",
            "kind": "target_already_matches_before",
            "file": rel,
            "line": 0,
            "message": "Current target already matches the recorded before-state; rollback may be redundant.",
        })
    if not current.get("exists") and planned_action == "delete_created":
        findings.append({
            "severity": "medium",
            "kind": "created_target_already_missing",
            "file": rel,
            "line": 0,
            "message": "Transaction created this path, but it is already missing.",
        })
    if rel and not reads:
        reads.append({"file": rel, "line": 1, "read_hint": f"{rel}:1", "reason": "rollback target"})

    action_row = {
        "path": rel,
        "planned_action": planned_action,
        "would_restore_backup": planned_action == "restore_backup",
        "would_delete_created_path": planned_action == "delete_created",
        "current_matches_after": current_matches_after if after else None,
        "current_matches_before": current_matches_before if before else None,
        "target_changed_since_commit": target_changed,
        "backup_available": backup_available,
        "before": _state_subset(before),
        "after": _state_subset(after),
        "current": _state_subset(current),
        "backup": backup_state,
    }
    return action_row, findings, reads


def _status_findings(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    status = str(manifest.get("status") or "")
    if status == "committed":
        return []
    return [{
        "severity": "high" if status == "rolled_back" else "medium",
        "kind": "transaction_not_committed",
        "file": "",
        "line": 0,
        "message": f"Transaction status is {status or 'unknown'}, not committed.",
    }]


def analyze(workspace: str | Path, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    root = workspace_root(workspace)
    include_backup_state = str(args.get("include_backup_state") or "").strip().lower() in {"1", "true", "yes", "on"}
    manifest, manifest_rel = _read_manifest(
        root,
        transaction_id=str(args.get("transaction_id") or ""),
        manifest_path=str(args.get("manifest") or args.get("manifest_path") or ""),
    )
    entries = list(manifest.get("entries") or [])
    findings = _status_findings(manifest)
    actions: list[dict[str, Any]] = []
    suggested_reads: list[dict[str, Any]] = []
    for entry in entries:
        action, local_findings, local_reads = _preview_entry(root, entry, include_backup_state=include_backup_state)
        actions.append(action)
        findings.extend(local_findings)
        suggested_reads.extend(local_reads)

    unique_reads: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    for item in suggested_reads:
        file_name = str(item.get("file") or "")
        line_no = max(1, int(item.get("line") or 1))
        reason = str(item.get("reason") or "")
        key = (file_name, line_no, reason)
        if file_name and key not in seen:
            seen.add(key)
            unique_reads.append({**item, "line": line_no, "read_hint": str(item.get("read_hint") or f"{file_name}:{line_no}")})

    high_count = sum(1 for item in findings if str(item.get("severity")) == "high")
    medium_count = sum(1 for item in findings if str(item.get("severity")) == "medium")
    rollback_would_block = any(item.get("kind") == "rollback_target_changed" for item in findings)
    backup_missing = any(item.get("kind") == "rollback_backup_missing" for item in findings)
    status = "blocked" if high_count else ("warn" if medium_count else "clean")
    next_actions = [
        "This is a read-only preview; it does not perform rollback.",
        "Read suggested targets before deciding whether rollback_ops action=rollback is appropriate.",
    ]
    if rollback_would_block:
        next_actions.insert(0, "Do not force rollback until the model inspects changed targets; later user/model edits may be present.")
    elif backup_missing:
        next_actions.insert(0, "Do not rollback until missing backup evidence is resolved.")
    elif status == "clean":
        next_actions.insert(0, "If undo is still desired, call rollback_ops action=rollback, then run readback_verifier and validation.")
    else:
        next_actions.insert(0, "Resolve preview warnings before rollback, or choose a smaller corrective edit instead.")

    return {
        "schema": ROLLBACK_PREVIEW_SCHEMA,
        "ok": True,
        "advisory_only": True,
        "preview_only": True,
        "transaction_id": manifest.get("transaction_id") or "",
        "manifest_path": manifest_rel,
        "transaction_status": manifest.get("status") or "",
        "transaction_action": manifest.get("action") or "",
        "rollback_preview_status": status,
        "rollback_would_block_without_force": rollback_would_block,
        "force_would_be_required": rollback_would_block,
        "backup_missing": backup_missing,
        "entry_count": len(entries),
        "actions": actions,
        "findings": findings[:120],
        "suggested_reads": unique_reads[:80],
        "llm_brief": (
            f"transaction={manifest.get('transaction_id') or ''}; "
            f"status={manifest.get('status') or ''}; entries={len(entries)}; "
            f"preview={status}; high={high_count}; medium={medium_count}"
        ),
        "next_actions": next_actions,
        "raw_bytes_hidden": True,
    }


def run_text(workspace: str | Path, args: dict[str, Any] | None = None) -> str:
    return json_output(analyze(workspace, args), limit=24000)
