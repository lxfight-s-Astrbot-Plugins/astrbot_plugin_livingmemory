# -*- coding: utf-8 -*-
"""
config_validator.py - 配置验证模块
提供配置验证和默认值管理功能。
"""

from typing import Dict, Any, Optional
from pydantic import BaseModel, Field, model_validator
from astrbot.api import logger


class SessionManagerConfig(BaseModel):
    """会话管理器配置"""

    max_sessions: int = Field(default=1000, ge=1, le=10000, description="最大会话数量")
    session_ttl: int = Field(
        default=3600, ge=60, le=86400, description="会话生存时间（秒）"
    )


class RecallEngineConfig(BaseModel):
    """回忆引擎配置"""

    top_k: int = Field(default=5, ge=1, le=50, description="返回记忆数量")
    recall_strategy: str = Field(
        default="weighted", pattern="^(similarity|weighted)$", description="召回策略"
    )
    retrieval_mode: str = Field(
        default="hybrid", pattern="^(hybrid|dense|sparse)$", description="检索模式"
    )
    similarity_weight: float = Field(
        default=0.6, ge=0.0, le=1.0, description="相似度权重"
    )
    importance_weight: float = Field(
        default=0.2, ge=0.0, le=1.0, description="重要性权重"
    )
    recency_weight: float = Field(default=0.2, ge=0.0, le=1.0, description="新近度权重")

    @model_validator(mode="after")
    def validate_weights_sum(self):
        """验证权重总和接近1.0"""
        similarity = self.similarity_weight
        importance = self.importance_weight
        recency = self.recency_weight

        # 计算权重总和
        total = similarity + importance + recency
        if abs(total - 1.0) > 0.1:
            logger.warning(f"权重总和 {total:.2f} 偏离1.0较多，可能影响检索效果")

        return self


class FusionConfig(BaseModel):
    """结果融合配置"""

    strategy: str = Field(
        default="rrf",
        pattern="^(rrf|weighted|cascade|adaptive|convex|interleave|rank_fusion|score_fusion|hybrid_rrf)$",
        description="融合策略",
    )
    rrf_k: int = Field(default=60, ge=1, le=1000, description="RRF参数k")
    dense_weight: float = Field(default=0.7, ge=0.0, le=1.0, description="密集检索权重")
    sparse_weight: float = Field(
        default=0.3, ge=0.0, le=1.0, description="稀疏检索权重"
    )
    sparse_alpha: float = Field(
        default=1.0, ge=0.1, le=10.0, description="稀疏分数缩放"
    )
    sparse_epsilon: float = Field(
        default=0.0, ge=0.0, le=1.0, description="稀疏分数偏移"
    )

    # 新增参数
    convex_lambda: float = Field(default=0.5, ge=0.0, le=1.0, description="凸组合参数λ")
    interleave_ratio: float = Field(
        default=0.5, ge=0.0, le=1.0, description="交替融合比例"
    )
    rank_bias_factor: float = Field(
        default=0.1, ge=0.0, le=1.0, description="排序偏置因子"
    )
    diversity_bonus: float = Field(
        default=0.1, ge=0.0, le=1.0, description="多样性奖励"
    )


class ReflectionEngineConfig(BaseModel):
    """反思引擎配置"""

    summary_trigger_rounds: int = Field(
        default=10, ge=1, le=100, description="触发反思的对话轮次"
    )
    importance_threshold: float = Field(
        default=0.5, ge=0.0, le=1.0, description="记忆重要性阈值"
    )
    event_extraction_prompt: Optional[str] = Field(
        default=None, description="事件提取提示词"
    )
    evaluation_prompt: Optional[str] = Field(default=None, description="评分提示词")


class SparseRetrieverConfig(BaseModel):
    """稀疏检索器配置"""

    enabled: bool = Field(default=True, description="是否启用稀疏检索")
    bm25_k1: float = Field(default=1.2, ge=0.1, le=10.0, description="BM25 k1参数")
    bm25_b: float = Field(default=0.75, ge=0.0, le=1.0, description="BM25 b参数")
    use_jieba: bool = Field(default=True, description="是否使用jieba分词")


class ForgettingAgentConfig(BaseModel):
    """遗忘代理配置"""

    enabled: bool = Field(default=True, description="是否启用遗忘代理")
    check_interval_hours: int = Field(
        default=24, ge=1, le=168, description="检查间隔（小时）"
    )
    retention_days: int = Field(default=90, ge=1, le=3650, description="记忆保留天数")
    importance_decay_rate: float = Field(
        default=0.005, ge=0.0, le=1.0, description="重要性衰减率"
    )
    importance_threshold: float = Field(
        default=0.1, ge=0.0, le=1.0, description="删除阈值"
    )
    forgetting_batch_size: int = Field(
        default=1000, ge=100, le=10000, description="批处理大小"
    )


class FilteringConfig(BaseModel):
    """过滤配置"""

    use_persona_filtering: bool = Field(default=True, description="是否使用人格过滤")
    use_session_filtering: bool = Field(default=True, description="是否使用会话过滤")


class ProviderConfig(BaseModel):
    """Provider配置"""

    embedding_provider_id: Optional[str] = Field(
        default=None, description="Embedding Provider ID"
    )
    llm_provider_id: Optional[str] = Field(default=None, description="LLM Provider ID")


class TimezoneConfig(BaseModel):
    """时区配置"""

    timezone: str = Field(default="Asia/Shanghai", description="时区")


class LivingMemoryConfig(BaseModel):
    """完整插件配置"""

    session_manager: SessionManagerConfig = Field(default_factory=SessionManagerConfig)
    recall_engine: RecallEngineConfig = Field(default_factory=RecallEngineConfig)
    reflection_engine: ReflectionEngineConfig = Field(
        default_factory=ReflectionEngineConfig
    )
    sparse_retriever: SparseRetrieverConfig = Field(
        default_factory=SparseRetrieverConfig
    )
    forgetting_agent: ForgettingAgentConfig = Field(
        default_factory=ForgettingAgentConfig
    )
    filtering_settings: FilteringConfig = Field(default_factory=FilteringConfig)
    provider_settings: ProviderConfig = Field(default_factory=ProviderConfig)
    timezone_settings: TimezoneConfig = Field(default_factory=TimezoneConfig)

    # 为融合配置添加嵌套支持
    fusion: Optional[FusionConfig] = Field(
        default_factory=FusionConfig, description="结果融合配置"
    )

    model_config = {"extra": "allow"}  # 允许额外字段，向前兼容


def validate_config(raw_config: Dict[str, Any]) -> LivingMemoryConfig:
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


def get_default_config() -> Dict[str, Any]:
    """
    获取默认配置字典。

    Returns:
        Dict[str, Any]: 默认配置
    """
    return LivingMemoryConfig().model_dump()


def merge_config_with_defaults(user_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    将用户配置与默认配置合并。

    Args:
        user_config: 用户提供的配置

    Returns:
        Dict[str, Any]: 合并后的配置
    """
    default_config = get_default_config()

    def deep_merge(default: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
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
    current_config: LivingMemoryConfig, changes: Dict[str, Any]
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

        def update_nested_dict(target: Dict[str, Any], updates: Dict[str, Any]):
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
