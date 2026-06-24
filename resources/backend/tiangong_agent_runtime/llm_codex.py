"""LLMDrivenCodeX — LLM 主脑 + 直接工具集（绕过 Runtime）

执行模式（抄 Codex CLI）：
    工具全部直接操作文件系统。LLM 决策，工具执行。
    work_log_read | read_file | write_file | replace_lines | glob | grep | bash | list_dir | python_quality_runner | code_quality_runner | work_log_write
"""
from __future__ import annotations

import difflib, hashlib, json, re, os, sys, shutil, subprocess
from collections import Counter
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
try:
    from .codex_tool_specs import (
        CODEX_SYSTEM_FLOW,
        CODEX_TOOL_PLAYBOOK,
        codex_tool_descriptions,
    )
except ImportError:  # pragma: no cover - supports direct script-style imports
    from codex_tool_specs import (  # type: ignore
        CODEX_SYSTEM_FLOW,
        CODEX_TOOL_PLAYBOOK,
        codex_tool_descriptions,
    )

try:
    from .codex_stage_journal import (
        load_latest_planning_snapshot,
        new_run_id as new_codex_journal_run_id,
        write_stage_log,
        write_terminal_log,
        write_tool_step_log,
    )
except ImportError:  # pragma: no cover - supports direct script-style imports
    from codex_stage_journal import (  # type: ignore
        load_latest_planning_snapshot,
        new_run_id as new_codex_journal_run_id,
        write_stage_log,
        write_terminal_log,
        write_tool_step_log,
    )

SANDBOX_READ_ONLY = "read_only"
SANDBOX_WORKSPACE_WRITE = "workspace_write"
SANDBOX_DANGER_FULL = "danger_full"
DESTRUCTIVE_TOOLS = {"write_file", "replace_lines", "safe_apply_patch", "bash", "work_log_write"}
CODEX_WORK_LOG_REL = Path(".linyuanzhe") / "codex_work_log.md"
CODEX_SKIP_DIRS = {
    ".git", ".hg", ".svn", ".linyuanzhe", "__pycache__", "node_modules",
    ".venv", "venv", "dist", "build", "backend_runtime", "site-packages",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
}
CODEX_TEXT_ENCODINGS = ("utf-8", "utf-8-sig", "gb18030", "cp936", "latin-1")
CODEX_TEXT_SUFFIXES = {
    ".py", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".html", ".css",
    ".json", ".yaml", ".yml", ".toml", ".md", ".txt", ".ini", ".cfg", ".env",
    ".ps1", ".bat", ".cmd", ".sh", ".go", ".rs", ".java", ".kt", ".cs", ".cpp",
    ".c", ".h", ".hpp", ".sql", ".xml", ".vue", ".svelte",
}
CODEX_TERMINAL_REVIEW_THRESHOLD = 3
CODEX_RECOVERY_REVIEW_LIMIT = 2
DANGEROUS_COMMAND_PATTERNS = (
    r"\brm\s+(-rf|-fr|/s|/q)\b",
    r"\bdel\s+(/s|/q)\b",
    r"\brmdir\s+(/s|/q)\b",
    r"\bremove-item\b.*(?:^|\s)(?:-recurse|-r)\b",
    r"\bformat\b",
    r"\bmkfs\b",
    r"\breg\s+delete\b",
    r"\bpowershell(?:\.exe)?\s+-enc(?:odedcommand)?\b",
    r"\b(?:curl|wget|iwr|irm)\b.*\|\s*(?:sh|bash|powershell|pwsh)\b",
)


LLM_TOOLS = {
    "work_log_read": "读取上一轮 Code-X 工作日志卡。参数: 无。每次执行详细步骤前必须先调用",
    "read_file": "读取workspace内文本文件。参数: path, start_line?, max_lines?, max_chars?。返回行号、编码、sha256和截断标记",
    "list_dir": "列出workspace内目录。参数: path(默认.), limit?。返回目录/文件标记、数量和截断标记",
    "write_file": "创建/覆写workspace内文件。参数: path, content。支持空文件，覆写前备份，写后读回校验并返回diff与sha256",
    "replace_lines": "按行号事务化修改文件。参数: path, edits([{line,new_text,expected?/old_text?}]), dry_run?。先校验全部编辑，失败不写盘，成功返回diff/备份/sha256",
    "glob": "按通配符找workspace文件。参数: pattern(如*.py, src/**/*.rs), path?, limit?。默认跳过依赖/缓存目录",
    "grep": "搜索workspace文件内容。参数: pattern(正则), path?, limit?, timeout?。优先rg，回退Python扫描，返回文件:行号:内容",
    "bash": "执行安全命令。参数: command, timeout。workspace目录执行，命中危险命令模式会按A5阻断；优先用于测试/检查，不用于直接删改文件",
    "python_quality_runner": "Python语法检查。参数: target(文件或目录,默认.), timeout?。workspace边界内执行compileall",
    "code_quality_runner": "多语言项目质量检查。参数: target?, language?, timeout?, project_scripts?, run_tests?, skip_project_scripts?。检查Python/JS/JSON/TS/Go/Rust，并自动尝试安全npm脚本(smoke/ensure/check/lint)",
    "codebase_map": "生成代码库地图。参数: path?, max_files?。识别语言、框架、包管理器、入口文件、测试/构建脚本和关键目录",
    "git_inspect": "读取Git状态。参数: mode(status/diff/stat/files/show), target?, max_chars?。用于改前改后确认真实变更，不写文件",
    "failure_parser": "解析失败输出。参数: text 或 path, max_items?。提取文件、行号、错误类型、命令线索和下一步建议",
    "safe_apply_patch": "安全应用unified diff。参数: patch, dry_run?, reverse?。先git apply --check，校验workspace路径，应用前备份，返回diff/stat/备份",
    "test_selector": "根据改动文件选择验证命令。参数: files?, include_project_scripts?, run_tests?。返回建议的code_quality_runner/bash命令，不执行",
    "symbol_search": "符号级轻量搜索。参数: query, kind(definition/reference/import/any), path?, language?, limit?。用于定位函数/类/引用/导入",
    "dependency_probe": "探测代码工具依赖。参数: 无。检查node/npm/git/python、package scripts、Playwright模块和Chromium可启动性",
    "frontend_devserver": "前端开发服务器工具。参数: action(plan/status/probe/start/stop), script?, url?, port?, timeout?。只运行package.json内安全脚本",
    "browser_verify": "真实浏览器验证工具。参数: url, actions?, screenshot?, timeout?, viewport?。使用Playwright采集console/pageerror/request失败、DOM摘要和截图；缺依赖时返回安装提示",
    "semantic_index": "构建语义索引。参数: path?, max_files?, max_symbols?, max_calls?。零额外依赖解析 Python/JS/TS/Vue/Svelte，返回符号、导入、调用摘要",
    "semantic_lookup": "语义查找。参数: query, kind(any/definition/import/call/reference), path?, match(exact/contains), limit?。基于 semantic_index 查定义/导入/调用",
    "call_graph": "局部调用图。参数: symbol, direction(both/callees/callers), depth?, path?, limit?。返回调用边和相关定义",
    "work_log_write": "写入本次 Code-X 工作日志卡。参数: content(本次目标、变更、验证、遗留问题)。全部步骤done前必须调用",
    "task": "派发子任务给独立子智能体。参数: subtask, max_turns?, allow_write?。默认只读侦察，返回证据摘要；主线负责最终补丁",
}
LEGACY_LLM_TOOLS = LLM_TOOLS
LLM_TOOLS = codex_tool_descriptions()

SYSTEM_PROMPT = """你是临渊者 Code-X 执行体。工具集：work_log_read读取上一轮日志卡→dependency_probe确认环境→codebase_map识别项目→git_inspect看变更→read_file读取→glob/grep/symbol_search定位→replace_lines/safe_apply_patch/write_file修改→test_selector选择验证→frontend_devserver启动/探活前端→browser_verify真实浏览器验证→python_quality_runner/code_quality_runner验证→failure_parser解析失败→work_log_write写本次日志卡→task派发只读侦察子任务。

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
SYSTEM_PROMPT = re.sub(
    r"工具集：.*",
    f"Toolchain: {CODEX_SYSTEM_FLOW}",
    SYSTEM_PROMPT,
    count=1,
)
SYSTEM_PROMPT += "\n\n[Code-X Toolchain Principles]\n" + CODEX_TOOL_PLAYBOOK
SYSTEM_PROMPT += (
    "\n\n[Code-X Terminal Judgement Rule]\n"
    "Errors, malformed responses, empty tool calls, and repeated recoverable tool failures are not task failure. "
    "They trigger terminal judgement first. A task may stop only as done, waiting_for_user, external_error, or blocked with evidence."
)


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
        args = shlex.split(command, posix=os.name != "nt")
    except ValueError:
        args = shlex.split(command, posix=False)
    if os.name == "nt":
        args = [_strip_wrapping_quotes(item) for item in args]
        if args and Path(args[0]).name.lower() in {"python", "python.exe", "py", "py.exe"}:
            args[0] = sys.executable
        if args and Path(args[0]).name.lower() in {"bash", "bash.exe", "sh", "sh.exe"}:
            args = _normalize_windows_bash_args(args)
    return args


def _normalize_windows_bash_args(args: list[str]) -> list[str]:
    if not args:
        return args
    shell_name = Path(args[0]).name.lower()
    resolved = _resolve_windows_bash(shell_name)
    path_style = "wsl" if _is_windows_wsl_bash(resolved or args[0]) else "git"
    normalized = [resolved or args[0]]
    normalized.extend(_windows_path_to_bash_arg(item, style=path_style) for item in args[1:])
    return normalized


def _resolve_windows_bash(shell_name: str) -> str:
    candidates = []
    if os.name == "nt":
        program_files = [os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")]
        for base in program_files:
            if base:
                candidates.extend([
                    str(Path(base) / "Git" / "bin" / "bash.exe"),
                    str(Path(base) / "Git" / "usr" / "bin" / "bash.exe"),
                    str(Path(base) / "Git" / "usr" / "bin" / "sh.exe"),
                ])
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            candidates.append(str(Path(local_app) / "Programs" / "Git" / "bin" / "bash.exe"))
    candidates.extend([shutil.which(shell_name), shutil.which("bash" if shell_name.startswith("bash") else "sh")])
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(Path(candidate))
    return ""


def _is_windows_wsl_bash(value: str) -> bool:
    text = os.path.normcase(str(value or ""))
    return bool(text.endswith(os.path.normcase(r"\Windows\System32\bash.exe")) or text.endswith(os.path.normcase(r"\Windows\System32\wsl.exe")))


def _windows_path_to_bash_arg(value: str, *, style: str = "git") -> str:
    text = str(value or "")
    if os.name != "nt" or not re.match(r"^[A-Za-z]:[\\/]", text):
        return text
    drive = text[0].lower()
    rest = text[2:].replace("\\", "/")
    if not rest.startswith("/"):
        rest = "/" + rest
    if style == "wsl":
        return f"/mnt/{drive}{rest}"
    return f"/{drive}{rest}"


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


def _read_text_with_encoding(path: Path) -> tuple[str, str]:
    for encoding in CODEX_TEXT_ENCODINGS:
        try:
            return path.read_text(encoding=encoding), encoding
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace"), "utf-8-replace"


def _read_text_best_effort(path: Path, limit: int = 6000) -> str:
    try:
        text, _encoding = _read_text_with_encoding(path)
        return text[:limit]
    except Exception:
        return ""


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except Exception:
        number = default
    return max(minimum, min(maximum, number))


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _bytes_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _looks_binary(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return b"\x00" in fh.read(4096)
    except Exception:
        return False


def _dangerous_command_reason(command: str) -> str:
    text = str(command or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    for pattern in DANGEROUS_COMMAND_PATTERNS:
        if re.search(pattern, lowered):
            return f"dangerous command pattern matched: {pattern}"
    return ""


def _safe_rel(path: Path, root: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
    except Exception:
        return str(path)


def _short_context(value: Any, limit: int = 1200) -> str:
    text = str(value or "").replace("\x00", "").strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:limit]


def _format_runtime_guidance(items: list[dict[str, Any]]) -> str:
    lines = ["[Code-X运行中用户纠偏]"]
    for index, item in enumerate(items, 1):
        at = item.get("at") or ""
        text = _short_context(item.get("text") or item.get("message") or item.get("content"), 1000)
        if text:
            lines.append(f"{index}. at={at} {text}")
    lines.append("处理要求：立即回到大框架/结构架构卡/详细步骤，按用户新方向调整计划；保留已完成且不冲突的事实，冲突时以本纠偏为准。")
    return "\n".join(lines)


@dataclass
class LLMCodeXResult:
    ok: bool
    status: str = "incomplete"
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
        self._runtime_guidance_seen: set[str] = set()

    def _chat_kwargs(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.4,
            "max_tokens": 4000,
        }
        kwargs.update(_openai_sdk_thinking_kwargs(self.provider, self.thinking_enabled, self.thinking_depth))
        return kwargs

    def _runtime_guidance_path(self) -> Path | None:
        raw = os.environ.get("TIANGONG_RUNTIME_GUIDANCE_PATH") or os.environ.get("TIANGONG_CODEX_GUIDANCE_PATH")
        if not raw:
            return None
        try:
            return Path(raw).expanduser().resolve()
        except OSError:
            return None

    def _consume_runtime_guidance(self) -> list[dict[str, Any]]:
        path = self._runtime_guidance_path()
        if not path or not path.exists():
            return []
        items: list[dict[str, Any]] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            return []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or item.get("message") or item.get("content") or "").strip()
            if not text:
                continue
            guidance_id = str(item.get("id") or f"{item.get('at','')}:{text[:80]}")
            if guidance_id in self._runtime_guidance_seen:
                continue
            self._runtime_guidance_seen.add(guidance_id)
            items.append({**item, "id": guidance_id, "text": text[:4000]})
        return items

    def _recent_failure_summary(self, steps: list[dict], limit: int = 6) -> str:
        rows: list[str] = []
        for item in reversed(steps):
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "")
            if item_type == "tool" and bool(item.get("ok")):
                continue
            if item_type not in {"tool", "no_tool", "llm_error", "confirm"}:
                continue
            detail = item.get("output") or item.get("message") or item.get("llm_out") or item.get("error") or ""
            if item_type == "tool":
                label = f"tool:{item.get('tool_name') or '?'}"
            else:
                label = item_type
            rows.append(f"- {label}: {_short_context(detail, 220)}")
            if len(rows) >= limit:
                break
        return "\n".join(reversed(rows)) if rows else "- no recent failure evidence"

    def _terminal_judgement(
        self,
        *,
        failure_kind: str,
        recent_error: str,
        recovery_attempts: Counter[str],
        steps: list[dict],
        progress_snapshot: dict[str, Any],
    ) -> dict[str, str]:
        """Classify a would-be stop. Counts are evidence, not the verdict."""
        recovery_attempts[failure_kind] += 1
        attempt = int(recovery_attempts[failure_kind])
        progress = 0.0
        if isinstance(progress_snapshot, dict):
            try:
                progress = float(progress_snapshot.get("total_progress") or 0)
            except Exception:
                progress = 0.0
        recent = self._recent_failure_summary(steps)
        base_prompt = (
            "[Code-X terminal judgement: continue]\n"
            f"failure_kind={failure_kind}; review_attempt={attempt}; progress={progress:.2f}\n"
            "Do not classify this as final task failure. Return to the structured plan and choose one concrete next tool call.\n"
            "If the previous response was malformed, output only valid tool JSON with step_id/substep/tool_name/arguments.\n"
            "If a tool failed, inspect the failure, switch arguments or use an equivalent diagnostic tool.\n"
            "Recent failure evidence:\n"
            f"{recent}\n"
            f"last_error={_short_context(recent_error, 500)}"
        )
        if failure_kind == "llm_error":
            return {
                "action": "stop",
                "status": "external_error",
                "summary": "Model provider failed repeatedly; task route remains resumable.",
                "error": _short_context(recent_error, 900),
            }
        if failure_kind == "waiting_for_user":
            return {
                "action": "stop",
                "status": "waiting_for_user",
                "summary": "Code-X is waiting for user confirmation before continuing.",
                "error": _short_context(recent_error, 900),
            }
        hard_boundary = failure_kind == "sandbox_denied"
        limit = 1 if hard_boundary else CODEX_RECOVERY_REVIEW_LIMIT
        if attempt <= limit:
            return {
                "action": "continue",
                "status": "recovering",
                "summary": "Recoverable failure; continue after terminal judgement.",
                "error": _short_context(recent_error, 900),
                "prompt": base_prompt,
            }
        status = "blocked" if hard_boundary else "incomplete"
        return {
            "action": "stop",
            "status": status,
            "summary": (
                f"Terminal judgement stopped as {status}: {failure_kind}. "
                "This is not a completed-task failure; resume can continue from the saved route."
            ),
            "error": _short_context(recent_error or recent, 1200),
        }

    def _usable_initial_plans(self, initial_plans: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(initial_plans, dict):
            return {}
        source = dict(initial_plans.get("plans") or initial_plans)

        def pick(*names: str) -> Any:
            for name in names:
                value = source.get(name)
                if value not in (None, ""):
                    return value
            return ""

        structured = pick("structured_plan")
        if isinstance(structured, str):
            try:
                parsed = json.loads(structured)
                if isinstance(parsed, dict):
                    structured = parsed
            except Exception:
                pass
        plans = {
            "macro_plan": pick("macro_plan", "macro"),
            "structure_plan": pick("structure_plan", "structure"),
            "detailed_steps": pick("detailed_steps", "detail"),
            "structured_plan": structured,
        }
        if plans["macro_plan"] and plans["structure_plan"] and plans["detailed_steps"]:
            return plans
        return {}

    def run(self, task: str, workspace: str | Path, *, max_turns: int = 12,
            buzhou_huidiao: Any = None,
            guihua_huidiao: Any = None,
            jindu_huidiao: Any = None,
            initial_plans: dict[str, Any] | None = None,
            initial_progress_snapshot: dict[str, Any] | None = None,
            journal_run_id: str = "") -> LLMCodeXResult:
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
        run_id = journal_run_id or new_codex_journal_run_id(task)

        def _record_terminal(status: str, summary: str = "", error: str = "", progress_snapshot: dict[str, Any] | None = None) -> None:
            try:
                write_terminal_log(
                    ws,
                    run_id,
                    status=status,
                    summary=summary,
                    error=error,
                    plans=plans if isinstance(plans, dict) else {},
                    progress_snapshot=progress_snapshot or {},
                )
            except Exception:
                pass

        try:
            write_stage_log(
                ws,
                run_id,
                "run_start",
                task=task,
                content={"task": task, "workspace": str(ws), "model": self.model, "provider": self.provider},
                plans={},
                index_knowledge=False,
            )
        except Exception:
            pass

        # ═══ 三层递进规划（三轮独立对话） ═══
        durable_snapshot_plans = {}
        if initial_plans or "Code-X Durable Resume Card" in str(task):
            latest_planning_snapshot = load_latest_planning_snapshot(ws)
            latest_run_id = str(latest_planning_snapshot.get("run_id") or "").strip()
            if not journal_run_id or not latest_run_id or latest_run_id == journal_run_id:
                durable_snapshot_plans = self._usable_initial_plans(latest_planning_snapshot)
        plans = durable_snapshot_plans or self._usable_initial_plans(initial_plans)
        if plans:
            steps.append({"stage": "planning_resume", "output": "using saved Code-X three-stage planning snapshot"})
            if guihua_huidiao:
                try:
                    guihua_huidiao("planning_resume", plans, "done")
                except Exception:
                    pass
            try:
                write_stage_log(
                    ws,
                    run_id,
                    "planning_resume",
                    task=task,
                    content=plans,
                    plans=plans,
                    extra={"resume_without_replanning": True},
                )
            except Exception:
                pass
        else:
            plans = self._san_ceng_guihua(client, task, ws, tools_desc, steps, guihua_huidiao, run_id=run_id)
        plans["_journal"] = {"run_id": run_id}

        # ═══ 结构化计划 + 动态数学评估器 ═══
        structured_plan = normalize_structured_plan(
            plans.get("structured_plan") if isinstance(plans.get("structured_plan"), dict) else {},
            task=task,
            fallback_steps=fallback_step_titles(str(plans.get("detailed_steps", ""))),
        )
        evaluator = CodeXProgressEvaluator(structured_plan)
        task_steps = [f"{step.step_id}: {step.title}" for step in structured_plan.steps]
        if isinstance(initial_progress_snapshot, dict) and initial_progress_snapshot:
            initial_progress = evaluator.restore_snapshot(initial_progress_snapshot)
        else:
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
        def _build_execution_messages(
            active_task: str,
            active_plans: dict[str, Any],
            active_structured_plan: Any,
            progress_snapshot: dict[str, Any],
            guidance_note: str = "",
        ) -> list[dict[str, Any]]:
            start_rule = "第一轮必须调用 work_log_read。" if not self._work_log_read else "上一轮工作日志已读取；不要重复读日志，直接按更新后的计划继续。"
            guidance_line = f"\n\n{guidance_note}\n" if guidance_note else "\n"
            return [
                {"role": "system", "content": SYSTEM_PROMPT + f"\n\n工作目录: {ws}\n\n可用工具:\n{tools_desc}"},
                {"role": "user", "content": active_task},
                {"role": "assistant", "content": active_plans.get("macro_plan", "")},
                {"role": "assistant", "content": active_plans.get("structure_plan", "")},
                {"role": "assistant", "content": active_plans.get("detailed_steps", "")},
                {"role": "assistant", "content": json.dumps({"structured_plan": active_structured_plan.public_dict()}, ensure_ascii=False)},
                {
                    "role": "user",
                    "content": (
                        "规划完成。现在按照结构化计划开始执行工具调用。"
                        "每轮输出工具JSON + 中文进度播报(≤25字)，工具JSON必须带 step_id/substep。"
                        f"{start_rule}{guidance_line}\n"
                        + self._execution_context_card(ws, active_task, active_plans, active_structured_plan, progress_snapshot)
                    ),
                },
            ]

        messages = _build_execution_messages(task, plans, structured_plan, initial_progress)
        turn = 0
        consecutive_failures = 0
        recovery_attempts: Counter[str] = Counter()
        while True:
            runtime_guidance = self._consume_runtime_guidance()
            if runtime_guidance:
                guidance_card = _format_runtime_guidance(runtime_guidance)
                steps.append({"stage": "runtime_guidance", "output": guidance_card[:1000]})
                if guihua_huidiao:
                    try:
                        guihua_huidiao("runtime_guidance", {"items": runtime_guidance}, "done")
                        guihua_huidiao("reframe", guidance_card, "running")
                    except Exception:
                        pass
                task = "\n\n".join([
                    task,
                    guidance_card,
                    "继续要求：重新调整大框架、结构架构卡和详细步骤后再继续执行；不要沿用已被纠偏否定的路线。",
                ])
                self._work_log_written = False
                plans = self._san_ceng_guihua(client, task, ws, tools_desc, steps, guihua_huidiao, run_id=run_id)
                structured_plan = normalize_structured_plan(
                    plans.get("structured_plan") if isinstance(plans.get("structured_plan"), dict) else {},
                    task=task,
                    fallback_steps=fallback_step_titles(str(plans.get("detailed_steps", ""))),
                )
                evaluator = CodeXProgressEvaluator(structured_plan)
                progress_snapshot = evaluator.snapshot()
                if jindu_huidiao:
                    try:
                        jindu_huidiao(progress_snapshot)
                    except Exception:
                        pass
                if guihua_huidiao:
                    try:
                        guihua_huidiao("reframe", {"structured_plan": structured_plan.public_dict()}, "done")
                    except Exception:
                        pass
                messages = _build_execution_messages(task, plans, structured_plan, progress_snapshot, guidance_card)
                consecutive_failures = 0

            try:
                resp = client.chat.completions.create(**self._chat_kwargs(messages))
                content = resp.choices[0].message.content.strip()
            except Exception as e:
                consecutive_failures += 1
                reason = f"LLM调用失败: {e}"
                steps.append({"turn": turn, "type": "llm_error", "message": reason})
                if consecutive_failures >= CODEX_TERMINAL_REVIEW_THRESHOLD:
                    judgement = self._terminal_judgement(
                        failure_kind="llm_error",
                        recent_error=reason,
                        recovery_attempts=recovery_attempts,
                        steps=steps,
                        progress_snapshot=evaluator.snapshot(),
                    )
                    _record_terminal(judgement["status"], judgement["summary"], judgement["error"], evaluator.snapshot())
                    return LLMCodeXResult(
                        ok=False,
                        status=judgement["status"],
                        turns=turn + 3,
                        steps=steps,
                        plans=plans,
                        structured_plan=structured_plan.public_dict(),
                        progress_snapshot=evaluator.snapshot(),
                        summary=judgement["summary"],
                        error=judgement["error"],
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
                    if consecutive_failures >= CODEX_TERMINAL_REVIEW_THRESHOLD:
                        judgement = self._terminal_judgement(
                            failure_kind="done_without_log",
                            recent_error="done was emitted before work_log_write",
                            recovery_attempts=recovery_attempts,
                            steps=steps,
                            progress_snapshot=evaluator.snapshot(),
                        )
                        if judgement["action"] == "continue":
                            messages.append({"role": "user", "content": judgement["prompt"] + "\n\n" + self._execution_context_card(ws, task, plans, structured_plan, evaluator.snapshot())})
                            self._trim_messages(messages)
                            consecutive_failures = 0
                        else:
                            _record_terminal(judgement["status"], judgement["summary"], judgement["error"], evaluator.snapshot())
                            return LLMCodeXResult(
                                ok=False,
                                status=judgement["status"],
                                turns=turn + 3,
                                steps=steps,
                                plans=plans,
                                structured_plan=structured_plan.public_dict(),
                                progress_snapshot=evaluator.snapshot(),
                                summary=judgement["summary"],
                                error=judgement["error"],
                            )
                    turn += 1
                    continue
                progress_snapshot = evaluator.mark_done(done)
                if jindu_huidiao:
                    try:
                        jindu_huidiao(progress_snapshot)
                    except Exception:
                        pass
                steps.append({"turn": turn, "type": "done", "summary": done})
                _record_terminal("done", done, "", progress_snapshot)
                return LLMCodeXResult(
                    ok=True,
                    status="done",
                    turns=turn + 3,
                    steps=steps,
                    summary=done,
                    plans=plans,
                    structured_plan=structured_plan.public_dict(),
                    progress_snapshot=progress_snapshot,
                )

            if confirm:
                steps.append({"turn": turn, "type": "confirm", "message": confirm})
                judgement = self._terminal_judgement(
                    failure_kind="waiting_for_user",
                    recent_error=str(confirm),
                    recovery_attempts=recovery_attempts,
                    steps=steps,
                    progress_snapshot=evaluator.snapshot(),
                )
                _record_terminal(judgement["status"], confirm, judgement["error"], evaluator.snapshot())
                return LLMCodeXResult(ok=False, status=judgement["status"], turns=turn + 3, steps=steps, plans=plans,
                                      structured_plan=structured_plan.public_dict(),
                                      progress_snapshot=evaluator.snapshot(),
                                      summary=confirm, error=judgement["error"])

            if not tool:
                consecutive_failures += 1
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": "请输出工具调用 JSON 或 done。"})
                steps.append({"turn": turn, "type": "no_tool", "llm_out": content[:200]})
                if consecutive_failures >= CODEX_TERMINAL_REVIEW_THRESHOLD:
                    judgement = self._terminal_judgement(
                        failure_kind="no_tool",
                        recent_error="model did not emit a valid tool call",
                        recovery_attempts=recovery_attempts,
                        steps=steps,
                        progress_snapshot=evaluator.snapshot(),
                    )
                    if judgement["action"] == "continue":
                        messages.append({"role": "user", "content": judgement["prompt"] + "\n\n" + self._execution_context_card(ws, task, plans, structured_plan, evaluator.snapshot())})
                        self._trim_messages(messages)
                        consecutive_failures = 0
                    else:
                        _record_terminal(judgement["status"], judgement["summary"], judgement["error"], evaluator.snapshot())
                        return LLMCodeXResult(
                            ok=False,
                            status=judgement["status"],
                            turns=turn + 3,
                            steps=steps,
                            plans=plans,
                            structured_plan=structured_plan.public_dict(),
                            progress_snapshot=evaluator.snapshot(),
                            summary=judgement["summary"],
                            error=judgement["error"],
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
                if consecutive_failures >= CODEX_TERMINAL_REVIEW_THRESHOLD:
                    judgement = self._terminal_judgement(
                        failure_kind="missing_work_log_read",
                        recent_error="model tried to execute before work_log_read",
                        recovery_attempts=recovery_attempts,
                        steps=steps,
                        progress_snapshot=evaluator.snapshot(),
                    )
                    if judgement["action"] == "continue":
                        messages.append({"role": "user", "content": judgement["prompt"] + "\n\n" + self._execution_context_card(ws, task, plans, structured_plan, evaluator.snapshot())})
                        self._trim_messages(messages)
                        consecutive_failures = 0
                    else:
                        _record_terminal(judgement["status"], judgement["summary"], judgement["error"], evaluator.snapshot())
                        return LLMCodeXResult(
                            ok=False,
                            status=judgement["status"],
                            turns=turn + 3,
                            steps=steps,
                            plans=plans,
                            structured_plan=structured_plan.public_dict(),
                            progress_snapshot=evaluator.snapshot(),
                            summary=judgement["summary"],
                            error=judgement["error"],
                        )
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
                try:
                    write_tool_step_log(ws, run_id, step, progress_snapshot=progress_snapshot)
                except Exception:
                    pass
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
                if consecutive_failures >= CODEX_TERMINAL_REVIEW_THRESHOLD:
                    judgement = self._terminal_judgement(
                        failure_kind="sandbox_denied",
                        recent_error=f"sandbox denied tool={tool_name}",
                        recovery_attempts=recovery_attempts,
                        steps=steps,
                        progress_snapshot=progress_snapshot,
                    )
                    if judgement["action"] == "continue":
                        messages.append({"role": "user", "content": judgement["prompt"] + "\n\n" + self._execution_context_card(ws, task, plans, structured_plan, progress_snapshot)})
                        self._trim_messages(messages)
                        consecutive_failures = 0
                    else:
                        _record_terminal(judgement["status"], judgement["summary"], judgement["error"], progress_snapshot)
                        return LLMCodeXResult(
                            ok=False,
                            status=judgement["status"],
                            turns=turn + 3,
                            steps=steps,
                            plans=plans,
                            structured_plan=structured_plan.public_dict(),
                            progress_snapshot=progress_snapshot,
                            summary=judgement["summary"],
                            error=judgement["error"],
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
                elif tool_name == "file_ops":
                    tool_ok, tool_output = self._handle_file_ops(ws, args)
                elif tool_name == "rollback_ops":
                    tool_ok, tool_output = self._handle_rollback_ops(ws, args)
                elif tool_name == "rollback_preview":
                    tool_ok, tool_output = self._handle_rollback_preview(ws, args)
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
                elif tool_name == "codebase_map":
                    tool_ok, tool_output = self._handle_codebase_map(ws, args)
                elif tool_name == "git_inspect":
                    tool_ok, tool_output = self._handle_git_inspect(ws, args)
                elif tool_name == "failure_parser":
                    tool_ok, tool_output = self._handle_failure_parser(ws, args)
                elif tool_name == "safe_apply_patch":
                    tool_ok, tool_output = self._handle_safe_apply_patch(ws, args)
                elif tool_name == "diff_guard":
                    tool_ok, tool_output = self._handle_diff_guard(ws, args)
                elif tool_name == "evidence_card":
                    tool_ok, tool_output = self._handle_evidence_card(ws, args)
                elif tool_name == "readback_verifier":
                    tool_ok, tool_output = self._handle_readback_verifier(ws, args)
                elif tool_name == "impact_analyzer":
                    tool_ok, tool_output = self._handle_impact_analyzer(ws, args)
                elif tool_name == "test_selector":
                    tool_ok, tool_output = self._handle_test_selector(ws, args)
                elif tool_name == "symbol_search":
                    tool_ok, tool_output = self._handle_symbol_search(ws, args)
                elif tool_name == "semantic_index":
                    tool_ok, tool_output = self._handle_semantic_index(ws, args)
                elif tool_name == "semantic_lookup":
                    tool_ok, tool_output = self._handle_semantic_lookup(ws, args)
                elif tool_name == "call_graph":
                    tool_ok, tool_output = self._handle_call_graph(ws, args)
                elif tool_name == "dependency_probe":
                    tool_ok, tool_output = self._handle_dependency_probe(ws, args)
                elif tool_name == "frontend_devserver":
                    tool_ok, tool_output = self._handle_frontend_devserver(ws, args)
                elif tool_name == "browser_verify":
                    tool_ok, tool_output = self._handle_browser_verify(ws, args)
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
            try:
                write_tool_step_log(ws, run_id, step, progress_snapshot=progress_snapshot)
            except Exception:
                pass
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
            if tool_name not in ("read_file", "write_file", "file_ops", "rollback_ops", "rollback_preview", "glob", "grep", "list_dir", "bash", "replace_lines", "safe_apply_patch", "codebase_map", "git_inspect", "failure_parser", "diff_guard", "evidence_card", "readback_verifier", "impact_analyzer", "test_selector", "symbol_search", "semantic_index", "semantic_lookup", "call_graph", "dependency_probe", "frontend_devserver", "browser_verify"):
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
            if consecutive_failures >= CODEX_TERMINAL_REVIEW_THRESHOLD:
                judgement = self._terminal_judgement(
                    failure_kind="tool_error",
                    recent_error=f"tool={tool_name}; output={_short_context(tool_output, 900)}",
                    recovery_attempts=recovery_attempts,
                    steps=steps,
                    progress_snapshot=progress_snapshot,
                )
                if judgement["action"] == "continue":
                    messages.append({"role": "user", "content": judgement["prompt"] + "\n\n" + self._execution_context_card(ws, task, plans, structured_plan, progress_snapshot)})
                    self._trim_messages(messages)
                    consecutive_failures = 0
                else:
                    _record_terminal(judgement["status"], judgement["summary"], judgement["error"], progress_snapshot)
                    return LLMCodeXResult(
                        ok=False,
                        status=judgement["status"],
                        turns=turn + 3,
                        steps=steps,
                        plans=plans,
                        structured_plan=structured_plan.public_dict(),
                        progress_snapshot=progress_snapshot,
                        summary=judgement["summary"],
                        error=judgement["error"],
                    )
            turn += 1

    def _san_ceng_guihua(self, client, task: str, ws: Path, tools_desc: str,
                         steps: list, guihua_huidiao: Any = None,
                         run_id: str = "") -> dict[str, Any]:
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
            "规划必须继承用户原始任务。用户未明确的范围、日志或验收命令只能写成「待确认假设」，禁止伪造。\n\n"
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
        if run_id:
            try:
                write_stage_log(ws, run_id, "macro", task=task, content=macro, plans=plans)
            except Exception:
                pass
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
            f"文件清单必须优先来自用户明确范围、工作区项目标记和已读证据，"
            f"不确定的文件写「先定位后确认」，不要凭空指定。\n\n"
            f"基于宏观概念框架，完成以下五项。每一项都必须回答：\n\n"
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
            f"=== Five. Code-X tool integration boundary ===\n"
            f"- Do not create a separate intent-verifier layer; keep intent alignment inside this structure card.\n"
            f"- Use evidence_card only when evidence is too bulky for the model context.\n"
            f"- Put readback_verifier in detailed-step readback, after read_file.\n"
            f"- Put diff_guard, impact_analyzer, and test_selector in detailed-step quality planning, before real validation.\n"
            f"- Put rollback_preview only before rollback_ops when undo is being considered.\n\n"
            f"输出格式：纯文本，按以上模板逐项填写输出。不要输出JSON。\n"
            f"上一阶段输出参考：\n{macro[:1200]}"
        )
        plans["structure_plan"] = structure
        if run_id:
            try:
                write_stage_log(ws, run_id, "structure", task=task, content=structure, plans=plans)
            except Exception:
                pass
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
            f"把用户任务里仍不明确的内容转成第一步 inspect/clarify 的验证点；"
            f"验收方式不明确时，先用项目现有脚本或静态检查探测，不要硬编命令。\n\n"
            f"Tool integration rule: do not add new phases. Fold helper tools into the 6 substeps: "
            f"evidence_card in inspect when evidence is bulky; readback_verifier after read_file in readback; "
            f"diff_guard plus impact_analyzer/test_selector in quality planning; rollback_preview before rollback_ops only in recovery.\n\n"
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
            f"     - Rollback evidence: prefer rollback_ref returned by write_file / replace_lines / safe_apply_patch / file_ops.\n"
            f"     - Rollback preview: use rollback_preview only when undo is being considered; use rollback_ops only after the model decides.\n\n"
            f"  ③ 执行写入：\n"
            f"     - 工具：[write_file / replace_lines]\n"
            f"     - 目标位置：[文件路径，如果用replace_lines须写出行号范围]\n"
            f"     - 写入内容摘要：[简述要写入什么]\n\n"
            f"  ④ 读回验证（查看）：\n"
            f"     - Tool: read_file first; use readback_verifier when structured expected/forbidden checks are useful.\n"
            f"     - 读回文件：[路径]\n"
            f"     - 验证点：[行数 / 关键内容 / 文件大小]\n"
            f"     - 预期结果：[写什么就验证什么，必须一致]\n\n"
            f"  ⑤ 语法检查（校对）：\n"
            f"     - Tools: diff_guard for patch risk; impact_analyzer/test_selector for validation planning; python_quality_runner/code_quality_runner/browser_verify for real validation.\n"
            f"     - 检查目标：[文件或目录]\n"
            f"     - 预期结果：无语法错误\n"
            f"     - Failure handling: return to step 3 for a smaller edit; before rollback, call rollback_preview, then rollback_ops only if undo is intentional.\n\n"
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
        if run_id:
            try:
                write_stage_log(ws, run_id, "detail", task=task, content=detail, plans=plans)
            except Exception:
                pass
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
        if run_id:
            try:
                write_stage_log(
                    ws,
                    run_id,
                    "structured",
                    task=task,
                    content=plans["structured_plan"],
                    plans=plans,
                )
            except Exception:
                pass
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

    def _json_tool_output(self, payload: dict[str, Any], limit: int = 8000) -> str:
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        return text[:limit] + ("\n...[truncated]" if len(text) > limit else "")

    def _handle_codebase_map(self, ws: Path, args: dict) -> tuple[bool, str]:
        root, error = self._resolve_workspace_path(ws, args.get("path", "."))
        if error:
            return False, error
        if not root:
            return False, "[BAD_PATH] unresolved codebase root"
        if root.is_file():
            root = root.parent
        max_files = _bounded_int(args.get("max_files"), 800, 50, 5000)
        suffix_counts: Counter[str] = Counter()
        top_dirs: list[str] = []
        notable_files: list[str] = []
        total_files = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in CODEX_SKIP_DIRS and not d.startswith(".")]
            current = Path(dirpath)
            if current == root:
                top_dirs = sorted(dirnames)[:40]
            for name in filenames:
                total_files += 1
                path = current / name
                suffix_counts[path.suffix.lower() or "[no_ext]"] += 1
                if name in {
                    "package.json", "pyproject.toml", "requirements.txt", "pytest.ini", "tsconfig.json",
                    "vite.config.ts", "vite.config.js", "next.config.js", "electron-builder.yml",
                    "Cargo.toml", "go.mod", "README.md",
                }:
                    notable_files.append(_safe_rel(path, ws))
                if total_files >= max_files:
                    break
            if total_files >= max_files:
                break

        def _first_existing(names: list[str]) -> list[str]:
            hits = []
            for name in names:
                candidate = root / name
                if candidate.exists():
                    hits.append(_safe_rel(candidate, ws))
            return hits

        package_info: dict[str, Any] = {}
        package_json = root / "package.json"
        if package_json.exists():
            try:
                text, _encoding = _read_text_with_encoding(package_json)
                data = json.loads(text)
                deps = {**dict(data.get("dependencies") or {}), **dict(data.get("devDependencies") or {})}
                frameworks = [
                    name for name in ("electron", "react", "vue", "svelte", "next", "vite", "express", "typescript")
                    if name in deps or name == str(data.get("type") or "")
                ]
                package_info = {
                    "name": data.get("name"),
                    "type": data.get("type"),
                    "main": data.get("main"),
                    "scripts": list((data.get("scripts") or {}).keys())[:30],
                    "framework_hints": frameworks,
                }
            except Exception as exc:
                package_info = {"error": f"{type(exc).__name__}: {exc}"}

        python_info = {
            "config_files": _first_existing(["pyproject.toml", "requirements.txt", "setup.py", "pytest.ini", "tox.ini"]),
            "entry_candidates": _first_existing(["main.py", "app.py", "run_agent.py", "manage.py"]),
        }
        entry_candidates = _first_existing([
            "src/main.js", "src/main.ts", "src/index.js", "src/index.ts", "src/App.jsx", "src/App.tsx",
            "main.js", "preload.js", "index.html", "resources/backend/run_agent.py",
        ])
        suggested_checks = []
        if package_json.exists():
            scripts = package_info.get("scripts") or []
            for name in ("smoke", "ensure", "check", "lint", "test", "build"):
                if name in scripts:
                    suggested_checks.append(f"npm run {name}")
        if suffix_counts.get(".py"):
            suggested_checks.append("python -m py_compile / python_quality_runner")
        payload = {
            "schema": "tiangong.codex.codebase_map.v1",
            "root": _safe_rel(root, ws),
            "scanned_files": min(total_files, max_files),
            "truncated": total_files >= max_files,
            "top_dirs": top_dirs,
            "language_suffix_counts": dict(suffix_counts.most_common(20)),
            "notable_files": sorted(set(notable_files))[:60],
            "entry_candidates": entry_candidates,
            "package": package_info,
            "python": python_info,
            "suggested_checks": suggested_checks,
        }
        return True, self._json_tool_output(payload)

    def _run_git(self, ws: Path, args: list[str], timeout: int = 20) -> tuple[bool, str]:
        git = shutil.which("git")
        if not git:
            return False, "[GIT_NOT_FOUND] git executable not found"
        try:
            result = subprocess.run(
                [git, *args],
                cwd=str(ws),
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
            )
        except subprocess.TimeoutExpired:
            return False, f"[TIMEOUT] git {' '.join(args[:3])} > {timeout}s"
        output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part and part.strip())
        return result.returncode == 0, output

    def _handle_git_inspect(self, ws: Path, args: dict) -> tuple[bool, str]:
        mode = str(args.get("mode") or "status").strip().lower()
        target = str(args.get("target") or "").strip()
        max_chars = _bounded_int(args.get("max_chars"), 6000, 1000, 20000)
        ok, root_output = self._run_git(ws, ["rev-parse", "--show-toplevel"], timeout=8)
        if not ok:
            return False, "[NOT_GIT_REPO] workspace is not a git repository"
        root = Path(root_output.splitlines()[0]).resolve()
        target_args: list[str] = []
        if target:
            resolved, error = self._resolve_workspace_path(ws, target)
            if error:
                return False, error
            target_args = ["--", _safe_rel(resolved, root)]
        commands = {
            "status": ["status", "--short", "--branch"],
            "stat": ["diff", "--stat"],
            "files": ["diff", "--name-only"],
            "diff": ["diff"],
            "show": ["show", "--stat", "--oneline", "--decorate", "-1"],
        }
        cmd = commands.get(mode)
        if not cmd:
            return False, f"[BAD_ARGS] unknown git_inspect mode={mode}"
        if target and mode in {"status", "stat", "files", "diff"}:
            cmd = cmd + target_args
        ok, output = self._run_git(ws, cmd, timeout=_bounded_int(args.get("timeout"), 20, 5, 60))
        payload = {
            "schema": "tiangong.codex.git_inspect.v1",
            "mode": mode,
            "root": _safe_rel(root, ws),
            "ok": ok,
            "command": "git " + " ".join(cmd),
            "output": output[:max_chars],
            "truncated": len(output) > max_chars,
        }
        return ok, self._json_tool_output(payload)

    def _handle_failure_parser(self, ws: Path, args: dict) -> tuple[bool, str]:
        text = str(args.get("text") or "")
        if not text and args.get("path"):
            path, error = self._resolve_workspace_path(ws, args.get("path"))
            if error:
                return False, error
            text = _read_text_best_effort(path, _bounded_int(args.get("max_chars"), 20000, 1000, 80000))
        if not text.strip():
            return False, "[BAD_ARGS] failure_parser requires text or path"
        max_items = _bounded_int(args.get("max_items"), 20, 1, 100)
        patterns = [
            ("python_trace", r'File "([^"]+)", line (\d+)(?:, in ([^\n]+))?'),
            ("python_syntax", r"SyntaxError: ([^\n]+)"),
            ("module_missing", r"ModuleNotFoundError: No module named '([^']+)'"),
            ("js_stack", r"\s+at .+?\(([^():]+):(\d+):(\d+)\)"),
            ("ts_error", r"([^()\s]+)\((\d+),(\d+)\): error (TS\d+): ([^\n]+)"),
            ("pytest_fail", r"FAILED\s+([^\s:]+)(?:::[^\s]+)?"),
            ("generic_file_line", r"([A-Za-z]:)?[^:\n]+(?:\.py|\.js|\.ts|\.tsx|\.jsx|\.json|\.mjs|\.cjs):(\d+):(?:(\d+):)?\s*([^\n]+)"),
        ]
        findings: list[dict[str, Any]] = []
        for kind, pattern in patterns:
            for match in re.finditer(pattern, text):
                item = {"kind": kind, "match": match.group(0)[:300], "groups": [g for g in match.groups() if g is not None]}
                findings.append(item)
                if len(findings) >= max_items:
                    break
            if len(findings) >= max_items:
                break
        lowered = text.lower()
        suggestions = []
        if "syntaxerror" in lowered or "unexpected token" in lowered:
            suggestions.append("Run read_file around the reported line, then use replace_lines with expected text and run syntax check again.")
        if "modulenotfounderror" in lowered or "cannot find module" in lowered:
            suggestions.append("Inspect dependency files and import path; avoid editing before confirming package/runtime source.")
        if "timeout" in lowered:
            suggestions.append("Reduce target scope or run a narrower command with timeout.")
        if "permission" in lowered or "access is denied" in lowered:
            suggestions.append("Check workspace boundary and permission mode before retrying.")
        if not suggestions:
            suggestions.append("Use grep/symbol_search to locate the first reported file or symbol, then rerun the smallest quality check.")
        payload = {
            "schema": "tiangong.codex.failure_parser.v1",
            "findings": findings,
            "suggestions": suggestions,
            "first_lines": text.strip().splitlines()[:12],
        }
        return True, self._json_tool_output(payload)

    def _patch_touched_paths(self, patch_text: str, ws: Path) -> tuple[list[Path], str]:
        paths: list[Path] = []
        for line in patch_text.splitlines():
            if not (line.startswith("+++ ") or line.startswith("--- ")):
                continue
            raw = line[4:].strip().split("\t", 1)[0]
            if raw == "/dev/null":
                continue
            if raw.startswith(("a/", "b/")):
                raw = raw[2:]
            if not raw:
                continue
            path, error = self._resolve_workspace_path(ws, raw, must_exist=False)
            if error:
                return [], error
            if path and path not in paths:
                paths.append(path)
        return paths, ""

    def _handle_safe_apply_patch(self, ws: Path, args: dict) -> tuple[bool, str]:
        patch_text = str(args.get("patch") or args.get("diff") or "")
        if not patch_text.strip():
            return False, "[BAD_ARGS] safe_apply_patch requires patch"
        touched, error = self._patch_touched_paths(patch_text, ws)
        if error:
            return False, error
        if not touched:
            return False, "[BAD_PATCH] no touched paths detected in unified diff"
        git = shutil.which("git")
        if not git:
            return False, "[GIT_NOT_FOUND] safe_apply_patch requires git for patch validation"
        reverse = _coerce_bool(args.get("reverse"))
        apply_base = [git, "apply"]
        check_cmd = [*apply_base, "--check", "--whitespace=nowarn"]
        if reverse:
            check_cmd.append("--reverse")
        timeout_sec = _bounded_int(args.get("timeout"), 20, 5, 60)
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        patch_bytes = patch_text.encode("utf-8")

        def _proc_output(proc: subprocess.CompletedProcess) -> str:
            stdout = proc.stdout.decode("utf-8", "replace") if isinstance(proc.stdout, bytes) else str(proc.stdout or "")
            stderr = proc.stderr.decode("utf-8", "replace") if isinstance(proc.stderr, bytes) else str(proc.stderr or "")
            return "\n".join(part for part in (stdout.strip(), stderr.strip()) if part)

        try:
            check = subprocess.run(
                check_cmd,
                input=patch_bytes,
                cwd=str(ws),
                capture_output=True,
                timeout=timeout_sec,
                env=env,
            )
            use_unidiff_zero = False
            if check.returncode != 0:
                retry_cmd = [*check_cmd, "--unidiff-zero"]
                retry = subprocess.run(
                    retry_cmd,
                    input=patch_bytes,
                    cwd=str(ws),
                    capture_output=True,
                    timeout=timeout_sec,
                    env=env,
                )
                if retry.returncode == 0:
                    check = retry
                    use_unidiff_zero = True
        except subprocess.TimeoutExpired:
            return False, "[TIMEOUT] git apply --check timed out"
        if check.returncode != 0:
            return False, f"[PATCH_CHECK_FAILED]\n{_proc_output(check)[:3000]}"
        if _coerce_bool(args.get("dry_run")):
            payload = {
                "schema": "tiangong.codex.safe_apply_patch.v1",
                "status": "dry_run_ok",
                "touched_paths": [_safe_rel(path, ws) for path in touched],
                "path_count": len(touched),
            }
            return True, self._json_tool_output(payload)
        transaction = self._begin_codex_transaction(
            ws,
            "safe_apply_patch",
            touched,
            metadata={
                "reverse": reverse,
                "patch_sha256": hashlib.sha256(patch_text.encode("utf-8")).hexdigest(),
                "path_count": len(touched),
            },
        )
        apply_cmd = [*apply_base, "--whitespace=nowarn"]
        if use_unidiff_zero:
            apply_cmd.append("--unidiff-zero")
        if reverse:
            apply_cmd.append("--reverse")
        try:
            apply_run = subprocess.run(
                apply_cmd,
                input=patch_bytes,
                cwd=str(ws),
                capture_output=True,
                timeout=timeout_sec,
                env=env,
            )
        except subprocess.TimeoutExpired:
            rollback_note = self._rollback_codex_transaction(ws, transaction)
            return False, f"[TIMEOUT] git apply timed out; rollback attempted {rollback_note}"
        if apply_run.returncode != 0:
            rollback_note = self._rollback_codex_transaction(ws, transaction)
            return False, f"[PATCH_APPLY_FAILED_ROLLBACK_ATTEMPTED] {rollback_note}\n{_proc_output(apply_run)[:3000]}"
        try:
            transaction = self._commit_codex_transaction(ws, transaction, touched)
        except Exception as exc:
            rollback_note = self._rollback_codex_transaction(ws, transaction)
            return False, f"[PATCH_COMMIT_FAILED_ROLLBACK_ATTEMPTED] {rollback_note}: {type(exc).__name__}: {exc}"
        ok, stat = self._run_git(ws, ["diff", "--stat", "--", *[_safe_rel(path, ws) for path in touched]], timeout=15)
        payload = {
            "schema": "tiangong.codex.safe_apply_patch.v1",
            "status": "applied",
            "touched_paths": [_safe_rel(path, ws) for path in touched],
            "transaction_id": transaction.get("transaction_id"),
            "rollback_ref": self._transaction_module().rollback_ref(transaction),
            "git_diff_stat": stat if ok else "",
        }
        return True, self._json_tool_output(payload)

    def _handle_diff_guard(self, ws: Path, args: dict) -> tuple[bool, str]:
        try:
            from .codex_tools import diff_guard
        except ImportError:  # pragma: no cover
            from codex_tools import diff_guard  # type: ignore
        try:
            return True, diff_guard.run_text(ws, args)
        except Exception as exc:
            return False, f"[DIFF_GUARD_FAILED] {type(exc).__name__}: {exc}"

    def _handle_evidence_card(self, ws: Path, args: dict) -> tuple[bool, str]:
        try:
            from .codex_tools import evidence_card
        except ImportError:  # pragma: no cover
            from codex_tools import evidence_card  # type: ignore
        try:
            return True, evidence_card.run_text(ws, args)
        except Exception as exc:
            return False, f"[EVIDENCE_CARD_FAILED] {type(exc).__name__}: {exc}"

    def _handle_readback_verifier(self, ws: Path, args: dict) -> tuple[bool, str]:
        try:
            from .codex_tools import readback_verifier
        except ImportError:  # pragma: no cover
            from codex_tools import readback_verifier  # type: ignore
        try:
            return True, readback_verifier.run_text(ws, args)
        except Exception as exc:
            return False, f"[READBACK_VERIFIER_FAILED] {type(exc).__name__}: {exc}"

    def _handle_impact_analyzer(self, ws: Path, args: dict) -> tuple[bool, str]:
        try:
            from .codex_tools import impact_analyzer
        except ImportError:  # pragma: no cover
            from codex_tools import impact_analyzer  # type: ignore
        try:
            return True, impact_analyzer.run_text(ws, args)
        except Exception as exc:
            return False, f"[IMPACT_ANALYZER_FAILED] {type(exc).__name__}: {exc}"

    def _handle_test_selector(self, ws: Path, args: dict) -> tuple[bool, str]:
        try:
            from .codex_tools import impact_analyzer
        except ImportError:  # pragma: no cover
            from codex_tools import impact_analyzer  # type: ignore
        try:
            impact = impact_analyzer.analyze(ws, args)
            payload = {
                "schema": "tiangong.codex.test_selector.v1",
                "files": impact.get("files", [])[:100],
                "suffixes": impact.get("suffixes", []),
                "commands": (impact.get("validation_plan") or {}).get("commands", []),
                "browser_checks": (impact.get("validation_plan") or {}).get("browser_checks", []),
                "direct_test_candidates": (impact.get("validation_plan") or {}).get("direct_test_candidates", []),
                "impact_brief": impact.get("llm_brief", ""),
                "related_files": impact.get("related_files", [])[:20],
                "risk_notes": impact.get("risk_notes", []),
                "advisory_only": True,
            }
            return True, self._json_tool_output(payload)
        except Exception:
            pass
        files_arg = args.get("files")
        files: list[str] = []
        if isinstance(files_arg, str):
            files = [item.strip() for item in re.split(r"[,;\n]", files_arg) if item.strip()]
        elif isinstance(files_arg, list):
            files = [str(item).strip() for item in files_arg if str(item).strip()]
        if not files:
            ok, output = self._run_git(ws, ["diff", "--name-only"], timeout=10)
            if ok:
                files = [line.strip() for line in output.splitlines() if line.strip()]
        suffixes = {Path(file).suffix.lower() for file in files}
        commands: list[dict[str, Any]] = []
        code_quality_args: dict[str, Any] = {"target": ".", "language": "auto"}
        if suffixes.intersection({".py"}):
            commands.append({"tool": "python_quality_runner", "arguments": {"target": "."}, "reason": "Python files changed"})
        if suffixes.intersection({".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json"}):
            script_names = ["smoke", "ensure", "check", "lint"]
            if _coerce_bool(args.get("run_tests")):
                script_names.append("test")
            commands.append({
                "tool": "code_quality_runner",
                "arguments": {"target": ".", "language": "javascript", "project_scripts": ",".join(script_names)},
                "reason": "JS/TS/JSON files changed",
            })
        if suffixes.intersection({".go"}):
            commands.append({"tool": "bash", "arguments": {"command": "go test ./...", "timeout": 120}, "reason": "Go files changed"})
        if suffixes.intersection({".rs"}):
            commands.append({"tool": "bash", "arguments": {"command": "cargo check", "timeout": 120}, "reason": "Rust files changed"})
        if not commands:
            commands.append({"tool": "code_quality_runner", "arguments": code_quality_args, "reason": "Default mixed quality check"})
        payload = {
            "schema": "tiangong.codex.test_selector.v1",
            "files": files[:100],
            "suffixes": sorted(s for s in suffixes if s),
            "commands": commands,
        }
        return True, self._json_tool_output(payload)

    def _handle_symbol_search(self, ws: Path, args: dict) -> tuple[bool, str]:
        query = str(args.get("query") or "").strip()
        if not query:
            return False, "[BAD_ARGS] symbol_search requires query"
        kind = str(args.get("kind") or "any").strip().lower()
        root, error = self._resolve_workspace_path(ws, args.get("path", "."))
        if error:
            return False, error
        if not root:
            return False, "[BAD_PATH] unresolved search root"
        limit = _bounded_int(args.get("limit"), 80, 1, 300)
        escaped = re.escape(query)
        if kind == "definition":
            pattern = rf"^\s*(def|class|async\s+def|function|const|let|var|export\s+(function|class|const)|interface|type)\s+{escaped}\b"
        elif kind == "import":
            pattern = rf"^\s*(from\s+{escaped}\b|import\s+.*{escaped}\b|.*from\s+['\"].*{escaped}.*['\"])"
        elif kind == "reference":
            pattern = rf"\b{escaped}\b"
        else:
            pattern = rf"^\s*(def|class|async\s+def|function|const|let|var|export\s+(function|class|const)|interface|type)\s+{escaped}\b|\b{escaped}\b"
        rg = shutil.which("rg")
        if rg:
            cmd = [rg, "--line-number", "--color", "never", "--max-columns", "220"]
            for skipped in sorted(CODEX_SKIP_DIRS):
                cmd.extend(["--glob", f"!{skipped}/**"])
            cmd.extend(["--regexp", pattern, _safe_rel(root, ws)])
            run = subprocess.run(
                cmd,
                cwd=str(ws),
                capture_output=True,
                text=True,
                timeout=_bounded_int(args.get("timeout"), 15, 3, 60),
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
            )
            if run.returncode not in (0, 1):
                return False, f"[RG_ERROR] exit={run.returncode}\n{(run.stderr or run.stdout)[:1200]}"
            rows = run.stdout.splitlines()[:limit]
        else:
            regex = re.compile(pattern)
            rows = []
            files = root.rglob("*") if root.is_dir() else [root]
            for file in files:
                if len(rows) >= limit:
                    break
                if any(part in CODEX_SKIP_DIRS for part in file.parts):
                    continue
                if file.is_file() and file.suffix.lower() in CODEX_TEXT_SUFFIXES and not _looks_binary(file):
                    text, _encoding = _read_text_with_encoding(file)
                    for line_no, line in enumerate(text.splitlines(), 1):
                        if regex.search(line):
                            rows.append(f"{_safe_rel(file, ws)}:{line_no}: {line.strip()[:220]}")
                            if len(rows) >= limit:
                                break
        payload = {
            "schema": "tiangong.codex.symbol_search.v1",
            "query": query,
            "kind": kind,
            "root": _safe_rel(root, ws),
            "matches": rows,
            "truncated": len(rows) >= limit,
        }
        return True, self._json_tool_output(payload)

    def _handle_dependency_probe(self, ws: Path, args: dict) -> tuple[bool, str]:
        try:
            from .codex_tools import dependency_probe
            from .codex_tools.common import json_output
        except ImportError:  # pragma: no cover - direct import fallback
            from codex_tools import dependency_probe  # type: ignore
            from codex_tools.common import json_output  # type: ignore
        payload = dependency_probe.run(ws, args)
        return True, json_output(payload, limit=16000)

    def _handle_frontend_devserver(self, ws: Path, args: dict) -> tuple[bool, str]:
        action = str(args.get("action") or "plan").strip().lower()
        if self.sandbox_mode == SANDBOX_READ_ONLY and action in {"start", "stop"}:
            return False, f"[PERM_DENIED] read_only sandbox blocks frontend_devserver action={action}"
        try:
            from .codex_tools import frontend_devserver
            from .codex_tools.common import json_output
        except ImportError:  # pragma: no cover
            from codex_tools import frontend_devserver  # type: ignore
            from codex_tools.common import json_output  # type: ignore
        payload = frontend_devserver.run(ws, args)
        ok = bool(payload.get("ok", True)) if action in {"start", "probe"} else True
        return ok, json_output(payload, limit=16000)

    def _handle_browser_verify(self, ws: Path, args: dict) -> tuple[bool, str]:
        try:
            from .codex_tools import browser_verify
            from .codex_tools.common import json_output
        except ImportError:  # pragma: no cover
            from codex_tools import browser_verify  # type: ignore
            from codex_tools.common import json_output  # type: ignore
        payload = browser_verify.run(ws, args)
        return bool(payload.get("ok", False)), json_output(payload, limit=20000)

    def _handle_semantic_index(self, ws: Path, args: dict) -> tuple[bool, str]:
        try:
            from .codex_tools import semantic_index
        except ImportError:  # pragma: no cover
            from codex_tools import semantic_index  # type: ignore
        try:
            return True, semantic_index.index_text(ws, args)
        except Exception as exc:
            return False, f"[SEMANTIC_INDEX_FAILED] {type(exc).__name__}: {exc}"

    def _handle_semantic_lookup(self, ws: Path, args: dict) -> tuple[bool, str]:
        try:
            from .codex_tools import semantic_index
        except ImportError:  # pragma: no cover
            from codex_tools import semantic_index  # type: ignore
        try:
            return True, semantic_index.lookup_text(ws, args)
        except Exception as exc:
            return False, f"[SEMANTIC_LOOKUP_FAILED] {type(exc).__name__}: {exc}"

    def _handle_call_graph(self, ws: Path, args: dict) -> tuple[bool, str]:
        try:
            from .codex_tools import semantic_index
        except ImportError:  # pragma: no cover
            from codex_tools import semantic_index  # type: ignore
        try:
            return True, semantic_index.call_graph_text(ws, args)
        except Exception as exc:
            return False, f"[CALL_GRAPH_FAILED] {type(exc).__name__}: {exc}"

    def _handle_file_ops(self, ws: Path, args: dict) -> tuple[bool, str]:
        action = str(args.get("action") or "").strip().lower()
        if self.sandbox_mode == SANDBOX_READ_ONLY and action != "stat":
            return False, f"[PERM_DENIED] read_only sandbox blocks file_ops action={action or '<missing>'}"
        try:
            from .codex_tools import file_ops
        except ImportError:  # pragma: no cover
            from codex_tools import file_ops  # type: ignore
        try:
            return True, file_ops.run_text(ws, args)
        except Exception as exc:
            return False, f"[FILE_OPS_FAILED] {type(exc).__name__}: {exc}"

    def _handle_rollback_ops(self, ws: Path, args: dict) -> tuple[bool, str]:
        action = str(args.get("action") or "list").strip().lower()
        if self.sandbox_mode == SANDBOX_READ_ONLY and action == "rollback":
            return False, "[PERM_DENIED] read_only sandbox blocks rollback_ops action=rollback"
        try:
            from .codex_tools import transaction_ops
        except ImportError:  # pragma: no cover
            from codex_tools import transaction_ops  # type: ignore
        try:
            return True, transaction_ops.run_text(ws, args)
        except Exception as exc:
            return False, f"[ROLLBACK_OPS_FAILED] {type(exc).__name__}: {exc}"

    def _handle_rollback_preview(self, ws: Path, args: dict) -> tuple[bool, str]:
        try:
            from .codex_tools import rollback_preview
        except ImportError:  # pragma: no cover
            from codex_tools import rollback_preview  # type: ignore
        try:
            return True, rollback_preview.run_text(ws, args)
        except Exception as exc:
            return False, f"[ROLLBACK_PREVIEW_FAILED] {type(exc).__name__}: {exc}"

    def _handle_code_quality(self, ws: Path, args: dict) -> tuple[bool, str]:
        target = args.get("target", ".")
        language = str(args.get("language") or "auto").strip().lower()
        try:
            timeout_sec = int(args.get("timeout") or 60)
        except Exception:
            timeout_sec = 60
        timeout_sec = max(5, min(120, timeout_sec))
        target_path, error = self._resolve_workspace_path(ws, target)
        if error:
            return False, error
        if not target_path:
            return False, "[BAD_PATH] unresolved quality target"

        languages = {language}
        if language in {"", "auto", "mixed", "all"}:
            languages = {"python", "javascript", "json", "typescript", "go", "rust"}

        checked: list[str] = []
        skipped: list[str] = []
        errors: list[str] = []

        def _rel(path: Path) -> str:
            return _safe_rel(path, ws)

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
                        text, _encoding = _read_text_with_encoding(file)
                        json.loads(text)
                    except Exception as e:
                        errors.append(f"json:{_rel(file)}\n{type(e).__name__}: {e}")
                        if len(errors) >= 8:
                            break
            else:
                skipped.append("json: no .json files")

        project_dir = target_path if target_path.is_dir() else target_path.parent

        def _find_upwards(start: Path, filename: str) -> Path | None:
            workspace = ws.resolve(strict=False)
            current = start.resolve(strict=False)
            while True:
                try:
                    current.relative_to(workspace)
                except ValueError:
                    return None
                candidate = current / filename
                if candidate.exists():
                    return candidate
                if current == workspace:
                    return None
                current = current.parent

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

        if {"javascript", "typescript"}.intersection(languages):
            package_json = _find_upwards(project_dir, "package.json")
            npm = shutil.which("npm")
            if not npm:
                skipped.append("npm: npm not found")
            elif not package_json:
                skipped.append("npm: package.json not found")
            else:
                try:
                    package_text, _encoding = _read_text_with_encoding(package_json)
                    package_data = json.loads(package_text)
                    scripts = dict(package_data.get("scripts") or {})
                except Exception as exc:
                    errors.append(f"npm:{_rel(package_json)}\npackage.json parse failed: {type(exc).__name__}: {exc}")
                    scripts = {}
                default_scripts = ["smoke", "ensure", "check", "lint"]
                if _coerce_bool(args.get("run_tests")):
                    default_scripts.append("test")
                requested_scripts = args.get("project_scripts")
                if isinstance(requested_scripts, str):
                    script_names = [item.strip() for item in requested_scripts.split(",") if item.strip()]
                elif isinstance(requested_scripts, list):
                    script_names = [str(item).strip() for item in requested_scripts if str(item).strip()]
                else:
                    script_names = default_scripts
                if not _coerce_bool(args.get("skip_project_scripts")):
                    for script_name in script_names:
                        if script_name not in scripts:
                            continue
                        reason = _dangerous_command_reason(str(scripts.get(script_name) or ""))
                        if reason and self.sandbox_mode != SANDBOX_DANGER_FULL:
                            skipped.append(f"npm:{script_name} skipped: {reason}")
                            continue
                        ok, output = _run([npm, "run", "-s", script_name], package_json.parent)
                        checked.append(f"npm:{script_name}")
                        if not ok:
                            errors.append(f"npm:{script_name}\n{output}")
                            if len(errors) >= 8:
                                break

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

    def _resolve_workspace_path(
        self,
        ws: Path,
        raw_path: Any,
        *,
        must_exist: bool = True,
    ) -> tuple[Path | None, str]:
        raw = str(raw_path if raw_path not in (None, "") else ".").strip()
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = ws / candidate
        try:
            resolved = candidate.resolve(strict=False)
            workspace = ws.resolve(strict=False)
        except OSError as exc:
            return None, f"[BAD_PATH] cannot resolve path={raw}: {exc}"
        if self.sandbox_mode != SANDBOX_DANGER_FULL:
            try:
                resolved.relative_to(workspace)
            except ValueError:
                return None, f"[PATH_OUTSIDE_WORKSPACE] {raw}"
        if must_exist and not resolved.exists():
            return None, f"[FILE_NOT_FOUND] path not found: {raw}"
        return resolved, ""

    def _make_backup(self, target: Path) -> Path:
        digest = _bytes_sha256(target)[:12] if target.exists() and target.is_file() else "new"
        backup = target.with_name(f"{target.name}.{digest}.bak")
        if target.exists():
            shutil.copy2(target, backup)
        return backup

    def _transaction_module(self):
        try:
            from .codex_tools import transaction_ops
        except ImportError:  # pragma: no cover
            from codex_tools import transaction_ops  # type: ignore
        return transaction_ops

    def _begin_codex_transaction(self, ws: Path, action: str, paths: list[Path], metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        transaction_ops = self._transaction_module()
        return transaction_ops.prepare(ws, action, paths, metadata=metadata or {})

    def _commit_codex_transaction(self, ws: Path, transaction: dict[str, Any], paths: list[Path], metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        transaction_ops = self._transaction_module()
        return transaction_ops.commit(ws, transaction, paths=paths, metadata=metadata or {})

    def _rollback_codex_transaction(self, ws: Path, transaction: dict[str, Any] | None) -> str:
        if not transaction:
            return ""
        transaction_ops = self._transaction_module()
        try:
            payload = transaction_ops.rollback(ws, transaction_id=str(transaction.get("transaction_id") or ""), force=True)
            return f"rollback_ref={payload.get('rollback_manifest')}"
        except Exception as exc:
            return f"rollback_failed={type(exc).__name__}: {exc}"

    def _transaction_note(self, transaction: dict[str, Any] | None) -> str:
        if not transaction:
            return ""
        transaction_ops = self._transaction_module()
        tx_id = transaction.get("transaction_id") or ""
        ref = transaction_ops.rollback_ref(transaction)
        return f" transaction_id={tx_id} rollback_ref={ref} next=readback_then_impact_or_validate" if tx_id else ""

    def _diff_text(self, old_text: str, new_text: str, path: str, limit: int = 2200) -> str:
        diff = "\n".join(difflib.unified_diff(
            old_text.splitlines(),
            new_text.splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        ))
        return diff[:limit] if diff else "(no content change)"

    def _sandbox_allow(self, tool_name: str, args: dict, ws: Path) -> bool:
        """沙箱裁决：read_only 拒绝写操作，workspace_write 防路径越界。"""
        if self.sandbox_mode == SANDBOX_DANGER_FULL:
            return True
        if tool_name == "bash" and _dangerous_command_reason(str(args.get("command") or "")):
            return False
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
        target, error = self._resolve_workspace_path(ws, path)
        if error:
            return False, error
        if not target or target.is_dir():
            return False, f"[IS_DIRECTORY] read_file expects a file: {path}"
        if _looks_binary(target):
            return False, f"[BINARY_FILE] refusing to read binary file: {path}"
        try:
            content, encoding = _read_text_with_encoding(target)
            lines = content.splitlines()
            start_line = _bounded_int(args.get("start_line") or args.get("line") or 1, 1, max(1, len(lines)), 1)
            max_lines = _bounded_int(args.get("max_lines"), 260, 1, 1000)
            max_chars = _bounded_int(args.get("max_chars"), 12000, 1000, 30000)
            selected = lines[start_line - 1:start_line - 1 + max_lines]
            numbered = "\n".join(f"{i}|{line}" for i, line in enumerate(selected, start_line))
            truncated = len(lines) > start_line - 1 + len(selected)
            header = (
                f"path={_safe_rel(target, ws)} encoding={encoding} "
                f"lines={len(lines)} showing={start_line}-{start_line + len(selected) - 1} "
                f"sha256={_bytes_sha256(target)[:16]}"
            )
            if truncated:
                header += " truncated=true"
            body = numbered[:max_chars]
            if len(numbered) > max_chars:
                body += "\n...[truncated by max_chars]"
            return True, f"{header}\n{body}"
        except Exception as e:
            return False, f"读取失败: {e}"

    def _handle_list_dir(self, ws: Path, args: dict) -> tuple[bool, str]:
        """列出目录内容。"""
        path = args.get("path", ".")
        target, error = self._resolve_workspace_path(ws, path)
        if error:
            return False, error
        if not target or not target.is_dir():
            return False, f"[NOT_DIRECTORY] list_dir expects a directory: {path}"
        try:
            limit = _bounded_int(args.get("limit"), 80, 1, 300)
            items = sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
            rows = [
                f"{'[D]' if item.is_dir() else '[F]'} {item.name}"
                for item in items[:limit]
            ]
            header = f"path={_safe_rel(target, ws)} entries={len(items)} showing={len(rows)}"
            if len(items) > limit:
                header += " truncated=true"
            return True, header + ("\n" + "\n".join(rows) if rows else "\n(empty)")
        except Exception as e:
            return False, f"列目录失败: {e}"

    def _handle_write_file(self, ws: Path, args: dict) -> tuple[bool, str]:
        """创建/覆写文件。"""
        path = args.get("path", "")
        content = args.get("content")
        if not path or content is None:
            return False, "[BAD_ARGS] 缺少 path 或 content 参数"
        target, error = self._resolve_workspace_path(ws, path, must_exist=False)
        if error:
            return False, error
        if not target:
            return False, "[BAD_PATH] unresolved target"
        content = str(content)
        if target.exists() and target.is_dir():
            return False, f"[IS_DIRECTORY] write_file expects a file: {path}"
        old_text = ""
        if target.exists():
            old_text, _encoding = _read_text_with_encoding(target)
        transaction = self._begin_codex_transaction(
            ws,
            "write_file",
            [target],
            metadata={"path": _safe_rel(target, ws), "content_sha256": _text_sha256(content)},
        )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            readback, _encoding = _read_text_with_encoding(target)
            if readback != content:
                rollback_note = self._rollback_codex_transaction(ws, transaction)
                return False, f"[READBACK_MISMATCH] write verification failed: {path}; rollback attempted {rollback_note}"
            transaction = self._commit_codex_transaction(ws, transaction, [target])
            diff = self._diff_text(old_text, content, str(path)) if old_text else "(new file)"
            transaction_note = self._transaction_note(transaction)
            return True, (
                f"write_file ok path={_safe_rel(target, ws)} lines={len(content.splitlines())} "
                f"chars={len(content)} sha256={_text_sha256(content)[:16]}{transaction_note}\n--- diff ---\n{diff}"
            )
        except Exception as e:
            rollback_note = self._rollback_codex_transaction(ws, transaction)
            return False, f"write_file failed and rollback attempted {rollback_note}: {type(e).__name__}: {e}"

    def _handle_glob(self, ws: Path, args: dict) -> tuple[bool, str]:
        """按通配符找文件。"""
        pattern = args.get("pattern", "*")
        target_dir = args.get("path", ".")
        root, error = self._resolve_workspace_path(ws, target_dir)
        if error:
            return False, error
        if not root or not root.is_dir():
            return False, f"[NOT_DIRECTORY] glob expects a directory: {target_dir}"
        try:
            limit = _bounded_int(args.get("limit"), 160, 1, 500)
            matches = sorted(root.glob(pattern))
            result = []
            for m in matches:
                parts = set(m.relative_to(root).parts[:-1]) if m != root else set()
                if parts.intersection(CODEX_SKIP_DIRS):
                    continue
                rel = _safe_rel(m, ws)
                result.append(f"{'[D]' if m.is_dir() else '[F]'} {rel}")
                if len(result) >= limit:
                    break
            header = f"pattern={pattern} root={_safe_rel(root, ws)} matches={len(result)}"
            if len(result) >= limit:
                header += " truncated=true"
            return True, header + ("\n" + "\n".join(result) if result else "\n(no matches)")
        except Exception as e:
            return False, f"glob失败: {e}"

    def _handle_grep(self, ws: Path, args: dict) -> tuple[bool, str]:
        """搜索文件内容。"""
        pattern = args.get("pattern", "")
        search_path = args.get("path", ".")
        if not pattern:
            return False, "[BAD_ARGS] 缺少 pattern 参数"
        root, error = self._resolve_workspace_path(ws, search_path)
        if error:
            return False, error
        if not root:
            return False, "[BAD_PATH] unresolved search root"
        try:
            import re as _re
            regex = _re.compile(pattern)
            limit = _bounded_int(args.get("limit"), 120, 1, 500)
            rg = shutil.which("rg")
            if rg:
                root_arg = _safe_rel(root, ws) if self.sandbox_mode != SANDBOX_DANGER_FULL else str(root)
                cmd = [
                    rg,
                    "--line-number",
                    "--color",
                    "never",
                    "--max-columns",
                    "220",
                    "--glob",
                    "!resources/backend_runtime/**",
                ]
                for skipped in sorted(CODEX_SKIP_DIRS):
                    cmd.extend(["--glob", f"!{skipped}/**"])
                cmd.extend(["--regexp", pattern, root_arg])
                run = subprocess.run(
                    cmd,
                    cwd=str(ws),
                    capture_output=True,
                    text=True,
                    timeout=_bounded_int(args.get("timeout"), 15, 3, 60),
                    env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
                )
                if run.returncode == 0:
                    rows = run.stdout.splitlines()[:limit]
                    header = f"grep engine=rg pattern={pattern} root={root_arg} matches={len(rows)}"
                    if len(run.stdout.splitlines()) > limit:
                        header += " truncated=true"
                    return True, header + "\n" + "\n".join(rows)
                if run.returncode not in (0, 1):
                    return False, f"[RG_ERROR] exit={run.returncode}\n{(run.stderr or run.stdout)[:1200]}"
            results = []
            files = root.rglob("*") if root.is_dir() else [root]
            for f in files:
                if any(part in CODEX_SKIP_DIRS for part in f.parts):
                    continue
                if f.is_file() and f.suffix.lower() in CODEX_TEXT_SUFFIXES and not _looks_binary(f):
                    try:
                        text, _encoding = _read_text_with_encoding(f)
                        for i, line in enumerate(text.splitlines(), 1):
                            if regex.search(line):
                                rel = _safe_rel(f, ws)
                                results.append(f"{rel}:{i}: {line.strip()[:120]}")
                                if len(results) >= limit:
                                    break
                    except Exception:
                        pass
                    if len(results) >= limit:
                        break
            header = f"grep engine=python pattern={pattern} root={_safe_rel(root, ws)} matches={len(results)}"
            if len(results) >= limit:
                header += " truncated=true"
            return True, header + ("\n" + "\n".join(results) if results else "\n(no matches)")
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
        dangerous_reason = _dangerous_command_reason(str(command))
        if dangerous_reason and self.sandbox_mode != SANDBOX_DANGER_FULL:
            return False, f"[A5_BLOCKED] {dangerous_reason}. Use a governed workspace file tool instead."
        if not isinstance(timeout_sec, (int, float)) or timeout_sec < 1:
            timeout_sec = 30
        if timeout_sec > 120:
            timeout_sec = 120  # 硬上限

        try:
            env = os.environ.copy()
            python_dir = str(Path(sys.executable).parent)
            env["PATH"] = python_dir + os.pathsep + env.get("PATH", "")
            env.setdefault("PYTHONIOENCODING", "utf-8")
            env.setdefault("PYTHONUTF8", "1")
            shell_tokens = ("|", "&&", "||", ">", "<", " 2>", " 1>", " & ")
            cd_python = _maybe_cd_python_wrapper(command, ws) if os.name == "nt" else None
            normalized_args = _normalize_windows_command_args(command) if cd_python is None else []
            is_bash_command = bool(normalized_args and Path(normalized_args[0]).name.lower() in {"bash", "bash.exe", "sh", "sh.exe"})
            use_shell = os.name == "nt" and cd_python is None and not is_bash_command and any(token in command for token in shell_tokens)
            if not use_shell:
                if cd_python is not None:
                    args, run_cwd = cd_python
                else:
                    args = normalized_args
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
            mode = "shell" if use_shell else "argv"
            summary = f"exit={exit_code} mode={mode} cwd={_safe_rel(Path(run_cwd or ws) if not use_shell else ws, ws)} timeout={timeout_sec}s"
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
        try:
            target_path, error = self._resolve_workspace_path(ws, target)
            if error:
                return False, error
            if not target_path:
                return False, "[BAD_PATH] unresolved quality target"
            result = subprocess.run(
                [sys.executable, "-B", "-m", "compileall", "-q", str(target_path)],
                cwd=str(ws),
                capture_output=True,
                text=True,
                timeout=_bounded_int(args.get("timeout"), 30, 5, 120),
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
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

        allow_write = _coerce_bool(args.get("allow_write"))
        subtask_text = str(subtask)
        if not allow_write:
            subtask_text = (
                "[Subtask boundary] Read-only evidence scout. Inspect, search, summarize findings, "
                "and do not modify workspace files. Main Code-X will apply any patch.\n\n"
                + subtask_text
            )

        # 创建子智能体（共享密钥；默认只读，避免并行写冲突）
        sub_agent = LLMDrivenCodeX(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            sandbox_mode=self.sandbox_mode if allow_write else SANDBOX_READ_ONLY,
            provider=self.provider,
            thinking_enabled=self.thinking_enabled,
            thinking_depth=self.thinking_depth,
        )
        sub_agent._zi_zhinengti = True  # 标记为子智能体，抑制 TASK 输出
        result = sub_agent.run(task=subtask_text, workspace=ws, max_turns=max_turns)
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

        target, error = self._resolve_workspace_path(ws, path)
        if error:
            return False, error
        if not target or target.is_dir():
            return False, f"[IS_DIRECTORY] replace_lines expects a file: {path}"
        if not isinstance(edits, list):
            return False, "[BAD_ARGS] edits must be a list"

        old_text, encoding = _read_text_with_encoding(target)
        lines = old_text.splitlines(keepends=True)
        try:
            edits_sorted = sorted(edits, key=lambda e: int(e.get("line", 0) or 0), reverse=True)
        except Exception:
            return False, "[BAD_ARGS] every edit must include numeric line"
        n_total = len(edits_sorted)
        planned = list(lines)

        for idx, edit in enumerate(edits_sorted):
            if not isinstance(edit, dict):
                return False, f"[BAD_ARGS] edit #{idx + 1} must be an object"
            line_no = int(edit.get("line", 0) or 0) - 1
            if line_no < 0 or line_no >= len(planned):
                return False, f"[LINE_RANGE] edit {idx + 1}/{n_total} line={edit.get('line')} out of range; total_lines={len(lines)}; nothing written"
            expected = edit.get("expected", edit.get("old_text"))
            if expected is not None:
                current = planned[line_no].rstrip("\r\n")
                if current != str(expected).rstrip("\r\n"):
                    return False, (
                        f"[CONTEXT_MISMATCH] line={edit.get('line')} expected={str(expected)[:160]!r} "
                        f"actual={current[:160]!r}; nothing written"
                    )
            new_text = str(edit.get("new_text", ""))
            if not new_text.endswith("\n"):
                new_text += "\n"
            planned[line_no:line_no + 1] = new_text.splitlines(keepends=True)

        new_text_all = "".join(planned)
        diff = self._diff_text(old_text, new_text_all, str(path))
        if _coerce_bool(args.get("dry_run")):
            return True, f"replace_lines dry_run ok path={_safe_rel(target, ws)} edits={len(edits)} encoding={encoding}\n--- diff ---\n{diff}"
        transaction = self._begin_codex_transaction(
            ws,
            "replace_lines",
            [target],
            metadata={"path": _safe_rel(target, ws), "edit_count": len(edits), "content_sha256": _text_sha256(new_text_all)},
        )
        try:
            target.write_text(new_text_all, encoding="utf-8")
            readback, _encoding = _read_text_with_encoding(target)
            if readback != new_text_all:
                rollback_note = self._rollback_codex_transaction(ws, transaction)
                return False, f"[READBACK_MISMATCH] replace verification failed; rollback attempted {rollback_note}"
            transaction = self._commit_codex_transaction(ws, transaction, [target])
            transaction_note = self._transaction_note(transaction)
            return True, (
                f"replace_lines ok path={_safe_rel(target, ws)} edits={len(edits)} "
                f"sha256={_text_sha256(new_text_all)[:16]}{transaction_note}\n--- diff ---\n{diff}"
            )
        except Exception as exc:
            rollback_note = self._rollback_codex_transaction(ws, transaction)
            return False, f"replace_lines failed and rollback attempted {rollback_note}: {type(exc).__name__}: {exc}"

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
