"""Focused tests for graph-store subgraph shaping."""

import pytest
from astrbot_plugin_livingmemory.core.models.graph_models import (
    GraphEntry,
    GraphNode,
)
from astrbot_plugin_livingmemory.storage.graph_store import GraphStore


@pytest.mark.asyncio
async def test_subgraph_memory_stats_recompute_after_node_limit(tmp_path):
    store = GraphStore(str(tmp_path / "graph_limit.db"))
    await store.initialize()

    nodes = [
        GraphNode("topic", "Alpha", "alpha"),
        GraphNode("topic", "Beta", "beta"),
    ]
    node_key_to_id = await store.upsert_nodes(nodes)
    await store.add_entries(
        [
            GraphEntry(
                entry_key="entry-alpha",
                source_memory_id=1,
                session_id="s1",
                persona_id="p1",
                entry_type="fact",
                content="Alpha fact",
                metadata={"importance": 0.8},
                node_keys=["topic:alpha"],
            ),
            GraphEntry(
                entry_key="entry-beta",
                source_memory_id=1,
                session_id="s1",
                persona_id="p1",
                entry_type="fact",
                content="Beta fact",
                metadata={"importance": 0.8},
                node_keys=["topic:beta"],
            ),
        ],
        node_key_to_id,
        {},
    )

    subgraph = await store.get_subgraph_for_memories([1], limit_nodes=1)

    assert len(subgraph["nodes"]) == 1
    assert len(subgraph["entries"]) == 1
    assert subgraph["memories"][0]["entry_count"] == 1
    assert subgraph["memories"][0]["node_count"] == 1
    await store.close()


@pytest.mark.asyncio
async def test_search_nodes_by_tokens_batches_large_token_lists(tmp_path):
    store = GraphStore(str(tmp_path / "graph_tokens.db"))
    await store.initialize()

    await store.upsert_nodes(
        [
            GraphNode("topic", "needle alpha", "needle alpha"),
            GraphNode("topic", "needle beta", "needle beta"),
        ]
    )

    tokens = [f"token-{i}" for i in range(1100)]
    tokens.extend(["needle", "needle", ""])

    results = await store.search_nodes_by_tokens(tokens, limit=10)

    assert {item["canonical_value"] for item in results} == {
        "needle beta",
        "needle alpha",
    }
    await store.close()


@pytest.mark.asyncio
async def test_foreign_keys_cascade_on_runtime_connection(tmp_path):
    """运行时连接应开启外键级联，删除节点时关联边被自动清理。"""
    from astrbot_plugin_livingmemory.core.models.graph_models import GraphEdge

    store = GraphStore(str(tmp_path / "graph_fk_cascade.db"))
    await store.initialize()

    node_a = GraphNode("topic", "Alpha", "alpha")
    node_b = GraphNode("topic", "Beta", "beta")
    node_map = await store.upsert_nodes([node_a, node_b])

    edge = GraphEdge(
        source_key=node_a.node_key,
        target_key=node_b.node_key,
        relation_type="relates",
        source_memory_id=1,
    )
    edge_id = await store.add_edge(edge, node_map)
    assert edge_id > 0

    async with store._connect() as db:
        await db.execute(
            "DELETE FROM graph_nodes WHERE id = ?", (node_map[node_a.node_key],)
        )
        await db.commit()

        cursor = await db.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE id = ?", (edge_id,)
        )
        row = await cursor.fetchone()

    assert row[0] == 0
    await store.close()


@pytest.mark.asyncio
async def test_add_edge_upsert_same_edge_key_is_idempotent(tmp_path):
    """同一 edge_key 重复写入应返回同一 id 且不产生重复边。"""
    from astrbot_plugin_livingmemory.core.models.graph_models import GraphEdge

    store = GraphStore(str(tmp_path / "graph_edge_upsert.db"))
    await store.initialize()

    node_a = GraphNode("topic", "Alpha", "alpha")
    node_b = GraphNode("topic", "Beta", "beta")
    node_map = await store.upsert_nodes([node_a, node_b])

    edge = GraphEdge(
        source_key=node_a.node_key,
        target_key=node_b.node_key,
        relation_type="relates",
        source_memory_id=1,
        weight=1.0,
    )
    first_id = await store.add_edge(edge, node_map)
    second_id = await store.add_edge(edge, node_map)

    assert first_id == second_id

    async with store._connect() as db:
        cursor = await db.execute("SELECT COUNT(*) FROM graph_edges")
        row = await cursor.fetchone()

    assert row[0] == 1
    await store.close()


@pytest.mark.asyncio
async def test_read_connection_is_reused_across_queries(tmp_path, monkeypatch):
    """召回链路的只读查询复用同一条连接，不再每次 open/PRAGMA。"""
    from astrbot_plugin_livingmemory.storage import graph_store as graph_store_module

    real_connect = graph_store_module.aiosqlite.connect
    opened: list[int] = []

    async def counting_connect(*args, **kwargs):
        opened.append(1)
        return await real_connect(*args, **kwargs)

    monkeypatch.setattr(graph_store_module.aiosqlite, "connect", counting_connect)

    store = GraphStore(str(tmp_path / "graph_read_pool.db"), cache_reads=True)
    await store.initialize()
    assert len(opened) == 1  # 初始化写连接

    await store.list_vector_doc_ids()
    await store.list_vector_doc_ids()
    await store.get_memory_entry_stats()

    assert len(opened) == 2  # 首次读建一条连接，后续复用
    await store.close()


@pytest.mark.asyncio
async def test_neighbor_lookup_uses_target_node_index(tmp_path):
    """邻居扩展按 target_node_id 反查时必须命中索引，而非全表扫描。"""
    store = GraphStore(str(tmp_path / "graph_neighbor_index.db"))
    await store.initialize()

    async with store._connect() as db:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_graph_edges_target'"
        )
        assert await cursor.fetchone() is not None

        cursor = await db.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT target_node_id FROM graph_edges "
            "WHERE target_node_id IN (1, 2) AND status = 'active'"
        )
        plan = " ".join(str(row) for row in await cursor.fetchall())

    assert "idx_graph_edges_target" in plan
    await store.close()
