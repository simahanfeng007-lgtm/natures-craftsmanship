from __future__ import annotations
import argparse
from pathlib import Path
import re

EXPECTED_SKILLS = [
    "25_shuju_biaoge_fenxi",
    "26_liulanqi_zidonghua",
    "27_shujuku_sql",
    "28_api_jiekou_diaoyong",
    "29_renwu_pingce_benchmark",
    "30_qiye_zhishiku_goujian",
    "31_zhuomian_rpa",
    "32_xiaoshou_zuozhan",
    "33_keyan_qingbao_fenxi",
    "34_didaima_yingyong_shengcheng",
]
EXPECTED_TOOLS = [
    "table_profile",
    "table_schema_detect",
    "table_quality_check",
    "table_clean_plan",
    "table_deduplicate",
    "table_filter",
    "table_score",
    "table_join_plan",
    "table_pivot_summary",
    "table_export",
    "browser_open",
    "browser_extract",
    "browser_screenshot_plan",
    "browser_click_plan",
    "browser_type_plan",
    "browser_download_plan",
    "browser_form_fill_plan",
    "browser_session_close",
    "db_connect_check",
    "db_schema_inspect",
    "db_query_readonly",
    "db_query_explain",
    "db_table_profile",
    "db_export_csv",
    "db_import_csv_plan",
    "db_migration_plan",
    "api_request_spec",
    "api_schema_parse",
    "api_auth_check",
    "api_response_extract",
    "api_batch_request_plan",
    "api_webhook_test_plan",
    "api_error_diagnose",
    "eval_case_build",
    "eval_run_plan",
    "eval_compare",
    "eval_regression_check",
    "eval_tool_schema_check",
    "eval_skill_quality_check",
    "eval_report",
    "kb_ingest_plan",
    "kb_chunk_preview",
    "kb_index_plan",
    "kb_search_local",
    "kb_answer_draft",
    "kb_source_trace",
    "kb_update_plan",
    "kb_quality_check",
    "desktop_screenshot_plan",
    "desktop_click_plan",
    "desktop_type_plan",
    "desktop_hotkey_plan",
    "desktop_find_window_plan",
    "desktop_clipboard_plan",
    "desktop_open_app_plan",
    "desktop_file_dialog_plan",
    "lead_score",
    "company_profile_build",
    "contact_plan_generate",
    "sales_script_generate",
    "objection_handle",
    "followup_plan",
    "crm_note_generate",
    "deal_stage_judge",
    "paper_search_plan",
    "paper_read_plan",
    "paper_summarize",
    "paper_compare",
    "paper_method_extract",
    "paper_benchmark_extract",
    "tech_trend_report",
    "app_spec_build",
    "app_scaffold_plan",
    "frontend_page_spec",
    "backend_api_spec",
    "db_schema_generate",
    "app_preview_plan",
    "app_package_plan",
]

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
    schema_file = runtime / "tool_schemas.py"
    schema_txt = schema_file.read_text(encoding="utf-8") if schema_file.exists() else ""
    runtime_entry = runtime / "runtime_entry.py"
    rt_txt = runtime_entry.read_text(encoding="utf-8") if runtime_entry.exists() else ""
    for t in EXPECTED_TOOLS:
        if t not in schema_txt:
            errors.append(f"missing_schema:{t}")
        if t not in rt_txt:
            errors.append(f"missing_registration:{t}")
    adapter = runtime / "adapters" / "enterprise_extension_adapters.py"
    if not adapter.exists():
        errors.append("missing_adapter_file")
    print("enterprise_static_check")
    print(f"skills={len(EXPECTED_SKILLS)} tools={len(EXPECTED_TOOLS)} errors={len(errors)}")
    for e in errors[:200]:
        print("ERROR", e)
    raise SystemExit(1 if errors else 0)

if __name__ == "__main__":
    main()
