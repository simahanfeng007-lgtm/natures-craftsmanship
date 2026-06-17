from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

TOOL_NAMES = [
    "wangwen_novel_factory_run",
    "wangwen_novel_bible_build",
    "wangwen_chapter_brief_build",
    "wangwen_draft_quality_check",
    "wangwen_revision_plan_build",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", required=True)
    args = parser.parse_args()
    backend = Path(args.backend).resolve()
    runtime = backend / "tiangong_agent_runtime"
    errors: list[str] = []
    schema_text = ""
    for p in [runtime / "tool_schemas.py", runtime / "wangwen_unified_pipeline_tool_schemas.py"]:
        if p.exists():
            schema_text += "\n" + p.read_text(encoding="utf-8")
    runtime_text = (runtime / "runtime_entry.py").read_text(encoding="utf-8") if (runtime / "runtime_entry.py").exists() else ""
    skill_text = ""
    for skill_md in (runtime / "skills").glob("*/SKILL.md") if (runtime / "skills").exists() else []:
        skill_text += "\n" + skill_md.read_text(encoding="utf-8", errors="replace")
    for tool in TOOL_NAMES:
        if tool not in schema_text:
            errors.append(f"schema_missing:{tool}")
        if tool not in runtime_text:
            errors.append(f"runtime_registration_missing:{tool}")
        if f"`{tool}`" not in skill_text:
            errors.append(f"skill_declaration_missing:{tool}")
    generic_query = re.findall(r'"(wangwen_[a-zA-Z0-9_]+)"\s*:\s*\{[^{}]*"query"', schema_text)
    if generic_query:
        errors.append("generic_query_schema_detected:" + ",".join(sorted(set(generic_query))))
    if errors:
        print(json.dumps({"ok": False, "errors": errors}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(json.dumps({"ok": True, "checked_tools": TOOL_NAMES, "schema_coverage": "complete"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
