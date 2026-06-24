"""多媒体扩展静态检查。

运行位置：resources/backend
python tools_eval/check_multimedia_static.py
"""
from __future__ import annotations

from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
required_skills = [
    "16_tupian_jiexi", "17_shipin_jiexi", "18_tupian_zhizuo", "19_shipin_zhizuo", "20_shipin_jianji",
    "21_yinpin_jiexi", "22_yinpin_zhizuo", "23_duomeiti_jiegouhua_chouqu", "24_duomeiti_gongcheng",
]
required_files = [
    "tiangong_agent_runtime/adapters/multimedia_tools_adapter.py",
    "tiangong_agent_runtime/media_parser.py",
    "tiangong_agent_runtime/media_context_store.py",
    "tiangong_agent_runtime/tool_schemas_multimedia_additions.py",
]

def main() -> None:
    missing = []
    for s in required_skills:
        if not (ROOT / "tiangong_agent_runtime" / "skills" / s / "SKILL.md").exists():
            missing.append(f"skill:{s}")
    for f in required_files:
        if not (ROOT / f).exists():
            missing.append(f"file:{f}")
    for f in required_files:
        p = ROOT / f
        if p.exists() and p.suffix == ".py":
            ast.parse(p.read_text(encoding="utf-8"))
    if missing:
        print("FAIL", missing)
        raise SystemExit(1)
    print("PASS multimedia extension static check")

if __name__ == "__main__":
    main()
