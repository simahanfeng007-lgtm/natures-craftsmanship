# 图片制作

面向海报、封面、头像、产品图、课程图、营销图、信息图和视觉素材生成场景的技能。目标：优先把用户意图直接生成可打开的图片文件，并返回产物路径；只有当前工具无法本地完成时才输出后续制作规格。

## 什么时候用
- 用户说“做图、生成图片、海报、封面、头像、宣传图、产品图、换背景、抠图、放大、风格化、做一张图”时使用
- 需要把文字创意变成图片文件、图片生成或编辑请求时使用

## 反触发条件
- 用户只是要识别图片内容时，应转交图片解析技能
- 用户要生成视频时，应转交视频制作技能
- 用户要求侵犯版权、仿冒证件或不当人像用途时应拒绝或降级

## 标准流程
1. 明确图片用途、尺寸比例、风格、主体、文字和禁忌
2. 调用 image_generate 直接生成图片文件；该工具会自动优先尝试当前模型服务的图片生成接口，失败后再本地兜底
3. 已有图片编辑时选择 image_edit、image_inpaint、image_background_remove 或 image_style_transfer
4. 需要质量增强时调用 image_upscale 或 image_variation
5. 输出已生成图片的路径和可继续编辑信息；不要把工具调用或 Provider Bridge 配置步骤当成最终答复

## 工具
- `image_generate`
- `image_edit`
- `image_inpaint`
- `image_background_remove`
- `image_upscale`
- `image_style_transfer`
- `image_variation`
- `image_text_poster_generate`
