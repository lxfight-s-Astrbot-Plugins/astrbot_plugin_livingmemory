"""
插件初始化器
负责插件的初始化逻辑
"""

import asyncio
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.star import Context
from astrbot.core.provider.provider import EmbeddingProvider, Provider

from ..storage.conversation_store import ConversationStore
from ..storage.db_migration import DBMigration
from ..storage.sqlite_connection import resolve_journal_mode
from .astrbot_compat import get_using_provider
from .base.config_manager import ConfigManager
from .base.exceptions import InitializationError, ProviderNotReadyError
from .faiss_async_persist import install_async_persist
from .managers.consolidation_manager import MemoryConsolidationManager
from .managers.conversation_manager import ConversationManager
from .managers.memory_engine import MemoryEngine
from .plugin_initializer_faiss import InitializerFaissMixin
from .processors.memory_processor import MemoryProcessor
from .schedulers.decay_scheduler import DecayScheduler
from .validators.index_maintenance import IndexValidator

FaissVecDB: Any = None

# ── Faiss C++ fopen() 在 Windows 上使用 ANSI codepage ──
# Python 传给 Faiss 的路径是 UTF-8 字节，Windows fopen 期望 ANSI 编码，
# 含非 ASCII 字符的路径（如 C:\Users\<中文名>\...）被解读为乱码 →
# RuntimeError: could not open ... for reading: No such file or directory。
# 通过 monkey-patch faiss.read_index / write_index，经纯 ASCII 临时文件桥接。


class PluginInitializer(InitializerFaissMixin):
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
        self.db: Any | None = None
        self.graph_db: Any | None = None
        # FAISS 索引异步落盘器（core/faiss_async_persist.py），随 db/graph_db 创建
        self._index_persisters: list = []
        self.memory_engine: MemoryEngine | None = None
        self.memory_processor: MemoryProcessor | None = None
        self.db_migration: DBMigration | None = None
        self.conversation_manager: ConversationManager | None = None
        self.index_validator: IndexValidator | None = None
        self.decay_scheduler: DecayScheduler | None = None
        self.consolidation_manager: MemoryConsolidationManager | None = None

        # 初始化状态
        self._initialization_complete = False
        self._initialization_lock = asyncio.Lock()
        self._initialization_failed = False
        self._initialization_error: str | None = None
        self._providers_ready = False
        self._provider_check_attempts = 0
        self._max_provider_attempts = 60
        self._retry_task: asyncio.Task | None = None
        self._index_maintenance_task: asyncio.Task | None = None
        self._graph_index_requires_rebuild = False
        self._index_maintenance_status: dict[str, Any] = {
            "state": "idle",
            "reason": "",
            "current": 0,
            "total": 0,
            "message": "",
            "started_at": None,
            "finished_at": None,
            "result": None,
        }

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

        # 0. 初始化 PromptManager（尽早初始化，供后续组件使用）
        try:
            from .prompts.prompt_manager import init_prompt_manager

            init_prompt_manager(self.data_dir)
        except Exception as e:
            logger.warning(f"PromptManager 初始化失败（不影响核心功能）: {e}")

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
            async with self._initialization_lock:
                if self._initialization_complete or self._initialization_failed:
                    return self._initialization_complete
                await self._complete_initialization()
            return True

        except Exception as e:
            logger.error(f"LivingMemory 插件初始化失败: {e}", exc_info=True)
            self._initialization_error = str(e)
            # 清理半初始化资源并交由后台重试，避免瞬态错误永久禁用插件。
            await self._teardown_partial_init()
            self._start_retry_task_if_needed()
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
            await self._initialize_providers(silent=True)

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

            await self._initialize_providers(silent=True)
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
                    self._initialization_error = str(e)
                    # 清理半初始化资源后继续重试，避免瞬态错误永久禁用插件。
                    await self._teardown_partial_init()
                else:
                    break

            # 指数退避，最大30秒
            current_interval = min(current_interval * 1.5, max_interval)

        if not self._initialization_complete:
            missing = []
            if not self.embedding_provider:
                missing.append(
                    "Embedding Provider（请先在 AstrBot 后台「LLM 配置」中创建并启用 "
                    "Embedding Provider，再在插件配置 provider_settings.embedding_provider_id 中填写其 ID）"
                )
            if not self.llm_provider:
                missing.append("LLM Provider（请配置语言模型）")
            if missing:
                logger.error(
                    f"以下 Provider 在 {self._provider_check_attempts} 次尝试后仍未就绪，初始化失败: "
                    f"{', '.join(missing)}"
                )
                self._initialization_error = (
                    "Provider 初始化超时。"
                    f"未就绪 Provider: {', '.join(missing)}。"
                    "请检查 provider_settings 配置和 AstrBot 默认 Provider。"
                )
            else:
                logger.error(f"初始化在 {self._provider_check_attempts} 次尝试后仍失败")
            self._initialization_failed = True

    async def _initialize_providers(self, silent: bool = False):
        """初始化 Embedding 和 LLM provider"""
        # 初始化 Embedding Provider
        emb_id = self.config_manager.get("provider_settings.embedding_provider_id")
        if emb_id:
            provider = self._get_provider_by_id(emb_id, silent=silent)
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
                    logger.warning(
                        "未显式配置 provider_settings.embedding_provider_id，"
                        f"已回退到 AstrBot 第一个 Embedding Provider: {provider_id}。"
                        "若有多个 Provider 或非预期模型，请在插件配置中填写正确 ID，"
                        "否则可能出现维度不匹配、向量索引或图索引写入失败。"
                    )
            else:
                self.embedding_provider = None
                if not silent:
                    logger.debug("没有可用的 Embedding Provider")

        # 初始化 LLM Provider
        self.llm_provider = None
        llm_id = self.config_manager.get("provider_settings.llm_provider_id")
        if llm_id:
            provider = self._get_provider_by_id(llm_id, silent=silent)
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
                if silent and not self.context.get_all_providers():
                    self.llm_provider = None
                    return
                default_provider = await get_using_provider(self.context)
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
            except Exception as e:
                if not silent:
                    logger.debug(f"获取默认 LLM Provider 失败: {e}")
                self.llm_provider = None

    def _get_provider_by_id(self, provider_id: str, *, silent: bool):
        """静默检查阶段绕过会打印 warning 的 AstrBot 查询接口。"""
        if not provider_id:
            return None
        if not silent:
            return self.context.get_provider_by_id(provider_id)
        provider_manager = getattr(self.context, "provider_manager", None)
        inst_map = getattr(provider_manager, "inst_map", None)
        if isinstance(inst_map, dict):
            return inst_map.get(provider_id)
        return None

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

    async def stop_scheduler(self) -> None:
        """停止衰减调度器"""
        if self.decay_scheduler:
            await self.decay_scheduler.stop()
            self.decay_scheduler = None

    async def stop_background_tasks(self) -> None:
        """停止初始化阶段的后台任务（如Provider重试）"""
        if self._index_maintenance_task and not self._index_maintenance_task.done():
            self._index_maintenance_task.cancel()
            try:
                await self._index_maintenance_task
            except asyncio.CancelledError:
                pass
        self._index_maintenance_task = None

        if self._retry_task and not self._retry_task.done():
            self._retry_task.cancel()
            try:
                await self._retry_task
            except asyncio.CancelledError:
                pass
        self._retry_task = None

    async def _complete_initialization(self):
        """完成完整的初始化流程"""
        if self._initialization_complete:
            return

        logger.info("开始完整初始化流程...")
        try:
            storage = await self._open_vector_storage()
            self.db_migration = DBMigration(str(storage["db_path"]))
            if self.config_manager.get("migration_settings.auto_migrate", True):
                await self._check_and_migrate_database()

            await self._init_memory_engine(storage)
            await self._init_conversation_layer(storage)
            await self._init_index_and_scheduling(storage)

            self._initialization_complete = True
            logger.info("LivingMemory 插件初始化成功。")
        except Exception as e:
            logger.error(f"完整初始化流程失败: {e}", exc_info=True)
            raise InitializationError(f"初始化失败: {e}") from e

    async def _open_vector_storage(self) -> dict[str, Any]:
        """校验 Provider、打开 FAISS 存储并安装异步落盘。"""
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

        # 数据目录可能位于 Docker Desktop 共享挂载等不支持 WAL 共享内存的
        # 文件系统上；必须在建立任何 SQLite 连接前确定 journal 策略。
        await resolve_journal_mode(data_dir_path)

        # FAISS 运行时探测（subprocess）与整索引读取都是同步阻塞 I/O，
        # 放到线程池执行，避免阻塞 AstrBot 事件循环。
        faiss_vec_db_cls = await asyncio.to_thread(self._load_faiss_vec_db_class)

        self.db, _ = await self._open_faiss_db(faiss_vec_db_cls, db_path, index_path)
        self.graph_db = None
        if graph_memory_enabled:
            (
                self.graph_db,
                self._graph_index_requires_rebuild,
            ) = await self._open_faiss_db(
                faiss_vec_db_cls, graph_doc_path, graph_index_path
            )
        logger.info(f"数据库已初始化。数据目录: {self.data_dir}")

        # 实例级替换 save_index：变更锁 + 线程内直写 + 防抖合并，
        # 避免大索引整文件同步写阻塞事件循环（详见 core/faiss_async_persist.py）
        self._index_persisters = []
        for vec_db in (self.db, self.graph_db):
            storage = getattr(vec_db, "embedding_storage", None)
            persister = install_async_persist(storage)
            if persister is not None:
                self._index_persisters.append(persister)

        stopwords_dir = data_dir_path / "stopwords"
        stopwords_dir.mkdir(parents=True, exist_ok=True)
        return {
            "data_dir": data_dir_path,
            "db_path": db_path,
            "graph_doc_path": graph_doc_path,
            "graph_memory_enabled": graph_memory_enabled,
            "stopwords_dir": stopwords_dir,
        }

    async def _init_memory_engine(self, storage: dict[str, Any]) -> None:
        """装配 MemoryEngine 及检索/图谱配置。"""
        memory_engine_config = self._build_memory_engine_config(storage)

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
            db_path=str(storage["db_path"]),
            faiss_db=self.db,
            graph_vector_db=self.graph_db,
            llm_provider=self.llm_provider,
            config=memory_engine_config,
            rerank_provider_resolver=resolve_rerank_provider,
        )
        await self.memory_engine.initialize()
        logger.info("MemoryEngine 已初始化")

    def _build_memory_engine_config(self, storage: dict[str, Any]) -> dict[str, Any]:
        """把配置管理器的点路径配置整理成引擎运行期配置。"""
        adapter_instances = {
            item["id"]: item["type"]
            for item in self.context.get_config().get("platform", [])
            if item.get("id")
        }
        adapter_instances.update(
            {
                item.meta().id: item.meta().name
                for item in self.context.platform_manager.get_insts()
            }
        )
        graph_memory_enabled = storage["graph_memory_enabled"]
        get = self.config_manager.get
        return {
            "graph_document_path": str(storage["graph_doc_path"])
            if graph_memory_enabled
            else None,
            "adapter_instances": adapter_instances,
            "rrf_k": get("fusion_strategy.rrf_k", 60),
            "decay_rate": get("importance_decay.decay_rate", 0.01),
            "access_decay_window_days": get(
                "importance_decay.access_decay_window_days", 30.0
            ),
            "access_decay_max_count": get(
                "importance_decay.access_decay_max_count", 10
            ),
            "access_count_decay_multiplier": get(
                "importance_decay.access_count_decay_multiplier", 0.5
            ),
            "protected_importance_threshold": get(
                "importance_decay.protected_importance_threshold", 1.0
            ),
            "importance_weight": get("recall_engine.importance_weight", 1.0),
            "min_importance_for_retrieval": get(
                "recall_engine.min_importance_for_retrieval", 0.0
            ),
            "min_similarity_for_retrieval": get(
                "recall_engine.min_similarity_for_retrieval", 0.0
            ),
            "recent_memory_count": get("recall_engine.recent_memory_count", 2),
            "recent_memory_max_age_hours": get(
                "recall_engine.recent_memory_max_age_hours", 72
            ),
            "memory_type_filter": get("recall_engine.memory_type_filter", "all"),
            "search_cache_enabled": get("recall_engine.search_cache_enabled", True),
            "search_cache_ttl_seconds": get(
                "recall_engine.search_cache_ttl_seconds", 45.0
            ),
            "search_cache_max_size": get("recall_engine.search_cache_max_size", 256),
            "fallback_enabled": get("recall_engine.fallback_to_vector", True),
            "rerank_enabled": get("recall_engine.rerank_enabled", False),
            "rerank_candidates": get("recall_engine.rerank_candidates", 20),
            "fusion_mode": get("recall_engine.fusion_mode", "score"),
            "fusion_bm25_weight": get("recall_engine.fusion_bm25_weight", 0.5),
            "recall_candidate_multiplier": get(
                "recall_engine.recall_candidate_multiplier", 3
            ),
            "fusion_agreement_bonus": get("recall_engine.fusion_agreement_bonus", 0.25),
            "cleanup_days_threshold": get(
                "forgetting_agent.cleanup_days_threshold", 30
            ),
            "cleanup_importance_threshold": get(
                "forgetting_agent.cleanup_importance_threshold", 0.3
            ),
            "auto_cleanup_enabled": get("forgetting_agent.auto_cleanup_enabled", True),
            "auto_archived_enabled": get(
                "forgetting_agent.auto_archived_enabled", False
            ),
            "stopwords_path": str(storage["stopwords_dir"]),
            "tokenizer": get("recall_engine.tokenizer", "jieba"),
            "graph_memory_enabled": graph_memory_enabled,
            "document_route_weight": get("graph_memory.document_route_weight", 0.65),
            "graph_route_weight": get("graph_memory.graph_route_weight", 0.35),
            "cross_route_bonus": get("graph_memory.cross_route_bonus", 0.08),
            "graph_expansion_limit": get("graph_memory.expansion_limit", 24),
            "graph_expansion_hops": get("graph_memory.expansion_hops", 1),
            "graph_second_hop_weight": get("graph_memory.second_hop_weight", 0.4),
            "dynamic_route_weighting": get(
                "graph_memory.dynamic_route_weighting", True
            ),
            "graph_max_topics": get("graph_memory.max_topics_per_memory", 6),
            "graph_max_participants": get(
                "graph_memory.max_participants_per_memory", 8
            ),
            "graph_max_facts": get("graph_memory.max_facts_per_memory", 8),
            "atom_enabled": get("graph_memory.atom_enabled", True),
            "atom_maintenance_interval_hours": get(
                "graph_memory.atom_maintenance_interval_hours", 24.0
            ),
            "atom_forget_delay_days": get("graph_memory.atom_forget_delay_days", 7.0),
            "atom_purge_delay_days": get("graph_memory.atom_purge_delay_days", 30.0),
            "index_rebuild_batch_size": get("index_rebuild_settings.batch_size", 50),
            "index_rebuild_embedding_batch_size": get(
                "index_rebuild_settings.embedding_batch_size", 8
            ),
            "index_rebuild_tasks_limit": get("index_rebuild_settings.tasks_limit", 1),
            "index_rebuild_max_retries": get("index_rebuild_settings.max_retries", 5),
            "index_rebuild_retry_base_delay": get(
                "index_rebuild_settings.retry_base_delay", 30.0
            ),
            "index_rebuild_batch_delay": get("index_rebuild_settings.batch_delay", 5.0),
            "index_rebuild_request_delay": get(
                "index_rebuild_settings.request_delay", 5.0
            ),
            "index_rebuild_max_failure_ratio": get(
                "index_rebuild_settings.max_failure_ratio", 0.02
            ),
        }

    async def _init_conversation_layer(self, storage: dict[str, Any]) -> None:
        """初始化会话存储、会话管理器、记忆处理器与整合管理器。"""
        conversation_db_path = storage["data_dir"] / "conversations.db"
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

        await self._repair_message_counts(conversation_store)

        # MemoryProcessor 不直接持有 llm_provider 实例引用，而是在每次调用时
        # 通过 AstrBot 上下文动态解析 Provider，避免宿主重建 Provider 后旧实例
        # 的 httpx client 被关闭（"Cannot send a request..."）。
        llm_id = self.config_manager.get("provider_settings.llm_provider_id")
        self.memory_processor = MemoryProcessor(
            self.context,
            llm_provider=llm_id if llm_id else None,
            config={
                "atom_enabled": self.config_manager.get(
                    "graph_memory.atom_enabled", True
                ),
                "include_source_time_tags": self.config_manager.get(
                    "reflection_engine.include_source_time_tags", True
                ),
                "llm_stream_mode": self.config_manager.get(
                    "provider_settings.llm_stream_mode", "auto"
                ),
            },
        )
        logger.info("MemoryProcessor 已初始化")

        self.consolidation_manager = MemoryConsolidationManager(
            self.memory_engine,
            self.memory_processor,
            self.config_manager,
        )

    async def _init_index_and_scheduling(self, storage: dict[str, Any]) -> None:
        """初始化索引维护器、停用词与衰减调度器。"""
        self.index_validator = IndexValidator(str(storage["db_path"]), self.db)
        await self._auto_rebuild_index_if_needed()

        if self.memory_engine and self.memory_engine.text_processor:
            await self.memory_engine.text_processor.async_init()
            logger.info("TextProcessor 停用词已加载")

        decay_rate = self.config_manager.get("importance_decay.decay_rate", 0.01)
        auto_cleanup_enabled = self.config_manager.get(
            "forgetting_agent.auto_cleanup_enabled", True
        )
        consolidation_daily = (
            self.config_manager.get("memory_consolidation.enabled", False)
            and self.config_manager.get("memory_consolidation.trigger", "daily")
            == "daily"
        )
        if not (decay_rate > 0 or auto_cleanup_enabled or consolidation_daily):
            return

        scheduler = DecayScheduler(
            memory_engine=self.memory_engine,
            decay_rate=decay_rate,
            data_dir=self.data_dir,
            db_migration=self.db_migration,
            backup_enabled=self.config_manager.get("backup_settings.enabled", True),
            backup_keep_days=self.config_manager.get("backup_settings.keep_days", 7),
            consolidation_manager=self.consolidation_manager,
        )
        await scheduler.start()
        self.decay_scheduler = scheduler
        logger.info("DecayScheduler 已启动")

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

            require_backup = bool(
                self.config_manager.get("migration_settings.create_backup", True)
            )
            backup_path = await self.db_migration.create_backup()
            if backup_path:
                logger.info(f"迁移前备份已创建: {backup_path}")
            elif require_backup:
                raise InitializationError(
                    "迁移前备份失败，已中止迁移（数据库未修改）；"
                    "请检查磁盘空间与文件权限"
                )
            else:
                logger.warning("迁移前备份失败，按配置继续迁移。")

            result = await self.db_migration.migrate(
                progress_callback=None, create_backup=False
            )

            if result.get("success"):
                logger.info(f"数据库迁移结果: {result.get('message')}")
                logger.info(f"   耗时: {result.get('duration', 0):.2f}秒")
                return

            restored = False
            if backup_path:
                restored = await self._restore_db_backup(backup_path)
            raise InitializationError(
                "数据库迁移失败"
                + ("，已从迁移前备份恢复" if restored else "")
                + f": {result.get('message')}"
            )

        except InitializationError:
            raise
        except Exception as e:
            logger.error(f"数据库迁移检查失败: {e}", exc_info=True)

    async def _restore_db_backup(self, backup_path: str) -> bool:
        """用迁移前备份覆盖当前数据库（SQLite backup API，兼容已打开的连接）。"""
        try:
            import aiosqlite

            async with aiosqlite.connect(backup_path) as source:
                async with aiosqlite.connect(self.db_migration.db_path) as dest:
                    await source.backup(dest)
            logger.warning(f"已从迁移前备份恢复数据库: {backup_path}")
            return True
        except Exception as e:
            logger.error(f"从迁移前备份恢复数据库失败: {e}", exc_info=True)
            return False

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
                await self._run_scheduled_index_rebuild(
                    "Embedding Provider 指纹已变化",
                    0,
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

            # 增量写入路径已维护索引，启动维护只在指纹/迁移状态触发重建。
            graph_result = None
            if self._graph_index_requires_rebuild:
                graph_result = await self._run_graph_index_rebuild()
            self._set_index_maintenance_status(
                state="ready",
                reason="索引由增量写入维护",
                current=0,
                total=0,
                message=(
                    "索引维护完成，图谱索引已重建"
                    if graph_result is not None
                    else "索引维护完成"
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

    async def shutdown_index_persisters(self) -> None:
        """落盘并停止所有索引异步落盘器（terminate / 重新初始化前调用，幂等）。"""
        persisters = getattr(self, "_index_persisters", None) or []
        self._index_persisters = []
        for persister in persisters:
            try:
                await persister.aclose()
            except Exception:
                logger.warning("关闭索引异步落盘器失败", exc_info=True)

    async def _teardown_partial_init(self) -> None:
        """关闭初始化过程中已创建的资源，使后续重试可以从干净状态开始。"""
        # 先落盘索引未写变更，再关闭各存储组件
        await self.shutdown_index_persisters()

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
