# 多媒体工程编排

面向图文视频音频一体化内容工厂、课程素材、营销素材、批量短视频和交付包场景的技能。目标：把多模态任务拆成可执行流水线和交付清单。

## 什么时候用
- 用户说“批量做素材、做一套视频课程、图文视频一起做、内容工厂、素材流水线、多媒体交付包”时使用
- 任务同时涉及图片、视频、音频、字幕、脚本、导出和打包时使用

## 反触发条件
- 单一图片识别、单一视频剪辑、单一音频转写应转交对应专门技能
- 没有素材或目标时先澄清，不直接生成大流水线
- 不直接绕过各单项工具的限制

## 标准流程
1. 拆分任务目标、平台规格、素材来源和交付格式
2. 调用 multimedia_pipeline_plan 生成流程图与阶段计划
3. 调用 multimedia_asset_manifest 生成素材清单
4. 批量任务调用 multimedia_batch_plan
5. 最终调用 multimedia_delivery_package 生成交付包计划或清单

## 工具
- `multimedia_pipeline_plan`
- `multimedia_asset_manifest`
- `multimedia_batch_plan`
- `multimedia_delivery_package`
