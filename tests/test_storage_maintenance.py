"""Tests for automatic storage maintenance."""

from __future__ import annotations

from pathlib import Path

import pytest

from astrbot_plugin_livingmemory.core.managers.graph_memory_manager import GraphMemoryManager
from astrbot_plugin_livingmemory.core.models.graph_models import GraphEntry
from astrbot_plugin_livingmemory.core.processors.graph_extractor import GraphExtractor
from astrbot_plugin_livingmemory.core.retrieval.graph_vector_retriever import GraphVectorRetriever
from astrbot_plugin_livingmemory.storage.graph_store import GraphStore


class _FakeGraphVectorStorage:
    """Minimal FAISS-like storage supporting bulk delete + SQLAlchemy-free listing."""

    def __init__(self, docs: dict[int, dict]):
        self.docs = dict(docs)
        self.deleted_embedding_batches: list[list[int]] = []
        self.deleted_uuids: list[str] = []
        self.embedding_storage = self
        self.document_storage = self

    async def delete(self, doc_ids: list[int]):
        self.deleted_embedding_batches.append(list(doc_ids))

    async def get_documents(self, metadata_filters=None, ids=None, offset=0, limit=100):
        if ids is not None:
            return [self.docs[i] for i in ids if i in self.docs]
        items = list(self.docs.values())
        return items[offset : (offset + limit) if limit is not None else None]

    async def delete_document_by_doc_id(self, doc_uuid: str):
        self.deleted_uuids.append(doc_uuid)
        for key in [k for k, v in self.docs.items() if v.get("doc_id") == doc_uuid]:
            del self.docs[key]


@pytest.mark.asyncio
async def test_cleanup_orphan_vectors_removes_only_unreferenced_docs(tmp_path: Path):
    graph_store = GraphStore(str(tmp_path / "lmem.db"))
    await graph_store.initialize()

    fake_db = _FakeGraphVectorStorage(
        {
            10: {"id": 10, "doc_id": "u-10", "text": "active", "metadata": {}},
            20: {"id": 20, "doc_id": "u-20", "text": "orphan-1", "metadata": {}},
            30: {"id": 30, "doc_id": "u-30", "text": "orphan-2", "metadata": {}},
        }
    )
    retriever = GraphVectorRetriever(fake_db)
    manager = GraphMemoryManager(graph_store, retriever, GraphExtractor())

    # Insert one entry referencing vector_doc_id=10
    entry = GraphEntry(
        entry_key="k-10",
        source_memory_id=1,
        session_id="s1",
        persona_id="p1",
        entry_type="fact",
        content="kept fact",
        metadata={},
    )
    entry_ids = await graph_store.store_graph_structure([], [], [entry])
    await graph_store.update_entry_vector_doc_ids({entry_ids[0]: 10})

    removed = await manager.cleanup_orphan_vectors()
    assert removed == 2
    assert list(fake_db.docs.keys()) == [10]
    assert sorted(fake_db.deleted_uuids) == ["u-20", "u-30"]

    # Missing IDs raise when missing_ok=False, succeed when missing_ok=True.
    with pytest.raises(RuntimeError, match="批量图向量删除未找到文档"):
        await retriever.delete_entries_batch({0: [999]}, missing_ok=False)
    await retriever.delete_entries_batch({0: [999]}, missing_ok=True)

    # Skips cleanly while rebuild is active.
    manager._rebuild_active = True
    assert await manager.cleanup_orphan_vectors() == 0

    await graph_store.close()
