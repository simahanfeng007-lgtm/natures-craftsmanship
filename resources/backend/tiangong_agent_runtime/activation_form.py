"""L6.72.51 ActivationForm 兼容入口。

真实协议定义在 ``activation_protocol.py``。本模块保留旧导入路径，避免
Runtime / smoke / 历史模块因为迁移过程中的 import 名称不一致而崩溃。

边界：Runtime 只生成 ActivationFormSpec 材料；PromptCompiler 统一整合；
LLM 填 ActivationForm；Runtime 只校验，不用关键词重判用户意图。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tiangong_agent_shell.errors import AgentShellError
from tiangong_agent_shell.prompt_compiler import compile_activation_decision_prompt
from tiangong_agent_shell.safe_logging import redact_text

from .activation_protocol import ActivationForm, activation_schema_card, parse_activation_form


@dataclass(frozen=True)
class ActivationFormSpec:
    user_selected_mode: str = "chat"
    user_message_preview: str = ""
    available_tool_names: tuple[str, ...] = tuple()
    session_context_hint: str = ""

    def public_dict(self) -> dict[str, Any]:
        return {
            "user_selected_mode": self.user_selected_mode,
            "user_message_preview": self.user_message_preview,
            "available_tool_names": list(self.available_tool_names),
            "session_context_hint": self.session_context_hint,
        }

    def prompt_card(self) -> str:
        tool_line = ", ".join(self.available_tool_names[:120]) or "未上报"
        context_hint = self.session_context_hint
        if tool_line:
            context_hint = (context_hint + "\n" if context_hint else "") + f"available_tool_names={tool_line}"
        return activation_schema_card(user_selected_mode=self.user_selected_mode, context_hint=context_hint)


@dataclass(frozen=True)
class ActivationResult:
    ok: bool
    form: ActivationForm | None = None
    message: str = ""
    raw_preview: str = ""

    def public_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "form": self.form.public_dict() if self.form else None,
            "message": self.message,
            "raw_preview": self.raw_preview[:500],
        }


class ActivationFormDecider:
    """通过 PromptCompiler 中转，让 LLM 填写 ActivationForm。"""

    @staticmethod
    def _normal_chinese_marker_aliases(text: str) -> str:
        aliases: list[str] = []
        mapping = (
            ("创建", "write file"),
            ("保存", "save file"),
            ("写入", "write file"),
            ("改写", "write file"),
            ("修改", "edit file"),
            ("删除", "delete file"),
            ("移动", "move file"),
            ("复制", "copy file"),
            ("修复", "fix bug"),
            ("运行", "run command"),
            ("执行", "run command"),
            ("打包", "package zip"),
            ("读取", "read file"),
            ("打开文件", "read file"),
            ("列出", "list directory"),
            ("整理", "edit file"),
            ("检查", "check project"),
            ("测试", "test"),
            ("验证", "verify"),
            ("目录", "directory"),
            ("文件", "file"),
            ("网页下载", "web download"),
            ("下载网页", "web download"),
            ("代码", "code .py"),
            ("项目", "project"),
            ("桌面", "desktop"),
            ("下载", "downloads"),
            ("文档", "document file"),
            ("为什么", "why"),
            ("怎么回事", "explain"),
            ("什么意思", "explain"),
            ("解释", "explain"),
            ("分析", "analysis"),
            ("风险", "risk"),
            ("方案", "analysis"),
            ("建议", "analysis"),
            ("报错", "error"),
            ("错误", "error"),
            ("日志", "log"),
            ("网关", "gateway"),
            ("微信", "wechat"),
            ("飞书", "feishu"),
        )
        for marker, alias in mapping:
            if marker in text:
                aliases.append(alias)
        return " ".join(aliases)

    @staticmethod
    def _needs_work_retry(form: ActivationForm, user_selected_mode: str, user_message: str = "") -> bool:
        # Q22：显式选择“工作”只表示进入主脑填空裁决，不等于强制启用技能。
        # 只有用户话语本身像真实本地任务时，才对 chat/no_tools 裁决做一次纠偏重试；
        # 寒暄、解释、方案讨论等必须尊重 LLM 的 chat/no_tools 判断。
        return (
            str(user_selected_mode or "").strip().lower() in {"work", "auto"}
            and form.intent_type != "execute"
            and not form.activates_runtime_tools
            and ActivationFormDecider._looks_like_local_work(user_message)
        )

    @staticmethod
    def _work_retry_hint(context_hint: str) -> str:
        strict = (
            "STRICT_INTENTFORM_RETRY_Q24: The user explicitly selected work mode, but work mode "
            "is only a safety/preflight hint, not an entry hard gate. First classify the request as "
            "intent_type='chat' | 'consult' | 'execute'. If the request asks to read/list/create/modify "
            "local files, inspect a directory, run a command, test code, package, repair, verify, or complete "
            "a real local task, output intent_type='execute', mode='work', tool_policy='full', "
            "tools_requested=true, fallback_action='execute'. If the user only asks for explanation, risk "
            "analysis, capability status, log meaning, or architecture advice, output intent_type='consult', "
            "mode='chat', tool_policy='readonly', tools_requested=false. Use intent_type='chat' only for "
            "pure small talk. Return only one valid ActivationForm JSON object."
        )
        return (str(context_hint or "").strip() + "\n" + strict).strip()

    @staticmethod
    def _looks_like_local_work(user_message: str) -> bool:
        """只用于显式 work 模式下的纠偏重试，不能把咨询误压成 execute。"""
        text = str(user_message or "").lower()
        text = f"{text} {ActivationFormDecider._normal_chinese_marker_aliases(text)}"
        if not text.strip():
            return False
        hard_markers = (
            "read", "list", "create", "write", "modify", "edit", "run", "test",
            "package", "repair", "fix", "verify", "inspect directory", "open file",
            "download", "save",
            "读取", "列出", "创建", "保存", "写入", "修改", "编辑", "运行", "测试",
            "打包", "修复", "验证", "整理", "移动", "复制", "删除", "下载",
        )
        local_objects = (
            "目录", "文件", "代码", "项目", "桌面", "下载", "路径", "脚本", ".py",
            ".txt", ".md", ".json", ".yml", ".yaml", ".toml", "zip", "pytest",
            "网页", "链接", "url", "http",
            "folder", "file", "directory", "project", "desktop", "downloads", "script",
            "web", "link",
        )
        consult_only_markers = (
            "为什么", "怎么回事", "什么意思", "解释", "分析", "风险", "方案",
            "建议", "是不是", "能不能", "是否", "网关", "微信", "飞书", "报错含义",
        )
        has_hard = any(marker in text for marker in hard_markers)
        has_local_object = any(marker in text for marker in local_objects)
        if has_hard and has_local_object:
            return True
        # “检查/质检”只有绑定本地对象或交付动作时才是 execute；单纯“检查这个说法/方案”
        # 应留在 consult。
        if any(marker in text for marker in ("检查", "质检", "check", "inspect")) and has_local_object:
            return True
        if any(marker in text for marker in consult_only_markers) and not has_local_object:
            return False
        return False

    @staticmethod
    def _fallback_work_form(user_message: str) -> ActivationForm:
        text = str(user_message or "").lower()
        text = f"{text} {ActivationFormDecider._normal_chinese_marker_aliases(text)}"
        if any(marker in text for marker in ("代码", "code", "pytest", "test", "测试")):
            work_type = "code"
            required = ("file_read", "terminal_test")
        elif any(marker in text for marker in ("下载", "download", "网页", "url", "http", "链接")):
            work_type = "web"
            required = ("web_download", "file_write")
        elif any(marker in text for marker in ("run", "运行", "命令", "打包", "package")):
            work_type = "terminal"
            required = ("terminal",)
        elif any(marker in text for marker in ("目录", "list", "列出", "read", "读取")):
            work_type = "file"
            required = ("file_read", "list_dir")
        else:
            work_type = "file"
            required = ("file_read", "file_write")
        return ActivationForm(
            mode="work",
            intent_type="execute",
            tool_policy="full",
            skill_match_status="fuzzy",
            skill_id=f"execute.{work_type}",
            skill_name=f"{work_type} 执行技能",
            fallback_action="execute",
            work_type=work_type,
            execution_depth="single_step",
            tools_requested=True,
            required_tool_classes=required,
            risk_level="A1",
            need_quality_gate=True,
            need_user_confirm=False,
            confirmation_text="我理解为：执行你刚才描述的本地工作任务。开始吗？",
            expected_result="执行用户显式工作模式请求，并返回可审计执行报告。",
            final_output_contract="execution_report",
            reason="deterministic fallback after model returned non-execute/no-tools for explicit work mode",
        )

    def decide(
        self,
        user_message: str,
        *,
        model_config: Any,
        model_client: Any,
        user_selected_mode: str = "work",
        max_steps: int = 80,
        context_hint: str = "",
    ) -> ActivationResult:
        if model_client is None or model_config is None:
            return ActivationResult(False, message="ActivationForm 缺少 model_client/model_config。")
        envelope = compile_activation_decision_prompt(
            user_message,
            config=model_config,
            user_selected_mode=user_selected_mode,
            context_hint=context_hint,
            max_steps=max_steps,
        )
        api_key = str(getattr(model_config, "api_key", "") or "")
        try:
            chat_result = model_client.chat(envelope, model_config)
        except AgentShellError as exc:
            return ActivationResult(
                False,
                message=redact_text(exc.user_message, [api_key]),
                raw_preview=redact_text(exc.detail, [api_key])[:500],
            )
        except UnicodeEncodeError as exc:
            return ActivationResult(
                False,
                message="ActivationForm 调用失败：模型接口请求编码失败；请检查 API Key、Base URL、模型名是否包含中文、全角符号或不可见字符。",
                raw_preview=redact_text(str(exc), [api_key])[:500],
            )
        except Exception as exc:  # noqa: BLE001 - 激活边界不得打崩 Runtime
            return ActivationResult(False, message=f"ActivationForm 调用失败：{type(exc).__name__}。", raw_preview=redact_text(str(exc), [api_key])[:500])
        raw = str(getattr(chat_result, "content", "") or "")
        try:
            form = parse_activation_form(raw)
        except Exception as exc:  # noqa: BLE001
            return ActivationResult(False, message=f"ActivationForm 未通过校验：{type(exc).__name__}: {exc}。", raw_preview=raw[:500])
        if self._needs_work_retry(form, user_selected_mode, user_message):
            retry_envelope = compile_activation_decision_prompt(
                user_message,
                config=model_config,
                user_selected_mode=user_selected_mode,
                context_hint=self._work_retry_hint(context_hint),
                max_steps=max_steps,
            )
            try:
                retry_result = model_client.chat(retry_envelope, model_config)
                retry_raw = str(getattr(retry_result, "content", "") or "")
                retry_form = parse_activation_form(retry_raw)
            except AgentShellError as exc:
                return ActivationResult(
                    False,
                    form=form,
                    message=redact_text(exc.user_message, [api_key]),
                    raw_preview=redact_text(exc.detail, [api_key])[:500],
                )
            except Exception:
                retry_raw = locals().get("retry_raw", "")
            else:
                if retry_form.mode == "work" and retry_form.tools_requested:
                    return ActivationResult(
                        True,
                        form=retry_form,
                        message=(
                            "ActivationForm 已填写："
                            f"intent={retry_form.intent_type} mode={retry_form.mode} work_type={retry_form.work_type} depth={retry_form.execution_depth}。"
                        ),
                        raw_preview=retry_raw[:500],
                    )
            if self._looks_like_local_work(user_message):
                fallback_form = self._fallback_work_form(user_message)
                return ActivationResult(
                    True,
                    form=fallback_form,
                    message=(
                        "ActivationForm 已由显式工作模式兜底激活："
                        f"intent={fallback_form.intent_type} mode={fallback_form.mode} work_type={fallback_form.work_type} "
                        f"depth={fallback_form.execution_depth}。"
                    ),
                    raw_preview=(locals().get("retry_raw", raw) or raw)[:500],
                )
        return ActivationResult(
            True,
            form=form,
            message=f"ActivationForm 已填写：intent={form.intent_type} mode={form.mode} work_type={form.work_type} depth={form.execution_depth}。",
            raw_preview=raw[:500],
        )
