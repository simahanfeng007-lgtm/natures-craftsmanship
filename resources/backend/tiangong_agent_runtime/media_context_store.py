"""多媒体上下文轻量存储。

用于图片/视频/音频工具在同一工作区内复用最近解析结果。
当前为文件级 JSON 缓存，不改变 Runtime 主链。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def _store_path(workspace: str | Path) -> Path:
    base = Path(workspace).expanduser().resolve() / ".tiangong_media_context"
    base.mkdir(parents=True, exist_ok=True)
    return base / "media_context.json"


def save_media_context(workspace: str | Path, media_id: str, data: dict[str, Any]) -> None:
    path = _store_path(workspace)
    try:
        current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        current = {}
    current[str(media_id)] = {"saved_at": time.time(), "data": data}
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")


def load_media_context(workspace: str | Path, media_id: str = "") -> dict[str, Any]:
    path = _store_path(workspace)
    if not path.exists():
        return {}
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if media_id:
        return current.get(str(media_id), {}).get("data", {})
    if not current:
        return {}
    last_key = sorted(current.items(), key=lambda item: item[1].get("saved_at", 0))[-1][0]
    return current.get(last_key, {}).get("data", {})
