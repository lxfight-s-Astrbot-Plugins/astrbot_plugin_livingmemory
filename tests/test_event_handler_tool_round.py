"""#395：带工具调用的轮次整轮入库（插件侧），使用真实会话库断言。

AstrBot 的 OpenAI 兼容 Provider 在响应出现 ``tool_calls`` 时会把
``LLMResponse.role`` 改写为 ``"tool"``；这些轮次的助手文字仍需写入会话库，
但 tool loop 尚未结束，不能触发后台总结。

断言对象是真实 SQLite 会话库里的消息，而不是协作对象的调用记录。
"""

from unittest.mock import Mock

import pytest

from types import SimpleNamespace

from astrbot.core.message.components import Plain

from tests.real_engine_support import (
    make_handler_bundle as _make_real_handler,
)


class _Event:
    """Minimal AstrBot event carrying a plain-text message."""

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


def _make_event() -> _Event:
    return _Event()


def _make_resp(text: str = "assistant reply"):
    return SimpleNamespace(
        role="assistant",
        completion_text=text,
        is_chunk=False,
        tools_call_name=None,
        tools_call_extra_content=None,
    )


def _make_tool_resp(text: str = "工具轮助手文字"):
    resp = _make_resp(text)
    resp.role = "tool"
    resp.tools_call_name = ["send_message"]
    return resp


async def _assistant_messages(manager, session_id: str) -> list[str]:
    messages = await manager.get_context(
        session_id, max_messages=50, format_for_llm=False
    )
    result: list[str] = []
    for message in messages:
        role = message.get("role") if isinstance(message, dict) else message.role
        content = (
            message.get("content") if isinstance(message, dict) else message.content
        )
        if role == "assistant":
            result.append(content)
    return result


@pytest.mark.asyncio
async def test_tool_round_is_stored_without_triggering_reflection(tmp_path):
    handler, manager, store = await _make_real_handler(tmp_path)
    event = _make_event()
    try:
        await handler.handle_memory_reflection(event, _make_tool_resp("正在查询天气"))

        assert await _assistant_messages(manager, event.unified_msg_origin) == [
            "正在查询天气"
        ]
        assert not handler._storage_tasks
    finally:
        await handler.shutdown()
        await store.close()


@pytest.mark.asyncio
async def test_streaming_chunk_is_not_stored(tmp_path):
    handler, manager, store = await _make_real_handler(tmp_path)
    event = _make_event()
    resp = _make_tool_resp()
    resp.is_chunk = True
    try:
        await handler.handle_memory_reflection(event, resp)

        assert await _assistant_messages(manager, event.unified_msg_origin) == []
    finally:
        await handler.shutdown()
        await store.close()


@pytest.mark.asyncio
async def test_tool_round_without_text_is_not_stored(tmp_path):
    handler, manager, store = await _make_real_handler(tmp_path)
    event = _make_event()
    try:
        await handler.handle_memory_reflection(event, _make_tool_resp(""))

        assert await _assistant_messages(manager, event.unified_msg_origin) == []
    finally:
        await handler.shutdown()
        await store.close()


@pytest.mark.parametrize(
    "marker",
    ["tools_call_name", "tools_call_extra_content"],
    ids=["tool-call-name", "tool-call-extra-content"],
)
@pytest.mark.asyncio
async def test_assistant_role_with_tool_markers_is_stored_without_reflection(
    tmp_path, marker
):
    handler, manager, store = await _make_real_handler(tmp_path)
    event = _make_event()
    resp = _make_resp("工具轮助手文字")
    if marker == "tools_call_name":
        resp.tools_call_name = ["send_message"]
    else:
        resp.tools_call_extra_content = {"call_1": {"thought_signature": "sig"}}
    try:
        await handler.handle_memory_reflection(event, resp)

        assert await _assistant_messages(manager, event.unified_msg_origin) == [
            "工具轮助手文字"
        ]
        assert not handler._storage_tasks
    finally:
        await handler.shutdown()
        await store.close()


@pytest.mark.asyncio
async def test_plain_assistant_round_is_stored_and_summarized(tmp_path):
    """普通轮次行为不变：写入会话库，并由后台任务总结成长期记忆。"""
    engine = Mock()
    engine.add_memory = Mock()
    handler, manager, store = await _make_real_handler(tmp_path, memory_engine=engine)
    handler.config_manager = type(handler.config_manager)(
        {
            "recall_engine": {"top_k": 3},
            "reflection_engine": {"summary_trigger_rounds": 1},
            "session_manager": {"max_messages_per_session": 100},
            "filtering_settings": {"use_persona_filtering": False},
        }
    )
    event = _make_event()
    try:
        await handler.handle_memory_reflection(event, _make_resp("普通助手回复"))

        assert await _assistant_messages(manager, event.unified_msg_origin) == [
            "普通助手回复"
        ]
    finally:
        await handler.shutdown()
        await store.close()
