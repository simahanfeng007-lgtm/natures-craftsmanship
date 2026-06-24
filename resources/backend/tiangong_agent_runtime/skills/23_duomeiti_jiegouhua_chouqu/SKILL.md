# 多媒体结构化抽取

面向从图片、视频、音频中抽取实体、字段、主题、风险、知识点和业务线索场景的技能。目标：把多媒体内容转成表格化、JSON化、可检索的数据。

## 什么时候用
- 用户说“从视频/图片/音频里提取信息、整理成表、抽取关键词、抽取知识点、提取风险、提取卖点”时使用
- 多媒体内容需要进入知识库、CRM、培训材料或报告时使用

## 反触发条件
- 用户只是看图/看视频时，应先由图片解析或视频解析完成基础理解
- 用户要制作素材时，应转交制作类技能
- 证据不足时不能编造字段

## 标准流程
1. 确定抽取对象和字段格式
2. 根据媒体类型调用对应解析工具获取证据
3. 调用 media_entity_extract、media_kv_extract 或 media_topic_extract 抽取结构
4. 涉及风险或知识点时调用 media_risk_extract / media_knowledge_extract
5. 输出 JSON/表格、证据引用、置信度和缺失字段

## 工具
- `media_entity_extract`
- `media_kv_extract`
- `media_topic_extract`
- `media_risk_extract`
- `media_knowledge_extract`
