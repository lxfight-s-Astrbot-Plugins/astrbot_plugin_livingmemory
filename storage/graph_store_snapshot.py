"""
GraphStore 的 GraphStoreSnapshotMixin 拆分模块
自动从 storage/graph_store.py 拆分，保持行为不变
"""
from __future__ import annotations

import aiosqlite
from typing import Any
from ..core.utils.number_utils import safe_float


class GraphStoreSnapshotMixin:
    """GraphStore 拆分模块：GraphStoreSnapshotMixin"""
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

        async with self._connect() as db:
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

        nodes_were_limited = len(node_map) > limit_nodes
        if nodes_were_limited:
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

        if not nodes_were_limited:
            filtered_memory_map = memory_view
        else:
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
    ) -> dict[str, Any]:
        """Return every graph node and edge represented in the selected scope."""
        filters: list[str] = []
        params: list[Any] = []
        if session_id is not None:
            filters.append("ge.session_id = ?")
            params.append(session_id)
        if persona_id is not None:
            filters.append("ge.persona_id = ?")
            params.append(persona_id)
        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            node_cursor = await db.execute(
                f"""
                SELECT gn.id, gn.node_key, gn.node_type, gn.node_value,
                       gn.canonical_value, gn.metadata,
                       COUNT(DISTINCT ge.id) AS entry_count,
                       COUNT(DISTINCT ge.source_memory_id) AS memory_count
                FROM graph_nodes gn
                JOIN graph_entry_nodes gen ON gen.node_id = gn.id
                JOIN graph_entries ge ON ge.id = gen.entry_id
                {where_clause}
                GROUP BY gn.id
                ORDER BY gn.id ASC
                """,
                tuple(params),
            )
            node_rows = await node_cursor.fetchall()

            edge_cursor = await db.execute(
                f"""
                SELECT DISTINCT edge.id, edge.edge_key, edge.source_node_id,
                                edge.target_node_id, edge.relation_type,
                                edge.source_memory_id, edge.weight,
                                edge.confidence, edge.status, edge.metadata
                FROM graph_edges edge
                JOIN graph_entries ge
                  ON ge.source_memory_id = edge.source_memory_id
                {where_clause}
                ORDER BY edge.id ASC
                """,
                tuple(params),
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
                    {where_clause}
                    GROUP BY ge.source_memory_id
                ) latest ON latest.latest_entry_id = ge.id
                ORDER BY ge.source_memory_id ASC
                """,
                tuple(params),
            )
            memory_rows = await memory_cursor.fetchall()

        node_map: dict[int, dict[str, Any]] = {}
        for row in node_rows:
            node_id = int(row["id"])
            node_map[node_id] = {
                "id": node_id,
                "key": row["node_key"],
                "type": row["node_type"],
                "label": row["node_value"],
                "canonical_value": row["canonical_value"],
                "metadata": self._from_json(row["metadata"]),
                "entry_count": int(row["entry_count"] or 0),
                "memory_count": int(row["memory_count"] or 0),
                "degree": 0,
                "weight": 0.0,
            }

        edges: list[dict[str, Any]] = []
        for row in edge_rows:
            source = int(row["source_node_id"])
            target = int(row["target_node_id"])
            if source not in node_map or target not in node_map:
                continue
            edges.append(
                {
                    "id": int(row["id"]),
                    "key": row["edge_key"],
                    "source": source,
                    "target": target,
                    "relation_type": row["relation_type"],
                    "memory_id": int(row["source_memory_id"]),
                    "weight": float(row["weight"]),
                    "confidence": float(row["confidence"]),
                    "status": row["status"],
                    "metadata": self._from_json(row["metadata"]),
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

        memories: list[dict[str, Any]] = []
        for row in memory_rows:
            metadata = self._from_json(row["metadata"])
            summary = str(metadata.get("canonical_summary") or row["content"] or "")[
                :500
            ]
            memories.append(
                {
                    "memory_id": int(row["source_memory_id"]),
                    "summary": summary,
                    "session_id": metadata.get("session_id") or row["session_id"],
                    "persona_id": metadata.get("persona_id") or row["persona_id"],
                    "importance": safe_float(metadata.get("importance"), 0.0),
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
