"""
索引一致性验证器 - 检测并修复索引与数据库的不一致问题
"""

import asyncio
import os
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

    DEFAULT_REBUILD_BATCH_SIZE = 50
    DEFAULT_EMBEDDING_BATCH_SIZE = 8
    DEFAULT_TASKS_LIMIT = 1
    DEFAULT_MAX_RETRIES = 5
    DEFAULT_RETRY_BASE_DELAY = 30.0
    DEFAULT_BATCH_DELAY = 5.0
    DEFAULT_REQUEST_DELAY = 5.0
    RATE_LIMIT_RETRY_MIN_DELAY = 30.0
    DEFAULT_MAX_FAILURE_RATIO = 0.02

    async def _clear_bm25_with_retry(
        self, table_name: str = "livingmemory_memories_fts", max_attempts: int = 5
    ) -> None:
        """清空 BM25 索引表，不触碰 documents 原始数据。"""
        for attempt in range(max_attempts):
            try:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute("PRAGMA busy_timeout = 10000")
                    try:
                        await db.execute(f"DELETE FROM {table_name}")
                    except Exception as e:
                        logger.warning(f"清空BM25索引失败: {e}")
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

                    cursor = await db.execute(
                        "SELECT DISTINCT doc_id FROM livingmemory_memories_fts"
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

    def _get_rebuild_options(self, memory_engine: Any) -> dict[str, Any]:
        config = getattr(memory_engine, "config", {}) or {}

        def read_int(key: str, default: int, minimum: int, maximum: int) -> int:
            try:
                value = int(config.get(key, default))
            except (TypeError, ValueError):
                value = default
            return max(minimum, min(maximum, value))

        def read_float(
            key: str, default: float, minimum: float, maximum: float
        ) -> float:
            try:
                value = float(config.get(key, default))
            except (TypeError, ValueError):
                value = default
            return max(minimum, min(maximum, value))

        return {
            "batch_size": read_int(
                "index_rebuild_batch_size", self.DEFAULT_REBUILD_BATCH_SIZE, 1, 500
            ),
            "embedding_batch_size": read_int(
                "index_rebuild_embedding_batch_size",
                self.DEFAULT_EMBEDDING_BATCH_SIZE,
                1,
                256,
            ),
            "tasks_limit": read_int(
                "index_rebuild_tasks_limit", self.DEFAULT_TASKS_LIMIT, 1, 8
            ),
            "max_retries": read_int(
                "index_rebuild_max_retries", self.DEFAULT_MAX_RETRIES, 1, 8
            ),
            "retry_base_delay": read_float(
                "index_rebuild_retry_base_delay",
                self.DEFAULT_RETRY_BASE_DELAY,
                0.0,
                60.0,
            ),
            "batch_delay": read_float(
                "index_rebuild_batch_delay", self.DEFAULT_BATCH_DELAY, 0.0, 10.0
            ),
            "request_delay": read_float(
                "index_rebuild_request_delay", self.DEFAULT_REQUEST_DELAY, 0.0, 60.0
            ),
            "max_failure_ratio": read_float(
                "index_rebuild_max_failure_ratio",
                self.DEFAULT_MAX_FAILURE_RATIO,
                0.0,
                1.0,
            ),
        }

    @staticmethod
    def _failure_ratio(errors: int, total: int) -> float:
        if total <= 0:
            return 0.0
        return errors / total

    @staticmethod
    def _is_rate_limit_error(error: Exception) -> bool:
        message = str(error).lower()
        return (
            "429" in message
            or "rate limit" in message
            or "tpm limit" in message
            or "too many requests" in message
        )

    async def _get_document_count(self) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM documents")
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

    async def _get_document_ids(self) -> set[int]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT id FROM documents")
            return {int(row[0]) for row in await cursor.fetchall()}

    async def _iter_document_batches(
        self,
        batch_size: int,
        document_ids: set[int] | None = None,
    ):
        if document_ids is not None:
            sorted_ids = sorted(int(doc_id) for doc_id in document_ids)
            for start in range(0, len(sorted_ids), batch_size):
                chunk = sorted_ids[start : start + batch_size]
                placeholders = ",".join("?" for _ in chunk)
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute("PRAGMA busy_timeout = 10000")
                    cursor = await db.execute(
                        f"""
                        SELECT id, doc_id, text, metadata
                        FROM documents
                        WHERE id IN ({placeholders})
                        ORDER BY id
                        """,
                        chunk,
                    )
                    yield await cursor.fetchall()
            return

        last_id = 0
        while True:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA busy_timeout = 10000")
                cursor = await db.execute(
                    """
                    SELECT id, doc_id, text, metadata
                    FROM documents
                    WHERE id > ?
                    ORDER BY id
                    LIMIT ?
                    """,
                    (last_id, batch_size),
                )
                rows = await cursor.fetchall()

            if not rows:
                break
            last_id = int(rows[-1][0])
            yield rows

    def _get_vector_count(self) -> int:
        embedding_storage = getattr(self.faiss_db, "embedding_storage", None)
        index = getattr(embedding_storage, "index", None)
        if index is None:
            return 0
        return int(getattr(index, "ntotal", 0))

    def _get_vector_ids(self) -> set[int] | None:
        embedding_storage = getattr(self.faiss_db, "embedding_storage", None)
        index = getattr(embedding_storage, "index", None)
        if index is None:
            return set()
        try:
            import faiss

            if hasattr(index, "id_map"):
                vector_to_array = getattr(faiss, "vector_to_array", None)
                if callable(vector_to_array):
                    raw_ids = cast(Any, vector_to_array(index.id_map))
                    return {int(i) for i in raw_ids}
        except Exception as e:
            logger.debug(f"读取向量ID失败: {e}")
        return None

    async def _rebuild_bm25_index(
        self,
        memory_engine: Any,
        total: int,
        options: dict[str, Any],
        progress_callback=None,
    ) -> dict[str, Any]:
        bm25_retriever = getattr(memory_engine, "bm25_retriever", None)
        text_processor = getattr(bm25_retriever, "text_processor", None)
        if text_processor is None:
            text_processor = getattr(memory_engine, "text_processor", None)
        if text_processor is None:
            raise RuntimeError("无法重建 BM25：TextProcessor 未初始化")

        table_name = getattr(bm25_retriever, "fts_table", "livingmemory_memories_fts")
        batch_size = int(options["batch_size"])
        max_failure_ratio = float(options["max_failure_ratio"])

        await self._clear_bm25_with_retry(table_name)
        processed = 0
        failed_ids: set[int] = set()

        async for batch in self._iter_document_batches(batch_size):
            rows_to_insert: list[tuple[int, str]] = []
            for doc_id, _doc_uuid, text, _metadata_json in batch:
                try:
                    if hasattr(text_processor, "preprocess_for_bm25"):
                        processed_content = text_processor.preprocess_for_bm25(
                            text or ""
                        )
                    else:
                        tokens = text_processor.tokenize(text or "", True)
                        processed_content = " ".join(tokens)
                    rows_to_insert.append((int(doc_id), processed_content))
                except Exception as e:
                    failed_ids.add(int(doc_id))
                    logger.error(f"BM25 预处理失败 doc_id={doc_id}: {e}")

            if rows_to_insert:
                try:
                    async with aiosqlite.connect(self.db_path) as db:
                        await db.execute("PRAGMA busy_timeout = 10000")
                        await db.executemany(
                            f"INSERT INTO {table_name}(doc_id, content) VALUES (?, ?)",
                            rows_to_insert,
                        )
                        await db.commit()
                    processed += len(rows_to_insert)
                except Exception as batch_error:
                    logger.warning(
                        f"BM25 批量写入失败，将逐条重试: {batch_error}"
                    )
                    for row_doc_id, processed_content in rows_to_insert:
                        try:
                            async with aiosqlite.connect(self.db_path) as db:
                                await db.execute("PRAGMA busy_timeout = 10000")
                                await db.execute(
                                    f"INSERT INTO {table_name}(doc_id, content) VALUES (?, ?)",
                                    (row_doc_id, processed_content),
                                )
                                await db.commit()
                            processed += 1
                        except Exception as e:
                            failed_ids.add(int(row_doc_id))
                            logger.error(f"BM25 写入失败 doc_id={row_doc_id}: {e}")

            if progress_callback:
                await progress_callback(
                    processed,
                    total,
                    f"BM25 已处理 {processed}/{total} 条",
                )

            if self._failure_ratio(len(failed_ids), total) > max_failure_ratio:
                logger.error(
                    f"BM25 重建失败率过高: {len(failed_ids)}/{total}，停止后续重建"
                )
                break

        return {
            "processed": processed,
            "errors": len(failed_ids),
            "failed_ids": failed_ids,
        }

    async def _embed_batch_with_retry(
        self,
        provider: Any,
        contents: list[str],
        options: dict[str, Any],
    ) -> list[Any]:
        if not contents:
            return []

        max_retries = int(options["max_retries"])
        retry_base_delay = float(options["retry_base_delay"])
        embedding_batch_size = int(options["embedding_batch_size"])
        request_delay = float(options["request_delay"])
        vectors: list[Any] = []

        for start in range(0, len(contents), embedding_batch_size):
            chunk = contents[start : start + embedding_batch_size]
            vectors.extend(
                await self._embed_request_with_retry(
                    provider,
                    chunk,
                    max_retries=max_retries,
                    retry_base_delay=retry_base_delay,
                )
            )
            if request_delay > 0 and start + embedding_batch_size < len(contents):
                await asyncio.sleep(request_delay)

        return vectors

    async def _embed_request_with_retry(
        self,
        provider: Any,
        contents: list[str],
        *,
        max_retries: int,
        retry_base_delay: float,
    ) -> list[Any]:
        last_error: Exception | None = None

        for attempt in range(max_retries):
            try:
                get_embeddings = getattr(provider, "get_embeddings", None)
                if callable(get_embeddings):
                    return await get_embeddings(contents)

                if hasattr(provider, "get_embeddings_batch"):
                    try:
                        return await provider.get_embeddings_batch(
                            contents,
                            batch_size=len(contents),
                            tasks_limit=1,
                            max_retries=1,
                        )
                    except TypeError:
                        return await provider.get_embeddings_batch(contents)

                vectors = []
                for content in contents:
                    vectors.append(await provider.get_embedding(content))
                return vectors
            except Exception as e:
                last_error = e
                if attempt >= max_retries - 1:
                    break
                wait_seconds = retry_base_delay * (2**attempt)
                if self._is_rate_limit_error(e):
                    wait_seconds = max(wait_seconds, self.RATE_LIMIT_RETRY_MIN_DELAY)
                logger.warning(
                    f"Embedding 批次失败，{wait_seconds:.1f}s 后重试 "
                    f"({attempt + 1}/{max_retries}): {e}"
                )
                if wait_seconds > 0:
                    await asyncio.sleep(wait_seconds)

        raise RuntimeError(f"Embedding 批次重试失败: {last_error}") from last_error

    async def _repair_missing_vectors(
        self,
        memory_engine: Any,
        missing_ids: set[int],
        options: dict[str, Any],
        progress_callback=None,
    ) -> dict[str, Any]:
        import numpy as np

        faiss_db = getattr(memory_engine, "faiss_db", None)
        embedding_storage = getattr(faiss_db, "embedding_storage", None)
        provider = getattr(faiss_db, "embedding_provider", None)
        if embedding_storage is None or provider is None:
            raise RuntimeError("无法修复向量索引：Embedding 组件未初始化")

        total = len(missing_ids)
        processed = 0
        failed_ids: set[int] = set()
        batch_delay = float(options["batch_delay"])
        max_failure_ratio = float(options["max_failure_ratio"])

        async for batch in self._iter_document_batches(
            int(options["batch_size"]), missing_ids
        ):
            ids = [int(row[0]) for row in batch]
            contents = [row[2] or "" for row in batch]
            try:
                vectors = await self._embed_batch_with_retry(provider, contents, options)
                vectors_array = np.asarray(vectors, dtype=np.float32)
                if vectors_array.ndim != 2 or len(vectors_array) != len(ids):
                    raise ValueError(
                        f"Embedding 返回数量不匹配: 期望 {len(ids)}，实际 {len(vectors_array)}"
                    )
                await embedding_storage.insert_batch(vectors_array, ids)
                processed += len(ids)
            except Exception as e:
                failed_ids.update(ids)
                logger.error(f"向量补写批次失败 ids={ids[:3]}...: {e}", exc_info=True)

            if progress_callback:
                await progress_callback(
                    processed,
                    total,
                    f"向量补写已处理 {processed}/{total} 条",
                )

            if self._failure_ratio(len(failed_ids), total) > max_failure_ratio:
                break
            if batch_delay > 0:
                await asyncio.sleep(batch_delay)

        return {
            "mode": "repair",
            "processed": processed,
            "errors": len(failed_ids),
            "failed_ids": failed_ids,
            "switched": False,
            "partial": len(failed_ids) > 0,
        }

    async def _rebuild_vector_index_full(
        self,
        memory_engine: Any,
        total: int,
        options: dict[str, Any],
        progress_callback=None,
    ) -> dict[str, Any]:
        import faiss
        import numpy as np

        faiss_db = getattr(memory_engine, "faiss_db", None)
        embedding_storage = getattr(faiss_db, "embedding_storage", None)
        provider = getattr(faiss_db, "embedding_provider", None)
        if embedding_storage is None or provider is None:
            raise RuntimeError("无法重建向量索引：Embedding 组件未初始化")

        dimension = int(getattr(embedding_storage, "dimension", 0) or 0)
        if dimension <= 0:
            raise RuntimeError("无法重建向量索引：索引维度无效")

        temp_index = faiss.IndexIDMap(faiss.IndexFlatL2(dimension))
        processed = 0
        failed_ids: set[int] = set()
        batch_delay = float(options["batch_delay"])
        max_failure_ratio = float(options["max_failure_ratio"])

        async for batch in self._iter_document_batches(int(options["batch_size"])):
            ids = [int(row[0]) for row in batch]
            contents = [row[2] or "" for row in batch]
            try:
                vectors = await self._embed_batch_with_retry(provider, contents, options)
                vectors_array = np.asarray(vectors, dtype=np.float32)
                if vectors_array.ndim != 2 or len(vectors_array) != len(ids):
                    raise ValueError(
                        f"Embedding 返回数量不匹配: 期望 {len(ids)}，实际 {len(vectors_array)}"
                    )
                if vectors_array.shape[1] != dimension:
                    raise ValueError(
                        f"Embedding 维度不匹配: 期望 {dimension}，实际 {vectors_array.shape[1]}"
                    )
                temp_index.add_with_ids(vectors_array, np.asarray(ids, dtype=np.int64))
                processed += len(ids)
            except Exception as e:
                failed_ids.update(ids)
                logger.error(f"向量重建批次失败 ids={ids[:3]}...: {e}", exc_info=True)

            if progress_callback:
                await progress_callback(
                    processed,
                    total,
                    f"向量索引已处理 {processed}/{total} 条",
                )

            if self._failure_ratio(len(failed_ids), total) > max_failure_ratio:
                logger.error(
                    f"向量重建失败率过高: {len(failed_ids)}/{total}，不会切换新索引"
                )
                return {
                    "mode": "full",
                    "processed": processed,
                    "errors": len(failed_ids),
                    "failed_ids": failed_ids,
                    "switched": False,
                    "partial": True,
                }
            if batch_delay > 0:
                await asyncio.sleep(batch_delay)

        if total > 0 and processed == 0:
            return {
                "mode": "full",
                "processed": 0,
                "errors": max(total, len(failed_ids)),
                "failed_ids": failed_ids,
                "switched": False,
                "partial": True,
            }

        index_path = getattr(embedding_storage, "path", None)
        if index_path:
            temp_path = f"{index_path}.rebuild.tmp"
            try:
                faiss.write_index(temp_index, temp_path)
                os.replace(temp_path, index_path)
            finally:
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass

        embedding_storage.index = temp_index
        return {
            "mode": "full",
            "processed": processed,
            "errors": len(failed_ids),
            "failed_ids": failed_ids,
            "switched": True,
            "partial": len(failed_ids) > 0,
        }

    async def _rebuild_or_repair_vector_index(
        self,
        memory_engine: Any,
        total: int,
        options: dict[str, Any],
        progress_callback=None,
    ) -> dict[str, Any]:
        document_ids = await self._get_document_ids()
        if not document_ids:
            return {
                "mode": "skip",
                "processed": 0,
                "errors": 0,
                "failed_ids": set(),
                "switched": False,
                "partial": False,
            }

        vector_ids = self._get_vector_ids()
        vector_count = self._get_vector_count()
        if vector_ids is not None:
            missing_ids = document_ids - vector_ids
            if not missing_ids:
                return {
                    "mode": "skip",
                    "processed": 0,
                    "errors": 0,
                    "failed_ids": set(),
                    "switched": False,
                    "partial": False,
                }
            if vector_ids:
                logger.info(f"检测到 {len(missing_ids)} 条向量缺失，执行增量补写")
                return await self._repair_missing_vectors(
                    memory_engine, missing_ids, options, progress_callback
                )

        if vector_ids is None and vector_count >= total:
            logger.info("向量索引计数不小于 documents 数量，跳过全量向量重建")
            return {
                "mode": "skip",
                "processed": 0,
                "errors": 0,
                "failed_ids": set(),
                "switched": False,
                "partial": False,
            }

        logger.info("向量索引缺失或为空，执行安全全量重建")
        return await self._rebuild_vector_index_full(
            memory_engine, total, options, progress_callback
        )

    async def _update_migration_rebuild_status(
        self, completed_value: str = "true"
    ) -> None:
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
                        completed_value,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                await status_db.commit()
        except Exception as e:
            logger.warning(f"更新迁移状态失败: {e}")

    async def rebuild_indexes(
        self, memory_engine: Any, progress_callback=None
    ) -> dict[str, Any]:
        """
        分批安全重建索引

        安全策略：
        1. documents 表只读，始终作为原始数据源。
        2. BM25 直接按 documents 分批重建。
        3. 向量索引优先增量补缺；需要全量重建时先构建临时 FAISS 索引。
        4. 失败率超过阈值时不切换全量重建的新向量索引。

        Args:
            memory_engine: MemoryEngine实例
            progress_callback: 进度回调函数 (current, total, message)

        Returns:
            Dict: 重建结果
        """
        try:
            logger.info("开始分批安全重建索引。")
            options = self._get_rebuild_options(memory_engine)
            total = await self._get_document_count()

            if total <= 0:
                return {
                    "success": True,
                    "message": "没有需要重建的文档",
                    "processed": 0,
                    "errors": 0,
                    "total": 0,
                    "partial": False,
                    "switched": False,
                }

            logger.info(
                "重建参数: "
                f"total={total}, batch_size={options['batch_size']}, "
                f"embedding_batch_size={options['embedding_batch_size']}, "
                f"tasks_limit={options['tasks_limit']}, "
                f"request_delay={options['request_delay']}, "
                f"batch_delay={options['batch_delay']}, "
                f"max_failure_ratio={options['max_failure_ratio']}"
            )

            bm25_result = await self._rebuild_bm25_index(
                memory_engine, total, options, progress_callback
            )
            bm25_failed_ids = set(bm25_result["failed_ids"])
            if (
                self._failure_ratio(len(bm25_failed_ids), total)
                > float(options["max_failure_ratio"])
            ):
                message = (
                    f"BM25 重建失败率过高: {len(bm25_failed_ids)}/{total}。"
                    "documents 原始数据未被删除，已停止向量重建。"
                )
                logger.error(message)
                return {
                    "success": False,
                    "message": message,
                    "processed": total - len(bm25_failed_ids),
                    "errors": len(bm25_failed_ids),
                    "total": total,
                    "partial": True,
                    "switched": False,
                    "bm25_processed": bm25_result["processed"],
                    "bm25_errors": bm25_result["errors"],
                    "vector_processed": 0,
                    "vector_errors": 0,
                    "failure_ratio": self._failure_ratio(len(bm25_failed_ids), total),
                }

            vector_result = await self._rebuild_or_repair_vector_index(
                memory_engine, total, options, progress_callback
            )
            vector_failed_ids = set(vector_result["failed_ids"])
            failed_ids = bm25_failed_ids | vector_failed_ids
            failure_ratio = self._failure_ratio(len(failed_ids), total)
            accepted = failure_ratio <= float(options["max_failure_ratio"])
            partial = bool(failed_ids)

            if accepted:
                await self._update_migration_rebuild_status(
                    "partial" if partial else "true"
                )
                message = (
                    "索引重建完成"
                    if not partial
                    else (
                        "索引已按失败率阈值完成可接受切换，"
                        f"仍有 {len(failed_ids)} 条需后续重试"
                    )
                )
            else:
                message = (
                    f"索引重建失败率过高: {len(failed_ids)}/{total}。"
                    "全量向量重建未切换新索引，documents 原始数据未被删除。"
                )

            logger.info(
                "索引重建结果: "
                f"accepted={accepted}, partial={partial}, "
                f"bm25={bm25_result['processed']}/{total}, "
                f"vector={vector_result['processed']}/{total}, "
                f"errors={len(failed_ids)}, vector_mode={vector_result['mode']}"
            )

            return {
                "success": accepted,
                "message": message,
                "processed": max(0, total - len(failed_ids)),
                "errors": len(failed_ids),
                "total": total,
                "partial": partial,
                "switched": bool(vector_result["switched"]),
                "bm25_processed": bm25_result["processed"],
                "bm25_errors": bm25_result["errors"],
                "vector_processed": vector_result["processed"],
                "vector_errors": vector_result["errors"],
                "vector_mode": vector_result["mode"],
                "failure_ratio": failure_ratio,
            }

        except Exception as e:
            logger.error(f"重建索引失败: {e}", exc_info=True)
            return {
                "success": False,
                "message": (
                    f"重建索引失败: {str(e)}。documents 原始数据未被删除，"
                    "请查看日志后重试 /lmem rebuild-index。"
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
