"""Embedding provider refresh after the host rebuilds provider instances (#441).

AstrBot terminates and recreates a provider when its config is saved, so the
instance captured at plugin startup can keep failing with the old credentials.
The engine must rebind its vector stores from a resolver before embedding work.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine


class _Provider:
    def __init__(self, dim: int = 768, provider_id: str = "emb") -> None:
        self.dim = dim
        self.provider_config = {"id": provider_id}

    def get_dim(self) -> int:
        return self.dim


class _VecDB:
    def __init__(self, provider: _Provider, dim: int = 768) -> None:
        self.embedding_provider = provider
        self.embedding_storage = SimpleNamespace(index=SimpleNamespace(d=dim))


def _engine(
    tmp_path: Path,
    resolver,
    *,
    faiss_db=None,
    graph_db=None,
    config: dict | None = None,
) -> MemoryEngine:
    return MemoryEngine(
        db_path=str(tmp_path / "memory.db"),
        faiss_db=faiss_db if faiss_db is not None else _VecDB(_Provider()),
        graph_vector_db=graph_db,
        config=config or {},
        embedding_provider_resolver=resolver,
    )


def test_sync_rebinds_both_vector_stores(tmp_path: Path) -> None:
    old = _Provider(provider_id="old")
    new = _Provider(provider_id="new")
    faiss_db = _VecDB(old)
    graph_db = _VecDB(old)
    engine = _engine(tmp_path, lambda: new, faiss_db=faiss_db, graph_db=graph_db)

    engine._sync_embedding_provider()

    assert faiss_db.embedding_provider is new
    assert graph_db.embedding_provider is new


def test_sync_without_resolver_keeps_cached_instance(tmp_path: Path) -> None:
    old = _Provider()
    faiss_db = _VecDB(old)
    engine = _engine(tmp_path, None, faiss_db=faiss_db)

    engine._sync_embedding_provider()

    assert faiss_db.embedding_provider is old


def test_sync_is_noop_when_instance_is_unchanged(tmp_path: Path) -> None:
    same = _Provider()
    faiss_db = _VecDB(same)
    engine = _engine(tmp_path, lambda: same, faiss_db=faiss_db)

    engine._sync_embedding_provider()

    assert faiss_db.embedding_provider is same


def test_sync_swallows_resolver_errors(tmp_path: Path) -> None:
    old = _Provider()
    faiss_db = _VecDB(old)

    def _raise():
        raise RuntimeError("provider manager not ready")

    engine = _engine(tmp_path, _raise, faiss_db=faiss_db)

    engine._sync_embedding_provider()

    assert faiss_db.embedding_provider is old


def test_sync_keeps_provider_when_resolver_returns_none(tmp_path: Path) -> None:
    old = _Provider()
    faiss_db = _VecDB(old)
    engine = _engine(tmp_path, lambda: None, faiss_db=faiss_db)

    engine._sync_embedding_provider()

    assert faiss_db.embedding_provider is old


def test_sync_warns_when_embedding_dimension_changes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    old = _Provider(dim=768)
    new = _Provider(dim=1024)
    faiss_db = _VecDB(old, dim=768)
    engine = _engine(tmp_path, lambda: new, faiss_db=faiss_db)

    with caplog.at_level("WARNING"):
        engine._sync_embedding_provider()

    assert faiss_db.embedding_provider is new
    assert "重建索引" in caplog.text


@pytest.mark.asyncio
async def test_add_memory_refreshes_provider_before_writing(tmp_path: Path) -> None:
    old = _Provider(provider_id="old")
    new = _Provider(provider_id="new")
    faiss_db = _VecDB(old)
    engine = _engine(tmp_path, lambda: new, faiss_db=faiss_db)
    engine.index_writer = Mock()
    engine.index_writer.add_memory = AsyncMock(side_effect=RuntimeError("stop"))

    with pytest.raises(RuntimeError, match="stop"):
        await engine.add_memory("content", session_id="s1", persona_id=None)

    assert faiss_db.embedding_provider is new


@pytest.mark.asyncio
async def test_search_memories_refreshes_provider_before_retrieval(
    tmp_path: Path,
) -> None:
    old = _Provider(provider_id="old")
    new = _Provider(provider_id="new")
    faiss_db = _VecDB(old)
    engine = _engine(tmp_path, lambda: new, faiss_db=faiss_db)
    engine._search_memories_impl = AsyncMock(return_value=[])

    await engine.search_memories("query")

    assert faiss_db.embedding_provider is new


@pytest.mark.asyncio
async def test_find_similar_memory_refreshes_provider_before_embedding(
    tmp_path: Path,
) -> None:
    old = _Provider(provider_id="old")
    new = _Provider(provider_id="new")
    faiss_db = _VecDB(old)
    engine = _engine(tmp_path, lambda: new, faiss_db=faiss_db)
    engine.vector_retriever = Mock()
    engine.vector_retriever.search = AsyncMock(return_value=[])

    assert await engine.find_similar_memory("summary") is None
    assert faiss_db.embedding_provider is new
