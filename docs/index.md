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
      text: 查看架构
      link: /architecture
features:
  - title: 自动长期记忆
    details: 对话达到触发轮次后自动总结，保存为可检索的长期记忆。
  - title: 主动回忆与写入
    details: 为 Agent 注册 recall_long_term_memory 与 memorize_long_term_memory 工具。
  - title: 双路四模式检索
    details: 文档路和图谱路同时使用关键词与向量检索，再用 RRF 融合排序。
  - title: 时间感知生命周期
    details: 记忆原子拥有 TTL、衰减、访问强化和自动清理机制。
  - title: 插件页面管理
    details: 在 AstrBot Pages 中查看、搜索、调试召回和浏览知识图谱。
  - title: 数据安全
    details: 支持版本备份、迁移前备份、索引回滚和事务删除。
---

<img class="diagram" src="/images/architecture-flow.svg" alt="LivingMemory runtime architecture">

## 这份文档适合谁？

如果你只是想装好插件并让 AstrBot 拥有长期记忆，从 [快速开始](/guide/getting-started) 读起。  
如果你想理解为什么它能同时处理事实、关系、偏好和旧记忆衰减，直接看 [功能说明](/features) 和 [技术架构](/architecture)。

::: tip 文档范围
这里保留的是面向使用、配置、功能理解和架构说明的内容。旧版本阶段总结、内部开发记录和过期 API 草稿已经不再放进文档站。
:::
