"""GraphStore 的读取方法与图快照组装。

宿主契约：``_connect`` / ``_read_connect`` 连接工厂与
``_NODE_TOKEN_QUERY_BATCH_SIZE`` 批量常量由 ``GraphStore`` 提供。
"""

from __future__ import annotations

import math
from typing import Any

import aiosqlite

from astrbot.api import logger

from ..core.memory_scope import adapter_sql, memory_scope_sql
from ..core.utils.json_utils import safe_json_dict
from ..core.utils.number_utils import safe_float

# 扩展候选先按 3 倍 limit 取回，再按 hub 惩罚重排后截断；
# 3 倍足以让低 degree 邻居/入口挤进前 limit，不会放大成全表扫描。
_EXPANSION_OVERFETCH = 3


class GraphStoreReadMixin:
    """GraphStore 的读取方法与图快照组装。"""

    # 宿主共享常量契约
    _NODE_TOKEN_QUERY_BATCH_SIZE: int

    # initialize() 之后按需探测：只有宿主 documents 表存在时才过滤 archived 记忆；
    # 独立图谱库（重建 shadow / 单元测试）没有该表，退化为不过滤。
    _documents_table_available: bool = False

    async def _documents_table_exists(self, db: aiosqlite.Connection) -> bool:
        """Return whether the shared database carries the host ``documents`` table."""
        if getattr(self, "_documents_table_available", False):
            return True
        cursor = await db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'documents' LIMIT 1"
        )
        self._documents_table_available = await cursor.fetchone() is not None
        return self._documents_table_available

    # Non-active sources in the host ``documents`` table. Malformed JSON counts
    # as active so a broken legacy row cannot silently hide graph memories.
    _NON_ACTIVE_DOCUMENTS_SQL: str = (
        "SELECT d.id FROM documents d WHERE "
        "CASE WHEN json_valid(d.metadata) THEN "
        "COALESCE(json_extract(d.metadata, '$.status'), 'active') "
        "ELSE 'active' END <> 'active'"
    )

    async def _archived_memory_exclusion(
        self, db: aiosqlite.Connection, column: str
    ) -> str | None:
        """Predicate excluding entries whose source memory is archived/deleted.

        Uses an exclusion (not an inclusion) predicate on purpose: graph entries
        whose source document is missing from ``documents`` (rebuild shadow rows,
        fake/partial stores, half-written migrations) stay retrievable instead of
        being silently dropped. Malformed JSON metadata counts as active.

        Args:
            db: Open read connection used to probe the schema.
            column: Column holding the source memory id (e.g. ``ge.source_memory_id``).

        Returns:
            ``"<column> NOT IN (SELECT ... non-active documents)"`` or ``None``
            when the database has no host ``documents`` table.
        """
        if not await self._documents_table_exists(db):
            return None
        return f"{column} NOT IN ({self._NON_ACTIVE_DOCUMENTS_SQL})"

    @staticmethod
    def _hub_penalty(degree: int) -> float:
        """Weight multiplier for a node with the given active-edge degree."""
        return 1.0 / (1.0 + math.log1p(max(0, int(degree))))

    async def _node_hub_factors(
        self, db: aiosqlite.Connection, node_ids: list[int]
    ) -> dict[int, float]:
        """Return normalized ``1/(1+log1p(degree))`` factors for expansion nodes.

        Degrees are computed from active edges so hub nodes (high degree) expand
        with a lower weight than specific leaf nodes. Factors are normalized by
        the maximum so the most specific node keeps weight 1.0 and results stay
        in the same score range as before.

        Args:
            db: Open read connection.
            node_ids: Nodes being expanded from.

        Returns:
            Mapping ``node_id -> factor`` in ``(0, 1]``; missing nodes mean degree 0.
        """
        unique_ids = sorted({int(node_id) for node_id in node_ids})
        if not unique_ids:
            return {}
        placeholders = ",".join("?" * len(unique_ids))
        cursor = await db.execute(
            f"""
            SELECT node_id, COUNT(*) AS degree
            FROM (
                SELECT source_node_id AS node_id FROM graph_edges
                WHERE status = 'active' AND source_node_id IN ({placeholders})
                UNION ALL
                SELECT target_node_id AS node_id FROM graph_edges
                WHERE status = 'active' AND target_node_id IN ({placeholders})
            )
            GROUP BY node_id
            """,
            (*unique_ids, *unique_ids),
        )
        degrees = {int(row[0]): int(row[1]) for row in await cursor.fetchall()}
        raw = {
            node_id: self._hub_penalty(degrees.get(node_id, 0))
            for node_id in unique_ids
        }
        peak = max(raw.values(), default=1.0) or 1.0
        return {node_id: value / peak for node_id, value in raw.items()}

    async def list_vector_doc_ids(self) -> list[int]:
        async with self._read_connect() as db:
            cursor = await db.execute(
                "SELECT DISTINCT vector_doc_id FROM graph_entries "
                "WHERE vector_doc_id IS NOT NULL"
            )
            return [int(row[0]) for row in await cursor.fetchall()]

    async def list_vector_doc_ids_by_source(self) -> dict[int, list[int]]:
        async with self._read_connect() as db:
            cursor = await db.execute(
                """
                SELECT source_memory_id, vector_doc_id
                FROM graph_entries
                WHERE vector_doc_id IS NOT NULL
                GROUP BY source_memory_id, vector_doc_id
                """
            )
            result: dict[int, list[int]] = {}
            for source_memory_id, vector_doc_id in await cursor.fetchall():
                result.setdefault(int(source_memory_id), []).append(int(vector_doc_id))
            return result

    async def iter_memory_entry_groups(self, batch_size: int = 100):
        """Yield graph-vector payloads grouped by source memory."""
        last_memory_id = -1
        async with self._read_connect() as db:
            while True:
                cursor = await db.execute(
                    """
                    SELECT DISTINCT source_memory_id
                    FROM graph_entries
                    WHERE source_memory_id > ?
                    ORDER BY source_memory_id
                    LIMIT ?
                    """,
                    (last_memory_id, max(1, int(batch_size))),
                )
                memory_ids = [int(row[0]) for row in await cursor.fetchall()]
                if not memory_ids:
                    break

                placeholders = ",".join("?" for _ in memory_ids)
                entry_cursor = await db.execute(
                    f"""
                    SELECT id, source_memory_id, content, metadata
                    FROM graph_entries
                    WHERE source_memory_id IN ({placeholders})
                    ORDER BY source_memory_id, id
                    """,
                    memory_ids,
                )
                rows = await entry_cursor.fetchall()

                grouped: dict[int, list[tuple[int, str, dict[str, Any]]]] = {}
                for entry_id, source_memory_id, content, metadata in rows:
                    grouped.setdefault(int(source_memory_id), []).append(
                        (int(entry_id), str(content or ""), safe_json_dict(metadata))
                    )
                yield [
                    (
                        source_memory_id,
                        entries[0][0],
                        [(content, metadata) for _, content, metadata in entries],
                    )
                    for source_memory_id, entries in grouped.items()
                    if entries
                ]
                last_memory_id = memory_ids[-1]

    async def search_entries_by_bm25(
        self,
        fts_query: str,
        limit: int,
        session_id: str | None = None,
        persona_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search graph entries through the FTS table."""
        filters: list[str] = []
        params: list[Any] = [fts_query]
        scope_clause, scope_params = memory_scope_sql("ge.session_id", session_id)
        if scope_clause:
            filters.append(scope_clause)
            params.extend(scope_params)
        if persona_id is not None:
            filters.append("ge.persona_id = ?")
            params.append(persona_id)
        ownership, ownership_params = adapter_sql("ge.metadata")
        filters.append(ownership)
        params.extend(ownership_params)

        async with self._read_connect() as db:
            db.row_factory = aiosqlite.Row
            archived_exclusion = await self._archived_memory_exclusion(
                db, "ge.source_memory_id"
            )
            if archived_exclusion is not None:
                filters.append(archived_exclusion)
            where_clause = f"AND {' AND '.join(filters)}" if filters else ""
            try:
                cursor = await db.execute(
                    f"""
                    SELECT ge.id, ge.source_memory_id, ge.content, ge.metadata,
                           ge.entry_type, ge.relation_type, ge.session_id, ge.persona_id,
                           bm25(livingmemory_graph_entries_fts) AS score
                    FROM livingmemory_graph_entries_fts
                    JOIN graph_entries ge ON ge.id = livingmemory_graph_entries_fts.entry_id
                    WHERE livingmemory_graph_entries_fts MATCH ? {where_clause}
                    ORDER BY score ASC
                    LIMIT ?
                    """,
                    (*params, limit),
                )
                rows = await cursor.fetchall()
            except Exception as e:
                logger.warning(f"[GraphStore] FTS 检索失败，已跳过图关键词路: {e}")
                rows = []

        if not rows:
            return []

        scores = [float(row["score"]) for row in rows]
        max_score = max(scores)
        min_score = min(scores)
        score_range = max_score - min_score
        hits: list[dict[str, Any]] = []
        for row in rows:
            normalized = (
                1.0
                if score_range == 0
                else (max_score - float(row["score"])) / score_range
            )
            metadata = safe_json_dict(row["metadata"])
            hits.append(
                {
                    "entry_id": int(row["id"]),
                    "source_memory_id": int(row["source_memory_id"]),
                    "content": row["content"],
                    "metadata": metadata,
                    "entry_type": row["entry_type"],
                    "relation_type": row["relation_type"],
                    "score": normalized,
                }
            )
        return hits

    async def search_nodes_by_tokens(
        self, tokens: list[str], limit: int = 20
    ) -> list[dict[str, Any]]:
        """Find graph nodes whose canonical values overlap query tokens."""
        normalized_tokens = list(dict.fromkeys(str(token).strip() for token in tokens))
        normalized_tokens = [token for token in normalized_tokens if token]
        if not normalized_tokens:
            return []

        ownership, ownership_params = adapter_sql("ge.metadata")
        node_filters: list[str] = []
        if ownership_params:
            node_filters.append(
                "id IN (SELECT gen.node_id FROM graph_entry_nodes gen "
                "JOIN graph_entries ge ON ge.id=gen.entry_id "
                f"WHERE {ownership})"
            )
        rows_by_id: dict[int, aiosqlite.Row] = {}
        async with self._read_connect() as db:
            db.row_factory = aiosqlite.Row
            archived_exclusion = await self._archived_memory_exclusion(
                db, "ge.source_memory_id"
            )
            if archived_exclusion is not None:
                # 只排除“仅挂在 archived 记忆上”的节点；没有入口关联或入口
                # 指向缺失文档的节点继续可检索，避免图谱重建中途静默丢节点。
                node_filters.append(
                    "id NOT IN ("
                    "SELECT gen.node_id FROM graph_entry_nodes gen "
                    "JOIN graph_entries ge ON ge.id = gen.entry_id "
                    f"WHERE ge.source_memory_id IN ({self._NON_ACTIVE_DOCUMENTS_SQL}))"
                )
            node_filter = " AND ".join(node_filters) if node_filters else "1"
            for start in range(
                0, len(normalized_tokens), self._NODE_TOKEN_QUERY_BATCH_SIZE
            ):
                batch = normalized_tokens[
                    start : start + self._NODE_TOKEN_QUERY_BATCH_SIZE
                ]
                clauses = ["canonical_value LIKE ?" for _ in batch]
                params = [f"%{token}%" for token in batch]
                if self._node_fts_available:
                    clauses = [
                        "id IN (SELECT rowid FROM livingmemory_graph_nodes_fts WHERE canonical_value LIKE ?)"
                        if len(token) >= 3 and "%" not in token and "_" not in token
                        else "canonical_value LIKE ?"
                        for token in batch
                    ]
                cursor = await db.execute(
                    f"""
                    SELECT id, node_key, node_type, node_value, canonical_value, metadata
                    FROM graph_nodes
                    WHERE ({" OR ".join(clauses)}) AND {node_filter}
                    ORDER BY LENGTH(canonical_value) ASC
                    LIMIT ?
                    """,
                    (*params, *ownership_params, limit),
                )
                batch_rows = await cursor.fetchall()
                for row in batch_rows:
                    rows_by_id.setdefault(int(row["id"]), row)

        rows = sorted(
            rows_by_id.values(),
            key=lambda row: (len(str(row["canonical_value"])), int(row["id"])),
        )[:limit]

        return [
            {
                "id": int(row["id"]),
                "node_key": row["node_key"],
                "node_type": row["node_type"],
                "node_value": row["node_value"],
                "canonical_value": row["canonical_value"],
                "metadata": safe_json_dict(row["metadata"]),
            }
            for row in rows
        ]

    async def get_entries_for_node_ids(
        self,
        node_ids: list[int],
        limit: int,
        session_id: str | None = None,
        persona_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Expand one hop from matched nodes to their linked entries."""
        if not node_ids:
            return []

        placeholders = ",".join("?" * len(node_ids))
        filters: list[str] = []
        params: list[Any] = list(node_ids)

        scope_clause, scope_params = memory_scope_sql("ge.session_id", session_id)
        if scope_clause:
            filters.append(scope_clause)
            params.extend(scope_params)
        if persona_id is not None:
            filters.append("ge.persona_id = ?")
            params.append(persona_id)
        ownership, ownership_params = adapter_sql("ge.metadata")
        filters.append(ownership)
        params.extend(ownership_params)

        async with self._read_connect() as db:
            db.row_factory = aiosqlite.Row
            archived_exclusion = await self._archived_memory_exclusion(
                db, "ge.source_memory_id"
            )
            if archived_exclusion is not None:
                filters.append(archived_exclusion)
            where_clause = f"AND {' AND '.join(filters)}" if filters else ""
            factors = await self._node_hub_factors(db, node_ids)
            cursor = await db.execute(
                f"""
                SELECT ge.id, ge.source_memory_id, ge.content, ge.metadata,
                       ge.entry_type, ge.relation_type,
                       COUNT(DISTINCT gen.node_id) AS hit_count,
                       GROUP_CONCAT(DISTINCT gen.node_id) AS linked_node_ids
                FROM graph_entry_nodes gen
                JOIN graph_entries ge ON ge.id = gen.entry_id
                WHERE gen.node_id IN ({placeholders}) {where_clause}
                GROUP BY ge.id
                ORDER BY hit_count DESC, ge.id DESC
                LIMIT ?
                """,
                (*params, max(1, limit) * _EXPANSION_OVERFETCH),
            )
            rows = await cursor.fetchall()

        hits: list[dict[str, Any]] = []
        for row in rows:
            metadata = safe_json_dict(row["metadata"])
            linked_node_ids = [
                int(value)
                for value in str(row["linked_node_ids"] or "").split(",")
                if value
            ]
            # 从低 degree 节点扩展出来的入口权重更高；hub 节点命中多但信息泛，
            # 用最大因子（最具体的那条路径）避免多路径命中被过度惩罚。
            penalty = max(
                (factors.get(node_id, 1.0) for node_id in linked_node_ids),
                default=1.0,
            )
            base_score = min(1.0, 0.35 + 0.15 * int(row["hit_count"]))
            hits.append(
                {
                    "entry_id": int(row["id"]),
                    "source_memory_id": int(row["source_memory_id"]),
                    "content": row["content"],
                    "metadata": metadata,
                    "entry_type": row["entry_type"],
                    "relation_type": row["relation_type"],
                    "score": min(1.0, base_score * penalty),
                    "hit_count": int(row["hit_count"]),
                    "hub_penalty": round(penalty, 4),
                }
            )
        hits.sort(key=lambda hit: (hit["score"], hit["entry_id"]), reverse=True)
        return hits[: max(1, limit)]

    async def get_neighbor_node_ids(
        self,
        node_ids: list[int],
        limit: int,
    ) -> list[int]:
        """Return graph nodes adjacent to the given nodes through active edges."""
        if not node_ids:
            return []

        normalized_ids = sorted({int(item) for item in node_ids})
        placeholders = ",".join("?" * len(normalized_ids))
        limit = max(1, min(limit, 500))

        ownership, ownership_params = adapter_sql("metadata")
        owner_filter = (
            f"AND source_memory_id IN (SELECT source_memory_id FROM graph_entries WHERE {ownership})"
            if ownership_params
            else ""
        )
        async with self._read_connect() as db:
            cursor = await db.execute(
                f"""
                SELECT neighbor_id, SUM(edge_weight) AS total_weight
                FROM (
                    SELECT target_node_id AS neighbor_id, weight AS edge_weight
                    FROM graph_edges
                    WHERE source_node_id IN ({placeholders})
                      AND status = 'active' {owner_filter}
                    UNION ALL
                    SELECT source_node_id AS neighbor_id, weight AS edge_weight
                    FROM graph_edges
                    WHERE target_node_id IN ({placeholders})
                      AND status = 'active' {owner_filter}
                )
                WHERE neighbor_id NOT IN ({placeholders})
                GROUP BY neighbor_id
                ORDER BY total_weight DESC, neighbor_id ASC
                LIMIT ?
                """,
                (
                    *normalized_ids,
                    *ownership_params,
                    *normalized_ids,
                    *ownership_params,
                    *normalized_ids,
                    max(1, limit) * _EXPANSION_OVERFETCH,
                ),
            )
            rows = await cursor.fetchall()
            factors = await self._node_hub_factors(db, [int(row[0]) for row in rows])

        # 按“边权 × hub 惩罚”重排：邻居本身是 hub 时扩展噪声大，
        # 让更具体的邻居优先进入 limit，避免 24 条扩展名额被 hub 占满。
        ranked = sorted(
            (
                (float(row[1] or 0.0) * factors.get(int(row[0]), 1.0), int(row[0]))
                for row in rows
            ),
            key=lambda item: (-item[0], item[1]),
        )
        return [neighbor_id for _, neighbor_id in ranked[: max(1, limit)]]

    async def get_memory_entry_stats(self) -> dict[str, int]:
        """Return graph storage counts for status reporting."""
        ownership, ownership_params = adapter_sql("ge.metadata")
        if ownership_params:
            async with self._read_connect() as db:
                entries = await (
                    await db.execute(
                        f"SELECT COUNT(*) FROM graph_entries ge WHERE {ownership}",
                        ownership_params,
                    )
                ).fetchone()
                nodes = await (
                    await db.execute(
                        f"SELECT COUNT(DISTINCT gen.node_id) FROM graph_entry_nodes gen JOIN graph_entries ge ON ge.id=gen.entry_id WHERE {ownership}",
                        ownership_params,
                    )
                ).fetchone()
                edges = await (
                    await db.execute(
                        f"SELECT COUNT(DISTINCT ge.edge_id) FROM graph_entries ge WHERE {ownership}",
                        ownership_params,
                    )
                ).fetchone()
            return {
                "graph_nodes": nodes[0],
                "graph_edges": edges[0],
                "graph_entries": entries[0],
            }
        async with self._read_connect() as db:
            node_cursor = await db.execute("SELECT COUNT(*) FROM graph_nodes")
            edge_cursor = await db.execute("SELECT COUNT(*) FROM graph_edges")
            entry_cursor = await db.execute("SELECT COUNT(*) FROM graph_entries")
            node_count_row = await node_cursor.fetchone()
            edge_count_row = await edge_cursor.fetchone()
            entry_count_row = await entry_cursor.fetchone()
        return {
            "graph_nodes": int(node_count_row[0]) if node_count_row else 0,
            "graph_edges": int(edge_count_row[0]) if edge_count_row else 0,
            "graph_entries": int(entry_count_row[0]) if entry_count_row else 0,
        }

    # ==================== 图快照 ====================

    async def get_subgraph_for_memories(
        self,
        memory_ids: list[int],
        limit_entries: int = 36,
        limit_nodes: int = 48,
        limit_edges: int = 72,
    ) -> dict[str, Any]:
        """Return a compact graph snapshot for the provided memory identifiers."""
        normalized_memory_ids = self._normalize_snapshot_ids(memory_ids)
        if not normalized_memory_ids:
            return {"nodes": [], "edges": [], "entries": [], "memories": []}

        limit_entries = max(1, min(limit_entries, 400))
        limit_nodes = max(1, min(limit_nodes, 200))
        limit_edges = max(1, min(limit_edges, 400))

        entry_rows, node_rows, edge_rows = await self._read_snapshot_rows(
            normalized_memory_ids, limit_entries, limit_edges
        )
        if not entry_rows:
            return {"nodes": [], "edges": [], "entries": [], "memories": []}

        node_map, entries, edges, memory_base = self._build_snapshot_graph(
            node_rows, entry_rows, edge_rows
        )
        node_map, entries, edges, nodes_were_limited = self._limit_snapshot_nodes(
            node_map, entries, edges, limit_nodes
        )
        memories = self._build_snapshot_memories(
            memory_base, entries, edges, nodes_were_limited
        )

        nodes = sorted(
            node_map.values(),
            key=lambda item: (
                -safe_float(item.get("weight"), 0.0),
                -int(item.get("entry_count", 0)),
                -int(item.get("degree", 0)),
                str(item.get("label", "")),
            ),
        )
        memories.sort(
            key=lambda item: (
                -int(item.get("entry_count", 0)),
                -int(item.get("node_count", 0)),
                -int(item.get("edge_count", 0)),
                -safe_float(item.get("importance"), 0.0),
            )
        )

        return {
            "nodes": nodes,
            "edges": edges,
            "entries": entries,
            "memories": memories,
        }

    @staticmethod
    def _normalize_snapshot_ids(memory_ids: list[int]) -> list[int]:
        """去重并转成整数，非法 ID 直接跳过。"""
        normalized_memory_ids: list[int] = []
        seen_memory_ids: set[int] = set()
        for memory_id in memory_ids:
            try:
                normalized = int(memory_id)
            except (TypeError, ValueError):
                continue
            if normalized in seen_memory_ids:
                continue
            seen_memory_ids.add(normalized)
            normalized_memory_ids.append(normalized)
        return normalized_memory_ids

    async def _read_snapshot_rows(
        self,
        normalized_memory_ids: list[int],
        limit_entries: int,
        limit_edges: int,
    ) -> tuple[list[aiosqlite.Row], list[aiosqlite.Row], list[aiosqlite.Row]]:
        """一次连接内读取 entry / node / edge 三批快照行。"""
        ownership, ownership_params = adapter_sql("metadata")
        memory_placeholders = ",".join("?" * len(normalized_memory_ids))
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            entry_cursor = await db.execute(
                f"""
                SELECT id, source_memory_id, session_id, persona_id,
                       entry_type, relation_type, content, metadata, edge_id
                FROM graph_entries
                WHERE source_memory_id IN ({memory_placeholders}) AND {ownership}
                ORDER BY id DESC
                LIMIT ?
                """,
                (*normalized_memory_ids, *ownership_params, limit_entries),
            )
            entry_rows = await entry_cursor.fetchall()

            if not entry_rows:
                return [], [], []

            entry_ids = [int(row["id"]) for row in entry_rows]
            entry_placeholders = ",".join("?" * len(entry_ids))
            node_cursor = await db.execute(
                f"""
                SELECT gen.entry_id,
                       gn.id AS node_id,
                       gn.node_key,
                       gn.node_type,
                       gn.node_value,
                       gn.canonical_value,
                       gn.metadata
                FROM graph_entry_nodes gen
                JOIN graph_nodes gn ON gn.id = gen.node_id
                WHERE gen.entry_id IN ({entry_placeholders})
                ORDER BY gn.id ASC
                """,
                tuple(entry_ids),
            )
            node_rows = await node_cursor.fetchall()

            node_ids = sorted({int(row["node_id"]) for row in node_rows})
            edge_rows: list[aiosqlite.Row] = []
            if node_ids:
                node_placeholders = ",".join("?" * len(node_ids))
                edge_cursor = await db.execute(
                    f"""
                    SELECT id, edge_key, source_node_id, target_node_id,
                           relation_type, source_memory_id, weight,
                           confidence, status, metadata
                    FROM graph_edges
                    WHERE source_memory_id IN ({memory_placeholders})
                      AND source_node_id IN ({node_placeholders})
                      AND target_node_id IN ({node_placeholders})
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (
                        *normalized_memory_ids,
                        *node_ids,
                        *node_ids,
                        limit_edges,
                    ),
                )
                edge_rows = await edge_cursor.fetchall()

        return entry_rows, node_rows, edge_rows

    @staticmethod
    def _build_snapshot_graph(
        node_rows: list[aiosqlite.Row],
        entry_rows: list[aiosqlite.Row],
        edge_rows: list[aiosqlite.Row],
    ) -> tuple[
        dict[int, dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        dict[int, dict[str, Any]],
    ]:
        """把快照行组装成节点/条目/边和按记忆聚合的基础视图。"""
        entry_node_map: dict[int, list[int]] = {}
        node_map: dict[int, dict[str, Any]] = {}
        memory_base: dict[int, dict[str, Any]] = {}

        for row in node_rows:
            entry_id = int(row["entry_id"])
            node_id = int(row["node_id"])
            entry_node_map.setdefault(entry_id, []).append(node_id)
            if node_id not in node_map:
                node_map[node_id] = {
                    "id": node_id,
                    "key": row["node_key"],
                    "type": row["node_type"],
                    "label": row["node_value"],
                    "canonical_value": row["canonical_value"],
                    "metadata": safe_json_dict(row["metadata"]),
                    "entry_count": 0,
                    "memory_count": 0,
                    "degree": 0,
                    "weight": 0.0,
                    "_memory_ids": set(),
                }

        entries: list[dict[str, Any]] = []
        for row in entry_rows:
            entry_id = int(row["id"])
            memory_id = int(row["source_memory_id"])
            metadata = safe_json_dict(row["metadata"])
            node_ids_for_entry = list(dict.fromkeys(entry_node_map.get(entry_id, [])))

            entries.append(
                {
                    "id": entry_id,
                    "memory_id": memory_id,
                    "entry_type": row["entry_type"],
                    "relation_type": row["relation_type"],
                    "content": row["content"],
                    "metadata": metadata,
                    "session_id": row["session_id"],
                    "persona_id": row["persona_id"],
                    "edge_id": int(row["edge_id"]) if row["edge_id"] else None,
                    "node_ids": node_ids_for_entry,
                }
            )

            base = memory_base.setdefault(
                memory_id,
                {
                    "memory_id": memory_id,
                    "summary": metadata.get("canonical_summary") or row["content"],
                    "session_id": metadata.get("session_id") or row["session_id"],
                    "persona_id": metadata.get("persona_id") or row["persona_id"],
                    "importance": safe_float(metadata.get("importance"), 0.0),
                    "entry_count": 0,
                    "edge_count": 0,
                    "node_ids": set(),
                    "entry_types": set(),
                },
            )
            base["entry_count"] += 1
            base["entry_types"].add(row["entry_type"])
            base["node_ids"].update(node_ids_for_entry)

            for node_id in node_ids_for_entry:
                node = node_map.get(node_id)
                if node is None:
                    continue
                node["entry_count"] += 1
                node["_memory_ids"].add(memory_id)

        edges: list[dict[str, Any]] = []
        for row in edge_rows:
            source_node_id = int(row["source_node_id"])
            target_node_id = int(row["target_node_id"])
            edge = {
                "id": int(row["id"]),
                "key": row["edge_key"],
                "source": source_node_id,
                "target": target_node_id,
                "relation_type": row["relation_type"],
                "memory_id": int(row["source_memory_id"]),
                "weight": float(row["weight"]),
                "confidence": float(row["confidence"]),
                "status": row["status"],
                "metadata": safe_json_dict(row["metadata"]),
            }
            edges.append(edge)

            if source_node_id in node_map:
                node_map[source_node_id]["degree"] += 1
            if target_node_id in node_map:
                node_map[target_node_id]["degree"] += 1
            if edge["memory_id"] in memory_base:
                memory_base[edge["memory_id"]]["edge_count"] += 1

        for node in node_map.values():
            memory_ids_for_node = node.pop("_memory_ids", set())
            node["memory_count"] = len(memory_ids_for_node)
            node["weight"] = round(
                node["entry_count"]
                + node["memory_count"] * 0.75
                + node["degree"] * 0.35,
                4,
            )

        return node_map, entries, edges, memory_base

    @staticmethod
    def _limit_snapshot_nodes(
        node_map: dict[int, dict[str, Any]],
        entries: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        limit_nodes: int,
    ) -> tuple[
        dict[int, dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        bool,
    ]:
        """节点数超限时按权重截断，并同步裁掉条目与边。"""
        nodes_were_limited = len(node_map) > limit_nodes
        if not nodes_were_limited:
            return node_map, entries, edges, False

        ranked_nodes = sorted(
            node_map.values(),
            key=lambda item: (
                -safe_float(item.get("weight"), 0.0),
                -int(item.get("entry_count", 0)),
                -int(item.get("degree", 0)),
                str(item.get("label", "")),
            ),
        )
        allowed_node_ids = {node["id"] for node in ranked_nodes[:limit_nodes]}
        node_map = {
            node_id: node
            for node_id, node in node_map.items()
            if node_id in allowed_node_ids
        }
        edges = [
            edge
            for edge in edges
            if edge["source"] in allowed_node_ids and edge["target"] in allowed_node_ids
        ]
        filtered_entries: list[dict[str, Any]] = []
        for entry in entries:
            entry["node_ids"] = [
                node_id for node_id in entry["node_ids"] if node_id in allowed_node_ids
            ]
            if entry["node_ids"] or entry["entry_type"] == "summary":
                filtered_entries.append(entry)
        return node_map, filtered_entries, edges, True

    @staticmethod
    def _build_snapshot_memories(
        memory_base: dict[int, dict[str, Any]],
        entries: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        nodes_were_limited: bool,
    ) -> list[dict[str, Any]]:
        """按（可选的）截断后条目/边重建记忆视图。"""
        memory_view: dict[int, dict[str, Any]] = {}
        for memory_id, base in memory_base.items():
            memory_view[memory_id] = {
                "memory_id": memory_id,
                "summary": base["summary"],
                "session_id": base["session_id"],
                "persona_id": base["persona_id"],
                "importance": base["importance"],
                "entry_count": base["entry_count"],
                "edge_count": base["edge_count"],
                "node_ids": set(base["node_ids"]),
                "entry_types": set(base["entry_types"]),
            }

        if nodes_were_limited:
            filtered_memory_map = {
                memory_id: {
                    **base,
                    "entry_count": 0,
                    "edge_count": 0,
                    "node_ids": set(),
                    "entry_types": set(),
                }
                for memory_id, base in memory_view.items()
            }
            for entry in entries:
                memory = filtered_memory_map.get(entry["memory_id"])
                if memory is None:
                    continue
                memory["entry_count"] += 1
                memory["node_ids"].update(entry["node_ids"])
                memory["entry_types"].add(entry["entry_type"])
            for edge in edges:
                memory = filtered_memory_map.get(edge["memory_id"])
                if memory is not None:
                    memory["edge_count"] += 1
        else:
            filtered_memory_map = memory_view

        memories: list[dict[str, Any]] = []
        for memory in filtered_memory_map.values():
            if memory["entry_count"] == 0 and memory["edge_count"] == 0:
                continue
            node_ids_for_memory = memory.pop("node_ids")
            entry_types = memory.pop("entry_types")
            memory["node_count"] = len(node_ids_for_memory)
            memory["entry_types"] = sorted(entry_types)
            memories.append(memory)
        return memories

    async def get_graph_snapshot(
        self,
        session_id: str | None = None,
        persona_id: str | None = None,
        limit_memories: int = 12,
        limit_entries: int = 36,
        limit_nodes: int = 48,
        limit_edges: int = 72,
    ) -> dict[str, Any]:
        """Return a recent graph snapshot for overview screens."""
        memory_ids = await self.get_recent_memory_ids(
            limit=limit_memories,
            session_id=session_id,
            persona_id=persona_id,
        )
        return await self.get_subgraph_for_memories(
            memory_ids,
            limit_entries=limit_entries,
            limit_nodes=limit_nodes,
            limit_edges=limit_edges,
        )

    async def get_full_graph_snapshot(
        self,
        session_id: str | None = None,
        persona_id: str | None = None,
        max_nodes: int = 5000,
        max_edges: int = 20000,
        max_memories: int | None = None,
    ) -> dict[str, Any]:
        """Return the scoped graph and memory-node associations under hard caps.

        Archived memories are skipped when the shared database also holds the
        engine's ``documents`` table; graph-only databases keep every entry.

        Args:
            session_id: Optional session scope.
            persona_id: Optional persona scope.
            max_nodes: Maximum nodes kept, most active first.
            max_edges: Maximum active relations kept, heaviest first.
            max_memories: Maximum memories kept; defaults to ``max_nodes``.

        Returns:
            Nodes, relations and memories with their associated node IDs.
        """
        if max_memories is None:
            max_memories = max_nodes

        filters: list[str] = []
        params: list[Any] = []
        if session_id is not None:
            filters.append("ge.session_id = ?")
            params.append(session_id)
        if persona_id is not None:
            filters.append("ge.persona_id = ?")
            params.append(persona_id)
        ownership, ownership_params = adapter_sql("ge.metadata")
        filters.append(ownership)
        params.extend(ownership_params)

        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            # Graph and documents share one SQLite file in the engine; standalone
            # graph stores (shadow stores, tests) never create the documents table.
            probe = await db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents'"
            )
            documents_available = await probe.fetchone() is not None

            entry_where = f"WHERE {' AND '.join(filters)}"
            edge_conditions = [
                "EXISTS (SELECT 1 FROM graph_entries ge "
                "WHERE ge.source_memory_id = edge.source_memory_id "
                f"AND {' AND '.join(filters)})",
                "edge.status = 'active'",
            ]
            if documents_available:
                active_memory_exists = (
                    "EXISTS (SELECT 1 FROM documents d WHERE d.id = {} "
                    "AND COALESCE(json_extract(d.metadata, '$.status'), 'active') "
                    "= 'active')"
                )
                entry_where += (
                    f" AND {active_memory_exists.format('ge.source_memory_id')}"
                )
                edge_conditions.append(
                    active_memory_exists.format("edge.source_memory_id")
                )
            edge_where = " AND ".join(edge_conditions)

            node_cursor = await db.execute(
                f"""
                SELECT gn.id, gn.node_key, gn.node_type, gn.node_value,
                       gn.canonical_value, gn.metadata,
                       COUNT(ge.id) AS entry_count,
                       GROUP_CONCAT(DISTINCT ge.source_memory_id) AS memory_ids
                FROM graph_nodes gn
                JOIN graph_entry_nodes gen ON gen.node_id = gn.id
                JOIN graph_entries ge ON ge.id = gen.entry_id
                {entry_where}
                GROUP BY gn.id
                ORDER BY COUNT(ge.id) DESC, gn.id ASC
                LIMIT ?
                """,
                (*params, max_nodes),
            )
            node_rows = await node_cursor.fetchall()

            edge_cursor = await db.execute(
                f"""
                SELECT edge.id, edge.source_node_id, edge.target_node_id,
                       edge.relation_type, edge.source_memory_id, edge.weight,
                       edge.confidence
                FROM graph_edges edge
                WHERE {edge_where}
                ORDER BY edge.weight DESC, edge.id ASC
                LIMIT ?
                """,
                (*params, max_edges),
            )
            edge_rows = await edge_cursor.fetchall()

            memory_cursor = await db.execute(
                f"""
                SELECT ge.source_memory_id, ge.session_id, ge.persona_id,
                       ge.content, ge.metadata
                FROM graph_entries ge
                JOIN (
                    SELECT ge.source_memory_id, MAX(ge.id) AS latest_entry_id
                    FROM graph_entries ge
                    {entry_where}
                    GROUP BY ge.source_memory_id
                ) latest ON latest.latest_entry_id = ge.id
                ORDER BY ge.source_memory_id ASC
                """,
                tuple(params),
            )
            memory_rows = await memory_cursor.fetchall()

        node_map: dict[int, dict[str, Any]] = {}
        memory_node_ids: dict[int, list[int]] = {}
        for row in node_rows:
            node_id = int(row["id"])
            memory_ids = row["memory_ids"].split(",")
            node_map[node_id] = {
                "id": node_id,
                "key": row["node_key"],
                "type": row["node_type"],
                "label": row["node_value"],
                "canonical_value": row["canonical_value"],
                "metadata": safe_json_dict(row["metadata"]),
                "entry_count": int(row["entry_count"] or 0),
                "memory_count": len(memory_ids),
                "degree": 0,
                "weight": 0.0,
            }
            for memory_id in memory_ids:
                memory_node_ids.setdefault(int(memory_id), []).append(node_id)

        edges: list[dict[str, Any]] = []
        for row in edge_rows:
            source = int(row["source_node_id"])
            target = int(row["target_node_id"])
            if source not in node_map or target not in node_map:
                continue
            edges.append(
                {
                    "id": int(row["id"]),
                    "source": source,
                    "target": target,
                    "relation_type": row["relation_type"],
                    "memory_id": int(row["source_memory_id"]),
                    "weight": float(row["weight"]),
                    "confidence": float(row["confidence"]),
                }
            )
            node_map[source]["degree"] += 1
            node_map[target]["degree"] += 1

        for node in node_map.values():
            node["weight"] = round(
                node["entry_count"]
                + node["memory_count"] * 0.75
                + node["degree"] * 0.35,
                4,
            )

        referenced_memory_ids = set(memory_node_ids)
        referenced_memory_ids.update(edge["memory_id"] for edge in edges)
        memories: list[dict[str, Any]] = []
        for row in memory_rows:
            memory_id = int(row["source_memory_id"])
            if memory_id not in referenced_memory_ids:
                continue
            if len(memories) >= max_memories:
                break
            metadata = safe_json_dict(row["metadata"])
            summary = str(metadata.get("canonical_summary") or row["content"] or "")[
                :500
            ]
            memories.append(
                {
                    "memory_id": memory_id,
                    "summary": summary,
                    "session_id": metadata.get("session_id") or row["session_id"],
                    "persona_id": metadata.get("persona_id") or row["persona_id"],
                    "importance": safe_float(metadata.get("importance"), 0.0),
                    "node_ids": sorted(
                        node_id
                        for node_id in memory_node_ids.get(memory_id, [])
                        if node_id in node_map
                    ),
                }
            )

        nodes = sorted(
            node_map.values(),
            key=lambda item: (
                -safe_float(item.get("weight"), 0.0),
                -int(item.get("degree", 0)),
                str(item.get("label", "")),
            ),
        )
        return {
            "nodes": nodes,
            "edges": edges,
            "entries": [],
            "memories": memories,
        }

    # ==================== 最近记忆 ====================

    async def get_recent_memory_ids(
        self,
        limit: int = 12,
        session_id: str | None = None,
        persona_id: str | None = None,
    ) -> list[int]:
        """Return recently updated memory identifiers represented in the graph."""
        limit = max(1, min(limit, 200))
        filters: list[str] = []
        params: list[Any] = []

        if session_id is not None:
            filters.append("session_id = ?")
            params.append(session_id)
        if persona_id is not None:
            filters.append("persona_id = ?")
            params.append(persona_id)
        ownership, ownership_params = adapter_sql("metadata")
        filters.append(ownership)
        params.extend(ownership_params)

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                f"""
                SELECT source_memory_id, MAX(id) AS latest_entry_id
                FROM graph_entries
                {where_clause}
                GROUP BY source_memory_id
                ORDER BY latest_entry_id DESC
                LIMIT ?
                """,
                (*params, limit),
            )
            rows = await cursor.fetchall()

        return [int(row["source_memory_id"]) for row in rows]
