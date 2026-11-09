"""Near-duplicate merge rebuilds the derived indexes from the merged body.

The unit tests in ``test_memory_dedup.py`` cover threshold/candidate wiring with
mocks; this module runs the real path (SQLite documents + real FAISS indexes) and
asserts that a merge which changes the body re-embeds it, refreshes the BM25/FTS
entry, moves graph entries and atoms to the surviving document, and leaves a
single active memory behind.
"""

import math
from types import SimpleNamespace

import pytest
import pytest_asyncio
from astrbot.core.db.vec_db.faiss_impl.vec_db import FaissVecDB
from astrbot.core.provider.provider import EmbeddingProvider
from astrbot_plugin_livingmemory.core.event_handler_modules.memory_reflection import (
    MemoryReflection,
)
from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine

_DIM = 16
_CONTENT_OLD = "用户喜欢喝茶。"
_CONTENT_NEW = "用户住在杭州。"
_CONTENT_MERGED = "用户喜欢喝茶。用户住在杭州。"
# 1 - squared_l2 / 2 == cosine for unit vectors, so the angle sets the dedup score.
_COS_DUPLICATE = 0.95
_COS_DISTINCT = 0.85


def _unit(angle: float) -> list[float]:
    vector = [0.0] * _DIM
    vector[0] = math.cos(angle)
    vector[1] = math.sin(angle)
    return vector


class _MappedEmbeddingProvider(EmbeddingProvider):
    """Deterministic embeddings with controlled pairwise similarity."""

    def __init__(self, vectors: dict[str, list[float]]):
        super().__init__({"id": "test-embedding", "type": "test"}, {})
        self._vectors = {key.strip(): value for key, value in vectors.items()}

    def _hash_vector(self, text: str) -> list[float]:
        vector = [0.0] * _DIM
        for index, byte in enumerate(text.encode("utf-8")):
            vector[index % _DIM] += ((byte % 31) + 1) / 31.0
        norm = sum(value * value for value in vector) ** 0.5 or 1.0
        return [value / norm for value in vector]

    async def get_embedding(self, text: str) -> list[float]:
        mapped = self._vectors.get(text.strip())
        return mapped if mapped is not None else self._hash_vector(text)

    async def get_embeddings(self, text: list[str]) -> list[list[float]]:
        return [await self.get_embedding(item) for item in text]

    def get_dim(self) -> int:
        return _DIM


def _reflection(engine: MemoryEngine, *, threshold: float, max_candidates: int):
    reflection = MemoryReflection.__new__(MemoryReflection)
    reflection.memory_engine = engine
    reflection.consolidation_manager = None
    reflection.config_manager = SimpleNamespace(
        get=lambda key, default=None: {
            "memory_dedup.enabled": True,
            "memory_dedup.similarity_threshold": threshold,
            "memory_dedup.max_candidates": max_candidates,
        }.get(key, default)
    )
    return reflection


@pytest_asyncio.fixture
async def dedup_stack(tmp_path):
    """Real MemoryEngine with graph memory enabled and controlled embeddings."""
    duplicate_angle = math.acos(_COS_DUPLICATE)
    distinct_angle = math.acos(_COS_DISTINCT)
    provider = _MappedEmbeddingProvider(
        {
            _CONTENT_OLD: _unit(0.0),
            _CONTENT_NEW: _unit(duplicate_angle),
            _CONTENT_MERGED: _unit(math.radians(60.0)),
            "用户只吃素。": _unit(distinct_angle),
        }
    )
    db_path = tmp_path / "dedup.db"
    faiss_db = FaissVecDB(
        doc_store_path=str(db_path),
        index_store_path=str(tmp_path / "dedup.index"),
        embedding_provider=provider,
    )
    await faiss_db.initialize()
    graph_faiss_db = FaissVecDB(
        doc_store_path=str(tmp_path / "dedup_graph.db"),
        index_store_path=str(tmp_path / "dedup_graph.index"),
        embedding_provider=provider,
    )
    await graph_faiss_db.initialize()

    engine = MemoryEngine(
        db_path=str(db_path),
        faiss_db=faiss_db,
        graph_vector_db=graph_faiss_db,
        config={
            "graph_memory_enabled": True,
            "graph_memory_atom_enabled": True,
            "recent_memory_count": 0,
            "search_cache_enabled": False,
        },
    )
    await engine.initialize()
    try:
        yield engine
    finally:
        await engine.close()
        await graph_faiss_db.close()
        await faiss_db.close()


async def _count_active(engine: MemoryEngine) -> int:
    cursor = await engine.db_connection.execute(
        "SELECT COUNT(*) FROM documents "
        "WHERE COALESCE(json_extract(metadata, '$.status'), 'active') = 'active'"
    )
    row = await cursor.fetchone()
    return int(row[0])


@pytest.mark.asyncio
async def test_near_duplicate_merge_rebuilds_body_and_indexes(dedup_stack):
    engine = dedup_stack
    scope = "test:private:dedup"
    old_id = await engine.add_memory(
        _CONTENT_OLD,
        scope,
        "persona-dedup",
        0.5,
        {"topics": ["饮品"], "key_facts": ["用户喜欢喝茶"]},
    )
    assert await _count_active(engine) == 1

    reflection = _reflection(engine, threshold=0.92, max_candidates=5)
    memory_id, created, similarity = await MemoryReflection._persist_summary_memory(
        reflection,
        session_id=scope,
        memory_scope=scope,
        persona_id="persona-dedup",
        content=_CONTENT_NEW,
        metadata={
            "topics": ["居住地"],
            "key_facts": ["用户住在杭州"],
        },
        importance=0.7,
        atoms=[],
        source_messages=None,
    )

    assert created is False, "similarity above threshold must merge, not insert"
    assert similarity == pytest.approx(_COS_DUPLICATE, abs=0.02)
    assert memory_id is not None and memory_id != old_id
    assert await engine.get_memory(old_id) is None
    assert await _count_active(engine) == 1

    memory = await engine.get_memory(memory_id)
    assert memory["text"] == _CONTENT_MERGED

    # Embedding was recomputed from the merged body: querying with the merged
    # text returns cosine ~1.0, which can only happen for the new vector.
    vector_hits = await engine.vector_retriever.search(_CONTENT_MERGED, 5)
    assert vector_hits and vector_hits[0].doc_id == memory_id
    assert vector_hits[0].score > 0.9

    # BM25/FTS carries both the old and the incoming facts.
    for query in ("喝茶", "杭州"):
        bm25_hits = await engine.bm25_retriever.search(query, 5)
        assert [hit.doc_id for hit in bm25_hits] == [memory_id], query

    # Graph entries moved to the surviving document (old document is gone).
    nodes = await engine.graph_store.search_nodes_by_tokens(["居住地"], limit=5)
    assert nodes, "merged metadata must be re-indexed into the graph route"
    node_ids = [int(node["id"]) for node in nodes]
    entry_hits = await engine.graph_store.get_entries_for_node_ids(node_ids, limit=10)
    assert memory_id in {hit["source_memory_id"] for hit in entry_hits}
    assert old_id not in {hit["source_memory_id"] for hit in entry_hits}

    # Atoms were re-classified for the surviving memory, none left on the old id.
    merged_atoms = await engine.atom_store.get_by_parent(memory_id)
    assert merged_atoms
    assert await engine.atom_store.get_by_parent(old_id) == []


@pytest.mark.asyncio
async def test_below_threshold_summary_is_stored_as_new_memory(dedup_stack):
    engine = dedup_stack
    scope = "test:private:distinct"
    await engine.add_memory(
        _CONTENT_OLD,
        scope,
        "persona-distinct",
        0.5,
        {"topics": ["饮品"]},
    )

    reflection = _reflection(engine, threshold=0.92, max_candidates=5)
    memory_id, created, similarity = await MemoryReflection._persist_summary_memory(
        reflection,
        session_id=scope,
        memory_scope=scope,
        persona_id="persona-distinct",
        content="用户只吃素。",
        metadata={"topics": ["饮食"]},
        importance=0.6,
        atoms=[],
        source_messages=None,
    )

    assert created is True, "similarity below threshold must not merge"
    assert similarity is None
    assert memory_id is not None
    assert await _count_active(engine) == 2
