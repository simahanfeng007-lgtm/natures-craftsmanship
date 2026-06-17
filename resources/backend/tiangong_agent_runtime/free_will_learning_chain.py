"""
L6.80 自由意志学习链。
聊天经验沉淀→空闲时自主上网学习→LLM判→生成skill→补tool。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .tool_invocation import ToolInvocation
from .tool_result import ToolResult, ToolResultStatus
from .turn_context import TurnContext

# ── 经验池 ──────────────────────────────────────────────────
JINGYAN_CHI_LUJING = Path.home() / ".tiangong" / "jingyan" / "jingyan_chi.jsonl"


def _zhaichao(text: str, limit: int = 200) -> str:
    return text.strip()[:limit]


def _cankao_digest(*cailiao: Any) -> str:
    raw = "|".join(str(c) for c in cailiao)
    return hashlib.sha256(raw.encode()).hexdigest()[:10]


@dataclass
class JingyanTiao:
    """经验条目"""
    tiao_id: str
    laiyuan: str  # "chat"/"code_repair"/"file_exec"
    zhaiyao: str
    yuanshi_renwu: str  # 用户原始消息
    chuangjian_shijian: float = field(default_factory=time.time)
    yichuli: bool = False  # 是否已被学习链处理
    xuexi_jieguo: str = ""  # 学习结果
    shengcheng_skill: str = ""  # 生成的skill名
    shengcheng_tool: str = ""  # 生成的tool名

    def gongkai_zidian(self) -> dict[str, Any]:
        return {
            "tiao_id": self.tiao_id,
            "laiyuan": self.laiyuan,
            "zhaiyao": self.zhaiyao,
            "yuanshi_renwu": self.yuanshi_renwu,
            "chuangjian_shijian": self.chuangjian_shijian,
            "yichuli": self.yichuli,
            "xuexi_jieguo": self.xuexi_jieguo,
            "shengcheng_skill": self.shengcheng_skill,
            "shengcheng_tool": self.shengcheng_tool,
        }


class JingyanChi:
    """经验池：JSONL文件持久化"""

    def __init__(self, lujing: Path | None = None):
        self.lujing = Path(lujing or JINGYAN_CHI_LUJING)
        self.lujing.parent.mkdir(parents=True, exist_ok=True)

    def touru(self, laiyuan: str, zhaiyao: str, yuanshi_renwu: str) -> JingyanTiao:
        tiao_id = f"jy_{_cankao_digest(laiyuan, zhaiyao, time.time())}"
        tiao = JingyanTiao(tiao_id=tiao_id, laiyuan=laiyuan, zhaiyao=zhaiyao, yuanshi_renwu=yuanshi_renwu)
        with open(self.lujing, "a", encoding="utf-8") as f:
            f.write(json.dumps(tiao.gongkai_zidian(), ensure_ascii=False) + "\n")
        return tiao

    def _du_quanbu_raw(self) -> list[dict[str, Any] | str]:
        if not self.lujing.exists():
            return []
        rows: list[dict[str, Any] | str] = []
        with open(self.lujing, encoding="utf-8") as f:
            for hang in f:
                line = hang.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    rows.append(line)
        return rows

    def _xie_quanbu_raw(self, rows: list[dict[str, Any] | str]) -> None:
        import tempfile

        linshi = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.lujing.parent, delete=False, suffix=".tmp")
        try:
            for row in rows:
                if isinstance(row, dict):
                    linshi.write(json.dumps(row, ensure_ascii=False) + "\n")
                else:
                    linshi.write(str(row) + "\n")
            linshi.flush()
            os.replace(linshi.name, self.lujing)
        finally:
            if os.path.exists(linshi.name):
                os.unlink(linshi.name)

    def tiaomu_by_id(self, tiao_id: str) -> JingyanTiao | None:
        """按 ID 读取经验条目。"""
        target = str(tiao_id or "").strip()
        if not target:
            return None
        for row in self._du_quanbu_raw():
            if not isinstance(row, dict) or row.get("tiao_id") != target:
                continue
            return JingyanTiao(**{k: v for k, v in row.items() if k in JingyanTiao.__dataclass_fields__})
        return None

    def weichuli_tiaomu(self, xianzhi: int = 5) -> list[JingyanTiao]:
        """返回未处理条目，最多 xianzhi 条"""
        if not self.lujing.exists():
            return []
        tiaomu = []
        with open(self.lujing, encoding="utf-8") as f:
            for hang in f:
                hang = hang.strip()
                if not hang:
                    continue
                try:
                    d = json.loads(hang)
                except json.JSONDecodeError:
                    continue
                if not d.get("yichuli"):
                    tiaomu.append(JingyanTiao(**{k: v for k, v in d.items() if k in JingyanTiao.__dataclass_fields__}))
                if len(tiaomu) >= xianzhi:
                    break
        return tiaomu

    def biaoji_yichuli(self, tiao_id: str, xuexi_jieguo: str = "", shengcheng_skill: str = "", shengcheng_tool: str = "") -> None:
        """标记某条为已处理，写入学习结果"""
        if not self.lujing.exists():
            return
        hang_liebiao = []
        with open(self.lujing, encoding="utf-8") as f:
            for hang in f:
                hang = hang.strip()
                if not hang:
                    continue
                try:
                    d = json.loads(hang)
                except json.JSONDecodeError:
                    hang_liebiao.append(hang)
                    continue
                if d.get("tiao_id") == tiao_id:
                    d["yichuli"] = True
                    d["xuexi_jieguo"] = xuexi_jieguo
                    d["shengcheng_skill"] = shengcheng_skill
                    d["shengcheng_tool"] = shengcheng_tool
                hang_liebiao.append(json.dumps(d, ensure_ascii=False))
        self._xie_quanbu_raw(hang_liebiao)

    def shanchu_weixuexi(self, tiao_id: str) -> dict[str, Any]:
        """删除尚未学习的经验条目；已处理条目不允许删。"""
        target = str(tiao_id or "").strip()
        if not target:
            return {"ok": False, "error": "missing_id"}
        rows = self._du_quanbu_raw()
        if not rows:
            return {"ok": False, "error": "empty_pool", "id": target}
        next_rows: list[dict[str, Any] | str] = []
        removed: dict[str, Any] | None = None
        for row in rows:
            if isinstance(row, dict) and row.get("tiao_id") == target:
                if bool(row.get("yichuli")):
                    return {"ok": False, "error": "already_learned", "id": target, "item": row}
                removed = row
                continue
            next_rows.append(row)
        if removed is None:
            return {"ok": False, "error": "not_found", "id": target}
        self._xie_quanbu_raw(next_rows)
        return {"ok": True, "id": target, "removed": removed}


# ── 网上搜索 ─────────────────────────────────────────────────
def wangshang_sousuo(chaxun: str, jieguo_shu: int = 5) -> str:
    """用 Bing 搜索，返回文本摘要。无需API密钥。"""
    try:
        import urllib.parse
        bianma = urllib.parse.quote(chaxun)
        jieguo = subprocess.run(
            [
                "curl", "-s", "--max-time", "12", "-L",
                "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "-H", "Accept-Language: zh-CN,zh;q=0.9",
                f"https://cn.bing.com/search?q={bianma}"
            ],
            capture_output=True, text=True, timeout=15
        )
        if jieguo.returncode != 0:
            return f"[搜索失败: exit={jieguo.returncode}]"

        # 提取 Bing 搜索结果摘要
        import re
        # Bing 结果摘要通常在 <p> 或 <span class="b_caption"> 中
        pianduan = re.findall(r'<p[^>]*>(.*?)</p>', jieguo.stdout, re.DOTALL)
        # 过滤太短的和纯标签的
        jieguo_wenben = []
        for p in pianduan:
            qingli = re.sub(r'<[^>]+>', '', p).strip()
            qingli = re.sub(r'&[a-z]+;', ' ', qingli).strip()
            if len(qingli) > 30 and not re.match(r'^[ \t\n\r]*$', qingli):
                # 去重
                if qingli not in jieguo_wenben:
                    jieguo_wenben.append(qingli[:250])
                if len(jieguo_wenben) >= jieguo_shu:
                    break

        if not jieguo_wenben:
            return "[无搜索结果]"
        return "\n".join(f"[{i+1}] {w}" for i, w in enumerate(jieguo_wenben))
    except Exception as e:
        return f"[搜索异常: {e}]"


# ── 学习链 ───────────────────────────────────────────────────
class XuexiLian:
    """自由意志学习链：扫池→LLM判定→搜索→学习→skill→tool"""

    def __init__(self, moxing_kehuduan: Any, jingyan_chi: JingyanChi | None = None, web_searcher: Any | None = None):
        self.moxing = moxing_kehuduan
        self.chi = jingyan_chi or JingyanChi()
        self.web_searcher = web_searcher
        self.zuihou_jieguo: str = ""

    def _sousuo(self, chaxun: str) -> str:
        if callable(self.web_searcher):
            try:
                jieguo = str(self.web_searcher(chaxun) or "").strip()
                if jieguo:
                    return jieguo
            except Exception:
                pass
        return wangshang_sousuo(chaxun)

    def _llm_pan(self, xitong_tishi: str, yonghu_xiaoxi: str, wendu: float = 0.2, zuida_lingpai: int = 400) -> str:
        """调用LLM，返回文本"""
        try:
            resp = self.moxing.chat.completions.create(
                model=getattr(self.moxing, "model", "deepseek-v4-pro"),
                messages=[
                    {"role": "system", "content": xitong_tishi},
                    {"role": "user", "content": yonghu_xiaoxi},
                ],
                temperature=wendu,
                max_tokens=zuida_lingpai,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            return f"[LLM异常: {e}]"

    def _juece_json(self, xitong_tishi: str, yonghu_xiaoxi: str) -> dict[str, Any]:
        """LLM判→返回JSON dict"""
        import json as _json
        daan = self._llm_pan(xitong_tishi, yonghu_xiaoxi, wendu=0.1, zuida_lingpai=300)
        kaishi = daan.find("{")
        jieshu = daan.rfind("}") + 1
        if kaishi >= 0 and jieshu > kaishi:
            try:
                return _json.loads(daan[kaishi:jieshu])
            except _json.JSONDecodeError:
                pass
        return {}

    def yunxing(self, yuanshi_xiaoxi: str = "", jingyan_beizhu: str = "", laiyuan: str = "chat", l3_houxuan: list[dict] | None = None, target_tiao_id: str = "") -> str:
        """
        完整学习链。
        - 如果 l3_houxuan 非空 → 选题从 L3 记忆库选（优先级最高）
        - 否则回退到经验池选题
        返回可读的执行摘要。
        """
        buzhou = []

        # ── 0. 选题：L3 优先 → 经验池回退 ──
        xuanding = None
        sousuo_cx = ""
        laizi_l3 = False

        target_tiao_id = str(target_tiao_id or "").strip()

        if l3_houxuan:
            # 从 L3 记忆库选题
            houxuan_wenben = "\n".join(
                f"  [{i}] L3记忆: {h.get('zhaiyao', h.get('neirong', ''))[:200]}"
                for i, h in enumerate(l3_houxuan[:5], 1)
            )
            tiaoxuan_juece = self._juece_json(
                "你是自主学习选题器。从L3记忆库中挑一个最值得上网深入学习的主题。只输出JSON。",
                f"L3候选记忆：\n{houxuan_wenben}\n\n"
                '输出JSON：{"xuanti_index": 数字, "liyou": "理由≤20字", "sousuo_chaxun": "搜索查询词≤30字"}'
            )
            suoyin = max(0, min(len(l3_houxuan) - 1, int(tiaoxuan_juece.get("xuanti_index", 1)) - 1))
            xuanding_dict = l3_houxuan[suoyin]
            sousuo_cx = tiaoxuan_juece.get("sousuo_chaxun", xuanding_dict.get("zhaiyao", "")[:30])
            xuanding_zhaiyao = xuanding_dict.get("zhaiyao", xuanding_dict.get("neirong", ""))[:300]
            xuanding_lujing = xuanding_dict.get("lujing")  # Path 对象
            laizi_l3 = True
            buzhou.append(f"L3选题: {xuanding_dict.get('wenjian', '?')} → 搜索「{sousuo_cx}」")
        else:
            # 回退经验池
            if jingyan_beizhu and yuanshi_xiaoxi:
                tiao = self.chi.touru(laiyuan=laiyuan, zhaiyao=jingyan_beizhu, yuanshi_renwu=yuanshi_xiaoxi)
                buzhou.append(f"已入池: {tiao.tiao_id}")

            weichuli = self.chi.weichuli_tiaomu(xianzhi=50 if target_tiao_id else 3)
            if target_tiao_id:
                weichuli = [t for t in weichuli if t.tiao_id == target_tiao_id]
            if not weichuli:
                self.zuihou_jieguo = f"未找到未学习条目：{target_tiao_id}" if target_tiao_id else "池中无未学条目"
                return self.zuihou_jieguo

            if target_tiao_id:
                xuanding = weichuli[0]
                sousuo_cx = xuanding.zhaiyao[:50]
                buzhou.append(f"指定学习: {xuanding.tiao_id} → 搜索「{sousuo_cx}」")
            else:
                houxuan_wenben = "\n".join(
                    f"  [{i}] {t.tiao_id}: {t.zhaiyao[:150]} (来源:{t.laiyuan})"
                    for i, t in enumerate(weichuli, 1)
                )

                tiaoxuan_juece = self._juece_json(
                    "你是自主学习选题器。从经验池中挑一个最值得上网深入学习的主题。只输出JSON。",
                    f"候选经验：\n{houxuan_wenben}\n\n"
                    '输出JSON：{"xuanti_index": 数字, "liyou": "理由≤20字", "sousuo_chaxun": "搜索查询词≤30字"}'
                )
                suoyin = max(0, min(len(weichuli) - 1, int(tiaoxuan_juece.get("xuanti_index", 1)) - 1))
                xuanding = weichuli[suoyin]
                sousuo_cx = tiaoxuan_juece.get("sousuo_chaxun", xuanding.zhaiyao[:30])
                buzhou.append(f"选定学习: {xuanding.tiao_id} → 搜索「{sousuo_cx}」")
            xuanding_zhaiyao = xuanding.zhaiyao[:300]

        # ── 2. 网上搜索 ──
        sousuo_jieguo = self._sousuo(sousuo_cx)
        buzhou.append(f"搜索完成: {len(sousuo_jieguo)}字符")

        # ── 3. LLM 学习总结 ──
        xuexi_zongjie = self._llm_pan(
            "你是知识萃取器。根据搜索结果和原始经验，提炼出可复用的知识点。≤200字。",
            f"原始经验：{xuanding_zhaiyao}\n原始任务：{yuanshi_xiaoxi if not laizi_l3 else xuanding_zhaiyao[:200]}\n"
            f"搜索结果：\n{sousuo_jieguo}\n\n学习总结：",
            zuida_lingpai=300,
        )
        buzhou.append(f"学习总结: {xuexi_zongjie[:80]}...")

        # ── 4. LLM 判断：值得生成skill吗？ ──
        pinzhi_juece = self._juece_json(
            "你是技能判定器。判断学到的知识是否值得做成skill。只输出JSON。",
            f"学习总结：{xuexi_zongjie}\n\n"
            '输出JSON：{"zhide_skill": true/false, "liyou": "理由≤20字", '
            '"skill_ming": "skill名称(英文连字符)", "skill_miaoshu": "一句话描述"}'
        )

        if not pinzhi_juece.get("zhide_skill"):
            if laizi_l3 and xuanding_lujing:
                self._biaoji_l3_yixue(xuanding_lujing, xuexi_zongjie)
            else:
                self.chi.biaoji_yichuli(xuanding.tiao_id, xuexi_jieguo=xuexi_zongjie)
            buzhou.append("判定: 不值得生成skill")
            self.zuihou_jieguo = " → ".join(buzhou)
            return self.zuihou_jieguo

        skill_ming = pinzhi_juece.get("skill_ming", "auto_learned_skill")
        skill_ms = pinzhi_juece.get("skill_miaoshu", "")
        buzhou.append(f"判定: 值得→skill「{skill_ming}」")

        # ── 5. 生成 skill ──
        buzhou.append(f"待生成skill: {skill_ming} ({skill_ms})")

        # ── 6. 检查是否需要 tool ──
        tool_juece = self._juece_json(
            "你是工具判定器。判断这个skill是否需要一个新tool来支撑。只输出JSON。",
            f"Skill名：{skill_ming}\n描述：{skill_ms}\n学习总结：{xuexi_zongjie[:200]}\n\n"
            '输出JSON：{"xuyao_tool": true/false, "liyou": "理由≤20字", '
            '"tool_ming": "工具名(英文连字符)", "tool_ms": "一句话工具描述"}'
        )

        shengcheng_tool = ""
        if tool_juece.get("xuyao_tool"):
            shengcheng_tool = tool_juece.get("tool_ming", "")
            buzhou.append(f"需要tool: {shengcheng_tool}")
        else:
            buzhou.append("无需额外tool")

        # ── 7. 标记已处理 ──
        if laizi_l3 and xuanding_lujing:
            self._biaoji_l3_yixue(xuanding_lujing, xuexi_zongjie, skill_ming, shengcheng_tool)
        else:
            self.chi.biaoji_yichuli(
                xuanding.tiao_id,
                xuexi_jieguo=xuexi_zongjie,
                shengcheng_skill=skill_ming,
                shengcheng_tool=shengcheng_tool,
            )

        self.zuihou_jieguo = " → ".join(buzhou)
        return self.zuihou_jieguo

    def _biaoji_l3_yixue(self, lujing: Path, xuexi_jieguo: str, skill: str = "", tool: str = "") -> None:
        """在 L3 文件中追加「已学习」标记。"""
        try:
            neirong = lujing.read_text(encoding="utf-8") if lujing.exists() else ""
        except Exception:
            return
        from datetime import datetime
        ji = f"""

## 学习记录
- 学习时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}
- 学习结果：{xuexi_jieguo[:200]}
- 生成skill：{skill or '无'}
- 生成tool：{tool or '无'}
- 状态：已学习 ✓
"""
        lujing.write_text(neirong + ji, encoding="utf-8")

    def chansheng_skill_he_tool(
        self, shangxianwen: Any, yuanshi_xiaoxi: str, jingyan_beizhu: str, laiyuan: str = "free_will"
    ) -> dict[str, Any]:
        """
        产生 skill 和 tool 的调用请求（需在 runtime_entry 上下文中执行）。
        返回 {skill_inv: ToolInvocation|None, tool_inv: ToolInvocation|None}
        """
        # 先运行学习链（不重复投池，只做学习→判定）
        jieguo = self.yunxing(yuanshi_xiaoxi=yuanshi_xiaoxi, jingyan_beizhu=jingyan_beizhu, laiyuan=laiyuan)

        # 从最后结果解析 skill/tool 名
        skill_ming = ""
        tool_ming = ""
        skill_ms = ""

        import re
        sm = re.search(r"待生成skill:\s*(\S+)", jieguo)
        if sm:
            skill_ming = sm.group(1)
            skill_ms = re.search(rf"{re.escape(skill_ming)}\s*\(([^)]*)\)", jieguo)
            skill_ms = skill_ms.group(1) if skill_ms else ""

        tm = re.search(r"需要tool:\s*(\S+)", jieguo)
        if tm:
            tool_ming = tm.group(1)

        jieguo_dict: dict[str, Any] = {"skill_inv": None, "tool_inv": None, "lian_jieguo": jieguo}

        if skill_ming:
            jieguo_dict["skill_inv"] = ToolInvocation(
                "queue_skill_candidates",
                {"notes": f"skill={skill_ming}\n描述: {skill_ms}\n学自: {jingyan_beizhu}", "max_items": 1},
            )
        if tool_ming and skill_ming:
            jieguo_dict["tool_inv"] = ToolInvocation(
                "queue_tool_production_requests",
                {"notes": f"tool={tool_ming}\nskill={skill_ming}\n学自: {jingyan_beizhu}", "max_items": 1},
            )

        return jieguo_dict
