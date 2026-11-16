"""Regression coverage for bounded conversation caching and message limits."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_livingmemory.core.event_handler_modules.message_utils import (
    MessageUtils,
)
from astrbot_plugin_livingmemory.core.managers.conversation_manager import (
    ConversationManager,
)
from astrbot_plugin_livingmemory.storage.conversation_store import ConversationStore


@pytest.mark.asyncio
async def test_recent_cache_updates_and_expands_correctly(tmp_path):
    store = ConversationStore(str(tmp_path / "messages.db"))
    await store.initialize()
    manager = ConversationManager(store=store)
    try:
        for n in range(4):
            await manager.add_message("group", "user", str(n), timestamp=100 + n)
        assert [m.content for m in await manager.get_messages("group", 2)] == ["2", "3"]
        store.get_messages = AsyncMock(wraps=store.get_messages)
        await manager.add_message("group", "user", "new", timestamp=104)
        assert [m.content for m in await manager.get_messages("group", 2)] == [
            "3",
            "new",
        ]
        store.get_messages.assert_not_awaited()
        assert [m.content for m in await manager.get_messages("group", 4)] == [
            "1",
            "2",
            "3",
            "new",
        ]
        store.get_messages.assert_awaited_once()
        await manager.add_message("group", "user", "late", timestamp=101.5)
        assert [m.content for m in await manager.get_messages("group", 4)] == [
            "late",
            "2",
            "3",
            "new",
        ]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_counts_are_atomic_and_hint_skips_only_under_cap(tmp_path):
    store = ConversationStore(str(tmp_path / "counts.db"))
    await store.initialize()
    manager = ConversationManager(store=store)
    config = type(
        "Config",
        (),
        {
            "get": lambda self, key, default=None: (
                3 if key.endswith("max_messages_per_session") else default
            )
        },
    )()
    utils = MessageUtils(config_manager=config, conversation_manager=manager)
    try:
        messages = await asyncio.gather(
            *(manager.add_message("group", "user", str(n)) for n in range(4))
        )
        assert sorted(m.session_message_count for m in messages) == [1, 2, 3, 4]
        store.get_message_count = AsyncMock(wraps=store.get_message_count)
        await utils.enforce_message_limit("group", count_hint=3)
        store.get_message_count.assert_not_awaited()
        await utils.enforce_message_limit("group", count_hint=4)
        assert store.get_message_count.await_count >= 1
        assert await store.get_message_count("group") <= 3
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_short_history_grows_to_requested_capacity_and_zero_preserves_cache(
    tmp_path,
):
    store = ConversationStore(str(tmp_path / "short.db"))
    await store.initialize()
    manager = ConversationManager(store=store)
    try:
        for n in range(3):
            await manager.add_message("group", "user", str(n), timestamp=100 + n)
        assert len(await manager.get_messages("group", 5)) == 3
        store.get_messages = AsyncMock(wraps=store.get_messages)
        for n in range(3, 6):
            await manager.add_message("group", "user", str(n), timestamp=100 + n)
            expected = [str(i) for i in range(max(0, n - 4), n + 1)]
            assert [
                m.content for m in await manager.get_messages("group", 5)
            ] == expected
        store.get_messages.assert_not_awaited()
        assert await manager.get_messages("group", 0) == []
        assert [m.content for m in await manager.get_messages("group", 5)] == [
            "1",
            "2",
            "3",
            "4",
            "5",
        ]
        assert store.get_messages.await_count == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_write_refreshes_lru(tmp_path):
    store = ConversationStore(str(tmp_path / "lru.db"))
    await store.initialize()
    manager = ConversationManager(store=store, max_cache_size=2)
    try:
        for sid in ("a", "b"):
            await manager.add_message(sid, "user", sid)
            await manager.get_messages(sid, 5)
        await manager.add_message("a", "user", "new")
        await manager.add_message("c", "user", "c")
        await manager.get_messages("c", 5)
        assert list(manager._cache) == ["a", "c"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slow_session_does_not_block_another_cache_hit(tmp_path):
    store = ConversationStore(str(tmp_path / "independent.db"))
    await store.initialize()
    manager = ConversationManager(store=store)
    entered, release = asyncio.Event(), asyncio.Event()
    original = store.get_messages

    async def slow_read(session_id, limit, sender_id=None):
        if session_id == "a":
            entered.set()
            await release.wait()
        return await original(session_id, limit, sender_id)

    try:
        await manager.add_message("b", "user", "cached")
        await manager.get_messages("b", 5)
        store.get_messages = slow_read
        task = asyncio.create_task(manager.get_messages("a", 5))
        await asyncio.wait_for(entered.wait(), 2)
        assert (await asyncio.wait_for(manager.get_messages("b", 5), 2))[
            0
        ].content == "cached"
        release.set()
        await task
    finally:
        release.set()
        await store.close()


@pytest.mark.asyncio
async def test_invalidation_waits_for_inflight_read_and_discards_old_result(tmp_path):
    store = ConversationStore(str(tmp_path / "race.db"))
    await store.initialize()
    manager = ConversationManager(store=store)
    entered, release = asyncio.Event(), asyncio.Event()
    original = store.get_messages

    async def delayed_read(session_id, limit, sender_id=None):
        result = await original(session_id, limit, sender_id)
        entered.set()
        await release.wait()
        return result

    try:
        await manager.add_message("a", "user", "old")
        store.get_messages = delayed_read
        reader = asyncio.create_task(manager.get_messages("a", 5))
        await asyncio.wait_for(entered.wait(), 2)
        await store.delete_session_messages("a")
        invalidator = asyncio.create_task(manager.invalidate_cache("a"))
        release.set()
        await reader
        await invalidator
        store.get_messages = original
        assert await manager.get_messages("a", 5) == []
    finally:
        release.set()
        await store.close()


@pytest.mark.asyncio
async def test_read_then_write_same_session_cannot_repopulate_stale_cache(tmp_path):
    store = ConversationStore(str(tmp_path / "write-race.db"))
    await store.initialize()
    manager = ConversationManager(store=store)
    entered, release = asyncio.Event(), asyncio.Event()
    original = store.get_messages

    async def delayed_read(session_id, limit, sender_id=None):
        result = await original(session_id, limit, sender_id)
        entered.set()
        await release.wait()
        return result

    try:
        await manager.add_message("a", "user", "old")
        store.get_messages = delayed_read
        reader = asyncio.create_task(manager.get_messages("a", 5))
        await asyncio.wait_for(entered.wait(), 2)
        writer = asyncio.create_task(manager.add_message("a", "user", "new"))
        release.set()
        await asyncio.gather(reader, writer)
        store.get_messages = AsyncMock(wraps=original)
        assert [m.content for m in await manager.get_messages("a", 5)] == ["old", "new"]
        store.get_messages.assert_not_awaited()
    finally:
        release.set()
        await store.close()
