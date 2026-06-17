"""L6.9 外壳配置加载。

优先级：CLI 参数 > 环境变量 > 配置文件 > 默认值。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .errors import ConfigError
from .safe_logging import sanitize_mapping
from tiangong_agent_runtime.planner_mode import PlannerMode, normalize_planner_mode

from .tool_bridge import ToolExecutionMode, normalize_tool_mode


DEFAULT_MODEL_TIMEOUT_SECONDS = 240.0


@dataclass(frozen=True)
class ModelConfig:
    provider: str = "openai_compatible"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout: float = DEFAULT_MODEL_TIMEOUT_SECONDS
    max_tokens: int = 0
    stream: bool = False
    multimodal_input: bool | None = None
    image_input: bool | None = None
    video_input: bool | None = None
    audio_input: bool | None = None
    thinking_enabled: bool = False
    thinking_depth: str = ""
    tool_execution_mode: ToolExecutionMode = ToolExecutionMode.RUNTIME_GOVERNED
    planner_mode: PlannerMode = PlannerMode.RULE_ONLY

    def sanitized_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tool_execution_mode"] = self.tool_execution_mode.value
        data["planner_mode"] = self.planner_mode.value
        return sanitize_mapping(data)

    @property
    def has_real_api_key(self) -> bool:
        return bool(self.api_key) and self.api_key not in {
            "PLEASE_SET_YOUR_API_KEY",
            "YOUR_API_KEY",
            "example",
        }


ENV_MAP = {
    "provider": "TIANGONG_PROVIDER",
    "base_url": "TIANGONG_BASE_URL",
    "api_key": "TIANGONG_API_KEY",
    "model": "TIANGONG_MODEL",
    "timeout": "TIANGONG_TIMEOUT",
    "max_tokens": "TIANGONG_MAX_TOKENS",
    "multimodal_input": "TIANGONG_MULTIMODAL_INPUT",
    "image_input": "TIANGONG_IMAGE_INPUT",
    "video_input": "TIANGONG_VIDEO_INPUT",
    "audio_input": "TIANGONG_AUDIO_INPUT",
    "thinking_enabled": "TIANGONG_THINKING_ENABLED",
    "thinking_depth": "TIANGONG_THINKING_DEPTH",
    "tool_execution_mode": "TIANGONG_TOOL_MODE",
    "planner_mode": "TIANGONG_PLANNER_MODE",
}

# L6.50/L6.51：DeepSeek Provider 使用受控配置别名进入统一 ModelConfig。
# 这些环境变量只在配置层解析，不允许前端、测试脚本或临时工具裸调 Provider SDK。
ENV_ALIASES = {
    "provider": ("DEEPSEEK_PROVIDER",),
    "base_url": ("DEEPSEEK_BASE_URL",),
    "api_key": ("DEEPSEEK_API_KEY",),
    "model": ("DEEPSEEK_MODEL",),
    "timeout": ("DEEPSEEK_TIMEOUT",),
}


def load_model_config(args: Any | None = None) -> ModelConfig:
    """从 CLI/env/file/default 合成模型配置。"""
    args = args or object()
    data: dict[str, Any] = {
        "provider": "openai_compatible",
        "base_url": "",
        "api_key": "",
        "model": "",
        "timeout": DEFAULT_MODEL_TIMEOUT_SECONDS,
        "max_tokens": 0,
        "stream": False,
        "multimodal_input": None,
        "image_input": None,
        "video_input": None,
        "audio_input": None,
        "thinking_enabled": False,
        "thinking_depth": "",
        "tool_execution_mode": ToolExecutionMode.RUNTIME_GOVERNED.value,
        "planner_mode": PlannerMode.RULE_ONLY.value,
    }

    config_path = getattr(args, "config", None)
    if config_path:
        data.update(_normalize_config_keys(_read_config_file(Path(config_path))))

    for key, env_name in ENV_MAP.items():
        value = os.getenv(env_name)
        if value not in (None, ""):
            data[key] = value

    # Provider-specific aliases have lower priority than TIANGONG_* canonical envs
    # but higher priority than config-file defaults.
    for key, alias_names in ENV_ALIASES.items():
        if data.get(key) not in (None, "", "openai_compatible") and key == "provider":
            continue
        if data.get(key) not in (None, "") and key != "provider":
            continue
        for alias_name in alias_names:
            value = os.getenv(alias_name)
            if value not in (None, ""):
                data[key] = value
                break

    cli_map = {
        "provider": getattr(args, "provider", None),
        "base_url": getattr(args, "base_url", None),
        "api_key": getattr(args, "api_key", None),
        "model": getattr(args, "model", None),
        "timeout": getattr(args, "timeout", None),
        "max_tokens": getattr(args, "max_tokens", None),
        "thinking_enabled": getattr(args, "thinking_enabled", None),
        "thinking_depth": getattr(args, "thinking_depth", None),
        "tool_execution_mode": getattr(args, "tool_mode", None),
        "planner_mode": getattr(args, "planner_mode", None),
    }
    for key, value in cli_map.items():
        if value not in (None, ""):
            data[key] = value

    return _coerce_model_config(data)


def _read_config_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"配置文件不存在：{path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置文件不是合法 JSON：{path}", detail=str(exc)) from exc
    except OSError as exc:
        raise ConfigError(f"配置文件读取失败：{path}", detail=str(exc)) from exc


def _normalize_config_keys(raw: dict[str, Any]) -> dict[str, Any]:
    data = dict(raw or {})
    aliases = {
        "modelProvider": "provider",
        "modelBaseUrl": "base_url",
        "modelApiKey": "api_key",
        "modelName": "model",
        "modelTimeout": "timeout",
        "modelMaxTokens": "max_tokens",
        "modelThinkingEnabled": "thinking_enabled",
        "modelThinkingDepth": "thinking_depth",
        "modelMultimodalInput": "multimodal_input",
        "modelImageInput": "image_input",
        "modelVideoInput": "video_input",
        "modelAudioInput": "audio_input",
        "toolMode": "tool_execution_mode",
        "plannerMode": "planner_mode",
    }
    for old_key, new_key in aliases.items():
        if new_key not in data and old_key in data:
            data[new_key] = data[old_key]
    return data


def _coerce_model_config(data: dict[str, Any]) -> ModelConfig:
    provider = str(data.get("provider") or "openai_compatible").strip().lower()
    base_url = str(data.get("base_url") or "").strip()
    api_key = str(data.get("api_key") or "").strip()
    model = str(data.get("model") or "").strip()
    try:
        timeout = float(data.get("timeout", DEFAULT_MODEL_TIMEOUT_SECONDS))
    except (TypeError, ValueError) as exc:
        raise ConfigError("timeout 必须是数字。", detail=str(exc)) from exc
    max_tokens = _coerce_non_negative_int(data.get("max_tokens"), field_name="max_tokens", default=0)
    stream = bool(data.get("stream", False))
    multimodal_input = _coerce_optional_bool(data.get("multimodal_input"))
    image_input = _coerce_optional_bool(data.get("image_input"))
    video_input = _coerce_optional_bool(data.get("video_input"))
    audio_input = _coerce_optional_bool(data.get("audio_input"))
    thinking_enabled = _coerce_bool(data.get("thinking_enabled"), default=False)
    thinking_depth = str(data.get("thinking_depth") or "").strip().lower()
    tool_mode = normalize_tool_mode(data.get("tool_execution_mode"))
    planner_mode = normalize_planner_mode(data.get("planner_mode"))
    return ModelConfig(
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=timeout,
        max_tokens=max_tokens,
        stream=stream,
        multimodal_input=multimodal_input,
        image_input=image_input,
        video_input=video_input,
        audio_input=audio_input,
        thinking_enabled=thinking_enabled,
        thinking_depth=thinking_depth,
        tool_execution_mode=tool_mode,
        planner_mode=planner_mode,
    )


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled", "enable"}:
        return True
    if text in {"0", "false", "no", "off", "disabled", "disable"}:
        return False
    return default


def _coerce_optional_bool(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text in {"auto", "default", "unknown", "detect"}:
        return None
    return _coerce_bool(value, default=False)


def _coerce_non_negative_int(value: Any, *, field_name: str, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field_name} must be a non-negative integer.", detail=str(exc)) from exc
    if number < 0:
        raise ConfigError(f"{field_name} must be a non-negative integer.", detail=str(number))
    return number
