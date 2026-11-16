"""Tests for orphan graph-vector cleanup, multi-DB freelist reclaim, and compact backups."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from astrbot_plugin_livingmemory.core.managers.backup_manager import BackupManager
from astrbot_plugin_livingmemory.core.managers.graph_memory_manager import GraphMemoryManager
from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine
from astrbot_plugin_livingmemory.core.models.graph_models import GraphEntry
from astrbot_plugin_livingmemory.core.processors.graph_extractor import GraphExtractor
from astrbot_plugin_livingmemory.core.retrieval.graph_vector_retriever import GraphVectorRetriever
from astrbot_plugin_livingmemory.storage.db_migration import DBMigration
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
        await retriever.delete_entries_by_ids([999], missing_ok=False)
    assert await retriever.delete_entries_by_ids([999], missing_ok=True) == 0

    # Skips cleanly while rebuild is active.
    manager._rebuild_active = True
    assert await manager.cleanup_orphan_vectors() == 0

    await graph_store.close()


@pytest.mark.asyncio
async def test_cleanup_orphan_vectors_compat_path_deletes_by_id_not_source_zero(
    tmp_path: Path,
):
    """Fallback path without bulk delete primitives must delete by ID, not source_memory_id=0."""
    graph_store = GraphStore(str(tmp_path / "lmem_compat.db"))
    await graph_store.initialize()

    docs = {
        10: {"id": 10, "doc_id": "u-10", "text": "active", "metadata": {"source_memory_id": 1}},
        20: {"id": 20, "doc_id": "u-20", "text": "orphan", "metadata": {"source_memory_id": 99}},
    }

    async def _delete_documents(metadata_filters=None):
        # Simulate legacy AstrBot delete_documents filtering by source_memory_id=0 (no-op).
        sid = (metadata_filters or {}).get("source_memory_id")
        for key in [k for k, v in list(docs.items()) if v["metadata"].get("source_memory_id") == sid]:
            del docs[key]

    async def _get_documents(metadata_filters=None, ids=None, offset=0, limit=100):
        if ids is not None:
            return [docs[i] for i in ids if i in docs]
        items = list(docs.values())
        return items[offset : (offset + limit) if limit is not None else None]

    async def _delete_by_uuid(doc_uuid: str):
        for key in [k for k, v in list(docs.items()) if v.get("doc_id") == doc_uuid]:
            del docs[key]

    # Notice: no embedding_storage.delete -> delete_faiss_documents_by_ids returns None.
    compat_db = SimpleNamespace(
        document_storage=SimpleNamespace(get_documents=_get_documents),
        delete_documents=AsyncMock(side_effect=_delete_documents),
        delete=AsyncMock(side_effect=_delete_by_uuid),
    )
    retriever = GraphVectorRetriever(compat_db)
    manager = GraphMemoryManager(graph_store, retriever, GraphExtractor())

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
    assert removed == 1
    assert list(docs.keys()) == [10]
    compat_db.delete_documents.assert_not_awaited()
    compat_db.delete.assert_awaited_once_with("u-20")

    # Missing IDs in compat path report 0 deleted instead of claiming requested count.
    assert await retriever.delete_entries_by_ids([999], missing_ok=True) == 0

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

    # graph_doc_db uses INCREMENTAL auto_vacuum -> auto-reclaimed when vacuum=False.
    # conv_db uses legacy auto_vacuum=NONE -> detected as needs_manual_compact, only compacted when vacuum=True.
    for db_file, incremental in ((graph_doc_db, True), (conv_db, False)):
        async with aiosqlite.connect(db_file) as db:
            if incremental:
                await db.execute("PRAGMA auto_vacuum = INCREMENTAL")
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
        assert compacted_by_name["conversations.db"]["compacted"] is False
        assert compacted_by_name["conversations.db"]["needs_manual_compact"] is True
        assert compacted_by_name["livingmemory.db"]["compacted"] is False
        assert graph_doc_db.stat().st_size < before_graph_size
        assert conv_db.stat().st_size == before_conv_size

        # Explicit vacuum=True converts and compacts legacy auto_vacuum=NONE DBs too.
        forced = await engine.maintain_storage(vacuum=True)
        assert forced["success"] is True
        forced_by_name = {
            Path(item["path"]).name: item for item in forced["compacted_databases"]
        }
        assert forced_by_name["conversations.db"]["compacted"] is True
        assert conv_db.stat().st_size < before_conv_size
    finally:
        await engine.close()


def test_backup_manager_uses_compact_sqlite_snapshots_without_wal(tmp_path: Path):
    db_path = tmp_path / "livingmemory_graph_documents.db"
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, text TEXT)")
        conn.executemany(
            "INSERT INTO documents (text) VALUES (?)",
            [("z" * 600,) for _ in range(400)],
        )
        conn.execute("DELETE FROM documents WHERE id > 10")
        conn.execute("INSERT INTO documents (id, text) VALUES (999, 'from_wal')")
    finally:
        conn.close()

    # Keep a sibling WAL/SHM file present to verify compacted backups omit sidecars.
    wal_file = tmp_path / "livingmemory_graph_documents.db-wal"
    if not wal_file.exists():
        wal_file.write_bytes(b"wal-bytes")
    raw_size = db_path.stat().st_size

    mgr = BackupManager(str(tmp_path))
    mgr.version_file.write_text("2.12.0", encoding="utf-8")
    backup_dir = Path(mgr.backup_if_needed())

    backed_up_db = backup_dir / "livingmemory_graph_documents.db"
    assert backed_up_db.is_file()
    assert not (backup_dir / "livingmemory_graph_documents.db-wal").exists()
    assert not (backup_dir / "livingmemory_graph_documents.db-shm").exists()
    assert backed_up_db.stat().st_size < raw_size

    check_conn = sqlite3.connect(str(backed_up_db))
    try:
        assert check_conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        texts = [r[0] for r in check_conn.execute("SELECT text FROM documents").fetchall()]
        assert "from_wal" in texts
        assert len(texts) == 11
    finally:
        check_conn.close()


@pytest.mark.asyncio
async def test_db_migration_create_backup_produces_compact_snapshot(tmp_path: Path):
    db_path = tmp_path / "livingmemory.db"
    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, body TEXT)")
        await db.executemany(
            "INSERT INTO items (body) VALUES (?)",
            [("w" * 500,) for _ in range(300)],
        )
        await db.execute("DELETE FROM items WHERE id > 15")
        await db.commit()
    bloated_size = db_path.stat().st_size

    migration = DBMigration(str(db_path))
    backup_path_str = await migration.create_backup()
    assert backup_path_str is not None
    backup_path = Path(backup_path_str)
    assert backup_path.stat().st_size < bloated_size

    async with aiosqlite.connect(backup_path) as check_db:
        row = await (await check_db.execute("PRAGMA integrity_check")).fetchone()
        assert row[0] == "ok"
        cnt = await (await check_db.execute("SELECT COUNT(*) FROM items")).fetchone()
        assert cnt[0] == 15


def test_compact_sqlite_backup_preserves_existing_dest_on_failure(tmp_path: Path):
    src = tmp_path / "corrupt_src.db"
    src.write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)
    dest = tmp_path / "existing.db"
    dest.write_bytes(b"keep-existing-snapshot")

    assert BackupManager._backup_sqlite_compact(src, dest) is False
    assert dest.read_bytes() == b"keep-existing-snapshot"
    assert not any(tmp_path.glob("*.tmp"))
