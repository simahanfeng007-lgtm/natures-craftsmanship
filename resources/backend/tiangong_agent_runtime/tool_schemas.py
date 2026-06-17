"""工具参数 schema —— 注册层单一真相源。

RuntimeToolRegistry.register() 从此处取 schema，
CLI 层也从 registry descriptor 读取，不再维护独立 tool_cards。
新增工具时在此处加一条即可。
"""

from __future__ import annotations
from typing import Any

# ── 空参标准模板 ──────────────────────────────
EMPTY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}

# ── 资产操作专用模板 ──────────────────────────
XUEXI_ZICHAN_GOUJIAN_CANSHU: dict[str, Any] = {
    "type": "object",
    "properties": {
        "asset_id": {"type": "string", "description": "学习资产 ID"},
        "source_path": {"type": "string", "description": "资产来源路径"},
        "dry_run": {"type": "boolean", "description": "试运行模式，true=不实际执行", "default": False},
    },
    "required": [],
    "additionalProperties": False,
}

XUEXI_ZICHAN_YINGYONG_CANSHU: dict[str, Any] = {
    "type": "object",
    "properties": {
        "asset_id": {"type": "string", "description": "要应用的学习资产 ID"},
        "dry_run": {"type": "boolean", "description": "试运行模式", "default": False},
        "confirm": {"type": "boolean", "description": "确认执行，true=实际执行", "default": False},
    },
    "required": [],
    "additionalProperties": False,
}

# ── 工具专属参数 schema ──────────────────────────
GONGJU_CANSHU_SCHEMA: dict[str, dict[str, Any]] = {
    "conversation_history_search": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
        },
        "required": ["query"],
    },
    "create_release_bundle": {
        "type": "object",
        "required": [],
    },
    "create_zip_package": {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "打包源路径，默认 ."},
            "target": {"type": "string", "description": "输出 ZIP 路径，默认 dist/tiangong_delivery.zip"},
        },
        "required": [],
    },
    "diagnose_project": {
        "type": "object",
        "required": [],
    },
    "dns_resolve": {
        "type": "object",
        "properties": {
            "host": {"type": "string", "description": "域名或主机名"},
            "domain": {"type": "string", "description": "域名（host别名）"},
            "url": {"type": "string", "description": "URL（自动提取域名）"},
            "timeout": {"type": "number", "description": "超时秒数，默认10，最大30"},
        },
        "required": [],
    },
    "document_apply_rewrite": {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string", "description": "修改计划 ID"},
        },
        "required": [],
    },
    "document_export": {
        "type": "object",
        "properties": {
            "format": {"type": "string", "description": "导出格式（json/md/txt），默认 json"},
        },
        "required": [],
    },
    "document_parse": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要解析的文档路径（支持 txt/md/csv/json/docx/xlsx/pptx/pdf）"},
        },
        "required": ["path"],
    },
    "document_query": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "对已解析文档的查询问题"},
        },
        "required": ["query"],
    },
    "document_rewrite_plan": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "文档修改需求描述"},
        },
        "required": ["query"],
    },
    "document_rollback": {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string", "description": "要回滚的修改计划 ID"},
        },
        "required": [],
    },
    "document_text_extract": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要提取文本的文档路径"},
        },
        "required": ["path"],
    },
    "evaluate_quality_gate": {
        "type": "object",
        "required": [],
    },
    "experience_mentor_search": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "经验搜索关键词"},
        },
        "required": ["query"],
    },
    "file_sha256": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要计算 SHA256 的文件路径"},
        },
        "required": ["path"],
    },
    "http_client": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "请求URL"},
            "method": {"type": "string", "description": "HTTP方法(GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS)，默认GET"},
            "headers": {"type": "object", "description": "请求头"},
            "params": {"type": "object", "description": "查询参数"},
            "timeout": {"type": "number", "description": "超时秒数，默认15，最大60"},
            "max_bytes": {"type": "integer", "description": "最大响应字节，默认65536"},
            "allow_loopback_http": {"type": "boolean", "description": "允许回环地址，默认false"},
            "allow_private_network": {"type": "boolean", "description": "允许内网地址，默认false"},
        },
        "required": ["url"],
    },
    "learning_master_plan": {
        "type": "object",
        "properties": {
            "goal": {"type": "string", "description": "学习目标"},
            "sources": {"type": "array", "description": "参考来源列表"},
            "target_depth": {"type": "string", "description": "学习深度，默认 auto"},
        },
        "required": [],
    },
    "list_dir": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要列出的目录路径，默认工作区根目录"},
        },
        "required": [],
    },
    "make_dir": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path inside the active workspace."},
        },
        "required": ["path"],
    },
    "move_path": {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Existing source file or directory inside the active workspace."},
            "target": {"type": "string", "description": "Destination path inside the active workspace."},
            "overwrite": {"type": "boolean", "description": "Overwrite destination if it exists. Default false."},
        },
        "required": ["source", "target"],
    },
    "copy_path": {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Existing source file or directory inside the active workspace."},
            "target": {"type": "string", "description": "Destination path inside the active workspace."},
            "overwrite": {"type": "boolean", "description": "Overwrite destination if it exists. Default false."},
        },
        "required": ["source", "target"],
    },
    "delete_path": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File or directory path inside the active workspace."},
            "recursive": {"type": "boolean", "description": "Required for non-empty directories. Default false."},
        },
        "required": ["path"],
    },
    "model_chat": {
        "type": "object",
        "properties": {
            "messages": {"type": "array", "description": "消息列表"},
        },
        "required": [],
    },
    "network_request": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "请求URL"},
            "method": {"type": "string", "description": "HTTP方法，默认GET"},
            "headers": {"type": "object", "description": "请求头"},
            "params": {"type": "object", "description": "查询参数"},
            "timeout": {"type": "number", "description": "超时秒数，默认15，最大60"},
            "max_bytes": {"type": "integer", "description": "最大响应字节，默认65536"},
        },
        "required": ["url"],
    },
    "protocol_adapter": {
        "type": "object",
        "properties": {
            "curl": {"type": "string", "description": "curl命令行"},
            "command": {"type": "string", "description": "命令文本（curl别名）"},
            "text": {"type": "string", "description": "URL或API请求文本"},
        },
        "required": [],
    },
    "queue_skill_candidates": {
        "type": "object",
        "required": [],
    },
    "queue_tool_production_requests": {
        "type": "object",
        "required": [],
    },
    "read_file": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要读取的文件路径（相对于工作区）"},
        },
        "required": ["path"],
    },
    "return_analysis": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "分析结果文本"},
            "task": {"type": "string", "description": "任务简述"},
        },
        "required": ["content"],
    },
    "return_code": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "要返回的代码内容"},
            "language": {"type": "string", "description": "编程语言，默认 text"},
            "task": {"type": "string", "description": "任务简述"},
        },
        "required": ["content"],
    },
    "run_python_quality_check": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "命令类型（compileall 或 pytest），默认 compileall"},
            "command_type": {"type": "string", "description": "命令类型别名"},
            "target": {"type": "string", "description": "目标路径，默认当前工作区"},
            "timeout": {"type": "number", "description": "超时秒数"},
        },
        "required": [],
    },
    "run_python_tests": {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "测试目标路径，默认当前工作区"},
            "timeout": {"type": "integer", "description": "超时时间（秒），范围 5-300"},
        },
        "required": [],
    },
    "runtime_llm_operational_drill": {
        "type": "object",
        "required": [],
    },
    "runtime_tool_alignment_check": {
        "type": "object",
        "required": [],
    },
    "scan_project": {
        "type": "object",
        "required": [],
    },
    "synthesize_experience_candidates": {
        "type": "object",
        "required": [],
    },
    "task_pattern_search": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "任务模式关键词"},
        },
        "required": ["query"],
    },
    "tool_skill_blueprint": {
        "type": "object",
        "properties": {
            "goal": {"type": "string", "description": "Tool/Skill 生产目标"},
            "asset_type": {"type": "string", "description": "资产类型，默认 tool+skill"},
            "name_hint": {"type": "string", "description": "命名提示"},
        },
        "required": [],
    },
    "v1_clean_import_audit": {
        "type": "object",
        "required": [],
    },
    "v1_clean_import_guide": {
        "type": "object",
        "required": [],
    },
    "v1_clean_import_status": {
        "type": "object",
        "required": [],
    },
    "web_readability_extract": {
        "type": "object",
        "properties": {
            "html_or_text": {"type": "string", "description": "HTML 或网页正文内容"},
            "url": {"type": "string", "description": "来源 URL"},
            "max_chars": {"type": "integer", "description": "最大字符数，默认 12000"},
        },
        "required": [],
    },
    "web_search": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "用户要搜索或核验的问题。新闻/最新事实要保留时间词和主题词"},
            "max_results": {"type": "integer", "description": "希望返回的来源数量，默认 6，建议 3-8"},
            "mode": {"type": "string", "description": "搜索模式，可选 news/current/general/official_doc"},
        },
        "required": ["query"],
    },
    "workspace_text_search": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词或正则表达式"},
        },
        "required": ["query"],
    },
    "write_workspace_file": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "写入的文件路径（相对于工作区，如 output/文章.md）"},
            "content": {"type": "string", "description": "文件完整内容"},
            "encoding": {"type": "string", "description": "编码（默认 utf-8）"},
        },
        "required": ["path", "content"],
    },

    # ── build_* 报告工具（全部无参）──────────
    "build_delivery_standardization": EMPTY_SCHEMA,
    "build_execution_exoskeleton": EMPTY_SCHEMA,
    "build_governance_execution": EMPTY_SCHEMA,
    "build_l6_38_budget_snapshot": EMPTY_SCHEMA,
    "build_l6_38_handoff_integration": EMPTY_SCHEMA,
    "build_l6_38_p0_integration": EMPTY_SCHEMA,
    "build_l6_38_provider_integration": EMPTY_SCHEMA,
    "build_l6_38_skill_integration": EMPTY_SCHEMA,
    "build_l6_39_audit_integration": EMPTY_SCHEMA,
    "build_l6_39_memory_integration": EMPTY_SCHEMA,
    "build_l6_39_p0_integration": EMPTY_SCHEMA,
    "build_l6_39_quality_gate_integration": EMPTY_SCHEMA,
    "build_l6_39_recovery_integration": EMPTY_SCHEMA,
    "build_learning_convergence": EMPTY_SCHEMA,
    "build_planner_context": EMPTY_SCHEMA,
    "build_project_repair_plan": EMPTY_SCHEMA,
    "build_provider_adaptation": EMPTY_SCHEMA,
    "build_recovery_coordination": EMPTY_SCHEMA,
    "build_shell_system_mount": EMPTY_SCHEMA,

    # ── learning_asset_* 状态/报告/检查类（无参）──
    "learning_asset_activation_guide": EMPTY_SCHEMA,
    "learning_asset_activation_status": EMPTY_SCHEMA,
    "learning_asset_activation_smoke": EMPTY_SCHEMA,
    "learning_asset_adapter_guide": EMPTY_SCHEMA,
    "learning_asset_adapter_template_list": EMPTY_SCHEMA,
    "learning_asset_adapter_template_normalize": EMPTY_SCHEMA,
    "learning_asset_adapter_template_validate": EMPTY_SCHEMA,
    "learning_asset_adapter_template_smoke": EMPTY_SCHEMA,
    "learning_asset_adapter_drill": EMPTY_SCHEMA,
    "learning_asset_candidate_sandbox_guide": EMPTY_SCHEMA,
    "learning_asset_candidate_sandbox_validate": EMPTY_SCHEMA,
    "learning_asset_candidate_sandbox_review": EMPTY_SCHEMA,
    "learning_asset_contract_guide": EMPTY_SCHEMA,
    "learning_asset_contract_normalize": EMPTY_SCHEMA,
    "learning_asset_contract_validate": EMPTY_SCHEMA,
    "learning_asset_release_gate_guide": EMPTY_SCHEMA,
    "learning_asset_release_gate_check": EMPTY_SCHEMA,
    "learning_asset_sandbox_guide": EMPTY_SCHEMA,
    "learning_asset_sandbox_align": EMPTY_SCHEMA,
    "learning_asset_sandbox_validate": EMPTY_SCHEMA,

    # ── learning_asset_* build/apply 类（有参）──
    "learning_asset_candidate_sandbox_build": XUEXI_ZICHAN_GOUJIAN_CANSHU,
    "learning_asset_activation_apply": XUEXI_ZICHAN_YINGYONG_CANSHU,

}

# ── 通用回退 ──────────────────────────────
GONGJU_TONGYONG_CANSHU: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "查询参数"},
    },
    "required": [],
}
# TG_MULTIMEDIA_SCHEMA_BEGIN
try:
    from .tool_schemas_multimedia_additions import MULTIMEDIA_TOOL_SCHEMAS
except Exception:
    MULTIMEDIA_TOOL_SCHEMAS = {}
for _name, _schema in MULTIMEDIA_TOOL_SCHEMAS.items():
    GONGJU_CANSHU_SCHEMA[_name] = _schema
# TG_MULTIMEDIA_SCHEMA_END

# ENTERPRISE_SCHEMA_BEGIN
try:
    from .enterprise_tool_schemas import ENTERPRISE_TOOL_SCHEMAS
except Exception:
    ENTERPRISE_TOOL_SCHEMAS = {}
for _name, _schema in ENTERPRISE_TOOL_SCHEMAS.items():
    GONGJU_CANSHU_SCHEMA[_name] = _schema
# ENTERPRISE_SCHEMA_END

# BEGIN_OPS_EXTENSION_SCHEMAS
from tiangong_agent_runtime.ops_tool_schemas import OPS_TOOL_SCHEMAS
GONGJU_CANSHU_SCHEMA.update(OPS_TOOL_SCHEMAS)
# END_OPS_EXTENSION_SCHEMAS

# BEGIN_WANGWEN_UNIFIED_PIPELINE_SCHEMAS
# WANGWEN_UNIFIED_PIPELINE_TOOLS: wangwen_novel_factory_run, wangwen_novel_bible_build, wangwen_chapter_brief_build, wangwen_draft_quality_check, wangwen_revision_plan_build
from tiangong_agent_runtime.wangwen_unified_pipeline_tool_schemas import WANGWEN_UNIFIED_PIPELINE_TOOL_SCHEMAS
GONGJU_CANSHU_SCHEMA.update(WANGWEN_UNIFIED_PIPELINE_TOOL_SCHEMAS)
# END_WANGWEN_UNIFIED_PIPELINE_SCHEMAS
