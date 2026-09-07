"""Regression coverage for recall failures, scope isolation and atom expiry."""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest
import pytest_asyncio
from astrbot_plugin_livingmemory.core.faiss_async_persist import install_async_persist
from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine
from astrbot_plugin_livingmemory.core.models.graph_models import GraphNode
from astrbot_plugin_livingmemory.core.models.memory_atom import MemoryAtom
from astrbot_plugin_livingmemory.core.processors.graph_extractor import GraphExtractor
from astrbot_plugin_livingmemory.core.processors.text_processor import TextProcessor
from astrbot_plugin_livingmemory.core.retrieval.bm25_retriever import BM25Retriever
from astrbot_plugin_livingmemory.core.retrieval.dual_route_retriever import (
    DualRouteRetriever,
)
from astrbot_plugin_livingmemory.core.retrieval.graph_keyword_retriever import (
    GraphKeywordResult,
)
from astrbot_plugin_livingmemory.core.retrieval.graph_retriever import GraphRetriever
from astrbot_plugin_livingmemory.core.retrieval.hybrid_retriever import HybridResult
from astrbot_plugin_livingmemory.core.retrieval.rrf_fusion import RRFFusion
from astrbot_plugin_livingmemory.core.retrieval.vector_retriever import VectorRetriever
from astrbot_plugin_livingmemory.storage.graph_store import GraphStore

from astrbot.core.db.vec_db.faiss_impl.vec_db import FaissVecDB


class Embedding:
    def __init__(self):
        self.calls = []

    def get_dim(self):
        return 4

    async def get_embedding(self, text):
        self.calls.append(text)
        await asyncio.sleep(0)
        return [1.0, 0.0, 0.0, 0.0]

    async def get_embeddings_batch(self, contents, **kwargs):
        return [await self.get_embedding(content) for content in contents]


@pytest_asyncio.fixture
async def engine(tmp_path):
    provider = Embedding()
    document_db = FaissVecDB(
        str(tmp_path / "memory.db"), str(tmp_path / "memory.index"), provider
    )
    graph_db = FaissVecDB(
        str(tmp_path / "graph.db"), str(tmp_path / "graph.index"), provider
    )
    await document_db.initialize()
    await graph_db.initialize()
    persisters = [
        install_async_persist(db.embedding_storage, 600)
        for db in (document_db, graph_db)
    ]
    memory = MemoryEngine(
        str(tmp_path / "memory.db"),
        document_db,
        config={"recent_memory_count": 0, "graph_memory_enabled": True},
        graph_vector_db=graph_db,
    )
    await memory.initialize()
    await memory.atom_lifecycle_manager.stop()
    try:
        yield memory
    finally:
        for persister in persisters:
            await persister.aclose()
        await memory.close()
        await document_db.close()


@pytest.mark.asyncio
async def test_long_memory_roundtrips_full_text_and_rolls_back_failed_vector(
    engine, monkeypatch
):
    content = "A" * 2500 + "UNIQUE MIDDLE FACT" + "Z" * 2500
    doc_id = await engine.add_memory(content)
    assert (await engine.get_memory(doc_id))["text"] == content
    assert any(len(text) == 4000 for text in engine.faiss_db.embedding_provider.calls)
    before = await engine.faiss_db.document_storage.count_documents({})
    monkeypatch.setattr(
        engine.faiss_db.embedding_storage,
        "insert",
        AsyncMock(side_effect=RuntimeError("disk failed")),
    )
    with pytest.raises(RuntimeError, match="disk failed"):
        await engine.vector_retriever.add_document(content)
    assert await engine.faiss_db.document_storage.count_documents({}) == before


@pytest.mark.asyncio
async def test_bm25_filters_before_limit(tmp_path):
    path = tmp_path / "bm25.db"
    retriever = BM25Retriever(str(path), TextProcessor())
    await retriever.initialize()
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "CREATE TABLE documents(id INTEGER PRIMARY KEY, text TEXT, metadata TEXT)"
        )
        rows = [
            (i, "coffee", json.dumps({"session_id": "other"})) for i in range(1, 101)
        ]
        rows.append(
            (
                101,
                "coffee " + "filler " * 100,
                json.dumps({"session_id": "target", "persona_id": "p"}),
            )
        )
        await db.executemany("INSERT INTO documents VALUES (?, ?, ?)", rows)
        await db.executemany(
            "INSERT INTO livingmemory_memories_fts(doc_id, content) VALUES (?, ?)",
            [(i, text) for i, text, _ in rows],
        )
        await db.commit()
    hits = await retriever.search("coffee", 5, "target", "p")
    assert [hit.doc_id for hit in hits] == [101]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("offline"), asyncio.TimeoutError()])
async def test_graph_failure_preserves_document_results(failure):
    good = HybridResult(1, 1, 1, 1, None, "healthy", {"status": "active"})
    graph = GraphRetriever(
        SimpleNamespace(search=AsyncMock(return_value=[])),
        SimpleNamespace(search=AsyncMock(side_effect=failure)),
        RRFFusion(),
    )
    dual = DualRouteRetriever(
        SimpleNamespace(search=AsyncMock(return_value=[good])), graph, AsyncMock()
    )
    assert await dual.search("coffee") == [good]


@pytest.mark.asyncio
async def test_slow_vector_times_out_without_losing_graph_keywords():
    keyword = GraphKeywordResult(1, 1, "coffee", {})
    cancelled = asyncio.Event()

    async def slow(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    graph = GraphRetriever(
        SimpleNamespace(search=AsyncMock(return_value=[keyword])),
        SimpleNamespace(search=slow),
        RRFFusion(),
        {"retrieval_timeout_seconds": 0.01},
    )
    assert len(await graph.search("coffee")) == 1
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_new_and_legacy_atoms_do_not_decay_from_unix_epoch():
    atom = MemoryAtom(1, content="coffee")
    entry = GraphExtractor().extract(1, "coffee", {}, [atom]).entries[0]
    assert entry.metadata["last_access_time"] == atom.last_accessed_at
    for metadata in (
        entry.metadata,
        {"ttl_days": 30},
        {"ttl_days": 30, "last_access_time": 0},
    ):
        graph = GraphRetriever(
            SimpleNamespace(
                search=AsyncMock(
                    return_value=[GraphKeywordResult(1, 1, "coffee", metadata)]
                )
            ),
            SimpleNamespace(search=AsyncMock(return_value=[])),
            RRFFusion(),
        )
        result = (await graph.search("coffee"))[0]
        assert result.score_breakdown["graph_temporal_factor"] > 0.99


@pytest.mark.asyncio
async def test_vectors_expand_past_other_sessions_with_one_embedding(engine):
    import numpy as np

    storage = engine.faiss_db.document_storage
    ids = []
    for i in range(50):
        ids.append(
            await storage.insert_document(str(i), "coffee", {"session_id": "other"})
        )
    target = await storage.insert_document(
        "target", "coffee target", {"session_id": "target"}
    )
    ids.append(target)
    vectors = np.zeros((51, 4), dtype=np.float32)
    vectors[:50, 0] = 1
    vectors[50, 0] = 0.8
    await engine.faiss_db.embedding_storage.insert_batch(vectors, ids)
    provider = engine.faiss_db.embedding_provider
    provider.calls.clear()
    hits = await VectorRetriever(engine.faiss_db).search("coffee", 1, "target")
    assert [hit.doc_id for hit in hits] == [target]
    assert provider.calls == ["coffee"]


@pytest.mark.asyncio
async def test_dual_routes_share_embedding_and_batch_hydration(engine, monkeypatch):
    await engine.add_memory("coffee", session_id="s")
    provider = engine.faiss_db.embedding_provider
    provider.calls.clear()
    assert await engine.search_memories("coffee", session_id="s")
    assert provider.calls == ["coffee"]


@pytest.mark.asyncio
async def test_atom_expiry_is_enforced_on_cached_recall_and_access_is_updated(engine):
    live = MemoryAtom(0, content="coffee live")
    expired = MemoryAtom(0, content="coffee expired")
    memory_id = await engine.add_memory(
        "coffee live and coffee expired", atoms=[live, expired]
    )
    await engine.db_connection.execute(
        "UPDATE memory_atoms SET expires_at = ? WHERE id = ?",
        (time.time() - 1, expired.atom_id),
    )
    await engine.db_connection.execute(
        "UPDATE memory_atoms SET last_accessed_at = 1 WHERE id = ?", (live.atom_id,)
    )
    await engine.db_connection.commit()
    results = await engine.search_memories("coffee")
    assert [result.doc_id for result in results] == [memory_id]
    assert results[0].content == "coffee live"
    await asyncio.gather(*list(engine._pending_tasks))
    assert (await engine.atom_store.get_by_parent(memory_id))[0].last_accessed_at > 1
    await engine.db_connection.execute(
        "UPDATE memory_atoms SET expires_at = ? WHERE id = ?",
        (time.time() - 1, live.atom_id),
    )
    await engine.db_connection.commit()
    assert await engine.search_memories("coffee") == []


@pytest.mark.asyncio
async def test_failed_delta_replay_restores_drained_updates_and_marks_pending(
    engine, monkeypatch
):
    manager = engine.graph_memory_manager
    original = manager.graph_vector_retriever.add_memory_entries_batch
    attempts = 0

    async def fail_once(groups):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("delta embedding failed")
        return await original(groups)

    monkeypatch.setattr(
        manager.graph_vector_retriever, "add_memory_entries_batch", fail_once
    )
    memory_id = None

    async def batches():
        nonlocal memory_id
        memory_id = await engine.add_memory("concurrent coffee")
        if False:
            yield []

    with pytest.raises(RuntimeError, match="delta embedding failed"):
        await manager.rebuild_memory_batches(batches())
    assert memory_id in await engine.graph_store.list_vector_doc_ids_by_source()
    cursor = await engine.db_connection.execute(
        "SELECT status FROM memory_write_ops WHERE memory_id = ?", (memory_id,)
    )
    assert (await cursor.fetchone())[0] == "needs_repair"


@pytest.mark.asyncio
async def test_node_trigram_index_follows_snapshot_replacement(engine, tmp_path):
    live = engine.graph_store
    await live.upsert_node(GraphNode("topic", "old coffee", "old coffee"))
    assert await live.search_nodes_by_tokens(["coffee"])
    shadow = GraphStore(str(tmp_path / "shadow.db"))
    await shadow.initialize()
    await shadow.upsert_node(GraphNode("topic", "new tea", "new tea"))
    await live.replace_all_from(shadow.db_path)
    assert not await live.search_nodes_by_tokens(["coffee"])
    assert await live.search_nodes_by_tokens(["tea"])
    assert await live.search_nodes_by_tokens(["te"])
    async with live._connect() as db:
        await db.execute(
            "INSERT INTO livingmemory_graph_nodes_fts(livingmemory_graph_nodes_fts, rank) VALUES ('integrity-check', 1)"
        )


@pytest.mark.asyncio
async def test_atom_route_recovers_a_live_fact_when_document_routes_miss(
    engine, monkeypatch
):
    atom = MemoryAtom(0, content="coffee preference")
    memory_id = await engine.add_memory("summary", atoms=[atom])
    monkeypatch.setattr(
        engine.dual_route_retriever, "search", AsyncMock(return_value=[])
    )
    results = await engine.search_memories("coffee")
    assert [result.doc_id for result in results] == [memory_id]
    assert results[0].content == atom.content


@pytest.mark.asyncio
async def test_graph_only_results_use_one_batch_document_read():
    from astrbot_plugin_livingmemory.core.retrieval.graph_retriever import GraphResult

    graph_results = [GraphResult(i, 0.8, 0.1, 0.8, None, "fact", {}) for i in (1, 2, 3)]
    batch_loader = AsyncMock(
        return_value=[
            {"id": i, "text": f"full {i}", "metadata": {"status": "active"}}
            for i in (1, 2, 3)
        ]
    )
    single_loader = AsyncMock(
        side_effect=AssertionError("unexpected per-document read")
    )
    dual = DualRouteRetriever(
        SimpleNamespace(search=AsyncMock(return_value=[])),
        SimpleNamespace(search=AsyncMock(return_value=graph_results)),
        single_loader,
        memory_batch_loader=batch_loader,
    )
    results = await dual.search("query")
    assert {result.content for result in results} == {"full 1", "full 2", "full 3"}
    batch_loader.assert_awaited_once()
    single_loader.assert_not_awaited()
