"""统计、整合候选与存储维护接口。"""

import asyncio
import json
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

    # ------------------------------------------------------------------
    # 数据体检（/lmem diag）与存储压缩
    # ------------------------------------------------------------------

    async def get_diagnostics(self) -> dict[str, Any]:
        """收集数据目录/数据库健康信息，供 ``/lmem diag`` 展示。

        体检只做只读操作（``PRAGMA quick_check`` 会读整库校验页结构，大库
        可能需要数秒）；任何单项失败只影响该字段，不会抛异常。

        Returns:
            诊断字典：db_version/quick_check/migration/documents/graph/faiss/
            db_bytes/wal_bytes/data_dir_bytes/backups/last_decay 等字段；
            无法获取的字段为 ``None``。
        """
        db_path = Path(self.db_path)
        data_dir = db_path.parent
        diagnostics: dict[str, Any] = {
            "db_path": str(db_path),
            "db_version": None,
            "quick_check": None,
            "migration": {
                "pending": [],
                "pending_documents": None,
                "legacy_session_markers": 0,
            },
            "documents": None,
            "graph": None,
            "faiss": self._diagnostics_faiss_totals(),
        }
        diagnostics.update(
            await asyncio.to_thread(self._diagnostics_storage_files, db_path, data_dir)
        )
        if self.db_connection is None:
            return diagnostics

        diagnostics["db_version"] = await self._diagnostics_db_version()
        diagnostics["quick_check"] = await self._diagnostics_quick_check()
        diagnostics["migration"] = await self._diagnostics_migration_status()
        diagnostics["documents"] = await self._diagnostics_document_counts()
        diagnostics["graph"] = await self._diagnostics_graph_counts()
        return diagnostics

    async def _diagnostics_db_version(self) -> int | None:
        """读取最新 db_version；缺表或读取失败返回 None。"""
        try:
            cursor = await self.db_connection.execute(
                "SELECT version FROM db_version ORDER BY id DESC LIMIT 1"
            )
            row = await cursor.fetchone()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("[Diag] 读取 db_version 失败", exc_info=True)
            return None
        if not row or row[0] is None:
            return None
        try:
            return int(row[0])
        except (TypeError, ValueError):
            return None

    async def _diagnostics_quick_check(self) -> str:
        """执行 PRAGMA quick_check；失败返回 ``error: ...`` 而不是抛异常。"""
        try:
            cursor = await self.db_connection.execute("PRAGMA quick_check")
            rows = await cursor.fetchall()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return f"error: {exc}"
        messages = [str(row[0]) for row in rows if row and row[0] is not None]
        return "; ".join(messages) if messages else "unknown"

    async def _diagnostics_migration_status(self) -> dict[str, Any]:
        """汇总 migration_status 中仍待处理的迁移标记。"""
        result: dict[str, Any] = {
            "pending": [],
            "pending_documents": None,
            "legacy_session_markers": 0,
        }
        try:
            cursor = await self.db_connection.execute(
                "SELECT key, value FROM migration_status"
            )
            rows = await cursor.fetchall()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("[Diag] 读取 migration_status 失败", exc_info=True)
            return result

        for row in rows:
            key = str(row[0] or "")
            value = str(row[1] or "")
            if key.startswith("migrated_umo_"):
                result["legacy_session_markers"] += 1
            elif key == "needs_index_rebuild" and value.casefold() == "true":
                result["pending"].append(key)
            elif key == "pending_documents_count":
                try:
                    result["pending_documents"] = int(value)
                except (TypeError, ValueError):
                    pass
        return result

    async def _diagnostics_document_counts(self) -> dict[str, int] | None:
        """按 metadata.status 统计 documents 数量（缺表返回 None）。"""
        status_expr = (
            "CASE WHEN json_valid(metadata) THEN "
            "COALESCE(json_extract(metadata, '$.status'), 'active') "
            "ELSE 'active' END"
        )
        try:
            cursor = await self.db_connection.execute(
                "SELECT COUNT(*) AS total, "
                f"SUM(CASE WHEN {status_expr} = 'active' THEN 1 ELSE 0 END) AS active, "
                f"SUM(CASE WHEN {status_expr} = 'archived' THEN 1 ELSE 0 END) "
                "AS archived, "
                f"SUM(CASE WHEN {status_expr} = 'deleted' THEN 1 ELSE 0 END) "
                "AS deleted "
                "FROM documents"
            )
            row = await cursor.fetchone()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("[Diag] 统计 documents 失败", exc_info=True)
            return None
        if not row:
            return None
        return {
            "total": int(row[0] or 0),
            "active": int(row[1] or 0),
            "archived": int(row[2] or 0),
            "deleted": int(row[3] or 0),
        }

    async def _diagnostics_graph_counts(self) -> dict[str, int] | None:
        """统计图记忆节点/边数量；图功能未启用或缺表返回 None。"""
        try:
            cursor = await self.db_connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name IN ('graph_nodes', 'graph_edges')"
            )
            tables = {str(row[0]) for row in await cursor.fetchall()}
            counts: dict[str, int] = {}
            for table in ("graph_nodes", "graph_edges"):
                if table not in tables:
                    return None
                table_cursor = await self.db_connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                )
                count_row = await table_cursor.fetchone()
                counts[table] = int(count_row[0] or 0) if count_row else 0
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("[Diag] 统计图记忆失败", exc_info=True)
            return None
        return {"nodes": counts["graph_nodes"], "edges": counts["graph_edges"]}

    def _diagnostics_faiss_totals(self) -> dict[str, int | None]:
        """读取主/图 FAISS 索引的 ntotal；未初始化时返回 None。"""

        def _ntotal(vector_db: Any) -> int | None:
            storage = getattr(vector_db, "embedding_storage", None)
            index = getattr(storage, "index", None)
            total = getattr(index, "ntotal", None)
            return int(total) if isinstance(total, int) else None

        return {
            "main": _ntotal(getattr(self, "faiss_db", None)),
            "graph": _ntotal(getattr(self, "graph_vector_db", None)),
        }

    @staticmethod
    def _diagnostics_storage_files(db_path: Path, data_dir: Path) -> dict[str, Any]:
        """同步收集数据库/WAL/备份/数据目录/衰减状态文件信息。

        Args:
            db_path: 主数据库文件路径。
            data_dir: 插件数据目录（数据库与 backups 的父目录）。

        Returns:
            文件尺寸与备份/衰减状态字段。
        """

        def _file_size(path: Path) -> int | None:
            try:
                return path.stat().st_size if path.is_file() else None
            except OSError:
                return None

        def _tree_sum(path: Path) -> tuple[int, int] | None:
            total = 0
            count = 0
            try:
                for item in path.rglob("*"):
                    if not item.is_file():
                        continue
                    try:
                        total += item.stat().st_size
                    except OSError:
                        continue
                    count += 1
            except OSError:
                return None
            return total, count

        backups_dir = data_dir / "backups"
        backups: dict[str, Any] = {"count": 0, "bytes": 0, "latest_at": None}
        if backups_dir.is_dir():
            for item in backups_dir.rglob("*"):
                if not item.is_file():
                    continue
                try:
                    stat = item.stat()
                except OSError:
                    continue
                backups["count"] += 1
                backups["bytes"] += stat.st_size
                if backups["latest_at"] is None or stat.st_mtime > backups["latest_at"]:
                    backups["latest_at"] = stat.st_mtime

        last_decay = None
        decay_state_path = data_dir / "decay_state.json"
        if decay_state_path.is_file():
            try:
                state = json.loads(decay_state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                state = None
            if isinstance(state, dict):
                last_decay = {
                    "date": state.get("last_decay_date"),
                    "timestamp": state.get("last_decay_timestamp"),
                }

        data_dir_total = _tree_sum(data_dir)
        return {
            "db_bytes": _file_size(db_path),
            "wal_bytes": _file_size(Path(f"{db_path}-wal")),
            "data_dir_bytes": data_dir_total[0] if data_dir_total else None,
            "backups": backups,
            "last_decay": last_decay,
        }

    async def compact_storage(self) -> dict[str, Any]:
        """用独立连接执行 ``VACUUM``，返回压缩前后大小。

        使用独立的 autocommit 连接，避免复用引擎长连接上的隐式事务（SQLite
        禁止在事务内 VACUUM）。引擎连接保持打开时 VACUUM 需要短暂的独占写锁；
        若此时有并发写入会抛 ``database is locked``，交由命令错误处理展示。

        Returns:
            压缩统计：压缩前后与释放的字节数。

        Raises:
            RuntimeError: 数据库文件不存在。
        """
        db_path = Path(self.db_path)
        if not db_path.is_file():
            raise RuntimeError(f"database file not found: {db_path}")
        wal_path = Path(f"{self.db_path}-wal")
        before_db = db_path.stat().st_size
        before_wal = wal_path.stat().st_size if wal_path.is_file() else 0
        logger.info("[Diag] 开始 VACUUM 压缩数据库: %s", db_path)
        async with aiosqlite.connect(str(db_path), isolation_level=None) as connection:
            # PRAGMA 的结果必须取回并关闭游标，否则游标仍在执行中，VACUUM 会报
            # "cannot VACUUM - SQL statements in progress"。
            for statement in (
                "PRAGMA busy_timeout = 10000",
                "PRAGMA wal_checkpoint(TRUNCATE)",
                "PRAGMA incremental_vacuum",
            ):
                cursor = await connection.execute(statement)
                await cursor.fetchall()
                await cursor.close()
            await connection.execute("VACUUM")
        after_db = db_path.stat().st_size
        after_wal = wal_path.stat().st_size if wal_path.is_file() else 0
        return {
            "success": True,
            "db_size_before": before_db,
            "db_size_after": after_db,
            "wal_size_before": before_wal,
            "wal_size_after": after_wal,
            "bytes_reclaimed": max(0, before_db + before_wal - after_db - after_wal),
        }
