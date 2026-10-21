"""Write-side index maintenance for the memory engine.

Keeps the BM25 index, the vector index and the shared ``documents`` table in
sync for add / update / delete. Query-side fusion lives in
``hybrid_retriever``; separating the two makes the read/write responsibilities
explicit and keeps the fusion module focused on ranking.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from astrbot.api import logger


class MemoryIndexWriter:
    """Coordinate writes across the BM25 and vector stores."""

    def __init__(self, bm25_retriever: Any, vector_retriever: Any):
        self.bm25_retriever = bm25_retriever
        self.vector_retriever = vector_retriever

    async def add_memory(
        self, content: str, metadata: dict[str, Any] | None = None
    ) -> int:
        """添加记忆到两个索引。

        Args:
            content: 记忆内容。
            metadata: 元数据（缺少时补默认值）。

        Returns:
            int: 两个索引中一致的文档 ID。
        """
        metadata = metadata or {}
        if "importance" not in metadata:
            metadata["importance"] = 0.5
        if "create_time" not in metadata:
            metadata["create_time"] = time.time()
        if "last_access_time" not in metadata:
            metadata["last_access_time"] = time.time()
        if "session_id" not in metadata:
            metadata["session_id"] = None
        if "persona_id" not in metadata:
            metadata["persona_id"] = None

        # 先添加到向量库获取 doc_id，再用同一 ID 写入 BM25。
        doc_id = await self.vector_retriever.add_document(content, metadata)

        # BM25 失败时必须清理已写入的向量和文档记录，否则调用方无法得知残留 ID。
        try:
            await self.bm25_retriever.add_document(doc_id, content, metadata)
        except asyncio.CancelledError:
            await asyncio.shield(self.vector_retriever.delete_document(doc_id))
            raise
        except Exception:
            await self.vector_retriever.delete_document(doc_id)
            raise

        return doc_id

    async def update_metadata(self, doc_id: int, metadata: dict[str, Any]) -> bool:
        """同步更新所有存储层的元数据。

        FAISS 侧会同步更新 DocumentStorage；BM25 不存储 metadata，从
        ``documents`` 表读取，因此无需额外更新。

        Args:
            doc_id: documents 表主键。
            metadata: 新的元数据字典。

        Returns:
            bool: 是否更新成功。
        """
        try:
            vector_success = await self.vector_retriever.update_metadata(
                doc_id, metadata
            )
            if not vector_success:
                logger.error(f"[同步更新] FAISS更新失败 (doc_id={doc_id})")
                return False
            logger.info(f"[同步更新] 元数据更新成功 (doc_id={doc_id})")
            return True
        except Exception as e:
            logger.error(f"[同步更新] 失败 (doc_id={doc_id}): {e}", exc_info=True)
            return False

    async def delete_memory(self, doc_id: int) -> bool:
        """从多个存储层中删除记忆（向量删除失败时回滚 BM25）。

        Args:
            doc_id: 文档ID。

        Returns:
            bool: 是否成功删除。
        """
        backup_content: str | None = None
        backup_metadata: dict[str, Any] = {}
        try:
            try:
                async with self.bm25_retriever._connect() as db:
                    cursor = await db.execute(
                        "SELECT text, metadata FROM documents WHERE id = ?", (doc_id,)
                    )
                    row = await cursor.fetchone()
                    if row:
                        backup_content = row[0]
                        metadata_raw = row[1]
                        if isinstance(metadata_raw, str) and metadata_raw:
                            try:
                                backup_metadata = json.loads(metadata_raw)
                            except (json.JSONDecodeError, TypeError):
                                backup_metadata = {}
                        elif isinstance(metadata_raw, dict):
                            backup_metadata = metadata_raw
            except Exception as e:
                logger.warning(f"[删除] 备份文档内容失败 (doc_id={doc_id}): {e}")

            # 先删除 BM25（外键引用），再删向量主数据。
            try:
                bm25_deleted = await self.bm25_retriever.delete_document(doc_id)
                if not bm25_deleted:
                    logger.warning(f"[删除] BM25索引删除失败 (doc_id={doc_id})")
                    return False
                logger.debug(f"[删除] BM25索引已删除 (doc_id={doc_id})")
            except Exception as e:
                logger.error(f"[删除] BM25删除异常 (doc_id={doc_id}): {e}")
                return False

            try:
                vector_deleted = await self.vector_retriever.delete_document(doc_id)
                if not vector_deleted:
                    logger.error(f"[删除] 向量库删除失败，需回滚 (doc_id={doc_id})")
                    await self._rollback_bm25_delete(
                        doc_id, backup_content, backup_metadata
                    )
                    return False
                logger.debug(f"[删除] 向量库已删除 (doc_id={doc_id})")
            except Exception as e:
                logger.error(f"[删除] 向量删除异常，回滚BM25 (doc_id={doc_id}): {e}")
                await self._rollback_bm25_delete(
                    doc_id, backup_content, backup_metadata
                )
                return False

            try:
                async with self.bm25_retriever._connect() as db:
                    await db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
                    await db.commit()
                logger.debug(f"[删除] documents表已删除 (doc_id={doc_id})")
            except Exception as e:
                # documents 删除失败不影响整体，主要数据已删除。
                logger.warning(f"[删除] documents表删除失败 (doc_id={doc_id}): {e}")

            logger.info(f"[删除] 记忆删除成功 (doc_id={doc_id})")
            return True
        except Exception as e:
            logger.error(f"[删除] 删除记忆失败 (doc_id={doc_id}): {e}", exc_info=True)
            return False

    async def _rollback_bm25_delete(
        self,
        doc_id: int,
        content: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """尽力恢复被误删的 BM25 文档。"""
        if not content:
            logger.error(
                f"[回滚] 缺少备份文本，无法恢复BM25索引 (doc_id={doc_id})。"
                "建议执行索引重建修复一致性。"
            )
            return
        try:
            rollback_ok = await self.bm25_retriever.update_document(
                doc_id, content, metadata or {}
            )
            if rollback_ok:
                logger.info(f"[回滚] 已恢复BM25索引 (doc_id={doc_id})")
            else:
                logger.error(
                    f"[回滚] BM25索引恢复失败 (doc_id={doc_id})，建议执行索引重建"
                )
        except Exception as e:
            logger.error(
                f"[回滚] BM25索引恢复异常 (doc_id={doc_id}): {e}",
                exc_info=True,
            )


__all__ = ["MemoryIndexWriter"]
