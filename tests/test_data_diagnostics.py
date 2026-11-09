"""D4 数据体检与存储压缩（引擎层）。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import aiosqlite
import pytest
from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine

from .test_memory_engine import _FakeFaissDB


async def _make_engine(tmp_path: Path) -> MemoryEngine:
    engine = MemoryEngine(
        db_path=str(tmp_path / "memory.db"),
        faiss_db=_FakeFaissDB(),
        config={"atom_enabled": False, "graph_memory_enabled": False},
    )
    await engine.initialize()
    return engine


@pytest.mark.asyncio
async def test_get_diagnostics_reports_health_and_storage(tmp_path: Path):
    engine = await _make_engine(tmp_path)
    now = time.time()
    metadata = json.dumps({"status": "active", "session_id": "s1", "importance": 0.5})
    await engine.db_connection.execute(
        "INSERT INTO documents "
        "(id, doc_id, text, metadata, created_at, updated_at) "
        "VALUES (1, 'uuid-1', 'hello', ?, datetime('now'), datetime('now'))",
        (metadata,),
    )
    for key, value in (
        ("needs_index_rebuild", "true"),
        ("pending_documents_count", "3"),
        ("migrated_umo_livingmemory:user:u1", "true"),
    ):
        await engine.db_connection.execute(
            "INSERT OR REPLACE INTO migration_status (key, value, updated_at) "
            "VALUES (?, ?, datetime('now'))",
            (key, value),
        )
    await engine.db_connection.commit()

    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    (backups_dir / "livingmemory_backup_1.db").write_bytes(b"x" * 16)
    (tmp_path / "decay_state.json").write_text(
        json.dumps({"last_decay_date": "2026-11-08", "last_decay_timestamp": now}),
        encoding="utf-8",
    )

    diagnostics = await engine.get_diagnostics()

    assert diagnostics["db_version"] == 9
    assert diagnostics["quick_check"] == "ok"
    assert diagnostics["documents"] == {
        "total": 1,
        "active": 1,
        "archived": 0,
        "deleted": 0,
    }
    assert diagnostics["migration"]["pending"] == ["needs_index_rebuild"]
    assert diagnostics["migration"]["pending_documents"] == 3
    assert diagnostics["migration"]["legacy_session_markers"] == 1
    assert diagnostics["graph"] is None
    assert diagnostics["faiss"] == {"main": None, "graph": None}
    assert diagnostics["backups"]["count"] == 1
    assert diagnostics["backups"]["bytes"] == 16
    assert diagnostics["backups"]["latest_at"] is not None
    assert diagnostics["last_decay"]["date"] == "2026-11-08"
    assert diagnostics["db_bytes"] is not None
    assert diagnostics["wal_bytes"] is not None
    assert diagnostics["data_dir_bytes"] is not None
    assert diagnostics["data_dir_bytes"] >= 16
    await engine.close()


@pytest.mark.asyncio
async def test_get_diagnostics_without_database_connection(tmp_path: Path):
    engine = await _make_engine(tmp_path)
    await engine.db_connection.close()
    engine.db_connection = None

    diagnostics = await engine.get_diagnostics()

    assert diagnostics["db_version"] is None
    assert diagnostics["quick_check"] is None
    assert diagnostics["documents"] is None
    assert diagnostics["migration"] == {
        "pending": [],
        "pending_documents": None,
        "legacy_session_markers": 0,
    }
    await engine.close()


@pytest.mark.asyncio
async def test_get_diagnostics_quick_check_error_does_not_raise(tmp_path: Path):
    engine = await _make_engine(tmp_path)
    await engine.db_connection.close()

    diagnostics = await engine.get_diagnostics()

    assert isinstance(diagnostics["quick_check"], str)
    assert diagnostics["quick_check"].startswith("error:")
    engine.db_connection = None
    await engine.close()


@pytest.mark.asyncio
async def test_compact_storage_vacuums_and_keeps_database_readable(tmp_path: Path):
    db_path = tmp_path / "compact.db"
    async with aiosqlite.connect(db_path) as database:
        await database.execute(
            "CREATE TABLE payloads (id INTEGER PRIMARY KEY, body TEXT)"
        )
        await database.executemany(
            "INSERT INTO payloads (body) VALUES (?)",
            [("x" * 400,) for _ in range(300)],
        )
        await database.execute("DELETE FROM payloads WHERE id > 50")
        await database.commit()
    size_before = db_path.stat().st_size

    engine = MemoryEngine(db_path=str(db_path), faiss_db=_FakeFaissDB())
    result = await engine.compact_storage()

    assert result["success"] is True
    assert result["db_size_after"] < size_before
    assert result["bytes_reclaimed"] > 0
    async with aiosqlite.connect(db_path) as database:
        cursor = await database.execute("PRAGMA integrity_check")
        assert (await cursor.fetchone())[0] == "ok"
        cursor = await database.execute("SELECT COUNT(*) FROM payloads")
        assert (await cursor.fetchone())[0] == 50
    await engine.close()


@pytest.mark.asyncio
async def test_compact_storage_rejects_missing_database(tmp_path: Path):
    engine = MemoryEngine(
        db_path=str(tmp_path / "missing.db"),
        faiss_db=_FakeFaissDB(),
    )

    with pytest.raises(RuntimeError, match="database file not found"):
        await engine.compact_storage()
    await engine.close()
