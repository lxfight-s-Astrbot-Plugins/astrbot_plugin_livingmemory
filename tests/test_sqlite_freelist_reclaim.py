"""Tests for SQLite freelist reclamation and /lmem diag compact across all three DBs."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine
from astrbot_plugin_livingmemory.storage.sqlite_connection import open_connection


class _FakeFaissDB:
    def __init__(self):
        self.embedding_storage = None
        self.document_storage = None


@pytest.mark.asyncio
async def test_open_connection_enables_incremental_auto_vacuum_for_new_db(
    tmp_path: Path,
):
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

    try:
        result = await engine.maintain_storage(
            vacuum=False,
            auto_vacuum_threshold_ratio=0.2,
            auto_vacuum_min_free_bytes=16 * 1024,
        )
        assert result["success"] is True
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



@pytest.mark.asyncio
async def test_compact_storage_converts_and_compacts_all_three_databases(
    tmp_path: Path,
):
    main_db = tmp_path / "livingmemory.db"
    graph_db = tmp_path / "livingmemory_graph_documents.db"
    conv_db = tmp_path / "conversations.db"

    for db_file in (main_db, graph_db, conv_db):
        async with aiosqlite.connect(db_file) as db:
            await db.execute(
                "CREATE TABLE payloads (id INTEGER PRIMARY KEY, body TEXT)"
            )
            await db.executemany(
                "INSERT INTO payloads (body) VALUES (?)",
                [("z" * 500,) for _ in range(300)],
            )
            await db.execute("DELETE FROM payloads WHERE id > 15")
            await db.commit()

    before_sizes = {
        db_file: db_file.stat().st_size for db_file in (main_db, graph_db, conv_db)
    }

    engine = MemoryEngine(
        db_path=str(main_db),
        faiss_db=_FakeFaissDB(),
        config={
            "atom_enabled": False,
            "graph_memory_enabled": False,
            "graph_document_path": str(graph_db),
        },
    )
    result = await engine.compact_storage()
    try:
        assert result["success"] is True
        assert result["bytes_reclaimed"] > 0
        assert len(result["databases"]) == 3
        for db_file in (main_db, graph_db, conv_db):
            assert db_file.stat().st_size < before_sizes[db_file]
            async with aiosqlite.connect(db_file) as check_db:
                mode_row = await (
                    await check_db.execute("PRAGMA auto_vacuum")
                ).fetchone()
                assert mode_row[0] == 2
                cnt_row = await (
                    await check_db.execute("SELECT COUNT(*) FROM payloads")
                ).fetchone()
                assert cnt_row[0] == 15
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_compact_storage_truncates_post_vacuum_wal_on_wal_mode_db(
    tmp_path: Path,
):
    main_db = tmp_path / "livingmemory.db"
    async with aiosqlite.connect(main_db, isolation_level=None) as db:
        await db.execute("PRAGMA journal_mode = WAL")
        await db.execute("CREATE TABLE payloads (id INTEGER PRIMARY KEY, body TEXT)")
        await db.executemany(
            "INSERT INTO payloads (body) VALUES (?)",
            [("q" * 600,) for _ in range(300)],
        )
        await db.execute("DELETE FROM payloads WHERE id > 10")

    engine = MemoryEngine(db_path=str(main_db), faiss_db=_FakeFaissDB())
    try:
        result = await engine.compact_storage()
        assert result["success"] is True
        assert result["wal_size_after"] == 0
        assert result["bytes_reclaimed"] > 0
    finally:
        await engine.close()
