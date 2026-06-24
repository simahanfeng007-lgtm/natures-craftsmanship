# 产品化打包提示：Electron 前端到 Code-X 链路

结论：前端输入到后端 `run_agent.py`，再到 Code-X 代码修复的链路可以产品化；但不能依赖用户机器已有的 Python、Python 包或 site-packages。当前开发环境里出现过 `No module named openai`，根因是 Electron 以 `python -S` 启动后端时隔离了 Python site 初始化，而依赖实际安装在系统 site-packages 中。

## 必须满足

- 安装包必须内置 Python 运行时，Electron 优先调用包内 Python，不依赖用户系统 `python`。
- 安装包必须内置后端依赖目录，至少覆盖 Code-X 和模型适配所需依赖，例如 `openai`、HTTP 客户端、测试执行依赖等。
- 安装包必须内置后端入口和源码，并确认资源路径与 Electron 运行时一致。当前工程根目录入口是 `run_agent.py`，打包后映射为 `resources/backend/run_agent.py`。
- 启动前必须做自检：`run_agent.py` 存在、包内 Python 存在、关键依赖可导入、`run_agent.py --status` 可运行。
- 发布验收必须包含真实前端输入链路：textarea 输入 -> Electron IPC -> 后端 spawn -> Code-X 修复 -> 文件写回 -> 测试通过 -> 前端中文结果显示。
- 安装包不得包含本机真实 `config/model_config.json`；只能包含 `config/model_config.example.json`。用户密钥必须走前端设置或用户数据目录。

## 不允许

- 不允许把“开发机能跑”当作产品可发布依据。
- 不允许最终安装包依赖用户自行安装 Python、`openai`、`pytest` 或其他后端依赖。
- 不允许只跑 `run_agent.py --once` 作为桌面端验收；它只能证明后端，不能证明 Electron 前端链路。
- 不允许在启动后用户第一次执行任务时才暴露依赖缺失；必须在启动自检或设置页状态中提前给出中文错误。

## 当前开发兜底

Electron 主进程已在开发模式下把系统 site-packages 和用户 site-packages 显式加入 `PYTHONPATH`，用于解决 `python -S` 下找不到 `openai` 的问题。这只是开发/工程包兜底，不是最终产品化方案。正式安装包仍应采用“内置 Python + 内置依赖 + 固定资源路径 + 启动自检”。

## 准备和自检入口

```bash
python installer/runtime/prepare_embedded_python_runtime.py --python-dir <prepared-python-dir> --install-deps
python installer/runtime/embedded_runtime_self_check.py
python installer/startup/startup_self_check_l669.py
```

Electron 打包资源规则：

- `resources/backend`：后端入口、后端源码、示例配置和运行所需资源。
- `resources/backend_runtime`：内置 Python 与内置依赖。
- `config/model_config.json`：明确排除，避免把本机 API Key 打入安装包。

## 发布判定

只有以下结果全部通过，才允许进入正式安装包候选：

- `SignalKind=resource`
- `HealthState=healthy`
- `CoreResult.status=ok`
- Electron 前端真实输入 Code-X 修复链路通过
- 目标系统原生安装包验收通过
