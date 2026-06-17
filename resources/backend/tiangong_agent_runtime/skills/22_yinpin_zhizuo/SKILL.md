# 音频制作

面向配音、TTS、背景音乐、音效、降噪、音量标准化、混音和音频导出场景的技能。目标：把脚本和素材转成可交付音频产物或制作请求。

## 什么时候用
- 用户说“生成配音、文字转语音、语音克隆、做BGM、降噪、音量统一、混音、导出音频”时使用
- 视频制作或剪辑需要旁白、音乐、音效时使用

## 反触发条件
- 用户只要转写已有音频时，应转交音频解析技能
- 涉及未经授权的声音克隆时必须拒绝或要求授权
- 用户要最终视频成片时应与视频制作/剪辑协作

## 标准流程
1. 明确声音风格、语速、语气、语言、授权和输出格式
2. 调用 tts_generate 或 audio_clone_voice 生成配音请求
3. 需要背景音乐时调用 bgm_generate
4. 已有音频处理时调用 audio_denoise、audio_normalize 或 audio_mix
5. 调用 audio_export 输出音频文件或请求规格

## 工具
- `tts_generate`
- `audio_clone_voice`
- `bgm_generate`
- `audio_mix`
- `audio_denoise`
- `audio_normalize`
- `audio_export`
