"""
MemoryEngine 的 MemoryEngineBatchMixin 拆分模块
自动从 core/managers/memory_engine.py 拆分，保持行为不变
"""

from typing import Any
import asyncio
from ..utils.number_utils import clamp_float, safe_float
from ..processors.atom_classifier import classify_atoms
import json
from astrbot.api import logger
from pathlib import Path
import time


class MemoryEngineBatchMixin:
    """MemoryEngine 拆分模块：MemoryEngineBatchMixin"""
    async def batch_delete_memories(self, memory_ids: list[int]) -> int:
        """Batch delete multiple memories using bulk SQL operations."""
        if not memory_ids:
            return 0

        if self.db_connection is None:
            logger.error("[批量删除] 数据库连接未初始化")
            return 0

        self._invalidate_search_cache()
        total_deleted = 0
        sql_batch_size = 200

        for i in range(0, len(memory_ids), sql_batch_size):
            batch = memory_ids[i : i + sql_batch_size]
            placeholders = ",".join("?" * len(batch))
            op_id = await self._start_write_op(
                "batch_delete",
                {
                    "memory_ids": batch,
                    "batch_offset": i,
                    "batch_size": len(batch),
                },
            )
            batch_deleted = 0

            try:
                # 1. Batch delete from BM25 FTS
                await self.db_connection.execute(
                    f"DELETE FROM livingmemory_memories_fts WHERE doc_id IN ({placeholders})",
                    batch,
                )
                await self._advance_write_op(
                    op_id,
                    "bm25_deleted",
                    payload_patch={"memory_ids": batch},
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
                await self._advance_write_op(
                    op_id,
                    "faiss_deleted",
                    payload_patch={"memory_ids": batch, "found_ids": found_ids},
                )

                # 3. Batch delete from documents table
                cursor = await self.db_connection.execute(
                    f"DELETE FROM documents WHERE id IN ({placeholders})",
                    batch,
                )
                await self.db_connection.execute(
                    f"DELETE FROM memory_sources WHERE memory_id IN ({placeholders})",
                    batch,
                )
                await self.db_connection.commit()
                # FaissVecDB 与本引擎共享 documents 表；向量删除可能已移除
                # 对应行，因此以删除前查到的记录数作为准确结果。
                batch_deleted = len(found_ids)
                await self._advance_write_op(
                    op_id,
                    "documents_deleted",
                    payload_patch={
                        "memory_ids": batch,
                        "found_ids": found_ids,
                        "deleted_count": batch_deleted,
                    },
                )

                # 4. Batch delete graph artifacts and atoms
                await self._delete_graph_and_atoms_for_batch(batch)
                await self._advance_write_op(
                    op_id,
                    "graph_atoms_deleted",
                    payload_patch={"memory_ids": batch, "deleted_count": batch_deleted},
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                await self._advance_write_op(
                    op_id,
                    "batch_delete_failed",
                    status="needs_repair",
                    error=str(e),
                    payload_patch={
                        "memory_ids": batch,
                        "deleted_count": batch_deleted,
                    },
                )
                logger.error(
                    f"[批量删除] 批次删除失败 (offset={i}, size={len(batch)})",
                    exc_info=True,
                )
                raise

            await self._advance_write_op(
                op_id,
                "completed",
                status="completed",
                payload_patch={"memory_ids": batch, "deleted_count": batch_deleted},
            )
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
            safe_json_dict = self._safe_json_dict

            # 使用主键 keyset 分页流式读取，避免 OFFSET 分页的 O(N²) 开销。
            while True:
                cursor = await self.db_connection.execute(
                    "SELECT id, metadata FROM documents WHERE id > ? ORDER BY id LIMIT ?",
                    (last_id, batch_size),
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

    async def archive_memories(self, memory_ids: list[int]) -> int:
        """Remove memories from retrieval indexes while retaining their documents."""
        if not memory_ids or self.db_connection is None:
            return 0

        unique_ids = list(dict.fromkeys(int(memory_id) for memory_id in memory_ids))
        documents = await self.faiss_db.document_storage.get_documents(
            metadata_filters={},
            ids=unique_ids,
            offset=0,
            limit=len(unique_ids),
        )
        active_documents = []
        metadata_updates: list[tuple[str, int]] = []
        archived_at = time.time()
        for document in documents:
            metadata = self._safe_json_dict(document.get("metadata"))
            if str(metadata.get("status") or "active") == "archived":
                continue
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
        await self.db_connection.executemany(
            "UPDATE documents SET metadata = ? WHERE id = ?",
            metadata_updates,
        )
        await self.db_connection.execute(
            f"DELETE FROM livingmemory_memories_fts WHERE doc_id IN ({placeholders})",
            archived_ids,
        )
        await self.db_connection.commit()

        embedding_storage = getattr(self.faiss_db, "embedding_storage", None)
        embedding_delete = getattr(embedding_storage, "delete", None)
        if callable(embedding_delete):
            await embedding_delete(archived_ids)
        else:
            logger.warning(
                "[归档] 当前 AstrBot 不支持独立删除向量，已保留软归档状态"
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
        metadata = self._safe_json_dict(memory.get("metadata"))
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
                parent_importance=clamp_float(
                    metadata.get("importance"), default=0.5
                ),
                session_id=metadata.get("session_id"),
                persona_id=metadata.get("persona_id"),
            )
            for atom in atoms:
                atom.parent_memory_id = memory_id
            if atoms and self.atom_store is not None:
                await self.atom_store.insert_many(atoms)
            if self.graph_memory_manager is not None:
                await self.graph_memory_manager.index_memory(
                    memory_id, content, metadata, atoms
                )

            await self.db_connection.execute(
                "UPDATE documents SET metadata = ? WHERE id = ?",
                (json.dumps(metadata, ensure_ascii=False), memory_id),
            )
            await self.db_connection.commit()
            self._invalidate_search_cache()
            return True
        except Exception:
            if fts_inserted:
                await self.db_connection.execute(
                    "DELETE FROM livingmemory_memories_fts WHERE doc_id = ?",
                    (memory_id,),
                )
                await self.db_connection.commit()
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
                await self.db_connection.execute(
                    "INSERT OR REPLACE INTO migration_status (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                    (migration_key, "true"),
                )
                await self.db_connection.commit()
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

            if updates:
                await self.db_connection.executemany(
                    "UPDATE documents SET metadata = ? WHERE id = ?",
                    updates,
                )
            updated_count = len(updates)

            # 6. 提交更新
            await self.db_connection.commit()

            # 7. 标记为已迁移
            await self.db_connection.execute(
                "INSERT OR REPLACE INTO migration_status (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                (migration_key, "true"),
            )
            await self.db_connection.commit()

            logger.info(
                f"[自动迁移] 完成！已更新 {updated_count} 条记录 -> {unified_msg_origin}"
            )

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[自动迁移] 迁移失败: {e}", exc_info=True)

    async def get_statistics(self) -> dict[str, Any]:
        """
        获取记忆统计信息（使用批量处理避免内存问题）

        Returns:
            Dict: 统计信息,包含:
                - total_memories: 总记忆数
                - sessions: 各会话的记忆数（按UUID分组）
                - status_breakdown: 各状态的记忆数
                - avg_importance: 平均重要性
                - oldest_memory: 最旧记忆时间
                - newest_memory: 最新记忆时间
        """
        try:
            if self.db_connection is None:
                raise RuntimeError("数据库连接未初始化")

            cursor = await self.db_connection.execute("SELECT COUNT(*) FROM documents")
            row = await cursor.fetchone()
            total_count = int(row[0]) if row and row[0] is not None else 0

            stats = {}
            stats["total_memories"] = total_count

            # 初始化统计变量
            session_counts: dict[str, int] = {}
            status_breakdown = {"active": 0, "archived": 0, "deleted": 0}
            importance_sum = 0
            importance_count = 0
            importance_distribution = {
                "0-1": 0, "1-2": 0, "2-3": 0, "3-4": 0, "4-5": 0,
                "5-6": 0, "6-7": 0, "7-8": 0, "8-9": 0, "9-10": 0,
            }
            bucket_keys = [
                "0-1", "1-2", "2-3", "3-4", "4-5",
                "5-6", "6-7", "7-8", "8-9", "9-10",
            ]
            oldest_time = None
            newest_time = None

            # 使用主键 keyset 分页流式读取，避免 OFFSET 分页的 O(N²) 开销。
            batch_size = 500
            last_id = 0
            safe_json_dict = self._safe_json_dict

            while True:
                cursor = await self.db_connection.execute(
                    "SELECT id, metadata FROM documents WHERE id > ? ORDER BY id LIMIT ?",
                    (last_id, batch_size),
                )
                rows = await cursor.fetchall()
                if not rows:
                    break
                last_id = int(rows[-1]["id"])

                # 通过线程池批量解析 metadata（避免大量 json.loads 阻塞事件循环）
                raw_metadata = [r["metadata"] for r in rows]
                parsed = await asyncio.to_thread(
                    lambda: [safe_json_dict(m) for m in raw_metadata]
                )

                for metadata in parsed:
                    # 统计会话（直接使用session_id分组）
                    session_id = metadata.get("session_id")
                    if session_id:
                        session_counts[session_id] = (
                            session_counts.get(session_id, 0) + 1
                        )

                    # 统计状态（默认 active）
                    status = metadata.get("status", "active")
                    if status in status_breakdown:
                        status_breakdown[status] += 1
                    else:
                        # 未知状态默认计入 active
                        status_breakdown["active"] += 1

                    # 统计重要性
                    importance = metadata.get("importance")
                    if importance is not None:
                        clamped = clamp_float(importance, default=0.5)
                        importance_sum += clamped
                        importance_count += 1
                        # 分桶统计 (0-10 归一化)
                        display_importance = clamped * 10 if clamped <= 1 else clamped
                        bucket_idx = min(9, max(0, int(display_importance)))
                        importance_distribution[bucket_keys[bucket_idx]] += 1

                    # 统计时间
                    create_time = metadata.get("create_time")
                    if create_time:
                        create_time = safe_float(create_time, 0.0)
                        if oldest_time is None or create_time < oldest_time:
                            oldest_time = create_time
                        if newest_time is None or create_time > newest_time:
                            newest_time = create_time

            stats["sessions"] = session_counts
            stats["status_breakdown"] = status_breakdown
            stats["avg_importance"] = (
                importance_sum / importance_count if importance_count > 0 else 0.0
            )
            stats["importance_distribution"] = importance_distribution
            stats["oldest_memory"] = oldest_time
            stats["newest_memory"] = newest_time
            if self.graph_store is not None:
                stats.update(await self.graph_store.get_memory_entry_stats())
                stats["graph_memory_enabled"] = True
            else:
                stats["graph_memory_enabled"] = False
            stats["index_maintenance"] = dict(self.index_maintenance_status)

            return stats
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"获取统计信息失败: {e}", exc_info=True)
            return {
                "total_memories": 0,
                "sessions": {},
                "status_breakdown": {"active": 0, "archived": 0, "deleted": 0},
                "avg_importance": 0.0,
                "oldest_memory": None,
                "newest_memory": None,
                "graph_memory_enabled": bool(self.graph_store is not None),
            }

    async def maintain_storage(self, *, vacuum: bool = False) -> dict[str, Any]:
        """Run SQLite storage maintenance and return size diagnostics."""
        try:
            db_path = Path(self.db_path)
            wal_path = Path(f"{self.db_path}-wal")
            before_size = db_path.stat().st_size if db_path.exists() else 0
            before_wal_size = wal_path.stat().st_size if wal_path.exists() else 0

            if self.db_connection is None:
                return {
                    "success": False,
                    "error": "database connection is not initialized",
                }

            for fts_table in (
                "livingmemory_memories_fts",
                "livingmemory_graph_entries_fts",
                "memory_atoms_fts",
            ):
                try:
                    await self.db_connection.execute(
                        f"INSERT INTO {fts_table}({fts_table}) VALUES ('optimize')"
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.debug(
                        f"[StorageMaintenance] 跳过 FTS optimize: {fts_table}",
                        exc_info=True,
                    )

            await self.db_connection.commit()
            await self.db_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

            if vacuum:
                await self.db_connection.execute("VACUUM")

            after_size = db_path.stat().st_size if db_path.exists() else 0
            after_wal_size = wal_path.stat().st_size if wal_path.exists() else 0
            return {
                "success": True,
                "vacuum": vacuum,
                "db_size_before": before_size,
                "db_size_after": after_size,
                "wal_size_before": before_wal_size,
                "wal_size_after": after_wal_size,
                "bytes_reclaimed": max(
                    0,
                    before_size + before_wal_size - after_size - after_wal_size,
                ),
            }
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[StorageMaintenance] 执行存储维护失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
