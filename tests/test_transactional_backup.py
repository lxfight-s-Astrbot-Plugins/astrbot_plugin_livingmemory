"""Tests for transactional compact SQLite backups and recovery."""

from __future__ import annotations

import errno
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
import pytest

from astrbot_plugin_livingmemory.core.managers.backup_manager import (
    PLUGIN_VERSION,
    BackupManager,
)
from astrbot_plugin_livingmemory.storage import sqlite_connection as sqlite_conn_mod
from astrbot_plugin_livingmemory.storage.db_migration import DBMigration
from astrbot_plugin_livingmemory.storage.sqlite_connection import backup_sqlite_file


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
        texts = [
            r[0] for r in check_conn.execute("SELECT text FROM documents").fetchall()
        ]
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


def test_backup_if_needed_aborts_on_sqlite_compact_failure_without_clobbering_or_marking_version(
    tmp_path: Path,
):
    valid_db = tmp_path / "livingmemory.db"
    conn = sqlite3.connect(str(valid_db))
    try:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()

    corrupt_db = tmp_path / "conversations.db"
    corrupt_db.write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)
    (tmp_path / "decay_state.json").write_text('{"ok": true}', encoding="utf-8")

    existing_dir = tmp_path / "backups" / "v2.12.0"
    existing_dir.mkdir(parents=True)
    (existing_dir / "previous_marker.txt").write_text(
        "keep-existing-backup", encoding="utf-8"
    )

    mgr = BackupManager(str(tmp_path))
    mgr.version_file.write_text("2.12.0", encoding="utf-8")

    with pytest.raises(RuntimeError, match="conversations.db"):
        mgr.backup_if_needed()

    assert mgr.get_stored_version() == "2.12.0"
    assert (existing_dir / "previous_marker.txt").read_text(
        encoding="utf-8"
    ) == "keep-existing-backup"
    assert not (existing_dir / "livingmemory.db").exists()
    assert not (existing_dir / "decay_state.json").exists()
    assert not any((tmp_path / "backups").glob("*.tmp"))


def test_backup_if_needed_atomically_replaces_existing_backup_on_success(
    tmp_path: Path,
):
    valid_db = tmp_path / "livingmemory.db"
    conn = sqlite3.connect(str(valid_db))
    try:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
        conn.execute("INSERT INTO t (val) VALUES ('fresh')")
        conn.commit()
    finally:
        conn.close()
    (tmp_path / "decay_state.json").write_text('{"ok": true}', encoding="utf-8")

    existing_dir = tmp_path / "backups" / "v2.12.0"
    existing_dir.mkdir(parents=True)
    (existing_dir / "stale.txt").write_text("old-stale-file", encoding="utf-8")

    mgr = BackupManager(str(tmp_path))
    mgr.version_file.write_text("2.12.0", encoding="utf-8")

    result = mgr.backup_if_needed()
    assert result == str(existing_dir)
    assert mgr.get_stored_version() == PLUGIN_VERSION
    assert not (existing_dir / "stale.txt").exists()
    assert (existing_dir / "livingmemory.db").is_file()
    assert (existing_dir / "decay_state.json").is_file()
    assert not any((tmp_path / "backups").glob("*.tmp"))


def test_backup_if_needed_skips_zero_byte_db_and_rejects_corrupt_db(tmp_path: Path):
    valid_db = tmp_path / "livingmemory.db"
    conn = sqlite3.connect(str(valid_db))
    try:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()
    (tmp_path / "conversations.db").write_bytes(b"")
    (tmp_path / "conversations.db-wal").write_bytes(b"")

    mgr = BackupManager(str(tmp_path))
    mgr.version_file.write_text("2.12.0", encoding="utf-8")

    backup_dir = Path(mgr.backup_if_needed())
    assert backup_dir == tmp_path / "backups" / "v2.12.0"
    assert (backup_dir / "livingmemory.db").is_file()
    assert not (backup_dir / "conversations.db").exists()
    assert not (backup_dir / "conversations.db-wal").exists()
    assert mgr.get_stored_version() == PLUGIN_VERSION

    # Non-empty unreadable file must still abort transactionally.
    bad_dir = tmp_path / "corrupt_case"
    bad_dir.mkdir()
    (bad_dir / "livingmemory.db").write_bytes(b"not a sqlite header at all")
    bad_mgr = BackupManager(str(bad_dir))
    bad_mgr.version_file.write_text("2.12.0", encoding="utf-8")

    with pytest.raises(RuntimeError, match="livingmemory.db"):
        bad_mgr.backup_if_needed()

    assert bad_mgr.get_stored_version() == "2.12.0"
    assert not (bad_dir / "backups" / "v2.12.0").exists()


def test_backup_manager_recovers_interrupted_publish_and_hides_dot_dirs(
    tmp_path: Path,
):
    backups_root = tmp_path / "backups"
    interrupted = backups_root / ".v2.12.0.old.999.deadbeef.tmp"
    interrupted.mkdir(parents=True)
    (interrupted / "marker.txt").write_text("prior-backup", encoding="utf-8")

    # Hidden temp dirs are not exposed by list_backups.
    assert BackupManager.list_backups(str(tmp_path)) == []

    # Triggering recovery restores the archived directory if a subsequent backup aborts.
    (tmp_path / "livingmemory.db").write_bytes(b"corrupt")
    mgr = BackupManager(str(tmp_path))
    mgr.version_file.write_text("2.12.0", encoding="utf-8")

    with pytest.raises(RuntimeError, match="livingmemory.db"):
        mgr.backup_if_needed()

    restored_dir = backups_root / "v2.12.0"
    assert (restored_dir / "marker.txt").read_text(encoding="utf-8") == "prior-backup"
    assert not interrupted.exists()


def test_backup_if_needed_reports_insufficient_disk_space_and_preserves_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    db_path = tmp_path / "livingmemory.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, body TEXT)")
        conn.execute("INSERT INTO t (body) VALUES (?)", ("x" * 2048,))
        conn.commit()
    finally:
        conn.close()

    existing_dir = tmp_path / "backups" / "v2.12.0"
    existing_dir.mkdir(parents=True)
    (existing_dir / "keep.txt").write_text("existing", encoding="utf-8")

    mgr = BackupManager(str(tmp_path))
    mgr.version_file.write_text("2.12.0", encoding="utf-8")

    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=1024, used=1000, free=64),
    )

    with pytest.raises(OSError, match="磁盘空间不足") as exc_info:
        mgr.backup_if_needed()

    assert exc_info.value.errno == errno.ENOSPC
    assert mgr.get_stored_version() == "2.12.0"
    assert (existing_dir / "keep.txt").read_text(encoding="utf-8") == "existing"


def test_backup_sqlite_file_falls_back_to_online_backup_on_vacuum_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    src = tmp_path / "source.db"
    conn = sqlite3.connect(str(src), isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, val TEXT)")
        conn.execute("INSERT INTO items (val) VALUES ('persisted-via-online-backup')")
    finally:
        conn.close()

    real_connect = sqlite3.connect

    class _ConnProxy:
        def __init__(self, inner: sqlite3.Connection):
            self._inner = inner

        def execute(self, sql: str, *args, **kwargs):
            if sql.strip().upper().startswith("VACUUM INTO"):
                raise sqlite3.OperationalError("simulated vacuum unsupported")
            return self._inner.execute(sql, *args, **kwargs)

        def __getattr__(self, name: str):
            return getattr(self._inner, name)

    def proxy_connect(database, *args, **kwargs):
        inner = real_connect(database, *args, **kwargs)
        if Path(database) == src:
            return _ConnProxy(inner)
        return inner

    monkeypatch.setattr(sqlite_conn_mod.sqlite3, "connect", proxy_connect)

    dest = tmp_path / "dest.db"
    backup_sqlite_file(src, dest)
    assert dest.is_file()

    verify = real_connect(str(dest))
    try:
        assert verify.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        rows = verify.execute("SELECT val FROM items").fetchall()
        assert rows == [("persisted-via-online-backup",)]
    finally:
        verify.close()
