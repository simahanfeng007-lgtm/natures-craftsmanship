"""L6.72 自愈系统更新日志。

每次系统改动(代码修改/配置变更/架构调整)时追加一条记录。
自愈引擎诊断时先读此日志，定位最近改动是否引入bug。

格式(JSONL)：{"shijian":"2026-06-13T06:50:00","wenjian":["runtime_entry.py"],"zhaiyao":"描述","huigun_yaodian":"回滚要点"}
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RIZHI_LUJING = Path(__file__).parent / "gengxin_rizhi.jsonl"


def jilu_gengxin(
    wenjian: list[str],
    zhaiyao: str,
    huigun_yaodian: str = "",
    *,
    shijian: str | None = None,
) -> None:
    """追加一条更新记录。"""
    if shijian is None:
        shijian = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tiaomu: dict[str, Any] = {
        "shijian": shijian,
        "wenjian": [str(w) for w in wenjian],
        "zhaiyao": zhaiyao,
        "huigun_yaodian": huigun_yaodian,
    }
    with open(RIZHI_LUJING, "a", encoding="utf-8") as f:
        f.write(json.dumps(tiaomu, ensure_ascii=False) + "\n")


def du_rizhi(zuijin_n_tiao: int = 20) -> list[dict[str, Any]]:
    """读取最近N条更新记录。"""
    if not RIZHI_LUJING.exists():
        return []
    tiaomu: list[dict[str, Any]] = []
    with open(RIZHI_LUJING, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    tiaomu.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return tiaomu[-zuijin_n_tiao:]


# 初始化：写入首条记录(知识库创建)
if not RIZHI_LUJING.exists():
    jilu_gengxin(
        wenjian=["zhiyu_xitong/zhiyu_zhishiku.md", "zhiyu_xitong/gengxin_rizhi.py"],
        zhaiyao="自愈系统初始化：架构知识库+更新日志机制",
        huigun_yaodian="删除 zhiyu_xitong/ 目录即可回滚",
    )
