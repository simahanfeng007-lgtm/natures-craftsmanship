"""
L6.82 迭代池。
接收自由意志产物 → LLM独立判定 → 真改进入池 → 不需要则删除。
入池后生成前端投影，等用户确认。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _state_root() -> Path:
    raw = (
        os.environ.get("TIANGONG_JIA")
        or os.environ.get("LINYUANZHE_STATE_DIR")
        or os.environ.get("TIANGONG_STATE_DIR")
        or os.environ.get("TIANGONG_PACKAGE_STATE_DIR")
        or os.environ.get("HERMES_HOME")
        or ""
    )
    return Path(raw).expanduser() if raw else Path.home() / ".tiangong"


DIEDAI_CHI_LUJING = _state_root() / "diedai" / "diedai_chi.jsonl"
QIANDUAN_TOUYING_LUJING = _state_root() / "diedai" / "qianduan_touying.json"


def _zhaichao(text: str, limit: int = 200) -> str:
    return text.strip()[:limit]


@dataclass
class DiedaiTiao:
    """迭代条目"""
    tiao_id: str
    laiyuan: str  # "daima_zhijian" / "xuexi_zhishi"
    neirong: str
    chuangjian_shijian: float = field(default_factory=time.time)
    llm_panjue: str = ""  # LLM判定理由
    zhuangtai: str = "querengzhong"  # querengzhong/rukou/queshan
    yonghu_quereng: bool = False

    def gongkai_zidian(self) -> dict[str, Any]:
        return {
            "tiao_id": self.tiao_id,
            "laiyuan": self.laiyuan,
            "neirong": self.neirong,
            "chuangjian_shijian": self.chuangjian_shijian,
            "llm_panjue": self.llm_panjue,
            "zhuangtai": self.zhuangtai,
            "yonghu_quereng": self.yonghu_quereng,
        }


class DiedaiChi:
    """迭代池：接收→LLM判→入池或删→前端投影"""

    def __init__(self, lujing: Path | None = None):
        self.lujing = Path(lujing or DIEDAI_CHI_LUJING)
        self.lujing.parent.mkdir(parents=True, exist_ok=True)
        self.touying_lujing = self.lujing.parent / "qianduan_touying.json"

    def _juece_json(self, moxing: Any, xitong: str, yonghu: str) -> dict[str, Any]:
        import json as _json
        try:
            resp = moxing.chat.completions.create(
                model=getattr(moxing, "model", "deepseek-v4-pro"),
                messages=[{"role": "system", "content": xitong}, {"role": "user", "content": yonghu}],
                temperature=0.1, max_tokens=300,
            )
            daan = resp.choices[0].message.content.strip()
        except Exception:
            return {}
        kaishi = daan.find("{")
        jieshu = daan.rfind("}") + 1
        if kaishi >= 0 and jieshu > kaishi:
            try:
                return _json.loads(daan[kaishi:jieshu])
            except _json.JSONDecodeError:
                pass
        return {}

    def tousu(self, moxing: Any, laiyuan: str, neirong: str) -> DiedaiTiao | None:
        """
        投递迭代候选 → LLM独立判定。
        返回 DiedaiTiao 如果入池，返回 None 如果被LLM判删。
        """
        import hashlib
        tiao_id = "dd_" + hashlib.sha256(f"{laiyuan}{neirong}{time.time()}".encode()).hexdigest()[:10]

        # LLM独立判定
        panjue = self._juece_json(
            moxing,
            "你是迭代池判官。独立判断这个改进建议对天工造物（LLM自主Agent框架）是否真实需要改代码。只输出JSON。",
            f"来源：{laiyuan}\n内容：{neirong}\n\n"
            '输出JSON：{"xuyao_gaidaima": true/false, "liyou": "理由≤30字", '
            '"fengxian_dengji": "A1/A2/A3/A4", "jianyi_fanwei": "改进范围≤20字"}'
        )

        xuyao = panjue.get("xuyao_gaidaima", False)
        liyou = panjue.get("liyou", "")
        fengxian = panjue.get("fengxian_dengji", "A3")

        if not xuyao:
            # 不需要改代码 → 删除（不入池）
            return None

        # 入池
        tiao = DiedaiTiao(
            tiao_id=tiao_id,
            laiyuan=laiyuan,
            neirong=neirong,
            llm_panjue=f"{liyou} [风险:{fengxian}]",
            zhuangtai="querengzhong",
        )
        self._xieru_tiao(tiao)
        self._gengxin_touying()
        return tiao

    def _xieru_tiao(self, tiao: DiedaiTiao) -> None:
        with open(self.lujing, "a", encoding="utf-8") as f:
            f.write(json.dumps(tiao.gongkai_zidian(), ensure_ascii=False) + "\n")

    def quanbu_tiaomu(self) -> list[DiedaiTiao]:
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
                    tiaomu.append(DiedaiTiao(**{k: v for k, v in d.items() if k in DiedaiTiao.__dataclass_fields__}))
                except (json.JSONDecodeError, TypeError):
                    continue
        return tiaomu

    def weiquereng_tiaomu(self) -> list[DiedaiTiao]:
        return [t for t in self.quanbu_tiaomu() if not t.yonghu_quereng]

    def quereng_tiao(self, tiao_id: str) -> bool:
        """用户确认某条目"""
        quanbu = self.quanbu_tiaomu()
        zhaodao = False
        hang_liebiao = []
        for t in quanbu:
            if t.tiao_id == tiao_id:
                t.yonghu_quereng = True
                t.zhuangtai = "rukou"
                zhaodao = True
            hang_liebiao.append(json.dumps(t.gongkai_zidian(), ensure_ascii=False))

        if zhaodao:
            # 原子写
            import tempfile
            fd, linshi_ming = tempfile.mkstemp(dir=self.lujing.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as linshi:
                    linshi.write("\n".join(hang_liebiao) + ("\n" if hang_liebiao else ""))
                    linshi.flush()
                os.replace(linshi_ming, self.lujing)
            finally:
                if os.path.exists(linshi_ming):
                    os.unlink(linshi_ming)
            self._gengxin_touying()
        return zhaodao

    def shanchu_tiao(self, tiao_id: str) -> bool:
        """删除条目"""
        quanbu = self.quanbu_tiaomu()
        hang_liebiao = [json.dumps(t.gongkai_zidian(), ensure_ascii=False) for t in quanbu if t.tiao_id != tiao_id]
        import tempfile
        fd, linshi_ming = tempfile.mkstemp(dir=self.lujing.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as linshi:
                linshi.write("\n".join(hang_liebiao) + ("\n" if hang_liebiao else ""))
                linshi.flush()
            os.replace(linshi_ming, self.lujing)
        finally:
            if os.path.exists(linshi_ming):
                os.unlink(linshi_ming)
        self._gengxin_touying()
        return True

    def _gengxin_touying(self) -> None:
        """生成前端投影JSON"""
        weiquereng = self.weiquereng_tiaomu()
        touying = {
            "schema": "tiangong.diedai_chi.qianduan_touying.v1",
            "gengxin_shijian": time.time(),
            "quyu_mingcheng": "自我迭代区",
            "tiaomu_shu": len(weiquereng),
            "tiaomu": [],
        }
        for t in weiquereng:
            touying["tiaomu"].append({
                "tiao_id": t.tiao_id,
                "laiyuan": t.laiyuan,
                "neirong_zhaiyao": t.neirong[:120],
                "llm_panjue": t.llm_panjue,
                "zhuangtai": t.zhuangtai,
            })
        self.touying_lujing.parent.mkdir(parents=True, exist_ok=True)
        self.touying_lujing.write_text(json.dumps(touying, ensure_ascii=False, indent=2), encoding="utf-8")
