"""Durable Code-X stage journal and knowledge-base projection.

The journal is an insurance layer for long code tasks. It stores complete
planning artifacts on disk, writes lightweight event logs, and mirrors stage
content into the local document knowledge base so a later resume can follow the
same route instead of planning from scratch.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from .document_context_store import context_dir, safe_text
except ImportError:  # pragma: no cover - supports direct script-style imports
    from document_context_store import context_dir, safe_text  # type: ignore


JOURNAL_SCHEMA = "tiangong.codex.stage_journal.v1"
PLANNING_SNAPSHOT_SCHEMA = "tiangong.codex.planning_snapshot.v1"
KB_CONTEXT_SCHEMA = "tiangong.l6_72_45.document_context.v1"
JOURNAL_REL = Path(".linyuanzhe") / "codex_stage_logs"
CODEX_KB_FOLDER = "code-x"
LATEST_PLANNING_REL = Path(".linyuanzhe") / "codex_latest_planning_snapshot.json"
EVENTS_NAME = "events.jsonl"
PLANNING_STAGES = {
    "macro": "macro_plan",
    "structure": "structure_plan",
    "detail": "detailed_steps",
    "structured": "structured_plan",
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _digest(value: Any, length: int = 16) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:length]


def _slug(value: Any, limit: int = 56) -> str:
    text = safe_text(value, 160).lower()
    text = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff._-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-._")
    return (text or "untitled")[:limit].strip("-._") or "untitled"


def new_run_id(task: str = "") -> str:
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    return f"codex_{stamp}_{_slug(task, 48)}_{_digest(task, 10)}"


def _archive_parts(run_id: str, task: str = "") -> tuple[str, str]:
    run = safe_text(run_id, 180)
    match = re.match(r"^codex_(\d{8})_\d{6}_(.+)_[0-9a-f]{10}$", run)
    if match:
        raw_date = match.group(1)
        date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
        return date, _slug(task or match.group(2))
    return datetime.now().astimezone().strftime("%Y-%m-%d"), _slug(task)


def _root(workspace: str | Path, run_id: str, task: str = "") -> Path:
    date, task_slug = _archive_parts(run_id, task)
    root = Path(workspace).expanduser().resolve() / JOURNAL_REL / date / task_slug / safe_text(run_id, 180)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _codex_kb_root(workspace: str | Path, run_id: str, task: str = "") -> Path:
    date, task_slug = _archive_parts(run_id, task)
    root = context_dir(workspace) / CODEX_KB_FOLDER / date / task_slug / safe_text(run_id, 180)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _codex_kb_index_path(workspace: str | Path) -> Path:
    root = context_dir(workspace) / CODEX_KB_FOLDER
    root.mkdir(parents=True, exist_ok=True)
    return root / "index.json"


def _load_codex_kb_index(workspace: str | Path) -> dict[str, Any]:
    path = _codex_kb_index_path(workspace)
    if not path.exists():
        return {"schema": "tiangong.codex.knowledge_index.v1", "documents": {}, "last_document_id": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("schema", "tiangong.codex.knowledge_index.v1")
            data.setdefault("documents", {})
            data.setdefault("last_document_id", "")
            return data
    except Exception:
        pass
    return {"schema": "tiangong.codex.knowledge_index.v1", "documents": {}, "last_document_id": ""}


def _save_codex_kb_index(workspace: str | Path, index: dict[str, Any]) -> None:
    payload = {
        "schema": "tiangong.codex.knowledge_index.v1",
        "updated_at": _now(),
        "last_document_id": safe_text(index.get("last_document_id"), 120),
        "documents": dict(index.get("documents") or {}),
    }
    _atomic_write_json(_codex_kb_index_path(workspace), payload)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, default=str))
        fh.write("\n")


def _chunk_text(text: str, size: int = 1400) -> list[str]:
    source = str(text or "")
    if not source:
        return []
    return [source[index:index + size] for index in range(0, len(source), size)]


def _knowledge_doc_id(run_id: str, stage: str) -> str:
    return f"codex_stage_{_digest({'run_id': run_id, 'stage': stage}, 18)}"


def _index_stage_to_knowledge(workspace: str | Path, run_id: str, stage: str, title: str, content: Any, payload: dict[str, Any]) -> str:
    body = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2, default=str)
    if not str(body).strip():
        body = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    document_id = _knowledge_doc_id(run_id, stage)
    blocks = []
    for index, chunk in enumerate(_chunk_text(str(body)), start=1):
        blocks.append({
            "local_id": f"stage{index}",
            "kind": "codex_stage_log",
            "title": safe_text(f"{title} part {index}", 160),
            "text": safe_text(chunk, 1600),
            "meta": {"run_id": run_id, "stage": stage, "part": index},
            "citation_id": f"{document_id}#stage{index}",
        })
    if not blocks:
        blocks.append({
            "local_id": "stage1",
            "kind": "codex_stage_log",
            "title": safe_text(title, 160),
            "text": safe_text(json.dumps(payload, ensure_ascii=False, default=str), 1600),
            "meta": {"run_id": run_id, "stage": stage, "part": 1},
            "citation_id": f"{document_id}#stage1",
        })
    ctx = {
        "schema": KB_CONTEXT_SCHEMA,
        "document_id": document_id,
        "created_at": _now(),
        "metadata": {
            "file_name": safe_text(f"Code-X {stage} stage log", 180),
            "file_path_digest": _digest({"run_id": run_id, "stage": stage}),
            "file_path": str(_root(workspace, run_id, str(payload.get("task") or "")) / f"{safe_text(stage, 80)}.json"),
            "file_type": "codex_stage_log",
            "suffix": ".json",
            "parser": "codex_stage_journal",
            "status": "indexed",
            "size_bytes": len(str(body).encode("utf-8", errors="ignore")),
            "summary": safe_text(f"Code-X stage={stage}; run_id={run_id}; complete planning/log content is mirrored in blocks.", 600),
        },
        "blocks": blocks,
        "search_text_digest": _digest("\n".join(block["text"] for block in blocks), 20),
        "raw_bytes_hidden": True,
        "safe_projection_only": True,
    }
    kb_dir = _codex_kb_root(workspace, run_id, str(payload.get("task") or ""))
    _atomic_write_json(kb_dir / f"{document_id}.json", ctx)
    index = _load_codex_kb_index(workspace)
    docs = dict(index.get("documents") or {})
    docs[document_id] = {
        "file_name": ctx["metadata"]["file_name"],
        "file_type": "codex_stage_log",
        "parser": "codex_stage_journal",
        "created_at": ctx["created_at"],
        "citation_count": len(blocks),
        "file_path_digest": ctx["metadata"]["file_path_digest"],
        "archive_date": _archive_parts(run_id, str(payload.get("task") or ""))[0],
        "task_slug": _archive_parts(run_id, str(payload.get("task") or ""))[1],
        "context_path": str(kb_dir / f"{document_id}.json"),
    }
    index["documents"] = docs
    index["last_document_id"] = document_id
    _save_codex_kb_index(workspace, index)
    return document_id


def _load_latest_snapshot(workspace: str | Path) -> dict[str, Any]:
    path = Path(workspace).expanduser().resolve() / LATEST_PLANNING_REL
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_latest_planning_snapshot(workspace: str | Path) -> dict[str, Any]:
    return _load_latest_snapshot(workspace)


def _save_latest_planning_snapshot(workspace: str | Path, run_id: str, task: str, stage: str, plans: dict[str, Any]) -> None:
    path = Path(workspace).expanduser().resolve() / LATEST_PLANNING_REL
    prior = _load_latest_snapshot(workspace)
    merged_plans = dict(prior.get("plans") or {})
    merged_plans.update({key: value for key, value in plans.items() if value not in (None, "")})
    snapshot = {
        "schema": PLANNING_SNAPSHOT_SCHEMA,
        "run_id": run_id,
        "task": task or prior.get("task") or "",
        "updated_at": _now(),
        "current_stage": stage,
        "plans": merged_plans,
    }
    _atomic_write_json(path, snapshot)


def write_stage_log(
    workspace: str | Path,
    run_id: str,
    stage: str,
    *,
    task: str = "",
    content: Any = "",
    plans: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    index_knowledge: bool = True,
) -> dict[str, Any]:
    stage_key = safe_text(stage, 80) or "stage"
    root = _root(workspace, run_id, task)
    payload = {
        "schema": JOURNAL_SCHEMA,
        "kind": "stage",
        "run_id": run_id,
        "stage": stage_key,
        "task": task,
        "created_at": _now(),
        "content": content,
        "plans": dict(plans or {}),
        "extra": dict(extra or {}),
    }
    target = root / f"{stage_key}.json"
    _atomic_write_json(target, payload)
    _append_jsonl(root / EVENTS_NAME, {
        "schema": JOURNAL_SCHEMA,
        "kind": "stage",
        "run_id": run_id,
        "stage": stage_key,
        "at": payload["created_at"],
        "path": str(target),
    })
    kb_id = ""
    if index_knowledge:
        kb_id = _index_stage_to_knowledge(workspace, run_id, stage_key, f"Code-X {stage_key}", content, payload)
    if stage_key in PLANNING_STAGES:
        plan_key = PLANNING_STAGES[stage_key]
        _save_latest_planning_snapshot(workspace, run_id, task, stage_key, {plan_key: content, **dict(plans or {})})
    elif stage_key in {"planning_resume", "planning_snapshot", "terminal"} and plans:
        _save_latest_planning_snapshot(workspace, run_id, task, stage_key, dict(plans))
    return {"ok": True, "path": str(target), "knowledge_document_id": kb_id, "run_id": run_id, "stage": stage_key}


def write_tool_step_log(
    workspace: str | Path,
    run_id: str,
    step: dict[str, Any],
    *,
    progress_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = _root(workspace, run_id)
    payload = {
        "schema": JOURNAL_SCHEMA,
        "kind": "tool_step",
        "run_id": run_id,
        "created_at": _now(),
        "step": dict(step or {}),
        "progress_snapshot": dict(progress_snapshot or {}),
    }
    _append_jsonl(root / "tool_steps.jsonl", payload)
    _append_jsonl(root / EVENTS_NAME, {
        "schema": JOURNAL_SCHEMA,
        "kind": "tool_step",
        "run_id": run_id,
        "at": payload["created_at"],
        "tool_name": payload["step"].get("tool_name"),
        "ok": payload["step"].get("ok"),
        "step_id": payload["step"].get("step_id"),
    })
    _atomic_write_json(root / "latest_checkpoint.json", payload)
    return {"ok": True, "run_id": run_id, "path": str(root / "tool_steps.jsonl")}


def write_terminal_log(
    workspace: str | Path,
    run_id: str,
    *,
    status: str,
    summary: str = "",
    error: str = "",
    plans: dict[str, Any] | None = None,
    progress_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return write_stage_log(
        workspace,
        run_id,
        "terminal",
        content={
            "status": status,
            "summary": summary,
            "error": error,
            "progress_snapshot": dict(progress_snapshot or {}),
        },
        plans=dict(plans or {}),
        extra={"terminal_status": status},
        index_knowledge=True,
    )
