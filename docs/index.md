---
layout: home
title: LivingMemory
titleTemplate: 智能长期记忆插件
hero:
  name: LivingMemory
  text: 为 AstrBot 打造的智能长期记忆插件
  tagline: 让机器人记住长期偏好、关系、约定和项目上下文，并用可控的生命周期保持记忆新鲜。
  image:
    src: https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/raw/master/logo.png
    alt: LivingMemory logo
  actions:
    - theme: brand
      text: 快速开始
      link: /guide/getting-started
    - theme: alt
      text: 2.7.1 稳定版
      link: /releases
features:
  - title: 自动长期记忆
    details: 对话达到触发轮次后自动总结，保存为可检索的长期记忆。
  - title: 主动回忆与写入
    details: 为 Agent 注册 recall_long_term_memory 与 memorize_long_term_memory 工具。
  - title: 双路四模式检索
    details: 文档路和图谱路同时使用关键词与向量检索，再按归一化分数加权融合排序。
  - title: 时间感知生命周期
    details: 记忆原子拥有 TTL、衰减、访问强化和自动清理机制。
  - title: 四种风格的管理界面
    details: Editorial、Studio、Paper、Terminal；支持明暗切换、稳定图谱阅读与清晰的编辑和导入流程。
  - title: 数据安全
    details: 支持版本备份、迁移前备份、索引回滚和事务删除。
---

```mermaid
flowchart LR
    MSG["对话消息"] --> HOOK["EventHandler 钩子"]
    HOOK -->|"请求前"| RECALL["双路召回"]
    HOOK -->|"回复后"| REFLECT["反思与总结"]
    RECALL --> ENGINE["MemoryEngine"]
    REFLECT --> ENGINE
    ENGINE --> SQLITE[("SQLite 记忆库")]
    ENGINE --> FAISS[("FAISS 向量索引")]
    ENGINE --> API["Dashboard 与 Agent 工具"]
```

## 2.7.1 已发布

2.7.1 补丁版聚焦配置体验与重复记忆：高级配置从 AstrBot 插件配置页迁移到 Dashboard 设置页（常用设置 + 5 类折叠分组），并新增写入时近重复合并。稳定版会正常参与 AstrBot 的版本更新提示；[版本与升级](/releases)提供完整说明、下载与升级步骤。

## 这份文档适合谁？

如果你只是想装好插件并让 AstrBot 拥有长期记忆，从 [快速开始](/guide/getting-started) 读起。  
如果你想理解为什么它能同时处理事实、关系、偏好和旧记忆衰减，直接看 [功能说明](/features) 和 [技术架构](/architecture)。

::: tip 文档范围
这里保留的是面向使用、配置、功能理解和架构说明的内容。旧版本阶段总结、内部开发记录和过期 API 草稿已经不再放进文档站。
:::
