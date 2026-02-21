"""
Tests for EventHandler core behaviors.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.event_handler import EventHandler

from astrbot.api.platform import MessageType


@pytest.fixture
def memory_engine():
    engine = Mock()
    engine.search_memories = AsyncMock(return_value=[])
    engine.add_memory = AsyncMock(return_value=1)
    return engine


@pytest.fixture
def memory_processor():
    processor = Mock()
    processor.process_conversation = AsyncMock(
        return_value=("summary", {"topics": ["t1"]}, 0.6)
    )
    return processor


@pytest.fixture
def conversation_manager():
    manager = Mock()
    manager.add_message_from_event = AsyncMock(return_value=Mock(id=1, metadata={}))
    manager.get_session_info = AsyncMock(return_value=Mock(message_count=12))
    session_metadata = {"last_summarized_index": 0, "pending_summary": None}

    async def _get_session_metadata(session_id, key, default=None):
        return session_metadata.get(key, default)

    async def _update_session_metadata(session_id, key, value):
        session_metadata[key] = value

    manager.get_session_metadata = AsyncMock(side_effect=_get_session_metadata)
    manager.get_messages_range = AsyncMock(
        return_value=[Mock(group_id=None), Mock(group_id=None)]
    )
    manager.update_session_metadata = AsyncMock(side_effect=_update_session_metadata)
    manager.invalidate_cache = AsyncMock()
    manager.store = Mock()
    manager.store.get_message_count = AsyncMock(return_value=12)
    manager.store.update_message_metadata = AsyncMock()
    manager.store.connection = Mock()
    manager.store.connection.execute = AsyncMock(return_value=Mock(rowcount=1))
    manager.store.connection.commit = AsyncMock()
    return manager


@pytest.fixture
def handler(memory_engine, memory_processor, conversation_manager):
    return EventHandler(
        context=Mock(),
        config_manager=ConfigManager(
            {
                "recall_engine": {"top_k": 3, "injection_method": "system_prompt"},
                "reflection_engine": {"summary_trigger_rounds": 1},
                "session_manager": {"max_messages_per_session": 100},
            }
        ),
        memory_engine=memory_engine,
        memory_processor=memory_processor,
        conversation_manager=conversation_manager,
    )


def _make_req(prompt: str = "hello"):
    req = Mock()
    req.prompt = prompt
    req.system_prompt = ""
    req.contexts = []
    req.extra_user_content_parts = []
    return req


def _make_resp(text: str = "assistant reply"):
    resp = Mock()
    resp.role = "assistant"
    resp.completion_text = text
    resp.tools_call_name = None
    resp.tools_call_extra_content = None
    return resp


def _make_event(group: bool = False):
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


@pytest.mark.asyncio
async def test_message_dedup_cache_works(handler):
    key = "id:123"
    assert await handler._is_duplicate_message(key) is False
    await handler._mark_message_processed(key)
    assert await handler._is_duplicate_message(key) is True


@pytest.mark.asyncio
async def test_handle_memory_recall_injects_system_prompt(handler, memory_engine):
    event = _make_event(group=False)
    req = _make_req("query text")
    recalled = Mock(content="mem1", final_score=0.7, metadata={"importance": 0.9})
    memory_engine.search_memories = AsyncMock(return_value=[recalled])

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await handler.handle_memory_recall(event, req)

    memory_engine.search_memories.assert_awaited_once()
    assert "<RAG-Faiss-Memory>" in req.system_prompt


@pytest.mark.asyncio
async def test_handle_memory_recall_stores_private_user_message(
    handler, conversation_manager
):
    event = _make_event(group=False)
    req = _make_req("user input")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
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
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
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
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await handler.handle_memory_recall(event, req)

    memory_engine.search_memories.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_memory_recall_injection_user_message_before(
    handler, memory_engine
):
    """injection_method=user_message_before 时，记忆应追加到 prompt 前面。"""
    # 重新构造 handler，使用 user_message_before 注入方式
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
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "p1"
        await h.handle_memory_recall(event, req)

    assert "mem_before" in req.prompt
    assert req.prompt.index("<RAG-Faiss-Memory>") < req.prompt.index("user question")


@pytest.mark.asyncio
async def test_handle_memory_recall_injection_user_message_after(
    handler, memory_engine
):
    """injection_method=user_message_after 时，记忆应追加到 prompt 后面。"""
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
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "p1"
        await h.handle_memory_recall(event, req)

    assert "mem_after" in req.prompt
    assert req.prompt.index("user question") < req.prompt.index("<RAG-Faiss-Memory>")


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

    async def _capture_add_memory(content, session_id, persona_id, importance, metadata):
        captured_metadata.update(metadata)
        return 1

    memory_engine.add_memory = AsyncMock(side_effect=_capture_add_memory)

    await handler._storage_task(
        session_id="s1",
        history_messages=messages,
        persona_id="p1",
        start_index=0,
        end_index=2,
        retry_count=0,
    )

    assert "source_window" in captured_metadata
    sw = captured_metadata["source_window"]
    assert sw["session_id"] == "s1"
    assert sw["start_index"] == 0
    assert sw["end_index"] == 2
    assert sw["message_count"] == 2


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
            id=1, session_id="s1", role="user", content="msg",
            sender_id="u1", sender_name="U", group_id=None, platform="test", metadata={}
        )
    ]

    await handler._storage_task(
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
async def test_handle_memory_reflection_skips_error_response(
    handler, conversation_manager, memory_engine
):
    """包含错误指示词的响应应被跳过，不触发记忆存储。"""
    event = _make_event(group=False)
    resp = _make_resp("api error: rate limit exceeded")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
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
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
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
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "p1"
        await handler.handle_memory_reflection(event, resp)

    # pending_summary 应被清除
    assert session_metadata.get("pending_summary") is None
    # add_memory 不应被调用（放弃了该范围）
    memory_engine.add_memory.assert_not_awaited()
