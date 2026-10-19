"""
Tests for ConversationManager behaviors.
"""

import asyncio
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


# ==================== #289 会话状态回收与元数据锁 ====================


async def _prepare_two_sessions(tmp_path: Path):
    """创建过期/活跃两个真实 SQLite 会话，并建立锁与修订号条目。"""
    store = ConversationStore(str(tmp_path / "session-state.db"))
    await store.initialize()
    manager = ConversationManager(store=store, session_ttl=60)
    expired_sid = "test:private:expired"
    live_sid = "test:private:live"
    await manager.add_message(session_id=expired_sid, role="user", content="old")
    await manager.add_message(session_id=live_sid, role="user", content="new")
    # 把过期会话的 last_active_at 拨回 TTL 之外
    await store.connection.execute(
        "UPDATE sessions SET last_active_at = ? WHERE session_id = ?",
        (time.time() - 3600, expired_sid),
    )
    await store.connection.commit()
    await manager.bump_session_revision(expired_sid)
    await manager.bump_session_revision(live_sid)
    return store, manager, expired_sid, live_sid


@pytest.mark.asyncio
async def test_cleanup_expired_sessions_prunes_session_state(tmp_path: Path):
    """#289：过期会话被清理后，其锁/修订号条目一并回收，活跃会话不受影响。"""
    store, manager, expired_sid, live_sid = await _prepare_two_sessions(tmp_path)
    assert manager._session_locks.get(expired_sid) is not None
    assert manager._session_revisions.get(expired_sid) == 1

    deleted = await manager.cleanup_expired_sessions()

    assert deleted == 1
    assert expired_sid not in manager._session_locks
    assert expired_sid not in manager._session_revisions
    assert live_sid in manager._session_locks
    assert manager._session_revisions.get(live_sid) == 1
    await store.close()


@pytest.mark.asyncio
async def test_cleanup_expired_sessions_keeps_busy_lock(tmp_path: Path):
    """#289：仍有在途操作持锁时不得回收，否则同一会话会出现两把锁。"""
    store, manager, expired_sid, _ = await _prepare_two_sessions(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()

    async def hold_lock():
        async with manager._session_lock(expired_sid):
            started.set()
            await release.wait()

    holder = asyncio.create_task(hold_lock())
    await asyncio.wait_for(started.wait(), 2)
    try:
        assert await manager.cleanup_expired_sessions() == 1
        assert expired_sid in manager._session_locks, "在用的锁必须保留"
        assert manager._session_revisions.get(expired_sid) == 1
    finally:
        release.set()
        await holder
        await store.close()


@pytest.mark.asyncio
async def test_concurrent_metadata_updates_keep_unrelated_keys(tmp_path: Path):
    """#289：并发无条件写入不得基于同一旧快照互相覆盖无关键。"""
    store = ConversationStore(str(tmp_path / "metadata-lock.db"))
    await store.initialize()
    manager = ConversationManager(store=store)
    sid = "test:private:metadata-lock"
    await manager.add_message(session_id=sid, role="user", content="hi")

    first_write_started = asyncio.Event()
    release_first_write = asyncio.Event()
    real_persist = manager._persist_session_metadata
    write_calls = 0

    async def controlled_persist(session_id, metadata):
        nonlocal write_calls
        write_calls += 1
        if write_calls == 1:
            # 第一个写入在"读快照 → 写回"之间暂停，给第二个写入插队机会
            first_write_started.set()
            await release_first_write.wait()
        return await real_persist(session_id, metadata)

    manager._persist_session_metadata = controlled_persist
    try:
        first = asyncio.create_task(
            manager.update_session_metadata(sid, "custom_key_a", 1)
        )
        await asyncio.wait_for(first_write_started.wait(), 2)
        second = asyncio.create_task(
            manager.update_session_metadata(sid, "custom_key_b", 2)
        )
        await asyncio.sleep(0.05)
        assert not second.done(), "第二个写入必须阻塞在会话锁上"
        release_first_write.set()
        await asyncio.wait_for(asyncio.gather(first, second), 2)
    finally:
        release_first_write.set()

    session = await store.get_session(sid)
    assert session.metadata["custom_key_a"] == 1
    assert session.metadata["custom_key_b"] == 2
    await store.close()


@pytest.mark.asyncio
async def test_update_session_metadata_if_revision_writes_under_lock(tmp_path: Path):
    """#289：条件写入改调无锁变体后，写入与修订号拒绝语义保持不变。"""
    store = ConversationStore(str(tmp_path / "if-revision.db"))
    await store.initialize()
    manager = ConversationManager(store=store)
    sid = "test:private:if-revision"
    await manager.add_message(session_id=sid, role="user", content="hi")

    assert (
        await manager.update_session_metadata_if_revision(sid, "custom_key", 7, 0)
        is True
    )
    session = await store.get_session(sid)
    assert session.metadata["custom_key"] == 7

    assert (
        await manager.update_session_metadata_if_revision(sid, "custom_key", 9, 99)
        is False
    )
    session = await store.get_session(sid)
    assert session.metadata["custom_key"] == 7
    await store.close()
