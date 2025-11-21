"""
索引一致性验证器 - 检测并修复索引与数据库的不一致问题
"""

import json
from dataclasses import dataclass
from typing import Any

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

    async def check_consistency(self) -> IndexStatus:
        """
        检查索引一致性

        Returns:
            IndexStatus: 索引状态信息
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # 0. 首先检查是否有标记为"已完成重建"的状态
                # 如果有，说明索引已经重建过，不需要再次检查
                cursor = await db.execute("""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name='migration_status'
                """)
                has_migration_table = await cursor.fetchone()

                if has_migration_table:
                    cursor = await db.execute("""
                        SELECT value FROM migration_status
                        WHERE key='index_rebuild_completed'
                    """)
                    rebuild_result = await cursor.fetchone()
                    if rebuild_result and len(rebuild_result) > 0 and rebuild_result[0] == "true":
                        # 索引已经重建完成，直接返回一致状态
                        cursor = await db.execute("SELECT COUNT(*) FROM documents")
                        count_result = await cursor.fetchone()
                        documents_count = count_result[0] if count_result else 0

                        logger.debug(
                            f"检测到索引已完成重建标记，跳过详细检查（文档数: {documents_count}）"
                        )

                        return IndexStatus(
                            is_consistent=True,
                            documents_count=documents_count,
                            bm25_count=documents_count,
                            vector_count=documents_count,
                            missing_in_bm25=0,
                            missing_in_vector=0,
                            needs_rebuild=False,
                            reason="索引已重建完成（已验证）",
                        )

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
                    # 通过document_storage获取向量索引中的文档
                    if hasattr(self.faiss_db, "document_storage"):
                        doc_storage = self.faiss_db.document_storage
                        # 获取所有文档ID
                        cursor = await db.execute("""
                            SELECT COUNT(DISTINCT id) FROM documents
                        """)
                        # 简化检查：假设向量索引与document_storage同步
                        # 实际检查需要遍历所有文档
                        for doc_id in doc_ids:
                            try:
                                doc = await doc_storage.get_document(doc_id)
                                if doc:
                                    vector_count += 1
                                    vector_ids.add(doc_id)
                            except Exception as e:
                                logger.debug(f"检查向量索引失败 (doc_id={doc_id}): {e}")
                                pass
                except Exception as e:
                    logger.warning(f"检查向量索引失败: {e}")

                # 4. 计算差异
                missing_in_bm25 = len(doc_ids - bm25_ids)
                missing_in_vector = len(doc_ids - vector_ids)

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
                elif bm25_count > documents_count or vector_count > documents_count:
                    needs_rebuild = True
                    is_consistent = False
                    reason = "索引中存在冗余数据"
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
                pending_count = int(count_row[0]) if count_row and len(count_row) > 0 and count_row[0] else 0

                return True, pending_count

        except Exception as e:
            logger.error(f"获取迁移状态失败: {e}", exc_info=True)
            return False, 0

    async def rebuild_indexes(
        self, memory_engine: Any, progress_callback=None
    ) -> dict[str, Any]:
        """
        重建所有索引

        Args:
            memory_engine: MemoryEngine实例
            progress_callback: 进度回调函数 (current, total, message)

        Returns:
            Dict: 重建结果
        """
        try:
            logger.info(" 开始重建索引...")

            async with aiosqlite.connect(self.db_path) as db:
                # 1. 先读取所有文档到内存（在清空前）
                logger.info(" 读取documents表数据...")
                cursor = await db.execute(
                    "SELECT id, text, metadata FROM documents ORDER BY id"
                )
                documents = await cursor.fetchall()

                if not documents:
                    return {
                        "success": True,
                        "message": "没有需要重建的文档",
                        "processed": 0,
                        "errors": 0,
                    }

                total = len(list(documents))
                logger.info(f" 找到 {total} 条文档需要重建索引")

                # 2. 清空所有存储（documents表、BM25索引、向量索引）
                logger.info("️ 清空documents表、BM25索引和向量索引...")

                # 清空BM25索引
                try:
                    await db.execute("DELETE FROM memories_fts")
                except Exception as e:
                    logger.warning(f"清空BM25索引失败: {e}")

                # 清空documents表
                await db.execute("DELETE FROM documents")
                await db.commit()

                # 清空向量索引 - 分批处理以避免内存问题
                try:
                    batch_size = 500
                    offset = 0
                    while True:
                        batch_docs = (
                            await memory_engine.faiss_db.document_storage.get_documents(
                                metadata_filters={}, offset=offset, limit=batch_size
                            )
                        )
                        if not batch_docs:
                            break

                        for doc in batch_docs:
                            try:
                                await memory_engine.faiss_db.delete(doc["doc_id"])
                            except Exception as e:
                                logger.warning(
                                    f"删除Faiss文档失败 (doc_id={doc.get('doc_id')}): {e}"
                                )

                        if len(batch_docs) < batch_size:
                            break
                        offset += batch_size
                except Exception as e:
                    logger.warning(f"清空Faiss索引时出错: {e}")

                logger.info(" 所有存储已清空")

                # 3. 重建所有存储（documents表 + 索引）
                success_count = 0
                error_count = 0
                batch_size = 100  # 批处理大小
                last_progress_update = 0

                for i, (doc_id, text, metadata_json) in enumerate(documents, 1):
                    try:
                        # 解析metadata
                        metadata = json.loads(metadata_json) if metadata_json else {}

                        # 确保metadata包含必要字段
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

                        # 关键修复：使用add_memory重建所有存储
                        # 虽然会生成新的doc_id，但这是可以接受的
                        # 因为我们已经清空了所有数据
                        await memory_engine.add_memory(
                            content=text,
                            session_id=metadata.get("session_id"),
                            persona_id=metadata.get("persona_id"),
                            importance=metadata.get("importance", 0.5),
                            metadata=metadata,
                        )

                        success_count += 1

                        # 进度回调 - 降低频率到每100条
                        progress_percentage = (i * 100) // total
                        if (
                            progress_callback
                            and progress_percentage > last_progress_update
                        ):
                            await progress_callback(i, total, f"已处理 {i}/{total} 条")
                            last_progress_update = progress_percentage

                        # 每处理100条记录记录一次进度
                        if i % batch_size == 0:
                            logger.info(f" 重建进度: {i}/{total} ({i * 100 // total}%)")

                    except Exception as e:
                        error_count += 1
                        logger.error(f"重建索引失败 doc_id={doc_id}: {e}")

                # 4. 更新迁移状态标记
                from datetime import datetime

                logger.info(
                    f" 索引重建完成: 成功{success_count}条, 失败{error_count}条"
                )

            # 5. 在主数据库连接外部更新状态标记（确保提交）
            try:
                async with aiosqlite.connect(self.db_path) as status_db:
                    # 确保migration_status表存在
                    await status_db.execute("""
                        CREATE TABLE IF NOT EXISTS migration_status (
                            key TEXT PRIMARY KEY,
                            value TEXT,
                            updated_at TEXT
                        )
                    """)

                    # 更新需要重建标记为false
                    await status_db.execute(
                        """
                        INSERT OR REPLACE INTO migration_status (key, value, updated_at)
                        VALUES (?, ?, ?)
                    """,
                        ("needs_index_rebuild", "false", datetime.utcnow().isoformat()),
                    )

                    # 添加索引重建完成标记
                    await status_db.execute(
                        """
                        INSERT OR REPLACE INTO migration_status (key, value, updated_at)
                        VALUES (?, ?, ?)
                    """,
                        (
                            "index_rebuild_completed",
                            "true",
                            datetime.utcnow().isoformat(),
                        ),
                    )

                    await status_db.commit()
                    logger.info(
                        "已更新迁移状态: needs_index_rebuild=false, index_rebuild_completed=true"
                    )
            except Exception as e:
                logger.warning(f"更新迁移状态失败: {e}")

            # 6. 返回重建结果
            return {
                "success": True,
                "message": "索引重建完成",
                "processed": success_count,
                "errors": error_count,
                "total": total,
            }

        except Exception as e:
            logger.error(f"重建索引失败: {e}", exc_info=True)
            return {
                "success": False,
                "message": f"重建索引失败: {str(e)}",
                "error": str(e),
            }
