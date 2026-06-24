"""Python 质量检查适配器。"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import tokenize
from pathlib import Path

from ..result_normalizer import truncate_text
from ..tool_invocation import ToolInvocation
from ..tool_result import ToolResult, ToolResultStatus
from ..turn_context import TurnContext
from ..workspace_guard import WorkspaceGuard, WorkspaceViolation


ALLOWED_COMMANDS = {"compileall", "python -m compileall", "pytest", "python -m pytest"}
A5_COMMAND_PATTERNS = (
    re.compile(r"\brm\s+-[^\n]*r[^\n]*f\b", re.I),
    re.compile(r"\bdel\s+/[sq]\b", re.I),
    re.compile(r"\b(format|mkfs|shutdown|reboot|halt)\b", re.I),
    re.compile(r"\bpowershell\b.*\b-enc", re.I),
    re.compile(r"\breg\s+delete\b", re.I),
)


def _is_a5_command(command: str) -> bool:
    return any(pattern.search(command) for pattern in A5_COMMAND_PATTERNS)


def _normalize_windows_bash_args(argv: list[str]) -> list[str]:
    shell_name = Path(argv[0]).name.lower() if argv else "bash"
    resolved = _resolve_windows_bash(shell_name)
    path_style = "wsl" if _is_windows_wsl_bash(resolved or argv[0]) else "git"
    normalized = [resolved or argv[0]]
    normalized.extend(_windows_path_to_bash_arg(item, style=path_style) for item in argv[1:])
    return normalized


def _resolve_windows_bash(shell_name: str) -> str:
    candidates = []
    program_files = [os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")]
    for base in program_files:
        if base:
            candidates.extend([
                str(Path(base) / "Git" / "bin" / "bash.exe"),
                str(Path(base) / "Git" / "usr" / "bin" / "bash.exe"),
                str(Path(base) / "Git" / "usr" / "bin" / "sh.exe"),
            ])
    local_app = os.environ.get("LOCALAPPDATA", "")
    if local_app:
        candidates.append(str(Path(local_app) / "Programs" / "Git" / "bin" / "bash.exe"))
    candidates.extend([shutil.which(shell_name), shutil.which("bash" if shell_name.startswith("bash") else "sh")])
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(Path(candidate))
    return ""


def _is_windows_wsl_bash(value: str) -> bool:
    text = os.path.normcase(str(value or ""))
    return bool(text.endswith(os.path.normcase(r"\Windows\System32\bash.exe")) or text.endswith(os.path.normcase(r"\Windows\System32\wsl.exe")))


def _windows_path_to_bash_arg(value: str, *, style: str = "git") -> str:
    text = str(value or "")
    if not re.match(r"^[A-Za-z]:[\\/]", text):
        return text
    drive = text[0].lower()
    rest = text[2:].replace("\\", "/")
    if not rest.startswith("/"):
        rest = "/" + rest
    if style == "wsl":
        return f"/mnt/{drive}{rest}"
    return f"/{drive}{rest}"


def run_python_quality_check_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    raw_command = str(invocation.arguments.get("command") or invocation.arguments.get("command_type") or "compileall").strip()
    command = raw_command.lower()
    target = str(invocation.arguments.get("target") or ".")
    timeout = float(invocation.arguments.get("timeout") or context.policy.default_timeout_seconds)
    guard = WorkspaceGuard(context.workspace)
    try:
        safe_target = guard.resolve_for_read(target)
    except WorkspaceViolation as exc:
        return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.BLOCKED, str(exc), error_code="workspace_violation")

    if command not in ALLOWED_COMMANDS:
        # L6.72.39：非 A5 本地命令不再因 allowlist 被硬阻断；仍使用 shell=False、timeout、审计输出。
        if _is_a5_command(raw_command):
            return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.BLOCKED, "命令命中 A5 高危模式，已阻断。", error_code="a5_command_blocked")
        try:
            argv = shlex.split(raw_command, posix=sys.platform != "win32")
        except ValueError as exc:
            return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.FAILED, f"命令解析失败：{exc}", error_code="command_parse_failed")
        if not argv:
            return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.FAILED, "命令为空。", error_code="empty_command")
        if sys.platform == "win32" and Path(argv[0]).name.lower() in {"bash", "bash.exe", "sh", "sh.exe"}:
            argv = _normalize_windows_bash_args(argv)
    elif "compileall" in command:
        return _run_compile_syntax_check(invocation, context, safe_target)
    else:
        target_arg = _workspace_relative_arg(safe_target, context.workspace)
        argv = [sys.executable, "-m", "pytest", target_arg, "-q"]

    try:
        env = dict(os.environ)
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8:replace")
        env.pop("PYTHONSTARTUP", None)
        completed = subprocess.run(  # noqa: S603 - argv is allowlisted and shell=False
            argv,
            cwd=str(context.workspace),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return ToolResult(
            invocation.step_id,
            invocation.tool_name,
            ToolResultStatus.FAILED,
            f"质量检查超时：{timeout} 秒。",
            error_code="timeout",
            data={
                "stdout": truncate_text(_redact_workspace_paths(exc.stdout or "", context.workspace)),
                "stderr": truncate_text(_redact_workspace_paths(exc.stderr or "", context.workspace)),
            },
        )

    output = _redact_workspace_paths("\n".join(part for part in [completed.stdout, completed.stderr] if part), context.workspace)
    status = ToolResultStatus.OK if completed.returncode == 0 else ToolResultStatus.FAILED
    return ToolResult(
        step_id=invocation.step_id,
        tool_name=invocation.tool_name,
        status=status,
        output_summary=truncate_text(output or f"命令退出码：{completed.returncode}", context.policy.max_output_chars),
        error_code="" if completed.returncode == 0 else "quality_check_failed",
        data={"returncode": completed.returncode, "argv": argv},
    )


def _run_compile_syntax_check(invocation: ToolInvocation, context: TurnContext, safe_target: Path) -> ToolResult:
    """Run a compileall-equivalent syntax check without writing __pycache__ files.

    The previous subprocess ``compileall`` path was faithful but noisy: it printed
    absolute workspace paths and created pyc files during mock long-chain runs.
    For Runtime quality gates we only need syntax validation, so we compile source
    text in memory and keep the output deterministic/reproducible.
    """
    files = list(_iter_python_files(safe_target))
    failures: list[str] = []
    for path in files:
        display = _workspace_relative_arg(path, context.workspace)
        try:
            with tokenize.open(str(path)) as handle:
                source = handle.read()
            compile(source, display, "exec", dont_inherit=True)
        except SyntaxError as exc:
            failures.append(_format_syntax_error(exc, display))
        except UnicodeDecodeError as exc:
            failures.append(f"*** Error compiling '{display}'...\nUnicodeDecodeError: {exc}")
        except OSError as exc:
            failures.append(f"*** Error compiling '{display}'...\nOSError: {exc}")

    if failures:
        output = "\n".join(failures)
        return ToolResult(
            step_id=invocation.step_id,
            tool_name=invocation.tool_name,
            status=ToolResultStatus.FAILED,
            output_summary=truncate_text(output, context.policy.max_output_chars),
            error_code="quality_check_failed",
            data={"returncode": 1, "checked_files": len(files), "failed_files": len(failures), "argv": ["compileall-no-pyc", _workspace_relative_arg(safe_target, context.workspace)]},
        )

    target_label = _workspace_relative_arg(safe_target, context.workspace)
    output = f"compile_check PASS: checked {len(files)} Python files under {target_label}; no pycache written."
    return ToolResult(
        step_id=invocation.step_id,
        tool_name=invocation.tool_name,
        status=ToolResultStatus.OK,
        output_summary=truncate_text(output, context.policy.max_output_chars),
        error_code="",
        data={"returncode": 0, "checked_files": len(files), "argv": ["compileall-no-pyc", target_label]},
    )


def _iter_python_files(target: Path):
    if target.is_file():
        if target.suffix == ".py":
            yield target
        return
    for path in sorted(target.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _workspace_relative_arg(path: Path, workspace: Path) -> str:
    try:
        resolved = Path(path).resolve()
        root = Path(workspace).resolve()
        if resolved == root:
            return "."
        return resolved.relative_to(root).as_posix()
    except Exception:
        return Path(path).name


def _redact_workspace_paths(text: str, workspace: Path) -> str:
    value = str(text or "")
    try:
        root = str(Path(workspace).resolve())
        if root:
            value = value.replace(root, "<workspace>")
    except Exception:
        pass
    return value


def _format_syntax_error(exc: SyntaxError, display: str) -> str:
    lines = [f"*** Error compiling '{display}'..."]
    filename = display
    lineno = exc.lineno or 0
    lines.append(f'  File "{filename}", line {lineno}')
    if exc.text:
        source_line = exc.text.rstrip("\n")
        lines.append(f"    {source_line.strip()}")
        if exc.offset:
            caret_col = max(1, int(exc.offset))
            lines.append("    " + " " * (caret_col - 1) + "^")
    lines.append(f"{exc.__class__.__name__}: {exc.msg}")
    return "\n".join(lines)


def run_python_tests_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    """在受控工作区中执行 pytest/unittest，返回测试结果。"""
    target = str(invocation.arguments.get("target") or ".")
    timeout = float(invocation.arguments.get("timeout") or context.policy.default_timeout_seconds)
    guard = WorkspaceGuard(context.workspace)
    try:
        safe_target = guard.resolve_for_read(target)
    except WorkspaceViolation as exc:
        return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.BLOCKED, str(exc), error_code="workspace_violation")

    target_arg = _workspace_relative_arg(safe_target, context.workspace)
    argv = [sys.executable, "-m", "pytest", target_arg, "-q", "--tb=short"]

    try:
        env = dict(os.environ)
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8:replace")
        env.pop("PYTHONSTARTUP", None)
        completed = subprocess.run(
            argv,
            cwd=str(context.workspace),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return ToolResult(
            invocation.step_id,
            invocation.tool_name,
            ToolResultStatus.FAILED,
            f"测试执行超时：{timeout} 秒。",
            error_code="timeout",
            data={
                "stdout": truncate_text(_redact_workspace_paths(exc.stdout or "", context.workspace)),
                "stderr": truncate_text(_redact_workspace_paths(exc.stderr or "", context.workspace)),
            },
        )

    output = _redact_workspace_paths("\n".join(part for part in [completed.stdout, completed.stderr] if part), context.workspace)
    passed = completed.returncode == 0
    return ToolResult(
        step_id=invocation.step_id,
        tool_name=invocation.tool_name,
        status=ToolResultStatus.OK if passed else ToolResultStatus.FAILED,
        output_summary=truncate_text(output or f"测试退出码：{completed.returncode}", context.policy.max_output_chars),
        error_code="" if passed else "tests_failed",
        data={"returncode": completed.returncode, "argv": argv, "passed": passed},
    )
