"""CLI 主入口。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from .cli_loop import run_interactive, run_once, write_line
from .composition_root import build_agent_context
from .config_loader import load_model_config
from .errors import AgentShellError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_agent.py",
        description="天工造物 L6.9-L6.37 外壳式智能体启动器 / 临渊者执行链冻结 Runtime",
    )
    parser.add_argument("--once", metavar="TEXT", help="单轮输入并退出。")
    parser.add_argument("--activation-form-json", metavar="TEXT", help=argparse.SUPPRESS)
    parser.add_argument("--config", help="模型配置 JSON 文件路径。")
    parser.add_argument("--provider", help="模型 Provider，例如 openai_compatible。")
    parser.add_argument("--base-url", dest="base_url", help="OpenAI-compatible Base URL。")
    parser.add_argument("--api-key", dest="api_key", help="API Key。建议优先使用环境变量。")
    parser.add_argument("--model", help="模型名。")
    parser.add_argument("--timeout", type=float, help="请求超时时间，单位秒。")
    parser.add_argument("--max-tokens", dest="max_tokens", type=int, help="最大输出 token 数；0 表示按模型自动选择。")
    parser.add_argument("--thinking-enabled", dest="thinking_enabled", help="模型思考模式开关：1/0、enabled/disabled。")
    parser.add_argument("--thinking-depth", dest="thinking_depth", help="模型思考深度，例如 xhigh、max、standard、deep。")
    parser.add_argument(
        "--tool-mode",
        dest="tool_mode",
        choices=["disabled", "dry_run", "runtime_governed"],
        help="工具桥模式；默认 disabled。runtime_governed 将启用 L6.32 Planner 执行主链。",
    )
    parser.add_argument("--workspace", help="L6.10 受治理工具工作区；默认当前目录。")
    parser.add_argument(
        "--planner-mode",
        dest="planner_mode",
        choices=["rule_only", "model_suggest", "model_required"],
        help="L6.14 自然语言计划生成模式；默认 rule_only。",
    )
    parser.add_argument("--max-steps", type=int, default=20, help="单轮最多执行步骤数；默认 20。")
    parser.add_argument("--status", action="store_true", help="显示状态后退出。")
    parser.add_argument("--show-config", action="store_true", help="显示脱敏配置后退出。")
    lifecycle = parser.add_mutually_exclusive_group()
    lifecycle.add_argument("--lifecycle-confirm", metavar="ID", help="确认待确认自主更新后退出。")
    lifecycle.add_argument("--lifecycle-deny", metavar="ID", help="拒绝并移除待确认自主更新后退出。")
    parser.add_argument("--learning-delete", metavar="ID", help="删除尚未学习的经验池条目后退出。")
    return parser


def _read_jsonl(path: Any, *, limit: int = 200) -> list[dict[str, Any]]:
    try:
        from pathlib import Path
        target = Path(path)
        if not target.exists():
            return []
        rows: list[dict[str, Any]] = []
        with target.open("r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
                if len(rows) >= limit:
                    break
        return rows
    except Exception:
        return []


def _learning_pool_snapshot(runtime: Any) -> dict[str, Any]:
    pool = getattr(runtime, "jingyan_chi", None)
    path = str(getattr(pool, "lujing", "") or "")
    rows = _read_jsonl(path)
    latest: list[dict[str, Any]] = []
    counters = {
        "pending_learning": 0,
        "candidate_ready": 0,
        "learned": 0,
        "learned_no_asset": 0,
        "failed": 0,
    }
    for item in rows:
        processed = bool(item.get("yichuli"))
        result = str(item.get("xuexi_jieguo") or "")
        skill = str(item.get("shengcheng_skill") or "")
        tool = str(item.get("shengcheng_tool") or "")
        failed = any(marker in result for marker in ("失败", "异常", "error", "Error", "failed"))
        if failed:
            status = "failed"
            counters["failed"] += 1
        elif not processed:
            status = "pending_learning"
            counters["pending_learning"] += 1
        elif skill or tool:
            status = "learned"
            counters["learned"] += 1
            counters["candidate_ready"] += 1
        else:
            status = "learned_no_asset"
            counters["learned_no_asset"] += 1
        latest.append({
            "id": str(item.get("tiao_id") or ""),
            "source": str(item.get("laiyuan") or ""),
            "summary": str(item.get("zhaiyao") or "")[:500],
            "task_preview": str(item.get("yuanshi_renwu") or "")[:300],
            "learning_result": result[:500],
            "status": status,
            "can_manual_learn": status == "pending_learning",
            "can_delete": status == "pending_learning",
            "has_skill": bool(skill),
            "skill_name": skill[:120],
            "has_tool": bool(tool),
            "tool_name": tool[:120],
            "created_at": item.get("chuangjian_shijian"),
        })
    latest.sort(key=lambda row: (0 if row.get("status") == "pending_learning" else 1, -float(row.get("created_at") or 0)))
    return {
        "schema": "tiangong.desktop.learning_pool_snapshot.v1",
        "status": "pending" if counters["pending_learning"] else ("synced" if rows else "empty"),
        "path": path,
        "total": len(rows),
        **counters,
        "latest": latest[:8],
    }


def _snapshot_or_disabled(runtime: Any, method_name: str, name: str) -> dict[str, Any]:
    method = getattr(runtime, method_name, None)
    if not callable(method):
        return {"schema": f"tiangong.desktop.{name}.v1", "status": "not_configured"}
    try:
        value = method()
    except Exception as exc:
        return {"schema": f"tiangong.desktop.{name}.v1", "status": "failed", "error": exc.__class__.__name__}
    return value if isinstance(value, dict) else {"schema": f"tiangong.desktop.{name}.v1", "status": "unknown"}


def _public_text(value: Any, *, limit: int = 360) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\x00", "").replace("\r", " ").replace("\n", " ").strip()
    lowered = text.lower()
    if any(marker in lowered for marker in ("api_key", "apikey", "authorization", "bearer ", "token", "secret", "password", "credential")):
        return "[redacted-sensitive-summary]"
    return text[:limit]


def _runtime_pending_confirmations(runtime: Any) -> list[dict[str, Any]]:
    method = getattr(runtime, "pending_confirmations", None)
    if not callable(method):
        return []
    try:
        value = method()
    except Exception:
        return []
    return value if isinstance(value, list) else []


def _lifecycle_update_from_diedai(item: Any) -> dict[str, Any]:
    data = item.gongkai_zidian() if callable(getattr(item, "gongkai_zidian", None)) else {}
    tiao_id = _public_text(data.get("tiao_id") or getattr(item, "tiao_id", ""), limit=120)
    reason = _public_text(data.get("llm_panjue") or getattr(item, "llm_panjue", ""), limit=260)
    summary = _public_text(data.get("neirong") or getattr(item, "neirong", ""), limit=500)
    risk_level = ""
    if "[风险:" in reason:
        risk_level = reason.split("[风险:", 1)[-1].split("]", 1)[0].strip()
    return {
        "id": f"diedai:{tiao_id}",
        "source_id": tiao_id,
        "kind": "self_iteration",
        "title": "自我迭代候选",
        "summary": summary,
        "reason": reason,
        "risk_level": risk_level or "A3",
        "source": _public_text(data.get("laiyuan") or getattr(item, "laiyuan", ""), limit=120),
        "status": _public_text(data.get("zhuangtai") or getattr(item, "zhuangtai", ""), limit=80),
        "created_at": data.get("chuangjian_shijian") or getattr(item, "chuangjian_shijian", 0),
        "requires_user_confirmation": True,
        "confirmation_effect": "进入 Planner/P4 自我迭代入口，不直接执行补丁或热切换。",
    }


def _lifecycle_iteration_pool_snapshot() -> dict[str, Any]:
    try:
        from tiangong_agent_runtime.diedai_chi import DiedaiChi

        chi = DiedaiChi()
        items = chi.quanbu_tiaomu()
        pending = [_lifecycle_update_from_diedai(item) for item in items if not bool(getattr(item, "yonghu_quereng", False))]
        confirmed = [_lifecycle_update_from_diedai(item) for item in items if bool(getattr(item, "yonghu_quereng", False))]
        confirmed.sort(key=lambda row: float(row.get("created_at") or 0), reverse=True)
        return {
            "schema": "tiangong.desktop.lifecycle_iteration_pool.v1",
            "status": "pending" if pending else ("confirmed" if confirmed else "empty"),
            "path": str(chi.lujing),
            "projection_path": str(chi.touying_lujing),
            "pending": pending[:20],
            "confirmed_recent": confirmed[:8],
            "pending_count": len(pending),
            "confirmed_count": len(confirmed),
        }
    except Exception as exc:
        return {
            "schema": "tiangong.desktop.lifecycle_iteration_pool.v1",
            "status": "failed",
            "error": exc.__class__.__name__,
            "pending": [],
            "confirmed_recent": [],
            "pending_count": 0,
            "confirmed_count": 0,
        }


def _diedai_id_from_update_id(update_id: str) -> str:
    text = _public_text(update_id, limit=160)
    for prefix in ("diedai:", "iteration:", "lifecycle:"):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def _handle_lifecycle_update(action: str, update_id: str) -> dict[str, Any]:
    try:
        from tiangong_agent_runtime.diedai_chi import DiedaiChi

        chi = DiedaiChi()
        source_id = _diedai_id_from_update_id(update_id)
        exists = any(getattr(item, "tiao_id", "") == source_id for item in chi.quanbu_tiaomu())
        if not source_id or not exists:
            snapshot = _lifecycle_iteration_pool_snapshot()
            return {
                "ok": False,
                "action": action,
                "id": update_id,
                "error": "待确认自主更新不存在或已处理。",
                "lifecycle": snapshot,
            }
        if action == "confirm":
            ok = chi.quereng_tiao(source_id)
            message = "已确认，候选已进入 Planner/P4 自我迭代入口。"
        else:
            ok = chi.shanchu_tiao(source_id)
            message = "已拒绝，候选已从待确认自主更新中移除。"
        snapshot = _lifecycle_iteration_pool_snapshot()
        return {
            "ok": bool(ok),
            "action": action,
            "id": update_id,
            "message": message if ok else "处理失败。",
            "lifecycle": snapshot,
            "pending_confirmations": int(snapshot.get("pending_count") or 0),
        }
    except Exception as exc:
        return {
            "ok": False,
            "action": action,
            "id": update_id,
            "error": f"{exc.__class__.__name__}: {_public_text(exc, limit=220)}",
            "lifecycle": _lifecycle_iteration_pool_snapshot(),
        }


def _handle_learning_delete(tiao_id: str) -> dict[str, Any]:
    try:
        from tiangong_agent_runtime.free_will_learning_chain import JingyanChi

        chi = JingyanChi()
        result = chi.shanchu_weixuexi(tiao_id)
        snapshot = {
            "schema": "tiangong.desktop.learning_pool_delete_result.v1",
            "path": str(chi.lujing),
            **result,
        }
        if result.get("ok"):
            snapshot["message"] = "已删除未学习经验。"
        elif result.get("error") == "already_learned":
            snapshot["message"] = "这条经验已经学习过，不能从未学习池删除。"
        elif result.get("error") == "not_found":
            snapshot["message"] = "未找到这条未学习经验。"
        else:
            snapshot["message"] = "删除失败。"
        return snapshot
    except Exception as exc:
        return {
            "schema": "tiangong.desktop.learning_pool_delete_result.v1",
            "ok": False,
            "id": tiao_id,
            "error": f"{exc.__class__.__name__}: {_public_text(exc, limit=220)}",
        }


def _status_payload(context: Any, args: Any) -> dict[str, Any]:
    cfg = context.config
    rt = context.runtime
    provider_ready = bool(getattr(cfg, "has_real_api_key", False) and getattr(cfg, "base_url", "") and getattr(cfg, "model", ""))
    pool = _learning_pool_snapshot(rt)
    runtime_pending = _runtime_pending_confirmations(rt)
    lifecycle_pool = _lifecycle_iteration_pool_snapshot()
    lifecycle_pending = lifecycle_pool.get("pending", []) if isinstance(lifecycle_pool.get("pending"), list) else []
    return {
        "schema": "tiangong.desktop.status_payload.v1",
        "workspace": str(context.workspace),
        "max_steps": int(getattr(args, "max_steps", 20) or 20),
        "permission_mode": os.getenv("TIANGONG_PERMISSION_MODE", "workspace_write"),
        "provider": getattr(cfg, "provider", ""),
        "model": getattr(cfg, "model", ""),
        "endpoint_state": "configured" if provider_ready else "not_configured",
        "credential_state": "configured" if getattr(cfg, "has_real_api_key", False) else "missing",
        "kernel_importable": bool(getattr(context, "kernel_importable", False)),
        "messages_count": len(getattr(context.session, "messages", []) or []),
        "pending_confirmations": len(runtime_pending) + len(lifecycle_pending),
        "pending_confirmation_items": runtime_pending,
        "lifecycle": {
            "policy": {
                "free_will_frequency": os.getenv("TIANGONG_FREE_WILL_FREQUENCY", "manual"),
                "learning_scope": os.getenv("TIANGONG_LEARNING_SCOPE", "workspace"),
            },
            "runtime": _snapshot_or_disabled(rt, "lifecycle_runtime_snapshot", "lifecycle"),
            "iteration_pool": lifecycle_pool,
            "pending_updates": lifecycle_pending,
            "confirmed_updates": lifecycle_pool.get("confirmed_recent", []) if isinstance(lifecycle_pool.get("confirmed_recent"), list) else [],
        },
        "learning": {
            "status": pool.get("status", "empty"),
            "jingyan_chi": pool,
            "skill_queue": _snapshot_or_disabled(rt, "skill_queue_snapshot", "skill_queue"),
            "tool_requests": _snapshot_or_disabled(rt, "tool_request_snapshot", "tool_requests"),
            "experience": _snapshot_or_disabled(rt, "experience_snapshot", "experience"),
        },
        "runtime": {
            "context": _snapshot_or_disabled(rt, "context_snapshot", "context"),
            "planner_execution": _snapshot_or_disabled(rt, "planner_execution_snapshot", "planner_execution"),
            "interface_wiring": _snapshot_or_disabled(rt, "interface_wiring_snapshot", "interface_wiring"),
        },
    }


def _status_text(context: Any, args: Any) -> str:
    payload = _status_payload(context, args)
    lines = [
        f"workspace={payload['workspace']}",
        f"provider={payload['provider'] or '未配置'}",
        f"model={payload['model'] or '未配置'}",
        f"endpoint_state={payload['endpoint_state']}",
        f"credential_state={payload['credential_state']}",
        f"signal_kind=resource",
        f"health_state=ok",
        f"max_steps={payload['max_steps']}",
        f"permission_mode={payload['permission_mode']}",
        f"kernel_importable={payload['kernel_importable']}",
        "TIANGONG_STATUS_JSON_START",
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        "TIANGONG_STATUS_JSON_END",
    ]
    return "\n".join(lines)


def _changshi_zhiyu(context: Any, yichang: Exception) -> None:
    """尝试自愈诊断并自动修复。仅对系统级异常触发，不影响主流程。"""
    try:
        from tiangong_agent_runtime.zhiyu_xitong.zhiyu_yinqing import (
            shi_xitongji_yichang,
            zhiyu_zhenduan,
        )
        yichang_str = f"{yichang.__class__.__name__}: {yichang}"
        if not shi_xitongji_yichang(yichang_str):
            return
        if context is None:
            write_line("[自愈] 上下文未初始化，跳过诊断", stream=sys.stderr)
            return
        model_client = getattr(context, "model_client", None)
        if model_client is None:
            write_line("[自愈] 无模型客户端，跳过诊断", stream=sys.stderr)
            return
        jieguo = zhiyu_zhenduan(model_client, yichang_str)
        if jieguo.get("xuyao_xiufu"):
            write_line(f"[自愈诊断] {jieguo.get('zhenduan', '?')}", stream=sys.stderr)
            fangan = jieguo.get("fangan", "")
            if fangan:
                write_line(f"[修复方案] {fangan}", stream=sys.stderr)
            buzhou = jieguo.get("buzhou", [])
            for i, b in enumerate(buzhou, 1):
                write_line(f"  {i}. {b}", stream=sys.stderr)
            # ── 自动修复：通过 CodeX 执行修复方案 ──
            if buzhou and context:
                _zhiyu_xiufu(context, fangan, buzhou)
        else:
            write_line(
                f"[自愈] 诊断完成，无需修复：{jieguo.get('zhenduan', '?')}",
                stream=sys.stderr,
            )
    except Exception:
        pass  # 自愈本身绝不能崩


def _zhiyu_xiufu(context: Any, fangan: str, buzhou: list[str]) -> None:
    """通过 CodeX 执行自愈修复方案。"""
    try:
        runtime = getattr(context, "runtime", None)
        if runtime is None:
            write_line("[自愈修复] 运行时未初始化，无法执行修复", stream=sys.stderr)
            return
        codex = getattr(runtime, "codex", None)
        if codex is None:
            write_line("[自愈修复] CodeX 子系统不可用", stream=sys.stderr)
            return
        workspace = getattr(context, "workspace", "")
        if not workspace:
            write_line("[自愈修复] 工作区未设置", stream=sys.stderr)
            return
        # 把诊断方案组装成 CodeX 可执行的任务
        renwu = f"自愈修复任务\n\n诊断：{fangan}\n\n执行步骤：\n"
        for i, b in enumerate(buzhou, 1):
            renwu += f"{i}. {b}\n"
        renwu += "\n请按照步骤执行修复。先读取相关文件了解现状，再修改。改完后跑 python3 -m compileall 验证。"
        write_line("[自愈修复] 正在通过 CodeX 自动修复...", stream=sys.stderr)
        jieguo = codex.run_repair(task=renwu, workspace=workspace, max_turns=8)
        # ── 审计记录：自愈修复结果写入 Runtime 审计链 ──
        try:
            audit = getattr(runtime, "audit", None)
            if audit:
                from tiangong_agent_runtime.audit_bridge import AuditBridge
                shenji = audit  # type: AuditBridge
                shenji.record(
                    event_type="zhiyu_xiufu",
                    detail={
                        "ok": jieguo.ok,
                        "turns": jieguo.turns,
                        "summary": jieguo.summary[:500],
                        "fangan": fangan[:500],
                    },
                )
        except Exception:
            pass
        if jieguo.ok:
            write_line(
                f"[自愈修复] ✅ 修复完成（{jieguo.turns}轮）",
                stream=sys.stderr,
            )
        else:
            write_line(
                f"[自愈修复] ❌ 修复未完全成功：{jieguo.summary}",
                stream=sys.stderr,
            )
    except Exception as e:
        write_line(f"[自愈修复] 执行异常：{e}", stream=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    context = None  # 可能在 build_agent_context 前就崩，提前占位
    if args.lifecycle_confirm is not None:
        write_line(json.dumps(_handle_lifecycle_update("confirm", args.lifecycle_confirm), ensure_ascii=False, indent=2, default=str))
        return 0
    if args.lifecycle_deny is not None:
        write_line(json.dumps(_handle_lifecycle_update("deny", args.lifecycle_deny), ensure_ascii=False, indent=2, default=str))
        return 0
    if args.learning_delete is not None:
        write_line(json.dumps(_handle_learning_delete(args.learning_delete), ensure_ascii=False, indent=2, default=str))
        return 0
    readonly_probe = bool(args.status or args.show_config)
    previous_soul_persist = os.environ.get("TIANGONG_SOUL_BASELINE_PERSIST")
    if readonly_probe:
        # Status/config probes must be read-only: do not bootstrap .linyuanzhe/soul state.
        os.environ["TIANGONG_SOUL_BASELINE_PERSIST"] = "0"
    try:
        config = load_model_config(args)
        context = build_agent_context(config, workspace=args.workspace, max_steps=args.max_steps)
        if args.status:
            write_line(_status_text(context, args))
            return 0
        if args.show_config:
            write_line(json.dumps(context.config.sanitized_dict(), ensure_ascii=False, default=str))
            return 0
        if args.activation_form_json is not None:
            write_line("activation-form-json 已在新链路中移除，请直接用消息。")
            return 0
        if args.once is not None:
            run_once(context, args.once, persist=False)
            return 0
        return run_interactive(context)
    except AgentShellError as exc:
        write_line(f"[错误] {exc.user_message}", stream=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - keep CLI user-facing and non-crashy
        _changshi_zhiyu(context, exc)
        write_line(f"[错误] 启动器遇到未预期错误：{exc.__class__.__name__}。", stream=sys.stderr)
        write_line("请检查配置文件、环境变量，或在桌面端【设置】页保存模型接口。", stream=sys.stderr)
        return 2
    finally:
        if readonly_probe:
            if previous_soul_persist is None:
                os.environ.pop("TIANGONG_SOUL_BASELINE_PERSIST", None)
            else:
                os.environ["TIANGONG_SOUL_BASELINE_PERSIST"] = previous_soul_persist


if __name__ == "__main__":
    raise SystemExit(main())
