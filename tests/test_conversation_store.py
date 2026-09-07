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
async def test_add_message_normalizes_multimodal_content(tmp_path: Path):
    db_path = tmp_path / "conversations_multimodal.db"
    store = ConversationStore(str(db_path))
    await store.initialize()

    msg = Message(
        id=0,
        session_id="s1",
        role="user",
        content=[
            {"type": "image_url", "image_url": {"url": "https://example.test/a.png"}},
            {"type": "text", "text": "图片里的日程是下午三点"},
        ],
        sender_id="u1",
        sender_name="tester",
        group_id=None,
        platform="test",
        metadata={},
    )

    await store.add_message(msg)
    messages = await store.get_messages("s1", limit=10)

    assert messages[0].content == "图片里的日程是下午三点"
    assert "image_url" not in messages[0].content

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


@pytest.mark.asyncio
async def test_create_session_is_idempotent(tmp_path: Path):
    """重复创建同一会话应返回同一记录，不抛异常也不产生重复行。"""
    db_path = tmp_path / "create_session.db"
    store = ConversationStore(str(db_path))
    await store.initialize()

    first = await store.create_session("sess-dup", "test")
    second = await store.create_session("sess-dup", "test")

    assert first.session_id == "sess-dup"
    assert second.session_id == "sess-dup"
    assert first.id == second.id

    cursor = await store.connection.execute(
        "SELECT COUNT(*) FROM sessions WHERE session_id = ?", ("sess-dup",)
    )
    row = await cursor.fetchone()

    assert row[0] == 1

    await store.close()


def _make_msg(session_id: str, role: str, content: str, checkpoint: str | None = None):
    metadata = {"llm_checkpoint_id": checkpoint} if checkpoint else {}
    return Message(
        id=0,
        session_id=session_id,
        role=role,
        content=content,
        sender_id="sender-1",
        sender_name="tester",
        group_id=None,
        platform="test",
        metadata=metadata,
    )


async def _seed_turns(store: ConversationStore, session_id: str, turns: list[str]):
    for checkpoint in turns:
        await store.add_message(_make_msg(session_id, "user", f"u-{checkpoint}", checkpoint))
        await store.add_message(
            _make_msg(session_id, "assistant", f"a-{checkpoint}", checkpoint)
        )


async def _set_session_metadata(
    store: ConversationStore, session_id: str, metadata: dict
):
    import json

    await store.connection.execute(
        "UPDATE sessions SET metadata = ? WHERE session_id = ?",
        (json.dumps(metadata, ensure_ascii=False), session_id),
    )
    await store.connection.commit()


@pytest.mark.asyncio
async def test_delete_messages_from_position_trims_tail(tmp_path: Path):
    db_path = tmp_path / "delete_position.db"
    store = ConversationStore(str(db_path))
    await store.initialize()
    await _seed_turns(store, "s-del", ["C1", "C2"])

    deleted = await store.delete_messages_from_position("s-del", 2)
    assert deleted == 2
    assert await store.get_message_count("s-del") == 2

    remaining = await store.get_session_messages_asc("s-del")
    assert [m.role for m in remaining] == ["user", "assistant"]
    assert remaining[0].metadata["llm_checkpoint_id"] == "C1"

    await store.close()


@pytest.mark.asyncio
async def test_delete_messages_from_position_clamps_summary_cursor(tmp_path: Path):
    db_path = tmp_path / "delete_cursor.db"
    store = ConversationStore(str(db_path))
    await store.initialize()
    await _seed_turns(store, "s-cursor", ["C1"])

    # 游标超出被删范围之后 → 收敛到切口
    await _set_session_metadata(store, "s-cursor", {"last_summarized_index": 6})
    await store.delete_messages_from_position("s-cursor", 1)
    session = await store.get_session("s-cursor")
    assert session.message_count == 1
    assert session.metadata["last_summarized_index"] == 1

    await store.close()


@pytest.mark.asyncio
async def test_delete_messages_from_position_keeps_valid_cursor(tmp_path: Path):
    db_path = tmp_path / "delete_cursor2.db"
    store = ConversationStore(str(db_path))
    await store.initialize()
    await _seed_turns(store, "s-cursor2", ["C1", "C2"])

    # 游标在切口之前 → 保持不变
    await _set_session_metadata(store, "s-cursor2", {"last_summarized_index": 1})
    await store.delete_messages_from_position("s-cursor2", 2)
    session = await store.get_session("s-cursor2")
    assert session.message_count == 2
    assert session.metadata["last_summarized_index"] == 1

    await store.close()


@pytest.mark.asyncio
async def test_delete_messages_from_position_clears_pending_summary(tmp_path: Path):
    db_path = tmp_path / "delete_pending.db"
    store = ConversationStore(str(db_path))
    await store.initialize()
    await _seed_turns(store, "s-pending", ["C1"])

    # pending 范围整体落在被删区域 → 清空
    await _set_session_metadata(
        store,
        "s-pending",
        {"pending_summary": {"start_index": 2, "end_index": 4, "retry_count": 1}},
    )
    await store.delete_messages_from_position("s-pending", 1)
    session = await store.get_session("s-pending")
    assert "pending_summary" not in session.metadata

    await store.close()


@pytest.mark.asyncio
async def test_delete_messages_from_position_clamps_pending_summary(tmp_path: Path):
    db_path = tmp_path / "delete_pending2.db"
    store = ConversationStore(str(db_path))
    await store.initialize()
    await _seed_turns(store, "s-pending2", ["C1", "C2"])

    # pending 范围越界 → 钳制 end_index，保留 retry_count
    await _set_session_metadata(
        store,
        "s-pending2",
        {"pending_summary": {"start_index": 0, "end_index": 6, "retry_count": 2}},
    )
    await store.delete_messages_from_position("s-pending2", 2)
    session = await store.get_session("s-pending2")
    pending = session.metadata["pending_summary"]
    assert pending["end_index"] == 2
    assert pending["retry_count"] == 2

    await store.close()


@pytest.mark.asyncio
async def test_delete_messages_from_position_negative_and_out_of_range(tmp_path: Path):
    db_path = tmp_path / "delete_bounds.db"
    store = ConversationStore(str(db_path))
    await store.initialize()
    await _seed_turns(store, "s-bounds", ["C1"])

    assert await store.delete_messages_from_position("s-bounds", -1) == 0
    assert await store.delete_messages_from_position("s-bounds", 99) == 0
    assert await store.get_message_count("s-bounds") == 2

    # position 0 = 全部删除
    assert await store.delete_messages_from_position("s-bounds", 0) == 2
    assert await store.get_message_count("s-bounds") == 0

    await store.close()


@pytest.mark.asyncio
async def test_get_session_message_refs_asc_returns_minimal_fields(tmp_path: Path):
    """对账轻量查询应只返回 id/role/metadata，且保持会话顺序。"""
    db_path = tmp_path / "refs_asc.db"
    store = ConversationStore(str(db_path))
    await store.initialize()
    await _seed_turns(store, "s-refs", ["C1", "C2"])

    refs = await store.get_session_message_refs_asc("s-refs")
    assert [(r["role"], r["metadata"].get("llm_checkpoint_id")) for r in refs] == [
        ("user", "C1"),
        ("assistant", "C1"),
        ("user", "C2"),
        ("assistant", "C2"),
    ]
    assert all("content" not in r for r in refs)

    await store.close()
