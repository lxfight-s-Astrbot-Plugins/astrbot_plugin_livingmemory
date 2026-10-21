"""
Event handler tests: message capture, recall injection and storage basics.
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.event_handler import EventHandler

from astrbot.api.platform import MessageType



from tests.event_handler_support import (
    _make_event,
    _make_real_handler,
    _make_req,
    _make_resp,
    _seed_turns,
    conversation_manager,
    handler,
    memory_engine,
    memory_processor,
)

@pytest.mark.asyncio
async def test_message_dedup_cache_works(handler):
    key = "id:123"
    assert await handler._message_utils.is_duplicate_message(key) is False
    await handler._message_utils.mark_message_processed(key)
    assert await handler._message_utils.is_duplicate_message(key) is True


@pytest.mark.asyncio
async def test_handle_memory_recall_injects_extra_user_content(handler, memory_engine):
    """extra_user_content 注入方式：记忆应追加到 extra_user_content_parts 并标记为临时消息。"""
    event = _make_event(group=False)
    req = _make_req("query text")
    recalled = Mock(content="mem1", final_score=0.7, metadata={"importance": 0.9})
    memory_engine.search_memories = AsyncMock(return_value=[recalled])

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await handler.handle_memory_recall(event, req)

    memory_engine.search_memories.assert_awaited_once()
    assert len(req.extra_user_content_parts) == 1
    text_part = req.extra_user_content_parts[0]
    assert "<RAG-Faiss-Memory>" in text_part.text
    assert getattr(text_part, "_no_save", False) is True
    # system_prompt 不应被修改
    assert req.system_prompt == ""


@pytest.mark.asyncio
async def test_handle_memory_recall_stores_private_user_message(
    handler, conversation_manager
):
    event = _make_event(group=False)
    req = _make_req("user input")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
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
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_reflection.get_persona_id",
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
async def test_enforce_message_limit_uses_cleanup_batch_size(
    memory_engine, memory_processor, conversation_manager
):
    handler = EventHandler(
        context=Mock(),
        config_manager=ConfigManager(
            {
                "recall_engine": {"top_k": 3, "injection_method": "extra_user_content"},
                "reflection_engine": {"summary_trigger_rounds": 1},
                "session_manager": {
                    "max_messages_per_session": 100,
                    "cleanup_batch_size": 20,
                },
            }
        ),
        memory_engine=memory_engine,
        memory_processor=memory_processor,
        conversation_manager=conversation_manager,
    )

    conversation_manager.store.get_message_count = AsyncMock(return_value=101)
    conversation_manager.get_session_metadata = AsyncMock(return_value=80)
    conversation_manager.trim_session_messages = AsyncMock(return_value=20)

    await handler._message_utils.enforce_message_limit("test:private:sid-1")

    conversation_manager.trim_session_messages.assert_awaited_once_with(
        "test:private:sid-1", 20
    )
    conversation_manager.get_session_metadata.assert_any_await(
        "test:private:sid-1", "last_summarized_index", 60
    )
    conversation_manager.update_session_metadata.assert_not_awaited()
    conversation_manager.invalidate_cache.assert_awaited_once_with("test:private:sid-1")


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


# ── EventHandler 边界条件与 source_window 测试 ────────────────────────────────


@pytest.mark.asyncio
async def test_handle_memory_recall_skips_when_prompt_empty(handler, memory_engine):
    """req.prompt 为空时，应跳过记忆召回，不调用 search_memories。"""
    event = _make_event(group=False)
    req = _make_req(prompt="")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await handler.handle_memory_recall(event, req)

    memory_engine.search_memories.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_memory_recall_injection_user_message_before(
    handler, memory_engine
):
    """injection_method=user_message_before 已废弃，应回退为 extra_user_content。"""
    from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
    from astrbot_plugin_livingmemory.core.event_handler import EventHandler

    h = EventHandler(
        context=Mock(),
        config_manager=ConfigManager(
            {
                "recall_engine": {
                    "top_k": 3,
                    "injection_method": "user_message_before",
                },
                "reflection_engine": {"summary_trigger_rounds": 1},
                "session_manager": {"max_messages_per_session": 100},
            }
        ),
        memory_engine=memory_engine,
        memory_processor=Mock(),
        conversation_manager=Mock(),
    )
    h.conversation_manager.add_message_from_event = AsyncMock()

    recalled = Mock(content="mem_before", final_score=0.8, metadata={"importance": 0.9})
    memory_engine.search_memories = AsyncMock(return_value=[recalled])

    event = _make_event(group=False)
    req = _make_req("user question")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "p1"
        await h.handle_memory_recall(event, req)

    # 废弃模式不应改写 prompt（改写会被 AstrBot 持久化进对话历史）
    assert req.prompt == "user question"

    # 记忆应以临时片段追加到用户消息末尾，且不落库
    assert len(req.extra_user_content_parts) == 1
    text_part = req.extra_user_content_parts[0]
    assert "mem_before" in text_part.text
    assert getattr(text_part, "_no_save", False) is True


@pytest.mark.asyncio
async def test_handle_memory_recall_injection_user_message_after(
    handler, memory_engine
):
    """injection_method=user_message_after 已废弃，应回退为 extra_user_content。"""
    from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
    from astrbot_plugin_livingmemory.core.event_handler import EventHandler

    h = EventHandler(
        context=Mock(),
        config_manager=ConfigManager(
            {
                "recall_engine": {
                    "top_k": 3,
                    "injection_method": "user_message_after",
                },
                "reflection_engine": {"summary_trigger_rounds": 1},
                "session_manager": {"max_messages_per_session": 100},
            }
        ),
        memory_engine=memory_engine,
        memory_processor=Mock(),
        conversation_manager=Mock(),
    )
    h.conversation_manager.add_message_from_event = AsyncMock()

    recalled = Mock(content="mem_after", final_score=0.8, metadata={"importance": 0.9})
    memory_engine.search_memories = AsyncMock(return_value=[recalled])

    event = _make_event(group=False)
    req = _make_req("user question")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "p1"
        await h.handle_memory_recall(event, req)

    # 废弃模式不应改写 prompt
    assert req.prompt == "user question"

    # 记忆应以临时片段追加到用户消息末尾，且不落库
    assert len(req.extra_user_content_parts) == 1
    text_part = req.extra_user_content_parts[0]
    assert "mem_after" in text_part.text
    assert getattr(text_part, "_no_save", False) is True


@pytest.mark.asyncio
async def test_storage_task_writes_source_window(
    handler, conversation_manager, memory_engine
):
    """_storage_task 应在 metadata 中写入 source_window 字段。"""
    from astrbot_plugin_livingmemory.core.models.conversation_models import Message

    messages = [
        Message(
            id=1,
            session_id="s1",
            role="user",
            content="hello",
            sender_id="u1",
            sender_name="User",
            group_id=None,
            platform="test",
            metadata={},
        ),
        Message(
            id=2,
            session_id="s1",
            role="assistant",
            content="hi",
            sender_id="bot",
            sender_name="Bot",
            group_id=None,
            platform="test",
            metadata={"is_bot_message": True},
        ),
    ]

    captured_metadata = {}
    captured_scope = None
    captured_source = None

    async def _capture_add_memory(
        content, session_id, persona_id, importance, metadata, atoms=None, **kwargs
    ):
        nonlocal captured_scope, captured_source
        captured_scope = session_id
        captured_source = kwargs.get("source_messages")
        captured_metadata.update(metadata)
        return 1

    memory_engine.add_memory = AsyncMock(side_effect=_capture_add_memory)
    handler._memory_reflection.memory_processor.process_conversation.return_value = (
        "summary",
        {"topics": ["t1"]},
        0.9,
    )

    await handler._memory_reflection._storage_task(
        session_id="s1",
        history_messages=messages,
        persona_id="p1",
        start_index=0,
        end_index=2,
        retry_count=0,
        memory_scope="livingmemory:user:test:u1",
    )

    assert captured_scope == "livingmemory:user:test:u1"
    assert [item["content"] for item in captured_source] == ["hello", "hi"]
    assert "source_window" in captured_metadata
    sw = captured_metadata["source_window"]
    assert sw["session_id"] == "s1"
    assert sw["start_index"] == 0
    assert sw["end_index"] == 2
    assert sw["message_count"] == 2


@pytest.mark.asyncio
async def test_storage_task_does_not_retain_source_below_threshold(
    handler, memory_engine
):
    from astrbot_plugin_livingmemory.core.models.conversation_models import Message

    messages = [
        Message(
            id=index,
            session_id="s1",
            role=role,
            content=content,
            sender_id=sender,
        )
        for index, role, content, sender in (
            (1, "user", "hello", "u1"),
            (2, "assistant", "hi", "bot"),
        )
    ]
    handler._memory_reflection.memory_processor.process_conversation.return_value = (
        "summary",
        {"topics": ["t1"]},
        0.79,
    )

    await handler._memory_reflection._storage_task(
        session_id="s1",
        history_messages=messages,
        persona_id="p1",
        start_index=0,
        end_index=2,
        retry_count=0,
    )

    assert memory_engine.add_memory.await_args.kwargs["source_messages"] is None


@pytest.mark.asyncio
async def test_storage_task_skips_when_already_summarized(
    handler, conversation_manager, memory_engine
):
    """当 last_summarized_index >= end_index 时，_storage_task 应直接跳过。"""
    from astrbot_plugin_livingmemory.core.models.conversation_models import Message

    # 模拟已经总结到 end_index=5
    conversation_manager.get_session_metadata = AsyncMock(return_value=5)

    messages = [
        Message(
            id=1,
            session_id="s1",
            role="user",
            content="msg",
            sender_id="u1",
            sender_name="U",
            group_id=None,
            platform="test",
            metadata={},
        )
    ]

    await handler._memory_reflection._storage_task(
        session_id="s1",
        history_messages=messages,
        persona_id=None,
        start_index=0,
        end_index=5,  # end_index == current_summarized → 过期任务
        retry_count=0,
    )

    # 过期任务不应调用 add_memory
    memory_engine.add_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_memory_recall_prefers_get_message_str_over_non_string_attr(
    handler, memory_engine
):
    event = _make_event(group=False)
    event.message_str = Mock()
    event.get_message_str = Mock(return_value="hello from getter")
    req = _make_req("query text")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await handler.handle_memory_recall(event, req)

    memory_engine.search_memories.assert_awaited_once()
    assert (
        memory_engine.search_memories.await_args.kwargs["query"] == "hello from getter"
    )


@pytest.mark.asyncio
async def test_handle_memory_recall_uses_extra_content_parts_when_prompt_empty(
    handler, memory_engine
):
    event = _make_event(group=False)
    event.message_str = Mock()
    event.get_message_str = Mock(return_value="describe image")
    req = _make_req(prompt="")
    req.extra_user_content_parts = [Mock(text="<image_caption>cat</image_caption>")]

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await handler.handle_memory_recall(event, req)

    memory_engine.search_memories.assert_awaited_once()
    assert memory_engine.search_memories.await_args.kwargs["query"] == "describe image"


@pytest.mark.asyncio
async def test_handle_memory_reflection_skips_error_response(
    handler, conversation_manager, memory_engine
):
    """包含错误指示词的响应应被跳过，不触发记忆存储。"""
    event = _make_event(group=False)
    resp = _make_resp("api error: rate limit exceeded")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_reflection.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "p1"
        await handler.handle_memory_reflection(event, resp)

    # 错误响应不应触发任何存储
    memory_engine.add_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_memory_reflection_skips_empty_response(
    handler, conversation_manager, memory_engine
):
    """空响应应被跳过，不触发记忆存储。"""
    event = _make_event(group=False)
    resp = _make_resp("")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_reflection.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "p1"
        await handler.handle_memory_reflection(event, resp)

    memory_engine.add_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_memory_reflection_pending_retry_exceeds_max(
    handler, conversation_manager, memory_engine
):
    """待处理失败总结重试次数 >= 3 时，应放弃并清除 pending_summary。"""
    event = _make_event(group=False)
    resp = _make_resp("assistant answer")

    # 模拟 pending_summary 已失败 3 次
    session_metadata = {
        "last_summarized_index": 0,
        "pending_summary": {
            "start_index": 0,
            "end_index": 2,
            "retry_count": 3,
        },
    }

    async def _get_meta(session_id, key, default=None):
        return session_metadata.get(key, default)

    async def _update_meta(session_id, key, value):
        session_metadata[key] = value

    conversation_manager.get_session_metadata = AsyncMock(side_effect=_get_meta)
    conversation_manager.update_session_metadata = AsyncMock(side_effect=_update_meta)
    conversation_manager.store.get_message_count = AsyncMock(return_value=4)

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_reflection.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "p1"
        await handler.handle_memory_reflection(event, resp)

    # pending_summary 应被清除
    assert session_metadata.get("pending_summary") is None
    # add_memory 不应被调用（放弃了该范围）
    memory_engine.add_memory.assert_not_awaited()


