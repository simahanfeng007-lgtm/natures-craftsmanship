"""受治理执行脊。"""

from __future__ import annotations

from tiangong_agent_shell.tool_bridge import ToolExecutionMode

from .audit_bridge import AuditBridge
from .confirmation_ticket import ConfirmationTicketStore
from .execution_policy import PermitStatus, RiskLevel
from .risk_classifier import RiskClassifier, build_security_classifier
from .runtime_tool_registry import RuntimeToolRegistry
from .turn_context import TurnContext
from .tool_invocation import ToolInvocation
from .tool_result import ToolResult, ToolResultStatus


class ExecutionSpine:
    def __init__(self, registry: RuntimeToolRegistry, *, audit: AuditBridge | None = None, ticket_store: ConfirmationTicketStore | None = None) -> None:
        self.registry = registry
        self.audit = audit or AuditBridge()
        self.ticket_store = ticket_store or ConfirmationTicketStore()
        self.classifier = build_security_classifier()

    def execute_plan(self, context: TurnContext, plan: list[ToolInvocation]) -> list[ToolResult]:
        results: list[ToolResult] = []
        for invocation in plan:
            result = self._execute_one(invocation, context)
            results.append(result)
        return results

    def _execute_one(self, invocation: ToolInvocation, context: TurnContext) -> ToolResult:
        adapter = self.registry.get(invocation.tool_name)
        if adapter is None:
            return ToolResult(step_id=invocation.step_id, tool_name=invocation.tool_name, status=ToolResultStatus.FAILED, output_summary=f"未注册工具: {invocation.tool_name}", error_code="tool_not_found")
        risk_level, reason = self.classifier.classify(invocation)
        if risk_level == RiskLevel.A5:
            return ToolResult(step_id=invocation.step_id, tool_name=invocation.tool_name, status=ToolResultStatus.BLOCKED, output_summary=f"A5 阻断: {reason}", error_code="a5_blocked")
        # A0-A4 自动放行（仅审计）
        try:
            result = adapter(invocation, context)
            try:
                self.audit.record(invocation, risk_level=risk_level, permit_status=PermitStatus.ALLOWED, result=result)
            except Exception:
                pass
            return result
        except Exception as exc:
            return ToolResult(step_id=invocation.step_id, tool_name=invocation.tool_name, status=ToolResultStatus.FAILED, output_summary=f"执行异常: {exc}", error_code="execution_error")
