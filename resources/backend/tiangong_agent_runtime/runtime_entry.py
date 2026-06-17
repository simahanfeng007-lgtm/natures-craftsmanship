"""Thin RuntimeHost for Tiangong execution.

Runtime is now only an outer host:
- L3 owns orchestration decisions and service requests.
- L4 / the governed execution spine owns real tool execution.
- L5 hosts plugin capability surfaces.
- L6 contributes plugin / skill / projection capabilities only when scheduled.

The public class remains named RuntimeEntry so existing CLI, desktop bridge,
and the upcoming Electron/Web shell can keep using the same import path.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from pathlib import Path
from tempfile import gettempdir
from time import time
from types import SimpleNamespace
from typing import Any
from uuid import uuid4
import json
import os
import re

from tiangong_agent_shell.model_client_port import CompiledPromptEnvelope
from tiangong_agent_shell.tool_bridge import ToolExecutionMode

from .activation_form import ActivationFormDecider
from .activation_protocol import ActivationForm, parse_activation_form
from .adapters.diagnose_project_adapter import build_diagnose_project_adapter
from .adapters.document_context_adapters import document_export_adapter, document_query_adapter, document_rewrite_plan_adapter, document_text_extract_adapter
from .adapters.document_parse_adapter import document_parse_adapter
from .adapters.document_writeback_adapters import document_apply_rewrite_adapter, document_rollback_adapter
from .adapters.model_chat_adapter import model_chat_adapter
from .adapters.network_tools_adapter import dns_resolve_adapter, http_client_adapter, network_request_adapter, protocol_adapter_adapter, web_readability_extract_adapter
from .adapters.project_scan_adapter import build_scan_project_adapter
from .adapters.python_test_adapter import run_python_quality_check_adapter, run_python_tests_adapter
from .adapters.quality_gate_adapter import build_evaluate_quality_gate_adapter
from .adapters.readonly_file_adapter import file_sha256_adapter, list_dir_adapter, read_file_adapter
from .adapters.virtual_return_adapter import return_analysis_adapter, return_code_adapter
from .adapters.web_search_adapter import web_search_adapter
from .adapters.workspace_file_ops_adapter import copy_path_adapter, delete_path_adapter, make_dir_adapter, move_path_adapter
from .adapters.workspace_write_adapter import write_workspace_file_adapter
from .adapters.zip_package_adapter import create_zip_package_adapter

# TG_MULTIMEDIA_IMPORTS_BEGIN
# 把下面 import 追加到 runtime_entry.py 的 adapter import 区域
from .adapters.multimedia_tools_adapter import (
    image_inspect_adapter,
    image_ocr_parse_adapter,
    image_layout_parse_adapter,
    image_region_query_adapter,
    image_compare_adapter,
    image_table_extract_adapter,
    image_chart_extract_adapter,
    image_crop_export_adapter,
    video_inspect_adapter,
    video_keyframe_extract_adapter,
    video_scene_split_adapter,
    video_ocr_parse_adapter,
    video_audio_transcribe_adapter,
    video_subtitle_extract_adapter,
    video_event_timeline_adapter,
    video_compare_adapter,
    image_generate_adapter,
    image_edit_adapter,
    image_inpaint_adapter,
    image_background_remove_adapter,
    image_upscale_adapter,
    image_style_transfer_adapter,
    image_variation_adapter,
    image_text_poster_generate_adapter,
    video_generate_from_text_adapter,
    video_generate_from_images_adapter,
    storyboard_generate_adapter,
    shot_plan_generate_adapter,
    video_avatar_generate_adapter,
    voiceover_generate_adapter,
    subtitle_burn_in_adapter,
    video_render_adapter,
    video_trim_adapter,
    video_concat_adapter,
    video_cut_by_timestamps_adapter,
    video_add_subtitles_adapter,
    video_add_bgm_adapter,
    video_add_transition_adapter,
    video_resize_reframe_adapter,
    video_export_adapter,
    audio_transcribe_adapter,
    audio_diarize_adapter,
    audio_summary_adapter,
    audio_keywords_extract_adapter,
    audio_event_detect_adapter,
    tts_generate_adapter,
    audio_clone_voice_adapter,
    bgm_generate_adapter,
    audio_mix_adapter,
    audio_denoise_adapter,
    audio_normalize_adapter,
    audio_export_adapter,
    media_entity_extract_adapter,
    media_kv_extract_adapter,
    media_topic_extract_adapter,
    media_risk_extract_adapter,
    media_knowledge_extract_adapter,
    multimedia_pipeline_plan_adapter,
    multimedia_asset_manifest_adapter,
    multimedia_batch_plan_adapter,
    multimedia_delivery_package_adapter
)
# TG_MULTIMEDIA_IMPORTS_END

# BEGIN_OPS_EXTENSION_IMPORTS
from tiangong_agent_runtime.adapters.ops_extension_adapters import (
    ops_funnel_map_adapter,
    ops_customer_journey_map_adapter,
    ops_bottleneck_detect_adapter,
    ops_next_best_action_adapter,
    ops_weekly_growth_plan_adapter,
    ops_monthly_revenue_review_adapter,
    market_segment_analyze_adapter,
    icp_profile_build_adapter,
    buyer_persona_build_adapter,
    pain_point_extract_adapter,
    competitor_positioning_map_adapter,
    value_proposition_design_adapter,
    channel_strategy_plan_adapter,
    campaign_plan_build_adapter,
    campaign_budget_plan_adapter,
    channel_roi_estimate_adapter,
    landing_page_audit_adapter,
    event_lead_capture_plan_adapter,
    content_calendar_build_adapter,
    content_topic_cluster_adapter,
    case_study_generate_adapter,
    landing_page_copy_check_adapter,
    short_video_script_generate_adapter,
    conversion_material_pack_adapter,
    lead_signal_extract_adapter,
    lead_fit_score_adapter,
    lead_intent_score_adapter,
    lead_priority_rank_adapter,
    account_score_adapter,
    stakeholder_map_adapter,
    nurture_sequence_generate_adapter,
    wechat_followup_plan_adapter,
    email_sequence_generate_adapter,
    community_operation_plan_adapter,
    touchpoint_log_parse_adapter,
    next_touch_recommend_adapter,
    sales_call_brief_adapter,
    sales_discovery_question_set_adapter,
    spin_need_diagnose_adapter,
    objection_map_build_adapter,
    meeting_summary_to_crm_adapter,
    deal_stage_judge_adapter,
    proposal_outline_build_adapter,
    roi_argument_build_adapter,
    pricing_strategy_plan_adapter,
    decision_risk_detect_adapter,
    closing_plan_generate_adapter,
    contract_handoff_check_adapter,
    crm_pipeline_profile_adapter,
    pipeline_velocity_check_adapter,
    deal_win_probability_adapter,
    multi_touch_attribution_plan_adapter,
    channel_contribution_estimate_adapter,
    revops_dashboard_spec_adapter,
    growth_experiment_design_adapter,
    ab_test_plan_adapter,
    uplift_targeting_plan_adapter,
    bandit_allocation_plan_adapter,
    experiment_result_analyze_adapter,
    growth_retrospective_report_adapter,
)
# END_OPS_EXTENSION_IMPORTS

# BEGIN_ENTERPRISE_EXTENSION_IMPORTS
from tiangong_agent_runtime.adapters.enterprise_extension_adapters import (
    table_profile_adapter,
    table_schema_detect_adapter,
    table_quality_check_adapter,
    table_clean_plan_adapter,
    table_deduplicate_adapter,
    table_filter_adapter,
    table_score_adapter,
    table_join_plan_adapter,
    table_pivot_summary_adapter,
    table_export_adapter,
    browser_open_adapter,
    browser_extract_adapter,
    browser_screenshot_plan_adapter,
    browser_click_plan_adapter,
    browser_type_plan_adapter,
    browser_download_plan_adapter,
    browser_form_fill_plan_adapter,
    browser_session_close_adapter,
    db_connect_check_adapter,
    db_schema_inspect_adapter,
    db_query_readonly_adapter,
    db_query_explain_adapter,
    db_table_profile_adapter,
    db_export_csv_adapter,
    db_import_csv_plan_adapter,
    db_migration_plan_adapter,
    api_request_spec_adapter,
    api_schema_parse_adapter,
    api_auth_check_adapter,
    api_response_extract_adapter,
    api_batch_request_plan_adapter,
    api_webhook_test_plan_adapter,
    api_error_diagnose_adapter,
    eval_case_build_adapter,
    eval_run_plan_adapter,
    eval_compare_adapter,
    eval_regression_check_adapter,
    eval_tool_schema_check_adapter,
    eval_skill_quality_check_adapter,
    eval_report_adapter,
    kb_ingest_plan_adapter,
    kb_chunk_preview_adapter,
    kb_index_plan_adapter,
    kb_search_local_adapter,
    kb_answer_draft_adapter,
    kb_source_trace_adapter,
    kb_update_plan_adapter,
    kb_quality_check_adapter,
    desktop_screenshot_plan_adapter,
    desktop_click_plan_adapter,
    desktop_type_plan_adapter,
    desktop_hotkey_plan_adapter,
    desktop_find_window_plan_adapter,
    desktop_clipboard_plan_adapter,
    desktop_open_app_plan_adapter,
    desktop_file_dialog_plan_adapter,
    lead_score_adapter,
    company_profile_build_adapter,
    contact_plan_generate_adapter,
    sales_script_generate_adapter,
    objection_handle_adapter,
    followup_plan_adapter,
    crm_note_generate_adapter,
    deal_stage_judge_adapter,
    paper_search_plan_adapter,
    paper_read_plan_adapter,
    paper_summarize_adapter,
    paper_compare_adapter,
    paper_method_extract_adapter,
    paper_benchmark_extract_adapter,
    tech_trend_report_adapter,
    app_spec_build_adapter,
    app_scaffold_plan_adapter,
    frontend_page_spec_adapter,
    backend_api_spec_adapter,
    db_schema_generate_adapter,
    app_preview_plan_adapter,
    app_package_plan_adapter,
)
# END_ENTERPRISE_EXTENSION_IMPORTS

from .affective_execution_route import AffectiveExecutionRoute, AffectiveExecutionRouter
from .affective_state import AffectiveState, AffectiveStateEngine, SevenEmotionSignalSources, SixDesireSignalSources
from .audit_bridge import AuditBridge
from .audit_replay import replay_audit_events
from .confirmation_ticket import ConfirmationTicketStore
from .context_memory_bridge import ContextMemoryBridge
from .context_window_manager import ContextWindowManager
from .delivery_manifest import DeliveryManifestBridge, build_create_release_bundle_adapter
from .delivery_standardization import DeliveryStandardizationBridge, build_delivery_standardization_adapter
from .diagnostic_bridge import EngineeringDiagnosticBridge
from .execution_exoskeleton import ExecutionExoskeletonBridge, build_execution_exoskeleton_adapter
from .execution_spine import ExecutionSpine
from .experience_synthesis import ExperienceSynthesisBridge, build_synthesize_experience_adapter
from .forgetting_review_router import ForgetReviewDecision  # ForgetReviewRouter已移除（L6.72执行链简化）
from .free_will_learning_chain import JingyanChi, XuexiLian
from .governance_execution import GovernanceExecutionBridge, build_governance_execution_adapter
from .intent_bridge import IntentResult
from .learning_asset_activation import LearningAssetActivationBridge, build_learning_asset_activation_apply_adapter, build_learning_asset_activation_guide_adapter, build_learning_asset_activation_smoke_adapter, build_learning_asset_activation_status_adapter
from .learning_asset_adapter import LearningAssetAdapterBridge, build_learning_asset_adapter_drill_adapter, build_learning_asset_adapter_guide_adapter, build_learning_asset_adapter_template_list_adapter, build_learning_asset_adapter_template_normalize_adapter, build_learning_asset_adapter_template_smoke_adapter, build_learning_asset_adapter_template_validate_adapter
from .learning_asset_candidate_sandbox import LearningAssetCandidateSandboxBridge, build_learning_asset_candidate_sandbox_build_adapter, build_learning_asset_candidate_sandbox_guide_adapter, build_learning_asset_candidate_sandbox_review_adapter, build_learning_asset_candidate_sandbox_validate_adapter
from .learning_asset_contract import LearningAssetContractBridge, build_learning_asset_contract_guide_adapter, build_learning_asset_contract_normalize_adapter, build_learning_asset_contract_validate_adapter
from .learning_asset_release_gate import LearningAssetReleaseGateBridge, build_learning_asset_release_gate_check_adapter, build_learning_asset_release_gate_guide_adapter
from .learning_asset_sandbox_alignment import LearningAssetSandboxAlignmentBridge, build_learning_asset_sandbox_align_adapter, build_learning_asset_sandbox_guide_adapter, build_learning_asset_sandbox_validate_adapter
from .learning_convergence import LearningConvergenceBridge, build_learning_convergence_adapter
from .lifecycle_coordinator import LifecycleCoordinator, LifecycleRouteBundle
from .long_chain_runner import LongChainRunSummary, LongChainRunner
from .memory_math_core import DecayKernel, ForgettingScoreVector, MemoryCategory
from .memory_recall_router import L640MemoryRecallRoute, MemoryRecallRouter
from .memory_store_bridge import MemoryLevel, MemoryRecord, MemoryStoreBridge
from .p0_system_integration import L638P0SystemIntegrationBridge, build_l6_38_budget_adapter, build_l6_38_handoff_adapter, build_l6_38_p0_report_adapter, build_l6_38_provider_adapter, build_l6_38_skill_adapter
from .p0_system_integration_two import L639P0SystemIntegrationBridge, build_l6_39_audit_adapter, build_l6_39_memory_adapter, build_l6_39_p0_report_adapter, build_l6_39_quality_gate_adapter, build_l6_39_recovery_adapter
from .plan_bridge import PlanBridge
from .planner_context_integration import PlannerContextIntegrationBridge, build_planner_context_integration_adapter
from .planner_execution_controller import PlannerExecutionController, PlannerExecutionReport
from .project_index_bridge import ProjectIndexBridge
from .project_repair_plan import ProjectRepairPlanBridge, build_project_repair_plan_adapter
from .provider_adaptation_shell import ProviderAdaptationBridge, build_provider_adaptation_adapter
from .quality_gate_bridge import QualityGateBridge
from .recovery_coordination import RecoveryCoordinationBridge, build_recovery_coordination_adapter
from .public_projection_bridge import RuntimeProjection, build_public_projection
from .runtime_tool_registry import RuntimeToolRegistry, ToolDescriptor
from .runtime_tool_alignment import build_runtime_llm_drill_adapter, build_runtime_tool_alignment_adapter
from .shell_system_mount import ShellSystemMountBridge, build_shell_system_mount_adapter, discover_runtime_module_files
from .tool_schemas import GONGJU_CANSHU_SCHEMA
from .skill_playbook_router import SkillPlaybookRouter
from .skill_review_queue import SkillReviewQueueBridge, build_queue_skill_candidates_adapter
from .subsystems.codex_subsystem import CodeXSubsystem
from .task_state_ledger import TaskStateLedger
from .tool_invocation import ToolInvocation
from .tool_production_request import ToolProductionRequestBridge, build_queue_tool_production_requests_adapter
from .tool_result import ToolResult, ToolResultStatus
from .turn_context import TurnContext
# BEGIN_WANGWEN_UNIFIED_PIPELINE_IMPORTS
from tiangong_agent_runtime.adapters.wangwen_unified_pipeline_adapters import (
    wangwen_novel_factory_run_adapter,
    wangwen_novel_bible_build_adapter,
    wangwen_chapter_brief_build_adapter,
    wangwen_draft_quality_check_adapter,
    wangwen_revision_plan_build_adapter,
)
# END_WANGWEN_UNIFIED_PIPELINE_IMPORTS


@dataclass(frozen=True)
class RuntimeRunResult:
    intent: IntentResult
    plan: list[ToolInvocation]
    results: list[ToolResult]
    projection: RuntimeProjection
    audit_events: list[dict[str, Any]]
    chain_summary: LongChainRunSummary | None = None
    suggestion_bridge: Any | None = None
    pending_confirmations: list[dict[str, Any]] | None = None
    planner_result: Any | None = None
    planner_execution_report: PlannerExecutionReport | None = None
    activation_form: ActivationForm | None = None
    task_id: str = ""
    model_profile: Any | None = None
    model_execution_policy: Any | None = None
    task_state_snapshot: dict[str, Any] | None = None
    status: str = ""
    failure_kind: str = ""
    provider_status: str = ""
    has_executed_tools: bool = False
    plan_repair_attempted: bool = False
    deterministic_fallback_used: bool = False
    final_output_contract: str = "execution_report"
    user_visible_summary: str = ""
    next_action: str = ""
    context_window_bundle: Any | None = None
    skill_playbook_route: Any | None = None
    active_model_policy: Any | None = None

    @property
    def has_plan(self) -> bool:
        return bool(self.plan)


@dataclass(frozen=True)
class ExecutionActivationSnapshot:
    phase: str
    memory_recall_active: bool
    affective_active: bool
    forgetting_active: bool
    lifecycle_active: bool
    context_usage_ratio: float
    notes: str = ""

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema": "tiangong.runtime_host.activation_snapshot.v1",
            "phase": self.phase,
            "memory_recall_active": self.memory_recall_active,
            "affective_active": self.affective_active,
            "forgetting_active": self.forgetting_active,
            "lifecycle_active": self.lifecycle_active,
            "context_usage_ratio": self.context_usage_ratio,
            "notes": self.notes,
        }


class _LegacyChatCompletionsAdapter:
    """Adapter for older lifecycle modules that still call chat.completions."""

    def __init__(self, client: Any, config: Any) -> None:
        self._client = client
        self._config = config

    def create(
        self,
        *,
        model: str = "",
        messages: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
        temperature: float = 0.1,
        max_tokens: int = 300,
        **_: Any,
    ) -> Any:
        if self._client is None or self._config is None or not callable(getattr(self._client, "chat", None)):
            raise RuntimeError("model client is not available")
        clean_messages: list[dict[str, str]] = []
        for item in messages or ():
            role = str(item.get("role") or "user").strip() or "user"
            content = str(item.get("content") or "")
            if content:
                clean_messages.append({"role": role, "content": content})
        if not clean_messages:
            clean_messages.append({"role": "user", "content": ""})
        if clean_messages[0].get("role") != "system":
            clean_messages.insert(0, {"role": "system", "content": "你是天工造物生命周期后台的简洁助手。"})
        config = self._config
        try:
            requested = int(max_tokens or 0)
            if requested > 0 and not int(getattr(config, "max_tokens", 0) or 0):
                config = replace(config, max_tokens=requested)
        except Exception:
            pass
        envelope = CompiledPromptEnvelope(
            messages=tuple(clean_messages),
            compiled_prompt_id=f"legacy_lifecycle_{uuid4().hex[:12]}",
            phase="lifecycle_learning",
            output_contract="json_or_text",
            metadata={
                "legacy_completion_shim": True,
                "requested_model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        result = self._client.chat(envelope, config)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=str(getattr(result, "content", "") or ""))
                )
            ]
        )


def _legacy_completion_client(client: Any, config: Any | None = None) -> Any:
    """Return a chat.completions-compatible view when the runtime has a ModelClientPort."""

    if client is None:
        return None
    chat = getattr(client, "chat", None)
    completions = getattr(chat, "completions", None)
    if callable(getattr(completions, "create", None)):
        return client
    if callable(chat) and config is not None:
        return SimpleNamespace(
            model=str(getattr(config, "model", "") or ""),
            chat=SimpleNamespace(completions=_LegacyChatCompletionsAdapter(client, config)),
        )
    return client


class RuntimeEntry:
    """Compatibility entrypoint backed by a thin RuntimeHost."""

    FORGETTING_CONTEXT_THRESHOLD = 0.80

    def __init__(
        self,
        registry: RuntimeToolRegistry | None = None,
        audit: AuditBridge | None = None,
        ticket_store: ConfirmationTicketStore | None = None,
        memory_store: MemoryStoreBridge | None = None,
    ) -> None:
        self.audit = audit or AuditBridge()
        self.ticket_store = ticket_store or ConfirmationTicketStore()
        self.registry = registry or build_default_registry()
        self.spine = ExecutionSpine(self.registry, audit=self.audit, ticket_store=self.ticket_store)
        self.plan_bridge = PlanBridge()
        self.intent_bridge: Any = None  # 已迁移至cli_loop合并判定，保留占位
        self.activation_decider = ActivationFormDecider()
        self.context_memory = ContextMemoryBridge()
        self.task_state_ledger = TaskStateLedger()
        self.context_window = ContextWindowManager()
        self.skill_playbook_router = SkillPlaybookRouter()
        self.planner_execution = PlannerExecutionController()
        self.lifecycle_coordinator = LifecycleCoordinator()
        self._affective_engine = AffectiveStateEngine()
        self._affective_router = AffectiveExecutionRouter()
        self._memory_store = memory_store or _default_memory_store()
        self._affective_state: AffectiveState | None = None
        self._affective_route: AffectiveExecutionRoute | None = None
        self._last_affective_update_at: float | None = None
        self._affective_turn_count = 0
        self._lunshu: int = self._huifu_lunshu()  # 记忆系统轮数计数器（2000轮触发遗忘，持久化跨重启）
        self.codex: CodeXSubsystem = CodeXSubsystem()
        self._last_memory_recall_route: L640MemoryRecallRoute | None = None
        self._last_memory_recall_error = ""
        self._last_forget_review_decisions: tuple[ForgetReviewDecision, ...] = tuple()
        self._last_forget_review_error = ""
        self._last_lifecycle_bundle: LifecycleRouteBundle | None = None
        self._last_activation_snapshot = ExecutionActivationSnapshot(
            phase="init",
            memory_recall_active=False,
            affective_active=False,
            forgetting_active=False,
            lifecycle_active=False,
            context_usage_ratio=0.0,
            notes="RuntimeHost initialized; lifecycle is deferred until idle/post-task heartbeat.",
        )
        self.last_result: RuntimeRunResult | None = None
        self.jingyan_chi = JingyanChi()
        self.experience_bridge = ExperienceSynthesisBridge()

    def run_model_chat(
        self,
        messages: list[dict[str, str]],
        *,
        model_config: Any | None = None,
        model_client: Any | None = None,
        workspace: str | Path | None = None,
        user_message: str = "",
        tool_mode: str | ToolExecutionMode = ToolExecutionMode.RUNTIME_GOVERNED,
        max_steps: int = 1,
        compiled_prompt: Any | None = None,
    ) -> RuntimeRunResult:
        context = TurnContext.create(
            user_message or (messages[-1].get("content", "") if messages else "model_chat"),
            workspace=workspace,
            tool_mode=tool_mode,
            max_steps=max_steps,
            model_config=model_config,
            model_client=model_client,
            messages=messages,
            compiled_prompt=compiled_prompt,
        )
        plan = [ToolInvocation("model_chat", {}, reason="chat_execution")]
        results = self.spine.execute_plan(context, plan)
        projection = build_public_projection(results, len(self.audit.events), pending_confirmations=self.pending_confirmations())
        result = RuntimeRunResult(
            intent=IntentResult("model_chat", 1.0),
            plan=plan,
            results=results,
            projection=projection,
            audit_events=self.audit.recent_summary(),
            pending_confirmations=self.pending_confirmations(),
            status=projection.status,
            provider_status="ok" if all(item.ok for item in results) else "failed",
            user_visible_summary=projection.summary,
            next_action="final_report",
        )
        return self._remember(result)

    def execute_plan(
        self,
        plan: list[ToolInvocation],
        *,
        workspace: str | Path | None = None,
        user_message: str = "execute_plan",
        tool_mode: str | ToolExecutionMode = ToolExecutionMode.RUNTIME_GOVERNED,
        max_steps: int = 20,
    ) -> RuntimeRunResult:
        context = TurnContext.create(user_message, workspace=workspace, tool_mode=tool_mode, max_steps=max_steps)
        results, chain_summary, planner_report = self._execute_plan_with_report(context, plan)
        projection = build_public_projection(results, len(self.audit.events), chain_summary, self.pending_confirmations())
        result = RuntimeRunResult(
            intent=IntentResult("tool_task", 1.0),
            plan=plan,
            results=results,
            projection=projection,
            audit_events=self.audit.recent_summary(),
            chain_summary=chain_summary,
            pending_confirmations=self.pending_confirmations(),
            planner_execution_report=planner_report,
            status=projection.status,
            has_executed_tools=bool(results),
            user_visible_summary=projection.summary,
            next_action="final_report",
        )
        return self._remember(result)
    def confirm_ticket(self, ticket_id: str, *, workspace: str | Path | None = None, tool_mode: str | ToolExecutionMode = ToolExecutionMode.RUNTIME_GOVERNED, max_steps: int = 1) -> RuntimeRunResult:
        context = TurnContext.create(f"confirm {ticket_id}", workspace=workspace, tool_mode=tool_mode, max_steps=max_steps)
        result = self.spine.execute_confirmed_ticket(context, ticket_id)
        projection = build_public_projection([result], len(self.audit.events), pending_confirmations=self.pending_confirmations())
        runtime_result = RuntimeRunResult(
            intent=IntentResult("confirm_ticket", 1.0),
            plan=[],
            results=[result],
            projection=projection,
            audit_events=self.audit.recent_summary(),
            pending_confirmations=self.pending_confirmations(),
            status=projection.status,
            user_visible_summary=projection.summary,
        )
        return self._remember(runtime_result)

    def deny_ticket(self, ticket_id: str) -> dict[str, Any]:
        ticket = self.ticket_store.deny(ticket_id)
        return {"ok": ticket is not None, "ticket": ticket.to_public_dict() if ticket is not None else None}

    def available_tools(self) -> list[ToolDescriptor]:
        return self.registry.describe()

    def should_handle_with_tools(self, user_message: str) -> bool:
        return _looks_like_grounded_file_task(user_message) or bool(self.plan_bridge.build_plan(user_message))

    def pending_confirmations(self) -> list[dict[str, Any]]:
        return self.ticket_store.public_pending()

    def context_snapshot(self) -> dict[str, Any]:
        return self.context_memory.snapshot().public_dict()

    def planner_execution_snapshot(self) -> dict[str, Any]:
        return self.planner_execution.public_dict()

    def export_planner_execution_json(self, path: str | Path) -> Path:
        return self.planner_execution.export_json(path)

    def reset_planner_execution(self) -> None:
        self.planner_execution.reset()

    def export_context_json(self, path: str | Path) -> Path:
        return self.context_memory.export_json(path)

    def reset_context_memory(self) -> None:
        self.context_memory.reset()

    def export_audit_jsonl(self, path: str | Path) -> Path:
        return self.audit.export_jsonl(path)

    def replay_audit_jsonl(self, path: str | Path) -> Any:
        return replay_audit_events(self.audit.load_jsonl(path))

    def project_snapshot(self) -> dict[str, Any]:
        return {"schema": "tiangong.runtime_host.project_snapshot.v1", "status": "host_only", "message": "Project radar is not part of the main RuntimeHost chain."}

    def quality_gate_snapshot(self) -> dict[str, Any]:
        return _disabled_snapshot("quality_gate")

    def delivery_snapshot(self) -> dict[str, Any]:
        return _disabled_snapshot("delivery")

    def experience_snapshot(self) -> dict[str, Any]:
        return {
            "schema": "tiangong.runtime_host.experience_snapshot.v1",
            "status": "available",
            "sink": "jingyan_chi_and_memory_store",
            "summary": "任务后处理会沉淀经验，后台学习链按自由意志频率消费经验池。",
            "runtime_host_only": True,
        }

    def skill_queue_snapshot(self) -> dict[str, Any]:
        return {
            "schema": "tiangong.runtime_host.skill_queue_snapshot.v1",
            "status": "queue_ready",
            "draft_versions": 0,
            "review_queue": 0,
            "activation_required": True,
            "summary": "技能候选由学习链生成，生效前仍需候选沙箱和发布门。",
        }

    def tool_request_snapshot(self) -> dict[str, Any]:
        return {
            "schema": "tiangong.runtime_host.tool_request_snapshot.v1",
            "status": "queue_ready",
            "production_requests": 0,
            "review_queue": 0,
            "activation_required": True,
            "summary": "工具补全请求可以排队，但不会绕过用户确认和运行边界。",
        }

    def exoskeleton_snapshot(self) -> dict[str, Any]:
        return _disabled_snapshot("execution_exoskeleton")

    def shell_mount_snapshot(self) -> dict[str, Any]:
        return _disabled_snapshot("shell_mount")

    def project_repair_snapshot(self) -> dict[str, Any]:
        return _disabled_snapshot("project_repair")

    def delivery_standardization_snapshot(self) -> dict[str, Any]:
        return _disabled_snapshot("delivery_standardization")

    def provider_adaptation_snapshot(self) -> dict[str, Any]:
        return _disabled_snapshot("provider_adaptation")

    def learning_convergence_snapshot(self) -> dict[str, Any]:
        return _disabled_snapshot("learning_convergence")

    def recovery_coordination_snapshot(self) -> dict[str, Any]:
        return _disabled_snapshot("recovery_coordination")

    def governance_execution_snapshot(self) -> dict[str, Any]:
        return _disabled_snapshot("governance_execution")

    def planner_context_snapshot(self) -> dict[str, Any]:
        return _disabled_snapshot("planner_context")

    def execution_chain_contract_snapshot(self) -> dict[str, Any]:
        return {
            "schema": "tiangong.runtime_host.execution_chain_contract.v1",
            "status": "active",
            "chain": [
                "goal_intake",
                "memory_recall",
                "affective_modulation",
                "tool_action_observation",
                "recovery_replan",
                "context_pressure_check",
                "forgetting_at_context_80",
                "memory_commit",
                "lifecycle_idle_heartbeat",
            ],
            "runtime_host_only": True,
        }

    def p0_system_integration_snapshot(self) -> dict[str, Any]:
        return _disabled_snapshot("l6_38_p0_system_integration")

    def p0_system_integration_two_snapshot(self) -> dict[str, Any]:
        return _disabled_snapshot("l6_39_p0_system_integration_two")

    def diagnosis_snapshot(self) -> dict[str, Any]:
        return _disabled_snapshot("diagnosis")

    def affective_runtime_snapshot(self) -> dict[str, Any]:
        return {
            "schema": "tiangong.runtime_host.affective_snapshot.v1",
            "emotion_engine_attached": self._affective_engine is not None,
            "turn_count": self._affective_turn_count,
            "has_previous_state": self._affective_state is not None,
            "state": self._affective_state.public_dict() if self._affective_state is not None else None,
            "route": self._affective_route.public_dict() if self._affective_route is not None else None,
            "not_authorization": True,
            "not_refusal": True,
            "no_tool_dispatch": True,
            "no_quality_gate_override": True,
        }

    def memory_recall_runtime_snapshot(self) -> dict[str, Any]:
        return {
            "schema": "tiangong.runtime_host.memory_recall_snapshot.v1",
            "memory_store_attached": self._memory_store is not None,
            "last_error": self._last_memory_recall_error,
            "route": self._last_memory_recall_route.public_dict() if self._last_memory_recall_route is not None else None,
            "summary_only": True,
            "no_raw_memory_body": True,
            "no_long_term_write": True,
            "no_memory_delete": True,
        }

    def forgetting_review_runtime_snapshot(self) -> dict[str, Any]:
        return {
            "schema": "tiangong.runtime_host.forgetting_review_snapshot.v1",
            "reviewer_attached": self._memory_store is not None,
            "memory_store_attached": self._memory_store is not None,
            "activation_threshold": self.FORGETTING_CONTEXT_THRESHOLD,
            "activation": self._last_activation_snapshot.public_dict(),
            "review_count": len(self._last_forget_review_decisions),
            "last_error": self._last_forget_review_error,
            "decisions": [decision.public_dict() for decision in self._last_forget_review_decisions],
            "no_physical_delete": True,
            "no_memory_mutation": True,
        }

    def lifecycle_runtime_snapshot(self) -> dict[str, Any]:
        return {
            "schema": "tiangong.runtime_host.lifecycle_snapshot.v1",
            "bundle": self._last_lifecycle_bundle.public_dict() if self._last_lifecycle_bundle is not None else None,
            "activation": self._last_activation_snapshot.public_dict(),
            "scheduler_attached": True,
            "free_will_frequency": os.environ.get("TIANGONG_FREE_WILL_FREQUENCY", "manual"),
            "idle_only": True,
            "no_direct_execution": True,
            "no_tool_invocation": True,
            "no_kernel_mutation": True,
        }

    def interface_wiring_snapshot(self) -> dict[str, Any]:
        return {
            "schema": "tiangong.runtime_host.interface_wiring_snapshot.v1",
            "runtime_entry": "RuntimeHost",
            "legacy_runtime_entry": "removed_from_main_chain",
            "activation": self._last_activation_snapshot.public_dict(),
            "affective": self.affective_runtime_snapshot(),
            "memory_recall": self.memory_recall_runtime_snapshot(),
            "forgetting_review": self.forgetting_review_runtime_snapshot(),
            "lifecycle": self.lifecycle_runtime_snapshot(),
            "l3_orchestration": "decision_contract_owner",
            "l4_execution": "tool_execution_owner",
            "l5_plugin_host": "plugin_host_owner",
            "l6_plugins": "plugin_skill_projection_only",
        }

    def run_idle_heartbeat(self, model_client: Any, *, notes: str = "", model_config: Any | None = None) -> dict[str, Any]:
        """凌晨心跳：LLM判定P4/P5是否可执行 → 执行。"""
        import time as _time
        legacy_client = _legacy_completion_client(model_client, model_config)

        huoyue_lujing = Path.home() / ".tiangong" / "zuihou_huoyue.txt"
        kongxian_miao = 900.0
        if huoyue_lujing.exists():
            try:
                ts = float(huoyue_lujing.read_text().strip())
                kongxian_miao = _time.time() - ts
            except (ValueError, OSError):
                pass

        from .diedai_chi import DiedaiChi
        chi = DiedaiChi()
        weiquereng = chi.weiquereng_tiaomu()
        diedai_zhuangtai = f"待确认{len(weiquereng)}条" if weiquereng else "空"

        panjue: dict[str, Any] = {}
        if legacy_client is not None:
            panjue = self._llm_quick_judge(
                legacy_client,
                "你是天工造物心跳调度器。判断现在是否适合执行后台自主任务。只输出JSON。",
                f"当前时间: {_time.strftime('%H:%M')}\n"
                f"距上次用户活跃: {kongxian_miao:.0f}秒\n"
                f"迭代池: {diedai_zhuangtai}\n\n"
                "规则：用户活跃(<300秒)时P5通常不跑；迭代池有已确认项时可跑P4。\n"
                '输出JSON: {"pao_p4": true/false, "pao_p5": true/false, "liyou": "理由≤20字"}',
            )

        pao_p4 = panjue.get("pao_p4", False)
        pao_p5 = panjue.get("pao_p5", False)

        p4_jieguo = ""
        if pao_p4:
            # P4：迭代池已确认项处理。当前仅做轻量统计，不执行自动修改。
            yiquereng = [t for t in chi.quanbu_tiaomu() if t.quereng]
            p4_jieguo = f"迭代池已确认{len(yiquereng)}项，待后续手动处理"

        p5_jieguo = ""
        if pao_p5:
            from .free_will_suiji_yinqing import ZiyouYizhiYinqing
            yinqing = ZiyouYizhiYinqing(moxing_kehuduan=legacy_client)
            chanchu_liebiao = yinqing.yunxing()
            jieguo_parts: list[str] = []
            for c in chanchu_liebiao:
                if c.leixing == "xitong_gaijin":
                    tiao = chi.tousu(legacy_client, c.dongzuo_ming,
                                     f"来源：{c.laiyuan_xinxi}\n发现：{c.neirong}")
                    jieguo_parts.append(
                        f"[{c.dongzuo_ming}] →迭代池 #{tiao.tiao_id}" if tiao
                        else f"[{c.dongzuo_ming}] →迭代池(LLM判不需要)"
                    )
                elif c.leixing == "zhishi_jineng":
                    self.jingyan_chi.touru("free_will", c.neirong, c.laiyuan_xinxi)
                    lian_jg = self._run_free_will_learning_chain(
                        model_client, TurnContext.create("xintiao_p5", max_steps=10), model_config=model_config)
                    jieguo_parts.append(
                        f"[{c.dongzuo_ming}] →自学链 {lian_jg[:60] if lian_jg else '已处理'}"
                    )
                else:
                    jieguo_parts.append(f"[{c.dongzuo_ming}] {c.zhaiyao[:60]}")
            p5_jieguo = " | ".join(jieguo_parts) if jieguo_parts else "无产出"

        return {
            "kongxian_miao": kongxian_miao,
            "panjue": panjue,
            "pao_p4": pao_p4,
            "pao_p5": pao_p5,
            "p4_jieguo": p4_jieguo,
            "p5_jieguo": p5_jieguo,
        }

    def _active_learned_asset_names(self) -> tuple[str, ...]:
        return tuple()

    def _build_initial_plan(self, user_message: str, *, activation_form: Any | None = None, skill_playbook_route: Any | None = None) -> list[ToolInvocation]:
        plan = self.plan_bridge.build_plan(user_message)
        if plan:
            return plan
        if _activation_requests_tools(activation_form):
            playbook_step = self._initial_playbook_grounding_step(user_message, skill_playbook_route)
            if playbook_step is not None:
                return [playbook_step]
        if _looks_like_grounded_file_task(user_message):
            return [ToolInvocation("list_dir", {"path": "."}, reason="ground_files_before_reading")]
        if _activation_requests_tools(activation_form):
            return [ToolInvocation("list_dir", {"path": "."}, reason="tool_activation_needs_grounding")]
        return []

    def _initial_playbook_grounding_step(self, user_message: str, skill_playbook_route: Any | None) -> ToolInvocation | None:
        tools = tuple(getattr(skill_playbook_route, "recommended_tools", ()) or ())
        reason = f"skill_playbook:{getattr(skill_playbook_route, 'playbook_id', 'default')}:grounding"
        path_hint = _extract_first_mentioned_path(user_message)
        for tool_name in tools:
            if tool_name == "scan_project":
                return ToolInvocation("scan_project", {"path": ".", "max_depth": 6, "max_files": 1500}, reason=reason)
            if tool_name == "diagnose_project":
                return ToolInvocation("diagnose_project", {"path": ".", "max_depth": 6, "max_files": 1500}, reason=reason)
            if tool_name == "list_dir":
                return ToolInvocation("list_dir", {"path": "."}, reason=reason)
            if tool_name == "document_parse" and path_hint:
                return ToolInvocation("document_parse", {"path": path_hint, "max_chars": 256_000}, reason=reason)
            if tool_name == "read_file" and path_hint:
                return ToolInvocation("read_file", {"path": path_hint, "max_bytes": 256_000}, reason=reason)
        return None

    def _execute_plan_with_report(self, context: TurnContext, plan: list[ToolInvocation]) -> tuple[list[ToolResult], LongChainRunSummary, PlannerExecutionReport]:
        runner = LongChainRunner(self.spine)
        return self.planner_execution.execute(
            context,
            plan,
            runner,
            task_id=f"task_{context.turn_id}",
            run_id=context.turn_id,
            planner_context_digest="runtime_host_l3_l4",
        )

    def _build_follow_up_from_observation(self, user_message: str, plan: list[ToolInvocation], results: list[ToolResult]) -> list[ToolInvocation]:
        if any(item.tool_name in {"read_file", "document_parse"} and item.ok for item in results):
            return []
        entries = _entries_from_results(results)
        if not entries:
            if any(item.error_code == "path_not_found" for item in results):
                return [ToolInvocation("list_dir", {"path": "."}, reason="recover_missing_path_by_listing")]
            return []
        target = _choose_file_from_entries(user_message, entries)
        if not target:
            return []
        already_requested = {str(invocation.arguments.get("path") or "") for invocation in plan if invocation.tool_name in {"read_file", "document_parse"}}
        if target in already_requested:
            return []
        return [ToolInvocation("read_file", {"path": target, "max_bytes": 256_000}, reason="grounded_from_list_dir_observation")]

    def _run_affective(self, user_message: str, *, now: float | None = None, mutate: bool) -> AffectiveExecutionRoute:
        if now is None:
            now = time()
        emotion_sources, desire_sources = _derive_affective_sources(user_message)
        elapsed = 0.0 if self._last_affective_update_at is None else max(0.0, now - self._last_affective_update_at)
        state = self._affective_engine.evolve(emotion_sources, desire_sources, previous_state=self._affective_state, elapsed_seconds=elapsed)
        route = self._affective_router.route(state)
        if mutate:
            self._affective_state = state
            self._affective_route = route
            self._last_affective_update_at = now
            self._affective_turn_count += 1
        return route

    def _run_memory_recall(self, user_message: str, *, now: float | None = None) -> L640MemoryRecallRoute | None:
        self._last_memory_recall_error = ""
        if self._memory_store is None:
            self._last_memory_recall_route = None
            return None
        try:
            route = MemoryRecallRouter(self._memory_store).route(user_message, top_k=5, now=now)
        except Exception as exc:  # noqa: BLE001
            self._last_memory_recall_error = f"{type(exc).__name__}: {exc}"
            self._last_memory_recall_route = None
            return None
        self._last_memory_recall_route = route
        return route

    def _remember(self, result: RuntimeRunResult) -> RuntimeRunResult:
        self.last_result = result
        self.context_memory.observe_run(result)
        self._persist_to_memory_store(result)
        self._lunshu += 1
        self._baocun_lunshu()
        try:
            self._jiancha_jinsheng_yu_yiwang()
        except Exception:
            pass
        return result

    def _huifu_lunshu(self) -> int:
        """从磁盘恢复 _lunshu，跨进程重启不丢失遗忘进度。"""
        try:
            from tiangong_agent_shell.session_state import _tiangong_jia
            lujing = _tiangong_jia() / "lunshu.txt"
            if lujing.exists():
                return int(lujing.read_text().strip())
        except Exception:
            pass
        return 0

    def _baocun_lunshu(self) -> None:
        """持久化 _lunshu 到磁盘。"""
        try:
            from tiangong_agent_shell.session_state import _tiangong_jia
            lujing = _tiangong_jia() / "lunshu.txt"
            lujing.write_text(str(self._lunshu))
        except Exception:
            pass

    def _persist_to_memory_store(self, result: RuntimeRunResult) -> None:
        """将任务结果摘要写入 JSONL 长期记忆。"""
        if self._memory_store is None:
            return
        try:
            from uuid import uuid4
            summary = getattr(result.projection, "summary", "") or ""
            intent_label = getattr(getattr(result, "intent", None), "label", "") or ""
            record = MemoryRecord(
                memory_id=f"mem_{uuid4().hex[:12]}",
                memory_category=MemoryCategory.EPISODIC,
                sanitized_summary=f"[{intent_label}] {summary[:250]}",
            )
            self._memory_store.add_candidate(record)
        except Exception:
            pass

    # ── 记忆系统完整链路（L1 → L2 → L3 → L4 → L5）──

    # 晋升阈值
    L1_SHENG_L2 = 3    # reuse_count ≥ 3
    L2_SHENG_L3 = 5    # L2 检索命中 ≥ 5 次
    L3_SHENG_L4 = 5    # L3 检索命中 ≥ 5 次
    L4_SHENG_L5 = 10   # L4 检索命中 ≥ 10 次

    def remember_turn(self, xiaoxi: str, huifu: str, lujing: str = "chat") -> str | None:
        """供 CLI loop 调用的统一记忆落盘入口。L1 JSONL 落盘（_lunshu 与遗忘检查已由 _remember 统一处理）。"""
        jiyi_id = None
        try:
            from uuid import uuid4
            from datetime import datetime
            now_str = datetime.now().strftime("%Y年%m月%d日%H时")
            summary = f"[{now_str}][{lujing}] 用户: {xiaoxi[:120]}"
            if huifu:
                summary += f" | 助手: {huifu[:120]}"
            qinggan = ""
            try:
                if self._affective_route:
                    qinggan = getattr(self._affective_route, "dominant_emotion", "") or ""
            except Exception:
                pass
            # 关键词判定记忆分类
            fenlei = self._panding_fenlei(xiaoxi)
            jiyi_id = f"mem_{uuid4().hex[:12]}"
            record = MemoryRecord(
                memory_id=jiyi_id,
                memory_category=MemoryCategory(fenlei),
                sanitized_summary=summary[:500],
                emotional_tag=qinggan,
            )
            if self._memory_store:
                self._memory_store.add_candidate(record)
        except Exception:
            pass
        return jiyi_id

    def _jiyi_mulu(self, cengji: str = "l2", fenlei: str = "") -> Path:
        """记忆文件目录。L2扁平，L3/L4/L5按6标签分子目录。
        fenlei: working/episodic/semantic/procedural/self/runtime"""
        from tiangong_agent_shell.session_state import _tiangong_jia
        mulu_ming = {"l2": "l2_tst", "l3": "l3_xuexi", "l4": "l4_xiguan", "l5": "l5_yongjiu"}
        mulu = _tiangong_jia() / mulu_ming.get(cengji, f"l{cengji}")
        if cengji in ("l3", "l4", "l5") and fenlei:
            mulu = mulu / f"{fenlei}_memory"
        mulu.mkdir(parents=True, exist_ok=True)
        return mulu

    @staticmethod
    def _panding_fenlei(xiaoxi: str) -> str:
        """关键词判定记忆分类：procedural/self/semantic/runtime/working/episodic"""
        text = xiaoxi.lower()
        if any(w in text for w in ("修", "改代码", "写代码", "bug", "修复", "诊断", "重构", "代码")):
            return "procedural_memory"
        if any(w in text for w in ("我叫", "我的", "我喜欢", "习惯", "偏好", "喜欢", "讨厌")):
            return "self_memory"
        if any(w in text for w in ("搜索", "查一下", "什么是", "为什么", "知识", "百科", "定义")):
            return "semantic_memory"
        if any(w in text for w in ("运行", "执行", "测试", "部署", "打包", "构建", "启动")):
            return "runtime_memory"
        if any(w in text for w in ("你好", "测试", "在吗", "hello", "hi", "ok")):
            return "working_memory"
        return "episodic_memory"

    def _jiancha_jinsheng_yu_yiwang(self) -> None:
        """全级别晋升检查 + 遗忘检查。
        L1: 2000轮触发，清理 reuse_count=0 且非最近100条
        L2: 30天未命中 → 删除
        L3: 60天未命中 → 降回 L2
        L4: 90天未命中 → 降回 L3
        L5: 永不遗忘
        """
        if self._memory_store is None:
            return
        # L1→L2 晋升
        try:
            for record in self._memory_store.active_records():
                if record.reuse_count >= self.L1_SHENG_L2 and record.memory_level.value == "L1":
                    self._xie_l2_tst(record)
        except Exception:
            pass
        # L1 遗忘：2000轮触发，保留最近100条
        if self._lunshu > 0 and self._lunshu % 2000 == 0:
            try:
                records = sorted(self._memory_store.active_records(), key=lambda r: r.created_at, reverse=True)
                baoliu = {r.memory_id for r in records[:100]}
                for record in records[100:]:
                    if record.reuse_count == 0 and record.memory_level.value != "L5":
                        self._memory_store.mark_tombstone(record.memory_id)
            except Exception:
                pass
        # L2-L4 遗忘：每次检查（按文件 mtime，天数阈值）
        self._yiwang_l2_l4()

    def _xie_l2_tst(self, record: MemoryRecord) -> None:
        """L1 记忆 → L2 TST 事件文件。"""
        from datetime import datetime
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        qinggan = record.emotional_tag or "平静"
        dianping = record.ai_comment or "（暂无点评）"
        neirong = f"""# TST 事件记忆 (L2)
- 记录时间：{now_str}
- 记忆ID：{record.memory_id}
- 情感标记：{qinggan}
- AI点评：{dianping}
- 命中次数：0
- 分类：{record.memory_category.value}

## 框架起因
{record.sanitized_summary}

## 进展
已被检索 {record.reuse_count} 次，置信度 {record.confidence_score:.2f}

## 现状
L2 事件记忆，参与检索拼接

## 相关记忆链接
（通过 memory_id 关联）
"""
        lujing = self._jiyi_mulu("l2") / f"{record.memory_id}.tst"
        lujing.write_text(neirong, encoding="utf-8")
        try:
            self._memory_store._append_event("promoted_to_l2", record.memory_id, {"new_level": "L2", "file": str(lujing)})
        except Exception:
            pass

    # ── 命中计数与晋升 ──────────────────────────

    def _jilu_mingzhong(self, wenjian_lujing: Path) -> tuple[str, int]:
        """记录一次检索命中：读取文件→命中次数+1→写回。返回 (当前级别, 新计数)。"""
        try:
            neirong = wenjian_lujing.read_text(encoding="utf-8")
        except Exception:
            return ("", 0)
        import re as _re
        pipei = _re.search(r'命中次数：(\d+)', neirong)
        dangqian = int(pipei.group(1)) if pipei else 0
        xin = dangqian + 1
        neirong = _re.sub(r'命中次数：\d+', f'命中次数：{xin}', neirong)
        wenjian_lujing.write_text(neirong, encoding="utf-8")
        # 判定级别
        if wenjian_lujing.suffix == ".tst":
            cengji = "l2"
        elif wenjian_lujing.suffix == ".l3":
            cengji = "l3"
        elif wenjian_lujing.suffix == ".l4":
            cengji = "l4"
        elif wenjian_lujing.suffix == ".l5":
            cengji = "l5"
        else:
            cengji = "l2"
        # 晋升检查
        self._jinsheng(wenjian_lujing, neirong, cengji, xin)
        return (cengji, xin)

    def _jinsheng(self, yuan_lujing: Path, neirong: str, dangqian_ceng: str, mingzhong: int) -> None:
        """按命中次数触发链式晋升。"""
        mubiao = None
        xin_houzhui = None
        if dangqian_ceng == "l2" and mingzhong >= self.L2_SHENG_L3:
            mubiao, xin_houzhui = "l3", ".l3"
        elif dangqian_ceng == "l3" and mingzhong >= self.L3_SHENG_L4:
            mubiao, xin_houzhui = "l4", ".l4"
        elif dangqian_ceng == "l4" and mingzhong >= self.L4_SHENG_L5:
            mubiao, xin_houzhui = "l5", ".l5"
        else:
            return
        from datetime import datetime
        import re as _re2
        # 提取分类标签
        fenlei = "episodic"
        fl_match = _re2.search(r'分类：(\w+)', neirong)
        if fl_match:
            fenlei = fl_match.group(1).replace("_memory", "")
        cengji_ming = {"l2": "L2 事件记忆", "l3": "L3 学习目标", "l4": "L4 用户习惯", "l5": "L5 永久记忆"}
        jiu = cengji_ming.get(dangqian_ceng, dangqian_ceng)
        xin = cengji_ming.get(mubiao, mubiao)
        zhuijia = f"""

## 升级记录
- 晋升时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}
- 从 {jiu} 晋升为 {xin}
- 触发条件：检索命中 {mingzhong} 次 ≥ 阈值
"""
        xin_neirong = neirong + zhuijia
        xin_lujing = self._jiyi_mulu(mubiao, fenlei=fenlei) / (yuan_lujing.stem + xin_houzhui)
        xin_lujing.write_text(xin_neirong, encoding="utf-8")
        # 删除原文件（已晋升）
        try:
            yuan_lujing.unlink()
        except Exception:
            pass

    # ── L2-L4 天数遗忘 ──────────────────────────

    L2_YIWANG_TIAN = 15   # L2 15天未命中 → 删除（命中率高，周期短）
    L3_YIWANG_TIAN = 45   # L3 45天未命中 → 降回 L2
    L4_YIWANG_TIAN = 90   # L4 90天未命中 → 降回 L3

    def _yiwang_l2_l4(self) -> None:
        """按天数清理 L2-L4 文件记忆。"""
        from datetime import datetime
        xianzai = datetime.now()
        # L2: 30天未命中 → 删除
        for wj in self._jiyi_mulu("l2").glob("*.tst"):
            try:
                mtime = datetime.fromtimestamp(wj.stat().st_mtime)
                if (xianzai - mtime).days >= self.L2_YIWANG_TIAN:
                    wj.unlink()
            except Exception:
                pass
        # L3: 45天未命中 → 降回 L2 (遍历所有分类子目录)
        FENLEI_L = ["working", "episodic", "semantic", "procedural", "self", "runtime"]
        l3_wenjian = []
        for f in FENLEI_L:
            l3_wenjian.extend(self._jiyi_mulu("l3", fenlei=f).glob("*.l3"))
        for wj in l3_wenjian:
            try:
                mtime = datetime.fromtimestamp(wj.stat().st_mtime)
                if (xianzai - mtime).days >= self.L3_YIWANG_TIAN:
                    neirong = wj.read_text(encoding="utf-8")
                    xin_lujing = self._jiyi_mulu("l2") / (wj.stem + ".tst")
                    # 追加降级记录
                    neirong += f"\n\n## 降级记录\n- 时间：{xianzai.strftime('%Y-%m-%d %H:%M')}\n- 原因：{self.L3_YIWANG_TIAN}天未命中，从 L3 降回 L2\n"
                    xin_lujing.write_text(neirong, encoding="utf-8")
                    wj.unlink()
            except Exception:
                pass
        # L4: 90天未命中 → 降回 L3 (遍历所有分类子目录)
        l4_wenjian = []
        for f in FENLEI_L:
            l4_wenjian.extend(self._jiyi_mulu("l4", fenlei=f).glob("*.l4"))
        for wj in l4_wenjian:
            try:
                mtime = datetime.fromtimestamp(wj.stat().st_mtime)
                if (xianzai - mtime).days >= self.L4_YIWANG_TIAN:
                    neirong = wj.read_text(encoding="utf-8")
                    xin_lujing = self._jiyi_mulu("l3") / (wj.stem + ".l3")
                    neirong += f"\n\n## 降级记录\n- 时间：{xianzai.strftime('%Y-%m-%d %H:%M')}\n- 原因：{self.L4_YIWANG_TIAN}天未命中，从 L4 降回 L3\n"
                    xin_lujing.write_text(neirong, encoding="utf-8")
                    wj.unlink()
            except Exception:
                pass

    # ── L5 直通 ──────────────────────────────────

    def yongjiu_jiyi(self, xiaoxi: str, beizhu: str = "", fenlei: str = "episodic") -> str:
        """用户说「永远记住」→ 直接写入 L5 永久记忆。"""
        from datetime import datetime
        from uuid import uuid4
        jiyi_id = f"l5_{uuid4().hex[:12]}"
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        qinggan = ""
        try:
            if self._affective_route:
                qinggan = getattr(self._affective_route, "dominant_emotion", "") or ""
        except Exception:
            pass
        neirong = f"""# 永久记忆 (L5)
- 记录时间：{now_str}
- 记忆ID：{jiyi_id}
- 情感标记：{qinggan or "平静"}
- 命中次数：0
- 来源：用户主动标记

## 内容
{xiaoxi}

## 备注
{beizhu or "用户要求永久记住此内容"}
"""
        lujing = self._jiyi_mulu("l5", fenlei=fenlei) / f"{jiyi_id}.l5"
        lujing.write_text(neirong, encoding="utf-8")
        return jiyi_id

    # ── 统一检索（L2-L5）──────────────────────

    def _du_jiyi_wenjian(self, chaxun: str = "") -> dict[str, list[dict]]:
        """读取所有 L2-L5 记忆文件，按查询过滤。返回 {级别: [文件信息]}。
        同时记录命中计数（触发晋升检查）。"""
        jieguo: dict[str, list[dict]] = {"l2": [], "l3": [], "l4": [], "l5": []}
        leibie = [("l2", "*.tst"), ("l3", "*.l3"), ("l4", "*.l4"), ("l5", "*.l5")]
        FENLEI_LIST = ["working", "episodic", "semantic", "procedural", "self", "runtime"]
        for cengji, geshi in leibie:
            mulu_list = [self._jiyi_mulu(cengji)] if cengji == "l2" else [self._jiyi_mulu(cengji, fenlei=f) for f in FENLEI_LIST]
            for mulu in mulu_list:
                for wj in sorted(mulu.glob(geshi)):
                    try:
                        neirong = wj.read_text(encoding="utf-8")
                    except Exception:
                        continue
                    if chaxun:
                        ci = {w.strip().lower() for w in chaxun.split() if w.strip()}
                        if not any(c in neirong.lower() for c in ci):
                            continue
                    # 命中 → 计数 + 晋升检查
                    self._jilu_mingzhong(wj)
                    jieguo[cengji].append({
                        "wenjian": wj.name,
                        "neirong": neirong[:800],
                        "qinggan": self._tiqu_qinggan(neirong),
                        "cengji": cengji,
                    })
        return jieguo

    def _tiqu_qinggan(self, neirong: str) -> str:
        for hang in neirong.split("\n"):
            if "情感标记：" in hang:
                return hang.split("情感标记：", 1)[-1].strip()
        return ""

    # ── 检索增强 ──────────────────────────────────

    def _build_memory_context(self) -> str:
        """从召回路由 + L2-L5 文件生成记忆上下文。"""
        route = self._last_memory_recall_route
        lines = ["## 相关记忆"]
        if route is not None and route.hints:
            for h in route.hints[:3]:
                summary = h.sanitized_summary[:200]
                if summary:
                    lines.append(f"- {summary}")
        try:
            quanbu = self._du_jiyi_wenjian()
            biaoqian_ceng = {"l2": "L2 事件", "l3": "L3 学习", "l4": "L4 习惯", "l5": "L5 永久"}
            for cengji in ("l5", "l4", "l3", "l2"):
                liebiao = quanbu.get(cengji, [])
                if liebiao:
                    lines.append(f"## {biaoqian_ceng[cengji]}记忆")
                    for t in liebiao[:2]:
                        qinggan = t.get("qinggan", "")
                        biaoqian = f"[{qinggan}] " if qinggan else ""
                        lines.append(f"- {biaoqian}{t['neirong'][:250]}")
        except Exception:
            pass
        return "\n".join(lines) if len(lines) > 1 else ""

    def _disabled_runtime_result(self, name: str, *, workspace: str | Path | None = None) -> RuntimeRunResult:
        projection = RuntimeProjection(
            status="not_enabled",
            summary=f"{name} belongs to the removed legacy RuntimeEntry management surface. Use L3/L4 execution chain or L5/L6 plugin host.",
            audit_count=len(self.audit.events),
            pending_confirmations=self.pending_confirmations(),
        )
        result = RuntimeRunResult(
            intent=IntentResult(name, 0.0),
            plan=[],
            results=[],
            projection=projection,
            audit_events=self.audit.recent_summary(),
            pending_confirmations=self.pending_confirmations(),
            status=projection.status,
            user_visible_summary=projection.summary,
        )
        return self._remember(result)

    def __getattr__(self, name: str) -> Any:
        if name.endswith("_snapshot") or name.endswith("_status"):
            return lambda *args, **kwargs: _disabled_snapshot(name)
        if name.startswith("export_") and (name.endswith("_json") or name.endswith("_jsonl")):
            return lambda path, *args, **kwargs: _export_disabled_snapshot(path, name)
        if name.startswith("reset_"):
            return lambda *args, **kwargs: None
        if name.startswith("run_"):
            return lambda *args, **kwargs: self._disabled_runtime_result(name, workspace=kwargs.get("workspace"))
        raise AttributeError(name)

    def _llm_quick_judge(self, client: Any, xitong: str, yonghu: str) -> dict[str, Any]:
        """轻量 LLM JSON 判定"""
        try:
            create = getattr(getattr(getattr(client, "chat", None), "completions", None), "create", None)
            if not callable(create):
                return {}
            resp = create(
                model=getattr(client, 'model', 'deepseek-v4-pro'),
                messages=[{"role": "system", "content": xitong},
                          {"role": "user", "content": yonghu}],
                temperature=0.1, max_tokens=300)
            text = resp.choices[0].message.content.strip()
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(text[start:end])
        except Exception:
            pass
        return {}

    def _make_learning_web_searcher(self, context: Any, *, model_config: Any | None = None, model_client: Any | None = None):
        def _search(query: str) -> str:
            q = str(query or "").strip()
            if not q:
                return ""
            source_context = context if isinstance(context, TurnContext) else None
            tool_context = TurnContext.create(
                q,
                workspace=getattr(source_context, "workspace", None),
                tool_mode=getattr(source_context, "tool_mode", ToolExecutionMode.RUNTIME_GOVERNED),
                max_steps=5,
                model_config=model_config or getattr(source_context, "model_config", None),
                model_client=model_client or getattr(source_context, "model_client", None),
            )
            result = self.spine.execute_plan(
                tool_context,
                [ToolInvocation("web_search", {"query": q, "max_results": 5}, reason="self_learning_content_search")],
            )[0]
            if not result.ok:
                raise RuntimeError(result.output_summary or result.error_code or "web_search failed")
            data = result.data or {}
            content = str(data.get("content") or "").strip()
            if not content:
                rows: list[str] = []
                for index, item in enumerate(data.get("results") or [], start=1):
                    if not isinstance(item, dict):
                        continue
                    title = str(item.get("title") or "Untitled").strip()
                    url = str(item.get("url") or "").strip()
                    snippet = str(item.get("snippet") or "").strip()
                    rows.append(f"[{index}] {title}\nURL: {url}\n{snippet}".strip())
                content = "\n".join(rows).strip() or str(result.output_summary or "")
            return content[:10000]

        return _search

    def _run_free_will_learning_chain(self, client: Any, context: Any, jingyan_beizhu: str = "", laiyuan: str = "chat", l3_houxuan: list[dict] | None = None, model_config: Any | None = None, target_tiao_id: str = "") -> str:
        """自由意志学习链：扫池→搜索→学习→判→生成 skill/tool。L3 优先。"""
        effective_config = model_config or getattr(context, "model_config", None)
        lian = XuexiLian(
            _legacy_completion_client(client, effective_config),
            jingyan_chi=self.jingyan_chi,
            web_searcher=self._make_learning_web_searcher(context, model_config=effective_config, model_client=client),
        )
        xiaoxi = getattr(context, 'user_message', '') or ''
        return lian.yunxing(
            yuanshi_xiaoxi=xiaoxi,
            jingyan_beizhu=jingyan_beizhu,
            laiyuan=laiyuan,
            l3_houxuan=l3_houxuan,
            target_tiao_id=target_tiao_id,
        )

    def _make_turn_context(self, xiaoxi: str, *, workspace: str | Path | None = None) -> Any:
        """创建 TurnContext（供 cli_loop 调用）"""
        from .turn_context import TurnContext
        return TurnContext.create(xiaoxi, workspace=workspace)

    def _hecheng_jingyan(self, xiaoxi: str, touying: Any) -> None:
        """⑤ 经验合成：把本轮执行沉淀为经验信号"""
        try:
            self.experience_bridge.synthesize(
                context_snapshot=self.context_snapshot(),
                manual_notes=f"{xiaoxi[:200]} → {getattr(touying, 'summary', '')[:200]}"
            )
        except Exception:
            pass

    def _panding_xuexi(self, xiaoxi: str, touying: Any, client: Any) -> None:
        """⑥ 自主学习判定：优先从 L3 记忆库选题，无 L3 则回退经验池。"""
        try:
            zhaiyao = getattr(touying, 'summary', '') or str(touying)[:300]
            # 先检查 L3 记忆库
            l3_houxuan = self._du_weixuexi_l3()
            if l3_houxuan and client is not None:
                # L3 有候选 → 直接走学习链
                self._run_free_will_learning_chain(
                    client, self._make_turn_context(xiaoxi),
                    jingyan_beizhu=zhaiyao, laiyuan="l3",
                    l3_houxuan=l3_houxuan,
                )
                return
            # 无 L3 → 回退经验池
            self.jingyan_chi.touru("runtime_task", zhaiyao, xiaoxi[:200])
            weichuli = self.jingyan_chi.weichuli_tiaomu(xianzhi=1)
            if weichuli and client is not None:
                panjue = self._llm_quick_judge(
                    client,
                    "你是学习判定器。只输出JSON。",
                    f"经验：{zhaiyao[:300]}\n任务：{xiaoxi[:200]}\n"
                    '输出JSON：{"zhide_xue": true/false, "liyou":"理由≤20字"}'
                )
                if panjue.get("zhide_xue"):
                    self._run_free_will_learning_chain(
                        client, self._make_turn_context(xiaoxi),
                        jingyan_beizhu=zhaiyao, laiyuan="chat",
                    )
        except Exception:
            pass

    def _du_weixuexi_l3(self) -> list[dict]:
        """读取 L3 目录中尚未学习的文件，返回候选列表。遍历所有分类子目录。"""
        FENLEI_L = ["working", "episodic", "semantic", "procedural", "self", "runtime"]
        houxuan = []
        l3_wenjian = []
        for f in FENLEI_L:
            l3_wenjian.extend(self._jiyi_mulu("l3", fenlei=f).glob("*.l3"))
        for wj in sorted(l3_wenjian, key=lambda p: p.name):
            try:
                neirong = wj.read_text(encoding="utf-8")
            except Exception:
                continue
            # 跳过已学习的
            if "状态：已学习" in neirong:
                continue
            houxuan.append({
                "wenjian": wj.name,
                "lujing": wj,
                "neirong": neirong[:600],
                "zhaiyao": self._tiqu_l3_zhaiyao(neirong),
            })
        return houxuan

    def _tiqu_l3_zhaiyao(self, neirong: str) -> str:
        """从 L3 内容提取摘要。"""
        # 取「框架起因」段
        for hang in neirong.split("\n"):
            hang = hang.strip()
            if hang and not hang.startswith("#") and not hang.startswith("-"):
                return hang[:200]
        return neirong[:200]


def _zc_zhuce(registry: "RuntimeToolRegistry", mingcheng: str, miaoshu: str, fengxian: str, adapter):
    """注册工具，自动从 tool_schemas 注入参数 schema。"""
    canshu = GONGJU_CANSHU_SCHEMA.get(mingcheng)
    registry.register(
        ToolDescriptor(mingcheng, miaoshu, fengxian, parameters_schema=canshu),
        adapter,
    )


def build_default_registry() -> RuntimeToolRegistry:
    registry = RuntimeToolRegistry()
    project_index = ProjectIndexBridge()
    diagnostics = EngineeringDiagnosticBridge()
    quality_gate = QualityGateBridge()
    delivery_manifest = DeliveryManifestBridge()
    _zc_zhuce(registry, "model_chat", "Execute model chat through the governed audit chain.", "A2", model_chat_adapter)
    _zc_zhuce(registry, "list_dir", "List files in the active workspace.", "A1", list_dir_adapter)
    _zc_zhuce(registry, "scan_project", "Scan workspace project structure.", "A1", build_scan_project_adapter(project_index))
    _zc_zhuce(registry, "diagnose_project", "Diagnose project state from the latest scan.", "A1/A2", build_diagnose_project_adapter(project_index, diagnostics))
    _zc_zhuce(registry, "read_file", "Read a text or parseable document file in the active workspace.", "A1", read_file_adapter)
    _zc_zhuce(registry, "file_sha256", "Calculate SHA256 for a workspace file.", "A1", file_sha256_adapter)
    _zc_zhuce(registry, "document_parse", "Parse txt/md/csv/json/code/html/docx/xlsx/pptx/pdf into safe document context.", "A1", document_parse_adapter)
    _zc_zhuce(registry, "document_query", "Query the latest or selected document context.", "A1", document_query_adapter)
    _zc_zhuce(registry, "document_export", "Export parsed document context.", "A3", document_export_adapter)
    _zc_zhuce(registry, "document_rewrite_plan", "Generate a safe document rewrite plan.", "A2", document_rewrite_plan_adapter)
    _zc_zhuce(registry, "document_apply_rewrite", "Apply a governed document rewrite with backup.", "A3", document_apply_rewrite_adapter)
    _zc_zhuce(registry, "document_rollback", "Rollback a governed document rewrite.", "A3", document_rollback_adapter)
    _zc_zhuce(registry, "document_text_extract", "从已解析的文档中提取纯文本。", "A1", document_text_extract_adapter)
    _zc_zhuce(registry, "write_workspace_file", "Write a workspace file with backup.", "A3/A4", write_workspace_file_adapter)
    _zc_zhuce(registry, "make_dir", "Create a directory inside the active workspace. Requires workspace_full permission.", "A3", make_dir_adapter)
    _zc_zhuce(registry, "move_path", "Move or rename a file/directory inside the active workspace. Requires workspace_full permission.", "A4", move_path_adapter)
    _zc_zhuce(registry, "copy_path", "Copy a file/directory inside the active workspace. Requires workspace_full permission.", "A3", copy_path_adapter)
    _zc_zhuce(registry, "delete_path", "Delete a file/directory inside the active workspace. Requires workspace_full permission; workspace root is protected.", "A4", delete_path_adapter)
    _zc_zhuce(registry, "return_code", "Return code without executing it.", "A2", return_code_adapter)
    _zc_zhuce(registry, "return_analysis", "Return analysis without side effects.", "A2", return_analysis_adapter)
    _zc_zhuce(registry, "run_python_quality_check", "Run allowlisted Python quality checks.", "A3", run_python_quality_check_adapter)
    _zc_zhuce(registry, "run_python_tests", "在受控工作区执行 pytest 测试。", "A3", run_python_tests_adapter)
    _zc_zhuce(registry, "create_zip_package", "Create a delivery ZIP in the workspace.", "A3", create_zip_package_adapter)
    _zc_zhuce(registry, "evaluate_quality_gate", "Evaluate quality gate evidence for the current task.", "A2", build_evaluate_quality_gate_adapter(quality_gate))
    _zc_zhuce(registry, "create_release_bundle", "Create a governed release bundle with manifest evidence.", "A3", build_create_release_bundle_adapter(delivery_manifest, quality_gate, diagnostics, AuditBridge()))
    try:
        from .v1_clean_import_adapters import register_v1_clean_import_tools

        register_v1_clean_import_tools(registry)
    except Exception:
        pass
    _zc_zhuce(registry, "dns_resolve", "Resolve DNS records for a host or URL.", "A2", dns_resolve_adapter)
    _zc_zhuce(registry, "network_request", "Perform a governed HTTPS network request with size and timeout limits.", "A3", network_request_adapter)
    _zc_zhuce(registry, "http_client", "HTTP client for governed GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS requests.", "A3", http_client_adapter)
    _zc_zhuce(registry, "protocol_adapter", "Normalize URL/cURL/API request inputs into an auditable http_client request.", "A2", protocol_adapter_adapter)
    _zc_zhuce(registry, "web_search", "联网搜索并返回中文证据包：综合摘要、来源标题、发布时间、完整链接和不确定点。", "A3", web_search_adapter)
    _zc_zhuce(registry, "web_readability_extract", "读取指定 URL 或已给 HTML/正文并清洗出正文文本，供搜索结果复核和总结。", "A2", web_readability_extract_adapter)
    _register_runtime_host_plugin_tools(registry, project_index, diagnostics, quality_gate, delivery_manifest)
    _zc_zhuce(registry, "runtime_tool_alignment_check", "Check Runtime tool registry and LLM usage card alignment.", "A1", build_runtime_tool_alignment_adapter(lambda: registry.describe()))
    _zc_zhuce(registry, "runtime_llm_operational_drill", "Simulate LLM intent to Runtime tool routing.", "A1", build_runtime_llm_drill_adapter(lambda: registry.describe(), PlanBridge().build_plan))
    
# TG_MULTIMEDIA_REGISTRATION_BEGIN
    # 把下面注册块放到 build_default_registry() 中，return registry 之前。
# TG_MULTIMEDIA_REGISTRATION_BEGIN
    def _tg_mm_reg(name: str, description: str, risk: str, adapter):
        try:
            _zc_zhuce(registry, name, description, risk, adapter)
        except NameError:
            registry.register(ToolDescriptor(name, description, risk), adapter)
    _tg_mm_reg("image_inspect", "对单张图片做整体理解与可见内容分析。", "A1", image_inspect_adapter)
    _tg_mm_reg("image_ocr_parse", "从图片中提取文字和OCR证据。", "A1", image_ocr_parse_adapter)
    _tg_mm_reg("image_layout_parse", "解析图片、截图或文档图的区域布局。", "A1", image_layout_parse_adapter)
    _tg_mm_reg("image_region_query", "按问题查询图片局部区域内容。", "A1", image_region_query_adapter)
    _tg_mm_reg("image_compare", "比较两张图片的尺寸、哈希和可见差异。", "A1", image_compare_adapter)
    _tg_mm_reg("image_table_extract", "从图片表格截图中抽取表格结构。", "A1", image_table_extract_adapter)
    _tg_mm_reg("image_chart_extract", "从图表截图中抽取标题、趋势和可见信息。", "A1", image_chart_extract_adapter)
    _tg_mm_reg("image_crop_export", "裁切图片局部区域并导出产物。", "A1", image_crop_export_adapter)
    _tg_mm_reg("video_inspect", "读取视频元信息和基础可解析状态。", "A1", video_inspect_adapter)
    _tg_mm_reg("video_keyframe_extract", "用ffmpeg从视频中提取关键帧。", "A1", video_keyframe_extract_adapter)
    _tg_mm_reg("video_scene_split", "基于关键帧对视频做轻量场景分段。", "A1", video_scene_split_adapter)
    _tg_mm_reg("video_ocr_parse", "对视频关键帧执行OCR解析。", "A1", video_ocr_parse_adapter)
    _tg_mm_reg("video_audio_transcribe", "生成视频音频转写请求或调用ASR。", "A1", video_audio_transcribe_adapter)
    _tg_mm_reg("video_subtitle_extract", "提取视频外置字幕或字幕线索。", "A1", video_subtitle_extract_adapter)
    _tg_mm_reg("video_event_timeline", "生成视频事件时间线。", "A1", video_event_timeline_adapter)
    _tg_mm_reg("video_compare", "比较两个视频的元信息和基础差异。", "A1", video_compare_adapter)
    _tg_mm_reg("image_generate", "image generate 多媒体工具。", "A3", image_generate_adapter)
    _tg_mm_reg("image_edit", "image edit 多媒体工具。", "A3", image_edit_adapter)
    _tg_mm_reg("image_inpaint", "image inpaint 多媒体工具。", "A3", image_inpaint_adapter)
    _tg_mm_reg("image_background_remove", "image background remove 多媒体工具。", "A3", image_background_remove_adapter)
    _tg_mm_reg("image_upscale", "image upscale 多媒体工具。", "A3", image_upscale_adapter)
    _tg_mm_reg("image_style_transfer", "image style transfer 多媒体工具。", "A3", image_style_transfer_adapter)
    _tg_mm_reg("image_variation", "image variation 多媒体工具。", "A3", image_variation_adapter)
    _tg_mm_reg("image_text_poster_generate", "image text poster generate 多媒体工具。", "A3", image_text_poster_generate_adapter)
    _tg_mm_reg("video_generate_from_text", "video generate from text 多媒体工具。", "A3", video_generate_from_text_adapter)
    _tg_mm_reg("video_generate_from_images", "video generate from images 多媒体工具。", "A3", video_generate_from_images_adapter)
    _tg_mm_reg("storyboard_generate", "storyboard generate 多媒体工具。", "A3", storyboard_generate_adapter)
    _tg_mm_reg("shot_plan_generate", "shot plan generate 多媒体工具。", "A3", shot_plan_generate_adapter)
    _tg_mm_reg("video_avatar_generate", "video avatar generate 多媒体工具。", "A3", video_avatar_generate_adapter)
    _tg_mm_reg("voiceover_generate", "voiceover generate 多媒体工具。", "A3", voiceover_generate_adapter)
    _tg_mm_reg("subtitle_burn_in", "subtitle burn in 多媒体工具。", "A3", subtitle_burn_in_adapter)
    _tg_mm_reg("video_render", "video render 多媒体工具。", "A3", video_render_adapter)
    _tg_mm_reg("video_trim", "video trim 多媒体工具。", "A3", video_trim_adapter)
    _tg_mm_reg("video_concat", "video concat 多媒体工具。", "A3", video_concat_adapter)
    _tg_mm_reg("video_cut_by_timestamps", "video cut by timestamps 多媒体工具。", "A3", video_cut_by_timestamps_adapter)
    _tg_mm_reg("video_add_subtitles", "video add subtitles 多媒体工具。", "A3", video_add_subtitles_adapter)
    _tg_mm_reg("video_add_bgm", "video add bgm 多媒体工具。", "A3", video_add_bgm_adapter)
    _tg_mm_reg("video_add_transition", "video add transition 多媒体工具。", "A3", video_add_transition_adapter)
    _tg_mm_reg("video_resize_reframe", "video resize reframe 多媒体工具。", "A3", video_resize_reframe_adapter)
    _tg_mm_reg("video_export", "video export 多媒体工具。", "A3", video_export_adapter)
    _tg_mm_reg("audio_transcribe", "audio transcribe 多媒体工具。", "A1", audio_transcribe_adapter)
    _tg_mm_reg("audio_diarize", "audio diarize 多媒体工具。", "A1", audio_diarize_adapter)
    _tg_mm_reg("audio_summary", "audio summary 多媒体工具。", "A2", audio_summary_adapter)
    _tg_mm_reg("audio_keywords_extract", "audio keywords extract 多媒体工具。", "A2", audio_keywords_extract_adapter)
    _tg_mm_reg("audio_event_detect", "audio event detect 多媒体工具。", "A1", audio_event_detect_adapter)
    _tg_mm_reg("tts_generate", "tts generate 多媒体工具。", "A3", tts_generate_adapter)
    _tg_mm_reg("audio_clone_voice", "audio clone voice 多媒体工具。", "A3", audio_clone_voice_adapter)
    _tg_mm_reg("bgm_generate", "bgm generate 多媒体工具。", "A3", bgm_generate_adapter)
    _tg_mm_reg("audio_mix", "audio mix 多媒体工具。", "A3", audio_mix_adapter)
    _tg_mm_reg("audio_denoise", "audio denoise 多媒体工具。", "A3", audio_denoise_adapter)
    _tg_mm_reg("audio_normalize", "audio normalize 多媒体工具。", "A3", audio_normalize_adapter)
    _tg_mm_reg("audio_export", "audio export 多媒体工具。", "A3", audio_export_adapter)
    _tg_mm_reg("media_entity_extract", "media entity extract 多媒体工具。", "A2", media_entity_extract_adapter)
    _tg_mm_reg("media_kv_extract", "media kv extract 多媒体工具。", "A2", media_kv_extract_adapter)
    _tg_mm_reg("media_topic_extract", "media topic extract 多媒体工具。", "A2", media_topic_extract_adapter)
    _tg_mm_reg("media_risk_extract", "media risk extract 多媒体工具。", "A2", media_risk_extract_adapter)
    _tg_mm_reg("media_knowledge_extract", "media knowledge extract 多媒体工具。", "A2", media_knowledge_extract_adapter)
    _tg_mm_reg("multimedia_pipeline_plan", "multimedia pipeline plan 多媒体工具。", "A2", multimedia_pipeline_plan_adapter)
    _tg_mm_reg("multimedia_asset_manifest", "multimedia asset manifest 多媒体工具。", "A2", multimedia_asset_manifest_adapter)
    _tg_mm_reg("multimedia_batch_plan", "multimedia batch plan 多媒体工具。", "A2", multimedia_batch_plan_adapter)
    _tg_mm_reg("multimedia_delivery_package", "multimedia delivery package 多媒体工具。", "A3", multimedia_delivery_package_adapter)
# TG_MULTIMEDIA_REGISTRATION_END
# TG_MULTIMEDIA_REGISTRATION_END

    
# BEGIN_ENTERPRISE_EXTENSION_REGISTRATION
# BEGIN_ENTERPRISE_EXTENSION_REGISTRATION
    _zc_zhuce(registry, "table_profile", "对表格文件做行列、字段、缺失值、样例值概览。", "A1", table_profile_adapter)
    _zc_zhuce(registry, "table_schema_detect", "识别表格字段类型、主键候选、枚举列、数值列和日期列。", "A1", table_schema_detect_adapter)
    _zc_zhuce(registry, "table_quality_check", "检查表格缺失值、重复值、异常值、编码问题和字段一致性。", "A1", table_quality_check_adapter)
    _zc_zhuce(registry, "table_clean_plan", "生成表格清洗方案，不直接改原文件。", "A2", table_clean_plan_adapter)
    _zc_zhuce(registry, "table_deduplicate", "生成表格去重方案或在安全输出路径生成去重结果。", "A3", table_deduplicate_adapter)
    _zc_zhuce(registry, "table_filter", "按条件筛选表格并输出筛选摘要或结果文件。", "A3", table_filter_adapter)
    _zc_zhuce(registry, "table_score", "按规则对表格记录打分，适合线索评分和优先级排序。", "A3", table_score_adapter)
    _zc_zhuce(registry, "table_join_plan", "生成多表关联方案，说明关联键、风险和输出结构。", "A2", table_join_plan_adapter)
    _zc_zhuce(registry, "table_pivot_summary", "生成透视汇总、分组统计和关键指标摘要。", "A2", table_pivot_summary_adapter)
    _zc_zhuce(registry, "table_export", "将结构化结果导出为 csv/json/xlsx 规格或安全输出文件。", "A3", table_export_adapter)
    _zc_zhuce(registry, "browser_open", "生成浏览器打开页面的安全动作规格。", "A2", browser_open_adapter)
    _zc_zhuce(registry, "browser_extract", "从网页文本或HTML片段中抽取结构化信息。", "A2", browser_extract_adapter)
    _zc_zhuce(registry, "browser_screenshot_plan", "生成浏览器截图采集计划。", "A2", browser_screenshot_plan_adapter)
    _zc_zhuce(registry, "browser_click_plan", "生成浏览器点击动作计划，默认不真实点击。", "A3", browser_click_plan_adapter)
    _zc_zhuce(registry, "browser_type_plan", "生成浏览器输入动作计划，默认不真实输入。", "A3", browser_type_plan_adapter)
    _zc_zhuce(registry, "browser_download_plan", "生成网页下载动作计划与风险检查清单。", "A3", browser_download_plan_adapter)
    _zc_zhuce(registry, "browser_form_fill_plan", "生成网页表单填写计划，默认需要确认。", "A3", browser_form_fill_plan_adapter)
    _zc_zhuce(registry, "browser_session_close", "生成关闭浏览器会话的动作规格。", "A2", browser_session_close_adapter)
    _zc_zhuce(registry, "db_connect_check", "检查数据库连接参数结构和可支持类型，默认不泄露凭证。", "A1", db_connect_check_adapter)
    _zc_zhuce(registry, "db_schema_inspect", "只读检查数据库 schema，当前优先支持 sqlite。", "A1", db_schema_inspect_adapter)
    _zc_zhuce(registry, "db_query_readonly", "执行只读 SQL 查询，禁止写入语句。", "A1", db_query_readonly_adapter)
    _zc_zhuce(registry, "db_query_explain", "解释 SQL 查询意图、风险和性能注意点。", "A1", db_query_explain_adapter)
    _zc_zhuce(registry, "db_table_profile", "对数据库表做字段和样例行概览。", "A1", db_table_profile_adapter)
    _zc_zhuce(registry, "db_export_csv", "将只读查询结果导出为 csv。", "A3", db_export_csv_adapter)
    _zc_zhuce(registry, "db_import_csv_plan", "生成 csv 导入数据库方案，不直接写库。", "A2", db_import_csv_plan_adapter)
    _zc_zhuce(registry, "db_migration_plan", "生成数据库迁移方案、回滚点和风险清单。", "A2", db_migration_plan_adapter)
    _zc_zhuce(registry, "api_request_spec", "生成 API 请求规格，支持方法、URL、headers、body 和风险提示。", "A2", api_request_spec_adapter)
    _zc_zhuce(registry, "api_schema_parse", "解析 OpenAPI/JSON Schema/API 文档片段。", "A1", api_schema_parse_adapter)
    _zc_zhuce(registry, "api_auth_check", "检查 API 鉴权方式、密钥字段和泄露风险。", "A1", api_auth_check_adapter)
    _zc_zhuce(registry, "api_response_extract", "从 API 响应 JSON/文本中抽取字段。", "A1", api_response_extract_adapter)
    _zc_zhuce(registry, "api_batch_request_plan", "生成批量 API 调用计划，不直接批量请求。", "A2", api_batch_request_plan_adapter)
    _zc_zhuce(registry, "api_webhook_test_plan", "生成 webhook 测试计划和验收字段。", "A2", api_webhook_test_plan_adapter)
    _zc_zhuce(registry, "api_error_diagnose", "诊断 API 错误码、响应结构和下一步修复建议。", "A1", api_error_diagnose_adapter)
    _zc_zhuce(registry, "eval_case_build", "构建任务评测用例，包含输入、期望、风险和验收规则。", "A2", eval_case_build_adapter)
    _zc_zhuce(registry, "eval_run_plan", "生成评测运行计划，不直接修改系统。", "A2", eval_run_plan_adapter)
    _zc_zhuce(registry, "eval_compare", "对比两次评测结果，输出退化、提升和未验证项。", "A1", eval_compare_adapter)
    _zc_zhuce(registry, "eval_regression_check", "生成回归检查清单，定位是否破坏旧能力。", "A1", eval_regression_check_adapter)
    _zc_zhuce(registry, "eval_tool_schema_check", "检查工具 schema 覆盖率、退化通用 query 和孤儿 schema。", "A1", eval_tool_schema_check_adapter)
    _zc_zhuce(registry, "eval_skill_quality_check", "检查 SKILL.md 是否符合标准格式和工具声明一致性。", "A1", eval_skill_quality_check_adapter)
    _zc_zhuce(registry, "eval_report", "生成评测报告。", "A2", eval_report_adapter)
    _zc_zhuce(registry, "kb_ingest_plan", "生成企业知识库文档入库计划。", "A2", kb_ingest_plan_adapter)
    _zc_zhuce(registry, "kb_chunk_preview", "对文本或文档片段生成分块预览。", "A1", kb_chunk_preview_adapter)
    _zc_zhuce(registry, "kb_index_plan", "生成知识库索引方案，含向量/关键词/图谱索引建议。", "A2", kb_index_plan_adapter)
    _zc_zhuce(registry, "kb_search_local", "在给定文本块中做轻量本地检索。", "A1", kb_search_local_adapter)
    _zc_zhuce(registry, "kb_answer_draft", "基于给定证据生成知识库问答草稿。", "A2", kb_answer_draft_adapter)
    _zc_zhuce(registry, "kb_source_trace", "生成知识库答案的来源追踪结构。", "A1", kb_source_trace_adapter)
    _zc_zhuce(registry, "kb_update_plan", "生成知识库更新、删除、重建索引计划。", "A2", kb_update_plan_adapter)
    _zc_zhuce(registry, "kb_quality_check", "检查知识库覆盖率、引用完整性和幻觉风险。", "A1", kb_quality_check_adapter)
    _zc_zhuce(registry, "desktop_screenshot_plan", "生成桌面截图采集计划。", "A2", desktop_screenshot_plan_adapter)
    _zc_zhuce(registry, "desktop_click_plan", "生成桌面点击动作计划，默认需要确认。", "A3", desktop_click_plan_adapter)
    _zc_zhuce(registry, "desktop_type_plan", "生成桌面输入动作计划，默认需要确认。", "A3", desktop_type_plan_adapter)
    _zc_zhuce(registry, "desktop_hotkey_plan", "生成快捷键动作计划，默认需要确认。", "A3", desktop_hotkey_plan_adapter)
    _zc_zhuce(registry, "desktop_find_window_plan", "生成查找窗口/应用的计划。", "A2", desktop_find_window_plan_adapter)
    _zc_zhuce(registry, "desktop_clipboard_plan", "生成剪贴板读写计划，写入需确认。", "A3", desktop_clipboard_plan_adapter)
    _zc_zhuce(registry, "desktop_open_app_plan", "生成打开桌面应用的计划，默认需要确认。", "A3", desktop_open_app_plan_adapter)
    _zc_zhuce(registry, "desktop_file_dialog_plan", "生成处理文件选择弹窗的计划。", "A3", desktop_file_dialog_plan_adapter)
    _zc_zhuce(registry, "lead_score", "对企业线索进行评分和优先级排序。", "A2", lead_score_adapter)
    _zc_zhuce(registry, "company_profile_build", "生成企业客户画像。", "A2", company_profile_build_adapter)
    _zc_zhuce(registry, "contact_plan_generate", "生成客户触达计划。", "A2", contact_plan_generate_adapter)
    _zc_zhuce(registry, "sales_script_generate", "生成电话、微信、邮件销售话术。", "A2", sales_script_generate_adapter)
    _zc_zhuce(registry, "objection_handle", "生成异议处理策略。", "A2", objection_handle_adapter)
    _zc_zhuce(registry, "followup_plan", "生成跟进计划和下一步动作。", "A2", followup_plan_adapter)
    _zc_zhuce(registry, "crm_note_generate", "生成 CRM 记录和复盘摘要。", "A2", crm_note_generate_adapter)
    _zc_zhuce(registry, "deal_stage_judge", "判断商机阶段和推进风险。", "A2", deal_stage_judge_adapter)
    _zc_zhuce(registry, "paper_search_plan", "生成论文检索计划和关键词矩阵。", "A2", paper_search_plan_adapter)
    _zc_zhuce(registry, "paper_read_plan", "生成论文阅读和证据提取计划。", "A1", paper_read_plan_adapter)
    _zc_zhuce(registry, "paper_summarize", "基于论文文本生成结构化摘要。", "A2", paper_summarize_adapter)
    _zc_zhuce(registry, "paper_compare", "对比多篇论文的方法、数据集、指标和局限。", "A2", paper_compare_adapter)
    _zc_zhuce(registry, "paper_method_extract", "抽取论文方法、模型结构、训练数据和工程启发。", "A2", paper_method_extract_adapter)
    _zc_zhuce(registry, "paper_benchmark_extract", "抽取论文 benchmark、指标、baseline 和结论。", "A2", paper_benchmark_extract_adapter)
    _zc_zhuce(registry, "tech_trend_report", "生成技术趋势和工程落地报告。", "A2", tech_trend_report_adapter)
    _zc_zhuce(registry, "app_spec_build", "生成低代码应用需求规格。", "A2", app_spec_build_adapter)
    _zc_zhuce(registry, "app_scaffold_plan", "生成应用脚手架计划，不直接写文件。", "A2", app_scaffold_plan_adapter)
    _zc_zhuce(registry, "frontend_page_spec", "生成前端页面规格。", "A2", frontend_page_spec_adapter)
    _zc_zhuce(registry, "backend_api_spec", "生成后端 API 规格。", "A2", backend_api_spec_adapter)
    _zc_zhuce(registry, "db_schema_generate", "生成数据库表结构设计。", "A2", db_schema_generate_adapter)
    _zc_zhuce(registry, "app_preview_plan", "生成应用预览与验收计划。", "A2", app_preview_plan_adapter)
    _zc_zhuce(registry, "app_package_plan", "生成应用打包交付计划。", "A2", app_package_plan_adapter)
# END_ENTERPRISE_EXTENSION_REGISTRATION

# BEGIN_OPS_EXTENSION_REGISTRATION
    _zc_zhuce(registry, "ops_funnel_map", "生成获客到成交的全链路漏斗地图。", "A1", ops_funnel_map_adapter)
    _zc_zhuce(registry, "ops_customer_journey_map", "生成客户旅程地图和关键触点。", "A1", ops_customer_journey_map_adapter)
    _zc_zhuce(registry, "ops_bottleneck_detect", "根据漏斗数据识别运营瓶颈。", "A1", ops_bottleneck_detect_adapter)
    _zc_zhuce(registry, "ops_next_best_action", "生成下一步最佳运营动作建议。", "A2", ops_next_best_action_adapter)
    _zc_zhuce(registry, "ops_weekly_growth_plan", "生成周度增长运营计划。", "A2", ops_weekly_growth_plan_adapter)
    _zc_zhuce(registry, "ops_monthly_revenue_review", "生成月度收入运营复盘。", "A2", ops_monthly_revenue_review_adapter)
    _zc_zhuce(registry, "market_segment_analyze", "分析目标市场和细分行业机会。", "A2", market_segment_analyze_adapter)
    _zc_zhuce(registry, "icp_profile_build", "构建理想客户画像 ICP。", "A2", icp_profile_build_adapter)
    _zc_zhuce(registry, "buyer_persona_build", "构建买方角色画像。", "A2", buyer_persona_build_adapter)
    _zc_zhuce(registry, "pain_point_extract", "从材料中抽取客户痛点。", "A1", pain_point_extract_adapter)
    _zc_zhuce(registry, "competitor_positioning_map", "生成竞品定位和差异化地图。", "A2", competitor_positioning_map_adapter)
    _zc_zhuce(registry, "value_proposition_design", "生成价值主张和卖点表达。", "A2", value_proposition_design_adapter)
    _zc_zhuce(registry, "channel_strategy_plan", "生成获客渠道策略。", "A2", channel_strategy_plan_adapter)
    _zc_zhuce(registry, "campaign_plan_build", "生成营销活动计划。", "A2", campaign_plan_build_adapter)
    _zc_zhuce(registry, "campaign_budget_plan", "生成活动预算分配计划。", "A2", campaign_budget_plan_adapter)
    _zc_zhuce(registry, "channel_roi_estimate", "估算渠道ROI和获客成本。", "A2", channel_roi_estimate_adapter)
    _zc_zhuce(registry, "landing_page_audit", "检查落地页转化问题。", "A1", landing_page_audit_adapter)
    _zc_zhuce(registry, "event_lead_capture_plan", "生成活动线索收集方案。", "A2", event_lead_capture_plan_adapter)
    _zc_zhuce(registry, "content_calendar_build", "生成内容日历。", "A2", content_calendar_build_adapter)
    _zc_zhuce(registry, "content_topic_cluster", "生成内容选题集群。", "A2", content_topic_cluster_adapter)
    _zc_zhuce(registry, "case_study_generate", "生成客户案例结构。", "A2", case_study_generate_adapter)
    _zc_zhuce(registry, "landing_page_copy_check", "检查落地页文案转化力。", "A1", landing_page_copy_check_adapter)
    _zc_zhuce(registry, "short_video_script_generate", "生成短视频转化脚本。", "A2", short_video_script_generate_adapter)
    _zc_zhuce(registry, "conversion_material_pack", "生成转化素材包清单。", "A2", conversion_material_pack_adapter)
    _zc_zhuce(registry, "lead_signal_extract", "从文本/表格记录中抽取线索需求信号。", "A1", lead_signal_extract_adapter)
    _zc_zhuce(registry, "lead_fit_score", "计算ICP匹配分。", "A2", lead_fit_score_adapter)
    _zc_zhuce(registry, "lead_intent_score", "计算意向强度分。", "A2", lead_intent_score_adapter)
    _zc_zhuce(registry, "lead_priority_rank", "对线索列表做优先级排序。", "A2", lead_priority_rank_adapter)
    _zc_zhuce(registry, "account_score", "对企业账户做综合评分。", "A2", account_score_adapter)
    _zc_zhuce(registry, "stakeholder_map", "生成多角色决策链地图。", "A2", stakeholder_map_adapter)
    _zc_zhuce(registry, "nurture_sequence_generate", "生成客户培育序列。", "A2", nurture_sequence_generate_adapter)
    _zc_zhuce(registry, "wechat_followup_plan", "生成微信/企微跟进计划。", "A2", wechat_followup_plan_adapter)
    _zc_zhuce(registry, "email_sequence_generate", "生成邮件培育序列。", "A2", email_sequence_generate_adapter)
    _zc_zhuce(registry, "community_operation_plan", "生成社群运营计划。", "A2", community_operation_plan_adapter)
    _zc_zhuce(registry, "touchpoint_log_parse", "解析客户触达记录。", "A1", touchpoint_log_parse_adapter)
    _zc_zhuce(registry, "next_touch_recommend", "推荐下一次触达动作。", "A2", next_touch_recommend_adapter)
    _zc_zhuce(registry, "sales_call_brief", "生成销售电话前简报。", "A2", sales_call_brief_adapter)
    _zc_zhuce(registry, "sales_discovery_question_set", "生成需求诊断问题集。", "A2", sales_discovery_question_set_adapter)
    _zc_zhuce(registry, "spin_need_diagnose", "用SPIN框架诊断需求。", "A2", spin_need_diagnose_adapter)
    _zc_zhuce(registry, "objection_map_build", "生成异议地图和应对策略。", "A2", objection_map_build_adapter)
    _zc_zhuce(registry, "meeting_summary_to_crm", "把会议纪要转成CRM记录草稿。", "A2", meeting_summary_to_crm_adapter)
    _zc_zhuce(registry, "deal_stage_judge", "判断商机阶段。", "A2", deal_stage_judge_adapter)
    _zc_zhuce(registry, "proposal_outline_build", "生成方案书大纲。", "A2", proposal_outline_build_adapter)
    _zc_zhuce(registry, "roi_argument_build", "生成ROI论证。", "A2", roi_argument_build_adapter)
    _zc_zhuce(registry, "pricing_strategy_plan", "生成报价策略。", "A2", pricing_strategy_plan_adapter)
    _zc_zhuce(registry, "decision_risk_detect", "识别成交决策风险。", "A1", decision_risk_detect_adapter)
    _zc_zhuce(registry, "closing_plan_generate", "生成成交推进计划。", "A2", closing_plan_generate_adapter)
    _zc_zhuce(registry, "contract_handoff_check", "生成合同交接检查清单。", "A1", contract_handoff_check_adapter)
    _zc_zhuce(registry, "crm_pipeline_profile", "生成CRM销售漏斗画像。", "A1", crm_pipeline_profile_adapter)
    _zc_zhuce(registry, "pipeline_velocity_check", "检查销售漏斗速度和卡点。", "A1", pipeline_velocity_check_adapter)
    _zc_zhuce(registry, "deal_win_probability", "估算商机胜率。", "A2", deal_win_probability_adapter)
    _zc_zhuce(registry, "multi_touch_attribution_plan", "生成多触点归因计划。", "A2", multi_touch_attribution_plan_adapter)
    _zc_zhuce(registry, "channel_contribution_estimate", "估算渠道贡献。", "A2", channel_contribution_estimate_adapter)
    _zc_zhuce(registry, "revops_dashboard_spec", "生成RevOps看板规格。", "A2", revops_dashboard_spec_adapter)
    _zc_zhuce(registry, "growth_experiment_design", "设计增长实验。", "A2", growth_experiment_design_adapter)
    _zc_zhuce(registry, "ab_test_plan", "生成A/B测试计划。", "A2", ab_test_plan_adapter)
    _zc_zhuce(registry, "uplift_targeting_plan", "生成增量转化目标选择计划。", "A2", uplift_targeting_plan_adapter)
    _zc_zhuce(registry, "bandit_allocation_plan", "生成多臂老虎机预算/流量分配计划。", "A2", bandit_allocation_plan_adapter)
    _zc_zhuce(registry, "experiment_result_analyze", "分析增长实验结果。", "A1", experiment_result_analyze_adapter)
    _zc_zhuce(registry, "growth_retrospective_report", "生成增长复盘报告。", "A2", growth_retrospective_report_adapter)
# END_OPS_EXTENSION_REGISTRATION

    # BEGIN_WANGWEN_UNIFIED_PIPELINE_REGISTRATION
    _zc_zhuce(registry, "wangwen_novel_factory_run", "网络小说完整生产主流水线：从类型承诺、故事圣经、章节简报、文风声纹到终审修订一次穿线。", "A2", wangwen_novel_factory_run_adapter)
    _zc_zhuce(registry, "wangwen_novel_bible_build", "生成网络小说故事圣经：类型承诺、主角欲望、金手指、世界规则、长线伏笔。", "A2", wangwen_novel_bible_build_adapter)
    _zc_zhuce(registry, "wangwen_chapter_brief_build", "生成单章生产简报：场景目标、冲突、爽点、钩子、文风、人物声线。", "A2", wangwen_chapter_brief_build_adapter)
    _zc_zhuce(registry, "wangwen_draft_quality_check", "检查网络小说成稿质量：节奏、AI腔、套话、抽象密度、人物声线、留存风险。", "A1", wangwen_draft_quality_check_adapter)
    _zc_zhuce(registry, "wangwen_revision_plan_build", "生成网络小说修订补丁：按质量问题给出可执行改写路线。", "A2", wangwen_revision_plan_build_adapter)
    # END_WANGWEN_UNIFIED_PIPELINE_REGISTRATION

    return registry


def _register_runtime_host_plugin_tools(
    registry: RuntimeToolRegistry,
    project_index: ProjectIndexBridge,
    diagnostics: EngineeringDiagnosticBridge,
    quality_gate: QualityGateBridge,
    delivery_manifest: DeliveryManifestBridge,
) -> None:
    registry_audit = AuditBridge()
    registry_tickets = ConfirmationTicketStore()
    context_memory = ContextMemoryBridge()
    experience = ExperienceSynthesisBridge()
    skill_queue = SkillReviewQueueBridge()
    tool_requests = ToolProductionRequestBridge()
    exoskeleton = ExecutionExoskeletonBridge()
    shell_mount = ShellSystemMountBridge()
    project_repair = ProjectRepairPlanBridge()
    delivery_standardization = DeliveryStandardizationBridge()
    provider_adaptation = ProviderAdaptationBridge()
    learning_convergence = LearningConvergenceBridge()
    recovery_coordination = RecoveryCoordinationBridge()
    governance_execution = GovernanceExecutionBridge()
    planner_context = PlannerContextIntegrationBridge()
    p0_one = L638P0SystemIntegrationBridge()
    p0_two = L639P0SystemIntegrationBridge()
    planner_execution = PlannerExecutionController()
    learning_contract = LearningAssetContractBridge()
    learning_sandbox = LearningAssetSandboxAlignmentBridge()
    candidate_sandbox = LearningAssetCandidateSandboxBridge()
    release_gate = LearningAssetReleaseGateBridge()
    activation = LearningAssetActivationBridge(registry)
    learning_adapter = LearningAssetAdapterBridge(activation)

    def state_provider() -> dict[str, Any]:
        return {
            "available_tools": registry.names(),
            "available_modules": discover_runtime_module_files(),
        }

    _zc_zhuce(registry, "synthesize_experience_candidates", "Synthesize safe experience candidates for later review.", "A2", build_synthesize_experience_adapter(experience, context_memory, diagnostics, quality_gate, delivery_manifest))
    _zc_zhuce(registry, "queue_skill_candidates", "Queue skill candidates for review.", "A2", build_queue_skill_candidates_adapter(skill_queue, experience))
    _zc_zhuce(registry, "queue_tool_production_requests", "Queue tool production requests for sandbox review.", "A2", build_queue_tool_production_requests_adapter(tool_requests, experience))
    _zc_zhuce(registry, "build_execution_exoskeleton", "Build execution exoskeleton compression report.", "A2", build_execution_exoskeleton_adapter(exoskeleton, experience, skill_queue, tool_requests))
    _zc_zhuce(registry, "build_shell_system_mount", "Build shell-system mount report without mutating the kernel.", "A2", build_shell_system_mount_adapter(shell_mount, state_provider))
    _zc_zhuce(registry, "build_project_repair_plan", "Build project repair planning report.", "A2", build_project_repair_plan_adapter(project_repair, project_index, diagnostics, state_provider))
    _zc_zhuce(registry, "build_delivery_standardization", "Build delivery standardization report.", "A2", build_delivery_standardization_adapter(delivery_standardization, quality_gate, diagnostics, delivery_manifest, project_repair, shell_mount, registry_audit))
    _zc_zhuce(registry, "build_provider_adaptation", "Build provider adaptation report.", "A2", build_provider_adaptation_adapter(provider_adaptation, shell_mount, delivery_standardization, registry_audit))
    _zc_zhuce(registry, "build_learning_convergence", "Build learning convergence report.", "A2", build_learning_convergence_adapter(learning_convergence, experience, skill_queue, tool_requests, exoskeleton))
    _zc_zhuce(registry, "build_recovery_coordination", "Build recovery coordination report.", "A2", build_recovery_coordination_adapter(recovery_coordination, diagnostics, quality_gate, project_repair, learning_convergence, delivery_standardization, registry_audit))
    _zc_zhuce(registry, "build_governance_execution", "Build low-friction governance execution report.", "A2", build_governance_execution_adapter(governance_execution, recovery_coordination, learning_convergence, provider_adaptation, delivery_standardization, project_repair, shell_mount, registry_audit, registry_tickets))
    _zc_zhuce(registry, "build_planner_context", "Build unified planner context report.", "A2", build_planner_context_integration_adapter(planner_context, shell_mount, project_repair, delivery_standardization, provider_adaptation, learning_convergence, recovery_coordination, governance_execution))

    _zc_zhuce(registry, "build_l6_38_provider_integration", "Build L6.38 provider integration report.", "A2", build_l6_38_provider_adapter(p0_one, provider_adaptation))
    _zc_zhuce(registry, "build_l6_38_budget_snapshot", "Build L6.38 budget snapshot.", "A2", build_l6_38_budget_adapter(p0_one, planner_execution))
    _zc_zhuce(registry, "build_l6_38_skill_integration", "Build L6.38 skill integration report.", "A2", build_l6_38_skill_adapter(p0_one, skill_queue))
    _zc_zhuce(registry, "build_l6_38_handoff_integration", "Build L6.38 handoff integration report.", "A2", build_l6_38_handoff_adapter(p0_one))
    _zc_zhuce(registry, "build_l6_38_p0_integration", "Build L6.38 P0 integration report.", "A2", build_l6_38_p0_report_adapter(p0_one))
    _zc_zhuce(registry, "build_l6_39_memory_integration", "Build L6.39 memory integration report.", "A2", build_l6_39_memory_adapter(p0_two, context_memory))
    _zc_zhuce(registry, "build_l6_39_audit_integration", "Build L6.39 audit integration report.", "A2", build_l6_39_audit_adapter(p0_two, registry_audit))
    _zc_zhuce(registry, "build_l6_39_recovery_integration", "Build L6.39 recovery integration report.", "A2", build_l6_39_recovery_adapter(p0_two, recovery_coordination))
    _zc_zhuce(registry, "build_l6_39_quality_gate_integration", "Build L6.39 quality gate integration report.", "A2", build_l6_39_quality_gate_adapter(p0_two, quality_gate))
    _zc_zhuce(registry, "build_l6_39_p0_integration", "Build L6.39 P0 integration report.", "A2", build_l6_39_p0_report_adapter(p0_two))

    _zc_zhuce(registry, "learning_asset_contract_guide", "Show the learning asset contract guide.", "A2", build_learning_asset_contract_guide_adapter())
    _zc_zhuce(registry, "learning_asset_contract_normalize", "Normalize learning asset contracts.", "A2", build_learning_asset_contract_normalize_adapter(learning_contract, experience, skill_queue, tool_requests, lambda: registry.describe()))
    _zc_zhuce(registry, "learning_asset_contract_validate", "Validate learning asset contracts.", "A2", build_learning_asset_contract_validate_adapter(learning_contract))
    _zc_zhuce(registry, "learning_asset_sandbox_guide", "Show learning asset sandbox guide.", "A2", build_learning_asset_sandbox_guide_adapter())
    _zc_zhuce(registry, "learning_asset_sandbox_align", "Align learning asset contracts to sandbox requests.", "A2", build_learning_asset_sandbox_align_adapter(learning_sandbox, learning_contract, tool_requests))
    _zc_zhuce(registry, "learning_asset_sandbox_validate", "Validate learning asset sandbox alignment.", "A2", build_learning_asset_sandbox_validate_adapter(learning_sandbox, learning_contract, tool_requests))
    _zc_zhuce(registry, "learning_asset_candidate_sandbox_guide", "Show candidate sandbox guide.", "A2", build_learning_asset_candidate_sandbox_guide_adapter())
    _zc_zhuce(registry, "learning_asset_candidate_sandbox_build", "Build isolated learning asset candidate package.", "A3", build_learning_asset_candidate_sandbox_build_adapter(candidate_sandbox, learning_contract, learning_sandbox))
    _zc_zhuce(registry, "learning_asset_candidate_sandbox_validate", "Validate candidate sandbox package.", "A2", build_learning_asset_candidate_sandbox_validate_adapter(candidate_sandbox))
    _zc_zhuce(registry, "learning_asset_candidate_sandbox_review", "Review candidate sandbox package.", "A2", build_learning_asset_candidate_sandbox_review_adapter(candidate_sandbox))
    _zc_zhuce(registry, "learning_asset_release_gate_guide", "Show learning asset release gate guide.", "A2", build_learning_asset_release_gate_guide_adapter())
    _zc_zhuce(registry, "learning_asset_release_gate_check", "Check learning asset release gate.", "A2", build_learning_asset_release_gate_check_adapter(release_gate, candidate_sandbox))
    _zc_zhuce(registry, "learning_asset_activation_guide", "Show learning asset activation guide.", "A2", build_learning_asset_activation_guide_adapter())
    _zc_zhuce(registry, "learning_asset_activation_apply", "Apply governed learning asset activation.", "A3", build_learning_asset_activation_apply_adapter(activation, release_gate, candidate_sandbox))
    _zc_zhuce(registry, "learning_asset_activation_status", "Read active learning asset status.", "A2", build_learning_asset_activation_status_adapter(activation))
    _zc_zhuce(registry, "learning_asset_activation_smoke", "Smoke active learning assets.", "A3", build_learning_asset_activation_smoke_adapter(activation))
    _zc_zhuce(registry, "learning_asset_adapter_guide", "Show learning asset adapter guide.", "A2", build_learning_asset_adapter_guide_adapter(learning_adapter))
    _zc_zhuce(registry, "learning_asset_adapter_template_list", "List learning asset adapter templates.", "A2", build_learning_asset_adapter_template_list_adapter(learning_adapter))
    _zc_zhuce(registry, "learning_asset_adapter_template_normalize", "Normalize learning asset adapter template spec.", "A2", build_learning_asset_adapter_template_normalize_adapter(learning_adapter))
    _zc_zhuce(registry, "learning_asset_adapter_template_validate", "Validate learning asset adapter template spec.", "A2", build_learning_asset_adapter_template_validate_adapter(learning_adapter))
    _zc_zhuce(registry, "learning_asset_adapter_template_smoke", "Smoke learning asset adapter templates.", "A2", build_learning_asset_adapter_template_smoke_adapter(learning_adapter))
    _zc_zhuce(registry, "learning_asset_adapter_drill", "Drill learning asset adapter activation.", "A3", build_learning_asset_adapter_drill_adapter(learning_adapter))


def _default_memory_store() -> MemoryStoreBridge | None:
    base = os.environ.get("TIANGONG_STATE_DIR") or os.environ.get("LINYUANZHE_STATE_DIR")
    if not base:
        base = str(Path(gettempdir()) / "tiangong_runtime_host_state")
    try:
        return MemoryStoreBridge(Path(base) / "memory" / "memory_store.jsonl")
    except Exception:
        return None


def _looks_like_grounded_file_task(text: str) -> bool:
    lowered = str(text or "").lower()
    markers = (
        ".txt",
        ".md",
        ".json",
        ".csv",
        ".docx",
        ".xlsx",
        "read",
        "cat ",
        "file",
        "文件",
        "读取",
        "读",
        "看到",
        "看一下",
        "看下",
        "提示词",
        "目录",
        "列出",
    )
    return any(marker in lowered for marker in markers)


def _entries_from_results(results: list[ToolResult]) -> list[str]:
    entries: list[str] = []
    for result in results:
        if result.tool_name != "list_dir" or not result.ok:
            continue
        raw_entries = result.data.get("entries") if isinstance(result.data, dict) else None
        if isinstance(raw_entries, list):
            for item in raw_entries:
                text = str(item)
                if text.startswith("file\t"):
                    entries.append(text.split("\t", 1)[1])
    return entries


def _extract_first_mentioned_path(user_message: str) -> str:
    text = str(user_message or "")
    patterns = (
        r"([A-Za-z]:[\\/][^\s\"'<>|]+)",
        r"([^\s\"'<>|]+\.(?:txt|md|json|csv|py|js|ts|tsx|jsx|html|css|docx|pdf|pptx|xlsx))",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip().rstrip(".,;:)]}\"'")
    return ""


def _choose_file_from_entries(user_message: str, entries: list[str]) -> str:
    files = [entry for entry in entries if entry and not entry.endswith("/")]
    if not files:
        return ""
    text = _normalize_match_text(user_message)
    candidates: list[tuple[float, str]] = []
    for path in files:
        name = Path(path).name
        stem = Path(path).stem
        normalized_name = _normalize_match_text(name)
        normalized_stem = _normalize_match_text(stem)
        score = 0.0
        if normalized_name and normalized_name in text:
            score = max(score, 1.0)
        if normalized_stem and normalized_stem in text:
            score = max(score, 0.95)
        score = max(score, SequenceMatcher(None, normalized_stem, text).ratio())
        if Path(path).suffix.lower() == ".txt" and ("txt" in text or "提示词" in user_message or "文件" in user_message):
            score += 0.12
        if "提示词" in name and "提示词" in user_message:
            score += 0.20
        candidates.append((score, path))
    candidates.sort(key=lambda item: item[0], reverse=True)
    if not candidates:
        return ""
    best_score, best_path = candidates[0]
    txt_files = [path for path in files if Path(path).suffix.lower() == ".txt"]
    if len(txt_files) == 1 and ("txt" in text or "文件" in user_message):
        return txt_files[0]
    return best_path if best_score >= 0.36 else ""


def _normalize_match_text(value: str) -> str:
    return re.sub(r"[\s_\-。，“”\"'`~!@#$%^&*()[\]{}:;，、/\\]+", "", str(value or "").lower())


def _activation_requests_tools(activation_form: Any | None) -> bool:
    if activation_form is None:
        return False
    if isinstance(activation_form, ActivationForm):
        return bool(activation_form.activates_runtime_tools)
    if isinstance(activation_form, dict):
        return bool(activation_form.get("tools_requested") or activation_form.get("activates_runtime_tools"))
    return bool(getattr(activation_form, "activates_runtime_tools", False))


def _derive_affective_sources(user_message: str) -> tuple[SevenEmotionSignalSources, SixDesireSignalSources]:
    text = str(user_message or "").lower()

    def has_any(*tokens: str) -> bool:
        return any(token.lower() in text for token in tokens)

    risk = 0.72 if has_any("风险", "危险", "高危", "失败", "崩", "bug", "错误", "报错", "焦虑", "紧张", "压力", "担心", "来不及", "截止", "紧迫") else 0.18
    reflection = 0.68 if has_any("看看", "确认", "分析", "为什么", "检查", "对比") else 0.32
    obstruction = 0.66 if has_any("不对", "问题", "bug", "报错", "失败", "不能", "卡住", "阻塞", "超时") else 0.10
    achievement = 0.76 if has_any("执行", "重写", "修改", "修复", "完成", "继续") else 0.46
    order = 0.74 if has_any("框架", "系统", "链条", "稳定", "正确", "确认") else 0.42
    curiosity = 0.60 if has_any("看看", "为什么", "怎么", "分析", "搜索") else 0.28
    joy_hit = has_any("开心", "高兴", "愉快", "不错", "好", "棒", "顺利", "成功")
    sadness_hit = has_any("难过", "伤心", "遗憾", "失败", "坏了", "不行")
    return (
        SevenEmotionSignalSources(
            joy_reward_signal=0.64 if joy_hit else 0.16,
            obstruction_violation_signal=_clamp01(obstruction),
            uncertainty_future_risk_signal=_clamp01(risk),
            reflection_load_signal=_clamp01(reflection),
            loss_failure_signal=0.58 if sadness_hit else 0.05,
            threat_irreversible_signal=0.72 if has_any("删除", "不可逆", "凭证", "密钥") else _clamp01(risk * 0.55),
            novelty_prediction_error_signal=0.62 if has_any("重写", "新的", "换成", "electron") else 0.20,
        ),
        SixDesireSignalSources(
            survival_resource_boundary_signal=_clamp01(risk),
            curiosity_knowledge_gap_signal=_clamp01(curiosity),
            achievement_goal_gap_signal=_clamp01(achievement),
            connection_alignment_signal=0.32,
            order_entropy_signal=_clamp01(order),
            rest_fatigue_recovery_signal=0.18,
        ),
    )


def _estimate_context_usage(user_message: str, external_context_hint: str = "") -> float:
    approx_chars = len(str(user_message or "")) + len(str(external_context_hint or ""))
    return _clamp01(approx_chars / 100_000.0)


def _merge_chain_summaries(
    left: LongChainRunSummary | None,
    right: LongChainRunSummary | None,
) -> LongChainRunSummary | None:
    if left is None:
        return right
    if right is None:
        return left
    checkpoints = list(left.checkpoints) + list(right.checkpoints)
    return LongChainRunSummary(
        total_steps=int(left.total_steps) + int(right.total_steps),
        executed_steps=int(left.executed_steps) + int(right.executed_steps),
        failure_count=int(left.failure_count) + int(right.failure_count),
        stopped_reason=right.stopped_reason if right.stopped_reason != "completed" else left.stopped_reason,
        checkpoints=checkpoints,
        progress_snapshots=list(left.progress_snapshots) + list(right.progress_snapshots),
    )


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _tool_invocation_public(invocation: ToolInvocation) -> dict[str, Any]:
    return {"step_id": invocation.step_id, "tool_name": invocation.tool_name, "arguments": dict(invocation.arguments), "reason": invocation.reason}


def _disabled_snapshot(name: str) -> dict[str, Any]:
    return {
        "schema": "tiangong.runtime_host.disabled_legacy_surface.v1",
        "status": "not_enabled",
        "surface": name,
        "message": "Legacy RuntimeEntry management surface was removed. Main execution now flows through RuntimeHost -> L3/L4 -> L5/L6.",
    }


def _export_disabled_snapshot(path: str | Path, name: str) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(_disabled_snapshot(name), ensure_ascii=False, indent=2), encoding="utf-8")
    return target
