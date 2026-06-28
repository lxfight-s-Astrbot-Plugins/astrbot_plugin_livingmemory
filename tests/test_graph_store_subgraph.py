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
