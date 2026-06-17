"""
L6.81 自由意志总调度引擎。
凌晨心跳唯一入口 → 随机激活LLM执行任务 → 产出分类 → 分发。
  系统改进 → 迭代池
  知识技能 → 自学链
"""

from __future__ import annotations

import random, os, time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .free_will_learning_chain import wangshang_sousuo, JingyanChi


# ── 产出分类 ─────────────────────────────────────────────────
CHANCHU_LEIXING = ["xitong_gaijin", "zhishi_jineng", "wuyong"]


@dataclass
class ZiyouYizhiChanchu:
    """自由意志一次随机任务的产出"""
    dongzuo_ming: str
    leixing: str  # xitong_gaijin / zhishi_jineng / wuyong
    zhaiyao: str
    neirong: str = ""  # 详细内容，给下游用
    laiyuan_xinxi: str = ""  # 来源文件/搜索词等，给迭代池参考


class ZiyouYizhiYinqing:
    """自由意志总调度引擎"""

    def __init__(self, moxing_kehuduan: Any, daima_lujing: str = ""):
        self.moxing = moxing_kehuduan
        self.daima_lujing = daima_lujing or str(Path.home() / "桌面" / "开发版" / "backend")
        self.chi = JingyanChi()

    def _llm(self, xitong: str, yonghu: str, wendu: float = 0.2, pai: int = 300) -> str:
        try:
            resp = self.moxing.chat.completions.create(
                model=getattr(self.moxing, "model", "deepseek-v4-pro"),
                messages=[{"role": "system", "content": xitong}, {"role": "user", "content": yonghu}],
                temperature=wendu, max_tokens=pai,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            return f"[LLM异常: {e}]"

    def _json(self, xitong: str, yonghu: str) -> dict[str, Any]:
        import json as _json
        daan = self._llm(xitong, yonghu, wendu=0.1, pai=300)
        kaishi = daan.find("{")
        jieshu = daan.rfind("}") + 1
        if kaishi >= 0 and jieshu > kaishi:
            try:
                return _json.loads(daan[kaishi:jieshu])
            except _json.JSONDecodeError:
                pass
        return {}

    def yunxing(self) -> list[ZiyouYizhiChanchu]:
        """运行一次自由意志：随机选动作→执行→分类→返回所有产出"""
        chanchu_liebiao = []

        # 随机选1-2个动作
        dongzuo_shu = random.choice([1, 1, 2])  # 大部分1个，偶尔2个
        dongzuo_liebiao = random.sample(["daima_zhijian", "xuexi_zhishi", "jiagou_shencha"], min(dongzuo_shu, 3))

        for dongzuo in dongzuo_liebiao:
            if dongzuo == "daima_zhijian":
                chanchu = self._zhijian_daima()
            elif dongzuo == "xuexi_zhishi":
                chanchu = self._xuexi_zhishi()
            elif dongzuo == "jiagou_shencha":
                chanchu = self._jiagou_shencha()
            else:
                continue
            if chanchu:
                chanchu_liebiao.append(chanchu)

        return chanchu_liebiao

    # ── 动作1：代码质检 ──────────────────────────────────────
    def _zhijian_daima(self) -> ZiyouYizhiChanchu | None:
        py_files = []
        for root, dirs, files in os.walk(self.daima_lujing):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
            for f in files:
                if f.endswith(".py") and not f.startswith("."):
                    py_files.append(os.path.join(root, f))
            if len(py_files) > 100:
                break
        if not py_files:
            return None

        mugiao = random.choice(py_files)
        try:
            with open(mugiao, encoding="utf-8") as f:
                neirong = f.read()[:2000]
        except Exception:
            return None

        fenxi = self._llm(
            "代码审查。发现潜在的bug、安全漏洞、性能瓶颈、架构坏味。只报告真实问题，不过滤风格问题。无则说「无」。≤120字。",
            f"文件：{os.path.basename(mugiao)}\n代码：\n{neirong}\n\n分析：",
            pai=200,
        )
        if not fenxi or fenxi.startswith("[LLM异常") or "无" in fenxi[:10]:
            return ZiyouYizhiChanchu(
                dongzuo_ming="daima_zhijian", leixing="wuyong",
                zhaiyao=f"质检 {os.path.basename(mugiao)} 未发现问题",
            )

        # LLM 分类：系统改进 or 知识技能？
        fenlei = self._json(
            "分类器。判断这个发现应该归为系统改进还是知识技能。只输出JSON。",
            f"发现：{fenxi}\n\n"
            '输出JSON：{"leixing": "xitong_gaijin/zhishi_jineng", "liyou": "理由≤15字"}'
        )
        leixing = fenlei.get("leixing", "xitong_gaijin")

        return ZiyouYizhiChanchu(
            dongzuo_ming="daima_zhijian", leixing=leixing,
            zhaiyao=f"质检 {os.path.basename(mugiao)}: {fenxi[:80]}",
            neirong=fenxi,
            laiyuan_xinxi=f"文件: {mugiao}",
        )

    # ── 动作2：学习知识 ──────────────────────────────────────
    def _xuexi_zhishi(self) -> ZiyouYizhiChanchu | None:
        weichuli = self.chi.weichuli_tiaomu(xianzhi=3)
        if weichuli:
            tiao = random.choice(weichuli)
            sousuo_cx = tiao.zhaiyao[:50]
            beijing = f"经验池：{tiao.zhaiyao[:100]}"
        else:
            zhuti = random.choice([
                "AI Agent 自主决策 架构设计 2026",
                "LLM 智能体 自我进化 工程实践",
                "多智能体协作 框架 最新进展",
                "Agent memory management 知识图谱",
            ])
            sousuo_cx = zhuti
            beijing = f"随机探索：{zhuti}"

        sousuo_jg = wangshang_sousuo(sousuo_cx)
        xuexi = self._llm(
            "知识萃取。提炼出对天工造物（LLM自主Agent框架）有参考价值的知识点。≤150字。无则说「无」。",
            f"背景：{beijing}\n搜索：\n{sousuo_jg}\n\n提炼：",
            pai=250,
        )
        if not xuexi or xuexi.startswith("[LLM异常") or "无" in xuexi[:10]:
            return ZiyouYizhiChanchu(dongzuo_ming="xuexi_zhishi", leixing="wuyong", zhaiyao="未学到有价值知识")

        # 分类
        fenlei = self._json(
            "分类器。判断这个知识对天工造物属于系统改进还是知识技能。只输出JSON。",
            f"知识：{xuexi}\n\n"
            '输出JSON：{"leixing": "xitong_gaijin/zhishi_jineng", "liyou": "理由≤15字"}'
        )
        leixing = fenlei.get("leixing", "zhishi_jineng")

        return ZiyouYizhiChanchu(
            dongzuo_ming="xuexi_zhishi", leixing=leixing,
            zhaiyao=f"学「{sousuo_cx[:30]}」→ {leixing}",
            neirong=xuexi,
            laiyuan_xinxi=f"搜索: {sousuo_cx}",
        )

    # ── 动作3：架构审查 ──────────────────────────────────────
    def _jiagou_shencha(self) -> ZiyouYizhiChanchu | None:
        """扫项目结构，LLM发现架构级问题"""
        import subprocess
        try:
            tree = subprocess.run(
                ["find", self.daima_lujing, "-name", "*.py", "-type", "f", "!", "-path", "*__pycache__*", "!", "-path", "*/.git/*"],
                capture_output=True, text=True, timeout=5
            )
            wenjian_liebiao = tree.stdout.strip()[:2000]
        except Exception:
            wenjian_liebiao = "无法获取文件列表"

        fenxi = self._llm(
            "架构审查。看项目结构，发现模块耦合、循环依赖、职责不清、目录混乱等架构问题。无则说「无」。≤150字。",
            f"项目文件：\n{wenjian_liebiao}\n\n架构分析：",
            pai=250,
        )
        if not fenxi or fenxi.startswith("[LLM异常") or "无" in fenxi[:10]:
            return ZiyouYizhiChanchu(dongzuo_ming="jiagou_shencha", leixing="wuyong", zhaiyao="架构审查未发现问题")

        return ZiyouYizhiChanchu(
            dongzuo_ming="jiagou_shencha", leixing="xitong_gaijin",
            zhaiyao=f"架构审查: {fenxi[:80]}",
            neirong=fenxi,
            laiyuan_xinxi="项目结构审查",
        )
