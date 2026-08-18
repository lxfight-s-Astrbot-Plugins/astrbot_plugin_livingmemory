"""
索引一致性验证器 - 检测并修复索引与数据库的不一致问题
"""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any, cast

import aiosqlite

from astrbot.api import logger
from .index_validator_rebuild import IndexValidatorRebuildMixin

@dataclass
class IndexStatus:
    """索引状态信息"""

    is_consistent: bool  # 是否一致
    documents_count: int  # documents表中的文档数
    bm25_count: int  # BM25索引中的文档数
    vector_count: int  # 向量索引中的文档数
    missing_in_bm25: int  # documents中有但BM25中缺失的数量
    missing_in_vector: int  # documents中有但向量索引中缺失的数量
    needs_rebuild: bool  # 是否需要重建
    reason: str  # 不一致的原因描述

class IndexValidator(IndexValidatorRebuildMixin):
    """
    索引一致性验证器

    检测documents表与BM25索引、向量索引之间的一致性
    """

    def __init__(self, db_path: str, faiss_db: Any):
        """
        初始化验证器

        Args:
            db_path: SQLite数据库路径
            faiss_db: FaissVecDB实例
        """
        self.db_path = db_path
        self.faiss_db = faiss_db
        self._maintenance_lock = asyncio.Lock()

    DEFAULT_REBUILD_BATCH_SIZE = 50
    DEFAULT_EMBEDDING_BATCH_SIZE = 8
    DEFAULT_TASKS_LIMIT = 1
    DEFAULT_MAX_RETRIES = 5
    DEFAULT_RETRY_BASE_DELAY = 30.0
    DEFAULT_BATCH_DELAY = 5.0
    DEFAULT_REQUEST_DELAY = 5.0
    RATE_LIMIT_RETRY_MIN_DELAY = 30.0
    DEFAULT_MAX_FAILURE_RATIO = 0.02
    VECTOR_SCHEMA_VERSION = "document-vector-v2"
    ACTIVE_DOCUMENT_SQL = (
        "COALESCE(json_extract(metadata, '$.status'), 'active') = 'active'"
    )

    def get_provider_fingerprint(self) -> str:
        provider = getattr(self.faiss_db, "embedding_provider", None)
        provider_config = getattr(provider, "provider_config", {}) or {}
        if not isinstance(provider_config, dict):
            provider_config = {}

        get_model = getattr(provider, "get_model", None)
        try:
            model = get_model() if callable(get_model) else None
        except Exception:
            model = None
        model = (
            model
            or getattr(provider, "model", None)
            or getattr(provider, "model_name", None)
            or provider_config.get("embedding_model")
            or provider_config.get("model")
            or "unknown"
        )

        get_dim = getattr(provider, "get_dim", None)
        try:
            dimension = int(get_dim()) if callable(get_dim) else 0
        except Exception:
            dimension = 0
        if dimension <= 0:
            storage = getattr(self.faiss_db, "embedding_storage", None)
            dimension = int(getattr(storage, "dimension", 0) or 0)

        payload = {
            "schema": self.VECTOR_SCHEMA_VERSION,
            "provider_class": (
                f"{type(provider).__module__}.{type(provider).__qualname__}"
                if provider is not None
                else "unknown"
            ),
            "provider_id": provider_config.get("id", "unknown"),
            "provider_type": provider_config.get("type", "unknown"),
            "model": str(model),
            "dimension": dimension,
            "dimensions_mode": provider_config.get("embedding_dimensions_mode"),
            "input_type": provider_config.get("input_type"),
        }
        encoded = json.dumps(
            payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    async def provider_fingerprint_changed(self) -> bool:
        """Adopt legacy indexes once, then detect semantic provider changes."""
        current = self.get_provider_fingerprint()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS migration_status (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT
                )
                """
            )
            cursor = await db.execute(
                "SELECT value FROM migration_status WHERE key = ?",
                ("document_vector_provider_fingerprint",),
            )
            row = await cursor.fetchone()
            if row is None:
                await db.execute(
                    """
                    INSERT INTO migration_status(key, value, updated_at)
                    VALUES (?, ?, datetime('now'))
                    """,
                    ("document_vector_provider_fingerprint", current),
                )
                await db.commit()
                return False
            return str(row[0] or "") != current

    async def record_provider_fingerprint(self) -> None:
        current = self.get_provider_fingerprint()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO migration_status(key, value, updated_at)
                VALUES (?, ?, datetime('now'))
                """,
                ("document_vector_provider_fingerprint", current),
            )
            await db.commit()

    async def check_consistency(self) -> IndexStatus:
        """
        检查索引一致性

        Returns:
            IndexStatus: 索引状态信息
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # 1. 获取documents表中的文档数和ID集合
                cursor = await db.execute(
                    f"SELECT COUNT(*) FROM documents WHERE {self.ACTIVE_DOCUMENT_SQL}"
                )
                count_result = await cursor.fetchone()
                documents_count = count_result[0] if count_result else 0

                cursor = await db.execute(
                    f"SELECT id FROM documents WHERE {self.ACTIVE_DOCUMENT_SQL}"
                )
                doc_ids = {row[0] for row in await cursor.fetchall()}

                # 2. 检查BM25索引（livingmemory_memories_fts表）
                cursor = await db.execute("""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name='livingmemory_memories_fts'
                """)
                has_fts_table = await cursor.fetchone()

                if has_fts_table:
                    cursor = await db.execute(
                        "SELECT COUNT(DISTINCT doc_id) FROM livingmemory_memories_fts"
                    )
                    bm25_result = await cursor.fetchone()
                    bm25_count = bm25_result[0] if bm25_result else 0

                    # 直接在 SQL 层计算 BM25 缺失数量，避免加载全量 ID 集合到内存。
                    cursor = await db.execute(
                        f"""
                        SELECT COUNT(*) FROM documents d
                        WHERE {self.ACTIVE_DOCUMENT_SQL}
                          AND NOT EXISTS (
                              SELECT 1 FROM livingmemory_memories_fts f
                              WHERE f.doc_id = d.id
                          )
                        """
                    )
                    missing_result = await cursor.fetchone()
                    missing_in_bm25 = missing_result[0] if missing_result else 0
                else:
                    bm25_count = 0
                    missing_in_bm25 = 0

                # 3. 检查向量索引
                vector_count = 0
                vector_ids = set()

                try:
                    embedding_storage = getattr(
                        self.faiss_db, "embedding_storage", None
                    )
                    index = getattr(embedding_storage, "index", None)
                    if index is not None:
                        vector_count = int(getattr(index, "ntotal", 0))
                        # Try to get concrete vector IDs from IndexIDMap.
                        try:
                            import faiss

                            if hasattr(index, "id_map"):
                                vector_to_array = getattr(
                                    faiss, "vector_to_array", None
                                )
                                if callable(vector_to_array):
                                    raw_ids = cast(Any, vector_to_array(index.id_map))
                                    vector_ids = {int(i) for i in raw_ids}
                        except Exception as e:
                            logger.debug(f"读取向量ID失败，使用计数模式: {e}")
                except Exception as e:
                    logger.warning(f"检查向量索引失败: {e}")

                # 4. 计算差异
                if vector_ids:
                    missing_in_vector = len(doc_ids - vector_ids)
                else:
                    missing_in_vector = max(0, documents_count - vector_count)

                # 5. 判断是否需要重建
                needs_rebuild = False
                reason = ""

                if documents_count == 0:
                    reason = "数据库为空"
                    is_consistent = True
                elif missing_in_bm25 > 0 or missing_in_vector > 0:
                    needs_rebuild = True
                    is_consistent = False
                    reasons = []
                    if missing_in_bm25 > 0:
                        reasons.append(f"BM25索引缺失{missing_in_bm25}条文档")
                    if missing_in_vector > 0:
                        reasons.append(f"向量索引缺失{missing_in_vector}条文档")
                    reason = "；".join(reasons)
                elif bm25_count > documents_count:
                    needs_rebuild = True
                    is_consistent = False
                    reason = "BM25索引中存在冗余数据"
                elif vector_count > documents_count:
                    # FAISS ntotal 包含逻辑删除的槽位，冗余向量不影响召回正确性，
                    # 不触发全量重建（否则每次启动都会重建）
                    is_consistent = True
                    reason = f"向量索引含{vector_count - documents_count}条冗余槽位（正常，不影响召回）"
                else:
                    is_consistent = True
                    reason = "索引状态正常"

                return IndexStatus(
                    is_consistent=is_consistent,
                    documents_count=documents_count,
                    bm25_count=bm25_count,
                    vector_count=vector_count,
                    missing_in_bm25=missing_in_bm25,
                    missing_in_vector=missing_in_vector,
                    needs_rebuild=needs_rebuild,
                    reason=reason,
                )

        except Exception as e:
            logger.error(f"检查索引一致性失败: {e}", exc_info=True)
            return IndexStatus(
                is_consistent=False,
                documents_count=0,
                bm25_count=0,
                vector_count=0,
                missing_in_bm25=0,
                missing_in_vector=0,
                needs_rebuild=True,
                reason=f"检查失败: {str(e)}",
            )

    async def get_migration_status(self) -> tuple[bool, int]:
        """
        获取v1迁移状态

        Returns:
            Tuple[bool, int]: (是否需要重建, 待处理文档数)
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # 检查migration_status表
                cursor = await db.execute("""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name='migration_status'
                """)
                has_table = await cursor.fetchone()

                if not has_table:
                    return False, 0

                # 检查是否需要重建
                cursor = await db.execute("""
                    SELECT value FROM migration_status
                    WHERE key='needs_index_rebuild'
                """)
                row = await cursor.fetchone()

                if not row or len(row) == 0 or row[0] != "true":
                    return False, 0

                # 获取待处理文档数
                cursor = await db.execute("""
                    SELECT value FROM migration_status
                    WHERE key='pending_documents_count'
                """)
                count_row = await cursor.fetchone()
                pending_count = (
                    int(count_row[0])
                    if count_row and len(count_row) > 0 and count_row[0]
                    else 0
                )

                return True, pending_count

        except Exception as e:
            logger.error(f"获取迁移状态失败: {e}", exc_info=True)
            return False, 0

    def _get_vector_ids(self) -> set[int] | None:
        embedding_storage = getattr(self.faiss_db, "embedding_storage", None)
        index = getattr(embedding_storage, "index", None)
        return self._get_ids_from_index(index)
