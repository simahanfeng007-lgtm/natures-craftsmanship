"""Governed network, DNS, HTTP client, and protocol-adapter tools."""

from __future__ import annotations

import base64
import ipaddress
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping

from tiangong_agent_shell.network_policy import NetworkPolicyError, validate_url, urlopen_with_policy

from ..result_normalizer import truncate_text
from ..tool_invocation import ToolInvocation
from ..tool_result import ToolResult, ToolResultStatus
from ..turn_context import TurnContext


_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
_SAFE_RESPONSE_HEADERS = {
    "content-type",
    "content-length",
    "date",
    "server",
    "last-modified",
    "etag",
    "cache-control",
    "location",
}


def dns_resolve_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    host = _extract_host(invocation.arguments.get("host") or invocation.arguments.get("domain") or invocation.arguments.get("url") or context.user_message)
    if not host:
        return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.FAILED, "缺少要解析的域名。", error_code="dns_host_missing")

    family_arg = str(invocation.arguments.get("family") or "any").strip().lower()
    family = socket.AF_UNSPEC
    if family_arg in {"ipv4", "a", "4"}:
        family = socket.AF_INET
    elif family_arg in {"ipv6", "aaaa", "6"}:
        family = socket.AF_INET6

    port = _clamp_int(invocation.arguments.get("port"), minimum=1, maximum=65535, default=443)
    started = time.perf_counter()
    dns_error = ""
    try:
        infos = socket.getaddrinfo(host, port, family, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        infos = []
        dns_error = str(exc)

    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in infos:
        address = str(item[4][0])
        version = "ipv6" if ":" in address else "ipv4"
        key = (version, address)
        if key in seen:
            continue
        seen.add(key)
        records.append({"family": version, "address": address, "private": _is_private_ip(address)})
    if not records:
        records, doh_error = _resolve_doh(host, family=family, timeout=_clamp_float(invocation.arguments.get("timeout_sec") or invocation.arguments.get("timeout"), minimum=1.0, maximum=30.0, default=10.0))
        if doh_error and not dns_error:
            dns_error = doh_error
    if not records:
        return ToolResult(
            invocation.step_id,
            invocation.tool_name,
            ToolResultStatus.FAILED,
            f"DNS 解析失败：{dns_error or 'no records'}",
            error_code="dns_resolve_failed",
            data={"host": host, "error": dns_error or "no records"},
        )

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    summary = f"DNS 解析完成：{host} -> " + ", ".join(record["address"] for record in records[:12])
    return ToolResult(
        invocation.step_id,
        invocation.tool_name,
        ToolResultStatus.OK,
        truncate_text(summary, context.policy.max_output_chars),
        data={"host": host, "port": port, "records": records, "elapsed_ms": elapsed_ms},
    )


def _resolve_doh(host: str, *, family: socket.AddressFamily, timeout: float) -> tuple[list[dict[str, Any]], str]:
    query_types: list[tuple[str, str]] = []
    if family in {socket.AF_UNSPEC, socket.AF_INET}:
        query_types.append(("A", "ipv4"))
    if family in {socket.AF_UNSPEC, socket.AF_INET6}:
        query_types.append(("AAAA", "ipv6"))
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for qtype, version in query_types:
        url = "https://dns.google/resolve?" + urllib.parse.urlencode({"name": host, "type": qtype})
        try:
            with urlopen_with_policy(url, timeout=timeout, purpose="dns_resolve_doh") as response:
                payload = json.loads(response.read(256_000).decode("utf-8", errors="replace"))
        except Exception as exc:
            errors.append(str(exc))
            continue
        status_raw = payload.get("Status", 1)
        try:
            status = int(status_raw)
        except (TypeError, ValueError):
            status = 1
        if status != 0:
            errors.append(f"DoH {qtype} status={payload.get('Status')}")
            continue
        for answer in payload.get("Answer") or []:
            address = str(answer.get("data") or "").strip()
            if not address:
                continue
            try:
                ipaddress.ip_address(address)
            except ValueError:
                continue
            key = (version, address)
            if key in seen:
                continue
            seen.add(key)
            records.append({"family": version, "address": address, "private": _is_private_ip(address), "source": "doh"})
    return records, "; ".join(errors)


def network_request_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    return _http_request(invocation, context, tool_label="网络请求")


def http_client_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    return _http_request(invocation, context, tool_label="HTTP 客户端")


def protocol_adapter_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    args = dict(invocation.arguments or {})
    command = str(args.get("curl") or args.get("command") or args.get("text") or "").strip()
    parsed = _parse_curl_command(command) if command.lower().startswith("curl") else {}
    url = str(args.get("url") or parsed.get("url") or _first_url(context.user_message) or "").strip()
    if not url:
        return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.FAILED, "协议适配失败：缺少 URL。", error_code="protocol_url_missing")

    method = _normalize_method(args.get("method") or parsed.get("method") or ("POST if body/json is present else GET"))
    if method == "POST IF BODY/JSON IS PRESENT ELSE GET":
        method = "POST" if any(key in args for key in ("body", "data", "json")) or parsed.get("body") else "GET"

    headers = _safe_headers(args.get("headers") or parsed.get("headers") or {})
    normalized = {
        "target_tool": "http_client",
        "url": _merge_query_params(url, args.get("params")),
        "method": method,
        "headers": headers,
        "timeout_sec": _clamp_float(args.get("timeout_sec") or args.get("timeout"), minimum=1.0, maximum=60.0, default=15.0),
        "max_bytes": _clamp_int(args.get("max_bytes"), minimum=1024, maximum=1024 * 1024, default=65536),
        "allow_loopback_http": bool(args.get("allow_loopback_http", False)),
        "allow_private_network": bool(args.get("allow_private_network", False)),
    }
    if "json" in args:
        normalized["json"] = args["json"]
    elif parsed.get("body") is not None:
        normalized["body"] = parsed["body"]
    elif "body" in args or "data" in args:
        normalized["body"] = args.get("body", args.get("data"))

    try:
        validate_url(str(normalized["url"]), allow_loopback_http=bool(normalized["allow_loopback_http"]), purpose="protocol_adapter")
    except NetworkPolicyError as exc:
        return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.BLOCKED, str(exc), error_code="network_policy_blocked", data={"normalized_request": _public_request(normalized)})

    return ToolResult(
        invocation.step_id,
        invocation.tool_name,
        ToolResultStatus.OK,
        f"协议适配完成：{method} {normalized['url']} -> http_client",
        data={"normalized_request": _public_request(normalized)},
    )


def _http_request(invocation: ToolInvocation, context: TurnContext, *, tool_label: str) -> ToolResult:
    args = dict(invocation.arguments or {})
    url = str(args.get("url") or _first_url(context.user_message) or "").strip()
    if not url:
        return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.FAILED, f"{tool_label}失败：缺少 URL。", error_code="url_missing")

    method = _normalize_method(args.get("method") or "GET")
    if method not in _ALLOWED_METHODS:
        return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.BLOCKED, f"不支持的 HTTP 方法：{method}", error_code="http_method_not_allowed")

    allow_loopback_http = bool(args.get("allow_loopback_http", False))
    allow_private_network = bool(args.get("allow_private_network", False))
    timeout = _clamp_float(args.get("timeout_sec") or args.get("timeout"), minimum=1.0, maximum=60.0, default=15.0)
    max_bytes = _clamp_int(args.get("max_bytes"), minimum=1024, maximum=1024 * 1024, default=65536)
    url = _merge_query_params(url, args.get("params"))

    try:
        validate_url(url, allow_loopback_http=allow_loopback_http, purpose=invocation.tool_name)
        _validate_private_boundary(url, timeout=timeout, allow_private_network=allow_private_network)
    except NetworkPolicyError as exc:
        return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.BLOCKED, str(exc), error_code="network_policy_blocked")

    headers = _safe_headers(args.get("headers") or {})
    data = _request_body(args, headers)
    request = urllib.request.Request(url=url, data=data, method=method, headers=headers)
    started = time.perf_counter()
    try:
        with urlopen_with_policy(request, timeout=timeout, allow_loopback_http=allow_loopback_http, purpose=invocation.tool_name) as response:
            return _response_result(invocation, context, response, url=url, started=started, max_bytes=max_bytes)
    except urllib.error.HTTPError as exc:
        return _response_result(invocation, context, exc, url=url, started=started, max_bytes=max_bytes)
    except TimeoutError as exc:
        return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.TIMEOUT, f"{tool_label}超时：{exc}", error_code="network_timeout")
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, TimeoutError):
            return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.TIMEOUT, f"{tool_label}超时：{reason}", error_code="network_timeout")
        return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.FAILED, f"{tool_label}失败：{reason}", error_code="network_request_failed", data={"error": str(reason)})
    except Exception as exc:
        return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.FAILED, f"{tool_label}失败：{exc}", error_code="network_request_failed", data={"error": str(exc)})


def _response_result(invocation: ToolInvocation, context: TurnContext, response: Any, *, url: str, started: float, max_bytes: int) -> ToolResult:
    raw = response.read(max_bytes + 1)
    truncated = len(raw) > max_bytes
    raw = raw[:max_bytes]
    status_code = int(getattr(response, "status", getattr(response, "code", 0)) or 0)
    headers = _response_headers(getattr(response, "headers", {}) or {})
    content_type = str(headers.get("content-type") or "").lower()
    preview, encoding, binary = _body_preview(raw, content_type)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    summary = f"HTTP {status_code or '?'}，读取 {len(raw)} bytes"
    if truncated:
        summary += "，已截断"
    return ToolResult(
        invocation.step_id,
        invocation.tool_name,
        ToolResultStatus.OK,
        truncate_text(summary + ("\n" + preview if preview and not binary else ""), context.policy.max_output_chars),
        data={
            "url": url,
            "status_code": status_code,
            "ok_status": 200 <= status_code < 400 if status_code else False,
            "headers": headers,
            "body_preview": preview,
            "body_encoding": encoding,
            "body_is_binary": binary,
            "bytes_read": len(raw),
            "truncated": truncated,
            "elapsed_ms": elapsed_ms,
        },
    )


def _request_body(args: Mapping[str, Any], headers: dict[str, str]) -> bytes | None:
    if "json" in args:
        headers.setdefault("Content-Type", "application/json")
        return json.dumps(args["json"], ensure_ascii=False).encode("utf-8")
    value = args.get("body", args.get("data", None))
    if value is None:
        return None
    if isinstance(value, bytes):
        return value
    if isinstance(value, (dict, list)):
        headers.setdefault("Content-Type", "application/json")
        return json.dumps(value, ensure_ascii=False).encode("utf-8")
    return str(value).encode("utf-8")


def _safe_headers(raw: Any) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        return {"User-Agent": "Tiangong-Linyuanzhe/2"}
    headers: dict[str, str] = {"User-Agent": "Tiangong-Linyuanzhe/2"}
    for key, value in raw.items():
        name = str(key or "").strip()
        val = str(value or "").strip()
        if not name or name.lower() == "host":
            continue
        if any(ch in name + val for ch in "\r\n"):
            continue
        headers[name] = val
    return headers


def _public_request(request: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(request)
    headers = result.get("headers")
    if isinstance(headers, Mapping):
        redacted: dict[str, str] = {}
        for key, value in headers.items():
            lowered = str(key or "").lower()
            if lowered in {"authorization", "cookie", "set-cookie", "x-api-key", "api-key"}:
                redacted[str(key)] = "<redacted>"
            else:
                redacted[str(key)] = str(value)
        result["headers"] = redacted
    return result


def _response_headers(raw: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    items = raw.items() if hasattr(raw, "items") else []
    for key, value in items:
        lower = str(key or "").lower()
        if lower in _SAFE_RESPONSE_HEADERS:
            result[lower] = str(value)
    return result


def _body_preview(raw: bytes, content_type: str) -> tuple[str, str, bool]:
    if not raw:
        return "", "", False
    text_like = any(marker in content_type for marker in ("text/", "json", "xml", "html", "javascript", "yaml"))
    if text_like or _looks_text(raw):
        return raw.decode("utf-8", errors="replace"), "utf-8-replace", False
    return base64.b64encode(raw[:2048]).decode("ascii"), "base64", True


def _looks_text(raw: bytes) -> bool:
    sample = raw[:2048]
    if b"\x00" in sample:
        return False
    printable = sum(1 for value in sample if value in b"\n\r\t" or 32 <= value < 127 or value >= 128)
    return printable / max(1, len(sample)) > 0.92


def _merge_query_params(url: str, params: Any) -> str:
    if not isinstance(params, Mapping) or not params:
        return url
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    for key, value in params.items():
        query.append((str(key), str(value)))
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), parsed.fragment))


def _validate_private_boundary(url: str, *, timeout: float, allow_private_network: bool) -> None:
    host = urllib.parse.urlparse(url).hostname or ""
    if not host:
        raise NetworkPolicyError("network_request: invalid URL host")
    if allow_private_network:
        return
    if _host_is_private(host, timeout=timeout):
        raise NetworkPolicyError("network_request: private, loopback, link-local, multicast, and reserved targets require allow_private_network=true")


def _host_is_private(host: str, *, timeout: float) -> bool:
    if host.lower() in {"localhost", "0.0.0.0"}:
        return True
    try:
        return _is_private_ip(str(ipaddress.ip_address(host.strip("[]"))))
    except ValueError:
        pass
    old_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(min(5.0, max(1.0, timeout)))
        infos = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except Exception:
        return False
    finally:
        socket.setdefaulttimeout(old_timeout)
    return any(_is_private_ip(str(item[4][0])) for item in infos)


def _is_private_ip(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def _extract_host(value: Any) -> str:
    text = str(value or "").strip().strip("<>\"'")
    if not text:
        return ""
    parsed = urllib.parse.urlparse(text if "://" in text else f"//{text}", scheme="")
    host = parsed.hostname or text.split("/", 1)[0].split(":", 1)[0]
    return host.strip("[]").strip()


def _first_url(text: str) -> str:
    for token in str(text or "").split():
        item = token.strip("，。；;()[]{}<>\"'")
        if item.startswith(("https://", "http://")):
            return item
    return ""


def _normalize_method(value: Any) -> str:
    return str(value or "GET").strip().upper().replace("-", "_")


def _parse_curl_command(command: str) -> dict[str, Any]:
    try:
        import shlex

        parts = shlex.split(command)
    except Exception:
        parts = command.split()
    if parts and parts[0].lower() == "curl":
        parts = parts[1:]
    result: dict[str, Any] = {"headers": {}}
    index = 0
    while index < len(parts):
        item = parts[index]
        lower = item.lower()
        if lower in {"-x", "--request"} and index + 1 < len(parts):
            result["method"] = parts[index + 1]
            index += 2
            continue
        if lower in {"-h", "--header"} and index + 1 < len(parts):
            header = parts[index + 1]
            if ":" in header:
                key, value = header.split(":", 1)
                result["headers"][key.strip()] = value.strip()
            index += 2
            continue
        if lower in {"-d", "--data", "--data-raw", "--data-binary"} and index + 1 < len(parts):
            result["body"] = parts[index + 1]
            result.setdefault("method", "POST")
            index += 2
            continue
        if item.startswith(("http://", "https://")):
            result["url"] = item
        index += 1
    return result


def _clamp_int(value: Any, *, minimum: int, maximum: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _clamp_float(value: Any, *, minimum: float, maximum: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def web_readability_extract_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    """获取网页并清洗出正文文本，去除广告、导航等噪音。"""
    url = str(invocation.arguments.get("url") or "")
    html_or_text = str(invocation.arguments.get("html_or_text") or invocation.arguments.get("text") or invocation.arguments.get("content") or "")
    if not url and not html_or_text:
        return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.FAILED, "缺少要清洗的网页 URL 或正文。", error_code="url_missing")

    timeout = _clamp_float(invocation.arguments.get("timeout"), minimum=3.0, maximum=60.0, default=15.0)
    max_chars = int(invocation.arguments.get("max_chars") or context.policy.max_output_chars or 32000)

    if url:
        try:
            response = urlopen_with_policy(url, timeout=timeout)
        except NetworkPolicyError as exc:
            return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.BLOCKED, str(exc), error_code="network_policy_blocked")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.FAILED, f"网页请求失败：{exc}", error_code="network_request_failed")

        raw_bytes = b""
        chunk_size = 2_000_000
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            raw_bytes += chunk
            if len(raw_bytes) > chunk_size:
                break

        content_type = response.headers.get("content-type", "")
        charset = "utf-8"
        for part in content_type.split(";"):
            part = part.strip()
            if part.lower().startswith("charset="):
                charset = part.split("=", 1)[1].strip().strip('"').strip("'")
                break

        try:
            html = raw_bytes.decode(charset, errors="replace")
        except (UnicodeDecodeError, LookupError):
            html = raw_bytes.decode("utf-8", errors="replace")
    else:
        content_type = "text/plain"
        html = html_or_text

    import re as _re
    cleaned = _re.sub(r"<(script|style|noscript|iframe|svg)[^>]*?>.*?</\1>", "", html, flags=_re.DOTALL | _re.IGNORECASE)
    text = _re.sub(r"<[^>]+>", " ", cleaned)
    text = _re.sub(r"[\n\r\t ]+", " ", text)
    text = text.strip()
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    text = _re.sub(r" {2,}", " ", text)

    if len(text) > max_chars:
        text = text[:max_chars] + f"\\n\\n…(截断，原文共 {len(text):,} 字符)"

    return ToolResult(
        step_id=invocation.step_id,
        tool_name=invocation.tool_name,
        status=ToolResultStatus.OK,
        output_summary=truncate_text(text[:2000], context.policy.max_output_chars),
        data={"url": url, "text": text, "text_length": len(text), "content_type": content_type},
    )
