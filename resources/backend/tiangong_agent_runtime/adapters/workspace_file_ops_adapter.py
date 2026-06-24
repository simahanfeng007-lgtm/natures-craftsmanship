"""Workspace file operation adapters.

These tools intentionally operate only inside the active workspace.  They are
used for real desktop/workspace organization tasks where write_file is not
enough: create folders, move/rename, copy, and delete.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from ..tool_invocation import ToolInvocation
from ..tool_result import ToolResult, ToolResultStatus
from ..turn_context import TurnContext


FULL_PERMISSION_VALUES = {"workspace_full", "full_access", "full", "unrestricted"}


def _permission_mode() -> str:
    return str(os.getenv("TIANGONG_PERMISSION_MODE") or "workspace_write").strip().lower()


def _blocked(invocation: ToolInvocation, summary: str, code: str, data: dict[str, Any] | None = None) -> ToolResult:
    return ToolResult(
        invocation.step_id,
        invocation.tool_name,
        ToolResultStatus.BLOCKED,
        summary,
        error_code=code,
        data=data or {},
    )


def _require_full_permission(invocation: ToolInvocation) -> ToolResult | None:
    mode = _permission_mode()
    if mode in FULL_PERMISSION_VALUES:
        return None
    return _blocked(
        invocation,
        "workspace file operation blocked: permission mode is not workspace_full.",
        "permission_required",
        {"permission_mode": mode, "required": "workspace_full"},
    )


def _workspace_root(context: TurnContext) -> Path:
    root = Path(context.workspace).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_inside(context: TurnContext, value: str | Path, *, must_exist: bool = False) -> Path:
    root = _workspace_root(context)
    raw = Path(str(value or "."))
    if not raw.is_absolute():
        raw = root / raw
    resolved = raw.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path outside workspace: {resolved}") from exc
    if must_exist and not resolved.exists():
        raise FileNotFoundError(str(resolved))
    return resolved


def _rel(context: TurnContext, path: Path) -> str:
    try:
        return path.resolve().relative_to(_workspace_root(context)).as_posix()
    except Exception:
        return path.as_posix()


def _same_or_child(candidate: Path, parent: Path) -> bool:
    try:
        candidate.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _remove_path(target: Path, *, recursive: bool) -> None:
    if target.is_dir() and not target.is_symlink():
        if recursive:
            shutil.rmtree(target)
        else:
            target.rmdir()
    else:
        target.unlink()


def _prepare_target(context: TurnContext, invocation: ToolInvocation, target: Path, *, overwrite: bool) -> ToolResult | None:
    root = _workspace_root(context)
    if target == root:
        return _blocked(invocation, "refusing to operate on workspace root.", "workspace_root_protected", {"path": str(target)})
    if target.exists():
        if not overwrite:
            return ToolResult(
                invocation.step_id,
                invocation.tool_name,
                ToolResultStatus.FAILED,
                f"target already exists: {_rel(context, target)}",
                error_code="target_exists",
                data={"target": _rel(context, target)},
            )
        _remove_path(target, recursive=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    return None


def make_dir_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    blocked = _require_full_permission(invocation)
    if blocked:
        return blocked
    try:
        target = _resolve_inside(context, invocation.arguments.get("path") or invocation.arguments.get("target") or ".")
        if target == _workspace_root(context):
            return _blocked(invocation, "workspace root already exists; no folder was created.", "workspace_root_protected", {"path": str(target)})
        target.mkdir(parents=True, exist_ok=True)
        rel = _rel(context, target)
        return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.OK, f"directory ready: {rel}", data={"path": rel}, artifacts=[rel])
    except ValueError as exc:
        return _blocked(invocation, str(exc), "workspace_violation")
    except OSError as exc:
        return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.FAILED, f"make_dir failed: {exc}", error_code="os_error")


def move_path_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    blocked = _require_full_permission(invocation)
    if blocked:
        return blocked
    try:
        source = _resolve_inside(context, invocation.arguments.get("source") or invocation.arguments.get("from") or invocation.arguments.get("path") or "", must_exist=True)
        target = _resolve_inside(context, invocation.arguments.get("target") or invocation.arguments.get("to") or invocation.arguments.get("dest") or "")
        overwrite = bool(invocation.arguments.get("overwrite") or False)
        root = _workspace_root(context)
        if source == root or target == root:
            return _blocked(invocation, "refusing to move workspace root.", "workspace_root_protected", {"source": str(source), "target": str(target)})
        if source == target:
            rel = _rel(context, source)
            return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.OK, f"move skipped: source and target are the same path: {rel}", data={"source": rel, "target": rel, "skipped": True}, artifacts=[rel])
        if source.is_dir() and _same_or_child(target, source):
            return _blocked(invocation, "refusing to move a directory into itself.", "target_inside_source")
        prepared = _prepare_target(context, invocation, target, overwrite=overwrite)
        if prepared:
            return prepared
        shutil.move(str(source), str(target))
        src_rel = _rel(context, source)
        dst_rel = _rel(context, target)
        return ToolResult(
            invocation.step_id,
            invocation.tool_name,
            ToolResultStatus.OK,
            f"moved: {src_rel} -> {dst_rel}",
            data={"source": src_rel, "target": dst_rel, "overwrite": overwrite},
            artifacts=[dst_rel],
        )
    except FileNotFoundError as exc:
        return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.FAILED, f"source not found: {exc}", error_code="path_not_found")
    except ValueError as exc:
        return _blocked(invocation, str(exc), "workspace_violation")
    except OSError as exc:
        return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.FAILED, f"move_path failed: {exc}", error_code="os_error")


def copy_path_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    blocked = _require_full_permission(invocation)
    if blocked:
        return blocked
    try:
        source = _resolve_inside(context, invocation.arguments.get("source") or invocation.arguments.get("from") or invocation.arguments.get("path") or "", must_exist=True)
        target = _resolve_inside(context, invocation.arguments.get("target") or invocation.arguments.get("to") or invocation.arguments.get("dest") or "")
        overwrite = bool(invocation.arguments.get("overwrite") or False)
        if target == _workspace_root(context):
            return _blocked(invocation, "refusing to copy over workspace root.", "workspace_root_protected", {"target": str(target)})
        if source == target:
            rel = _rel(context, source)
            return _blocked(invocation, "refusing to copy a path onto itself.", "same_source_target", {"source": rel, "target": rel})
        if source.is_dir() and _same_or_child(target, source):
            return _blocked(invocation, "refusing to copy a directory into itself.", "target_inside_source")
        prepared = _prepare_target(context, invocation, target, overwrite=overwrite)
        if prepared:
            return prepared
        if source.is_dir() and not source.is_symlink():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
        src_rel = _rel(context, source)
        dst_rel = _rel(context, target)
        return ToolResult(
            invocation.step_id,
            invocation.tool_name,
            ToolResultStatus.OK,
            f"copied: {src_rel} -> {dst_rel}",
            data={"source": src_rel, "target": dst_rel, "overwrite": overwrite},
            artifacts=[dst_rel],
        )
    except FileNotFoundError as exc:
        return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.FAILED, f"source not found: {exc}", error_code="path_not_found")
    except ValueError as exc:
        return _blocked(invocation, str(exc), "workspace_violation")
    except OSError as exc:
        return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.FAILED, f"copy_path failed: {exc}", error_code="os_error")


def delete_path_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    blocked = _require_full_permission(invocation)
    if blocked:
        return blocked
    try:
        target = _resolve_inside(context, invocation.arguments.get("path") or invocation.arguments.get("target") or "", must_exist=True)
        if target == _workspace_root(context):
            return _blocked(invocation, "refusing to delete workspace root.", "workspace_root_protected", {"path": str(target)})
        recursive = bool(invocation.arguments.get("recursive") or False)
        rel = _rel(context, target)
        _remove_path(target, recursive=recursive)
        return ToolResult(
            invocation.step_id,
            invocation.tool_name,
            ToolResultStatus.OK,
            f"deleted: {rel}",
            data={"path": rel, "recursive": recursive},
        )
    except FileNotFoundError as exc:
        return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.FAILED, f"path not found: {exc}", error_code="path_not_found")
    except ValueError as exc:
        return _blocked(invocation, str(exc), "workspace_violation")
    except OSError as exc:
        return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.FAILED, f"delete_path failed: {exc}", error_code="os_error")
