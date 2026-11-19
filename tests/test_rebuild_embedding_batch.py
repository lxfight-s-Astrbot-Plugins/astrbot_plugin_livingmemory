"""`/lmem rebuild-graph` 的 embedding 批量上限（#429）。

断言行为：重建图向量时，单次交给向量库的条目数不超过
``index_rebuild_settings.embedding_batch_size``，否则 DashScope 这类每批上限
20 条的 Provider 会直接拒绝请求。
"""

import json
from pathlib import Path

import pytest

from astrbot_plugin_livingmemory.core.managers.graph_memory_manager import (
    GraphMemoryManager,
)
from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine
from astrbot_plugin_livingmemory.core.processors.graph_extractor import GraphExtractor
from astrbot_plugin_livingmemory.core.retrieval.graph_vector_retriever import (
    GraphVectorRetriever,
)
from astrbot_plugin_livingmemory.storage.graph_store import GraphStore


class _RecordingVecDB:
    """Minimal vector store that records how many entries each insert carries."""

    def __init__(self) -> None:
        self.batch_sizes: list[int] = []
        self._next_id = 1

    async def insert_batch(self, contents, metadatas):
        self.batch_sizes.append(len(contents))
        ids = []
        for _ in contents:
            ids.append(self._next_id)
            self._next_id += 1
        return ids

    async def insert(self, content, metadata):
        return (await self.insert_batch([content], [metadata]))[0]

    async def delete_documents(self, metadata_filters=None):
        return None


class _DocumentVecDB:
    """Document-route vector store placeholder (unused by the graph rebuild)."""


async def _engine_with_documents(tmp_path: Path, *, batch_size: int, memories: int):
    engine = MemoryEngine(
        db_path=str(tmp_path / "memory.db"),
        faiss_db=_DocumentVecDB(),
        config={
            "graph_memory_enabled": True,
            "index_rebuild_embedding_batch_size": batch_size,
        },
    )
    await engine.initialize()
    graph_store = GraphStore(str(tmp_path / "graph.db"))
    await graph_store.initialize()
    vec_db = _RecordingVecDB()
    engine.graph_store = graph_store
    engine.graph_memory_manager = GraphMemoryManager(
        graph_store,
        GraphVectorRetriever(vec_db),
        GraphExtractor(),
    )
    rows = [
        (
            index,
            f"uuid-{index}",
            f"memory {index} about topic {index}",
            json.dumps(
                {
                    "status": "active",
                    "topics": [f"topic-{index}"],
                    "key_facts": [f"fact {index}"],
                    "participants": ["Alice"],
                }
            ),
        )
        for index in range(1, memories + 1)
    ]
    await engine.db_connection.executemany(
        "INSERT INTO documents(id, doc_id, text, metadata) VALUES (?, ?, ?, ?)", rows
    )
    await engine.db_connection.commit()
    return engine, vec_db, graph_store


@pytest.mark.asyncio
async def test_graph_rebuild_respects_the_configured_embedding_batch(tmp_path: Path):
    engine, vec_db, graph_store = await _engine_with_documents(
        tmp_path, batch_size=4, memories=40
    )
    try:
        result = await engine.rebuild_graph_index()

        assert result["rebuilt"] == 40
        assert vec_db.batch_sizes, "rebuild produced no vector inserts"
        assert max(vec_db.batch_sizes) <= 4
        # 40 条记忆必然超过单批上限，说明限制真的生效了
        assert sum(vec_db.batch_sizes) >= 40
    finally:
        await engine.close()
        await graph_store.close()


@pytest.mark.asyncio
async def test_graph_rebuild_defaults_to_eight_entries_per_batch(tmp_path: Path):
    engine, vec_db, graph_store = await _engine_with_documents(
        tmp_path, batch_size=8, memories=30
    )
    try:
        await engine.rebuild_graph_index()

        assert max(vec_db.batch_sizes) <= 8
    finally:
        await engine.close()
        await graph_store.close()


@pytest.mark.asyncio
async def test_graph_rebuild_clamps_invalid_batch_sizes(tmp_path: Path):
    """0 会钳到 1，避免配置错误导致空批量或无限循环。"""
    engine, vec_db, graph_store = await _engine_with_documents(
        tmp_path, batch_size=0, memories=6
    )
    try:
        await engine.rebuild_graph_index()

        assert max(vec_db.batch_sizes) == 1
    finally:
        await engine.close()
        await graph_store.close()
