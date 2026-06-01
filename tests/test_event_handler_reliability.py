"""Reliability-focused tests for EventHandler."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest
from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.event_handler import EventHandler

from astrbot.api.platform import MessageType


def _make_handler() -> EventHandler:
    memory_engine = Mock()
    memory_engine.search_memories = AsyncMock(return_value=[])
    memory_engine.add_memory = AsyncMock(return_value=1)

    memory_processor = Mock()
    memory_processor.process_conversation = AsyncMock(return_value=("summary", {}, 0.5))

    conversation_manager = Mock()
    conversation_manager.add_message_from_event = AsyncMock(return_value=Mock(id=1))
    conversation_manager.get_session_info = AsyncMock(
        return_value=Mock(message_count=2)
    )
    conversation_manager.get_session_metadata = AsyncMock(return_value=0)
    conversation_manager.update_session_metadata = AsyncMock()
    conversation_manager.get_messages_range = AsyncMock(return_value=[])
    conversation_manager.store = Mock()
    conversation_manager.store.get_message_count = AsyncMock(return_value=2)

    handler = EventHandler(
        context=Mock(),
        config_manager=ConfigManager(
            {
                "recall_engine": {"top_k": 3, "injection_method": "extra_user_content"},
                "reflection_engine": {"summary_trigger_rounds": 1},
                "session_manager": {"max_messages_per_session": 100},
            }
        ),
        memory_engine=memory_engine,
        memory_processor=memory_processor,
        conversation_manager=conversation_manager,
    )
    return handler


def _make_event(group: bool = False) -> Mock:
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


def _make_req(prompt: str = "hello") -> Mock:
    req = Mock()
    req.prompt = prompt
    req.system_prompt = ""
    req.contexts = []
    req.extra_user_content_parts = []
    return req


def _make_resp(text: str = "assistant reply") -> Mock:
    resp = Mock()
    resp.role = "assistant"
    resp.completion_text = text
    resp.tools_call_name = None
    resp.tools_call_extra_content = None
    return resp


@pytest.mark.asyncio
async def test_handle_all_group_messages_reraises_cancelled_error() -> None:
    handler = _make_handler()
    handler.conversation_manager.add_message_from_event = AsyncMock(
        side_effect=asyncio.CancelledError()
    )

    with pytest.raises(asyncio.CancelledError):
        await handler.handle_all_group_messages(_make_event(group=True))


@pytest.mark.asyncio
async def test_handle_memory_recall_reraises_cancelled_error() -> None:
    handler = _make_handler()
    handler.memory_engine.search_memories = AsyncMock(
        side_effect=asyncio.CancelledError()
    )

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        with pytest.raises(asyncio.CancelledError):
            await handler.handle_memory_recall(_make_event(), _make_req("query"))


@pytest.mark.asyncio
async def test_handle_memory_reflection_reraises_cancelled_error() -> None:
    handler = _make_handler()
    handler.conversation_manager.add_message_from_event = AsyncMock(
        side_effect=asyncio.CancelledError()
    )

    with pytest.raises(asyncio.CancelledError):
        await handler.handle_memory_reflection(_make_event(), _make_resp())


@pytest.mark.asyncio
async def test_handle_memory_recall_awaits_async_get_message_str() -> None:
    handler = _make_handler()
    event = _make_event()

    async def get_message_str() -> str:
        return "async getter text"

    event.get_message_str = get_message_str

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await handler.handle_memory_recall(event, _make_req("query"))

    handler.memory_engine.search_memories.assert_awaited_once()
    assert (
        handler.memory_engine.search_memories.await_args.kwargs["query"]
        == "async getter text"
    )


@pytest.mark.asyncio
async def test_extract_message_content_skips_unknown_components() -> None:
    from astrbot.core.message.components import Plain

    handler = _make_handler()
    event = _make_event()
    unknown = Mock(type="sticker")
    event.get_messages = Mock(return_value=[Plain("hello"), unknown])

    content = await handler._extract_message_content(event)

    assert content == "hello"
