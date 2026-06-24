# 视频解析

面向视频识别、视频摘要、关键帧、场景分段、视频OCR、字幕/音频转写和事件时间线场景的技能。目标：把视频转成关键帧、字幕、事件、证据片段组成的可追问结构。

## 什么时候用
- 用户说“看视频、分析视频、视频里发生了什么、提取关键帧、识别视频文字、提取字幕、视频转文字、视频时间线”时使用
- 需要从视频中提取场景、动作、画面文字、音频、字幕、事件顺序时使用

## 反触发条件
- 用户要求剪辑、拼接、加字幕、导出成片时，应转交视频剪辑技能
- 用户要求生成新视频时，应转交视频制作技能
- 视频内容涉及专业诊断或违法识别时，只做可见/可听材料解析

## 标准流程
1. 读取视频元信息，确认时长、分辨率、编码和是否可处理
2. 调用 video_keyframe_extract 抽关键帧
3. 需要分段时调用 video_scene_split
4. 需要画面文字时调用 video_ocr_parse
5. 需要语音内容时调用 video_audio_transcribe 或 video_subtitle_extract
6. 综合生成 video_event_timeline，输出时间点、证据、置信度和限制

## 工具
- `video_inspect`
- `video_keyframe_extract`
- `video_scene_split`
- `video_ocr_parse`
- `video_audio_transcribe`
- `video_subtitle_extract`
- `video_event_timeline`
- `video_compare`
