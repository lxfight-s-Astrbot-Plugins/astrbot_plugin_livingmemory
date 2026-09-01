"""
统一记忆引擎 - MemoryEngine
提供统一的记忆管理接口,整合所有底层组件
"""

import asyncio
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import aiosqlite

from astrbot.api import logger

from ...storage.atom_store import AtomStore
from ...storage.graph_store import GraphStore
from ..managers.atom_lifecycle_manager import AtomLifecycleManager
from ..managers.graph_memory_manager import GraphMemoryManager
from ..processors.graph_extractor import GraphExtractor
from ..processors.text_processor import TextProcessor
from ..retrieval.atom_retriever import AtomRetriever
from ..retrieval.bm25_retriever import BM25Retriever
from ..retrieval.dual_route_retriever import DualRouteRetriever
from ..retrieval.graph_keyword_retriever import GraphKeywordRetriever
from ..retrieval.graph_retriever import GraphRetriever
from ..retrieval.graph_vector_retriever import GraphVectorRetriever
from ..retrieval.hybrid_retriever import HybridResult, HybridRetriever
from ..retrieval.rrf_fusion import RRFFusion
from ..retrieval.vector_retriever import VectorRetriever
from .memory_engine_write_ops import MemoryEngineWriteOpsMixin
from .memory_engine_crud import MemoryEngineCrudMixin
from .memory_engine_batch import MemoryEngineBatchMixin

class MemoryEngine(MemoryEngineWriteOpsMixin, MemoryEngineCrudMixin, MemoryEngineBatchMixin):
    """
    统一记忆引擎

    整合BM25检索、向量检索和混合检索,提供完整的记忆管理接口。

    主要功能:
    1. 记忆CRUD操作(添加、检索、更新、删除)
    2. 自动化记忆整理和清理
    3. 重要性评估和时间衰减
    4. 会话隔离和统计

    ID管理体系说明：
    ==================
    本系统使用三层存储架构，统一使用整数ID作为主键：

    1. **DocumentStorage (FAISS内部)**
       - 表: documents (SQLite，由SQLAlchemy管理)
       - 主键: id (INTEGER, AUTOINCREMENT) - 这是统一的整数标识符
       - UUID字段: doc_id (TEXT) - FAISS内部使用的UUID字符串
       - 关系: id ←→ doc_id (一对一映射)

    2. **BM25 FTS5索引**
       - 表: livingmemory_memories_fts (SQLite FTS5虚拟表)
       - 字段: doc_id (UNINDEXED) - 引用documents.id的整数
       - 注意: 只存储分词后的内容，metadata从documents表读取

    3. **FAISS向量索引**
       - 存储: EmbeddingStorage (FAISS索引文件)
       - 索引ID: 使用documents.id作为向量的整数索引

    插件对外接口：
    - add_memory() 返回: int (documents.id)
    - search_memories() 返回: HybridResult包含doc_id (int)
    - update_memory(memory_id: int) 参数: documents.id
    - delete_memory(memory_id: int) 参数: documents.id

    同步保证：
    - 添加: 先插入DocumentStorage获取id，再用此id插入BM25和FAISS
    - 更新: 通过vector_retriever更新DocumentStorage (自动同步)
    - 删除: 先删除BM25，再通过FaissVecDB.delete()删除DocumentStorage和向量
    """

    def __init__(
        self,
        db_path: str,
        faiss_db,
        graph_vector_db=None,
        llm_provider=None,
        config: dict[str, Any] | None = None,
        rerank_provider_resolver: Callable[[], Any] | None = None,
    ):
        """
        初始化记忆引擎

        Args:
            db_path: SQLite数据库路径
            faiss_db: FAISS向量数据库实例
            llm_provider: LLM提供者(可选,用于高级功能)
            config: 配置字典,支持以下参数:
                - rrf_k: RRF参数,默认60
                - decay_rate: 时间衰减率,默认0.01
                - importance_weight: 重要性权重,默认1.0
                - min_importance_for_retrieval: 召回最低重要性,默认0.0
                - fallback_enabled: 启用退化机制,默认True
                - cleanup_days_threshold: 清理天数阈值,默认30
                - cleanup_importance_threshold: 清理重要性阈值,默认0.3
                - stopwords_path: 停用词文件路径(可选)
            rerank_provider_resolver: 返回RerankProvider实例的可调用对象(可选)。
                动态解析以适配AstrBot的Provider实例重建,传None时跳过重排序。
        """
        self.db_path = db_path
        self.faiss_db = faiss_db
        self.graph_vector_db = graph_vector_db
        self.llm_provider = llm_provider
        self.config = config or {}
        self.rerank_provider_resolver = rerank_provider_resolver
        self.graph_enabled = bool(self.config.get("graph_memory_enabled", False))
        self.atom_enabled = bool(
            self.config.get(
                "atom_enabled",
                self.config.get("graph_memory_atom_enabled", True),
            )
        )

        # 确保数据库目录存在
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # 后台任务跟踪
        self._pending_tasks: set[asyncio.Task] = set()

        # 初始化组件(在initialize中完成)
        self.text_processor = None
        self.bm25_retriever = None
        self.vector_retriever = None
        self.rrf_fusion = None
        self.hybrid_retriever = None
        self.graph_store = None
        self.graph_extractor = None
        self.graph_keyword_retriever = None
        self.graph_vector_retriever = None
        self.graph_retriever = None
        self.graph_memory_manager = None
        self.dual_route_retriever = None
        self.atom_store = None
        self.atom_lifecycle_manager = None
        self.atom_retriever = None
        self.db_connection = None
        self._search_cache_enabled = bool(self.config.get("search_cache_enabled", True))
        self._search_cache_ttl = float(
            self.config.get("search_cache_ttl_seconds", 45.0)
        )
        self._search_cache_max_size = int(self.config.get("search_cache_max_size", 256))
        self._search_cache_generation = 0
        self._search_cache: OrderedDict[
            tuple[Any, ...], tuple[float, list[HybridResult]]
        ] = OrderedDict()
        self._write_op_repair_enabled = bool(
            self.config.get("write_op_repair_enabled", True)
        )
        self._write_op_max_retries = int(self.config.get("write_op_max_retries", 3))
        self.index_maintenance_status: dict[str, Any] = {
            "state": "idle",
            "current": 0,
            "total": 0,
            "message": "",
        }

    async def initialize(self):
        """
        异步初始化引擎

        创建数据库表、初始化所有检索器组件
        """
        # 1. 连接数据库
        self.db_connection = await aiosqlite.connect(self.db_path)
        self.db_connection.row_factory = aiosqlite.Row
        await self.db_connection.execute("PRAGMA journal_mode = WAL")
        await self.db_connection.execute("PRAGMA busy_timeout = 10000")

        # 2. 创建表结构
        await self._create_tables()

        # 3. 初始化文本处理器
        stopwords_path = self.config.get("stopwords_path")
        self.text_processor = TextProcessor(stopwords_path)

        # 4. 初始化RRF融合器
        rrf_k = self.config.get("rrf_k", 60)
        self.rrf_fusion = RRFFusion(k=rrf_k)

        # 5. 初始化BM25检索器
        self.bm25_retriever = BM25Retriever(
            self.db_path, self.text_processor, self.config
        )
        await self.bm25_retriever.initialize()

        # 6. 初始化向量检索器
        self.vector_retriever = VectorRetriever(self.faiss_db, self.config)

        # 7. 初始化混合检索器
        self.hybrid_retriever = HybridRetriever(
            self.bm25_retriever,
            self.vector_retriever,
            self.rrf_fusion,
            self.config,
            rerank_provider_resolver=self.rerank_provider_resolver,
        )

        if self.graph_enabled and self.graph_vector_db is not None:
            self.graph_store = GraphStore(self.db_path)
            await self.graph_store.initialize()

            self.atom_store = AtomStore(self.db_path)
            await self.atom_store.initialize()

            if self.atom_enabled:
                self.atom_lifecycle_manager = AtomLifecycleManager(
                    self.atom_store, self.config
                )
                self.atom_retriever = AtomRetriever(self.atom_store, self.config)
                await self.atom_lifecycle_manager.start()

            self.graph_extractor = GraphExtractor(self.config)
            self.graph_keyword_retriever = GraphKeywordRetriever(
                self.graph_store,
                self.text_processor,
                self.config,
            )
            self.graph_vector_retriever = GraphVectorRetriever(
                self.graph_vector_db,
                self.config,
            )
            self.graph_retriever = GraphRetriever(
                self.graph_keyword_retriever,
                self.graph_vector_retriever,
                self.rrf_fusion,
                self.config,
            )
            self.graph_memory_manager = GraphMemoryManager(
                self.graph_store,
                self.graph_vector_retriever,
                self.graph_extractor,
            )
            self.dual_route_retriever = DualRouteRetriever(
                self.hybrid_retriever,
                self.graph_retriever,
                self.get_memory,
                self.config,
            )

        if self._write_op_repair_enabled:
            await self._repair_incomplete_write_ops()

    async def close(self):
        """关闭数据库连接和清理资源"""
        if self.atom_lifecycle_manager is not None:
            await self.atom_lifecycle_manager.stop()
        if self._pending_tasks:
            for task in self._pending_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self._pending_tasks, return_exceptions=True)
            self._pending_tasks.clear()
        if self.db_connection:
            await self.db_connection.close()
        if self.graph_vector_db is not None:
            await self.graph_vector_db.close()

    def _create_tracked_task(self, coro) -> asyncio.Task:
        """Create and track a background task, auto-discarding on completion."""
        task = asyncio.create_task(coro)
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)
        return task

    async def _create_tables(self):
        """创建数据库表

        注意：documents 表主要由 FAISS 的 DocumentStorage 类创建和管理。
        这里使用 CREATE TABLE IF NOT EXISTS 确保兼容性：
        - 如果 FAISS 已创建，不会重复创建（IF NOT EXISTS）
        - 如果 FAISS 未创建（极端情况），插件仍能正常工作
        - 插件需要直接操作此表进行高频更新（如访问时间）
        """
        # documents表 - 与FAISS共享，IF NOT EXISTS确保不重复创建
        if self.db_connection is not None:
            await self._drop_legacy_documents_fts_triggers()

            await self.db_connection.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id TEXT,
                text TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                created_at TEXT,
                updated_at TEXT
            )
        """)

            # 兼容旧版插件创建的简化 documents 表，确保 FAISS DocumentStorage 所需字段存在
            cursor = await self.db_connection.execute("PRAGMA table_info(documents)")
            column_rows = await cursor.fetchall()
            existing_columns = {row[1] for row in column_rows}

            missing_columns = []
            if "doc_id" not in existing_columns:
                await self.db_connection.execute(
                    "ALTER TABLE documents ADD COLUMN doc_id TEXT"
                )
                missing_columns.append("doc_id")
            if "created_at" not in existing_columns:
                await self.db_connection.execute(
                    "ALTER TABLE documents ADD COLUMN created_at TEXT"
                )
                missing_columns.append("created_at")
            if "updated_at" not in existing_columns:
                await self.db_connection.execute(
                    "ALTER TABLE documents ADD COLUMN updated_at TEXT"
                )
                missing_columns.append("updated_at")

            if missing_columns:
                logger.warning(
                    "[MemoryEngine] 检测到旧版 documents 表结构，已补齐字段: "
                    f"{', '.join(missing_columns)}"
                )

            # 回填旧数据，避免 doc_id/timestamp 缺失导致删除与展示异常
            await self.db_connection.execute("""
            UPDATE documents
            SET doc_id = 'legacy-' || id
            WHERE doc_id IS NULL OR TRIM(doc_id) = ''
        """)
            await self.db_connection.execute("""
            UPDATE documents
            SET created_at = datetime('now')
            WHERE created_at IS NULL OR TRIM(CAST(created_at AS TEXT)) = ''
        """)
            await self.db_connection.execute("""
            UPDATE documents
            SET updated_at = COALESCE(created_at, datetime('now'))
            WHERE updated_at IS NULL OR TRIM(CAST(updated_at AS TEXT)) = ''
        """)

            # 创建索引以提升session_id查询性能
            await self.db_connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_doc_metadata
            ON documents(json_extract(metadata, '$.session_id'))
        """)
            await self.db_connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_doc_persona_metadata
            ON documents(json_extract(metadata, '$.persona_id'))
        """)
            await self.db_connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_doc_importance_metadata
            ON documents(json_extract(metadata, '$.importance'))
        """)
            await self.db_connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_doc_last_access_metadata
            ON documents(json_extract(metadata, '$.last_access_time'))
        """)
            await self.db_connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_documents_doc_id
            ON documents(doc_id)
        """)

            await self.db_connection.execute("""
            CREATE TABLE IF NOT EXISTS memory_sources (
                memory_id INTEGER PRIMARY KEY,
                source_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)

            await self._create_write_ops_table()

            # 创建版本管理表
            await self.db_connection.execute("""
            CREATE TABLE IF NOT EXISTS db_version (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version INTEGER NOT NULL,
                description TEXT,
                migrated_at TEXT NOT NULL,
                migration_duration_seconds REAL
            )
        """)

            # 创建迁移状态表
            await self.db_connection.execute("""
            CREATE TABLE IF NOT EXISTS migration_status (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            )
        """)

            await self.db_connection.commit()

            # 检查是否需要初始化版本信息
            cursor = await self.db_connection.execute("SELECT COUNT(*) FROM db_version")
            version_result = await cursor.fetchone()
            version_count = version_result[0] if version_result else 0

            if version_count == 0:
                # 全新数据库，设置初始版本为最新迁移版本
                from datetime import datetime, timezone

                from ...storage.db_migration import DBMigration

                await self.db_connection.execute(
                    """
                    INSERT INTO db_version (version, description, migrated_at, migration_duration_seconds)
                    VALUES (?, ?, ?, ?)
                """,
                    (
                        DBMigration.CURRENT_VERSION,
                        "初始版本 - 当前架构",
                        datetime.now(timezone.utc).isoformat(),
                        0.0,
                    ),
                )
                await self.db_connection.commit()

                logger.info(f"已初始化数据库版本信息: v{DBMigration.CURRENT_VERSION}")

    # ==================== 核心记忆操作 ====================

    # ==================== 高级功能 ====================
