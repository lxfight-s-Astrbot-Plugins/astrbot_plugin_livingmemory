"""
Tests for chatroom parser.
"""

from astrbot_plugin_livingmemory.core.processors.chatroom_parser import (
    ChatroomContextParser,
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
