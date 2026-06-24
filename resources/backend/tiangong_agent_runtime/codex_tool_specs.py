"""Single source of truth for Code-X tool semantics.

Keep all prompt-facing Code-X tool semantics in ASCII English. The Code-X loop
executes tools directly, while the surrounding runtime exposes tool cards,
schema hints, and progress interpretation. These surfaces should derive from
this file instead of maintaining parallel prompt text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


CODEX_SYSTEM_FLOW = (
    "work_log_read restore prior work log -> "
    "dependency_probe check local tool environment -> "
    "codebase_map/git_inspect build project map and change context -> "
    "glob/grep/symbol_search/semantic_index/semantic_lookup/call_graph locate and understand code -> "
    "read_file collect file evidence -> "
    "evidence_card compress bulky evidence into short lossy cards when useful -> "
    "file_ops/replace_lines/safe_apply_patch/write_file modify filesystem and files with rollback refs -> "
    "read_file read back modified files -> "
    "readback_verifier check expected and forbidden file evidence after edits -> "
    "diff_guard review patch risk, suggested reads, and validation hints -> "
    "impact_analyzer summarize advisory impact, related files, and validation surfaces -> "
    "test_selector choose validation surface -> "
    "python_quality_runner/code_quality_runner/frontend_devserver/browser_verify validate behavior -> "
    "failure_parser parse failures and recover -> "
    "rollback_preview inspect undo impact before rollback when recovery needs it -> "
    "rollback_ops inspect or restore transaction manifests when the model chooses undo -> "
    "work_log_write persist this run log -> "
    "task dispatch read-only parallel investigation when useful"
)

CODEX_TOOL_PLAYBOOK = (
    "Default loop: locate -> read -> modify -> readback -> verify -> reflect -> log. "
    "The model chooses tools; the system only enforces workspace and A5 hard boundaries. "
    "Use glob/grep/symbol_search for fast location; use semantic_index file_cards/symbol_cards/import_graph for LLM code comprehension; use semantic_lookup/call_graph for cross-file reasoning. "
    "Use evidence_card when a previous tool output, diff, or file set is too bulky and the model needs a short lossy evidence reminder with suggested reads. "
    "Use file_ops for mkdir/copy/move/delete/stat instead of shell file-management commands. "
    "Prefer replace_lines for precise edits, safe_apply_patch for small unified diffs, and write_file for whole-file rewrites. "
    "Successful write tools return transaction_id and rollback_ref; keep rollback as a recovery path, not a normal phase. "
    "Use rollback_preview before rollback_ops when later edits may have touched the same files or undo impact must be inspected; preview is advisory and read-only. "
    "Use readback_verifier after edits when the model needs a structured advisory check that expected text exists, forbidden text is absent, or sha256 matches. "
    "Use diff_guard as an advisory patch review tool before or after edits when the model needs patch risks, suggested reads, and validation hints; it does not approve or reject patches. "
    "Use impact_analyzer as an advisory LLM convenience tool: it summarizes impact and tool-ready validation suggestions, but the model still chooses the final path. "
    "Map helper tools into the existing three-stage workflow: evidence_card supports evidence compression, readback_verifier supports readback, diff_guard/impact_analyzer/test_selector support quality planning, and rollback_preview supports recovery. "
    "Do not add a separate intent-verifier layer; preserve intent alignment inside the structure card and detailed plan. "
    "Prefer python_quality_runner/code_quality_runner for validation; use frontend_devserver plus browser_verify for UI behavior. "
    "A failed tool call is recoverable: parse the failure, adjust arguments or use an equivalent tool, then continue."
)


def _schema(
    properties: dict[str, dict[str, Any]] | None = None,
    required: Iterable[str] = (),
    *,
    additional: bool = False,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties or {},
        "required": list(required),
        "additionalProperties": additional,
    }


@dataclass(frozen=True)
class CodeXToolSpec:
    name: str
    description: str
    phase: str
    substep: str
    risk: str
    schema: dict[str, Any]
    use_when: str
    avoid_when: str
    recovery: str

    def llm_description(self) -> str:
        return (
            f"{self.description} Phase:{self.phase}. "
            f"Args:{_schema_hint(self.schema)}. "
            f"Use when:{self.use_when}. Avoid when:{self.avoid_when}. "
            f"Recovery:{self.recovery}."
        )

    def usage_card(self) -> str:
        return (
            f"{self.description} args:{_schema_hint(self.schema)}; "
            f"phase={self.phase}; substep={self.substep}; risk={self.risk}; "
            f"use={self.use_when}; avoid={self.avoid_when}; recover={self.recovery}"
        )


def _schema_hint(schema: dict[str, Any]) -> str:
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    if not props:
        return "none"
    parts = []
    for name in props:
        suffix = "" if name in required else "?"
        parts.append(f"{name}{suffix}")
    return ", ".join(parts)


CODEX_TOOL_SPECS: tuple[CodeXToolSpec, ...] = (
    CodeXToolSpec(
        "work_log_read",
        "Read the previous Code-X work log and restore task context",
        "context",
        "inspect",
        "read",
        _schema(),
        "starting detailed execution or resuming a prior run",
        "the current run has already read the work log",
        "if no log exists, continue the current task instead of blocking",
    ),
    CodeXToolSpec(
        "read_file",
        "Read a workspace text file with line numbers, encoding, sha256, and truncation metadata",
        "evidence",
        "readback",
        "read",
        _schema({
            "path": {"type": "string"},
            "start_line": {"type": "integer"},
            "max_lines": {"type": "integer"},
            "max_chars": {"type": "integer"},
        }, ["path"]),
        "file evidence, pre-edit inspection, post-edit readback, or exact line review is needed",
        "only file discovery or text search is needed; use glob, grep, or semantic_lookup first",
        "if the path fails, use list_dir or glob to relocate the file",
    ),
    CodeXToolSpec(
        "list_dir",
        "List a workspace directory with file and directory markers plus truncation metadata",
        "locate",
        "inspect",
        "read",
        _schema({"path": {"type": "string"}, "limit": {"type": "integer"}}),
        "the directory structure or likely entry points are unclear",
        "content search is needed; use grep, or use glob for recursive name matching",
        "if the directory is missing, step up to the parent path and inspect again",
    ),
    CodeXToolSpec(
        "write_file",
        "Create or overwrite a workspace file, then read back and return diff, sha256, and rollback_ref",
        "modify",
        "write",
        "write",
        _schema({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
        "a whole file, function, or module rewrite is clearer than line edits",
        "a precise small edit is enough; prefer replace_lines or safe_apply_patch",
        "after writing, verify with read_file or a quality tool",
    ),
    CodeXToolSpec(
        "file_ops",
        "Perform structured workspace file operations: stat, mkdir, copy, move, or delete with rollback_ref for writes",
        "modify",
        "write",
        "write",
        _schema({
            "action": {"type": "string"},
            "path": {"type": "string"},
            "source": {"type": "string"},
            "target": {"type": "string"},
            "overwrite": {"type": "boolean"},
            "recursive": {"type": "boolean"},
            "dry_run": {"type": "boolean"},
        }, ["action"]),
        "directory creation, copy, move, delete, or stat should be done without shell commands",
        "text content needs to be edited; use replace_lines, safe_apply_patch, or write_file",
        "use dry_run first for risky moves or deletes; protected paths and workspace escape are blocked",
    ),
    CodeXToolSpec(
        "rollback_ops",
        "List, show, or rollback Code-X transaction manifests produced by write tools",
        "recover",
        "write",
        "write",
        _schema({
            "action": {"type": "string"},
            "transaction_id": {"type": "string"},
            "manifest": {"type": "string"},
            "manifest_path": {"type": "string"},
            "force": {"type": "boolean"},
            "limit": {"type": "integer"},
        }, ["action"]),
        "a prior write returned rollback_ref, or a committed change must be inspected or undone",
        "normal editing or validation is needed; use the original write or quality tool",
        "show the transaction first if rollback may overwrite later edits; use force only after confirming the target changed intentionally",
    ),
    CodeXToolSpec(
        "rollback_preview",
        "Preview a Code-X rollback without mutating files, showing planned actions, target drift, backup availability, and force risk",
        "recover",
        "inspect",
        "read",
        _schema({
            "transaction_id": {"type": "string"},
            "manifest": {"type": "string"},
            "manifest_path": {"type": "string"},
            "include_backup_state": {"type": "boolean"},
        }),
        "the model is considering rollback and needs to know whether target files changed after the transaction",
        "the decision to rollback has already been made and target freshness was just inspected",
        "if rollback_would_block_without_force is true, read suggested targets and avoid force unless overwrite is intentional",
    ),
    CodeXToolSpec(
        "replace_lines",
        "Apply transactional line-based edits after validating all edits first, then return rollback_ref",
        "modify",
        "write",
        "write",
        _schema({
            "path": {"type": "string"},
            "edits": {"type": "array"},
            "dry_run": {"type": "boolean"},
        }, ["path", "edits"]),
        "precise local edits need expected text protection against drift",
        "a large structural rewrite is needed; use write_file or safe_apply_patch",
        "if line numbers drift, read the file again before editing",
    ),
    CodeXToolSpec(
        "glob",
        "Find workspace files by glob pattern while skipping dependency and cache folders",
        "locate",
        "inspect",
        "read",
        _schema({
            "pattern": {"type": "string"},
            "path": {"type": "string"},
            "limit": {"type": "integer"},
        }, ["pattern"]),
        "a filename pattern or extension can quickly narrow the search space",
        "content matching is needed; use grep, or semantic_lookup for definitions",
        "if results are too broad, narrow path or pattern",
    ),
    CodeXToolSpec(
        "grep",
        "Search workspace text content, using rg first and a Python fallback if needed",
        "locate",
        "inspect",
        "read",
        _schema({
            "pattern": {"type": "string"},
            "path": {"type": "string"},
            "limit": {"type": "integer"},
            "timeout": {"type": "integer"},
        }, ["pattern"]),
        "locating text, error strings, API names, or config keys",
        "symbol-level or semantic results are needed; use symbol_search or semantic_lookup",
        "if output is noisy, narrow the path or regex",
    ),
    CodeXToolSpec(
        "bash",
        "Run a safe command in the workspace; dangerous command patterns are blocked outside danger_full mode",
        "execute",
        "quality",
        "execute",
        _schema({"command": {"type": "string"}, "timeout": {"type": "integer"}}, ["command"]),
        "project tests, builds, read-only diagnostics, or commands not covered by dedicated tools are needed",
        "direct file deletion or file editing can be done with structured tools",
        "on failure, run failure_parser or switch to a dedicated quality tool",
    ),
    CodeXToolSpec(
        "python_quality_runner",
        "Run Python syntax and compileall-style validation inside the workspace boundary",
        "verify",
        "quality",
        "execute",
        _schema({"target": {"type": "string"}, "timeout": {"type": "integer"}}),
        "a Python edit needs the smallest fast validation surface",
        "a multi-language project or npm script should be checked; use code_quality_runner",
        "on failure, read the first reported file and fix the concrete error",
    ),
    CodeXToolSpec(
        "code_quality_runner",
        "Run multi-language quality checks for Python, JS, JSON, TS, Go, Rust, and safe npm scripts",
        "verify",
        "quality",
        "execute",
        _schema({
            "target": {"type": "string"},
            "language": {"type": "string"},
            "timeout": {"type": "integer"},
            "project_scripts": {"type": "boolean"},
            "run_tests": {"type": "boolean"},
            "skip_project_scripts": {"type": "boolean"},
        }),
        "cross-language changes or project-level smoke, ensure, check, or lint validation is needed",
        "only one Python file needs syntax validation",
        "on failure, use failure_parser to extract the first actionable error",
    ),
    CodeXToolSpec(
        "codebase_map",
        "Build a project map of languages, frameworks, package managers, entry files, scripts, and key folders",
        "locate",
        "inspect",
        "read",
        _schema({"path": {"type": "string"}, "max_files": {"type": "integer"}}),
        "entering an unfamiliar project or needing a high-level structure map",
        "the exact file is already known and only content is needed",
        "if the map is too coarse, use semantic_index or grep for deeper evidence",
    ),
    CodeXToolSpec(
        "git_inspect",
        "Read Git status, diff, stat, file list, or show output without writing files",
        "evidence",
        "inspect",
        "read",
        _schema({
            "mode": {"type": "string"},
            "target": {"type": "string"},
            "max_chars": {"type": "integer"},
        }),
        "pre-edit or post-edit confirmation of real changes and existing user edits is needed",
        "the workspace is not a Git tree; do not block on that",
        "if Git is unavailable, rely on file readback and quality validation",
    ),
    CodeXToolSpec(
        "failure_parser",
        "Parse failure output into files, lines, error classes, command clues, and next-step hints",
        "recover",
        "inspect",
        "read",
        _schema({
            "text": {"type": "string"},
            "path": {"type": "string"},
            "max_items": {"type": "integer"},
        }),
        "test, build, or browser validation failed and needs a recovery entry point",
        "there is no failure output to parse",
        "use parsed findings to return to read_file, grep, or validation tools",
    ),
    CodeXToolSpec(
        "safe_apply_patch",
        "Apply a unified diff safely after git apply --check, workspace path validation, and transaction backup",
        "modify",
        "write",
        "write",
        _schema({
            "patch": {"type": "string"},
            "dry_run": {"type": "boolean"},
            "reverse": {"type": "boolean"},
        }, ["patch"]),
        "a small or multi-file standard diff is the clearest edit format",
        "a single-file line edit can use replace_lines",
        "if check fails, reread the target region and produce a smaller patch",
    ),
    CodeXToolSpec(
        "diff_guard",
        "Review a diff or patch as advisory LLM-facing evidence: patch stats, findings, suggested reads, and validation hints",
        "review",
        "inspect",
        "read",
        _schema({
            "diff": {"type": "string"},
            "patch": {"type": "string"},
            "files": {"type": "array"},
            "mode": {"type": "string"},
            "path": {"type": "string"},
            "run_tests": {"type": "boolean"},
            "url": {"type": "string"},
            "frontend_url": {"type": "string"},
            "skip_git_check": {"type": "boolean"},
        }),
        "the model wants a compact review of patch risk, readback targets, or validation hints before or after editing",
        "the patch should be applied now; use safe_apply_patch or editing tools after the model decides",
        "treat findings as advisory prompts; inspect suggested_reads before changing code further",
    ),
    CodeXToolSpec(
        "evidence_card",
        "Compress bulky tool output, text, diffs, or file snippets into short advisory evidence cards for the model",
        "evidence",
        "inspect",
        "read",
        _schema({
            "content": {"type": "string"},
            "text": {"type": "string"},
            "output": {"type": "string"},
            "diff": {"type": "string"},
            "patch": {"type": "string"},
            "tool_name": {"type": "string"},
            "source_tool": {"type": "string"},
            "files": {"type": "array"},
            "paths": {"type": "array"},
            "path": {"type": "string"},
            "focus": {"type": "string"},
            "query": {"type": "string"},
            "max_cards": {"type": "integer"},
            "max_signals": {"type": "integer"},
            "max_context_chars": {"type": "integer"},
            "max_file_chars": {"type": "integer"},
        }),
        "the model has bulky evidence and needs a compact lossy reminder, risks, and suggested readback targets",
        "exact wording, full logs, or complete source must be preserved; use read_file or the original tool output",
        "treat compact_context as lossy; read suggested_reads before editing or final claims",
    ),
    CodeXToolSpec(
        "readback_verifier",
        "Verify readback evidence after edits by checking expected text, forbidden text, regexes, sha256, or diff-derived lines",
        "readback",
        "readback",
        "read",
        _schema({
            "path": {"type": "string"},
            "paths": {"type": "array"},
            "files": {"type": "array"},
            "checks": {"type": "array"},
            "expected": {"type": "string"},
            "expected_text": {"type": "string"},
            "expected_texts": {"type": "array"},
            "contains": {"type": "string"},
            "must_contain": {"type": "string"},
            "absent": {"type": "string"},
            "absent_text": {"type": "string"},
            "absent_texts": {"type": "array"},
            "forbidden": {"type": "string"},
            "must_not_contain": {"type": "string"},
            "expected_regex": {"type": "string"},
            "expected_regexes": {"type": "array"},
            "absent_regex": {"type": "string"},
            "absent_regexes": {"type": "array"},
            "forbidden_regex": {"type": "string"},
            "sha256": {"type": "string"},
            "expected_sha256": {"type": "string"},
            "sha256_by_file": {"type": "object"},
            "diff": {"type": "string"},
            "patch": {"type": "string"},
            "max_diff_lines_per_file": {"type": "integer"},
        }),
        "the model has edited files and wants structured readback evidence before deciding the next validation or fix",
        "behavioral correctness must be proven; use quality tools or browser_verify after readback",
        "treat readback_status as advisory; inspect suggested_reads and run validation before final claims",
    ),
    CodeXToolSpec(
        "impact_analyzer",
        "Summarize changed-file impact, related symbols/files, advisory risk notes, and tool-ready validation suggestions",
        "verify_plan",
        "inspect",
        "read",
        _schema({
            "files": {"type": "array"},
            "diff": {"type": "string"},
            "patch": {"type": "string"},
            "path": {"type": "string"},
            "max_files": {"type": "integer"},
            "max_related": {"type": "integer"},
            "run_tests": {"type": "boolean"},
            "url": {"type": "string"},
            "frontend_url": {"type": "string"},
        }),
        "the model needs a compact advisory map before choosing inspections, edits, or validation",
        "the exact test command is already known and should be executed now",
        "treat llm_brief, next_actions, and validation_plan as suggestions; inspect evidence before broad edits",
    ),
    CodeXToolSpec(
        "test_selector",
        "Suggest validation commands from changed files without executing them, using impact analysis when available",
        "verify_plan",
        "inspect",
        "read",
        _schema({
            "files": {"type": "array"},
            "include_project_scripts": {"type": "boolean"},
            "run_tests": {"type": "boolean"},
        }),
        "the right validation surface is unclear",
        "the exact validation command is already known or should be executed now",
        "after suggestions, execute validation with quality tools or bash",
    ),
    CodeXToolSpec(
        "symbol_search",
        "Lightweight symbol search for functions, classes, references, and imports",
        "locate",
        "inspect",
        "read",
        _schema({
            "query": {"type": "string"},
            "kind": {"type": "string"},
            "path": {"type": "string"},
            "language": {"type": "string"},
            "limit": {"type": "integer"},
        }, ["query"]),
        "function, class, or import names can quickly locate the relevant code",
        "cross-file call impact is needed; use call_graph",
        "if results are weak, cross-check with semantic_lookup or grep",
    ),
    CodeXToolSpec(
        "dependency_probe",
        "Probe local code-tool dependencies, package scripts, Playwright availability, and Chromium startup",
        "environment",
        "inspect",
        "read",
        _schema(),
        "starting frontend, browser, or Node project work",
        "the task is pure text or the environment was already verified",
        "if dependencies are missing, do not fake browser validation; report the gap or use available checks",
    ),
    CodeXToolSpec(
        "frontend_devserver",
        "Plan, start, probe, status-check, or stop a frontend dev server using safe package scripts",
        "verify",
        "quality",
        "execute",
        _schema({
            "action": {"type": "string"},
            "script": {"type": "string"},
            "url": {"type": "string"},
            "port": {"type": "integer"},
            "timeout": {"type": "integer"},
        }),
        "real page validation is needed; normally plan or probe before start",
        "backend-only or pure algorithm changes do not need a browser surface",
        "if startup fails, inspect scripts and ports, then rerun dependency_probe",
    ),
    CodeXToolSpec(
        "browser_verify",
        "Use a real browser to collect console errors, page errors, request failures, DOM summary, and screenshots",
        "verify",
        "quality",
        "execute",
        _schema({
            "url": {"type": "string"},
            "actions": {"type": "array"},
            "screenshot": {"type": "boolean"},
            "timeout": {"type": "integer"},
            "viewport": {"type": "object"},
        }, ["url"]),
        "frontend interaction, visual state, dialogs, QR codes, or console behavior must be verified",
        "there is no reachable URL or Playwright is unavailable",
        "combine console, pageerror, request failure, and screenshot evidence to return to code",
    ),
    CodeXToolSpec(
        "semantic_index",
        "Build an advisory LLM-facing code map with file_cards, symbol_cards, import_graph, symbols, imports, and calls",
        "understand",
        "inspect",
        "read",
        _schema({
            "path": {"type": "string"},
            "max_files": {"type": "integer"},
            "max_symbols": {"type": "integer"},
            "max_calls": {"type": "integer"},
            "max_cards": {"type": "integer"},
            "max_symbol_cards": {"type": "integer"},
            "max_import_edges": {"type": "integer"},
        }),
        "the model needs compact structural evidence before reading or editing code",
        "only one term needs to be found; use grep or symbol_search first",
        "if the index is too large, narrow path or max_files; treat cards as evidence, not decisions",
    ),
    CodeXToolSpec(
        "semantic_lookup",
        "Look up definitions, imports, calls, and references, returning suggested read hints for the model",
        "understand",
        "inspect",
        "read",
        _schema({
            "query": {"type": "string"},
            "kind": {"type": "string"},
            "path": {"type": "string"},
            "match": {"type": "string"},
            "limit": {"type": "integer"},
        }, ["query"]),
        "an advisory lower-noise definition, import, call, or reference search is needed",
        "a complete call hierarchy is needed; use call_graph",
        "if no match appears, try contains matching or a wider path; read suggested locations before editing",
    ),
    CodeXToolSpec(
        "call_graph",
        "Build an advisory local call graph with related files and model read hints",
        "understand",
        "inspect",
        "read",
        _schema({
            "symbol": {"type": "string"},
            "direction": {"type": "string"},
            "depth": {"type": "integer"},
            "path": {"type": "string"},
            "limit": {"type": "integer"},
        }, ["symbol"]),
        "an advisory caller or callee map is needed before the model decides what to read or edit",
        "only a definition location is needed; use semantic_lookup or symbol_search",
        "if the graph is empty, confirm the symbol short name and path",
    ),
    CodeXToolSpec(
        "work_log_write",
        "Write the current Code-X work log with goal, changes, validation, residual risks, and next step",
        "context",
        "report",
        "write",
        _schema({"content": {"type": "string"}}, ["content"]),
        "all business steps are complete and done is about to be reported",
        "the task is still unverified or recovering from failure",
        "if writing fails, report the path problem instead of pretending the log exists",
    ),
    CodeXToolSpec(
        "task",
        "Dispatch a read-only investigation subtask to an independent sub-agent; mainline owns final edits",
        "parallel_inspect",
        "inspect",
        "read",
        _schema({
            "subtask": {"type": "string"},
            "max_turns": {"type": "integer"},
            "allow_write": {"type": "boolean"},
        }, ["subtask"]),
        "several independent evidence-gathering branches can run in parallel",
        "the task is tiny or the mainline must edit files directly",
        "mainline must review subtask evidence before making edits",
    ),
)


_SPEC_BY_NAME = {spec.name: spec for spec in CODEX_TOOL_SPECS}


def codex_tool_descriptions() -> dict[str, str]:
    return {spec.name: spec.llm_description() for spec in CODEX_TOOL_SPECS}


def codex_tool_usage_cards() -> dict[str, str]:
    return {spec.name: spec.usage_card() for spec in CODEX_TOOL_SPECS}


def codex_tool_schemas() -> dict[str, dict[str, Any]]:
    return {spec.name: spec.schema for spec in CODEX_TOOL_SPECS}


def codex_tool_names() -> tuple[str, ...]:
    return tuple(spec.name for spec in CODEX_TOOL_SPECS)


def codex_tool_names_by_substep(substep: str) -> set[str]:
    normalized = str(substep or "").strip().lower()
    return {spec.name for spec in CODEX_TOOL_SPECS if spec.substep == normalized}


def codex_default_substep(tool_name: str, fallback: str = "inspect") -> str:
    spec = _SPEC_BY_NAME.get(str(tool_name or ""))
    return spec.substep if spec else fallback


def codex_quality_tools() -> set[str]:
    return codex_tool_names_by_substep("quality")


def codex_write_tools() -> set[str]:
    return codex_tool_names_by_substep("write")


def codex_readback_tools() -> set[str]:
    return codex_tool_names_by_substep("readback")


def codex_inspect_tools() -> set[str]:
    return codex_tool_names_by_substep("inspect")
