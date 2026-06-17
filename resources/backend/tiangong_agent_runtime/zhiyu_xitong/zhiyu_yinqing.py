"""L6.72 自愈引擎 —— 紧急系统修复室。

触发条件：系统级异常（服务崩溃/OOM/fd耗尽/启动失败等），不是任务小失败。
流程：读架构知识库 → 读更新日志 → LLM诊断 → 输出修复/回滚方案。

本模块只做诊断和方案生成，不直接执行修复。执行由调用方通过 CodeX 完成。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .gengxin_rizhi import du_rizhi

ZHISHIKU_LUJING = Path(__file__).parent / "zhiyu_zhishiku.md"


def _du_zhishiku() -> str:
    """读取架构知识库全文。"""
    if ZHISHIKU_LUJING.exists():
        return ZHISHIKU_LUJING.read_text(encoding="utf-8")
    return "(自愈知识库缺失)"


def _du_rizhi_wenben(zuijin: int = 20) -> str:
    """读取更新日志为可读文本。"""
    tiaomu = du_rizhi(zuijin)
    if not tiaomu:
        return "(无更新记录)"
    hang = []
    for t in tiaomu:
        hang.append(
            f"- [{t.get('shijian','?')}] {t.get('zhaiyao','?')} "
            f"文件: {', '.join(t.get('wenjian',[]))} "
            f"回滚: {t.get('huigun_yaodian','无')}"
        )
    return "\n".join(hang)


def zhiyu_zhenduan(
    model_client: Any,
    guzhang_miaoshu: str,
    *,
    yunxing_rizhi: str = "",
) -> dict[str, Any]:
    """自愈诊断。"""
    if model_client is None:
        return {"xuyao_xiufu": False, "zhenduan": "无LLM客户端，无法诊断"}

    zhishiku = _du_zhishiku()
    rizhi_wenben = _du_rizhi_wenben()

    system_prompt = f"""你是天工造物自愈系统诊断器。以下是天工造物完整架构知识库：

{zhishiku}

遇到系统级故障时，你需要：
1. 根据知识库理解系统架构
2. 根据更新日志判断最近改动是否引入bug
3. 输出诊断结论和修复方案（回滚或修复）
4. 只输出JSON，不输出其他内容"""

    yonghu_prompt = f"""最近更新记录：
{rizhi_wenben}

故障描述：
{guzhang_miaoshu}

{'运行日志片段：' + yunxing_rizhi[:2000] if yunxing_rizhi else ''}

请诊断并输出JSON：
{{
    "xuyao_xiufu": true/false,
    "yanzhong_chengdu": "紧急/高/中/低",
    "zhenduan": "诊断结论，≤100字",
    "genyin": "根因分析，≤100字",
    "fangan": "修复方案，≤200字",
    "xuyao_huigun": true/false,
    "huigun_mubiao": "如需回滚，描述回滚目标；否则空字符串",
    "buzhou": ["步骤1", "步骤2"]
}}"""

    try:
        resp = model_client.chat.completions.create(
            model=getattr(model_client, "model", "deepseek-v4-pro"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": yonghu_prompt},
            ],
            temperature=0.0,
            max_tokens=800,
        )
        raw = resp.choices[0].message.content.strip()
        s = raw.find("{")
        e = raw.rfind("}") + 1
        if s >= 0 and e > s:
            return json.loads(raw[s:e])
        return {"xuyao_xiufu": False, "zhenduan": f"LLM输出解析失败: {raw[:200]}"}
    except Exception as e:
        return {"xuyao_xiufu": False, "zhenduan": f"LLM调用失败: {e}"}


def shi_xitongji_yichang(yichang_xinxi: str) -> bool:
    """判断是否为系统级异常（触发自愈），而非任务级小失败。"""
    guanjianci = [
        "oom", "out of memory", "内存不足", "memoryerror",
        "file descriptor", "fd ", "too many open files",
        "segfault", "segmentation fault", "core dump",
        "启动失败", "import error", "modulenotfound",
        "connection refused", "端口", "port",
        "gateway", "网关",
        "crash", "崩溃", "退出", "exit",
        "signal", "sigkill", "sigterm",
        "limit", "ulimit",
    ]
    lower = yichang_xinxi.lower()
    return any(k in lower for k in guanjianci)
