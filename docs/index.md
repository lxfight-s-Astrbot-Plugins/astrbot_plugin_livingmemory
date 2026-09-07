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
      text: 2.7.0-beta.1 测试版
      link: /releases
features:
  - title: 自动长期记忆
    details: 对话达到触发轮次后自动总结，保存为可检索的长期记忆。
  - title: 主动回忆与写入
    details: 为 Agent 注册 recall_long_term_memory 与 memorize_long_term_memory 工具。
  - title: 双路四模式检索
    details: 文档路和图谱路同时使用关键词与向量检索，再用 RRF 融合排序。
  - title: 时间感知生命周期
    details: 记忆原子拥有 TTL、衰减、访问强化和自动清理机制。
  - title: 四种风格的管理界面
    details: Editorial、Studio、Paper、Terminal；支持明暗切换、稳定图谱阅读与清晰的编辑和导入流程。
  - title: 数据安全
    details: 支持版本备份、迁移前备份、索引回滚和事务删除。
---

<img class="diagram" src="/images/architecture-flow.svg" alt="LivingMemory runtime architecture">

## 2.7.0-beta.1 正在测试

本次汇总 2.6.1 之后的全部合并改动，包含召回与索引恢复修复、四种主题、图谱标签稳定性和管理流程优化。[版本与升级](/releases)提供完整 PR 与作者名单、测试版下载、安装和回退步骤。稳定版仍为 2.6.1。

## 这份文档适合谁？

如果你只是想装好插件并让 AstrBot 拥有长期记忆，从 [快速开始](/guide/getting-started) 读起。  
如果你想理解为什么它能同时处理事实、关系、偏好和旧记忆衰减，直接看 [功能说明](/features) 和 [技术架构](/architecture)。

::: tip 文档范围
这里保留的是面向使用、配置、功能理解和架构说明的内容。旧版本阶段总结、内部开发记录和过期 API 草稿已经不再放进文档站。
:::
