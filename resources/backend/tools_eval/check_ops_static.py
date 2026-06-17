from __future__ import annotations
import argparse
from pathlib import Path

EXPECTED_SKILLS = ['43_zonghe_yunying_zongkong', '44_shichang_dongcha_icp', '45_huoke_qudao_huodong', '46_neirong_yunying_zhuanhua', '47_xiansuo_shibie_pingfen', '48_siyu_peiyu_chuda', '49_xiaoshou_zhuanhua_xuqiu', '50_fangan_baojia_chengjiao', '51_revops_shuju_guiyin', '52_zengzhang_shiyan_fupan']
EXPECTED_TOOLS = ['ops_funnel_map', 'ops_customer_journey_map', 'ops_bottleneck_detect', 'ops_next_best_action', 'ops_weekly_growth_plan', 'ops_monthly_revenue_review', 'market_segment_analyze', 'icp_profile_build', 'buyer_persona_build', 'pain_point_extract', 'competitor_positioning_map', 'value_proposition_design', 'channel_strategy_plan', 'campaign_plan_build', 'campaign_budget_plan', 'channel_roi_estimate', 'landing_page_audit', 'event_lead_capture_plan', 'content_calendar_build', 'content_topic_cluster', 'case_study_generate', 'landing_page_copy_check', 'short_video_script_generate', 'conversion_material_pack', 'lead_signal_extract', 'lead_fit_score', 'lead_intent_score', 'lead_priority_rank', 'account_score', 'stakeholder_map', 'nurture_sequence_generate', 'wechat_followup_plan', 'email_sequence_generate', 'community_operation_plan', 'touchpoint_log_parse', 'next_touch_recommend', 'sales_call_brief', 'sales_discovery_question_set', 'spin_need_diagnose', 'objection_map_build', 'meeting_summary_to_crm', 'deal_stage_judge', 'proposal_outline_build', 'roi_argument_build', 'pricing_strategy_plan', 'decision_risk_detect', 'closing_plan_generate', 'contract_handoff_check', 'crm_pipeline_profile', 'pipeline_velocity_check', 'deal_win_probability', 'multi_touch_attribution_plan', 'channel_contribution_estimate', 'revops_dashboard_spec', 'growth_experiment_design', 'ab_test_plan', 'uplift_targeting_plan', 'bandit_allocation_plan', 'experiment_result_analyze', 'growth_retrospective_report']

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", required=True)
    args = parser.parse_args()
    backend = Path(args.backend)
    runtime = backend / "tiangong_agent_runtime"
    errors = []
    for s in EXPECTED_SKILLS:
        p = runtime / "skills" / s / "SKILL.md"
        if not p.exists():
            errors.append(f"missing_skill:{s}")
        else:
            txt = p.read_text(encoding="utf-8")
            for h in ["## 什么时候用", "## 标准流程", "## 工具"]:
                if h not in txt:
                    errors.append(f"bad_skill_format:{s}:{h}")
    schema_txt = (runtime / "tool_schemas.py").read_text(encoding="utf-8") if (runtime / "tool_schemas.py").exists() else ""
    ops_schema_txt = (runtime / "ops_tool_schemas.py").read_text(encoding="utf-8") if (runtime / "ops_tool_schemas.py").exists() else ""
    rt_txt = (runtime / "runtime_entry.py").read_text(encoding="utf-8") if (runtime / "runtime_entry.py").exists() else ""
    for t in EXPECTED_TOOLS:
        if t not in schema_txt and t not in ops_schema_txt:
            errors.append(f"missing_schema:{t}")
        if t not in rt_txt:
            errors.append(f"missing_registration:{t}")
    if not (runtime / "adapters" / "ops_extension_adapters.py").exists():
        errors.append("missing_adapter_file")
    print("ops_static_check")
    print(f"skills={len(EXPECTED_SKILLS)} tools={len(EXPECTED_TOOLS)} errors={len(errors)}")
    for e in errors[:200]:
        print("ERROR", e)
    raise SystemExit(1 if errors else 0)

if __name__ == "__main__":
    main()
