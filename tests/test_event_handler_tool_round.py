"""#395：带工具调用的轮次整轮入库（插件侧）测试。

AstrBot 的 OpenAI 兼容 Provider 在响应出现 ``tool_calls`` 时会把
``LLMResponse.role`` 改写为 ``"tool"``；这些轮次的助手文字仍需写入会话库，
但 tool loop 尚未结束，不能触发后台总结。
"""

from unittest.mock import AsyncMock, patch

import pytest

from tests.event_handler_support import _make_event, _make_resp
from tests.event_handler_support import conversation_manager as conversation_manager
from tests.event_handler_support import handler as handler
from tests.event_handler_support import memory_engine as memory_engine
from tests.event_handler_support import memory_processor as memory_processor


def _make_tool_resp(text: str = "工具轮助手文字"):
    resp = _make_resp(text)
    resp.role = "tool"
    resp.tools_call_name = ["send_message"]
    return resp


@pytest.mark.asyncio
async def test_tool_round_records_assistant_message_without_summary(
    handler, conversation_manager
):
    """role="tool" 且有助手文字：只写助手消息，不产生后台总结任务。"""
    handler._message_utils.enforce_message_limit = AsyncMock()
    event = _make_event()
    resp = _make_tool_resp("正在查询天气")

    await handler.handle_memory_reflection(event, resp)

    conversation_manager.add_message_from_event.assert_awaited_once_with(
        event=event,
        role="assistant",
        content="正在查询天气",
    )
    handler._message_utils.enforce_message_limit.assert_awaited_once_with(
        "test:private:sid-1"
    )
    assert not handler._storage_tasks


@pytest.mark.asyncio
async def test_tool_round_chunk_is_not_recorded(handler, conversation_manager):
    """流式增量（is_chunk=True）不是完整回复，不写入会话库。"""
    handler._message_utils.enforce_message_limit = AsyncMock()
    resp = _make_tool_resp()
    resp.is_chunk = True

    await handler.handle_memory_reflection(_make_event(), resp)

    conversation_manager.add_message_from_event.assert_not_awaited()
    handler._message_utils.enforce_message_limit.assert_not_awaited()
    assert not handler._storage_tasks


@pytest.mark.asyncio
async def test_tool_round_without_text_is_not_recorded(handler, conversation_manager):
    """纯工具调用没有助手文字时不写库。"""
    handler._message_utils.enforce_message_limit = AsyncMock()
    resp = _make_tool_resp("")

    await handler.handle_memory_reflection(_make_event(), resp)

    conversation_manager.add_message_from_event.assert_not_awaited()
    handler._message_utils.enforce_message_limit.assert_not_awaited()
    assert not handler._storage_tasks


@pytest.mark.parametrize(
    "marker",
    ["tools_call_name", "tools_call_extra_content"],
    ids=["tool-call-name", "tool-call-extra-content"],
)
@pytest.mark.asyncio
async def test_assistant_tool_markers_record_without_summary(
    handler, conversation_manager, marker
):
    """assistant role 但携带工具调用元数据同样按工具轮处理（写库不总结）。"""
    handler._message_utils.enforce_message_limit = AsyncMock()
    resp = _make_resp("工具轮助手文字")
    if marker == "tools_call_name":
        resp.tools_call_name = ["send_message"]
    else:
        resp.tools_call_extra_content = {"call_1": {"thought_signature": "sig"}}

    await handler.handle_memory_reflection(_make_event(), resp)

    conversation_manager.add_message_from_event.assert_awaited_once()
    handler._message_utils.enforce_message_limit.assert_awaited_once()
    assert not handler._storage_tasks


@pytest.mark.asyncio
async def test_plain_assistant_round_still_writes_and_triggers_summary(
    handler, conversation_manager, memory_engine
):
    """普通 assistant 轮次行为不变：写库并调度后台总结。"""
    handler._message_utils.enforce_message_limit = AsyncMock()
    event = _make_event()
    resp = _make_resp("普通助手回复")

    with patch(
        "astrbot_plugin_livingmemory.core.utils.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await handler.handle_memory_reflection(event, resp)
        # 任务已创建但尚未开始执行（create_task 尚未让出事件循环）
        assert handler._storage_tasks

    await handler.shutdown()

    conversation_manager.add_message_from_event.assert_awaited_once_with(
        event=event,
        role="assistant",
        content="普通助手回复",
    )
    memory_engine.add_memory.assert_awaited()


@pytest.mark.asyncio
async def test_tool_round_skipped_when_response_superseded(
    handler, conversation_manager
):
    """被取代回复守卫优先：工具轮同样不写库。"""
    handler._message_utils.enforce_message_limit = AsyncMock()
    handler._memory_reflection._is_superseded_response = AsyncMock(return_value=True)
    resp = _make_tool_resp()

    await handler.handle_memory_reflection(_make_event(), resp)

    conversation_manager.add_message_from_event.assert_not_awaited()
    handler._message_utils.enforce_message_limit.assert_not_awaited()
    assert not handler._storage_tasks
