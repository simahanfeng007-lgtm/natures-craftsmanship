"""交付 ZIP 打包适配器。"""

from __future__ import annotations

import hashlib
import os
import zipfile
from pathlib import Path

from ..tool_invocation import ToolInvocation
from ..tool_result import ToolResult, ToolResultStatus
from ..turn_context import TurnContext
from ..workspace_guard import SENSITIVE_NAMES, SENSITIVE_SUFFIXES, WorkspaceGuard, WorkspaceViolation

EXCLUDE_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".linyuanzhe", "reports", ".r21_adapter_smoke_workspace",
    "document_contexts", "file_handoffs", "model_profiles", "prompt_trace", "tasks",
}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}
EXCLUDE_NAMES = {".DS_Store"}
MAX_PACKAGE_FILES = 5000


def _is_filesystem_root(path: Path) -> bool:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        resolved = path
    anchor = Path(resolved.anchor) if resolved.anchor else None
    return bool(anchor and resolved == anchor)


def _iter_package_files(source: Path, target: Path, *, max_files: int = MAX_PACKAGE_FILES):
    if source.is_file():
        yield source
        return

    emitted = 0
    for dirpath, dirnames, filenames in os.walk(source):
        current = Path(dirpath)
        kept_dirs: list[str] = []
        for dirname in dirnames:
            child = current / dirname
            if _should_skip(child) or child.is_symlink():
                continue
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for filename in filenames:
            path = current / filename
            if path == target or _should_skip(path):
                continue
            emitted += 1
            if emitted > max_files:
                raise RuntimeError(f"package_file_limit_exceeded:{max_files}")
            yield path


def create_zip_package_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    guard = WorkspaceGuard(context.workspace)
    try:
        source = guard.resolve_for_read(invocation.arguments.get("source") or ".")
    except WorkspaceViolation as exc:
        return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.BLOCKED, str(exc), error_code="workspace_violation")

    if not source.exists():
        return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.FAILED, "打包源不存在。", error_code="path_not_found")
    if _is_filesystem_root(source):
        return ToolResult(
            invocation.step_id,
            invocation.tool_name,
            ToolResultStatus.BLOCKED,
            "拒绝把磁盘根目录作为打包源。请先选择具体项目目录或文件夹，例如桌面的 tmp/desktop_organizer 包目录。",
            error_code="root_workspace_package_blocked",
            data={"source": str(source), "workspace": str(context.workspace)},
        )
    try:
        target = guard.resolve_for_artifact(invocation.arguments.get("target") or "dist/tiangong_delivery.zip")
    except WorkspaceViolation as exc:
        return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.BLOCKED, str(exc), error_code="workspace_violation")

    skipped: list[str] = []
    files_added = 0
    try:
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            base = source if source.is_dir() else source.parent
            for path in _iter_package_files(source, target):
                if path.is_dir():
                    continue
                rel = path.relative_to(base)
                if _should_skip(path):
                    skipped.append(str(rel))
                    continue
                if target == path:
                    continue
                zf.write(path, rel.as_posix())
                files_added += 1
    except RuntimeError as exc:
        if str(exc).startswith("package_file_limit_exceeded:"):
            limit = str(exc).split(":", 1)[1]
            return ToolResult(
                invocation.step_id,
                invocation.tool_name,
                ToolResultStatus.BLOCKED,
                f"打包文件数超过上限 {limit}，请缩小 source 到具体项目目录。",
                error_code="package_file_limit_exceeded",
                data={"source": str(source), "max_files": int(limit)},
            )
        return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.FAILED, f"ZIP 打包失败：{exc}", error_code="zip_failed")
    except OSError as exc:
        return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.FAILED, f"ZIP 打包失败：{exc}", error_code="zip_failed")

    sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
    sha_path = target.with_suffix(target.suffix + ".sha256")
    sha_path.write_text(f"{sha256}  {target.name}\n", encoding="utf-8")
    rel_target = target.relative_to(context.workspace).as_posix()
    rel_sha = sha_path.relative_to(context.workspace).as_posix()
    return ToolResult(
        step_id=invocation.step_id,
        tool_name=invocation.tool_name,
        status=ToolResultStatus.OK,
        output_summary=f"已生成 ZIP：{rel_target}；文件数：{files_added}；SHA256：{sha256}",
        artifacts=[rel_target, rel_sha],
        data={"sha256": sha256, "files_added": files_added, "skipped": skipped[:50]},
    )


def _should_skip(path: Path) -> bool:
    lowered_parts = {part.lower() for part in path.parts}
    if lowered_parts.intersection(EXCLUDE_DIRS):
        return True
    if path.name in EXCLUDE_NAMES or path.name.lower() in SENSITIVE_NAMES:
        return True
    if path.suffix.lower() in EXCLUDE_SUFFIXES or path.suffix.lower() in SENSITIVE_SUFFIXES:
        return True
    lowered = path.as_posix().lower()
    return any(term in lowered for term in ["secret", "token", "credential", "api_key"])
