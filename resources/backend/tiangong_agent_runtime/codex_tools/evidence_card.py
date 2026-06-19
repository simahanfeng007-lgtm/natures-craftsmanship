from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .common import ToolInputError, bounded_int, json_output, resolve_workspace_path, safe_rel, workspace_root


EVIDENCE_CARD_SCHEMA = "tiangong.codex.evidence_card.v1"

PATH_RE = re.compile(
    r"(?P<path>(?:[A-Za-z0-9_.-]+[\\/])+[A-Za-z0-9_.-]+\."
    r"(?:py|pyw|js|mjs|cjs|ts|tsx|jsx|json|yaml|yml|toml|md|txt|html|css|vue|svelte|go|rs|java|kt|cs|cpp|c|h|hpp))"
    r"(?::(?P<line>\d{1,7}))?"
)
SECRET_RE = re.compile(r"(?i)(api[_-]?key|secret|token|password|private[_-]?key)\s*[:=]")
ERROR_RE = re.compile(r"(?i)(traceback|exception|error|failed|failure|assertionerror|syntaxerror|typeerror|modulenotfounderror)")
WARN_RE = re.compile(r"(?i)(warning|deprecated|skipped|truncated|timeout|retry)")
SUCCESS_RE = re.compile(r"(?i)(\bok\b|passed|success|succeeded|completed|validated|verified)")
DEBUG_RE = re.compile(r"\b(print|console\.log|debugger|pdb\.set_trace|breakpoint)\b")
DEF_RE = re.compile(r"^\s*(def |class |async def |function |export function |export class |const |let |var |interface |type )")


def _as_list(value: Any) -> list[str]:
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


def _try_json(text: str) -> Any | None:
    raw = str(text or "").strip()
    if not raw or raw[-14:] == "...[truncated]":
        raw = raw.replace("\n...[truncated]", "").strip()
    if not raw or raw[0] not in "[{":
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _severity_for_line(line: str) -> tuple[str, str]:
    if SECRET_RE.search(line):
        return "high", "possible_secret"
    if ERROR_RE.search(line) or '"ok": false' in line.lower():
        return "high", "error_signal"
    if DEBUG_RE.search(line):
        return "medium", "debug_signal"
    if WARN_RE.search(line):
        return "medium", "warning_signal"
    if SUCCESS_RE.search(line):
        return "info", "success_signal"
    return "info", "context_signal"


def _collect_signals(text: str, *, focus: str = "", max_signals: int = 16) -> list[dict[str, Any]]:
    focus_terms = [term.lower() for term in re.split(r"\s+", str(focus or "")) if len(term.strip()) >= 3]
    signals: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_no, line in enumerate(str(text or "").splitlines(), start=1):
        trimmed = line.strip()
        if not trimmed:
            continue
        interesting = (
            ERROR_RE.search(trimmed)
            or WARN_RE.search(trimmed)
            or SECRET_RE.search(trimmed)
            or DEBUG_RE.search(trimmed)
            or SUCCESS_RE.search(trimmed)
            or any(term in trimmed.lower() for term in focus_terms)
        )
        if not interesting:
            continue
        severity, kind = _severity_for_line(trimmed)
        key = f"{kind}:{_short(trimmed, 160)}"
        if key in seen:
            continue
        seen.add(key)
        signals.append({
            "severity": severity,
            "kind": kind,
            "line": line_no,
            "text": _short(trimmed, 260),
        })
        if len(signals) >= max_signals:
            break
    return signals


def _path_reads(root: Path, text: str, *, max_items: int = 24) -> list[dict[str, Any]]:
    reads: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for match in PATH_RE.finditer(str(text or "")):
        raw = match.group("path").replace("\\", "/")
        line_no = int(match.group("line") or 1)
        try:
            path = resolve_workspace_path(root, raw, must_exist=False)
            rel = safe_rel(path, root)
        except ToolInputError:
            rel = raw
        key = (rel, line_no)
        if key in seen:
            continue
        seen.add(key)
        reads.append({
            "file": rel,
            "line": max(1, line_no),
            "read_hint": f"{rel}:{max(1, line_no)}",
            "reason": "path mentioned in evidence",
        })
        if len(reads) >= max_items:
            break
    return reads


def _summarize_json(payload: Any, *, max_cards: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    cards: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    reads: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        schema = str(payload.get("schema") or payload.get("type") or "json")
        brief = payload.get("llm_brief") or payload.get("summary") or payload.get("status") or payload.get("message") or ""
        cards.append({
            "kind": "json_summary",
            "title": schema,
            "summary": _short(brief or f"JSON object with {len(payload)} top-level keys.", 360),
            "evidence_refs": [schema],
            "confidence": "medium",
        })
        for item in list(payload.get("findings") or payload.get("risk_notes") or [])[: max_cards]:
            if not isinstance(item, dict):
                continue
            signals.append({
                "severity": str(item.get("severity") or "info"),
                "kind": str(item.get("kind") or "finding"),
                "file": str(item.get("file") or ""),
                "line": int(item.get("line") or 0),
                "text": _short(item.get("message") or item.get("summary") or item, 260),
            })
        for item in list(payload.get("suggested_reads") or payload.get("related_files") or [])[: max_cards * 2]:
            if isinstance(item, dict):
                file_name = str(item.get("file") or item.get("path") or "")
                if file_name:
                    reads.append({
                        "file": file_name,
                        "line": int(item.get("line") or item.get("start_line") or 1),
                        "read_hint": str(item.get("read_hint") or f"{file_name}:{int(item.get('line') or 1)}"),
                        "reason": str(item.get("reason") or "suggested by source tool"),
                    })
            elif isinstance(item, str):
                reads.append({"file": item, "line": 1, "read_hint": f"{item}:1", "reason": "suggested by source tool"})
        commands = ((payload.get("validation_hints") or payload.get("validation_plan") or {}) or {}).get("commands")
        if commands:
            cards.append({
                "kind": "validation_hint",
                "title": "Validation hints",
                "summary": _short(commands[:3], 420),
                "evidence_refs": ["validation_hints.commands"],
                "confidence": "medium",
            })
    elif isinstance(payload, list):
        cards.append({
            "kind": "json_summary",
            "title": "JSON list",
            "summary": f"JSON list with {len(payload)} item(s). First item: {_short(payload[0] if payload else '', 260)}",
            "evidence_refs": ["json[0]"],
            "confidence": "low",
        })
    return cards[:max_cards], signals, reads


def _diff_card(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if "diff --git " not in text and not re.search(r"^@@\s+-\d+", text, re.MULTILINE):
        return [], []
    try:
        from . import diff_guard

        parsed = diff_guard.parse_diff(text)
    except Exception:
        return [{
            "kind": "diff_summary",
            "title": "Unified diff",
            "summary": "Diff-like text detected, but compact diff parsing was unavailable.",
            "evidence_refs": ["diff"],
            "confidence": "low",
        }], []
    cards = [{
        "kind": "diff_summary",
        "title": "Patch footprint",
        "summary": (
            f"{len(parsed.get('touched_paths', []))} file(s), "
            f"{parsed.get('additions', 0)} addition(s), {parsed.get('deletions', 0)} deletion(s), "
            f"{parsed.get('hunks', 0)} hunk(s)."
        ),
        "evidence_refs": list(parsed.get("touched_paths", []))[:8],
        "confidence": "medium",
    }]
    reads = list(parsed.get("suggested_reads") or [])[:24]
    return cards, reads


def _file_cards(root: Path, files: list[str], *, focus: str, max_cards: int, max_file_chars: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    cards: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    reads: list[dict[str, Any]] = []
    for raw in files[:max_cards]:
        try:
            path = resolve_workspace_path(root, raw, must_exist=True)
        except ToolInputError as exc:
            signals.append({"severity": "medium", "kind": "file_unavailable", "text": str(exc), "line": 0})
            continue
        if not path.is_file():
            signals.append({"severity": "medium", "kind": "not_a_file", "file": safe_rel(path, root), "line": 0, "text": "Path is not a file."})
            continue
        try:
            data = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            signals.append({"severity": "medium", "kind": "read_failed", "file": safe_rel(path, root), "line": 0, "text": f"{type(exc).__name__}: {exc}"})
            continue
        rel = safe_rel(path, root)
        snippet = data[:max_file_chars]
        anchors: list[str] = []
        focus_terms = [term.lower() for term in re.split(r"\s+", str(focus or "")) if len(term.strip()) >= 3]
        for line_no, line in enumerate(data.splitlines(), start=1):
            if DEF_RE.search(line) or any(term in line.lower() for term in focus_terms):
                anchors.append(f"{line_no}: {_short(line, 120)}")
            if len(anchors) >= 8:
                break
        cards.append({
            "kind": "file_summary",
            "title": rel,
            "summary": f"{len(data.splitlines())} line(s), {len(data)} char(s), sha256={hashlib.sha256(data.encode('utf-8', 'replace')).hexdigest()[:16]}.",
            "evidence_refs": anchors or [f"{rel}:1"],
            "confidence": "medium",
        })
        reads.append({"file": rel, "line": 1, "read_hint": f"{rel}:1", "reason": "input file"})
        signals.extend(_collect_signals(snippet, focus=focus, max_signals=6))
    return cards, signals, reads


def _make_compact_context(cards: list[dict[str, Any]], signals: list[dict[str, Any]], reads: list[dict[str, Any]], *, max_chars: int) -> str:
    lines = ["Evidence card (lossy, advisory):"]
    for idx, card in enumerate(cards[:8], start=1):
        lines.append(f"{idx}. {card.get('kind')}: {_short(card.get('title'), 80)} - {_short(card.get('summary'), 240)}")
    for signal in signals[:8]:
        loc = ""
        if signal.get("file"):
            loc = f" {signal.get('file')}:{signal.get('line') or 1}"
        lines.append(f"- {signal.get('severity')}/{signal.get('kind')}{loc}: {_short(signal.get('text'), 220)}")
    if reads:
        hints = ", ".join(str(item.get("read_hint") or item.get("file")) for item in reads[:8])
        lines.append(f"Suggested reads: {hints}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        return text[: max(0, max_chars - 18)].rstrip() + "\n...[card truncated]"
    return text


def analyze(workspace: str | Path, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    root = workspace_root(workspace)
    content = str(args.get("content") or args.get("text") or args.get("output") or args.get("diff") or args.get("patch") or "")
    source_tool = str(args.get("tool_name") or args.get("source_tool") or "").strip()
    focus = str(args.get("focus") or args.get("query") or "").strip()
    max_cards = bounded_int(args.get("max_cards"), 8, 1, 24)
    max_signals = bounded_int(args.get("max_signals"), 16, 1, 48)
    max_context_chars = bounded_int(args.get("max_context_chars"), 2400, 600, 8000)
    max_file_chars = bounded_int(args.get("max_file_chars"), 12000, 500, 60000)
    cards: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    reads: list[dict[str, Any]] = []
    warnings: list[str] = []

    parsed_json = _try_json(content)
    if parsed_json is not None:
        json_cards, json_signals, json_reads = _summarize_json(parsed_json, max_cards=max_cards)
        cards.extend(json_cards)
        signals.extend(json_signals)
        reads.extend(json_reads)
    if content:
        diff_cards, diff_reads = _diff_card(content)
        cards.extend(diff_cards)
        reads.extend(diff_reads)
        signals.extend(_collect_signals(content, focus=focus, max_signals=max_signals))
        reads.extend(_path_reads(root, content))
        if not cards:
            lines = [line for line in content.splitlines() if line.strip()]
            cards.append({
                "kind": "text_summary",
                "title": source_tool or "raw_text",
                "summary": f"{len(lines)} non-empty line(s), {len(content)} char(s). First useful line: {_short(lines[0] if lines else '', 260)}",
                "evidence_refs": [source_tool or "content"],
                "confidence": "low",
            })

    file_args = _as_list(args.get("files") or args.get("paths") or args.get("path"))
    if file_args:
        file_cards, file_signals, file_reads = _file_cards(root, file_args, focus=focus, max_cards=max_cards, max_file_chars=max_file_chars)
        cards.extend(file_cards)
        signals.extend(file_signals)
        reads.extend(file_reads)

    if not cards and not signals and not reads:
        warnings.append("[EMPTY_INPUT] provide content/text/output/diff/patch or files")
        cards.append({
            "kind": "empty",
            "title": "No evidence input",
            "summary": "No text or file evidence was provided.",
            "evidence_refs": [],
            "confidence": "low",
        })

    unique_reads: list[dict[str, Any]] = []
    seen_reads: set[tuple[str, int]] = set()
    for item in reads:
        file_name = str(item.get("file") or "")
        line_no = int(item.get("line") or 1)
        key = (file_name, line_no)
        if file_name and key not in seen_reads:
            seen_reads.add(key)
            unique_reads.append({**item, "line": max(1, line_no), "read_hint": str(item.get("read_hint") or f"{file_name}:{max(1, line_no)}")})

    high_count = sum(1 for item in signals if str(item.get("severity")) == "high")
    medium_count = sum(1 for item in signals if str(item.get("severity")) == "medium")
    cards = cards[:max_cards]
    signals = signals[:max_signals]
    compact_context = _make_compact_context(cards, signals, unique_reads, max_chars=max_context_chars)
    next_actions = [
        "Use compact_context as a lossy reminder, not as a replacement for reading source evidence.",
        "Read suggested_reads before editing or making a final claim.",
    ]
    if high_count:
        next_actions.insert(0, "Resolve high-severity evidence signals before continuing broad edits.")
    if medium_count and not high_count:
        next_actions.insert(0, "Review medium-severity evidence signals before final validation.")
    if content and len(content) > max_context_chars:
        next_actions.append("Keep the original output available if exact wording or line numbers matter.")

    return {
        "schema": EVIDENCE_CARD_SCHEMA,
        "ok": True,
        "advisory_only": True,
        "lossy_summary": True,
        "source_tool": source_tool,
        "source_sha256": hashlib.sha256(content.encode("utf-8", "replace")).hexdigest() if content else "",
        "llm_brief": f"{len(cards)} card(s), {len(signals)} signal(s), {len(unique_reads)} suggested read(s), high={high_count}, medium={medium_count}",
        "cards": cards,
        "signals": signals,
        "suggested_reads": unique_reads[:48],
        "compact_context": compact_context,
        "next_actions": next_actions,
        "warnings": warnings,
    }


def run_text(workspace: str | Path, args: dict[str, Any] | None = None) -> str:
    return json_output(analyze(workspace, args), limit=20000)
