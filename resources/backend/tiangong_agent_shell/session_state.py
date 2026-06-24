"""CLI 会话状态，含最小可用持久化。

只使用 Python 标准库，不引入第三方依赖，不新建源码文件。
持久化失败只记警告，不阻断对话。加载失败自动降级新会话。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .config_loader import ModelConfig
from .prompt_builder import build_system_prompt

logger = logging.getLogger(__name__)

_HUIHUA_BANBEN = 1
_HUIHUA_MULU = "sessions"


def _tiangong_jia() -> Path:
    """天工家目录：优先使用桌面包注入的动态本体状态目录。"""
    jia = (
        os.environ.get("TIANGONG_JIA")
        or os.environ.get("LINYUANZHE_STATE_DIR")
        or os.environ.get("TIANGONG_STATE_DIR")
        or os.environ.get("TIANGONG_PACKAGE_STATE_DIR")
        or os.environ.get("HERMES_HOME")
        or ""
    )
    if jia:
        return Path(jia)
    return Path.home() / ".tiangong"


def _huihua_mulu() -> Path:
    """会话持久化目录，不存在则创建。"""
    mulu = _tiangong_jia() / _HUIHUA_MULU
    mulu.mkdir(parents=True, exist_ok=True)
    return mulu


def _yuanzi_xieru(lujing: Path, shuju: dict) -> None:
    """原子写 JSON：先写 .tmp，再 os.replace 替换正式文件。"""
    try:
        lujing.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(lujing.parent),
            prefix=f".{lujing.stem}_",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(shuju, f, indent=2, ensure_ascii=False, default=str)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, lujing)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception:
        logger.warning("会话保存失败: %s", lujing, exc_info=True)


def _guolv_xiaoxi(xiaoxi_liebiao: list[dict]) -> list[dict]:
    """只保留 role 为 user / assistant 的消息，去除内部字段。"""
    yunxu_juese = {"user", "assistant"}
    jieguo = []
    for xiaoxi in xiaoxi_liebiao:
        juese = str(xiaoxi.get("role", "")).strip().lower()
        if juese not in yunxu_juese:
            continue
        neirong = str(xiaoxi.get("content", ""))
        created = xiaoxi.get("created_at") or datetime.now(timezone.utc).isoformat()
        jieguo.append({
            "role": juese,
            "content": neirong,
            "created_at": str(created),
        })
    return jieguo


class SessionState:
    """CLI 会话状态，自动持久化到 ~/.tiangong/sessions/。

    每次 add_user / add_assistant 后自动原子落盘。
    启动时自动加载最近会话，失败则降级新会话。
    """

    def __init__(
        self,
        config: ModelConfig,
        session_id: str = "",
        created_at: str = "",
        messages: list[dict[str, str]] | None = None,
        codex_state: dict | None = None,
    ):
        self.config = config
        self.session_id = session_id or uuid4().hex
        self.created_at = created_at or datetime.now(timezone.utc).isoformat()
        self.messages = messages if messages is not None else []
        self.codex_state = codex_state if isinstance(codex_state, dict) else {}

    def _baocun_lujing(self) -> Path:
        return _huihua_mulu() / f"{self.session_id}.json"

    def _baocun(self) -> None:
        """原子保存当前会话到磁盘。只保存 user/assistant 消息。"""
        try:
            now = datetime.now(timezone.utc).isoformat()
            shuju = {
                "schema_version": _HUIHUA_BANBEN,
                "session_id": self.session_id,
                "created_at": self.created_at,
                "updated_at": now,
                "messages": _guolv_xiaoxi(self.messages),
            }
            if self.codex_state:
                shuju["codex_state"] = self.codex_state
            _yuanzi_xieru(self._baocun_lujing(), shuju)
        except Exception:
            logger.warning("会话保存失败，对话不受影响", exc_info=True)

    @classmethod
    def jiazai_zuixin(cls, config: ModelConfig) -> "SessionState":
        """加载最近修改的可用会话文件。没有或损坏则新建。"""
        try:
            mulu = _huihua_mulu()
            wenjian_liebiao = sorted(
                [f for f in mulu.glob("*.json") if not f.name.endswith(".tmp")],
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
            for lujing in wenjian_liebiao:
                try:
                    with open(lujing, "r", encoding="utf-8") as f:
                        yuanshi = json.load(f)
                except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                    # 损坏文件重命名为 .corrupt
                    try:
                        sunhuai = lujing.with_suffix(".corrupt")
                        # 避免覆盖已有 .corrupt 文件
                        if sunhuai.exists():
                            sunhuai = lujing.with_suffix(f".corrupt.{uuid4().hex[:8]}")
                        os.rename(lujing, sunhuai)
                        logger.warning("会话文件损坏，已重命名: %s → %s", lujing, sunhuai)
                    except OSError:
                        pass
                    continue

                if not isinstance(yuanshi, dict):
                    continue

                xiaoxi = yuanshi.get("messages")
                if not isinstance(xiaoxi, list):
                    continue

                session_id = str(yuanshi.get("session_id", lujing.stem))
                created_at = str(yuanshi.get("created_at", ""))
                return cls(
                    config=config,
                    session_id=session_id,
                    created_at=created_at,
                    messages=cls._shoujiao_xiaoxi(xiaoxi),
                    codex_state=yuanshi.get("codex_state") if isinstance(yuanshi.get("codex_state"), dict) else {},
                )
        except Exception:
            logger.warning("会话加载失败，新建会话", exc_info=True)

        return cls._xinjian(config)

    @staticmethod
    def _shoujiao_xiaoxi(yuanshi_liebiao: list) -> list[dict[str, str]]:
        """收脚原始消息列表，只保留 role/content。"""
        jieguo = []
        for xiaoxi in yuanshi_liebiao:
            if not isinstance(xiaoxi, dict):
                continue
            juese = str(xiaoxi.get("role", "")).strip().lower()
            if juese not in ("user", "assistant"):
                continue
            neirong = str(xiaoxi.get("content", ""))
            if not neirong:
                continue
            jieguo.append({"role": juese, "content": neirong})
        return jieguo

    @classmethod
    def _xinjian(cls, config: ModelConfig) -> "SessionState":
        session = cls(config=config)
        session.messages.append({
            "role": "system",
            "content": build_system_prompt(config),
        })
        return session

    @classmethod
    def create(cls, config: ModelConfig) -> "SessionState":
        """创建会话：优先加载最近会话，失败则新建。"""
        session = cls.jiazai_zuixin(config)
        # 确保有 system prompt
        if not session.messages or session.messages[0].get("role") != "system":
            session.messages.insert(0, {
                "role": "system",
                "content": build_system_prompt(config),
            })
        return session

    def set_system_prompt(self, content: str) -> None:
        """刷新 system prompt，不清空当前会话历史。"""
        clean = str(content or "").replace("\x00", "").strip()
        if not clean:
            return
        dialog = [m for m in self.messages if m.get("role") != "system"]
        self.messages = [{"role": "system", "content": clean}] + dialog

    def add_user(self, content: str) -> None:
        struct = str(content or "")
        if struct:
            self.messages.append({
                "role": "user",
                "content": struct,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            self._baocun()

    def add_assistant(self, content: str) -> None:
        struct = str(content or "")
        if struct:
            self.messages.append({
                "role": "assistant",
                "content": struct,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            self._baocun()

    def set_codex_state(self, state: dict | None) -> None:
        self.codex_state = state if isinstance(state, dict) else {}
        self._baocun()

    def clear_codex_state(self) -> None:
        if self.codex_state:
            self.codex_state = {}
            self._baocun()

    def reset(self) -> None:
        self.messages = [{
            "role": "system",
            "content": build_system_prompt(self.config),
        }]
        self._baocun()

    def recent_dialog_messages(self, *, turns: int = 3) -> list[dict[str, str]]:
        """返回最近 N 轮非 system 对话。"""
        count = max(1, int(turns)) * 2
        return [m for m in self.messages if m.get("role") != "system"][-count:]

    def build_context_hint(self, *, turns: int = 3, max_chars: int = 2400) -> str:
        """压缩最近会话摘要给模型 Planner 使用。"""
        recent = self.recent_dialog_messages(turns=turns)
        if not recent:
            return ""
        lines = ["CLI 最近会话上下文（仅供计划生成续接上文，不含密钥原文）："]
        for index, message in enumerate(recent, start=1):
            role = str(message.get("role") or "unknown")
            content = str(message.get("content") or "").replace("\x00", "")
            if len(content) > 700:
                content = content[:700] + "…"
            lines.append(f"{index}. {role}: {content}")
        return "\n".join(lines)[:max(200, int(max_chars))]

    @property
    def message_count(self) -> int:
        return len(self.messages)
