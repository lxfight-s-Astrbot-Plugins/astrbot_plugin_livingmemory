# 配置参考

LivingMemory 的默认配置已经适合大多数场景。真正需要调整的通常是模型 Provider、召回规模、记忆隔离、图谱检索和备份清理。

## 推荐配置模板

| 场景 | 建议 |
| --- | --- |
| 新手默认使用 | 只配置 `provider_settings.llm_provider_id` 和 `provider_settings.embedding_provider_id`，其他保持默认 |
| 私聊长期助手 | 开启人格隔离和会话隔离，`summary_trigger_rounds` 保持 8-12 |
| 群聊陪伴 | 开启 `session_manager.enable_full_group_capture`，适当增大 `context_window_size` |
| 低配服务器 | 减小 `index_rebuild_settings.embedding_batch_size`，保持 `tasks_limit = 1`，增大请求间隔 |
| 高质量召回 | 开启图记忆和原子化，`recall_engine.top_k` 设置为 5-8 |
| 成本敏感 | 降低 `top_k`，关闭跨轮次扩展检索，适当增大总结触发轮次 |

## 模型 Provider

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `provider_settings.embedding_provider_id` | 空 | 用于向量化记忆。留空使用 AstrBot 默认 Embedding Provider |
| `provider_settings.llm_provider_id` | 空 | 用于总结对话和评估记忆重要性。留空使用 AstrBot 默认 LLM |

建议 Embedding 模型保持稳定，不要频繁更换。更换 Embedding 后，如发现旧记忆召回异常，可执行 `/lmem rebuild-index`。

## 会话管理

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `session_manager.enable_full_group_capture` | `true` | 捕获群聊中未直接 @Bot 的消息，用于建立完整群聊背景 |
| `session_manager.context_window_size` | `50` | 传给总结与上下文分析的历史消息窗口 |
| `session_manager.max_messages_per_session` | `1000` | 单会话数据库保留消息上限 |
| `session_manager.cleanup_batch_size` | `50` | 超限后每批清理的已总结旧消息数量 |

群聊消息量很大时，可以适当降低 `context_window_size` 或关闭全量群聊捕获。

## 召回与注入

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `recall_engine.top_k` | `5` | 每轮自动召回的记忆数量 |
| `recall_engine.max_k` | `10` | Agent 主动检索工具允许返回的最大数量 |
| `recall_engine.importance_weight` | `1.0` | 重要性在最终排序中的权重 |
| `recall_engine.fallback_to_vector` | `true` | 混合检索失败时降级到向量检索 |
| `recall_engine.injection_method` | `extra_user_content` | 记忆注入到 LLM 请求的位置或形式 |
| `recall_engine.inject_with_recent_context` | `false` | 是否拼接最近对话扩展查询 |
| `recall_engine.search_cache_enabled` | `true` | 是否启用短期检索缓存 |

`extra_user_content` 是最稳妥的默认注入方式。Gemini Provider 下不建议强制使用 `fake_tool_call`；DeepSeek V4 thinking 模式如需伪工具语义，可选择 `fake_tool_call_deepseek_v4`。

## 记忆隔离

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `filtering_settings.use_persona_filtering` | `true` | 只召回当前人格相关记忆 |
| `filtering_settings.use_session_filtering` | `true` | 只召回当前会话相关记忆 |

如果你希望不同群或不同私聊共享同一批长期记忆，可以关闭会话隔离；如果机器人有多个明显不同的人格，建议始终开启人格隔离。

## 总结与生命周期

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `reflection_engine.summary_trigger_rounds` | `10` | 达到多少轮对话后触发总结 |
| `importance_decay.decay_rate` | `0.01` | 每日重要性衰减比例 |
| `importance_decay.access_decay_window_days` | `30.0` | 访问强化的时间窗口 |
| `importance_decay.access_decay_max_count` | `10` | 最大访问强化次数 |

如果你希望机器人更快记住短期上下文，可以降低 `summary_trigger_rounds`；如果希望减少 LLM 调用成本，可以提高它。

## Agent 主动工具

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `agent_tools.enable_recall_tool` | `true` | 注册 `recall_long_term_memory`，允许 Agent 主动检索长期记忆 |
| `agent_tools.enable_memorize_tool` | `false` | 注册 `memorize_long_term_memory`，允许 Agent 主动写入长期记忆 |

主动写入工具更强，也更需要模型自律。建议先只开启主动回忆，确认效果稳定后再启用主动写入。

## 图记忆与原子化

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `graph_memory.enabled` | `true` | 启用图谱路线检索 |
| `graph_memory.document_route_weight` | `0.65` | 文档路权重 |
| `graph_memory.graph_route_weight` | `0.35` | 图路权重 |
| `graph_memory.cross_route_bonus` | `0.08` | 同时命中文档路和图路时的加分 |
| `graph_memory.expansion_hops` | `1` | 图谱邻居扩展跳数 |
| `graph_memory.dynamic_route_weighting` | `true` | 根据查询意图动态调整路由权重 |
| `graph_memory.atom_enabled` | `true` | 启用记忆原子化 |

关系型问题较多时，可以提高图路权重或把 `expansion_hops` 调到 `2`。如果你的数据库很大，二跳扩展会增加查询开销，建议先观察 WebUI 召回调试结果。

## 备份、迁移与清理

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `migration_settings.auto_migrate` | `true` | 启动时自动迁移旧数据库 |
| `migration_settings.create_backup` | `true` | 迁移前自动备份 |
| `backup_settings.enabled` | `true` | 每日自动备份数据库 |
| `backup_settings.keep_days` | `7` | 自动备份保留天数 |
| `forgetting_agent.auto_cleanup_enabled` | `true` | 每日清理久远且低重要性记忆 |
| `forgetting_agent.cleanup_days_threshold` | `30` | 进入清理候选的天数 |
| `forgetting_agent.cleanup_importance_threshold` | `0.3` | 清理候选的重要性阈值 |

生产使用建议保持备份和迁移备份开启。清理策略偏保守时，可以提高天数阈值或降低重要性阈值。

## 索引重建调优

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `index_rebuild_settings.batch_size` | `50` | 每批读取的记忆条数 |
| `index_rebuild_settings.embedding_batch_size` | `8` | 单次 Embedding 请求包含的文本数量 |
| `index_rebuild_settings.tasks_limit` | `1` | Embedding 并发上限 |
| `index_rebuild_settings.max_retries` | `5` | 单批失败重试次数 |
| `index_rebuild_settings.request_delay` | `5.0` | Embedding 请求间隔 |
| `index_rebuild_settings.max_failure_ratio` | `0.02` | 允许失败比例 |

如果遇到 API 限流，优先增大 `request_delay`，再降低 `embedding_batch_size`。不要盲目提高并发，索引重建更看重稳定完成。
