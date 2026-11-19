"""Exercise message count hints through actual handlers and SQLite."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot.core.message.components import Plain

from tests.real_engine_support import HostContext, close_handler, make_handler


class _Event:
    """Minimal private-message event for black-box handler tests."""

    unified_msg_origin = "test:private:sid-1"
    message_str = "hello"
    sender_id = "user-1"
    sender_name = "Tester"

    def get_message_type(self):
        from astrbot.api.platform import MessageType

        return MessageType.FRIEND_MESSAGE

    def get_sender_id(self):
        return self.sender_id

    def get_self_id(self):
        return "bot-1"

    def get_sender_name(self):
        return self.sender_name

    def get_message_str(self):
        return self.message_str

    def get_messages(self):
        return [Plain(self.message_str)]

    def get_platform_name(self):
        return "test"

    def get_platform_id(self):
        return "test-instance"

    def get_group_id(self):
        return ""

    def get_extra(self, key, default=None):
        return default


def _make_req(prompt: str = "hello"):
    return SimpleNamespace(
        prompt=prompt,
        system_prompt="",
        contexts=[],
        extra_user_content_parts=[],
    )


def _make_resp(text: str = "assistant reply"):
    return SimpleNamespace(
        role="assistant",
        completion_text=text,
        is_chunk=False,
        tools_call_name=None,
        tools_call_extra_content=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["private", "tool", "reply"])
async def test_message_routes_avoid_redundant_count_queries(tmp_path, route):
    bundle = await make_handler(tmp_path, host=HostContext(persona_id="persona"))
    handler, _manager, store, _engine, _db = bundle
    event = _Event()
    store.get_message_count = AsyncMock(wraps=store.get_message_count)
    try:
        if route == "private":
            await handler.handle_memory_recall(event, _make_req("hello"))
        elif route == "tool":
            response = _make_resp("tool result")
            response.role = "tool"
            await handler.handle_memory_reflection(event, response)
        else:
            await handler._memory_reflection._reflection_plan(
                event, event.unified_msg_origin, "reply"
            )
        # Normal reflection still needs one fresh count for its summary cursor.
        assert store.get_message_count.await_count == (1 if route == "reply" else 0)
        assert await store.get_message_count(event.unified_msg_origin) == 1
    finally:
        await close_handler(*bundle)
