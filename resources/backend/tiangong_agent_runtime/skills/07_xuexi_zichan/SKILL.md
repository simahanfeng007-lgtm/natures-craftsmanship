# 学习资产管理

创建/验证/审核/发布学习资产，管理资产契约与沙箱对齐

## 内容生产依赖

学习内容需要公开资料、实时信息、网页材料或事实核验时，先用 `web_search` 获取来源；必要时对关键 URL 使用 `web_readability_extract` 读取正文，再进入学习资产队列和激活流程。

主动学习默认只做资料搜索、知识萃取、学习卡生成、经验沉淀和候选排队；只有用户明确要求“激活、发布、注册为工具/Skill、应用候选”时，才允许进入 activation/build/drill 类写入流程。

当用户明确说“去学一下/学习一下/研究一下/掌握一下/生成学习卡”时，应把本轮学习目标整理成学习卡，由自主学习队列继续执行、质检和归类；不要把普通聊天后处理当作学习卡来源。

## 工具

- `web_search`
- `web_readability_extract`
- `learning_asset_activation_apply`
- `learning_asset_activation_guide`
- `learning_asset_activation_smoke`
- `learning_asset_activation_status`
- `learning_asset_adapter_drill`
- `learning_asset_adapter_guide`
- `learning_asset_adapter_template_list`
- `learning_asset_adapter_template_normalize`
- `learning_asset_adapter_template_smoke`
- `learning_asset_adapter_template_validate`
- `learning_asset_candidate_sandbox_build`
- `learning_asset_candidate_sandbox_guide`
- `learning_asset_candidate_sandbox_review`
- `learning_asset_candidate_sandbox_validate`
- `learning_asset_contract_guide`
- `learning_asset_contract_normalize`
- `learning_asset_contract_validate`
- `learning_asset_release_gate_check`
- `learning_asset_release_gate_guide`
- `learning_asset_sandbox_align`
- `learning_asset_sandbox_guide`
- `learning_asset_sandbox_validate`
- `queue_skill_candidates`
- `queue_tool_production_requests`
