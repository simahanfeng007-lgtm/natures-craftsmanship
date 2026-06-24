"""运行时工具注册表。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import re

from .tool_invocation import ToolInvocation
from .tool_result import ToolResult
from .turn_context import TurnContext

ToolAdapter = Callable[[ToolInvocation, TurnContext], ToolResult]


_TOOL_NAME_ALIASES = {
    "documentparse": "document_parse",
    "document_parse": "document_parse",
    "parse_document": "document_parse",
    "doc_parse": "document_parse",
    "documentquery": "document_query",
    "document_query": "document_query",
    "doc_query": "document_query",
    "query_document": "document_query",
    "文档追问": "document_query",
    "文档查询": "document_query",
    "documentexport": "document_export",
    "document_export": "document_export",
    "doc_export": "document_export",
    "export_document": "document_export",
    "文档导出": "document_export",
    "documentrewriteplan": "document_rewrite_plan",
    "document_rewrite_plan": "document_rewrite_plan",
    "document_rewrite": "document_rewrite_plan",
    "document_edit_plan": "document_rewrite_plan",
    "文档修改计划": "document_rewrite_plan",
    "documentapplyrewrite": "document_apply_rewrite",
    "document_apply_rewrite": "document_apply_rewrite",
    "document_writeback": "document_apply_rewrite",
    "document_write_back": "document_apply_rewrite",
    "document_edit_apply": "document_apply_rewrite",
    "apply_document_rewrite": "document_apply_rewrite",
    "apply_rewrite": "document_apply_rewrite",
    "文档写回": "document_apply_rewrite",
    "应用文档修改": "document_apply_rewrite",
    "documentrollback": "document_rollback",
    "document_rollback": "document_rollback",
    "rollback_document": "document_rollback",
    "document_writeback_rollback": "document_rollback",
    "文档回滚": "document_rollback",
    "dns": "dns_resolve",
    "dnsresolve": "dns_resolve",
    "dns_resolve": "dns_resolve",
    "dns解析": "dns_resolve",
    "域名解析": "dns_resolve",
    "networkrequest": "network_request",
    "network_request": "network_request",
    "网络请求": "network_request",
    "httpclient": "http_client",
    "http_client": "http_client",
    "http客户端": "http_client",
    "protocoladapter": "protocol_adapter",
    "protocol_adapter": "protocol_adapter",
    "协议适配": "protocol_adapter",
    "list": "list_dir",
    "ls": "list_dir",
    "listdir": "list_dir",
    "list_dir": "list_dir",
    "dir": "list_dir",
    "read": "read_file",
    "readfile": "read_file",
    "read_file": "read_file",
    "write": "write_workspace_file",
    "writefile": "write_workspace_file",
    "write_file": "write_workspace_file",
    "writeworkspacefile": "write_workspace_file",
    "write_workspace_file": "write_workspace_file",
    "runpythontests": "run_python_tests",
    "run_python_tests": "run_python_tests",
    "pytest": "run_python_tests",
    "python_tests": "run_python_tests",
    "runpythonqualitycheck": "run_python_quality_check",
    "run_python_quality_check": "run_python_quality_check",
    "python_quality_runner": "run_python_quality_check",
    "compileall": "run_python_quality_check",
    "websearch": "web_search",
    "web_search": "web_search",
    "联网搜索": "web_search",
    "网页检索": "web_search",
    "webreadabilityextract": "web_readability_extract",
    "web_readability_extract": "web_readability_extract",
    "readability": "web_readability_extract",
    "webdownload": "web_download",
    "web_download": "web_download",
    "download_url": "web_download",
    "saveurl": "web_download",
    "savefile": "web_download",
    "mkdir": "make_dir",
    "make_dir": "make_dir",
    "makedir": "make_dir",
    "create_dir": "make_dir",
    "create_folder": "make_dir",
    "move": "move_path",
    "mv": "move_path",
    "move_path": "move_path",
    "move_file": "move_path",
    "move_folder": "move_path",
    "rename": "move_path",
    "rename_path": "move_path",
    "rename_file": "move_path",
    "copy": "copy_path",
    "cp": "copy_path",
    "copy_path": "copy_path",
    "copy_file": "copy_path",
    "copy_folder": "copy_path",
    "delete": "delete_path",
    "remove": "delete_path",
    "rm": "delete_path",
    "delete_path": "delete_path",
    "delete_file": "delete_path",
    "delete_folder": "delete_path",
}



def canonical_tool_name(tool_name: str) -> str:
    text = str(tool_name or "").strip()
    if not text:
        return ""
    split_camel = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    key = re.sub(r"[\s\-]+", "_", split_camel).strip().lower()
    compact = re.sub(r"[^0-9a-zA-Z_一-鿿]", "", key)
    return (
        _TOOL_NAME_ALIASES.get(key)
        or _TOOL_NAME_ALIASES.get(compact)
        or key
    )


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    description: str
    default_risk: str
    parameters_schema: dict[str, Any] | None = None  # OpenAI function calling schema

    def get_parameters_schema(self) -> dict[str, Any]:
        """返回参数schema，无则返回通用回退。"""
        if self.parameters_schema:
            return self.parameters_schema
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "查询参数"},
            },
            "required": [],
        }


class RuntimeToolRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, ToolAdapter] = {}
        self._descriptors: dict[str, ToolDescriptor] = {}

    def register(self, descriptor: ToolDescriptor, adapter: ToolAdapter) -> None:
        canonical = canonical_tool_name(descriptor.name)
        self._descriptors[canonical] = ToolDescriptor(
            canonical, descriptor.description, descriptor.default_risk,
            parameters_schema=descriptor.parameters_schema,
        )
        self._adapters[canonical] = adapter

    def get(self, tool_name: str) -> ToolAdapter | None:
        return self._adapters.get(canonical_tool_name(tool_name))

    def describe(self) -> list[ToolDescriptor]:
        return [self._descriptors[name] for name in sorted(self._descriptors)]

    def names(self) -> list[str]:
        return sorted(self._adapters)
