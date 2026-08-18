"""
GraphStore 的 GraphStoreReadMixin 拆分模块
自动从 storage/graph_store.py 拆分，保持行为不变
"""
from __future__ import annotations

import aiosqlite
from astrbot.api import logger
from typing import Any


class GraphStoreReadMixin:
    """GraphStore 拆分模块：GraphStoreReadMixin"""
    async def list_vector_doc_ids(self) -> list[int]:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT DISTINCT vector_doc_id FROM graph_entries "
                "WHERE vector_doc_id IS NOT NULL"
            )
            return [int(row[0]) for row in await cursor.fetchall()]

    async def list_vector_doc_ids_by_source(self) -> dict[int, list[int]]:
        async with self._connect() as db:
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
        async with self._connect() as db:
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
                        (int(entry_id), str(content or ""), self._from_json(metadata))
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
        if session_id is not None:
            filters.append("ge.session_id = ?")
            params.append(session_id)
        if persona_id is not None:
            filters.append("ge.persona_id = ?")
            params.append(persona_id)

        where_clause = f"AND {' AND '.join(filters)}" if filters else ""

        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
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
            metadata = self._from_json(row["metadata"])
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

        rows_by_id: dict[int, aiosqlite.Row] = {}
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            for start in range(
                0, len(normalized_tokens), self._NODE_TOKEN_QUERY_BATCH_SIZE
            ):
                batch = normalized_tokens[
                    start : start + self._NODE_TOKEN_QUERY_BATCH_SIZE
                ]
                clauses = ["canonical_value LIKE ?" for _ in batch]
                params = [f"%{token}%" for token in batch]
                cursor = await db.execute(
                    f"""
                    SELECT id, node_key, node_type, node_value, canonical_value, metadata
                    FROM graph_nodes
                    WHERE {" OR ".join(clauses)}
                    ORDER BY LENGTH(canonical_value) ASC
                    LIMIT ?
                    """,
                    (*params, limit),
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
                "metadata": self._from_json(row["metadata"]),
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

        if session_id is not None:
            filters.append("ge.session_id = ?")
            params.append(session_id)
        if persona_id is not None:
            filters.append("ge.persona_id = ?")
            params.append(persona_id)
        where_clause = f"AND {' AND '.join(filters)}" if filters else ""

        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                f"""
                SELECT ge.id, ge.source_memory_id, ge.content, ge.metadata,
                       ge.entry_type, ge.relation_type, COUNT(DISTINCT gen.node_id) AS hit_count
                FROM graph_entry_nodes gen
                JOIN graph_entries ge ON ge.id = gen.entry_id
                WHERE gen.node_id IN ({placeholders}) {where_clause}
                GROUP BY ge.id
                ORDER BY hit_count DESC, ge.id DESC
                LIMIT ?
                """,
                (*params, limit),
            )
            rows = await cursor.fetchall()

        hits: list[dict[str, Any]] = []
        for row in rows:
            metadata = self._from_json(row["metadata"])
            hits.append(
                {
                    "entry_id": int(row["id"]),
                    "source_memory_id": int(row["source_memory_id"]),
                    "content": row["content"],
                    "metadata": metadata,
                    "entry_type": row["entry_type"],
                    "relation_type": row["relation_type"],
                    "score": min(1.0, 0.35 + 0.15 * int(row["hit_count"])),
                    "hit_count": int(row["hit_count"]),
                }
            )
        return hits

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

        async with self._connect() as db:
            cursor = await db.execute(
                f"""
                SELECT neighbor_id, SUM(edge_weight) AS total_weight
                FROM (
                    SELECT target_node_id AS neighbor_id, weight AS edge_weight
                    FROM graph_edges
                    WHERE source_node_id IN ({placeholders})
                      AND status = 'active'
                    UNION ALL
                    SELECT source_node_id AS neighbor_id, weight AS edge_weight
                    FROM graph_edges
                    WHERE target_node_id IN ({placeholders})
                      AND status = 'active'
                )
                WHERE neighbor_id NOT IN ({placeholders})
                GROUP BY neighbor_id
                ORDER BY total_weight DESC, neighbor_id ASC
                LIMIT ?
                """,
                (*normalized_ids, *normalized_ids, *normalized_ids, limit),
            )
            rows = await cursor.fetchall()

        return [int(row[0]) for row in rows]

    async def get_memory_entry_stats(self) -> dict[str, int]:
        """Return graph storage counts for status reporting."""
        async with self._connect() as db:
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
