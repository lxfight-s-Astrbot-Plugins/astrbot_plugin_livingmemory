"""
插件初始化器
负责插件的初始化逻辑
"""

import asyncio
import os
import time

from astrbot.api import logger
from astrbot.api.star import Context
from astrbot.core.db.vec_db.faiss_impl.vec_db import FaissVecDB
from astrbot.core.provider.provider import EmbeddingProvider, Provider

from ..storage.conversation_store import ConversationStore
from ..storage.db_migration import DBMigration
from .base.config_manager import ConfigManager
from .base.exceptions import InitializationError, ProviderNotReadyError
from .managers.conversation_manager import ConversationManager
from .managers.memory_engine import MemoryEngine
from .processors.memory_processor import MemoryProcessor
from .schedulers.decay_scheduler import DecayScheduler
from .validators.index_validator import IndexValidator


class PluginInitializer:
    """插件初始化器"""

    def __init__(self, context: Context, config_manager: ConfigManager, data_dir: str):
        """
        初始化插件初始化器

        Args:
            context: AstrBot上下文
            config_manager: 配置管理器
            data_dir: 插件数据目录路径
        """
        self.context = context
        self.config_manager = config_manager
        self.data_dir = data_dir

        # 组件实例
        self.embedding_provider: EmbeddingProvider | None = None
        self.llm_provider: Provider | None = None
        self.db: FaissVecDB | None = None
        self.memory_engine: MemoryEngine | None = None
        self.memory_processor: MemoryProcessor | None = None
        self.db_migration: DBMigration | None = None
        self.conversation_manager: ConversationManager | None = None
        self.index_validator: IndexValidator | None = None
        self.decay_scheduler: DecayScheduler | None = None

        # 初始化状态
        self._initialization_complete = False
        self._initialization_lock = asyncio.Lock()
        self._initialization_failed = False
        self._initialization_error: str | None = None
        self._providers_ready = False
        self._provider_check_attempts = 0
        self._max_provider_attempts = 60
        self._retry_task: asyncio.Task | None = None

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
                missing = []
                if not self.embedding_provider:
                    missing.append(
                        "Embedding Provider（请在 AstrBot 中配置向量嵌入模型）"
                    )
                if not self.llm_provider:
                    missing.append("LLM Provider（请在 AstrBot 中配置语言模型）")
                logger.warning(
                    f"以下 Provider 暂时不可用，将在后台继续尝试: {', '.join(missing)}"
                )
                self._start_retry_task_if_needed()
                return False

            # 2. Provider 就绪，继续完整初始化
            await self._complete_initialization()
            return True

        except Exception as e:
            logger.error(f"LivingMemory 插件初始化失败: {e}", exc_info=True)
            self._initialization_failed = True
            self._initialization_error = str(e)
            return False

    def _start_retry_task_if_needed(self) -> None:
        """启动后台重试任务（避免重复启动）"""
        if self._retry_task and not self._retry_task.done():
            return

        self._retry_task = asyncio.create_task(self._retry_initialization())
        self._retry_task.add_done_callback(self._on_retry_task_done)

    def _on_retry_task_done(self, task: asyncio.Task) -> None:
        """重试任务完成回调，回收状态并记录异常"""
        self._retry_task = None
        if task.cancelled():
            return
        try:
            exc = task.exception()
            if exc:
                logger.error(f"Provider 重试任务异常退出: {exc}")
        except Exception:
            # 防御性处理：读取 task.exception() 时不应阻断主流程
            pass

    async def _wait_for_providers_non_blocking(self, max_wait: float = 5.0) -> bool:
        """非阻塞地检查 Provider 是否可用"""
        start_time = time.time()
        check_interval = 1.0

        while time.time() - start_time < max_wait:
            self._initialize_providers(silent=True)

            if self.embedding_provider and self.llm_provider:
                logger.info(
                    "Provider check passed: embedding and llm providers are ready."
                )
                self._providers_ready = True
                return True

            await asyncio.sleep(check_interval)
            self._provider_check_attempts += 1

        logger.debug(
            f"Provider 在 {max_wait}秒内未就绪（已尝试 {self._provider_check_attempts} 次）"
            f"：embedding={'ready' if self.embedding_provider else 'not ready'}, "
            f"llm={'ready' if self.llm_provider else 'not ready'}"
        )
        return False

    async def _retry_initialization(self):
        """后台重试初始化任务（指数退避策略）"""
        base_interval = 2.0
        max_interval = 30.0
        current_interval = base_interval
        log_interval = 5

        while (
            not self._initialization_complete
            and not self._initialization_failed
            and self._provider_check_attempts < self._max_provider_attempts
        ):
            await asyncio.sleep(current_interval)

            self._initialize_providers(silent=True)
            self._provider_check_attempts += 1

            if self._provider_check_attempts % log_interval == 0:
                missing = []
                if not self.embedding_provider:
                    missing.append("Embedding Provider")
                if not self.llm_provider:
                    missing.append("LLM Provider")
                logger.info(
                    f"等待 Provider 就绪（未就绪: {', '.join(missing)}）..."
                    f"（已尝试 {self._provider_check_attempts}/{self._max_provider_attempts} 次，"
                    f"下次重试间隔 {current_interval:.1f}s）"
                )

            if self.embedding_provider and self.llm_provider:
                logger.info(
                    f"Provider 在第 {self._provider_check_attempts} 次尝试后就绪，继续初始化。"
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

            # 指数退避，最大30秒
            current_interval = min(current_interval * 1.5, max_interval)

        if not self._initialization_complete and not self._initialization_failed:
            missing = []
            if not self.embedding_provider:
                missing.append("Embedding Provider（请配置向量嵌入模型）")
            if not self.llm_provider:
                missing.append("LLM Provider（请配置语言模型）")
            logger.error(
                f"以下 Provider 在 {self._provider_check_attempts} 次尝试后仍未就绪，初始化失败: "
                f"{', '.join(missing) if missing else '未知'}"
            )
            self._initialization_failed = True
            self._initialization_error = (
                "Provider 初始化超时。"
                f"未就绪 Provider: {', '.join(missing) if missing else '未知'}。"
                "请检查 provider_settings 配置和 AstrBot 默认 Provider。"
            )

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
        self.llm_provider = None
        llm_id = self.config_manager.get("provider_settings.llm_provider_id")
        if llm_id:
            provider = self.context.get_provider_by_id(llm_id)
            if provider and isinstance(provider, Provider):
                self.llm_provider = provider
                if not silent:
                    logger.info(f"成功从配置加载 LLM Provider: {llm_id}")
            elif provider and not silent:
                logger.warning(
                    f"Provider {llm_id} 不是聊天 Provider 类型，已忽略该配置。"
                )

        if not self.llm_provider:
            try:
                default_provider = self.context.get_using_provider()
                if default_provider and not isinstance(default_provider, Provider):
                    if not silent:
                        logger.warning(
                            "AstrBot 默认 Provider 类型不正确，期望聊天 Provider。"
                        )
                    self.llm_provider = None
                else:
                    self.llm_provider = default_provider
                if not silent and self.llm_provider:
                    logger.info("使用 AstrBot 当前默认的 LLM Provider。")
            except (ValueError, Exception) as e:
                if not silent:
                    logger.debug(f"获取默认 LLM Provider 失败: {e}")
                self.llm_provider = None

    async def _complete_initialization(self):
        """完成完整的初始化流程"""
        if self._initialization_complete:
            return

        logger.info("开始完整初始化流程...")

        try:
            # 初始化数据库
            db_path = os.path.join(self.data_dir, "livingmemory.db")
            index_path = os.path.join(self.data_dir, "livingmemory.index")

            if not self.embedding_provider:
                raise ProviderNotReadyError("Embedding Provider 未初始化")
            if not self.llm_provider or not isinstance(self.llm_provider, Provider):
                raise ProviderNotReadyError("LLM Provider 未初始化或类型不正确")

            # 检查索引文件维度与当前 embedding provider 维度是否一致
            await self._check_and_fix_dimension_mismatch(index_path)

            self.db = FaissVecDB(db_path, index_path, self.embedding_provider)
            await self.db.initialize()
            logger.info(f"数据库已初始化。数据目录: {self.data_dir}")

            # 初始化数据库迁移管理器
            self.db_migration = DBMigration(db_path)

            # 检查并执行数据库迁移
            if self.config_manager.get("migration_settings.auto_migrate", True):
                await self._check_and_migrate_database()

            # 初始化MemoryEngine
            stopwords_dir = os.path.join(self.data_dir, "stopwords")
            os.makedirs(stopwords_dir, exist_ok=True)

            memory_engine_config = {
                "rrf_k": self.config_manager.get("fusion_strategy.rrf_k", 60),
                "decay_rate": self.config_manager.get(
                    "importance_decay.decay_rate", 0.01
                ),
                "importance_weight": self.config_manager.get(
                    "recall_engine.importance_weight", 1.0
                ),
                "fallback_enabled": self.config_manager.get(
                    "recall_engine.fallback_to_vector", True
                ),
                "cleanup_days_threshold": self.config_manager.get(
                    "forgetting_agent.cleanup_days_threshold", 30
                ),
                "cleanup_importance_threshold": self.config_manager.get(
                    "forgetting_agent.cleanup_importance_threshold", 0.3
                ),
                "auto_cleanup_enabled": self.config_manager.get(
                    "forgetting_agent.auto_cleanup_enabled", True
                ),
                "stopwords_path": stopwords_dir,
            }

            self.memory_engine = MemoryEngine(
                db_path=db_path,
                faiss_db=self.db,
                llm_provider=self.llm_provider,
                config=memory_engine_config,
            )
            await self.memory_engine.initialize()
            logger.info("MemoryEngine 已初始化")

            # 初始化 ConversationManager
            conversation_db_path = os.path.join(self.data_dir, "conversations.db")
            conversation_store = ConversationStore(conversation_db_path)
            await conversation_store.initialize()

            session_config = self.config_manager.session_manager
            self.conversation_manager = ConversationManager(
                store=conversation_store,
                max_cache_size=session_config.get("max_sessions", 100),
                context_window_size=session_config.get("context_window_size", 50),
                session_ttl=session_config.get("session_ttl", 3600),
            )
            logger.info("ConversationManager 已初始化")

            # 自动修复 message_count 不一致问题
            await self._repair_message_counts(conversation_store)

            # 初始化 MemoryProcessor
            if not self.llm_provider or not isinstance(self.llm_provider, Provider):
                raise ProviderNotReadyError("LLM Provider 未初始化或类型不正确")
            self.memory_processor = MemoryProcessor(self.llm_provider, self.context)
            logger.info("MemoryProcessor 已初始化")

            # 初始化索引验证器并自动重建索引
            self.index_validator = IndexValidator(db_path, self.db)
            await self._auto_rebuild_index_if_needed()

            # 异步初始化 TextProcessor
            if self.memory_engine and hasattr(self.memory_engine, "text_processor"):
                if self.memory_engine.text_processor and hasattr(
                    self.memory_engine.text_processor, "async_init"
                ):
                    await self.memory_engine.text_processor.async_init()
                    logger.info("TextProcessor 停用词已加载")

            # 启动重要性衰减调度器
            decay_rate = self.config_manager.get("importance_decay.decay_rate", 0.01)
            auto_cleanup_enabled = self.config_manager.get(
                "forgetting_agent.auto_cleanup_enabled", True
            )
            if self.memory_engine and (decay_rate > 0 or auto_cleanup_enabled):
                backup_enabled = self.config_manager.get(
                    "backup_settings.enabled", True
                )
                backup_keep_days = self.config_manager.get(
                    "backup_settings.keep_days", 7
                )
                scheduler = DecayScheduler(
                    memory_engine=self.memory_engine,
                    decay_rate=decay_rate,
                    data_dir=self.data_dir,
                    db_migration=self.db_migration,
                    backup_enabled=backup_enabled,
                    backup_keep_days=backup_keep_days,
                )
                await scheduler.start()
                self.decay_scheduler = scheduler
                logger.info("DecayScheduler 已启动")

            # 标记初始化完成
            self._initialization_complete = True
            logger.info("LivingMemory 插件初始化成功。")

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
                logger.info("数据库版本已是最新，无需迁移")
                return

            logger.info("检测到旧版本数据库，开始自动迁移。")

            if self.config_manager.get("migration_settings.create_backup", True):
                backup_path = await self.db_migration.create_backup()
                if backup_path:
                    logger.info(f"数据库备份已创建: {backup_path}")

            result = await self.db_migration.migrate(
                sparse_retriever=None, progress_callback=None
            )

            if result.get("success"):
                logger.info(f"数据库迁移结果: {result.get('message')}")
                logger.info(f"   耗时: {result.get('duration', 0):.2f}秒")
            else:
                logger.error(f"数据库迁移失败: {result.get('message')}")

        except Exception as e:
            logger.error(f"数据库迁移检查失败: {e}", exc_info=True)

    async def _auto_rebuild_index_if_needed(self):
        """自动检查并重建索引"""
        try:
            if not self.index_validator or not self.memory_engine:
                return

            # 检查v1迁移状态
            (
                needs_migration_rebuild,
                pending_count,
            ) = await self.index_validator.get_migration_status()

            if needs_migration_rebuild:
                logger.info(f"检测到 v1 迁移数据需要重建索引（{pending_count} 条文档）")
                logger.info("开始自动重建索引。")

                result = await self.index_validator.rebuild_indexes(self.memory_engine)

                if result["success"]:
                    logger.info(
                        f"索引自动重建完成: 成功 {result['processed']} 条, 失败 {result['errors']} 条"
                    )
                else:
                    logger.error(f"索引自动重建失败: {result.get('message')}")
                return

            # 检查索引一致性
            status = await self.index_validator.check_consistency()

            if not status.is_consistent and status.needs_rebuild:
                logger.warning(f"检测到索引不一致: {status.reason}")
                logger.info(
                    f"当前索引计数 - Documents: {status.documents_count}, BM25: {status.bm25_count}, Vector: {status.vector_count}"
                )
                logger.info("开始自动重建索引。")

                result = await self.index_validator.rebuild_indexes(self.memory_engine)

                if result["success"]:
                    logger.info(
                        f"索引自动重建完成: 成功 {result['processed']} 条, 失败 {result['errors']} 条"
                    )
                else:
                    logger.error(f"索引自动重建失败: {result.get('message')}")
            else:
                logger.info(f"索引一致性检查通过: {status.reason}")

        except Exception as e:
            logger.error(f"自动重建索引失败: {e}", exc_info=True)

    async def _repair_message_counts(self, conversation_store: ConversationStore):
        """修复会话表中 message_count 与实际消息数量不一致的问题"""
        try:
            logger.info("开始检查并修复 message_count 一致性。")
            fixed_sessions = await conversation_store.sync_message_counts()

            if fixed_sessions:
                logger.info(f"已修复 {len(fixed_sessions)} 个会话的 message_count")
            else:
                logger.debug("所有会话的 message_count 均正确")

        except Exception as e:
            logger.error(f"修复 message_count 失败: {e}", exc_info=True)

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

    async def _check_and_fix_dimension_mismatch(self, index_path: str) -> None:
        """
        检查 FAISS 索引维度与当前 embedding provider 维度是否一致

        当用户更换 embedding provider 后，旧索引的维度可能与新模型不匹配，
        导致 FAISS 插入时报错 "assert d == self.d"。
        此方法检测并自动删除不兼容的旧索引，让系统重建。

        Args:
            index_path: FAISS 索引文件路径
        """
        if not os.path.exists(index_path):
            return

        try:
            import faiss

            old_index = faiss.read_index(index_path)
            old_dim = old_index.d
            new_dim = self.embedding_provider.get_dim()  # type: ignore

            if old_dim != new_dim:
                logger.warning(
                    f"检测到 FAISS 索引维度不匹配: 索引维度={old_dim}, "
                    f"当前 Embedding Provider 维度={new_dim}"
                )
                logger.warning(
                    "这通常由 Embedding 模型切换导致。"
                    "旧索引将被删除，系统会自动重建索引。"
                )

                os.remove(index_path)
                logger.info(f"已删除不兼容的旧索引文件: {index_path}")
                logger.info("注意: 向量检索功能将暂时不可用，直到重新导入记忆数据。")

        except Exception as e:
            logger.error(f"检查索引维度时出错: {e}", exc_info=True)

    async def stop_scheduler(self) -> None:
        """停止衰减调度器"""
        if self.decay_scheduler:
            await self.decay_scheduler.stop()
            self.decay_scheduler = None

    async def stop_background_tasks(self) -> None:
        """停止初始化阶段的后台任务（如Provider重试）"""
        if self._retry_task and not self._retry_task.done():
            self._retry_task.cancel()
            try:
                await self._retry_task
            except asyncio.CancelledError:
                pass
        self._retry_task = None
