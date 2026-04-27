"""
Tests for ConversationManager behaviors.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from astrbot_plugin_livingmemory.core.managers.conversation_manager import (
    ConversationManager,
)
from astrbot_plugin_livingmemory.storage.conversation_store import ConversationStore

from astrbot.api.platform import MessageType


class _DummyEvent:
    def __init__(self, session_id: str, group: bool = False):
        self.unified_msg_origin = session_id
        self._group = group
        self.sender_id = "u1"
        self.sender_name = "Tester"

    def get_sender_id(self):
        return "u1"

    def get_sender_name(self):
        return "Tester"

    def get_message_type(self):
        return MessageType.GROUP_MESSAGE if self._group else MessageType.FRIEND_MESSAGE

    def get_platform_name(self):
        return "test"


class _DummyTelegramEvent(_DummyEvent):
    def __init__(
        self,
        session_id: str,
        first_name: str | None = None,
        last_name: str | None = None,
        user_id: int = 12345,
    ):
        super().__init__(session_id, group=False)
        self.sender_id = str(user_id)
        raw_user = SimpleNamespace(
            id=user_id,
            username=None,
            first_name=first_name,
            last_name=last_name,
        )
        self.message_obj = SimpleNamespace(
            sender=SimpleNamespace(user_id=str(user_id), nickname="Unknown"),
            raw_message=SimpleNamespace(
                message=SimpleNamespace(from_user=raw_user),
                effective_user=raw_user,
            ),
        )

    def get_sender_id(self):
        return self.sender_id

    def get_sender_name(self):
        return "Unknown"

    def get_platform_name(self):
        return "telegram"


@pytest.mark.asyncio
async def test_conversation_manager_add_and_get_context(tmp_path: Path):
    db_path = tmp_path / "cm.db"
    store = ConversationStore(str(db_path))
    await store.initialize()
    manager = ConversationManager(store=store, max_cache_size=2, context_window_size=10)

    event = _DummyEvent("test:private:s1", group=False)
    await manager.add_message_from_event(event, role="user", content="hello")
    await manager.add_message_from_event(event, role="assistant", content="world")

    context = await manager.get_context("test:private:s1")
    assert len(context) == 2
    assert context[0]["role"] == "user"

    messages = await manager.get_messages("test:private:s1", limit=10)
    assert len(messages) == 2

    session = await manager.get_session_info("test:private:s1")
    assert session is not None
    assert session.message_count == 2

    await store.close()


@pytest.mark.asyncio
async def test_conversation_manager_range_and_metadata(tmp_path: Path):
    db_path = tmp_path / "cm2.db"
    store = ConversationStore(str(db_path))
    await store.initialize()
    manager = ConversationManager(store=store, max_cache_size=2, context_window_size=10)

    event = _DummyEvent("test:private:s2", group=False)
    for i in range(6):
        role = "user" if i % 2 == 0 else "assistant"
        await manager.add_message_from_event(event, role=role, content=f"m-{i}")

    rng = await manager.get_messages_range(
        "test:private:s2", start_index=2, end_index=5
    )
    assert [m.content for m in rng] == ["m-2", "m-3", "m-4"]

    await manager.update_session_metadata("test:private:s2", "last_summarized_index", 3)
    assert (
        await manager.get_session_metadata(
            "test:private:s2", "last_summarized_index", default=0
        )
        == 3
    )

    await manager.clear_session("test:private:s2")
    assert await store.get_message_count("test:private:s2") == 0

    await store.close()


@pytest.mark.asyncio
async def test_conversation_manager_resolves_telegram_name_without_username(
    tmp_path: Path,
):
    db_path = tmp_path / "telegram.db"
    store = ConversationStore(str(db_path))
    await store.initialize()
    manager = ConversationManager(store=store, max_cache_size=2, context_window_size=10)

    event = _DummyTelegramEvent(
        "telegram:private:s3",
        first_name="Alice",
        last_name="Lee",
        user_id=67890,
    )
    message = await manager.add_message_from_event(event, role="user", content="hello")

    assert message.sender_id == "67890"
    assert message.sender_name == "Alice Lee"

    await store.close()


@pytest.mark.asyncio
async def test_conversation_manager_falls_back_to_sender_id_for_unknown_name(
    tmp_path: Path,
):
    db_path = tmp_path / "telegram-id.db"
    store = ConversationStore(str(db_path))
    await store.initialize()
    manager = ConversationManager(store=store, max_cache_size=2, context_window_size=10)

    event = _DummyTelegramEvent("telegram:private:s4", user_id=24680)
    message = await manager.add_message_from_event(event, role="user", content="hello")

    assert message.sender_id == "24680"
    assert message.sender_name == "24680"

    await store.close()
