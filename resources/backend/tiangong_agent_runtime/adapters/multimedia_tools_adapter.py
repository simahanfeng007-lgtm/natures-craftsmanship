"""多媒体工具适配器。

本文件提供图片解析、视频解析、图片/视频/音频制作请求、剪辑和多媒体结构化抽取工具。
设计目标：不改变 Runtime 主链，只通过 Runtime Registry 新增工具能力。
"""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import hashlib
import math
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from tiangong_agent_shell.network_policy import urlopen_with_policy
from tiangong_agent_shell.safe_logging import redact_text

from ..host_path_normalizer import normalize_argument_path, normalization_public_data
from ..media_context_store import save_media_context
from ..media_parser import (
    base_file_meta,
    crop_image,
    extract_keyframes,
    image_meta,
    media_meta,
    run_ffmpeg,
    safe_output_name,
    sha256_file,
    try_ocr_image,
)
from ..result_normalizer import truncate_text
from ..tool_invocation import ToolInvocation
from ..tool_result import ToolResult, ToolResultStatus
from ..turn_context import TurnContext
from ..workspace_guard import WorkspaceGuard, WorkspaceViolation


def _ok(invocation: ToolInvocation, summary: str, data: dict[str, Any], artifacts: list[str] | None = None) -> ToolResult:
    return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.OK, truncate_text(summary, 2000), data=data, artifacts=artifacts or [])


def _fail(invocation: ToolInvocation, summary: str, error_code: str, data: dict[str, Any] | None = None) -> ToolResult:
    return ToolResult(invocation.step_id, invocation.tool_name, ToolResultStatus.FAILED, summary, data=data or {}, error_code=error_code)


def _data(tool_name: str, **extra: Any) -> dict[str, Any]:
    try:
        dependency_status = _local_media_dependency_status()
    except Exception:
        dependency_status = {}
    payload = {
        "schema": "tool_result.data.v2",
        "tool_name": tool_name,
        "evidence_refs": [],
        "confidence": "medium",
        "error_category": "",
        "retryable": None,
        "next_action": "",
        "verification": {},
        "dependency_status": dependency_status,
    }
    payload.update(extra)
    return payload


def _read_path(context: TurnContext, raw_path: str) -> tuple[Path, dict[str, Any]]:
    normalized_path, path_normalization = normalize_argument_path(raw_path, context.user_message)
    target = WorkspaceGuard(context.workspace).resolve_for_read(normalized_path)
    return target, normalization_public_data(path_normalization)


def _artifact_path(context: TurnContext, output_name: str, suffix: str, subdir: str = "multimedia_outputs") -> Path:
    name = output_name or safe_output_name("media", suffix)
    if not Path(name).suffix:
        name += suffix if suffix.startswith(".") else "." + suffix
    return WorkspaceGuard(context.workspace).resolve_for_artifact(Path(subdir) / name)


def _json_artifact(context: TurnContext, invocation: ToolInvocation, title: str, payload: dict[str, Any], output_name: str = "") -> ToolResult:
    out = _artifact_path(context, output_name or safe_output_name(invocation.tool_name, ".json"), ".json", "multimedia_requests")
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    data = _data(invocation.tool_name, request_spec=payload, output_path=str(out), artifact_hash=sha256_file(out), confidence="high")
    return _ok(invocation, f"已生成{title}请求规格：{out}", data, [str(out)])


def _provider_request(invocation: ToolInvocation, context: TurnContext, title: str, payload: dict[str, Any]) -> ToolResult:
    payload = dict(payload)
    payload.setdefault("provider_status", "provider_required")
    payload.setdefault("note", "当前工具已生成标准化请求规格；若需真实生成/渲染，请接入图片/视频/音频 Provider Bridge。")
    return _json_artifact(context, invocation, title, payload, str(invocation.arguments.get("output_name") or ""))


def _parse_image_size(raw: Any) -> tuple[int, int]:
    text = str(raw or "").strip().lower()
    match = re.search(r"(\d{2,5})\s*[x×*]\s*(\d{2,5})", text)
    if match:
        width, height = int(match.group(1)), int(match.group(2))
    elif "16:9" in text:
        width, height = 1280, 720
    elif "9:16" in text:
        width, height = 720, 1280
    elif "4:3" in text:
        width, height = 1024, 768
    elif "3:4" in text:
        width, height = 768, 1024
    else:
        width, height = 1024, 1024
    width = max(256, min(width, 2048))
    height = max(256, min(height, 2048))
    return width, height


def _png_output_name(raw: Any, prompt: str) -> str:
    name = str(raw or "").strip()
    if not name:
        stem = "cute_kitten" if _is_cat_prompt(prompt) else "generated_image"
        return safe_output_name(stem, ".png")
    path = Path(name)
    if path.suffix.lower() != ".png":
        path = path.with_suffix(".png")
    return str(path)


def _is_cat_prompt(prompt: str) -> bool:
    text = prompt.lower()
    return any(key in text for key in ("小猫", "猫咪", "奶猫", "橘猫", "猫", "cat", "kitten", "kitty", "feline"))


def _color_lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _load_font(size: int):
    try:
        from PIL import ImageFont  # type: ignore

        for font_path in (
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ):
            if Path(font_path).exists():
                return ImageFont.truetype(font_path, size=size)
        return ImageFont.load_default()
    except Exception:
        return None


def _wrap_text(text: str, max_chars: int) -> list[str]:
    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        return []
    lines: list[str] = []
    current = ""
    for ch in clean:
        current += ch
        if len(current) >= max_chars:
            lines.append(current)
            current = ""
        if len(lines) >= 4:
            break
    if current and len(lines) < 4:
        lines.append(current)
    return lines


def _draw_soft_gradient(draw: Any, width: int, height: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> None:
    for y in range(height):
        t = y / max(1, height - 1)
        draw.line([(0, y), (width, y)], fill=_color_lerp(top, bottom, t))


def _render_cat_image(prompt: str, style: str, output_path: Path, size: tuple[int, int]) -> None:
    from PIL import Image, ImageDraw, ImageFilter  # type: ignore

    width, height = size
    img = Image.new("RGBA", size, (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)
    _draw_soft_gradient(draw, width, height, (255, 244, 226), (197, 231, 225))

    seed = hashlib.sha256((prompt + style).encode("utf-8", errors="ignore")).digest()
    accent = [(239, 144, 82), (95, 159, 143), (128, 119, 190), (230, 184, 74)][seed[0] % 4]
    s = min(width, height)
    cx = width // 2
    floor_y = int(height * 0.77)

    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((int(width * 0.12), int(height * 0.04), int(width * 0.92), int(height * 0.78)), fill=(255, 229, 166, 70))
    glow = glow.filter(ImageFilter.GaussianBlur(max(8, s // 22)))
    img.alpha_composite(glow)
    draw = ImageDraw.Draw(img)

    draw.ellipse((int(width * 0.22), floor_y - int(s * 0.05), int(width * 0.78), floor_y + int(s * 0.08)), fill=(107, 91, 73, 48))
    cushion = (int(width * 0.20), floor_y - int(s * 0.08), int(width * 0.80), floor_y + int(s * 0.09))
    draw.rounded_rectangle(cushion, radius=max(16, s // 18), fill=(244, 189, 129, 255), outline=(221, 139, 91, 255), width=max(2, s // 160))
    draw.line((cushion[0] + s * 0.04, floor_y, cushion[2] - s * 0.04, floor_y), fill=(229, 151, 102, 180), width=max(2, s // 120))

    fur = (245, 174, 92, 255)
    fur_dark = (201, 119, 62, 255)
    fur_light = (255, 218, 154, 255)
    body = (cx - int(s * 0.22), int(height * 0.39), cx + int(s * 0.22), int(height * 0.78))
    draw.ellipse(body, fill=fur, outline=fur_dark, width=max(3, s // 120))
    belly = (cx - int(s * 0.12), int(height * 0.52), cx + int(s * 0.12), int(height * 0.75))
    draw.ellipse(belly, fill=fur_light)

    tail_width = max(10, s // 32)
    tail = [
        (cx + int(s * 0.18), int(height * 0.61)),
        (cx + int(s * 0.31), int(height * 0.52)),
        (cx + int(s * 0.33), int(height * 0.38)),
        (cx + int(s * 0.21), int(height * 0.37)),
    ]
    draw.line(tail, fill=fur_dark, width=tail_width, joint="curve")
    draw.line(tail, fill=fur, width=max(4, tail_width - 8), joint="curve")

    head_r = int(s * 0.18)
    head_cy = int(height * 0.35)
    left_ear = [(cx - int(s * 0.14), head_cy - int(s * 0.11)), (cx - int(s * 0.25), head_cy - int(s * 0.30)), (cx - int(s * 0.04), head_cy - int(s * 0.23))]
    right_ear = [(cx + int(s * 0.14), head_cy - int(s * 0.11)), (cx + int(s * 0.25), head_cy - int(s * 0.30)), (cx + int(s * 0.04), head_cy - int(s * 0.23))]
    draw.polygon(left_ear, fill=fur, outline=fur_dark)
    draw.polygon(right_ear, fill=fur, outline=fur_dark)
    draw.polygon([(cx - int(s * 0.13), head_cy - int(s * 0.15)), (cx - int(s * 0.20), head_cy - int(s * 0.26)), (cx - int(s * 0.06), head_cy - int(s * 0.21))], fill=(255, 188, 185, 255))
    draw.polygon([(cx + int(s * 0.13), head_cy - int(s * 0.15)), (cx + int(s * 0.20), head_cy - int(s * 0.26)), (cx + int(s * 0.06), head_cy - int(s * 0.21))], fill=(255, 188, 185, 255))

    head = (cx - head_r, head_cy - head_r, cx + head_r, head_cy + head_r)
    draw.ellipse(head, fill=fur, outline=fur_dark, width=max(3, s // 110))

    for offset in (-0.07, 0.0, 0.07):
        x = cx + int(s * offset)
        draw.line((x, head_cy - int(s * 0.16), x - int(s * 0.035), head_cy - int(s * 0.05)), fill=fur_dark, width=max(2, s // 140))

    eye_y = head_cy - int(s * 0.03)
    eye_dx = int(s * 0.075)
    eye_w = int(s * 0.035)
    eye_h = int(s * 0.052)
    for ex in (cx - eye_dx, cx + eye_dx):
        draw.ellipse((ex - eye_w, eye_y - eye_h, ex + eye_w, eye_y + eye_h), fill=(38, 69, 66, 255))
        draw.ellipse((ex - eye_w // 3, eye_y - eye_h // 2, ex, eye_y - eye_h // 5), fill=(255, 255, 245, 245))

    nose_y = head_cy + int(s * 0.045)
    draw.polygon([(cx, nose_y + int(s * 0.018)), (cx - int(s * 0.025), nose_y - int(s * 0.012)), (cx + int(s * 0.025), nose_y - int(s * 0.012))], fill=(216, 102, 119, 255))
    draw.arc((cx - int(s * 0.055), nose_y, cx, nose_y + int(s * 0.07)), 5, 85, fill=(96, 69, 63, 255), width=max(2, s // 170))
    draw.arc((cx, nose_y, cx + int(s * 0.055), nose_y + int(s * 0.07)), 95, 175, fill=(96, 69, 63, 255), width=max(2, s // 170))

    whisker_y = nose_y + int(s * 0.01)
    for side in (-1, 1):
        for dy in (-0.025, 0.0, 0.025):
            draw.line(
                (
                    cx + side * int(s * 0.035),
                    whisker_y + int(s * dy),
                    cx + side * int(s * 0.19),
                    whisker_y + int(s * (dy - 0.02 * side)),
                ),
                fill=(97, 73, 66, 170),
                width=max(1, s // 230),
            )

    paw_y = int(height * 0.71)
    for px in (cx - int(s * 0.09), cx + int(s * 0.09)):
        draw.ellipse((px - int(s * 0.055), paw_y - int(s * 0.04), px + int(s * 0.055), paw_y + int(s * 0.04)), fill=(255, 207, 141, 255), outline=fur_dark, width=max(2, s // 160))
        for toe in (-1, 0, 1):
            draw.arc((px + toe * int(s * 0.022) - int(s * 0.013), paw_y - int(s * 0.010), px + toe * int(s * 0.022) + int(s * 0.013), paw_y + int(s * 0.027)), 200, 340, fill=fur_dark, width=max(1, s // 260))

    for i in range(9):
        angle = (i / 9) * math.tau
        px = int(width * 0.10 + (seed[i + 1] / 255) * width * 0.80)
        py = int(height * 0.08 + (seed[i + 10] / 255) * height * 0.35)
        r = max(2, int(s * (0.006 + (seed[i + 20] / 255) * 0.010)))
        draw.ellipse((px - r, py - r, px + r, py + r), fill=(*accent, 85))
        draw.arc((px - r * 3, py - r * 3, px + r * 3, py + r * 3), int(angle * 18), int(angle * 18 + 120), fill=(*accent, 75), width=max(1, r // 2))

    img.convert("RGB").save(output_path, "PNG", optimize=True)


def _render_generic_image(prompt: str, style: str, output_path: Path, size: tuple[int, int]) -> None:
    from PIL import Image, ImageDraw  # type: ignore

    width, height = size
    digest = hashlib.sha256((prompt + style).encode("utf-8", errors="ignore")).digest()
    palettes = [
        ((249, 241, 225), (110, 157, 146), (225, 127, 91)),
        ((236, 245, 247), (87, 132, 184), (237, 187, 83)),
        ((250, 240, 243), (151, 107, 158), (86, 166, 142)),
        ((241, 246, 232), (95, 145, 114), (219, 135, 88)),
    ]
    bg1, bg2, accent = palettes[digest[0] % len(palettes)]
    img = Image.new("RGB", size, bg1)
    draw = ImageDraw.Draw(img)
    _draw_soft_gradient(draw, width, height, bg1, bg2)
    s = min(width, height)
    for i in range(18):
        x = int((digest[(i * 3 + 1) % len(digest)] / 255) * width)
        y = int((digest[(i * 3 + 2) % len(digest)] / 255) * height)
        r = max(18, int(s * (0.04 + (digest[(i * 3 + 3) % len(digest)] / 255) * 0.16)))
        color = _color_lerp(accent, (255, 255, 255), 0.25 + (i % 3) * 0.18)
        if i % 3 == 0:
            draw.ellipse((x - r, y - r, x + r, y + r), fill=color, outline=(255, 255, 255), width=max(2, s // 180))
        elif i % 3 == 1:
            draw.rounded_rectangle((x - r, y - r // 2, x + r, y + r // 2), radius=max(8, r // 5), fill=color, outline=(255, 255, 255), width=max(2, s // 180))
        else:
            draw.polygon([(x, y - r), (x + r, y + r // 2), (x - r, y + r // 2)], fill=color)

    title_font = _load_font(max(20, s // 30))
    text_font = _load_font(max(16, s // 44))
    panel = (int(width * 0.08), int(height * 0.70), int(width * 0.92), int(height * 0.91))
    draw.rounded_rectangle(panel, radius=max(12, s // 40), fill=(255, 255, 255), outline=accent, width=max(2, s // 190))
    if title_font:
        draw.text((panel[0] + int(s * 0.03), panel[1] + int(s * 0.025)), "Local Image Render", fill=(36, 55, 59), font=title_font)
    if text_font:
        y = panel[1] + int(s * 0.078)
        for line in _wrap_text(prompt, max(12, width // max(18, s // 34))):
            draw.text((panel[0] + int(s * 0.03), y), line, fill=(62, 75, 78), font=text_font)
            y += int(s * 0.045)
    img.save(output_path, "PNG", optimize=True)


def _render_local_image(prompt: str, style: str, output_path: Path, size: tuple[int, int]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if _is_cat_prompt(prompt):
        _render_cat_image(prompt, style, output_path, size)
    else:
        _render_generic_image(prompt, style, output_path, size)


def _image_provider_mode() -> str:
    mode = os.getenv("TIANGONG_IMAGE_PROVIDER_MODE", "auto").strip().lower()
    if mode in {"off", "disable", "disabled", "none"}:
        return "local"
    if mode in {"provider", "model", "provider_only", "model_only"}:
        return "provider_only"
    if mode in {"local", "fallback"}:
        return "local"
    return "auto"


def _provider_config_status(context: TurnContext) -> tuple[bool, str]:
    config = getattr(context, "model_config", None)
    if config is None:
        return False, "model_config_missing"
    has_key = bool(getattr(config, "has_real_api_key", False) or getattr(config, "api_key", ""))
    if not has_key:
        return False, "api_key_missing"
    if not getattr(config, "base_url", ""):
        return False, "base_url_missing"
    return True, ""


def _local_media_dependency_status() -> dict[str, Any]:
    modules = ("PIL", "pytesseract", "cv2", "easyocr", "paddleocr", "whisper")
    binaries = ("ffmpeg", "ffprobe")
    return {
        "python_modules": {name: importlib.util.find_spec(name) is not None for name in modules},
        "binaries": {name: bool(shutil.which(name)) for name in binaries},
    }


def _is_mimo_config(config: Any) -> bool:
    haystack = " ".join(str(getattr(config, key, "") or "").lower() for key in ("provider", "base_url", "model"))
    return "mimo" in haystack or "xiaomimimo" in haystack


def _image_generation_url(config: Any) -> str:
    explicit = os.getenv("TIANGONG_IMAGE_GENERATION_URL", "").strip()
    if explicit:
        return explicit
    base = os.getenv("TIANGONG_IMAGE_BASE_URL", "").strip() or str(getattr(config, "base_url", "") or "").strip()
    base = base.rstrip("/")
    if base.endswith("/images/generations"):
        return base
    if base.endswith("/chat/completions"):
        return base[: -len("/chat/completions")] + "/images/generations"
    if base.endswith("/v1"):
        return f"{base}/images/generations"
    return f"{base}/images/generations"


def _image_generation_model(config: Any, invocation: ToolInvocation) -> str:
    return str(
        invocation.arguments.get("image_model")
        or invocation.arguments.get("imageModel")
        or os.getenv("TIANGONG_IMAGE_MODEL", "").strip()
        or getattr(config, "model", "")
        or ""
    ).strip()


def _provider_timeout(config: Any) -> float:
    raw = os.getenv("TIANGONG_IMAGE_TIMEOUT_SEC", "").strip()
    if not raw:
        raw = str(getattr(config, "timeout", 45) or 45)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 45.0
    return max(8.0, min(value, 120.0))


def _image_generation_headers(config: Any) -> dict[str, str]:
    api_key = str(getattr(config, "api_key", "") or "")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if _is_mimo_config(config):
        headers["api-key"] = api_key
    return headers


def _chat_completions_url(config: Any) -> str:
    explicit = os.getenv("TIANGONG_VISION_CHAT_COMPLETIONS_URL", "").strip()
    if explicit:
        return explicit
    base = str(getattr(config, "base_url", "") or "").strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/chat/completions"


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


def _model_name_has_image_marker(config: Any) -> bool:
    text = " ".join(
        str(getattr(config, key, "") or "").lower()
        for key in ("provider", "base_url", "model")
    )
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


def _configured_image_support(config: Any) -> bool | None:
    for name in ("image_input", "image_input_supported", "multimodal_input", "multimodal_input_supported"):
        value = _truthy_capability(getattr(config, name, None))
        if value is not None:
            return value
    for env_name in ("TIANGONG_IMAGE_INPUT", "TIANGONG_IMAGE_INPUT_SUPPORTED", "TIANGONG_MULTIMODAL_INPUT"):
        value = _truthy_capability(os.getenv(env_name, ""))
        if value is not None:
            return value
    return None


def _model_supports_image_input(config: Any) -> bool:
    configured = _configured_image_support(config)
    if configured is not None:
        return configured
    factsheet = _provider_factsheet_for_config(config)
    if factsheet:
        image = _truthy_capability(factsheet.get("image_input_supported"))
        multi = _truthy_capability(factsheet.get("multimodal_input_supported"))
        if image is True or multi is True:
            return True
        if image is False and multi is False and not _model_name_has_image_marker(config):
            return False
    return _model_name_has_image_marker(config)


def _vision_model(config: Any, invocation: ToolInvocation) -> str:
    return str(
        invocation.arguments.get("vision_model")
        or invocation.arguments.get("visionModel")
        or os.getenv("TIANGONG_VISION_MODEL", "").strip()
        or getattr(config, "model", "")
        or ""
    ).strip()


def _image_mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".avif": "image/avif",
    }.get(suffix, "application/octet-stream")


def _max_inline_image_bytes() -> int:
    raw = os.getenv("TIANGONG_VISION_MAX_IMAGE_BYTES", "").strip()
    try:
        value = int(raw) if raw else 12 * 1024 * 1024
    except (TypeError, ValueError):
        value = 12 * 1024 * 1024
    return max(256 * 1024, min(value, 40 * 1024 * 1024))


def _image_data_url(path: Path) -> tuple[str, int]:
    size = path.stat().st_size
    max_bytes = _max_inline_image_bytes()
    if size > max_bytes:
        raise ValueError(f"image_too_large_for_inline_vision:{size}>{max_bytes}")
    raw = path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{_image_mime_type(path)};base64,{encoded}", size


def _message_content_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
        return "\n".join(part.strip() for part in parts if part and part.strip()).strip()
    if isinstance(value, dict):
        text = value.get("text") or value.get("content") or value.get("answer")
        return str(text or "").strip()
    return ""


def _extract_chat_completion_text(data: Any) -> str:
    try:
        choices = data.get("choices") if isinstance(data, dict) else None
        first = choices[0] if isinstance(choices, list) and choices else {}
        message = first.get("message") if isinstance(first, dict) else {}
        content = message.get("content") if isinstance(message, dict) else ""
        text = _message_content_to_text(content)
        if text:
            return text
        return _message_content_to_text(first.get("text") if isinstance(first, dict) else "")
    except Exception:
        return ""
    return ""


def _provider_vision_error_code(status_code: int | None, detail: str) -> str:
    compact = str(detail or "").lower()
    unsupported_markers = (
        "image_url",
        "multimodal",
        "vision",
        "not support",
        "unsupported",
        "does not support",
        "invalid content type",
        "invalid image",
    )
    if status_code in {400, 404, 405, 415} and any(marker in compact for marker in unsupported_markers):
        return "provider_vision_unsupported"
    if status_code in {401, 403}:
        return "provider_auth_error"
    if status_code in {408, 429, 500, 502, 503, 504}:
        return "provider_vision_retryable"
    return "provider_vision_error"


def _apply_multimodal_thinking_payload(payload: dict[str, Any], config: Any) -> None:
    provider = str(getattr(config, "provider", "") or "").strip().lower()
    enabled = bool(getattr(config, "thinking_enabled", False))
    depth = str(getattr(config, "thinking_depth", "") or "").strip().lower()
    if provider in {"deepseek", "deepseek_v4", "zhipu", "glm", "mimo"}:
        payload["thinking"] = {"type": "enabled" if enabled else "disabled"}
        return
    if provider in {"qwen", "dashscope"}:
        payload["enable_thinking"] = enabled
        if enabled and depth in {"deep", "high", "max", "xhigh"}:
            payload["thinking_budget"] = 8192 if depth in {"deep", "high"} else 16384
        return
    if provider == "minimax" and enabled:
        payload["reasoning_split"] = True


def _try_provider_image_inspect(invocation: ToolInvocation, context: TurnContext, target: Path, meta: dict[str, Any]) -> dict[str, Any]:
    ready, reason = _provider_config_status(context)
    if not ready:
        return {"ok": False, "skipped": True, "error_code": reason}
    config = context.model_config
    if not _model_supports_image_input(config):
        return {
            "ok": False,
            "skipped": True,
            "error_code": "model_image_input_not_declared",
            "provider": getattr(config, "provider", ""),
            "model": getattr(config, "model", ""),
        }
    model = _vision_model(config, invocation)
    if not model:
        return {"ok": False, "skipped": True, "error_code": "vision_model_missing"}
    try:
        data_url, image_bytes = _image_data_url(target)
    except Exception as exc:
        return {
            "ok": False,
            "error_code": "image_prepare_failed",
            "detail": str(exc)[:300],
            "provider": getattr(config, "provider", ""),
            "model": model,
        }
    question = str(invocation.arguments.get("question") or context.user_message or "").strip()
    if not question:
        question = "请分析这张图片的可见内容，包含文字、表格、界面、对象、关键信息和可能的用途。"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是严谨的图片理解与 OCR 助手。请只依据图片可见内容回答；"
                    "如果图片包含界面、表格、图纸或截图，请概括结构并提取关键文字。"
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        "stream": False,
    }
    _apply_multimodal_thinking_payload(payload, config)
    url = _chat_completions_url(config)
    headers = _image_generation_headers(config)
    api_key = str(getattr(config, "api_key", "") or "")
    request = urllib.request.Request(
        url=url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    try:
        with urlopen_with_policy(request, timeout=_provider_timeout(config), allow_loopback_http=True, purpose="provider_image_inspect") as response:
            raw_body = response.read()
    except urllib.error.HTTPError as exc:
        detail = _http_error_detail(exc, api_key)
        return {
            "ok": False,
            "error_code": _provider_vision_error_code(exc.code, detail),
            "status_code": exc.code,
            "provider": getattr(config, "provider", ""),
            "model": model,
            "endpoint": url,
            "detail": detail[:500],
        }
    except Exception as exc:
        detail = redact_text(str(exc), [api_key])
        return {
            "ok": False,
            "error_code": _provider_vision_error_code(None, detail),
            "provider": getattr(config, "provider", ""),
            "model": model,
            "endpoint": url,
            "detail": detail[:500],
        }
    try:
        data = json.loads(raw_body.decode("utf-8", errors="replace"))
    except Exception as exc:
        return {
            "ok": False,
            "error_code": "provider_vision_response_parse_failed",
            "detail": str(exc)[:300],
            "provider": getattr(config, "provider", ""),
            "model": model,
            "endpoint": url,
        }
    answer = _extract_chat_completion_text(data)
    if not answer:
        return {
            "ok": False,
            "error_code": "provider_vision_empty_answer",
            "provider": getattr(config, "provider", ""),
            "model": model,
            "endpoint": url,
            "response_keys": list(data.keys())[:12] if isinstance(data, dict) else [],
        }
    return {
        "ok": True,
        "provider": getattr(config, "provider", ""),
        "model": model,
        "endpoint": url,
        "image_bytes": image_bytes,
        "image_meta": meta,
        "answer": answer,
    }


def _http_error_detail(exc: urllib.error.HTTPError, api_key: str) -> str:
    try:
        detail = exc.read().decode("utf-8", errors="replace")
    except Exception:
        detail = str(exc)
    return redact_text(detail[:1200], [api_key])


def _provider_image_error_code(status_code: int | None, detail: str) -> str:
    compact = str(detail or "").lower()
    unsupported_markers = (
        "not found",
        "unknown url",
        "unsupported",
        "not support",
        "does not support",
        "model_not_support",
        "invalid endpoint",
        "images/generations",
        "image generation",
    )
    if status_code in {400, 404, 405, 415} or any(marker in compact for marker in unsupported_markers):
        return "provider_image_unsupported"
    if status_code in {401, 403}:
        return "provider_auth_error"
    if status_code in {408, 429, 500, 502, 503, 504}:
        return "provider_image_retryable"
    return "provider_image_error"


def _looks_like_image_bytes(raw: bytes) -> bool:
    return raw.startswith(b"\x89PNG\r\n\x1a\n") or raw.startswith(b"\xff\xd8") or raw.startswith(b"GIF8") or raw.startswith(b"RIFF") or raw.startswith(b"BM")


def _decode_b64_image(value: str) -> bytes | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith("data:image/") and "," in text:
        text = text.split(",", 1)[1]
    if len(text) < 40:
        return None
    if not re.fullmatch(r"[A-Za-z0-9+/=\s_-]+", text):
        return None
    try:
        raw = base64.b64decode(text.replace("\n", "").replace("\r", ""), validate=False)
    except Exception:
        return None
    return raw if _looks_like_image_bytes(raw) else None


def _collect_b64_images(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key).lower()
            if isinstance(item, str) and key_text in {"b64_json", "image_base64", "base64", "base64_image", "image"}:
                found.append(item)
            else:
                found.extend(_collect_b64_images(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_collect_b64_images(item))
    return found


def _collect_image_urls(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key).lower()
            if isinstance(item, str) and key_text in {"url", "image_url", "image"} and item.startswith("http"):
                found.append(item)
            else:
                found.extend(_collect_image_urls(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_collect_image_urls(item))
    return found


def _save_image_bytes_as_png(raw: bytes, output_path: Path) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image  # type: ignore

        with Image.open(io.BytesIO(raw)) as img:
            mode = "RGBA" if ("A" in img.getbands()) else "RGB"
            img.convert(mode).save(output_path, "PNG", optimize=True)
    except Exception as exc:
        if not _looks_like_image_bytes(raw):
            return {"ok": False, "error_code": "provider_image_invalid_bytes", "detail": str(exc)[:300]}
        output_path.write_bytes(raw)
    meta = image_meta(output_path)
    if not meta.get("width") or not meta.get("height"):
        return {"ok": False, "error_code": "provider_image_invalid_file", "detail": "saved file is not readable as image"}
    return {"ok": True, "image_meta": meta}


def _download_provider_image(url: str, config: Any, output_path: Path) -> dict[str, Any]:
    request = urllib.request.Request(url=url, method="GET", headers={"Accept": "image/*,*/*", "User-Agent": "TiangongImageProvider/1.0"})
    with urlopen_with_policy(request, timeout=_provider_timeout(config), allow_loopback_http=True, purpose="provider_image_download") as response:
        raw = response.read()
    return _save_image_bytes_as_png(raw, output_path)


def _try_provider_image_generation(invocation: ToolInvocation, context: TurnContext, prompt: str, style: str, size: tuple[int, int], output_path: Path) -> dict[str, Any]:
    mode = _image_provider_mode()
    if mode == "local":
        return {"ok": False, "skipped": True, "error_code": "image_provider_mode_local"}
    ready, reason = _provider_config_status(context)
    if not ready:
        return {"ok": False, "skipped": True, "error_code": reason}

    config = context.model_config
    model = _image_generation_model(config, invocation)
    if not model:
        return {"ok": False, "skipped": True, "error_code": "image_model_missing"}

    width, height = size
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "size": f"{width}x{height}",
        "n": 1,
        "response_format": "b64_json",
    }
    if style:
        payload["style"] = style
    url = _image_generation_url(config)
    headers = _image_generation_headers(config)
    api_key = str(getattr(config, "api_key", "") or "")
    request = urllib.request.Request(
        url=url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    try:
        with urlopen_with_policy(request, timeout=_provider_timeout(config), allow_loopback_http=True, purpose="provider_image_generate") as response:
            raw_body = response.read()
    except urllib.error.HTTPError as exc:
        detail = _http_error_detail(exc, api_key)
        return {
            "ok": False,
            "error_code": _provider_image_error_code(exc.code, detail),
            "status_code": exc.code,
            "provider": getattr(config, "provider", ""),
            "model": model,
            "endpoint": url,
            "detail": detail[:500],
        }
    except Exception as exc:
        detail = redact_text(str(exc), [api_key])
        return {
            "ok": False,
            "error_code": _provider_image_error_code(None, detail),
            "provider": getattr(config, "provider", ""),
            "model": model,
            "endpoint": url,
            "detail": detail[:500],
        }

    try:
        data = json.loads(raw_body.decode("utf-8", errors="replace"))
    except Exception as exc:
        return {"ok": False, "error_code": "provider_image_response_parse_failed", "detail": str(exc)[:300], "provider": getattr(config, "provider", ""), "model": model, "endpoint": url}

    for item in _collect_b64_images(data):
        raw_image = _decode_b64_image(item)
        if raw_image:
            saved = _save_image_bytes_as_png(raw_image, output_path)
            if saved.get("ok"):
                return {"ok": True, "renderer": "provider_images_generations", "provider": getattr(config, "provider", ""), "model": model, "endpoint": url, "response_kind": "base64", **saved}

    for item_url in _collect_image_urls(data):
        try:
            saved = _download_provider_image(item_url, config, output_path)
        except Exception as exc:
            return {"ok": False, "error_code": "provider_image_download_failed", "detail": redact_text(str(exc), [api_key])[:500], "provider": getattr(config, "provider", ""), "model": model, "endpoint": url}
        if saved.get("ok"):
            return {"ok": True, "renderer": "provider_images_generations", "provider": getattr(config, "provider", ""), "model": model, "endpoint": url, "response_kind": "url", **saved}

    return {
        "ok": False,
        "error_code": "provider_image_no_image_in_response",
        "provider": getattr(config, "provider", ""),
        "model": model,
        "endpoint": url,
        "response_keys": list(data.keys())[:12] if isinstance(data, dict) else [],
    }

# ---------------- 图片解析 ----------------

def image_inspect_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    try:
        target, norm = _read_path(context, invocation.arguments.get("image_path") or invocation.arguments.get("path") or "")
        if not target.exists():
            return _fail(invocation, "图片不存在。", "file_not_found")
        meta = image_meta(target)
        image_type = "screenshot_or_document" if meta.get("width") and meta.get("height") and meta.get("width", 0) > meta.get("height", 0) else "image"
        vision = _try_provider_image_inspect(invocation, context, target, meta)
        if vision.get("ok"):
            answer = str(vision.get("answer") or "").strip()
            data = _data(
                invocation.tool_name,
                image_path=str(target),
                image_meta=meta,
                image_type=image_type,
                answer=answer,
                human_readable_summary=answer,
                summary=answer[:900],
                provider=vision.get("provider"),
                model=vision.get("model"),
                renderer="provider_multimodal_chat_completions",
                normalized_host_path=norm,
                confidence="medium",
            )
            data["evidence_refs"] = [
                {
                    "evidence_id": "provider_vision_answer",
                    "kind": "multimodal_model_answer",
                    "content_summary": answer[:500],
                    "confidence": "medium",
                }
            ]
            save_media_context(context.workspace, meta.get("sha256") or str(target), data)
            return _ok(invocation, answer or f"图片解析完成：{target.name}", data)

        data = _data(
            invocation.tool_name,
            image_path=str(target),
            image_meta=meta,
            image_type=image_type,
            summary="已读取图片元信息，但当前模型/接口未完成多模态图片理解。",
            no_answer_reason=vision.get("error_code") or "provider_vision_unavailable",
            provider=vision.get("provider"),
            model=vision.get("model"),
            provider_attempt=vision,
            limitations=["未获得模型视觉回答；不会根据文件名编造图片内容。"],
            normalized_host_path=norm,
            confidence="low",
        )
        save_media_context(context.workspace, meta.get("sha256") or str(target), data)
        return _fail(invocation, "图片元信息已读取，但模型视觉调用未完成。", str(data.get("no_answer_reason") or "provider_vision_unavailable"), data)
    except WorkspaceViolation as exc:
        return _fail(invocation, str(exc), "workspace_violation")
    except Exception as exc:
        return _fail(invocation, f"图片解析失败：{exc}", "image_inspect_failed")


def image_ocr_parse_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    try:
        target, norm = _read_path(context, invocation.arguments.get("image_path") or invocation.arguments.get("path") or "")
        result = try_ocr_image(target, str(invocation.arguments.get("language_hint") or "auto"))
        provider_ocr_result: dict[str, Any] | None = None
        meta = image_meta(target)
        if not result.get("ok"):
            provider_args = dict(invocation.arguments)
            provider_args["question"] = (
                str(invocation.arguments.get("question") or "").strip()
                or "请对这张图片执行 OCR，尽量逐行提取可见文字；如果是界面截图或表格，请保留结构和关键字段。"
            )
            provider_invocation = ToolInvocation(invocation.tool_name, provider_args, invocation.step_id, invocation.risk_level, invocation.reason)
            provider_ocr_result = _try_provider_image_inspect(provider_invocation, context, target, meta)
            if provider_ocr_result.get("ok"):
                text = str(provider_ocr_result.get("answer") or "").strip()
                data = _data(
                    invocation.tool_name,
                    image_path=str(target),
                    image_meta=meta,
                    full_text=text,
                    blocks=[],
                    ocr_engine="provider_multimodal_chat_completions",
                    provider=provider_ocr_result.get("provider"),
                    model=provider_ocr_result.get("model"),
                    local_ocr_error=result.get("error_code"),
                    dependency_status=_local_media_dependency_status(),
                    normalized_host_path=norm,
                    confidence="medium",
                )
                if text:
                    data["evidence_refs"] = [{"evidence_id": "provider_ocr_text", "kind": "ocr_text", "content_summary": text[:300], "confidence": "medium"}]
                save_media_context(context.workspace, meta.get("sha256") or str(target), data)
                return _ok(invocation, "OCR 已由多模态模型完成。", data)
        if not result.get("ok"):
            data = _data(invocation.tool_name, image_path=str(target), image_meta=meta, no_answer_reason=result.get("error_code"), next_action="安装/接入 OCR 引擎，如 PaddleOCR、RapidOCR 或 pytesseract。", retryable=True, normalized_host_path=norm, confidence="low")
            return _fail(invocation, result.get("summary", "OCR 不可用。"), result.get("error_code", "ocr_failed"), data)
        text = result.get("text", "")
        data = _data(invocation.tool_name, image_path=str(target), image_meta=meta, full_text=text, blocks=result.get("blocks", []), average_confidence=result.get("average_confidence"), ocr_engine=result.get("engine"), normalized_host_path=norm, confidence="medium")
        if text:
            data["evidence_refs"] = [{"evidence_id": "ocr_full_text", "kind": "ocr_text", "content_summary": text[:300], "confidence": "medium"}]
        save_media_context(context.workspace, meta.get("sha256") or str(target), data)
        return _ok(invocation, "OCR 完成。" if text else "OCR 完成，但未识别到文本。", data)
    except WorkspaceViolation as exc:
        return _fail(invocation, str(exc), "workspace_violation")
    except Exception as exc:
        return _fail(invocation, f"OCR 失败：{exc}", "ocr_failed")


def image_layout_parse_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    try:
        target, norm = _read_path(context, invocation.arguments.get("image_path") or invocation.arguments.get("path") or "")
        meta = image_meta(target)
        w, h = int(meta.get("width") or 0), int(meta.get("height") or 0)
        regions = []
        if w and h:
            regions = [
                {"region_id": "top", "region_type": "header_or_top_area", "bbox": [0, 0, w, max(1, h // 4)], "confidence": "low", "short_desc": "基于图像尺寸的顶部区域。"},
                {"region_id": "middle", "region_type": "main_area", "bbox": [0, h // 4, w, max(1, h // 2)], "confidence": "low", "short_desc": "基于图像尺寸的主体区域。"},
                {"region_id": "bottom", "region_type": "footer_or_bottom_area", "bbox": [0, (h * 3) // 4, w, max(1, h // 4)], "confidence": "low", "short_desc": "基于图像尺寸的底部区域。"},
            ]
        data = _data(invocation.tool_name, image_path=str(target), image_meta=meta, layout_type="lightweight", regions=regions, reading_order=[r["region_id"] for r in regions], page_structure_summary="轻量布局解析；精细文档版面需接入 PaddleOCR PP-Structure / Layout 模型。", normalized_host_path=norm, confidence="low")
        data["evidence_refs"] = [{"evidence_id": r["region_id"], "kind": "region", "bbox": r["bbox"], "content_summary": r["short_desc"], "confidence": r["confidence"]} for r in regions]
        return _ok(invocation, "图片布局轻量解析完成。", data)
    except WorkspaceViolation as exc:
        return _fail(invocation, str(exc), "workspace_violation")
    except Exception as exc:
        return _fail(invocation, f"布局解析失败：{exc}", "layout_failed")


def image_region_query_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    try:
        target, norm = _read_path(context, invocation.arguments.get("image_path") or invocation.arguments.get("path") or "")
        question = str(invocation.arguments.get("question") or context.user_message or "").strip()
        bbox = invocation.arguments.get("bbox") or []
        meta = image_meta(target)
        matched = []
        if isinstance(bbox, list) and len(bbox) == 4:
            matched.append({"bbox": bbox, "match_reason": "用户提供 bbox。", "local_answer": "已定位到指定区域；语义识别需结合 OCR/多模态 Provider。", "confidence": "medium"})
        else:
            matched.append({"bbox": [], "match_reason": str(invocation.arguments.get("region_hint") or question)[:120], "local_answer": "未提供精确 bbox，当前返回区域定位建议。", "confidence": "low"})
        data = _data(invocation.tool_name, image_path=str(target), image_meta=meta, question=question, matched_regions=matched, final_answer=matched[0]["local_answer"], uncertainty="需要 OCR/grounding 模型提升局部语义定位。", normalized_host_path=norm, confidence=matched[0]["confidence"])
        data["evidence_refs"] = [{"evidence_id": "region_1", "kind": "region", "bbox": matched[0].get("bbox", []), "content_summary": matched[0]["local_answer"], "confidence": matched[0]["confidence"]}]
        return _ok(invocation, "局部区域查询完成。", data)
    except WorkspaceViolation as exc:
        return _fail(invocation, str(exc), "workspace_violation")
    except Exception as exc:
        return _fail(invocation, f"区域查询失败：{exc}", "region_query_failed")


def image_compare_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    try:
        a, norm_a = _read_path(context, invocation.arguments.get("image_path_a") or "")
        b, norm_b = _read_path(context, invocation.arguments.get("image_path_b") or "")
        ma, mb = image_meta(a), image_meta(b)
        diffs = []
        if ma.get("sha256") == mb.get("sha256"):
            sim = 1.0
        else:
            sim = 0.5
            if ma.get("width") != mb.get("width") or ma.get("height") != mb.get("height"):
                diffs.append({"diff_type": "size_changed", "description": f"尺寸不同：{ma.get('width')}x{ma.get('height')} vs {mb.get('width')}x{mb.get('height')}", "severity": "medium", "confidence": "high"})
            if ma.get("sha256") != mb.get("sha256"):
                diffs.append({"diff_type": "content_hash_changed", "description": "文件哈希不同，图片内容或编码存在差异。", "severity": "medium", "confidence": "high"})
        data = _data(invocation.tool_name, image_path_a=str(a), image_path_b=str(b), image_meta_a=ma, image_meta_b=mb, overall_similarity=sim, differences=diffs, normalized_host_path_a=norm_a, normalized_host_path_b=norm_b, confidence="medium")
        data["evidence_refs"] = [{"evidence_id": f"diff_{i+1}", "kind": "diff", "content_summary": d["description"], "confidence": d["confidence"]} for i, d in enumerate(diffs)]
        return _ok(invocation, "图片对比完成。", data)
    except WorkspaceViolation as exc:
        return _fail(invocation, str(exc), "workspace_violation")
    except Exception as exc:
        return _fail(invocation, f"图片对比失败：{exc}", "image_compare_failed")


def image_table_extract_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    # 轻量版：依赖 OCR 文本行，复杂表格需接入表格模型。
    ocr = image_ocr_parse_adapter(invocation, context)
    if not ocr.ok:
        return ocr
    text = ocr.data.get("full_text", "")
    rows = [re.split(r"\s{2,}|\t|,", line.strip()) for line in text.splitlines() if line.strip()]
    tables = []
    if rows:
        tables.append({"headers": rows[0], "rows": rows[1:], "bbox": [], "confidence": "low", "warnings": ["轻量OCR重建表格，复杂线框/合并单元格需表格模型。"]})
    data = _data(invocation.tool_name, image_path=ocr.data.get("image_path"), tables=tables, confidence="low", warnings=["不要把低置信OCR表格当成精确财务/医学数据。"])
    data["evidence_refs"] = [{"evidence_id": "table_1", "kind": "table", "content_summary": f"rows={len(rows)}", "confidence": "low"}] if rows else []
    return _ok(invocation, "表格截图轻量抽取完成。" if tables else "未抽取到表格。", data)


def image_chart_extract_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    inspect = image_inspect_adapter(invocation, context)
    if not inspect.ok:
        return inspect
    data = _data(invocation.tool_name, image_path=inspect.data.get("image_path"), image_meta=inspect.data.get("image_meta"), chart_type=str(invocation.arguments.get("chart_type_hint") or "auto"), trend_summary="当前本地轻量版只生成图表解析请求，不强行读取精确数值。", data_points=[], warnings=["精确读数需接入图表理解模型或人工校验。"], confidence="low")
    return _ok(invocation, "图表截图轻量解析完成。", data)


def image_crop_export_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    try:
        target, norm = _read_path(context, invocation.arguments.get("image_path") or invocation.arguments.get("path") or "")
        bbox = invocation.arguments.get("bbox") or []
        out = _artifact_path(context, invocation.arguments.get("output_name") or safe_output_name("image_crop", ".png"), ".png", "multimedia_outputs")
        result = crop_image(target, bbox, out)
        if not result.get("ok"):
            return _fail(invocation, result.get("summary", "裁切失败。"), result.get("error_code", "crop_failed"), _data(invocation.tool_name, image_path=str(target), bbox=bbox, retryable=True, normalized_host_path=norm))
        data = _data(invocation.tool_name, image_path=str(target), output_path=str(out), bbox=result.get("bbox"), sha256=result.get("sha256"), ready_for_followup=True, normalized_host_path=norm, confidence="high")
        return _ok(invocation, f"已导出裁切图片：{out}", data, [str(out)])
    except WorkspaceViolation as exc:
        return _fail(invocation, str(exc), "workspace_violation")
    except Exception as exc:
        return _fail(invocation, f"图片裁切失败：{exc}", "crop_failed")

# ---------------- 视频解析 ----------------

def video_inspect_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    try:
        target, norm = _read_path(context, invocation.arguments.get("video_path") or invocation.arguments.get("path") or "")
        meta = media_meta(target, "video")
        data = _data(invocation.tool_name, video_path=str(target), video_meta=meta, summary="已读取视频元信息。", normalized_host_path=norm, confidence="medium")
        save_media_context(context.workspace, meta.get("sha256") or str(target), data)
        return _ok(invocation, f"视频读取完成：{target.name}，时长={meta.get('duration_sec')}秒。", data)
    except WorkspaceViolation as exc:
        return _fail(invocation, str(exc), "workspace_violation")
    except Exception as exc:
        return _fail(invocation, f"视频解析失败：{exc}", "video_inspect_failed")


def video_keyframe_extract_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    try:
        target, norm = _read_path(context, invocation.arguments.get("video_path") or invocation.arguments.get("path") or "")
        out_dir_raw = invocation.arguments.get("output_dir") or f"multimedia_outputs/keyframes_{int(time.time())}"
        out_dir = WorkspaceGuard(context.workspace).resolve_for_artifact(out_dir_raw)
        result = extract_keyframes(target, out_dir, float(invocation.arguments.get("interval_sec") or 5), int(invocation.arguments.get("max_frames") or 24))
        if not result.get("ok"):
            return _fail(invocation, result.get("summary", "关键帧提取失败。"), result.get("error_code", "ffmpeg_failed"), _data(invocation.tool_name, video_path=str(target), retryable=True, next_action="安装 ffmpeg 或降低视频处理要求。", normalized_host_path=norm))
        frames = result.get("frames", [])
        data = _data(invocation.tool_name, video_path=str(target), output_dir=str(out_dir), keyframes=frames, frame_count=len(frames), normalized_host_path=norm, confidence="medium")
        data["evidence_refs"] = [{"evidence_id": f"frame_{i+1}", "kind": "keyframe", "content_summary": p, "confidence": "medium"} for i, p in enumerate(frames)]
        return _ok(invocation, f"已提取关键帧 {len(frames)} 张。", data, frames)
    except WorkspaceViolation as exc:
        return _fail(invocation, str(exc), "workspace_violation")
    except Exception as exc:
        return _fail(invocation, f"关键帧提取失败：{exc}", "keyframe_failed")


def video_scene_split_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    key = video_keyframe_extract_adapter(invocation, context)
    if not key.ok:
        return key
    frames = key.data.get("keyframes", [])
    segments = []
    for i, f in enumerate(frames):
        segments.append({"segment_id": f"scene_{i+1}", "approx_time_sec": i * float(invocation.arguments.get("interval_sec") or 5), "keyframe": f, "confidence": "low"})
    data = _data(invocation.tool_name, video_path=key.data.get("video_path"), segments=segments, method="keyframe_interval_lightweight", confidence="low", warnings=["轻量版按关键帧近似分段；高精度需镜头检测模型。"])
    return _ok(invocation, f"视频轻量分段完成：{len(segments)} 段。", data, key.artifacts)


def video_ocr_parse_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    key = video_keyframe_extract_adapter(invocation, context)
    if not key.ok:
        return key
    blocks = []
    for i, frame in enumerate(key.data.get("keyframes", [])):
        result = try_ocr_image(frame, str(invocation.arguments.get("language_hint") or "auto"))
        if result.get("ok") and result.get("text"):
            blocks.append({"time_index": i, "frame": frame, "text": result.get("text"), "confidence": "medium"})
    data = _data(invocation.tool_name, video_path=key.data.get("video_path"), ocr_blocks=blocks, keyframes=key.data.get("keyframes", []), confidence="medium" if blocks else "low")
    if not blocks:
        data.update({"no_answer_reason": "ocr_engine_missing_or_no_text", "next_action": "安装/接入 OCR 引擎后重试。", "retryable": True})
    return _ok(invocation, f"视频OCR完成，识别文本帧 {len(blocks)} 个。", data, key.artifacts)


def video_audio_transcribe_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    try:
        target, norm = _read_path(context, invocation.arguments.get("video_path") or invocation.arguments.get("path") or "")
        payload = {"source_video": str(target), "task": "video_audio_transcribe", "language_hint": invocation.arguments.get("language_hint") or "auto", "provider_status": "asr_provider_required"}
        data = _data(invocation.tool_name, video_path=str(target), transcript="", segments=[], no_answer_reason="asr_provider_required", next_action="接入 Whisper/FunASR/Provider ASR 后执行转写。", retryable=True, normalized_host_path=norm, confidence="low")
        return _ok(invocation, "已生成视频音频转写请求；当前未接入ASR引擎。", data)
    except WorkspaceViolation as exc:
        return _fail(invocation, str(exc), "workspace_violation")
    except Exception as exc:
        return _fail(invocation, f"视频音频转写失败：{exc}", "video_audio_transcribe_failed")


def video_subtitle_extract_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    try:
        target, norm = _read_path(context, invocation.arguments.get("video_path") or invocation.arguments.get("path") or "")
        sidecar = invocation.arguments.get("sidecar_path") or ""
        subtitles = ""
        sidecar_path = ""
        if sidecar:
            s, _ = _read_path(context, sidecar)
            sidecar_path = str(s)
            subtitles = s.read_text(encoding="utf-8", errors="replace")[:20000]
        else:
            for ext in (".srt", ".vtt", ".ass"):
                s = target.with_suffix(ext)
                if s.exists():
                    sidecar_path = str(s)
                    subtitles = s.read_text(encoding="utf-8", errors="replace")[:20000]
                    break
        data = _data(invocation.tool_name, video_path=str(target), sidecar_path=sidecar_path, subtitles=subtitles, no_answer_reason="subtitle_not_found" if not subtitles else "", normalized_host_path=norm, confidence="medium" if subtitles else "low")
        return _ok(invocation, "字幕提取完成。" if subtitles else "未找到外置字幕文件。", data)
    except WorkspaceViolation as exc:
        return _fail(invocation, str(exc), "workspace_violation")
    except Exception as exc:
        return _fail(invocation, f"字幕提取失败：{exc}", "subtitle_extract_failed")


def video_event_timeline_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    inspect = video_inspect_adapter(invocation, context)
    if not inspect.ok:
        return inspect
    duration = inspect.data.get("video_meta", {}).get("duration_sec") or 0
    events = [{"time_sec": 0, "event": "视频开始", "confidence": "high"}]
    if duration:
        events.append({"time_sec": duration, "event": "视频结束", "confidence": "high"})
    data = _data(invocation.tool_name, video_path=inspect.data.get("video_path"), timeline_events=events, confidence="low", warnings=["轻量版只提供元信息时间线；细节事件需关键帧/OCR/ASR进一步分析。"])
    return _ok(invocation, "视频事件时间线轻量生成完成。", data)


def video_compare_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    try:
        a, _ = _read_path(context, invocation.arguments.get("video_path_a") or "")
        b, _ = _read_path(context, invocation.arguments.get("video_path_b") or "")
        ma, mb = media_meta(a, "video"), media_meta(b, "video")
        diffs = []
        for key in ("duration_sec", "width", "height", "video_codec", "audio_codec"):
            if ma.get(key) != mb.get(key):
                diffs.append({"diff_type": key, "a": ma.get(key), "b": mb.get(key), "confidence": "high"})
        data = _data(invocation.tool_name, video_path_a=str(a), video_path_b=str(b), video_meta_a=ma, video_meta_b=mb, differences=diffs, overall_similarity=1.0 if not diffs and ma.get("sha256") == mb.get("sha256") else 0.5, confidence="medium")
        return _ok(invocation, "视频对比完成。", data)
    except WorkspaceViolation as exc:
        return _fail(invocation, str(exc), "workspace_violation")
    except Exception as exc:
        return _fail(invocation, f"视频对比失败：{exc}", "video_compare_failed")

# ---------------- 生成/制作请求 ----------------

def image_generate_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    try:
        prompt = str(invocation.arguments.get("prompt") or context.user_message or "").strip()
        style = str(invocation.arguments.get("style") or "").strip()
        width, height = _parse_image_size(invocation.arguments.get("size") or "1024x1024")
        output_name = _png_output_name(invocation.arguments.get("output_name"), prompt)
        out = _artifact_path(context, output_name, ".png", "multimedia_outputs")
        provider_attempt = _try_provider_image_generation(invocation, context, prompt, style, (width, height), out)
        if provider_attempt.get("ok"):
            meta = image_meta(out)
            content = f"图片已由模型服务生成：\n\n![生成图片]({out})\n\n文件路径：`{out}`"
            data = _data(
                invocation.tool_name,
                prompt=prompt,
                style=style,
                size=f"{width}x{height}",
                output_path=str(out),
                image_path=str(out),
                image_meta=meta,
                artifact_hash=sha256_file(out),
                renderer=str(provider_attempt.get("renderer") or "provider_images_generations"),
                provider_status="generated",
                provider_image_attempt=provider_attempt,
                ready_for_followup=True,
                content=content,
                markdown=content,
                confidence="high",
            )
            return _ok(invocation, f"图片已由模型服务生成：{out}", data, [str(out)])

        if _image_provider_mode() == "provider_only":
            data = _data(
                invocation.tool_name,
                prompt=prompt,
                style=style,
                size=f"{width}x{height}",
                provider_status="failed",
                provider_image_attempt=provider_attempt,
                retryable=provider_attempt.get("error_code") in {"provider_image_retryable"},
                confidence="low",
            )
            return _fail(invocation, f"模型服务图片生成失败：{provider_attempt.get('error_code') or 'unknown'}", str(provider_attempt.get("error_code") or "provider_image_failed"), data)

        _render_local_image(prompt, style, out, (width, height))
        meta = image_meta(out)
        content = f"图片已生成：\n\n![生成图片]({out})\n\n文件路径：`{out}`"
        data = _data(
            invocation.tool_name,
            prompt=prompt,
            style=style,
            size=f"{width}x{height}",
            output_path=str(out),
            image_path=str(out),
            image_meta=meta,
            artifact_hash=sha256_file(out),
            renderer="local_pillow",
            provider_status="fallback_local",
            provider_image_attempt=provider_attempt,
            ready_for_followup=True,
            content=content,
            markdown=content,
            confidence="high",
        )
        return _ok(invocation, f"图片已生成：{out}", data, [str(out)])
    except WorkspaceViolation as exc:
        return _fail(invocation, str(exc), "workspace_violation")
    except Exception as exc:
        data = _data(invocation.tool_name, retryable=True, next_action="检查 Pillow 依赖或换一个输出文件名。", confidence="low")
        return _fail(invocation, f"图片生成失败：{exc}", "image_generate_failed", data)

def image_edit_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    return _provider_request(invocation, context, "图片编辑", {"type": "image_edit", "image_path": invocation.arguments.get("image_path"), "instruction": invocation.arguments.get("instruction") or context.user_message})

def image_inpaint_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    return _provider_request(invocation, context, "图片局部重绘", {"type": "image_inpaint", **invocation.arguments})

def image_background_remove_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    return _provider_request(invocation, context, "图片抠图", {"type": "image_background_remove", **invocation.arguments})

def image_upscale_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    return _provider_request(invocation, context, "图片放大", {"type": "image_upscale", **invocation.arguments})

def image_style_transfer_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    return _provider_request(invocation, context, "图片风格化", {"type": "image_style_transfer", **invocation.arguments})

def image_variation_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    return _provider_request(invocation, context, "图片变体", {"type": "image_variation", **invocation.arguments})

def image_text_poster_generate_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    return _provider_request(invocation, context, "图文海报", {"type": "image_text_poster_generate", **invocation.arguments})

# ---------------- 视频制作/剪辑 ----------------

def storyboard_generate_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    topic = str(invocation.arguments.get("topic") or context.user_message)
    scene_count = int(invocation.arguments.get("scene_count") or 5)
    scenes = [{"scene": i+1, "goal": f"围绕 {topic} 的第 {i+1} 个镜头", "duration_sec": 3, "visual": "待生成", "voiceover": "待生成"} for i in range(max(1, min(scene_count, 30)))]
    return _json_artifact(context, invocation, "分镜", {"type": "storyboard", "topic": topic, "scenes": scenes})

def shot_plan_generate_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    return _json_artifact(context, invocation, "镜头计划", {"type": "shot_plan", "script": invocation.arguments.get("script") or context.user_message, "style": invocation.arguments.get("style") or "", "duration_sec": invocation.arguments.get("duration_sec")})

def video_generate_from_text_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    return _provider_request(invocation, context, "文生视频", {"type": "video_generate_from_text", **invocation.arguments})

def video_generate_from_images_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    return _provider_request(invocation, context, "图生视频", {"type": "video_generate_from_images", **invocation.arguments})

def video_avatar_generate_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    return _provider_request(invocation, context, "数字人视频", {"type": "video_avatar_generate", **invocation.arguments})

def voiceover_generate_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    return _provider_request(invocation, context, "旁白配音", {"type": "voiceover_generate", **invocation.arguments})

def subtitle_burn_in_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    return video_add_subtitles_adapter(invocation, context)

def video_render_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    return _provider_request(invocation, context, "视频渲染", {"type": "video_render", **invocation.arguments})


def video_trim_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    try:
        target, _ = _read_path(context, invocation.arguments.get("video_path") or "")
        out = _artifact_path(context, invocation.arguments.get("output_name") or safe_output_name("video_trim", ".mp4"), ".mp4")
        start = float(invocation.arguments.get("start_sec") or 0)
        end = float(invocation.arguments.get("end_sec") or 0)
        duration_args = []
        if end > start:
            duration_args = ["-t", str(end - start)]
        result = run_ffmpeg(["-ss", str(start), "-i", str(target)] + duration_args + ["-c", "copy", str(out)])
        if not result.get("ok"):
            return _fail(invocation, "视频裁剪失败。", result.get("error_code", "ffmpeg_failed"), _data(invocation.tool_name, ffmpeg=result, retryable=True))
        return _ok(invocation, f"视频裁剪完成：{out}", _data(invocation.tool_name, input_path=str(target), output_path=str(out), artifact_hash=sha256_file(out), confidence="high"), [str(out)])
    except WorkspaceViolation as exc:
        return _fail(invocation, str(exc), "workspace_violation")
    except Exception as exc:
        return _fail(invocation, f"视频裁剪失败：{exc}", "video_trim_failed")


def video_concat_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    try:
        paths = invocation.arguments.get("video_paths") or []
        if not isinstance(paths, list) or len(paths) < 2:
            return _fail(invocation, "至少需要两个视频路径。", "invalid_args")
        resolved = [_read_path(context, p)[0] for p in paths]
        list_file = _artifact_path(context, safe_output_name("concat_list", ".txt"), ".txt")
        list_file.write_text("\n".join(f"file '{p.as_posix()}'" for p in resolved), encoding="utf-8")
        out = _artifact_path(context, invocation.arguments.get("output_name") or safe_output_name("video_concat", ".mp4"), ".mp4")
        result = run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out)])
        if not result.get("ok"):
            return _fail(invocation, "视频拼接失败。", result.get("error_code", "ffmpeg_failed"), _data(invocation.tool_name, ffmpeg=result, retryable=True))
        return _ok(invocation, f"视频拼接完成：{out}", _data(invocation.tool_name, output_path=str(out), artifact_hash=sha256_file(out), input_paths=[str(p) for p in resolved], confidence="high"), [str(out)])
    except WorkspaceViolation as exc:
        return _fail(invocation, str(exc), "workspace_violation")
    except Exception as exc:
        return _fail(invocation, f"视频拼接失败：{exc}", "video_concat_failed")


def video_cut_by_timestamps_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    # 最小可用：按第一个片段裁剪；多片段建议走 concat 后续增强。
    segments = invocation.arguments.get("segments") or []
    if not segments:
        return _fail(invocation, "缺少 segments。", "invalid_args")
    first = segments[0]
    invocation.arguments["start_sec"] = first[0]
    invocation.arguments["end_sec"] = first[1]
    return video_trim_adapter(invocation, context)


def video_add_subtitles_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    try:
        video, _ = _read_path(context, invocation.arguments.get("video_path") or "")
        sub, _ = _read_path(context, invocation.arguments.get("subtitle_path") or "")
        out = _artifact_path(context, invocation.arguments.get("output_name") or safe_output_name("video_subtitles", ".mp4"), ".mp4")
        # ffmpeg subtitles filter 在 Windows 路径中易受转义影响，此处先生成安全请求；真实烧录可在工程环境二次调试。
        return _json_artifact(context, invocation, "字幕烧录", {"type": "subtitle_burn_in", "video_path": str(video), "subtitle_path": str(sub), "suggested_output": str(out), "provider_status": "ffmpeg_filter_ready"})
    except WorkspaceViolation as exc:
        return _fail(invocation, str(exc), "workspace_violation")
    except Exception as exc:
        return _fail(invocation, f"字幕处理失败：{exc}", "subtitle_failed")


def video_add_bgm_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    try:
        video, _ = _read_path(context, invocation.arguments.get("video_path") or "")
        audio, _ = _read_path(context, invocation.arguments.get("audio_path") or "")
        out = _artifact_path(context, invocation.arguments.get("output_name") or safe_output_name("video_bgm", ".mp4"), ".mp4")
        result = run_ffmpeg(["-i", str(video), "-i", str(audio), "-filter_complex", f"[1:a]volume={float(invocation.arguments.get('volume') or 0.35)}[bgm];[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[a]", "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-shortest", str(out)])
        if not result.get("ok"):
            return _fail(invocation, "添加BGM失败。", result.get("error_code", "ffmpeg_failed"), _data(invocation.tool_name, ffmpeg=result, retryable=True))
        return _ok(invocation, f"已添加BGM：{out}", _data(invocation.tool_name, output_path=str(out), artifact_hash=sha256_file(out), confidence="high"), [str(out)])
    except WorkspaceViolation as exc:
        return _fail(invocation, str(exc), "workspace_violation")
    except Exception as exc:
        return _fail(invocation, f"添加BGM失败：{exc}", "video_bgm_failed")


def video_add_transition_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    return _json_artifact(context, invocation, "转场剪辑", {"type": "video_add_transition", **invocation.arguments, "note": "复杂转场需后续接入 MoviePy/ffmpeg filtergraph。"})


def video_resize_reframe_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    try:
        target, _ = _read_path(context, invocation.arguments.get("video_path") or "")
        width = int(invocation.arguments.get("width") or 1080)
        height = int(invocation.arguments.get("height") or 1920)
        out = _artifact_path(context, invocation.arguments.get("output_name") or safe_output_name("video_resize", ".mp4"), ".mp4")
        result = run_ffmpeg(["-i", str(target), "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2", str(out)])
        if not result.get("ok"):
            return _fail(invocation, "视频比例适配失败。", result.get("error_code", "ffmpeg_failed"), _data(invocation.tool_name, ffmpeg=result, retryable=True))
        return _ok(invocation, f"视频比例适配完成：{out}", _data(invocation.tool_name, output_path=str(out), artifact_hash=sha256_file(out), confidence="high"), [str(out)])
    except WorkspaceViolation as exc:
        return _fail(invocation, str(exc), "workspace_violation")
    except Exception as exc:
        return _fail(invocation, f"视频比例适配失败：{exc}", "video_resize_failed")


def video_export_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    try:
        target, _ = _read_path(context, invocation.arguments.get("video_path") or "")
        fmt = str(invocation.arguments.get("format") or "mp4").lstrip(".")
        out = _artifact_path(context, invocation.arguments.get("output_name") or safe_output_name("video_export", "." + fmt), "." + fmt)
        result = run_ffmpeg(["-i", str(target), str(out)])
        if not result.get("ok"):
            return _fail(invocation, "视频导出失败。", result.get("error_code", "ffmpeg_failed"), _data(invocation.tool_name, ffmpeg=result, retryable=True))
        return _ok(invocation, f"视频导出完成：{out}", _data(invocation.tool_name, output_path=str(out), artifact_hash=sha256_file(out), confidence="high"), [str(out)])
    except WorkspaceViolation as exc:
        return _fail(invocation, str(exc), "workspace_violation")
    except Exception as exc:
        return _fail(invocation, f"视频导出失败：{exc}", "video_export_failed")

# ---------------- 音频 ----------------

def audio_transcribe_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    try:
        target, norm = _read_path(context, invocation.arguments.get("audio_path") or invocation.arguments.get("path") or "")
        data = _data(invocation.tool_name, audio_path=str(target), audio_meta=media_meta(target, "audio"), transcript="", segments=[], no_answer_reason="asr_provider_required", next_action="接入 Whisper/FunASR/Provider ASR 后执行转写。", retryable=True, normalized_host_path=norm, confidence="low")
        return _ok(invocation, "已读取音频；当前未接入ASR引擎。", data)
    except WorkspaceViolation as exc:
        return _fail(invocation, str(exc), "workspace_violation")
    except Exception as exc:
        return _fail(invocation, f"音频转写失败：{exc}", "audio_transcribe_failed")


def audio_diarize_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    return _provider_request(invocation, context, "说话人分离", {"type": "audio_diarize", **invocation.arguments})


def audio_summary_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    text = str(invocation.arguments.get("transcript") or "").strip()
    summary = text[:500] if text else "未提供转写文本；请先调用 audio_transcribe 或传入 transcript。"
    return _ok(invocation, "音频摘要完成。", _data(invocation.tool_name, summary=summary, confidence="medium" if text else "low"))


def audio_keywords_extract_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    text = str(invocation.arguments.get("transcript") or "")
    words = [w for w in re.split(r"[\s,，。；;：:！？!?]+", text) if len(w) >= 2]
    top_k = int(invocation.arguments.get("top_k") or 10)
    keywords = []
    seen = set()
    for w in words:
        if w not in seen:
            seen.add(w); keywords.append(w)
        if len(keywords) >= top_k: break
    return _ok(invocation, "关键词提取完成。", _data(invocation.tool_name, keywords=keywords, confidence="low" if not text else "medium"))


def audio_event_detect_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    return _provider_request(invocation, context, "音频事件检测", {"type": "audio_event_detect", **invocation.arguments})


def tts_generate_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    return _provider_request(invocation, context, "TTS配音", {"type": "tts_generate", **invocation.arguments})

def audio_clone_voice_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    if not str(invocation.arguments.get("authorization_note") or "").strip():
        return _fail(invocation, "缺少声音克隆授权说明。", "authorization_required", _data(invocation.tool_name, retryable=True, next_action="补充授权说明后重试。"))
    return _provider_request(invocation, context, "声音克隆", {"type": "audio_clone_voice", **invocation.arguments})

def bgm_generate_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    return _provider_request(invocation, context, "BGM生成", {"type": "bgm_generate", **invocation.arguments})

def audio_mix_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    return _provider_request(invocation, context, "音频混音", {"type": "audio_mix", **invocation.arguments})

def audio_denoise_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    try:
        target, _ = _read_path(context, invocation.arguments.get("audio_path") or "")
        out = _artifact_path(context, invocation.arguments.get("output_name") or safe_output_name("audio_denoise", target.suffix or ".wav"), target.suffix or ".wav")
        result = run_ffmpeg(["-i", str(target), "-af", "afftdn", str(out)])
        if not result.get("ok"):
            return _fail(invocation, "音频降噪失败。", result.get("error_code", "ffmpeg_failed"), _data(invocation.tool_name, ffmpeg=result, retryable=True))
        return _ok(invocation, f"音频降噪完成：{out}", _data(invocation.tool_name, output_path=str(out), artifact_hash=sha256_file(out), confidence="medium"), [str(out)])
    except WorkspaceViolation as exc:
        return _fail(invocation, str(exc), "workspace_violation")
    except Exception as exc:
        return _fail(invocation, f"音频降噪失败：{exc}", "audio_denoise_failed")


def audio_normalize_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    try:
        target, _ = _read_path(context, invocation.arguments.get("audio_path") or "")
        out = _artifact_path(context, invocation.arguments.get("output_name") or safe_output_name("audio_normalize", target.suffix or ".wav"), target.suffix or ".wav")
        result = run_ffmpeg(["-i", str(target), "-af", "loudnorm", str(out)])
        if not result.get("ok"):
            return _fail(invocation, "音频标准化失败。", result.get("error_code", "ffmpeg_failed"), _data(invocation.tool_name, ffmpeg=result, retryable=True))
        return _ok(invocation, f"音频标准化完成：{out}", _data(invocation.tool_name, output_path=str(out), artifact_hash=sha256_file(out), confidence="medium"), [str(out)])
    except WorkspaceViolation as exc:
        return _fail(invocation, str(exc), "workspace_violation")
    except Exception as exc:
        return _fail(invocation, f"音频标准化失败：{exc}", "audio_normalize_failed")


def audio_export_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    try:
        target, _ = _read_path(context, invocation.arguments.get("audio_path") or "")
        fmt = str(invocation.arguments.get("format") or target.suffix.lstrip(".") or "wav")
        out = _artifact_path(context, invocation.arguments.get("output_name") or safe_output_name("audio_export", "." + fmt), "." + fmt)
        result = run_ffmpeg(["-i", str(target), str(out)])
        if not result.get("ok"):
            return _fail(invocation, "音频导出失败。", result.get("error_code", "ffmpeg_failed"), _data(invocation.tool_name, ffmpeg=result, retryable=True))
        return _ok(invocation, f"音频导出完成：{out}", _data(invocation.tool_name, output_path=str(out), artifact_hash=sha256_file(out), confidence="high"), [str(out)])
    except WorkspaceViolation as exc:
        return _fail(invocation, str(exc), "workspace_violation")
    except Exception as exc:
        return _fail(invocation, f"音频导出失败：{exc}", "audio_export_failed")

# ---------------- 结构化抽取/编排 ----------------

def media_entity_extract_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    text = str(invocation.arguments.get("source_text") or context.user_message)
    entities = re.findall(r"[A-Za-z0-9_\-\.]+|[\u4e00-\u9fff]{2,}", text)[:50]
    return _ok(invocation, "多媒体实体轻量抽取完成。", _data(invocation.tool_name, entities=entities, confidence="low"))

def media_kv_extract_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    text = str(invocation.arguments.get("source_text") or context.user_message)
    fields = invocation.arguments.get("fields") or []
    kv = {str(f): "" for f in fields} if isinstance(fields, list) else {}
    return _ok(invocation, "字段抽取模板已生成。", _data(invocation.tool_name, fields=kv, source_preview=text[:500], confidence="low"))

def media_topic_extract_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    text = str(invocation.arguments.get("source_text") or context.user_message)
    topics = list(dict.fromkeys([w for w in re.split(r"[\s,，。；;：:！？!?]+", text) if len(w) >= 2]))[: int(invocation.arguments.get("top_k") or 10)]
    return _ok(invocation, "主题轻量抽取完成。", _data(invocation.tool_name, topics=topics, confidence="low"))

def media_risk_extract_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    text = str(invocation.arguments.get("source_text") or context.user_message)
    markers = [m for m in ["违规", "侵权", "隐私", "密钥", "医疗", "法律", "金融", "儿童", "裸露", "暴力"] if m in text]
    return _ok(invocation, "风险线索轻量抽取完成。", _data(invocation.tool_name, risk_markers=markers, confidence="medium" if markers else "low"))

def media_knowledge_extract_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    text = str(invocation.arguments.get("source_text") or context.user_message)
    bullets = [line.strip() for line in text.splitlines() if line.strip()][:20]
    return _ok(invocation, "知识点轻量抽取完成。", _data(invocation.tool_name, knowledge_points=bullets, confidence="low"))

def multimedia_pipeline_plan_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    goal = str(invocation.arguments.get("goal") or context.user_message)
    plan = ["素材盘点", "图片/视频/音频解析", "脚本与分镜", "生成/剪辑", "字幕/配音", "导出与打包"]
    return _json_artifact(context, invocation, "多媒体流水线", {"type": "multimedia_pipeline_plan", "goal": goal, "steps": plan, "assets": invocation.arguments.get("assets") or []})

def multimedia_asset_manifest_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    paths = invocation.arguments.get("asset_paths") or []
    items = []
    for p in paths if isinstance(paths, list) else []:
        try:
            target, _ = _read_path(context, p)
            items.append(base_file_meta(target))
        except Exception as exc:
            items.append({"path": p, "error": str(exc)})
    return _json_artifact(context, invocation, "素材清单", {"type": "multimedia_asset_manifest", "project_name": invocation.arguments.get("project_name") or "", "items": items})

def multimedia_batch_plan_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    count = int(invocation.arguments.get("count") or 1)
    count = max(1, min(count, 100))
    return _json_artifact(context, invocation, "批量多媒体计划", {"type": "multimedia_batch_plan", "goal": invocation.arguments.get("goal") or context.user_message, "count": count, "template": invocation.arguments.get("template") or ""})

def multimedia_delivery_package_adapter(invocation: ToolInvocation, context: TurnContext) -> ToolResult:
    return _json_artifact(context, invocation, "多媒体交付包", {"type": "multimedia_delivery_package", "project_name": invocation.arguments.get("project_name") or "", "asset_paths": invocation.arguments.get("asset_paths") or [], "note": "当前生成交付清单；如需压缩包可继续调用 create_zip_package。"})
