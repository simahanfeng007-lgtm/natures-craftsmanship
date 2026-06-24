from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from . import impact_analyzer
from .common import ToolInputError, bounded_int, command_exists, json_output, resolve_workspace_path, run_command, safe_rel, workspace_root


DIFF_GUARD_SCHEMA = "tiangong.codex.diff_guard.v1"

GENERATED_HINTS = {
    "dist",
    "build",
    "coverage",
    ".next",
    ".nuxt",
    ".svelte-kit",
    "__pycache__",
    "node_modules",
    "backend_runtime",
    "site-packages",
}
PROTECTED_HINTS = {".git", ".hg", ".svn", ".linyuanzhe", ".codex", ".env", ".npmrc", ".pypirc"}
SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|secret|token|password|private[_-]?key)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=:-]{12,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_./+=:-]{16,}"),
)
HUNK_RE = re.compile(r"@@\s+-(?P<old>\d+)(?:,(?P<old_count>\d+))?\s+\+(?P<new>\d+)(?:,(?P<new_count>\d+))?")


def _as_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,;\n]", value) if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _git_diff(root: Path, *, staged: bool = False) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if not command_exists("git"):
        return "", ["[GIT_NOT_FOUND] git executable not found"]
    args = ["git", "diff"]
    if staged:
        args.append("--cached")
    ok, output, _code = run_command(args, cwd=root, timeout=20)
    if not ok and not output:
        warnings.append("[GIT_DIFF_FAILED] unable to read git diff")
    return output, warnings


def _normalize_diff_path(raw: str) -> str:
    text = str(raw or "").strip().split("\t", 1)[0]
    if text == "/dev/null":
        return ""
    if text.startswith(("a/", "b/")):
        text = text[2:]
    return text


def _resolve_diff_file(old_path: str, new_path: str) -> str:
    return new_path or old_path


def _line_findings(line: str, file_path: str, line_no: int) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    content = line[1:] if line.startswith(("+", "-")) else line
    if line.startswith("+"):
        for pattern in SECRET_PATTERNS:
            if pattern.search(content):
                findings.append({
                    "severity": "high",
                    "kind": "possible_secret",
                    "file": file_path,
                    "line": line_no,
                    "message": "Added line looks like a secret or credential.",
                    "suggestion": "Do not commit secrets; replace with config lookup or placeholder before applying.",
                })
                break
        if re.search(r"\b(print|console\.log|debugger|pdb\.set_trace|breakpoint)\b", content):
            findings.append({
                "severity": "medium",
                "kind": "debug_artifact",
                "file": file_path,
                "line": line_no,
                "message": "Added line looks like temporary debug code.",
                "suggestion": "Confirm this is intentional before final validation.",
            })
        if len(content) > 240:
            findings.append({
                "severity": "low",
                "kind": "long_line",
                "file": file_path,
                "line": line_no,
                "message": "Added line is very long.",
                "suggestion": "Read the surrounding code and check formatting expectations.",
            })
    return findings


def _path_findings(path: str) -> list[dict[str, Any]]:
    normalized = path.replace("\\", "/")
    parts = set(part for part in normalized.split("/") if part)
    findings: list[dict[str, Any]] = []
    blocked = sorted(parts & PROTECTED_HINTS)
    if blocked:
        findings.append({
            "severity": "high",
            "kind": "protected_path",
            "file": path,
            "line": 1,
            "message": f"Patch touches protected-looking path segment {blocked[0]}.",
            "suggestion": "Confirm workspace boundary and intent before editing this path.",
        })
    generated = sorted(parts & GENERATED_HINTS)
    if generated:
        findings.append({
            "severity": "medium",
            "kind": "generated_or_dependency_path",
            "file": path,
            "line": 1,
            "message": f"Patch touches generated/dependency-looking path segment {generated[0]}.",
            "suggestion": "Prefer editing source files unless this artifact is intentionally checked in.",
        })
    if normalized.endswith((".lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock")):
        findings.append({
            "severity": "low",
            "kind": "lockfile_changed",
            "file": path,
            "line": 1,
            "message": "Patch touches a lockfile.",
            "suggestion": "Verify dependency intent and run the project package checks.",
        })
    return findings


def parse_diff(diff_text: str) -> dict[str, Any]:
    files: dict[str, dict[str, Any]] = {}
    current_old = ""
    current_new = ""
    current_path = ""
    new_line = 0
    old_line = 0
    findings: list[dict[str, Any]] = []
    suggested_reads: list[dict[str, Any]] = []
    total_additions = 0
    total_deletions = 0
    total_hunks = 0

    def ensure_file(path: str) -> dict[str, Any]:
        row = files.setdefault(path, {
            "file": path,
            "additions": 0,
            "deletions": 0,
            "hunks": 0,
            "first_changed_line": 1,
            "status": "modified",
        })
        return row

    for raw_line in str(diff_text or "").splitlines():
        if raw_line.startswith("diff --git "):
            parts = raw_line.split()
            current_old = _normalize_diff_path(parts[2]) if len(parts) > 2 else ""
            current_new = _normalize_diff_path(parts[3]) if len(parts) > 3 else ""
            current_path = _resolve_diff_file(current_old, current_new)
            if current_path:
                ensure_file(current_path)
            continue
        if raw_line.startswith("--- "):
            current_old = _normalize_diff_path(raw_line[4:])
            current_path = _resolve_diff_file(current_old, current_new)
            continue
        if raw_line.startswith("+++ "):
            current_new = _normalize_diff_path(raw_line[4:])
            current_path = _resolve_diff_file(current_old, current_new)
            if current_path:
                ensure_file(current_path)
            continue
        match = HUNK_RE.search(raw_line)
        if match:
            old_line = int(match.group("old") or 0)
            new_line = int(match.group("new") or 0)
            total_hunks += 1
            if current_path:
                row = ensure_file(current_path)
                row["hunks"] += 1
                if not row.get("first_changed_line") or row.get("first_changed_line") == 1:
                    row["first_changed_line"] = max(1, new_line)
                suggested_reads.append({
                    "file": current_path,
                    "line": max(1, new_line),
                    "read_hint": f"{current_path}:{max(1, new_line)}",
                    "reason": "changed hunk",
                })
            continue
        if not current_path:
            continue
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            row = ensure_file(current_path)
            row["additions"] += 1
            total_additions += 1
            findings.extend(_line_findings(raw_line, current_path, max(1, new_line)))
            new_line += 1
            continue
        if raw_line.startswith("-") and not raw_line.startswith("---"):
            row = ensure_file(current_path)
            row["deletions"] += 1
            total_deletions += 1
            old_line += 1
            continue
        if raw_line.startswith(" "):
            new_line += 1
            old_line += 1

    for path, row in files.items():
        if row["additions"] and not row["deletions"]:
            row["status"] = "added_or_expanded"
        elif row["deletions"] and not row["additions"]:
            row["status"] = "deleted_or_reduced"
        findings.extend(_path_findings(path))
    return {
        "files": list(files.values()),
        "touched_paths": list(files.keys()),
        "additions": total_additions,
        "deletions": total_deletions,
        "hunks": total_hunks,
        "findings": findings,
        "suggested_reads": suggested_reads[:80],
    }


def _size_findings(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    file_count = len(parsed.get("touched_paths") or [])
    total_lines = int(parsed.get("additions") or 0) + int(parsed.get("deletions") or 0)
    if file_count >= 12:
        findings.append({
            "severity": "medium",
            "kind": "wide_patch",
            "file": "",
            "line": 0,
            "message": f"Patch touches {file_count} files.",
            "suggestion": "Ask the model to split review by subsystem and validate each surface.",
        })
    if total_lines >= 500:
        findings.append({
            "severity": "medium",
            "kind": "large_patch",
            "file": "",
            "line": 0,
            "message": f"Patch changes {total_lines} lines.",
            "suggestion": "Prefer semantic_index plus impact_analyzer before further edits.",
        })
    return findings


def _git_check(root: Path, diff_text: str, *, skip: bool) -> dict[str, Any]:
    if skip:
        return {"ran": False, "ok": None, "output": "", "reason": "skip_git_check=true"}
    if not diff_text.strip():
        return {"ran": False, "ok": None, "output": "", "reason": "empty diff"}
    if not command_exists("git"):
        return {"ran": False, "ok": None, "output": "", "reason": "git not found"}
    ok, output, code = run_command(["git", "apply", "--check", "--whitespace=nowarn", "-"], cwd=root, timeout=20, input_text=diff_text)
    if not ok and "No valid patches" in output:
        return {"ran": True, "ok": False, "returncode": code, "output": output[:2000], "reason": "not a git-apply compatible patch"}
    return {"ran": True, "ok": ok, "returncode": code, "output": output[:2000]}


def _severity_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(item.get("severity") or "info") for item in findings)
    return dict(counts)


def _next_actions(findings: list[dict[str, Any]], impact: dict[str, Any], git_check: dict[str, Any]) -> list[str]:
    actions = [
        "Read suggested locations before editing further.",
        "Use impact_analyzer or test_selector suggestions to choose the smallest useful validation.",
    ]
    if any(item.get("severity") == "high" for item in findings):
        actions.insert(0, "Review high-severity findings before applying or finalizing this patch.")
    if git_check.get("ran") and git_check.get("ok") is False:
        actions.insert(0, "Patch did not pass git apply --check; reread target hunks and produce a smaller patch.")
    if (impact.get("validation_plan") or {}).get("browser_checks"):
        actions.append("For UI changes, use frontend_devserver and browser_verify on the real page.")
    actions.append("These are advisory hints for the model, not a hard approval or rejection.")
    return actions


def analyze(workspace: str | Path, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    root = workspace_root(workspace)
    diff_text = str(args.get("diff") or args.get("patch") or "")
    warnings: list[str] = []
    if not diff_text.strip():
        staged = str(args.get("mode") or "").strip().lower() in {"staged", "cached"}
        diff_text, warnings = _git_diff(root, staged=staged)
    files_arg = _as_list(args.get("files"))
    parsed = parse_diff(diff_text)
    if files_arg and not parsed["touched_paths"]:
        resolved_files = []
        for raw in files_arg:
            try:
                path = resolve_workspace_path(root, raw, must_exist=False)
                resolved_files.append(safe_rel(path, root))
            except ToolInputError as exc:
                warnings.append(str(exc))
        parsed["touched_paths"] = resolved_files
        parsed["files"] = [{"file": rel, "additions": 0, "deletions": 0, "hunks": 0, "first_changed_line": 1, "status": "listed"} for rel in resolved_files]
        parsed["suggested_reads"] = [{"file": rel, "line": 1, "read_hint": f"{rel}:1", "reason": "listed file"} for rel in resolved_files[:80]]
    findings = [*parsed.get("findings", []), *_size_findings(parsed)]
    impact = impact_analyzer.analyze(root, {
        "files": parsed.get("touched_paths", []),
        "diff": diff_text,
        "path": args.get("path", "."),
        "run_tests": args.get("run_tests"),
        "url": args.get("url") or args.get("frontend_url"),
    })
    git_check = _git_check(root, diff_text, skip=str(args.get("skip_git_check") or "").lower() in {"1", "true", "yes"})
    if git_check.get("ran") and git_check.get("ok") is False:
        findings.append({
            "severity": "high",
            "kind": "patch_check_failed",
            "file": "",
            "line": 0,
            "message": "git apply --check failed for this patch.",
            "suggestion": "Reread target regions and generate a smaller patch before applying.",
        })
    severity_counts = _severity_counts(findings)
    return {
        "schema": DIFF_GUARD_SCHEMA,
        "ok": True,
        "advisory_only": True,
        "llm_brief": (
            f"{len(parsed.get('touched_paths', []))} file(s), {parsed.get('additions', 0)} addition(s), "
            f"{parsed.get('deletions', 0)} deletion(s), findings={len(findings)}, severities={severity_counts}"
        ),
        "patch_stats": {
            "files": len(parsed.get("touched_paths", [])),
            "additions": parsed.get("additions", 0),
            "deletions": parsed.get("deletions", 0),
            "hunks": parsed.get("hunks", 0),
        },
        "files": parsed.get("files", []),
        "touched_paths": parsed.get("touched_paths", []),
        "findings": findings[:120],
        "severity_counts": severity_counts,
        "suggested_reads": parsed.get("suggested_reads", [])[:80],
        "validation_hints": (impact.get("validation_plan") or {}),
        "impact_brief": impact.get("llm_brief", ""),
        "related_files": impact.get("related_files", [])[:40],
        "git_apply_check": git_check,
        "next_actions": _next_actions(findings, impact, git_check),
        "warnings": warnings[:40],
    }


def run_text(workspace: str | Path, args: dict[str, Any] | None = None) -> str:
    return json_output(analyze(workspace, args), limit=24000)
