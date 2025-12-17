"""
插件初始化器
负责插件的初始化逻辑
"""

import asyncio
import os
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.star import Context, StarTools
from astrbot.core.db.vec_db.faiss_impl.vec_db import FaissVecDB
from astrbot.core.provider.provider import EmbeddingProvider

from .config_manager import ConfigManager
from .conversation_manager import ConversationManager
from .exceptions import InitializationError, ProviderNotReadyError
from .index_validator import IndexValidator
from .memory_engine import MemoryEngine
from .memory_processor import MemoryProcessor
from ..storage.conversation_store import ConversationStore
from ..storage.db_migration import DBMigration


class PluginInitializer:
    """插件初始化器"""

    def __init__(self, context: Context, config_manager: ConfigManager):
        """
        初始化插件初始化器

        Args:
            context: AstrBot上下文
            config_manager: 配置管理器
        """
        self.context = context
        self.config_manager = config_manager

        # 组件实例
        self.embedding_provider: EmbeddingProvider | None = None
        self.llm_provider = None
        self.db: FaissVecDB | None = None
        self.memory_engine: MemoryEngine | None = None
        self.memory_processor: MemoryProcessor | None = None
        self.db_migration: DBMigration | None = None
        self.conversation_manager: ConversationManager | None = None
        self.index_validator: IndexValidator | None = None

        # 初始化状态
        self._initialization_complete = False
        self._initialization_lock = asyncio.Lock()
        self._initialization_failed = False
        self._initialization_error: str | None = None
        self._providers_ready = False
        self._provider_check_attempts = 0
        self._max_provider_attempts = 60

    async def initialize(self) -> bool:
        """
        执行初始化

        Returns:
            bool: 是否初始化成功
        """
        async with self._initialization_lock:
            if self._initialization_complete or self._initialization_failed:
                return self._initialization_complete

        logger.info("LivingMemory 插件开始后台初始化...")

        try:
            # 1. 等待 Provider 就绪
            if not await self._wait_for_providers_non_blocking():
                logger.warning("Provider 暂时不可用，将在后台继续尝试...")
                asyncio.create_task(self._retry_initialization())
                return False

            # 2. Provider 就绪，继续完整初始化
            await self._complete_initialization()
            return True

        except Exception as e:
            logger.error(f"LivingMemory 插件初始化失败: {e}", exc_info=True)
            self._initialization_failed = True
            self._initialization_error = str(e)
            return False

    async def _wait_for_providers_non_blocking(self, max_wait: float = 5.0) -> bool:
        """非阻塞地检查 Provider 是否可用"""
        start_time = time.time()
        check_interval = 1.0

        while time.time() - start_time < max_wait:
            self._initialize_providers(silent=True)

            if self.embedding_provider and self.llm_provider:
                logger.info("✅ Provider 已就绪")
                self._providers_ready = True
                return True

            await asyncio.sleep(check_interval)
            self._provider_check_attempts += 1

        logger.debug(
            f"Provider 在 {max_wait}秒内未就绪（已尝试 {self._provider_check_attempts} 次）"
        )
        return False

    async def _retry_initialization(self):
        """后台重试初始化任务"""
        retry_interval = 2.0
        log_interval = 5

        while (
            not self._initialization_complete
            and not self._initialization_failed
            and self._provider_check_attempts < self._max_provider_attempts
        ):
            await asyncio.sleep(retry_interval)

            self._initialize_providers(silent=True)
            self._provider_check_attempts += 1

            if self._provider_check_attempts % log_interval == 0:
                logger.info(
                    f"⏳ 等待 Provider 就绪中...（已尝试 {self._provider_check_attempts}/{self._max_provider_attempts} 次）"
                )

            if self.embedding_provider and self.llm_provider:
                logger.info(
                    f"✅ Provider 在第 {self._provider_check_attempts} 次尝试后就绪，继续初始化..."
                )
                self._providers_ready = True

                try:
                    async with self._initialization_lock:
                        if not self._initialization_complete:
                            await self._complete_initialization()
                except Exception as e:
                    logger.error(f"重试初始化失败: {e}", exc_info=True)
                    self._initialization_failed = True
                    self._initialization_error = str(e)
                break

        if not self._initialization_complete and not self._initialization_failed:
            logger.error(
                f"❌ Provider 在 {self._provider_check_attempts} 次尝试后仍未就绪，初始化失败"
            )
            self._initialization_failed = True
            self._initialization_error = "Provider 初始化超时"

    def _initialize_providers(self, silent: bool = False):
        """初始化 Embedding 和 LLM provider"""
        # 初始化 Embedding Provider
        emb_id = self.config_manager.get("provider_settings.embedding_provider_id")
        if emb_id:
            provider = self.context.get_provider_by_id(emb_id)
            if provider and isinstance(provider, EmbeddingProvider):
                self.embedding_provider = provider
                if not silent:
                    logger.info(f"成功从配置加载 Embedding Provider: {emb_id}")
            elif provider and not silent:
                logger.warning(f"Provider {emb_id} 不是 EmbeddingProvider 类型")

        if not self.embedding_provider:
            embedding_providers = self.context.get_all_embedding_providers()
            if embedding_providers:
                self.embedding_provider = embedding_providers[0]
                if not silent:
                    provider_id = getattr(
                        self.embedding_provider.provider_config,
                        "id",
                        self.embedding_provider.provider_config.get("id", "unknown"),
                    )
                    logger.info(f"未指定 Embedding Provider，使用默认的: {provider_id}")
            else:
                self.embedding_provider = None
                if not silent:
                    logger.debug("没有可用的 Embedding Provider")

        # 初始化 LLM Provider
        llm_id = self.config_manager.get("provider_settings.llm_provider_id")
        if llm_id:
            provider = self.context.get_provider_by_id(llm_id)
            if provider:
                self.llm_provider = provider
                if not silent:
                    logger.info(f"成功从配置加载 LLM Provider: {llm_id}")

        if not self.llm_provider:
            self.llm_provider = self.context.get_using_provider()
            if not silent and self.llm_provider:
                logger.info("使用 AstrBot 当前默认的 LLM Provider。")

    async def _complete_initialization(self):
        """完成完整的初始化流程"""
        if self._initialization_complete:
            return

        logger.info("开始完整初始化流程...")

        try:
            # 初始化数据库
            data_dir = StarTools.get_data_dir()
            db_path = os.path.join(data_dir, "livingmemory.db")
            index_path = os.path.join(data_dir, "livingmemory.index")

            if not self.embedding_provider:
                raise ProviderNotReadyError("Embedding Provider 未初始化")

            self.db = FaissVecDB(db_path, index_path, self.embedding_provider)
            await self.db.initialize()
            logger.info(f"数据库已初始化。数据目录: {data_dir}")

            # 初始化数据库迁移管理器
            self.db_migration = DBMigration(db_path)

            # 检查并执行数据库迁移
            if self.config_manager.get("migration_settings.auto_migrate", True):
                await self._check_and_migrate_database()

            # 初始化MemoryEngine
            stopwords_dir = os.path.join(data_dir, "stopwords")
            os.makedirs(stopwords_dir, exist_ok=True)

            memory_engine_config = {
                "rrf_k": self.config_manager.get("fusion_strategy.rrf_k", 60),
                "decay_rate": self.config_manager.get("importance_decay.decay_rate", 0.01),
                "importance_weight": self.config_manager.get("recall_engine.importance_weight", 1.0),
                "fallback_enabled": self.config_manager.get("recall_engine.fallback_to_vector", True),
                "cleanup_days_threshold": self.config_manager.get("forgetting_agent.cleanup_days_threshold", 30),
                "cleanup_importance_threshold": self.config_manager.get("forgetting_agent.cleanup_importance_threshold", 0.3),
                "stopwords_path": stopwords_dir,
            }

            self.memory_engine = MemoryEngine(
                db_path=db_path,
                faiss_db=self.db,
                llm_provider=self.llm_provider,
                config=memory_engine_config,
            )
            await self.memory_engine.initialize()
            logger.info("✅ MemoryEngine 已初始化")

            # 初始化 ConversationManager
            conversation_db_path = os.path.join(data_dir, "conversations.db")
            conversation_store = ConversationStore(conversation_db_path)
            await conversation_store.initialize()

            session_config = self.config_manager.session_manager
            self.conversation_manager = ConversationManager(
                store=conversation_store,
                max_cache_size=session_config.get("max_sessions", 100),
                context_window_size=session_config.get("context_window_size", 50),
                session_ttl=session_config.get("session_ttl", 3600),
            )
            logger.info("✅ ConversationManager 已初始化")

            # 初始化 MemoryProcessor
            if not self.llm_provider:
                raise ProviderNotReadyError("LLM Provider 未初始化")
            self.memory_processor = MemoryProcessor(self.llm_provider)
            logger.info("✅ MemoryProcessor 已初始化")

            # 初始化索引验证器并自动重建索引
            self.index_validator = IndexValidator(db_path, self.db)
            await self._auto_rebuild_index_if_needed()

            # 异步初始化 TextProcessor
            if self.memory_engine and hasattr(self.memory_engine, "text_processor"):
                if self.memory_engine.text_processor and hasattr(
                    self.memory_engine.text_processor, "async_init"
                ):
                    await self.memory_engine.text_processor.async_init()
                    logger.info("✅ TextProcessor 停用词已加载")

            # 标记初始化完成
            self._initialization_complete = True
            logger.info("✅ LivingMemory 插件初始化成功！")

        except Exception as e:
            logger.error(f"完整初始化流程失败: {e}", exc_info=True)
            self._initialization_failed = True
            self._initialization_error = str(e)
            raise InitializationError(f"初始化失败: {e}") from e

    async def _check_and_migrate_database(self):
        """检查并执行数据库迁移"""
        try:
            if not self.db_migration:
                logger.warning("数据库迁移管理器未初始化")
                return

            needs_migration = await self.db_migration.needs_migration()

            if not needs_migration:
                logger.info("✅ 数据库版本已是最新，无需迁移")
                return

            logger.info("🔄 检测到旧版本数据库，开始自动迁移...")

            if self.config_manager.get("migration_settings.create_backup", True):
                backup_path = await self.db_migration.create_backup()
                if backup_path:
                    logger.info(f"💾 数据库备份已创建: {backup_path}")

            result = await self.db_migration.migrate(
                sparse_retriever=None, progress_callback=None
            )

            if result.get("success"):
                logger.info(f"✅ {result.get('message')}")
                logger.info(f"   耗时: {result.get('duration', 0):.2f}秒")
            else:
                logger.error(f"❌ 数据库迁移失败: {result.get('message')}")

        except Exception as e:
            logger.error(f"数据库迁移检查失败: {e}", exc_info=True)

    async def _auto_rebuild_index_if_needed(self):
        """自动检查并重建索引"""
        try:
            if not self.index_validator or not self.memory_engine:
                return

            # 检查v1迁移状态
            needs_migration_rebuild, pending_count = await self.index_validator.get_migration_status()

            if needs_migration_rebuild:
                logger.info(f"🔄 检测到 v1 迁移数据需要重建索引（{pending_count} 条文档）")
                logger.info("🔨 开始自动重建索引...")

                result = await self.index_validator.rebuild_indexes(self.memory_engine)

                if result["success"]:
                    logger.info(
                        f"✅ 索引自动重建完成: 成功 {result['processed']} 条, 失败 {result['errors']} 条"
                    )
                else:
                    logger.error(f"❌ 索引自动重建失败: {result.get('message')}")
                return

            # 检查索引一致性
            status = await self.index_validator.check_consistency()

            if not status.is_consistent and status.needs_rebuild:
                logger.warning(f"⚠️ 检测到索引不一致: {status.reason}")
                logger.info(
                    f"📊 Documents: {status.documents_count}, BM25: {status.bm25_count}, Vector: {status.vector_count}"
                )
                logger.info("🔨 开始自动重建索引...")

                result = await self.index_validator.rebuild_indexes(self.memory_engine)

                if result["success"]:
                    logger.info(
                        f"✅ 索引自动重建完成: 成功 {result['processed']} 条, 失败 {result['errors']} 条"
                    )
                else:
                    logger.error(f"❌ 索引自动重建失败: {result.get('message')}")
            else:
                logger.info(f"✅ 索引一致性检查通过: {status.reason}")

        except Exception as e:
            logger.error(f"自动重建索引失败: {e}", exc_info=True)

    @property
    def is_initialized(self) -> bool:
        """是否已初始化"""
        return self._initialization_complete

    @property
    def is_failed(self) -> bool:
        """是否初始化失败"""
        return self._initialization_failed

    @property
    def error_message(self) -> str | None:
        """错误消息"""
        return self._initialization_error

    async def ensure_initialized(self, timeout: float = 30.0) -> bool:
        """
        确保插件已初始化

        Args:
            timeout: 超时时间（秒）

        Returns:
            bool: 是否初始化成功
        """
        if self._initialization_complete:
            return True

        if self._initialization_failed:
            return False

        # 等待初始化完成
        start_time = time.time()
        while not self._initialization_complete and not self._initialization_failed:
            if time.time() - start_time > timeout:
                logger.error(f"等待插件初始化超时（{timeout}秒）")
                return False
            await asyncio.sleep(0.2)

        return self._initialization_complete
