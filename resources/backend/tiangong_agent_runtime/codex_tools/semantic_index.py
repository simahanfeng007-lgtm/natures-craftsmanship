from __future__ import annotations

import ast
import re
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any

from .common import (
    SKIP_DIRS,
    TEXT_SUFFIXES,
    ToolInputError,
    bounded_int,
    json_output,
    resolve_workspace_path,
    safe_rel,
    workspace_root,
)


SEMANTIC_SCHEMA = "tiangong.codex.semantic_index.v1"
CODE_SUFFIXES = {".py", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue", ".svelte"}
JS_SYMBOL_RE = re.compile(
    r"^\s*(?:export\s+default\s+|export\s+)?(?:(?:async\s+)?function\s+([A-Za-z_$][\w$]*)|class\s+([A-Za-z_$][\w$]*)|"
    r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>|"
    r"(?:interface|type)\s+([A-Za-z_$][\w$]*))"
)
JS_IMPORT_RE = re.compile(
    r"^\s*(?:import\s+(?P<body>.+?)\s+from\s+['\"](?P<module>[^'\"]+)['\"]|"
    r"import\s+['\"](?P<side_effect>[^'\"]+)['\"]|"
    r"(?:const|let|var)\s+(?P<require_name>[A-Za-z_$][\w$]*)\s*=\s*require\(['\"](?P<require_module>[^'\"]+)['\"]\))"
)
CALL_RE = re.compile(r"\b([A-Za-z_$][\w$]*)\s*\(")
CALL_SKIP = {
    "if", "for", "while", "switch", "catch", "function", "return", "typeof", "sizeof",
    "print", "len", "str", "int", "float", "bool", "list", "dict", "set", "tuple",
}


def _read_text(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "cp936", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def _looks_binary(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return b"\x00" in fh.read(4096)
    except Exception:
        return False


def _iter_code_files(root: Path, *, max_files: int) -> tuple[list[Path], bool]:
    files: list[Path] = []
    truncated = False
    if root.is_file():
        return ([root] if root.suffix.lower() in CODE_SUFFIXES else []), False
    for dirpath, dirnames, filenames in root.walk() if hasattr(root, "walk") else _walk_compat(root):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS and not name.startswith(".")]
        for name in filenames:
            path = Path(dirpath) / name
            if path.suffix.lower() in CODE_SUFFIXES and not _looks_binary(path):
                files.append(path)
                if len(files) >= max_files:
                    return files, True
    return files, truncated


def _walk_compat(root: Path):
    import os

    for dirpath, dirnames, filenames in os.walk(root):
        yield Path(dirpath), dirnames, filenames


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


class _PythonIndexer(ast.NodeVisitor):
    def __init__(self, file_rel: str) -> None:
        self.file_rel = file_rel
        self.symbols: list[dict[str, Any]] = []
        self.imports: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self._class_stack: list[str] = []
        self._function_stack: list[str] = []

    def _scope(self) -> str:
        return ".".join([*self._class_stack, *self._function_stack])

    def visit_Import(self, node: ast.Import) -> Any:
        for alias in node.names:
            self.imports.append({"file": self.file_rel, "line": node.lineno, "module": alias.name, "name": alias.asname or alias.name, "kind": "import"})

    def visit_ImportFrom(self, node: ast.ImportFrom) -> Any:
        module = "." * int(node.level or 0) + str(node.module or "")
        for alias in node.names:
            self.imports.append({"file": self.file_rel, "line": node.lineno, "module": module, "name": alias.asname or alias.name, "kind": "from"})

    def visit_ClassDef(self, node: ast.ClassDef) -> Any:
        qname = ".".join([*self._class_stack, node.name]) if self._class_stack else node.name
        self.symbols.append({
            "file": self.file_rel,
            "line": node.lineno,
            "end_line": getattr(node, "end_lineno", node.lineno),
            "name": node.name,
            "qualified_name": qname,
            "kind": "class",
            "language": "python",
            "scope": ".".join(self._class_stack),
        })
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self._visit_function(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> Any:
        self._visit_function(node, "async_function")

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, kind: str) -> None:
        qname = ".".join([*self._class_stack, *self._function_stack, node.name])
        self.symbols.append({
            "file": self.file_rel,
            "line": node.lineno,
            "end_line": getattr(node, "end_lineno", node.lineno),
            "name": node.name,
            "qualified_name": qname,
            "kind": kind,
            "language": "python",
            "scope": ".".join([*self._class_stack, *self._function_stack]),
        })
        self._function_stack.append(node.name)
        self.generic_visit(node)
        self._function_stack.pop()

    def visit_Call(self, node: ast.Call) -> Any:
        callee = _call_name(node.func)
        if callee:
            self.calls.append({
                "file": self.file_rel,
                "line": getattr(node, "lineno", 0),
                "caller": self._scope() or "<module>",
                "callee": callee,
                "callee_short": callee.rsplit(".", 1)[-1],
                "language": "python",
            })
        self.generic_visit(node)


def _analyze_python(path: Path, root: Path) -> dict[str, Any]:
    rel = safe_rel(path, root)
    text = _read_text(path)
    indexer = _PythonIndexer(rel)
    try:
        tree = ast.parse(text, filename=rel)
        indexer.visit(tree)
        parse_error = ""
    except SyntaxError as exc:
        parse_error = f"SyntaxError line={exc.lineno}: {exc.msg}"
    except Exception as exc:
        parse_error = f"{type(exc).__name__}: {exc}"
    return {
        "file": rel,
        "language": "python",
        "symbols": indexer.symbols,
        "imports": indexer.imports,
        "calls": indexer.calls,
        "parse_error": parse_error,
        "line_count": len(text.splitlines()),
    }


def _analyze_js_like(path: Path, root: Path) -> dict[str, Any]:
    rel = safe_rel(path, root)
    text = _read_text(path)
    lines = text.splitlines()
    symbols: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    for line_no, line in enumerate(lines, 1):
        import_match = JS_IMPORT_RE.search(line)
        if import_match:
            module = import_match.group("module") or import_match.group("side_effect") or import_match.group("require_module") or ""
            body = import_match.group("body") or import_match.group("require_name") or ""
            imports.append({"file": rel, "line": line_no, "module": module, "name": body[:180], "kind": "import"})
        symbol_match = JS_SYMBOL_RE.search(line)
        if symbol_match:
            name = next((item for item in symbol_match.groups() if item), "")
            if name:
                kind = "class" if "class" in line[: symbol_match.end()] else "function"
                if "interface " in line or "type " in line:
                    kind = "type"
                symbols.append({
                    "file": rel,
                    "line": line_no,
                    "end_line": line_no,
                    "name": name,
                    "qualified_name": name,
                    "kind": kind,
                    "language": path.suffix.lower().lstrip(".") or "javascript",
                    "scope": "",
                })
        for call in CALL_RE.finditer(line):
            name = call.group(1)
            if name in CALL_SKIP:
                continue
            calls.append({
                "file": rel,
                "line": line_no,
                "caller": "<module>",
                "callee": name,
                "callee_short": name,
                "language": path.suffix.lower().lstrip(".") or "javascript",
            })
    symbols_sorted = sorted(symbols, key=lambda item: item["line"])
    for idx, symbol in enumerate(symbols_sorted):
        next_line = symbols_sorted[idx + 1]["line"] if idx + 1 < len(symbols_sorted) else len(lines) + 1
        symbol["end_line"] = max(symbol["line"], next_line - 1)
    for call in calls:
        container = ""
        for symbol in symbols_sorted:
            if int(symbol["line"]) <= int(call["line"]) <= int(symbol.get("end_line") or symbol["line"]):
                container = str(symbol["qualified_name"])
        if container:
            call["caller"] = container
    return {
        "file": rel,
        "language": path.suffix.lower().lstrip(".") or "javascript",
        "symbols": symbols_sorted,
        "imports": imports,
        "calls": calls,
        "parse_error": "",
        "line_count": len(lines),
    }


def _normalized_rel(value: str) -> str:
    return str(value or "").replace("\\", "/").strip("/")


def _without_suffix(rel: str) -> str:
    path = Path(rel)
    suffix = path.suffix
    return rel[: -len(suffix)] if suffix else rel


def _module_aliases(rel: str) -> set[str]:
    normalized = _normalized_rel(rel)
    no_suffix = _without_suffix(normalized)
    path = Path(normalized)
    aliases = {
        no_suffix,
        no_suffix.replace("/", "."),
        path.stem,
    }
    if path.stem in {"__init__", "index"}:
        parent = _normalized_rel(str(path.parent))
        if parent and parent != ".":
            aliases.add(parent)
            aliases.add(parent.replace("/", "."))
    for prefix in ("src.", "lib.", "app.", "resources.backend.", "resources.backend.tiangong_agent_runtime."):
        for alias in list(aliases):
            if alias.startswith(prefix):
                aliases.add(alias[len(prefix):])
    return {alias for alias in aliases if alias and alias != "."}


def _build_module_map(files: list[dict[str, Any]]) -> dict[str, str]:
    module_map: dict[str, str] = {}
    for row in files:
        rel = str(row.get("file") or "")
        for alias in _module_aliases(rel):
            module_map.setdefault(alias, rel)
    return module_map


def _relative_import_candidates(importer: str, module: str) -> list[str]:
    normalized_module = module.strip()
    importer_dir = Path(_normalized_rel(importer)).parent
    candidates: list[str] = []
    if normalized_module.startswith("."):
        dot_count = len(normalized_module) - len(normalized_module.lstrip("."))
        rest = normalized_module[dot_count:].lstrip("/\\")
        base = importer_dir
        for _ in range(max(0, dot_count - 1)):
            base = base.parent
        if rest:
            base = base / rest.replace(".", "/")
    else:
        base = importer_dir / normalized_module
    base_text = _normalized_rel(base.as_posix())
    if base_text:
        candidates.append(base_text)
        for suffix in CODE_SUFFIXES:
            candidates.append(base_text + suffix)
            candidates.append(base_text + "/index" + suffix)
            candidates.append(base_text + "/__init__" + suffix)
    return candidates


def _resolve_import(importer: str, module: str, module_map: dict[str, str], file_set: set[str]) -> tuple[str, str]:
    module_text = str(module or "").strip()
    if not module_text:
        return "", "empty"
    normalized = _normalized_rel(module_text)
    if normalized in module_map:
        return module_map[normalized], "module_alias"
    dotted = normalized.replace("/", ".")
    if dotted in module_map:
        return module_map[dotted], "module_alias"
    if module_text.startswith(".") or module_text.startswith("/"):
        for candidate in _relative_import_candidates(importer, module_text):
            if candidate in file_set:
                return candidate, "relative_file"
            alias = _without_suffix(candidate)
            if alias in module_map:
                return module_map[alias], "relative_alias"
            dotted_alias = alias.replace("/", ".")
            if dotted_alias in module_map:
                return module_map[dotted_alias], "relative_alias"
    return "", "unresolved"


def _build_import_graph(files: list[dict[str, Any]], imports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    module_map = _build_module_map(files)
    file_set = {str(row.get("file") or "") for row in files if row.get("file")}
    edges: list[dict[str, Any]] = []
    for item in imports:
        source = str(item.get("file") or "")
        module = str(item.get("module") or "")
        target, confidence = _resolve_import(source, module, module_map, file_set)
        edges.append({
            "from": source,
            "module": module,
            "name": item.get("name") or "",
            "line": item.get("line") or 0,
            "to": target,
            "resolved": bool(target),
            "confidence": confidence,
        })
    return edges


def _file_role(rel: str) -> str:
    lowered = _normalized_rel(rel).lower()
    name = Path(lowered).name
    if "/test/" in lowered or "/tests/" in lowered or name.startswith("test_") or name.endswith("_test.py") or ".test." in name or ".spec." in name:
        return "test"
    if any(part in lowered for part in ("/components/", "/pages/", "/routes/", "/app/")) or Path(lowered).suffix in {".vue", ".svelte", ".tsx", ".jsx", ".html", ".css"}:
        return "ui"
    if name in {"main.py", "app.py", "server.py", "runtime_entry.py", "index.ts", "index.js", "main.ts", "main.js"}:
        return "entrypoint"
    return "source"


def _build_file_cards(files: list[dict[str, Any]], symbols: list[dict[str, Any]], import_graph: list[dict[str, Any]], calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    symbols_by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
    incoming: Counter[str] = Counter()
    outgoing: Counter[str] = Counter()
    calls_by_file: Counter[str] = Counter()
    for symbol in symbols:
        symbols_by_file[str(symbol.get("file") or "")].append(symbol)
    for edge in import_graph:
        outgoing[str(edge.get("from") or "")] += 1
        if edge.get("to"):
            incoming[str(edge.get("to") or "")] += 1
    for call in calls:
        calls_by_file[str(call.get("file") or "")] += 1
    cards = []
    for row in files:
        rel = str(row.get("file") or "")
        file_symbols = symbols_by_file.get(rel, [])
        cards.append({
            "file": rel,
            "role": _file_role(rel),
            "language": row.get("language") or "",
            "line_count": row.get("line_count") or 0,
            "symbol_count": len(file_symbols),
            "top_symbols": [str(item.get("qualified_name") or item.get("name") or "") for item in file_symbols[:8]],
            "imports_out": outgoing.get(rel, 0),
            "imported_by": incoming.get(rel, 0),
            "calls": calls_by_file.get(rel, 0),
            "parse_error": row.get("parse_error") or "",
            "read_hint": f"{rel}:1",
        })
    return cards


def _build_symbol_cards(symbols: list[dict[str, Any]], calls: list[dict[str, Any]], import_graph: list[dict[str, Any]], *, limit: int = 160) -> list[dict[str, Any]]:
    callers: Counter[str] = Counter()
    callees: Counter[str] = Counter()
    file_importers: Counter[str] = Counter()
    for call in calls:
        callee = str(call.get("callee_short") or call.get("callee") or "")
        caller = str(call.get("caller") or "")
        if callee:
            callers[callee] += 1
        if caller:
            callees[caller] += 1
    for edge in import_graph:
        if edge.get("to"):
            file_importers[str(edge.get("to"))] += 1
    cards = []
    for symbol in symbols:
        name = str(symbol.get("name") or "")
        qname = str(symbol.get("qualified_name") or name)
        rel = str(symbol.get("file") or "")
        score = callers.get(name, 0) * 3 + file_importers.get(rel, 0) * 2 + (1 if symbol.get("kind") in {"class", "function", "async_function"} else 0)
        cards.append({
            "symbol": qname,
            "name": name,
            "kind": symbol.get("kind") or "",
            "file": rel,
            "line": symbol.get("line") or 0,
            "end_line": symbol.get("end_line") or symbol.get("line") or 0,
            "caller_hits": callers.get(name, 0),
            "internal_call_count": callees.get(qname, 0),
            "file_importers": file_importers.get(rel, 0),
            "score": score,
            "read_hint": f"{rel}:{symbol.get('line') or 1}",
        })
    return sorted(cards, key=lambda item: (-int(item.get("score") or 0), str(item.get("file") or ""), int(item.get("line") or 0)))[:limit]


def _entry_points(symbol_cards: list[dict[str, Any]], file_cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entry_names = {"main", "run", "start", "handler", "create_app", "app", "setup", "execute"}
    rows: list[dict[str, Any]] = []
    entry_files = {str(card.get("file")) for card in file_cards if card.get("role") == "entrypoint"}
    for card in symbol_cards:
        name = str(card.get("name") or "")
        if name in entry_names or card.get("file") in entry_files or int(card.get("caller_hits") or 0) >= 3:
            rows.append(card)
        if len(rows) >= 20:
            break
    return rows


def _llm_brief(index: dict[str, Any]) -> str:
    return (
        f"{index.get('scanned_files', 0)} file(s), {len(index.get('symbols', []))} symbol(s), "
        f"{len(index.get('imports', []))} import(s), {len(index.get('calls', []))} call(s); "
        f"languages={','.join(index.get('languages', {}).keys()) or 'unknown'}; "
        f"errors={len(index.get('errors', []))}; advisory_only=true"
    )


def _next_actions(index: dict[str, Any]) -> list[str]:
    actions = [
        "Use file_cards to choose files to read; use symbol_cards read_hint for exact line reads.",
        "Use semantic_lookup for exact symbols before editing; use call_graph for caller/callee checks.",
        "Use impact_analyzer after a diff or changed file list to choose validation commands.",
    ]
    if index.get("errors"):
        actions.insert(0, "Inspect parse errors before trusting the full semantic map.")
    if index.get("truncated"):
        actions.insert(0, "Narrow path or raise max_files because this semantic map is truncated.")
    return actions


def build_index(workspace: str | Path, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    ws = workspace_root(workspace)
    root = resolve_workspace_path(ws, args.get("path", "."), must_exist=True)
    max_files = bounded_int(args.get("max_files"), 800, 20, 5000)
    files, truncated = _iter_code_files(root, max_files=max_files)
    file_rows: list[dict[str, Any]] = []
    symbols: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    languages: Counter[str] = Counter()
    for path in files:
        suffix = path.suffix.lower()
        try:
            if suffix in {".py", ".pyw"}:
                row = _analyze_python(path, ws)
            else:
                row = _analyze_js_like(path, ws)
        except Exception as exc:
            errors.append({"file": safe_rel(path, ws), "error": f"{type(exc).__name__}: {exc}"})
            continue
        file_rows.append({
            "file": row["file"],
            "language": row["language"],
            "line_count": row["line_count"],
            "symbols": len(row["symbols"]),
            "imports": len(row["imports"]),
            "calls": len(row["calls"]),
            "parse_error": row["parse_error"],
        })
        languages[str(row["language"])] += 1
        symbols.extend(row["symbols"])
        imports.extend(row["imports"])
        calls.extend(row["calls"])
        if row["parse_error"]:
            errors.append({"file": row["file"], "error": row["parse_error"]})
    import_graph = _build_import_graph(file_rows, imports)
    file_cards = _build_file_cards(file_rows, symbols, import_graph, calls)
    symbol_cards = _build_symbol_cards(
        symbols,
        calls,
        import_graph,
        limit=bounded_int(args.get("max_symbol_cards"), 160, 20, 1000),
    )
    payload = {
        "schema": SEMANTIC_SCHEMA,
        "version": "2.0",
        "advisory_only": True,
        "root": safe_rel(root, ws),
        "scanned_files": len(files),
        "truncated": truncated,
        "languages": dict(languages.most_common()),
        "file_cards": file_cards,
        "symbol_cards": symbol_cards,
        "entry_points": _entry_points(symbol_cards, file_cards),
        "import_graph": import_graph,
        "symbols": symbols,
        "imports": imports,
        "calls": calls,
        "files": file_rows,
        "errors": errors[:50],
    }
    payload["llm_brief"] = _llm_brief(payload)
    payload["next_actions"] = _next_actions(payload)
    return payload


def lookup(workspace: str | Path, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolInputError("[BAD_ARGS] semantic_lookup requires query")
    kind = str(args.get("kind") or "any").strip().lower()
    exact = str(args.get("match") or "exact").lower() != "contains"
    limit = bounded_int(args.get("limit"), 80, 1, 300)
    index = build_index(workspace, args)

    def hit(value: Any) -> bool:
        text = str(value or "")
        return text == query if exact else query.lower() in text.lower()

    matches: list[dict[str, Any]] = []
    if kind in {"any", "definition", "symbol"}:
        for symbol in index["symbols"]:
            if hit(symbol.get("name")) or hit(symbol.get("qualified_name")):
                matches.append({"match_kind": "definition", **symbol})
    if kind in {"any", "import"}:
        for item in index["imports"]:
            if hit(item.get("module")) or hit(item.get("name")):
                matches.append({"match_kind": "import", **item})
    if kind in {"any", "call", "reference"}:
        for call in index["calls"]:
            if hit(call.get("callee")) or hit(call.get("callee_short")) or hit(call.get("caller")):
                matches.append({"match_kind": "call", **call})
    suggested_reads = []
    seen_reads: set[str] = set()
    for item in matches:
        rel = str(item.get("file") or "")
        line = int(item.get("line") or 1)
        hint = f"{rel}:{line}"
        if rel and hint not in seen_reads:
            seen_reads.add(hint)
            suggested_reads.append({"file": rel, "line": line, "read_hint": hint, "reason": str(item.get("match_kind") or "")})
    return {
        "schema": "tiangong.codex.semantic_lookup.v1",
        "advisory_only": True,
        "query": query,
        "kind": kind,
        "match": "exact" if exact else "contains",
        "root": index["root"],
        "matches": matches[:limit],
        "total_matches": len(matches),
        "truncated": len(matches) > limit,
        "suggested_reads": suggested_reads[:20],
        "llm_brief": f"{len(matches)} semantic match(es) for {query!r}; suggested_reads={min(len(suggested_reads), 20)}",
        "next_actions": [
            "Read suggested locations before editing.",
            "Use call_graph on a matched symbol when caller/callee impact matters.",
            "Use grep as a fallback if semantic matches look incomplete.",
        ],
        "index_summary": {
            "scanned_files": index["scanned_files"],
            "symbols": len(index["symbols"]),
            "imports": len(index["imports"]),
            "calls": len(index["calls"]),
            "errors": len(index["errors"]),
        },
    }


def call_graph(workspace: str | Path, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    symbol = str(args.get("symbol") or "").strip()
    if not symbol:
        raise ToolInputError("[BAD_ARGS] call_graph requires symbol")
    direction = str(args.get("direction") or "both").strip().lower()
    depth = bounded_int(args.get("depth"), 2, 1, 5)
    limit = bounded_int(args.get("limit"), 120, 1, 500)
    index = build_index(workspace, args)
    edges: set[tuple[str, str]] = set()
    for call in index["calls"]:
        caller = str(call.get("caller") or "<module>")
        callee = str(call.get("callee_short") or call.get("callee") or "")
        if caller and callee and caller != callee:
            edges.add((caller, callee))
    forward: dict[str, set[str]] = defaultdict(set)
    reverse: dict[str, set[str]] = defaultdict(set)
    for caller, callee in edges:
        forward[caller].add(callee)
        reverse[callee].add(caller)

    def _walk(seed: str, graph: dict[str, set[str]]) -> list[dict[str, Any]]:
        rows = []
        seen = {seed}
        queue = deque([(seed, 0)])
        while queue and len(rows) < limit:
            node, level = queue.popleft()
            if level >= depth:
                continue
            for nxt in sorted(graph.get(node, set())):
                rows.append({"from": node, "to": nxt, "depth": level + 1})
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append((nxt, level + 1))
                if len(rows) >= limit:
                    break
        return rows

    graph_rows: list[dict[str, Any]] = []
    if direction in {"both", "callees", "out", "forward"}:
        graph_rows.extend({**row, "direction": "callees"} for row in _walk(symbol, forward))
    if direction in {"both", "callers", "in", "reverse"}:
        graph_rows.extend({**row, "direction": "callers"} for row in _walk(symbol, reverse))
    related_defs = [
        item for item in index["symbols"]
        if item.get("name") == symbol or item.get("qualified_name") == symbol
    ][:20]
    related_files = sorted({
        str(item.get("file") or "")
        for item in related_defs
        if item.get("file")
    } | {
        str(item.get("file") or "")
        for item in index["calls"]
        if item.get("callee_short") == symbol or item.get("caller") == symbol
    })
    return {
        "schema": "tiangong.codex.call_graph.v1",
        "advisory_only": True,
        "symbol": symbol,
        "direction": direction,
        "depth": depth,
        "edges": graph_rows[:limit],
        "truncated": len(graph_rows) > limit,
        "definitions": related_defs,
        "related_files": related_files[:40],
        "llm_brief": f"{len(graph_rows)} call edge(s) for {symbol!r}; definitions={len(related_defs)}; related_files={min(len(related_files), 40)}",
        "next_actions": [
            "Read definitions first, then inspect related_files with the highest caller relevance.",
            "Use impact_analyzer after edits to choose validation.",
        ],
        "index_summary": {
            "scanned_files": index["scanned_files"],
            "edge_count": len(edges),
            "symbols": len(index["symbols"]),
        },
    }


def index_text(workspace: str | Path, args: dict[str, Any] | None = None) -> str:
    payload = build_index(workspace, args)
    max_symbols = bounded_int((args or {}).get("max_symbols"), 200, 20, 1000)
    max_calls = bounded_int((args or {}).get("max_calls"), 200, 20, 1000)
    max_cards = bounded_int((args or {}).get("max_cards"), 120, 20, 500)
    max_import_edges = bounded_int((args or {}).get("max_import_edges"), 200, 20, 1000)
    payload["symbols"] = payload["symbols"][:max_symbols]
    payload["imports"] = payload["imports"][:max_symbols]
    payload["calls"] = payload["calls"][:max_calls]
    payload["file_cards"] = payload["file_cards"][:max_cards]
    payload["symbol_cards"] = payload["symbol_cards"][:max_cards]
    payload["import_graph"] = payload["import_graph"][:max_import_edges]
    return json_output(payload, limit=20000)


def lookup_text(workspace: str | Path, args: dict[str, Any] | None = None) -> str:
    return json_output(lookup(workspace, args), limit=16000)


def call_graph_text(workspace: str | Path, args: dict[str, Any] | None = None) -> str:
    return json_output(call_graph(workspace, args), limit=16000)
