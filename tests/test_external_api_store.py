"""Tests for external API key/idempotency storage and schema migration."""

from __future__ import annotations

import asyncio
import time

import aiosqlite
import pytest
from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine
from astrbot_plugin_livingmemory.storage.db_migration import DBMigration
from astrbot_plugin_livingmemory.storage.external_api_store import (
    API_KEY_PREFIX,
    ExternalApiStore,
    ExternalApiStoreError,
    generate_api_key,
    hash_api_key,
    key_display_prefix,
)

from tests.test_memory_engine import _FakeFaissDB


def _run(coro):
    return asyncio.run(coro)


class TestTokenHelpers:
    def test_generate_api_key_is_random_and_prefixed(self) -> None:
        first = generate_api_key()
        second = generate_api_key()

        assert first.startswith(API_KEY_PREFIX)
        assert len(first) > len(API_KEY_PREFIX) + 20
        assert first != second

    def test_hash_and_prefix_are_deterministic(self) -> None:
        token = "lm_example-token_value"

        assert hash_api_key(token) == hash_api_key(token)
        assert len(hash_api_key(token)) == 64
        assert hash_api_key(token) != token
        assert key_display_prefix(token) == token[:8]
        assert token.startswith(key_display_prefix(token))


class TestExternalApiStore:
    @pytest.mark.asyncio
    async def test_initialize_is_idempotent_and_creates_tables(self, tmp_path):
        store = ExternalApiStore(str(tmp_path / "external.db"))

        await store.initialize()
        await store.initialize()

        async with aiosqlite.connect(store.db_path) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN ('external_api_keys', 'external_api_idempotency')"
            )
            names = {row[0] for row in await cursor.fetchall()}
        assert names == {"external_api_keys", "external_api_idempotency"}

    @pytest.mark.asyncio
    async def test_create_key_returns_plaintext_once_and_stores_hash(self, tmp_path):
        store = ExternalApiStore(str(tmp_path / "external.db"))
        await store.initialize()

        record, token = await store.create_key(
            "workbuddy",
            scopes=["read", "write"],
            allowed_personas=["p1"],
            allowed_scopes=["s1"],
            rate_limit_per_min=30,
            write_limit_per_min=5,
        )

        assert token.startswith(API_KEY_PREFIX)
        assert record.key_prefix == token[:8]
        assert record.scopes == ["read", "write"]
        assert record.rate_limit_per_min == 30
        assert record.write_limit_per_min == 5
        # The plaintext token must never be stored.
        stored = await store.find_active_key_by_hash(hash_api_key(token))
        assert stored is not None
        assert stored.id == record.id
        assert stored.to_public_dict().get("token") is None
        async with aiosqlite.connect(store.db_path) as db:
            cursor = await db.execute(
                "SELECT key_hash, key_prefix FROM external_api_keys"
            )
            row = await cursor.fetchone()
        assert row[0] == hash_api_key(token)
        assert row[1] == key_display_prefix(token)
        assert token not in str(row)

    @pytest.mark.asyncio
    async def test_create_key_rejects_duplicate_name_and_upgrades_write_scope(
        self, tmp_path
    ):
        store = ExternalApiStore(str(tmp_path / "external.db"))
        await store.initialize()
        await store.create_key("duplicate", scopes=["read"])

        with pytest.raises(ExternalApiStoreError, match="already exists"):
            await store.create_key("duplicate", scopes=["read"])
        write_record, _ = await store.create_key("write-only", scopes=["write"])
        assert write_record.scopes == ["read", "write"]

    @pytest.mark.asyncio
    async def test_list_and_revoke_keys(self, tmp_path):
        store = ExternalApiStore(str(tmp_path / "external.db"))
        await store.initialize()
        first, _ = await store.create_key("alpha", scopes=["read"])
        second, _ = await store.create_key("beta", scopes=["read", "write"])

        keys = await store.list_keys()
        assert [key.name for key in keys] == ["alpha", "beta"]

        revoked = await store.revoke_key("alpha")
        assert revoked is not None
        assert revoked.revoked is True
        assert revoked.revoked_at is not None
        # Already revoked keys keep their first timestamp.
        again = await store.revoke_key(first.id)
        assert again is not None
        assert again.revoked_at == revoked.revoked_at

        assert await store.get_key(first.id) is not None
        assert await store.get_key("beta") is not None
        assert await store.get_key(second.id + 100) is None
        assert await store.revoke_key("missing") is None

        keys = await store.list_keys()
        assert [key.revoked for key in keys] == [True, False]
        # find_active skips revoked rows.
        assert await store.find_active_key_by_hash(first.key_hash) is None

    @pytest.mark.asyncio
    async def test_idempotency_records_roundtrip(self, tmp_path):
        store = ExternalApiStore(str(tmp_path / "external.db"))
        await store.initialize()

        assert await store.get_idempotency("release-1") is None
        assert await store.save_idempotency("release-1", 42) == 42
        assert await store.get_idempotency("release-1") == 42
        # A retry with the same key keeps the first memory id.
        assert await store.save_idempotency("release-1", 77) == 42
        assert await store.get_idempotency("release-1") == 42

    @pytest.mark.asyncio
    async def test_initialize_prunes_expired_idempotency_records(self, tmp_path):
        db_path = str(tmp_path / "external.db")
        store = ExternalApiStore(db_path)
        await store.initialize()
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT INTO external_api_idempotency "
                "(idempotency_key, memory_id, created_at) VALUES (?, ?, ?)",
                ("stale", 1, time.time() - 60 * 86400),
            )
            await db.commit()

        await store.initialize()

        assert await store.get_idempotency("stale") is None


class TestExternalApiSchemaIsVersionIndependent:
    """The external API tables are created idempotently, not through a migration.

    The user-profile branch owns db_version 9, so this feature must not consume a
    migration version; startup DDL must work for fresh and existing databases and
    must leave the recorded db_version untouched.
    """

    @pytest.mark.asyncio
    async def test_store_initialize_does_not_change_db_version(self, tmp_path):
        db_path = str(tmp_path / "legacy.db")
        migration = DBMigration(db_path)
        await migration.initialize_version_table()
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT INTO db_version (version, description, migrated_at) "
                "VALUES (?, 'legacy', '2026-01-01')",
                (DBMigration.CURRENT_VERSION,),
            )
            await db.commit()

        store = ExternalApiStore(db_path)
        await store.initialize()
        await store.initialize()

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN ('external_api_keys', 'external_api_idempotency')"
            )
            names = {row[0] for row in await cursor.fetchall()}
            cursor = await db.execute("SELECT version FROM db_version")
            versions = [row[0] for row in await cursor.fetchall()]
        assert names == {"external_api_keys", "external_api_idempotency"}
        # No version row is added or rewritten by the idempotent DDL.
        assert versions == [DBMigration.CURRENT_VERSION]

    @pytest.mark.asyncio
    async def test_store_initialize_is_idempotent_on_existing_database(self, tmp_path):
        db_path = str(tmp_path / "existing.db")
        store = ExternalApiStore(db_path)
        await store.initialize()
        first, _token = await store.create_key("persisted", scopes=["read"])

        await store.initialize()

        assert await store.get_key(first.id) is not None
        records = await store.list_keys()
        assert [record.name for record in records] == ["persisted"]


class TestMemoryEngineSchema:
    @pytest.mark.asyncio
    async def test_memory_engine_initialize_creates_external_api_tables(self, tmp_path):
        engine = MemoryEngine(
            db_path=str(tmp_path / "engine.db"),
            faiss_db=_FakeFaissDB(),
            config={"atom_enabled": False, "graph_memory_enabled": False},
        )
        await engine.initialize()
        try:
            cursor = await engine.db_connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name LIKE 'external_api_%'"
            )
            names = {row[0] for row in await cursor.fetchall()}
            cursor = await engine.db_connection.execute(
                "SELECT version FROM db_version ORDER BY id DESC LIMIT 1"
            )
            version = (await cursor.fetchone())[0]
        finally:
            await engine.close()

        assert names == {"external_api_keys", "external_api_idempotency"}
        assert version == DBMigration.CURRENT_VERSION

    @pytest.mark.asyncio
    async def test_engine_restart_keeps_tables_and_db_version(self, tmp_path):
        db_path = str(tmp_path / "restart.db")
        first = MemoryEngine(
            db_path=db_path,
            faiss_db=_FakeFaissDB(),
            config={"atom_enabled": False, "graph_memory_enabled": False},
        )
        await first.initialize()
        try:
            cursor = await first.db_connection.execute(
                "SELECT version FROM db_version ORDER BY id DESC LIMIT 1"
            )
            version_after_first_start = (await cursor.fetchone())[0]
        finally:
            await first.close()

        second = MemoryEngine(
            db_path=db_path,
            faiss_db=_FakeFaissDB(),
            config={"atom_enabled": False, "graph_memory_enabled": False},
        )
        await second.initialize()
        try:
            cursor = await second.db_connection.execute(
                "SELECT version FROM db_version ORDER BY id DESC LIMIT 1"
            )
            version_after_second_start = (await cursor.fetchone())[0]
            cursor = await second.db_connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                "AND name='external_api_keys'"
            )
            table_count = (await cursor.fetchone())[0]
        finally:
            await second.close()

        assert version_after_second_start == version_after_first_start
        assert table_count == 1
