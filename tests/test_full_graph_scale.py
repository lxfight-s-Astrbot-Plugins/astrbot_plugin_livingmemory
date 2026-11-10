"""Full graph requests preserve the selected scope at display-scale volumes."""

import json

import pytest
from astrbot_plugin_livingmemory.core.models.graph_models import GraphNode
from astrbot_plugin_livingmemory.storage.graph_store import GraphStore

EDGE_FIELDS = {
    "id",
    "source",
    "target",
    "relation_type",
    "memory_id",
    "weight",
    "confidence",
}


async def _seed_full_graph(store: GraphStore) -> None:
    """Seed 5002 nodes, 10002 relations and 2002 node-referenced memories."""
    nodes = [GraphNode("topic", str(i), str(i)) for i in range(1, 5003)]
    async with store._connect() as db:
        await db.executemany(
            "INSERT INTO graph_nodes "
            "(id,node_key,node_type,node_value,canonical_value,created_at,updated_at) "
            "VALUES (?,?,'topic',?,?,'2026-01-01','2026-01-01')",
            [
                (i, node.node_key, node.value, node.canonical_value)
                for i, node in enumerate(nodes, 1)
            ],
        )
        await db.executemany(
            "INSERT INTO graph_entries "
            "(id,entry_key,source_memory_id,session_id,entry_type,content,created_at,updated_at) "
            "VALUES (?,?,?,'selected','summary',?,'2026-01-01','2026-01-01')",
            [(i, f"entry:{i}", i, f"Memory {i}") for i in range(1, 2003)],
        )
        await db.executemany(
            "INSERT INTO graph_entry_nodes (entry_id,node_id) VALUES (1,?)",
            [(i,) for i in range(1, 5003)],
        )
        # Refer every memory from a node so the snapshot keeps the whole set.
        await db.executemany(
            "INSERT INTO graph_entry_nodes (entry_id,node_id) VALUES (?,?)",
            [(i, i) for i in range(2, 2003)],
        )
        await db.executemany(
            "INSERT INTO graph_entries "
            "(entry_key,source_memory_id,session_id,entry_type,content,created_at,updated_at) "
            "VALUES (?,1,'selected','fact','Another entry','2026-01-01','2026-01-01')",
            [("extra:1",), ("extra:2",)],
        )
        await db.executemany(
            "INSERT INTO graph_entry_nodes (entry_id,node_id) VALUES (?,1)",
            [(2003,), (2004,)],
        )
        await db.executemany(
            "INSERT INTO graph_edges "
            "(edge_key,source_node_id,target_node_id,relation_type,source_memory_id,created_at,updated_at) "
            "VALUES (?,?,?,'related',1,'2026-01-01','2026-01-01')",
            [(f"edge:{i}", i % 5002 + 1, (i + 1) % 5002 + 1) for i in range(10002)],
        )
        await db.commit()


@pytest.mark.asyncio
async def test_full_graph_retains_all_nodes_relations_and_memories(tmp_path):
    store = GraphStore(str(tmp_path / "full_graph.db"))
    await store.initialize()
    await _seed_full_graph(store)

    snapshot = await store.get_full_graph_snapshot(session_id="selected")
    assert len(snapshot["nodes"]) == 5002
    assert len(snapshot["edges"]) == 10002
    assert len(snapshot["memories"]) == 2002
    assert all(set(edge) == EDGE_FIELDS for edge in snapshot["edges"])
    assert (
        next(node for node in snapshot["nodes"] if node["id"] == 1)["memory_count"] == 1
    )
    assert next(memory for memory in snapshot["memories"] if memory["memory_id"] == 1)[
        "node_ids"
    ] == list(range(1, 5003))
    assert await store.get_full_graph_snapshot(session_id="other") == {
        "nodes": [],
        "edges": [],
        "entries": [],
        "memories": [],
    }
    await store.close()


@pytest.mark.asyncio
async def test_full_graph_snapshot_includes_archived_memories(tmp_path):
    store = GraphStore(str(tmp_path / "full_graph_archived.db"))
    await store.initialize()
    async with store._connect() as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY, metadata TEXT)"
        )
        await db.executemany(
            "INSERT INTO documents (id, metadata) VALUES (?, ?)",
            [
                (1, json.dumps({"status": "active"})),
                (2, json.dumps({"status": "archived"})),
            ],
        )
        await db.executemany(
            "INSERT INTO graph_nodes "
            "(id,node_key,node_type,node_value,canonical_value,created_at,updated_at) "
            "VALUES (?,?,'topic',?,?,'2026-01-01','2026-01-01')",
            [(i, f"node:{i}", f"Node {i}", f"Node {i}") for i in range(1, 5)],
        )
        await db.executemany(
            "INSERT INTO graph_entries "
            "(id,entry_key,source_memory_id,session_id,entry_type,content,created_at,updated_at) "
            "VALUES (?,?,?,'scope','summary',?,'2026-01-01','2026-01-01')",
            [(1, "entry:1", 1, "Active memory"), (2, "entry:2", 2, "Archived memory")],
        )
        await db.executemany(
            "INSERT INTO graph_entry_nodes (entry_id,node_id) VALUES (?,?)",
            [(1, 1), (1, 2), (2, 3), (2, 4)],
        )
        await db.executemany(
            "INSERT INTO graph_edges "
            "(id,edge_key,source_node_id,target_node_id,relation_type,"
            "source_memory_id,status,created_at,updated_at) "
            "VALUES (?,?,?,?,'related',?,?,'2026-01-01','2026-01-01')",
            [
                (1, "edge:active", 1, 2, 1, "active"),
                (2, "edge:archived", 3, 4, 2, "archived"),
                (3, "edge:deleted", 1, 2, 1, "deleted"),
            ],
        )
        await db.commit()

    snapshot = await store.get_full_graph_snapshot(session_id="scope")
    assert {node["id"] for node in snapshot["nodes"]} == {1, 2, 3, 4}
    assert [edge["id"] for edge in snapshot["edges"]] == [1, 2]
    assert {memory["memory_id"] for memory in snapshot["memories"]} == {1, 2}
    await store.close()

    # Graph-only databases have no documents table and keep every entry.
    graph_only = GraphStore(str(tmp_path / "full_graph_only.db"))
    await graph_only.initialize()
    async with graph_only._connect() as db:
        await db.execute(
            "INSERT INTO graph_nodes "
            "(id,node_key,node_type,node_value,canonical_value,created_at,updated_at) "
            "VALUES (1,'node:1','topic','Node 1','Node 1','2026-01-01','2026-01-01')"
        )
        await db.execute(
            "INSERT INTO graph_entries "
            "(id,entry_key,source_memory_id,session_id,entry_type,content,created_at,updated_at) "
            "VALUES (1,'entry:1',7,'scope','summary','Memory 7','2026-01-01','2026-01-01')"
        )
        await db.execute(
            "INSERT INTO graph_entry_nodes (entry_id,node_id) VALUES (1,1)"
        )
        await db.execute(
            "INSERT INTO graph_edges "
            "(id,edge_key,source_node_id,target_node_id,relation_type,"
            "source_memory_id,created_at,updated_at) "
            "VALUES (1,'edge:1',1,1,'related',7,'2026-01-01','2026-01-01')"
        )
        await db.commit()

    fallback = await graph_only.get_full_graph_snapshot(session_id="scope")
    assert len(fallback["nodes"]) == 1
    assert len(fallback["edges"]) == 1
    assert [memory["memory_id"] for memory in fallback["memories"]] == [7]
    await graph_only.close()
