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
    selected = _safe(user_selected_mode, 40) or "未显式选择"
    hint = _safe(context_hint, 1800)
    lines = [
        "[ActivationFormSpec / 主脑填空激活协议 / L6.73.8-Q23]",
        "Runtime 不能替 LLM 做入口硬路由；Runtime 只提交本填空题材料给 PromptCompiler。",
        "PromptCompiler 是唯一提示词整合出口；你必须在完整上下文中填写 ActivationForm。",
        f"user_selected_mode={selected}；该值只是用户显式偏好，不是 Runtime 硬路由。",
        "第一层必须填写 intent_type 三分类：chat / consult / execute。",
        "chat：情绪表达、寒暄、闲聊、哲学唠嗑、普通问答；不需要 Skill，不进 Runner。",
        "consult：解释报错、分析方案风险、阅读用户已给文本、只读日志/文档分析；允许只读 Skill，不写文件、不运行命令、不打包。",
        "execute：打开/读取本地文件、写入/修改、运行脚本、整理目录、修复 bug、打包交付、长链验收；才进入完整 Runtime/工具链。",
        "mode 仅为兼容字段，只允许 chat/work：intent_type=chat/consult 时 mode=chat；intent_type=execute 时 mode=work。",
        "tool_policy 只能是 none/readonly/full：chat=none，consult=readonly，execute=full。",
        "Skill 匹配必须填写 skill_match_status：exact/fuzzy/none；匹配不到时不得报 Runtime 错，按 fallback_action 降级。",
        "fallback_action 只能是 answer_as_chat/answer_as_consult/ask_clarify/block/execute。",
        "A0-A4 仍走 Runtime/QualityGate 审计链；A5 极高危必须硬拦或人工确认，不能被简化流程绕开。",
        "consult 不得把 tools_requested 置为 true；需要读本地路径、运行命令、写文件或打包时才是 execute。",
        "execute 若会改变文件/运行命令，应给 confirmation_text：例如“我理解为：检查项目、修复并复测。开始吗？”",
        "work_type 只能是：none/file/document/code/terminal/desktop/web/mixed。",
        "execution_depth 只能是：single_turn/single_step/multi_step/long_chain。长链是执行深度，不是用户模式。",
        "必须只输出合法 JSON，不附加解释、Markdown 或代码块。",
        "JSON schema:",
        '{"intent_type":"chat|consult|execute","mode":"chat|work","tool_policy":"none|readonly|full","skill_match_status":"exact|fuzzy|none","skill_id":"optional","skill_name":"optional","fallback_action":"answer_as_chat|answer_as_consult|ask_clarify|block|execute","work_type":"none|file|document|code|terminal|desktop|web|mixed","execution_depth":"single_turn|single_step|multi_step|long_chain","tools_requested":true|false,"required_tool_classes":["file_read","document_parse"],"risk_level":"A0|A1|A2|A3|A4|A5","need_quality_gate":true|false,"need_user_confirm":true|false,"confirmation_text":"...","expected_result":"...","final_output_contract":"answer_only|execution_report|artifact_delivery","reason":"简短裁决理由"}',
    ]
    if hint:
        lines.extend(["[recent_context_hint]", hint])
    return "\n".join(lines)


def activation_execution_card(form: ActivationForm, *, context_hint: str = "") -> str:
    lines = [
        "[ActivationForm / LLM已填写的激活答案 / Runtime只校验不重判]",
        json.dumps(form.public_dict(), ensure_ascii=False, indent=2),
        "执行阶段规则：",
        "- intent_type=chat 不得执行工具；intent_type=consult 只读分析；intent_type=execute 才能进入真实执行。",
        "- Runtime 按上面的 intent_type/tool_policy/work_type/execution_depth/tools_requested 装配工具与长链协议。",
        "- 你现在必须输出可审计 JSON plan；不得只给建议而不执行。",
        "- 创建文件必须规划真实写入工具；列目录必须规划目录工具；修改代码必须读取/修改/验证。",
        "- 工具失败后应继续诊断、降级、替代路径或明确失败点。",
        "- 长链任务必须阶段化：Plan -> Act -> Observe -> Verify -> Replan/Continue -> Checkpoint -> Final。",
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
