# 天工造物 v2 架构知识库

## 1. 项目概述

天工造物 v2.0 - 临渊者 (L6.72.x)，LLM 自主驱动的智能体系统。

核心目录：`~/桌面/开发版/backend/project/tiangong_agent_runtime/`
数据目录：`~/.tiangong/`

## 2. 系统架构 (L0-L6)

```
L0 原语层 (l0_primitives)：基础数据结构
L1 端口层 (l1_ports)：外部接口定义
L2 状态层 (l2_state)：状态管理
L3 编排层 (l3_orchestration)：决策编排
L4 执行层 (l4_execution)：工具执行
L5 插件宿主 (l5_plugin_host)：插件管理
L6 插件/技能 (l6_plugins)：各类插件
```

## 3. 核心子系统

### 3.1 三选一路由 (runtime_entry.py)
- 聊天路径 → model_chat_adapter
- 代码系统路径 → CodeXSubsystem (llm_codex.py)
- 文件执行路径 → PlanBridge + ExecutionSpine

### 3.2 自由意志系统
- free_will_suiji_yinqing.py：随机动作引擎
- free_will_learning_chain.py：自学链
- self_learning_route.py：自学路由
- self_iteration_route.py：自迭代路由

### 3.3 自愈系统 (zhiyu_xitong/)
- zhiyu_zhishiku.md：本文档
- gengxin_rizhi.py/jsonl：更新日志
- zhiyu_yinqing.py：自愈引擎（诊断+方案）
- 触发条件：系统级异常（OOM/crash/fd耗尽/启动失败）
- 不触发：任务级小失败

### 3.4 记忆系统
- memory_store_bridge.py：JSONL持久化
- memory_recall_router.py：召回路由
- memory_math_core.py：遗忘算法（DecayKernel）
- 遗忘执行：_execute_forgetting_actions()
- 召回注入：_build_memory_context()

### 3.5 生命周期系统
- lifecycle_coordinator.py：生命周期协调器
- run_idle_heartbeat()：凌晨心跳（LLM判定P4/P5）
- run_xuexi_xintiao.py：心跳脚本入口

### 3.6 迭代池
- diedai_chi.py：迭代项管理
- self_iteration_frontend_projection.py：前端投影

### 3.7 CodeX 代码系统
- subsystems/codex_subsystem.py：LLM自主编程执行循环

## 4. 关键文件清单（922文件）

核心入口：
- runtime_entry.py：主入口 RuntimeEntry
- run_agent.py：CLI入口

适配器 (adapters/)：
- model_chat_adapter.py：LLM聊天适配器
- diagnose_project_adapter.py：项目诊断
- document_*_adapter.py：文档操作系列
- python_test_adapter.py：Python语法测试
- zip_package_adapter.py：打包

## 5. 数据流

```
用户输入 → cli_loop 判定 → 四路径路由 →
  ├─ 聊天 → model_chat_adapter → LLM回应
  ├─ 代码系统 → CodeXSubsystem → LLM自主编程执行循环
  └─ 文件执行 → PlanBridge → ExecutionSpine → 工具执行
→ 结果标准化 → 经验提取 → 记忆持久化 → 返回用户
```

## 6. 自愈触发判定

系统级异常关键词：
- OOM / out of memory / 内存不足
- file descriptor / fd / too many open files
- segfault / core dump
- 启动失败 / import error / ModuleNotFound
- connection refused / 端口
- gateway / 网关
- crash / 崩溃 / 退出 / exit
- signal / SIGKILL / SIGTERM
- limit / ulimit

## 7. 模型配置

- 模型：deepseek-v4-pro
- base_url：https://api.deepseek.com/v1
- API Key：环境变量 DEEPSEEK_API_KEY
- OpenAI客户端：openai.OpenAI(timeout=Timeout(connect=8, read=120, write=60, pool=8))

## 8. 数据持久化

- jiyi_v2.db：记忆SQLite数据库
- huiyu.db：会话数据库
- freewill_goals.db：自由意志目标数据库
- codex_state.db：CodeX状态数据库

## 9. 回滚要点

- 修改 runtime_entry.py 前备份 .bak-时间戳
- zhiyu_xitong/ 目录可整体删除回滚自愈系统
- 记忆系统三个接入点分别在 L854/L890/L905
- 生命周期心跳在 L566
