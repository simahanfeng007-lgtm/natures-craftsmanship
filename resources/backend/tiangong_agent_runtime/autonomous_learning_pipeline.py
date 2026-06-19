"""Autonomous learning card pipeline.

This module is the single queue and state machine for desktop autonomous
learning.  The stored schema uses English keys so tools can consume it
reliably; UI layers translate the public snapshot into Chinese labels.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from time import time
from typing import Any, Callable


SCHEMA = "tiangong.autonomous_learning.card.v1"
SNAPSHOT_SCHEMA = "tiangong.desktop.learning_card_snapshot.v1"

READY_STATUSES = {"formal_ready", "manual_ready_now", "scheduled"}
RUNNING_STATUSES = {"learning_running", "qa_running", "alignment_running"}
TERMINAL_DISCARDED = {"discarded", "skipped_by_user", "skipped_by_judge", "duplicate_removed", "no_value"}
TERMINAL_FAILED = {"failed", "failed_retryable", "failed_permanent"}
TERMINAL_COMPLETED = {"completed"}
LEARNING_CARD_SYSTEM_RULES = (
    "Autonomous learning SOP: create candidate learning cards from L3 memory or explicit user learning requests only. "
    "A card must include reusable learning content, knowledge_time, whether web learning is needed, required skills/tools, "
    "expected artifact, risk level, source summary, and priority evidence. "
    "The only path is candidate -> duplicate check -> upgrade check -> value check -> formal card -> priority ordered learning -> QA -> alignment -> removal. "
    "Duplicates that cannot upgrade an existing skill/tool are discarded. Cards without durable learning value are discarded. "
    "Never treat a failed tool call, PermissionError, pseudo tool protocol, or half-finished plan as a completed skill or knowledge asset."
)


def _now() -> float:
    return time()


def _safe_text(value: Any, limit: int = 1000) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\x00", "").replace("\r", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def state_root() -> Path:
    root = (
        os.environ.get("TIANGONG_JIA")
        or os.environ.get("LINYUANZHE_STATE_DIR")
        or os.environ.get("TIANGONG_STATE_DIR")
        or os.environ.get("HERMES_HOME")
        or ""
    )
    if root:
        return Path(root).expanduser()
    return Path.home() / ".tiangong"


def _atomic_json_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.stem}_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _append_jsonl(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")


def _priority_bucket(score: int) -> str:
    if score >= 90:
        return "P0"
    if score >= 75:
        return "P1"
    if score >= 55:
        return "P2"
    if score >= 35:
        return "P3"
    return "P4"


def _fallback_priority(text: str) -> tuple[str, int, str]:
    compact = text.lower()
    if any(k in compact for k in ("winerror", "permissionerror", "error", "failed", "失败", "出错", "工具", "tool", "bash")):
        return "P0", 95, "Blocks execution or fixes repeated failures."
    if any(k in compact for k in ("skill", "下载", "搜索", "联网", "代码", "工程", "download", "web", "code")):
        return "P1", 82, "Improves high-value user workflows."
    if any(k in compact for k in ("习惯", "偏好", "记忆", "memory", "preference")):
        return "P2", 62, "Useful for memory and personalization."
    if len(compact) < 60:
        return "P4", 25, "Short or low-context learning signal."
    return "P2", 58, "Reusable knowledge candidate."


def _status_dir(status: str) -> str:
    if status in {"raw_candidate", "duplicate_checking", "upgrade_checking", "value_checking"}:
        return "candidate"
    if status in READY_STATUSES:
        return "formal_ready"
    if status == "learning_running":
        return "running"
    if status in {"learning_done", "qa_running"}:
        return "qa"
    if status == "alignment_running":
        return "alignment"
    if status in TERMINAL_COMPLETED:
        return "completed"
    if status in TERMINAL_DISCARDED:
        return "discarded"
    if status in TERMINAL_FAILED:
        return "failed"
    return "candidate"


@dataclass
class LearningCardStore:
    root: Path | None = None

    def __post_init__(self) -> None:
        if self.root is None:
            self.root = state_root() / "learning"
        self.root = Path(self.root)
        for name in ("candidate", "formal_ready", "running", "qa", "alignment", "completed", "discarded", "failed"):
            (self.root / "cards" / name).mkdir(parents=True, exist_ok=True)
        for name in ("indexes", "logs", "archive", "knowledge", "memory"):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def _card_path(self, card: dict[str, Any]) -> Path:
        return self.root / "cards" / _status_dir(str(card.get("status") or "")) / f"{card.get('card_id')}.json"

    def _paths_for_id(self, card_id: str) -> list[Path]:
        paths: list[Path] = []
        cards_root = self.root / "cards"
        for child in cards_root.iterdir() if cards_root.exists() else []:
            if not child.is_dir():
                continue
            path = child / f"{card_id}.json"
            if path.exists():
                paths.append(path)
        return paths

    def save(self, card: dict[str, Any]) -> dict[str, Any]:
        card["updated_at"] = _now()
        target = self._card_path(card)
        _atomic_json_write(target, card)
        for old in self._paths_for_id(str(card.get("card_id") or "")):
            if old.resolve() != target.resolve():
                try:
                    old.unlink()
                except OSError:
                    pass
        self.rebuild_indexes()
        return card

    def load(self, card_id: str) -> dict[str, Any] | None:
        card_id = str(card_id or "").strip()
        if not card_id:
            return None
        for path in self._paths_for_id(card_id):
            try:
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except (OSError, json.JSONDecodeError):
                continue
        return None

    def all_cards(self) -> list[dict[str, Any]]:
        cards: list[dict[str, Any]] = []
        for path in (self.root / "cards").glob("*/*.json"):
            try:
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    cards.append(data)
            except (OSError, json.JSONDecodeError):
                continue
        return cards

    def discard(self, card_id: str, reason: str = "user_deleted") -> dict[str, Any]:
        card = self.load(card_id)
        if not card:
            return {"ok": False, "error": "not_found", "id": card_id}
        card["status"] = "skipped_by_user" if reason == "user_deleted" else "discarded"
        card.setdefault("triage", {})["discard_reason"] = reason
        card.setdefault("lifecycle", {})["deleted_from_queue"] = True
        self.save(card)
        return {"ok": True, "id": card_id, "card": card}

    def next_ready(self) -> dict[str, Any] | None:
        ready = [c for c in self.all_cards() if str(c.get("status") or "") in READY_STATUSES]
        ready.sort(key=lambda c: (-int((c.get("priority") or {}).get("priority_score") or 0), float(c.get("created_at") or 0)))
        return ready[0] if ready else None

    def append_alignment_record(self, kind: str, card: dict[str, Any]) -> str:
        ref = f"{kind}_{_digest({'card': card.get('card_id'), 'at': _now()})}"
        _append_jsonl(
            self.root / kind / f"{kind}_records.jsonl",
            {
                "ref": ref,
                "card_id": card.get("card_id"),
                "created_at": _now(),
                "learning_content": (card.get("content") or {}).get("learning_content", ""),
                "learning_result": (card.get("execution") or {}).get("learning_result", ""),
            },
        )
        return ref

    def rebuild_indexes(self) -> None:
        cards = self.all_cards()
        ready = [c for c in cards if str(c.get("status") or "") in READY_STATUSES]
        ready.sort(key=lambda c: (-int((c.get("priority") or {}).get("priority_score") or 0), float(c.get("created_at") or 0)))
        _atomic_json_write(
            self.root / "indexes" / "priority_queue.json",
            {
                "schema": "tiangong.autonomous_learning.priority_queue.v1",
                "updated_at": _now(),
                "items": [
                    {
                        "card_id": c.get("card_id"),
                        "priority": (c.get("priority") or {}).get("priority"),
                        "priority_score": (c.get("priority") or {}).get("priority_score"),
                        "created_at": c.get("created_at"),
                    }
                    for c in ready
                ],
            },
        )
        counts: dict[str, int] = {}
        for c in cards:
            status = str(c.get("status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
        _atomic_json_write(
            self.root / "indexes" / "status_counts.json",
            {"schema": "tiangong.autonomous_learning.status_counts.v1", "updated_at": _now(), "counts": counts},
        )


class AutonomousLearningPipeline:
    def __init__(self, store: LearningCardStore | None = None) -> None:
        self.store = store or LearningCardStore()

    def _llm_json(self, client: Any, system: str, user: str, *, max_tokens: int = 500) -> dict[str, Any]:
        if client is None:
            return {}
        try:
            resp = client.chat.completions.create(
                model=getattr(client, "model", ""),
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.1,
                max_tokens=max_tokens,
            )
            text = str(resp.choices[0].message.content or "")
        except Exception:
            return {}
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                value = json.loads(text[start:end])
                return value if isinstance(value, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}

    def make_card_from_turn(
        self,
        *,
        user_message: str,
        assistant_result: str,
        route: str,
        workspace: str = "",
        trigger_type: str = "post_turn",
        model_client: Any = None,
        immediate: bool = False,
    ) -> dict[str, Any] | None:
        user_message = _safe_text(user_message, 3000)
        assistant_result = _safe_text(assistant_result, 4000)
        if not user_message and not assistant_result:
            return None
        decision = self._learning_card_decision(user_message, assistant_result, route, trigger_type, model_client)
        if not bool(decision.get("should_create_card", False)):
            return None
        now = _now()
        content = _safe_text(decision.get("learning_content") or assistant_result or user_message, 1200)
        card_id = f"lc_{_digest({'u': user_message, 'a': assistant_result, 't': now})}"
        priority_name, priority_score, priority_reason = self._priority_decision(content, user_message, assistant_result, model_client)
        card = {
            "schema": SCHEMA,
            "card_id": card_id,
            "version": "1.0.0",
            "status": "raw_candidate",
            "created_at": now,
            "updated_at": now,
            "source": {
                "trigger_type": trigger_type,
                "session_id": os.environ.get("TIANGONG_SESSION_SCOPE", ""),
                "turn_id": os.environ.get("TIANGONG_REQUEST_ID", ""),
                "workspace": workspace,
                "route": route,
                "user_message": user_message,
                "assistant_result": assistant_result,
            },
            "content": {
                "learning_content": content,
                "learning_goal": _safe_text(decision.get("learning_goal") or content, 500),
                "knowledge_time": decision.get("knowledge_time") or now,
                "evidence_refs": decision.get("evidence_refs") if isinstance(decision.get("evidence_refs"), list) else [],
                "source_summary": _safe_text(decision.get("source_summary") or assistant_result or user_message, 500),
            },
            "plan": {
                "need_web_learning": bool(decision.get("need_web_learning", self._looks_web_worthy(content))),
                "web_queries": self._string_list(decision.get("web_queries"), fallback=[content[:80]]),
                "required_skills": self._string_list(decision.get("required_skills")),
                "required_tools": self._string_list(decision.get("required_tools")),
                "expected_artifact": self._expected_artifact(decision.get("expected_artifact"), content),
                "risk_level": str(decision.get("risk_level") or "A2")[:20],
            },
            "triage": {
                "duplicate_decision": "unchecked",
                "duplicate_refs": [],
                "upgrade_decision": "unchecked",
                "upgrade_target": "",
                "value_decision": "unchecked",
                "discard_reason": "",
            },
            "priority": {
                "priority": priority_name,
                "priority_score": priority_score,
                "priority_reason": priority_reason,
            },
            "execution": {
                "run_mode": "manual_now" if immediate else "cron",
                "started_at": 0,
                "finished_at": 0,
                "attempt_count": 0,
                "execution_log_refs": [],
                "learning_result": "",
            },
            "qa": {"qa_status": "not_started", "qa_report": "", "validation_errors": [], "retry_or_block_reason": ""},
            "alignment": {
                "alignment_status": "not_started",
                "skill_refs": [],
                "tool_refs": [],
                "knowledge_refs": [],
                "memory_refs": [],
                "activation_refs": [],
            },
            "lifecycle": {
                "user_start_message_sent": False,
                "user_done_message_sent": False,
                "final_message_sent": False,
                "archived_at": 0,
                "deleted_from_queue": False,
            },
        }
        self.store.save(card)
        return self.triage_card(card_id, model_client=model_client)

    def make_card_from_learning_request(
        self,
        *,
        request: str,
        learning_result: str = "",
        route: str = "active_learning_skill",
        workspace: str = "",
        selected_skills: list[str] | None = None,
        model_client: Any = None,
    ) -> dict[str, Any] | None:
        request = _safe_text(request, 3000)
        learning_result = _safe_text(learning_result, 4000)
        if not request:
            return None
        decision = self._learning_card_decision(request, learning_result, route, "user_learning_request", model_client)
        content = _safe_text(decision.get("learning_content") or learning_result or request, 1200)
        goal = _safe_text(decision.get("learning_goal") or request, 500)
        skill_list = self._string_list(selected_skills or decision.get("required_skills"))
        card_id = f"lc_user_{_digest({'request': request, 'route': route, 'workspace': workspace})}"
        existing = self.store.load(card_id)
        if existing:
            return existing
        now = _now()
        priority_name, priority_score, priority_reason = self._priority_decision(goal, request, content, model_client)
        card = {
            "schema": SCHEMA,
            "card_id": card_id,
            "version": "1.0.0",
            "status": "formal_ready",
            "created_at": now,
            "updated_at": now,
            "source": {
                "trigger_type": "user_learning_request",
                "session_id": os.environ.get("TIANGONG_SESSION_SCOPE", ""),
                "turn_id": os.environ.get("TIANGONG_REQUEST_ID", ""),
                "workspace": workspace,
                "route": route,
                "requested_by_user": True,
                "user_message": request,
                "assistant_result": learning_result,
            },
            "content": {
                "learning_content": content,
                "learning_goal": goal,
                "knowledge_time": decision.get("knowledge_time") or now,
                "evidence_refs": decision.get("evidence_refs") if isinstance(decision.get("evidence_refs"), list) else [],
                "source_summary": _safe_text(decision.get("source_summary") or learning_result or request, 500),
            },
            "plan": {
                "need_web_learning": bool(decision.get("need_web_learning", self._looks_web_worthy(f"{request}\n{learning_result}"))),
                "web_queries": self._string_list(decision.get("web_queries"), fallback=[request[:120]]),
                "required_skills": skill_list,
                "required_tools": self._string_list(decision.get("required_tools")),
                "expected_artifact": self._expected_artifact(decision.get("expected_artifact"), f"{request}\n{learning_result}"),
                "risk_level": str(decision.get("risk_level") or "A2")[:20],
            },
            "triage": {
                "duplicate_decision": "user_requested",
                "duplicate_refs": [],
                "upgrade_decision": "unchecked",
                "upgrade_target": "",
                "value_decision": "user_requested",
                "discard_reason": "",
            },
            "priority": {
                "priority": priority_name,
                "priority_score": priority_score,
                "priority_reason": priority_reason,
            },
            "execution": {
                "run_mode": "manual_now",
                "started_at": 0,
                "finished_at": 0,
                "attempt_count": 0,
                "execution_log_refs": [],
                "learning_result": "",
            },
            "qa": {"qa_status": "not_started", "qa_report": "", "validation_errors": [], "retry_or_block_reason": ""},
            "alignment": {
                "alignment_status": "not_started",
                "skill_refs": [],
                "tool_refs": [],
                "knowledge_refs": [],
                "memory_refs": [],
                "activation_refs": [],
            },
            "lifecycle": {
                "user_start_message_sent": False,
                "user_done_message_sent": False,
                "final_message_sent": False,
                "archived_at": 0,
                "deleted_from_queue": False,
            },
        }
        return self.store.save(card)

    def _learning_card_decision(self, user_message: str, assistant_result: str, route: str, trigger_type: str, client: Any) -> dict[str, Any]:
        data = self._llm_json(
            client,
            (
                "You create autonomous learning cards for an agent. Return JSON only. "
                + LEARNING_CARD_SYSTEM_RULES
                + " "
                "Create a card only when the turn contains reusable knowledge, a repeated failure, "
                "a tool/skill gap, user preference, or a workflow improvement. "
                "Do not create a card for pure small talk, raw pseudo tool-call text, or one-off noise."
            ),
            json.dumps(
                {
                    "trigger_type": trigger_type,
                    "route": route,
                    "user_message": user_message[:1600],
                    "assistant_result": assistant_result[:2200],
                    "required_json": {
                        "should_create_card": "boolean",
                        "learning_content": "string",
                        "learning_goal": "string",
                        "knowledge_time": "timestamp_or_string",
                        "need_web_learning": "boolean",
                        "web_queries": ["string"],
                        "required_skills": ["string"],
                        "required_tools": ["string"],
                        "expected_artifact": "knowledge|skill|tool|memory|skill_upgrade|tool_upgrade",
                        "risk_level": "A0-A5",
                        "source_summary": "string",
                    },
                },
                ensure_ascii=False,
            ),
        )
        if isinstance(data.get("should_create_card"), bool):
            return data
        text = f"{user_message}\n{assistant_result}"
        compact = re.sub(r"\s+", "", text.lower())
        trivial = len(compact) < 40 or compact in {"hi", "hello", "你好", "在不", "ok"}
        should = not trivial and any(
            key in compact
            for key in ("失败", "错误", "bug", "工具", "下载", "网页", "搜索", "学习", "skill", "tool", "error", "code", "记住", "习惯")
        )
        return {
            "should_create_card": should,
            "learning_content": _safe_text(assistant_result or user_message, 900),
            "learning_goal": "Extract reusable knowledge or improve the agent behavior.",
            "need_web_learning": self._looks_web_worthy(text),
            "web_queries": [user_message[:100]] if self._looks_web_worthy(text) else [],
            "required_skills": [],
            "required_tools": [],
            "expected_artifact": self._expected_artifact("", text),
            "risk_level": "A2",
            "source_summary": _safe_text(assistant_result or user_message, 400),
        }

    def triage_card(self, card_id: str, *, model_client: Any = None) -> dict[str, Any] | None:
        card = self.store.load(card_id)
        if not card:
            return None
        status = str(card.get("status") or "")
        if status not in {"raw_candidate", "duplicate_checking", "upgrade_checking", "value_checking"}:
            return card
        card["status"] = "duplicate_checking"
        self.store.save(card)
        duplicate = self._find_duplicate(card)
        triage = card.setdefault("triage", {})
        if duplicate:
            triage["duplicate_decision"] = "duplicate"
            triage["duplicate_refs"] = [duplicate["card_id"]]
            card["status"] = "upgrade_checking"
            self.store.save(card)
            if self._can_upgrade(card, duplicate, model_client):
                artifact = str((card.get("plan") or {}).get("expected_artifact") or "skill")
                card.setdefault("plan", {})["expected_artifact"] = "tool_upgrade" if "tool" in artifact else "skill_upgrade"
                triage["upgrade_decision"] = "upgrade"
                triage["upgrade_target"] = duplicate["card_id"]
            else:
                card["status"] = "duplicate_removed"
                triage["upgrade_decision"] = "no_upgrade"
                triage["discard_reason"] = "duplicate_without_upgrade_value"
                card.setdefault("lifecycle", {})["deleted_from_queue"] = True
                return self.store.save(card)
        else:
            triage["duplicate_decision"] = "unique"
        card["status"] = "value_checking"
        self.store.save(card)
        if not self._has_learning_value(card, model_client):
            card["status"] = "no_value"
            triage["value_decision"] = "no_value"
            triage["discard_reason"] = "llm_or_heuristic_no_learning_value"
            card.setdefault("lifecycle", {})["deleted_from_queue"] = True
            return self.store.save(card)
        triage["value_decision"] = "valuable"
        card["status"] = "formal_ready"
        return self.store.save(card)

    def execute_card(
        self,
        card_id: str,
        *,
        model_client: Any = None,
        run_mode: str = "manual_now",
        web_searcher: Callable[[str], str] | None = None,
    ) -> str:
        card = self.store.next_ready() if card_id in {"", "__next__", "next"} else self.store.load(card_id)
        if not card:
            return "没有找到可学习的学习卡。"
        if str(card.get("status") or "") not in READY_STATUSES:
            card = self.triage_card(str(card.get("card_id") or ""), model_client=model_client) or card
        if str(card.get("status") or "") not in READY_STATUSES:
            return f"学习卡当前状态不是待学习：{card.get('status')}"
        content = (card.get("content") or {}).get("learning_content", "")
        card["status"] = "learning_running"
        card.setdefault("execution", {})["run_mode"] = run_mode
        card["execution"]["started_at"] = _now()
        card["execution"]["attempt_count"] = int(card["execution"].get("attempt_count") or 0) + 1
        card.setdefault("lifecycle", {})["user_start_message_sent"] = True
        self.store.save(card)
        start_msg = f"我要开始学习啦：{content[:180]}"
        try:
            result = self._simple_learning(card, model_client, web_searcher=web_searcher)
        except Exception as exc:
            result = f"学习执行失败：{exc.__class__.__name__}: {str(exc)[:240]}"
        card = self.store.load(str(card.get("card_id") or "")) or card
        card["execution"]["learning_result"] = _safe_text(result, 3000)
        card["execution"]["finished_at"] = _now()
        card["status"] = "learning_done"
        card["lifecycle"]["user_done_message_sent"] = True
        self.store.save(card)
        done_msg = f"我学完了：{_safe_text(result, 500)}"
        qa_ok, qa_report = self._qa_result(card)
        card["status"] = "qa_running"
        card.setdefault("qa", {})["qa_status"] = "pass" if qa_ok else "failed"
        card["qa"]["qa_report"] = qa_report
        self.store.save(card)
        if not qa_ok:
            card["status"] = "failed_retryable"
            card["qa"]["retry_or_block_reason"] = qa_report
            self.store.save(card)
            return f"{start_msg}\n\n{done_msg}\n\n质检未通过：{qa_report}"
        card["status"] = "alignment_running"
        self.store.save(card)
        alignment_summary = self._align_card(card)
        card = self.store.load(str(card.get("card_id") or "")) or card
        card["status"] = "completed"
        card.setdefault("alignment", {})["alignment_status"] = "completed"
        card.setdefault("lifecycle", {})["final_message_sent"] = True
        card["lifecycle"]["archived_at"] = _now()
        card["lifecycle"]["deleted_from_queue"] = True
        self.store.save(card)
        return f"{start_msg}\n\n{done_msg}\n\n归类完成：{alignment_summary}"

    def _collect_web_evidence(self, card: dict[str, Any], web_searcher: Callable[[str], str] | None) -> str:
        plan = card.get("plan") if isinstance(card.get("plan"), dict) else {}
        if not bool(plan.get("need_web_learning")) or not callable(web_searcher):
            return ""
        content = card.get("content") if isinstance(card.get("content"), dict) else {}
        queries = self._string_list(plan.get("web_queries"), fallback=[str(content.get("learning_goal") or content.get("learning_content") or "")[:120]])
        rows: list[str] = []
        for index, query in enumerate(queries[:3], start=1):
            q = str(query or "").strip()
            if not q:
                continue
            try:
                evidence = _safe_text(web_searcher(q), 2500)
            except Exception as exc:
                evidence = f"联网学习失败：{exc.__class__.__name__}: {str(exc)[:220]}"
            if evidence:
                rows.append(f"[web:{index}] query={q}\n{evidence}")
        summary = "\n\n".join(rows)[:7000]
        if summary:
            card.setdefault("content", {})["web_evidence_summary"] = summary[:3000]
            card.setdefault("execution", {}).setdefault("execution_log_refs", []).append("inline:web_evidence_summary")
            self.store.save(card)
        return summary

    def _simple_learning(self, card: dict[str, Any], client: Any, *, web_searcher: Callable[[str], str] | None = None) -> str:
        content = (card.get("content") or {}).get("learning_content", "")
        web_evidence = self._collect_web_evidence(card, web_searcher)
        plan = card.get("plan") if isinstance(card.get("plan"), dict) else {}
        data = self._llm_json(
            client,
            (
                "Learn from this autonomous learning card. Return JSON only. "
                + LEARNING_CARD_SYSTEM_RULES
                + " "
                "Create reusable agent knowledge, concrete rules, and any tool or skill upgrade notes. "
                "If web evidence is provided, synthesize it instead of copying it. "
                "Keep failures as diagnostics unless QA and alignment make them reusable."
            ),
            json.dumps(
                {
                    "learning_content": content,
                    "learning_goal": (card.get("content") or {}).get("learning_goal", ""),
                    "expected_artifact": plan.get("expected_artifact", "knowledge"),
                    "need_web_learning": bool(plan.get("need_web_learning")),
                    "web_evidence": web_evidence[:5000],
                    "required_json": {
                        "summary": "string",
                        "rules": ["string"],
                        "tool_or_skill_upgrade_notes": ["string"],
                        "validation_notes": "string",
                    },
                },
                ensure_ascii=False,
            ),
            max_tokens=900,
        )
        if data:
            return _safe_text(data.get("summary") or json.dumps(data, ensure_ascii=False), 1600)
        fallback = content
        if web_evidence:
            fallback = f"{content}\n\n联网补充：{web_evidence[:1200]}"
        return _safe_text(fallback, 1800)

    def _find_duplicate(self, card: dict[str, Any]) -> dict[str, Any] | None:
        text = (card.get("content") or {}).get("learning_content", "")
        best: tuple[float, dict[str, Any] | None] = (0.0, None)
        for other in self.store.all_cards():
            if other.get("card_id") == card.get("card_id"):
                continue
            if str(other.get("status") or "") in TERMINAL_DISCARDED:
                continue
            other_text = (other.get("content") or {}).get("learning_content", "")
            score = SequenceMatcher(None, text[:1200], other_text[:1200]).ratio()
            if score > best[0]:
                best = (score, other)
        return best[1] if best[0] >= 0.88 else None

    def _can_upgrade(self, card: dict[str, Any], duplicate: dict[str, Any], client: Any) -> bool:
        data = self._llm_json(
            client,
            "Decide if a duplicate learning card can upgrade the existing card. Return JSON only. "
            + LEARNING_CARD_SYSTEM_RULES
            + " Upgrade only when the new card adds a concrete rule, tool behavior, skill step, or verified correction.",
            json.dumps(
                {
                    "new_card": (card.get("content") or {}).get("learning_content", "")[:1000],
                    "existing_card": (duplicate.get("content") or {}).get("learning_content", "")[:1000],
                    "required_json": {"can_upgrade": "boolean", "reason": "string"},
                },
                ensure_ascii=False,
            ),
            max_tokens=300,
        )
        if isinstance(data.get("can_upgrade"), bool):
            return bool(data["can_upgrade"])
        text = ((card.get("content") or {}).get("learning_content", "") + " " + (duplicate.get("content") or {}).get("learning_content", "")).lower()
        return any(k in text for k in ("upgrade", "升级", "修复", "bug", "error", "tool", "skill"))

    def _has_learning_value(self, card: dict[str, Any], client: Any) -> bool:
        content = (card.get("content") or {}).get("learning_content", "")
        data = self._llm_json(
            client,
            "Decide if this learning card has durable learning value. Return JSON only. "
            + LEARNING_CARD_SYSTEM_RULES
            + " Reject cards that are only pseudo tool text, raw errors without reusable diagnosis, short chatter, or duplicated noise.",
            json.dumps({"learning_content": content[:1400], "required_json": {"has_value": "boolean", "reason": "string"}}, ensure_ascii=False),
            max_tokens=300,
        )
        if isinstance(data.get("has_value"), bool):
            return bool(data["has_value"])
        compact = re.sub(r"\s+", "", content.lower())
        if len(compact) < 30:
            return False
        if compact.startswith("<tool_call>") or compact.startswith("<||dsml"):
            return False
        return any(k in compact for k in ("失败", "错误", "工具", "网页", "下载", "搜索", "代码", "学习", "skill", "tool", "error", "bug", "流程"))

    def _priority_decision(self, content: str, user_message: str, assistant_result: str, client: Any) -> tuple[str, int, str]:
        data = self._llm_json(
            client,
            "Assign learning priority. Return JSON only. "
            + LEARNING_CARD_SYSTEM_RULES
            + " Higher priority means it blocks user work, fixes repeated execution failures, or expands high-frequency capabilities.",
            json.dumps(
                {
                    "learning_content": content[:1200],
                    "user_message": user_message[:800],
                    "assistant_result": assistant_result[:800],
                    "priority_rules": {
                        "P0": "blocks user work or repeated execution failures",
                        "P1": "high frequency or major ability gap",
                        "P2": "normal reusable knowledge",
                        "P3": "low frequency preference",
                        "P4": "optional exploration",
                    },
                    "required_json": {"priority": "P0|P1|P2|P3|P4", "priority_score": "0-100", "priority_reason": "string"},
                },
                ensure_ascii=False,
            ),
            max_tokens=300,
        )
        try:
            score = int(data.get("priority_score"))
            score = max(0, min(100, score))
            name = str(data.get("priority") or _priority_bucket(score))
            if name not in {"P0", "P1", "P2", "P3", "P4"}:
                name = _priority_bucket(score)
            reason = _safe_text(data.get("priority_reason") or "LLM priority decision.", 240)
            return name, score, reason
        except Exception:
            return _fallback_priority(f"{content}\n{user_message}\n{assistant_result}")

    def _align_card(self, card: dict[str, Any]) -> str:
        artifact = str((card.get("plan") or {}).get("expected_artifact") or "knowledge")
        refs: list[str] = []
        alignment = card.setdefault("alignment", {})
        if "skill" in artifact:
            ref = self.store.append_alignment_record("skill", card)
            alignment.setdefault("skill_refs", []).append(ref)
            refs.append(f"技能:{ref}")
        if "tool" in artifact:
            ref = self.store.append_alignment_record("tool", card)
            alignment.setdefault("tool_refs", []).append(ref)
            refs.append(f"工具:{ref}")
        if artifact == "memory":
            ref = self.store.append_alignment_record("memory", card)
            alignment.setdefault("memory_refs", []).append(ref)
            refs.append(f"记忆:{ref}")
        if not refs or artifact == "knowledge":
            ref = self.store.append_alignment_record("knowledge", card)
            alignment.setdefault("knowledge_refs", []).append(ref)
            refs.append(f"知识:{ref}")
        self.store.save(card)
        return "，".join(refs)

    def _qa_result(self, card: dict[str, Any]) -> tuple[bool, str]:
        result = str((card.get("execution") or {}).get("learning_result") or "")
        lowered = result.lower()
        if not result.strip():
            return False, "学习结果为空。"
        if any(k in lowered for k in ("traceback", "permissionerror", "[llm", "学习执行失败")):
            return False, "学习过程中出现错误，需要重试。"
        return True, "学习结果非空且未发现明显执行错误。"

    def snapshot(self, *, limit: int = 12) -> dict[str, Any]:
        cards = self.store.all_cards()
        active = [c for c in cards if str(c.get("status") or "") not in TERMINAL_DISCARDED]
        counters = {
            "pending_learning": 0,
            "candidate_ready": 0,
            "learned": 0,
            "learned_no_asset": 0,
            "failed": 0,
            "running": 0,
            "discarded": 0,
        }
        latest: list[dict[str, Any]] = []
        for card in cards:
            status = str(card.get("status") or "")
            public_status = self._public_status(status)
            if status in READY_STATUSES:
                counters["pending_learning"] += 1
                counters["candidate_ready"] += 1
            elif status in RUNNING_STATUSES:
                counters["running"] += 1
            elif status in TERMINAL_COMPLETED:
                counters["learned"] += 1
            elif status in TERMINAL_FAILED:
                counters["failed"] += 1
            elif status in TERMINAL_DISCARDED:
                counters["discarded"] += 1
            content = card.get("content") if isinstance(card.get("content"), dict) else {}
            source = card.get("source") if isinstance(card.get("source"), dict) else {}
            priority = card.get("priority") if isinstance(card.get("priority"), dict) else {}
            execution = card.get("execution") if isinstance(card.get("execution"), dict) else {}
            plan = card.get("plan") if isinstance(card.get("plan"), dict) else {}
            triage = card.get("triage") if isinstance(card.get("triage"), dict) else {}
            latest.append(
                {
                    "id": str(card.get("card_id") or ""),
                    "source": str(source.get("route") or source.get("trigger_type") or ""),
                    "summary": str(content.get("learning_content") or "")[:500],
                    "task_preview": str(source.get("user_message") or "")[:300],
                    "learning_result": str(execution.get("learning_result") or "")[:500],
                    "status": public_status,
                    "raw_status": status,
                    "can_manual_learn": status in READY_STATUSES,
                    "can_delete": status in READY_STATUSES or status in {"raw_candidate", "value_checking"},
                    "has_skill": "skill" in str(plan.get("expected_artifact") or ""),
                    "skill_name": str(plan.get("expected_artifact") or "")[:120],
                    "has_tool": "tool" in str(plan.get("expected_artifact") or ""),
                    "tool_name": str(plan.get("expected_artifact") or "")[:120],
                    "created_at": card.get("created_at"),
                    "priority": str(priority.get("priority") or ""),
                    "priority_score": int(priority.get("priority_score") or 0),
                    "priority_reason": str(priority.get("priority_reason") or "")[:240],
                    "judge_reason": str(triage.get("value_decision") or "")[:120],
                    "last_error": str((card.get("qa") or {}).get("retry_or_block_reason") or "")[:240],
                }
            )
        latest.sort(key=lambda row: (0 if row["status"] == "pending_learning" else 1, -int(row.get("priority_score") or 0), float(row.get("created_at") or 0)))
        status = "pending" if counters["pending_learning"] else ("running" if counters["running"] else ("synced" if active else "empty"))
        return {
            "schema": SNAPSHOT_SCHEMA,
            "status": status,
            "path": str(self.store.root),
            "total": len(active),
            **counters,
            "latest": latest[:limit],
        }

    @staticmethod
    def _public_status(status: str) -> str:
        if status in READY_STATUSES:
            return "pending_learning"
        if status in RUNNING_STATUSES or status in {"learning_done"}:
            return "pending"
        if status in TERMINAL_COMPLETED:
            return "learned"
        if status in TERMINAL_FAILED:
            return "failed"
        if status in TERMINAL_DISCARDED:
            return "skipped_by_judge"
        return "pending_approval"

    @staticmethod
    def _string_list(value: Any, *, fallback: list[str] | None = None) -> list[str]:
        if isinstance(value, list):
            return [_safe_text(v, 160) for v in value if _safe_text(v, 160)][:8]
        if isinstance(value, str) and value.strip():
            return [_safe_text(value, 160)]
        return fallback or []

    @staticmethod
    def _looks_web_worthy(text: str) -> bool:
        compact = str(text or "").lower()
        return any(k in compact for k in ("网页", "搜索", "论文", "github", "http", "下载", "web", "paper", "latest"))

    @staticmethod
    def _expected_artifact(value: Any, text: str) -> str:
        raw = str(value or "").strip().lower()
        allowed = {"knowledge", "skill", "tool", "memory", "skill_upgrade", "tool_upgrade"}
        if raw in allowed:
            return raw
        compact = str(text or "").lower()
        if any(k in compact for k in ("用户习惯", "偏好", "记住", "memory")):
            return "memory"
        if any(k in compact for k in ("tool", "工具", "下载器", "网页下载")):
            return "tool"
        if any(k in compact for k in ("skill", "技能", "流程", "sop")):
            return "skill"
        return "knowledge"
