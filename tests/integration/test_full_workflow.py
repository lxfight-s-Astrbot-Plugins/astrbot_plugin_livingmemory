"""
Integration-style workflow tests with mocked dependencies.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.command_handler import CommandHandler
from astrbot_plugin_livingmemory.core.event_handler import EventHandler

from astrbot.api.platform import MessageType


@pytest.fixture
def setup_bundle():
    context = Mock()
    config_manager = ConfigManager(
        {
            "recall_engine": {"top_k": 3},
            "reflection_engine": {"summary_trigger_rounds": 1},
        }
    )

    memory_engine = Mock()
    memory_engine.search_memories = AsyncMock(return_value=[])
    memory_engine.add_memory = AsyncMock(return_value=1)
    memory_engine.get_statistics = AsyncMock(
        return_value={
            "total_memories": 0,
            "sessions": {},
            "newest_memory": None,
        }
    )

    memory_processor = Mock()
    memory_processor.process_conversation = AsyncMock(
        return_value=("摘要", {"topics": ["测试"]}, 0.7)
    )

    conversation_manager = Mock()
    conversation_manager.add_message_from_event = AsyncMock(
        return_value=Mock(id=1, metadata={})
    )
    conversation_manager.get_session_info = AsyncMock(
        return_value=Mock(message_count=2)
    )
    session_metadata = {"last_summarized_index": 0, "pending_summary": None}

    async def _get_session_metadata(session_id, key, default=None):
        return session_metadata.get(key, default)

    async def _update_session_metadata(session_id, key, value):
        session_metadata[key] = value

    conversation_manager.get_session_metadata = AsyncMock(
        side_effect=_get_session_metadata
    )
    conversation_manager.get_messages_range = AsyncMock(
        return_value=[Mock(group_id=None), Mock(group_id=None)]
    )
    conversation_manager.update_session_metadata = AsyncMock(
        side_effect=_update_session_metadata
    )
    conversation_manager.clear_session = AsyncMock()
    conversation_manager.store = Mock()
    conversation_manager.store.get_message_count = AsyncMock(return_value=2)
    conversation_manager.store.update_message_metadata = AsyncMock()
    conversation_manager.store.connection = Mock()
    conversation_manager.store.connection.execute = AsyncMock(
        return_value=Mock(rowcount=1)
    )
    conversation_manager.store.connection.commit = AsyncMock()

    event_handler = EventHandler(
        context=context,
        config_manager=config_manager,
        memory_engine=memory_engine,
        memory_processor=memory_processor,
        conversation_manager=conversation_manager,
    )
    command_handler = CommandHandler(
        context=context,
        config_manager=config_manager,
        memory_engine=memory_engine,
        conversation_manager=conversation_manager,
        index_validator=Mock(),
    )

    return event_handler, command_handler, memory_engine, conversation_manager


def _make_event():
    event = Mock()
    event.unified_msg_origin = "test:private:u1"
    event.get_message_type = Mock(return_value=MessageType.FRIEND_MESSAGE)
    event.get_sender_id = Mock(return_value="u1")
    event.get_self_id = Mock(return_value="bot")
    event.get_sender_name = Mock(return_value="Tester")
    event.get_message_str = Mock(return_value="hello")
    event.get_messages = Mock(return_value=[])
    event.get_platform_name = Mock(return_value="test")
    event.plain_result = lambda x: x
    return event


@pytest.mark.asyncio
async def test_recall_reflection_and_search_workflow(setup_bundle):
    event_handler, command_handler, memory_engine, conversation_manager = setup_bundle
    event = _make_event()

    req = Mock()
    req.prompt = "用户提问"
    req.system_prompt = ""
    req.contexts = []
    req.extra_user_content_parts = []

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_a"
        await event_handler.handle_memory_recall(event, req)

    assert memory_engine.search_memories.await_count == 1

    resp = Mock(role="assistant", completion_text="助手回复", tools_call_name=None, tools_call_extra_content=None)
    with patch(
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_a"
        await event_handler.handle_memory_reflection(event, resp)
        await event_handler.shutdown()

    assert memory_engine.add_memory.await_count >= 1

    search_messages = [
        msg async for msg in command_handler.handle_search(event, query="测试", k=5)
    ]
    assert len(search_messages) == 1
    assert "记忆" in search_messages[0]

    reset_messages = [msg async for msg in command_handler.handle_reset(event)]
    assert "重置" in reset_messages[0]
    conversation_manager.clear_session.assert_awaited_once()
