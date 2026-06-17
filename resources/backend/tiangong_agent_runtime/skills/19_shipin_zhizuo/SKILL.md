# 视频制作

面向短视频、宣传片、数字人口播、图生视频、文生视频、分镜脚本和视频渲染场景的技能。目标：把创意需求转成可生产的视频方案、镜头表、素材清单和生成请求。

## 什么时候用
- 用户说“做视频、生成视频、宣传片、短视频、数字人、图生视频、文生视频、分镜、旁白、配音”时使用
- 需要从脚本、图片或主题生成视频制作计划时使用

## 反触发条件
- 用户只是分析已有视频时，应转交视频解析技能
- 用户只是剪已有视频时，应转交视频剪辑技能
- 用户只做配音或音频处理时，应转交音频制作技能

## 标准流程
1. 明确目标平台、时长、比例、风格、受众和素材来源
2. 调用 storyboard_generate 与 shot_plan_generate 生成分镜和镜头计划
3. 根据输入选择 video_generate_from_text、video_generate_from_images 或 video_avatar_generate
4. 需要旁白时调用 voiceover_generate，需要字幕时调用 subtitle_burn_in
5. 调用 video_render 生成渲染请求或最终导出计划

## 工具
- `video_generate_from_text`
- `video_generate_from_images`
- `storyboard_generate`
- `shot_plan_generate`
- `video_avatar_generate`
- `voiceover_generate`
- `subtitle_burn_in`
- `video_render`
