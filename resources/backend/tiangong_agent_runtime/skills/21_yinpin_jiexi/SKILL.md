# 音频解析

面向录音、会议音频、视频音轨、通话录音、播客和声音事件理解场景的技能。目标：把音频转成转写文本、说话人片段、关键词、摘要和事件线索。

## 什么时候用
- 用户说“音频转文字、转写、会议录音、提取关键词、音频摘要、谁在说话、声音事件”时使用
- 需要从音频或视频音轨中提取语言内容和结构时使用

## 反触发条件
- 用户要生成配音或背景音乐时，应转交音频制作技能
- 用户要剪视频时，应转交视频剪辑技能
- 专业声纹身份确认不能凭音频工具直接断言

## 标准流程
1. 读取音频元信息和时长
2. 调用 audio_transcribe 转写语音
3. 需要说话人时调用 audio_diarize
4. 需要摘要或关键词时调用 audio_summary / audio_keywords_extract
5. 需要非语言事件时调用 audio_event_detect
6. 输出时间戳、文本、置信度和无法确认项

## 工具
- `audio_transcribe`
- `audio_diarize`
- `audio_summary`
- `audio_keywords_extract`
- `audio_event_detect`
