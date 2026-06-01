"""
Tests for ConversationStore persistence behaviors.
"""

import json
from pathlib import Path

import pytest
from astrbot_plugin_livingmemory.core.models.conversation_models import Message
from astrbot_plugin_livingmemory.storage.conversation_store import ConversationStore


@pytest.mark.asyncio
async def test_conversation_store_crud(tmp_path: Path):
    db_path = tmp_path / "conversations.db"
    store = ConversationStore(str(db_path))
    await store.initialize()

    msg = Message(
        id=0,
        session_id="s1",
        role="user",
        content="hello",
        sender_id="u1",
        sender_name="tester",
        group_id=None,
        platform="test",
        metadata={},
    )
    mid = await store.add_message(msg)
    assert mid > 0

    session = await store.get_session("s1")
    assert session is not None
    assert session.message_count == 1

    msgs = await store.get_messages("s1", limit=10)
    assert len(msgs) == 1
    assert msgs[0].content == "hello"

    count = await store.get_message_count("s1")
    assert count == 1

    await store.update_message_metadata(mid, {"flag": True})
    msgs2 = await store.get_messages("s1", limit=10)
    assert msgs2[0].metadata["flag"] is True

    search = await store.search_messages("s1", "hell", limit=5)
    assert len(search) == 1

    deleted = await store.delete_session_messages("s1")
    assert deleted == 1
    assert await store.get_message_count("s1") == 0

    await store.close()


@pytest.mark.asyncio
async def test_trim_session_messages_respects_last_summarized_index(tmp_path: Path):
    db_path = tmp_path / "trim-safe.db"
    store = ConversationStore(str(db_path))
    await store.initialize()

    for index in range(5):
        msg = Message(
            id=0,
            session_id="s-trim",
            role="user",
            content=f"content-{index}",
            sender_id="u1",
            sender_name="tester",
            group_id=None,
            platform="test",
            metadata={},
        )
        await store.add_message(msg)

    await store.connection.execute(
        "UPDATE sessions SET metadata = ? WHERE session_id = ?",
        (json.dumps({"last_summarized_index": 2}), "s-trim"),
    )
    await store.connection.commit()

    deleted = await store.trim_session_messages("s-trim", 4)

    assert deleted == 2
    assert await store.get_message_count("s-trim") == 3

    remaining = await store.get_messages_range("s-trim", offset=0, limit=10)
    assert [message.content for message in remaining] == [
        "content-2",
        "content-3",
        "content-4",
    ]

    session = await store.get_session("s-trim")
    assert session is not None
    assert session.metadata["last_summarized_index"] == 0

    await store.close()


@pytest.mark.asyncio
async def test_trim_session_messages_skips_when_no_summary_marker(tmp_path: Path):
    db_path = tmp_path / "trim-no-marker.db"
    store = ConversationStore(str(db_path))
    await store.initialize()

    for index in range(3):
        msg = Message(
            id=0,
            session_id="s-no-marker",
            role="user",
            content=f"content-{index}",
            sender_id="u1",
            sender_name="tester",
            group_id=None,
            platform="test",
            metadata={},
        )
        await store.add_message(msg)

    deleted = await store.trim_session_messages("s-no-marker", 2)

    assert deleted == 0
    assert await store.get_message_count("s-no-marker") == 3
    remaining = await store.get_messages_range("s-no-marker", offset=0, limit=10)
    assert [message.content for message in remaining] == [
        "content-0",
        "content-1",
        "content-2",
    ]

    await store.close()


@pytest.mark.asyncio
async def test_conversation_store_ranges_and_stats(tmp_path: Path):
    db_path = tmp_path / "ranges.db"
    store = ConversationStore(str(db_path))
    await store.initialize()

    for i in range(5):
        msg = Message(
            id=0,
            session_id="s2",
            role="user" if i % 2 == 0 else "assistant",
            content=f"content-{i}",
            sender_id=f"u{i % 2}",
            sender_name=f"name-{i % 2}",
            group_id=None,
            platform="test",
            metadata={},
        )
        await store.add_message(msg)

    rng = await store.get_messages_range("s2", offset=1, limit=3)
    assert [m.content for m in rng] == ["content-1", "content-2", "content-3"]

    stats = await store.get_user_message_stats("s2")
    assert isinstance(stats, dict)
    assert sum(stats.values()) >= 1

    fixed = await store.sync_message_counts()
    assert isinstance(fixed, dict)

    await store.close()
