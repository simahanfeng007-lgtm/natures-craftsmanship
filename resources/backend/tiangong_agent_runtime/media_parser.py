"""多媒体工具轻量解析层。

设计原则：
- 只做本地可交付能力：元信息、hash、关键帧、裁切、ffmpeg封装、OCR可选接入。
- 不硬依赖重型模型；Pillow / ffmpeg / pytesseract / moviepy 均为可选。
- 高阶VLM、OCR、TTS、视频生成由 Provider Bridge 后续接入；当前工具会返回 provider_required，而不是编造结果。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}


def sha256_file(path: str | Path) -> str:
    p = Path(path)
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def base_file_meta(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    data: dict[str, Any] = {"path": str(p), "exists": p.exists(), "suffix": p.suffix.lower()}
    if p.exists() and p.is_file():
        st = p.stat()
        data.update({"size_bytes": st.st_size, "mtime": st.st_mtime, "sha256": sha256_file(p)})
    return data


def _png_size(raw: bytes) -> tuple[int, int] | None:
    if len(raw) >= 24 and raw[:8] == b"\x89PNG\r\n\x1a\n":
        return int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")
    return None


def _jpeg_size(raw: bytes) -> tuple[int, int] | None:
    if len(raw) < 4 or not raw.startswith(b"\xff\xd8"):
        return None
    i = 2
    while i + 9 < len(raw):
        if raw[i] != 0xFF:
            i += 1
            continue
        marker = raw[i + 1]
        i += 2
        if marker in (0xD8, 0xD9):
            continue
        if i + 2 > len(raw):
            break
        length = int.from_bytes(raw[i:i+2], "big")
        if length < 2 or i + length > len(raw):
            break
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h = int.from_bytes(raw[i+3:i+5], "big")
            w = int.from_bytes(raw[i+5:i+7], "big")
            return w, h
        i += length
    return None


def image_meta(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    data = base_file_meta(p)
    data["media_type"] = "image"
    if not p.exists() or not p.is_file():
        return data
    try:
        from PIL import Image  # type: ignore
        with Image.open(p) as img:
            data.update({"width": img.width, "height": img.height, "format": img.format, "mode": img.mode})
            return data
    except Exception as exc:
        data["pil_warning"] = str(exc)[:160]
    try:
        raw = p.read_bytes()[:65536]
        size = _png_size(raw) or _jpeg_size(raw)
        if size:
            data.update({"width": size[0], "height": size[1], "format": p.suffix.lower().lstrip(".").upper(), "mode": "unknown"})
    except Exception as exc:
        data["header_warning"] = str(exc)[:160]
    return data


def ffprobe_json(path: str | Path) -> dict[str, Any]:
    if not shutil.which("ffprobe"):
        return {"available": False, "error": "ffprobe_not_found"}
    cmd = ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
    except Exception as exc:
        return {"available": True, "error": str(exc)[:300]}
    if proc.returncode != 0:
        return {"available": True, "error": proc.stderr[-500:]}
    try:
        return {"available": True, "data": json.loads(proc.stdout or "{}")}
    except Exception as exc:
        return {"available": True, "error": f"ffprobe_json_error: {exc}"}


def media_meta(path: str | Path, kind: str = "auto") -> dict[str, Any]:
    p = Path(path)
    suffix = p.suffix.lower()
    if kind == "image" or suffix in IMAGE_SUFFIXES:
        return image_meta(p)
    data = base_file_meta(p)
    if kind != "auto":
        data["media_type"] = kind
    elif suffix in VIDEO_SUFFIXES:
        data["media_type"] = "video"
    elif suffix in AUDIO_SUFFIXES:
        data["media_type"] = "audio"
    else:
        data["media_type"] = "unknown"
    probe = ffprobe_json(p) if p.exists() else {"available": False, "error": "file_not_found"}
    data["ffprobe"] = probe
    if probe.get("data"):
        fmt = probe["data"].get("format", {})
        streams = probe["data"].get("streams", [])
        data["duration_sec"] = _to_float(fmt.get("duration"))
        data["bit_rate"] = _to_int(fmt.get("bit_rate"))
        for s in streams:
            if s.get("codec_type") == "video" and "width" in s:
                data["width"] = s.get("width")
                data["height"] = s.get("height")
                data["video_codec"] = s.get("codec_name")
            if s.get("codec_type") == "audio":
                data["audio_codec"] = s.get("codec_name")
                data["sample_rate"] = _to_int(s.get("sample_rate"))
    return data


def _to_float(v: Any) -> float | None:
    try: return float(v)
    except Exception: return None


def _to_int(v: Any) -> int | None:
    try: return int(float(v))
    except Exception: return None


def safe_output_name(default_stem: str, suffix: str) -> str:
    stem = re.sub(r"[^0-9A-Za-z_\-一-鿿]+", "_", str(default_stem or "media_output")).strip("_") or "media_output"
    if not suffix.startswith("."):
        suffix = "." + suffix
    return f"{stem}_{int(time.time())}{suffix}"


def run_ffmpeg(args: list[str], timeout: int = 300) -> dict[str, Any]:
    if not shutil.which("ffmpeg"):
        return {"ok": False, "error_code": "ffmpeg_not_found", "summary": "未检测到 ffmpeg。"}
    cmd = ["ffmpeg", "-y"] + args
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error_code": "timeout", "summary": "ffmpeg 执行超时。", "cmd": cmd}
    except Exception as exc:
        return {"ok": False, "error_code": "ffmpeg_error", "summary": str(exc)[:300], "cmd": cmd}
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-4000:], "cmd": cmd}


def extract_keyframes(video_path: str | Path, output_dir: str | Path, interval_sec: float = 5.0, max_frames: int = 24) -> dict[str, Any]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    # fps=1/interval, then stop by max files using -vframes
    fps = max(0.02, 1.0 / max(0.5, float(interval_sec)))
    pattern = out / "frame_%04d.jpg"
    result = run_ffmpeg(["-i", str(video_path), "-vf", f"fps={fps}", "-vframes", str(max_frames), str(pattern)], timeout=300)
    frames = sorted(out.glob("frame_*.jpg"))
    result["frames"] = [str(p) for p in frames]
    return result


def try_ocr_image(path: str | Path, language_hint: str = "auto") -> dict[str, Any]:
    # 先尝试 pytesseract；若环境未装，返回 ocr_engine_missing，不编造文本。
    try:
        from PIL import Image  # type: ignore
        import pytesseract  # type: ignore
    except Exception as exc:
        return {"ok": False, "error_code": "ocr_engine_missing", "summary": f"未检测到可用 OCR 引擎：{exc}"}
    try:
        lang = "chi_sim+eng" if language_hint in {"auto", "mixed", "zh"} else "eng"
        with Image.open(path) as img:
            text = pytesseract.image_to_string(img, lang=lang)
            return {"ok": True, "text": text.strip(), "blocks": [], "average_confidence": None, "engine": "pytesseract"}
    except Exception as exc:
        return {"ok": False, "error_code": "ocr_failed", "summary": str(exc)[:300]}


def crop_image(path: str | Path, bbox: list[float], output_path: str | Path) -> dict[str, Any]:
    try:
        from PIL import Image  # type: ignore
    except Exception as exc:
        return {"ok": False, "error_code": "pillow_missing", "summary": f"未检测到 Pillow：{exc}"}
    try:
        x, y, w, h = [int(float(v)) for v in bbox]
        with Image.open(path) as img:
            x = max(0, min(x, img.width))
            y = max(0, min(y, img.height))
            w = max(1, min(w, img.width - x))
            h = max(1, min(h, img.height - y))
            crop = img.crop((x, y, x + w, y + h))
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            crop.save(out)
        return {"ok": True, "output_path": str(output_path), "bbox": [x, y, w, h], "sha256": sha256_file(output_path)}
    except Exception as exc:
        return {"ok": False, "error_code": "crop_failed", "summary": str(exc)[:300]}
