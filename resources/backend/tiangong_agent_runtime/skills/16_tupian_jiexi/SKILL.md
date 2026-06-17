# 图片解析

面向图片识别、OCR、截图理解、图表/表格截图、区域问答和图片对比场景的技能。目标：把图片转成可证据化、可追问、可复核的结构化理解结果。

## 什么时候用
- 用户说“看图、识别图片、图片里有什么、截图什么意思、提取图片文字、OCR、识别表格截图、识别图表、对比两张图”时使用
- 需要从图片中提取对象、文字、区域、布局、表格、图表或差异时使用
- 图片证据不足、模糊、过曝、裁切时仍可用，但必须标注不确定性

## 反触发条件
- 用户要求直接生成/编辑图片时，应转交图片制作技能
- 用户要求视频帧级分析时，应转交视频解析技能
- 用户要求医学/法律最终诊断时，只能做材料可见内容解析，不做最终专业结论

## 标准流程
1. 确认图片路径、问题类型和输出格式
2. 优先调用 image_inspect 判断图片类型与可见内容
3. 涉及文字时调用 image_ocr_parse；涉及版面时调用 image_layout_parse
4. 涉及局部问题时调用 image_region_query；涉及双图差异时调用 image_compare
5. 涉及表格或图表时调用 image_table_extract 或 image_chart_extract
6. 输出中文结论、证据块、置信度和无法确认项，不编造不可见内容

## 工具
- `image_inspect`
- `image_ocr_parse`
- `image_layout_parse`
- `image_region_query`
- `image_compare`
- `image_table_extract`
- `image_chart_extract`
- `image_crop_export`
