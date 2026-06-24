# 项目打包

## 工具

- `scan_project`
- `create_zip_package`
- `create_release_bundle`
- `build_delivery_standardization`
- `file_sha256`
- `return_analysis`

## 1. 定位
项目打包不只是压缩 zip。它负责把项目从"开发目录"变成"可交付、可追踪、可回滚、可验收"的发布资产。覆盖打包范围确认、排除规则、版本命名、发布清单、文件完整性、运行时依赖检查、smoke 验证和最终交付。

## 2. 触发条件
- 用户要求"打包 / 发我完整包 / 生成 zip / 做交付包"
- 修复完成后要求交付
- Skill 升级完成后要求整理成果
- 项目阶段收口后生成发布资产
- 用户要求生成修复包、交接包、问题清单包

## 3. 反触发条件
- 未完成质检 → 先转 15_xiangmu_shoukou_zhijian
- 只是查看文件 → 02_wenjian_guanli
- 只是代码诊断 → 04_daima_zhenduan
- 只是文档导出 → 03_wendang_chuli

## 4. 输入契约
- source_root：源码目录
- output_dir：输出目录
- 包名
- 版本号
- 打包类型：完整包 / 修复包 / 交接包 / 问题清单包
- 排除目录：__pycache__、.git、node_modules、venv、dist 等默认排除
- 是否需要 manifest/hash
- 是否需要 smoke 报告

## 5. 输出契约
每次打包最终输出：
- zip 路径
- 包名和版本号
- 包类型
- 包大小
- 文件数量
- manifest 路径和内容摘要
- hash 信息（sha256）
- 排除项说明
- smoke/质检状态
- 已知未纳入项

## 6. 工具选择矩阵

**create_zip_package**
- 用途：创建普通 zip 包
- 适用：用户明确要求 zip、问题清单包、修复报告包、源码包
- 失败恢复：检查输出目录权限、路径长度、文件占用

**create_release_bundle**
- 用途：创建完整发布 bundle
- 适用：安装包、阶段基线、完整交付
- 失败恢复：生成缺件清单，转 15_xiangmu_shoukou_zhijian

**build_delivery_standardization**
- 用途：生成交付标准化报告
- 适用：任何正式交付前
- 失败恢复：输出无法标准化的字段（版本缺失、manifest 缺失等）

## 7. 标准执行流程

**阶段 A：打包前确认**
- 明确包类型和范围
- 明确 source_root 和 output_dir
- 确认是否通过收口质检；未通过时提示风险

**阶段 B：打包范围扫描**
- 调用 02_wenjian_guanli / scan_project 获取文件树
- 确认入口文件、配置文件、资源文件、运行时依赖是否纳入
- 排除缓存、临时目录、无关测试输出、__pycache__、node_modules

**阶段 C：生成标准化信息**
- 版本号
- 修改摘要
- 文件清单
- 已知问题
- 验收状态

**阶段 D：创建 zip / bundle**
- 普通交付用 create_zip_package
- 正式发布用 create_release_bundle
- 附带 manifest 和 hash

**阶段 E：打包后验证**
- zip 存在且大小非 0
- 关键文件存在（入口、配置、资源）
- manifest/hash 生成成功
- 安装包类项目：检查内置运行时是否完整

**阶段 F：交付收口**
- 输出下载路径
- 输出内容摘要
- 输出风险和后续建议

## 8. 发布包类型规则

**完整包**
- 包含：源码/资源/运行时/配置模板/启动入口/文档
- 必须带 manifest 和验收报告

**修复包**
- 只包含：修改文件 + 修复报告 + 替换说明
- 必须说明基线版本

**交接包**
- 包含：计划 + 当前状态 + 问题清单 + 后续任务 + 关键文件

**问题清单包**
- 包含：txt/xlsx/md 报告
- 默认不包含完整源码

## 9. 证据链规则
- 不能只说"已打包"，必须给 zip 路径和文件大小
- 正式包必须给 manifest + hash
- 缺少关键运行时文件时标记 P0
- 未执行 smoke 时说明"未执行 smoke"

## 10. 失败恢复
- 输出目录不可写：改用可写目录
- 路径过长：缩短包名或使用临时目录
- 文件占用：提示关闭占用程序
- 包体过大：输出大文件清单，建议排除
- 关键文件缺失：停止正式发布，转 15_xiangmu_shoukou_zhijian
- 打包成功但验证失败：标记不通过，不交付为最终包

## 11. 质量门
- 通过：zip/bundle 存在、关键文件齐全、manifest/hash/报告齐全
- 部分通过：包已生成但缺少 smoke 或部分报告
- 不通过：包不存在、包体为空、关键文件缺失、运行时不完整

## 12. 协作规则
- 打包前文件清单 → 02_wenjian_guanli
- 打包前质量检查 → 15_xiangmu_shoukou_zhijian
- 打包失败根因 → 04_daima_zhenduan / 13_zhongduan_zhixing
- 文档导出 → 03_wendang_chuli

## 13. 验收用例
1. 正常：生成完整 zip 包，含 manifest + hash
2. 修复包：只含修改文件和修复报告
3. 交接包：含当前状态和后续任务清单
4. 打包后发现关键文件缺失：停止发布，转质检
5. 输出目录不可写：自动改用备用目录
6. 包体过大：提示大文件清单建议排除
7. 安装包内置 Python runtime 缺件：标记 P0
