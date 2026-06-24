"""IntentResult 数据类。IntentBridge 已迁移至 cli_loop 合并判定。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IntentResult:
    intent: str
    confidence: float
