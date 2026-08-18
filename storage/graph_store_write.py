"""
GraphStore 的 GraphStoreWriteMixin 拆分模块
自动从 storage/graph_store.py 拆分，保持行为不变
"""
from __future__ import annotations

import aiosqlite
from typing import Any
from ..core.models.graph_models import GraphEdge, GraphEntry, GraphNode


class GraphStoreWriteMixin:
    """GraphStore 拆分模块：GraphStoreWriteMixin"""
    async def initialize(self) -> None:
        """Create tables used by the graph-memory layer."""
        async with self._connect() as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_nodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_key TEXT NOT NULL UNIQUE,
                    node_type TEXT NOT NULL,
                    node_value TEXT NOT NULL,
                    canonical_value TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_edges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    edge_key TEXT NOT NULL UNIQUE,
                    source_node_id INTEGER NOT NULL,
                    target_node_id INTEGER NOT NULL,
                    relation_type TEXT NOT NULL,
                    source_memory_id INTEGER NOT NULL,
                    weight REAL NOT NULL DEFAULT 1.0,
                    confidence REAL NOT NULL DEFAULT 0.8,
                    status TEXT NOT NULL DEFAULT 'active',
                    metadata TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(source_node_id) REFERENCES graph_nodes(id) ON DELETE CASCADE,
                    FOREIGN KEY(target_node_id) REFERENCES graph_nodes(id) ON DELETE CASCADE
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_key TEXT NOT NULL UNIQUE,
                    source_memory_id INTEGER NOT NULL,
                    session_id TEXT,
                    persona_id TEXT,
                    entry_type TEXT NOT NULL,
                    relation_type TEXT,
                    content TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}',
                    edge_id INTEGER,
                    vector_doc_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(edge_id) REFERENCES graph_edges(id) ON DELETE CASCADE
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_entry_nodes (
                    entry_id INTEGER NOT NULL,
                    node_id INTEGER NOT NULL,
                    PRIMARY KEY(entry_id, node_id),
                    FOREIGN KEY(entry_id) REFERENCES graph_entries(id) ON DELETE CASCADE,
                    FOREIGN KEY(node_id) REFERENCES graph_nodes(id) ON DELETE CASCADE
                )
                """
            )
            await db.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS livingmemory_graph_entries_fts
                USING fts5(content, entry_id UNINDEXED, tokenize='unicode61')
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_graph_nodes_canonical ON graph_nodes(canonical_value)"
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_graph_edges_semantic
                ON graph_edges(source_node_id, target_node_id, relation_type)
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_graph_edges_memory_id ON graph_edges(source_memory_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_graph_entries_memory_id ON graph_entries(source_memory_id)"
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_graph_entries_scope_latest
                ON graph_entries(session_id, persona_id, source_memory_id, id DESC)
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_graph_entries_session_id ON graph_entries(session_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_graph_entries_persona_id ON graph_entries(persona_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_graph_entry_nodes_node ON graph_entry_nodes(node_id)"
            )
            await db.commit()

    async def upsert_node(self, node: GraphNode) -> int:
        """Insert or update one graph node and return its identifier."""
        now = self._now_iso()
        async with self._connect() as db:
            node_id = await self._upsert_node(db, node, now)
            await db.commit()
            return node_id

    async def upsert_nodes(self, nodes: list[GraphNode]) -> dict[str, int]:
        """Insert or update nodes in one transaction."""
        if not nodes:
            return {}

        now = self._now_iso()
        node_key_to_id: dict[str, int] = {}
        async with self._connect() as db:
            for node in nodes:
                node_key_to_id[node.node_key] = await self._upsert_node(db, node, now)
            await db.commit()
        return node_key_to_id

    async def _upsert_node(
        self,
        db: aiosqlite.Connection,
        node: GraphNode,
        now: str,
    ) -> int:
        cursor = await db.execute(
            """
            INSERT INTO graph_nodes(
                node_key, node_type, node_value, canonical_value,
                metadata, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(node_key) DO UPDATE SET
                node_value = excluded.node_value,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at
            """,
            (
                node.node_key,
                node.node_type,
                node.value,
                node.canonical_value,
                self._to_json(node.metadata),
                now,
                now,
            ),
        )
        cursor = await db.execute(
            "SELECT id FROM graph_nodes WHERE node_key = ?",
            (node.node_key,),
        )
        row = await cursor.fetchone()
        return int(row[0])

    async def add_edge(
        self,
        edge: GraphEdge,
        node_key_to_id: dict[str, int],
    ) -> int:
        """Insert or update one graph edge and return its identifier.

        Uses semantic_edge_key for cross-memory merging:
        when the same semantic edge already exists (from a different memory),
        confidence is updated via EMA and weight accumulates evidence.
        """
        source_node_id = node_key_to_id[edge.source_key]
        target_node_id = node_key_to_id[edge.target_key]
        now = self._now_iso()
        async with self._connect() as db:
            edge_id = await self._add_edge(
                db,
                edge,
                source_node_id,
                target_node_id,
                now,
            )
            await db.commit()
            return edge_id

    async def add_edges(
        self,
        edges: list[GraphEdge],
        node_key_to_id: dict[str, int],
    ) -> dict[str, int]:
        """Insert or update edges in one transaction."""
        if not edges:
            return {}

        now = self._now_iso()
        edge_key_to_id: dict[str, int] = {}
        async with self._connect() as db:
            for edge in edges:
                source_node_id = node_key_to_id.get(edge.source_key)
                target_node_id = node_key_to_id.get(edge.target_key)
                if source_node_id is None or target_node_id is None:
                    continue
                edge_key_to_id[edge.edge_key] = await self._add_edge(
                    db,
                    edge,
                    source_node_id,
                    target_node_id,
                    now,
                )
            await db.commit()
        return edge_key_to_id

    async def _add_edge(
        self,
        db: aiosqlite.Connection,
        edge: GraphEdge,
        source_node_id: int,
        target_node_id: int,
        now: str,
    ) -> int:
        # Exact key match first (same memory, same edge)
        cursor = await db.execute(
            "SELECT id FROM graph_edges WHERE edge_key = ?",
            (edge.edge_key,),
        )
        row = await cursor.fetchone()
        if row:
            await db.execute(
                """
                UPDATE graph_edges
                SET weight = ?, confidence = ?, status = ?, metadata = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    edge.weight,
                    edge.confidence,
                    edge.status,
                    self._to_json(edge.metadata),
                    now,
                    row[0],
                ),
            )
            return int(row[0])

        # Cross-memory semantic merge: find same relation between same nodes.
        semantic_cursor = await db.execute(
            """
            SELECT id, confidence, weight FROM graph_edges
            WHERE source_node_id = ? AND target_node_id = ?
              AND relation_type = ?
            ORDER BY id ASC LIMIT 1
            """,
            (source_node_id, target_node_id, edge.relation_type),
        )
        semantic_row = await semantic_cursor.fetchone()

        if semantic_row:
            existing_id = int(semantic_row[0])
            old_conf = float(semantic_row[1] or 0.8)
            old_weight = float(semantic_row[2] or 1.0)
            merged_confidence = old_conf * 0.7 + edge.confidence * 0.3
            merged_weight = old_weight + edge.weight * 0.15
            await db.execute(
                """
                UPDATE graph_edges
                SET confidence = ?, weight = ?, updated_at = ?
                WHERE id = ?
                """,
                (merged_confidence, merged_weight, now, existing_id),
            )
            return existing_id

        cursor = await db.execute(
            """
            INSERT INTO graph_edges(
                edge_key, source_node_id, target_node_id, relation_type,
                source_memory_id, weight, confidence, status,
                metadata, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(edge_key) DO UPDATE SET
                weight = excluded.weight,
                confidence = excluded.confidence,
                status = excluded.status,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at
            """,
            (
                edge.edge_key,
                source_node_id,
                target_node_id,
                edge.relation_type,
                edge.source_memory_id,
                edge.weight,
                edge.confidence,
                edge.status,
                self._to_json(edge.metadata),
                now,
                now,
            ),
        )
        cursor = await db.execute(
            "SELECT id FROM graph_edges WHERE edge_key = ?",
            (edge.edge_key,),
        )
        row = await cursor.fetchone()
        return int(row[0])

    async def add_entry(
        self,
        entry: GraphEntry,
        node_key_to_id: dict[str, int],
        edge_id: int | None = None,
    ) -> int:
        """Insert or update a searchable graph entry."""
        now = self._now_iso()
        async with self._connect() as db:
            entry_id = await self._add_entry(db, entry, node_key_to_id, edge_id, now)
            await db.commit()
            return entry_id

    async def add_entries(
        self,
        entries: list[GraphEntry],
        node_key_to_id: dict[str, int],
        edge_key_to_id: dict[str, int],
    ) -> list[int]:
        """Insert or update searchable graph entries in one transaction."""
        if not entries:
            return []

        now = self._now_iso()
        entry_ids: list[int] = []
        async with self._connect() as db:
            for entry in entries:
                edge_id = None
                if entry.relation_type and len(entry.node_keys) >= 2:
                    edge_key = (
                        f"{entry.node_keys[0]}|{entry.relation_type}|"
                        f"{entry.node_keys[1]}|{entry.source_memory_id}"
                    )
                    edge_id = edge_key_to_id.get(edge_key)
                entry_ids.append(
                    await self._add_entry(db, entry, node_key_to_id, edge_id, now)
                )
            await db.commit()
        return entry_ids

    async def _add_entry(
        self,
        db: aiosqlite.Connection,
        entry: GraphEntry,
        node_key_to_id: dict[str, int],
        edge_id: int | None,
        now: str,
    ) -> int:
        cursor = await db.execute(
            "SELECT id FROM graph_entries WHERE entry_key = ?",
            (entry.entry_key,),
        )
        row = await cursor.fetchone()

        if row:
            entry_id = int(row[0])
            await db.execute(
                """
                UPDATE graph_entries
                SET session_id = ?, persona_id = ?, entry_type = ?, relation_type = ?,
                    content = ?, metadata = ?, edge_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    entry.session_id,
                    entry.persona_id,
                    entry.entry_type,
                    entry.relation_type,
                    entry.content,
                    self._to_json(entry.metadata),
                    edge_id,
                    now,
                    entry_id,
                ),
            )
            await db.execute(
                "DELETE FROM livingmemory_graph_entries_fts WHERE entry_id = ?",
                (entry_id,),
            )
            await db.execute(
                "DELETE FROM graph_entry_nodes WHERE entry_id = ?",
                (entry_id,),
            )
        else:
            cursor = await db.execute(
                """
                INSERT INTO graph_entries(
                    entry_key, source_memory_id, session_id, persona_id,
                    entry_type, relation_type, content, metadata,
                    edge_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.entry_key,
                    entry.source_memory_id,
                    entry.session_id,
                    entry.persona_id,
                    entry.entry_type,
                    entry.relation_type,
                    entry.content,
                    self._to_json(entry.metadata),
                    edge_id,
                    now,
                    now,
                ),
            )
            entry_id = int(cursor.lastrowid)

        await db.execute(
            "INSERT INTO livingmemory_graph_entries_fts(entry_id, content) VALUES (?, ?)",
            (entry_id, entry.content),
        )
        entry_node_rows = [
            (entry_id, node_id)
            for node_id in (
                node_key_to_id.get(node_key) for node_key in entry.node_keys
            )
            if node_id is not None
        ]
        if entry_node_rows:
            await db.executemany(
                "INSERT OR IGNORE INTO graph_entry_nodes(entry_id, node_id) VALUES (?, ?)",
                entry_node_rows,
            )
        return entry_id

    async def update_entry_vector_doc_id(
        self, entry_id: int, vector_doc_id: int
    ) -> None:
        """Persist the vector-store identifier for one graph entry."""
        async with self._connect() as db:
            await db.execute(
                "UPDATE graph_entries SET vector_doc_id = ?, updated_at = ? WHERE id = ?",
                (vector_doc_id, self._now_iso(), entry_id),
            )
            await db.commit()

    async def update_entry_vector_doc_ids(
        self,
        entry_vector_doc_ids: dict[int, int],
    ) -> None:
        """Persist vector-store identifiers for graph entries in one transaction."""
        if not entry_vector_doc_ids:
            return

        now = self._now_iso()
        async with self._connect() as db:
            await db.executemany(
                "UPDATE graph_entries SET vector_doc_id = ?, updated_at = ? WHERE id = ?",
                [
                    (vector_doc_id, now, entry_id)
                    for entry_id, vector_doc_id in entry_vector_doc_ids.items()
                ],
            )
            await db.commit()

    async def clear_all(self) -> list[int]:
        """Clear every graph artifact and return referenced vector document IDs."""
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT DISTINCT vector_doc_id FROM graph_entries "
                "WHERE vector_doc_id IS NOT NULL"
            )
            vector_doc_ids = [int(row[0]) for row in await cursor.fetchall()]
            await db.execute("DELETE FROM livingmemory_graph_entries_fts")
            await db.execute("DELETE FROM graph_entry_nodes")
            await db.execute("DELETE FROM graph_entries")
            await db.execute("DELETE FROM graph_edges")
            await db.execute("DELETE FROM graph_nodes")
            await db.commit()
        return vector_doc_ids

    async def replace_all_from(self, shadow_db_path: str) -> None:
        """Atomically replace live graph tables from a fully built shadow DB."""
        async with self._connect() as db:
            await db.execute("PRAGMA foreign_keys = OFF")
            await db.execute("ATTACH DATABASE ? AS shadow_graph", (shadow_db_path,))
            try:
                await db.execute("BEGIN IMMEDIATE")
                await db.execute("DELETE FROM livingmemory_graph_entries_fts")
                await db.execute("DELETE FROM graph_entry_nodes")
                await db.execute("DELETE FROM graph_entries")
                await db.execute("DELETE FROM graph_edges")
                await db.execute("DELETE FROM graph_nodes")

                await db.execute(
                    """
                    INSERT INTO graph_nodes(
                        id, node_key, node_type, node_value, canonical_value,
                        metadata, created_at, updated_at
                    )
                    SELECT id, node_key, node_type, node_value, canonical_value,
                           metadata, created_at, updated_at
                    FROM shadow_graph.graph_nodes
                    """
                )
                await db.execute(
                    """
                    INSERT INTO graph_edges(
                        id, edge_key, source_node_id, target_node_id,
                        relation_type, source_memory_id, weight, confidence,
                        status, metadata, created_at, updated_at
                    )
                    SELECT id, edge_key, source_node_id, target_node_id,
                           relation_type, source_memory_id, weight, confidence,
                           status, metadata, created_at, updated_at
                    FROM shadow_graph.graph_edges
                    """
                )
                await db.execute(
                    """
                    INSERT INTO graph_entries(
                        id, entry_key, source_memory_id, session_id, persona_id,
                        entry_type, relation_type, content, metadata, edge_id,
                        vector_doc_id, created_at, updated_at
                    )
                    SELECT id, entry_key, source_memory_id, session_id, persona_id,
                           entry_type, relation_type, content, metadata, edge_id,
                           vector_doc_id, created_at, updated_at
                    FROM shadow_graph.graph_entries
                    """
                )
                await db.execute(
                    """
                    INSERT INTO graph_entry_nodes(entry_id, node_id)
                    SELECT entry_id, node_id
                    FROM shadow_graph.graph_entry_nodes
                    """
                )
                await db.execute(
                    """
                    INSERT INTO livingmemory_graph_entries_fts(content, entry_id)
                    SELECT content, entry_id
                    FROM shadow_graph.livingmemory_graph_entries_fts
                    """
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
            finally:
                await db.execute("DETACH DATABASE shadow_graph")

    async def delete_memory(self, source_memory_id: int) -> list[int]:
        """Delete graph artifacts belonging to one source memory."""
        vector_doc_ids: list[int] = []
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT id, vector_doc_id FROM graph_entries WHERE source_memory_id = ?",
                (source_memory_id,),
            )
            rows = await cursor.fetchall()
            entry_ids = [int(row[0]) for row in rows]
            vector_doc_ids = [int(row[1]) for row in rows if row[1] is not None]

            if entry_ids:
                placeholders = ",".join("?" * len(entry_ids))
                await db.execute(
                    f"DELETE FROM livingmemory_graph_entries_fts WHERE entry_id IN ({placeholders})",
                    entry_ids,
                )
                await db.execute(
                    f"DELETE FROM graph_entry_nodes WHERE entry_id IN ({placeholders})",
                    entry_ids,
                )
                await db.execute(
                    f"DELETE FROM graph_entries WHERE id IN ({placeholders})",
                    entry_ids,
                )

            await db.execute(
                "DELETE FROM graph_edges WHERE source_memory_id = ?",
                (source_memory_id,),
            )
            await db.execute(
                """
                DELETE FROM graph_nodes
                WHERE id NOT IN (
                    SELECT source_node_id FROM graph_edges
                    UNION
                    SELECT target_node_id FROM graph_edges
                    UNION
                    SELECT node_id FROM graph_entry_nodes
                )
                """
            )
            await db.commit()
        return vector_doc_ids

    async def batch_delete_memories(
        self, source_memory_ids: list[int]
    ) -> dict[int, list[int]]:
        """Batch delete graph artifacts for multiple source memories."""
        result: dict[int, list[int]] = {}
        if not source_memory_ids:
            return result

        normalized_ids = sorted({int(item) for item in source_memory_ids})
        async with self._connect() as db:
            for batch in self._chunked(normalized_ids, self._SQLITE_BATCH_SIZE):
                memory_placeholders = ",".join("?" * len(batch))

                cursor = await db.execute(
                    f"""
                    SELECT id, source_memory_id, vector_doc_id
                    FROM graph_entries
                    WHERE source_memory_id IN ({memory_placeholders})
                    """,
                    batch,
                )
                rows = await cursor.fetchall()
                entry_ids: list[int] = []
                for row in rows:
                    entry_id = int(row[0])
                    memory_id = int(row[1])
                    vector_doc_id = row[2]
                    entry_ids.append(entry_id)
                    if vector_doc_id is not None:
                        result.setdefault(memory_id, []).append(int(vector_doc_id))

                if entry_ids:
                    for entry_batch in self._chunked(
                        entry_ids,
                        self._SQLITE_BATCH_SIZE,
                    ):
                        entry_placeholders = ",".join("?" * len(entry_batch))
                        await db.execute(
                            f"DELETE FROM livingmemory_graph_entries_fts WHERE entry_id IN ({entry_placeholders})",
                            entry_batch,
                        )
                        await db.execute(
                            f"DELETE FROM graph_entry_nodes WHERE entry_id IN ({entry_placeholders})",
                            entry_batch,
                        )
                        await db.execute(
                            f"DELETE FROM graph_entries WHERE id IN ({entry_placeholders})",
                            entry_batch,
                        )

                await db.execute(
                    f"DELETE FROM graph_edges WHERE source_memory_id IN ({memory_placeholders})",
                    batch,
                )

            await db.execute(
                """
                DELETE FROM graph_nodes
                WHERE id NOT IN (
                    SELECT source_node_id FROM graph_edges
                    UNION
                    SELECT target_node_id FROM graph_edges
                    UNION
                    SELECT node_id FROM graph_entry_nodes
                )
                """
            )
            await db.commit()
        return result

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
