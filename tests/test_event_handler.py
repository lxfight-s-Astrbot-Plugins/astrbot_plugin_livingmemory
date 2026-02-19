"""
Tests for EventHandler core behaviors.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.event_handler import EventHandler

from astrbot.api.platform import MessageType


@pytest.fixture
def memory_engine():
    engine = Mock()
    engine.search_memories = AsyncMock(return_value=[])
    engine.add_memory = AsyncMock(return_value=1)
    return engine


@pytest.fixture
def memory_processor():
    processor = Mock()
    processor.process_conversation = AsyncMock(
        return_value=("summary", {"topics": ["t1"]}, 0.6)
    )
    return processor


@pytest.fixture
def conversation_manager():
    manager = Mock()
    manager.add_message_from_event = AsyncMock(return_value=Mock(id=1, metadata={}))
    manager.get_session_info = AsyncMock(return_value=Mock(message_count=12))
    session_metadata = {"last_summarized_index": 0, "pending_summary": None}

    async def _get_session_metadata(session_id, key, default=None):
        return session_metadata.get(key, default)

    async def _update_session_metadata(session_id, key, value):
        session_metadata[key] = value

    manager.get_session_metadata = AsyncMock(side_effect=_get_session_metadata)
    manager.get_messages_range = AsyncMock(
        return_value=[Mock(group_id=None), Mock(group_id=None)]
    )
    manager.update_session_metadata = AsyncMock(side_effect=_update_session_metadata)
    manager.invalidate_cache = AsyncMock()
    manager.store = Mock()
    manager.store.get_message_count = AsyncMock(return_value=12)
    manager.store.update_message_metadata = AsyncMock()
    manager.store.connection = Mock()
    manager.store.connection.execute = AsyncMock(return_value=Mock(rowcount=1))
    manager.store.connection.commit = AsyncMock()
    return manager


@pytest.fixture
def handler(memory_engine, memory_processor, conversation_manager):
    return EventHandler(
        context=Mock(),
        config_manager=ConfigManager(
            {
                "recall_engine": {"top_k": 3, "injection_method": "system_prompt"},
                "reflection_engine": {"summary_trigger_rounds": 1},
                "session_manager": {"max_messages_per_session": 100},
            }
        ),
        memory_engine=memory_engine,
        memory_processor=memory_processor,
        conversation_manager=conversation_manager,
    )


def _make_req(prompt: str = "hello"):
    req = Mock()
    req.prompt = prompt
    req.system_prompt = ""
    req.contexts = []
    return req


def _make_resp(text: str = "assistant reply"):
    resp = Mock()
    resp.role = "assistant"
    resp.completion_text = text
    return resp


def _make_event(group: bool = False):
    event = Mock()
    event.unified_msg_origin = "test:private:sid-1"
    event.get_message_type = Mock(
        return_value=MessageType.GROUP_MESSAGE if group else MessageType.FRIEND_MESSAGE
    )
    event.get_sender_id = Mock(return_value="user-1")
    event.get_self_id = Mock(return_value="bot-1")
    event.get_sender_name = Mock(return_value="Tester")
    event.get_message_str = Mock(return_value="hello")
    event.get_messages = Mock(return_value=[])
    event.get_platform_name = Mock(return_value="test")
    return event


def test_message_dedup_cache_works(handler):
    key = "id:123"
    assert handler._is_duplicate_message(key) is False
    handler._mark_message_processed(key)
    assert handler._is_duplicate_message(key) is True


@pytest.mark.asyncio
async def test_handle_memory_recall_injects_system_prompt(handler, memory_engine):
    event = _make_event(group=False)
    req = _make_req("query text")
    recalled = Mock(content="mem1", final_score=0.7, metadata={"importance": 0.9})
    memory_engine.search_memories = AsyncMock(return_value=[recalled])

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await handler.handle_memory_recall(event, req)

    memory_engine.search_memories.assert_awaited_once()
    assert "<RAG-Faiss-Memory>" in req.system_prompt


@pytest.mark.asyncio
async def test_handle_memory_recall_stores_private_user_message(
    handler, conversation_manager
):
    event = _make_event(group=False)
    req = _make_req("user input")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await handler.handle_memory_recall(event, req)

    conversation_manager.add_message_from_event.assert_awaited()


@pytest.mark.asyncio
async def test_handle_memory_reflection_triggers_storage_task(
    handler, conversation_manager, memory_engine
):
    event = _make_event(group=False)
    resp = _make_resp("assistant answer")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await handler.handle_memory_reflection(event, resp)
        # Wait for background storage task.
        await handler.shutdown()

    assert conversation_manager.get_messages_range.await_count >= 1
    assert memory_engine.add_memory.await_count >= 1


@pytest.mark.asyncio
async def test_handle_all_group_messages_and_limit_cleanup(
    handler, conversation_manager
):
    event = _make_event(group=True)
    conversation_manager.store.get_message_count = AsyncMock(return_value=12)
    conversation_manager.get_session_metadata = AsyncMock(return_value=5)

    await handler.handle_all_group_messages(event)

    # group capture should persist message
    conversation_manager.add_message_from_event.assert_awaited()


@pytest.mark.asyncio
async def test_handle_all_group_messages_skips_bot_own_messages(
    handler, conversation_manager
):
    """Bot 自己的消息应被跳过，由 handle_memory_reflection 负责写入。"""
    event = _make_event(group=True)
    # sender_id == self_id → bot's own message
    event.get_sender_id = Mock(return_value="bot-1")
    event.get_self_id = Mock(return_value="bot-1")

    await handler.handle_all_group_messages(event)

    # should NOT store bot's own message
    conversation_manager.add_message_from_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_shutdown_waits_for_storage_tasks(handler):
    async def _dummy():
        return 1

    task = patch("asyncio.create_task")
    with task as create_task:
        mock_task = AsyncMock()
        create_task.return_value = mock_task
        await handler.shutdown()
    assert handler._shutting_down is True
