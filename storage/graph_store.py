"""SQLite-backed graph-memory storage."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from ..core.models.graph_models import GraphEdge, GraphEntry, GraphNode


class GraphStore:
    """Persist graph nodes, edges, and searchable entries."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _to_json(payload: dict[str, Any] | None) -> str:
        return json.dumps(payload or {}, ensure_ascii=False)

    @staticmethod
    def _from_json(payload: str | dict[str, Any] | None) -> dict[str, Any]:
        if isinstance(payload, dict):
            return payload
        if not payload:
            return {}
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}

    async def initialize(self) -> None:
        """Create tables used by the graph-memory layer."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON")
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
                CREATE VIRTUAL TABLE IF NOT EXISTS graph_entries_fts
                USING fts5(content, entry_id UNINDEXED, tokenize='unicode61')
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_graph_nodes_canonical ON graph_nodes(canonical_value)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_graph_edges_memory_id ON graph_edges(source_memory_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_graph_entries_memory_id ON graph_entries(source_memory_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_graph_entries_session_id ON graph_entries(session_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_graph_entries_persona_id ON graph_entries(persona_id)"
            )
            await db.commit()

    async def upsert_node(self, node: GraphNode) -> int:
        """Insert or update one graph node and return its identifier."""
        now = self._now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT id FROM graph_nodes WHERE node_key = ?",
                (node.node_key,),
            )
            row = await cursor.fetchone()
            if row:
                await db.execute(
                    """
                    UPDATE graph_nodes
                    SET node_value = ?, metadata = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (node.value, self._to_json(node.metadata), now, row[0]),
                )
                await db.commit()
                return int(row[0])

            cursor = await db.execute(
                """
                INSERT INTO graph_nodes(
                    node_key, node_type, node_value, canonical_value,
                    metadata, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
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
            await db.commit()
            return int(cursor.lastrowid)

    async def add_edge(
        self,
        edge: GraphEdge,
        node_key_to_id: dict[str, int],
    ) -> int:
        """Insert or update one graph edge and return its identifier."""
        source_node_id = node_key_to_id[edge.source_key]
        target_node_id = node_key_to_id[edge.target_key]
        now = self._now_iso()
        async with aiosqlite.connect(self.db_path) as db:
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
                await db.commit()
                return int(row[0])

            cursor = await db.execute(
                """
                INSERT INTO graph_edges(
                    edge_key, source_node_id, target_node_id, relation_type,
                    source_memory_id, weight, confidence, status,
                    metadata, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            await db.commit()
            return int(cursor.lastrowid)

    async def add_entry(
        self,
        entry: GraphEntry,
        node_key_to_id: dict[str, int],
        edge_id: int | None = None,
    ) -> int:
        """Insert or update a searchable graph entry."""
        now = self._now_iso()
        async with aiosqlite.connect(self.db_path) as db:
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
                    "DELETE FROM graph_entries_fts WHERE entry_id = ?", (entry_id,)
                )
                await db.execute(
                    "DELETE FROM graph_entry_nodes WHERE entry_id = ?", (entry_id,)
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
                "INSERT INTO graph_entries_fts(entry_id, content) VALUES (?, ?)",
                (entry_id, entry.content),
            )
            for node_key in entry.node_keys:
                node_id = node_key_to_id.get(node_key)
                if node_id is None:
                    continue
                await db.execute(
                    "INSERT OR IGNORE INTO graph_entry_nodes(entry_id, node_id) VALUES (?, ?)",
                    (entry_id, node_id),
                )
            await db.commit()
            return entry_id

    async def update_entry_vector_doc_id(
        self, entry_id: int, vector_doc_id: int
    ) -> None:
        """Persist the vector-store identifier for one graph entry."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE graph_entries SET vector_doc_id = ?, updated_at = ? WHERE id = ?",
                (vector_doc_id, self._now_iso(), entry_id),
            )
            await db.commit()

    async def delete_memory(self, source_memory_id: int) -> list[int]:
        """Delete graph artifacts belonging to one source memory."""
        vector_doc_ids: list[int] = []
        async with aiosqlite.connect(self.db_path) as db:
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
                    f"DELETE FROM graph_entries_fts WHERE entry_id IN ({placeholders})",
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

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                f"""
                SELECT ge.id, ge.source_memory_id, ge.content, ge.metadata,
                       ge.entry_type, ge.relation_type, ge.session_id, ge.persona_id,
                       bm25(graph_entries_fts) AS score
                FROM graph_entries_fts
                JOIN graph_entries ge ON ge.id = graph_entries_fts.entry_id
                WHERE graph_entries_fts MATCH ? {where_clause}
                ORDER BY score ASC
                LIMIT ?
                """,
                (*params, limit),
            )
            rows = await cursor.fetchall()

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
        if not tokens:
            return []
        clauses = ["canonical_value LIKE ?" for _ in tokens]
        params = [f"%{token}%" for token in tokens]
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
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
            rows = await cursor.fetchall()

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

        async with aiosqlite.connect(self.db_path) as db:
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

        async with aiosqlite.connect(self.db_path) as db:
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

    async def get_subgraph_for_memories(
        self,
        memory_ids: list[int],
        limit_entries: int = 36,
        limit_nodes: int = 48,
        limit_edges: int = 72,
    ) -> dict[str, Any]:
        """Return a compact graph snapshot for the provided memory identifiers."""
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

        if not normalized_memory_ids:
            return {"nodes": [], "edges": [], "entries": [], "memories": []}

        limit_entries = max(1, min(limit_entries, 400))
        limit_nodes = max(1, min(limit_nodes, 200))
        limit_edges = max(1, min(limit_edges, 400))

        memory_placeholders = ",".join("?" * len(normalized_memory_ids))

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            entry_cursor = await db.execute(
                f"""
                SELECT id, source_memory_id, session_id, persona_id,
                       entry_type, relation_type, content, metadata, edge_id
                FROM graph_entries
                WHERE source_memory_id IN ({memory_placeholders})
                ORDER BY id DESC
                LIMIT ?
                """,
                (*normalized_memory_ids, limit_entries),
            )
            entry_rows = await entry_cursor.fetchall()

            if not entry_rows:
                return {"nodes": [], "edges": [], "entries": [], "memories": []}

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
                    "metadata": self._from_json(row["metadata"]),
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
            metadata = self._from_json(row["metadata"])
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
                    "importance": float(metadata.get("importance", 0.0) or 0.0),
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
                "metadata": self._from_json(row["metadata"]),
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

        if len(node_map) > limit_nodes:
            ranked_nodes = sorted(
                node_map.values(),
                key=lambda item: (
                    -float(item.get("weight", 0.0)),
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
                if edge["source"] in allowed_node_ids
                and edge["target"] in allowed_node_ids
            ]
            filtered_entries: list[dict[str, Any]] = []
            for entry in entries:
                entry["node_ids"] = [
                    node_id
                    for node_id in entry["node_ids"]
                    if node_id in allowed_node_ids
                ]
                if entry["node_ids"] or entry["entry_type"] == "summary":
                    filtered_entries.append(entry)
            entries = filtered_entries

        filtered_memory_map: dict[int, dict[str, Any]] = {}
        for memory_id, base in memory_base.items():
            filtered_memory_map[memory_id] = {
                "memory_id": memory_id,
                "summary": base["summary"],
                "session_id": base["session_id"],
                "persona_id": base["persona_id"],
                "importance": base["importance"],
                "entry_count": 0,
                "edge_count": 0,
                "node_ids": set(),
                "entry_types": set(),
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

        memories: list[dict[str, Any]] = []
        for memory in filtered_memory_map.values():
            if memory["entry_count"] == 0 and memory["edge_count"] == 0:
                continue
            node_ids_for_memory = memory.pop("node_ids")
            entry_types = memory.pop("entry_types")
            memory["node_count"] = len(node_ids_for_memory)
            memory["entry_types"] = sorted(entry_types)
            memories.append(memory)

        nodes = sorted(
            node_map.values(),
            key=lambda item: (
                -float(item.get("weight", 0.0)),
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
                -float(item.get("importance", 0.0)),
            )
        )

        return {
            "nodes": nodes,
            "edges": edges,
            "entries": entries,
            "memories": memories,
        }

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

    async def get_memory_entry_stats(self) -> dict[str, int]:
        """Return graph storage counts for status reporting."""
        async with aiosqlite.connect(self.db_path) as db:
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


__all__ = ["GraphStore"]
