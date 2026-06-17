"""Unified Chinese web-novel production pipeline adapters for Tiangong v2.

The adapter layer is intentionally deterministic and side-effect free:
- no external requests
- no publishing/submission
- no Runtime routing changes
- no Code-X changes
It returns production briefs and quality/revision plans for the LLM to turn into final prose.
"""
from __future__ import annotations

from statistics import mean
import re
from typing import Any

try:
    from tiangong_agent_runtime.tool_result import ToolResult, ToolResultStatus
except Exception:  # pragma: no cover - offline packaging compatibility
    ToolResult = None  # type: ignore
    ToolResultStatus = None  # type: ignore

AI_PHRASES = [
    "首先", "其次", "最后", "综上所述", "值得注意的是", "不可否认的是", "随着时代的发展",
    "在当今社会", "具有重要意义", "全方位", "赋能", "闭环", "底层逻辑", "抓手",
    "深度融合", "从某种意义上说", "毋庸置疑", "他知道", "他明白", "他意识到",
]
WEBNOVEL_CLICHES = [
    "他嘴角微微上扬", "恐怖如斯", "倒吸一口凉气", "此子断不可留", "三十年河东三十年河西",
    "你已有取死之道", "全场震惊", "众人哗然", "眼中闪过一丝", "一股暖流涌上心头", "空气仿佛凝固",
]
ABSTRACT_WORDS = [
    "命运", "时代", "意义", "价值", "精神", "力量", "梦想", "未来", "成长", "热血", "信念",
    "底色", "格局", "担当", "情怀", "维度", "逻辑", "复杂", "震撼", "强大",
]

GENRE_PRESETS: dict[str, dict[str, Any]] = {
    "都市爽文": {
        "reader_promise": "压迫后反击、身份落差、资源逆转、强情绪兑现。",
        "style": "短句强压迫，动作和旁观者反应优先，少文艺化解释。",
        "payoff": "羞辱/质疑铺垫后，用反击、证据、失态和群体反应兑现爽点。",
        "opening_hook": "主角被低估、资源被抢、身份被误判或关键机会被阻断。",
    },
    "玄幻仙侠": {
        "reader_promise": "境界压制、规则反转、升级回报、天地尺度。",
        "style": "中短句推进，关键异象可铺陈；设定嵌入动作，不堆名词。",
        "payoff": "用境界错位、法则破局、旁观者认知崩塌兑现快感。",
        "opening_hook": "废柴/低阶/被逐出局，同时露出可成长规则。",
    },
    "悬疑诡异": {
        "reader_promise": "异常细节、信息差、认知反转、持续不安。",
        "style": "冷感短句，少解释恐怖，多写违和物件、感官和停顿。",
        "payoff": "用细节回收和认知坍塌兑现惊悚，不直接喊恐怖。",
        "opening_hook": "熟悉场景出现一个无法解释的异常。",
    },
    "女频情感": {
        "reader_promise": "关系权力变化、情绪边界、拉扯、被看见与反击。",
        "style": "贴身视角，中句细腻推进，爆点处短句断开。",
        "payoff": "用关系位置变化、对方失控、边界确认兑现爽感。",
        "opening_hook": "关系失衡、误解、背叛、重逢或身份反转。",
    },
    "历史权谋": {
        "reader_promise": "局势翻盘、话术反杀、利益博弈、伏笔回收。",
        "style": "克制旁白，中长句铺局，短句落刀。",
        "payoff": "用话里藏刀、礼制压力和暗线回收完成反杀。",
        "opening_hook": "看似无解的局、错位身份或一件小事牵出大势。",
    },
    "职业文": {
        "reader_promise": "专业判断解决高压问题，让质疑者产生认知落差。",
        "style": "专业细节必须场景化，关键判断用短句。",
        "payoff": "用专业流程、责任边界和结果证明兑现成就感。",
        "opening_hook": "高压现场出现专业难题或责任甩锅。",
    },
}
DEFAULT_PRESET = {
    "reader_promise": "明确目标、持续冲突、阶段回报、章末期待。",
    "style": "贴身第三人称，中短句结合，动作、对白、反应优先，解释后置。",
    "payoff": "先压迫，再选择，再反击/反转，最后留下新期待。",
    "opening_hook": "主角目标被阻断，同时出现新的机会或危险。",
}


def _split_call(first: Any, second: Any) -> tuple[Any | None, Any, dict[str, Any]]:
    if hasattr(first, "arguments"):
        return first, second, dict(getattr(first, "arguments", {}) or {})
    if isinstance(second, dict):
        return None, first, dict(second)
    if hasattr(second, "arguments"):
        return second, first, dict(getattr(second, "arguments", {}) or {})
    return None, first, {}


def _step_id(invocation: Any | None, context: Any, fallback: str) -> str:
    if invocation is not None and getattr(invocation, "step_id", ""):
        return str(getattr(invocation, "step_id"))
    for name in ("step_id", "request_id", "run_id"):
        value = getattr(context, name, "")
        if value:
            return str(value)
    return fallback


def _status(status: str) -> Any:
    if ToolResultStatus is None:
        return status
    table = {
        "ok": getattr(ToolResultStatus, "OK", status),
        "failed": getattr(ToolResultStatus, "FAILED", status),
        "blocked": getattr(ToolResultStatus, "BLOCKED", status),
        "skipped": getattr(ToolResultStatus, "SKIPPED", status),
    }
    return table.get(status, status)


def _result(invocation: Any | None, context: Any, tool_name: str, status: str = "ok", summary: str = "", data: dict[str, Any] | None = None, error_code: str = "") -> Any:
    payload = data or {}
    payload.setdefault("schema", "tool_result.data.v2")
    payload.setdefault("tool_name", tool_name)
    payload.setdefault("confidence", "medium")
    payload.setdefault("evidence_refs", [])
    payload.setdefault("error_category", error_code or "")
    payload.setdefault("retryable", False)
    payload.setdefault("next_action", "")
    if ToolResult is None:
        return {"step_id": _step_id(invocation, context, tool_name), "tool_name": tool_name, "status": status, "output_summary": summary, "data": payload, "error_code": error_code}
    return ToolResult(
        step_id=_step_id(invocation, context, tool_name),
        tool_name=tool_name,
        status=_status(status),
        output_summary=summary,
        data=payload,
        artifacts=[],
        error_code=error_code,
        audit_ref="",
    )


def _get(args: dict[str, Any], key: str, default: Any = "") -> Any:
    value = args.get(key, default)
    return default if value is None else value


def _genre(args: dict[str, Any]) -> str:
    return str(_get(args, "genre", "") or "未指定")


def _preset(genre: str) -> dict[str, Any]:
    merged = dict(DEFAULT_PRESET)
    for key, value in GENRE_PRESETS.items():
        if key in genre or genre in key:
            merged.update(value)
            break
    return merged


def _sentences(text: str) -> list[str]:
    return [x.strip() for x in re.split(r"(?<=[。！？!?；;])\s*", text.strip()) if x.strip()]


def _paras(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


def _hit_list(text: str, phrases: list[str], kind: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for phrase in phrases:
        for match in re.finditer(re.escape(phrase), text):
            hits.append({"phrase": phrase, "start": match.start(), "end": match.end(), "type": kind})
    return hits


def _text_metrics(text: str) -> dict[str, Any]:
    sents = _sentences(text)
    paras = _paras(text)
    lengths = [len(s) for s in sents]
    dialogue_count = len(re.findall(r"[“\"].+?[”\"]", text))
    return {
        "char_count": len(text),
        "paragraph_count": len(paras),
        "sentence_count": len(sents),
        "avg_sentence_len": round(mean(lengths), 1) if lengths else 0,
        "short_sentence_ratio": round(sum(1 for n in lengths if n <= 18) / max(len(lengths), 1), 2),
        "long_sentence_ratio": round(sum(1 for n in lengths if n >= 55) / max(len(lengths), 1), 2),
        "dialogue_count": dialogue_count,
        "dialogue_ratio_hint": round(dialogue_count / max(len(paras), 1), 2),
    }


def _scan_text(text: str) -> dict[str, Any]:
    ai_hits = _hit_list(text, AI_PHRASES, "ai_tone")
    cliche_hits = _hit_list(text, WEBNOVEL_CLICHES, "webnovel_cliche")
    abstract_hits = _hit_list(text, ABSTRACT_WORDS, "abstract_word")
    metrics = _text_metrics(text)
    risks: list[dict[str, Any]] = []
    if ai_hits:
        risks.append({"type": "ai_tone", "priority": "high", "count": len(ai_hits), "message": "存在AI腔/说明腔连接词或总结句。"})
    if cliche_hits:
        risks.append({"type": "cliche", "priority": "medium", "count": len(cliche_hits), "message": "存在高频网文套话，建议用当前人物动作替代。"})
    if len(abstract_hits) / max(len(text), 1) * 1000 > 8:
        risks.append({"type": "abstract_density", "priority": "high", "count": len(abstract_hits), "message": "抽象词密度偏高，缺少身体动作、物件和具体反应。"})
    if metrics["avg_sentence_len"] >= 45 and metrics["short_sentence_ratio"] < 0.25:
        risks.append({"type": "rhythm_flat", "priority": "medium", "message": "句子偏长且节奏不够切分，爆点处需要短句。"})
    if metrics["paragraph_count"] and metrics["dialogue_ratio_hint"] < 0.15:
        risks.append({"type": "dialogue_low", "priority": "medium", "message": "对白占比偏低，网文章节可能说明感过重。"})
    score = 100
    for risk in risks:
        score -= 16 if risk["priority"] == "high" else 9
    score = max(score, 40 if text.strip() else 0)
    return {
        "metrics": metrics,
        "hits": {"ai_tone": ai_hits[:80], "cliche": cliche_hits[:80], "abstract": abstract_hits[:80]},
        "risks": risks,
        "quality_score": score,
        "pass_hint": "score>=78 且无 high 风险，可作为阶段成稿；否则先修订。",
    }


def _normalize_characters(raw: Any, args: dict[str, Any]) -> list[dict[str, Any]]:
    items = raw if isinstance(raw, list) else []
    out: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, str):
            out.append({"name": item, "role": "未指定", "desire": "围绕本章目标行动", "voice": _voice_rule(item)})
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("姓名") or item.get("角色") or "未命名角色")
            role = str(item.get("role") or item.get("身份") or item.get("position") or "未指定")
            trait = str(item.get("trait") or item.get("性格") or item.get("personality") or "")
            desire = str(item.get("desire") or item.get("目标") or item.get("want") or _get(args, "chapter_goal", "阶段目标"))
            out.append({"name": name, "role": role, "trait": trait, "desire": desire, "voice": _voice_rule(f"{role} {trait} {desire}")})
    if not out:
        out = [
            {"name": "主角", "role": "主角", "desire": str(_get(args, "chapter_goal", "完成阶段目标")), "voice": _voice_rule("主角")},
            {"name": "阻碍者", "role": "冲突方", "desire": "阻止主角达成目标", "voice": "说话带压迫、试探或轻视，为主角反击制造落点。"},
        ]
    return out


def _voice_rule(text: str) -> str:
    if any(k in text for k in ["反派", "宗主", "老板", "上位", "权贵", "冲突方"]):
        return "少解释，多规则、代价、命令句；情绪不外露，话里有压迫。"
    if any(k in text for k in ["主角", "天才", "重生", "穿越"]):
        return "少自证，多判断；关键处用反问和短句，避免长篇心理说明。"
    if any(k in text for k in ["女主", "恋人", "妻", "公主"]):
        return "保持独立判断和边界感；情绪通过停顿、反问、动作表现。"
    if any(k in text for k in ["市井", "小人物", "伙计", "村民", "员工"]):
        return "口语、利益判断、短句多；可有俗语，但不要变成段子。"
    return "每句台词都带身份、利益、情绪或隐瞒信息，不说纯功能性废话。"


def _build_style_card(args: dict[str, Any]) -> dict[str, Any]:
    genre = _genre(args)
    preset = _preset(genre)
    sample = str(_get(args, "sample_text", ""))
    metrics = _text_metrics(sample) if sample else {}
    style = str(_get(args, "style_target", "") or preset["style"])
    return {
        "genre": genre,
        "style_target": style,
        "narration": "贴身第三人称优先；若用户指定第一人称，保持主角即时感受。",
        "sentence_rhythm": "铺垫段中句，压迫和爆点段短句；不要段段均匀。",
        "language_density": "动作、对白、反应、物件细节优先；设定和心理解释后置。",
        "emotion_temperature": "根据题材控制：爽文热，悬疑冷，权谋克制，女频细腻。",
        "reader_promise": preset["reader_promise"],
        "payoff_style": preset["payoff"],
        "sample_metrics": metrics,
        "guardrails": [
            "不模仿特定在世作者，只使用抽象类型文风。",
            "不要用论文腔解释人物心理，要让人物通过动作和对白暴露。",
            "章末必须留下新信息、新危险、新问题或新承诺。",
        ],
    }


def _build_bible(args: dict[str, Any]) -> dict[str, Any]:
    genre = _genre(args)
    preset = _preset(genre)
    raw_bible = _get(args, "story_bible", {}) if isinstance(_get(args, "story_bible", {}), dict) else {}
    characters = _normalize_characters(_get(args, "characters", []), args)
    premise = str(_get(args, "premise", "") or _get(args, "instruction", "") or _get(args, "chapter_goal", ""))
    title = str(_get(args, "title", "") or "未定书名")
    bible = {
        "title": title,
        "genre": genre,
        "platform": str(_get(args, "platform", "") or "未指定"),
        "target_reader": str(_get(args, "target_reader", "") or "按题材默认读者"),
        "core_premise": premise or "主角在强阻碍下达成阶段目标，并获得新的问题。",
        "type_contract": {
            "reader_promise": preset["reader_promise"],
            "opening_hook": preset["opening_hook"],
            "payoff_style": preset["payoff"],
        },
        "protagonist_engine": {
            "desire": str(_get(args, "volume_goal", "变强、翻盘、守住关键关系或完成职业目标")),
            "fear_or_cost": "每次获得回报都要附带代价、暴露风险或更高层级冲突。",
            "agency_rule": "主角不能靠旁白解释取胜，必须主动选择、行动、承担后果。",
        },
        "goldfinger_or_power_rule": {
            "design_rule": "能力必须有边界、代价、冷却、信息差或使用条件。",
            "balance_rule": "不能早期无代价碾压；每次兑现后引出更强阻碍。",
            "reward_loop": "目标→行动→代价→奖励→新目标。",
        },
        "world_rules": {
            "rule_card": raw_bible.get("world_rules", "规则要服务冲突，不单独堆设定。"),
            "continuity_rule": "战力、时间线、道具、关系状态必须可追踪。",
            "detail_rule": "现实/职业/文化细节必须嵌入任务流程和人物代价。",
        },
        "characters": characters,
        "longline": {
            "foreshadowing": raw_bible.get("foreshadowing", []),
            "unresolved_questions": raw_bible.get("unresolved_questions", []),
            "next_volume_pressure": str(_get(args, "volume_goal", "阶段目标未指定")),
        },
    }
    return bible


def _scene_cards(args: dict[str, Any], bible: dict[str, Any]) -> list[dict[str, Any]]:
    outline = str(_get(args, "chapter_outline", ""))
    if outline:
        parts = [x.strip(" -—\t") for x in re.split(r"[\n；;]+", outline) if x.strip()]
    else:
        parts = [
            "开场压迫：主角目标被阻断或被误判",
            "冲突升级：阻碍者提出代价或制造公开压力",
            "选择与反击：主角用能力/信息/关系完成逆转",
            "回报与新钩子：阶段爽点兑现，同时抛出新危险或新问题",
        ]
    cards: list[dict[str, Any]] = []
    for i, part in enumerate(parts, 1):
        cards.append({
            "scene": i,
            "beat": part,
            "goal": "让场景有明确目标和结果，不写纯过渡。",
            "conflict": "必须出现阻碍、误判、代价或信息差。",
            "outcome": "场景结束时人物关系、资源、信息或危险状态发生变化。",
            "language_rule": "动作/对白/旁观者反应优先，解释后置。",
        })
    return cards


def _chapter_brief(args: dict[str, Any], bible: dict[str, Any] | None = None) -> dict[str, Any]:
    bible = bible or _build_bible(args)
    style_card = _build_style_card(args)
    characters = bible.get("characters") or _normalize_characters(_get(args, "characters", []), args)
    scenes = _scene_cards(args, bible)
    chapter_goal = str(_get(args, "chapter_goal", "") or _get(args, "instruction", "") or "完成一章可连载正文")
    word_count = int(_get(args, "word_count", 2200) or 2200)
    must_include = _get(args, "must_include", []) if isinstance(_get(args, "must_include", []), list) else []
    avoid = _get(args, "avoid", []) if isinstance(_get(args, "avoid", []), list) else []
    brief = {
        "chapter_index": _get(args, "chapter_index", 0),
        "chapter_goal": chapter_goal,
        "target_word_count": word_count,
        "scene_chain": scenes,
        "sweet_point_plan": [
            "先让读者看到压迫或损失。",
            "再让主角作出主动选择。",
            "兑现时必须有对方失态、旁人反应、资源变化或关系权力变化。",
            "爽点后立即抛出下一层阻碍，防止章节塌陷。",
        ],
        "cliffhanger_rule": "结尾留新信息、新危险、新误会、新邀约或更高层敌人，不用空泛悬念。",
        "style_card": style_card,
        "voice_matrix": {"characters": characters, "dialogue_rules": ["每句对白至少承担身份、利益、情绪或隐瞒信息之一。", "避免所有角色同一种解释腔。"]},
        "must_include": must_include,
        "avoid": avoid + ["论文腔总结", "命运齿轮式假文学", "所有人物说话像同一个人", "无代价碾压"],
        "writer_output_contract": [
            "直接输出正文，不要先解释写作思路。",
            "段落不要平均；压迫段和爆点段用短句。",
            "每个场景结束都要有状态变化。",
            "用动作、对白、反应替代抽象心理说明。",
        ],
    }
    return brief


def _revision_plan(text: str, diagnosis: dict[str, Any] | None = None, strictness: str = "medium") -> dict[str, Any]:
    scan = diagnosis or _scan_text(text)
    patches: list[dict[str, Any]] = []
    for risk in scan.get("risks", []):
        rtype = risk.get("type")
        if rtype == "ai_tone":
            patches.append({"priority": "high", "target": "AI腔/说明腔", "action": "删除机械连接词和总结句，改成动作、对白、物件或场景反应。"})
        elif rtype == "cliche":
            patches.append({"priority": "medium", "target": "网文套话", "action": "保留爽点功能，换成当前人物独有动作和当前场景独有细节。"})
        elif rtype == "abstract_density":
            patches.append({"priority": "high", "target": "抽象心理/主题表达", "action": "每段补一个身体动作、一个环境物件、一个具体声音/触感。"})
        elif rtype == "rhythm_flat":
            patches.append({"priority": "medium", "target": "句式节奏", "action": "爆点处拆成二到四个短句；铺垫段保留中句。"})
        elif rtype == "dialogue_low":
            patches.append({"priority": "medium", "target": "对白和人物声线", "action": "增加带利益、试探、反问或隐瞒信息的对白。"})
    if not patches:
        patches.append({"priority": "low", "target": "整体润色", "action": "保留主结构，增强动作细节、章末钩子和人物声线差异。"})
    return {
        "strictness": strictness,
        "patches": patches,
        "rewrite_order": ["先修场景目标和冲突", "再修人物动作与对白", "再修句子节奏", "最后修章末钩子"],
        "final_output_rule": "LLM 根据 patches 直接输出修订稿；不要只输出建议。",
    }


def _serial_update(args: dict[str, Any], bible: dict[str, Any], chapter_brief: dict[str, Any]) -> dict[str, Any]:
    feedback = str(_get(args, "reader_feedback", ""))
    return {
        "chapter_summary_update": {
            "chapter_index": chapter_brief.get("chapter_index", 0),
            "core_change": "本章应记录：目标、冲突、结果、资源变化、关系变化、伏笔变化。",
            "new_open_loops": ["下一章要立刻回应章末钩子，不要跳空。"],
        },
        "story_bible_update_plan": [
            "更新人物关系和情绪状态。",
            "更新战力/资源/道具状态。",
            "更新已埋伏笔、已回收伏笔和未解问题。",
        ],
        "reader_feedback_digest": feedback or "暂无读者反馈；按题材默认留存逻辑推进。",
        "next_chapter_push": "下一章优先承接本章钩子，再制造新的具体阻碍。",
    }


def _draft_instruction(chapter_brief: dict[str, Any]) -> str:
    lines = [
        "按以下生产简报直接写网络小说正文，不要解释思路：",
        f"目标字数：{chapter_brief.get('target_word_count', 2200)}",
        f"本章目标：{chapter_brief.get('chapter_goal', '')}",
        "场景链：",
    ]
    for card in chapter_brief.get("scene_chain", []):
        lines.append(f"{card.get('scene')}. {card.get('beat')}｜冲突：{card.get('conflict')}｜结果：{card.get('outcome')}")
    lines.extend([
        "文风要求：" + str(chapter_brief.get("style_card", {}).get("style_target", "")),
        "爽点要求：先压迫、再主动选择、再兑现反击/反转、最后留下一层新期待。",
        "对白要求：人物说话必须体现身份、利益、情绪或隐瞒信息。",
        "禁止项：" + "、".join(str(x) for x in chapter_brief.get("avoid", [])),
    ])
    return "\n".join(lines)


def wangwen_novel_factory_run_adapter(first: Any, second: Any) -> Any:
    invocation, context, args = _split_call(first, second)
    bible = _build_bible(args)
    chapter = _chapter_brief(args, bible)
    text = str(_get(args, "text", "") or _get(args, "existing_draft", ""))
    diagnosis = _scan_text(text) if text.strip() else None
    revision = _revision_plan(text, diagnosis, str(_get(args, "strictness", "medium") or "medium")) if text.strip() else None
    data = {
        "pipeline_contract": [
            {"stage": 1, "name": "类型承诺与读者期待", "folded_from": "35 类型定位"},
            {"stage": 2, "name": "金手指/升级/奖励循环", "folded_from": "36 爽点与金手指"},
            {"stage": 3, "name": "章节场景链/钩子/伏笔", "folded_from": "37 情节节奏"},
            {"stage": 4, "name": "人物欲望/群像/对白声线", "folded_from": "38 人物群像"},
            {"stage": 5, "name": "世界规则/战力/现实细节", "folded_from": "39 世界观设定"},
            {"stage": 6, "name": "连载留存/反馈/故事圣经更新", "folded_from": "40 连载读者反馈"},
            {"stage": 7, "name": "文风卡/人物声纹/去AI腔", "folded_from": "41 去AI腔 + 53 文风流水线"},
            {"stage": 8, "name": "终审质量门与修订补丁", "folded_from": "42 终审质检"},
        ],
        "book_bible": bible,
        "chapter_production_brief": chapter,
        "draft_instruction": _draft_instruction(chapter),
        "quality_report": diagnosis,
        "revision_patch_plan": revision,
        "serial_state_update": _serial_update(args, bible, chapter),
        "execution_rule": "默认只调用本工具完成生产线穿线；LLM 随后按 draft_instruction 直接输出正文或修订稿。",
        "boundary": "本工具不发布、不投稿、不写入外部平台；最终正文由 LLM 根据生产简报生成。",
        "next_action": "直接输出成品正文；若输入了 text/existing_draft，则直接输出修订稿。",
    }
    return _result(invocation, context, "wangwen_novel_factory_run", "ok", "网络小说完整生产线已穿线。", data)


def wangwen_novel_bible_build_adapter(first: Any, second: Any) -> Any:
    invocation, context, args = _split_call(first, second)
    data = {"book_bible": _build_bible(args), "next_action": "用故事圣经继续生成章节生产简报或开书大纲。"}
    return _result(invocation, context, "wangwen_novel_bible_build", "ok", "网络小说故事圣经已生成。", data)


def wangwen_chapter_brief_build_adapter(first: Any, second: Any) -> Any:
    invocation, context, args = _split_call(first, second)
    bible = _build_bible(args)
    chapter = _chapter_brief(args, bible)
    data = {"chapter_production_brief": chapter, "draft_instruction": _draft_instruction(chapter), "next_action": "LLM 按 draft_instruction 直接输出正文。"}
    return _result(invocation, context, "wangwen_chapter_brief_build", "ok", "单章生产简报已生成。", data)


def wangwen_draft_quality_check_adapter(first: Any, second: Any) -> Any:
    invocation, context, args = _split_call(first, second)
    text = str(_get(args, "text", "") or _get(args, "existing_draft", ""))
    if not text.strip():
        return _result(invocation, context, "wangwen_draft_quality_check", "failed", "缺少待检查正文。", {"retryable": True}, "missing_text")
    data = _scan_text(text)
    data["next_action"] = "如果 score<78 或存在 high 风险，调用 wangwen_revision_plan_build 后直接输出修订稿。"
    return _result(invocation, context, "wangwen_draft_quality_check", "ok", "网络小说成稿质量检查完成。", data)


def wangwen_revision_plan_build_adapter(first: Any, second: Any) -> Any:
    invocation, context, args = _split_call(first, second)
    text = str(_get(args, "text", "") or _get(args, "existing_draft", ""))
    if not text.strip():
        return _result(invocation, context, "wangwen_revision_plan_build", "failed", "缺少待修订正文。", {"retryable": True}, "missing_text")
    diagnosis = _get(args, "diagnosis", {}) if isinstance(_get(args, "diagnosis", {}), dict) else _scan_text(text)
    data = {"diagnosis": diagnosis, "revision_patch_plan": _revision_plan(text, diagnosis, str(_get(args, "strictness", "medium") or "medium")), "next_action": "LLM 根据 revision_patch_plan 直接输出修订稿。"}
    return _result(invocation, context, "wangwen_revision_plan_build", "ok", "网络小说修订补丁已生成。", data)
