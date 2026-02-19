"""
Tests for chatroom parser and message utility helpers.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from astrbot_plugin_livingmemory.core.processors.chatroom_parser import (
    ChatroomContextParser,
)
from astrbot_plugin_livingmemory.core.processors.message_utils import (
    MAX_SINGLE_MESSAGE_LENGTH,
    store_round_with_length_check,
    truncate_message_if_needed,
)


def test_chatroom_context_detection_and_extract():
    prompt = (
        "You are now in a chatroom. The chat history is as follows:\n"
        "[A/10:30]: hi\n---\n"
        "Now, a new message is coming: `\n"
        "[User ID: 123, Nickname: A]\n"
        "今天吃什么?`.\n"
        "Please react to it."
    )

    assert ChatroomContextParser.is_chatroom_context(prompt) is True
    assert ChatroomContextParser.extract_actual_message(prompt) == "今天吃什么?"


def test_chatroom_extract_returns_original_for_non_chatroom():
    prompt = "normal prompt"
    assert ChatroomContextParser.is_chatroom_context(prompt) is False
    assert ChatroomContextParser.extract_actual_message(prompt) == prompt


def test_truncate_message_if_needed():
    short = "hello"
    out, truncated = truncate_message_if_needed(short, max_length=10)
    assert out == short
    assert truncated is False

    long_text = "x" * 20
    out2, truncated2 = truncate_message_if_needed(long_text, max_length=10)
    assert truncated2 is True
    assert out2.startswith("x" * 10)


@pytest.mark.asyncio
async def test_store_round_with_length_check_success():
    engine = Mock()
    engine.add_memory = AsyncMock(return_value=1)
    user_msg = SimpleNamespace(content="u" * 5, role="user")
    assistant_msg = SimpleNamespace(content="a" * 5, role="assistant")

    ok, error = await store_round_with_length_check(
        memory_engine=engine,
        user_msg=user_msg,
        assistant_msg=assistant_msg,
        session_id="sid",
        persona_id="persona",
        round_index=1,
    )

    assert ok is True
    assert error == ""
    engine.add_memory.assert_awaited_once()


@pytest.mark.asyncio
async def test_store_round_with_length_check_truncates_and_stores():
    engine = Mock()
    engine.add_memory = AsyncMock(return_value=1)
    too_long = "x" * (MAX_SINGLE_MESSAGE_LENGTH * 2)
    user_msg = SimpleNamespace(content=too_long, role="user")
    assistant_msg = SimpleNamespace(content="ok", role="assistant")

    ok, error = await store_round_with_length_check(
        memory_engine=engine,
        user_msg=user_msg,
        assistant_msg=assistant_msg,
        session_id="sid",
        persona_id="persona",
        round_index=2,
    )

    assert ok is True
    assert error == ""
    engine.add_memory.assert_awaited_once()
    payload = engine.add_memory.await_args.kwargs
    assert payload["metadata"]["truncated"] is True
    assert len(payload["content"]) <= MAX_SINGLE_MESSAGE_LENGTH * 2


@pytest.mark.asyncio
async def test_store_round_with_length_check_skip_when_still_too_long():
    engine = Mock()
    engine.add_memory = AsyncMock(return_value=1)
    very_long = "x" * (MAX_SINGLE_MESSAGE_LENGTH * 3)
    user_msg = SimpleNamespace(content=very_long, role="user")
    assistant_msg = SimpleNamespace(content=very_long, role="assistant")

    ok, error = await store_round_with_length_check(
        memory_engine=engine,
        user_msg=user_msg,
        assistant_msg=assistant_msg,
        session_id="sid",
        persona_id="persona",
        round_index=3,
    )

    assert ok is False
    assert "仍过长" in error
    engine.add_memory.assert_not_called()
