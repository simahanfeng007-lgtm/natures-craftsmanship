"""LLMDrivenCodeX — LLM 主脑 + 直接工具集（绕过 Runtime）

执行模式（抄 Codex CLI）：
    工具全部直接操作文件系统。LLM 决策，工具执行。
    work_log_read | read_file | write_file | replace_lines | glob | grep | bash | list_dir | python_quality_runner | code_quality_runner | work_log_write
"""
from __future__ import annotations

import json, re, os, sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from .codex_progress_evaluator import (
        CodeXProgressEvaluator,
        extract_plan_payload,
        fallback_step_titles,
        normalize_structured_plan,
    )
except ImportError:  # pragma: no cover - supports direct script-style imports
    from codex_progress_evaluator import (  # type: ignore
        CodeXProgressEvaluator,
        extract_plan_payload,
        fallback_step_titles,
        normalize_structured_plan,
    )

# ─── Sandbox（抄 Codex CLI） ───
SANDBOX_READ_ONLY = "read_only"
SANDBOX_WORKSPACE_WRITE = "workspace_write"
SANDBOX_DANGER_FULL = "danger_full"
DESTRUCTIVE_TOOLS = {"write_file", "replace_lines", "bash", "work_log_write"}
CODEX_WORK_LOG_REL = Path(".linyuanzhe") / "codex_work_log.md"
CODEX_SKIP_DIRS = {".git", ".hg", ".svn", ".linyuanzhe", "__pycache__", "node_modules", ".venv", "venv", "dist", "build"}


LLM_TOOLS = {
    "work_log_read": "读取上一轮 Code-X 工作日志卡。参数: 无。每次执行详细步骤前必须先调用",
    "read_file": "读取文件。参数: path(相对或绝对路径)。返回带行号的完整内容",
    "list_dir": "列出目录。参数: path(默认.)",
    "write_file": "创建/覆写文件。参数: path, content(完整文件内容)。用于创建新文件或完全重写",
    "replace_lines": "按行号修改文件。参数: path, edits(列表，每条{\"line\":行号,\"new_text\":\"新内容\"})。行号从1开始，从下往上应用",
    "glob": "按通配符找文件。参数: pattern(如*.py, src/**/*.rs)。返回匹配的文件路径列表",
    "grep": "搜索文件内容。参数: pattern(正则), path(目录,默认.)。返回 文件:行号:内容 的匹配列表",
    "bash": "执行shell命令。参数: command(命令), timeout(秒,默认30)。在workspace目录执行，返回stdout+stderr+exit_code",
    "python_quality_runner": "Python 语法检查。参数: target(文件或目录,默认.)。内部使用当前解释器执行 compileall，返回错误列表",
    "code_quality_runner": "多语言轻量代码检查。参数: target(文件或目录,默认.), language(auto/python/javascript/json/go/rust/typescript), timeout。自动检查 Python/JS/JSON，并在本机工具链存在时检查 Go/Rust/TypeScript",
    "work_log_write": "写入本次 Code-X 工作日志卡。参数: content(本次目标、变更、验证、遗留问题)。全部步骤done前必须调用",
    "task": "派发子任务给独立子智能体。参数: subtask(子任务描述), max_turns(兼容参数)。子智能体在相同workspace执行，返回结果摘要。用于并行处理多个独立任务",
}

SYSTEM_PROMPT = """你是临渊者 Code-X 执行体。工具集：work_log_read读取上一轮日志卡→read_file读取→glob/grep定位→replace_lines修改→write_file新建→python_quality_runner/code_quality_runner验证→work_log_write写本次日志卡→task派发子任务。

工作方式：系统已完成三层递进规划，你将看到规划结果。按详细步骤执行，每步先向用户汇报再动手。

语气：跟随上下文中的注入提示词风格，动态适配。默认温润从容，带诗书气。

输出格式（每轮）：
【第X/N步】人话汇报，解释这一步做什么、为什么
{"step_id":"S1","substep":"inspect|backup|write|readback|quality|report","tool_name":"xxx","arguments":{...}}

步骤进度追踪（每轮必须执行）：
- 进入任何详细执行步骤前，第一轮必须先调用 work_log_read 读取上一轮工作日志卡
- 每步执行前，从详细步骤中大声读出当前是第几步、这一步的要求是什么
- 每步执行后，汇报：【第X/N步】已完成，结果符合/偏离计划
- 如果工具返回 error，这不是终局；必须先做纠偏：分析错误类别，换参数/换等价工具/补读文件后重试验证
- 如果发现偏离详细步骤，立即停止操作，重新对照结构图和详细步骤纠正
- 全部业务步骤完成后，done 前必须调用 work_log_write 写本次工作日志卡，记录目标、已改文件、验证结果、遗留问题、下一步

铁律：
- 每步必须先读计划再动手，禁止凭记忆执行
- 从详细步骤的第1步开始，严格顺序执行，禁止跳过、合并、调序
- 禁止提前执行后续步骤；只有当前步骤完成并验证通过后，才能进入下一步
- 工具JSON必须绑定结构化计划中的 step_id 和 substep
- 每步先向用户汇报，再执行
- read_file后马上replace_lines，不反复读
- 从下往上排列edits（行号大的在前）
- 一次修完所有bug放在一个replace_lines里
- 重写整个函数/文件时用 write_file
- 找文件用glob，搜内容用grep
- Python 语法检查优先用 python_quality_runner；其他语言或混合项目优先用 code_quality_runner；只有需要运行程序/测试命令时才用 bash
- 独立子任务用 task 并行处理
- 单次工具失败后禁止直接 done、confirm 或宣布任务失败；连续纠偏仍失败时再报告阻塞原因
- 未调用 work_log_write 写入本次日志卡之前，禁止输出 {"done":true,...}
- 全部步骤完成后输出 {"done":true,"summary":"..."}
"""


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "on", "enabled", "enable"}


def _openai_sdk_thinking_kwargs(provider: str, enabled: bool, depth: str) -> dict[str, Any]:
    provider_id = str(provider or "").strip().lower()
    if provider_id in {"openai", "openai_compatible", ""}:
        return {}
    depth_id = str(depth or "").strip().lower()
    extra_body: dict[str, Any] = {}
    kwargs: dict[str, Any] = {}
    if provider_id in {"deepseek", "deepseek_v4"}:
        extra_body["thinking"] = {"type": "enabled" if enabled else "disabled"}
        if enabled:
            extra_body["reasoning_effort"] = "max" if depth_id == "max" else "high"
    elif provider_id in {"qwen", "dashscope"}:
        extra_body["enable_thinking"] = enabled
        budget = _qwen_thinking_budget(depth_id)
        if enabled and budget:
            extra_body["thinking_budget"] = budget
    elif provider_id in {"zhipu", "glm", "mimo"}:
        extra_body["thinking"] = {"type": "enabled" if enabled else "disabled"}
    elif provider_id == "minimax":
        if enabled:
            extra_body["reasoning_split"] = True
    elif provider_id == "openrouter":
        extra_body["reasoning"] = {"effort": _openrouter_effort(depth_id) if enabled else "none"}
    if extra_body:
        kwargs["extra_body"] = extra_body
    return kwargs


def _openrouter_effort(depth: str) -> str:
    return depth if depth in {"minimal", "low", "medium", "high", "xhigh"} else "high"


def _qwen_thinking_budget(depth: str) -> int:
    if depth in {"max", "xhigh"}:
        return 16384
    if depth in {"deep", "high"}:
        return 8192
    if depth in {"standard", "medium"}:
        return 4096
    if depth in {"light", "low"}:
        return 2048
    return 0


def _strip_wrapping_quotes(value: Any) -> str:
    text = str(value or "")
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def _normalize_windows_command_args(command: str) -> list[str]:
    import shlex

    try:
        args = shlex.split(command, posix=True)
    except ValueError:
        args = shlex.split(command, posix=False)
    if os.name == "nt":
        args = [_strip_wrapping_quotes(item) for item in args]
        if args and Path(args[0]).name.lower() in {"python", "python.exe", "py", "py.exe"}:
            args[0] = sys.executable
    return args


def _python_script_run_wrapper(args: list[str], ws: Path) -> tuple[list[str], Path | None]:
    if not args or Path(args[0]).name.lower() not in {"python.exe", "python"}:
        return args, None
    index = 1
    options_with_values = {"-X", "-W"}
    while index < len(args):
        token = str(args[index])
        if token in {"-c", "-m"}:
            return args, None
        if token == "--":
            index += 1
            break
        if token.startswith("-"):
            index += 2 if token in options_with_values and index + 1 < len(args) else 1
            continue
        break
    if index >= len(args):
        return args, None
    script = Path(_strip_wrapping_quotes(args[index]))
    if script.suffix.lower() != ".py":
        return args, None
    script_path = script if script.is_absolute() else (ws / script)
    try:
        script_path = script_path.resolve()
    except OSError:
        return args, None
    if not script_path.exists():
        return args, None
    wrapper = (
        "import pathlib, runpy, sys; "
        "script=pathlib.Path(sys.argv[1]).resolve(); "
        "sys.path.insert(0, str(script.parent)); "
        "sys.argv=[str(script)] + sys.argv[2:]; "
        "runpy.run_path(str(script), run_name='__main__')"
    )
    return [args[0], *args[1:index], "-c", wrapper, str(script_path), *args[index + 1:]], script_path.parent


def _maybe_cd_python_wrapper(command: str, ws: Path) -> tuple[list[str], Path | None] | None:
    match = re.match(r"^\s*cd\s+(.+?)\s*(?:&&|&)\s*(python(?:\.exe)?|py(?:\.exe)?)(.*)$", str(command or ""), re.IGNORECASE)
    if not match:
        return None
    workdir_text = _strip_wrapping_quotes(match.group(1).strip())
    python_tail = f"{match.group(2)}{match.group(3) or ''}".strip()
    workdir = Path(workdir_text)
    if not workdir.is_absolute():
        workdir = ws / workdir
    try:
        workdir = workdir.resolve()
    except OSError:
        return None
    if not workdir.exists() or not workdir.is_dir():
        return None
    args = _normalize_windows_command_args(python_tail)
    wrapped, run_cwd = _python_script_run_wrapper(args, workdir)
    return wrapped, run_cwd or workdir


def _read_text_best_effort(path: Path, limit: int = 6000) -> str:
    try:
        return path.read_text(encoding="utf-8")[:limit]
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:limit]
        except Exception:
            return ""
    except Exception:
        return ""


def _short_context(value: Any, limit: int = 1200) -> str:
    text = str(value or "").replace("\x00", "").strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:limit]


@dataclass
class LLMCodeXResult:
    ok: bool
    turns: int = 0
    steps: list[dict] = field(default_factory=list)
    summary: str = ""
    error: str = ""
    plans: dict[str, Any] = field(default_factory=dict)  # 三层规划输出
    structured_plan: dict[str, Any] = field(default_factory=dict)
    progress_snapshot: dict[str, Any] = field(default_factory=dict)


class LLMDrivenCodeX:
    def __init__(self, api_key: str, base_url: str, model: str,
                 sandbox_mode: str = SANDBOX_WORKSPACE_WRITE, anjing: bool = True,
                 provider: str = "", thinking_enabled: Any = None, thinking_depth: str = ""):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.provider = provider or os.environ.get("TIANGONG_PROVIDER", "")
        self.thinking_enabled = _coerce_bool(thinking_enabled if thinking_enabled is not None else os.environ.get("TIANGONG_THINKING_ENABLED", "0"))
        self.thinking_depth = thinking_depth or os.environ.get("TIANGONG_THINKING_DEPTH", "")
        self.sandbox_mode = sandbox_mode
        self.max_turns = 12
        self.anjing = anjing  # True = 不打印调试日志到 stdout
        self._zi_zhinengti = False  # 子智能体标记，抑制 TASK 输出
        self._work_log_read = False
        self._work_log_written = False

    def _chat_kwargs(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.4,
            "max_tokens": 4000,
        }
        kwargs.update(_openai_sdk_thinking_kwargs(self.provider, self.thinking_enabled, self.thinking_depth))
        return kwargs

    def run(self, task: str, workspace: str | Path, *, max_turns: int = 12,
            buzhou_huidiao: Any = None,
            guihua_huidiao: Any = None,
            jindu_huidiao: Any = None) -> LLMCodeXResult:
        """执行 Code-X 代码系统任务。
        buzhou_huidiao(step_dict) 在每步工具执行后立刻回调。
        guihua_huidiao(layer_name, content, status) 在每层规划完成后回调。
            layer_name: 'macro'/'structure'/'detail'
            content: 规划文本
            status: 'running'(开始)/'done'(完成)
        jindu_huidiao(snapshot) 在数学评估快照更新后回调。
        """
        self.max_turns = max_turns
        ws = Path(workspace).expanduser().resolve()
        tools_desc = "\n".join(f"- {n}: {d}" for n, d in LLM_TOOLS.items())

        import openai
        client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url)

        steps: list[dict] = []

        # ═══ 三层递进规划（三轮独立对话） ═══
        plans = self._san_ceng_guihua(client, task, ws, tools_desc, steps, guihua_huidiao)

        # ═══ 结构化计划 + 动态数学评估器 ═══
        structured_plan = normalize_structured_plan(
            plans.get("structured_plan") if isinstance(plans.get("structured_plan"), dict) else {},
            task=task,
            fallback_steps=fallback_step_titles(str(plans.get("detailed_steps", ""))),
        )
        evaluator = CodeXProgressEvaluator(structured_plan)
        task_steps = [f"{step.step_id}: {step.title}" for step in structured_plan.steps]
        initial_progress = evaluator.snapshot()
        if jindu_huidiao:
            try:
                jindu_huidiao(initial_progress)
            except Exception:
                pass
        if not self._zi_zhinengti:
            for i, buzou in enumerate(task_steps):
                if not self.anjing:
                    print(f"[TASK] {i+1}/{len(task_steps)}:pending:{buzou}")

        # ═══ 执行阶段 ═══
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + f"\n\n工作目录: {ws}\n\n可用工具:\n{tools_desc}"},
            {"role": "user", "content": task},
            {"role": "assistant", "content": plans.get("macro_plan", "")},
            {"role": "assistant", "content": plans.get("structure_plan", "")},
            {"role": "assistant", "content": plans.get("detailed_steps", "")},
            {"role": "assistant", "content": json.dumps({"structured_plan": structured_plan.public_dict()}, ensure_ascii=False)},
            {
                "role": "user",
                "content": (
                    "规划完成。现在按照结构化计划开始执行工具调用。"
                    "每轮输出工具JSON + 中文进度播报(≤25字)，工具JSON必须带 step_id/substep。"
                    "第一轮必须调用 work_log_read。\n\n"
                    + self._execution_context_card(ws, task, plans, structured_plan, initial_progress)
                ),
            },
        ]
        turn = 0
        consecutive_failures = 0
        while True:
            try:
                resp = client.chat.completions.create(**self._chat_kwargs(messages))
                content = resp.choices[0].message.content.strip()
            except Exception as e:
                consecutive_failures += 1
                reason = f"LLM调用失败: {e}"
                steps.append({"turn": turn, "type": "llm_error", "message": reason})
                if consecutive_failures >= 3:
                    return LLMCodeXResult(
                        ok=False,
                        turns=turn + 3,
                        steps=steps,
                        plans=plans,
                        structured_plan=structured_plan.public_dict(),
                        progress_snapshot=evaluator.snapshot(),
                        summary="连续3次失败，中止执行",
                        error=reason,
                    )
                turn += 1
                continue

            if not self.anjing:
                print(f"\n[DEBUG turn {turn}] LLM raw ({len(content)} chars):\n{content[:500]}\n")

            tool, broadcast, done, confirm = self._parse_response(content)

            if done:
                if not self._work_log_written and not self._zi_zhinengti:
                    consecutive_failures += 1
                    messages.append({"role": "assistant", "content": content})
                    messages.append({
                        "role": "user",
                        "content": (
                            "done 被拒绝：全部业务步骤完成后，必须先调用 work_log_write 写本次工作日志卡，"
                            "然后才能输出 done。\n\n"
                            + self._execution_context_card(ws, task, plans, structured_plan, evaluator.snapshot())
                        ),
                    })
                    self._trim_messages(messages)
                    turn += 1
                    continue
                progress_snapshot = evaluator.mark_done(done)
                if jindu_huidiao:
                    try:
                        jindu_huidiao(progress_snapshot)
                    except Exception:
                        pass
                steps.append({"turn": turn, "type": "done", "summary": done})
                return LLMCodeXResult(
                    ok=True,
                    turns=turn + 3,
                    steps=steps,
                    summary=done,
                    plans=plans,
                    structured_plan=structured_plan.public_dict(),
                    progress_snapshot=progress_snapshot,
                )

            if confirm:
                steps.append({"turn": turn, "type": "confirm", "message": confirm})
                return LLMCodeXResult(ok=False, turns=turn + 3, steps=steps, plans=plans,
                                      summary=confirm, error="需要用户确认")

            if not tool:
                consecutive_failures += 1
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": "请输出工具调用 JSON 或 done。"})
                steps.append({"turn": turn, "type": "no_tool", "llm_out": content[:200]})
                if consecutive_failures >= 3:
                    return LLMCodeXResult(
                        ok=False,
                        turns=turn + 3,
                        steps=steps,
                        plans=plans,
                        structured_plan=structured_plan.public_dict(),
                        progress_snapshot=evaluator.snapshot(),
                        summary="连续3次失败，中止执行",
                        error="连续3次未输出有效工具调用",
                    )
                turn += 1
                continue

            tool_name = tool.get("tool_name", "")
            args = tool.get("arguments", {})
            step_id = evaluator.resolve_step_id(tool)
            substep = str(tool.get("substep") or "")

            if not self._work_log_read and not self._zi_zhinengti and tool_name != "work_log_read":
                consecutive_failures += 1
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": (
                        "详细执行前必须先读取上一轮工作日志卡。下一轮只输出 work_log_read 工具 JSON，"
                        "例如 {\"step_id\":\"S1\",\"substep\":\"inspect\",\"tool_name\":\"work_log_read\",\"arguments\":{}}。\n\n"
                        + self._execution_context_card(ws, task, plans, structured_plan, evaluator.snapshot())
                    ),
                })
                self._trim_messages(messages)
                turn += 1
                continue

            # 沙箱裁决
            if not self._sandbox_allow(tool_name, args, ws):
                consecutive_failures += 1
                progress_snapshot = evaluator.update_from_tool(step_id, substep, tool_name, False, "沙箱拒绝")
                step = {"turn": turn, "type": "tool", "tool_name": tool_name,
                        "step_id": step_id, "substep": substep,
                        "ok": False, "broadcast": broadcast,
                        "output": f"[PERM_DENIED] 沙箱={self.sandbox_mode} 拒绝 {tool_name}",
                        "progress_snapshot": progress_snapshot}
                steps.append(step)
                if buzhou_huidiao:
                    try:
                        buzhou_huidiao(step)
                    except Exception:
                        pass
                if jindu_huidiao:
                    try:
                        jindu_huidiao(progress_snapshot)
                    except Exception:
                        pass
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": f"沙箱拒绝: {tool_name}"})
                self._trim_messages(messages)
                if consecutive_failures >= 3:
                    return LLMCodeXResult(
                        ok=False,
                        turns=turn + 3,
                        steps=steps,
                        plans=plans,
                        structured_plan=structured_plan.public_dict(),
                        progress_snapshot=progress_snapshot,
                        summary="连续3次失败，中止执行",
                        error=f"连续3次失败，最后失败为沙箱拒绝: {tool_name}",
                    )
                turn += 1
                continue

            # 执行工具
            try:
                if tool_name == "work_log_read":
                    tool_ok, tool_output = self._handle_work_log_read(ws, args)
                elif tool_name == "replace_lines":
                    tool_ok, tool_output = self._handle_replace_lines(ws, args)
                elif tool_name == "read_file":
                    tool_ok, tool_output = self._handle_read_file(ws, args)
                elif tool_name == "list_dir":
                    tool_ok, tool_output = self._handle_list_dir(ws, args)
                elif tool_name == "write_file":
                    tool_ok, tool_output = self._handle_write_file(ws, args)
                elif tool_name == "glob":
                    tool_ok, tool_output = self._handle_glob(ws, args)
                elif tool_name == "grep":
                    tool_ok, tool_output = self._handle_grep(ws, args)
                elif tool_name == "bash":
                    tool_ok, tool_output = self._handle_bash(ws, args)
                elif tool_name == "python_quality_runner":
                    tool_ok, tool_output = self._handle_python_quality(ws, args)
                elif tool_name == "code_quality_runner":
                    tool_ok, tool_output = self._handle_code_quality(ws, args)
                elif tool_name == "work_log_write":
                    tool_ok, tool_output = self._handle_work_log_write(
                        ws, args, task=task, plans=plans,
                        structured_plan=structured_plan, progress_snapshot=evaluator.snapshot()
                    )
                elif tool_name == "task":
                    tool_ok, tool_output = self._handle_task(ws, args)
                else:
                    tool_ok, tool_output = False, f"未知工具: {tool_name}"
            except Exception as e:
                tool_ok = False
                tool_output = f"执行异常: {type(e).__name__}: {e}"

            if tool_name == "work_log_read" and tool_ok:
                self._work_log_read = True
            if tool_name == "work_log_write" and tool_ok:
                self._work_log_written = True

            step = {
                "turn": turn, "type": "tool", "tool_name": tool_name,
                "step_id": step_id, "substep": substep,
                "args": {k: str(v)[:80] for k, v in args.items()},
                "ok": tool_ok, "broadcast": broadcast, "output": tool_output[:300],
            }
            progress_snapshot = evaluator.update_from_tool(step_id, substep, tool_name, tool_ok, tool_output)
            step["progress_snapshot"] = progress_snapshot
            steps.append(step)
            # 流式回调：立刻通知外部
            if buzhou_huidiao:
                try:
                    buzhou_huidiao(step)
                except Exception:
                    pass
            if jindu_huidiao:
                try:
                    jindu_huidiao(progress_snapshot)
                except Exception:
                    pass
            icon = "✅" if tool_ok else "❌"
            if not self.anjing:
                print(f"  [{turn+1}] {icon} {tool_name} → {broadcast}")
            if not tool_ok:
                consecutive_failures += 1
                if not self.anjing:
                    print(f"       error: {tool_output[:200]}")
            else:
                consecutive_failures = 0

            feedback = tool_output
            if tool_name not in ("read_file", "write_file", "glob", "grep", "list_dir", "bash", "replace_lines"):
                feedback = tool_output[:600]
            
            # 构建步骤进度卡
            jin_du_ka = f"执行结果 {icon}: {feedback}"
            if not tool_ok:
                jin_du_ka += (
                    "\n\n[失败纠偏要求]\n"
                    "这次工具失败仍可恢复，下一轮必须继续输出工具JSON进行补救，不要直接done/confirm/宣布失败。\n"
                    "先判断是参数、路径、命令、环境、交互等待还是代码缺陷；优先换参数或换等价工具验证。"
                    "如果是bash路径/引号/Windows命令问题，改用相对路径、当前工作目录，或改用python_quality_runner/read_file等工具交叉验证。"
                )
            if task_steps and not self._zi_zhinengti:
                jin_du_ka += "\n\n" + self._progress_card(progress_snapshot)
                jin_du_ka += "\n\n" + self._execution_context_card(ws, task, plans, structured_plan, progress_snapshot)
            
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": jin_du_ka})
            self._trim_messages(messages)
            if consecutive_failures >= 3:
                return LLMCodeXResult(
                    ok=False,
                    turns=turn + 3,
                    steps=steps,
                    plans=plans,
                    structured_plan=structured_plan.public_dict(),
                    progress_snapshot=progress_snapshot,
                    summary="连续3次失败，中止执行",
                    error=f"连续3次失败，最后失败工具={tool_name}",
                )
            turn += 1

    def _san_ceng_guihua(self, client, task: str, ws: Path, tools_desc: str,
                         steps: list, guihua_huidiao: Any = None) -> dict[str, Any]:
        """三层递进规划：三次独立 LLM 调用，逐层深入。"""
        plans: dict[str, Any] = {}

        def _call_llm(user_prompt: str) -> str:
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT + f"\n\n工作目录: {ws}\n\n可用工具:\n{tools_desc}"},
                {"role": "user", "content": task},
                {"role": "user", "content": user_prompt},
            ]
            resp = client.chat.completions.create(**self._chat_kwargs(messages))
            return resp.choices[0].message.content.strip()

        # ── 第一层：宏观概念框架（项目定义书） ──
        if guihua_huidiao:
            guihua_huidiao('macro', '', 'running')
        macro = _call_llm(
            "【第一阶段·宏观概念框架】⚠️ 本阶段只输出规划内容，禁止输出任何工具调用JSON。\n\n"
            "你需要填写以下「项目定义书」。每一项都必须回答，不可跳过：\n\n"
            "═══ 项目定义书 ═══\n\n"
            "一、系统定位\n"
            "  1.1 项目名称：\n"
            "  1.2 一句话描述：\n"
            "  1.3 目标用户/外部角色（列出所有外部实体）：\n"
            "  1.4 系统边界（做什么、不做什么，对外输入/输出接口）：\n\n"
            "二、功能全景\n"
            "  2.1 核心功能清单（按优先级排列，每项写：名称 + 一句话描述）：\n"
            "  2.2 非功能约束（逐项回答，无要求写「无」）：\n"
            "      性能：\n"
            "      安全：\n"
            "      可靠性：\n"
            "      可维护性：\n"
            "      兼容性：\n\n"
            "三、技术决策\n"
            "  3.1 编程语言+版本：\n"
            "  3.2 核心依赖库+版本（每个带一句话用途）：\n"
            "  3.3 运行时环境（OS/Python版本等）：\n\n"
            "四、顶层模块划分\n"
            "  4.1 模块清单（每个写：名称 + 职责一句话 + 对外接口函数签名）：\n"
            "  4.2 模块间数据流（用 → 标注数据流向）：\n"
            "  4.3 模块依赖关系（A→B 表示 A 依赖 B）：\n\n"
            "输出格式：纯文本，按以上模板逐项填写输出。不要输出JSON。"
        )
        plans["macro_plan"] = macro
        if guihua_huidiao:
            guihua_huidiao('macro', macro[:2000], 'done')
        steps.append({"stage": "macro_plan", "output": macro[:500]})
        if not self.anjing:
            print(f"\n{'='*60}\n📐 宏观架构\n{'='*60}\n{macro}\n")

        # ── 第二层：结构架构卡 + 逻辑关系图 ──
        if guihua_huidiao:
            guihua_huidiao('structure', '', 'running')
        structure = _call_llm(
            f"【第二阶段·结构架构卡】⚠️ 本阶段只输出规划内容，禁止输出任何工具调用JSON。\n\n"
            f"基于宏观概念框架，完成以下四项。每一项都必须回答：\n\n"
            f"═══ 一、架构模式选择 ═══\n"
            f"从以下6种模式中选择最合适的一种（根据项目类型判断）：\n"
            f"  A. 分层架构 — 表示层→业务层→持久层。适用：Web服务、REST API\n"
            f"  B. MVC — Model→View→Controller。适用：Web应用\n"
            f"  C. 管道-过滤器 — 输入→过滤1→过滤2→输出。适用：数据处理、ETL\n"
            f"  D. 六边形架构 — 领域核心←端口→适配器。适用：长期维护的业务系统\n"
            f"  E. 事件驱动 — 事件→总线→处理器。适用：异步消息系统\n"
            f"  F. 单体脚本 — 函数链→入口。适用：CLI工具、一次性脚本\n"
            f"选定模式：[写模式字母+名称]\n"
            f"选择理由：[一句话]\n\n"
            f"═══ 二、文件清单 ═══\n"
            f"列出每一个需要创建/修改的文件（精确到每个文件路径）：\n"
            f"  xxx.py  [新建/修改]  职责：[一句话]\n"
            f"  yyy.py  [新建/修改]  职责：[一句话]\n\n"
            f"═══ 三、逻辑关系图（四种关系必须全部画出） ═══\n\n"
            f"3.1 调用关系链（从程序入口到最底层，用缩进表示层级）：\n"
            f"    main()\n"
            f"      → 模块A.函数1()\n"
            f"        → 模块B.函数2()\n"
            f"          → 模块C.函数3()\n\n"
            f"3.2 数据流转图（数据从哪来、经过哪些变换、最终落到哪）：\n"
            f"    [输入源] → 函数A 读取/解析 → 函数B 转换/处理 → 函数C 写入/输出 → [输出目标]\n\n"
            f"3.3 类/模块组合关系（谁包含谁、谁继承谁）：\n"
            f"    父类X\n"
            f"      ├── 子类Y（继承X，增加功能Z）\n"
            f"      └── 子类W（继承X，重写方法V）\n"
            f"    模块A 包含 → 类B、类C\n\n"
            f"3.4 时序依赖关系（跨文件的执行顺序，标注前置条件）：\n"
            f"    步骤1: 创建 a.py（无前置依赖）\n"
            f"    步骤2: 创建 b.py（依赖 a.py 中的类X）← 必须先完成步骤1\n"
            f"    步骤3: 修改 c.py（依赖 b.py 中的函数Y）← 必须先完成步骤2\n\n"
            f"═══ 四、关键接口契约 ═══\n"
            f"列出每个跨文件调用的函数/类签名：\n"
            f"  文件.函数名(参数:类型) → 返回值类型\n"
            f"  功能：[一句话]\n"
            f"  被谁调用：[调用方]\n"
            f"  前置条件：[调用前必须满足什么]\n\n"
            f"输出格式：纯文本，按以上模板逐项填写输出。不要输出JSON。\n"
            f"上一阶段输出参考：\n{macro[:1200]}"
        )
        plans["structure_plan"] = structure
        if guihua_huidiao:
            guihua_huidiao('structure', structure[:2000], 'done')
        steps.append({"stage": "structure_plan", "output": structure[:500]})
        if not self.anjing:
            print(f"\n{'='*60}\n📁 组织结构\n{'='*60}\n{structure}\n")

        # ── 第三层：详细步骤（6子步骤模板） ──
        if guihua_huidiao:
            guihua_huidiao('detail', '', 'running')
        detail = _call_llm(
            f"【第三阶段·详细步骤】⚠️ 本阶段只输出规划内容，禁止输出任何工具调用JSON。\n\n"
            f"基于结构架构卡，将每个文件操作展开为严格步骤。每个步骤必须包含完整的6子步骤模板：\n\n"
            f"═══ 步骤模板（每个步骤都是6子步骤，不可跳过任何一个） ═══\n\n"
            f"步骤N: [用一句话描述这一步要做什么]\n\n"
            f"  ① 写入前检查：\n"
            f"     - 目标文件：[路径]\n"
            f"     - 文件状态：[已存在需修改 / 不存在需新建]\n"
            f"     - 前置依赖检查：[依赖的文件是否已就绪？是/否]\n\n"
            f"  ② 备份（回滚锚点）：\n"
            f"     - 如是修改已有文件：cp [原文件] → [原文件].bak\n"
            f"     - 如是新建文件：无需备份，记录路径即可\n"
            f"     - 备份命令：[填写具体shell命令]\n\n"
            f"  ③ 执行写入：\n"
            f"     - 工具：[write_file / replace_lines]\n"
            f"     - 目标位置：[文件路径，如果用replace_lines须写出行号范围]\n"
            f"     - 写入内容摘要：[简述要写入什么]\n\n"
            f"  ④ 读回验证（查看）：\n"
            f"     - 工具：read_file\n"
            f"     - 读回文件：[路径]\n"
            f"     - 验证点：[行数 / 关键内容 / 文件大小]\n"
            f"     - 预期结果：[写什么就验证什么，必须一致]\n\n"
            f"  ⑤ 语法检查（校对）：\n"
            f"     - 工具：python_quality_runner\n"
            f"     - 检查目标：[文件或目录]\n"
            f"     - 预期结果：无语法错误\n"
            f"     - 失败处理：回到③修正，修正不了则回滚②的备份文件\n\n"
            f"  ⑥ 汇报（返回）：\n"
            f"     - 向用户报告：[第N步完成/失败]\n"
            f"     - 状态标识：✅成功 / ❌失败+原因\n\n"
            f"═══ 输出要求 ═══\n"
            f"先写出完整步骤清单（步骤1/步骤2/...），再对每个步骤展开6子步骤。\n"
            f"步骤间有严格时序依赖的必须标注「← 必须先完成步骤X」。\n"
            f"最后必须输出严格 JSON，字段如下；不要放在 Markdown 代码块里：\n"
            f"{{\"structured_plan\":{{\"schema\":\"tiangong.codex.structured_plan.v1\",\"steps\":["
            f"{{\"step_id\":\"S1\",\"title\":\"步骤标题\",\"target_files\":[\"相对路径\"],"
            f"\"depends_on\":[],\"actions\":[\"inspect\",\"backup\",\"write\",\"readback\",\"quality\",\"report\"],"
            f"\"verify_points\":[\"验证点\"],\"rollback_ref\":\"rollback:S1\",\"risk_level\":\"A2\",\"weight\":1.0}}"
            f"]}}}}\n\n"
            f"宏观概念框架：\n{macro[:600]}\n\n"
            f"结构架构卡：\n{structure[:600]}"
        )
        plans["detailed_steps"] = detail
        if guihua_huidiao:
            guihua_huidiao('detail', detail[:2000], 'done')
        steps.append({"stage": "detailed_steps", "output": detail[:500]})

        structured_payload = extract_plan_payload(detail)
        fallback_titles = fallback_step_titles(detail)
        if guihua_huidiao:
            guihua_huidiao('structured', '', 'running')
        if not structured_payload:
            repair = _call_llm(
                "把下面的详细步骤重排为严格 JSON。只输出 JSON，不要输出工具调用。\n"
                "JSON 结构必须是："
                "{\"structured_plan\":{\"schema\":\"tiangong.codex.structured_plan.v1\",\"steps\":["
                "{\"step_id\":\"S1\",\"title\":\"步骤标题\",\"target_files\":[],\"depends_on\":[],"
                "\"actions\":[\"inspect\",\"backup\",\"write\",\"readback\",\"quality\",\"report\"],"
                "\"verify_points\":[],\"rollback_ref\":\"rollback:S1\",\"risk_level\":\"A2\",\"weight\":1.0}"
                "]}}\n\n"
                f"详细步骤原文：\n{detail[:3500]}"
            )
            plans["structured_repair"] = repair
            structured_payload = extract_plan_payload(repair)
        structured_plan = normalize_structured_plan(
            structured_payload or {},
            task=task,
            fallback_steps=fallback_titles,
        )
        plans["structured_plan"] = structured_plan.public_dict()
        if guihua_huidiao:
            guihua_huidiao('structured', plans["structured_plan"], 'done')
        steps.append({
            "stage": "structured_plan",
            "output": json.dumps(plans["structured_plan"], ensure_ascii=False)[:500],
        })
        if not self.anjing:
            print(f"\n{'='*60}\n📝 详细步骤\n{'='*60}\n{detail}\n")

        return plans

    def _work_log_path(self, ws: Path) -> Path:
        return ws / CODEX_WORK_LOG_REL

    def _execution_context_card(
        self,
        ws: Path,
        task: str,
        plans: dict[str, Any],
        structured_plan: Any,
        progress_snapshot: dict[str, Any] | None,
    ) -> str:
        snapshot = progress_snapshot if isinstance(progress_snapshot, dict) else {}
        step_items = snapshot.get("steps") if isinstance(snapshot.get("steps"), list) else []
        active = str(snapshot.get("active_step_id") or "-")
        completed = []
        current = []
        unfinished = []
        failed = []
        for item in step_items:
            if not isinstance(item, dict):
                continue
            label = f"{item.get('step_id') or '?'}:{_short_context(item.get('title'), 80)}"
            status = str(item.get("status") or "pending")
            if status == "done":
                completed.append(label)
            elif item.get("step_id") == active:
                current.append(f"{label} status={status}")
            else:
                unfinished.append(f"{label} status={status}")
            if status == "failed" or int(item.get("failures") or 0) > 0:
                failed.append(f"{label} failures={item.get('failures') or 0} summary={_short_context(item.get('summary'), 120)}")

        plan_public = structured_plan.public_dict() if hasattr(structured_plan, "public_dict") else structured_plan
        framework = {
            "task": _short_context(task, 260),
            "macro": _short_context(plans.get("macro_plan"), 460),
            "structure": _short_context(plans.get("structure_plan"), 460),
            "structured": _short_context(json.dumps(plan_public, ensure_ascii=False), 700),
        }
        previous_log = _read_text_best_effort(self._work_log_path(ws), 2200) or "无上一轮工作日志卡。"
        path_lines = [
            f"active_step_id={active}",
            "current=" + (" | ".join(current[:3]) if current else "未开始"),
            "completed_path=" + (" -> ".join(completed[:10]) if completed else "无"),
            "next_unfinished=" + (" | ".join(unfinished[:5]) if unfinished else "无"),
        ]
        hard_lines = [
            f"workspace={ws}",
            "must_first_tool=work_log_read" if not self._work_log_read else "work_log_read=done",
            "must_write_log_before_done=" + ("done" if self._work_log_written else "pending"),
            "single_tool_failure_is_recoverable=retry_with_params_or_equivalent_tool",
        ]
        if failed:
            hard_lines.append("failure_evidence=" + " | ".join(failed[:4]))
        return (
            "[Code-X Execution Context Card]\n"
            "## 大框架\n"
            + json.dumps(framework, ensure_ascii=False)
            + "\n\n## 本次步骤路径及已完成步骤路径\n"
            + "\n".join(path_lines)
            + "\n\n## 上次详细落实内容\n"
            + _short_context(previous_log, 2200)
            + "\n\n## 当前失败证据/硬约束\n"
            + "\n".join(hard_lines)
        )

    def _handle_work_log_read(self, ws: Path, args: dict) -> tuple[bool, str]:
        path = self._work_log_path(ws)
        if not path.exists():
            return True, f"上一轮工作日志卡不存在: {CODEX_WORK_LOG_REL}。这是全新或未记录过的任务，请继续执行当前结构化计划。"
        content = _read_text_best_effort(path, 6000)
        if not content:
            return True, f"上一轮工作日志卡存在但无法读取或为空: {CODEX_WORK_LOG_REL}。请继续执行当前结构化计划。"
        return True, f"[上一轮 Code-X 工作日志卡]\npath={CODEX_WORK_LOG_REL}\n{content}"

    def _handle_work_log_write(
        self,
        ws: Path,
        args: dict,
        *,
        task: str,
        plans: dict[str, Any],
        structured_plan: Any,
        progress_snapshot: dict[str, Any] | None,
    ) -> tuple[bool, str]:
        content = str(args.get("content") or args.get("summary") or args.get("log") or "").strip()
        if not content:
            content = "模型未提供 content，运行器根据当前快照生成兜底日志。"
        path = self._work_log_path(ws)
        history_path = path.with_name("codex_work_log_history.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        from datetime import datetime

        snapshot = progress_snapshot if isinstance(progress_snapshot, dict) else {}
        plan_public = structured_plan.public_dict() if hasattr(structured_plan, "public_dict") else structured_plan
        steps_summary = []
        for item in (snapshot.get("steps") or [])[:20]:
            if isinstance(item, dict):
                steps_summary.append(
                    f"- {item.get('step_id')}: {item.get('status')} score={item.get('score')} title={item.get('title')}"
                )
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        log_text = "\n".join([
            "# Code-X Work Log",
            "",
            f"- time: {now}",
            f"- workspace: {ws}",
            f"- task: {_short_context(task, 500)}",
            "",
            "## 本次详细落实内容",
            content,
            "",
            "## 当前进度快照",
            "\n".join(steps_summary) if steps_summary else "无结构化步骤快照",
            "",
            "## 结构化计划摘要",
            _short_context(json.dumps(plan_public, ensure_ascii=False), 1800),
            "",
            "## 大框架摘要",
            _short_context(plans.get("macro_plan"), 700),
            "",
        ])
        try:
            path.write_text(log_text, encoding="utf-8")
            with history_path.open("a", encoding="utf-8") as fh:
                fh.write("\n\n---\n\n")
                fh.write(log_text)
            return True, f"写入工作日志卡: {CODEX_WORK_LOG_REL}，并追加历史: {history_path.name}"
        except Exception as e:
            return False, f"写入工作日志卡失败: {type(e).__name__}: {e}"

    def _collect_code_files(self, target_path: Path, suffixes: set[str], limit: int = 120) -> list[Path]:
        if target_path.is_file():
            return [target_path] if target_path.suffix.lower() in suffixes else []
        files: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(target_path):
            dirnames[:] = [
                d for d in dirnames
                if d not in CODEX_SKIP_DIRS and not d.startswith(".")
            ]
            for name in filenames:
                candidate = Path(dirpath) / name
                if candidate.suffix.lower() in suffixes:
                    files.append(candidate)
                    if len(files) >= limit:
                        return files
        return files

    def _handle_code_quality(self, ws: Path, args: dict) -> tuple[bool, str]:
        target = args.get("target", ".")
        language = str(args.get("language") or "auto").strip().lower()
        try:
            timeout_sec = int(args.get("timeout") or 60)
        except Exception:
            timeout_sec = 60
        timeout_sec = max(5, min(120, timeout_sec))
        target_path = Path(str(target or "."))
        if not target_path.is_absolute():
            target_path = ws / target_path
        try:
            target_path = target_path.resolve()
        except OSError:
            return False, f"[BAD_PATH] 无法解析检查目标: {target}"
        if not target_path.exists():
            return False, f"[FILE_NOT_FOUND] 检查目标不存在: {target}"

        import shutil
        import subprocess

        languages = {language}
        if language in {"", "auto", "mixed", "all"}:
            languages = {"python", "javascript", "json", "typescript", "go", "rust"}

        checked: list[str] = []
        skipped: list[str] = []
        errors: list[str] = []

        def _rel(path: Path) -> str:
            try:
                return str(path.relative_to(ws))
            except ValueError:
                return str(path)

        def _run(cmd: list[str], cwd: Path) -> tuple[bool, str]:
            try:
                result = subprocess.run(
                    cmd, cwd=str(cwd), capture_output=True, text=True,
                    timeout=timeout_sec,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
                )
            except subprocess.TimeoutExpired:
                return False, f"[TIMEOUT] {' '.join(cmd[:3])} > {timeout_sec}s"
            except Exception as e:
                return False, f"[RUN_ERROR] {' '.join(cmd[:3])}: {e}"
            output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part and part.strip())
            return result.returncode == 0, output[:1200]

        if "python" in languages:
            py_files = self._collect_code_files(target_path, {".py"}, 100)
            if py_files:
                for file in py_files:
                    ok, output = _run([sys.executable, "-B", "-m", "py_compile", str(file)], ws)
                    checked.append(f"python:{_rel(file)}")
                    if not ok:
                        errors.append(f"python:{_rel(file)}\n{output}")
                        if len(errors) >= 8:
                            break
            else:
                skipped.append("python: no .py files")

        if "javascript" in languages:
            node = shutil.which("node")
            js_files = self._collect_code_files(target_path, {".js", ".mjs", ".cjs"}, 80)
            if not node:
                skipped.append("javascript: node not found")
            elif js_files:
                for file in js_files:
                    ok, output = _run([node, "--check", str(file)], ws)
                    checked.append(f"javascript:{_rel(file)}")
                    if not ok:
                        errors.append(f"javascript:{_rel(file)}\n{output}")
                        if len(errors) >= 8:
                            break
            else:
                skipped.append("javascript: no .js/.mjs/.cjs files")

        if "json" in languages:
            json_files = self._collect_code_files(target_path, {".json"}, 80)
            if json_files:
                for file in json_files:
                    checked.append(f"json:{_rel(file)}")
                    try:
                        json.loads(file.read_text(encoding="utf-8"))
                    except Exception as e:
                        errors.append(f"json:{_rel(file)}\n{type(e).__name__}: {e}")
                        if len(errors) >= 8:
                            break
            else:
                skipped.append("json: no .json files")

        project_dir = target_path if target_path.is_dir() else target_path.parent
        if "typescript" in languages:
            tsc = shutil.which("tsc")
            tsconfig = project_dir / "tsconfig.json"
            if not tsconfig.exists():
                tsconfig = ws / "tsconfig.json"
            if not tsc:
                skipped.append("typescript: tsc not found")
            elif not tsconfig.exists():
                skipped.append("typescript: tsconfig.json not found")
            else:
                ok, output = _run([tsc, "--noEmit", "--pretty", "false", "-p", str(tsconfig.parent)], tsconfig.parent)
                checked.append(f"typescript:{_rel(tsconfig)}")
                if not ok:
                    errors.append(f"typescript:{_rel(tsconfig)}\n{output}")

        if "go" in languages:
            go = shutil.which("go")
            go_mod = project_dir / "go.mod"
            if not go_mod.exists():
                go_mod = ws / "go.mod"
            if not go:
                skipped.append("go: go not found")
            elif not go_mod.exists():
                skipped.append("go: go.mod not found")
            else:
                ok, output = _run([go, "test", "./..."], go_mod.parent)
                checked.append(f"go:{_rel(go_mod.parent)}")
                if not ok:
                    errors.append(f"go:{_rel(go_mod.parent)}\n{output}")

        if "rust" in languages:
            cargo = shutil.which("cargo")
            cargo_toml = project_dir / "Cargo.toml"
            if not cargo_toml.exists():
                cargo_toml = ws / "Cargo.toml"
            if not cargo:
                skipped.append("rust: cargo not found")
            elif not cargo_toml.exists():
                skipped.append("rust: Cargo.toml not found")
            else:
                ok, output = _run([cargo, "check"], cargo_toml.parent)
                checked.append(f"rust:{_rel(cargo_toml.parent)}")
                if not ok:
                    errors.append(f"rust:{_rel(cargo_toml.parent)}\n{output}")

        if errors:
            return False, (
                f"code_quality_runner failed. checked={len(checked)} skipped={len(skipped)}\n"
                + "\n\n".join(errors[:8])
                + ("\n\nskipped:\n" + "\n".join(skipped[:12]) if skipped else "")
            )[:4000]
        return True, (
            f"code_quality_runner passed. checked={len(checked)} skipped={len(skipped)}\n"
            f"checked:\n" + "\n".join(checked[:40])
            + ("\n\nskipped:\n" + "\n".join(skipped[:12]) if skipped else "")
        )[:4000]

    @staticmethod
    def _progress_card(snapshot: dict[str, Any]) -> str:
        if not isinstance(snapshot, dict):
            return "[Code-X结构化进度] 暂无快照"
        pct = int(float(snapshot.get("total_progress") or 0) * 100)
        confidence = int(float(snapshot.get("confidence") or 0) * 100)
        risk = int(float(snapshot.get("risk_score") or 0) * 100)
        health = int(float(snapshot.get("health_score") or 0) * 100)
        lines = [
            f"[Code-X结构化进度] 进度={pct}% 置信={confidence}% 风险={risk}% 健康={health}% active={snapshot.get('active_step_id') or '-'}"
        ]
        for item in (snapshot.get("steps") or [])[:12]:
            score = int(float(item.get("score") or 0) * 100)
            lines.append(f"- {item.get('step_id')}: {item.get('status')} score={score}% {item.get('title')}")
        if len(snapshot.get("steps") or []) > 12:
            lines.append(f"- ... 其余 {len(snapshot.get('steps') or []) - 12} 步继续按结构化计划评估")
        return "\n".join(lines)

    def _trim_messages(self, messages: list[dict]):
        """估算总 token 数，超过阈值时保留 system + 三层规划 + 最近执行轮次。
        
        消息结构：system(0) + user任务(1) + 宏观规划(2) + 结构规划(3) + 详细步骤(4) + 
        开始指令(5) + 执行历史(6..)。裁剪只动执行历史，不动结构图。
        """
        total_chars = sum(len(m.get("content", "")) for m in messages)
        estimated_tokens = total_chars // 3
        if estimated_tokens <= 8000:
            return
        # 固定保留前 7 条（结构化计划不能丢）+ 最近 8 条执行历史
        GUTOU_LENGTH = 7  # system + task + 3规划 + structured_plan + 开始指令
        if len(messages) > GUTOU_LENGTH + 8:
            messages[GUTOU_LENGTH:-8] = [{"role": "user", "content": "…[中间执行历史已裁剪]…"}]

    def _sandbox_allow(self, tool_name: str, args: dict, ws: Path) -> bool:
        """沙箱裁决：read_only 拒绝写操作，workspace_write 防路径越界。"""
        if self.sandbox_mode == SANDBOX_DANGER_FULL:
            return True
        if self.sandbox_mode == SANDBOX_READ_ONLY and tool_name in DESTRUCTIVE_TOOLS:
            return False
        if self.sandbox_mode == SANDBOX_WORKSPACE_WRITE and tool_name in DESTRUCTIVE_TOOLS:
            path_arg = args.get("path", "")
            if path_arg:
                try:
                    (ws / path_arg).resolve().relative_to(ws.resolve())
                except ValueError:
                    return False
        return True

    def _handle_read_file(self, ws: Path, args: dict) -> tuple[bool, str]:
        """直接读取文件，返回带行号的完整内容。不走 Runtime，跟妾身工具一样。"""
        path = args.get("path", "")
        if not path:
            return False, "[BAD_ARGS] 缺少 path 参数"
        target = ws / path
        if not target.exists():
            return False, f"[FILE_NOT_FOUND] 文件不存在: {path}"
        try:
            content = target.read_text(encoding="utf-8")
            lines = content.splitlines()
            numbered = "\n".join(f"{i+1}|{l}" for i, l in enumerate(lines))
            return True, numbered[:4000]
        except Exception as e:
            return False, f"读取失败: {e}"

    def _handle_list_dir(self, ws: Path, args: dict) -> tuple[bool, str]:
        """列出目录内容。"""
        path = args.get("path", ".")
        target = ws / path
        if not target.exists():
            return False, f"[FILE_NOT_FOUND] 目录不存在: {path}"
        try:
            items = sorted(target.iterdir())
            return True, "\n".join(
                f"{'[D]' if i.is_dir() else '[F]'} {i.name}" for i in items[:50]
            )
        except Exception as e:
            return False, f"列目录失败: {e}"

    def _handle_write_file(self, ws: Path, args: dict) -> tuple[bool, str]:
        """创建/覆写文件。"""
        path = args.get("path", "")
        content = args.get("content", "")
        if not path or not content:
            return False, "[BAD_ARGS] 缺少 path 或 content 参数"
        target = ws / path
        target.parent.mkdir(parents=True, exist_ok=True)
        # 覆写前备份
        if target.exists():
            import shutil
            shutil.copy2(target, target.with_suffix(target.suffix + '.bak'))
        try:
            target.write_text(content, encoding="utf-8")
            return True, f"写入 {path}：{len(content.splitlines())}行"
        except Exception as e:
            return False, f"写入失败: {e}"

    def _handle_glob(self, ws: Path, args: dict) -> tuple[bool, str]:
        """按通配符找文件。"""
        pattern = args.get("pattern", "*")
        target_dir = args.get("path", ".")
        root = ws / target_dir
        if not root.exists():
            return False, f"[FILE_NOT_FOUND] 目录不存在: {target_dir}"
        try:
            matches = sorted(root.glob(pattern))
            # 转相对路径
            result = []
            for m in matches[:100]:
                try:
                    rel = str(m.relative_to(ws))
                except ValueError:
                    rel = str(m)
                result.append(f"{'[D]' if m.is_dir() else '[F]'} {rel}")
            return True, "\n".join(result) if result else f"无匹配: {pattern}"
        except Exception as e:
            return False, f"glob失败: {e}"

    def _handle_grep(self, ws: Path, args: dict) -> tuple[bool, str]:
        """搜索文件内容。"""
        pattern = args.get("pattern", "")
        search_path = args.get("path", ".")
        if not pattern:
            return False, "[BAD_ARGS] 缺少 pattern 参数"
        root = ws / search_path
        if not root.exists():
            return False, f"[FILE_NOT_FOUND] 路径不存在: {search_path}"
        try:
            import re as _re
            regex = _re.compile(pattern)
            results = []
            files = root.rglob("*") if root.is_dir() else [root]
            for f in files:
                if f.is_file() and f.suffix in {".py", ".rs", ".js", ".ts", ".html", ".css", ".json", ".yaml", ".yml", ".toml", ".md", ".txt"}:
                    try:
                        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                            if regex.search(line):
                                try:
                                    rel = str(f.relative_to(ws))
                                except ValueError:
                                    rel = str(f)
                                results.append(f"{rel}:{i}: {line.strip()[:120]}")
                                if len(results) >= 80:
                                    break
                    except Exception:
                        pass
                    if len(results) >= 80:
                        break
            return True, "\n".join(results) if results else f"无匹配: {pattern}"
        except _re.error as e:
            return False, f"[REGEX_ERR] 正则错误: {e}"
        except Exception as e:
            return False, f"grep失败: {e}"

    def _handle_bash(self, ws: Path, args: dict) -> tuple[bool, str]:
        """执行shell命令，在workspace目录运行，有超时和输出限制。"""
        command = args.get("command", "")
        timeout_sec = args.get("timeout", 30)
        if not command:
            return False, "[BAD_ARGS] 缺少 command 参数"
        if not isinstance(timeout_sec, (int, float)) or timeout_sec < 1:
            timeout_sec = 30
        if timeout_sec > 120:
            timeout_sec = 120  # 硬上限

        import subprocess
        try:
            env = os.environ.copy()
            python_dir = str(Path(sys.executable).parent)
            env["PATH"] = python_dir + os.pathsep + env.get("PATH", "")
            env.setdefault("PYTHONIOENCODING", "utf-8")
            env.setdefault("PYTHONUTF8", "1")
            shell_tokens = ("|", "&&", "||", ">", "<", " 2>", " 1>", " & ")
            cd_python = _maybe_cd_python_wrapper(command, ws) if os.name == "nt" else None
            use_shell = os.name == "nt" and cd_python is None and any(token in command for token in shell_tokens)
            if not use_shell:
                if cd_python is not None:
                    args, run_cwd = cd_python
                else:
                    args = _normalize_windows_command_args(command)
                    args, run_cwd = _python_script_run_wrapper(args, ws)
                result = subprocess.run(
                    args, shell=False, cwd=str(run_cwd or ws),
                    capture_output=True, text=True,
                    timeout=timeout_sec,
                    env=env,
                )
            else:
                result = subprocess.run(
                    command, shell=True, cwd=str(ws),
                    capture_output=True, text=True,
                    timeout=timeout_sec,
                    env=env,
                )
            stdout = result.stdout[:3000]
            stderr = result.stderr[:1000]
            exit_code = result.returncode
            ok = exit_code == 0
            summary = f"exit={exit_code}"
            if stdout:
                summary += f"\nstdout:\n{stdout}"
            if stderr:
                summary += f"\nstderr:\n{stderr}"
            return ok, summary[:4000]
        except subprocess.TimeoutExpired:
            return False, f"[TIMEOUT] 命令超时（>{timeout_sec}秒）"
        except Exception as e:
            return False, f"执行失败: {e}"

    def _handle_python_quality(self, ws: Path, args: dict) -> tuple[bool, str]:
        """直接运行当前解释器的 compileall 做语法检查。"""
        target = args.get("target", ".")
        import subprocess
        try:
            target_path = Path(str(target or "."))
            if not target_path.is_absolute():
                target_path = ws / target_path
            if not target_path.exists():
                return False, f"[FILE_NOT_FOUND] 检查目标不存在: {target}"
            result = subprocess.run(
                [sys.executable, "-B", "-m", "compileall", "-q", str(target_path)],
                cwd=str(ws), capture_output=True, text=True, timeout=30,
            )
            stdout = result.stdout.strip()
            stderr = result.stderr.strip()
            if result.returncode == 0:
                return True, "语法检查通过，无错误"
            # 提取错误行
            combined = "\n".join(part for part in (stdout, stderr) if part)
            errors = [l for l in combined.split('\n') if 'SyntaxError' in l or 'Error' in l or '***' in l][:12]
            if errors:
                return False, f"语法错误({len(errors)}处):\n" + "\n".join(errors)
            return False, f"语法检查失败(exit={result.returncode}):\n{combined[:800]}"
        except subprocess.TimeoutExpired:
            return False, "[TIMEOUT] 语法检查超时"
        except Exception as e:
            return False, f"语法检查异常: {e}"

    def _handle_task(self, ws: Path, args: dict) -> tuple[bool, str]:
        """派发子任务给独立子智能体。共享 API 配置和 workspace。"""
        subtask = args.get("subtask", "")
        if not subtask:
            return False, "[BAD_ARGS] 缺少 subtask 参数"
        max_turns = max(1, int(args.get("max_turns", 6) or 6))

        # 创建子智能体（共享密钥和沙箱模式）
        sub_agent = LLMDrivenCodeX(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            sandbox_mode=self.sandbox_mode,
            provider=self.provider,
            thinking_enabled=self.thinking_enabled,
            thinking_depth=self.thinking_depth,
        )
        sub_agent._zi_zhinengti = True  # 标记为子智能体，抑制 TASK 输出
        result = sub_agent.run(task=subtask, workspace=ws, max_turns=max_turns)
        if result.ok:
            return True, f"[子任务完成] turns={result.turns} summary={result.summary[:300]}"
        else:
            return False, f"[子任务失败] turns={result.turns} error={result.error[:200]}"

    def _handle_replace_lines(self, ws: Path, args: dict) -> tuple[bool, str]:
        """按行号替换文件中的行。从下往上应用避免行号偏移。"""
        path = args.get("path", "")
        edits = args.get("edits", [])
        if not path or not edits:
            return False, "[BAD_ARGS] 缺少 path 或 edits 参数"

        target = ws / path
        if not target.exists():
            return False, f"文件不存在: {path}"

        # 快照
        import shutil
        backup = target.with_suffix(target.suffix + '.bak')
        shutil.copy2(target, backup)

        lines = target.read_text(encoding="utf-8").splitlines(keepends=True)

        # 从下往上排序，避免行号偏移
        edits_sorted = sorted(edits, key=lambda e: e.get("line", 0), reverse=True)
        n_total = len(edits_sorted)

        for idx, e in enumerate(edits_sorted):
            line_no = e.get("line", 0) - 1  # 转 0-indexed
            new_text = e.get("new_text", "")
            if line_no < 0 or line_no >= len(lines):
                n_done = n_total - idx - 1
                return False, f"[LINE_RANGE] 第{idx+1}/{n_total}处编辑失败：行号{e.get('line')}越界（共{len(lines)}行）。前{n_done}处已就绪但未写盘"
            # 替换：确保新文本以换行结尾
            if not new_text.endswith('\n'):
                new_text += '\n'
            # 如果 new_text 含多行（用 \n 分隔）
            new_lines = new_text.splitlines(keepends=True)
            lines[line_no:line_no+1] = new_lines

        target.write_text("".join(lines), encoding="utf-8")
        # 生成 diff 供 LLM 验证
        import difflib
        old_lines = backup.read_text(encoding="utf-8").splitlines()
        new_lines = target.read_text(encoding="utf-8").splitlines()
        diff = "\n".join(difflib.unified_diff(old_lines, new_lines, fromfile=f"a/{path}", tofile=f"b/{path}", lineterm=""))
        diff_summary = diff[:800] if diff else "（无变化）"
        return True, f"替换成功：{len(edits)}处修改\n--- diff ---\n{diff_summary}"

    def _parse_response(self, content: str) -> tuple[dict | None, str, str | None, str | None]:
        text = content.strip()

        # 用 _extract_json 提取 JSON（支持多行），再检查 done/confirm
        obj = self._extract_json(text)
        if obj:
            if obj.get("done") is True and "summary" in obj:
                return None, "", obj["summary"], None
            if "confirm" in obj:
                return None, "", None, obj["confirm"]

        lines = [l.strip() for l in text.split('\n') if l.strip()]
        tool = None
        broadcast = ""

        for i, line in enumerate(lines):
            obj = self._extract_json(line)
            if obj and "tool_name" in obj:
                tool = obj
                # 人话在前——取 JSON 前面的行作为播报
                ren_hua = []
                for j in range(i):
                    if not lines[j].startswith('{'):
                        ren_hua.append(lines[j])
                broadcast = " ".join(ren_hua) if ren_hua else ""
                break

        if not tool:
            obj = self._extract_json(text)
            if obj and "tool_name" in obj:
                tool = obj

        if broadcast.startswith("【"): broadcast = broadcast.lstrip("【").rstrip("】")
        return tool, broadcast, None, None

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        start = text.find("{")
        if start == -1:
            return None
        depth = 0
        in_string = False
        escape = False
        for i, ch in enumerate(text[start:], start):
            if escape:
                escape = False
                continue
            if ch == '\\':
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        return None
        return None
