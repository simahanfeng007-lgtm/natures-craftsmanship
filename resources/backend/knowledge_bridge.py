"""Electron bridge for the local document knowledge base.

The bridge keeps the desktop UI thin: Electron sends one JSON payload to stdin,
and the existing document context store performs parsing, indexing, querying,
exporting, and safe index removal.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from tiangong_agent_runtime.document_context_store import (
    context_dir,
    load_document_context,
    load_index,
    public_context_payload,
    query_document_context,
    render_context_markdown,
    render_context_text,
    safe_text,
    save_document_context,
    save_index,
)
from tiangong_agent_runtime.document_parser import parse_document, should_route_to_document_parse
from tiangong_agent_runtime.physical_commit import write_text_atomic_verified


def _json_out(payload: dict[str, Any]) -> int:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")
    return 0 if payload.get("ok") else 1


def _read_stdin() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _clean_document_id(value: Any) -> str:
    return safe_text(value, 100)


def _context_file(workspace: Path, document_id: str) -> Path:
    clean = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(document_id or ""))[:80]
    return context_dir(workspace) / f"{clean}.json"


def _entry_from_context(workspace: Path, document_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    ctx = load_document_context(workspace, document_id=document_id)
    metadata = dict(ctx.get("metadata") or {}) if ctx else {}
    blocks = ctx.get("blocks") or [] if ctx else []
    merged = {
        "document_id": document_id,
        "file_name": entry.get("file_name") or metadata.get("file_name") or document_id,
        "file_type": entry.get("file_type") or metadata.get("file_type") or "",
        "suffix": metadata.get("suffix") or "",
        "parser": entry.get("parser") or metadata.get("parser") or "",
        "status": metadata.get("status") or "",
        "created_at": entry.get("created_at") or ctx.get("created_at") if ctx else entry.get("created_at", ""),
        "citation_count": int(entry.get("citation_count") or len(blocks) or 0),
        "size_bytes": int(metadata.get("size_bytes") or 0),
        "summary": metadata.get("summary") or "",
        "file_path": metadata.get("file_path") or "",
        "file_path_digest": entry.get("file_path_digest") or metadata.get("file_path_digest") or "",
        "safe_projection_only": True,
        "raw_bytes_hidden": True,
    }
    return merged


def _knowledge_list(workspace: Path) -> dict[str, Any]:
    index = load_index(workspace)
    docs = index.get("documents") or {}
    documents = [
        _entry_from_context(workspace, str(document_id), entry if isinstance(entry, dict) else {})
        for document_id, entry in docs.items()
    ]
    documents.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return {
        "ok": True,
        "workspace": str(workspace),
        "index_path": str(context_dir(workspace) / "index.json"),
        "count": len(documents),
        "last_document_id": index.get("last_document_id") or "",
        "documents": documents,
    }


def _knowledge_import(workspace: Path, payload: dict[str, Any]) -> dict[str, Any]:
    paths = payload.get("paths") or []
    if isinstance(paths, (str, Path)):
        paths = [paths]
    imported: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for raw_path in paths:
        target = Path(str(raw_path or "")).expanduser()
        try:
            if not target.exists() or not target.is_file():
                failed.append({"path": str(target), "error": "文件不存在"})
                continue
            if not should_route_to_document_parse(target):
                failed.append({"path": str(target), "error": "不支持的文件类型"})
                continue
            parsed = parse_document(target, max_chars=int(payload.get("max_chars") or 12000))
            ctx, enriched = save_document_context(workspace, parsed)
            imported.append(
                {
                    "document_id": ctx.get("document_id"),
                    "file_name": enriched.get("file_name") or target.name,
                    "file_path": str(target.resolve()),
                    "size_bytes": target.stat().st_size,
                    "status": enriched.get("status"),
                    "parser": enriched.get("parser"),
                    "summary": enriched.get("summary"),
                    "citation_count": len(ctx.get("blocks") or []),
                }
            )
        except Exception as exc:  # noqa: BLE001 - bridge must report per-file failures.
            failed.append({"path": str(target), "error": f"{type(exc).__name__}: {safe_text(exc, 300)}"})
    listing = _knowledge_list(workspace)
    return {
        **listing,
        "ok": bool(imported) or not failed,
        "imported": imported,
        "failed": failed,
    }


def _knowledge_query(workspace: Path, payload: dict[str, Any]) -> dict[str, Any]:
    document_id = _clean_document_id(payload.get("document_id"))
    query = safe_text(payload.get("query"), 800)
    if not query:
        return {"ok": False, "error": "请输入查询内容"}
    ctx = load_document_context(workspace, document_id=document_id)
    if not ctx:
        return {"ok": False, "error": "没有找到已解析的文档"}
    result = query_document_context(ctx, query, top_k=int(payload.get("top_k") or 6))
    return {"ok": True, "document_id": ctx.get("document_id"), "query": query, "result": result}


def _knowledge_export(workspace: Path, payload: dict[str, Any]) -> dict[str, Any]:
    document_id = _clean_document_id(payload.get("document_id"))
    ctx = load_document_context(workspace, document_id=document_id)
    if not ctx:
        return {"ok": False, "error": "没有找到可导出的文档"}
    fmt = safe_text(payload.get("format") or "md", 20).lower().lstrip(".")
    if fmt not in {"md", "markdown", "txt", "json"}:
        fmt = "md"
    suffix = "json" if fmt == "json" else ("txt" if fmt == "txt" else "md")
    output_dir = workspace / "document_exports"
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{ctx.get('document_id')}_summary.{suffix}"
    if fmt == "json":
        content = json.dumps(public_context_payload(ctx), ensure_ascii=False, indent=2)
    elif fmt == "txt":
        content = render_context_text(ctx)
    else:
        content = render_context_markdown(ctx)
    commit = write_text_atomic_verified(target, content, encoding="utf-8")
    return {
        "ok": bool(commit.get("physical_commit_verified")),
        "document_id": ctx.get("document_id"),
        "target": str(target),
        "format": suffix,
        "commit": commit,
    }


def _knowledge_remove(workspace: Path, payload: dict[str, Any]) -> dict[str, Any]:
    document_id = _clean_document_id(payload.get("document_id"))
    if not document_id:
        return {"ok": False, "error": "缺少文档 ID"}
    index = load_index(workspace)
    documents = dict(index.get("documents") or {})
    if document_id not in documents:
        return {"ok": False, "error": "索引中没有这个文档"}
    documents.pop(document_id, None)
    path_index = {
        key: value
        for key, value in dict(index.get("path_index") or {}).items()
        if value != document_id
    }
    if index.get("last_document_id") == document_id:
        index["last_document_id"] = next(iter(documents.keys()), "")
    index["documents"] = documents
    index["path_index"] = path_index
    save_index(workspace, index)
    context_path = _context_file(workspace, document_id)
    removed_context = False
    if context_path.exists():
        context_path.unlink()
        removed_context = True
    listing = _knowledge_list(workspace)
    return {
        **listing,
        "removed_document_id": document_id,
        "removed_context": removed_context,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", required=True, choices=["list", "import", "query", "export", "remove"])
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    workspace = Path(args.workspace).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    payload = _read_stdin()

    try:
        if args.action == "list":
            return _json_out(_knowledge_list(workspace))
        if args.action == "import":
            return _json_out(_knowledge_import(workspace, payload))
        if args.action == "query":
            return _json_out(_knowledge_query(workspace, payload))
        if args.action == "export":
            return _json_out(_knowledge_export(workspace, payload))
        if args.action == "remove":
            return _json_out(_knowledge_remove(workspace, payload))
    except Exception as exc:  # noqa: BLE001 - top-level bridge error is returned as JSON.
        return _json_out(
            {
                "ok": False,
                "error": f"{type(exc).__name__}: {safe_text(exc, 600)}",
                "workspace": str(workspace),
                "time": datetime.now().isoformat(timespec="seconds"),
            }
        )
    return _json_out({"ok": False, "error": "未知动作"})


if __name__ == "__main__":
    raise SystemExit(main())
