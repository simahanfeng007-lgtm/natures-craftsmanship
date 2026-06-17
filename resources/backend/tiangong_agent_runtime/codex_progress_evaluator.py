"""Code-X structured plan and progress evaluation.

This module is intentionally inert: it normalizes plan facts and computes
progress from tool results. It does not execute tools, read files, call models,
or mutate runtime state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import time
from typing import Any

from tiangong_kernel.l0_primitives.evidence import EvidenceKind, EvidenceRef, EvidenceState
from tiangong_kernel.l0_primitives.identity import RefId, TypedRef
from tiangong_kernel.l0_primitives.metric import (
    MetricAggregation,
    MetricKind,
    MetricPoint,
    MetricRef,
    MetricUnit,
    MetricValue,
)
from tiangong_kernel.l0_primitives.plan import PlanKind, PlanRef, PlanState
from tiangong_kernel.l0_primitives.risk import RiskLevel, RiskRef
from tiangong_kernel.l0_primitives.time import Timestamp
from tiangong_kernel.l0_primitives.transaction import TransactionKind, TransactionRef, TransactionState


SCHEMA = "tiangong.codex.structured_plan.v1"
SUBSTEPS = ("inspect", "backup", "write", "readback", "quality", "report")
SUBSTEP_WEIGHTS = {
    "inspect": 0.20,
    "backup": 0.05,
    "write": 0.25,
    "readback": 0.20,
    "quality": 0.20,
    "report": 0.10,
}
RISK_PENALTY = {
    "A0": 0.00,
    "A1": 0.01,
    "A2": 0.02,
    "A3": 0.05,
    "A4": 0.10,
    "A5": 0.24,
}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    try:
        number = float(value)
    except Exception:
        return low
    if number != number:
        return low
    return max(low, min(high, number))


def _short_text(value: Any, limit: int = 160) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text[:limit]


def _list_text(value: Any, *, limit: int = 12) -> list[str]:
    if value is None:
        return []
    raw = value if isinstance(value, list) else [value]
    items = []
    for item in raw:
        text = _short_text(item, 240)
        if text and text not in items:
            items.append(text)
        if len(items) >= limit:
            break
    return items


def _digest_ref(prefix: str, *parts: Any) -> RefId:
    digest = hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:32]
    return RefId(f"{prefix}:{digest}")


def _risk_enum(level: str) -> RiskLevel:
    normalized = str(level or "A2").upper()
    return {
        "A0": RiskLevel.A0_SAFE,
        "A1": RiskLevel.A1_LOW,
        "A2": RiskLevel.A2_NORMAL,
        "A3": RiskLevel.A3_ELEVATED,
        "A4": RiskLevel.A4_REVIEW_REQUIRED,
        "A5": RiskLevel.A5_CRITICAL,
    }.get(normalized, RiskLevel.A2_NORMAL)


@dataclass(slots=True)
class CodeXStructuredStep:
    step_id: str
    title: str
    target_files: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    verify_points: list[str] = field(default_factory=list)
    rollback_ref: str = ""
    risk_level: str = "A2"
    weight: float = 1.0
    l0_refs: dict[str, str] = field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "title": self.title,
            "target_files": list(self.target_files),
            "depends_on": list(self.depends_on),
            "actions": list(self.actions),
            "verify_points": list(self.verify_points),
            "rollback_ref": self.rollback_ref,
            "risk_level": self.risk_level,
            "weight": self.weight,
            "l0_refs": dict(self.l0_refs),
        }


@dataclass(slots=True)
class CodeXStructuredPlan:
    plan_ref: str
    task: str
    steps: list[CodeXStructuredStep]
    schema: str = SCHEMA
    plan_kind: str = PlanKind.DAG.value
    state: str = PlanState.ACTIVE.value
    l0_refs: dict[str, str] = field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "plan_ref": self.plan_ref,
            "plan_kind": self.plan_kind,
            "state": self.state,
            "task": self.task,
            "steps": [step.public_dict() for step in self.steps],
            "l0_refs": dict(self.l0_refs),
        }


@dataclass(slots=True)
class StepEvaluation:
    step_id: str
    title: str
    status: str = "pending"
    substeps: dict[str, float] = field(default_factory=lambda: {name: 0.0 for name in SUBSTEPS})
    failures: int = 0
    tool_events: int = 0
    confidence: float = 0.35
    score: float = 0.0
    risk_penalty: float = 0.0
    summary: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "title": self.title,
            "status": self.status,
            "substeps": {k: round(v, 4) for k, v in self.substeps.items()},
            "failures": self.failures,
            "tool_events": self.tool_events,
            "confidence": round(self.confidence, 4),
            "score": round(self.score, 4),
            "risk_penalty": round(self.risk_penalty, 4),
            "summary": self.summary,
            "evidence_refs": list(self.evidence_refs[-8:]),
            "metrics": {k: round(v, 4) for k, v in self.metrics.items()},
        }


def normalize_structured_plan(raw: Any, *, task: str, fallback_steps: list[str] | None = None) -> CodeXStructuredPlan:
    payload = raw if isinstance(raw, dict) else {}
    if "structured_plan" in payload and isinstance(payload["structured_plan"], dict):
        payload = payload["structured_plan"]
    raw_steps = payload.get("steps") if isinstance(payload.get("steps"), list) else None
    if raw_steps is None and isinstance(payload.get("detailed_steps"), list):
        raw_steps = [{"title": item} for item in payload["detailed_steps"]]
    if raw_steps is None:
        raw_steps = [{"title": item} for item in (fallback_steps or [])]
    if not raw_steps:
        raw_steps = [{"title": "执行 Code-X 代码任务", "actions": ["inspect", "write", "readback", "quality", "report"]}]

    plan_ref_obj = PlanRef(_digest_ref("codexplan", task, raw_steps), kind=PlanKind.DAG, state=PlanState.ACTIVE)
    plan_typed_ref = TypedRef(plan_ref_obj.value, "codex_structured_plan")
    tx_ref = TransactionRef(_digest_ref("codextran", task, raw_steps), kind=TransactionKind.CHECKPOINTED, state=TransactionState.IN_PROGRESS)
    steps: list[CodeXStructuredStep] = []
    total_weight = 0.0

    for index, item in enumerate(raw_steps, 1):
        item = item if isinstance(item, dict) else {"title": item}
        step_id = _short_text(item.get("step_id") or item.get("id") or f"S{index}", 48)
        title = _short_text(item.get("title") or item.get("summary") or f"步骤 {index}", 160)
        risk_level = _short_text(item.get("risk_level") or "A2", 8).upper()
        if risk_level not in RISK_PENALTY:
            risk_level = "A2"
        weight = max(0.1, min(10.0, float(item.get("weight") or 1.0)))
        total_weight += weight
        risk_ref = RiskRef(_digest_ref("codexrisk", task, step_id, risk_level), level=_risk_enum(risk_level))
        rollback_ref = _short_text(item.get("rollback_ref") or f"rollback:{step_id}", 120)
        evidence_ref = EvidenceRef(
            _digest_ref("codexevid", task, step_id),
            kind=EvidenceKind.TOOL_OUTPUT_EVIDENCE,
            state=EvidenceState.PROPOSED,
        )
        steps.append(
            CodeXStructuredStep(
                step_id=step_id,
                title=title,
                target_files=_list_text(item.get("target_files") or item.get("files") or item.get("target_file")),
                depends_on=_list_text(item.get("depends_on") or item.get("dependencies")),
                actions=_list_text(item.get("actions") or item.get("substeps") or SUBSTEPS),
                verify_points=_list_text(item.get("verify_points") or item.get("verification") or item.get("verify")),
                rollback_ref=rollback_ref,
                risk_level=risk_level,
                weight=weight,
                l0_refs={
                    "plan_ref": plan_ref_obj.value.value,
                    "risk_ref": risk_ref.value.value,
                    "evidence_ref": evidence_ref.value.value,
                    "transaction_ref": tx_ref.value.value,
                    "typed_plan_ref": plan_typed_ref.ref_id.value,
                },
            )
        )

    if total_weight <= 0:
        for step in steps:
            step.weight = 1.0

    return CodeXStructuredPlan(
        plan_ref=plan_ref_obj.value.value,
        task=_short_text(task, 260),
        steps=steps,
        l0_refs={
            "plan_ref": plan_ref_obj.value.value,
            "transaction_ref": tx_ref.value.value,
            "typed_plan_ref": plan_typed_ref.ref_id.value,
        },
    )


def extract_plan_payload(text: str) -> dict[str, Any] | None:
    source = str(text or "")
    decoder = json.JSONDecoder()
    for start, char in enumerate(source):
        if char != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(source[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and (
            obj.get("schema") == SCHEMA
            or "structured_plan" in obj
            or "steps" in obj
            or "detailed_steps" in obj
        ):
            return obj
    return None


def fallback_step_titles(detail_text: str) -> list[str]:
    payload = extract_plan_payload(detail_text)
    if isinstance(payload, dict):
        plan = payload.get("structured_plan") if isinstance(payload.get("structured_plan"), dict) else payload
        raw_steps = plan.get("steps") if isinstance(plan.get("steps"), list) else plan.get("detailed_steps")
        if isinstance(raw_steps, list):
            titles = []
            for index, item in enumerate(raw_steps, 1):
                if isinstance(item, dict):
                    titles.append(_short_text(item.get("title") or item.get("summary") or item.get("step_id") or f"步骤 {index}", 120))
                else:
                    titles.append(_short_text(item, 120))
            return [title for title in titles if title]
    return []


class CodeXProgressEvaluator:
    def __init__(self, plan: CodeXStructuredPlan):
        self.plan = plan
        self._by_step = {step.step_id: step for step in plan.steps}
        self._evals = {step.step_id: StepEvaluation(step.step_id, step.title) for step in plan.steps}
        self._active_step = plan.steps[0].step_id if plan.steps else ""
        self._event_count = 0

    @property
    def active_step_id(self) -> str:
        return self._active_step

    def resolve_step_id(self, tool_payload: dict[str, Any] | None = None) -> str:
        tool_payload = tool_payload or {}
        candidate = _short_text(tool_payload.get("step_id") or tool_payload.get("step") or "", 48)
        if candidate in self._by_step:
            return candidate
        return self._active_step or next(iter(self._by_step), "S1")

    def update_from_tool(self, step_id: str, substep: str, tool_name: str, ok: bool, output: str = "") -> dict[str, Any]:
        step_id = step_id if step_id in self._evals else self.resolve_step_id({"step_id": step_id})
        state = self._evals[step_id]
        state.tool_events += 1
        self._event_count += 1
        normalized_substep = self._normalize_substep(substep, tool_name)
        if ok:
            state.substeps[normalized_substep] = max(state.substeps.get(normalized_substep, 0.0), 1.0)
            if tool_name in {"write_file", "replace_lines"}:
                state.substeps["write"] = 1.0
            if tool_name in {"read_file", "glob", "grep", "list_dir"} and normalized_substep == "inspect":
                state.substeps["inspect"] = 1.0
            if tool_name == "read_file" and state.substeps.get("write", 0.0) > 0:
                state.substeps["readback"] = 1.0
            if tool_name in {"python_quality_runner", "code_quality_runner"}:
                state.substeps["quality"] = 1.0
        else:
            state.failures += 1
        if all(state.substeps.get(name, 0.0) >= 1.0 for name in ("write", "readback", "quality")):
            state.substeps["report"] = max(state.substeps.get("report", 0.0), 0.8)

        state.score = self._score(state)
        state.risk_penalty = self._risk_penalty(step_id, state)
        state.confidence = self._confidence(state)
        state.status = self._status(state)
        state.summary = self._summary(tool_name, ok, output)
        evidence = EvidenceRef(
            _digest_ref("codexevid", self.plan.plan_ref, step_id, self._event_count, tool_name, ok),
            kind=EvidenceKind.TEST_RESULT_EVIDENCE if tool_name in {"python_quality_runner", "code_quality_runner"} else EvidenceKind.TOOL_OUTPUT_EVIDENCE,
            state=EvidenceState.VERIFIED if ok else EvidenceState.QUESTIONED,
        )
        state.evidence_refs.append(evidence.value.value)
        state.metrics = self._metric_points(step_id, state)
        self._advance_active_step()
        return self.snapshot()

    def mark_done(self, summary: str = "") -> dict[str, Any]:
        for state in self._evals.values():
            if state.status != "failed":
                state.substeps["report"] = 1.0
                state.score = max(state.score, self._score(state))
                state.confidence = max(state.confidence, self._confidence(state))
                state.status = "done" if state.score >= 0.72 else state.status
                if summary:
                    state.summary = _short_text(summary, 220)
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        total_weight = sum(max(0.1, step.weight) for step in self.plan.steps) or 1.0
        weighted_progress = 0.0
        weighted_confidence = 0.0
        risk_penalty = 0.0
        failed_steps = 0
        for step in self.plan.steps:
            state = self._evals[step.step_id]
            weight = max(0.1, step.weight)
            weighted_progress += weight * state.score * state.confidence
            weighted_confidence += weight * state.confidence
            risk_penalty += weight * state.risk_penalty
            if state.status == "failed":
                failed_steps += 1
        total_progress = _clamp(weighted_progress / total_weight)
        confidence = _clamp(weighted_confidence / total_weight)
        risk_score = _clamp(risk_penalty / total_weight)
        health_score = _clamp(total_progress - risk_score)
        status = "failed" if failed_steps else ("done" if total_progress >= 0.98 else "running")
        return {
            "schema": "tiangong.codex.progress_snapshot.v1",
            "plan_ref": self.plan.plan_ref,
            "active_step_id": self._active_step,
            "status": status,
            "total_progress": round(total_progress, 4),
            "confidence": round(confidence, 4),
            "risk_score": round(risk_score, 4),
            "health_score": round(health_score, 4),
            "step_count": len(self.plan.steps),
            "failed_steps": failed_steps,
            "steps": [self._evals[step.step_id].public_dict() for step in self.plan.steps],
            "l0_refs": dict(self.plan.l0_refs),
        }

    def _normalize_substep(self, substep: str, tool_name: str) -> str:
        value = _short_text(substep, 40).lower()
        if value in SUBSTEPS:
            return value
        aliases = {
            "check": "inspect",
            "locate": "inspect",
            "backup": "backup",
            "write": "write",
            "patch": "write",
            "modify": "write",
            "read": "readback",
            "readback": "readback",
            "verify": "quality",
            "quality": "quality",
            "test": "quality",
            "report": "report",
        }
        if value in aliases:
            return aliases[value]
        if tool_name in {"glob", "grep", "list_dir"}:
            return "inspect"
        if tool_name in {"write_file", "replace_lines"}:
            return "write"
        if tool_name == "read_file":
            return "readback"
        if tool_name in {"python_quality_runner", "code_quality_runner"}:
            return "quality"
        return "inspect"

    def _score(self, state: StepEvaluation) -> float:
        return _clamp(sum(state.substeps.get(name, 0.0) * weight for name, weight in SUBSTEP_WEIGHTS.items()))

    def _risk_penalty(self, step_id: str, state: StepEvaluation) -> float:
        step = self._by_step.get(step_id)
        base = RISK_PENALTY.get((step.risk_level if step else "A2").upper(), 0.02)
        failure_penalty = min(0.30, state.failures * 0.06)
        missing_verify = 0.08 if state.substeps.get("write", 0.0) and not state.substeps.get("quality", 0.0) else 0.0
        return _clamp(base + failure_penalty + missing_verify)

    def _confidence(self, state: StepEvaluation) -> float:
        verified = (
            0.25 * state.substeps.get("inspect", 0.0)
            + 0.25 * state.substeps.get("write", 0.0)
            + 0.25 * state.substeps.get("readback", 0.0)
            + 0.25 * state.substeps.get("quality", 0.0)
        )
        retry_penalty = min(0.30, state.failures * 0.08)
        return _clamp(0.30 + 0.70 * verified - retry_penalty)

    def _status(self, state: StepEvaluation) -> str:
        if state.failures >= 3 and state.score < 0.55:
            return "failed"
        if state.score >= 0.82 and state.confidence >= 0.72:
            return "done"
        if state.tool_events > 0:
            return "running"
        return "pending"

    def _summary(self, tool_name: str, ok: bool, output: str) -> str:
        prefix = "完成" if ok else "需纠偏"
        return _short_text(f"{prefix} {tool_name}: {output}", 220)

    def _metric_points(self, step_id: str, state: StepEvaluation) -> dict[str, float]:
        now = Timestamp(int(time.time() * 1000))
        metrics = []
        for name, value, kind in (
            ("score", state.score, MetricKind.QUALITY),
            ("confidence", state.confidence, MetricKind.CONFIDENCE),
            ("risk_penalty", state.risk_penalty, MetricKind.RATIO),
        ):
            metrics.append(
                MetricPoint(
                    metric_ref=MetricRef(_digest_ref("codexmetr", self.plan.plan_ref, step_id, name, self._event_count)),
                    kind=kind,
                    value=MetricValue(_clamp(value)),
                    unit=MetricUnit("ratio"),
                    observed_at=now,
                    aggregation=MetricAggregation.POINT,
                )
            )
        return {f"{point.kind.value}:{index}": float(point.value.value) for index, point in enumerate(metrics)}

    def _advance_active_step(self) -> None:
        for step in self.plan.steps:
            if self._evals[step.step_id].status not in {"done"}:
                self._active_step = step.step_id
                return
        self._active_step = self.plan.steps[-1].step_id if self.plan.steps else ""
