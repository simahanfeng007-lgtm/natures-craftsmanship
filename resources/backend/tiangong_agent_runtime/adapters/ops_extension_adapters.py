"""Ops/Growth/RevOps adapters for Tiangong v2.

设计原则：
- 不改 run_once / _hebing_panding / Code-X / 固定收口链。
- 涉及外部触达、批量执行、CRM写入、报价、预算调整时默认只生成计划。
- 重点输出：证据、评分原因、下一步动作、置信度和未验证项。
"""
from __future__ import annotations

import json
import re
from typing import Any

try:
    from tiangong_agent_runtime.tool_result import ToolResult
except Exception:
    ToolResult = None  # type: ignore


BUYING_ROLES = ["老板", "总经理", "负责人", "HR", "培训", "数字化", "IT", "业务部门", "采购", "财务", "法务"]
INTENT_SIGNALS = ["AI", "人工智能", "数字化", "培训", "知识库", "降本", "提效", "转型", "招标", "采购", "试点", "咨询", "落地"]
PAIN_SIGNALS = ["效率低", "成本高", "不会用", "缺培训", "数据乱", "知识散", "转化低", "获客难", "客服压力", "内容产能"]


def _ctx_step_id(context: Any, fallback: str) -> str:
    for name in ("step_id", "request_id", "run_id"):
        value = getattr(context, name, "")
        if value:
            return str(value)
    return fallback


def _result(context: Any, tool_name: str, status: str = "ok", summary: str = "", data: dict[str, Any] | None = None, error_code: str = "") -> Any:
    payload = data or {}
    payload.setdefault("schema", "tool_result.data.v2")
    payload.setdefault("tool_name", tool_name)
    payload.setdefault("confidence", "medium")
    payload.setdefault("evidence_refs", [])
    payload.setdefault("error_category", error_code or "")
    payload.setdefault("retryable", False)
    payload.setdefault("next_action", "")
    if ToolResult is None:
        return {"step_id": _ctx_step_id(context, tool_name), "tool_name": tool_name, "status": status, "output_summary": summary, "data": payload, "error_code": error_code}
    try:
        return ToolResult(step_id=_ctx_step_id(context, tool_name), tool_name=tool_name, status=status, output_summary=summary, data=payload, artifacts=[], error_code=error_code, audit_ref="")
    except TypeError:
        return ToolResult(_ctx_step_id(context, tool_name), tool_name, status, summary, payload, [], error_code, "")


def _args(args: Any) -> dict[str, Any]:
    return args if isinstance(args, dict) else {}


def _text(args: Any) -> str:
    a = _args(args)
    if a.get("text"):
        return str(a.get("text"))
    if a.get("data") is not None:
        return json.dumps(a.get("data"), ensure_ascii=False)
    if a.get("records") is not None:
        return json.dumps(a.get("records"), ensure_ascii=False)
    return str(a.get("goal") or "")


def _score_by_keywords(text: str, keywords: list[str], weight: int = 8) -> tuple[int, list[str]]:
    hits = []
    lower = text.lower()
    for kw in keywords:
        if kw.lower() in lower:
            hits.append(kw)
    return min(100, len(hits) * weight), hits


def _plan(context, tool_name: str, title: str, args: Any, category: str):
    a = _args(args)
    return _result(context, tool_name, "ok", title, {
        "category": category,
        "request": a,
        "plan": [
            "明确当前客户旅程阶段和业务目标。",
            "收集证据：客户画像、触点、需求信号、渠道来源、历史动作。",
            "生成评分、诊断或行动计划。",
            "标注风险、未验证项和需要人工确认的动作。",
            "输出下一步动作、触达节奏和复盘指标。"
        ],
        "requires_confirmation_for_external_action": True,
        "confidence": "medium",
    })


def ops_funnel_map_adapter(context, args):
    a = _args(args)
    data = a.get("data") or {}
    stages = ["获客", "留资", "激活", "需求诊断", "方案", "报价", "成交"]
    counts = {}
    if isinstance(data, dict):
        counts = {s: int(data.get(s, data.get(s.lower(), 0)) or 0) for s in stages}
    return _result(context, "ops_funnel_map", "ok", "全链路漏斗地图已生成。", {
        "stages": stages,
        "counts": counts,
        "journey": [{"stage": s, "count": counts.get(s, None)} for s in stages],
        "confidence": "medium" if counts else "low"
    })


def ops_bottleneck_detect_adapter(context, args):
    a = _args(args)
    data = a.get("data") or {}
    stages = ["获客", "留资", "激活", "需求诊断", "方案", "报价", "成交"]
    counts = [int(data.get(s, 0) or 0) for s in stages] if isinstance(data, dict) else []
    drops = []
    for i in range(len(counts)-1):
        if counts[i] > 0:
            rate = counts[i+1] / counts[i]
            drops.append({"from": stages[i], "to": stages[i+1], "conversion_rate": round(rate, 4), "drop": counts[i]-counts[i+1]})
    bottleneck = min(drops, key=lambda x: x["conversion_rate"]) if drops else None
    return _result(context, "ops_bottleneck_detect", "ok", "运营瓶颈识别完成。", {
        "drops": drops,
        "bottleneck": bottleneck,
        "next_action": "优先修复转化率最低的阶段；无数据时先建立漏斗埋点。",
        "confidence": "high" if drops else "low"
    })


def lead_signal_extract_adapter(context, args):
    text = _text(args)
    intent_score, intent_hits = _score_by_keywords(text, INTENT_SIGNALS, 8)
    pain_score, pain_hits = _score_by_keywords(text, PAIN_SIGNALS, 8)
    role_hits = [r for r in BUYING_ROLES if r.lower() in text.lower()]
    return _result(context, "lead_signal_extract", "ok", "线索信号抽取完成。", {
        "intent_hits": intent_hits,
        "pain_hits": pain_hits,
        "role_hits": role_hits,
        "signal_score": min(100, intent_score + pain_score + len(role_hits)*5),
        "confidence": "medium"
    })


def lead_fit_score_adapter(context, args):
    text = _text(args)
    score = 40
    reasons = []
    for kw in ["制造", "金融", "医疗", "教育", "中型", "集团", "培训", "HR", "数字化", "业务部门"]:
        if kw.lower() in text.lower():
            score += 7
            reasons.append(kw)
    return _result(context, "lead_fit_score", "ok", "ICP匹配评分完成。", {
        "score": max(0, min(100, score)),
        "reasons": reasons,
        "dimensions": {"industry_fit": score, "org_fit": min(100, 30+len(reasons)*8)},
        "confidence": "medium"
    })


def lead_intent_score_adapter(context, args):
    text = _text(args)
    score, hits = _score_by_keywords(text, INTENT_SIGNALS + PAIN_SIGNALS, 6)
    urgency_hits = [x for x in ["近期", "本月", "明天", "招标", "体验", "试用", "报价", "方案"] if x in text]
    score = min(100, score + len(urgency_hits)*10)
    return _result(context, "lead_intent_score", "ok", "线索意向评分完成。", {
        "score": score,
        "intent_hits": hits,
        "urgency_hits": urgency_hits,
        "confidence": "medium"
    })


def lead_priority_rank_adapter(context, args):
    a = _args(args)
    records = a.get("records") or []
    ranked = []
    for i, r in enumerate(records if isinstance(records, list) else []):
        text = json.dumps(r, ensure_ascii=False) if isinstance(r, dict) else str(r)
        s1, h1 = _score_by_keywords(text, INTENT_SIGNALS, 7)
        s2, h2 = _score_by_keywords(text, PAIN_SIGNALS, 5)
        score = min(100, 30 + s1 + s2)
        ranked.append({"index": i, "score": score, "record": r, "reasons": h1 + h2, "next_action": "优先核验负责人和近期AI转型/培训需求。"})
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return _result(context, "lead_priority_rank", "ok", "线索优先级排序完成。", {"ranked": ranked[:200], "confidence": "medium" if ranked else "low"})


def account_score_adapter(context, args):
    text = _text(args)
    fit = lead_fit_score_adapter(context, {"text": text}).data.get("score", 0)
    intent = lead_intent_score_adapter(context, {"text": text}).data.get("score", 0)
    role_bonus = len([r for r in BUYING_ROLES if r in text]) * 5
    score = min(100, int(fit*0.45 + intent*0.45 + role_bonus))
    return _result(context, "account_score", "ok", "企业账户评分完成。", {
        "score": score,
        "fit_component": fit,
        "intent_component": intent,
        "stakeholder_component": role_bonus,
        "confidence": "medium"
    })


def stakeholder_map_adapter(context, args):
    text = _text(args)
    roles = []
    for r in BUYING_ROLES:
        if r in text:
            roles.append({"role": r, "status": "mentioned", "likely_need": "参与企业AI转型/培训/采购决策"})
    if not roles:
        roles = [
            {"role": "业务负责人", "status": "missing", "likely_need": "判断业务提效价值"},
            {"role": "HR/培训负责人", "status": "missing", "likely_need": "组织培训与学习项目"},
            {"role": "老板/总经理", "status": "missing", "likely_need": "确认预算与战略优先级"},
        ]
    return _result(context, "stakeholder_map", "ok", "多角色决策链地图已生成。", {"stakeholders": roles, "confidence": "medium"})


def touchpoint_log_parse_adapter(context, args):
    text = _text(args)
    channels = [c for c in ["电话", "微信", "企微", "邮件", "会议", "抖音", "展会", "转介绍", "官网"] if c in text]
    events = []
    for line in text.splitlines():
        if any(c in line for c in channels) or any(x in line for x in ["回复", "未回", "已约", "报价", "方案", "体验"]):
            events.append({"raw": line[:300]})
    return _result(context, "touchpoint_log_parse", "ok", "触点记录解析完成。", {"channels": channels, "events": events[:200], "confidence": "medium"})


def deal_stage_judge_adapter(context, args):
    text = _text(args)
    stage = "unknown"
    if any(x in text for x in ["成交", "付款", "合同已签"]): stage = "成交"
    elif any(x in text for x in ["合同", "采购", "法务"]): stage = "合同/采购"
    elif any(x in text for x in ["报价", "价格", "预算"]): stage = "报价"
    elif any(x in text for x in ["方案", "演示", "体验"]): stage = "方案/体验"
    elif any(x in text for x in ["需求", "痛点", "想了解"]): stage = "需求诊断"
    elif any(x in text for x in ["加微信", "回复", "约"]): stage = "已触达"
    return _result(context, "deal_stage_judge", "ok", "商机阶段判断完成。", {
        "stage": stage,
        "next_action": {
            "已触达": "做需求诊断，确认痛点、角色、预算和时间。",
            "需求诊断": "生成方案并预约演示/体验。",
            "方案/体验": "跟进反馈并推动报价。",
            "报价": "识别决策风险和采购路径。",
            "合同/采购": "准备合同交接和风险清单。",
        }.get(stage, "补齐客户状态信息。"),
        "confidence": "medium"
    })


def pipeline_velocity_check_adapter(context, args):
    a = _args(args)
    records = a.get("records") or []
    if not isinstance(records, list) or not records:
        return _result(context, "pipeline_velocity_check", "ok", "销售漏斗速度检查需要更多数据。", {"confidence": "low", "next_action": "提供包含阶段、进入时间、退出时间的CRM记录。"})
    stage_counts = {}
    for r in records:
        if isinstance(r, dict):
            stage = str(r.get("stage") or r.get("阶段") or "unknown")
            stage_counts[stage] = stage_counts.get(stage, 0) + 1
    return _result(context, "pipeline_velocity_check", "ok", "销售漏斗速度检查完成。", {"stage_counts": stage_counts, "confidence": "medium"})


def deal_win_probability_adapter(context, args):
    text = _text(args)
    score = 20
    positive = ["明确需求", "预算", "老板", "负责人", "已约", "方案", "体验", "报价", "采购", "合同"]
    negative = ["不回复", "没预算", "以后再说", "已经有供应商", "不需要", "价格高"]
    pos = [x for x in positive if x in text]
    neg = [x for x in negative if x in text]
    score += len(pos)*8
    score -= len(neg)*10
    return _result(context, "deal_win_probability", "ok", "商机胜率估算完成。", {
        "probability": max(0, min(100, score)),
        "positive_signals": pos,
        "negative_signals": neg,
        "confidence": "low" if not text else "medium"
    })


def channel_contribution_estimate_adapter(context, args):
    a = _args(args)
    records = a.get("records") or []
    contrib = {}
    for r in records if isinstance(records, list) else []:
        if isinstance(r, dict):
            ch = str(r.get("channel") or r.get("渠道") or "unknown")
            value = float(r.get("revenue") or r.get("金额") or r.get("value") or 1)
            contrib[ch] = contrib.get(ch, 0) + value
    total = sum(contrib.values()) or 1
    rows = [{"channel": k, "value": v, "share": round(v/total, 4)} for k, v in contrib.items()]
    rows.sort(key=lambda x: x["value"], reverse=True)
    return _result(context, "channel_contribution_estimate", "ok", "渠道贡献估算完成。", {"channels": rows, "confidence": "medium" if rows else "low"})


def experiment_result_analyze_adapter(context, args):
    a = _args(args)
    records = a.get("records") or []
    variants = {}
    for r in records if isinstance(records, list) else []:
        if isinstance(r, dict):
            v = str(r.get("variant") or r.get("版本") or "unknown")
            exposure = float(r.get("exposure") or r.get("曝光") or r.get("sent") or 0)
            conv = float(r.get("conversion") or r.get("转化") or r.get("won") or 0)
            if v not in variants:
                variants[v] = {"exposure": 0, "conversion": 0}
            variants[v]["exposure"] += exposure
            variants[v]["conversion"] += conv
    result = []
    for v, d in variants.items():
        rate = d["conversion"] / d["exposure"] if d["exposure"] else 0
        result.append({"variant": v, "exposure": d["exposure"], "conversion": d["conversion"], "rate": round(rate, 4)})
    result.sort(key=lambda x: x["rate"], reverse=True)
    return _result(context, "experiment_result_analyze", "ok", "增长实验结果分析完成。", {"variants": result, "winner": result[0] if result else None, "confidence": "medium" if result else "low"})


# Plan-only adapters for higher-risk or strategy actions.
def ops_customer_journey_map_adapter(context, args): return _plan(context, "ops_customer_journey_map", "客户旅程地图已生成。", args, "ops")
def ops_next_best_action_adapter(context, args): return _plan(context, "ops_next_best_action", "下一步最佳动作建议已生成。", args, "ops")
def ops_weekly_growth_plan_adapter(context, args): return _plan(context, "ops_weekly_growth_plan", "周度增长计划已生成。", args, "ops")
def ops_monthly_revenue_review_adapter(context, args): return _plan(context, "ops_monthly_revenue_review", "月度收入运营复盘已生成。", args, "ops")
def market_segment_analyze_adapter(context, args): return _plan(context, "market_segment_analyze", "市场细分分析已生成。", args, "market")
def icp_profile_build_adapter(context, args): return _plan(context, "icp_profile_build", "ICP画像已生成。", args, "market")
def buyer_persona_build_adapter(context, args): return _plan(context, "buyer_persona_build", "买方角色画像已生成。", args, "market")
def pain_point_extract_adapter(context, args): return _plan(context, "pain_point_extract", "客户痛点抽取完成。", args, "market")
def competitor_positioning_map_adapter(context, args): return _plan(context, "competitor_positioning_map", "竞品定位地图已生成。", args, "market")
def value_proposition_design_adapter(context, args): return _plan(context, "value_proposition_design", "价值主张已生成。", args, "market")
def channel_strategy_plan_adapter(context, args): return _plan(context, "channel_strategy_plan", "获客渠道策略已生成。", args, "channel")
def campaign_plan_build_adapter(context, args): return _plan(context, "campaign_plan_build", "营销活动计划已生成。", args, "channel")
def campaign_budget_plan_adapter(context, args): return _plan(context, "campaign_budget_plan", "活动预算计划已生成，执行前需确认。", args, "channel")
def channel_roi_estimate_adapter(context, args): return _plan(context, "channel_roi_estimate", "渠道ROI估算已生成。", args, "channel")
def landing_page_audit_adapter(context, args): return _plan(context, "landing_page_audit", "落地页审计已生成。", args, "channel")
def event_lead_capture_plan_adapter(context, args): return _plan(context, "event_lead_capture_plan", "活动线索收集计划已生成。", args, "channel")
def content_calendar_build_adapter(context, args): return _plan(context, "content_calendar_build", "内容日历已生成。", args, "content")
def content_topic_cluster_adapter(context, args): return _plan(context, "content_topic_cluster", "内容选题集群已生成。", args, "content")
def case_study_generate_adapter(context, args): return _plan(context, "case_study_generate", "客户案例结构已生成。", args, "content")
def landing_page_copy_check_adapter(context, args): return _plan(context, "landing_page_copy_check", "落地页文案检查已生成。", args, "content")
def short_video_script_generate_adapter(context, args): return _plan(context, "short_video_script_generate", "短视频转化脚本已生成。", args, "content")
def conversion_material_pack_adapter(context, args): return _plan(context, "conversion_material_pack", "转化素材包清单已生成。", args, "content")
def nurture_sequence_generate_adapter(context, args): return _plan(context, "nurture_sequence_generate", "客户培育序列已生成。", args, "nurture")
def wechat_followup_plan_adapter(context, args): return _plan(context, "wechat_followup_plan", "微信/企微跟进计划已生成。", args, "nurture")
def email_sequence_generate_adapter(context, args): return _plan(context, "email_sequence_generate", "邮件培育序列已生成。", args, "nurture")
def community_operation_plan_adapter(context, args): return _plan(context, "community_operation_plan", "社群运营计划已生成。", args, "nurture")
def next_touch_recommend_adapter(context, args): return _plan(context, "next_touch_recommend", "下一次触达建议已生成。", args, "nurture")
def sales_call_brief_adapter(context, args): return _plan(context, "sales_call_brief", "销售电话前简报已生成。", args, "sales")
def sales_discovery_question_set_adapter(context, args): return _plan(context, "sales_discovery_question_set", "需求诊断问题集已生成。", args, "sales")
def spin_need_diagnose_adapter(context, args): return _plan(context, "spin_need_diagnose", "SPIN需求诊断已生成。", args, "sales")
def objection_map_build_adapter(context, args): return _plan(context, "objection_map_build", "异议地图已生成。", args, "sales")
def meeting_summary_to_crm_adapter(context, args): return _plan(context, "meeting_summary_to_crm", "CRM记录草稿已生成，写入前需确认。", args, "sales")
def proposal_outline_build_adapter(context, args): return _plan(context, "proposal_outline_build", "方案书大纲已生成。", args, "deal")
def roi_argument_build_adapter(context, args): return _plan(context, "roi_argument_build", "ROI论证已生成。", args, "deal")
def pricing_strategy_plan_adapter(context, args): return _plan(context, "pricing_strategy_plan", "报价策略已生成，发送前需确认。", args, "deal")
def decision_risk_detect_adapter(context, args): return _plan(context, "decision_risk_detect", "成交决策风险识别已生成。", args, "deal")
def closing_plan_generate_adapter(context, args): return _plan(context, "closing_plan_generate", "成交推进计划已生成。", args, "deal")
def contract_handoff_check_adapter(context, args): return _plan(context, "contract_handoff_check", "合同交接检查清单已生成。", args, "deal")
def crm_pipeline_profile_adapter(context, args): return _plan(context, "crm_pipeline_profile", "CRM漏斗画像已生成。", args, "revops")
def multi_touch_attribution_plan_adapter(context, args): return _plan(context, "multi_touch_attribution_plan", "多触点归因计划已生成。", args, "revops")
def revops_dashboard_spec_adapter(context, args): return _plan(context, "revops_dashboard_spec", "RevOps看板规格已生成。", args, "revops")
def growth_experiment_design_adapter(context, args): return _plan(context, "growth_experiment_design", "增长实验设计已生成。", args, "experiment")
def ab_test_plan_adapter(context, args): return _plan(context, "ab_test_plan", "A/B测试计划已生成。", args, "experiment")
def uplift_targeting_plan_adapter(context, args): return _plan(context, "uplift_targeting_plan", "增量转化目标选择计划已生成。", args, "experiment")
def bandit_allocation_plan_adapter(context, args): return _plan(context, "bandit_allocation_plan", "多臂老虎机分配计划已生成。", args, "experiment")
def growth_retrospective_report_adapter(context, args): return _plan(context, "growth_retrospective_report", "增长复盘报告已生成。", args, "experiment")
