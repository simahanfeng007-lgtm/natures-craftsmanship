"""Unified Wangwen novel production pipeline schemas for Tiangong v2.

This compact surface replaces scattered 35-42 + 53 usage with one main pipeline.
It does not change Runtime routing, Code-X routing, run_once, or _hebing_panding.
"""
from __future__ import annotations

COMMON_PROPERTIES = {
    "mode": {"type": "string", "description": "new_book/chapter/rewrite/review/serial_update/full，默认 full。"},
    "genre": {"type": "string", "description": "题材类型，如都市爽文/玄幻仙侠/悬疑诡异/女频情感/历史权谋/职业文。"},
    "platform": {"type": "string", "description": "目标平台或连载环境，可留空。"},
    "target_reader": {"type": "string", "description": "目标读者画像。"},
    "title": {"type": "string", "description": "书名或候选书名。"},
    "premise": {"type": "string", "description": "一句话故事核心、开书想法或用户要求。"},
    "instruction": {"type": "string", "description": "用户自然语言要求。"},
    "volume_goal": {"type": "string", "description": "阶段目标、卷目标或当前连载目标。"},
    "chapter_index": {"type": "integer", "description": "章节序号。"},
    "chapter_goal": {"type": "string", "description": "本章目标：推进什么事件、兑现什么爽点、埋什么钩子。"},
    "chapter_outline": {"type": "string", "description": "章节大纲或场景顺序。"},
    "previous_summary": {"type": "string", "description": "前文摘要、上一章结尾或已有剧情。"},
    "story_bible": {"type": "object", "description": "故事圣经：人物、世界观、设定、伏笔、时间线。"},
    "characters": {
        "type": "array",
        "description": "人物列表。可用字符串或对象。",
        "items": {"oneOf": [{"type": "string"}, {"type": "object", "additionalProperties": True}]},
    },
    "style_target": {"type": "string", "description": "目标文风，如短句强压迫/古风高燃/悬疑冷感/女频细腻拉扯。"},
    "sample_text": {"type": "string", "description": "样章或参考文本，用于抽象分析文风，不做特定作者仿写。"},
    "existing_draft": {"type": "string", "description": "已有正文草稿。rewrite/review 模式使用。"},
    "text": {"type": "string", "description": "待诊断或待修订的正文。"},
    "reader_feedback": {"type": "string", "description": "读者评论、编辑反馈或追读表现。"},
    "word_count": {"type": "integer", "description": "目标字数。"},
    "must_include": {"type": "array", "items": {"type": "string"}, "description": "必须出现的事件、物件、台词或信息。"},
    "avoid": {"type": "array", "items": {"type": "string"}, "description": "禁止或避免的表达、情节、口吻。"},
    "strictness": {"type": "string", "description": "light/medium/heavy，默认 medium。"},
}

FULL_PIPELINE_SCHEMA = {
    "type": "object",
    "properties": COMMON_PROPERTIES,
    "required": [],
    "additionalProperties": False,
}

TEXT_REQUIRED_SCHEMA = {
    "type": "object",
    "properties": COMMON_PROPERTIES,
    "required": ["text"],
    "additionalProperties": False,
}

REVISION_SCHEMA = {
    "type": "object",
    "properties": {
        **COMMON_PROPERTIES,
        "diagnosis": {"type": "object", "description": "可选，来自 wangwen_draft_quality_check 的质量报告。"},
    },
    "required": ["text"],
    "additionalProperties": False,
}

WANGWEN_UNIFIED_PIPELINE_TOOL_SCHEMAS = {
    "wangwen_novel_factory_run": FULL_PIPELINE_SCHEMA,
    "wangwen_novel_bible_build": FULL_PIPELINE_SCHEMA,
    "wangwen_chapter_brief_build": FULL_PIPELINE_SCHEMA,
    "wangwen_draft_quality_check": TEXT_REQUIRED_SCHEMA,
    "wangwen_revision_plan_build": REVISION_SCHEMA,
}


def install_into(tool_schemas: dict) -> dict:
    tool_schemas.update(WANGWEN_UNIFIED_PIPELINE_TOOL_SCHEMAS)
    return tool_schemas
