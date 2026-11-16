"""Regression coverage for bounded conversation caching and message limits."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_livingmemory.core.event_handler_modules.message_utils import MessageUtils
from astrbot_plugin_livingmemory.core.managers.conversation_manager import ConversationManager
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
        assert [m.content for m in await manager.get_messages("group", 2)] == ["3", "new"]
        store.get_messages.assert_not_awaited()
        assert [m.content for m in await manager.get_messages("group", 4)] == ["1", "2", "3", "new"]
        store.get_messages.assert_awaited_once()
        await manager.add_message("group", "user", "late", timestamp=101.5)
        assert [m.content for m in await manager.get_messages("group", 4)] == ["late", "2", "3", "new"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_counts_are_atomic_and_hint_skips_only_under_cap(tmp_path):
    store = ConversationStore(str(tmp_path / "counts.db"))
    await store.initialize()
    manager = ConversationManager(store=store)
    config = type("Config", (), {"get": lambda self, key, default=None: 3 if key.endswith("max_messages_per_session") else default})()
    utils = MessageUtils(config_manager=config, conversation_manager=manager)
    try:
        messages = await asyncio.gather(*(manager.add_message("group", "user", str(n)) for n in range(4)))
        assert sorted(m.session_message_count for m in messages) == [1, 2, 3, 4]
        store.get_message_count = AsyncMock(wraps=store.get_message_count)
        await utils.enforce_message_limit("group", count_hint=3)
        store.get_message_count.assert_not_awaited()
        await utils.enforce_message_limit("group", count_hint=4)
        assert store.get_message_count.await_count >= 1
        assert await store.get_message_count("group") <= 3
    finally:
        await store.close()
