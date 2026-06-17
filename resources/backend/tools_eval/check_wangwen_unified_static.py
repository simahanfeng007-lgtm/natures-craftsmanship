from __future__ import annotations

import argparse
import ast
import json
import py_compile
import re
from pathlib import Path

REQUIRED_SECTIONS = ["## 什么时候用", "## 标准流程", "## 工具"]
TOOL_NAMES = [
    "wangwen_novel_factory_run",
    "wangwen_novel_bible_build",
    "wangwen_chapter_brief_build",
    "wangwen_draft_quality_check",
    "wangwen_revision_plan_build",
]
SKILL_DIRS = ["54_wangwen_xiaoshuo_shengchan_liushui"]


def load_schema_keys(schema_file: Path) -> set[str]:
    text = schema_file.read_text(encoding="utf-8")
    keys = set(re.findall(r'"(wangwen_[a-zA-Z0-9_]+)"\s*:', text))
    return keys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", required=True)
    args = parser.parse_args()
    backend = Path(args.backend).resolve()
    runtime = backend / "tiangong_agent_runtime"
    errors: list[str] = []

    for dirname in SKILL_DIRS:
        skill_md = runtime / "skills" / dirname / "SKILL.md"
        if not skill_md.exists():
            errors.append(f"缺少 Skill: {skill_md}")
            continue
        text = skill_md.read_text(encoding="utf-8")
        for section in REQUIRED_SECTIONS:
            if section not in text:
                errors.append(f"{skill_md} 缺少 {section}")
        for tool in TOOL_NAMES:
            if f"`{tool}`" not in text:
                errors.append(f"{skill_md} 未声明工具 {tool}")

    schema_file = runtime / "tool_schemas.py"
    ext_schema_file = runtime / "wangwen_unified_pipeline_tool_schemas.py"
    if not schema_file.exists():
        errors.append("缺少 tool_schemas.py")
        schema_keys = set()
    else:
        schema_keys = load_schema_keys(schema_file)
    if ext_schema_file.exists():
        schema_keys |= load_schema_keys(ext_schema_file)
    else:
        errors.append("缺少 wangwen_unified_pipeline_tool_schemas.py")
    for tool in TOOL_NAMES:
        if tool not in schema_keys:
            errors.append(f"schema 缺少工具 {tool}")

    adapter_file = runtime / "adapters" / "wangwen_unified_pipeline_adapters.py"
    if not adapter_file.exists():
        errors.append("缺少 adapter 文件")
    else:
        adapter_text = adapter_file.read_text(encoding="utf-8")
        for tool in TOOL_NAMES:
            if f"def {tool}_adapter" not in adapter_text:
                errors.append(f"adapter 缺少 {tool}_adapter")

    runtime_entry = runtime / "runtime_entry.py"
    if not runtime_entry.exists():
        errors.append("缺少 runtime_entry.py")
    else:
        rt = runtime_entry.read_text(encoding="utf-8")
        for tool in TOOL_NAMES:
            if tool not in rt:
                errors.append(f"runtime_entry.py 未注册或未导入 {tool}")

    if len(TOOL_NAMES) != len(set(TOOL_NAMES)):
        errors.append("工具名存在重复")

    for compile_target in [runtime / "runtime_entry.py", runtime / "adapters" / "wangwen_unified_pipeline_adapters.py", runtime / "wangwen_unified_pipeline_tool_schemas.py"]:
        if compile_target.exists():
            try:
                py_compile.compile(str(compile_target), doraise=True)
            except Exception as exc:
                errors.append(f"compile_failed:{compile_target}:{exc}")

    if errors:
        print(json.dumps({"ok": False, "errors": errors}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(json.dumps({"ok": True, "skills": len(SKILL_DIRS), "tools": len(TOOL_NAMES), "errors": 0}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
