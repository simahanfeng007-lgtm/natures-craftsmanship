# Code-X 三层规划 → 前端对接日志

## 修改日期
2026-06-16

## 涉及文件

| 文件 | 改动 |
|------|------|
| `tiangong_agent_runtime/llm_codex.py` | ①三层规划提示词替换为结构化框架模板 ②`guihua_huidiao`回调参数 |
| `tiangong_agent_shell/cli_loop.py` | ①`STREAM_EVENT_PREFIX`常量恢复 ②`_codex_zhixing`接入两个回调 |

---

## 一、三层规划框架（已落地）

```
L1 项目定义书（宏观概念框架）
  ├── 系统定位（名称/描述/用户/边界）
  ├── 功能全景（核心功能+非功能约束5维度）
  ├── 技术决策（语言/依赖/运行时）
  └── 顶层模块划分（模块清单+数据流+依赖关系）

L2 结构架构卡（6种模式可选）
  ├── 架构模式选择（分层/MVC/管道/六边形/事件驱动/单体脚本）
  ├── 文件清单（每个文件+职责）
  ├── 逻辑关系图（4种：调用链/数据流/组合/时序依赖）
  └── 关键接口契约（跨文件函数签名）

L3 详细步骤（6子步骤模板）
  每步含：①写入前检查→②备份→③执行写入→④读回验证→⑤语法检查→⑥汇报
```

---

## 二、流事件协议（前端需对接）

传输方式：Python stdout → Electron `main.js` 解析 `__TIANGONG_STREAM_EVENT__ ` 前缀 → IPC `runtime:run-step`

### 规划阶段事件（Code-X专属，新增）

| step_id | status | 携带额外字段 | 说明 |
|---------|--------|------------|------|
| `codex_plan_macro` | running | — | L1 项目定义书开始生成 |
| `codex_plan_macro` | done | `plan_content`: 项目定义书全文 | L1 完成，附带完整规划文本 |
| `codex_plan_structure` | running | — | L2 结构架构卡开始生成 |
| `codex_plan_structure` | done | `plan_content`: 架构卡全文 | L2 完成，含逻辑关系图 |
| `codex_plan_detail` | running | — | L3 详细步骤开始生成 |
| `codex_plan_detail` | done | `plan_content`: 详细步骤全文 | L3 完成，含6子步骤 |

### 执行阶段事件

| step_id | status | 携带额外字段 | 说明 |
|---------|--------|------------|------|
| `codex_step_1` | done | `tool_name`, `step_index` | 第1步工具调用成功 |
| `codex_step_1` | failed | `tool_name`, `step_index` | 第1步工具调用失败 |
| `codex_step_N` | done/failed | 同上 | 第N步 |

### 通用字段（所有事件都有）

```json
{
  "schema": "tiangong.desktop.stream_event.v1",
  "type": "step",
  "request_id": "请求ID",
  "step_id": "上述step_id",
  "title": "中文标题",
  "status": "running|done|failed",
  "summary": "简短描述(≤80字)",
  "ts": 1234567890.123,
  "plan_content": "仅done时有，规划全文(≤2000字)",
  "tool_name": "仅执行步骤有",
  "step_index": "仅执行步骤有"
}
```

---

## 三、前端渲染建议

### 规划阶段 UI

当收到 `codex_plan_*` 的 running 事件时：
- 在聊天区或工作区显示一个"规划中"的卡片/面板
- running → 显示loading动画
- done → 展开显示 `plan_content` 全文

三个规划层可以做成手风琴/折叠面板：
```
📐 项目定义书 ▾
  [L1 规划全文...]

📁 结构架构卡 ▾
  [L2 规划全文...]

📝 详细步骤 ▾
  [L3 规划全文...]
```

### 执行阶段 UI

收到 `codex_step_N` 事件时：
- 显示步骤进度条：`✅ write_file → ✅ read_file → 🔄 python_quality_runner → ⬜ 完成`
- 失败时红色标记

---

## 四、测试验证

```bash
# 后端冒烟（确认流事件正常发射）
cd resources/backend/tiangong_agent_runtime
TIANGONG_STREAM_EVENTS=1 python3 -c "
from llm_codex import LLMDrivenCodeX
import tempfile, os
ws = tempfile.mkdtemp()
codex = LLMDrivenCodeX(api_key='...', base_url='https://api.deepseek.com', model='deepseek-chat')
calls = []
def cb(layer, content, status):
    calls.append(f'{layer}:{status}')
result = codex.run(task='创建hello.py打印Hello', workspace=ws, max_turns=8, guihua_huidiao=cb)
# 期望：6次回调 macro:running→done, structure:running→done, detail:running→done
print(calls)
"
```

实测结果（2026-06-16）：
- 规划回调：6次 ✅
- 步骤回调：3次（write_file→read_file→python_quality_runner）✅
- 文件正确落盘 ✅
