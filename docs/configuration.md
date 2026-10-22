# 配置参考

> [!NOTE]
> AstrBot 插件配置页只保留 **模型 Provider 与回复语言**；召回、反思、图谱、原子、整合、备份、索引重建、访问控制等高级配置已迁移到 Dashboard 的「设置」页，按分组编辑并保存即可。本页仍作为全部配置项的参考说明。

LivingMemory 的默认配置已经适合大多数场景。真正需要调整的通常是模型 Provider、召回规模、记忆隔离、图谱检索和备份清理。

> [!NOTE]
> 每个数值配置项都有硬性允许范围，在 AstrBot 配置页以滑条+数字输入框展示，悬停提示与 [配置参考] 表格均可查看。超出范围的值在插件加载时会被自动修正到最近边界（非法值回退该项默认值），并逐项输出 warning 日志和写回配置文件——单项坏值不会再导致整包配置回退为默认值。

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
| `provider_settings.embedding_provider_id` | 空（不建议） | 用于向量化记忆，应填写已启用的 Embedding Provider ID。留空只在 AstrBot 存在可用 Embedding Provider 时回退到第一个，可能选错模型或导致向量/图索引初始化失败 |
| `provider_settings.llm_provider_id` | 空 | 用于总结对话和评估记忆重要性。留空使用 AstrBot 默认 LLM |
| `provider_settings.llm_stream_mode` | `auto` | 记忆处理调用 LLM 的方式：`auto` 优先非流式、遇“不支持非流式”错误自动转流式；`always` 始终流式；`never` 始终非流式（旧行为） |

建议 Embedding 模型保持稳定，不要频繁更换。更换 Embedding 后，如发现旧记忆召回异常，可执行 `/lmem rebuild-index`。

只接受流式请求（`stream=true`）的模型服务在 `auto` 模式下会自动改用流式调用，无需手动配置；需要强制始终流式时设为 `always`。流式中断、超时或未收到完整响应会按失败处理，不会把不完整结果写入记忆。

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
| `recall_engine.min_importance_for_retrieval` | `0.0` | 最低重要性阈值，`0` 表示不过滤 |
| `recall_engine.min_similarity_for_retrieval` | `0.0` | 最低向量相似度；纯关键词命中不受影响 |
| `recall_engine.recent_memory_count` | `2` | 每次召回为近期记忆保留的槽位数 |
| `recall_engine.recent_memory_max_age_hours` | `72` | 近期记忆保底的时间窗口 |
| `recall_engine.memory_type_filter` | `all` | 设为 `event_only` 可排除明确的纯偏好/关系记忆 |
| `recall_engine.fallback_to_vector` | `true` | 混合检索失败时降级到向量检索 |
| `recall_engine.rerank_enabled` | `false` | 启用 Rerank 模型对融合候选按查询相关性重排序 |
| `recall_engine.rerank_provider_id` | `""` | AstrBot 中 Rerank 类型提供商的 ID，留空则跳过 |
| `recall_engine.rerank_candidates` | `20` | 送入 Rerank 的融合候选数量（2–100），重排序后保留 top_k |
| `recall_engine.fusion_mode` | `score` | 结果融合策略：`score` 按归一化分数加权融合，`rrf` 为旧的排名级融合 |
| `recall_engine.fusion_bm25_weight` | `0.5` | `score` 融合时 BM25 路权重（0–1），向量路为 `1 - 该值` |
| `recall_engine.recall_candidate_multiplier` | `3` | 融合前每路召回的候选数相对 `k` 的倍数（1–10） |
| `recall_engine.fusion_agreement_bonus` | `0.25` | 两路都召回且名次接近时的共识奖励强度（0 关闭）；中文集网格显示 0.4–0.6 略优，但英文集 0.25 更稳，故保持默认 |
| `recall_engine.injection_method` | `extra_user_content` | 记忆注入方式（唯一支持 add-only 临时片段注入） |
| `recall_engine.inject_with_recent_context` | `false` | 是否拼接最近对话扩展查询 |
| `recall_engine.search_cache_enabled` | `true` | 是否启用短期检索缓存 |

`extra_user_content` 是唯一支持的注入方式：记忆以 `mark_as_temp` 临时片段追加到当前用户消息末尾，AstrBot 保存对话历史时会自动过滤该片段，因此注入不影响前缀缓存，也**完全不写入 AstrBot 的对话历史**。插件不再改写历史消息格式或向对话历史写入内容。旧的 `user_message_before` / `user_message_after` / `fake_tool_call` / `fake_tool_call_deepseek_v4` / `system_prompt` 均已废弃，配置后自动回退到 `extra_user_content`。

## 记忆隔离

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `filtering_settings.use_persona_filtering` | `true` | 只召回当前人格相关记忆 |
| `filtering_settings.memory_scope_mode` | `legacy` | `legacy` 保持旧行为；也可按会话、用户或全局共享 |
| `filtering_settings.use_session_filtering` | `true` | 仅在 `legacy` 模式下控制会话过滤 |
| `filtering_settings.isolated_sessions` | 空 | 始终强制隔离的完整会话 ID，每行一个 |
| `access_control.whitelist_enabled` | `false` | 仅允许名单内身份使用长期记忆 |
| `access_control.allowed_ids` | 空 | 用户 ID、`平台:用户 ID`、群组 ID 或完整会话 ID |
| `access_control.identity_aliases` | 空 | `来源身份=统一名称`，每行一个 |

`user` 模式让同一平台用户在不同群聊和私聊共享记忆；`global` 模式让所有非例外会话共享。`isolated_sessions` 的优先级最高；在 `legacy` 模式关闭会话过滤并配置例外后，非例外会话也会进入专用全局作用域，避免读取例外会话。开启白名单但名单为空会拒绝所有自动捕获、总结、召回和 Agent 记忆工具。

身份别名按 `平台:用户 ID`、用户 ID、当前用户名的顺序匹配，并在对话总结前替换显示名称。作用域配置只影响升级后新写入的记忆，现有记忆不会自动迁移或重新生成向量。

## 总结与生命周期

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `reflection_engine.summary_trigger_rounds` | `10` | 达到多少轮对话后触发总结 |
| `reflection_engine.window_split_by_day` | `true` | 跨天时只总结最老的一天，避免跨天对话合并进同一条记忆 |
| `reflection_engine.window_split_gap_minutes` | `180` | 相邻消息空闲间隔超过该分钟数时只总结最老片段；`0` 关闭空闲间隔切分 |
| `reflection_engine.include_source_time_tags` | `true` | 从原始消息时间写入来源日期标签 |
| `reflection_engine.source_retention_importance_threshold` | `0.8` | 达到阈值时独立保留原始消息 |
| `importance_decay.decay_rate` | `0.01` | 每日重要性衰减比例 |
| `importance_decay.access_decay_window_days` | `30.0` | 访问强化的时间窗口 |
| `importance_decay.access_decay_max_count` | `10` | 最大访问强化次数 |
| `importance_decay.protected_importance_threshold` | `1.0` | 达到阈值的记忆不参与每日衰减 |

如果你希望机器人更快记住短期上下文，可以降低 `summary_trigger_rounds`；如果希望减少 LLM 调用成本，可以提高它。

总结窗口虽然仍按未总结轮次触发，但不会再跨天或跨长空闲合并：存在边界时只总结最老的连续片段（FIFO），较新的内容留到后续触发再总结。消息入库时间优先使用平台消息时间戳，缺失或异常（例如毫秒时间戳、平台时钟明显超前）时回退到接收时间，因此窗口边界与消息实际发生时间一致。

保留的原文只写入 SQLite `memory_sources` 表，不进入向量、BM25、图或原子索引，因此不会增加向量数量。它会增加数据库磁盘占用。Dashboard 详情的重新总结会调用一次 LLM，并替换原记忆、重新生成 Embedding 和全部派生索引。

## Agent 主动工具

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `agent_tools.enable_recall_tool` | `true` | 注册 `recall_long_term_memory`，允许 Agent 主动检索长期记忆 |
| `agent_tools.enable_memorize_tool` | `false` | 注册 `memorize_long_term_memory`，允许 Agent 主动写入长期记忆 |
| `agent_tools.enable_forget_tool` | `false` | 注册 `forget_long_term_memory`；仅管理员可调用，只做可逆归档（软删除），可由管理员从 WebUI 恢复 |
| `agent_tools.forget_tool_max_memories` | `3` | 遗忘工具单次最多归档的记忆条数（1–10），超出整批拒绝 |
| `agent_tools.forget_tool_cooldown_seconds` | `60` | 同一会话两次成功归档的最小间隔（0–3600），`0` 表示不限制 |
| `agent_tools.enable_agentic_recall_beta` | `false` | 启用自主多轮回忆（Beta），需同时开启 `enable_recall_tool` |
| `agent_tools.agentic_recall_max_calls` | `4` | 单次请求内记忆搜索/深读工具共享的最大调用次数（1–20） |
| `agent_tools.agentic_recall_time_budget_seconds` | `20` | 单次请求内记忆工具累计可工作秒数（1–300） |
| `agent_tools.agentic_recall_max_result_chars` | `12000` | Beta 记忆工具单次返回的最大字符数（500–100000），超出部分可按游标续读 |

主动写入工具更强，也更需要模型自律。建议先只开启主动回忆，确认效果稳定后再启用主动写入。

主动遗忘工具默认关闭，开启后也只对管理员生效：模型只能把记忆归档（从检索中移除，documents 记录保留），不会物理删除；管理员可在 Dashboard 记忆页用 `archived` 状态筛选后改回 `active` 恢复。单次调用条数受限，并有按会话的冷却时间。

自主回忆为 Beta 功能，默认关闭。开启后 `recall_long_term_memory` 支持按当前发言人检索、事件时间范围过滤、排除已看候选与游标分页，并注册 `read_memory_evidence`，按记忆 ID 读取有效事实与（可验证时）保留原文。工具调用次数、累计耗时与单次返回长度共享单次请求预算；回忆规则以临时片段追加注入。开关与三项预算始终显示在设置页，保存并成功重载插件后对新请求生效。

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
| `forgetting_agent.auto_archived_enabled` | `false` | 将清理候选归档并移出检索索引，而非永久删除 |
| `forgetting_agent.cleanup_days_threshold` | `30` | 进入清理候选的天数 |
| `forgetting_agent.cleanup_importance_threshold` | `0.3` | 清理候选的重要性阈值 |

生产使用建议保持备份和迁移备份开启。启用自动归档后，原始文档仍可在 Dashboard 查看和恢复，恢复时会重新生成 Embedding 并重建 BM25、图谱和记忆原子索引。

## 记忆库整合

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `memory_consolidation.enabled` | `false` | 是否启用记忆库定期整合 |
| `memory_consolidation.trigger` | `daily` | 触发方式：`daily`=每日定时，`reflection`=每次反思时顺带检查 |
| `memory_consolidation.granularity` | `session` | 聚合粒度：`session`=同一会话，`semantic`=跨会话语义聚类 |
| `memory_consolidation.keep_original` | `archive` | 整合后旧记忆处理：`archive`=归档保留，`delete`=直接删除 |
| `memory_consolidation.min_memories_per_group` | `3` | 每组至少多少条记忆才触发整合 |
| `memory_consolidation.min_age_days` | `7` | 只整合创建早于该天数的记忆 |
| `memory_consolidation.max_importance` | `0.5` | 只整合重要度低于该值的记忆 |
| `memory_consolidation.max_groups_per_run` | `5` | 每次运行最多整合的组数 |
| `memory_consolidation.semantic_similarity_threshold` | `0.7` | 语义聚类模式下的最小相似度 |

记忆整合从源头控制记忆库规模：把零散的低价值记忆聚合、整理、总结为更精炼的一条，避免注入时硬截断带来的信息损失。整合结果写入新记忆，旧记忆按 `keep_original` 归档或删除。`trigger=reflection` 时带 6 小时冷却，不会每条消息都触发。

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
