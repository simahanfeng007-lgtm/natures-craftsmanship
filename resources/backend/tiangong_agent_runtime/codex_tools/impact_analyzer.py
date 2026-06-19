from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from . import semantic_index
from .common import (
    SKIP_DIRS,
    ToolInputError,
    bounded_int,
    coerce_bool,
    command_exists,
    json_output,
    read_package_json,
    resolve_workspace_path,
    run_command,
    safe_rel,
    workspace_root,
)


IMPACT_SCHEMA = "tiangong.codex.impact_analyzer.v1"

PY_SUFFIXES = {".py", ".pyw"}
JS_SUFFIXES = {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json"}
UI_SUFFIXES = {".html", ".css", ".scss", ".sass", ".vue", ".svelte", ".tsx", ".jsx"}
CONFIG_SUFFIXES = {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"}
CODE_SUFFIXES = PY_SUFFIXES | JS_SUFFIXES | UI_SUFFIXES | {".go", ".rs", ".java", ".kt", ".cs", ".cpp", ".c", ".h", ".hpp"}
SCRIPT_ORDER = ("smoke", "ensure", "check", "lint", "typecheck", "test", "build")


def _as_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,;\n]", value) if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _paths_from_diff(diff_text: str) -> list[str]:
    paths: list[str] = []
    for line in str(diff_text or "").splitlines():
        raw = ""
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                raw = parts[3]
        elif line.startswith(("+++ ", "--- ")):
            raw = line[4:].strip().split("\t", 1)[0]
        if not raw or raw == "/dev/null":
            continue
        if raw.startswith(("a/", "b/")):
            raw = raw[2:]
        if raw and raw not in paths:
            paths.append(raw)
    return paths


def _git_changed_files(root: Path) -> list[str]:
    if not command_exists("git"):
        return []
    ok, output, _code = run_command(["git", "diff", "--name-only"], cwd=root, timeout=10)
    files = [line.strip() for line in output.splitlines() if line.strip()] if ok else []
    ok_cached, output_cached, _code_cached = run_command(["git", "diff", "--cached", "--name-only"], cwd=root, timeout=10)
    if ok_cached:
        for line in output_cached.splitlines():
            item = line.strip()
            if item and item not in files:
                files.append(item)
    return files


def _resolve_files(root: Path, raw_files: list[str]) -> tuple[list[Path], list[str]]:
    resolved: list[Path] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for raw in raw_files:
        try:
            path = resolve_workspace_path(root, raw, must_exist=False)
        except ToolInputError as exc:
            warnings.append(str(exc))
            continue
        rel = safe_rel(path, root)
        if rel in seen:
            continue
        seen.add(rel)
        resolved.append(path)
    return resolved, warnings


def _module_names(rel: str) -> set[str]:
    path = Path(rel)
    stem = path.stem
    no_suffix = rel[: -len(path.suffix)] if path.suffix else rel
    dotted = no_suffix.replace("\\", "/").replace("/", ".")
    names = {stem, dotted}
    for prefix in ("src.", "lib.", "app.", "resources.backend."):
        if dotted.startswith(prefix):
            names.add(dotted[len(prefix):])
    return {name for name in names if name and name != "__init__"}


def _is_test_file(rel: str) -> bool:
    lowered = rel.lower().replace("\\", "/")
    name = Path(lowered).name
    return (
        "/test/" in lowered
        or "/tests/" in lowered
        or name.startswith("test_")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
    )


def _is_ui_file(rel: str, suffix: str) -> bool:
    normalized = rel.lower().replace("\\", "/")
    return suffix in UI_SUFFIXES or any(part in normalized for part in ("/src/pages/", "/src/routes/", "/app/", "/pages/", "/components/"))


def _walk_candidate_files(root: Path, names: set[str], *, limit: int = 80) -> list[str]:
    hits: list[str] = []
    if not names:
        return hits
    for item in root.rglob("*"):
        if len(hits) >= limit:
            break
        if any(part in SKIP_DIRS or part.startswith(".") for part in item.relative_to(root).parts[:-1]):
            continue
        if item.is_file() and item.name in names:
            hits.append(safe_rel(item, root))
    return sorted(dict.fromkeys(hits))


def _direct_test_candidates(root: Path, files: list[Path]) -> list[str]:
    names: set[str] = set()
    for path in files:
        suffix = path.suffix.lower()
        stem = path.stem
        if suffix in PY_SUFFIXES:
            names.update({f"test_{stem}.py", f"{stem}_test.py"})
        if suffix in {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx"}:
            names.update({
                f"{stem}.test{suffix}",
                f"{stem}.spec{suffix}",
                f"{stem}.test.ts",
                f"{stem}.spec.ts",
                f"{stem}.test.tsx",
                f"{stem}.spec.tsx",
                f"{stem}.test.js",
                f"{stem}.spec.js",
            })
    return _walk_candidate_files(root, names)


def _known_scripts(root: Path) -> tuple[str, dict[str, str]]:
    package_path, package = read_package_json(root)
    scripts = package.get("scripts") if isinstance(package, dict) else {}
    if not isinstance(scripts, dict):
        scripts = {}
    return (safe_rel(package_path, root) if package_path else ""), {str(key): str(value) for key, value in scripts.items()}


def _recommended_scripts(scripts: dict[str, str], *, run_tests: bool) -> list[str]:
    selected: list[str] = []
    for name in SCRIPT_ORDER:
        if name == "test" and not run_tests:
            continue
        if name in scripts:
            selected.append(name)
    return selected


def _validation_plan(root: Path, files: list[Path], suffixes: set[str], args: dict[str, Any], package_scripts: dict[str, str]) -> dict[str, Any]:
    run_tests = coerce_bool(args.get("run_tests"))
    url = str(args.get("url") or args.get("frontend_url") or "").strip()
    commands: list[dict[str, Any]] = []
    quality_surface: list[str] = []
    browser_checks: list[dict[str, Any]] = []

    if suffixes & PY_SUFFIXES:
        commands.append({"tool": "python_quality_runner", "arguments": {"target": "."}, "reason": "Python files changed"})
        quality_surface.append("python")

    if suffixes & JS_SUFFIXES or suffixes & UI_SUFFIXES:
        selected_scripts = _recommended_scripts(package_scripts, run_tests=run_tests)
        script_arg = ",".join(selected_scripts or ["smoke", "ensure", "check", "lint"])
        commands.append({
            "tool": "code_quality_runner",
            "arguments": {"target": ".", "language": "javascript", "project_scripts": script_arg},
            "reason": "JS/TS/JSON/UI files changed",
        })
        quality_surface.append("javascript")

    if ".go" in suffixes:
        commands.append({"tool": "bash", "arguments": {"command": "go test ./...", "timeout": 120}, "reason": "Go files changed"})
        quality_surface.append("go")
    if ".rs" in suffixes:
        commands.append({"tool": "bash", "arguments": {"command": "cargo check", "timeout": 120}, "reason": "Rust files changed"})
        quality_surface.append("rust")

    ui_files = [safe_rel(path, root) for path in files if _is_ui_file(safe_rel(path, root), path.suffix.lower())]
    if ui_files:
        commands.append({"tool": "frontend_devserver", "arguments": {"action": "plan"}, "reason": "UI files changed; plan devserver before browser verification"})
        browser_checks.append({
            "requires_browser": True,
            "url": url or "",
            "files": ui_files[:20],
            "suggested_tool": "browser_verify" if url else "frontend_devserver start, then browser_verify with the returned URL",
            "viewport": "desktop and mobile when layout changed",
        })
        if url:
            commands.append({"tool": "browser_verify", "arguments": {"url": url, "screenshot": True}, "reason": "Frontend URL provided for UI verification"})

    if not commands:
        commands.append({"tool": "code_quality_runner", "arguments": {"target": ".", "language": "auto"}, "reason": "Default mixed validation"})
        quality_surface.append("mixed")

    direct_tests = _direct_test_candidates(root, files)
    return {
        "commands": commands,
        "quality_surface": sorted(dict.fromkeys(quality_surface)),
        "browser_checks": browser_checks,
        "direct_test_candidates": direct_tests[:40],
        "package_scripts_used": _recommended_scripts(package_scripts, run_tests=run_tests),
    }


def _risk_notes(files: list[Path], root: Path, suffixes: set[str]) -> list[str]:
    notes: list[str] = []
    rels = [safe_rel(path, root).lower().replace("\\", "/") for path in files]
    if any("llm_codex.py" in rel or "codex_tool_specs.py" in rel or "tool_schemas.py" in rel for rel in rels):
        notes.append("Code-X toolchain files changed; require tool registry and progress smoke checks.")
    if any("runtime_entry.py" in rel or "/adapters/" in rel or "/l0_primitives/" in rel for rel in rels):
        notes.append("Runtime or adapter surface changed; require runtime smoke and focused import checks.")
    if any(Path(rel).name in {"package.json", "package-lock.json", "pyproject.toml", "requirements.txt", "tsconfig.json"} for rel in rels):
        notes.append("Dependency or project configuration changed; include environment and script validation.")
    if suffixes & UI_SUFFIXES:
        notes.append("UI surface changed; include browser verification when a runnable URL is available.")
    if any(_is_test_file(rel) for rel in rels):
        notes.append("Test files changed; run the matching test surface, not only syntax checks.")
    return notes


def _llm_brief(rels: list[str], suffixes: set[str], related_files: list[dict[str, Any]], validation: dict[str, Any], risk_notes: list[str]) -> str:
    surfaces: list[str] = []
    if suffixes & PY_SUFFIXES:
        surfaces.append("python")
    if suffixes & JS_SUFFIXES:
        surfaces.append("js-ts-json")
    if suffixes & UI_SUFFIXES:
        surfaces.append("ui")
    if ".go" in suffixes:
        surfaces.append("go")
    if ".rs" in suffixes:
        surfaces.append("rust")
    if not surfaces:
        surfaces.append("mixed")
    command_names = [str(item.get("tool") or "") for item in validation.get("commands", []) if item.get("tool")]
    return (
        f"{len(rels)} changed file(s); surfaces={','.join(sorted(dict.fromkeys(surfaces)))}; "
        f"related_files={len(related_files)}; validation_tools={','.join(command_names) or 'none'}; "
        f"risk_notes={len(risk_notes)}"
    )


def _next_actions(related_files: list[dict[str, Any]], validation: dict[str, Any], risk_notes: list[str]) -> list[str]:
    actions: list[str] = []
    if related_files:
        top = ", ".join(str(item.get("file")) for item in related_files[:5])
        actions.append(f"Inspect top related files before broad edits: {top}")
    tests = validation.get("direct_test_candidates") or []
    if tests:
        actions.append(f"Prefer focused tests or readback around direct candidates: {', '.join(map(str, tests[:5]))}")
    commands = validation.get("commands") or []
    if commands:
        tools = ", ".join(str(item.get("tool") or "") for item in commands[:4])
        actions.append(f"Run suggested validation tools after edits: {tools}")
    if validation.get("browser_checks"):
        actions.append("For UI changes, start/probe the devserver and run browser_verify on the real URL.")
    if risk_notes:
        actions.append("Treat risk_notes as review prompts, not hard blockers.")
    if not actions:
        actions.append("Use the validation_plan as an advisory checklist and choose the smallest useful check.")
    return actions


def analyze(workspace: str | Path, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    root = workspace_root(workspace)
    files_raw = _as_list(args.get("files"))
    files_raw.extend(item for item in _paths_from_diff(str(args.get("diff") or args.get("patch") or "")) if item not in files_raw)
    if not files_raw:
        files_raw = _git_changed_files(root)
    files, warnings = _resolve_files(root, files_raw)
    max_files = bounded_int(args.get("max_files"), 1200, 50, 5000)
    max_related = bounded_int(args.get("max_related"), 80, 10, 300)
    suffixes = {path.suffix.lower() for path in files if path.suffix}
    rels = [safe_rel(path, root) for path in files]
    languages = Counter((path.suffix.lower().lstrip(".") or "none") for path in files)
    package_json, package_scripts = _known_scripts(root)

    semantic: dict[str, Any] = {"symbols": [], "imports": [], "calls": [], "files": [], "errors": []}
    if files and suffixes & CODE_SUFFIXES:
        try:
            semantic = semantic_index.build_index(root, {"path": str(args.get("path") or "."), "max_files": max_files})
        except Exception as exc:
            warnings.append(f"[SEMANTIC_INDEX_FAILED] {type(exc).__name__}: {exc}")

    rel_set = set(rels)
    touched_symbols = [item for item in semantic.get("symbols", []) if item.get("file") in rel_set]
    touched_imports = [item for item in semantic.get("imports", []) if item.get("file") in rel_set]
    touched_symbol_names = {str(item.get("name") or "") for item in touched_symbols if item.get("name")}
    touched_modules: set[str] = set()
    for rel in rels:
        touched_modules.update(_module_names(rel))

    related_scores: dict[str, int] = defaultdict(int)
    related_reasons: dict[str, set[str]] = defaultdict(set)
    related_calls: list[dict[str, Any]] = []
    related_imports: list[dict[str, Any]] = []

    for call in semantic.get("calls", []):
        file_rel = str(call.get("file") or "")
        if not file_rel or file_rel in rel_set:
            continue
        callee = str(call.get("callee_short") or call.get("callee") or "")
        if callee in touched_symbol_names:
            related_scores[file_rel] += 5
            related_reasons[file_rel].add(f"calls changed symbol {callee}")
            related_calls.append(call)

    for item in semantic.get("imports", []):
        file_rel = str(item.get("file") or "")
        if not file_rel or file_rel in rel_set:
            continue
        module = str(item.get("module") or "")
        name = str(item.get("name") or "")
        if any(module == candidate or module.endswith("." + candidate) or candidate.endswith("." + module) for candidate in touched_modules if module):
            related_scores[file_rel] += 4
            related_reasons[file_rel].add(f"imports changed module {module}")
            related_imports.append(item)
        elif touched_symbol_names and any(symbol in name for symbol in touched_symbol_names):
            related_scores[file_rel] += 3
            related_reasons[file_rel].add("imports changed symbol name")
            related_imports.append(item)

    for edge in semantic.get("import_graph", []):
        file_rel = str(edge.get("from") or "")
        target_rel = str(edge.get("to") or "")
        if file_rel and target_rel in rel_set and file_rel not in rel_set:
            related_scores[file_rel] += 6
            related_reasons[file_rel].add(f"imports changed file {target_rel}")
            related_imports.append(edge)

    direct_tests = _direct_test_candidates(root, files)
    for rel in direct_tests:
        if rel not in rel_set:
            related_scores[rel] += 6
            related_reasons[rel].add("direct test candidate")

    related_files = [
        {
            "file": rel,
            "score": score,
            "reasons": sorted(related_reasons[rel]),
        }
        for rel, score in sorted(related_scores.items(), key=lambda item: (-item[1], item[0]))[:max_related]
    ]

    validation = _validation_plan(root, files, suffixes, args, package_scripts)
    risk_notes = _risk_notes(files, root, suffixes)
    touched_symbol_cards = [
        card for card in semantic.get("symbol_cards", [])
        if card.get("file") in rel_set
    ][:40]
    return {
        "schema": IMPACT_SCHEMA,
        "ok": True,
        "advisory_only": True,
        "source": "args_or_diff_or_git_diff",
        "files": rels[:200],
        "file_count": len(rels),
        "suffixes": sorted(suffixes),
        "languages": dict(languages),
        "package_json": package_json,
        "package_scripts_available": sorted(package_scripts),
        "touched_symbols": touched_symbols[:80],
        "touched_imports": touched_imports[:80],
        "touched_symbol_cards": touched_symbol_cards,
        "related_files": related_files,
        "related_calls": related_calls[:max_related],
        "related_imports": related_imports[:max_related],
        "validation_plan": validation,
        "risk_notes": risk_notes,
        "llm_brief": _llm_brief(rels, suffixes, related_files, validation, risk_notes),
        "next_actions": _next_actions(related_files, validation, risk_notes),
        "warnings": warnings[:40],
        "semantic_summary": {
            "scanned_files": semantic.get("scanned_files", 0),
            "symbols": len(semantic.get("symbols", [])),
            "symbol_cards": len(semantic.get("symbol_cards", [])),
            "import_edges": len(semantic.get("import_graph", [])),
            "imports": len(semantic.get("imports", [])),
            "calls": len(semantic.get("calls", [])),
            "errors": len(semantic.get("errors", [])),
        },
    }


def run_text(workspace: str | Path, args: dict[str, Any] | None = None) -> str:
    return json_output(analyze(workspace, args), limit=24000)
