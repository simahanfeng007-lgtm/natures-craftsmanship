"""L6.72.58 OpenAI-compatible Provider adapter。

适用于 DeepSeek / Qwen / GLM / Minimax / Mimo 以及其他兼容
/v1/chat/completions 的服务。所有错误转为 ProviderError 分类后再抛出
ModelClientError，避免 Runtime 只能看到模糊 provider_error。
"""

from __future__ import annotations

import html
import json
import re
import socket
import urllib.error
import urllib.request
from typing import Any

from tiangong_agent_shell.config_loader import ModelConfig
from tiangong_agent_shell.errors import ModelClientError
from tiangong_agent_shell.model_client_port import ChatResult, ensure_compiled_prompt_envelope
from tiangong_agent_shell.network_policy import NetworkPolicyError, urlopen_with_policy
from tiangong_agent_shell.safe_logging import redact_text

from .provider_error import classify_provider_error, to_model_client_error_kwargs


class OpenAICompatibleAdapter:
    provider = "openai_compatible"

    def chat(self, prompt: Any, config: ModelConfig, *, tools: list[dict[str, Any]] | None = None) -> ChatResult:
        _validate_config(config, provider=self.provider)
        try:
            envelope = ensure_compiled_prompt_envelope(prompt)
        except TypeError as exc:
            raise ModelClientError(str(exc), detail="ProviderClient boundary: compiled_prompt_envelope_required", error_kind="unsupported_feature", provider=self.provider) from exc
        url = _chat_completions_url(config.base_url)
        payload: dict[str, Any] = {"model": config.model, "messages": envelope.as_messages(), "stream": False}
        max_tokens = _max_tokens_for_config(config)
        if max_tokens > 0:
            payload["max_tokens"] = max_tokens
        _apply_thinking_payload(payload, config)
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url=url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Tiangong-Prompt-Id": envelope.compiled_prompt_id,
                "X-Tiangong-Prompt-Integrator": envelope.prompt_integrator_version,
            },
        )
        try:
            with urlopen_with_policy(request, timeout=config.timeout, allow_loopback_http=True, purpose="model_provider") as response:
                raw_bytes = response.read()
        except urllib.error.HTTPError as exc:
            detail = _read_http_error_detail(exc, config.api_key)
            error = classify_provider_error(exc, provider=_public_provider(config), status_code=exc.code, detail=detail, api_key=config.api_key)
            raise ModelClientError(error.user_message, **to_model_client_error_kwargs(error)) from exc
        except NetworkPolicyError as exc:
            error = classify_provider_error(exc, provider=_public_provider(config), detail=str(exc), api_key=config.api_key)
            raise ModelClientError("网络策略拒绝：远程模型接口必须使用 HTTPS；本机回环地址可使用 HTTP。", **to_model_client_error_kwargs(error)) from exc
        except UnicodeEncodeError as exc:
            error = classify_provider_error(exc, provider=_public_provider(config), detail=str(exc), api_key=config.api_key)
            raise ModelClientError(error.user_message, **to_model_client_error_kwargs(error)) from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            error = classify_provider_error(exc, provider=_public_provider(config), detail=str(exc), api_key=config.api_key)
            raise ModelClientError(error.user_message, **to_model_client_error_kwargs(error)) from exc

        try:
            data = json.loads(raw_bytes.decode("utf-8"))
            choice = data["choices"][0]
            message = choice.get("message", {})
            content = message.get("content", "") or ""
            tool_calls = _parse_tool_calls(message)
            if not tool_calls:
                tool_calls = _parse_text_tool_calls(content)
            # 保留 reasoning_content (DeepSeek 思考模式必需)
            extra_fields = {}
            reasoning_content = _extract_reasoning_text(message)
            if reasoning_content:
                extra_fields["reasoning_content"] = reasoning_content
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            error = classify_provider_error(exc, provider=_public_provider(config), detail=str(exc), api_key=config.api_key)
            raise ModelClientError(error.user_message, **to_model_client_error_kwargs(error)) from exc
        raw = dict(data)
        raw.setdefault("tiangong_prompt", envelope.public_dict())
        return ChatResult(content=str(content), provider=_public_provider(config), model=config.model, raw=raw, tool_calls=tool_calls, reasoning_content=extra_fields.get("reasoning_content", ""))


def _validate_config(config: ModelConfig, *, provider: str) -> None:
    if not config.base_url:
        raise ModelClientError("缺少 Base URL：OpenAI-compatible Provider 必须设置服务商 Base URL。", error_kind="model_not_found", provider=provider)
    if not config.has_real_api_key:
        raise ModelClientError("缺少 API Key：请设置 TIANGONG_API_KEY 或 --api-key；桌面端请进入【设置】页保存模型接口配置。", error_kind="auth_error", provider=provider)
    if not config.model:
        raise ModelClientError("缺少模型名：请设置 TIANGONG_MODEL 或 --model。", error_kind="model_not_found", provider=provider)


def _chat_completions_url(base_url: str) -> str:
    base = str(base_url or "").rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/chat/completions"


def _read_http_error_detail(exc: urllib.error.HTTPError, api_key: str) -> str:
    try:
        raw = exc.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - best effort detail only
        raw = str(exc)
    return redact_text(raw, [api_key])


def _public_provider(config: ModelConfig) -> str:
    provider = str(getattr(config, "provider", "") or "").strip().lower()
    return provider or "openai_compatible"


def _max_tokens_for_config(config: ModelConfig) -> int:
    explicit = int(getattr(config, "max_tokens", 0) or 0)
    if explicit > 0:
        return explicit
    model = str(getattr(config, "model", "") or "").strip().lower()
    base_url = str(getattr(config, "base_url", "") or "").strip().lower()
    if "ark-code" in model or ("ark.cn-beijing.volces.com" in base_url and "code" in model):
        return 32768
    return 0


def _apply_thinking_payload(payload: dict[str, Any], config: ModelConfig) -> None:
    provider = _public_provider(config)
    if provider in {"openai", "openai_compatible"}:
        return
    enabled = bool(getattr(config, "thinking_enabled", False))
    depth = str(getattr(config, "thinking_depth", "") or "").strip().lower()
    if provider in {"deepseek", "deepseek_v4"}:
        payload["thinking"] = {"type": "enabled" if enabled else "disabled"}
        if enabled:
            payload["reasoning_effort"] = _deepseek_effort(depth)
        return
    if provider in {"qwen", "dashscope"}:
        payload["enable_thinking"] = enabled
        budget = _qwen_thinking_budget(depth)
        if enabled and budget:
            payload["thinking_budget"] = budget
        return
    if provider in {"zhipu", "glm"}:
        payload["thinking"] = {"type": "enabled" if enabled else "disabled"}
        return
    if provider == "mimo":
        payload["thinking"] = {"type": "enabled" if enabled else "disabled"}
        return
    if provider == "minimax":
        if enabled:
            payload["reasoning_split"] = True
        return
    if provider == "openrouter":
        payload["reasoning"] = {"effort": _openrouter_effort(depth) if enabled else "none"}


def _deepseek_effort(depth: str) -> str:
    if depth == "max":
        return "max"
    return "high"


def _openrouter_effort(depth: str) -> str:
    return depth if depth in {"minimal", "low", "medium", "high", "xhigh"} else "high"


def _qwen_thinking_budget(depth: str) -> int:
    if depth in {"max", "xhigh"}:
        return 16384
    if depth in {"deep", "high"}:
        return 8192
    if depth in {"standard", "medium"}:
        return 4096
    if depth in {"light", "low"}:
        return 2048
    return 0


def _extract_reasoning_text(message: dict[str, Any]) -> str:
    for key in ("reasoning_content", "reasoning"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value
    details = message.get("reasoning_details")
    if isinstance(details, str) and details.strip():
        return details
    if isinstance(details, list) and details:
        parts: list[str] = []
        for item in details:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or item.get("reasoning")
                if text:
                    parts.append(str(text))
        if parts:
            return "\n".join(parts)
    return ""


def _parse_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]] | None:
    raw = message.get("tool_calls")
    if not raw or not isinstance(raw, list):
        return None
    result: list[dict[str, Any]] = []
    for tc in raw:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function", {})
        result.append({
            "id": tc.get("id", ""),
            "type": tc.get("type", "function"),
            "function": {
                "name": fn.get("name", ""),
                "arguments": fn.get("arguments", "{}"),
            },
        })
    return result or None


_TEXT_TOOL_CALL_RE = re.compile(r"<tool_call\b[^>]*>(.*?)</tool_call>", re.IGNORECASE | re.DOTALL)
_TEXT_FUNCTION_RE = re.compile(
    r"<function\s*=\s*([A-Za-z_][\w.-]*)\s*>(.*?)</function>",
    re.IGNORECASE | re.DOTALL,
)
_TEXT_PARAMETER_EQ_RE = re.compile(
    r"<parameter\s*=\s*([A-Za-z_][\w.-]*)\s*>(.*?)</parameter>",
    re.IGNORECASE | re.DOTALL,
)
_TEXT_PARAMETER_NAME_RE = re.compile(
    r"<parameter\b[^>]*\bname=[\"']([^\"']+)[\"'][^>]*>(.*?)</parameter>",
    re.IGNORECASE | re.DOTALL,
)


def _parse_text_tool_calls(content: str) -> list[dict[str, Any]] | None:
    """兼容 MiMo/DeepSeek 把 function call 写进正文的 XML/JSON 外形。"""
    text = str(content or "")
    blocks = [m.group(1) for m in _TEXT_TOOL_CALL_RE.finditer(text)] or [text]

    # 去掉无 tool_call 标签的纯文本（避免把普通 JSON 误解析）
    has_tool_call_tag = bool(_TEXT_TOOL_CALL_RE.search(text))
    if not has_tool_call_tag:
        return None

    calls: list[dict[str, Any]] = []
    for block in blocks:
        # ① JSON 格式：{"tool":"name","args":{...}} 或 {"name":"name","arguments":{...}}
        json_calls = _parse_json_tool_calls(block)
        if json_calls:
            calls.extend(json_calls)
            continue

        # ② XML 格式：<function=name><parameter=key>value</parameter></function>
        for match in _TEXT_FUNCTION_RE.finditer(block):
            name = _safe_tool_name(match.group(1))
            if not name:
                continue
            body = match.group(2) or ""
            args = _parse_text_tool_arguments(body)
            calls.append({
                "id": f"text_call_{len(calls) + 1}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args, ensure_ascii=False),
                },
            })
    return calls or None


def _parse_json_tool_calls(block: str) -> list[dict[str, Any]] | None:
    """解析 JSON 格式的工具调用：{"tool":"name","args":{...}} 或 {"name":"name","arguments":{...}}。"""
    text = block.strip()
    # 找第一个 JSON 对象
    start = text.find("{")
    end = text.rfind("}") + 1
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end])
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    # 格式1: {"tool":"xxx","args":{...}}
    if "tool" in obj and isinstance(obj.get("tool"), str):
        name = _safe_tool_name(obj["tool"])
        if not name:
            return None
        args = obj.get("args")
        if not isinstance(args, dict):
            args = {}
        return [{
            "id": f"json_call_{_json_call_counter()}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(args, ensure_ascii=False),
            },
        }]
    # 格式2: {"name":"xxx","arguments":{...}} (OpenAI 标准)
    if "name" in obj and "arguments" in obj:
        name = _safe_tool_name(obj["name"])
        if not name:
            return None
        raw_args = obj["arguments"]
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args)
            except (json.JSONDecodeError, TypeError):
                args = {"query": raw_args}
        elif isinstance(raw_args, dict):
            args = raw_args
        else:
            args = {}
        return [{
            "id": f"json_call_{_json_call_counter()}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(args, ensure_ascii=False),
            },
        }]
    return None


_json_call_seq = 0


def _json_call_counter() -> int:
    global _json_call_seq
    _json_call_seq += 1
    return _json_call_seq


def _parse_text_tool_arguments(body: str) -> dict[str, Any]:
    args: dict[str, Any] = {}
    for regex in (_TEXT_PARAMETER_EQ_RE, _TEXT_PARAMETER_NAME_RE):
        for match in regex.finditer(body or ""):
            key = _safe_argument_name(match.group(1))
            if key:
                args[key] = html.unescape(_strip_xml_text(match.group(2)))
    if args:
        return args
    fallback = _strip_xml_text(body)
    return {"query": html.unescape(fallback)} if fallback else {}


def _safe_tool_name(value: Any) -> str:
    text = str(value or "").strip()
    return text if re.fullmatch(r"[A-Za-z_][\w.-]{0,127}", text) else ""


def _safe_argument_name(value: Any) -> str:
    text = str(value or "").strip()
    return text if re.fullmatch(r"[A-Za-z_][\w.-]{0,127}", text) else ""


def _strip_xml_text(value: Any) -> str:
    text = re.sub(r"<[^>]+>", "", str(value or ""))
    return text.replace("\x00", "").strip()
