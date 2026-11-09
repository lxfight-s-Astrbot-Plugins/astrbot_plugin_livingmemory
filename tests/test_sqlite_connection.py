"""SQLite journal 策略：WAL 能力探测、回退与连接配置。"""

from __future__ import annotations

import sqlite3

import pytest
from astrbot_plugin_livingmemory.storage import sqlite_connection


@pytest.fixture(autouse=True)
def _reset_journal_policy():
    mode = sqlite_connection._journal_mode
    resolved = sqlite_connection._resolved_dir
    yield
    sqlite_connection._journal_mode = mode
    sqlite_connection._resolved_dir = resolved


def test_mount_path_matching_is_component_aware():
    assert sqlite_connection._mount_contains("/", "/a/b")
    assert sqlite_connection._mount_contains("/a", "/a/b")
    assert not sqlite_connection._mount_contains("/a", "/ab")
    assert (
        sqlite_connection._unescape_mount_field("/mnt/with\040space")
        == "/mnt/with space"
    )


def test_unsafe_filesystem_types_are_rejected():
    assert sqlite_connection._is_unsafe_fstype("9p")
    assert sqlite_connection._is_unsafe_fstype("fuse.grpcfuse")
    assert sqlite_connection._is_unsafe_fstype("nfs4")

    assert not sqlite_connection._is_unsafe_fstype("ext4")
    assert not sqlite_connection._is_unsafe_fstype("overlay")
    assert not sqlite_connection._is_unsafe_fstype("fuseblk")


@pytest.mark.asyncio
async def test_resolve_keeps_wal_when_directory_supports_it(tmp_path):
    mode = await sqlite_connection.resolve_journal_mode(tmp_path)

    assert mode == sqlite_connection.JOURNAL_MODE_WAL
    for suffix in ("", "-wal", "-shm", "-journal"):
        assert not (tmp_path / f".wal_probe.db{suffix}").exists()


@pytest.mark.asyncio
async def test_resolve_falls_back_on_shared_mount(tmp_path, monkeypatch):
    """Docker Desktop 的 9p bind mount 不能承载 WAL，探测通过也不采用。"""
    monkeypatch.setattr(sqlite_connection, "_mount_fstype", lambda _path: "9p")
    monkeypatch.setattr(sqlite_connection, "_probe_wal_support", lambda _dir: True)

    mode = await sqlite_connection.resolve_journal_mode(tmp_path)

    assert mode == sqlite_connection.JOURNAL_MODE_DELETE


@pytest.mark.asyncio
async def test_resolve_converts_existing_wal_database(tmp_path, monkeypatch):
    db_path = tmp_path / "livingmemory.db"
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY)")
    connection.execute("INSERT INTO documents VALUES (1)")
    connection.commit()
    connection.close()
    monkeypatch.setattr(sqlite_connection, "_mount_fstype", lambda _path: None)
    monkeypatch.setattr(sqlite_connection, "_probe_wal_support", lambda _dir: False)

    mode = await sqlite_connection.resolve_journal_mode(tmp_path)

    assert mode == sqlite_connection.JOURNAL_MODE_DELETE
    connection = sqlite3.connect(db_path)
    try:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        count = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    finally:
        connection.close()
    assert journal_mode == "delete"
    assert count == 1


@pytest.mark.asyncio
async def test_resolve_raises_when_wal_database_cannot_be_converted(
    tmp_path, monkeypatch
):
    (tmp_path / "livingmemory.db").write_bytes(b"not a sqlite database")
    monkeypatch.setattr(sqlite_connection, "_mount_fstype", lambda _path: None)
    monkeypatch.setattr(sqlite_connection, "_probe_wal_support", lambda _dir: False)

    with pytest.raises(RuntimeError, match="rollback journal"):
        await sqlite_connection.resolve_journal_mode(tmp_path)


@pytest.mark.asyncio
async def test_open_connection_keeps_hot_path_free_of_journal_mode(tmp_path):
    connection = await sqlite_connection.open_connection(str(tmp_path / "memory.db"))
    try:
        cursor = await connection.execute("PRAGMA journal_mode")
        journal_mode = (await cursor.fetchone())[0]
        cursor = await connection.execute("PRAGMA busy_timeout")
        busy_timeout = (await cursor.fetchone())[0]
    finally:
        await connection.close()

    # 新建库保持 SQLite 默认 rollback journal；journal mode 只在启动期设置。
    assert journal_mode == "delete"
    assert busy_timeout == sqlite_connection.BUSY_TIMEOUT_MS


@pytest.mark.asyncio
async def test_open_connection_applies_journal_mode_when_requested(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        sqlite_connection, "_journal_mode", sqlite_connection.JOURNAL_MODE_DELETE
    )

    connection = await sqlite_connection.open_connection(
        str(tmp_path / "memory.db"), apply_journal_mode=True
    )
    try:
        cursor = await connection.execute("PRAGMA journal_mode")
        journal_mode = (await cursor.fetchone())[0]
    finally:
        await connection.close()

    assert journal_mode == "delete"


@pytest.mark.asyncio
async def test_open_connection_sets_wal_safe_pragmas(tmp_path):
    """WAL 下使用 NORMAL 同步级别并启用内存临时表。"""
    sqlite_connection._journal_mode = sqlite_connection.JOURNAL_MODE_WAL
    connection = await sqlite_connection.open_connection(str(tmp_path / "pragmas.db"))
    try:
        cursor = await connection.execute("PRAGMA synchronous")
        assert (await cursor.fetchone())[0] == 1  # NORMAL
        cursor = await connection.execute("PRAGMA temp_store")
        assert (await cursor.fetchone())[0] == 2  # MEMORY
    finally:
        await connection.close()
