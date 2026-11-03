"""Real SQLite ownership migration and legacy source boundaries."""

import json
import sqlite3

import aiosqlite
import pytest

from astrbot_plugin_livingmemory.core.managers.adapter_migrator import AdapterMigrator
from astrbot_plugin_livingmemory.core.memory_scope import infer_adapter_id


def test_adapter_inference_uses_provenance_and_keeps_ambiguous_sources():
    instances = {"qq-a": "qq", "qq-b": "qq", "wechat-a": "wechat"}
    assert (
        infer_adapter_id(
            {"source_session_id": "qq-a:FriendMessage:1"}, instances=instances
        )
        == "qq-a"
    )
    assert (
        infer_adapter_id({"session_id": "livingmemory:global"}, instances=instances)
        is None
    )
    assert (
        infer_adapter_id(
            {"source_session_id": "qq:FriendMessage:1"}, instances=instances
        )
        is None
    )
    assert (
        infer_adapter_id(
            {},
            [
                {"session_id": "qq-a:FriendMessage:1"},
                {"session_id": "wechat-a:FriendMessage:2"},
            ],
            instances,
        )
        is None
    )


@pytest.mark.asyncio
async def test_migration_preserves_content_ids_and_synchronizes_derived_metadata(
    tmp_path,
):
    path = tmp_path / "livingmemory.db"
    graph_path = tmp_path / "graph.db"
    old = {
        "session_id": "livingmemory:global",
        "source_session_id": "qq-a:FriendMessage:1",
        "persona_id": "p",
    }
    async with aiosqlite.connect(path) as db:
        await db.executescript("""
            CREATE TABLE documents(id INTEGER PRIMARY KEY, text TEXT, metadata TEXT);
            CREATE TABLE memory_sources(memory_id INTEGER PRIMARY KEY, source_json TEXT);
            CREATE TABLE memory_atoms(parent_memory_id INTEGER, content TEXT, metadata TEXT);
            CREATE TABLE graph_entries(source_memory_id INTEGER, content TEXT, metadata TEXT);
            CREATE TABLE migration_status(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
        """)
        await db.execute(
            "INSERT INTO documents VALUES(7, 'original text', ?)", (json.dumps(old),)
        )
        await db.execute(
            "INSERT INTO documents VALUES(8, 'unknown source', '{\"session_id\":\"livingmemory:global\"}')"
        )
        await db.execute("INSERT INTO memory_atoms VALUES(7, 'fact', '{}')")
        await db.execute("INSERT INTO graph_entries VALUES(7, 'entry', '{}')")
        await db.commit()
    async with aiosqlite.connect(graph_path) as db:
        await db.execute(
            "CREATE TABLE documents(id INTEGER PRIMARY KEY, text TEXT, metadata TEXT)"
        )
        await db.execute(
            "INSERT INTO documents VALUES(21, 'graph text', '{\"source_memory_id\":7}')"
        )
        await db.commit()
    migrator = AdapterMigrator(str(path), str(graph_path), {"qq-a": "qq"})
    await migrator.migrate()
    await migrator.migrate()
    async with aiosqlite.connect(path) as db:
        row = await (
            await db.execute("SELECT id, text, metadata FROM documents WHERE id=7")
        ).fetchone()
        assert row[:2] == (7, "original text")
        assert json.loads(row[2]) == dict(old, adapter_id="qq-a")
        assert await (
            await db.execute(
                "SELECT json_extract(metadata, '$.adapter_id') FROM memory_atoms"
            )
        ).fetchone() == ("qq-a",)
    async with aiosqlite.connect(graph_path) as db:
        assert await (
            await db.execute(
                "SELECT id, text, json_extract(metadata, '$.adapter_id') FROM documents"
            )
        ).fetchone() == (21, "graph text", "qq-a")
    rows = await migrator.survey()
    assert any(row["adapter_id"] is None and row["count"] == 1 for row in rows)
    snapshots = list((tmp_path / "backups").glob("adapter_ownership_*"))
    assert len(snapshots) == 1
    assert (snapshots[0] / path.name).exists()
    result = await migrator.assign({8: "qq-a"})
    assert result["updated"] == 1


@pytest.mark.asyncio
async def test_failed_graph_update_rolls_back_parent_ownership(tmp_path):
    main = tmp_path / "main.db"
    graph = tmp_path / "graph.db"
    async with aiosqlite.connect(main) as db:
        await db.executescript("""
            CREATE TABLE documents(id INTEGER PRIMARY KEY, metadata TEXT);
            INSERT INTO documents VALUES(7, '{}');
        """)
    async with aiosqlite.connect(graph) as db:
        await db.executescript("""
            CREATE TABLE documents(id INTEGER PRIMARY KEY, metadata TEXT);
            INSERT INTO documents VALUES(9, '{"source_memory_id":7}');
            CREATE TRIGGER reject_owner BEFORE UPDATE ON documents BEGIN
                SELECT RAISE(ABORT, 'graph update failed');
            END;
        """)
    migrator = AdapterMigrator(str(main), str(graph))
    with pytest.raises(sqlite3.IntegrityError, match="graph update failed"):
        await migrator.assign({7: "qq-a"})
    async with aiosqlite.connect(main) as db:
        assert await (
            await db.execute("SELECT metadata FROM documents")
        ).fetchone() == ("{}",)


@pytest.mark.asyncio
async def test_graph_enabled_after_initial_migration_inherits_existing_ownership(
    tmp_path,
):
    main = tmp_path / "main.db"
    graph = tmp_path / "graph.db"
    async with aiosqlite.connect(main) as db:
        await db.executescript("""
            CREATE TABLE documents(id INTEGER PRIMARY KEY, metadata TEXT);
            CREATE TABLE memory_sources(memory_id INTEGER PRIMARY KEY, source_json TEXT);
            CREATE TABLE migration_status(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
            INSERT INTO documents VALUES(7, '{"source_session_id":"qq-a:FriendMessage:1"}');
        """)
    await AdapterMigrator(str(main)).migrate()
    async with aiosqlite.connect(graph) as db:
        await db.executescript("""
            CREATE TABLE documents(id INTEGER PRIMARY KEY, metadata TEXT);
            INSERT INTO documents VALUES(19, '{"source_memory_id":"7"}');
        """)
    await AdapterMigrator(str(main), str(graph)).migrate()
    async with aiosqlite.connect(graph) as db:
        assert await (
            await db.execute(
                "SELECT json_extract(metadata, '$.adapter_id') FROM documents"
            )
        ).fetchone() == ("qq-a",)
