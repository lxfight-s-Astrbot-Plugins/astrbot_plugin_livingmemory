"""
PluginInitializer 的 InitializerFinalizeMixin 拆分模块
自动从 core/plugin_initializer.py 拆分，保持行为不变
"""

from typing import Any
import asyncio
from .managers.conversation_manager import ConversationManager
from ..storage.conversation_store import ConversationStore
from ..storage.db_migration import DBMigration
from .schedulers.decay_scheduler import DecayScheduler
from .validators.index_validator import IndexValidator
from .base.exceptions import InitializationError, ProviderNotReadyError
from astrbot.api import logger
from .managers.consolidation_manager import MemoryConsolidationManager
from .managers.memory_engine import MemoryEngine
from .processors.memory_processor import MemoryProcessor
from pathlib import Path
from astrbot.core.provider.provider import Provider
import time


class InitializerFinalizeMixin:
    """PluginInitializer 拆分模块：InitializerFinalizeMixin"""
    async def _complete_initialization(self):
        """完成完整的初始化流程"""
        if self._initialization_complete:
            return

        logger.info("开始完整初始化流程...")

        try:
            # 初始化数据库
            data_dir_path = Path(self.data_dir)
            db_path = data_dir_path / "livingmemory.db"
            index_path = data_dir_path / "livingmemory.index"
            graph_doc_path = data_dir_path / "livingmemory_graph_documents.db"
            graph_index_path = data_dir_path / "livingmemory_graph.index"
            graph_memory_enabled = self.config_manager.get("graph_memory.enabled", True)

            if not self.embedding_provider:
                raise ProviderNotReadyError("Embedding Provider 未初始化")
            if not self.llm_provider or not isinstance(self.llm_provider, Provider):
                raise ProviderNotReadyError("LLM Provider 未初始化或类型不正确")

            faiss_vec_db_cls = self._load_faiss_vec_db_class()

            # 检查索引文件维度与当前 embedding provider 维度是否一致
            await self._check_and_fix_dimension_mismatch(str(index_path))
            if graph_memory_enabled:
                self._graph_index_requires_rebuild = (
                    await self._check_and_fix_dimension_mismatch(str(graph_index_path))
                )

            self.db = faiss_vec_db_cls(
                str(db_path),
                str(index_path),
                self.embedding_provider,
            )
            await self.db.initialize()
            self.graph_db = None
            if graph_memory_enabled:
                self.graph_db = faiss_vec_db_cls(
                    str(graph_doc_path),
                    str(graph_index_path),
                    self.embedding_provider,
                )
                await self.graph_db.initialize()
            logger.info(f"数据库已初始化。数据目录: {self.data_dir}")

            # 初始化数据库迁移管理器
            self.db_migration = DBMigration(str(db_path))

            # 检查并执行数据库迁移
            if self.config_manager.get("migration_settings.auto_migrate", True):
                await self._check_and_migrate_database()

            # 初始化MemoryEngine
            stopwords_dir = data_dir_path / "stopwords"
            stopwords_dir.mkdir(parents=True, exist_ok=True)

            memory_engine_config = {
                "rrf_k": self.config_manager.get("fusion_strategy.rrf_k", 60),
                "decay_rate": self.config_manager.get(
                    "importance_decay.decay_rate", 0.01
                ),
                "access_decay_window_days": self.config_manager.get(
                    "importance_decay.access_decay_window_days", 30.0
                ),
                "access_decay_max_count": self.config_manager.get(
                    "importance_decay.access_decay_max_count", 10
                ),
                "access_count_decay_multiplier": self.config_manager.get(
                    "importance_decay.access_count_decay_multiplier", 0.5
                ),
                "protected_importance_threshold": self.config_manager.get(
                    "importance_decay.protected_importance_threshold", 1.0
                ),
                "importance_weight": self.config_manager.get(
                    "recall_engine.importance_weight", 1.0
                ),
                "min_importance_for_retrieval": self.config_manager.get(
                    "recall_engine.min_importance_for_retrieval", 0.0
                ),
                "min_similarity_for_retrieval": self.config_manager.get(
                    "recall_engine.min_similarity_for_retrieval", 0.0
                ),
                "recent_memory_count": self.config_manager.get(
                    "recall_engine.recent_memory_count", 2
                ),
                "recent_memory_max_age_hours": self.config_manager.get(
                    "recall_engine.recent_memory_max_age_hours", 72
                ),
                "memory_type_filter": self.config_manager.get(
                    "recall_engine.memory_type_filter", "all"
                ),
                "search_cache_enabled": self.config_manager.get(
                    "recall_engine.search_cache_enabled", True
                ),
                "search_cache_ttl_seconds": self.config_manager.get(
                    "recall_engine.search_cache_ttl_seconds", 45.0
                ),
                "search_cache_max_size": self.config_manager.get(
                    "recall_engine.search_cache_max_size", 256
                ),
                "fallback_enabled": self.config_manager.get(
                    "recall_engine.fallback_to_vector", True
                ),
                "rerank_enabled": self.config_manager.get(
                    "recall_engine.rerank_enabled", False
                ),
                "rerank_candidates": self.config_manager.get(
                    "recall_engine.rerank_candidates", 20
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
                "auto_archived_enabled": self.config_manager.get(
                    "forgetting_agent.auto_archived_enabled", False
                ),
                "stopwords_path": str(stopwords_dir),
                "graph_memory_enabled": graph_memory_enabled,
                "document_route_weight": self.config_manager.get(
                    "graph_memory.document_route_weight", 0.65
                ),
                "graph_route_weight": self.config_manager.get(
                    "graph_memory.graph_route_weight", 0.35
                ),
                "cross_route_bonus": self.config_manager.get(
                    "graph_memory.cross_route_bonus", 0.08
                ),
                "graph_expansion_limit": self.config_manager.get(
                    "graph_memory.expansion_limit", 24
                ),
                "graph_expansion_hops": self.config_manager.get(
                    "graph_memory.expansion_hops", 1
                ),
                "graph_second_hop_weight": self.config_manager.get(
                    "graph_memory.second_hop_weight", 0.4
                ),
                "dynamic_route_weighting": self.config_manager.get(
                    "graph_memory.dynamic_route_weighting", True
                ),
                "graph_max_topics": self.config_manager.get(
                    "graph_memory.max_topics_per_memory", 6
                ),
                "graph_max_participants": self.config_manager.get(
                    "graph_memory.max_participants_per_memory", 8
                ),
                "graph_max_facts": self.config_manager.get(
                    "graph_memory.max_facts_per_memory", 8
                ),
                "atom_enabled": self.config_manager.get(
                    "graph_memory.atom_enabled", True
                ),
                "atom_maintenance_interval_hours": self.config_manager.get(
                    "graph_memory.atom_maintenance_interval_hours", 24.0
                ),
                "atom_forget_delay_days": self.config_manager.get(
                    "graph_memory.atom_forget_delay_days", 7.0
                ),
                "atom_purge_delay_days": self.config_manager.get(
                    "graph_memory.atom_purge_delay_days", 30.0
                ),
                "index_rebuild_batch_size": self.config_manager.get(
                    "index_rebuild_settings.batch_size", 50
                ),
                "index_rebuild_embedding_batch_size": self.config_manager.get(
                    "index_rebuild_settings.embedding_batch_size", 8
                ),
                "index_rebuild_tasks_limit": self.config_manager.get(
                    "index_rebuild_settings.tasks_limit", 1
                ),
                "index_rebuild_max_retries": self.config_manager.get(
                    "index_rebuild_settings.max_retries", 5
                ),
                "index_rebuild_retry_base_delay": self.config_manager.get(
                    "index_rebuild_settings.retry_base_delay", 30.0
                ),
                "index_rebuild_batch_delay": self.config_manager.get(
                    "index_rebuild_settings.batch_delay", 5.0
                ),
                "index_rebuild_request_delay": self.config_manager.get(
                    "index_rebuild_settings.request_delay", 5.0
                ),
                "index_rebuild_max_failure_ratio": self.config_manager.get(
                    "index_rebuild_settings.max_failure_ratio", 0.02
                ),
            }

            # Rerank 提供商动态解析：每次调用时重新获取实例，
            # 适配 AstrBot 的 Provider 实例重建（旧实例 httpx 客户端会关闭）
            rerank_provider_id = (
                self.config_manager.get("recall_engine.rerank_provider_id", "") or ""
            )

            def resolve_rerank_provider():
                if not rerank_provider_id:
                    return None
                try:
                    return self._get_provider_by_id(rerank_provider_id, silent=True)
                except Exception as e:
                    logger.warning(f"解析 Rerank 提供商失败: {e}")
                    return None

            self.memory_engine = MemoryEngine(
                db_path=str(db_path),
                faiss_db=self.db,
                graph_vector_db=self.graph_db,
                llm_provider=self.llm_provider,
                config=memory_engine_config,
                rerank_provider_resolver=resolve_rerank_provider,
            )
            await self.memory_engine.initialize()
            logger.info("MemoryEngine 已初始化")

            # 初始化 ConversationManager
            conversation_db_path = data_dir_path / "conversations.db"
            conversation_store = ConversationStore(str(conversation_db_path))
            await conversation_store.initialize()

            session_config = self.config_manager.session_manager
            self.conversation_manager = ConversationManager(
                store=conversation_store,
                max_cache_size=session_config.get("max_sessions", 100),
                context_window_size=session_config.get("context_window_size", 50),
                session_ttl=session_config.get("session_ttl", 3600),
                identity_aliases=self.config_manager.get(
                    "access_control.identity_aliases", ""
                ),
            )
            logger.info("ConversationManager 已初始化")

            # 自动修复 message_count 不一致问题
            await self._repair_message_counts(conversation_store)

            # 初始化 MemoryProcessor
            # 注意：MemoryProcessor 不直接持有 llm_provider 实例引用，
            # 而是在每次调用时通过 AstrBot 上下文动态解析 Provider，
            # 以避免 AstrBot 重新创建 Provider 后旧实例的 httpx client 被关闭
            # 导致的 "Cannot send a request, as the client has been closed" 错误。
            llm_id = self.config_manager.get("provider_settings.llm_provider_id")
            self.memory_processor = MemoryProcessor(
                self.context,
                llm_provider=llm_id if llm_id else None,
                config={
                    "atom_enabled": memory_engine_config["atom_enabled"],
                    "include_source_time_tags": self.config_manager.get(
                        "reflection_engine.include_source_time_tags", True
                    ),
                },
            )
            logger.info("MemoryProcessor 已初始化")

            # 初始化记忆整合管理器
            self.consolidation_manager = MemoryConsolidationManager(
                self.memory_engine,
                self.memory_processor,
                self.config_manager,
            )

            # 初始化索引验证器并自动重建索引
            self.index_validator = IndexValidator(str(db_path), self.db)
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
            consolidation_daily = (
                self.config_manager.get("memory_consolidation.enabled", False)
                and self.config_manager.get(
                    "memory_consolidation.trigger", "daily"
                )
                == "daily"
            )
            if self.memory_engine and (
                decay_rate > 0 or auto_cleanup_enabled or consolidation_daily
            ):
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
                    consolidation_manager=self.consolidation_manager,
                )
                await scheduler.start()
                self.decay_scheduler = scheduler
                logger.info("DecayScheduler 已启动")

            # 标记初始化完成
            self._initialization_complete = True
            logger.info("LivingMemory 插件初始化成功。")

        except Exception as e:
            logger.error(f"完整初始化流程失败: {e}", exc_info=True)
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

            result = await self.db_migration.migrate(progress_callback=None)

            if result.get("success"):
                logger.info(f"数据库迁移结果: {result.get('message')}")
                logger.info(f"   耗时: {result.get('duration', 0):.2f}秒")
            else:
                logger.error(f"数据库迁移失败: {result.get('message')}")

        except Exception as e:
            logger.error(f"数据库迁移检查失败: {e}", exc_info=True)

    async def _auto_rebuild_index_if_needed(self):
        """Schedule index checking without blocking core plugin readiness."""
        if self._index_maintenance_task and not self._index_maintenance_task.done():
            return
        if not self.index_validator or not self.memory_engine:
            return

        self._set_index_maintenance_status(
            state="checking",
            reason="startup consistency check",
            current=0,
            total=0,
            message="正在检查索引一致性",
            started_at=time.time(),
            finished_at=None,
            result=None,
        )
        self._index_maintenance_task = asyncio.create_task(
            self._run_index_maintenance()
        )
        self._index_maintenance_task.add_done_callback(self._on_index_maintenance_done)

    def _set_index_maintenance_status(self, **updates: Any) -> None:
        self._index_maintenance_status.update(updates)
        if self.memory_engine is not None:
            self.memory_engine.index_maintenance_status = dict(
                self._index_maintenance_status
            )

    @property
    def index_maintenance_status(self) -> dict[str, Any]:
        return dict(self._index_maintenance_status)

    def _on_index_maintenance_done(self, task: asyncio.Task) -> None:
        if self._index_maintenance_task is task:
            self._index_maintenance_task = None
        if task.cancelled():
            return
        try:
            exception = task.exception()
        except Exception:
            return
        if exception:
            logger.error(f"索引维护任务异常退出: {exception}", exc_info=exception)

    async def _run_index_maintenance(self) -> None:
        """Check and repair indexes while the plugin remains available."""
        try:
            if not self.index_validator or not self.memory_engine:
                return

            fingerprint_changed = False
            check_fingerprint = getattr(
                self.index_validator, "provider_fingerprint_changed", None
            )
            if callable(check_fingerprint):
                fingerprint_changed = bool(await check_fingerprint())
            if fingerprint_changed:
                status = await self.index_validator.check_consistency()
                await self._run_scheduled_index_rebuild(
                    "Embedding Provider 指纹已变化",
                    status.documents_count,
                    force_full_vector=True,
                    rebuild_graph=True,
                )
                return

            # 检查v1迁移状态
            (
                needs_migration_rebuild,
                pending_count,
            ) = await self.index_validator.get_migration_status()

            if needs_migration_rebuild:
                reason = f"v1 迁移数据需要重建索引（{pending_count} 条文档）"
                await self._run_scheduled_index_rebuild(reason, pending_count)
                return

            # 检查索引一致性
            status = await self.index_validator.check_consistency()

            if not status.is_consistent and status.needs_rebuild:
                logger.warning(f"检测到索引不一致: {status.reason}")
                await self._run_scheduled_index_rebuild(
                    status.reason, status.documents_count
                )
            else:
                logger.info(f"索引一致性检查通过: {status.reason}")
                graph_result = None
                if self._graph_index_requires_rebuild:
                    graph_result = await self._run_graph_index_rebuild()
                self._set_index_maintenance_status(
                    state="ready",
                    reason=status.reason,
                    current=status.documents_count,
                    total=status.documents_count,
                    message=(
                        "索引一致性检查通过，图谱索引已重建"
                        if graph_result is not None
                        else "索引一致性检查通过"
                    ),
                    finished_at=time.time(),
                    result=(
                        {"success": True, "graph_rebuild": graph_result}
                        if graph_result is not None
                        else None
                    ),
                )

        except asyncio.CancelledError:
            self._set_index_maintenance_status(
                state="cancelled",
                message="索引维护已取消",
                finished_at=time.time(),
            )
            raise
        except Exception as e:
            logger.error(f"自动重建索引失败: {e}", exc_info=True)
            self._set_index_maintenance_status(
                state="failed",
                message=str(e),
                finished_at=time.time(),
                result={"success": False, "error": str(e)},
            )

    async def _run_scheduled_index_rebuild(
        self,
        reason: str,
        expected_total: int,
        *,
        force_full_vector: bool = False,
        rebuild_graph: bool = False,
    ) -> None:
        if not self.index_validator or not self.memory_engine:
            return

        self._set_index_maintenance_status(
            state="rebuilding",
            reason=reason,
            current=0,
            total=max(0, int(expected_total)),
            message="开始后台重建索引",
        )
        logger.info(f"开始后台索引维护: {reason}")

        async def update_progress(current: int, total: int, message: str) -> None:
            self._set_index_maintenance_status(
                current=max(0, int(current)),
                total=max(0, int(total)),
                message=message,
            )

        rebuild_kwargs: dict[str, Any] = {"progress_callback": update_progress}
        if force_full_vector:
            rebuild_kwargs["force_full_vector"] = True
        result = await self.index_validator.rebuild_indexes(
            self.memory_engine, **rebuild_kwargs
        )
        success = bool(result.get("success"))
        partial = bool(result.get("partial"))

        check_consistency = getattr(self.index_validator, "check_consistency", None)
        if success and callable(check_consistency):
            final_status = await check_consistency()
            if not final_status.is_consistent and final_status.needs_rebuild:
                logger.info(
                    "索引维护期间检测到并发写入，执行一次收尾补偿: "
                    f"{final_status.reason}"
                )
                reconciliation = await self.index_validator.rebuild_indexes(
                    self.memory_engine,
                    progress_callback=update_progress,
                )
                result["reconciliation"] = dict(reconciliation)
                success = bool(reconciliation.get("success"))
                partial = partial or bool(reconciliation.get("partial"))
                if success:
                    final_status = await check_consistency()

            if success and not final_status.is_consistent:
                partial = True
                result["post_check"] = {
                    "consistent": False,
                    "reason": final_status.reason,
                }

        if (
            success
            and not partial
            and (rebuild_graph or self._graph_index_requires_rebuild)
        ):
            result["graph_rebuild"] = await self._run_graph_index_rebuild()

        result["success"] = success
        result["partial"] = partial
        state = "partial" if success and partial else "ready" if success else "failed"
        self._set_index_maintenance_status(
            state=state,
            current=int(result.get("processed", 0) or 0),
            total=int(result.get("total", expected_total) or 0),
            message=str(result.get("message") or "索引维护完成"),
            finished_at=time.time(),
            result=dict(result),
        )
        if success:
            logger.info(
                f"索引后台维护完成: 成功 {result.get('processed', 0)} 条, "
                f"失败 {result.get('errors', 0)} 条"
            )
        else:
            logger.error(f"索引后台维护失败: {result.get('message')}")

    async def _run_graph_index_rebuild(self) -> dict[str, int]:
        if self.memory_engine is None:
            return {"rebuilt": 0, "skipped": 0}
        self._set_index_maintenance_status(message="正在重建图谱索引")
        result = await self.memory_engine.rebuild_graph_index()
        self._graph_index_requires_rebuild = False
        return result

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

    async def _teardown_partial_init(self) -> None:
        """关闭初始化过程中已创建的资源，使后续重试可以从干净状态开始。"""
        if self.decay_scheduler is not None:
            try:
                await self.decay_scheduler.stop()
            except Exception:
                logger.warning("停止衰减调度器失败", exc_info=True)
            self.decay_scheduler = None

        if self.conversation_manager is not None:
            if getattr(self.conversation_manager, "store", None) is not None:
                try:
                    await self.conversation_manager.store.close()
                except Exception:
                    logger.warning("关闭 ConversationManager 失败", exc_info=True)
            self.conversation_manager = None

        if self.memory_engine is not None:
            try:
                await self.memory_engine.close()
            except Exception:
                logger.warning("关闭 MemoryEngine 失败", exc_info=True)
            self.memory_engine = None
            # memory_engine.close() 已关闭 graph_vector_db（即 self.graph_db）
            self.graph_db = None

        if self.graph_db is not None:
            try:
                await self.graph_db.close()
            except Exception:
                logger.warning("关闭图 FaissVecDB 失败", exc_info=True)
            self.graph_db = None

        if self.db is not None:
            try:
                await self.db.close()
            except Exception:
                logger.warning("关闭 FaissVecDB 失败", exc_info=True)
            self.db = None
