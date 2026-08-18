"""
IndexValidator 的 IndexValidatorRebuildMixin 拆分模块
自动从 core/validators/index_validator.py 拆分，保持行为不变
"""

import aiosqlite
from typing import Any, cast
import asyncio
import json
from astrbot.api import logger
import os
import time


class IndexValidatorRebuildMixin:
    """IndexValidator 拆分模块：IndexValidatorRebuildMixin"""
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

    async def _create_bm25_shadow(self, table_name: str) -> str:
        """Create a fresh FTS5 table without touching the live index."""
        shadow_table = f"{table_name}_rebuild"
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA busy_timeout = 10000")
            await db.execute(f"DROP TABLE IF EXISTS {shadow_table}")
            await db.execute(
                f"""
                CREATE VIRTUAL TABLE {shadow_table}
                USING fts5(
                    content,
                    doc_id UNINDEXED,
                    tokenize='unicode61'
                )
                """
            )
            await db.commit()
        return shadow_table

    async def _drop_bm25_shadow(self, shadow_table: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA busy_timeout = 10000")
            await db.execute(f"DROP TABLE IF EXISTS {shadow_table}")
            await db.commit()

    async def _switch_bm25_shadow(self, live_table: str, shadow_table: str) -> None:
        """Atomically replace the live FTS table after a complete build."""
        old_table = f"{live_table}_previous"
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA busy_timeout = 10000")
            await db.execute("BEGIN IMMEDIATE")
            try:
                await db.execute(f"DROP TABLE IF EXISTS {old_table}")
                await db.execute(f"ALTER TABLE {live_table} RENAME TO {old_table}")
                await db.execute(f"ALTER TABLE {shadow_table} RENAME TO {live_table}")
                await db.commit()
            except Exception:
                await db.rollback()
                raise

        # Keep the switch transaction short. The previous generation can be
        # dropped after readers have observed the new schema.
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA busy_timeout = 10000")
            await db.execute(f"DROP TABLE IF EXISTS {old_table}")
            await db.commit()

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
            cursor = await db.execute(
                f"SELECT COUNT(*) FROM documents WHERE {self.ACTIVE_DOCUMENT_SQL}"
            )
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

    async def _get_document_ids(self) -> set[int]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                f"SELECT id FROM documents WHERE {self.ACTIVE_DOCUMENT_SQL}"
            )
            return {int(row[0]) for row in await cursor.fetchall()}

    async def _iter_document_batches(
        self,
        batch_size: int,
        document_ids: set[int] | None = None,
    ):
        # 复用单个连接遍历所有批次，避免每批重建连接带来的开销。
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA busy_timeout = 10000")

            if document_ids is not None:
                sorted_ids = sorted(int(doc_id) for doc_id in document_ids)
                for start in range(0, len(sorted_ids), batch_size):
                    chunk = sorted_ids[start : start + batch_size]
                    placeholders = ",".join("?" for _ in chunk)
                    cursor = await db.execute(
                        f"""
                        SELECT id, doc_id, text, metadata
                        FROM documents
                        WHERE id IN ({placeholders})
                          AND {self.ACTIVE_DOCUMENT_SQL}
                        ORDER BY id
                        """,
                        chunk,
                    )
                    yield await cursor.fetchall()
                return

            last_id = 0
            while True:
                cursor = await db.execute(
                    f"""
                    SELECT id, doc_id, text, metadata
                    FROM documents
                    WHERE id > ?
                      AND {self.ACTIVE_DOCUMENT_SQL}
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

    @staticmethod
    def _get_ids_from_index(index: Any) -> set[int] | None:
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

        shadow_table = await self._create_bm25_shadow(table_name)
        processed = 0
        failed_ids: set[int] = set()
        switched = False

        insert_db = await aiosqlite.connect(self.db_path)
        await insert_db.execute("PRAGMA busy_timeout = 10000")
        try:
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
                        await insert_db.executemany(
                            f"INSERT INTO {shadow_table}(doc_id, content) VALUES (?, ?)",
                            rows_to_insert,
                        )
                        await insert_db.commit()
                        processed += len(rows_to_insert)
                    except Exception as batch_error:
                        logger.warning(
                            f"BM25 shadow 批量写入失败，将逐条重试: {batch_error}"
                        )
                        try:
                            await insert_db.rollback()
                        except Exception:
                            pass
                        for row_doc_id, processed_content in rows_to_insert:
                            try:
                                await insert_db.execute(
                                    f"INSERT INTO {shadow_table}(doc_id, content) VALUES (?, ?)",
                                    (row_doc_id, processed_content),
                                )
                                await insert_db.commit()
                                processed += 1
                            except Exception as e:
                                try:
                                    await insert_db.rollback()
                                except Exception:
                                    pass
                                failed_ids.add(int(row_doc_id))
                                logger.error(
                                    f"BM25 shadow 写入失败 doc_id={row_doc_id}: {e}"
                                )

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

            if self._failure_ratio(len(failed_ids), total) <= max_failure_ratio:
                await self._switch_bm25_shadow(table_name, shadow_table)
                switched = True
        finally:
            await insert_db.close()
            if not switched:
                await self._drop_bm25_shadow(shadow_table)

        return {
            "processed": processed,
            "errors": len(failed_ids),
            "failed_ids": failed_ids,
            "switched": switched,
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
        tasks_limit = max(1, int(options.get("tasks_limit", 1)))
        vectors: list[Any] = []
        chunks = [
            contents[start : start + embedding_batch_size]
            for start in range(0, len(contents), embedding_batch_size)
        ]

        for wave_start in range(0, len(chunks), tasks_limit):
            wave = chunks[wave_start : wave_start + tasks_limit]
            logger.debug(
                "Embedding 并发窗口: "
                f"offset={wave_start}, requests={len(wave)}, total_requests={len(chunks)}"
            )
            wave_results = await asyncio.gather(
                *(
                    self._embed_request_with_retry(
                        provider,
                        chunk,
                        max_retries=max_retries,
                        retry_base_delay=retry_base_delay,
                    )
                    for chunk in wave
                )
            )
            for result in wave_results:
                vectors.extend(result)
            if request_delay > 0 and wave_start + tasks_limit < len(chunks):
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
        batch_index = 0

        async for batch in self._iter_document_batches(
            int(options["batch_size"]), missing_ids
        ):
            batch_index += 1
            ids = [int(row[0]) for row in batch]
            contents = [row[2] or "" for row in batch]
            logger.info(
                "向量补写批次开始: "
                f"batch={batch_index}, size={len(ids)}, "
                f"id_range={ids[0]}-{ids[-1]}, processed={processed}/{total}, "
                f"failed={len(failed_ids)}"
            )
            try:
                vectors = await self._embed_batch_with_retry(
                    provider, contents, options
                )
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

            logger.info(
                "向量补写进度: "
                f"processed={processed}/{total}, failed={len(failed_ids)}, "
                f"failure_ratio={self._failure_ratio(len(failed_ids), total):.2%}"
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

        index_path = getattr(embedding_storage, "path", None)
        temp_path = f"{index_path}.rebuild.tmp" if index_path else None
        metadata_path = f"{temp_path}.json" if temp_path else None
        provider_fingerprint = self.get_provider_fingerprint()
        active_document_ids = await self._get_document_ids()

        temp_index = None
        if temp_path and metadata_path and os.path.exists(temp_path):
            try:
                with open(metadata_path, encoding="utf-8") as metadata_file:
                    checkpoint_metadata = json.load(metadata_file)
                if (
                    checkpoint_metadata.get("provider_fingerprint")
                    == provider_fingerprint
                    and int(checkpoint_metadata.get("dimension", 0)) == dimension
                ):
                    candidate = await asyncio.to_thread(faiss.read_index, temp_path)
                    if int(getattr(candidate, "d", 0)) == dimension:
                        temp_index = candidate
                        logger.info(f"恢复向量重建检查点: vectors={candidate.ntotal}")
            except Exception as e:
                logger.warning(f"忽略无法恢复的向量重建检查点: {e}")

        if temp_index is None:
            temp_index = faiss.IndexIDMap(faiss.IndexFlatL2(dimension))
            for stale_path in (temp_path, metadata_path):
                if stale_path and os.path.exists(stale_path):
                    try:
                        os.remove(stale_path)
                    except OSError:
                        pass

        checkpoint_ids = self._get_ids_from_index(temp_index) or set()
        checkpoint_ids &= active_document_ids
        remaining_ids = active_document_ids - checkpoint_ids
        processed = len(checkpoint_ids)
        failed_ids: set[int] = set()
        batch_delay = float(options["batch_delay"])
        max_failure_ratio = float(options["max_failure_ratio"])
        batch_index = 0

        async def persist_checkpoint() -> None:
            if not temp_path or not metadata_path:
                return
            writing_path = f"{temp_path}.writing"
            await asyncio.to_thread(faiss.write_index, temp_index, writing_path)
            os.replace(writing_path, temp_path)
            checkpoint_metadata = {
                "provider_fingerprint": provider_fingerprint,
                "dimension": dimension,
                "processed": int(temp_index.ntotal),
                "updated_at": time.time(),
            }
            metadata_writing_path = f"{metadata_path}.writing"
            with open(metadata_writing_path, "w", encoding="utf-8") as metadata_file:
                json.dump(checkpoint_metadata, metadata_file, ensure_ascii=True)
            os.replace(metadata_writing_path, metadata_path)

        try:
            async for batch in self._iter_document_batches(
                int(options["batch_size"]), remaining_ids
            ):
                batch_index += 1
                ids = [int(row[0]) for row in batch]
                contents = [row[2] or "" for row in batch]
                logger.info(
                    "向量重建批次开始: "
                    f"batch={batch_index}, size={len(ids)}, "
                    f"id_range={ids[0]}-{ids[-1]}, processed={processed}/{total}, "
                    f"failed={len(failed_ids)}"
                )
                try:
                    vectors = await self._embed_batch_with_retry(
                        provider, contents, options
                    )
                    vectors_array = np.asarray(vectors, dtype=np.float32)
                    if vectors_array.ndim != 2 or len(vectors_array) != len(ids):
                        raise ValueError(
                            f"Embedding 返回数量不匹配: 期望 {len(ids)}，实际 {len(vectors_array)}"
                        )
                    if vectors_array.shape[1] != dimension:
                        raise ValueError(
                            f"Embedding 维度不匹配: 期望 {dimension}，实际 {vectors_array.shape[1]}"
                        )
                    temp_index.add_with_ids(
                        vectors_array, np.asarray(ids, dtype=np.int64)
                    )
                    processed += len(ids)
                except Exception as e:
                    failed_ids.update(ids)
                    logger.error(
                        f"向量重建批次失败 ids={ids[:3]}...: {e}", exc_info=True
                    )

                if progress_callback:
                    await progress_callback(
                        processed,
                        total,
                        f"向量索引已处理 {processed}/{total} 条",
                    )

                logger.info(
                    "向量重建进度: "
                    f"processed={processed}/{total}, failed={len(failed_ids)}, "
                    f"failure_ratio={self._failure_ratio(len(failed_ids), total):.2%}"
                )

                if batch_index % 10 == 0:
                    await persist_checkpoint()

                if self._failure_ratio(len(failed_ids), total) > max_failure_ratio:
                    logger.error(
                        f"向量重建失败率过高: {len(failed_ids)}/{total}，不会切换新索引"
                    )
                    await persist_checkpoint()
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
        except asyncio.CancelledError:
            await asyncio.shield(persist_checkpoint())
            raise

        if total > 0 and processed == 0:
            return {
                "mode": "full",
                "processed": 0,
                "errors": max(total, len(failed_ids)),
                "failed_ids": failed_ids,
                "switched": False,
                "partial": True,
            }

        if index_path:
            await persist_checkpoint()
            if temp_path:
                os.replace(temp_path, index_path)
            if metadata_path and os.path.exists(metadata_path):
                os.remove(metadata_path)

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
        force_full: bool = False,
    ) -> dict[str, Any]:
        if force_full:
            logger.info("Embedding Provider 指纹已变化，执行安全全量向量重建")
            return await self._rebuild_vector_index_full(
                memory_engine, total, options, progress_callback
            )

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
        self,
        memory_engine: Any,
        progress_callback=None,
        *,
        force_full_vector: bool = False,
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
        async with self._maintenance_lock:
            return await self._rebuild_indexes_locked(
                memory_engine,
                progress_callback=progress_callback,
                force_full_vector=force_full_vector,
            )

    async def _rebuild_indexes_locked(
        self,
        memory_engine: Any,
        progress_callback=None,
        *,
        force_full_vector: bool = False,
    ) -> dict[str, Any]:
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
            if self._failure_ratio(len(bm25_failed_ids), total) > float(
                options["max_failure_ratio"]
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
                memory_engine,
                total,
                options,
                progress_callback,
                force_full=force_full_vector,
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
                if not partial:
                    await self.record_provider_fingerprint()
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
