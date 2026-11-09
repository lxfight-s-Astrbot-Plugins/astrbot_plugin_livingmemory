"""
config_validator.py - 配置验证模块

Pydantic 模型是配置的唯一校验源：字段类型、范围与枚举约束全部在模型中声明，
插件启动时按模型校验原始配置，非法配置直接报错而不是静默修正。
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class SessionManagerConfig(BaseModel):
    """会话管理器配置"""

    max_sessions: int = Field(default=100, ge=1, le=10000)
    session_ttl: int = Field(default=3600, ge=60, le=86400)
    context_window_size: int = Field(default=50, ge=1, le=1000)
    enable_full_group_capture: bool = True
    max_messages_per_session: int = Field(default=1000, ge=100, le=10000)
    cleanup_batch_size: int = Field(default=50, ge=1, le=1000)


class RecallEngineConfig(BaseModel):
    """回忆引擎配置"""

    top_k: int = Field(default=5, ge=0, le=50)
    max_k: int = Field(default=10, ge=1, le=50)
    importance_weight: float = Field(default=1.0, ge=0.0, le=10.0)
    min_importance_for_retrieval: float = Field(default=0.0, ge=0.0, le=1.0)
    min_similarity_for_retrieval: float = Field(default=0.45, ge=0.0, le=1.0)
    recent_memory_count: int = Field(default=0, ge=0, le=20)
    recent_memory_max_age_hours: int = Field(default=72, ge=0, le=8760)
    memory_type_filter: str = Field(default="all", pattern="^(all|event_only)$")
    fallback_to_vector: bool = True
    rerank_enabled: bool = False
    rerank_provider_id: str = ""
    rerank_candidates: int = Field(default=20, ge=2, le=100)
    # 旧注入方式仅保留为合法枚举值，避免老配置升级后启动失败；运行时统一按
    # extra_user_content 处理（见 ConfigManager）。
    injection_method: Literal[
        "extra_user_content",
        "user_message_before",
        "user_message_after",
        "fake_tool_call",
        "fake_tool_call_deepseek_v4",
        "system_prompt",
    ] = "extra_user_content"
    auto_remove_injected: bool = True
    inject_with_recent_context: bool = False
    recent_context_max_age_seconds: int = Field(default=7200, ge=0, le=604800)
    search_cache_enabled: bool = True
    search_cache_ttl_seconds: float = Field(default=45.0, ge=0.0, le=600.0)
    search_cache_max_size: int = Field(default=256, ge=0, le=10000)
    fusion_mode: str = "score"
    fusion_bm25_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    recall_candidate_multiplier: int = Field(default=3, ge=1, le=10)
    fusion_agreement_bonus: float = Field(default=0.25, ge=0.0, le=1.0)


class FusionStrategyConfig(BaseModel):
    """结果融合策略配置"""

    rrf_k: int = Field(default=60, ge=1, le=1000)


class ReflectionEngineConfig(BaseModel):
    """反思引擎配置"""

    summary_trigger_rounds: int = Field(default=10, ge=1, le=100)
    max_messages_per_segment: int = Field(default=120, ge=2, le=1000)
    max_segments_per_run: int = Field(default=10, ge=1, le=50)
    window_split_by_day: bool = True
    window_split_gap_minutes: int = Field(default=180, ge=0, le=1440)
    include_source_time_tags: bool = True
    source_retention_importance_threshold: float = Field(default=0.8, ge=0.0, le=1.0)


class AgentToolsConfig(BaseModel):
    """Agent 工具配置"""

    enable_recall_tool: bool = True
    enable_agentic_recall_beta: bool = False
    enable_memorize_tool: bool = False
    enable_forget_tool: bool = False
    forget_tool_max_memories: int = Field(default=3, ge=1, le=10)
    forget_tool_cooldown_seconds: int = Field(default=60, ge=0, le=3600)
    agentic_recall_max_calls: int = Field(default=4, ge=1, le=20)
    agentic_recall_time_budget_seconds: int = Field(default=20, ge=1, le=300)
    agentic_recall_max_result_chars: int = Field(default=12000, ge=500, le=100000)


class ForgettingAgentConfig(BaseModel):
    """遗忘代理配置"""

    auto_cleanup_enabled: bool = True
    auto_archived_enabled: bool = True
    cleanup_days_threshold: int = Field(default=120, ge=1, le=3650)
    cleanup_importance_threshold: float = Field(default=0.25, ge=0.0, le=1.0)


class FilteringConfig(BaseModel):
    """过滤配置"""

    use_persona_filtering: bool = True
    use_session_filtering: bool = True
    memory_scope_mode: str = Field(
        default="legacy", pattern="^(legacy|session|user|global)$"
    )
    isolated_sessions: str = ""
    shared_adapter_ids: list[str] | None = None


class AccessControlConfig(BaseModel):
    """记忆访问控制与身份映射配置。"""

    whitelist_enabled: bool = False
    allowed_ids: str = ""
    identity_aliases: str = ""


class ProviderConfig(BaseModel):
    """Provider配置"""

    embedding_provider_id: str | None = None
    llm_provider_id: str | None = None
    llm_stream_mode: str = "auto"


class ImportanceDecayConfig(BaseModel):
    """重要性衰减配置"""

    decay_rate: float = Field(default=0.005, ge=0.0, le=1.0)
    access_decay_window_days: float = Field(default=30.0, ge=1.0, le=3650.0)
    access_decay_max_count: int = Field(default=10, ge=1, le=10000)
    access_count_decay_multiplier: float = Field(default=0.5, ge=0.0, le=1.0)
    protected_importance_threshold: float = Field(default=0.6, ge=0.0, le=1.0)


class MigrationSettings(BaseModel):
    """数据库迁移设置"""

    auto_migrate: bool = True
    create_backup: bool = True


class BackupSettings(BaseModel):
    """定期备份设置"""

    enabled: bool = True
    keep_days: int = Field(default=7, ge=1, le=365)


class IndexRebuildSettings(BaseModel):
    """索引重建设置"""

    batch_size: int = Field(default=50, ge=1, le=500)
    embedding_batch_size: int = Field(default=8, ge=1, le=256)
    tasks_limit: int = Field(default=1, ge=1, le=8)
    max_retries: int = Field(default=5, ge=1, le=8)
    retry_base_delay: float = Field(default=30.0, ge=0.0, le=60.0)
    batch_delay: float = Field(default=5.0, ge=0.0, le=10.0)
    request_delay: float = Field(default=5.0, ge=0.0, le=60.0)
    max_failure_ratio: float = Field(default=0.02, ge=0.0, le=1.0)


class GraphMemoryConfig(BaseModel):
    """Graph-memory retrieval configuration."""

    enabled: bool = True
    document_route_weight: float = Field(default=0.65, ge=0.0, le=1.0)
    graph_route_weight: float = Field(default=0.35, ge=0.0, le=1.0)
    cross_route_bonus: float = Field(default=0.08, ge=0.0, le=0.5)
    expansion_limit: int = Field(default=24, ge=1, le=200)
    expansion_hops: int = Field(default=1, ge=1, le=2)
    second_hop_weight: float = Field(default=0.4, ge=0.0, le=1.0)
    dynamic_route_weighting: bool = True
    max_topics_per_memory: int = Field(default=6, ge=1, le=20)
    max_participants_per_memory: int = Field(default=8, ge=1, le=30)
    max_facts_per_memory: int = Field(default=8, ge=1, le=30)
    # Atom-level memory configuration
    atom_enabled: bool = True
    atom_maintenance_interval_hours: float = Field(default=24.0, ge=1.0, le=168.0)
    atom_forget_delay_days: float = Field(default=7.0, ge=1.0, le=90.0)
    atom_purge_delay_days: float = Field(default=30.0, ge=1.0, le=365.0)


class MemoryConsolidationConfig(BaseModel):
    """记忆库定期整合配置"""

    enabled: bool = False
    trigger: str = Field(default="daily", pattern="^(daily|reflection)$")
    granularity: str = Field(default="session", pattern="^(session|semantic)$")
    keep_original: str = Field(default="archive", pattern="^(archive|delete)$")
    min_memories_per_group: int = Field(default=3, ge=2, le=50)
    min_age_days: int = Field(default=7, ge=0, le=3650)
    max_importance: float = Field(default=0.5, ge=0.0, le=1.0)
    max_groups_per_run: int = Field(default=5, ge=1, le=100)
    semantic_similarity_threshold: float = Field(default=0.7, ge=0.0, le=1.0)


class MemoryDedupConfig(BaseModel):
    """写入时近重复检测与合并配置。"""

    enabled: bool = True
    similarity_threshold: float = Field(default=0.92, ge=0.0, le=1.0)
    max_candidates: int = Field(default=5, ge=1, le=20)
    merge_sources: bool = True
    max_source_messages: int = Field(default=50, ge=1, le=500)


class LivingMemoryConfig(BaseModel):
    """完整插件配置"""

    session_manager: SessionManagerConfig = Field(default_factory=SessionManagerConfig)
    recall_engine: RecallEngineConfig = Field(default_factory=RecallEngineConfig)
    reflection_engine: ReflectionEngineConfig = Field(
        default_factory=ReflectionEngineConfig
    )
    agent_tools: AgentToolsConfig = Field(default_factory=AgentToolsConfig)
    forgetting_agent: ForgettingAgentConfig = Field(
        default_factory=ForgettingAgentConfig
    )
    access_control: AccessControlConfig = Field(default_factory=AccessControlConfig)
    filtering_settings: FilteringConfig = Field(default_factory=FilteringConfig)
    provider_settings: ProviderConfig = Field(default_factory=ProviderConfig)
    migration_settings: MigrationSettings = Field(default_factory=MigrationSettings)
    index_rebuild_settings: IndexRebuildSettings = Field(
        default_factory=IndexRebuildSettings
    )
    backup_settings: BackupSettings = Field(default_factory=BackupSettings)
    graph_memory: GraphMemoryConfig = Field(default_factory=GraphMemoryConfig)
    fusion_strategy: FusionStrategyConfig = Field(default_factory=FusionStrategyConfig)
    importance_decay: ImportanceDecayConfig = Field(
        default_factory=ImportanceDecayConfig
    )
    memory_consolidation: MemoryConsolidationConfig = Field(
        default_factory=MemoryConsolidationConfig
    )
    memory_dedup: MemoryDedupConfig = Field(default_factory=MemoryDedupConfig)

    model_config = {"extra": "allow"}  # 允许额外字段，向前兼容


def validate_config(raw_config: dict[str, Any]) -> LivingMemoryConfig:
    """按 Pydantic 模型校验配置。

    Args:
        raw_config: 原始配置字典。

    Returns:
        校验后的配置对象。

    Raises:
        pydantic.ValidationError: 类型不符、越界或枚举值非法。
    """
    return LivingMemoryConfig(**raw_config)


def get_default_config() -> dict[str, Any]:
    """获取默认配置字典。

    Returns:
        所有配置项的默认值。
    """
    return LivingMemoryConfig().model_dump()
