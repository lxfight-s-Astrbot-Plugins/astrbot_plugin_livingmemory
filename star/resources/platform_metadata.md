---
outline: deep
---

# PlatformMetadata

平台元数据, 包含了平台的基本信息, 例如平台名称, 平台类型等.

### 属性

1. `name(str)`: 平台的名称
2. `description(str)`: 平台的描述
3. `id(str)`: 平台的唯一标识符, 用于区分不同的平台
4. `default_config_tmpl(dict)`: 平台的默认配置模板, 用于生成平台的默认配置文件
5. `adapter_display_name(str)`: 显示在 WebUI 中的平台名称, 默认为 `name`(可以更改)
