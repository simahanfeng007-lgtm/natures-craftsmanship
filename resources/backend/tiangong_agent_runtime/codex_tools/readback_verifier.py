from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from .common import ToolInputError, bounded_int, json_output, resolve_workspace_path, safe_rel, workspace_root


READBACK_SCHEMA = "tiangong.codex.readback_verifier.v1"


def _as_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item) != ""]
    return [str(value)]


def _split_loose_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,;\n]", value) if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _short(value: Any, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _normalize_diff_path(raw: str) -> str:
    text = str(raw or "").strip().split("\t", 1)[0]
    if text == "/dev/null":
        return ""
    if text.startswith(("a/", "b/")):
        text = text[2:]
    return text


def _line_for(content: str, needle: str) -> int:
    if not needle:
        return 0
    for line_no, line in enumerate(content.splitlines(), start=1):
        if needle in line:
            return line_no
    return 0


def _regex_line(content: str, pattern: str) -> tuple[int, str]:
    try:
        compiled = re.compile(pattern, re.MULTILINE)
    except re.error as exc:
        return -1, f"{type(exc).__name__}: {exc}"
    match = compiled.search(content)
    if not match:
        return 0, ""
    return content[: match.start()].count("\n") + 1, match.group(0)


def _read_targets(root: Path, paths: list[str], warnings: list[str]) -> dict[str, dict[str, Any]]:
    targets: dict[str, dict[str, Any]] = {}
    for raw in paths:
        try:
            path = resolve_workspace_path(root, raw, must_exist=False)
        except ToolInputError as exc:
            warnings.append(str(exc))
            continue
        rel = safe_rel(path, root)
        row: dict[str, Any] = {
            "file": rel,
            "exists": path.exists(),
            "is_file": path.is_file(),
            "sha256": "",
            "line_count": 0,
            "char_count": 0,
            "content": "",
        }
        if path.exists() and path.is_file():
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
                row.update({
                    "sha256": hashlib.sha256(content.encode("utf-8", "replace")).hexdigest(),
                    "line_count": len(content.splitlines()),
                    "char_count": len(content),
                    "content": content,
                })
            except Exception as exc:
                row["read_error"] = f"{type(exc).__name__}: {exc}"
        targets[rel] = row
    return targets


def _diff_seed(diff_text: str, *, max_lines_per_file: int) -> tuple[list[str], dict[str, dict[str, list[str]]], list[dict[str, Any]]]:
    files: list[str] = []
    checks: dict[str, dict[str, list[str]]] = {}
    reads: list[dict[str, Any]] = []
    current_old = ""
    current_new = ""
    current_path = ""
    new_line = 0
    old_line = 0
    hunk_re = re.compile(r"@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?")
    for raw_line in str(diff_text or "").splitlines():
        if raw_line.startswith("diff --git "):
            parts = raw_line.split()
            current_old = _normalize_diff_path(parts[2]) if len(parts) > 2 else ""
            current_new = _normalize_diff_path(parts[3]) if len(parts) > 3 else ""
            current_path = current_new or current_old
            if current_path and current_path not in files:
                files.append(current_path)
                checks.setdefault(current_path, {"contains": [], "absent": []})
            continue
        if raw_line.startswith("--- "):
            current_old = _normalize_diff_path(raw_line[4:])
            continue
        if raw_line.startswith("+++ "):
            current_new = _normalize_diff_path(raw_line[4:])
            current_path = current_new or current_old
            if current_path and current_path not in files:
                files.append(current_path)
                checks.setdefault(current_path, {"contains": [], "absent": []})
            continue
        match = hunk_re.search(raw_line)
        if match:
            old_line = int(match.group(1) or 0)
            new_line = int(match.group(2) or 0)
            if current_path:
                reads.append({
                    "file": current_path,
                    "line": max(1, new_line),
                    "read_hint": f"{current_path}:{max(1, new_line)}",
                    "reason": "diff hunk",
                })
            continue
        if not current_path:
            continue
        bucket = checks.setdefault(current_path, {"contains": [], "absent": []})
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            text = raw_line[1:]
            if text.strip() and len(bucket["contains"]) < max_lines_per_file:
                bucket["contains"].append(text)
            new_line += 1
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            text = raw_line[1:]
            if text.strip() and len(bucket["absent"]) < max_lines_per_file:
                bucket["absent"].append(text)
            old_line += 1
        elif raw_line.startswith(" "):
            new_line += 1
            old_line += 1
    return files, checks, reads


def _check_file(
    row: dict[str, Any],
    *,
    contains: list[str],
    absent: list[str],
    regexes: list[str],
    absent_regexes: list[str],
    expected_sha256: str = "",
    weak_absent: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    file_name = str(row.get("file") or "")
    content = str(row.get("content") or "")
    result = {
        "file": file_name,
        "exists": bool(row.get("exists")),
        "is_file": bool(row.get("is_file")),
        "sha256": row.get("sha256") or "",
        "line_count": row.get("line_count") or 0,
        "char_count": row.get("char_count") or 0,
        "matched_contains": [],
        "missing_contains": [],
        "unexpected_present": [],
        "regex_matches": [],
        "missing_regexes": [],
        "unexpected_regexes": [],
        "sha256_match": None,
    }
    findings: list[dict[str, Any]] = []
    reads: list[dict[str, Any]] = []
    if not row.get("exists"):
        findings.append({
            "severity": "high",
            "kind": "file_missing",
            "file": file_name,
            "line": 0,
            "message": "Readback target does not exist.",
        })
        return result, findings, reads
    if not row.get("is_file"):
        findings.append({
            "severity": "high",
            "kind": "not_a_file",
            "file": file_name,
            "line": 0,
            "message": "Readback target is not a file.",
        })
        return result, findings, reads
    if row.get("read_error"):
        findings.append({
            "severity": "high",
            "kind": "read_failed",
            "file": file_name,
            "line": 0,
            "message": str(row.get("read_error")),
        })
        return result, findings, reads

    for needle in contains:
        line_no = _line_for(content, needle)
        if line_no:
            result["matched_contains"].append({"text": _short(needle), "line": line_no})
            reads.append({"file": file_name, "line": line_no, "read_hint": f"{file_name}:{line_no}", "reason": "expected text matched"})
        else:
            result["missing_contains"].append(_short(needle))
            findings.append({
                "severity": "high",
                "kind": "expected_text_missing",
                "file": file_name,
                "line": 0,
                "message": f"Expected text was not found: {_short(needle)}",
            })
            reads.append({"file": file_name, "line": 1, "read_hint": f"{file_name}:1", "reason": "expected text missing"})

    for needle in absent:
        line_no = _line_for(content, needle)
        if line_no:
            result["unexpected_present"].append({"text": _short(needle), "line": line_no})
            findings.append({
                "severity": "medium" if weak_absent else "high",
                "kind": "forbidden_text_present" if not weak_absent else "deleted_text_still_present",
                "file": file_name,
                "line": line_no,
                "message": f"Text expected to be absent is still present: {_short(needle)}",
            })
            reads.append({"file": file_name, "line": line_no, "read_hint": f"{file_name}:{line_no}", "reason": "unexpected text present"})

    for pattern in regexes:
        line_no, sample = _regex_line(content, pattern)
        if line_no == -1:
            findings.append({"severity": "medium", "kind": "bad_regex", "file": file_name, "line": 0, "message": sample})
        elif line_no:
            result["regex_matches"].append({"regex": pattern, "line": line_no, "sample": _short(sample)})
            reads.append({"file": file_name, "line": line_no, "read_hint": f"{file_name}:{line_no}", "reason": "expected regex matched"})
        else:
            result["missing_regexes"].append(pattern)
            findings.append({
                "severity": "high",
                "kind": "expected_regex_missing",
                "file": file_name,
                "line": 0,
                "message": f"Expected regex did not match: {_short(pattern)}",
            })

    for pattern in absent_regexes:
        line_no, sample = _regex_line(content, pattern)
        if line_no == -1:
            findings.append({"severity": "medium", "kind": "bad_regex", "file": file_name, "line": 0, "message": sample})
        elif line_no:
            result["unexpected_regexes"].append({"regex": pattern, "line": line_no, "sample": _short(sample)})
            findings.append({
                "severity": "high",
                "kind": "forbidden_regex_present",
                "file": file_name,
                "line": line_no,
                "message": f"Forbidden regex matched: {_short(pattern)}",
            })
            reads.append({"file": file_name, "line": line_no, "read_hint": f"{file_name}:{line_no}", "reason": "forbidden regex matched"})

    if expected_sha256:
        actual = str(row.get("sha256") or "")
        result["sha256_match"] = actual.lower() == str(expected_sha256).lower()
        if not result["sha256_match"]:
            findings.append({
                "severity": "high",
                "kind": "sha256_mismatch",
                "file": file_name,
                "line": 0,
                "message": f"Expected sha256 {expected_sha256}, got {actual}.",
            })
    return result, findings, reads


def _checks_by_file(root: Path, checks: Any, warnings: list[str]) -> tuple[list[str], dict[str, dict[str, list[str] | str]]]:
    paths: list[str] = []
    by_file: dict[str, dict[str, list[str] | str]] = {}
    if not isinstance(checks, list):
        return paths, by_file
    for item in checks:
        if not isinstance(item, dict):
            continue
        raw_path = str(item.get("path") or item.get("file") or "").strip()
        if not raw_path:
            warnings.append("[CHECK_WITHOUT_PATH] skipped a check without path/file")
            continue
        try:
            rel = safe_rel(resolve_workspace_path(root, raw_path, must_exist=False), root)
        except ToolInputError as exc:
            warnings.append(str(exc))
            continue
        if rel not in paths:
            paths.append(rel)
        bucket = by_file.setdefault(rel, {"contains": [], "absent": [], "regexes": [], "absent_regexes": [], "sha256": ""})
        bucket["contains"] = [*list(bucket.get("contains") or []), *_as_list(item.get("contains") or item.get("expected") or item.get("must_contain"))]
        bucket["absent"] = [*list(bucket.get("absent") or []), *_as_list(item.get("absent") or item.get("forbidden") or item.get("must_not_contain"))]
        bucket["regexes"] = [*list(bucket.get("regexes") or []), *_as_list(item.get("regex") or item.get("expected_regex"))]
        bucket["absent_regexes"] = [*list(bucket.get("absent_regexes") or []), *_as_list(item.get("absent_regex") or item.get("forbidden_regex"))]
        if item.get("sha256") or item.get("expected_sha256"):
            bucket["sha256"] = str(item.get("sha256") or item.get("expected_sha256"))
    return paths, by_file


def analyze(workspace: str | Path, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    root = workspace_root(workspace)
    warnings: list[str] = []
    max_diff_lines = bounded_int(args.get("max_diff_lines_per_file"), 24, 1, 80)
    diff_text = str(args.get("diff") or args.get("patch") or "")
    diff_files, diff_checks, diff_reads = _diff_seed(diff_text, max_lines_per_file=max_diff_lines) if diff_text.strip() else ([], {}, [])
    loose_paths = _split_loose_list(args.get("files") or args.get("paths") or args.get("path"))
    check_paths, check_map = _checks_by_file(root, args.get("checks"), warnings)
    all_paths: list[str] = []
    for item in [*loose_paths, *check_paths, *diff_files]:
        if item and item not in all_paths:
            all_paths.append(item)

    global_contains = (
        _as_list(args.get("expected_text"))
        + _as_list(args.get("expected_texts"))
        + _as_list(args.get("contains"))
        + _as_list(args.get("must_contain"))
        + _as_list(args.get("expected"))
    )
    global_absent = (
        _as_list(args.get("absent_text"))
        + _as_list(args.get("absent_texts"))
        + _as_list(args.get("absent"))
        + _as_list(args.get("must_not_contain"))
        + _as_list(args.get("forbidden"))
    )
    global_regexes = _as_list(args.get("expected_regex")) + _as_list(args.get("expected_regexes")) + _as_list(args.get("regex"))
    global_absent_regexes = _as_list(args.get("absent_regex")) + _as_list(args.get("absent_regexes")) + _as_list(args.get("forbidden_regex"))
    sha_by_file = args.get("sha256_by_file") if isinstance(args.get("sha256_by_file"), dict) else {}
    single_sha = str(args.get("sha256") or args.get("expected_sha256") or "")

    if not all_paths:
        warnings.append("[NO_TARGETS] provide path/files/checks or a diff/patch")
    targets = _read_targets(root, all_paths, warnings)
    file_results: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    suggested_reads: list[dict[str, Any]] = list(diff_reads)

    for rel, row in targets.items():
        local = check_map.get(rel, {"contains": [], "absent": [], "regexes": [], "absent_regexes": [], "sha256": ""})
        diff_local = diff_checks.get(rel, {"contains": [], "absent": []})
        expected_sha = str(local.get("sha256") or sha_by_file.get(rel) or "")
        if single_sha and len(targets) == 1:
            expected_sha = single_sha
        contains = [*global_contains, *list(local.get("contains") or []), *list(diff_local.get("contains") or [])]
        absent = [*global_absent, *list(local.get("absent") or []), *list(diff_local.get("absent") or [])]
        regexes = [*global_regexes, *list(local.get("regexes") or [])]
        absent_regexes = [*global_absent_regexes, *list(local.get("absent_regexes") or [])]
        result, local_findings, local_reads = _check_file(
            row,
            contains=contains,
            absent=absent,
            regexes=regexes,
            absent_regexes=absent_regexes,
            expected_sha256=expected_sha,
            weak_absent=bool(diff_local.get("absent")) and not global_absent,
        )
        file_results.append(result)
        findings.extend(local_findings)
        suggested_reads.extend(local_reads)

    unique_reads: list[dict[str, Any]] = []
    seen_reads: set[tuple[str, int, str]] = set()
    for item in suggested_reads:
        file_name = str(item.get("file") or "")
        line_no = max(1, int(item.get("line") or 1))
        reason = str(item.get("reason") or "")
        key = (file_name, line_no, reason)
        if file_name and key not in seen_reads:
            seen_reads.add(key)
            unique_reads.append({**item, "line": line_no, "read_hint": str(item.get("read_hint") or f"{file_name}:{line_no}")})

    high_count = sum(1 for item in findings if str(item.get("severity")) == "high")
    medium_count = sum(1 for item in findings if str(item.get("severity")) == "medium")
    status = "fail" if high_count else ("warn" if medium_count or warnings else "pass")
    next_actions = [
        "Treat this as advisory readback evidence, not as a substitute for tests or browser verification.",
        "Use suggested_reads to inspect the exact lines before further edits or final claims.",
    ]
    if status == "fail":
        next_actions.insert(0, "Read failing locations and decide whether to edit again, inspect a rollback_ref, or run targeted validation.")
    elif status == "warn":
        next_actions.insert(0, "Review warnings before relying on this readback.")
    else:
        next_actions.insert(0, "If behavior changed, run the smallest relevant validation tool next.")

    return {
        "schema": READBACK_SCHEMA,
        "ok": True,
        "advisory_only": True,
        "readback_status": status,
        "readback_ok": status == "pass",
        "llm_brief": (
            f"{len(file_results)} file(s), status={status}, "
            f"findings={len(findings)}, high={high_count}, medium={medium_count}"
        ),
        "files": file_results,
        "findings": findings[:120],
        "suggested_reads": unique_reads[:80],
        "diff_derived_checks": bool(diff_text.strip()),
        "next_actions": next_actions,
        "warnings": warnings[:40],
    }


def run_text(workspace: str | Path, args: dict[str, Any] | None = None) -> str:
    return json_output(analyze(workspace, args), limit=22000)
