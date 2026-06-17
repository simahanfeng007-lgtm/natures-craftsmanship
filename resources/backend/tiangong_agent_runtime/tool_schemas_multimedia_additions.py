"""多媒体扩展工具 Schema。

植入方式：把 MULTIMEDIA_TOOL_SCHEMAS 合并进现有 tool_schemas.py 的运行时 Schema 单一源。
不改变 run_once / _hebing_panding / Code-X / 收口固定链。
"""

from __future__ import annotations

from typing import Any

EMPTY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }

_PATH = {"type": "string", "description": "输入文件路径，优先使用工作区相对路径或已上传文件路径。"}
_IMAGE = {"type": "string", "description": "图片路径，优先使用工作区相对路径或已上传文件路径。"}
_VIDEO = {"type": "string", "description": "视频路径，优先使用工作区相对路径或已上传文件路径。"}
_AUDIO = {"type": "string", "description": "音频路径，优先使用工作区相对路径或已上传文件路径。"}
_QUESTION = {"type": "string", "description": "用户问题或处理要求。"}
_OUTPUT = {"type": "string", "description": "输出文件名或输出路径。"}
_BBOX = {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4, "description": "区域坐标 [x, y, width, height]。"}

MULTIMEDIA_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    # 图片解析
    "image_inspect": _obj({"image_path": _IMAGE, "question": _QUESTION, "detail_level": {"type": "string", "enum": ["low", "medium", "high"], "description": "细节等级。"}}, ["image_path"]),
    "image_ocr_parse": _obj({"image_path": _IMAGE, "language_hint": {"type": "string", "enum": ["auto", "zh", "en", "mixed"], "description": "OCR语言提示。"}, "return_blocks": {"type": "boolean", "description": "是否返回文本块。"}}, ["image_path"]),
    "image_layout_parse": _obj({"image_path": _IMAGE, "detect_tables": {"type": "boolean"}, "detect_ui_regions": {"type": "boolean"}}, ["image_path"]),
    "image_region_query": _obj({"image_path": _IMAGE, "question": _QUESTION, "region_hint": {"type": "string", "description": "局部区域描述，如左上角/中间红字。"}, "bbox": _BBOX}, ["image_path", "question"]),
    "image_compare": _obj({"image_path_a": _IMAGE, "image_path_b": _IMAGE, "compare_mode": {"type": "string", "enum": ["general", "ui", "text", "layout", "content"], "description": "对比模式。"}}, ["image_path_a", "image_path_b"]),
    "image_table_extract": _obj({"image_path": _IMAGE, "table_region_hint": {"type": "string"}, "output_format": {"type": "string", "enum": ["json", "markdown_table"]}}, ["image_path"]),
    "image_chart_extract": _obj({"image_path": _IMAGE, "chart_type_hint": {"type": "string", "enum": ["auto", "bar", "line", "pie", "scatter"]}, "question": _QUESTION}, ["image_path"]),
    "image_crop_export": _obj({"image_path": _IMAGE, "bbox": _BBOX, "output_name": _OUTPUT}, ["image_path", "bbox"]),

    # 视频解析
    "video_inspect": _obj({"video_path": _VIDEO, "question": _QUESTION}, ["video_path"]),
    "video_keyframe_extract": _obj({"video_path": _VIDEO, "interval_sec": {"type": "number", "minimum": 0.5, "maximum": 60}, "max_frames": {"type": "integer", "minimum": 1, "maximum": 200}, "output_dir": {"type": "string"}}, ["video_path"]),
    "video_scene_split": _obj({"video_path": _VIDEO, "sensitivity": {"type": "string", "enum": ["low", "medium", "high"]}}, ["video_path"]),
    "video_ocr_parse": _obj({"video_path": _VIDEO, "interval_sec": {"type": "number", "minimum": 1, "maximum": 60}, "language_hint": {"type": "string", "enum": ["auto", "zh", "en", "mixed"]}}, ["video_path"]),
    "video_audio_transcribe": _obj({"video_path": _VIDEO, "language_hint": {"type": "string", "enum": ["auto", "zh", "en", "mixed"]}}, ["video_path"]),
    "video_subtitle_extract": _obj({"video_path": _VIDEO, "sidecar_path": {"type": "string", "description": "可选外置srt/vtt字幕路径。"}}, ["video_path"]),
    "video_event_timeline": _obj({"video_path": _VIDEO, "focus": {"type": "string", "description": "关注对象或事件。"}}, ["video_path"]),
    "video_compare": _obj({"video_path_a": _VIDEO, "video_path_b": _VIDEO, "compare_mode": {"type": "string", "enum": ["general", "frames", "audio", "subtitle", "layout"]}}, ["video_path_a", "video_path_b"]),

    # 图片制作
    "image_generate": _obj({"prompt": {"type": "string", "description": "图片生成提示词。"}, "size": {"type": "string", "description": "尺寸，如1024x1024。"}, "style": {"type": "string"}, "output_name": _OUTPUT}, ["prompt"]),
    "image_edit": _obj({"image_path": _IMAGE, "instruction": {"type": "string", "description": "编辑要求。"}, "output_name": _OUTPUT}, ["image_path", "instruction"]),
    "image_inpaint": _obj({"image_path": _IMAGE, "mask_path": {"type": "string"}, "prompt": {"type": "string"}, "output_name": _OUTPUT}, ["image_path", "prompt"]),
    "image_background_remove": _obj({"image_path": _IMAGE, "output_name": _OUTPUT}, ["image_path"]),
    "image_upscale": _obj({"image_path": _IMAGE, "scale": {"type": "integer", "minimum": 2, "maximum": 4}, "output_name": _OUTPUT}, ["image_path"]),
    "image_style_transfer": _obj({"image_path": _IMAGE, "style_prompt": {"type": "string"}, "output_name": _OUTPUT}, ["image_path", "style_prompt"]),
    "image_variation": _obj({"image_path": _IMAGE, "count": {"type": "integer", "minimum": 1, "maximum": 4}, "variation_prompt": {"type": "string"}}, ["image_path"]),
    "image_text_poster_generate": _obj({"title": {"type": "string"}, "subtitle": {"type": "string"}, "body": {"type": "string"}, "style": {"type": "string"}, "size": {"type": "string"}, "output_name": _OUTPUT}, ["title"]),

    # 视频制作
    "video_generate_from_text": _obj({"prompt": {"type": "string"}, "duration_sec": {"type": "number", "minimum": 1, "maximum": 120}, "aspect_ratio": {"type": "string"}, "output_name": _OUTPUT}, ["prompt"]),
    "video_generate_from_images": _obj({"image_paths": {"type": "array", "items": {"type": "string"}}, "prompt": {"type": "string"}, "duration_sec": {"type": "number"}, "output_name": _OUTPUT}, ["image_paths"]),
    "storyboard_generate": _obj({"topic": {"type": "string"}, "duration_sec": {"type": "number"}, "scene_count": {"type": "integer", "minimum": 1, "maximum": 30}}, ["topic"]),
    "shot_plan_generate": _obj({"script": {"type": "string"}, "style": {"type": "string"}, "duration_sec": {"type": "number"}}, ["script"]),
    "video_avatar_generate": _obj({"script": {"type": "string"}, "avatar_style": {"type": "string"}, "voice_style": {"type": "string"}, "output_name": _OUTPUT}, ["script"]),
    "voiceover_generate": _obj({"script": {"type": "string"}, "voice_style": {"type": "string"}, "language": {"type": "string"}, "output_name": _OUTPUT}, ["script"]),
    "subtitle_burn_in": _obj({"video_path": _VIDEO, "subtitle_path": {"type": "string"}, "output_name": _OUTPUT}, ["video_path", "subtitle_path"]),
    "video_render": _obj({"render_spec": {"type": "object", "description": "视频渲染规格。"}, "output_name": _OUTPUT}, ["render_spec"]),

    # 视频剪辑
    "video_trim": _obj({"video_path": _VIDEO, "start_sec": {"type": "number", "minimum": 0}, "end_sec": {"type": "number", "minimum": 0}, "output_name": _OUTPUT}, ["video_path", "start_sec", "end_sec"]),
    "video_concat": _obj({"video_paths": {"type": "array", "items": {"type": "string"}}, "output_name": _OUTPUT}, ["video_paths"]),
    "video_cut_by_timestamps": _obj({"video_path": _VIDEO, "segments": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}}, "output_name": _OUTPUT}, ["video_path", "segments"]),
    "video_add_subtitles": _obj({"video_path": _VIDEO, "subtitle_path": {"type": "string"}, "output_name": _OUTPUT}, ["video_path", "subtitle_path"]),
    "video_add_bgm": _obj({"video_path": _VIDEO, "audio_path": _AUDIO, "volume": {"type": "number", "minimum": 0, "maximum": 2}, "output_name": _OUTPUT}, ["video_path", "audio_path"]),
    "video_add_transition": _obj({"video_paths": {"type": "array", "items": {"type": "string"}}, "transition": {"type": "string"}, "duration_sec": {"type": "number"}, "output_name": _OUTPUT}, ["video_paths"]),
    "video_resize_reframe": _obj({"video_path": _VIDEO, "aspect_ratio": {"type": "string"}, "width": {"type": "integer"}, "height": {"type": "integer"}, "output_name": _OUTPUT}, ["video_path"]),
    "video_export": _obj({"video_path": _VIDEO, "format": {"type": "string"}, "codec": {"type": "string"}, "output_name": _OUTPUT}, ["video_path"]),

    # 音频解析/制作
    "audio_transcribe": _obj({"audio_path": _AUDIO, "language_hint": {"type": "string", "enum": ["auto", "zh", "en", "mixed"]}}, ["audio_path"]),
    "audio_diarize": _obj({"audio_path": _AUDIO, "speaker_count_hint": {"type": "integer", "minimum": 1, "maximum": 20}}, ["audio_path"]),
    "audio_summary": _obj({"transcript": {"type": "string"}, "audio_path": _AUDIO}, []),
    "audio_keywords_extract": _obj({"transcript": {"type": "string"}, "audio_path": _AUDIO, "top_k": {"type": "integer", "minimum": 1, "maximum": 50}}, []),
    "audio_event_detect": _obj({"audio_path": _AUDIO}, ["audio_path"]),
    "tts_generate": _obj({"text": {"type": "string"}, "voice_style": {"type": "string"}, "language": {"type": "string"}, "output_name": _OUTPUT}, ["text"]),
    "audio_clone_voice": _obj({"reference_audio_path": _AUDIO, "text": {"type": "string"}, "authorization_note": {"type": "string"}, "output_name": _OUTPUT}, ["reference_audio_path", "text", "authorization_note"]),
    "bgm_generate": _obj({"mood": {"type": "string"}, "duration_sec": {"type": "number"}, "style": {"type": "string"}, "output_name": _OUTPUT}, ["mood"]),
    "audio_mix": _obj({"audio_paths": {"type": "array", "items": {"type": "string"}}, "output_name": _OUTPUT}, ["audio_paths"]),
    "audio_denoise": _obj({"audio_path": _AUDIO, "output_name": _OUTPUT}, ["audio_path"]),
    "audio_normalize": _obj({"audio_path": _AUDIO, "target_lufs": {"type": "number"}, "output_name": _OUTPUT}, ["audio_path"]),
    "audio_export": _obj({"audio_path": _AUDIO, "format": {"type": "string"}, "output_name": _OUTPUT}, ["audio_path"]),

    # 多媒体结构化/编排
    "media_entity_extract": _obj({"source_path": _PATH, "source_text": {"type": "string"}, "entity_types": {"type": "array", "items": {"type": "string"}}}, []),
    "media_kv_extract": _obj({"source_path": _PATH, "source_text": {"type": "string"}, "fields": {"type": "array", "items": {"type": "string"}}}, []),
    "media_topic_extract": _obj({"source_path": _PATH, "source_text": {"type": "string"}, "top_k": {"type": "integer", "minimum": 1, "maximum": 20}}, []),
    "media_risk_extract": _obj({"source_path": _PATH, "source_text": {"type": "string"}, "risk_scope": {"type": "string"}}, []),
    "media_knowledge_extract": _obj({"source_path": _PATH, "source_text": {"type": "string"}, "output_format": {"type": "string", "enum": ["markdown", "json"]}}, []),
    "multimedia_pipeline_plan": _obj({"goal": {"type": "string"}, "assets": {"type": "array", "items": {"type": "string"}}, "deadline": {"type": "string"}}, ["goal"]),
    "multimedia_asset_manifest": _obj({"asset_paths": {"type": "array", "items": {"type": "string"}}, "project_name": {"type": "string"}}, ["asset_paths"]),
    "multimedia_batch_plan": _obj({"goal": {"type": "string"}, "count": {"type": "integer", "minimum": 1, "maximum": 100}, "template": {"type": "string"}}, ["goal"]),
    "multimedia_delivery_package": _obj({"project_name": {"type": "string"}, "asset_paths": {"type": "array", "items": {"type": "string"}}, "output_name": _OUTPUT}, ["project_name"]),
}
