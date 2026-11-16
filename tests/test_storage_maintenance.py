"""Tests for automatic storage maintenance."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from astrbot_plugin_livingmemory.core.managers.graph_memory_manager import GraphMemoryManager
from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine
from astrbot_plugin_livingmemory.core.models.graph_models import GraphEntry
from astrbot_plugin_livingmemory.core.processors.graph_extractor import GraphExtractor
from astrbot_plugin_livingmemory.core.retrieval.graph_vector_retriever import GraphVectorRetriever
from astrbot_plugin_livingmemory.storage.graph_store import GraphStore
from astrbot_plugin_livingmemory.storage.sqlite_connection import open_connection
from tests.test_memory_engine import _FakeFaissDB


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


@pytest.mark.asyncio
async def test_open_connection_enables_incremental_auto_vacuum_for_new_db(tmp_path: Path):
    db_path = tmp_path / "fresh.db"
    conn = await open_connection(str(db_path), apply_journal_mode=True)
    try:
        cursor = await conn.execute("PRAGMA auto_vacuum")
        row = await cursor.fetchone()
        await cursor.close()
        assert int(row[0]) == 2  # INCREMENTAL
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_maintain_storage_reclaims_bloated_sibling_dbs(tmp_path: Path):
    main_db = tmp_path / "livingmemory.db"
    graph_doc_db = tmp_path / "livingmemory_graph_documents.db"
    conv_db = tmp_path / "conversations.db"

    for bloated_db in (graph_doc_db, conv_db):
        async with aiosqlite.connect(bloated_db) as db:
            await db.execute(
                "CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT)"
            )
            await db.executemany(
                "INSERT INTO documents (doc_id, text, metadata) VALUES (?, ?, ?)",
                [(f"u-{i}", "y" * 500, "{}") for i in range(400)],
            )
            await db.execute("DELETE FROM documents WHERE id > 20")
            await db.commit()

    before_graph_size = graph_doc_db.stat().st_size
    before_conv_size = conv_db.stat().st_size

    engine = MemoryEngine(
        db_path=str(main_db),
        faiss_db=_FakeFaissDB(),
        config={
            "atom_enabled": False,
            "graph_memory_enabled": False,
            "graph_document_path": str(graph_doc_db),
        },
    )
    await engine.initialize()

    class _FakeGraphMgr:
        def __init__(self):
            self.called = 0

        async def cleanup_orphan_vectors(self):
            self.called += 1
            return 3

    engine.graph_memory_manager = _FakeGraphMgr()
    try:
        result = await engine.maintain_storage(
            vacuum=False,
            auto_vacuum_threshold_ratio=0.2,
            auto_vacuum_min_free_bytes=16 * 1024,
        )
        assert result["success"] is True
        assert result["orphan_vectors_removed"] == 3
        assert engine.graph_memory_manager.called == 1
        assert result["bytes_reclaimed"] > 0
        compacted_by_name = {
            Path(item["path"]).name: item for item in result["compacted_databases"]
        }
        assert compacted_by_name["livingmemory_graph_documents.db"]["compacted"] is True
        assert compacted_by_name["conversations.db"]["compacted"] is True
        assert compacted_by_name["livingmemory.db"]["compacted"] is False
        assert graph_doc_db.stat().st_size < before_graph_size
        assert conv_db.stat().st_size < before_conv_size
    finally:
        await engine.close()
