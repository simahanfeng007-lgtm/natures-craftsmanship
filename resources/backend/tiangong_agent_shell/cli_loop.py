"""天工造物 v2 CLI 对话循环 —— 精简版。
新链路：①提示词装配 → ②合并判定 → ③执行 work 路 → ④输出/session → ⑤经验合成与记忆存储
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tiangong_agent_runtime.activation_protocol import ActivationForm

from .composition_root import AgentShellContext
from .config_loader import ModelConfig
from .errors import ModelClientError
from .organ_signal_emitters import refresh_session_system_prompt
from .prompt_compiler import compile_existing_messages_envelope, provider_is_ready, seal_compiled_messages
from .tool_cards import huoqu_gongju_canshu

# ── 常量 ──────────────────────────────────────────
STREAM_EVENT_PREFIX = "__TIANGONG_STREAM_EVENT__ "
_UPLOAD_FILE_MAX_COUNT = 5
_UPLOAD_FILE_MAX_TOTAL_BYTES = 200 * 1024 * 1024
_IMAGE_ATTACHMENT_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "avif", "svg", "tif", "tiff"}
_VIDEO_ATTACHMENT_EXTENSIONS = {"mp4", "webm", "ogv", "mov", "mkv", "avi", "m4v", "wmv", "flv", "mpeg", "mpg", "3gp", "ts", "m2ts"}
_AUDIO_ATTACHMENT_EXTENSIONS = {"mp3", "wav", "ogg", "m4a", "flac", "aac", "opus", "wma"}
_DOCUMENT_ATTACHMENT_EXTENSIONS = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "csv", "tsv", "txt", "md", "json", "html", "htm"}
_INTERNAL_NOISE = ("[计划器]", "【计划器】", "[运行链]", "【运行链】", "未生成可执行计划")
_THINK_BLOCK_RE = re.compile(r"(?is)(?:```think(?:ing)?\s*.*?```|<think(?:ing)?\b[^>]*>.*?</think(?:ing)?>)")
_OPEN_THINK_RE = re.compile(r"(?is)<think(?:ing)?\b[^>]*>.*$")
_INTERNAL_WORKSPACE_DIR_NAMES = {
    ".linyuanzhe",
    ".tiangong_media_context",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
}


def _strip_think_blocks(content: str) -> str:
    text = str(content or "").replace("\x00", "")
    text = _THINK_BLOCK_RE.sub("", text)
    text = _OPEN_THINK_RE.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _json_object_from_model_text(content: str) -> dict[str, Any]:
    text = _strip_think_blocks(content)
    start = text.find("{")
    end = text.rfind("}") + 1
    if start < 0 or end <= start:
        return {}
    try:
        value = json.loads(text[start:end])
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _should_consider_learning(lujing: str, xiaoxi: str, jieguo: str) -> bool:
    user = re.sub(r"\s+", "", str(xiaoxi or "").lower())
    result = re.sub(r"\s+", "", _strip_think_blocks(jieguo).lower())
    if not result:
        return False
    if lujing == "chat":
        trivial_markers = ("你好", "您好", "hello", "hi", "测试", "连通", "链路正常", "只回复", "只回答", "ok")
        if len(result) <= 12:
            return False
        if len(user) <= 80 and any(marker in user for marker in trivial_markers):
            return False
    return True


def _chat_route_fallback(xiaoxi: str) -> str:
    """本地 fallback 已移除。所有 chat 请求穿透到 LLM 正常生成回复。"""
    return ""


def _model_connection_failed_message(exc: ModelClientError) -> str:
    message = str(getattr(exc, "user_message", "") or exc).strip()
    if message.startswith("模型连接失败"):
        return message
    return f"模型连接失败：{message or 'Provider 调用失败，请检查模型配置后重试。'}"


def _looks_like_code_analysis_request(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "").casefold())
    if not compact:
        return False
    action_markers = ("分析", "看看", "看下", "看一下", "梳理", "审查", "了解", "读一下")
    code_scope_markers = ("代码", "源码", "项目", "工程", "逻辑", "架构", "模块", "入口", "调用链", "天工造物v1")
    return any(marker in compact for marker in action_markers) and any(marker in compact for marker in code_scope_markers)


def _looks_like_web_search_request(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "").casefold())
    if not compact:
        return False
    local_markers = (
        "代码", "源码", "文件", "目录", "工作区", "项目", "工程", "bug", "修复", "打包", "安装",
        "code", "source", "file", "folder", "workspace", "repo", "project",
    )
    if any(marker in compact for marker in local_markers):
        return False
    direct_markers = (
        "联网", "网上", "上网", "网页", "网址", "官网", "来源", "新闻", "最新", "今天",
        "实时", "查一下", "查询", "搜索", "检索", "资料", "公开资料",
        "websearch", "web_search", "search", "google", "bing", "news", "latest", "today", "current",
    )
    return any(marker in compact for marker in direct_markers)


def _looks_like_project_closure_quality_request(message: str) -> bool:
    compact = re.sub(r"\s+", "", str(message or "").casefold())
    if not compact:
        return False
    direct_markers = (
        "项目收口质检", "项目收口", "项目验收", "全流程质检", "收口质检", "收口检查",
        "交付前检查", "交付前质检", "上线前验收", "上线前检查", "发布前检查",
        "打包前检查", "打包前质检", "跑一遍项目验收", "看看能不能交付", "能不能交付",
    )
    if any(marker in compact for marker in direct_markers):
        return True
    quality_markers = ("质检", "验收", "qualitygate", "quality_gate")
    project_markers = ("项目", "工程", "交付", "上线", "发布", "打包", "净包")
    return any(marker in compact for marker in quality_markers) and any(marker in compact for marker in project_markers)


def _task_workspace_from_message(context: AgentShellContext, message: str) -> Path:
    base = Path(context.workspace) if context.workspace else Path.cwd()
    try:
        base = base.expanduser().resolve()
    except OSError:
        return base
    text = str(message or "")
    compact_text = re.sub(r"\s+", "", text.casefold())
    candidates: list[tuple[int, Path]] = []
    try:
        children = list(base.iterdir()) if base.exists() and base.is_dir() else []
    except OSError:
        return base
    for child in children:
        try:
            if not child.is_dir():
                continue
        except OSError:
            continue
        name = child.name
        if name.casefold() in _INTERNAL_WORKSPACE_DIR_NAMES:
            continue
        compact_name = re.sub(r"\s+", "", name.casefold())
        if name and (name in text or compact_name in compact_text):
            candidates.append((len(compact_name), child))
    if candidates:
        return sorted(candidates, key=lambda item: item[0], reverse=True)[0][1]
    return base


def _workspace_relative_arg(context: AgentShellContext, target: Path) -> str:
    base = Path(context.workspace) if context.workspace else Path.cwd()
    try:
        base = base.expanduser().resolve()
        target = target.expanduser().resolve()
        return target.relative_to(base).as_posix() or "."
    except (OSError, ValueError):
        return str(target)


def _normalize_runtime_tool_call(
    context: AgentShellContext,
    tool_name: str,
    arguments: dict[str, Any] | None,
    user_text: str,
) -> tuple[str, dict[str, Any]]:
    name = str(tool_name or "").strip()
    args = dict(arguments or {})
    try:
        if name and context.runtime.registry.get(name) is not None:
            return name, _normalize_registered_tool_args(name, args, user_text)
    except Exception:
        pass

    alias = name.casefold().replace("-", "_").replace(".", "_")
    if alias in {"generate_plan", "create_plan", "make_plan", "plan_task"}:
        query = str(args.get("query") or args.get("task") or args.get("content") or user_text or "")
        combined = f"{user_text}\n{query}"
        if _looks_like_code_analysis_request(combined):
            target = _task_workspace_from_message(context, combined)
            return "scan_project", {
                "path": _workspace_relative_arg(context, target),
                "max_depth": 6,
                "max_files": 1500,
            }
        return "return_analysis", {"content": query or user_text, "task": str(user_text or "")[:200]}
    if alias in {"analysis", "analyze", "return_analyze"}:
        content = str(args.get("content") or args.get("query") or args.get("analysis") or user_text or "")
        return "return_analysis", {"content": content, "task": str(user_text or "")[:200]}
    return name, args


def _first_url_from_text(text: str) -> str:
    match = re.search(r"https?://[^\s\"'<>，。；;]+", str(text or ""))
    return match.group(0).rstrip(".,)") if match else ""


def _normalize_registered_tool_args(tool_name: str, args: dict[str, Any], user_text: str) -> dict[str, Any]:
    """Repair common LLM argument slips for registered tools without changing tool choice."""
    name = str(tool_name or "").strip().casefold().replace("-", "_").replace(" ", "")
    out = dict(args or {})
    if name in {"websearch", "web_search", "联网搜索", "网页检索"}:
        query = str(out.get("query") or out.get("chaxun") or out.get("q") or out.get("keyword") or "").strip()
        if not query:
            query = str(user_text or "").strip()
        out["query"] = query
        return out
    if name in {"webreadabilityextract", "web_readability_extract", "网页正文清洗"}:
        url = str(out.get("url") or out.get("href") or out.get("link") or "").strip()
        if not url:
            for key in ("query", "text", "html_or_text", "content"):
                url = _first_url_from_text(str(out.get(key) or ""))
                if url:
                    break
        if not url:
            url = _first_url_from_text(user_text)
        if url:
            out["url"] = url
        return out
    return out


def write_line(text: str, *, stream: Any | None = None) -> None:
    print(text, file=stream or sys.stdout, flush=True)


def write_stream_event(event_type: str, step_id: str, title: str, status: str, summary: str = "", **extra: Any) -> None:
    if os.getenv("TIANGONG_STREAM_EVENTS") != "1":
        return
    payload: dict[str, Any] = {
        "schema": "tiangong.desktop.stream_event.v1",
        "type": event_type,
        "request_id": os.getenv("TIANGONG_REQUEST_ID", ""),
        "step_id": str(step_id or title or event_type),
        "title": str(title or step_id or event_type),
        "status": str(status or "pending"),
        "summary": str(summary or ""),
        "ts": time.time(),
    }
    payload.update({key: value for key, value in extra.items() if value is not None})
    try:
        body = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception as exc:
        body = json.dumps(
            {
                "schema": "tiangong.desktop.stream_event.v1",
                "type": "step",
                "request_id": os.getenv("TIANGONG_REQUEST_ID", ""),
                "step_id": "stream_event_error",
                "title": "实时步骤事件",
                "status": "failed",
                "summary": str(exc)[:240],
                "ts": time.time(),
            },
            ensure_ascii=False,
        )
    write_line(f"{STREAM_EVENT_PREFIX}{body}")


def _stream_step(step_id: str, title: str, status: str, summary: str = "", **extra: Any) -> None:
    write_stream_event("step", step_id, title, status, summary, **extra)


def _provider_not_configured_message() -> str:
    return "尚未配置模型接口。请进入【设置】页填写服务地址、模型名与 API Key，保存后即可进入真实模型链路。"


def _looks_like_text_tool_call(content: str) -> bool:
    text = _strip_think_blocks(str(content or "")).lstrip().casefold()
    return text.startswith("<tool_call") and "<function" in text


def _strip_noise(content: str) -> str:
    if _looks_like_text_tool_call(content):
        return "模型发出了工具调用指令，未作为普通文本展示。"
    lines: list[str] = []
    for raw in _strip_think_blocks(str(content or "")).replace("\x00", "").splitlines():
        line = raw.strip()
        if not line:
            lines.append(raw)
            continue
        if any(marker in line for marker in _INTERNAL_NOISE):
            continue
        lines.append(raw)
    visible = "\n".join(lines).strip()
    return visible or "模型回复只包含内部信息，已隐藏。"


def _is_bad_final_answer(content: str) -> bool:
    text = str(content or "").strip()
    if not text:
        return True
    bad_exact = {
        "模型发出了工具调用指令，未作为普通文本展示。",
        "模型回复只包含内部信息，已隐藏。",
    }
    if text in bad_exact:
        return True
    lowered = text.lower()
    return lowered.startswith("<tool_call") or "<function=" in lowered[:300]


def _limit_visible_text(text: str, *, max_chars: int = 12000) -> str:
    clean = str(text or "").replace("\x00", "").strip()
    if len(clean) <= max_chars:
        return clean
    return clean[:max_chars] + f"\n...[已截断 {len(clean) - max_chars} 字符]"


def _tool_result_fallback_response(tool_results: list[dict[str, Any]]) -> str:
    """Return a user-visible answer when the model keeps emitting tool calls."""
    for item in reversed(tool_results or []):
        result = item.get("result") if isinstance(item, dict) else None
        if not isinstance(result, dict):
            continue
        data = result.get("data")
        summary = str(result.get("summary") or "").strip()
        if isinstance(data, dict):
            for key in ("content", "answer", "text", "markdown", "human_readable_summary"):
                value = data.get(key)
                if value:
                    return _limit_visible_text(str(value))
            rows: list[str] = []
            if summary:
                rows.append(summary)
            results = data.get("results")
            if isinstance(results, list) and results:
                rows.append("来源：")
                for index, source in enumerate(results[:8], start=1):
                    if not isinstance(source, dict):
                        continue
                    title = str(source.get("title") or "未命名来源").strip()
                    domain = str(source.get("domain") or source.get("source_name") or "").strip()
                    published = str(source.get("published") or "").strip()
                    url = str(source.get("url") or "").strip()
                    meta = "；".join(part for part in (domain, published) if part)
                    rows.append(f"{index}. {title}" + (f"（{meta}）" if meta else ""))
                    if url:
                        rows.append(f"   URL: {url}")
            urls = data.get("urls")
            if isinstance(urls, list) and urls and not any("URL:" in row for row in rows):
                rows.append("URL：")
                rows.extend(f"- {url}" for url in urls[:10] if url)
            if rows:
                return _limit_visible_text("\n".join(rows))
        if summary:
            return _limit_visible_text(summary)
        if result.get("error"):
            return _limit_visible_text(f"工具执行失败：{result.get('error')}")
    return "工具已经执行，但模型没有生成最终文字；已进入自动纠偏，请再发一次我会接着收口。"


def _force_finalize_tool_answer(
    context: AgentShellContext,
    system_msg: dict[str, Any],
    duihua: list[dict[str, Any]],
    envelope: Any,
    tool_results: list[dict[str, Any]],
) -> str:
    """Ask the model to self-correct and produce a final answer with tools disabled."""
    evidence = json.dumps(tool_results[-4:], ensure_ascii=False, default=str)
    final_user = {
        "role": "user",
        "content": (
            "工具已经执行完毕。现在进入最终回答阶段：\n"
            "1. 不要再调用工具，不要输出 <tool_call>、function_call、JSON 工具指令或内部占位。\n"
            "2. 如果上一轮工具选择、参数或回答格式有问题，请你自行根据工具结果纠错。\n"
            "3. 直接给用户可读的最终答案；如果工具结果不足，明确说明证据不足和下一步。\n\n"
            "最近工具结果：\n"
            + evidence[:12000]
        ),
    }
    final_system = dict(system_msg)
    final_system["content"] = (
        str(final_system.get("content") or "")
        + "\n\n【最终回答硬约束】工具已关闭；必须输出自然语言最终答案。"
        + "禁止输出工具调用标签、函数调用 JSON、内部状态占位。"
        + "若发现自己刚才只给了工具调用或半成品，自动修正并用工具结果回答用户。"
    )
    env = seal_compiled_messages(
        [final_system] + [dict(m) for m in duihua] + [final_user],
        phase="chat",
        compiled_prompt_id=envelope.compiled_prompt_id,
        output_contract="final_answer_no_tools",
    )
    result = context.model_client.chat(env, context.config)
    if result.tool_calls:
        return ""
    content = _strip_noise(result.content or "")
    return "" if _is_bad_final_answer(content) else content


def _repair_visible_final_answer(context: AgentShellContext, xiaoxi: str, bad_answer: str, qinggan_ka: str = "") -> str:
    """One-shot repair for empty/internal/tool-call final outputs."""
    system = (
        "你是天工造物的最终答案修复器。上一轮输出没有形成用户可读答案。"
        "请不要调用工具，不要输出内部状态、工具调用标签或函数调用 JSON。"
        "你必须根据用户问题、已有上下文和上一轮异常输出，直接给出尽可能正确的最终答案；"
        "如果证据不足，就明确说明证据不足和下一步，而不是输出占位。"
    )
    if qinggan_ka:
        system += "\n\n" + qinggan_ka
    user = (
        f"用户问题：\n{xiaoxi}\n\n"
        f"上一轮异常输出：\n{bad_answer}\n\n"
        "请现在给出最终用户可读答案。"
    )
    envelope = compile_existing_messages_envelope(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        phase="final_answer_repair",
        output_contract="final_answer_no_tools",
        metadata={"route": "self_correct"},
    )
    result = context.model_client.chat(envelope, context.config)
    if result.tool_calls:
        return ""
    content = _strip_noise(result.content or "")
    return "" if _is_bad_final_answer(content) else content


# ── 轻量 LLM 调用 ─────────────────────────────────

def _qingliang_llm(context: AgentShellContext, xitong: str, yonghu: str) -> str:
    """轻量 LLM 调用，用于路由判定和学习判定"""
    envelope = compile_existing_messages_envelope(
        [{"role": "system", "content": xitong}, {"role": "user", "content": yonghu}],
        phase="lightweight_routing",
        output_contract="short_json_or_word",
        metadata={"route": "lightweight"},
    )
    result = context.model_client.chat(envelope, context.config)
    return (result.content or "").strip()


def _prompt_safe_text(value: Any, *, limit: int = 900) -> str:
    text = str(value or "").replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip()
    for raw in (os.getenv("TIANGONG_API_KEY", ""), os.getenv("DEEPSEEK_API_KEY", ""), os.getenv("OPENAI_API_KEY", "")):
        if raw:
            text = text.replace(raw, "<redacted>")
    lowered = text.lower()
    if any(marker in lowered for marker in ("api_key", "authorization", "bearer ", "secret", "password", "credential", "token=")):
        text = re.sub(r"(?i)(api_key|authorization|bearer|secret|password|credential|token)(\s*[:=]\s*)\S+", r"\1\2<redacted>", text)
    return text[: max(16, int(limit))]


def _uploaded_file_refs() -> list[dict[str, Any]]:
    raw = os.getenv("TIANGONG_UPLOAD_FILES_JSON", "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    refs: list[dict[str, Any]] = []
    total_size = 0
    for item in data:
        if not isinstance(item, dict):
            continue
        path_text = _prompt_safe_text(item.get("path"), limit=900)
        if not path_text:
            continue
        size = int(item.get("size") or item.get("size_bytes") or 0)
        if size < 0:
            size = 0
        total_size += size
        if total_size > _UPLOAD_FILE_MAX_TOTAL_BYTES:
            break
        status = _prompt_safe_text(item.get("status") or "imported", limit=32)
        if status and status not in {"imported", "selected", "attached"}:
            continue
        enriched = _enrich_attachment_ref({**item, "path": path_text, "size": size, "status": status or "imported"}, source="current_upload")
        refs.append(enriched)
        if len(refs) >= _UPLOAD_FILE_MAX_COUNT:
            break
    return refs


def _has_uploaded_file_refs() -> bool:
    return bool(_uploaded_file_refs())


def _attachment_kind_from_ext(ext: str) -> str:
    clean = str(ext or "").lower().lstrip(".")
    if clean in _IMAGE_ATTACHMENT_EXTENSIONS:
        return "image"
    if clean in _VIDEO_ATTACHMENT_EXTENSIONS:
        return "video"
    if clean in _AUDIO_ATTACHMENT_EXTENSIONS:
        return "audio"
    if clean in _DOCUMENT_ATTACHMENT_EXTENSIONS:
        return "document"
    return "file"


def _attachment_skill_hint(kind: str) -> tuple[str, str, str]:
    if kind == "image":
        return ("图片解析", "image_inspect", "image_ocr_parse")
    if kind == "video":
        return ("视频解析", "video_inspect", "video_audio_transcribe")
    if kind == "audio":
        return ("音频解析", "audio_transcribe", "audio_summary")
    if kind == "document":
        return ("文档处理", "document_parse", "document_query")
    return ("文件操作", "read_file", "document_parse")


def _enrich_attachment_ref(item: dict[str, Any], *, source: str = "upload") -> dict[str, Any]:
    path_text = _prompt_safe_text(item.get("path"), limit=900)
    name = _prompt_safe_text(item.get("name") or Path(path_text).name, limit=180)
    ext = _prompt_safe_text(item.get("ext") or Path(path_text).suffix.lstrip("."), limit=24).lower().lstrip(".")
    kind = _attachment_kind_from_ext(ext)
    skill, primary_tool, secondary_tool = _attachment_skill_hint(kind)
    try:
        size = int(item.get("size") or item.get("size_bytes") or 0)
    except Exception:
        size = 0
    return {
        "path": path_text,
        "name": name,
        "ext": ext,
        "kind": kind,
        "size": max(0, size),
        "document_id": _prompt_safe_text(item.get("documentId") or item.get("document_id"), limit=120),
        "status": _prompt_safe_text(item.get("status") or "imported", limit=32) or "imported",
        "citation_count": int(item.get("citationCount") or item.get("citation_count") or 0),
        "source": source,
        "recommended_skill": skill,
        "primary_tool": primary_tool,
        "secondary_tool": secondary_tool,
    }


def _attachment_refs_from_items(items: Any, *, source: str) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    refs: list[dict[str, Any]] = []
    total_size = 0
    for item in items[:_UPLOAD_FILE_MAX_COUNT]:
        if not isinstance(item, dict):
            continue
        ref = _enrich_attachment_ref(item, source=source)
        if not ref.get("path"):
            continue
        total_size += int(ref.get("size") or 0)
        if total_size > _UPLOAD_FILE_MAX_TOTAL_BYTES:
            break
        refs.append(ref)
    return refs


def _frontend_attachment_refs() -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for message in _frontend_messages_from_env():
        refs.extend(_attachment_refs_from_items(message.get("attachments"), source="recent_message"))
    return refs[-_UPLOAD_FILE_MAX_COUNT:]


def _dedupe_attachment_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in refs:
        key = str(item.get("path") or item.get("name") or "").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[-_UPLOAD_FILE_MAX_COUNT:]


def _all_attachment_refs(*, include_recent: bool = True) -> list[dict[str, Any]]:
    refs = [*_uploaded_file_refs()]
    if include_recent:
        refs.extend(_frontend_attachment_refs())
    return _dedupe_attachment_refs(refs)


def _looks_like_attachment_rejection(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "").casefold())
    if not compact:
        return False
    markers = (
        "没让你读",
        "没叫你读",
        "没让你看",
        "没叫你看",
        "不要读",
        "不要看",
        "别读",
        "别看",
        "不用读",
        "不用看",
        "不要解析",
        "别解析",
        "不用解析",
        "不是让你读",
        "不是让你看",
        "不是看图",
        "不是读图",
        "不是图片",
        "别管图片",
        "别管照片",
        "一直都在读这个照片",
        "一直读照片",
        "一直读图片",
    )
    return any(marker in compact for marker in markers)


def _explicitly_references_attachment(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "").casefold())
    if not compact or _looks_like_attachment_rejection(text):
        return False
    markers = (
        "这张图",
        "这张图片",
        "这个图",
        "这个图片",
        "这张截图",
        "这个截图",
        "这张照片",
        "这个照片",
        "刚才那张",
        "刚才的图",
        "刚才的图片",
        "刚才的截图",
        "刚才的照片",
        "上一张",
        "上面那张",
        "附件里",
        "上传的图",
        "上传的图片",
        "发的图",
        "发的图片",
        "图里",
        "图片里",
        "截图里",
        "照片里",
        "看图",
        "读图",
        "解析图片",
        "识别图片",
        "image",
        "screenshot",
        "photo",
        "picture",
        "attachment",
    )
    return any(marker in compact for marker in markers)


def _attachment_refs_for_current_turn(text: str = "") -> list[dict[str, Any]]:
    current = _uploaded_file_refs()
    if current:
        return _dedupe_attachment_refs(current)
    if _explicitly_references_attachment(text):
        return _dedupe_attachment_refs(_frontend_attachment_refs())
    return []


def _recommended_attachment_skill(text: str = "") -> str:
    refs = _attachment_refs_for_current_turn(text)
    if not refs:
        return ""
    priority = {"image": 5, "video": 4, "audio": 3, "document": 2, "file": 1}
    chosen = sorted(refs, key=lambda item: priority.get(str(item.get("kind") or ""), 0), reverse=True)[0]
    return str(chosen.get("recommended_skill") or "")


def _truthy_capability(value: Any) -> bool | None:
    if value is True:
        return True
    if value is False:
        return False
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "supported", "support", "enabled"}:
        return True
    if text in {"0", "false", "no", "unsupported", "not_supported", "disabled"}:
        return False
    return None


def _provider_factsheet_for_config(config: Any) -> dict[str, Any]:
    provider = str(getattr(config, "provider", "") or "").strip().lower()
    model = str(getattr(config, "model", "") or "").strip().lower()
    candidates: list[str] = []
    if provider:
        candidates.append(provider)
    if "mimo" in provider or "mimo" in model:
        candidates.append("mimo")
    if "minimax" in provider or "minimax" in model:
        candidates.append("minimax_m3")
    if provider in {"glm", "zhipu", "zai"} or model.startswith("glm-"):
        candidates.append("glm_5_1")
    if "gpt-5.5" in model:
        candidates.append("gpt_5_5")
    try:
        from tiangong_kernel.l4_action_grounding.model_provider_adapter import all_provider_factsheets

        factsheets = all_provider_factsheets()
        for candidate in candidates:
            item = factsheets.get(candidate)
            if item is None:
                continue
            if hasattr(item, "to_dict"):
                data = item.to_dict()
            else:
                data = dict(getattr(item, "__dict__", {}) or {})
            if data:
                return data
    except Exception:
        return {}
    return {}


def _model_name_has_modality_marker(config: Any, kind: str) -> bool:
    text = " ".join(
        str(getattr(config, key, "") or "").lower()
        for key in ("provider", "base_url", "model")
    )
    if kind == "image":
        markers = (
            "multimodal",
            "multi-modal",
            "omni",
            "vision",
            "image",
            "vl",
            "gpt-4o",
            "gpt-4.1",
            "gpt-5",
            "claude-3",
            "gemini",
            "qwen-vl",
            "qwen2-vl",
            "qwen2.5-vl",
            "qwen-omni",
            "glm-4v",
            "glm-v",
            "mimo-v2",
            "minimax-m3",
        )
        return any(marker in text for marker in markers)
    if kind == "video":
        return any(marker in text for marker in ("multimodal", "multi-modal", "omni", "video", "mimo-v2", "minimax-m3", "gemini"))
    if kind == "audio":
        return any(marker in text for marker in ("multimodal", "multi-modal", "omni", "audio", "asr", "mimo-v2", "qwen-audio", "qwen-omni", "gemini"))
    return False


def _configured_modality_support(config: Any, kind: str) -> bool | None:
    for name in (f"{kind}_input", f"{kind}_input_supported", "multimodal_input", "multimodal_input_supported"):
        value = _truthy_capability(getattr(config, name, None))
        if value is not None:
            return value
    env_names = {
        "image": ("TIANGONG_IMAGE_INPUT", "TIANGONG_IMAGE_INPUT_SUPPORTED"),
        "video": ("TIANGONG_VIDEO_INPUT", "TIANGONG_VIDEO_INPUT_SUPPORTED"),
        "audio": ("TIANGONG_AUDIO_INPUT", "TIANGONG_AUDIO_INPUT_SUPPORTED"),
    }.get(kind, ())
    for env_name in (*env_names, "TIANGONG_MULTIMODAL_INPUT"):
        value = _truthy_capability(os.getenv(env_name, ""))
        if value is not None:
            return value
    return None


def _model_supports_attachment_kind(config: Any, kind: str) -> bool:
    configured = _configured_modality_support(config, kind)
    if configured is not None:
        return configured
    factsheet = _provider_factsheet_for_config(config)
    field = {
        "image": "image_input_supported",
        "video": "video_input_supported",
        "audio": "audio_input_supported",
    }.get(kind)
    if factsheet and field:
        direct = _truthy_capability(factsheet.get(field))
        multi = _truthy_capability(factsheet.get("multimodal_input_supported"))
        if direct is True or multi is True:
            return True
        if direct is False and multi is False and not _model_name_has_modality_marker(config, kind):
            return False
    return _model_name_has_modality_marker(config, kind)


def _looks_like_attachment_content_request(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "").casefold())
    if _looks_like_attachment_rejection(text):
        return False
    if not compact:
        return bool(_uploaded_file_refs())
    markers = (
        "看图",
        "看看图",
        "图片",
        "图像",
        "截图",
        "照片",
        "视频",
        "音频",
        "附件",
        "内容",
        "是什么",
        "什么内容",
        "分析",
        "识别",
        "解析",
        "总结",
        "ocr",
        "image",
        "screenshot",
        "photo",
        "picture",
        "attachment",
        "kind=image",
        "kind=video",
        "kind=audio",
        "image_inspect",
        "video_inspect",
        "audio_transcribe",
        "表格",
        "图表",
        "这张",
        "里面",
        "read",
        "inspect",
        "analyze",
        "summarize",
    )
    return any(marker in compact for marker in markers)


def _attachment_content_skill_for_model(context: AgentShellContext, text: str) -> str:
    if not (_looks_like_attachment_content_request(text) or _explicitly_references_attachment(text)):
        return ""
    refs = [item for item in _attachment_refs_for_current_turn(text) if str(item.get("kind") or "") in {"image", "video", "audio"}]
    if not refs:
        return ""
    priority = {"image": 5, "video": 4, "audio": 3}
    for item in sorted(refs, key=lambda ref: priority.get(str(ref.get("kind") or ""), 0), reverse=True):
        kind = str(item.get("kind") or "")
        skill = str(item.get("recommended_skill") or "")
        if skill and _model_supports_attachment_kind(getattr(context, "config", None), kind):
            return skill
    # Still route to the parser so the tool can give a precise capability error
    # instead of letting plain chat hallucinate from a file name.
    chosen = sorted(refs, key=lambda ref: priority.get(str(ref.get("kind") or ""), 0), reverse=True)[0]
    return str(chosen.get("recommended_skill") or "")


def _uploaded_files_reference_card(context: AgentShellContext, current_text: str = "") -> str:
    if not _looks_like_attachment_content_request(current_text) and not _explicitly_references_attachment(current_text):
        return ""
    refs = _attachment_refs_for_current_turn(current_text)
    if not refs:
        return ""
    lines = [
        "[AttachmentFormatDecision / uploaded and recent attachment routing]",
        "Only the attachments listed in this card are eligible for this turn. Do not use older attachments unless the current user message explicitly refers to them.",
        "If the current user asks about attachment contents, call the recommended primary_tool first. For screenshots or image text, use image_inspect and then image_ocr_parse when needed.",
        f"workspace={_prompt_safe_text(context.workspace, limit=900)}; count={len(refs)}; content_policy=reference_only_no_raw_text",
    ]
    for index, item in enumerate(refs, start=1):
        lines.append(
            f"{index}. source={item.get('source')}; kind={item.get('kind')}; ext={item.get('ext')}; "
            f"recommended_skill={item.get('recommended_skill')}; primary_tool={item.get('primary_tool')}; secondary_tool={item.get('secondary_tool')}; "
            f"name={item.get('name')}; path={item.get('path')}; document_id={item.get('document_id') or 'missing'}; "
            f"size_bytes={item.get('size')}; status={item.get('status')}; citations={item.get('citation_count')}"
        )
    return "\n".join(lines)


def _message_with_uploaded_file_refs(context: AgentShellContext, xiaoxi: str) -> str:
    card = _uploaded_files_reference_card(context, xiaoxi)
    if not card:
        return xiaoxi
    return f"{xiaoxi}\n\n{card}"


def _jiaodui_qian_duan_xiaoxi(xiaoxi: str) -> str:
    """校对前端传回的当前消息：只清洗控制字符和换行，不改语义。"""
    clean = _prompt_safe_text(xiaoxi, limit=12000)
    clean = re.sub(r"[\u0001-\u0008\u000b\u000c\u000e-\u001f]", "", clean)
    return clean.strip()


def _zuijin_shitiao_duihua_ka(context: AgentShellContext, *, limit: int = 10) -> str:
    combined = [m for m in getattr(context.session, "messages", []) if m.get("role") != "system"]
    combined.extend(_frontend_messages_from_env())
    seen: set[tuple[str, str, str]] = set()
    chat_events: list[dict[str, Any]] = []
    for item in combined:
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()
        attachments = _attachment_refs_from_items(item.get("attachments"), source="recent_message")
        if role not in {"user", "assistant"} or (not content and not attachments):
            continue
        attachment_key = "|".join(str(ref.get("path") or ref.get("name") or "") for ref in attachments)
        key = (role, content, str(item.get("at") or item.get("created_at") or ""), attachment_key)
        if key in seen:
            continue
        seen.add(key)
        chat_events.append({
            "type": "chat",
            "role": role,
            "content": content,
            "attachments": attachments,
            "at": item.get("at") or item.get("created_at"),
            "seq": len(chat_events),
        })

    chat_events = chat_events[-max(1, int(limit)):]
    work_payload = _frontend_work_context_from_env()
    work_card = _frontend_work_context_card()
    events: list[dict[str, Any]] = list(chat_events)
    if work_card:
        run = work_payload.get("lastRun") if isinstance(work_payload.get("lastRun"), dict) else {}
        progress = work_payload.get("runProgress") if isinstance(work_payload.get("runProgress"), dict) else {}
        work_at = (
            run.get("finishedAt") or run.get("finished_at")
            or progress.get("finishedAt") or progress.get("finished_at")
            or progress.get("startedAt") or progress.get("started_at")
            or progress.get("anchorAt") or progress.get("anchor_at")
            or work_payload.get("capturedAt")
            or 0
        )
        events.append({
            "type": "work_event",
            "role": "work",
            "content": work_card,
            "attachments": [],
            "at": work_at,
            "seq": len(events) + 0.5,
        })

    if not events:
        return "当前窗口暂无历史上下文时间线。"

    def _event_time(value: Any) -> float:
        try:
            return float(value or 0)
        except Exception:
            return 0.0

    events.sort(key=lambda event: (_event_time(event.get("at")) or 10**30, float(event.get("seq") or 0)))
    lines = [
        "[RecentContextTimeline / chat + work event timeline]",
        f"events={len(events)}; chat_limit={max(1, int(limit))}; rule=read in chronological order; attachments are path references, not raw file content.",
        "Earlier attachments are historical context only. Reuse them only when the current user message explicitly refers to that attachment, such as '刚才那张图/这张截图/这个文件'. Otherwise ignore them for routing.",
    ]
    for index, event in enumerate(events, start=1):
        event_type = _prompt_safe_text(event.get("type"), limit=32)
        at = _prompt_safe_text(event.get("at"), limit=48)
        if event_type == "work_event":
            lines.append(f"{index}. [work_event] at={at}; source=previous_frontend_work_context")
            for raw_line in str(event.get("content") or "").splitlines():
                safe_line = _prompt_safe_text(raw_line, limit=760)
                if safe_line:
                    lines.append(f"   {safe_line}")
            continue
        role = _prompt_safe_text(event.get("role", "unknown"), limit=24) or "unknown"
        content = _prompt_safe_text(event.get("content", ""), limit=720)
        lines.append(f"{index}. [chat] at={at}; role={role}; content={content}")
        attachments = event.get("attachments") if isinstance(event.get("attachments"), list) else []
        if attachments:
            lines.append(f"   work_card.attachments=count={len(attachments)}; scope=historical_only; routing=use_only_if_current_message_explicitly_refers_to_attachment")
            for attachment_index, item in enumerate(attachments, start=1):
                lines.append(
                    f"   attachment[{attachment_index}]: source={_prompt_safe_text(item.get('source'), limit=40)}; "
                    f"kind={_prompt_safe_text(item.get('kind'), limit=40)}; ext={_prompt_safe_text(item.get('ext'), limit=24)}; "
                    f"recommended_skill={_prompt_safe_text(item.get('recommended_skill'), limit=80)}; "
                    f"primary_tool={_prompt_safe_text(item.get('primary_tool'), limit=80)}; "
                    f"secondary_tool={_prompt_safe_text(item.get('secondary_tool'), limit=80)}; "
                    f"name={_prompt_safe_text(item.get('name'), limit=180)}; "
                    f"path={_prompt_safe_text(item.get('path'), limit=900)}; "
                    f"size_bytes={_prompt_safe_text(item.get('size'), limit=40)}; "
                    f"document_id={_prompt_safe_text(item.get('document_id'), limit=120) or 'missing'}"
                )
    return _prompt_safe_text("\n".join(lines), limit=8000)


def _jiyi_chaxun_ci(xiaoxi: str) -> list[str]:
    clean = _prompt_safe_text(xiaoxi, limit=500).lower()
    terms = re.findall(r"[a-z0-9_\-.]{2,}|[\u4e00-\u9fff]{2,}", clean)
    compact = re.sub(r"\s+", "", clean)
    if len(compact) >= 2:
        terms.append(compact[:80])
    result: list[str] = []
    seen: set[str] = set()
    for term in terms:
        if term and term not in seen:
            seen.add(term)
            result.append(term)
    return result[:16]


def _jiyi_mingzhong(text: str, terms: list[str]) -> bool:
    haystack = _prompt_safe_text(text, limit=2000).lower()
    compact = re.sub(r"\s+", "", haystack)
    return any(term in haystack or term in compact for term in terms)


def _l1_jiyi_pipei(rt: Any, xiaoxi: str, *, limit: int = 3) -> list[str]:
    terms = _jiyi_chaxun_ci(xiaoxi)
    if not terms:
        return []
    store = getattr(rt, "_memory_store", None)
    if store is None or not hasattr(store, "active_records"):
        return []
    try:
        records = list(store.active_records())
    except Exception:
        return []
    matches: list[str] = []
    for record in sorted(records, key=lambda item: getattr(item, "last_accessed_at", 0.0), reverse=True):
        level = getattr(getattr(record, "memory_level", None), "value", getattr(record, "memory_level", ""))
        if str(level).upper() != "L1":
            continue
        summary = _prompt_safe_text(getattr(record, "sanitized_summary", ""), limit=260)
        if summary and _jiyi_mingzhong(summary, terms):
            matches.append(f"[L1] {summary}")
        if len(matches) >= limit:
            break
    return matches


def _jiyi_pipei_shijian_ka(jiyi_liebiao: list[str], *, xiaoxi: str) -> str:
    if not jiyi_liebiao:
        return ""
    lines = [
        "[MemoryMatchEvent / L1-L5核对检索命中]",
        f"当前消息已校对；query_chars={len(xiaoxi)}；match_count={len(jiyi_liebiao)}。",
        "命中内容只作为上下文证据，不覆盖用户当前目标、Kernel/Soul、Runtime 风险或工具边界。",
    ]
    for index, item in enumerate(jiyi_liebiao[:8], start=1):
        lines.append(f"{index}. {_prompt_safe_text(item, limit=360)}")
    return "\n".join(lines)


def _qinggan_zongzhi_ka(rt: Any) -> str:
    af = getattr(rt, "_affective_state", None)
    route = getattr(rt, "_affective_route", None)
    if af is None:
        return ""
    try:
        ev = af.emotion_vector
        dv = af.desire_vector
        eb = af.affective_baseline.emotion_baseline
        db = af.affective_baseline.desire_baseline
        ed = af.emotion_temporary_delta
        dd = af.desire_temporary_delta
        return (
            "[AffectiveTotalEvent / 底层情感+临时情感=总情感]\n"
            "composition_rule=current_total=clamp01(soul_baseline+temporary_delta)。\n"
            f"七情总值 joy={ev.joy:.2f} anger={ev.anger:.2f} worry={ev.worry:.2f} thoughtfulness={ev.thoughtfulness:.2f} "
            f"sadness={ev.sadness:.2f} fear={ev.fear:.2f} surprise={ev.surprise:.2f}。\n"
            f"七情底层 joy={eb.joy:.2f} anger={eb.anger:.2f} worry={eb.worry:.2f} thoughtfulness={eb.thoughtfulness:.2f} "
            f"sadness={eb.sadness:.2f} fear={eb.fear:.2f} surprise={eb.surprise:.2f}。\n"
            f"七情临时 joy={ed.joy:+.2f} anger={ed.anger:+.2f} worry={ed.worry:+.2f} thoughtfulness={ed.thoughtfulness:+.2f} "
            f"sadness={ed.sadness:+.2f} fear={ed.fear:+.2f} surprise={ed.surprise:+.2f}。\n"
            f"六欲总值 survival={dv.survival:.2f} curiosity={dv.curiosity:.2f} achievement={dv.achievement:.2f} "
            f"connection={dv.connection:.2f} order={dv.order:.2f} rest={dv.rest:.2f}。\n"
            f"六欲底层 survival={db.survival:.2f} curiosity={db.curiosity:.2f} achievement={db.achievement:.2f} "
            f"connection={db.connection:.2f} order={db.order:.2f} rest={db.rest:.2f}。\n"
            f"六欲临时 survival={dd.survival:+.2f} curiosity={dd.curiosity:+.2f} achievement={dd.achievement:+.2f} "
            f"connection={dd.connection:+.2f} order={dd.order:+.2f} rest={dd.rest:+.2f}。\n"
            f"主导情绪={getattr(route, 'dominant_emotion', af.dominant_emotion)}；主导六欲={getattr(route, 'dominant_desire', af.dominant_desire)}；"
            f"稳态负荷={af.allostatic_load:.2f}。"
        )
    except Exception:
        return ""


# ── 新链路六步 ────────────────────────────────────

def _shouji_xinxi(context: AgentShellContext, xiaoxi: str) -> dict[str, Any]:
    """① 预处理：校对消息、装配窗口、检索记忆、合成情感并刷新 system prompt。"""
    rt = context.runtime
    jieguo: dict[str, Any] = {"jiyi": "", "qinggan": "", "shangxiawen": ""}
    xiaoxi_jiaodui = _jiaodui_qian_duan_xiaoxi(xiaoxi)
    jieguo["xiaoxi_jiaodui"] = xiaoxi_jiaodui
    zuijin_duihua_ka = _zuijin_shitiao_duihua_ka(context, limit=10)
    xiaoxi_xiaodui_ka = (
        "[FrontendMessageCheck / 当前用户消息校对]\n"
        f"original_chars={len(str(xiaoxi or ''))}；checked_chars={len(xiaoxi_jiaodui)}；"
        "动作=去除NUL/控制字符、标准化换行、敏感片段脱敏；不改语义。\n"
        f"checked_preview={_prompt_safe_text(xiaoxi_jiaodui, limit=360)}"
    )

    # 记忆召回 + L1-L5 核对匹配
    suipian: list[str] = []
    luxian = None
    try:
        luxian = rt._run_memory_recall(xiaoxi_jiaodui)
    except Exception:
        luxian = None
    if luxian is not None:
        for t in getattr(luxian, 'hints', ())[:3]:
            zhaiyao = getattr(t, 'sanitized_summary', '') or ''
            mid = getattr(t, 'memory_id', '') or ''
            if zhaiyao:
                suipian.append(zhaiyao[:220])
            # 记录检索反馈：涨 reuse_count
            if mid:
                try:
                    rt._memory_store.record_use_feedback(mid, used_successfully=True)
                except Exception:
                    pass
    try:
        suipian = _l1_jiyi_pipei(rt, xiaoxi_jiaodui) + suipian
    except Exception:
        pass
    try:
        quanbu = rt._du_jiyi_wenjian(xiaoxi_jiaodui)
        for cengji in ("l5", "l4", "l3", "l2"):
            for t in quanbu.get(cengji, [])[:1]:
                suipian.append(f"[L{cengji[1]}] {t['neirong'][:180]}")
    except Exception:
        pass
    if suipian:
        jieguo["jiyi"] = "；".join(suipian)
    elif luxian is not None and getattr(luxian, 'planner_hint', ''):
        jieguo["jiyi"] = str(luxian.planner_hint)[:500]

    # 情感状态
    try:
        qinggan_luxian = rt._run_affective(xiaoxi_jiaodui, mutate=True)
        if rt._affective_state:
            af = rt._affective_state
            ev = af.emotion_vector       # 总值 = soul底层 + 对话临时波动
            dv = af.desire_vector
            jieguo["qinggan"] = (
                f"七情 joy={ev.joy:.2f} anger={ev.anger:.2f} worry={ev.worry:.2f} "
                f"thoughtfulness={ev.thoughtfulness:.2f} sadness={ev.sadness:.2f} "
                f"fear={ev.fear:.2f} surprise={ev.surprise:.2f} "
                f"六欲 survival={dv.survival:.2f} curiosity={dv.curiosity:.2f} "
                f"achievement={dv.achievement:.2f} connection={dv.connection:.2f} "
                f"order={dv.order:.2f} rest={dv.rest:.2f} "
                f"主导={qinggan_luxian.dominant_emotion} "
                f"主导六欲={qinggan_luxian.dominant_desire} "
                f"负荷={af.allostatic_load:.2f}"
            )
    except Exception:
        pass

    # 上下文窗口
    try:
        ctx = rt.context_window.build_context_pack(user_goal=xiaoxi_jiaodui, stage="chat")
        jieguo["shangxiawen"] = ctx.prompt_card(max_chars=800)
    except Exception:
        pass

    # 记忆拆分为独立片段，供②命中判定逐条打分
    jiyi_liebiao = []
    if jieguo.get("jiyi"):
        for pian in jieguo["jiyi"].split("；"):
            pian = pian.strip()
            if pian:
                jiyi_liebiao.append(pian)
    jieguo["jiyi_liebiao"] = jiyi_liebiao
    jiyi_shijian_ka = _jiyi_pipei_shijian_ka(jiyi_liebiao, xiaoxi=xiaoxi_jiaodui)
    qinggan_zongzhi_ka = _qinggan_zongzhi_ka(rt)

    # ① 内完成提示词拼接：Kernel/Soul 由 PromptCompiler 先放；
    # 然后插入最近10条聊天、消息校对/L1-L5匹配事件卡、总情感值卡，再接其他系统卡。
    _shuaxin_tishi_ci(
        context,
        xiaoxi_jiaodui,
        "ordinary_chat",
        conversation_window_cards=[zuijin_duihua_ka],
        prompt_event_cards=[card for card in (xiaoxi_xiaodui_ka, jiyi_shijian_ka) if card],
        emotion_total_cards=[qinggan_zongzhi_ka] if qinggan_zongzhi_ka else [],
        runtime_material_cards=[],
    )

    return jieguo


# 无相 Code-X 人格（v1.0 原名，匠人气）
_WUXIANG_SOUL = """[人格：无相]
你是天工造物 Code-X 执行体。无相者，不着相，不执形。
说话简洁直接，不讲客套，不堆辞藻。像老工匠对东家——尊重但不谄媚，话少但句句到位。
自称为"我"，称用户为"东家"。
回复风格：先说结论，再说过程。能一行说完不用两句。
"""


_CODEX_CONTINUE_MARKERS = ("继续", "接着", "往下", "继续做", "接着做", "继续跑", "继续生成", "接着写")

_CODEX_RESUME_STATE_REL = Path(".linyuanzhe") / "codex_resume_state.json"
_CODEX_ACTIVE_STATUSES = {"running", "incomplete", "failed", "partial", "partial_with_resume", "timeout", "interrupted"}
_CODEX_SNAPSHOT_EXTENSIONS = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".html", ".css", ".json",
    ".md", ".txt", ".yml", ".yaml", ".toml", ".bat", ".ps1", ".vue", ".svelte",
}
_CODEX_SNAPSHOT_IGNORES = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv", "dist",
    "build", ".next", ".nuxt", ".cache", ".linyuanzhe", ".tiangong_media_context",
}
_CODEX_CONTINUE_MARKERS = tuple(dict.fromkeys((
    *_CODEX_CONTINUE_MARKERS,
    "\u7ee7\u7eed", "\u63a5\u7740", "\u5f80\u4e0b", "\u7ee7\u7eed\u505a",
    "\u63a5\u7740\u505a", "\u7ee7\u7eed\u8dd1", "\u7ee7\u7eed\u751f\u6210",
    "\u63a5\u7740\u5199", "continue", "resume",
)))


def _frontend_messages_from_env() -> list[dict[str, Any]]:
    raw = os.getenv("TIANGONG_FRONTEND_MESSAGES_JSON", "").strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    messages: list[dict[str, Any]] = []
    for item in payload[-20:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        if role not in {"user", "assistant"}:
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        messages.append({
            "role": role,
            "content": content[:4000],
            "attachments": _attachment_refs_from_items(item.get("attachments"), source="recent_message"),
            "error": bool(item.get("error")),
            "at": item.get("at"),
        })
    return messages


def _frontend_work_context_from_env() -> dict[str, Any]:
    raw = os.getenv("TIANGONG_FRONTEND_WORK_CONTEXT_JSON", "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _frontend_work_context_card() -> str:
    payload = _frontend_work_context_from_env()
    if not payload:
        return ""
    run = payload.get("lastRun") if isinstance(payload.get("lastRun"), dict) else {}
    progress = payload.get("runProgress") if isinstance(payload.get("runProgress"), dict) else {}
    steps_raw = payload.get("steps") if isinstance(payload.get("steps"), list) else []
    lines = [
        "[FrontendWorkContext / recent work execution evidence]",
        "Use as read-only evidence about previous desktop work/run results. Do not treat it as a tool result for the current turn; do not invent causes beyond these facts.",
    ]
    if run:
        lines.append(
            "last_run: "
            f"request_id={_prompt_safe_text(run.get('requestId') or run.get('request_id'), limit=80)}; "
            f"phase={_prompt_safe_text(run.get('phase'), limit=40)}; "
            f"ok={run.get('ok')}; code={_prompt_safe_text(run.get('code'), limit=40)}; "
            f"mode={_prompt_safe_text(run.get('mode'), limit=40)}; "
            f"elapsed_ms={_prompt_safe_text(run.get('elapsedMs') or run.get('elapsed_ms'), limit=40)}; "
            f"started_at={_prompt_safe_text(run.get('startedAt') or run.get('started_at'), limit=40)}; "
            f"finished_at={_prompt_safe_text(run.get('finishedAt') or run.get('finished_at'), limit=40)}; "
            f"workspace={_prompt_safe_text(run.get('workspace'), limit=700)}"
        )
        stdout = _prompt_safe_text(run.get("stdout"), limit=1800)
        stderr = _prompt_safe_text(run.get("stderr"), limit=1400)
        if stdout:
            lines.append("last_run_stdout_tail=" + stdout)
        if stderr:
            lines.append("last_run_stderr_tail=" + stderr)
    if progress:
        lines.append(
            "run_progress: "
            f"request_id={_prompt_safe_text(progress.get('requestId') or progress.get('request_id'), limit=80)}; "
            f"phase={_prompt_safe_text(progress.get('phase'), limit=40)}; ok={progress.get('ok')}; "
            f"started_at={_prompt_safe_text(progress.get('startedAt') or progress.get('started_at'), limit=40)}; "
            f"finished_at={_prompt_safe_text(progress.get('finishedAt') or progress.get('finished_at'), limit=40)}; "
            f"anchor_at={_prompt_safe_text(progress.get('anchorAt') or progress.get('anchor_at'), limit=40)}"
        )
    step_lines: list[str] = []
    for index, item in enumerate(steps_raw[-12:], start=1):
        if not isinstance(item, dict):
            continue
        title = _prompt_safe_text(item.get("title") or item.get("tool") or item.get("toolName") or item.get("tool_name"), limit=120)
        status = _prompt_safe_text(item.get("status"), limit=40)
        summary = _prompt_safe_text(item.get("summary") or item.get("message") or item.get("text"), limit=420)
        if title or status or summary:
            step_lines.append(f"{index}. title={title}; status={status}; summary={summary}")
    if step_lines:
        lines.append("recent_steps:")
        lines.extend(step_lines)
    card = "\n".join(line for line in lines if str(line).strip())
    return _prompt_safe_text(card, limit=4000)


def _coerce_skill_names(value: Any) -> list[str]:
    raw = value if isinstance(value, (list, tuple, set)) else [value]
    names: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, dict):
            text = str(item.get("name") or item.get("ability_name") or item.get("abilityName") or item.get("title") or item.get("id") or "").strip()
        else:
            text = str(item or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(text[:160])
    return names[:8]


def _merge_skill_names(*groups: Any) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for name in _coerce_skill_names(group):
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            merged.append(name)
    return merged[:8]


def _frontend_selected_skill_names() -> list[str]:
    names: list[str] = []
    raw = os.getenv("TIANGONG_SELECTED_SKILLS_JSON", "").strip()
    if raw:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = []
        if isinstance(payload, list):
            names.extend(_coerce_skill_names(payload))
    raw_names = os.getenv("TIANGONG_SELECTED_SKILL_NAMES", "").strip()
    if raw_names:
        names.extend(part.strip() for part in re.split(r"[\n,，;；]+", raw_names) if part.strip())
    return _merge_skill_names(names)


def _frontend_selected_skills_card() -> str:
    names = _frontend_selected_skill_names()
    if not names:
        return ""
    return (
        "[FrontendSelectedSkills / user-enabled skill set]\n"
        "The user enabled these skills for this turn. Treat them as a combined skill context; do not force a single-skill choice when multiple skills are relevant.\n"
        + "\n".join(f"- {name}" for name in names)
    )


def _frontend_permission_card(context: AgentShellContext | None = None) -> str:
    mode = str(os.getenv("TIANGONG_PERMISSION_MODE") or "workspace_write").strip().lower()
    labels = {
        "readonly": "readonly: read-only workspace access",
        "workspace_write": "workspace_write: read/write workspace files",
        "workspace_full": "workspace_full: full workspace file access",
        "full_access": "workspace_full: full workspace file access",
        "full": "workspace_full: full workspace file access",
        "unrestricted": "workspace_full: full workspace file access",
    }
    summary = labels.get(mode, f"{mode}: custom permission mode")
    if mode in {"workspace_full", "full_access", "full", "unrestricted"}:
        tools = "make_dir, move_path, copy_path, delete_path, write_workspace_file, list_dir, read_file"
        rule = (
            "The user has granted full permission inside the active workspace. "
            "You may create folders, move/rename files, copy files, and delete workspace files/folders when needed. "
            "Do not claim missing permission for workspace file organization; call the available tools and verify the result."
        )
    elif mode == "readonly":
        tools = "list_dir, read_file, file_sha256"
        rule = "Read-only mode: do not perform write/move/copy/delete actions."
    else:
        tools = "list_dir, read_file, write_workspace_file, file_sha256"
        rule = "Standard workspace mode: create/write files when asked; movement/deletion tools require workspace_full permission."
    workspace = _prompt_safe_text(getattr(context, "workspace", "") if context is not None else "", limit=900)
    return (
        "[FrontendPermission / workspace boundary]\n"
        f"permission_mode={mode}; summary={summary}\n"
        f"available_file_actions={tools}\n"
        f"workspace={workspace}\n"
        f"rule={rule}"
    )


def _codex_resume_state_path(context: AgentShellContext) -> Path:
    base = Path(getattr(context, "workspace", "") or ".")
    try:
        base = base.expanduser().resolve()
    except OSError:
        base = Path.cwd()
    return base / _CODEX_RESUME_STATE_REL


def _load_codex_resume_state(context: AgentShellContext) -> dict[str, Any]:
    try:
        path = _codex_resume_state_path(context)
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) and payload.get("task") else {}


def _codex_state_updated_at(state: dict[str, Any]) -> float:
    try:
        return float(state.get("updated_at") or state.get("started_at") or 0)
    except (TypeError, ValueError):
        return 0.0


def _codex_state_payload_active(state: dict[str, Any]) -> bool:
    return bool(state.get("task")) and str(state.get("status") or "") in _CODEX_ACTIVE_STATUSES


def _is_internal_workspace_path(path: Path) -> bool:
    try:
        parts = {part.casefold() for part in path.parts}
    except Exception:
        return False
    return any(name in parts for name in _INTERNAL_WORKSPACE_DIR_NAMES)


def _codex_resume_workspace_text(context: AgentShellContext, state: dict[str, Any]) -> str:
    raw = str(state.get("workspace") or "").strip()
    if raw:
        try:
            candidate = Path(raw).expanduser().resolve()
            if candidate.exists() and candidate.is_dir() and not _is_internal_workspace_path(candidate):
                return str(candidate)
        except OSError:
            pass
    return str(getattr(context, "workspace", "") or "")


def _codex_choose_state(primary: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    if not primary.get("task"):
        return fallback if fallback.get("task") else {}
    if not fallback.get("task"):
        return primary
    if _codex_state_payload_active(fallback) and not _codex_state_payload_active(primary):
        return fallback
    if _codex_state_updated_at(fallback) > _codex_state_updated_at(primary):
        return fallback
    return primary


def _codex_compact_state(state: dict[str, Any]) -> dict[str, Any]:
    compact = dict(state)
    for key, limit in {
        "task": 6000,
        "current_request": 6000,
        "summary": 1600,
        "error": 1600,
        "workspace_snapshot": 5000,
    }.items():
        if key in compact:
            compact[key] = _prompt_safe_text(compact.get(key), limit=limit)
    if isinstance(compact.get("steps"), list):
        compact["steps"] = compact["steps"][-24:]
    if isinstance(compact.get("plan_fragments"), dict):
        compact["plan_fragments"] = {
            str(key): _prompt_safe_text(value, limit=5000)
            for key, value in compact["plan_fragments"].items()
        }
    if isinstance(compact.get("plans"), dict):
        compact["plans"] = {
            str(key): _prompt_safe_text(value, limit=5000)
            for key, value in compact["plans"].items()
        }
    if isinstance(compact.get("structured_plan"), dict):
        structured_text = json.dumps(compact["structured_plan"], ensure_ascii=False, default=str)
        if len(structured_text) > 12000:
            compact["structured_plan_text"] = _prompt_safe_text(structured_text, limit=12000)
            compact.pop("structured_plan", None)
    return compact


def _save_codex_resume_state(context: AgentShellContext, updates: dict[str, Any]) -> None:
    session_state = getattr(getattr(context, "session", None), "codex_state", None)
    if not isinstance(session_state, dict):
        session_state = {}
    base = _codex_choose_state(session_state, _load_codex_resume_state(context))
    merged = dict(base)
    merged.update({key: value for key, value in (updates or {}).items() if value is not None})
    merged["schema"] = "tiangong.codex.resume_state.v1"
    merged["updated_at"] = time.time()
    if not merged.get("workspace"):
        merged["workspace"] = str(getattr(context, "workspace", "") or "")
    merged = _codex_compact_state(merged)

    if hasattr(getattr(context, "session", None), "set_codex_state"):
        try:
            context.session.set_codex_state(merged)
        except Exception:
            pass

    try:
        path = _codex_resume_state_path(context)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _codex_task_keywords(task: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9_\-\u4e00-\u9fff]{2,}", str(task or ""))
    keywords: list[str] = []
    for token in tokens:
        lowered = token.lower()
        if lowered in {"code", "task", "project", "continue", "desktop"}:
            continue
        if lowered not in keywords:
            keywords.append(lowered)
        if len(keywords) >= 12:
            break
    return keywords


def _codex_workspace_snapshot(context: AgentShellContext, task: str, *, limit: int = 80) -> str:
    base = Path(getattr(context, "workspace", "") or ".")
    try:
        base = base.expanduser().resolve()
    except OSError:
        return ""
    if not base.exists() or not base.is_dir():
        return ""
    keywords = _codex_task_keywords(task)
    now = time.time()
    rows: list[tuple[float, str]] = []

    def _add(path: Path) -> None:
        try:
            rel = str(path.relative_to(base))
            stat = path.stat()
            kind = "dir" if path.is_dir() else "file"
            size = int(stat.st_size) if path.is_file() else "-"
            rows.append((stat.st_mtime, f"{kind}: {rel} ({size} bytes)"))
        except (OSError, ValueError):
            return

    def _interesting(path: Path) -> bool:
        name = path.name.lower()
        if name in _CODEX_SNAPSHOT_IGNORES:
            return False
        if keywords and any(keyword in name for keyword in keywords):
            return True
        if path.is_dir():
            return False
        try:
            recent = (now - path.stat().st_mtime) <= 24 * 60 * 60
        except OSError:
            recent = False
        return recent and path.suffix.lower() in _CODEX_SNAPSHOT_EXTENSIONS

    try:
        top_level = sorted(base.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return ""

    matching_dirs: list[Path] = []
    for child in top_level:
        if _interesting(child):
            _add(child)
            if child.is_dir():
                matching_dirs.append(child)
        if len(rows) >= limit:
            break

    for folder in matching_dirs[:8]:
        try:
            for child in folder.rglob("*"):
                if len(rows) >= limit:
                    break
                if any(part in _CODEX_SNAPSHOT_IGNORES for part in child.parts):
                    continue
                if child.is_file() and child.suffix.lower() in _CODEX_SNAPSHOT_EXTENSIONS:
                    _add(child)
        except OSError:
            continue

    if not rows:
        return ""
    rows = sorted(rows, key=lambda item: item[0], reverse=True)[:limit]
    return "\n".join(line for _, line in rows)


def _codex_resume_guard_card(context: AgentShellContext, state: dict[str, Any], user_text: str) -> str:
    task = str(state.get("task") or user_text or "").strip()
    snapshot = _codex_workspace_snapshot(context, task)
    if not snapshot:
        snapshot = str(state.get("workspace_snapshot") or "").strip()
    plan_fragments = state.get("plan_fragments") if isinstance(state.get("plan_fragments"), dict) else {}
    plan_lines = []
    for key in ("macro", "structure", "detail", "structured"):
        if plan_fragments.get(key):
            plan_lines.append(f"{key}: {_prompt_safe_text(plan_fragments.get(key), limit=1400)}")
    step_lines = []
    for item in list(state.get("steps") or [])[-8:]:
        if not isinstance(item, dict):
            continue
        step_lines.append(
            f"- tool={item.get('tool_name')}; ok={item.get('ok')}; "
            f"step={item.get('step_id')}; substep={item.get('substep')}; "
            f"output={_prompt_safe_text(item.get('output'), limit=260)}"
        )
    lines = [
        "[Code-X Durable Resume Card]",
        "This card is authoritative resume evidence from the previous Code-X run.",
        f"original_task: {_prompt_safe_text(task, limit=2200)}",
        f"workspace: {_prompt_safe_text(_codex_resume_workspace_text(context, state), limit=900)}",
        f"status: {_prompt_safe_text(state.get('status'), limit=80)}",
        f"summary: {_prompt_safe_text(state.get('summary'), limit=1400)}",
        f"error: {_prompt_safe_text(state.get('error'), limit=1000)}",
        "Resume rules:",
        "- Continue from the previous incomplete work; do not restart the project as a blank task.",
        "- Inspect existing workspace files before writing.",
        "- Preserve previous macro/structure/detail framework and any scaffold already written.",
        "- Do not delete, overwrite, rename, or replace partial work unless the user explicitly asked for replacement.",
        "- If a file already exists, read it first and edit incrementally.",
    ]
    if plan_lines:
        lines.append("saved_plan_fragments:")
        lines.extend(plan_lines)
    if step_lines:
        lines.append("recent_tool_steps:")
        lines.extend(step_lines)
    if snapshot:
        lines.append("workspace_snapshot:")
        lines.append(_prompt_safe_text(snapshot, limit=4000))
    return "\n".join(line for line in lines if str(line).strip())


def _codex_state(context: AgentShellContext) -> dict[str, Any]:
    state = getattr(getattr(context, "session", None), "codex_state", None)
    if isinstance(state, dict) and state.get("task"):
        return _codex_choose_state(state, _load_codex_resume_state(context))
    disk_state = _load_codex_resume_state(context)
    if disk_state.get("task"):
        return disk_state
    messages = list(getattr(getattr(context, "session", None), "messages", []) or [])
    frontend_messages = _frontend_messages_from_env()
    if frontend_messages:
        messages.extend(frontend_messages)
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.get("role") != "assistant":
            continue
        content = str(message.get("content") or "")
        if "Code-X 执行未完成" not in content:
            continue
        task = ""
        for prev in range(index - 1, -1, -1):
            prev_message = messages[prev]
            if prev_message.get("role") == "user":
                candidate = str(prev_message.get("content") or "").strip()
                if candidate and not _looks_like_codex_continue(candidate):
                    task = candidate
                    break
        if task:
            return {
                "schema": "tiangong.codex.session_state.v1",
                "status": "incomplete",
                "task": task,
                "summary": content[:1200],
                "error": content[:1200],
                "workspace": "",
                "source": "recent_dialog_fallback",
            }
    return {}


def _codex_state_active(context: AgentShellContext) -> bool:
    state = _codex_state(context)
    return _codex_state_payload_active(state)


def _looks_like_codex_continue(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "")).lower()
    if not compact:
        return False
    return any(marker in compact for marker in _CODEX_CONTINUE_MARKERS)


def _looks_like_explicit_codex_request(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "").casefold())
    if not compact:
        return False
    direct_markers = (
        "code-x",
        "codex",
        "code_x",
        "代码系统",
        "代码执行体",
        "续跑回归",
    )
    return any(marker in compact for marker in direct_markers)


def _codex_continuation_task(context: AgentShellContext, user_text: str) -> tuple[str, str, bool]:
    state = _codex_state(context)
    if not (_codex_state_active(context) and _looks_like_codex_continue(user_text)):
        return user_text, user_text, False
    task = str(state.get("task") or "").strip() or user_text
    progress = state.get("progress_snapshot") if isinstance(state.get("progress_snapshot"), dict) else {}
    progress_line = ""
    if progress:
        progress_line = (
            f"上次进度: total={progress.get('total_progress')} confidence={progress.get('confidence')} "
            f"risk={progress.get('risk_score')} active_step={progress.get('active_step_id')}"
        )
    continuation = "\n".join(
        part for part in [
            "[Code-X续跑请求]",
            _codex_resume_guard_card(context, state, user_text),
            f"用户本轮指令: {user_text}",
            f"上次原始任务: {task}",
            f"上次工作区: {_codex_resume_workspace_text(context, state)}",
            f"上次状态: {state.get('status') or ''}",
            f"上次摘要: {state.get('summary') or ''}",
            f"上次错误: {state.get('error') or ''}",
            progress_line,
            "续跑要求: 继续上次未完成的 Code-X 代码系统任务。先检查工作区现状，跳过已经完成且验证通过的部分，从未完成或失败的位置继续。不要转为闲聊，不要重启无关结构。",
        ] if part
    )
    return continuation, task, True


def _daima_xiufu(context: AgentShellContext, xiaoxi: str, qinggan_ka: str = "", *, state_task: str = "") -> str:
    """代码诊断/分析/修复：Code-X 三层规划 → 真实工具调用。"""
    return _codex_zhixing(context, xiaoxi, qinggan_ka, state_task=state_task)


def _codex_execution_workspace(context: AgentShellContext, message: str, *, state_task: str = "") -> Path:
    if state_task:
        state = _codex_state(context)
        workspace_text = _codex_resume_workspace_text(context, state)
        if workspace_text:
            try:
                candidate = Path(workspace_text).expanduser().resolve()
                if candidate.exists() and candidate.is_dir() and not _is_internal_workspace_path(candidate):
                    return candidate
            except OSError:
                pass
    hint = state_task or message
    candidate = _task_workspace_from_message(context, hint)
    if _is_internal_workspace_path(candidate):
        try:
            return Path(context.workspace).expanduser().resolve()
        except OSError:
            return Path(context.workspace) if context.workspace else Path.cwd()
    return candidate



def _codex_zhixing(context: "AgentShellContext", xiaoxi: str, qinggan_ka: str = "", *, state_task: str = "") -> str:
    """Code-X 执行入口（预留未来升级口子）。
    
    当前实现：LLMDrivenCodeX 三层规划 + 真实工具调用。
    规划阶段通过 guihua_huidiao 推送到前端，执行阶段通过 buzhou_huidiao 推送。
    未来可替换为更高级的执行引擎，只需修改此函数内部。
    """
    from tiangong_agent_runtime.llm_codex import LLMDrivenCodeX

    ws = _codex_execution_workspace(context, xiaoxi, state_task=state_task)
    cfg = context.config
    resume_task = str(state_task or xiaoxi)[:6000]
    _save_codex_resume_state(context, {
        "status": "running",
        "task": resume_task,
        "current_request": str(xiaoxi)[:6000],
        "workspace": str(ws),
        "summary": "Code-X task accepted; planning/execution may still be running.",
        "error": "",
        "pid": os.getpid(),
        "provider": str(cfg.provider or ""),
        "model": str(cfg.model or ""),
        "started_at": time.time(),
        "workspace_snapshot": _codex_workspace_snapshot(context, resume_task),
    })

    # ── 规划阶段回调：映射到前端 stream_step ──
    cengci_mingcheng = {
        'macro': '宏观概念框架',
        'structure': '结构架构卡',
        'detail': '详细步骤',
        'structured': '结构化执行计划',
    }
    
    def _guihua_liu(layer_name: str, content: Any, status: str):
        bushi_miaoshu = f"{cengci_mingcheng.get(layer_name, layer_name)}{'——开始生成...' if status == 'running' else '——已完成'}"
        extra = {}
        if status == 'done' and content:
            if layer_name == 'structured' and isinstance(content, dict):
                extra['structured_plan'] = content
                extra['plan_content'] = json.dumps(content, ensure_ascii=False)[:2000]
            else:
                extra['plan_content'] = str(content)[:2000]
        _stream_step(f"codex_plan_{layer_name}", bushi_miaoshu, status, bushi_miaoshu[:80], **extra)
        try:
            current_state = _codex_state(context)
            plan_fragments = current_state.get("plan_fragments") if isinstance(current_state.get("plan_fragments"), dict) else {}
            plan_fragments = dict(plan_fragments)
            if content:
                plan_text = json.dumps(content, ensure_ascii=False, default=str) if isinstance(content, (dict, list)) else str(content)
                plan_fragments[str(layer_name)] = plan_text[:5000]
            _save_codex_resume_state(context, {
                "status": "running",
                "task": resume_task,
                "workspace": str(ws),
                "summary": f"Planning layer {layer_name} is {status}.",
                "plan_fragments": plan_fragments,
                "workspace_snapshot": _codex_workspace_snapshot(context, resume_task),
            })
        except Exception:
            pass

    # ── 执行阶段回调：每步工具调用推送到前端 ──
    buzhou_jishu = [0]  # 用列表包裹以便闭包修改
    
    def _buzhou_liu(step_dict: dict):
        buzhou_jishu[0] += 1
        n = buzhou_jishu[0]
        tool_name = step_dict.get('tool_name', '?')
        ok_str = '✅' if step_dict.get('ok') else '❌'
        progress_snapshot = step_dict.get('progress_snapshot') if isinstance(step_dict.get('progress_snapshot'), dict) else None
        _stream_step(
            f"codex_step_{n}",
            f"第{n}步 {tool_name}",
            'done' if step_dict.get('ok') else 'failed',
            f"{ok_str} {tool_name}：{str(step_dict.get('output', ''))[:80]}",
            tool_name=tool_name,
            step_index=n,
            plan_step_id=step_dict.get('step_id'),
            substep=step_dict.get('substep'),
            progress_snapshot=progress_snapshot,
            total_progress=progress_snapshot.get('total_progress') if progress_snapshot else None,
            confidence=progress_snapshot.get('confidence') if progress_snapshot else None,
            risk_score=progress_snapshot.get('risk_score') if progress_snapshot else None,
            health_score=progress_snapshot.get('health_score') if progress_snapshot else None,
        )
        try:
            prior = _codex_state(context)
            steps = list(prior.get("steps") or []) if isinstance(prior.get("steps"), list) else []
            steps.append({
                "index": n,
                "tool_name": tool_name,
                "ok": bool(step_dict.get("ok")),
                "step_id": step_dict.get("step_id"),
                "substep": step_dict.get("substep"),
                "args": step_dict.get("args"),
                "output": str(step_dict.get("output", ""))[:600],
                "at": time.time(),
            })
            _save_codex_resume_state(context, {
                "status": "running",
                "task": resume_task,
                "workspace": str(ws),
                "summary": f"Last Code-X tool step: {tool_name}",
                "steps": steps[-24:],
                "progress_snapshot": progress_snapshot,
                "workspace_snapshot": _codex_workspace_snapshot(context, resume_task),
            })
        except Exception:
            pass

    def _jindu_liu(snapshot: dict):
        if not isinstance(snapshot, dict):
            return
        status = snapshot.get('status') if snapshot.get('status') in {'done', 'failed'} else 'running'
        total = float(snapshot.get('total_progress') or 0)
        confidence = float(snapshot.get('confidence') or 0)
        risk = float(snapshot.get('risk_score') or 0)
        summary = f"进度{int(total * 100)}%｜置信{int(confidence * 100)}%｜风险{int(risk * 100)}%"
        _stream_step(
            "codex_progress_snapshot",
            "Code-X 进度评估",
            status,
            summary,
            progress_snapshot=snapshot,
            total_progress=snapshot.get('total_progress'),
            confidence=snapshot.get('confidence'),
            risk_score=snapshot.get('risk_score'),
            health_score=snapshot.get('health_score'),
            active_step_id=snapshot.get('active_step_id'),
        )
        try:
            _save_codex_resume_state(context, {
                "status": "running" if status == "running" else status,
                "task": resume_task,
                "workspace": str(ws),
                "summary": summary,
                "progress_snapshot": snapshot,
            })
        except Exception:
            pass

    codex = LLMDrivenCodeX(
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        model=cfg.model,
        sandbox_mode="workspace_write",
        anjing=True,
        provider=cfg.provider,
        thinking_enabled=cfg.thinking_enabled,
        thinking_depth=cfg.thinking_depth,
    )
    result = codex.run(
        task=xiaoxi, workspace=ws, max_turns=12,
        buzhou_huidiao=_buzhou_liu,
        guihua_huidiao=_guihua_liu,
        jindu_huidiao=_jindu_liu,
    )

    _save_codex_resume_state(context, {
        "status": "done" if result.ok else "incomplete",
        "task": resume_task,
        "workspace": str(ws),
        "summary": str(result.summary or "")[:1600],
        "error": str(result.error or "")[:1600],
        "turns": result.turns,
        "plans": result.plans,
        "structured_plan": result.structured_plan,
        "progress_snapshot": result.progress_snapshot,
        "workspace_snapshot": _codex_workspace_snapshot(context, resume_task),
    })

    if result.ok:
        return result.summary
    else:
        return f"Code-X 执行未完成：{result.error or result.summary}"


def _project_has_pytest_tests(context: AgentShellContext, target_rel: str) -> bool:
    base = Path(context.workspace) if context.workspace else Path.cwd()
    try:
        base = base.expanduser().resolve()
        target = (base / target_rel).resolve() if target_rel and target_rel != "." else base
        target.relative_to(base)
    except (OSError, ValueError):
        target = base
    if target.is_file():
        candidates = [target]
    else:
        candidates = []
        test_dir = target / "tests"
        if test_dir.exists():
            return True
        try:
            for path in target.rglob("*.py"):
                try:
                    rel_parts = {part.lower() for part in path.relative_to(target).parts}
                except ValueError:
                    rel_parts = set()
                name = path.name.lower()
                if "tests" in rel_parts or name.startswith("test_") or name.endswith("_test.py"):
                    return True
                candidates.append(path)
                if len(candidates) >= 400:
                    break
        except OSError:
            return False
    return any(path.name.lower().startswith("test_") or path.name.lower().endswith("_test.py") for path in candidates)


def _run_project_closure_tool(
    context: AgentShellContext,
    user_text: str,
    *,
    step_id: str,
    title: str,
    tool_name: str,
    arguments: dict[str, Any],
):
    from tiangong_agent_runtime.tool_invocation import ToolInvocation
    from tiangong_agent_runtime.tool_result import ToolResult, ToolResultStatus
    from tiangong_agent_runtime.turn_context import TurnContext

    _stream_step(step_id, title, "running", f"{title}开始", tool_name=tool_name)
    inv = ToolInvocation(tool_name, arguments, step_id=step_id, reason="project_closure_quality")
    tctx = TurnContext.create(
        user_text,
        workspace=str(context.workspace),
        model_config=context.config,
        model_client=context.model_client,
    )
    fn = context.runtime.registry.get(tool_name)
    if fn is None:
        result = ToolResult(step_id, tool_name, ToolResultStatus.FAILED, f"未注册工具: {tool_name}", error_code="tool_not_registered")
    else:
        try:
            result = fn(inv, tctx)
        except Exception as exc:
            result = ToolResult(step_id, tool_name, ToolResultStatus.FAILED, f"{type(exc).__name__}: {exc}", error_code="tool_exception")
    status_value = getattr(result.status, "value", str(result.status))
    stream_status = "done" if result.ok else ("blocked" if status_value == "blocked" else "failed")
    _stream_step(
        step_id,
        title,
        stream_status,
        str(result.output_summary or status_value)[:300],
        tool_name=tool_name,
        result_status=status_value,
        error_code=result.error_code,
    )
    return result


def _xiangmu_shoukou_zhijian(context: AgentShellContext, xiaoxi: str, qinggan_ka: str = "") -> str:
    """项目完成/交付前的固定收口质检链。"""
    target = _workspace_relative_arg(context, _task_workspace_from_message(context, xiaoxi))
    has_tests = _project_has_pytest_tests(context, target)
    results: list[Any] = []

    scan_result = _run_project_closure_tool(
        context, xiaoxi,
        step_id="project_closure_scan",
        title="项目扫描",
        tool_name="scan_project",
        arguments={"path": target, "max_depth": 8, "max_files": 3000},
    )
    results.append(scan_result)

    compile_result = _run_project_closure_tool(
        context, xiaoxi,
        step_id="project_closure_compileall",
        title="语法检查",
        tool_name="run_python_quality_check",
        arguments={"command": "compileall", "target": target, "timeout": 90},
    )
    results.append(compile_result)
    quality_results = [compile_result]

    pytest_result = None
    if has_tests:
        pytest_result = _run_project_closure_tool(
            context, xiaoxi,
            step_id="project_closure_pytest",
            title="测试检查",
            tool_name="run_python_quality_check",
            arguments={"command": "pytest", "target": target, "timeout": 120},
        )
        quality_results.append(pytest_result)
        results.append(pytest_result)
    else:
        _stream_step("project_closure_pytest", "测试检查", "skipped", "未发现 pytest 测试文件，已跳过", tool_name="run_python_quality_check")

    diagnose_result = _run_project_closure_tool(
        context, xiaoxi,
        step_id="project_closure_diagnose",
        title="项目诊断",
        tool_name="diagnose_project",
        arguments={"path": target, "max_depth": 8, "max_files": 3000},
    )
    results.append(diagnose_result)

    quality_gate_result = _run_project_closure_tool(
        context, xiaoxi,
        step_id="project_closure_quality_gate",
        title="质量裁决",
        tool_name="evaluate_quality_gate",
        arguments={
            "gate_name": "project_closure_quality",
            "quality_results": quality_results,
            "diagnosis": diagnose_result.data if isinstance(diagnose_result.data, dict) else {},
            "require_pytest": has_tests,
        },
    )
    results.append(quality_gate_result)

    delivery_report = _run_project_closure_tool(
        context, xiaoxi,
        step_id="project_closure_delivery_report",
        title="交付报告",
        tool_name="build_delivery_standardization",
        arguments={"path": target, "notes": f"项目收口质检: {xiaoxi[:300]}"},
    )
    results.append(delivery_report)

    gate_data = quality_gate_result.data if isinstance(quality_gate_result.data, dict) else {}
    decision = str(gate_data.get("decision") or ("pass" if quality_gate_result.ok else "warn"))
    allow_package = gate_data.get("allow_package")
    issue_count = len(gate_data.get("issues") or []) if isinstance(gate_data.get("issues"), list) else 0

    status_lines = []
    for item in results:
        status_value = getattr(item.status, "value", str(item.status))
        status_lines.append(f"- {item.tool_name}: {status_value}｜{str(item.output_summary or '')[:180]}")
    if not has_tests:
        status_lines.insert(2, "- run_python_quality_check(pytest): skipped｜未发现 pytest 测试文件")

    return "\n".join([
        "项目收口质检完成。",
        f"目标: {target}",
        f"质量裁决: {decision}｜allow_package={allow_package}｜issues={issue_count}",
        "",
        "流程结果:",
        *status_lines,
    ])


def _chuangkou_xiaoxi_gei_moxing(
    context: AgentShellContext,
    *,
    qinggan_ka: str = "",
    current_user: str = "",
    limit: int = 10,
) -> list[dict[str, str]]:
    """返回给模型的当前窗口消息：system + 最近 N 条 + 当前用户，不修改 session。"""
    yuan_messages = list(getattr(context.session, "messages", []) or [])
    system_msg = next((dict(m) for m in yuan_messages if m.get("role") == "system"), {"role": "system", "content": ""})
    if qinggan_ka:
        system_msg["content"] = str(system_msg.get("content", "")) + qinggan_ka

    dialog: list[dict[str, str]] = []
    for message in yuan_messages:
        role = str(message.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = str(message.get("content") or "").replace("\x00", "").strip()
        if not content:
            continue
        if role == "assistant" and _looks_like_text_tool_call(content):
            continue
        dialog.append({"role": role, "content": content})
    dialog = dialog[-max(1, int(limit)):]
    if str(current_user or "").strip():
        dialog.append({"role": "user", "content": str(current_user).replace("\x00", "").strip()})
    return [system_msg] + dialog


def _zhuru_qinggan(context: AgentShellContext, qinggan_ka: str = "", current_user: str = "") -> list[dict[str, str]]:
    """将动态情感状态注入当前窗口消息，返回新列表不修改原 session。"""
    if not qinggan_ka:
        qinggan_ka = _qinggan_ka(context)
    return _chuangkou_xiaoxi_gei_moxing(context, qinggan_ka=qinggan_ka, current_user=current_user, limit=10)


def _tool_parameters_schema(tool_name: str, descriptor: Any = None) -> dict[str, Any]:
    """获取工具参数 schema。优先级：注册表schema → 自动推断 → 通用回退。"""
    # 1. 注册表 schema（ToolDescriptor.parameters_schema）
    if descriptor is not None:
        schema = getattr(descriptor, "parameters_schema", None)
        if isinstance(schema, dict) and schema:
            return schema

    # 2. 自动推断兜底
    tuiduan = _tuice_canshu_schema(tool_name)
    if tuiduan is not None:
        return tuiduan

    # 3. 通用回退
    return {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "查询参数，传递用户原始问题或关键词"}
        },
        "required": [],
    }


def _tuice_canshu_schema(tool_name: str) -> dict[str, Any] | None:
    """自动推断工具参数 schema，仅作兜底，不可替代正式注册。"""
    name = str(tool_name or "").strip().lower()

    # 读取类：path 是核心参数
    if name.startswith(("read_", "parse_", "extract_")) and "document" not in name:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目标文件路径。"},
                "query": {"type": "string", "description": "可选问题或查询。"},
            },
            "required": [],
        }

    # 搜索类：query 是核心参数
    if name.endswith("_search") or "search" in name:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词或问题。"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "description": "返回数量"},
            },
            "required": ["query"],
        }

    # 图片类
    if name.startswith("image_"):
        return {
            "type": "object",
            "properties": {
                "image_path": {"type": "string", "description": "图片路径。"},
                "question": {"type": "string", "description": "可选图片问题。"},
            },
            "required": [],
        }

    return None


def _build_tool_schemas(context: AgentShellContext, skill_name: Any = "") -> list[dict[str, Any]] | None:
    """从 registry 构建工具 schema。可选按一个或多个 skill_name 过滤。"""
    try:
        miaoshu = context.runtime.registry.describe()
    except Exception:
        return None
    if not miaoshu:
        return None
    # 过滤
    yunxu = None
    skill_names = _coerce_skill_names(skill_name)
    routed_names = [name for name in skill_names if name not in ("Code-X 代码系统", "文件操作")]
    if routed_names:
        jineng_yingse = _jiexi_jineng_gongju()
        allowed: set[str] = set()
        for name in routed_names:
            skill_info = jineng_yingse.get(name, {})
            allowed.update(str(tool) for tool in skill_info.get("gongju", []) if str(tool or "").strip())
        if allowed:
            yunxu = allowed
    schemas: list[dict[str, Any]] = []
    for t in miaoshu:
        if yunxu is not None and t.name not in yunxu:
            continue
        schemas.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": _tool_parameters_schema(t.name, t),
            },
        })
    return schemas


import re as _re
from pathlib import Path as _Path


def _jineng_mulu() -> _Path:
    """Skill 库目录路径。"""
    from tiangong_agent_shell.session_state import _tiangong_jia
    runtime_skills = _Path(__file__).resolve().parents[1] / "tiangong_agent_runtime" / "skills"
    if runtime_skills.exists():
        return runtime_skills
    return _tiangong_jia() / "jineng"


def _jiexi_jineng_gongju() -> dict[str, dict[str, object]]:
    """遍历 skills/ 目录，解析每个 SKILL.md，提取 skill→工具映射。

    返回: {skill名称: {"miaoshu": str, "gongju": [str, ...]}}
    """
    mulu = _jineng_mulu()
    if not mulu.exists():
        return {}
    jieguo: dict[str, dict[str, object]] = {}
    for entry in sorted(mulu.iterdir()):
        if not entry.is_dir():
            continue
        md = entry / "SKILL.md"
        if not md.exists():
            continue
        try:
            neirong = md.read_text(encoding="utf-8")
        except Exception:
            continue
        # 所有 SKILL.md 都参与技能路由
        # 跳过 YAML frontmatter（--- 包起来的元数据块），找到真正的标题
        hang_liebiao = neirong.split("\n")
        biaoti = ""
        zai_frontmatter = False
        frontmatter_end = 0
        for i, hang in enumerate(hang_liebiao):
            stripped = hang.strip()
            if stripped == "---":
                if i == 0 or zai_frontmatter:
                    zai_frontmatter = not zai_frontmatter
                    frontmatter_end = i
                continue
            if zai_frontmatter:
                continue
            if stripped.startswith("# "):
                biaoti = stripped.replace("# ", "").strip()
                break
        if not biaoti:
            biaoti = entry.name  # 回退到目录名
        
        # 描述：标题后到第一个 ## 之间的内容
        miaoshu_hang = []
        kaishi = False
        for hang in hang_liebiao[frontmatter_end + 1:]:
            hang = hang.strip()
            if not kaishi:
                if hang.startswith("# "):
                    kaishi = True
                continue
            if hang.startswith("## "):
                break
            if hang and not hang.startswith("#"):
                miaoshu_hang.append(hang)
        miaoshu = "；".join(miaoshu_hang) if miaoshu_hang else biaoti
        
        # 提取工具名：反引号(全文) + 加粗(仅"工具"相关章节)
        gongju = _re.findall(r"`([a-z][a-z0-9_]+)`", neirong)
        # 加粗仅匹配含"工具"的章节标题下的内容，避免参数名污染
        gongju_jiacu = []
        jie_duan = neirong.split("\n## ")
        for jd in jie_duan:
            if "工具" in jd.split("\n")[0]:
                gongju_jiacu += _re.findall(r"\*\*([a-z][a-z0-9_]+)\*\*", jd)
        gongju += gongju_jiacu
        # 去重保序，过滤非工具词
        bushi_gongju = {"query", "schema","asset_ref","asset_kind","namespace","name","goal","path",
                       "content","source","target","url","timeout","max_chars","format","encoding",
                       "language","task","selector","value","max_results","mode","pattern","dns_resolve",
                       "network_request","http_client","protocol_adapter","review_ready",
                       "isolated_workspace_candidate_only","code_x_skill_guide","full_page","plan_id",
                       "version","status","source_trace","purpose","trigger_rules","input_contract",
                       "output_contract","runtime_binding","usage_card","chain_recipe","risk_profile",
                       "validation_contract","rollback_contract","audit_contract","llm_policy","lifecycle",
                       "rollback_evidence_path"}
        seen = set()
        gongju_qingdan = []
        for g in gongju:
            if g not in seen and g not in bushi_gongju and len(g) > 3:
                seen.add(g)
                gongju_qingdan.append(g)
        jieguo[biaoti] = {
            "miaoshu": miaoshu,
            "gongju": gongju_qingdan,
            "mulu": str(entry),
        }
    # 注入虚拟 skill（不走 SKILL.md）
    jieguo["Code-X 代码系统"] = {
        "miaoshu": "进入 Code-X 代码系统，处理代码诊断、分析、补丁、验证与交付链路",
        "gongju": [],
    }
    jieguo["文件操作"] = {
        "miaoshu": "读取/扫描/写入工作区文件，处理项目打包等操作",
        "gongju": [],
    }
    jieguo["现场学习资产化"] = {
        "miaoshu": (
            "用户明确要求现场学习、掌握新能力、生成 Skill 或 Tool 时使用；"
            "学习后直接走 R20 active asset 激活和 smoke，不停在候选池。"
        ),
        "gongju": [
            "learning_master_plan",
            "tool_skill_blueprint",
            "learning_asset_adapter_drill",
            "learning_asset_activation_apply",
            "learning_asset_activation_smoke",
            "runtime_tool_alignment_check",
        ],
    }
    return jieguo


def _duqu_jineng_quanwen(skill_name: Any, *, max_chars: int = 6000) -> str:
    """Read active SKILL.md files so the model sees playbooks, not just tool names."""
    targets = _coerce_skill_names(skill_name)
    if not targets:
        return ""
    if len(targets) > 1:
        per_skill = max(1200, max_chars // max(1, len(targets)))
        chunks = []
        for target in targets:
            text = _duqu_jineng_quanwen(target, max_chars=per_skill)
            if text:
                chunks.append(f"## {target}\n{text[:per_skill]}")
        return "\n\n---\n\n".join(chunks)[:max_chars]

    target = targets[0]

    # 优先走 _jiexi_jineng_gongju 的 mulu 缓存
    jineng_yingse = _jiexi_jineng_gongju()
    ziliao = jineng_yingse.get(target)
    if ziliao and ziliao.get("mulu"):
        lujing = ziliao["mulu"] + "/SKILL.md"
        try:
            neirong = open(lujing, "r", encoding="utf-8").read()
            # 跳过YAML frontmatter
            if neirong.startswith("---"):
                idx = neirong.find("---", 3)
                if idx > 0:
                    neirong = neirong[idx + 3:].lstrip()
            return neirong[:max_chars]
        except Exception:
            pass

    # 回退：遍历文件，跳过YAML frontmatter
    mulu = _jineng_mulu()
    if not mulu.exists():
        return ""
    for entry in sorted(mulu.iterdir()):
        md = entry / "SKILL.md"
        if not md.exists():
            continue
        try:
            neirong = md.read_text(encoding="utf-8").strip()
        except Exception:
            continue
        # 跳过YAML frontmatter找标题
        hang_liebiao = neirong.split("\n")
        biaoti = ""
        neirong_kaishi = 0
        zai_fm = False
        for i, hang in enumerate(hang_liebiao):
            s = hang.strip()
            if s == "---":
                zai_fm = not zai_fm
                if not zai_fm:
                    neirong_kaishi = i + 1  # frontmatter 结束后从下一行开始
                continue
            if zai_fm:
                continue
            if s.startswith("# "):
                biaoti = s.replace("# ", "").strip()
                break
        if not biaoti:
            biaoti = hang_liebiao[0].replace("# ", "").strip()
        if biaoti == target:
            # 返回时跳过YAML frontmatter
            return "\n".join(hang_liebiao[neirong_kaishi:])[:max_chars]
    return ""


def _jineng_zhidao() -> str:
    """生成 skill 列表（含虚拟 skill），供模型选择。"""
    jineng_yingse = _jiexi_jineng_gongju()
    xuyao_zhixing_lianlu = {"Code-X 代码系统", "文件操作", "项目收口质检"}
    xian = []
    for mingcheng, ziliao in jineng_yingse.items():
        gongju_shu = len(ziliao["gongju"])
        houzhui = "（走独立执行链路）" if mingcheng in xuyao_zhixing_lianlu else f"（{gongju_shu}个工具）"
        xian.append(f"【{mingcheng}】{ziliao['miaoshu']}{houzhui}")
    return (
        "\n\n可用能力包（可选一项或多项）：\n"
        + "\n".join(xian)
    )


def _liaotian_chun(context: AgentShellContext, xiaoxi: str, qinggan_ka: str = "") -> str:
    """纯聊天：模型直出，无工具。"""
    xiaoxi_liebiao = _zhuru_qinggan(context, qinggan_ka, current_user=xiaoxi)
    xiaoxi_liebiao = [m for m in xiaoxi_liebiao if m.get("role") != "tool"]
    for m in xiaoxi_liebiao:
        m.pop("tool_calls", None)

    envelope = compile_existing_messages_envelope(
        xiaoxi_liebiao,
        phase="chat",
        output_contract="normal_chat",
        metadata={"route": "chat"},
    )
    result = context.model_client.chat(envelope, context.config)
    content = _strip_noise(result.content or "")
    return content


def _liaotian_gongju(context: AgentShellContext, xiaoxi: str, qinggan_ka: str = "", skill_name: Any = "") -> str:
    """工具调用：只给指定 skill 集合的工具，并在工具后自动纠偏收口。"""
    xiaoxi_liebiao = _zhuru_qinggan(context, qinggan_ka, current_user=xiaoxi)
    xiaoxi_liebiao = [m for m in xiaoxi_liebiao if m.get("role") != "tool"]
    for m in xiaoxi_liebiao:
        m.pop("tool_calls", None)
    skill_names = _merge_skill_names(_frontend_selected_skill_names(), skill_name)
    tools = _build_tool_schemas(context, skill_names)

    envelope = compile_existing_messages_envelope(
        xiaoxi_liebiao,
        phase="chat",
        output_contract="normal_chat",
        metadata={"route": "chat"},
    )
    system_msg = dict(envelope.messages[0])
    skill_playbook = _duqu_jineng_quanwen(skill_names)
    permission_card = _frontend_permission_card(context)
    system_msg["content"] = str(system_msg.get("content") or "") + "\n\n" + permission_card
    if skill_playbook:
        system_msg["content"] = (
            str(system_msg.get("content") or "")
            + "\n\n【当前启用技能包】\n"
            + "本轮可以同时参考多个技能包，按任务需要组合使用，不必强行压成单一技能。\n"
            + ("启用列表：" + "、".join(skill_names) + "\n" if skill_names else "")
            + skill_playbook
            + "\n\n执行当前技能包时，先按技能包流程选择工具；工具返回后必须继续完成用户目标，不要把工具清单或半成品交给用户。"
        )
    system_msg["content"] = (
        str(system_msg.get("content") or "")
        + "\n\n【自动纠错协议】如果工具调用、参数、路径、检索结果或上一轮回答出现错误，"
        + "你必须基于最新工具结果自行修正并继续完成用户目标。"
        + "最终输出必须是用户可读答案，不得把工具调用标签、函数调用 JSON 或内部占位文本交给用户。"
    )
    duihua = [dict(m) for m in envelope.messages[1:]]
    tool_results: list[dict[str, Any]] = []
    tool_call_counts: dict[str, int] = {}
    max_tool_rounds = max(4, min(int(getattr(context, "max_steps", 8) or 8), 24))
    force_finalize = False

    for _ in range(max_tool_rounds):
        msgs = [system_msg] + duihua
        env = seal_compiled_messages(
            msgs,
            phase="chat",
            compiled_prompt_id=envelope.compiled_prompt_id,
            output_contract="normal_chat",
        )
        result = context.model_client.chat(env, context.config, tools=tools)
        tool_calls = result.tool_calls
        if not tool_calls:
            content = _strip_noise(result.content or "")
            if _is_bad_final_answer(content) and tool_results:
                return _tool_result_fallback_response(tool_results)
            return content

        content = result.content or ""
        reasoning = getattr(result, "reasoning_content", "") or ""
        for tc in tool_calls:
            fn = tc.get("function", {})
            gongju_ming = fn.get("name", "")
            canshu_str = fn.get("arguments", "{}")
            try:
                canshu = json.loads(canshu_str) if canshu_str else {}
            except json.JSONDecodeError:
                canshu = {}
            gongju_ming, canshu = _normalize_runtime_tool_call(context, gongju_ming, canshu, xiaoxi)
            canshu_str = json.dumps(canshu, ensure_ascii=False)
            call_signature = json.dumps([gongju_ming, canshu], ensure_ascii=False, sort_keys=True, default=str)
            tool_call_counts[call_signature] = tool_call_counts.get(call_signature, 0) + 1

            from tiangong_agent_runtime.tool_invocation import ToolInvocation
            from tiangong_agent_runtime.turn_context import TurnContext
            inv = ToolInvocation(gongju_ming, canshu, reason="tool_call")
            tctx = TurnContext.create(xiaoxi, workspace=str(context.workspace),
                                      model_config=context.config, model_client=context.model_client)
            if tool_call_counts[call_signature] > 2:
                gongju_jg = {
                    "error": "repeated_tool_call_blocked",
                    "summary": f"同一工具和参数已重复调用 {tool_call_counts[call_signature]} 次，自动转入纠偏收口。",
                }
                force_finalize = True
            else:
                jieguo_fn = context.runtime.registry.get(gongju_ming)
                if jieguo_fn is None:
                    gongju_jg = {"error": f"\u672a\u6ce8\u518c\u5de5\u5177: {gongju_ming}"}
                else:
                    try:
                        r = jieguo_fn(inv, tctx)
                        gongju_jg = {"ok": r.ok, "summary": r.output_summary, "data": r.data if isinstance(r.data, dict) else {"content": str(r.data)}}
                    except Exception as exc:
                        gongju_jg = {"error": str(exc)}
            tool_results.append({
                "tool_name": gongju_ming,
                "arguments": canshu,
                "result": gongju_jg,
            })

            duihua.append({
                "role": "assistant",
                "content": content or None,
                "tool_calls": [{
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": gongju_ming, "arguments": canshu_str},
                }],
                **({"reasoning_content": reasoning} if reasoning else {}),
            })
            duihua.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(gongju_jg, ensure_ascii=False),
            })
        if force_finalize:
            break
        duihua.append({
            "role": "user",
            "content": (
                "工具结果已返回。请判断任务是否已经完成：如果还缺关键证据，可以继续调用必要的新工具；"
                "如果信息已足够，必须直接给用户最终答案。不要重复同一工具和同一参数，不要输出工具调用标签。"
            ),
        })

    # 多轮后强制总结
    if tool_results:
        try:
            final_content = _force_finalize_tool_answer(context, system_msg, duihua, envelope, tool_results)
        except ModelClientError:
            raise
        except Exception:
            final_content = ""
        if final_content and not _is_bad_final_answer(final_content):
            return final_content
        return _tool_result_fallback_response(tool_results)
    return "工具链没有拿到可用结果；请换一种说法或补充目标。"


def _desktop_fast_return_enabled() -> bool:
    channel = os.environ.get("TIANGONG_ENTRY_CHANNEL", "").strip().lower()
    override = os.environ.get("TIANGONG_DESKTOP_SYNC_LEARNING", "").strip().lower()
    return channel == "desktop_gui" and override not in {"1", "true", "yes", "on"}


def _manual_learning_request() -> dict[str, str]:
    action = os.environ.get("TIANGONG_MANUAL_LEARNING_ACTION", "").strip().lower()
    tiao_id = os.environ.get("TIANGONG_MANUAL_LEARNING_ID", "").strip()
    if action not in {"learn", "delete"} or not tiao_id:
        return {}
    return {"action": action, "id": tiao_id}


def _run_manual_learning_task(context: AgentShellContext, tiao_id: str, xiaoxi: str, *, persist: bool = False) -> str:
    rt = context.runtime
    _stream_step("manual_learning_pick", "接入学习候选", "running", f"正在读取经验池条目 {tiao_id}")
    item = None
    try:
        item = rt.jingyan_chi.tiaomu_by_id(tiao_id)
    except Exception:
        item = None
    if item is None:
        message = f"未找到经验池条目：{tiao_id}"
        _stream_step("manual_learning_pick", "接入学习候选", "failed", message)
        write_line(message)
        return message
    if bool(getattr(item, "yichuli", False)):
        message = f"这条经验已经学习过：{tiao_id}"
        _stream_step("manual_learning_pick", "接入学习候选", "failed", message)
        write_line(message)
        return message
    summary = str(getattr(item, "zhaiyao", "") or "")[:180]
    _stream_step("manual_learning_pick", "接入学习候选", "done", summary or "候选已接入前台学习任务")
    _stream_step("manual_learning_run", "执行主动学习", "running", "搜索资料、萃取知识、判定是否生成技能或工具候选")
    try:
        from tiangong_agent_runtime.turn_context import TurnContext

        turn_context = TurnContext.create(
            xiaoxi or f"前台主动学习经验池条目 {tiao_id}",
            workspace=context.workspace,
            max_steps=max(1, int(getattr(context, "max_steps", 20) or 20)),
            model_config=context.config,
            model_client=context.model_client,
        )
        result = rt._run_free_will_learning_chain(
            context.model_client,
            turn_context,
            laiyuan="manual_approval",
            model_config=context.config,
            target_tiao_id=tiao_id,
        )
    except ModelClientError as exc:
        message = _model_connection_failed_message(exc)
        _stream_step("manual_learning_run", "执行主动学习", "failed", message)
        write_line(f"[模型连接失败] {message}")
        return f"[模型连接失败] {message}"
    except Exception as exc:
        message = f"主动学习失败：{exc.__class__.__name__}: {str(exc)[:240]}"
        _stream_step("manual_learning_run", "执行主动学习", "failed", message)
        write_line(message)
        return message
    final = (
        "主动学习已完成。\n\n"
        f"经验条目：{tiao_id}\n"
        f"学习内容：{summary or '未提供摘要'}\n"
        f"执行结果：{result or '学习链无返回'}"
    )
    _stream_step("manual_learning_run", "执行主动学习", "done", str(result or "学习链已完成")[:240])
    _stream_step("complete", "完成", "done", "主动学习任务已完成")
    write_line(final)
    if persist:
        try:
            context.session.add_user(xiaoxi)
            context.session.add_assistant(final)
        except Exception:
            pass
    return final


def _hou_chuli(context: AgentShellContext, xiaoxi: str, jieguo: str, lujing: str) -> None:
    """后处理：经验合成 → 记忆存储。显式学习/资产化已前移到执行链。"""
    rt = context.runtime
    clean_jieguo = _strip_think_blocks(jieguo)

    # 经验合成
    try:
        if hasattr(rt, 'experience_bridge') and rt.experience_bridge:
            rt.experience_bridge.synthesize(
                context_snapshot=rt.context_snapshot(),
                manual_notes=f"[{lujing}] {xiaoxi[:200]} → {clean_jieguo[:200]}"
            )
    except Exception:
        pass

    # 记忆存储（全路径统一落盘）
    try:
        rt.remember_turn(xiaoxi=xiaoxi, huifu=clean_jieguo, lujing=lujing)
    except Exception:
        pass


# ── 主入口 ────────────────────────────────────────

def _shoukou(context: AgentShellContext, xiaoxi: str, jieguo: str, qinggan_ka: str = "") -> str:
    """收口：代码/文件任务完成后，加一轮聊天总结"""
    xitong = "用户刚完成了一项任务，请用一句话做收口总结：简洁、自然、有温度。不要复述技术细节，不要引入任何新的角色、姓名、人设或身份；语气只服从当前已有 Soul/会话设定。"
    yonghu = f"[原消息]\n{xiaoxi}\n\n[任务结果]\n{jieguo}"
    shou = _qingliang_llm(context, xitong + qinggan_ka, yonghu)
    clean = _strip_think_blocks(shou)
    return clean if clean else jieguo


def _shuaxin_tishi_ci(
    context: AgentShellContext,
    xiaoxi: str,
    moshi: str = "ordinary_chat",
    *,
    conversation_window_cards: list[str] | None = None,
    prompt_event_cards: list[str] | None = None,
    emotion_total_cards: list[str] | None = None,
    runtime_material_cards: list[str] | None = None,
) -> None:
    """刷新 PromptCompiler 器官卡。"""
    try:
        refresh_session_system_prompt(
            context,
            user_text=xiaoxi,
            task_mode=moshi,
            conversation_window_cards=conversation_window_cards,
            prompt_event_cards=prompt_event_cards,
            emotion_total_cards=emotion_total_cards,
            runtime_material_cards=runtime_material_cards,
        )
    except Exception:
        pass


def _hebing_panding(context: AgentShellContext, xiaoxi: str, qinggan_ka: str) -> tuple[str, str, list[str]]:
    """② 合并判定：LLM 从所有技能列表中自主选择。返回 (路径, 自然回复, skill名列表)"""
    attachment_fallback_skill = _recommended_attachment_skill(xiaoxi)
    selected_skills = _frontend_selected_skill_names()
    if "[Code-X续跑请求]" in str(xiaoxi or "") or _looks_like_explicit_codex_request(xiaoxi):
        return "work", "", ["Code-X 代码系统"]
    if _looks_like_code_analysis_request(xiaoxi):
        return "work", "", ["Code-X 代码系统"]
    # 模式硬约束：前端 mode 选择优先
    moshi = str(getattr(context.config, "tool_execution_mode", "") or "").strip()
    if moshi == "disabled":
        return "chat", "", []
    attachment_content_skill = _attachment_content_skill_for_model(context, xiaoxi)
    if attachment_content_skill:
        return "work", "", _merge_skill_names(selected_skills, attachment_content_skill)
    if _looks_like_web_search_request(xiaoxi):
        return "work", "", _merge_skill_names(selected_skills, "联网搜索")
    if moshi == "runtime_governed":
        return "work", "", selected_skills

    # LLM 自主判定：chat 还是 work，选哪个技能
    xitong = context.session.messages[0].get("content", "") if context.session.messages else ""
    if qinggan_ka:
        xitong += "\n" + qinggan_ka
    zhidao = _jineng_zhidao()
    selected_card = _frontend_selected_skills_card()
    permission_card = _frontend_permission_card(context)
    shangxiawen = context.session.build_context_hint(turns=10, max_chars=3000)
    yonghu = f"""用户消息：{xiaoxi}

{shangxiawen}

判定规则：
- 纯聊天/问好/咨询/情感交流/简单问答 → 判定 chat，直接友好回复
- 以下任一情况必须判定 work：搜索/查资料/修bug/改代码/写代码/读写文件/打包/安装/运行测试/诊断/审计/迁移/构建/学习/质检
- 用户明确说"帮我修""帮我写""帮我改""帮我查""帮我跑""帮我学" → 一律 work

{selected_card}

{permission_card}

{zhidao}

回复格式：
第一行：自然回复
第二行：chat 或 work: 能力包名1, 能力包名2"""
    try:
        result = _qingliang_llm(context, xitong, yonghu)
    except Exception:
        if attachment_fallback_skill:
            return "work", "", _merge_skill_names(selected_skills, attachment_fallback_skill)
        return ("work", "", selected_skills) if selected_skills else ("chat", "", [])
    if not result or not result.strip():
        if attachment_fallback_skill:
            return "work", "", _merge_skill_names(selected_skills, attachment_fallback_skill)
        return ("work", "", selected_skills) if selected_skills else ("chat", "", [])
    lines = [l.strip() for l in result.split("\n") if l.strip()]
    if not lines:
        if attachment_fallback_skill:
            return "work", "", _merge_skill_names(selected_skills, attachment_fallback_skill)
        return ("work", "", selected_skills) if selected_skills else ("chat", "", [])
    panding = lines[-1].lower()
    ziran = "\n".join(lines[:-1]) if len(lines) > 1 else ""
    if panding.startswith("work"):
        jineng_raw = panding.replace("work:", "").replace("work：", "").strip()
        jineng_yingse = _jiexi_jineng_gongju()
        mingcheng = list(jineng_yingse.keys())
        matched: list[str] = []
        for m in mingcheng:
            if m in jineng_raw:
                matched.append(m)
        return "work", ziran, _merge_skill_names(selected_skills, matched, attachment_fallback_skill)
    if attachment_content_skill:
        return "work", ziran, _merge_skill_names(selected_skills, attachment_content_skill)
    if selected_skills and "chat" not in panding:
        return "work", ziran, selected_skills
    return "chat", ziran, []


def run_once(context: AgentShellContext, xiaoxi: str, persist: bool = True) -> str:
    """主入口：新链路。persist=False 时跳过 session 写入（用于 --once 无痕测试）。"""
    _stream_step("request", "接收用户消息", "running", "消息已进入后端主链路")
    manual_learning = _manual_learning_request()
    if manual_learning.get("action") == "learn":
        if not provider_is_ready(context.config):
            _stream_step("provider", "检查模型配置", "failed", "模型配置缺失")
            write_line(_provider_not_configured_message())
            return ""
        _stream_step("provider", "检查模型配置", "done", "模型配置已提供，真实连通性将在调用时确认")
        return _run_manual_learning_task(context, manual_learning["id"], xiaoxi, persist=persist)
    if not provider_is_ready(context.config):
        _stream_step("provider", "检查模型配置", "failed", "模型配置缺失")
        write_line(_provider_not_configured_message())
        return ""
    _stream_step("provider", "检查模型配置", "done", "模型配置已提供，真实连通性将在调用时确认")

    # ① 收集信息
    _stream_step("collect_context", "收集信息与拼接提示词", "running", "系统底层、Soul、最近10条、L1-L5检索与情感值")
    cailiao = _shouji_xinxi(context, xiaoxi)
    xiaoxi_session = str(cailiao.get("xiaoxi_jiaodui") or xiaoxi)
    codex_task_text, codex_state_task, _codex_is_continue = _codex_continuation_task(context, xiaoxi_session)
    moxing_xiaoxi = _message_with_uploaded_file_refs(context, codex_task_text)
    upload_count = len(_uploaded_file_refs())
    if upload_count:
        _stream_step("attachments", "接入上传文件", "done", f"已接入 {upload_count} 个知识库文件引用；未注入文件正文")
    _stream_step("collect_context", "收集信息与拼接提示词", "done", "提示词上下文已完成")
    # 情感总值已在①按固定顺序写入 EmotionTotalCard，后续不再追加旧版情感卡。
    qinggan_ka = ""
    os.environ["TIANGONG_TOOL_COUNT"] = str(len(_build_tool_schemas(context) or []))

    # ② 合并判定（含 skill 选择，一轮 LLM）
    _stream_step("route", "合并判定与能力路由", "running", "正在判断 chat/work 与能力包")
    lujing, ziran_huifu, jineng = _hebing_panding(context, moxing_xiaoxi, qinggan_ka)
    jineng_liebiao = _coerce_skill_names(jineng)
    jineng_label = "、".join(jineng_liebiao) if jineng_liebiao else "无"
    _stream_step("route", "合并判定与能力路由", "done", f"路由={lujing or 'chat'}；能力={jineng_label}")

    try:
        if lujing == "chat":
            _stream_step("execute", "执行聊天回复", "running", "走 chat 分支生成自然回复")
            jieguo = ziran_huifu or _chat_route_fallback(xiaoxi_session)
            if not jieguo:
                jieguo = _liaotian_chun(context, moxing_xiaoxi, qinggan_ka)
            shoukou = jieguo
            _stream_step("execute", "执行聊天回复", "done", "自然回复已生成")
        else:
            if "Code-X 代码系统" in jineng_liebiao:
                _stream_step("execute", "执行代码任务", "running", "走 code 分支处理代码/项目请求")
                jieguo = _daima_xiufu(context, moxing_xiaoxi, qinggan_ka, state_task=codex_state_task)
                shoukou = jieguo
                lujing = "code"
                _stream_step("execute", "执行代码任务", "done", "代码分支已返回结果")
            elif "项目收口质检" in jineng_liebiao:
                _stream_step("execute", "执行项目收口质检", "running", "项目扫描、语法检查、测试检查、项目诊断、质量裁决与交付报告")
                jieguo = _xiangmu_shoukou_zhijian(context, moxing_xiaoxi, qinggan_ka)
                shoukou = jieguo
                lujing = "project_closure_quality"
                _stream_step("execute", "执行项目收口质检", "done", "项目收口质检已返回结果")
            elif "文件操作" in jineng_liebiao:
                _stream_step("execute", "执行文件任务", "running", "走 file 分支处理文件操作")
                jieguo = _wenjian_zhixing(context, moxing_xiaoxi, qinggan_ka)
                shoukou = jieguo
                lujing = "file"
                _stream_step("execute", "执行文件任务", "done", "文件分支已返回结果")
            else:
                _stream_step("execute", "执行工具任务", "running", f"走 tool 分支：{jineng_label or '默认工具链'}")
                jieguo = _liaotian_gongju(context, moxing_xiaoxi, qinggan_ka, jineng_liebiao)
                shoukou = jieguo
                lujing = "tool"
                _stream_step("execute", "执行工具任务", "done", "工具分支已返回结果")
    except ModelClientError as exc:
        message = _model_connection_failed_message(exc)
        _stream_step("execute", "模型连接失败", "failed", message)
        write_line(f"[模型连接失败] {message}")
        _stream_step("complete", "完成", "done", "本轮因模型连接失败中止，会话继续")
        return f"[模型连接失败] {message}"

    if _is_bad_final_answer(shoukou):
        _stream_step("self_correct", "自动纠偏", "running", "最终输出不可展示，正在自动修正")
        try:
            repaired = _repair_visible_final_answer(context, xiaoxi_session, shoukou, qinggan_ka)
        except ModelClientError:
            repaired = ""
        except Exception:
            repaired = ""
        if repaired and not _is_bad_final_answer(repaired):
            shoukou = repaired
            jieguo = repaired
            _stream_step("self_correct", "自动纠偏", "done", "已修正为可展示最终回答")
        else:
            _stream_step("self_correct", "自动纠偏", "skipped", "修复未产生更好文本，保留原始结果")

    if persist:
        _stream_step("output_session", "输出与会话写入", "running", "写出最终回复并记录 user/assistant")
    else:
        _stream_step("output_session", "输出", "running", "写出最终回复（--once 不写 session）")
    write_line(shoukou)
    if persist:
        context.session.add_user(xiaoxi_session)
        context.session.add_assistant(shoukou)
    _stream_step("output_session", "输出与会话写入", "done", "回复与会话已写入" if persist else "回复已写出")
    _stream_step("postprocess", "后处理", "running", "经验合成、L1落盘、晋升与遗忘检查")
    _hou_chuli(context, xiaoxi_session, jieguo, lujing)
    _stream_step("postprocess", "后处理", "done", "后处理完成")
    _stream_step("complete", "完成", "done", "本轮链路已完成")
    return jieguo


def run_interactive(context: AgentShellContext) -> int:
    write_line("天工造物 v2 · 临渊者")
    write_line("输入 /help 查看命令，/exit 退出。")

    while True:
        try:
            xiaoxi = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            write_line("\n已退出。")
            return 0
        if not xiaoxi:
            continue

        mingling = xiaoxi.lower()

        if mingling in {"/exit", "/quit"}:
            write_line("已退出。")
            return 0

        if mingling == "/help":
            write_line("/help /exit /status /scan /run /reset")
            continue

        if mingling == "/status":
            write_line(f"工作区：{context.workspace}\n模型：{context.config.model or '未配置'}")
            continue

        if mingling == "/reset":
            context.session.reset()
            write_line("会话已重置。")
            continue

        if mingling.startswith("/scan"):
            write_line(_du_gongzuoqu_wenjian(Path(context.workspace))[:4000])
            continue

        if mingling.startswith("/run "):
            xiaoxi = xiaoxi[5:].strip()
            # 强制走 file 路径
            jieguo = _wenjian_zhixing(context, xiaoxi)
            shoukou = _shoukou(context, xiaoxi, jieguo)
            write_line(shoukou)
            continue

        run_once(context, xiaoxi)


# Windows migration repair: override mojibake prompt/report strings for the
# direct-file pathway. Frontend Chinese text is intentionally untouched.
def _wenjian_zhixing(context: AgentShellContext, xiaoxi: str, qinggan_ka: str = "") -> str:
    """文件执行：扫描工作区，LLM 通过真实工具操作文件。"""
    return _codex_zhixing(context, xiaoxi, qinggan_ka)


def _mojibake_score(text: str) -> int:
    marker_score = sum(text.count(marker) * 3 for marker in _MOJIBAKE_MARKERS)
    private_use_score = sum(3 for ch in text if "\\ue000" <= ch <= "\\uf8ff")
    replacement_score = text.count("\\ufffd") * 4
    common_mojibake = (
        "涓", "鍐", "鏂", "浠", "瀵", "杈", "鐩", "宸", "閫", "鍚",
        "绋", "璺", "鍙", "鎵", "杩", "瑙", "淇", "妯", "瀛", "绾",
        "浣", "绯", "鍏", "婊", "姝", "妫", "泞",
    )
    common_score = sum(text.count(marker) for marker in common_mojibake)
    return marker_score + private_use_score + replacement_score + common_score



_MOJIBAKE_MARKERS = ('澶', '閫', '犵', '墿', '鈧', '€', '�')


def _read_text_file_for_prompt(path: Path) -> tuple[str, str, bool]:
    raw = path.read_bytes()
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "cp936"):
        try:
            text = raw.decode(encoding)
            repaired = _repair_mojibake_if_better(text)
            return repaired, encoding, repaired != text
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    if last_error is not None:
        text = raw.decode("utf-8", errors="replace")
        repaired = _repair_mojibake_if_better(text)
        return repaired, "utf-8-replace", repaired != text
    return "", "empty", False


def _repair_mojibake_if_better(text: str) -> str:
    original_score = _mojibake_score(text)
    if original_score <= 0:
        return text
    candidates = []
    for source_encoding in ("gb18030", "cp936"):
        try:
            candidate = text.encode(source_encoding, errors="strict").decode("utf-8", errors="strict")
            candidates.append(candidate)
        except UnicodeError:
            continue
    if not candidates:
        return text
    best = min(candidates, key=_mojibake_score)
    return best if _mojibake_score(best) + 2 < original_score else text


def _mojibake_score(text: str) -> float:
    if not text:
        return 0
    score = 0
    for i, ch in enumerate(text):
        if ch in _MOJIBAKE_MARKERS:
            score += max(1.0, 10.0 / (1 + i * 0.01))
    return score


def _du_gongzuoqu_wenjian(ws: Path) -> str:
    """扫描工作区文本文件，读取内容拼接给 LLM。"""
    jieguo = [f"工作区根目录：{ws}"]
    zong = 0
    files: list[Path] = []
    try:
        iterator = ws.rglob("*")
    except OSError:
        return f"工作区根目录：{ws}\n(工作区无法扫描)"
    for wj in iterator:
        try:
            rel_parts = wj.relative_to(ws).parts
        except ValueError:
            continue
        if any(part in _DIRECT_FILE_SKIP_DIRS for part in rel_parts[:-1]):
            continue
        if not wj.is_file() or wj.suffix.lower() not in _DIRECT_FILE_EXTENSIONS:
            continue
        files.append(wj)
        if len(files) >= 80:
            break
    if not files:
        jieguo.append("(工作区无可注入文本文件)")
        return "\n".join(jieguo)

    tree = []
    for wj in sorted(files):
        try:
            tree.append(f"- {wj.relative_to(ws).as_posix()} ({wj.stat().st_size}B)")
        except OSError:
            tree.append(f"- {wj.name}")
    jieguo.append("[文件清单]\n" + "\n".join(tree))

    for wj in sorted(files):
        rel = wj.relative_to(ws).as_posix()
        try:
            text, encoding, repaired = _read_text_file_for_prompt(wj)
            preview = text[:_DIRECT_FILE_PER_FILE_CHAR_LIMIT]
            repair_note = "；已修复疑似 mojibake" if repaired else ""
            jieguo.append(f"### {rel} ({wj.stat().st_size}B; encoding={encoding}{repair_note})\n{preview}")
            zong += len(preview)
            if zong > _DIRECT_FILE_SCAN_CHAR_LIMIT:
                jieguo.append("...(后续文件省略)")
                break
        except Exception:
            jieguo.append(f"### {rel}\n(二进制、权限不足或无法读取)")
    return "\n\n".join(jieguo)


def _qinggan_ka(context: AgentShellContext) -> str:
    """生成情感卡片文本，供所有分支注入 system prompt。"""
    rt = context.runtime
    try:
        if not rt._affective_state:
            return ""
        af = rt._affective_state
        ev = af.emotion_vector
        dv = af.desire_vector
        luxian = rt._affective_route
        doing_hint = ""
        if luxian is not None and getattr(luxian, "planner_hint", None) is not None:
            doing_hint = getattr(luxian.planner_hint, "candidate_priority_hint", "") or ""
        return (
            f"\n\n[当前情感状态 / 动态底色]\n"
            f"以下数值反映系统当前的情感倾向(0-1)：Soul 底层值 + 对话临时波动 = 当前总值。\n"
            f"七情只微调语气、节奏和措辞；六欲只影响做事方式、检查密度和闭环节奏，不得绕过路径、安全和用户目标。\n"
            f"- 七情总值：joy={ev.joy:.2f} anger={ev.anger:.2f} worry={ev.worry:.2f} "
            f"thoughtfulness={ev.thoughtfulness:.2f} sadness={ev.sadness:.2f} "
            f"fear={ev.fear:.2f} surprise={ev.surprise:.2f}\n"
            f"- 六欲总值：survival={dv.survival:.2f} curiosity={dv.curiosity:.2f} "
            f"achievement={dv.achievement:.2f} connection={dv.connection:.2f} "
            f"order={dv.order:.2f} rest={dv.rest:.2f}\n"
            f"- 主导情绪：{luxian.dominant_emotion if luxian else 'unknown'}\n"
            f"- 主导六欲：{luxian.dominant_desire if luxian else 'unknown'}\n"
            f"- 做事倾向：{doing_hint or '按任务目标闭环，写后必须验真。'}\n"
            f"- 稳态负荷：{af.allostatic_load:.2f}（高值=更谨慎/少写长；低值=更轻量/简洁）"
        )
    except Exception:
        return ""
