# 技术架构

LivingMemory 的运行时由事件钩子、记忆处理、检索融合、存储和 WebUI API 五个部分组成。它尽量把“自动记忆”和“主动工具”放在同一套数据模型上，避免两套记忆系统互相打架。

<img class="diagram" src="/images/architecture-flow.svg" alt="LivingMemory runtime architecture">

## 总体流程

1. AstrBot 收到消息后，`EventHandler` 捕获会话上下文。
2. 在 LLM 请求前，召回链路根据当前消息和最近上下文查询长期记忆。
3. 检索结果按配置注入到请求中，或作为 Agent 工具结果返回。
4. LLM 回复后，反思链路判断是否需要总结并写入新记忆。
5. 后台任务执行衰减、过期清理、备份和索引校验。

## 主要模块

| 模块 | 职责 |
| --- | --- |
| `main.py` | 注册插件、初始化核心组件、注册 Agent 工具和 Pages API |
| `core/plugin_initializer.py` | 非阻塞初始化、Provider 等待、数据库迁移、索引加载 |
| `core/event_handler.py` | 群聊捕获、记忆召回、记忆反思 |
| `core/managers/memory_engine.py` | 统一记忆写入、搜索、删除和索引维护 |
| `core/managers/graph_memory_manager.py` | 图谱节点、边、条目和图检索协调 |
| `core/managers/atom_lifecycle_manager.py` | 原子过期、遗忘、强化和生命周期维护 |
| `core/retrieval/` | BM25、向量、图谱、原子检索与 RRF 融合 |
| `storage/` | SQLite 存储、图谱存储、原子存储、数据库迁移 |
| `pages/dashboard/` | AstrBot Pages 管理界面 |

## 双路四模式检索

普通长期记忆和图谱记忆分别走两条路线：

| 路线 | 关键词模式 | 向量模式 |
| --- | --- | --- |
| 文档路 | `BM25Retriever` | `VectorRetriever` |
| 图谱路 | `GraphKeywordRetriever` | `GraphVectorRetriever` |

随后 `RRFFusion` 会融合多个排序列表，再叠加重要性、时间衰减、会话隔离和人格隔离等过滤条件。

## 记忆数据模型

| 类型 | 说明 |
| --- | --- |
| 会话消息 | 原始对话上下文，用于触发总结和补充查询 |
| 记忆条目 | LLM 总结后的长期记忆，包含摘要、重要性、会话和人格元数据 |
| 图谱节点与边 | 从记忆中抽取的实体和关系，支持跨记忆合并 |
| 记忆原子 | 独立事实单元，拥有类型、TTL、衰减和访问强化状态 |

## 数据安全设计

插件在高风险操作前尽量留下恢复点：

| 场景 | 保护措施 |
| --- | --- |
| 插件版本变化 | 启动时自动创建版本标记备份 |
| 数据库迁移 | 迁移前备份 |
| 索引重建 | 分批重建，失败后回滚 |
| 删除记忆 | 使用事务保护相关记录 |
| 管理页面操作 | 通过 Pages API 复用运行时组件，避免绕过 MemoryEngine |
