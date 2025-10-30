# -*- coding: utf-8 -*-
"""
config_validator.py - 配置验证模块
提供配置验证和默认值管理功能。
"""

from typing import Dict, Any, List, Optional, Union
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


class RecallEngineConfig(BaseModel):
    """回忆引擎配置"""

    top_k: int = Field(default=5, ge=1, le=50, description="返回记忆数量")
    importance_weight: float = Field(
        default=1.0, ge=0.0, le=10.0, description="重要性权重"
    )
    fallback_to_vector: bool = Field(default=True, description="是否启用向量检索回退")
    injection_method: str = Field(
        default="system_prompt",
        description="记忆注入方式: system_prompt(系统提示), user_message_before(用户消息前), user_message_after(用户消息后)",
    )
    auto_remove_injected: bool = Field(
        default=True, description="是否自动删除对话历史中已注入的记忆片段"
    )


class FusionStrategyConfig(BaseModel):
    """结果融合策略配置"""

    rrf_k: int = Field(default=60, ge=1, le=1000, description="RRF参数k")


class ReflectionEngineConfig(BaseModel):
    """反思引擎配置"""

    summary_trigger_rounds: int = Field(
        default=10, ge=1, le=100, description="触发反思的对话轮次"
    )
    save_original_conversation: bool = Field(
        default=False, description="保存记忆时是否包含原始对话历史"
    )


class SparseRetrieverConfig(BaseModel):
    """稀疏检索器配置"""

    enabled: bool = Field(default=True, description="是否启用稀疏检索")
    bm25_k1: float = Field(default=1.2, ge=0.1, le=10.0, description="BM25 k1参数")
    bm25_b: float = Field(default=0.75, ge=0.0, le=1.0, description="BM25 b参数")
    use_chinese_tokenizer: bool = Field(default=True, description="是否使用jieba分词")
    enable_stopwords_filtering: bool = Field(
        default=True, description="是否启用停用词过滤"
    )
    stopwords_source: str = Field(default="hit", description="停用词来源")
    custom_stopwords: Union[str, List[str]] = Field(
        default="", description="自定义停用词"
    )

    @model_validator(mode="after")
    def process_custom_stopwords(self):
        """处理自定义停用词字符串"""
        if isinstance(self.custom_stopwords, str):
            if self.custom_stopwords.strip():
                # 逗号或空格分隔
                self.custom_stopwords = [
                    w.strip()
                    for w in self.custom_stopwords.replace(",", " ").split()
                    if w.strip()
                ]
            else:
                self.custom_stopwords = []
        return self


class DenseRetrieverConfig(BaseModel):
    """稠密检索器配置"""

    enable_query_preprocessing: bool = Field(
        default=True, description="是否启用查询预处理"
    )


class ForgettingAgentConfig(BaseModel):
    """遗忘代理配置"""

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

    embedding_provider_id: Optional[str] = Field(
        default=None, description="Embedding Provider ID"
    )
    llm_provider_id: Optional[str] = Field(default=None, description="LLM Provider ID")


class ImportanceDecayConfig(BaseModel):
    """重要性衰减配置"""

    decay_rate: float = Field(default=0.01, ge=0.0, le=1.0, description="每日衰减率")


class MigrationSettings(BaseModel):
    """数据库迁移设置"""

    auto_migrate: bool = Field(default=True, description="是否启用自动迁移")
    create_backup: bool = Field(default=True, description="迁移前是否创建备份")


class WebUISettings(BaseModel):
    """WebUI 设置"""

    enabled: bool = Field(default=False, description="是否启用 WebUI 控制台")
    host: str = Field(default="127.0.0.1", description="WebUI 监听地址")
    port: int = Field(default=8080, ge=1, le=65535, description="WebUI 监听端口")
    access_password: str = Field(default="", description="WebUI 入口密码")
    session_timeout: int = Field(
        default=3600, ge=60, le=86400, description="WebUI 会话有效期（秒）"
    )

    @model_validator(mode="after")
    def validate_password(self):
        """启用时必须设置密码"""
        if self.enabled and not self.access_password:
            logger.info("WebUI 未设置访问密码，将在运行时自动生成随机密码。")
        return self


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
    dense_retriever: DenseRetrieverConfig = Field(default_factory=DenseRetrieverConfig)
    forgetting_agent: ForgettingAgentConfig = Field(
        default_factory=ForgettingAgentConfig
    )
    filtering_settings: FilteringConfig = Field(default_factory=FilteringConfig)
    provider_settings: ProviderConfig = Field(default_factory=ProviderConfig)
    webui_settings: WebUISettings = Field(default_factory=WebUISettings)
    migration_settings: MigrationSettings = Field(default_factory=MigrationSettings)
    fusion_strategy: FusionStrategyConfig = Field(
        default_factory=FusionStrategyConfig, description="结果融合策略配置"
    )
    importance_decay: ImportanceDecayConfig = Field(
        default_factory=ImportanceDecayConfig, description="重要性衰减配置"
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
