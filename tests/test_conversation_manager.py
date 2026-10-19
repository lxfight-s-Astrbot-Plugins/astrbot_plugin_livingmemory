"""
Tests for ConversationManager behaviors.
"""

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from astrbot_plugin_livingmemory.core.managers.conversation_manager import (
    ConversationManager,
)
from astrbot_plugin_livingmemory.storage.conversation_store import ConversationStore

from astrbot.api.platform import MessageType


class _DummyEvent:
    def __init__(
        self,
        session_id: str,
        group: bool = False,
        self_id: str = "bot-1",
    ):
        self.unified_msg_origin = session_id
        self._group = group
        self.self_id = self_id
        self.sender_id = "u1"
        self.sender_name = "Tester"

    def get_sender_id(self):
        return "u1"

    def get_sender_name(self):
        return "Tester"

    def get_self_id(self):
        return self.self_id

    def get_platform_id(self):
        return "test-instance"

    def get_message_type(self):
        return MessageType.GROUP_MESSAGE if self._group else MessageType.FRIEND_MESSAGE

    def get_platform_name(self):
        return "test"


class _TimestampedEvent(_DummyEvent):
    """携带平台消息对象时间戳的事件（模拟 AstrBot 的 message_obj.timestamp）。"""

    def __init__(self, session_id: str, timestamp):
        super().__init__(session_id, group=False)
        self.message_obj = SimpleNamespace(timestamp=timestamp)


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
    user_message = await manager.add_message_from_event(
        event, role="user", content="hello"
    )
    assistant_message = await manager.add_message_from_event(
        event, role="assistant", content="world"
    )

    assert user_message.sender_id == "u1"
    assert user_message.sender_name == "Tester"
    assert assistant_message.sender_id == "bot-1"
    assert assistant_message.sender_name == "bot-1"
    assert assistant_message.metadata == {"is_bot_message": True}

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
async def test_private_assistant_uses_explicit_bot_fallback_without_self_id(
    tmp_path: Path,
):
    store = ConversationStore(str(tmp_path / "bot-fallback.db"))
    await store.initialize()
    manager = ConversationManager(store=store)
    event = _DummyEvent("test:private:s-fallback", self_id="")

    message = await manager.add_message_from_event(
        event, role="assistant", content="world"
    )

    assert message.sender_id == "bot:test-instance"
    assert message.sender_name == "Bot"
    assert message.group_id is None
    await store.close()


@pytest.mark.asyncio
async def test_group_assistant_replaces_user_name_with_bot_name(tmp_path: Path):
    store = ConversationStore(str(tmp_path / "group-bot.db"))
    await store.initialize()
    manager = ConversationManager(store=store)
    event = _DummyEvent("test:group:g1", group=True)
    event.bot_name = "LivingMemory Bot"

    message = await manager.add_message_from_event(
        event, role="assistant", content="world"
    )

    assert message.sender_id == "bot-1"
    assert message.sender_name == "LivingMemory Bot"
    assert message.group_id == "test:group:g1"
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


# ==================== #255 平台消息时间戳入库 ====================


@pytest.mark.asyncio
async def test_add_message_stores_provided_timestamp(tmp_path: Path):
    """add_message 传入平台时间戳时按原值入库。"""
    store = ConversationStore(str(tmp_path / "ts.db"))
    await store.initialize()
    manager = ConversationManager(store=store)

    message = await manager.add_message(
        session_id="test:private:ts",
        role="user",
        content="hi",
        timestamp=1_700_000_000.0,
    )

    assert message.timestamp == 1_700_000_000.0
    await store.close()


@pytest.mark.parametrize(
    "bad_timestamp",
    [None, 0, -1, "bad", float("nan")],
    ids=["none", "zero", "negative", "string", "nan"],
)
@pytest.mark.asyncio
async def test_add_message_falls_back_to_now_for_invalid_timestamp(
    tmp_path: Path, bad_timestamp
):
    """缺失/非正数/非法时间戳回退到当前接收时间。"""
    store = ConversationStore(str(tmp_path / "ts-fallback.db"))
    await store.initialize()
    manager = ConversationManager(store=store)

    before = time.time()
    message = await manager.add_message(
        session_id="test:private:ts",
        role="user",
        content="hi",
        timestamp=bad_timestamp,
    )
    after = time.time()

    assert before <= message.timestamp <= after
    await store.close()


@pytest.mark.asyncio
async def test_add_message_from_event_uses_platform_timestamp(tmp_path: Path):
    """事件带 message_obj.timestamp 时，同一轮的用户消息与助手回复共用该时间。"""
    store = ConversationStore(str(tmp_path / "event-ts.db"))
    await store.initialize()
    manager = ConversationManager(store=store)
    event = _TimestampedEvent("test:private:ts2", 1_700_000_000.0)

    user_message = await manager.add_message_from_event(
        event, role="user", content="hi"
    )
    assistant_message = await manager.add_message_from_event(
        event, role="assistant", content="hello"
    )

    assert user_message.timestamp == 1_700_000_000.0
    assert assistant_message.timestamp == 1_700_000_000.0
    await store.close()


@pytest.mark.asyncio
async def test_add_message_from_event_falls_back_without_message_obj(tmp_path: Path):
    """平台未提供消息对象（老事件/测试替身）时回退接收时间，行为不变。"""
    store = ConversationStore(str(tmp_path / "event-ts-fallback.db"))
    await store.initialize()
    manager = ConversationManager(store=store)

    before = time.time()
    message = await manager.add_message_from_event(
        _DummyEvent("test:private:ts3"), role="user", content="hi"
    )
    after = time.time()

    assert before <= message.timestamp <= after
    await store.close()


@pytest.mark.parametrize(
    "bad_timestamp",
    [None, 0, -1, "bad", float("nan"), 1_700_000_000_000],
    ids=["none", "zero", "negative", "string", "nan", "milliseconds"],
)
@pytest.mark.asyncio
async def test_add_message_from_event_falls_back_for_implausible_timestamp(
    tmp_path: Path, bad_timestamp
):
    """非法或明显超前（毫秒时间戳/时钟异常）的平台时间戳回退接收时间。"""
    store = ConversationStore(str(tmp_path / "event-ts-bad.db"))
    await store.initialize()
    manager = ConversationManager(store=store)
    event = _TimestampedEvent("test:private:ts4", bad_timestamp)

    before = time.time()
    message = await manager.add_message_from_event(event, role="user", content="hi")
    after = time.time()

    assert before <= message.timestamp <= after
    await store.close()
