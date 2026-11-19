"""Tests for orphan graph-vector reconciliation and /lmem diag orphans preview."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from astrbot_plugin_livingmemory.core.command_handler import CommandHandler
from astrbot_plugin_livingmemory.core.managers.graph_memory_manager import (
    GraphMemoryManager,
)
from astrbot_plugin_livingmemory.core.processors.graph_extractor import GraphExtractor
from astrbot_plugin_livingmemory.core.retrieval.graph_vector_retriever import (
    GraphVectorRetriever,
)
from astrbot_plugin_livingmemory.storage.graph_store import GraphStore


class _InMemoryGraphDocumentStorage:
    def __init__(self):
        self.docs: dict[int, dict] = {}
        self.next_id = 1

    async def get_documents(self, metadata_filters=None, ids=None, limit=None):
        if ids is not None:
            return [self.docs[i] for i in ids if i in self.docs]
        items = [self.docs[k] for k in sorted(self.docs)]
        if limit is not None:
            items = items[:limit]
        return items


class _CompatGraphFaissDB:
    def __init__(self):
        self.document_storage = _InMemoryGraphDocumentStorage()
        self.embedding_storage = None
        self.deleted_filters: list[dict] = []

    async def insert(self, content: str, metadata: dict) -> int:
        doc_id = self.document_storage.next_id
        self.document_storage.next_id += 1
        self.document_storage.docs[doc_id] = {
            "id": doc_id,
            "doc_id": f"uuid-{doc_id}",
            "text": content,
            "metadata": dict(metadata),
        }
        return doc_id

    async def delete(self, doc_id: str):
        for key, doc in list(self.document_storage.docs.items()):
            if doc["doc_id"] == doc_id:
                del self.document_storage.docs[key]

    async def delete_documents(self, metadata_filters=None):
        if metadata_filters:
            self.deleted_filters.append(dict(metadata_filters))


class _DummyEvent:
    unified_msg_origin = "test:private:admin-1"

    def plain_result(self, text: str) -> str:
        return text


async def _seed_graph_with_orphans(tmp_path: Path):
    graph_store = GraphStore(str(tmp_path / "livingmemory.db"))
    await graph_store.initialize()
    faiss_db = _CompatGraphFaissDB()
    retriever = GraphVectorRetriever(faiss_db)
    manager = GraphMemoryManager(
        graph_store=graph_store,
        graph_vector_retriever=retriever,
        graph_extractor=GraphExtractor(),
    )

    indexed = await manager.index_memory(
        1,
        "Alice likes tea",
        {"session_id": "s1", "participants": ["Alice"], "topics": ["tea"]},
    )
    assert indexed is True
    live_ids = await graph_store.list_vector_doc_ids()
    assert len(live_ids) == 1
    live_vec_id = live_ids[0]

    orphan_a = await faiss_db.insert("orphan-a", {"source_memory_id": 99})
    orphan_b = await faiss_db.insert("orphan-b", {"source_memory_id": 100})
    return manager, graph_store, faiss_db, live_vec_id, [orphan_a, orphan_b]


@pytest.mark.asyncio
async def test_preview_orphan_vectors_reports_counts_and_samples_without_deleting(
    tmp_path: Path,
):
    manager, graph_store, faiss_db, live_id, orphan_ids = (
        await _seed_graph_with_orphans(tmp_path)
    )
    try:
        preview = await manager.preview_orphan_vectors(sample_limit=5)
        assert preview["rebuild_active"] is False
        assert preview["stored_count"] == 3
        assert preview["referenced_count"] == 1
        assert preview["orphan_count"] == 2
        assert preview["sample_ids"] == orphan_ids
        # Read-only preview must not delete any vector document.
        assert set(faiss_db.document_storage.docs) == {live_id, *orphan_ids}
    finally:
        await graph_store.close()


@pytest.mark.asyncio
async def test_preview_orphan_vectors_skips_while_rebuild_active(tmp_path: Path):
    manager, graph_store, _faiss_db, _live_id, _orphan_ids = (
        await _seed_graph_with_orphans(tmp_path)
    )
    try:
        manager._rebuild_active = True
        preview = await manager.preview_orphan_vectors()
        assert preview["rebuild_active"] is True
        assert preview["orphan_count"] == 0
    finally:
        await graph_store.close()


@pytest.mark.asyncio
async def test_cleanup_orphan_vectors_dry_run_and_apply_on_compat_store(tmp_path: Path):
    manager, graph_store, faiss_db, live_id, orphan_ids = (
        await _seed_graph_with_orphans(tmp_path)
    )
    try:
        dry_count = await manager.cleanup_orphan_vectors(dry_run=True)
        assert dry_count == 2
        assert set(faiss_db.document_storage.docs) == {live_id, *orphan_ids}

        removed = await manager.cleanup_orphan_vectors(batch_size=1, dry_run=False)
        assert removed == 2
        assert set(faiss_db.document_storage.docs) == {live_id}
        assert all(f.get("source_memory_id") != 0 for f in faiss_db.deleted_filters)
    finally:
        await graph_store.close()


@pytest.mark.asyncio
async def test_handle_diag_orphans_previews_candidates_without_deleting(tmp_path: Path):
    manager, graph_store, faiss_db, live_id, orphan_ids = (
        await _seed_graph_with_orphans(tmp_path)
    )
    try:
        engine = SimpleNamespace(graph_memory_manager=manager)
        handler = CommandHandler(
            context=SimpleNamespace(),
            config_manager=SimpleNamespace(get=lambda _k, default=None: default),
            memory_engine=engine,
            conversation_manager=None,
            index_validator=None,
        )
        event = _DummyEvent()

        outputs = [msg async for msg in handler.handle_diag(event, "orphans")]
        assert len(outputs) == 1
        assert "2" in outputs[0]
        assert str(orphan_ids[0]) in outputs[0]
        assert set(faiss_db.document_storage.docs) == {live_id, *orphan_ids}

        manager._rebuild_active = True
        rebuilding_out = [msg async for msg in handler.handle_diag(event, "orphans")]
        assert len(rebuilding_out) == 1
        assert "重建" in rebuilding_out[0] or "rebuild" in rebuilding_out[0].lower()

        engine.graph_memory_manager = None
        disabled_out = [msg async for msg in handler.handle_diag(event, "orphans")]
        assert len(disabled_out) == 1
        assert "未启用" in disabled_out[0] or "not enabled" in disabled_out[0].lower()
    finally:
        await graph_store.close()
