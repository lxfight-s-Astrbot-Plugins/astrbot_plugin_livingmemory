"""Full graph requests preserve the selected scope at display-scale volumes."""

import pytest
from astrbot_plugin_livingmemory.core.models.graph_models import GraphNode
from astrbot_plugin_livingmemory.storage.graph_store import GraphStore


@pytest.mark.asyncio
async def test_full_graph_retains_all_nodes_relations_and_memories(tmp_path):
    store = GraphStore(str(tmp_path / "full_graph.db"))
    await store.initialize()
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
    snapshot = await store.get_full_graph_snapshot(session_id="selected")
    assert len(snapshot["nodes"]) == 5002
    assert len(snapshot["edges"]) == 10002
    assert len(snapshot["memories"]) == 2002
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
