"""Write-time near-duplicate detection and merging."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from astrbot.core.db.vec_db.faiss_impl.vec_db import FaissVecDB
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
    reflection.consolidation_manager = None
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


# ==================== R3-c embedding 复用 ====================


class _CountingEmbeddingProvider:
    """确定性向量 + 调用计数/文本记录，用于断言 embedding 调用次数。"""

    def __init__(self, dimension: int = 8):
        self.dimension = dimension
        self.calls = 0
        self.texts: list[str] = []

    async def get_embedding(self, text: str) -> list[float]:
        self.calls += 1
        self.texts.append(text)
        vector = [0.0] * self.dimension
        for index, byte in enumerate(text.encode("utf-8")):
            vector[index % self.dimension] += ((byte % 31) + 1) / 31.0
        norm = sum(value * value for value in vector) ** 0.5 or 1.0
        return [value / norm for value in vector]

    def get_dim(self) -> int:
        return self.dimension


async def _make_real_engine(tmp_path: Path, provider: _CountingEmbeddingProvider):
    faiss_db = FaissVecDB(
        doc_store_path=str(tmp_path / "dedup-documents.db"),
        index_store_path=str(tmp_path / "dedup.index"),
        embedding_provider=provider,
    )
    await faiss_db.initialize()
    engine = MemoryEngine(
        db_path=str(tmp_path / "dedup-memory.db"),
        faiss_db=faiss_db,
        graph_vector_db=None,
        config={},
    )
    await engine.initialize()
    return engine, faiss_db


def _reflection_with_real_engine(engine, *, dedup_enabled: bool = True):
    reflection = MemoryReflection.__new__(MemoryReflection)
    reflection.memory_engine = engine
    reflection.consolidation_manager = None
    reflection.config_manager = SimpleNamespace(
        get=lambda key, default=None: {
            "memory_dedup.enabled": dedup_enabled,
            # 阈值拉高，保证测试内容不会与种子记忆判定为重复
            "memory_dedup.similarity_threshold": 0.9999,
            "memory_dedup.max_candidates": 3,
        }.get(key, default)
    )
    return reflection


async def _persist_summary(reflection, content: str = "用户计划下周去杭州出差"):
    return await MemoryReflection._persist_summary_memory(
        reflection,
        session_id="session",
        memory_scope="scope",
        persona_id="persona",
        content=content,
        metadata={},
        importance=0.6,
        atoms=[],
        source_messages=None,
    )


@pytest.mark.asyncio
async def test_dedup_then_insert_embeds_same_text_once(tmp_path):
    """去重检索算过的向量直接用于写入：同一文本只调用一次 embedding。"""
    provider = _CountingEmbeddingProvider()
    engine, faiss_db = await _make_real_engine(tmp_path, provider)
    try:
        # 预置一条无关记忆，让向量索引非空，去重检索才会真正计算查询向量
        await engine.add_memory(
            "完全无关的旧记忆内容", session_id="scope", persona_id="persona"
        )
        provider.calls = 0
        provider.texts.clear()

        memory_id, created, similarity = await _persist_summary(
            _reflection_with_real_engine(engine)
        )

        assert created is True
        assert similarity is None
        assert memory_id is not None
        assert provider.calls == 1
        assert provider.texts == ["用户计划下周去杭州出差"]
    finally:
        await engine.close()
        await faiss_db.close()


@pytest.mark.asyncio
async def test_insert_embeds_once_when_dedup_index_empty(tmp_path):
    """去重未命中（索引为空时无需检索向量）：写入仍只调用一次 embedding。"""
    provider = _CountingEmbeddingProvider()
    engine, faiss_db = await _make_real_engine(tmp_path, provider)
    try:
        memory_id, created, _similarity = await _persist_summary(
            _reflection_with_real_engine(engine)
        )

        assert created is True
        assert memory_id is not None
        assert provider.calls == 1
    finally:
        await engine.close()
        await faiss_db.close()


@pytest.mark.asyncio
async def test_dedup_disabled_embeds_only_on_insert(tmp_path):
    """去重关闭时不额外计算向量，仅写入时调用一次 embedding。"""
    provider = _CountingEmbeddingProvider()
    engine, faiss_db = await _make_real_engine(tmp_path, provider)
    engine.find_similar_memory = AsyncMock(return_value=(None, None))
    try:
        _memory_id, created, _similarity = await _persist_summary(
            _reflection_with_real_engine(engine, dedup_enabled=False)
        )

        assert created is True
        engine.find_similar_memory.assert_not_awaited()
        assert provider.calls == 1
    finally:
        await engine.close()
        await faiss_db.close()


@pytest.mark.asyncio
async def test_precomputed_embedding_insert_matches_host_fallback(tmp_path):
    """预计算向量写入与宿主 fallback 路径行为一致：文本完整入库且可被检索。"""
    provider = _CountingEmbeddingProvider()
    engine, faiss_db = await _make_real_engine(tmp_path, provider)
    try:
        fallback_id = await engine.add_memory(
            "fallback 路径记忆", session_id="scope", persona_id="persona"
        )
        embedding = await provider.get_embedding("预计算路径记忆")
        calls_before = provider.calls

        precomputed_id = await engine.add_memory(
            "预计算路径记忆",
            session_id="scope",
            persona_id="persona",
            embedding=embedding,
        )

        assert provider.calls == calls_before
        assert precomputed_id != fallback_id
        stored = await engine.get_memory(precomputed_id)
        assert stored is not None
        assert stored["text"] == "预计算路径记忆"

        duplicate = await engine.find_similar_memory(
            "预计算路径记忆",
            session_id="scope",
            persona_id="persona",
            threshold=0.99,
        )
        assert duplicate is not None
        assert duplicate["id"] == precomputed_id
    finally:
        await engine.close()
        await faiss_db.close()
