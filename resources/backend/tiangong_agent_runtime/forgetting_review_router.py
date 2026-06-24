"""L6.40 ForgetReviewDecision 数据类。ForgetReviewRouter 已迁移至 cli_loop 后处理。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tiangong_kernel.l6_plugins.common._common import ensure_bool

L6_40_FORGET_REVIEW_SCHEMA = "tiangong.l6_40.forgetting_review_router.v1"


@dataclass(frozen=True)
class ForgetReviewDecision:
    decision_id: str
    memory_id: str
    recommended_actions: tuple[str, ...]
    forgetting_score: float
    legal_delete_review_required: bool = False
    tombstone_review_required: bool = False
    active_recall_suppression_required: bool = False
    retention_exception_review_required: bool = False
    direct_delete_allowed: bool = False
    planner_consumable: bool = True
    no_physical_delete: bool = True
    no_memory_mutation: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.recommended_actions, tuple):
            raise ValueError("ForgetReviewDecision.recommended_actions must be tuple")
        for field_name in (
            "legal_delete_review_required",
            "tombstone_review_required",
            "active_recall_suppression_required",
            "retention_exception_review_required",
            "direct_delete_allowed",
            "planner_consumable",
            "no_physical_delete",
            "no_memory_mutation",
        ):
            ensure_bool(getattr(self, field_name), f"ForgetReviewDecision.{field_name}")
        if self.direct_delete_allowed or not self.no_physical_delete or not self.no_memory_mutation:
            raise ValueError("ForgetReviewDecision cannot allow physical delete or mutate memory")

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema": L6_40_FORGET_REVIEW_SCHEMA,
            "decision_id": self.decision_id,
            "memory_id": self.memory_id,
            "recommended_actions": list(self.recommended_actions),
            "forgetting_score": self.forgetting_score,
            "legal_delete_review_required": self.legal_delete_review_required,
            "tombstone_review_required": self.tombstone_review_required,
            "active_recall_suppression_required": self.active_recall_suppression_required,
            "retention_exception_review_required": self.retention_exception_review_required,
            "direct_delete_allowed": self.direct_delete_allowed,
            "planner_consumable": self.planner_consumable,
            "no_physical_delete": self.no_physical_delete,
            "no_memory_mutation": self.no_memory_mutation,
        }



