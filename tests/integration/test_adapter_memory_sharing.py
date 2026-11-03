"""Adapter filtering with real SQL, FAISS, graph and concurrent requests."""

import asyncio
import time

import pytest_asyncio
from astrbot_plugin_livingmemory.core.models.memory_atom import MemoryAtom

import pytest

from astrbot.core.db.vec_db.faiss_impl.vec_db import FaissVecDB
from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine
from astrbot_plugin_livingmemory.core.memory_scope import (
    adapter_access,
    GLOBAL_MEMORY_SCOPE,
)
from tests.integration.test_real_db_end_to_end import _DeterministicEmbeddingProvider


@pytest.mark.asyncio
async def test_adapter_filter_intersects_persona_and_prevents_cache_and_id_leaks(
    tmp_path,
):
    db = FaissVecDB(
        str(tmp_path / "main.db"),
        str(tmp_path / "main.index"),
        _DeterministicEmbeddingProvider(),
    )
    await db.initialize()
    engine = MemoryEngine(
        str(tmp_path / "main.db"), db, config={"recent_memory_count": 0}
    )
    await engine.initialize()
    ids = {}
    try:
        for owner, persona in [
            ("qq-a", "p"),
            ("wechat-a", "p"),
            ("telegram-a", "p"),
            ("qq-a", "other"),
        ]:
            ids[owner, persona] = await engine.add_memory(
                "memory coffee preference " + owner,
                session_id=GLOBAL_MEMORY_SCOPE,
                persona_id=persona,
                metadata={"adapter_id": owner},
            )
        config = {"filtering_settings": {"shared_adapter_ids": ["qq-a", "wechat-a"]}}
        with adapter_access(config, "qq-a"):
            results = await engine.search_memories(
                "coffee", 10, GLOBAL_MEMORY_SCOPE, "p"
            )
            assert {item.doc_id for item in results} == {
                ids["qq-a", "p"],
                ids["wechat-a", "p"],
            }
            assert await engine.get_memory(ids["telegram-a", "p"]) is None
            assert await engine.delete_memory(ids["telegram-a", "p"]) is False
        with adapter_access(config, "telegram-a"):
            results = await engine.search_memories(
                "coffee", 10, GLOBAL_MEMORY_SCOPE, "p"
            )
            assert {item.doc_id for item in results} == {ids["telegram-a", "p"]}
        assert await engine.get_memory(ids["telegram-a", "p"]) is not None
    finally:
        await engine.close()
        await db.close()


@pytest_asyncio.fixture
async def adapter_engine(tmp_path):
    provider = _DeterministicEmbeddingProvider()
    db = FaissVecDB(str(tmp_path / "main.db"), str(tmp_path / "main.index"), provider)
    graph = FaissVecDB(
        str(tmp_path / "graph.db"), str(tmp_path / "graph.index"), provider
    )
    await db.initialize()
    await graph.initialize()
    engine = MemoryEngine(
        str(tmp_path / "main.db"),
        db,
        graph_vector_db=graph,
        config={"recent_memory_count": 0, "graph_memory_enabled": True},
    )
    await engine.initialize()
    try:
        yield engine
    finally:
        await engine.close()
        await db.close()


@pytest.mark.asyncio
async def test_graph_atoms_recent_and_agent_evidence_share_adapter_boundary(
    adapter_engine,
):
    engine = adapter_engine
    ids = {}
    for owner in ("qq-a", "wechat-a", "telegram-a"):
        ids[owner] = await engine.add_memory(
            "shared coffee fact " + owner,
            session_id=GLOBAL_MEMORY_SCOPE,
            persona_id="p",
            metadata={
                "adapter_id": owner,
                "topics": ["coffee"],
                "key_facts": ["coffee fact " + owner],
            },
            atoms=[
                MemoryAtom(
                    parent_memory_id=0,
                    content="coffee fact " + owner,
                    expires_at=time.time() + 86400,
                )
            ],
        )
    config = {"filtering_settings": {"shared_adapter_ids": ["qq-a", "wechat-a"]}}
    with adapter_access(config, "qq-a"):
        allowed = {ids["qq-a"], ids["wechat-a"]}
        assert {
            hit.parent_memory_id
            for hit in await engine.atom_retriever.search(
                "coffee", 10, GLOBAL_MEMORY_SCOPE, "p"
            )
        } == allowed
        assert {
            hit.doc_id
            for hit in await engine._get_recent_memory_results(
                10, GLOBAL_MEMORY_SCOPE, "p"
            )
        } == allowed
        snapshot = await engine.graph_store.get_full_graph_snapshot(
            GLOBAL_MEMORY_SCOPE, "p"
        )
        assert {row["memory_id"] for row in snapshot["memories"]} == allowed
        assert await engine.get_memory_for_recall(ids["telegram-a"]) is None
        hits = await engine.graph_vector_retriever.search(
            "coffee", 10, GLOBAL_MEMORY_SCOPE, "p"
        )
        assert {hit.doc_id for hit in hits} == allowed
        stats = await engine.get_statistics()
        assert stats["total_memories"] == 2


@pytest.mark.asyncio
async def test_sharing_does_not_remove_session_isolation_and_writes_keep_their_owner(
    adapter_engine,
):
    engine = adapter_engine
    config = {"filtering_settings": {"shared_adapter_ids": ["qq-a", "wechat-a"]}}
    for owner, scope in [
        ("qq-a", "qq-a:FriendMessage:123"),
        ("wechat-a", "wechat-a:FriendMessage:abc"),
    ]:
        with adapter_access(config, owner, write=True):
            await engine.add_memory(
                "coffee preference", session_id=scope, persona_id="p"
            )
    with adapter_access(config, "qq-a"):
        hits = await engine.search_memories("coffee", 10, "qq-a:FriendMessage:123", "p")
        assert len(hits) == 1
        assert hits[0].metadata["adapter_id"] == "qq-a"


@pytest.mark.asyncio
async def test_adapter_predicate_precedes_keyword_limit_and_concurrent_caches(
    adapter_engine,
):
    engine = adapter_engine
    for i in range(12):
        await engine.add_memory(
            "coffee",
            session_id=GLOBAL_MEMORY_SCOPE,
            metadata={"adapter_id": "telegram-a"},
        )
    own = await engine.add_memory(
        "coffee", session_id=GLOBAL_MEMORY_SCOPE, metadata={"adapter_id": "qq-a"}
    )
    config = {"filtering_settings": {"shared_adapter_ids": []}}
    with adapter_access(config, "qq-a"):
        assert [
            hit.doc_id
            for hit in await engine.bm25_retriever.search(
                "coffee", 1, GLOBAL_MEMORY_SCOPE
            )
        ] == [own]

    async def search(owner):
        with adapter_access(config, owner):
            await asyncio.sleep(0)
            return await engine.search_memories("coffee", 3, GLOBAL_MEMORY_SCOPE)

    own_hits, other_hits = await asyncio.gather(search("qq-a"), search("telegram-a"))
    assert {hit.doc_id for hit in own_hits} == {own}
    assert own not in {hit.doc_id for hit in other_hits}


@pytest.mark.asyncio
async def test_consolidation_candidates_and_statistics_follow_the_adapter_filter(
    adapter_engine,
):
    engine = adapter_engine
    for owner in ("qq-a", "telegram-a"):
        await engine.add_memory(
            "coffee",
            session_id=GLOBAL_MEMORY_SCOPE,
            importance=0.1,
            metadata={"adapter_id": owner, "create_time": time.time() - 86400},
            preserve_create_time=True,
        )
    with adapter_access({"filtering_settings": {"shared_adapter_ids": []}}, "qq-a"):
        candidates = await engine.get_consolidation_candidates(0.5, time.time())
        assert len(candidates) == 1
        assert candidates[0]["metadata"]["adapter_id"] == "qq-a"
        assert (await engine.get_statistics())["total_memories"] == 1


@pytest.mark.asyncio
async def test_vector_adapter_candidates_are_selected_before_the_global_expansion_cap(
    adapter_engine,
):
    import json
    import numpy as np

    engine = adapter_engine
    total = 8300
    query = np.asarray(
        await engine.faiss_db.embedding_provider.get_embedding("coffee"),
        dtype=np.float32,
    )
    vectors = np.tile(query, (total, 1))
    vectors[-1] *= 0.9
    ids = np.arange(1, total + 1, dtype=np.int64)
    engine.faiss_db.embedding_storage.index.add_with_ids(vectors, ids)
    rows = [
        (
            int(i),
            str(i),
            "coffee",
            json.dumps(
                {"adapter_id": "qq-a" if i == total else "other", "status": "active"}
            ),
        )
        for i in ids
    ]
    await engine.db_connection.executemany(
        "INSERT INTO documents(id, doc_id, text, metadata) VALUES(?,?,?,?)", rows
    )
    await engine.db_connection.commit()
    from astrbot_plugin_livingmemory.core.faiss_async_persist import (
        install_async_persist,
    )

    persister = install_async_persist(engine.faiss_db.embedding_storage)
    try:
        with adapter_access({"filtering_settings": {"shared_adapter_ids": []}}, "qq-a"):
            hits = await engine.vector_retriever.search("coffee", 1)
            assert [hit.doc_id for hit in hits] == [total]
    finally:
        await persister.aclose()


@pytest.mark.asyncio
async def test_search_tool_crosses_adapters_when_legacy_session_filtering_is_disabled(
    adapter_engine,
):
    import json
    from types import SimpleNamespace
    from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
    from astrbot_plugin_livingmemory.core.tools.memory_search_tool import (
        MemorySearchTool,
    )

    engine = adapter_engine
    ids = set()
    for owner, scope in [
        ("qq-a", "qq-a:FriendMessage:123"),
        ("wechat-a", "wechat-a:FriendMessage:abc"),
    ]:
        ids.add(
            await engine.add_memory(
                "coffee preference", session_id=scope, metadata={"adapter_id": owner}
            )
        )
    config = ConfigManager(
        {
            "filtering_settings": {
                "shared_adapter_ids": ["qq-a", "wechat-a"],
                "use_session_filtering": False,
                "use_persona_filtering": False,
            }
        }
    )
    event = SimpleNamespace(
        unified_msg_origin="qq-a:FriendMessage:123", get_platform_id=lambda: "qq-a"
    )
    tool = MemorySearchTool(
        context=SimpleNamespace(), config_manager=config, memory_engine=engine
    )
    output = json.loads(
        await tool.call(
            SimpleNamespace(context=SimpleNamespace(event=event)), query="coffee"
        )
    )
    assert {row["id"] for row in output["results"]} == ids
    assert output["applied_filters"]["session_filtered"] is False
