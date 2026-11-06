"""批量维护、衰减与访问统计。"""

import asyncio
import json
import time
from collections import OrderedDict
from typing import Any

import aiosqlite

from astrbot.api import logger

from ...storage.atom_store import AtomStore
from ..memory_scope import (
    adapter_sql,
    current_adapter_access,
)
from ..processors.atom_classifier import classify_atoms
from ..retrieval.hybrid_retriever import HybridResult
from ..retrieval.vector_search import retrieve_documents
from ..utils.json_utils import safe_json_dict
from ..utils.number_utils import clamp_float, safe_float
from .agentic_recall_types import AgenticRecallOutcome as AgenticRecallOutcome


class MemoryEngineBatchMixin:
    """MemoryEngine 的 MemoryEngineBatchMixin 分组。

    宿主共享状态由 ``MemoryEngine`` 独占持有，本模块只通过 self 访问。
    """

    # 访问统计合并窗口：窗口内的多次召回合并为一次 UPDATE
    _ACCESS_UPDATE_DEBOUNCE_SECONDS = 3.0

    # 宿主共享状态契约（由 MemoryEngine.__init__ / initialize 赋值）
    db_connection: aiosqlite.Connection | None
    config: dict[str, Any]
    db_path: str
    faiss_db: Any
    atom_enabled: bool
    atom_store: AtomStore | None
    atom_retriever: Any | None
    vector_retriever: Any | None
    bm25_retriever: Any | None
    hybrid_retriever: Any | None
    index_writer: Any | None
    dual_route_retriever: Any | None
    graph_memory_manager: Any | None
    graph_store: Any | None
    _search_cache_enabled: bool
    _search_cache_ttl: float
    _search_cache_max_size: int
    _search_cache_generation: int
    _search_cache: OrderedDict[tuple[Any, ...], tuple[float, list[HybridResult]]]
    _access_update_pending: set[int]
    _access_atom_update_pending: set[int]
    _access_flush_task: asyncio.Task | None
    index_maintenance_status: dict[str, Any]

    async def batch_delete_memories(self, memory_ids: list[int]) -> int:
        """Batch delete multiple memories using bulk SQL operations."""
        if not memory_ids:
            return 0

        if self.db_connection is None:
            logger.error("[批量删除] 数据库连接未初始化")
            return 0

        if current_adapter_access.get() is not None:
            ownership, ownership_params = adapter_sql("metadata")
            slots = ",".join("?" for _ in memory_ids)
            cursor = await self.db_connection.execute(
                f"SELECT id FROM documents WHERE id IN ({slots}) AND {ownership}",
                (*memory_ids, *ownership_params),
            )
            memory_ids = [int(row[0]) for row in await cursor.fetchall()]
        self._invalidate_search_cache()
        total_deleted = 0
        sql_batch_size = 200

        for i in range(0, len(memory_ids), sql_batch_size):
            batch = memory_ids[i : i + sql_batch_size]
            placeholders = ",".join("?" * len(batch))
            batch_deleted = 0

            try:
                # 1. Batch delete from BM25 FTS
                async with self._write_transaction() as connection:
                    await connection.execute(
                        f"DELETE FROM livingmemory_memories_fts WHERE doc_id IN ({placeholders})",
                        batch,
                    )
                # 2. Look up UUIDs and delete from FAISS vector DB
                cursor = await self.db_connection.execute(
                    f"SELECT id, doc_id FROM documents WHERE id IN ({placeholders})",
                    batch,
                )
                uuid_rows = await cursor.fetchall()
                found_ids = [int(row["id"]) for row in uuid_rows]
                if found_ids:
                    deleted_vector_ids = await self.vector_retriever.delete_documents(
                        found_ids
                    )
                    if set(deleted_vector_ids) != set(found_ids):
                        raise RuntimeError(
                            "批量向量删除不完整: "
                            f"expected={found_ids}, deleted={deleted_vector_ids}"
                        )
                # 3. Batch delete from documents table
                async with self._write_transaction() as connection:
                    await connection.execute(
                        f"DELETE FROM documents WHERE id IN ({placeholders})",
                        batch,
                    )
                    await connection.execute(
                        f"DELETE FROM memory_sources WHERE memory_id IN ({placeholders})",
                        batch,
                    )
                # FaissVecDB 与本引擎共享 documents 表；向量删除可能已移除
                # 对应行，因此以删除前查到的记录数作为准确结果。
                batch_deleted = len(found_ids)
                # 4. Batch delete graph artifacts and atoms
                await self._delete_graph_and_atoms_for_batch(batch)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error(
                    f"[批量删除] 批次删除失败 (offset={i}, size={len(batch)})",
                    exc_info=True,
                )
                raise

            total_deleted += batch_deleted

        if total_deleted:
            logger.info(f"[批量删除] 共删除 {total_deleted} 条记忆")
        return total_deleted

    async def cleanup_old_memories(
        self,
        days_threshold: int | None = None,
        importance_threshold: float | None = None,
    ) -> int:
        """
        清理旧记忆（使用分批处理避免内存问题）

        删除超过阈值且重要性低的记忆

        Args:
            days_threshold: 天数阈值,默认从配置读取
            importance_threshold: 重要性阈值,默认从配置读取

        Returns:
            int: 删除的记忆数量
        """
        # 使用配置或参数值
        days = (
            self.config.get("cleanup_days_threshold", 30)
            if days_threshold is None
            else days_threshold
        )
        importance = (
            self.config.get("cleanup_importance_threshold", 0.3)
            if importance_threshold is None
            else importance_threshold
        )
        ownership, ownership_params = adapter_sql("metadata")
        try:
            days = int(days)
            importance = float(importance)
        except (TypeError, ValueError):
            logger.error(
                f"清理参数格式错误: days_threshold={days}, importance_threshold={importance}"
            )
            return 0

        if days < 0:
            logger.error(f"清理参数无效: days_threshold={days}（必须 >= 0）")
            return 0

        cutoff_time = time.time() - (days * 86400)

        try:
            if self.db_connection is None:
                return 0

            batch_size = 500
            last_id = 0
            candidates: list[int] = []

            # 使用主键 keyset 分页流式读取，避免 OFFSET 分页的 O(N²) 开销。
            while True:
                cursor = await self.db_connection.execute(
                    f"SELECT id, metadata FROM documents WHERE id > ? AND {ownership} ORDER BY id LIMIT ?",
                    (last_id, *ownership_params, batch_size),
                )
                rows = await cursor.fetchall()
                if not rows:
                    break
                last_id = int(rows[-1]["id"])

                raw_metadata = [r["metadata"] for r in rows]
                parsed = await asyncio.to_thread(
                    lambda: [safe_json_dict(m) for m in raw_metadata]
                )

                for row, metadata in zip(rows, parsed):
                    if str(metadata.get("status") or "active") != "active":
                        continue
                    create_time = safe_float(metadata.get("create_time"), time.time())
                    doc_importance = clamp_float(
                        metadata.get("importance"), default=0.5
                    )
                    if create_time < cutoff_time and doc_importance < importance:
                        candidates.append(int(row["id"]))

            if not candidates:
                return 0
            if self.config.get("auto_archived_enabled", False):
                logger.info(f"[清理] 发现 {len(candidates)} 条候选记忆，开始归档")
                return await self.archive_memories(candidates)

            logger.info(f"[清理] 发现 {len(candidates)} 条候选记忆，开始批量删除")
            deleted_count = await self.batch_delete_memories(candidates)
            logger.info(f"[清理] 完成，已删除 {deleted_count} 条旧记忆")
            return deleted_count
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("[清理] 清理旧记忆失败", exc_info=True)
            return 0

    async def archive_memories(
        self, memory_ids: list[int], *, audit: dict[str, Any] | None = None
    ) -> int:
        """Remove memories from retrieval indexes while retaining their documents.

        Args:
            memory_ids: 要归档（软删除）的记忆 ID 列表。
            audit: 可选审计字段，合并进每条被归档文档的 metadata（#279）；
                不允许覆盖 ``status`` / ``archived_at``。

        Returns:
            int: 实际归档的记忆条数。
        """
        if not memory_ids or self.db_connection is None:
            return 0

        # 审计字段由调用方提供，但不能覆盖归档状态字段
        audit_metadata = {
            key: value
            for key, value in (audit or {}).items()
            if key not in {"status", "archived_at"}
        }

        unique_ids = list(dict.fromkeys(int(memory_id) for memory_id in memory_ids))
        documents = await retrieve_documents(
            self.faiss_db,
            metadata_filters={},
            ids=unique_ids,
            offset=0,
            limit=len(unique_ids),
        )
        active_documents = []
        metadata_updates: list[tuple[str, int]] = []
        archived_at = time.time()
        for document in documents:
            metadata = safe_json_dict(document.get("metadata"))
            if str(metadata.get("status") or "active") == "archived":
                continue
            metadata.update(audit_metadata)
            metadata["status"] = "archived"
            metadata["archived_at"] = archived_at
            active_documents.append(document)
            metadata_updates.append(
                (json.dumps(metadata, ensure_ascii=False), int(document["id"]))
            )
        if not active_documents:
            return 0

        archived_ids = [int(document["id"]) for document in active_documents]
        placeholders = ",".join("?" * len(archived_ids))
        async with self._write_transaction() as connection:
            await connection.executemany(
                "UPDATE documents SET metadata = ? WHERE id = ?",
                metadata_updates,
            )
            await connection.execute(
                f"DELETE FROM livingmemory_memories_fts WHERE doc_id IN ({placeholders})",
                archived_ids,
            )

        embedding_storage = getattr(self.faiss_db, "embedding_storage", None)
        embedding_delete = getattr(embedding_storage, "delete", None)
        if callable(embedding_delete):
            await embedding_delete(archived_ids)
        else:
            logger.warning(
                "[归档] 当前 AstrBot 不支持独立删除向量，已保留软归档状态，"
                "并标记启动时重建向量索引以清除残留向量"
            )
            # 无法单独删向量时，让启动时的索引校验触发重建，避免软归档
            # 的向量长期占用召回候选名额。
            async with self._write_transaction() as connection:
                await connection.execute(
                    "INSERT OR REPLACE INTO migration_status (key, value, updated_at) "
                    "VALUES (?, ?, datetime('now'))",
                    ("needs_index_rebuild", "true"),
                )

        await self._delete_graph_and_atoms_for_batch(archived_ids)
        self._invalidate_search_cache()
        logger.info(f"[归档] 已归档 {len(archived_ids)} 条记忆")
        return len(archived_ids)

    async def restore_memory(self, memory_id: int) -> bool:
        """Restore one archived document and rebuild every retrieval index."""
        if self.db_connection is None:
            return False
        memory = await self.get_memory(memory_id)
        if not memory:
            return False
        metadata = safe_json_dict(memory.get("metadata"))
        if str(metadata.get("status") or "active") != "archived":
            return True

        embedding_storage = getattr(self.faiss_db, "embedding_storage", None)
        embedding_insert = getattr(embedding_storage, "insert", None)
        embedding_delete = getattr(embedding_storage, "delete", None)
        provider = getattr(self.faiss_db, "embedding_provider", None)
        get_embedding = getattr(provider, "get_embedding_with_retry", None)
        if not callable(get_embedding):
            get_embedding = getattr(provider, "get_embedding", None)
        if not callable(embedding_insert) or not callable(get_embedding):
            raise RuntimeError("当前 AstrBot 不支持恢复已归档向量")

        import numpy as np

        content = str(memory.get("text") or "")
        vector_inserted = False
        fts_inserted = False
        metadata["status"] = "active"
        metadata["restored_at"] = time.time()
        metadata.pop("archived_at", None)
        try:
            vector = np.asarray(await get_embedding(content), dtype=np.float32)
            await embedding_insert(vector, memory_id)
            vector_inserted = True
            if self.bm25_retriever is None:
                raise RuntimeError("BM25 检索器未初始化")
            await self.bm25_retriever.add_document(memory_id, content, metadata)
            fts_inserted = True

            atoms = classify_atoms(
                key_facts=list(metadata.get("key_facts") or []),
                topics=list(metadata.get("topics") or []),
                participants=list(metadata.get("participants") or []),
                parent_importance=clamp_float(metadata.get("importance"), default=0.5),
                session_id=metadata.get("session_id"),
                persona_id=metadata.get("persona_id"),
            )
            for atom in atoms:
                atom.metadata["adapter_id"] = metadata.get("adapter_id")
                atom.parent_memory_id = memory_id
            if atoms and self.atom_store is not None:
                await self.atom_store.insert_many(atoms)
            if self.graph_memory_manager is not None:
                await self.graph_memory_manager.index_memory(
                    memory_id, content, metadata, atoms
                )

            async with self._write_transaction() as connection:
                await connection.execute(
                    "UPDATE documents SET metadata = ? WHERE id = ?",
                    (json.dumps(metadata, ensure_ascii=False), memory_id),
                )
            self._invalidate_search_cache()
            return True
        except Exception:
            if fts_inserted:
                async with self._write_transaction() as connection:
                    await connection.execute(
                        "DELETE FROM livingmemory_memories_fts WHERE doc_id = ?",
                        (memory_id,),
                    )
            if vector_inserted and callable(embedding_delete):
                await embedding_delete([memory_id])
            if self.graph_memory_manager is not None:
                await self.graph_memory_manager.delete_memory(memory_id)
            if self.atom_store is not None:
                await self.atom_store.delete_by_parent(memory_id)
            raise

    async def _migrate_session_data_if_needed(self, unified_msg_origin: str) -> None:
        """
        运行时自动迁移：将旧格式的session_id更新为unified_msg_origin格式

        支持各种平台的旧格式（通用匹配策略）：
        - WebChat UUID: "ac8c2cef-959e-4146-ad22-c82d0230ad06"
        - WebChat带前缀: "webchat!astrbot!ac8c2cef-959e-4146-ad22-c82d0230ad06"
        - QQ号: "123456789"
        - 其他平台: 任意字符串

        目标格式: "platform:message_type:session_id"

        策略：
        1. 从unified_msg_origin解析出：platform、message_type、session_id
        2. 生成所有可能的旧格式匹配候选（递归拆分）
        3. 查找匹配任一候选且不含冒号的旧记录
        4. 批量更新为unified_msg_origin
        5. 使用unified_msg_origin本身作为迁移标记（避免重复）

        Args:
            unified_msg_origin: 完整的统一消息来源（格式：platform:type:session_id）
        """

        try:
            # 1. 解析 unified_msg_origin
            parts = unified_msg_origin.split(":", 2)
            if len(parts) != 3:
                logger.warning(
                    f"[自动迁移] unified_msg_origin 格式不正确: {unified_msg_origin}"
                )
                return

            platform_id, message_type, full_session_id = parts

            # 2. 生成所有可能的旧格式匹配候选
            # 对于 "webchat!astrbot!ac8c2cef-..." 会生成:
            #   ["webchat!astrbot!ac8c2cef-...", "astrbot!ac8c2cef-...", "ac8c2cef-..."]
            # 对于 "123456789" 会生成: ["123456789"]
            candidates = [full_session_id]

            # 按感叹号递归拆分
            if "!" in full_session_id:
                parts_by_bang = full_session_id.split("!")
                for i in range(1, len(parts_by_bang)):
                    candidates.append("!".join(parts_by_bang[i:]))

            logger.info(f"[自动迁移] 开始检查会话，候选匹配: {candidates}")

            # 3. 检查是否已迁移（使用unified_msg_origin本身作为标记）
            migration_key = f"migrated_umo_{unified_msg_origin}"
            if self.db_connection is None:
                return
            cursor = await self.db_connection.execute(
                "SELECT value FROM migration_status WHERE key = ?", (migration_key,)
            )
            row = await cursor.fetchone()
            if row and row[0] == "true":
                # 已迁移过，跳过
                return

            # 4. 查找所有需要迁移的记录
            # 条件：session_id 匹配任一候选 且 不包含冒号（旧格式标识）
            placeholders = " OR ".join(
                ["json_extract(metadata, '$.session_id') = ?" for _ in candidates]
            )
            query = f"""
                SELECT id, metadata FROM documents
                WHERE ({placeholders})
                AND json_extract(metadata, '$.session_id') NOT LIKE '%:%'
            """

            cursor = await self.db_connection.execute(query, tuple(candidates))
            rows = list(await cursor.fetchall())

            if not rows:
                logger.info("[自动迁移] 未找到需要迁移的旧数据")
                # 即使没有旧数据也标记为已检查，避免重复查询
                async with self._write_transaction() as connection:
                    await connection.execute(
                        "INSERT OR REPLACE INTO migration_status (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                        (migration_key, "true"),
                    )
                return

            logger.info(f"[自动迁移] 找到 {len(rows)} 条旧数据需要迁移")

            # 5. 批量更新
            updates: list[tuple[str, int]] = []
            for row in rows:
                doc_id = row[0]
                metadata_str = row[1]

                try:
                    metadata = json.loads(metadata_str) if metadata_str else {}
                except (json.JSONDecodeError, TypeError):
                    metadata = {}

                old_session_id = metadata.get("session_id", "unknown")

                # 更新为unified_msg_origin格式
                metadata["session_id"] = unified_msg_origin
                metadata["migrated_at"] = time.time()
                metadata["old_session_id"] = old_session_id  # 保留旧值便于追溯

                updates.append((json.dumps(metadata, ensure_ascii=False), doc_id))

            updated_count = len(updates)
            async with self._write_transaction() as connection:
                if updates:
                    await connection.executemany(
                        "UPDATE documents SET metadata = ? WHERE id = ?",
                        updates,
                    )
                await connection.execute(
                    "INSERT OR REPLACE INTO migration_status (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                    (migration_key, "true"),
                )

            logger.info(
                f"[自动迁移] 完成！已更新 {updated_count} 条记录 -> {unified_msg_origin}"
            )

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[自动迁移] 迁移失败: {e}", exc_info=True)

    async def rebuild_graph_index(self) -> dict[str, int]:
        """Stream active documents into a safe graph-memory rebuild."""
        if self.graph_memory_manager is None:
            return {"rebuilt": 0, "skipped": 0}

        if self.db_connection is None:
            raise RuntimeError("memory database is not initialized")

        async def active_memory_batches():
            last_id = 0
            batch_size = 200
            while True:
                cursor = await self.db_connection.execute(
                    """
                    SELECT id, text, metadata
                    FROM documents
                    WHERE id > ?
                      AND COALESCE(
                          json_extract(metadata, '$.status'), 'active'
                      ) = 'active'
                    ORDER BY id
                    LIMIT ?
                    """,
                    (last_id, batch_size),
                )
                rows = await cursor.fetchall()
                if not rows:
                    break

                batch: list[tuple[int, str, dict[str, Any]]] = []
                for row in rows:
                    metadata = row["metadata"] or {}
                    if isinstance(metadata, str):
                        try:
                            metadata = json.loads(metadata)
                        except (json.JSONDecodeError, TypeError):
                            metadata = {}
                    elif not isinstance(metadata, dict):
                        metadata = {}
                    batch.append((int(row["id"]), str(row["text"] or ""), metadata))

                last_id = int(rows[-1]["id"])
                yield batch

        self._invalidate_search_cache()
        return await self.graph_memory_manager.rebuild_memory_batches(
            active_memory_batches()
        )

    async def apply_daily_decay(self, decay_rate: float, days: int = 1) -> int:
        """
        批量应用重要性衰减

        Args:
            decay_rate: 每日衰减率 (0-1)
            days: 衰减天数（用于补偿错过的天数）

        Returns:
            int: 受影响的记忆数量；数据库失败时异常交由调度器处理。
        """
        if decay_rate <= 0 or days <= 0:
            return 0

        try:
            access_window_days = float(
                self.config.get("access_decay_window_days", 30.0)
            )
            max_access_count = float(self.config.get("access_decay_max_count", 10.0))
            access_decay_multiplier = float(
                self.config.get("access_count_decay_multiplier", 0.5)
            )
            protected_threshold = clamp_float(
                self.config.get("protected_importance_threshold"), default=1.0
            )
            access_window_start = time.time() - max(1.0, access_window_days) * 86400.0
            cursor = await self.db_connection.execute(
                "SELECT id, metadata FROM documents WHERE json_extract(metadata, '$.importance') IS NOT NULL OR metadata LIKE '%\"importance\"%'"
            )
            rows = await cursor.fetchall()

            def _compute_updates() -> list[tuple[float, int]]:
                updates: list[tuple[float, int]] = []
                for row in rows:
                    metadata = safe_json_dict(row["metadata"])
                    importance = clamp_float(metadata.get("importance"), default=0.5)
                    if importance >= protected_threshold:
                        continue
                    access_count = safe_float(metadata.get("access_count"), 0.0)
                    last_access_time = safe_float(metadata.get("last_access_time"), 0.0)

                    recent_access_factor = (
                        1.0 if last_access_time >= access_window_start else 0.5
                    )
                    access_factor = min(1.0, access_count / max(1.0, max_access_count))
                    effective_decay_rate = decay_rate * (
                        1 - 0.5 * access_factor * recent_access_factor
                    )
                    decay_factor = (1 - effective_decay_rate) ** days
                    updates.append((decay_factor, int(row["id"])))
                return updates

            # 卸载逐行 JSON 解析与数值计算，避免阻塞事件循环。
            updates = await asyncio.to_thread(_compute_updates)

            if not updates:
                return 0

            # 只改 importance / access_count 两个键，且新值在 SQL 里基于当前值计算：
            # 并发召回写入的 last_access_time（以及期间新增的 access_count）不会被
            # 旧快照整列回写覆盖。
            async with self._write_transaction() as connection:
                await connection.executemany(
                    """
                    UPDATE documents
                    SET metadata = json_set(
                        json_set(
                            metadata,
                            '$.importance',
                            MAX(
                                0.01,
                                ROUND(
                                    CAST(json_extract(metadata, '$.importance') AS REAL) * ?,
                                    4
                                )
                            )
                        ),
                        '$.access_count',
                        CAST(
                            COALESCE(
                                CAST(json_extract(metadata, '$.access_count') AS REAL),
                                0
                            ) * ?
                            AS INTEGER
                        )
                    )
                    WHERE id = ?
                    """,
                    [
                        (factor, access_decay_multiplier, doc_id)
                        for factor, doc_id in updates
                    ],
                )
            affected = len(updates)

            logger.info(
                f"[衰减] 批量衰减完成: 衰减率={decay_rate}, 天数={days}, "
                f"访问窗口={access_window_days:.1f}天, 影响记录={affected}"
            )

            self._invalidate_search_cache()
            return affected

        except asyncio.CancelledError:
            raise

    async def _update_access_times_internal(
        self, doc_ids: list[int], atom_ids=None
    ) -> bool:
        """合并同一时间窗口内的召回访问统计写。

        访问时间/次数只用于衰减排序，短时间内合并写不会改变语义；真正落库由
        `_flush_access_updates` 用单条原子 SQL 完成，避免每次召回都改写 k 行 metadata。

        Args:
            doc_ids: Document ids to bump.
            atom_ids: Optional atom ids to bump.

        Returns:
            bool: True if the ids were queued.
        """
        if self.db_connection is None:
            return False
        self._access_update_pending.update(int(doc_id) for doc_id in doc_ids)
        if atom_ids:
            self._access_atom_update_pending.update(
                int(atom_id) for atom_id in atom_ids
            )
        if not self._access_update_pending:
            return False
        if self._access_flush_task is None or self._access_flush_task.done():
            self._access_flush_task = self._create_tracked_task(
                self._deferred_flush_access_updates()
            )
        return True

    async def _deferred_flush_access_updates(self) -> None:
        await asyncio.sleep(self._ACCESS_UPDATE_DEBOUNCE_SECONDS)
        await self._flush_access_updates()

    async def _flush_access_updates(self) -> None:
        """把挂起的访问统计写入数据库（失败时保留挂起项，下次重试）。"""
        doc_ids = sorted(self._access_update_pending)
        atom_ids = sorted(self._access_atom_update_pending)
        if not doc_ids and not atom_ids:
            return
        written = await self._write_access_updates(doc_ids, atom_ids)
        if written:
            self._access_update_pending.difference_update(doc_ids)
            self._access_atom_update_pending.difference_update(atom_ids)

    async def _write_access_updates(
        self, doc_ids: list[int], atom_ids: list[int]
    ) -> bool:
        """单条原子 SQL 批量递增访问时间与次数。"""
        unique_ids = list(dict.fromkeys(int(doc_id) for doc_id in doc_ids))
        if not unique_ids or self.db_connection is None:
            return False

        current_time = time.time()
        try:
            # 单条原子 SQL，避免并发召回任务对同一记忆产生丢失更新
            placeholders = ",".join("?" * len(unique_ids))
            async with self._write_transaction() as connection:
                cursor = await connection.execute(
                    f"""
                    UPDATE documents
                    SET metadata = CASE
                        WHEN json_valid(metadata) THEN json_set(
                            json_set(metadata, '$.last_access_time', ?),
                            '$.access_count',
                            MIN(
                                COALESCE(
                                    CAST(json_extract(metadata, '$.access_count') AS INTEGER),
                                    0
                                ) + 1,
                                1000000
                            )
                        )
                        ELSE json_set('{{}}', '$.last_access_time', ?, '$.access_count', 1)
                    END
                    WHERE id IN ({placeholders})
                    """,
                    (current_time, current_time, *unique_ids),
                )
                if atom_ids and self.atom_store is not None:
                    unique_atoms = list(
                        dict.fromkeys(int(atom_id) for atom_id in atom_ids)
                    )
                    await connection.execute(
                        f"UPDATE memory_atoms SET last_accessed_at = ? "
                        f"WHERE id IN ({','.join('?' for _ in unique_atoms)}) "
                        "AND status = 'active' AND expires_at > ?",
                        (current_time, *unique_atoms, current_time),
                    )
            return cursor.rowcount > 0
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Access statistics are best effort; retain SQLite details for diagnosis.
            logger.warning(
                f"Failed to update memory access statistics "
                f"(db_path={self.db_path}, "
                f"sqlite_errorcode={getattr(e, 'sqlite_errorcode', None)}, "
                f"sqlite_errorname={getattr(e, 'sqlite_errorname', None)}, "
                f"doc_ids={unique_ids}): {e}",
                exc_info=True,
            )
            return False
