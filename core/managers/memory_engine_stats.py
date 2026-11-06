"""统计、整合候选与存储维护接口。"""

import asyncio
from collections import OrderedDict
from pathlib import Path
from typing import Any

import aiosqlite

from astrbot.api import logger

from ...storage.atom_store import AtomStore
from ..memory_scope import (
    adapter_sql,
)
from ..retrieval.hybrid_retriever import HybridResult
from ..utils.json_utils import safe_json_dict
from ..utils.number_utils import clamp_float, safe_float
from .agentic_recall_types import AgenticRecallOutcome as AgenticRecallOutcome


class MemoryEngineStatsMixin:
    """MemoryEngine 的 MemoryEngineStatsMixin 分组。

    宿主共享状态由 ``MemoryEngine`` 独占持有，本模块只通过 self 访问。
    """

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
            ownership, ownership_params = adapter_sql("metadata")
            cursor = await self.db_connection.execute(
                f"SELECT COUNT(*) FROM documents WHERE {ownership}", ownership_params
            )
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
                "0-1": 0,
                "1-2": 0,
                "2-3": 0,
                "3-4": 0,
                "4-5": 0,
                "5-6": 0,
                "6-7": 0,
                "7-8": 0,
                "8-9": 0,
                "9-10": 0,
            }
            bucket_keys = [
                "0-1",
                "1-2",
                "2-3",
                "3-4",
                "4-5",
                "5-6",
                "6-7",
                "7-8",
                "8-9",
                "9-10",
            ]
            oldest_time = None
            newest_time = None

            # 使用主键 keyset 分页流式读取，避免 OFFSET 分页的 O(N²) 开销。
            batch_size = 500
            last_id = 0

            while True:
                cursor = await self.db_connection.execute(
                    f"SELECT id, metadata FROM documents WHERE id > ? AND {ownership} ORDER BY id LIMIT ?",
                    (last_id, *ownership_params, batch_size),
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

    async def get_consolidation_candidates(
        self, max_importance: float, cutoff: float
    ) -> list[dict[str, Any]]:
        """
        查询符合整合条件的候选记忆（活跃状态 + 低重要度 + 足够旧）。

        Args:
            max_importance: 重要性上限（低于该值的记忆参与整合）
            cutoff: 创建时间上限（epoch 秒，早于该时间的记忆参与整合）

        Returns:
            List[Dict]: 候选列表，每项包含 id/content/metadata(已解析为 dict)

        Raises:
            RuntimeError: 数据库连接未初始化时抛出
        """
        if self.db_connection is None:
            raise RuntimeError("数据库连接未初始化")

        ownership, ownership_params = adapter_sql("metadata")
        cursor = await self.db_connection.execute(
            f"""
            SELECT id, text, metadata
            FROM documents
            WHERE {ownership} AND COALESCE(json_extract(metadata, '$.status'), 'active') = 'active'
              AND CAST(COALESCE(json_extract(metadata, '$.importance'), '0.5') AS REAL) < ?
              AND CAST(COALESCE(json_extract(metadata, '$.create_time'), '0') AS REAL) < ?
            ORDER BY id
            """,
            (*ownership_params, max_importance, cutoff),
        )
        rows = await cursor.fetchall()

        candidates: list[dict[str, Any]] = []
        for row in rows:
            candidates.append(
                {
                    "id": int(row["id"]),
                    "content": row["text"],
                    "metadata": safe_json_dict(row["metadata"]),
                }
            )
        return candidates

    async def get_consolidation_stats(self) -> dict[str, int]:
        """
        统计已整合与已归档的记忆数量（供管理页面展示）。

        Returns:
            Dict: 包含 consolidated_count 与 archived_count 的字典

        Raises:
            RuntimeError: 数据库连接未初始化时抛出
        """
        if self.db_connection is None:
            raise RuntimeError("数据库连接未初始化")

        ownership, ownership_params = adapter_sql(
            "CASE WHEN json_valid(metadata) THEN metadata ELSE '{}' END"
        )
        cursor = await self.db_connection.execute(
            "SELECT COUNT(*) FROM documents WHERE json_valid(metadata) "
            "AND json_array_length("
            "COALESCE(json_extract(metadata, '$.consolidated_from'), '[]')"
            ") > 0"
            f" AND {ownership}",
            ownership_params,
        )
        row = await cursor.fetchone()
        consolidated_count = int(row[0]) if row else 0

        cursor = await self.db_connection.execute(
            "SELECT COUNT(*) FROM documents WHERE "
            "COALESCE("
            "CASE WHEN json_valid(metadata) "
            "THEN json_extract(metadata, '$.status') END,"
            "'active'"
            ") = 'archived'"
            f" AND {ownership}",
            ownership_params,
        )
        row = await cursor.fetchone()
        archived_count = int(row[0]) if row else 0

        return {
            "consolidated_count": consolidated_count,
            "archived_count": archived_count,
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
                    async with self._write_transaction() as connection:
                        await connection.execute(
                            f"INSERT INTO {fts_table}({fts_table}) VALUES ('optimize')"
                        )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.debug(
                        f"[StorageMaintenance] 跳过 FTS optimize: {fts_table}",
                        exc_info=True,
                    )

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
