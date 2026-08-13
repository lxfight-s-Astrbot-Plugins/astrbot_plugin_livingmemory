# Configuration

LivingMemory defaults work for most users. The settings you usually need to touch are model providers, recall size, memory isolation, graph retrieval, backup, and cleanup.

## Recommended profiles

| Scenario | Recommendation |
| --- | --- |
| First-time setup | Configure only `provider_settings.llm_provider_id` and `provider_settings.embedding_provider_id`; keep the rest at defaults |
| Private long-term assistant | Keep persona and session filtering enabled; keep `summary_trigger_rounds` around 8-12 |
| Group companion | Enable `session_manager.enable_full_group_capture` and consider a larger `context_window_size` |
| Low-resource server | Reduce `index_rebuild_settings.embedding_batch_size`, keep `tasks_limit = 1`, and increase request delays |
| Higher recall quality | Keep graph memory and atomization enabled; set `recall_engine.top_k` to 5-8 |
| Cost-sensitive setup | Lower `top_k`, disable recent-context expansion, and increase summary trigger rounds |

## Model providers

| Key | Default | Description |
| --- | --- | --- |
| `provider_settings.embedding_provider_id` | empty | Generates memory vectors. Empty means AstrBot's default embedding provider |
| `provider_settings.llm_provider_id` | empty | Summarizes conversations and evaluates memory importance. Empty means AstrBot's default LLM |

Try to keep the embedding model stable. If you change it and old memories recall poorly, run `/lmem rebuild-index`.

## Session management

| Key | Default | Description |
| --- | --- | --- |
| `session_manager.enable_full_group_capture` | `true` | Captures group messages that do not directly mention the bot |
| `session_manager.context_window_size` | `50` | Historical message window used for summarization and context analysis |
| `session_manager.max_messages_per_session` | `1000` | Maximum stored messages for one session |
| `session_manager.cleanup_batch_size` | `50` | Number of old summarized messages cleaned per batch |

For very busy group chats, lower `context_window_size` or disable full group capture.

## Recall and injection

| Key | Default | Description |
| --- | --- | --- |
| `recall_engine.top_k` | `5` | Number of memories automatically recalled each turn |
| `recall_engine.max_k` | `10` | Maximum results returned by active agent recall |
| `recall_engine.importance_weight` | `1.0` | Importance weight in final ranking |
| `recall_engine.min_importance_for_retrieval` | `0.0` | Minimum importance; `0` disables the filter |
| `recall_engine.min_similarity_for_retrieval` | `0.0` | Minimum vector similarity; keyword-only hits remain eligible |
| `recall_engine.recent_memory_count` | `2` | Recall slots reserved for recent memories |
| `recall_engine.recent_memory_max_age_hours` | `72` | Time window for recent-memory slots |
| `recall_engine.memory_type_filter` | `all` | Use `event_only` to exclude known preference-only or relationship-only memories |
| `recall_engine.fallback_to_vector` | `true` | Falls back to vector search if hybrid retrieval fails |
| `recall_engine.injection_method` | `extra_user_content` | Where or how recalled memories are injected |
| `recall_engine.inject_with_recent_context` | `false` | Expands the query with recent conversation |
| `recall_engine.search_cache_enabled` | `true` | Enables short-term retrieval caching |

`extra_user_content` is the safest default. Gemini providers automatically fall back from `fake_tool_call` to `extra_user_content`. DeepSeek V4 thinking mode can now use normal `fake_tool_call` on recent AstrBot versions; the legacy `fake_tool_call_deepseek_v4` option is kept only as a compatibility alias and automatically falls back to `fake_tool_call`.

## Memory isolation

| Key | Default | Description |
| --- | --- | --- |
| `filtering_settings.use_persona_filtering` | `true` | Only recall memories for the current persona |
| `filtering_settings.memory_scope_mode` | `legacy` | Preserve legacy behavior, or scope by session, user, or globally |
| `filtering_settings.use_session_filtering` | `true` | Controls session filtering only in `legacy` mode |
| `filtering_settings.isolated_sessions` | Empty | Full session IDs that must always remain isolated, one per line |
| `access_control.whitelist_enabled` | `false` | Allow only listed identities to use long-term memory |
| `access_control.allowed_ids` | Empty | User ID, `platform:user ID`, group ID, or full session ID |
| `access_control.identity_aliases` | Empty | One `source identity=canonical name` mapping per line |

`user` shares memory across private and group chats for the same platform user. `global` shares memory across every non-isolated session. `isolated_sessions` always takes precedence. In `legacy` mode with session filtering disabled, configuring any isolated session moves non-isolated writes into the dedicated global scope so they cannot read isolated data. Enabling the allowlist with an empty list denies automatic capture, summarization, recall, and Agent memory tools.

Aliases are matched in this order: `platform:user ID`, user ID, then current username. The mapped display name is applied before summarization. Scope changes affect newly written memories only; existing memories are not migrated or re-embedded automatically.

## Reflection and lifecycle

| Key | Default | Description |
| --- | --- | --- |
| `reflection_engine.summary_trigger_rounds` | `10` | Number of conversation rounds before summarization |
| `reflection_engine.include_source_time_tags` | `true` | Derives source date tags from original message timestamps |
| `reflection_engine.source_retention_importance_threshold` | `0.8` | Retains original messages separately at or above the threshold |
| `importance_decay.decay_rate` | `0.01` | Daily importance decay |
| `importance_decay.access_decay_window_days` | `30.0` | Time window for access reinforcement |
| `importance_decay.access_decay_max_count` | `10` | Maximum access reinforcement count |
| `importance_decay.protected_importance_threshold` | `1.0` | Memories at or above this importance do not decay |

Lower `summary_trigger_rounds` if you want the bot to remember faster. Raise it if you want fewer LLM calls.

Retained source is written only to the SQLite `memory_sources` table, not to vector, BM25, graph, or atom indexes, so it does not increase vector count. It does increase database disk usage. Re-summarizing from Dashboard calls the LLM once, replaces the old memory, and regenerates its embedding and all derived indexes.

## Agent tools

| Key | Default | Description |
| --- | --- | --- |
| `agent_tools.enable_recall_tool` | `true` | Registers `recall_long_term_memory` for active recall |
| `agent_tools.enable_memorize_tool` | `false` | Registers `memorize_long_term_memory` for active writes |

The write tool is powerful and depends on model discipline. Start with active recall, then enable active writes after observing stable behavior.

## Graph memory and atomization

| Key | Default | Description |
| --- | --- | --- |
| `graph_memory.enabled` | `true` | Enables graph-route retrieval |
| `graph_memory.document_route_weight` | `0.65` | Document-route weight |
| `graph_memory.graph_route_weight` | `0.35` | Graph-route weight |
| `graph_memory.cross_route_bonus` | `0.08` | Bonus when both routes hit the same memory |
| `graph_memory.expansion_hops` | `1` | Graph neighbor expansion hops |
| `graph_memory.dynamic_route_weighting` | `true` | Adjusts route weights based on query intent |
| `graph_memory.atom_enabled` | `true` | Enables memory atomization |

For relationship-heavy use, increase graph-route weight or set `expansion_hops` to `2`. If the database is large, second-hop expansion adds query cost, so use the WebUI recall debugger to inspect results first.

## Backup, migration, and cleanup

| Key | Default | Description |
| --- | --- | --- |
| `migration_settings.auto_migrate` | `true` | Migrates old databases at startup |
| `migration_settings.create_backup` | `true` | Creates a backup before migration |
| `backup_settings.enabled` | `true` | Daily database backup |
| `backup_settings.keep_days` | `7` | Backup retention days |
| `forgetting_agent.auto_cleanup_enabled` | `true` | Daily cleanup for old low-importance memories |
| `forgetting_agent.auto_archived_enabled` | `false` | Archives cleanup candidates outside retrieval indexes instead of deleting them |
| `forgetting_agent.cleanup_days_threshold` | `30` | Age threshold for cleanup candidates |
| `forgetting_agent.cleanup_importance_threshold` | `0.3` | Importance threshold for cleanup candidates |

With automatic archiving enabled, source documents remain visible and restorable in the Dashboard. Restoring regenerates the embedding and rebuilds BM25, graph, and memory-atom indexes.

## Memory store consolidation

| Key | Default | Description |
| --- | --- | --- |
| `memory_consolidation.enabled` | `false` | Enables periodic memory-store consolidation |
| `memory_consolidation.trigger` | `daily` | Trigger mode: `daily` = daily schedule, `reflection` = piggyback on each reflection |
| `memory_consolidation.granularity` | `session` | Aggregation granularity: `session` = same session, `semantic` = cross-session semantic clustering |
| `memory_consolidation.keep_original` | `archive` | Handling of originals after merge: `archive` = keep archived, `delete` = remove permanently |
| `memory_consolidation.min_memories_per_group` | `3` | Minimum memories per group to trigger consolidation |
| `memory_consolidation.min_age_days` | `7` | Only consolidate memories older than this many days |
| `memory_consolidation.max_importance` | `0.5` | Only consolidate memories below this importance |
| `memory_consolidation.max_groups_per_run` | `5` | Maximum groups consolidated per run |
| `memory_consolidation.semantic_similarity_threshold` | `0.7` | Minimum similarity in semantic clustering mode |

Memory consolidation controls the memory-store size at the source: scattered low-value memories are aggregated, organized, and summarized into a single concise memory, avoiding information loss from hard truncation at injection time. The merged result is written as a new memory and the originals are archived or deleted according to `keep_original`. With `trigger=reflection`, a 6-hour cooldown prevents per-message triggering.

## Index rebuild tuning

| Key | Default | Description |
| --- | --- | --- |
| `index_rebuild_settings.batch_size` | `50` | Memories read per batch |
| `index_rebuild_settings.embedding_batch_size` | `8` | Texts per embedding request |
| `index_rebuild_settings.tasks_limit` | `1` | Embedding concurrency limit |
| `index_rebuild_settings.max_retries` | `5` | Retry count for a failed batch |
| `index_rebuild_settings.request_delay` | `5.0` | Delay between embedding requests |
| `index_rebuild_settings.max_failure_ratio` | `0.02` | Allowed failure ratio |

If you hit API rate limits, increase `request_delay` first, then lower `embedding_batch_size`. Avoid raising concurrency blindly; index rebuilds are more about finishing reliably than finishing aggressively.
