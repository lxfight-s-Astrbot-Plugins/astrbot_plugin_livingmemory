"""Regression coverage for bounded conversation caching and message limits."""

import asyncio
from unittest.mock import AsyncMock

import pytest

import astrbot_plugin_livingmemory.storage.conversation_store as conv_store_mod
from astrbot_plugin_livingmemory.core.event_handler_modules.message_utils import (
    MessageUtils,
)
from astrbot_plugin_livingmemory.core.managers.conversation_manager import (
    ConversationManager,
)
from astrbot_plugin_livingmemory.core.models.conversation_models import Message
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
async def test_counts_are_atomic_and_recent_count_skips_only_under_cap(tmp_path):
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
        store.get_message_count = AsyncMock(wraps=store.get_message_count)

        # Under cap (<= 3): one-shot recent count skips DB check once, then falls back.
        for _ in range(3):
            await manager.add_message("group", "user", "under")
        await utils.enforce_message_limit("group")
        store.get_message_count.assert_not_awaited()

        # Second check without a new write cannot reuse stale state.
        await utils.enforce_message_limit("group")
        assert store.get_message_count.await_count == 1

        # Concurrent writes past cap (4 > 3) trigger trimming cleanly.
        store.get_message_count.reset_mock()
        await asyncio.gather(
            *(manager.add_message("burst", "user", str(n)) for n in range(4))
        )
        assert manager.pop_recent_message_count("burst") == 4
        await utils.enforce_message_limit("burst")
        assert store.get_message_count.await_count >= 1
        assert await store.get_message_count("burst") <= 3
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


@pytest.mark.asyncio
async def test_cache_hit_then_larger_limit_fetches_full_history(tmp_path):
    store = ConversationStore(str(tmp_path / "expand.db"))
    await store.initialize()
    manager = ConversationManager(store=store)
    try:
        for n in range(6):
            await manager.add_message("group", "user", str(n), timestamp=100 + n)

        # Warm partial cache at capacity=2 (complete=False), then confirm hit.
        assert [m.content for m in await manager.get_messages("group", 2)] == ["4", "5"]
        store.get_messages = AsyncMock(wraps=store.get_messages)
        assert [m.content for m in await manager.get_messages("group", 2)] == ["4", "5"]
        store.get_messages.assert_not_awaited()

        # Requesting a larger limit must miss the partial cache and return 5 rows.
        assert [m.content for m in await manager.get_messages("group", 5)] == [
            "1",
            "2",
            "3",
            "4",
            "5",
        ]
        store.get_messages.assert_awaited_once()
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_store_add_message_without_returning_falls_back_and_rolls_back(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(conv_store_mod, "_SUPPORTS_RETURNING", False)
    store = ConversationStore(str(tmp_path / "compat.db"))
    await store.initialize()
    try:
        for idx in range(1, 4):
            msg_id, count = await store.add_message(
                Message(
                    id=None,
                    session_id="s1",
                    role="user",
                    content=f"m{idx}",
                    sender_id="u1",
                    timestamp=100.0 + idx,
                ),
                return_count=True,
            )
            assert msg_id == idx
            assert count == idx

        # Force failure mid-transaction after INSERT INTO messages to verify rollback.
        async def failing_update(*args, **kwargs):
            raise RuntimeError("simulated update failure")

        monkeypatch.setattr(store, "_update_session_after_insert", failing_update)
        with pytest.raises(RuntimeError, match="simulated update failure"):
            await store.add_message(
                Message(
                    id=None,
                    session_id="s1",
                    role="user",
                    content="should-rollback",
                    sender_id="u1",
                    timestamp=200.0,
                ),
                return_count=True,
            )
        assert await store.get_message_count("s1") == 3
        assert len(await store.get_messages("s1", limit=10)) == 3
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_manager_handles_legacy_int_store_add_message(tmp_path):
    store = ConversationStore(str(tmp_path / "legacy-mock.db"))
    await store.initialize()
    store.add_message = AsyncMock(return_value=42)
    manager = ConversationManager(store=store)
    try:
        msg = await manager.add_message("s1", "user", "hi")
        assert msg.id == 42
        assert manager.pop_recent_message_count("s1") is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_warm_cache_normalizes_structured_content_like_sqlite(tmp_path):
    store = ConversationStore(str(tmp_path / "structured-cache.db"))
    await store.initialize()
    manager = ConversationManager(store=store)
    try:
        await manager.add_message("s1", "user", "warmup", timestamp=100.0)
        # Warm cache before inserting structured list content.
        assert [m.content for m in await manager.get_messages("s1", limit=10)] == [
            "warmup"
        ]

        structured_text = [
            {"type": "text", "text": "图片里的日程是下午三点"},
            {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
        ]
        structured_media_only = [
            {"type": "image_url", "image_url": {"url": "https://example.com/b.png"}},
        ]
        written_text = await manager.add_message(
            "s1", "user", structured_text, timestamp=101.0
        )
        written_media = await manager.add_message(
            "s1", "user", structured_media_only, timestamp=102.0
        )
        assert written_text.content == "图片里的日程是下午三点"
        assert written_media.content == "[图片消息]"

        cached_messages = await manager.get_messages("s1", limit=10, use_cache=True)
        sqlite_messages = await manager.get_messages("s1", limit=10, use_cache=False)
        assert [m.content for m in cached_messages] == [
            "warmup",
            "图片里的日程是下午三点",
            "[图片消息]",
        ]
        assert [m.content for m in cached_messages] == [
            m.content for m in sqlite_messages
        ]
    finally:
        await store.close()
