"""Code-X 修复子系统 — 天工造物 Runtime 子系统

直接创建 LLMDrivenCodeX 实例执行修复。自愈调用方在 cli_main._zhiyu_xiufu 中注入审计。
抄 Codex CLI 架构：Agent Loop + Sandbox + 直接工具。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..llm_codex import LLMDrivenCodeX, LLMCodeXResult
from ..llm_codex import SANDBOX_WORKSPACE_WRITE


class CodeXSubsystem:
    """Code-X 修复子系统。初始化时注入 API 配置，run_repair() 执行修复。"""

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        model: str = "",
    ):
        self.api_key = api_key or os.environ.get("TIANGONG_API_KEY", "") or os.environ.get("DEEPSEEK_API_KEY", "")
        self.base_url = base_url or os.environ.get("TIANGONG_BASE_URL", "") or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
        self.model = model or os.environ.get("TIANGONG_MODEL", "") or os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")
        self.provider = os.environ.get("TIANGONG_PROVIDER", "deepseek")
        self.thinking_enabled = os.environ.get("TIANGONG_THINKING_ENABLED", "0")
        self.thinking_depth = os.environ.get("TIANGONG_THINKING_DEPTH", "")
        self._runner: LLMDrivenCodeX | None = None

    def _ensure_runner(self) -> LLMDrivenCodeX:
        if self._runner is None:
            self._runner = LLMDrivenCodeX(
                api_key=self.api_key,
                base_url=self.base_url,
                model=self.model,
                sandbox_mode=SANDBOX_WORKSPACE_WRITE,
                provider=self.provider,
                thinking_enabled=self.thinking_enabled,
                thinking_depth=self.thinking_depth,
            )
        return self._runner

    def run_repair(
        self,
        task: str,
        workspace: str | Path,
        *,
        max_turns: int = 12,
        buzhou_huidiao: Any = None,
    ) -> LLMCodeXResult:
        """执行 Code-X 代码系统任务。"""
        runner = self._ensure_runner()
        return runner.run(task=task, workspace=workspace, max_turns=max_turns,
                          buzhou_huidiao=buzhou_huidiao)

    def public_dict(self) -> dict[str, Any]:
        return {
            "api_key_configured": bool(self.api_key),
            "model": self.model,
            "base_url": self.base_url,
        }
