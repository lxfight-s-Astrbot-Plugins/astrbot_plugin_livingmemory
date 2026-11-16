"""Exercise message count hints through actual handlers and SQLite."""

from unittest.mock import AsyncMock, patch

import pytest

from tests.event_handler_support import _make_event, _make_real_handler, _make_req, _make_resp


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["private", "tool", "reply"])
async def test_message_routes_avoid_redundant_count_queries(tmp_path, route):
    handler, manager, store = await _make_real_handler(tmp_path)
    event = _make_event(group=False)
    store.get_message_count = AsyncMock(wraps=store.get_message_count)
    try:
        if route == "private":
            with patch(
                "astrbot_plugin_livingmemory.core.utils.get_persona_id",
                new_callable=AsyncMock,
                return_value="persona",
            ):
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
        await handler.shutdown()
        await store.close()
