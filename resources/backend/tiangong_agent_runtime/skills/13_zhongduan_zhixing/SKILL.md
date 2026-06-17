# 终端执行

## 1. 定位
终端执行是临渊者真实执行力的关键接口，面向受审计测试、编译、诊断、Runtime对齐、执行报告。不等同于无限制shell；只在可用工具范围内执行，不存在裸shell时不得伪装执行。

## 2. 触发条件
- 用户要求运行测试、语法检查、质量检查
- 用户要求验证修复是否生效
- 用户要求检查 Runtime 工具是否对齐
- 用户要求执行 operational drill
- 代码诊断需要运行命令验证
- 项目收口质检需要执行检查
- 打包后需要 smoke 验证

## 3. 反触发条件
- 只是读文件 → 文件管理
- 只是分析代码不运行 → 代码诊断
- 只是解析文档 → 文档处理
- 只是生成 zip → 项目打包
- 高风险动作（删除、格式化、权限提升、全盘扫描、安装未知依赖）→ 必须走闸门或拒绝执行
- 不存在的通用 shell 命令 → 不得伪装执行

## 4. 输入契约
- 项目根目录
- 要运行的检查类型（语法/测试/对齐/drill）
- 目标文件/模块（如适用）
- 是否允许耗时测试
- 成功判定标准
- 超时/失败时的降级策略

## 5. 输出契约
每次执行后必须输出：
- 执行工具名称
- 执行目的
- 输入范围
- returncode / status
- stdout/stderr 摘要（关键部分完整保留，不截断错误）
- 错误分类
- 是否通过
- 下一步建议（继续/转代码诊断/转收口质检）

## 6. 工具选择矩阵

**run_python_quality_check**
- 用途：Python 语法、导入、pytest 或质量检查
- 适用：Python 后端、工具模块、修复验证
- 失败恢复：把错误文件、行号、traceback 转给代码诊断

**run_python_tests**
- 用途：在受控工作区执行 pytest 测试
- 适用：代码修复后回归验证、项目质量检查
- 失败恢复：失败测试名和 traceback 转代码诊断

**runtime_tool_alignment_check**
- 用途：Runtime 工具注册、Skill 引用、工具契约对齐检查
- 适用：工具调用失败、Skill 升级后、Provider/Runtime 对接检查
- 失败恢复：输出缺失工具、重复工具、签名不一致清单

**runtime_llm_operational_drill**
- 用途：LLM 与 Runtime 的真实操作演练
- 适用：工作模式、工具调用链、长链稳定性验证
- 失败恢复：输出失败阶段、失败工具、可续接上下文

**return_analysis**
- 用途：汇总执行报告
- 适用：所有执行后收口

## 7. 标准执行流程

**阶段 A：执行前预检**
- 明确执行目的和范围
- 确认工作目录
- 确认工具是否存在且可用
- 判断是否有高风险动作（拒绝或请求确认）

**阶段 B：选择最小执行工具**
- Python 项目 → run_python_quality_check
- 工具/Skill 对齐 → runtime_tool_alignment_check
- 工作模式链路验证 → runtime_llm_operational_drill

**阶段 C：执行与观察**
- 获取 status / stdout / stderr
- 不截断关键错误信息
- 提取错误类型、文件、行号、模块名

**阶段 D：失败分类**
至少支持以下分类：
- syntax_error、import_error、dependency_missing
- path_not_found、permission_denied、timeout
- test_failed、provider_error
- tool_contract_mismatch、runtime_alignment_error
- unknown_error

**阶段 E：反馈转交**
- 代码错误 → 04_daima_zhenduan
- 工具注册错误 → 04_daima_zhenduan + Runtime 对齐检查
- 交付验收错误 → 15_xiangmu_shoukou_zhijian

**阶段 F：执行报告**
- 输出是否通过
- 输出证据摘要
- 输出下一步建议

## 8. 风险控制
- 禁止执行删除、格式化磁盘、权限提升、全盘扫描、安装未知依赖
- 不存在通用 shell 工具时，不得声称已运行 shell 命令
- 所有执行必须在可审计工具中进行
- 执行失败后不盲目重试超过 3 次

## 9. 证据链规则
- 每次执行必须有工具返回的原始输出作为证据
- "执行成功"结论必须有 status/returncode 佐证
- "执行失败"结论必须有 stderr/traceback 片段
- 错误分类必须引用实际错误信息，不得猜测
- 转交其他 Skill 时必须携带完整 stdout/stderr 摘要

## 10. 失败恢复
- 工具不可用：输出不可用，不伪执行
- 路径错误：转 02_wenjian_guanli 确认路径
- 测试失败：转 04_daima_zhenduan 定位根因
- 超时：缩小检查范围（单文件/单模块）
- 依赖缺失：输出缺失依赖清单，不自动安装（除非用户明确授权）
- 输出过长：保留关键 traceback 和失败摘要，截断重复部分

## 11. 质量门
- 通过：执行成功，输出可解释，结果符合成功标准
- 部分通过：部分检查通过，但存在可定位失败
- 不通过：工具不可用、执行失败且无法定位、无证据声称通过

## 12. 协作规则
- 路径确认 → 02_wenjian_guanli
- 错误根因分析 → 04_daima_zhenduan
- 发布检查 → 15_xiangmu_shoukou_zhijian
- 打包前 smoke → 05_xiangmu_dabao + 15_xiangmu_shoukou_zhijian

## 13. 验收用例
1. Python 语法检查通过：run_python_quality_check 全绿
2. Python ImportError 失败：正确分类为 import_error 并转代码诊断
3. Runtime 工具对齐检查：发现缺失工具并列出清单
4. operational drill 失败：输出失败阶段和失败工具
5. 用户要求执行不存在的 shell 命令：拒绝并说明原因
6. 测试输出过长：正确摘要关键错误
7. 执行失败后转交代码诊断：携带完整 traceback
