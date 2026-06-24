"""
config_validator.py - 配置验证模块
提供配置验证和默认值管理功能。
"""

from typing import Any

from pydantic import BaseModel, Field, model_validator

from astrbot.api import logger


class SessionManagerConfig(BaseModel):
    """会话管理器配置"""

    max_sessions: int = Field(
        default=100, ge=1, le=10000, description="最大会话缓存数量"
    )
    session_ttl: int = Field(
        default=3600, ge=60, le=86400, description="会话生存时间（秒）"
    )
    context_window_size: int = Field(
        default=50, ge=1, le=1000, description="上下文窗口大小"
    )
    enable_full_group_capture: bool = Field(
        default=True, description="是否捕获群聊中的所有消息(包括非@Bot的消息)"
    )
    max_messages_per_session: int = Field(
        default=1000,
        ge=100,
        le=10000,
        description="单会话最大消息数量(超出后自动删除旧消息)",
    )
    cleanup_batch_size: int = Field(
        default=50,
        ge=1,
        le=1000,
        description="历史消息超过上限后每次批量删除的旧已总结消息数",
    )


class RecallEngineConfig(BaseModel):
    """回忆引擎配置"""

    top_k: int = Field(
        default=5, ge=0, le=50, description="返回记忆数量。设为 0 则跳过自动召回和注入"
    )
    max_k: int = Field(
        default=10, ge=1, le=50, description="Agent 主动检索时允许的最大返回数量"
    )
    importance_weight: float = Field(
        default=1.0, ge=0.0, le=10.0, description="重要性权重"
    )
    fallback_to_vector: bool = Field(default=True, description="是否启用向量检索回退")
    injection_method: str = Field(
        default="extra_user_content",
        description=(
            "记忆注入方式: "
            "extra_user_content(推荐，临时消息追加到用户消息末尾，不影响前缀缓存且不污染对话历史), "
            "user_message_before(用户消息前), "
            "user_message_after(用户消息后), "
            "fake_tool_call(伪造工具调用), "
            "fake_tool_call_deepseek_v4(已废弃，自动回退至fake_tool_call), "
            "system_prompt(已废弃，自动回退至extra_user_content)"
        ),
    )
    auto_remove_injected: bool = Field(
        default=True, description="是否自动删除对话历史中已注入的记忆片段"
    )
    inject_with_recent_context: bool = Field(
        default=False,
        description="启用后使用最近2轮对话作为扩展查询关键词，提升检索精准度",
    )
    search_cache_enabled: bool = Field(
        default=True, description="是否启用短期检索结果缓存"
    )
    search_cache_ttl_seconds: float = Field(
        default=45.0, ge=0.0, le=600.0, description="检索缓存 TTL 秒数"
    )
    search_cache_max_size: int = Field(
        default=256, ge=0, le=10000, description="检索缓存最大条目数"
    )


class FusionStrategyConfig(BaseModel):
    """结果融合策略配置"""

    rrf_k: int = Field(default=60, ge=1, le=1000, description="RRF参数k")


class ReflectionEngineConfig(BaseModel):
    """反思引擎配置"""

    summary_trigger_rounds: int = Field(
        default=10, ge=1, le=100, description="触发反思的对话轮次"
    )


class AgentToolsConfig(BaseModel):
    """Agent 工具配置"""

    enable_recall_tool: bool = Field(
        default=True, description="是否启用 Agent 主动回忆工具"
    )
    enable_memorize_tool: bool = Field(
        default=False, description="是否启用 Agent 主动记忆写入工具"
    )


class ForgettingAgentConfig(BaseModel):
    """遗忘代理配置"""

    auto_cleanup_enabled: bool = Field(
        default=True, description="是否启用每日自动清理旧记忆"
    )
    cleanup_days_threshold: int = Field(
        default=30, ge=1, le=3650, description="清理天数阈值"
    )
    cleanup_importance_threshold: float = Field(
        default=0.3, ge=0.0, le=1.0, description="清理重要性阈值"
    )


class FilteringConfig(BaseModel):
    """过滤配置"""

    use_persona_filtering: bool = Field(default=True, description="是否使用人格过滤")
    use_session_filtering: bool = Field(default=True, description="是否使用会话过滤")


class ProviderConfig(BaseModel):
    """Provider配置"""

    embedding_provider_id: str | None = Field(
        default=None, description="Embedding Provider ID"
    )
    llm_provider_id: str | None = Field(default=None, description="LLM Provider ID")


class ImportanceDecayConfig(BaseModel):
    """重要性衰减配置"""

    decay_rate: float = Field(default=0.01, ge=0.0, le=1.0, description="每日衰减率")
    access_decay_window_days: float = Field(
        default=30.0, ge=1.0, le=3650.0, description="访问频次强化的有效窗口天数"
    )
    access_decay_max_count: int = Field(
        default=10, ge=1, le=10000, description="抵消衰减所需的访问次数上限"
    )
    access_count_decay_multiplier: float = Field(
        default=0.5, ge=0.0, le=1.0, description="每日衰减后访问次数保留比例"
    )


class MigrationSettings(BaseModel):
    """数据库迁移设置"""

    auto_migrate: bool = Field(default=True, description="是否启用自动迁移")
    create_backup: bool = Field(default=True, description="迁移前是否创建备份")


class IndexRebuildSettings(BaseModel):
    """索引重建设置"""

    batch_size: int = Field(default=50, ge=1, le=500, description="重建读取批量")
    embedding_batch_size: int = Field(
        default=8, ge=1, le=256, description="Embedding 请求批量"
    )
    tasks_limit: int = Field(default=1, ge=1, le=8, description="Embedding 并发上限")
    max_retries: int = Field(default=5, ge=1, le=8, description="批次最大重试次数")
    retry_base_delay: float = Field(
        default=30.0, ge=0.0, le=60.0, description="重试基础等待秒数"
    )
    batch_delay: float = Field(
        default=5.0, ge=0.0, le=10.0, description="读取批次间隔秒数"
    )
    request_delay: float = Field(
        default=5.0, ge=0.0, le=60.0, description="Embedding 请求间隔秒数"
    )
    max_failure_ratio: float = Field(
        default=0.02, ge=0.0, le=1.0, description="允许切换的最大失败比例"
    )


class GraphMemoryConfig(BaseModel):
    """Graph-memory retrieval configuration."""

    enabled: bool = Field(default=True, description="是否启用图记忆双路检索")
    document_route_weight: float = Field(
        default=0.65, ge=0.0, le=1.0, description="文档路权重"
    )
    graph_route_weight: float = Field(
        default=0.35, ge=0.0, le=1.0, description="图路权重"
    )
    cross_route_bonus: float = Field(
        default=0.08, ge=0.0, le=0.5, description="双路同时命中的额外加分"
    )
    expansion_limit: int = Field(
        default=24, ge=1, le=200, description="图邻居扩展候选上限"
    )
    expansion_hops: int = Field(
        default=1, ge=1, le=2, description="图关键词检索邻居扩展跳数"
    )
    second_hop_weight: float = Field(
        default=0.4, ge=0.0, le=1.0, description="二跳图扩展候选权重"
    )
    dynamic_route_weighting: bool = Field(
        default=True, description="是否按查询意图动态调整文档路和图路权重"
    )
    max_topics_per_memory: int = Field(
        default=6, ge=1, le=20, description="单条记忆最多索引主题数"
    )
    max_participants_per_memory: int = Field(
        default=8, ge=1, le=30, description="单条记忆最多索引参与者数"
    )
    max_facts_per_memory: int = Field(
        default=8, ge=1, le=30, description="单条记忆最多索引事实数"
    )
    # Atom-level memory configuration
    atom_enabled: bool = Field(
        default=True, description="是否启用记忆原子化（细化粒度+时间衰减）"
    )
    atom_maintenance_interval_hours: float = Field(
        default=24.0, ge=1.0, le=168.0, description="原子生命周期维护间隔(小时)"
    )
    atom_forget_delay_days: float = Field(
        default=7.0, ge=1.0, le=90.0, description="过期原子延迟遗忘天数"
    )
    atom_purge_delay_days: float = Field(
        default=30.0, ge=1.0, le=365.0, description="遗忘原子物理清理延迟天数"
    )

    @model_validator(mode="after")
    def validate_route_weights(self):
        """Normalize route weights to sum to 1.0 for numerically stable fusion."""
        total = self.document_route_weight + self.graph_route_weight
        if total <= 0:
            self.document_route_weight = 0.65
            self.graph_route_weight = 0.35
        elif total != 1.0:
            self.document_route_weight = self.document_route_weight / total
            self.graph_route_weight = self.graph_route_weight / total
        return self


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
    filtering_settings: FilteringConfig = Field(default_factory=FilteringConfig)
    provider_settings: ProviderConfig = Field(default_factory=ProviderConfig)
    migration_settings: MigrationSettings = Field(default_factory=MigrationSettings)
    index_rebuild_settings: IndexRebuildSettings = Field(
        default_factory=IndexRebuildSettings
    )
    graph_memory: GraphMemoryConfig = Field(default_factory=GraphMemoryConfig)
    fusion_strategy: FusionStrategyConfig = Field(
        default_factory=FusionStrategyConfig, description="结果融合策略配置"
    )
    importance_decay: ImportanceDecayConfig = Field(
        default_factory=ImportanceDecayConfig, description="重要性衰减配置"
    )

    model_config = {"extra": "allow"}  # 允许额外字段，向前兼容


def validate_config(raw_config: dict[str, Any]) -> LivingMemoryConfig:
    """
    验证并返回规范化的配置对象。

    Args:
        raw_config: 原始配置字典

    Returns:
        LivingMemoryConfig: 验证后的配置对象

    Raises:
        ValueError: 配置验证失败
    """
    try:
        config = LivingMemoryConfig(**raw_config)
        logger.info("配置验证成功")
        return config
    except Exception as e:
        logger.error(f"配置验证失败: {e}")
        raise ValueError(f"插件配置无效: {e}") from e


def get_default_config() -> dict[str, Any]:
    """
    获取默认配置字典。

    Returns:
        dict[str, Any]: 默认配置
    """
    return LivingMemoryConfig().model_dump()


def merge_config_with_defaults(user_config: dict[str, Any]) -> dict[str, Any]:
    """
    将用户配置与默认配置合并。

    Args:
        user_config: 用户提供的配置

    Returns:
        dict[str, Any]: 合并后的配置
    """
    default_config = get_default_config()

    def deep_merge(default: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
        """深度合并两个字典"""
        result = default.copy()
        for key, value in user.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    merged = deep_merge(default_config, user_config)
    logger.debug("配置已与默认值合并")
    return merged


def validate_runtime_config_changes(
    current_config: LivingMemoryConfig, changes: dict[str, Any]
) -> bool:
    """
    验证运行时配置更改是否有效。

    Args:
        current_config: 当前配置
        changes: 要更改的配置项

    Returns:
        bool: 是否有效
    """
    try:
        # 创建更新后的配置副本进行验证
        updated_dict = current_config.model_dump()

        def update_nested_dict(target: dict[str, Any], updates: dict[str, Any]):
            for key, value in updates.items():
                if "." in key:
                    # 处理嵌套键，如 "recall_engine.top_k"
                    parts = key.split(".")
                    current = target
                    for part in parts[:-1]:
                        if part not in current:
                            current[part] = {}
                        current = current[part]
                    current[parts[-1]] = value
                else:
                    target[key] = value

        update_nested_dict(updated_dict, changes)

        # 验证更新后的配置
        LivingMemoryConfig(**updated_dict)
        return True

    except Exception as e:
        logger.error(f"运行时配置更改验证失败: {e}")
        return False
