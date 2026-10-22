"""Write-time near-duplicate detection and merging."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from astrbot_plugin_livingmemory.core.event_handler_modules.memory_reflection import (
    MemoryReflection,
)
from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine
from astrbot_plugin_livingmemory.core.retrieval.vector_retriever import VectorResult


def _vector_result(doc_id: int, score: float) -> VectorResult:
    return VectorResult(
        doc_id=doc_id, score=score, content="candidate", metadata={"status": "active"}
    )


def _reflection_with_engine(engine: SimpleNamespace) -> MemoryReflection:
    reflection = MemoryReflection.__new__(MemoryReflection)
    reflection.memory_engine = engine
    reflection.config_manager = SimpleNamespace(
        get=lambda key, default=None: {
            "memory_dedup.enabled": True,
            "memory_dedup.similarity_threshold": 0.92,
            "memory_dedup.max_candidates": 3,
        }.get(key, default)
    )
    return reflection


@pytest.mark.asyncio
async def test_find_similar_memory_returns_best_above_threshold() -> None:
    engine = MemoryEngine.__new__(MemoryEngine)
    engine.vector_retriever = SimpleNamespace(
        search=AsyncMock(
            return_value=[
                _vector_result(1, 0.80),
                _vector_result(2, 0.95),
                _vector_result(3, 0.93),
            ]
        )
    )

    result = await engine.find_similar_memory(
        "summary", session_id="scope", persona_id="persona", threshold=0.92
    )

    assert result == {"id": 2, "similarity": 0.95}
    engine.vector_retriever.search.assert_awaited_once_with(
        "summary", 3, session_id="scope", persona_id="persona"
    )


@pytest.mark.asyncio
async def test_find_similar_memory_ignores_below_threshold() -> None:
    engine = MemoryEngine.__new__(MemoryEngine)
    engine.vector_retriever = SimpleNamespace(
        search=AsyncMock(return_value=[_vector_result(1, 0.80)])
    )

    assert await engine.find_similar_memory("summary", threshold=0.92) is None


@pytest.mark.asyncio
async def test_find_similar_memory_survives_retriever_failure() -> None:
    engine = MemoryEngine.__new__(MemoryEngine)
    engine.vector_retriever = SimpleNamespace(
        search=AsyncMock(side_effect=RuntimeError("embedding unavailable"))
    )

    assert await engine.find_similar_memory("summary", threshold=0.92) is None


@pytest.mark.asyncio
async def test_merge_duplicate_memory_updates_metadata_and_sources() -> None:
    engine = MemoryEngine.__new__(MemoryEngine)
    engine.db_connection = Mock()
    engine.atom_enabled = False
    engine.atom_store = None
    engine.config = {"memory_dedup": {"merge_sources": True, "max_source_messages": 2}}
    engine.get_memory = AsyncMock(
        return_value={
            "id": 5,
            "text": "old summary",
            "metadata": {
                "importance": 0.4,
                "topics": ["a"],
                "dedup_merge_count": 1,
            },
        }
    )
    engine.update_memory = AsyncMock(return_value=True)
    engine.get_memory_source = AsyncMock(
        return_value=[{"role": "user", "content": "old"}]
    )
    engine.save_memory_source = AsyncMock()

    merged = await engine.merge_duplicate_memory(
        5,
        importance=0.9,
        metadata={
            "topics": ["a", "b"],
            "key_facts": ["f1"],
            "source_window": {"start_index": 1},
        },
        source_messages=[
            {"role": "user", "content": "new"},
            {"role": "user", "content": "old"},
        ],
    )

    assert merged is True
    memory_id, payload = engine.update_memory.await_args.args
    assert memory_id == 5
    assert payload["importance"] == pytest.approx(0.9)
    assert payload["metadata"]["topics"] == ["a", "b"]
    assert payload["metadata"]["key_facts"] == ["f1"]
    assert payload["metadata"]["dedup_merge_count"] == 2
    assert payload["metadata"]["source_windows"] == [{"start_index": 1}]
    saved_sources = engine.save_memory_source.await_args.args[1]
    assert saved_sources == [
        {"role": "user", "content": "old"},
        {"role": "user", "content": "new"},
    ]


@pytest.mark.asyncio
async def test_persist_summary_memory_merges_near_duplicate() -> None:
    engine = SimpleNamespace(
        find_similar_memory=AsyncMock(return_value={"id": 7, "similarity": 0.95}),
        merge_duplicate_memory=AsyncMock(return_value=True),
        add_memory=AsyncMock(return_value=8),
    )
    reflection = _reflection_with_engine(engine)

    result = await MemoryReflection._persist_summary_memory(
        reflection,
        session_id="session",
        memory_scope="scope",
        persona_id="persona",
        content="summary",
        metadata={"topics": ["a"]},
        importance=0.7,
        atoms=[],
        source_messages=None,
    )

    assert result == (7, False, 0.95)
    engine.merge_duplicate_memory.assert_awaited_once()
    engine.add_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_persist_summary_memory_creates_when_no_duplicate() -> None:
    engine = SimpleNamespace(
        find_similar_memory=AsyncMock(return_value=None),
        merge_duplicate_memory=AsyncMock(return_value=True),
        add_memory=AsyncMock(return_value=11),
    )
    reflection = _reflection_with_engine(engine)

    result = await MemoryReflection._persist_summary_memory(
        reflection,
        session_id="session",
        memory_scope="scope",
        persona_id="persona",
        content="summary",
        metadata={},
        importance=0.5,
        atoms=[],
        source_messages=None,
    )

    assert result == (11, True, None)
    engine.add_memory.assert_awaited_once()


@pytest.mark.asyncio
async def test_persist_summary_memory_falls_back_when_merge_fails() -> None:
    engine = SimpleNamespace(
        find_similar_memory=AsyncMock(return_value={"id": 7, "similarity": 0.95}),
        merge_duplicate_memory=AsyncMock(return_value=False),
        add_memory=AsyncMock(return_value=12),
    )
    reflection = _reflection_with_engine(engine)

    result = await MemoryReflection._persist_summary_memory(
        reflection,
        session_id="session",
        memory_scope="scope",
        persona_id="persona",
        content="summary",
        metadata={},
        importance=0.5,
        atoms=[],
        source_messages=None,
    )

    assert result == (12, True, None)
    engine.add_memory.assert_awaited_once()
