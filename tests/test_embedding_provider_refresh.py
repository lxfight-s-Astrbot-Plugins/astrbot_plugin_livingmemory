"""保存 Embedding Provider 配置后，插件下一次嵌入操作应使用新实例（#441）。

这些用例只通过公开入口（写入、召回、近重复检测）验证行为：向量库里的
``embedding_provider`` 必须已经指向解析器返回的当前实例，而不是启动时缓存的
旧实例。私有同步方法本身不作为断言对象。
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


def _engine(tmp_path: Path, resolver, *, faiss_db=None, graph_db=None) -> MemoryEngine:
    return MemoryEngine(
        db_path=str(tmp_path / "memory.db"),
        faiss_db=faiss_db if faiss_db is not None else _VecDB(_Provider()),
        graph_vector_db=graph_db,
        config={},
        embedding_provider_resolver=resolver,
    )


@pytest.mark.asyncio
async def test_writing_a_memory_uses_the_current_provider(tmp_path: Path):
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
async def test_recall_uses_the_current_provider(tmp_path: Path):
    old = _Provider(provider_id="old")
    new = _Provider(provider_id="new")
    faiss_db = _VecDB(old)
    engine = _engine(tmp_path, lambda: new, faiss_db=faiss_db)
    engine._search_memories_impl = AsyncMock(return_value=[])

    await engine.search_memories("query")

    assert faiss_db.embedding_provider is new


@pytest.mark.asyncio
async def test_duplicate_check_uses_the_current_provider(tmp_path: Path):
    old = _Provider(provider_id="old")
    new = _Provider(provider_id="new")
    faiss_db = _VecDB(old)
    engine = _engine(tmp_path, lambda: new, faiss_db=faiss_db)
    engine.vector_retriever = Mock()
    engine.vector_retriever.search = AsyncMock(return_value=[])

    assert await engine.find_similar_memory("summary") is None
    assert faiss_db.embedding_provider is new


@pytest.mark.asyncio
async def test_unresolvable_provider_keeps_the_cached_instance(tmp_path: Path):
    old = _Provider()
    faiss_db = _VecDB(old)
    engine = _engine(tmp_path, lambda: None, faiss_db=faiss_db)
    engine._search_memories_impl = AsyncMock(return_value=[])

    await engine.search_memories("query")

    assert faiss_db.embedding_provider is old


@pytest.mark.asyncio
async def test_resolver_failure_does_not_break_recall(tmp_path: Path):
    """宿主 Provider 管理器异常时按旧实例继续工作，不抛给调用方。"""
    old = _Provider()
    faiss_db = _VecDB(old)

    def _boom():
        raise RuntimeError("provider manager not ready")

    engine = _engine(tmp_path, _boom, faiss_db=faiss_db)
    engine._search_memories_impl = AsyncMock(return_value=[])

    assert await engine.search_memories("query") == []
    assert faiss_db.embedding_provider is old
