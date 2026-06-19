"""L6.72.51/L6.73.8 主脑填空激活协议。

Q23 起，ActivationForm 不再只表达 chat/work 二元门，而是在兼容旧字段的
基础上增加三分类意图：

- intent_type=chat：纯对话，不进 Runner，不激活 Skill/Tool。
- intent_type=consult：半结构化咨询，只允许只读 Skill/分析上下文；不写文件、
  不运行命令、不打包。
- intent_type=execute：硬执行任务，才允许进入 Runtime/Planner/Tool 链，并继续
  走 QualityGate/A5/审计/回滚。

兼容边界：
旧调用方仍可只输出 mode/tools_requested；parse_activation_form 会推导
intent_type/tool_policy/skill_match_status。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

ALLOWED_MODES = {"chat", "work"}
ALLOWED_INTENT_TYPES = {"chat", "consult", "execute"}
ALLOWED_TOOL_POLICIES = {"none", "readonly", "full"}
ALLOWED_SKILL_MATCH_STATUS = {"exact", "fuzzy", "none"}
ALLOWED_FALLBACK_ACTIONS = {"answer_as_chat", "answer_as_consult", "ask_clarify", "block", "execute"}
ALLOWED_WORK_TYPES = {"none", "file", "document", "code", "terminal", "desktop", "web", "mixed"}
ALLOWED_EXECUTION_DEPTHS = {"single_turn", "single_step", "multi_step", "long_chain"}
ALLOWED_RISK_LEVELS = {"A0", "A1", "A2", "A3", "A4", "A5"}
ALLOWED_FINAL_OUTPUT_CONTRACTS = {"answer_only", "execution_report", "artifact_delivery"}


@dataclass(frozen=True)
class ActivationForm:
    mode: str = "chat"
    work_type: str = "none"
    execution_depth: str = "single_turn"
    tools_requested: bool = False
    required_tool_classes: tuple[str, ...] = tuple()
    risk_level: str = "A0"
    need_quality_gate: bool = False
    need_user_confirm: bool = False
    expected_result: str = ""
    final_output_contract: str = "answer_only"
    reason: str = ""
    # Q23 三分类意图与 Skill 降级字段；旧 JSON 不带这些字段时由 parser 推导。
    intent_type: str = "chat"
    tool_policy: str = "none"
    skill_match_status: str = "none"
    skill_id: str = ""
    skill_name: str = ""
    fallback_action: str = "answer_as_chat"
    confirmation_text: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        # Historical code may instantiate ActivationForm(mode="work",
        # tools_requested=True) directly without Q23 fields.  Public projection
        # must not serialize that legacy execute form as intent_type=chat, or a
        # later parse_activation_form() roundtrip will incorrectly collapse it.
        legacy_execute = (
            self.intent_type in {"", "chat"}
            and self.mode == "work"
            and bool(self.tools_requested)
            and self.tool_policy in {"", "none", "full"}
        )
        intent_type = "execute" if legacy_execute else self.intent_type
        tool_policy = "full" if legacy_execute else self.tool_policy
        skill_match_status = "fuzzy" if legacy_execute and self.skill_match_status == "none" else self.skill_match_status
        fallback_action = "execute" if legacy_execute and self.fallback_action == "answer_as_chat" else self.fallback_action
        return {
            "mode": self.mode,
            "intent_type": intent_type,
            "tool_policy": tool_policy,
            "skill_match_status": skill_match_status,
            "skill_id": self.skill_id,
            "skill_name": self.skill_name,
            "fallback_action": fallback_action,
            "work_type": self.work_type,
            "execution_depth": self.execution_depth,
            "tools_requested": self.tools_requested,
            "required_tool_classes": list(self.required_tool_classes),
            "risk_level": self.risk_level,
            "need_quality_gate": self.need_quality_gate,
            "need_user_confirm": self.need_user_confirm,
            "confirmation_text": self.confirmation_text,
            "expected_result": self.expected_result,
            "final_output_contract": self.final_output_contract,
            "reason": self.reason,
        }

    @property
    def activates_runtime_tools(self) -> bool:
        # Backward compatibility: many historical smokes instantiate
        # ActivationForm(mode="work", tools_requested=True) directly without
        # passing Q23 intent_type/tool_policy.  Treat that legacy shape as
        # execute/full so old Runtime tests do not silently degrade to chat.
        legacy_execute = (
            self.intent_type in {"", "chat"}
            and self.mode == "work"
            and bool(self.tools_requested)
            and self.tool_policy in {"", "none", "full"}
        )
        return (
            (self.intent_type == "execute" or legacy_execute)
            and self.mode == "work"
            and self.tool_policy in {"", "none", "full"}
            and self.tools_requested
            and self.risk_level != "A5"
        )

    @property
    def readonly_consult(self) -> bool:
        return self.intent_type == "consult" and self.tool_policy == "readonly"


def activation_schema_card(*, user_selected_mode: str = "", context_hint: str = "") -> str:
    selected = _safe(user_selected_mode, 40) or "not explicitly selected"
    hint = _safe(context_hint, 1800)
    lines = [
        "[ActivationFormSpec / Brain-Filled Activation Protocol / L6.73.8-Q23]",
        "Runtime must not hard-route the entry on behalf of the LLM. Runtime only submits this decision material to PromptCompiler.",
        "PromptCompiler is the only prompt integration outlet. Fill ActivationForm using the complete context.",
        f"user_selected_mode={selected}; this is only an explicit user preference, not a Runtime hard route.",
        "The first layer must fill intent_type as exactly one of chat, consult, execute.",
        "Double-decision rule: first use fixed words/fixed actions. If the fixed decision is chat, re-evaluate with recent context so 'continue', 'work', 'you stopped again', or 'start now' is not misclassified as casual chat.",
        "chat: emotion expression, greetings, casual talk, philosophy, or general Q&A. No Skill is needed and Runner must not start.",
        "consult: explain errors, analyze plan risks, read user-provided text, or analyze logs/docs read-only. Read-only Skill is allowed; no file writes, commands, or packaging.",
        "execute: open/read local files, write/modify, run scripts, organize directories, fix bugs, download from web pages, test, package, deliver, or long-chain acceptance. Only this enters the full Runtime/tool chain.",
        "Fixed execute actions include save, download, open path, read path, write, delete, move, copy, run, test, repair, package, deploy, quality-check, verify, and continue the previous unfinished work.",
        "Fixed consult actions include explain an error, analyze a plan, judge risk, discuss architecture, read pasted text/screenshots, and understand a phenomenon read-only. Do not casually modify files or run commands.",
        "mode is only a compatibility field: chat/consult => mode=chat; execute => mode=work.",
        "tool_policy must be none/readonly/full: chat=none, consult=readonly, execute=full.",
        "Skill matching must fill skill_match_status: exact/fuzzy/none. If no match exists, do not report a Runtime error; degrade through fallback_action.",
        "fallback_action must be answer_as_chat/answer_as_consult/ask_clarify/block/execute.",
        "A0-A4 still go through Runtime/QualityGate audit. A5 extreme risk must hard-block or require human confirmation and cannot be bypassed by a simplified flow.",
        "consult must not set tools_requested=true. Reading local paths, running commands, writing files, or packaging is execute.",
        "If execute will change files or run commands, provide confirmation_text, for example: 'I understand the task as checking the project, fixing issues, and retesting. Start?'",
        "work_type must be one of none/file/document/code/terminal/desktop/web/mixed.",
        "execution_depth must be one of single_turn/single_step/multi_step/long_chain. Long-chain is execution depth, not user mode.",
        "Output valid JSON only. Do not add explanations, Markdown, or code fences.",
        "JSON schema:",
        '{"intent_type":"chat|consult|execute","mode":"chat|work","tool_policy":"none|readonly|full","skill_match_status":"exact|fuzzy|none","skill_id":"optional","skill_name":"optional","fallback_action":"answer_as_chat|answer_as_consult|ask_clarify|block|execute","work_type":"none|file|document|code|terminal|desktop|web|mixed","execution_depth":"single_turn|single_step|multi_step|long_chain","tools_requested":true|false,"required_tool_classes":["file_read","document_parse"],"risk_level":"A0|A1|A2|A3|A4|A5","need_quality_gate":true|false,"need_user_confirm":true|false,"confirmation_text":"...","expected_result":"...","final_output_contract":"answer_only|execution_report|artifact_delivery","reason":"short decision reason"}',
    ]
    if hint:
        lines.extend(["[recent_context_hint]", hint])
    return "\n".join(lines)


def activation_execution_card(form: ActivationForm, *, context_hint: str = "") -> str:
    lines = [
        "[ActivationForm / LLM-Filled Activation Answer / Runtime Validates Only]",
        json.dumps(form.public_dict(), ensure_ascii=False, indent=2),
        "Execution-phase rules:",
        "- intent_type=chat must not execute tools. intent_type=consult is read-only analysis. Only intent_type=execute may enter real execution.",
        "- Runtime assembles tools and long-chain protocol according to intent_type, tool_policy, work_type, execution_depth, and tools_requested above.",
        "- You must now output an auditable JSON plan. Do not merely give advice instead of executing.",
        "- File creation must plan a real write tool. Directory listing must plan a directory tool. Code modification must read, modify, and verify.",
        "- Webpage download must plan web_download or an equivalent governed download tool. Do not stop after explaining a download method.",
        "- After tool failure, diagnose, degrade, try an alternative path, or state the failure point clearly.",
        "- Tool failure must not be written directly into long-term memory or formal skills. It may only become a candidate learning card after the task ends and must pass filtering.",
        "- Long-chain tasks must be staged: Plan -> Act -> Observe -> Verify -> Replan/Continue -> Checkpoint -> Final.",
    ]
    hint = _safe(context_hint, 2400)
    if hint:
        lines.extend(["[execution_context_hint]", hint])
    return "\n".join(lines)


def parse_activation_form(raw_text: str) -> ActivationForm:
    data = _extract_json_object(raw_text)
    if not isinstance(data, dict):
        raise ValueError("ActivationForm 不是 JSON object。")

    mode = _normalize_enum(data.get("mode"), ALLOWED_MODES, "chat")
    work_type = _normalize_enum(data.get("work_type"), ALLOWED_WORK_TYPES, "none")
    depth = _normalize_enum(data.get("execution_depth"), ALLOWED_EXECUTION_DEPTHS, "single_turn")
    tools = _bool(data.get("tools_requested"))
    risk = _normalize_risk(data.get("risk_level"))
    final_contract = _normalize_enum(data.get("final_output_contract"), ALLOWED_FINAL_OUTPUT_CONTRACTS, "answer_only")
    required = tuple(_safe(x, 80) for x in _as_list(data.get("required_tool_classes")) if _safe(x, 80))[:16]

    intent_raw = data.get("intent_type", data.get("intent"))
    intent_type = _normalize_enum(intent_raw, ALLOWED_INTENT_TYPES, "")
    tool_policy = _normalize_enum(data.get("tool_policy"), ALLOWED_TOOL_POLICIES, "")
    skill_status = _normalize_enum(data.get("skill_match_status", data.get("skill_match")), ALLOWED_SKILL_MATCH_STATUS, "none")
    fallback_action = _normalize_enum(data.get("fallback_action"), ALLOWED_FALLBACK_ACTIONS, "")

    # 兼容旧二元 ActivationForm：没有 intent_type 时根据 mode/tools/tool_policy 推导。
    if not intent_type:
        if mode == "work" and tools:
            intent_type = "execute"
        elif tool_policy == "readonly" or skill_status in {"exact", "fuzzy"}:
            intent_type = "consult"
        else:
            intent_type = "chat"

    # 三分类是主判定；mode/tools 是兼容投影。
    if intent_type == "chat":
        mode = "chat"
        work_type = "none"
        depth = "single_turn"
        tools = False
        tool_policy = "none"
        required = tuple()
        final_contract = "answer_only"
        skill_status = "none"
        if not fallback_action:
            fallback_action = "answer_as_chat"
    elif intent_type == "consult":
        mode = "chat"
        tools = False
        if tool_policy not in {"readonly"}:
            tool_policy = "readonly"
        if work_type == "none":
            work_type = "mixed"
        if depth == "single_step":
            depth = "single_turn"
        if final_contract != "answer_only":
            final_contract = "answer_only"
        if risk not in {"A0", "A1"}:
            risk = "A1"
        if not fallback_action:
            fallback_action = "answer_as_consult"
    else:  # execute
        mode = "work"
        tools = True
        if tool_policy != "full":
            tool_policy = "full"
        if work_type == "none":
            work_type = "mixed"
        if final_contract == "answer_only":
            final_contract = "execution_report"
        if not fallback_action:
            fallback_action = "execute"

    if mode == "work" and work_type == "none":
        work_type = "mixed"

    return ActivationForm(
        mode=mode,
        work_type=work_type,
        execution_depth=depth,
        tools_requested=tools,
        required_tool_classes=required,
        risk_level=risk,
        need_quality_gate=(_bool(data.get("need_quality_gate")) or bool(tools)) and intent_type == "execute",
        need_user_confirm=_bool(data.get("need_user_confirm")) or risk == "A5",
        expected_result=_safe(data.get("expected_result"), 500),
        final_output_contract=final_contract,
        reason=_safe(data.get("reason"), 400),
        intent_type=intent_type,
        tool_policy=tool_policy,
        skill_match_status=skill_status,
        skill_id=_safe(data.get("skill_id"), 120),
        skill_name=_safe(data.get("skill_name"), 120),
        fallback_action=fallback_action,
        confirmation_text=_safe(data.get("confirmation_text"), 300),
        raw=dict(data),
    )


def activation_failure_message(exc: Exception) -> str:
    return f"ActivationForm 未通过校验：{type(exc).__name__}: {_safe(exc, 220)}。"


def _extract_json_object(raw_text: str) -> Any:
    text = str(raw_text or "").strip()
    if not text:
        raise ValueError("空输出。")
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start < 0:
        raise ValueError("未找到 JSON object。")
    depth = 0
    in_str = False
    escape = False
    for index in range(start, len(text)):
        ch = text[index]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:index + 1])
    raise ValueError("JSON object 未闭合。")


def _normalize_enum(value: Any, allowed: Iterable[str] | set[str], default: str) -> str:
    clean = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    alias = {
        "聊天": "chat",
        "闲聊": "chat",
        "对话": "chat",
        "咨询": "consult",
        "分析": "consult",
        "只读": "readonly",
        "只读分析": "consult",
        "执行": "execute",
        "干活": "work",
        "工作": "work",
        "完整工具": "full",
        "无工具": "none",
        "精确": "exact",
        "模糊": "fuzzy",
        "无匹配": "none",
        "代码": "code",
        "文件": "file",
        "文档": "document",
        "终端": "terminal",
        "桌面": "desktop",
        "网页": "web",
        "长链": "long_chain",
        "single": "single_step",
        "multi": "multi_step",
        "answer_chat": "answer_as_chat",
        "answer_consult": "answer_as_consult",
        "clarify": "ask_clarify",
    }.get(clean, clean)
    allowed_set = set(allowed)
    return alias if alias in allowed_set else default


def _normalize_risk(value: Any) -> str:
    text = str(value or "A0").strip().upper()
    if text in ALLOWED_RISK_LEVELS:
        return text
    m = re.search(r"A[0-5]", text)
    return m.group(0) if m else "A0"


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "需要", "是", "启用"}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str) and value.strip():
        return [x.strip() for x in value.split(",") if x.strip()]
    return []


def _safe(value: Any, limit: int) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text[: max(16, int(limit))]
