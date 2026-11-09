"""Regression tests for atom FTS maintenance, CJK search and read-connection reuse."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

import astrbot_plugin_livingmemory.storage.atom_store as atom_store_mod
from astrbot_plugin_livingmemory.core.models.memory_atom import AtomStatus, MemoryAtom
from astrbot_plugin_livingmemory.core.processors.text_processor import TextProcessor
from astrbot_plugin_livingmemory.storage.atom_store import AtomStore


async def _insert_atom(
    store: AtomStore,
    content: str,
    *,
    parent_memory_id: int = 1,
    status: AtomStatus = AtomStatus.ACTIVE,
) -> int:
    atom = MemoryAtom(
        parent_memory_id=parent_memory_id,
        content=content,
        status=status,
    )
    atom_id = await store.insert(atom)
    if status != AtomStatus.ACTIVE:
        async with store._connect() as db:
            await db.execute(
                "UPDATE memory_atoms SET status = ?, expires_at = ? WHERE id = ?",
                (status.value, time.time() - 10.0, atom_id),
            )
            await db.commit()
    return atom_id


async def _forget_expired(store: AtomStore) -> int:
    return await store.forget_expired_atoms(older_than_days=0)


@pytest.mark.asyncio
async def test_initialize_does_not_rebuild_fts_when_only_forgotten_atoms_differ(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """forgotten 原子本来就不在 FTS 里，启动时不应因此全量重建。"""
    db_path = str(tmp_path / "atoms_forgotten.db")
    store = AtomStore(db_path, text_processor=TextProcessor())
    await store.initialize()
    await _insert_atom(store, "张三喜欢喝咖啡")
    await _insert_atom(store, "李四喜欢喝茶")
    await _insert_atom(store, "王五喜欢吃火锅", status=AtomStatus.EXPIRED)
    assert await _forget_expired(store) == 1
    await store.close()

    rebuild_calls: list[int] = []
    original_rebuild = AtomStore._rebuild_fts

    async def spy_rebuild(self, db):
        rebuild_calls.append(1)
        return await original_rebuild(self, db)

    monkeypatch.setattr(AtomStore, "_rebuild_fts", spy_rebuild)

    reopened = AtomStore(db_path, text_processor=TextProcessor())
    await reopened.initialize()

    assert rebuild_calls == []
    async with reopened._connect() as db:
        cursor = await db.execute("SELECT COUNT(*) FROM memory_atoms_fts")
        assert (await cursor.fetchone())[0] == 2
    await reopened.close()


@pytest.mark.asyncio
async def test_forgotten_atom_content_stays_out_of_search(tmp_path: Path) -> None:
    db_path = str(tmp_path / "atoms_no_resurrect.db")
    store = AtomStore(db_path, text_processor=TextProcessor())
    await store.initialize()
    await _insert_atom(store, "张三喜欢喝咖啡")
    await _insert_atom(store, "王五喜欢吃火锅", status=AtomStatus.EXPIRED)
    await _forget_expired(store)
    await store.close()

    reopened = AtomStore(db_path, text_processor=TextProcessor())
    await reopened.initialize()
    assert await reopened.search_fts("火锅", limit=5) == []
    assert await reopened.search_fts("咖啡", limit=5)
    await reopened.close()


@pytest.mark.asyncio
async def test_chinese_sentence_query_hits_tokenized_atom(tmp_path: Path) -> None:
    """查询与写入共用分词器后，中文整句查询能命中分词后的原子内容。"""
    store = AtomStore(str(tmp_path / "atoms_cjk.db"), text_processor=TextProcessor())
    await store.initialize()
    await _insert_atom(store, "我喜欢吃火锅")
    await _insert_atom(store, "明天项目deadline是周五")

    results = await store.search_fts("火锅很好吃", limit=5)
    assert [atom.content for atom in results] == ["我喜欢吃火锅"]

    english = await store.search_fts("project deadline", limit=5)
    assert "明天项目deadline是周五" in [atom.content for atom in english]
    await store.close()


@pytest.mark.asyncio
async def test_legacy_raw_fts_is_migrated_once_to_tokenized_format(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """旧库的原文 FTS 行应在首次启动时按分词格式重写，之后不再重建。"""
    db_path = str(tmp_path / "atoms_legacy.db")
    legacy = AtomStore(db_path)
    await legacy.initialize()
    await _insert_atom(legacy, "我喜欢吃火锅")
    async with legacy._connect() as db:
        cursor = await db.execute("SELECT content FROM memory_atoms_fts")
        assert (await cursor.fetchone())[0] == "我喜欢吃火锅"

    migrated = AtomStore(db_path, text_processor=TextProcessor())
    await migrated.initialize()
    async with migrated._connect() as db:
        cursor = await db.execute("SELECT content FROM memory_atoms_fts")
        assert (await cursor.fetchone())[0] == "喜欢 火锅 吃火锅"
    assert await migrated.search_fts("火锅很好吃", limit=5)
    await migrated.close()

    rebuild_calls: list[int] = []
    original_rebuild = AtomStore._rebuild_fts

    async def spy_rebuild(self, db):
        rebuild_calls.append(1)
        return await original_rebuild(self, db)

    monkeypatch.setattr(AtomStore, "_rebuild_fts", spy_rebuild)
    restarted = AtomStore(db_path, text_processor=TextProcessor())
    await restarted.initialize()
    assert rebuild_calls == []
    await restarted.close()


@pytest.mark.asyncio
async def test_read_connection_is_reused_until_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """开启 cache_reads 后查询复用同一条连接，close 之后可重新建立。"""
    real_open = atom_store_mod.open_connection
    opened: list[object] = []

    async def spy_open(db_path: str, **kwargs):
        connection = await real_open(db_path, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(atom_store_mod, "open_connection", spy_open)

    store = AtomStore(
        str(tmp_path / "atoms_reuse.db"),
        cache_reads=True,
        text_processor=TextProcessor(),
    )
    await store.initialize()
    await _insert_atom(store, "张三喜欢喝咖啡")
    opened.clear()

    await store.search_fts("咖啡", limit=5)
    await store.search_fts("咖啡", limit=5)
    assert len(opened) == 1

    await store.close()
    await store.search_fts("咖啡", limit=5)
    assert len(opened) == 2
    await store.close()
