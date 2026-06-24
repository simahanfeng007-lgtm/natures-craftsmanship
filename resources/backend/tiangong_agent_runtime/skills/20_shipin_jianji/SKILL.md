# 视频剪辑

面向视频裁剪、拼接、去头尾、加字幕、加背景音乐、转场、比例适配和导出场景的技能。目标：把已有视频素材加工成可交付成片。

## 什么时候用
- 用户说“剪视频、裁剪、拼接、去头去尾、加字幕、加BGM、转场、横版转竖版、导出视频”时使用
- 已有视频文件，需要按时间戳、字幕、片段或平台要求处理时使用

## 反触发条件
- 用户要理解视频内容时，应转交视频解析技能
- 用户要从无到有生成视频时，应转交视频制作技能
- 没有视频素材时，应先要求素材或转成制作计划

## 标准流程
1. 确认输入视频、目标时长、比例、输出格式和时间戳
2. 按任务选择 video_trim、video_concat 或 video_cut_by_timestamps
3. 需要字幕/BGM/转场/比例适配时调用对应工具
4. 调用 video_export 输出最终产物
5. 返回输出路径、处理摘要、风险和失败原因

## 工具
- `video_trim`
- `video_concat`
- `video_cut_by_timestamps`
- `video_add_subtitles`
- `video_add_bgm`
- `video_add_transition`
- `video_resize_reframe`
- `video_export`
