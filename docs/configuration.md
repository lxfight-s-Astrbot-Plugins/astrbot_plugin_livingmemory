# 配置参考

> [!NOTE]
> AstrBot 插件配置页只保留 **模型 Provider 与回复语言**；召回、反思、图谱、原子、整合、备份、索引重建、访问控制等高级配置已迁移到 Dashboard 的「设置」页，按分组编辑并保存即可。本页仍作为全部配置项的参考说明。

LivingMemory 的默认配置已经适合大多数场景。真正需要调整的通常是模型 Provider、召回规模、记忆隔离、图谱检索和备份清理。

> [!NOTE]
> 每个数值配置项都有硬性允许范围，在 Dashboard 的「设置」页以数字输入框展示并标注允许范围，本页表格也可查看。插件启动时按 Pydantic 模型校验整份配置：超出范围、类型不符或枚举值非法都会直接报错并阻止插件加载，不再自动夹紧或回退默认值；请按错误提示修正对应配置项后重启。

## 推荐配置模板

| 场景 | 建议 |
| --- | --- |
| 新手默认使用 | 只配置 `provider_settings.llm_provider_id` 和 `provider_settings.embedding_provider_id`，其他保持默认 |
| 私聊长期助手 | 开启人格隔离和会话隔离，`summary_trigger_rounds` 保持 8-12 |
| 群聊陪伴 | 开启 `session_manager.enable_full_group_capture`，适当增大 `context_window_size` |
| 低配服务器 | 减小 `index_rebuild_settings.embedding_batch_size`，保持 `tasks_limit = 1`，增大请求间隔 |
| 高质量召回 | 开启图记忆和原子化，`recall_engine.top_k` 设置为 5-8 |
| 成本敏感 | 降低 `top_k`，关闭跨轮次扩展检索，适当增大总结触发轮次 |

## 适配器互通与旧记忆

在设置页的访问与隔离分组中选择“互通的适配器”（`filtering_settings.shared_adapter_ids`）。选中的适配器实例共享记忆候选，未选中的实例各自隔离；同类型的两个机器人也按不同实例 ID 区分。未设置名单时不额外限制适配器，实际范围由会话和人格配置决定；保存其他选项不会自动启用名单；启用名单后，新增加且未选中的适配器默认隔离。

| 适配器 | 会话隔离 | 可见范围 |
| --- | --- | --- |
| 互通 | 开启 | 仍按各自完整会话 ID 隔离 |
| 互通 | 关闭 | 互通实例之间共享候选 |
| 隔离 | 开启 | 当前实例、当前会话 |
| 隔离 | 关闭 | 当前实例的多个会话 |

人格过滤始终遵循原配置；`user` 模式也保留原有平台用户范围，不会把不同平台用户 ID 认作同一个人。原始对话和近期消息缓存仍按真实会话保存。

已有按实际会话保存的旧记忆，可保留 `legacy` 模式并关闭会话过滤，按互通名单共享读取，无需改写旧作用域。例如：

```json
{"filtering_settings":{"memory_scope_mode":"legacy","use_session_filtering":false,"use_persona_filtering":true,"shared_adapter_ids":["qq-main","wechat-main"]}}
```

首次启动会从来源会话、保留原文和旧作用域恢复适配器归属，并同步主文档、原子、图条目和独立图向量文档的元数据。无法唯一确定来源、多个来源混合以及来源已丢失的记录保持未归属；启用名单后，这些记录需要先分配归属才能参与适配器召回。打开设置页的“记忆归属”，可按旧作用域分配未归属记录，也可将旧适配器的记录整体转交给另一个实例。

修改归属前会在插件数据目录的 `backups/adapter_ownership_*` 中备份受影响的 SQLite 库，更新失败回滚事务。迁移保留正文、原始会话、人格和文档 ID，不重新生成 Embedding，也不重建 FAISS 或 FTS。整合与写入去重保留适配器归属，避免把不同来源压成无法拆分的一条记录。

手动批量转交归属前，请暂停相关适配器并等待进行中的总结结束，完成后再恢复；已配置但未启动的适配器仍可选择。迁移只改归属，不改变 `session_id`，因此更换适配器后仍开启会话隔离时，旧会话不会自动变成新会话。

管理 API 可用 `adapter_id` 查询参数选择适配器范围；例如 `page/memories?adapter_id=qq-main` 继续与原有会话条件相交。`POST page/adapters/assign` 接受目标 `adapter_id`、原 `source_adapter_id` 和 `session_id`：原适配器为空时按旧作用域分配未归属记录，指定原适配器时转交该实例的记录。接口沿用 AstrBot 插件视图的鉴权。

## 用户档案

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `profile_settings.enabled` | `true` | 是否启用按「记忆作用域 + 人格」维护的用户档案 |
| `profile_settings.max_chars` | `300` | 档案注入文本的字符上限，独立于记忆注入预算 |
| `profile_settings.min_confidence` | `0.6` | 档案更新直接生效所需的最低置信度，未达标进入待确认候选 |
| `profile_settings.history_limit` | `20` | 每个档案保留的历史版本数量 |
| `profile_settings.max_pending` | `50` | 待确认候选的最大保留条数 |

总结时模型在同一次总结 JSON 中可选输出 `profile_updates`（`basic` / `preferences` / `status` / `task_preferences` 四个分区）：`set` 需要证据与置信度，`append` 去重追加到偏好类列表，`remove` 需要证据。所有生效更新都会写入历史版本，可在 Dashboard 的「用户档案」页查看、编辑、删除并处理待确认候选。档案不占用召回 `top_k` 名额，但计入注入字符预算；关闭 `profile_settings.enabled` 后不读写任何档案。

## 模型 Provider

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `provider_settings.embedding_provider_id` | 空（不建议） | 用于向量化记忆，应填写已启用的 Embedding Provider ID。留空只在 AstrBot 存在可用 Embedding Provider 时回退到第一个，可能选错模型或导致向量/图索引初始化失败 |
| `provider_settings.llm_provider_id` | 空 | 用于总结对话和评估记忆重要性。留空使用 AstrBot 默认 LLM |
| `provider_settings.llm_stream_mode` | `auto` | 记忆处理调用 LLM 的方式：`auto` 优先非流式、遇“不支持非流式”错误自动转流式；`always` 始终流式；`never` 始终非流式（旧行为） |
| `provider_settings.llm_fallback_provider_ids` | `[]` | 主 LLM 不可用时按顺序尝试的备用 Provider ID 列表（JSON 数组）；超时、限流（429）、5xx 或鉴权失败时切换，解析/格式错误不切换；留空不启用回退 |
| `provider_settings.llm_call_timeout_seconds` | `60` | 每个 Provider 单次调用超时（秒，5–600）；超时按该 Provider 不可用处理并切换到下一个 |

建议 Embedding 模型保持稳定，不要频繁更换。更换 Embedding 后，如发现旧记忆召回异常，可执行 `/lmem rebuild-index`。

使用硅基流动免费 BGE-M3 的具体参数、Provider ID 查找方式和验证步骤，见[嵌入模型与 BGE-M3](/guide/embedding-model)。

只接受流式请求（`stream=true`）的模型服务在 `auto` 模式下会自动改用流式调用，无需手动配置；需要强制始终流式时设为 `always`。流式中断、超时或未收到完整响应会按失败处理，不会把不完整结果写入记忆。

配置备用 Provider 后，主 Provider 超时、限流、5xx 或鉴权失败会按顺序切换到下一个备用 Provider；单个 Provider 内部仍只对瞬时错误重试一次，JSON 解析、校验或请求格式错误不会切换 Provider。回退调用会记录实际 Provider ID 与回退次数，可在 `/lmem status` 的用量行查看回退次数。

## 会话管理

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `session_manager.enable_full_group_capture` | `true` | 捕获群聊中未直接 @Bot 的消息，用于建立完整群聊背景 |
| `session_manager.context_window_size` | `50` | 传给总结与上下文分析的历史消息窗口 |
| `session_manager.max_messages_per_session` | `1000` | 单会话数据库保留消息上限 |
| `session_manager.cleanup_batch_size` | `50` | 优先批量清理已总结消息；仍超上限时只额外删除达到上限所需的最旧消息 |

群聊消息量很大时，可以适当降低 `context_window_size` 或关闭全量群聊捕获。超过 `max_messages_per_session` 时会优先删除已总结消息；已总结消息不足时，也会淘汰最旧的未总结缓存并警告，尚未形成记忆的内容可能丢失。正在处理的消息快照按消息 ID 和时间戳定位提交游标，不会因头部清理跳过剩余消息。

## 召回与注入

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `recall_engine.top_k` | `5` | 每轮自动召回的记忆数量 |
| `recall_engine.max_k` | `10` | Agent 主动检索工具允许返回的最大数量 |
| `recall_engine.importance_weight` | `1.0` | 重要性在最终排序中的权重 |
| `recall_engine.min_importance_for_retrieval` | `0.0` | 最低重要性阈值，`0` 表示不过滤 |
| `recall_engine.min_similarity_for_retrieval` | `0.45` | 最低向量相似度；纯关键词命中不受影响；`0` 关闭过滤 |
| `recall_engine.recent_memory_count` | `0` | 相关性结果不足时用近期记忆补位的条数；不再保留固定槽位 |
| `recall_engine.recent_memory_max_age_hours` | `72` | 近期记忆保底的时间窗口 |
| `recall_engine.memory_type_filter` | `all` | 设为 `event_only` 可排除明确的纯偏好/关系记忆 |
| `recall_engine.tokenizer` | `jieba` | 中文分词器：`jieba` 词级分词；`builtin` 内置分词、不导入 jieba、省约 55 MB 内存；切换后需 `/lmem rebuild-index` |
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
| `recall_engine.private_memory_notice_enabled` | `true` | 为 `visibility=private` 且 `disclosed=false` 的记忆追加“用户并不知道”的注入提示；关闭后不追加 |

`extra_user_content` 是唯一支持的注入方式：记忆以 `mark_as_temp` 临时片段追加到当前用户消息末尾，AstrBot 保存对话历史时会自动过滤该片段，因此注入不影响前缀缓存，也**完全不写入 AstrBot 的对话历史**。插件不再改写历史消息格式或向对话历史写入内容。旧的 `user_message_before` / `user_message_after` / `fake_tool_call` / `fake_tool_call_deepseek_v4` / `system_prompt` 均已废弃，配置后自动回退到 `extra_user_content`。

记忆条目支持可选的 `visibility`（`public` / `private`）与 `disclosed`（布尔）元数据，保存在 `documents.metadata` 中，不需要数据库迁移；缺少字段的旧数据按 `public` / `true` 处理。当 `visibility=private` 且 `disclosed=false` 时，自动注入会在该条记忆后追加提示，说明这是机器人私下经历、用户并不知道。提示文案可在提示词管理中修改 `private_memory_injection_notice`，或用 `recall_engine.private_memory_notice_enabled` 整体关闭。

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

## 频道公共记忆

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `channel_memory.enabled` | `false` | 开启后群聊总结写入当前群的频道作用域，群内成员均可召回；关闭时行为与旧版一致 |
| `channel_memory.min_user_slots` | `1` | 联合召回时至少保留的个人/会话作用域结果条数（0–5） |
| `channel_memory.min_channel_slots` | `1` | 联合召回时至少保留的频道作用域结果条数（0–5） |
| `channel_memory.include_archived` | `false` | 默认排除归档记忆；开启后仅对频道作用域结果放宽状态过滤 |

频道作用域形如 `livingmemory:channel:<平台>:<群ID>`，由群聊事件推导；`enabled` 默认关闭，关闭时不改变任何现有写入与召回行为。开启后群聊总结进入频道池，个人记忆（私聊总结、显式写入）仍保存在各自作用域；群聊自动召回会联合当前作用域与频道作用域，按 `top_k` 共享预算，并在可用时各保留 `min_user_slots` / `min_channel_slots` 条。人格、适配器与白名单隔离仍然生效；开启或切换开关不会迁移已有记忆。

## 总结与生命周期

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `reflection_engine.summary_trigger_rounds` | `10` | 达到多少轮对话后触发总结 |
| `reflection_engine.max_messages_per_segment` | `120` | 每段总结的消息条数上限；同一天连续对话也按此分段 |
| `reflection_engine.max_segments_per_run` | `10` | 单次触发最多连续总结的切分片段数；达到上限后剩余积压留到下次触发 |
| `reflection_engine.window_split_by_day` | `true` | 跨天时只总结最老的一天，避免跨天对话合并进同一条记忆 |
| `reflection_engine.window_split_gap_minutes` | `180` | 相邻消息空闲间隔超过该分钟数时只总结最老片段；`0` 关闭空闲间隔切分 |
| `reflection_engine.include_source_time_tags` | `true` | 从原始消息时间写入来源日期标签 |
| `reflection_engine.source_retention_importance_threshold` | `0.8` | 达到阈值时独立保留原始消息 |
| `importance_decay.decay_rate` | `0.005` | 每日重要性衰减比例（默认每天 0.5%） |
| `importance_decay.access_decay_window_days` | `30.0` | 访问强化的时间窗口 |
| `importance_decay.access_decay_max_count` | `10` | 最大访问强化次数 |
| `importance_decay.protected_importance_threshold` | `0.6` | 达到阈值的记忆不参与每日衰减 |

如果你希望机器人更快记住短期上下文，可以降低 `summary_trigger_rounds`；如果希望减少 LLM 调用成本，可以提高它。

总结窗口虽然仍按未总结轮次触发，但不会再跨天或跨长空闲合并：存在边界时按最老的连续片段依次总结，并在同一次后台任务内继续消费后续片段，直到清空积压或达到 `max_segments_per_run`。达到片段上限后，剩余积压会在下一次触发继续消费。消息入库时间优先使用平台消息时间戳，缺失或异常（例如毫秒时间戳、平台时钟明显超前）时回退到接收时间，因此窗口边界与消息实际发生时间一致。

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
| `agent_tools.agentic_recall_max_calls` | `3` | 单次请求内记忆搜索/深读工具共享的最大调用次数（1–20） |
| `agent_tools.agentic_recall_time_budget_seconds` | `20` | 单次请求内记忆工具累计可工作秒数（1–300） |
| `agent_tools.agentic_recall_max_result_chars` | `6000` | Beta 记忆工具单次返回的最大字符数（500–100000），超出部分可按游标续读 |

主动写入工具更强，也更需要模型自律。建议先只开启主动回忆，确认效果稳定后再启用主动写入。

主动遗忘工具默认关闭，开启后也只对管理员生效：模型只能把记忆归档（从检索中移除，documents 记录保留），不会物理删除；管理员可在 Dashboard 记忆页用 `archived` 状态筛选后改回 `active` 恢复。单次调用条数受限，并有按会话的冷却时间。

自主回忆为 Beta 功能，默认关闭。开启后 `recall_long_term_memory` 支持按当前发言人检索、事件时间范围过滤、排除已看候选与游标分页，并注册 `read_memory_evidence`，按记忆 ID 读取有效事实与（可验证时）保留原文。工具调用次数、累计耗时与单次返回长度共享单次请求预算；回忆规则以临时片段追加注入。开关与三项预算始终显示在设置页，保存并成功重载插件后对新请求生效。

每段输入最多 `max_messages_per_segment` 条，跨天或空闲边界更早时先在边界切分；同一任务继续处理后续片段，直到达到 `max_segments_per_run`。失败重试从仍未总结的位置开始，放弃连续失败片段也只跳过当前片段。反思触发的整合在积压任务结束后调度，整合与反思持有同一写入锁，LLM 推理仍并行执行。

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
| `backup_settings.keep_days` | `7` | 每日备份保留天数；版本备份额外只保留最近 2 份，备份总量上限 512 MB |
| `forgetting_agent.auto_cleanup_enabled` | `true` | 每日清理久远且低重要性记忆 |
| `forgetting_agent.auto_archived_enabled` | `true` | 将清理候选归档并移出检索索引，而非永久删除 |
| `forgetting_agent.cleanup_days_threshold` | `120` | 进入清理候选的天数 |
| `forgetting_agent.cleanup_importance_threshold` | `0.25` | 清理候选的重要性阈值 |

生产使用建议保持备份和迁移备份开启。自动归档默认开启，原始文档仍可在 Dashboard 查看和恢复，恢复时会重新生成 Embedding 并重建 BM25、图谱和记忆原子索引。启动和每日备份后会按保留策略自动清理过期备份（版本备份保留最近 2 份、备份总量上限 512 MB）。

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

## 数据库打开错误

`unable to open database file` 对应 `SQLITE_CANTOPEN`，与 `database is locked` 的 `SQLITE_BUSY` 分开排查（[#345](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/issues/345)）。访问统计写入失败时，日志会包含主数据库的 `db_path`、底层 `sqlite_errorcode`、`sqlite_errorname` 和完整堆栈；支持扩展错误码的环境会保留其原值。

请按日志中的路径确认数据库父目录可用、运行 AstrBot 的账户具有写权限，并在反馈时提供这条日志和完整堆栈。2.7.3 已修复异常、取消或提交失败后残留写事务的问题；仍出现打开失败时，需要依据实际错误码继续定位。

`disk I/O error` 对应 `SQLITE_IOERR`：数据目录位于 Docker Desktop bind mount、网络盘等共享文件系统时，SQLite 无法使用 WAL 的 `-shm` 共享内存索引，新建连接加入 WAL 会失败。插件启动时会识别挂载类型并探测 WAL，不可用就自动回退 rollback journal，并在日志中提示迁移数据目录；把数据目录放到 Docker 命名卷或本地磁盘后会自动恢复 WAL。

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

## 召回追踪

`trace_settings` 记录每次记忆召回的可解释轨迹：各检索路线的候选数与耗时、被检索策略拒绝的原因，以及最终注入的记忆条数与字符数。轨迹保存在进程内的环形缓冲中（`max_records`），由 `/lmem trace` 与 Page API 查询；默认不写盘，默认不保存查询原文，只保存 `query_hash`。

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `trace_settings.enabled` | `true` | 关闭后召回链路不再记录任何追踪信息 |
| `trace_settings.max_records` | `50` | 进程内保留的最近追踪条数，`0` 表示关闭 |
| `trace_settings.persist_enabled` | `false` | 开启后按天写入插件数据目录的 `recall_traces` 目录 |
| `trace_settings.include_query_text` | `false` | 是否保存查询原文，默认只保存哈希 |
| `trace_settings.max_file_mb` | `10` | 单个 JSONL 文件大小上限，达到后当天不再追加 |
| `trace_settings.retention_days` | `7` | 追踪文件保留天数，过期文件在下次写入时清理 |

开启 `persist_enabled` 后，追踪按天写入 `recall_traces` 目录；Page API 提供 `traces`、`traces/stats` 查询追踪，`memories` 血缘接口返回来源消息、取代链、图关联与命中该记忆的追踪。
