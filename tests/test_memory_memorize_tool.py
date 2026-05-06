"""Tests for the active long-term memory memorize tool."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock, patch

import pytest
from astrbot.api.platform import MessageType

from astrbot_plugin_livingmemory.core.tools.memory_memorize_tool import (
    MemoryMemorizeTool,
)


@pytest.fixture
def memory_engine():
    engine = Mock()
    engine.add_memory = AsyncMock(return_value=42)
    return engine


@pytest.fixture
def memory_processor():
    processor = Mock()
    processor.build_memory_from_structured_data = Mock(
        return_value=(
            "用户喜欢黑咖啡 | 不加糖",
            {
                "topics": ["饮食偏好"],
                "key_facts": ["不加糖"],
                "sentiment": "neutral",
                "interaction_type": "private_chat",
                "canonical_summary": "用户喜欢黑咖啡 | 不加糖",
                "persona_summary": "用户喜欢黑咖啡",
                "summary_schema_version": "v2",
                "summary_quality": "normal",
            },
            0.8,
        )
    )
    return processor


def _make_run_context(message_type=MessageType.FRIEND_MESSAGE):
    event = Mock()
    event.unified_msg_origin = "test:private:session-1"
    event.get_message_type = Mock(return_value=message_type)

    run_context = Mock()
    run_context.context = Mock()
    run_context.context.event = event
    return run_context


@pytest.mark.asyncio
async def test_memory_memorize_tool_writes_current_session_and_persona(
    memory_engine, memory_processor
):
    tool = MemoryMemorizeTool(
        context=Mock(),
        memory_engine=memory_engine,
        memory_processor=memory_processor,
    )

    with patch(
        "astrbot_plugin_livingmemory.core.tools.memory_memorize_tool.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_a"
        raw_result = await tool.call(
            _make_run_context(),
            memory="用户喜欢黑咖啡",
            topics=["饮食偏好"],
            key_facts=["不加糖"],
            importance=0.8,
        )

    result = json.loads(raw_result)
    assert result["memorized"] is True
    assert result["session_id"] == "test:private:session-1"
    assert result["persona_id"] == "persona_a"
    memory_engine.add_memory.assert_awaited_once()
    call_kwargs = memory_engine.add_memory.await_args.kwargs
    assert call_kwargs["session_id"] == "test:private:session-1"
    assert call_kwargs["persona_id"] == "persona_a"


@pytest.mark.asyncio
async def test_memory_memorize_tool_uses_memory_processor_format(
    memory_engine, memory_processor
):
    tool = MemoryMemorizeTool(
        context=Mock(),
        memory_engine=memory_engine,
        memory_processor=memory_processor,
    )

    with patch(
        "astrbot_plugin_livingmemory.core.tools.memory_memorize_tool.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_a"
        await tool.call(
            _make_run_context(),
            memory="用户喜欢黑咖啡",
            topics=["饮食偏好", "", "咖啡"],
            key_facts=["不加糖"],
            sentiment="neutral",
            importance=2.0,
            reason="用户明确要求记住",
        )

    memory_processor.build_memory_from_structured_data.assert_called_once_with(
        structured_data={
            "summary": "用户喜欢黑咖啡",
            "topics": ["饮食偏好", "咖啡"],
            "key_facts": ["不加糖"],
            "sentiment": "neutral",
            "importance": 2.0,
        },
        is_group_chat=False,
        fallback_excerpt="用户喜欢黑咖啡",
    )
    call_kwargs = memory_engine.add_memory.await_args.kwargs
    assert call_kwargs["content"] == "用户喜欢黑咖啡 | 不加糖"
    assert call_kwargs["importance"] == 0.8
    assert call_kwargs["metadata"]["source_window"] == {
        "session_id": "test:private:session-1",
        "triggered_by": "agent_tool",
        "tool_name": "memorize_long_term_memory",
    }
    assert call_kwargs["metadata"]["memory_origin"] == "agent_memorize_tool"
    assert call_kwargs["metadata"]["memorize_reason"] == "用户明确要求记住"


@pytest.mark.asyncio
async def test_memory_memorize_tool_detects_group_chat(memory_engine, memory_processor):
    tool = MemoryMemorizeTool(
        context=Mock(),
        memory_engine=memory_engine,
        memory_processor=memory_processor,
    )

    with patch(
        "astrbot_plugin_livingmemory.core.tools.memory_memorize_tool.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_a"
        await tool.call(
            _make_run_context(MessageType.GROUP_MESSAGE),
            memory="群里约定周五复盘",
        )

    assert memory_processor.build_memory_from_structured_data.call_args.kwargs[
        "is_group_chat"
    ] is True


@pytest.mark.asyncio
async def test_memory_memorize_tool_returns_error_for_empty_memory(
    memory_engine, memory_processor
):
    tool = MemoryMemorizeTool(
        context=Mock(),
        memory_engine=memory_engine,
        memory_processor=memory_processor,
    )

    raw_result = await tool.call(_make_run_context(), memory="   ")
    result = json.loads(raw_result)

    assert result == {"memorized": False, "error": "memory is empty"}
    memory_processor.build_memory_from_structured_data.assert_not_called()
    memory_engine.add_memory.assert_not_called()


@pytest.mark.asyncio
async def test_memory_memorize_tool_returns_not_initialized_error(memory_engine):
    tool = MemoryMemorizeTool(
        context=None,
        memory_engine=memory_engine,
        memory_processor=None,
    )

    raw_result = await tool.call(_make_run_context(), memory="需要记住的内容")
    result = json.loads(raw_result)

    assert result == {
        "memorized": False,
        "error": "memory memorize tool is not initialized",
    }
    memory_engine.add_memory.assert_not_called()


@pytest.mark.asyncio
async def test_memory_memorize_tool_hides_internal_exception_details(
    memory_engine, memory_processor
):
    tool = MemoryMemorizeTool(
        context=Mock(),
        memory_engine=memory_engine,
        memory_processor=memory_processor,
    )
    memory_engine.add_memory = AsyncMock(side_effect=RuntimeError("secret db path"))

    with patch(
        "astrbot_plugin_livingmemory.core.tools.memory_memorize_tool.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_a"
        raw_result = await tool.call(_make_run_context(), memory="异常测试")

    result = json.loads(raw_result)
    assert result == {"memorized": False, "error": "internal_error"}
    assert "secret db path" not in raw_result


@pytest.mark.asyncio
async def test_memory_memorize_tool_propagates_cancellation(
    memory_engine, memory_processor
):
    tool = MemoryMemorizeTool(
        context=Mock(),
        memory_engine=memory_engine,
        memory_processor=memory_processor,
    )
    memory_engine.add_memory = AsyncMock(side_effect=asyncio.CancelledError())

    with patch(
        "astrbot_plugin_livingmemory.core.tools.memory_memorize_tool.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_a"
        with pytest.raises(asyncio.CancelledError):
            await tool.call(_make_run_context(), memory="取消测试")
