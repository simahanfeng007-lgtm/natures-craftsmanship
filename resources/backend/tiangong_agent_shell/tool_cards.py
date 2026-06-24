"""工具卡片 —— 从 runtime 层 tool_schemas 重导出。

新工具请在 tiangong_agent_runtime.tool_schemas 中注册 schema，
注册时通过 ToolDescriptor(parameters_schema=...) 绑定。
此处保留向后兼容的 huoqu_gongju_canshu()。
"""

from __future__ import annotations

from typing import Any

from tiangong_agent_runtime.tool_schemas import GONGJU_CANSHU_SCHEMA, GONGJU_TONGYONG_CANSHU


def huoqu_gongju_canshu(gongju_ming: str) -> dict[str, Any]:
    """获取工具参数 schema（向后兼容）。新代码请从 ToolDescriptor.parameters_schema 读取。"""
    return GONGJU_CANSHU_SCHEMA.get(gongju_ming, GONGJU_TONGYONG_CANSHU)
