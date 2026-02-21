"""
索引一致性验证器 - 检测并修复索引与数据库的不一致问题
"""

import asyncio
import json
from dataclasses import dataclass
from typing import Any, cast

import aiosqlite

from astrbot.api import logger


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


class IndexValidator:
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

    async def _clear_sqlite_storages_with_retry(self, max_attempts: int = 5) -> None:
        """Clear BM25 and documents tables with retry on sqlite lock."""
        for attempt in range(max_attempts):
            try:
                async with aiosqlite.connect(self.db_path) as db:
                    # Wait up to 10s for lock release before failing fast.
                    await db.execute("PRAGMA busy_timeout = 10000")
                    try:
                        await db.execute("DELETE FROM memories_fts")
                    except Exception as e:
                        logger.warning(f"清空BM25索引失败: {e}")

                    await db.execute("DELETE FROM documents")
                    await db.commit()
                return
            except Exception as e:
                if (
                    "database is locked" in str(e).lower()
                    and attempt < max_attempts - 1
                ):
                    wait_seconds = 0.2 * (attempt + 1)
                    logger.warning(
                        f"清空SQLite存储遇到锁，{wait_seconds:.1f}s后重试 "
                        f"({attempt + 1}/{max_attempts}): {e}"
                    )
                    await asyncio.sleep(wait_seconds)
                    continue
                raise

    async def _delete_vector_entry_with_retry(
        self,
        memory_engine: Any,
        doc_id: int,
        doc_uuid: str | None,
        max_attempts: int = 5,
    ) -> None:
        """Delete one vector/document mapping with retry on sqlite lock."""
        for attempt in range(max_attempts):
            try:
                removed = False
                if doc_uuid:
                    await memory_engine.faiss_db.delete(doc_uuid)
                    removed = True

                if not removed:
                    await memory_engine.faiss_db.embedding_storage.delete([int(doc_id)])
                return
            except Exception as e:
                if (
                    "database is locked" in str(e).lower()
                    and attempt < max_attempts - 1
                ):
                    wait_seconds = 0.2 * (attempt + 1)
                    logger.warning(
                        f"删除Faiss文档遇到锁(doc_id={doc_id})，{wait_seconds:.1f}s后重试 "
                        f"({attempt + 1}/{max_attempts}): {e}"
                    )
                    await asyncio.sleep(wait_seconds)
                    continue
                raise

    async def check_consistency(self) -> IndexStatus:
        """
        检查索引一致性

        Returns:
            IndexStatus: 索引状态信息
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # 1. 获取documents表中的文档数和ID集合
                cursor = await db.execute("SELECT COUNT(*) FROM documents")
                count_result = await cursor.fetchone()
                documents_count = count_result[0] if count_result else 0

                cursor = await db.execute("SELECT id FROM documents")
                doc_ids = {row[0] for row in await cursor.fetchall()}

                # 2. 检查BM25索引（memories_fts表）
                cursor = await db.execute("""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name='memories_fts'
                """)
                has_fts_table = await cursor.fetchone()

                if has_fts_table:
                    cursor = await db.execute(
                        "SELECT COUNT(DISTINCT doc_id) FROM memories_fts"
                    )
                    bm25_result = await cursor.fetchone()
                    bm25_count = bm25_result[0] if bm25_result else 0

                    cursor = await db.execute(
                        "SELECT DISTINCT doc_id FROM memories_fts"
                    )
                    bm25_ids = {row[0] for row in await cursor.fetchall()}
                else:
                    bm25_count = 0
                    bm25_ids = set()

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
                missing_in_bm25 = len(doc_ids - bm25_ids)
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

    async def rebuild_indexes(
        self, memory_engine: Any, progress_callback=None
    ) -> dict[str, Any]:
        """
        重建所有索引

        安全策略：先备份原始数据到临时表，重建成功后再删除备份；
        任何步骤失败都可从临时表恢复，不会造成数据丢失。

        Args:
            memory_engine: MemoryEngine实例
            progress_callback: 进度回调函数 (current, total, message)

        Returns:
            Dict: 重建结果
        """
        try:
            logger.info("开始重建索引。")

            # 1. 读取所有文档到内存，同时备份到临时表（原子操作，防止读取期间数据变化）
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA busy_timeout = 10000")
                logger.info("读取 documents 表数据并创建备份。")

                # 创建备份临时表（如已存在则先删除，避免上次失败残留）
                await db.execute("DROP TABLE IF EXISTS _documents_rebuild_backup")
                await db.execute("""
                    CREATE TABLE _documents_rebuild_backup AS
                    SELECT id, doc_id, text, metadata, created_at, updated_at
                    FROM documents
                """)
                await db.commit()

                cursor = await db.execute(
                    "SELECT id, doc_id, text, metadata FROM _documents_rebuild_backup ORDER BY id"
                )
                documents = list(await cursor.fetchall())

            if not documents:
                # 清理空备份表
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute("DROP TABLE IF EXISTS _documents_rebuild_backup")
                    await db.commit()
                return {
                    "success": True,
                    "message": "没有需要重建的文档",
                    "processed": 0,
                    "errors": 0,
                }

            total = len(documents)
            logger.info(f"找到 {total} 条文档需要重建索引（备份已创建）")
            logger.info("清空 documents 表、BM25 索引和向量索引。")

            # 2. 删除向量索引条目（不持有 SQLite 写锁）
            for doc_id, doc_uuid, _text, _metadata_json in documents:
                try:
                    await self._delete_vector_entry_with_retry(
                        memory_engine=memory_engine,
                        doc_id=int(doc_id),
                        doc_uuid=str(doc_uuid) if doc_uuid else None,
                    )
                except Exception as e:
                    logger.warning(
                        f"删除Faiss文档失败 (doc_id={doc_id}, uuid={doc_uuid}): {e}"
                    )

            # 3. 清空 BM25/documents 表（此时备份表仍完整保留）
            await self._clear_sqlite_storages_with_retry()
            logger.info("所有存储已清空，开始重建。")

            # 4. 逐条重建（documents + BM25 + vector）
            success_count = 0
            error_count = 0
            batch_size = 100
            last_progress_update = 0

            for i, (doc_id, _doc_uuid, text, metadata_json) in enumerate(documents, 1):
                try:
                    metadata = json.loads(metadata_json) if metadata_json else {}

                    if "importance" not in metadata:
                        metadata["importance"] = 0.5
                    if "create_time" not in metadata:
                        import time

                        metadata["create_time"] = time.time()
                    if "last_access_time" not in metadata:
                        metadata["last_access_time"] = metadata.get(
                            "create_time", time.time()
                        )
                    if "session_id" not in metadata:
                        metadata["session_id"] = None
                    if "persona_id" not in metadata:
                        metadata["persona_id"] = None

                    await memory_engine.add_memory(
                        content=text,
                        session_id=metadata.get("session_id"),
                        persona_id=metadata.get("persona_id"),
                        importance=metadata.get("importance", 0.5),
                        metadata=metadata,
                    )

                    success_count += 1

                    progress_percentage = (i * 100) // total
                    if progress_callback and progress_percentage > last_progress_update:
                        await progress_callback(i, total, f"已处理 {i}/{total} 条")
                        last_progress_update = progress_percentage

                    if i % batch_size == 0:
                        logger.info(f"重建进度: {i}/{total} ({i * 100 // total}%)")

                except Exception as e:
                    error_count += 1
                    logger.error(f"重建索引失败 doc_id={doc_id}: {e}")

            logger.info(f"索引重建完成: 成功 {success_count} 条, 失败 {error_count} 条")

            # 5. 重建完成后删除备份表（只有全部流程成功才到这里）
            try:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute("DROP TABLE IF EXISTS _documents_rebuild_backup")
                    await db.commit()
                    logger.info("已删除重建备份表")
            except Exception as e:
                logger.warning(f"删除备份表失败（不影响功能）: {e}")

            # 6. 更新迁移状态标记
            from datetime import datetime, timezone

            try:
                async with aiosqlite.connect(self.db_path) as status_db:
                    await status_db.execute("""
                        CREATE TABLE IF NOT EXISTS migration_status (
                            key TEXT PRIMARY KEY,
                            value TEXT,
                            updated_at TEXT
                        )
                    """)
                    await status_db.execute(
                        """
                        INSERT OR REPLACE INTO migration_status (key, value, updated_at)
                        VALUES (?, ?, ?)
                    """,
                        (
                            "needs_index_rebuild",
                            "false",
                            datetime.now(timezone.utc).isoformat(),
                        ),
                    )
                    await status_db.execute(
                        """
                        INSERT OR REPLACE INTO migration_status (key, value, updated_at)
                        VALUES (?, ?, ?)
                    """,
                        (
                            "index_rebuild_completed",
                            "true",
                            datetime.now(timezone.utc).isoformat(),
                        ),
                    )
                    await status_db.commit()
                    logger.info(
                        "已更新迁移状态: needs_index_rebuild=false, index_rebuild_completed=true"
                    )
            except Exception as e:
                logger.warning(f"更新迁移状态失败: {e}")

            return {
                "success": True,
                "message": "索引重建完成",
                "processed": success_count,
                "errors": error_count,
                "total": total,
            }

        except Exception as e:
            logger.error(f"重建索引失败: {e}", exc_info=True)
            # 尝试从备份表恢复
            await self._try_restore_from_backup()
            return {
                "success": False,
                "message": (
                    f"重建索引失败: {str(e)}。"
                    "系统已尝试自动恢复备份，请查看日志后重试 /lmem rebuild-index。"
                ),
                "error": str(e),
            }

    async def _try_restore_from_backup(self) -> None:
        """
        重建失败时尝试从备份表恢复 documents 数据。
        仅在备份表存在且 documents 表为空时执行恢复。
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA busy_timeout = 10000")

                # 检查备份表是否存在
                cursor = await db.execute("""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name='_documents_rebuild_backup'
                """)
                if not await cursor.fetchone():
                    return

                # 只在 documents 表为空时恢复（避免覆盖部分重建的数据）
                cursor = await db.execute("SELECT COUNT(*) FROM documents")
                row = await cursor.fetchone()
                doc_count = row[0] if row else 0

                if doc_count > 0:
                    logger.warning(
                        f"documents 表已有 {doc_count} 条数据，跳过备份恢复（避免重复）"
                    )
                    return

                logger.warning("检测到重建失败且 documents 表为空，正在从备份表恢复...")
                await db.execute("""
                    INSERT INTO documents (id, doc_id, text, metadata, created_at, updated_at)
                    SELECT id, doc_id, text, metadata, created_at, updated_at
                    FROM _documents_rebuild_backup
                """)
                await db.commit()

                cursor = await db.execute("SELECT COUNT(*) FROM documents")
                row = await cursor.fetchone()
                restored = row[0] if row else 0
                logger.info(
                    f"已从备份表恢复 {restored} 条记忆数据，BM25/向量索引需手动重建"
                )

        except Exception as e:
            logger.error(f"从备份表恢复失败: {e}", exc_info=True)
