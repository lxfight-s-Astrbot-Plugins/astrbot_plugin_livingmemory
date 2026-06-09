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
    processor.process_and_store_memories = AsyncMock()
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
                "recall_engine": {"top_k": 3, "injection_method": "extra_user_content"},
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
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
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
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await handler.handle_memory_recall(event, req)

    conversation_manager.add_message_from_event.assert_awaited()


@pytest.mark.asyncio
async def test_handle_memory_reflection_triggers_storage_task(
    handler, conversation_manager, memory_processor
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
    assert memory_processor.process_and_store_memories.await_count >= 1


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
    conversation_manager.store.trim_session_messages = AsyncMock(return_value=20)

    await handler._message_utils.enforce_message_limit("test:private:sid-1")

    conversation_manager.store.trim_session_messages.assert_awaited_once_with(
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
    handler, conversation_manager, memory_processor
):
    """_storage_task 应调用 process_and_store_memories 处理记忆。"""
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

    await handler._memory_reflection._storage_task(
        session_id="s1",
        history_messages=messages,
        persona_id="p1",
        start_index=0,
        end_index=2,
        retry_count=0,
    )

    # 验证 process_and_store_memories 被正确调用
    memory_processor.process_and_store_memories.assert_awaited_once()
    call_args = memory_processor.process_and_store_memories.call_args
    assert call_args.kwargs["session_id"] == "s1"
    assert call_args.kwargs["persona_id"] == "p1"
    assert len(call_args.kwargs["history_messages"]) == 2


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
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
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
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
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


# ==================== fake_tool_call 注入策略测试 ====================


@pytest.mark.asyncio
async def test_format_memories_for_fake_tool_call():
    """format_memories_for_fake_tool_call 应生成正确的伪造工具调用消息对。"""
    from astrbot_plugin_livingmemory.core.utils import (
        format_memories_for_fake_tool_call,
    )

    memories = [
        {
            "id": 101,
            "content": "用户喜欢Python编程",
            "score": 0.85,
            "metadata": {
                "importance": 0.9,
                "session_id": "s1",
                "persona_id": "p1",
                "create_time": 1700000000,
                "last_access_time": 1700001000,
            },
            "timestamp": 1700000000,
        },
        {
            "doc_id": 202,
            "content": "用户讨论过机器学习项目",
            "score": 0.72,
            "metadata": {"importance": 0.7},
            "timestamp": None,
        },
    ]

    result = format_memories_for_fake_tool_call(
        memories,
        query="Python",
        k=5,
        session_filtered=True,
        persona_filtered=False,
    )

    # 应返回 2 条消息
    assert len(result) == 2

    assistant_msg = result[0]
    tool_msg = result[1]

    # 验证 assistant 消息格式
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["content"] is None
    assert len(assistant_msg["tool_calls"]) == 1

    tc = assistant_msg["tool_calls"][0]
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "recall_long_term_memory"
    assert tc["id"].startswith("fake_recall_")

    # 验证 tool 消息格式
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == tc["id"]
    assert tool_msg["name"] == "recall_long_term_memory"
    assert "Python" in tool_msg["content"]
    assert '"session_filtered": true' in tool_msg["content"]
    assert '"persona_filtered": false' in tool_msg["content"]
    assert '"id": 101' in tool_msg["content"]
    assert '"id": 202' in tool_msg["content"]
    assert "用户喜欢Python编程" in tool_msg["content"]
    assert "用户讨论过机器学习项目" in tool_msg["content"]


@pytest.mark.asyncio
async def test_format_memories_for_fake_tool_call_empty():
    """空记忆列表应返回空列表。"""
    from astrbot_plugin_livingmemory.core.utils import (
        format_memories_for_fake_tool_call,
    )

    result = format_memories_for_fake_tool_call([], query="test", k=5)
    assert result == []


@pytest.mark.asyncio
async def test_handle_memory_recall_injection_fake_tool_call(handler, memory_engine):
    """injection_method=fake_tool_call 时，记忆应以伪造工具调用的形式注入到 contexts。"""
    from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
    from astrbot_plugin_livingmemory.core.event_handler import EventHandler

    h = EventHandler(
        context=Mock(),
        config_manager=ConfigManager(
            {
                "recall_engine": {
                    "top_k": 3,
                    "injection_method": "fake_tool_call",
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

    recalled = Mock(
        content="用户喜欢吃火锅",
        final_score=0.88,
        metadata={"importance": 0.9, "create_time": 1700000000},
    )
    recalled.doc_id = 99
    memory_engine.search_memories = AsyncMock(return_value=[recalled])

    event = _make_event(group=False)
    req = _make_req("今天吃什么")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "p1"
        await h.handle_memory_recall(event, req)

    # contexts 应该新增了 2 条消息（assistant + tool）
    assert len(req.contexts) == 2

    assistant_msg = req.contexts[0]
    tool_msg = req.contexts[1]

    # 验证 assistant 消息
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["content"] is None
    assert len(assistant_msg["tool_calls"]) == 1
    assert (
        assistant_msg["tool_calls"][0]["function"]["name"] == "recall_long_term_memory"
    )

    # 验证 tool 消息
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == assistant_msg["tool_calls"][0]["id"]
    assert tool_msg["name"] == "recall_long_term_memory"
    assert '"session_filtered": true' in tool_msg["content"]
    assert '"persona_filtered": true' in tool_msg["content"]
    assert '"id": 99' in tool_msg["content"]
    assert "用户喜欢吃火锅" in tool_msg["content"]

    # prompt 和 system_prompt 不应被修改
    assert req.prompt == "今天吃什么"
    assert req.system_prompt == ""


@pytest.mark.asyncio
async def test_remove_fake_tool_call_from_context(handler):
    """_remove_fake_tool_call_from_context 应正确移除伪造消息对，保留正常消息。"""
    req = _make_req("hello")
    req.contexts = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好啊"},
        # 伪造的工具调用消息对
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "fake_recall_abc123def456",
                    "type": "function",
                    "function": {
                        "name": "recall_long_term_memory",
                        "arguments": '{"query": "test", "k": 5}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "fake_recall_abc123def456",
            "content": '{"query": "test", "count": 1, "results": []}',
        },
        {"role": "user", "content": "最近怎么样"},
    ]

    removed = handler._memory_recall._remove_fake_tool_call_from_context(req, "test-session")

    # 应删除 2 条伪造消息
    assert removed == 2

    # 应保留 3 条正常消息
    assert len(req.contexts) == 3
    assert req.contexts[0]["content"] == "你好"
    assert req.contexts[1]["content"] == "你好啊"
    assert req.contexts[2]["content"] == "最近怎么样"


@pytest.mark.asyncio
async def test_remove_fake_tool_call_preserves_real_tool_calls(handler):
    """_remove_fake_tool_call_from_context 不应误删真实的工具调用消息。"""
    req = _make_req("hello")
    req.contexts = [
        # 真实的工具调用消息对（ID 不以 fake_recall_ 开头）
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_real_abc123",
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "arguments": '{"city": "上海"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_real_abc123",
            "content": '{"temperature": 25}',
        },
    ]

    removed = handler._memory_recall._remove_fake_tool_call_from_context(req, "test-session")

    # 不应删除任何消息
    assert removed == 0
    assert len(req.contexts) == 2


def _make_recall_conversation_manager():
    manager = Mock()
    manager.add_message_from_event = AsyncMock()
    manager.store = Mock()
    manager.store.connection = None
    return manager


# ==================== Provider 注入兼容测试 ====================


@pytest.mark.asyncio
async def test_handle_memory_recall_fake_tool_call_fallback_on_gemini(
    memory_engine,
):
    """Gemini 下配置 fake_tool_call 应自动降级为 user_message_before 注入。"""
    gemini_provider = Mock()
    gemini_provider.provider_config = {"type": "googlegenai_chat_completion"}
    gemini_provider.get_model = Mock(return_value="gemini-2.5-pro")

    context = Mock()
    context.get_using_provider = Mock(return_value=gemini_provider)

    h = EventHandler(
        context=context,
        config_manager=ConfigManager(
            {
                "recall_engine": {
                    "top_k": 3,
                    "injection_method": "fake_tool_call",
                },
                "reflection_engine": {"summary_trigger_rounds": 1},
                "session_manager": {"max_messages_per_session": 100},
            }
        ),
        memory_engine=memory_engine,
        memory_processor=Mock(),
        conversation_manager=_make_recall_conversation_manager(),
    )

    recalled = Mock(
        content="用户喜欢吃火锅",
        final_score=0.88,
        metadata={"importance": 0.9, "create_time": 1700000000},
    )
    recalled.doc_id = 99
    memory_engine.search_memories = AsyncMock(return_value=[recalled])

    event = _make_event(group=False)
    req = _make_req("今天吃什么")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "p1"
        await h.handle_memory_recall(event, req)

    assert len(req.contexts) == 0
    assert req.system_prompt == ""
    assert "用户喜欢吃火锅" in req.prompt
    assert req.prompt.index("<RAG-Faiss-Memory>") < req.prompt.index("今天吃什么")


@pytest.mark.asyncio
async def test_handle_memory_recall_fake_tool_call_fetches_provider_for_fallback(
    memory_engine,
):
    """fake_tool_call 模式应查询会话级 provider，以便执行兼容降级。"""
    gemini_provider = Mock()
    gemini_provider.provider_config = {"type": "googlegenai_chat_completion"}
    gemini_provider.get_model = Mock(return_value="gemini-2.5-pro")

    context = Mock()
    context.get_using_provider = Mock(return_value=gemini_provider)

    h = EventHandler(
        context=context,
        config_manager=ConfigManager(
            {
                "recall_engine": {
                    "top_k": 3,
                    "injection_method": "fake_tool_call",
                },
                "reflection_engine": {"summary_trigger_rounds": 1},
                "session_manager": {"max_messages_per_session": 100},
            }
        ),
        memory_engine=memory_engine,
        memory_processor=Mock(),
        conversation_manager=_make_recall_conversation_manager(),
    )

    recalled = Mock(
        content="用户喜欢吃火锅",
        final_score=0.88,
        metadata={"importance": 0.9, "create_time": 1700000000},
    )
    recalled.doc_id = 99
    memory_engine.search_memories = AsyncMock(return_value=[recalled])

    event = _make_event(group=False)
    req = _make_req("今天吃什么")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "p1"
        await h.handle_memory_recall(event, req)

    context.get_using_provider.assert_called_once_with(event.unified_msg_origin)


@pytest.mark.asyncio
async def test_handle_memory_recall_fake_tool_call_fallback_logs_once(
    memory_engine,
):
    """Gemini fallback 应只记录一条 warning，避免重复日志。"""
    gemini_provider = Mock()
    gemini_provider.provider_config = {"type": "googlegenai_chat_completion"}
    gemini_provider.get_model = Mock(return_value="gemini-2.5-pro")

    context = Mock()
    context.get_using_provider = Mock(return_value=gemini_provider)

    h = EventHandler(
        context=context,
        config_manager=ConfigManager(
            {
                "recall_engine": {
                    "top_k": 3,
                    "injection_method": "fake_tool_call",
                },
                "reflection_engine": {"summary_trigger_rounds": 1},
                "session_manager": {"max_messages_per_session": 100},
            }
        ),
        memory_engine=memory_engine,
        memory_processor=Mock(),
        conversation_manager=_make_recall_conversation_manager(),
    )

    recalled = Mock(
        content="用户喜欢吃火锅",
        final_score=0.88,
        metadata={"importance": 0.9, "create_time": 1700000000},
    )
    recalled.doc_id = 99
    memory_engine.search_memories = AsyncMock(return_value=[recalled])

    event = _make_event(group=False)
    req = _make_req("今天吃什么")

    with (
        patch(
            "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
            new_callable=AsyncMock,
        ) as get_persona,
        patch(
            "astrbot_plugin_livingmemory.core.event_handler.logger.warning"
        ) as mock_warning,
    ):
        get_persona.return_value = "p1"
        await h.handle_memory_recall(event, req)

    mock_warning.assert_called_once()
    warning_text = mock_warning.call_args[0][0]
    assert "fake_tool_call" in warning_text
    assert "user_message_before" in warning_text


@pytest.mark.asyncio
async def test_format_memories_for_fake_tool_call_deepseek_v4():
    """DeepSeek V4 转录格式应包含头尾标记、工具名和结果内容。"""
    from astrbot_plugin_livingmemory.core.utils import (
        format_memories_for_fake_tool_call_deepseek_v4,
    )

    result = format_memories_for_fake_tool_call_deepseek_v4(
        [
            {
                "id": 101,
                "content": "用户喜欢重庆火锅",
                "score": 0.9,
                "metadata": {"importance": 0.8, "create_time": 1700000000},
                "timestamp": 1700000000,
            }
        ],
        query="今天吃什么",
        k=3,
        session_filtered=False,
        persona_filtered=True,
    )

    assert result
    assert "<RAG-Faiss-Memory>" in result
    assert "[DeepSeekV4-FakeToolCall-Replay]" in result
    assert "assistant -> recall_long_term_memory(" in result
    assert '"query": "今天吃什么"' in result
    assert '"k": 3' in result
    assert '"session_filtered": false' in result
    assert '"persona_filtered": true' in result
    assert "用户喜欢重庆火锅" in result
    assert "</RAG-Faiss-Memory>" in result


@pytest.mark.asyncio
async def test_format_memories_for_fake_tool_call_deepseek_v4_empty():
    """空记忆时 DeepSeek V4 转录应返回空字符串。"""
    from astrbot_plugin_livingmemory.core.utils import (
        format_memories_for_fake_tool_call_deepseek_v4,
    )

    result = format_memories_for_fake_tool_call_deepseek_v4([], query="test", k=5)
    assert result == ""


@pytest.mark.asyncio
async def test_handle_memory_recall_injection_fake_tool_call_deepseek_v4(
    memory_engine,
):
    """DeepSeek V4 模式应把转录内容注入到 prompt 前部。"""
    context = Mock()

    h = EventHandler(
        context=context,
        config_manager=ConfigManager(
            {
                "recall_engine": {
                    "top_k": 3,
                    "injection_method": "fake_tool_call_deepseek_v4",
                },
                "filtering_settings": {
                    "use_session_filtering": False,
                    "use_persona_filtering": True,
                },
                "reflection_engine": {"summary_trigger_rounds": 1},
                "session_manager": {"max_messages_per_session": 100},
            }
        ),
        memory_engine=memory_engine,
        memory_processor=Mock(),
        conversation_manager=_make_recall_conversation_manager(),
    )

    recalled = Mock(
        content="用户喜欢吃火锅",
        final_score=0.88,
        metadata={"importance": 0.9, "create_time": 1700000000},
    )
    recalled.doc_id = 99
    memory_engine.search_memories = AsyncMock(return_value=[recalled])

    event = _make_event(group=False)
    event.get_message_str = Mock(return_value="今天吃什么")
    req = _make_req("今天吃什么")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "p1"
        await h.handle_memory_recall(event, req)

    context.get_using_provider.assert_not_called()
    assert len(req.contexts) == 0
    assert req.system_prompt == ""
    assert req.prompt.endswith("今天吃什么")
    assert req.prompt.index("[DeepSeekV4-FakeToolCall-Replay]") < req.prompt.index(
        "今天吃什么"
    )
    assert '"session_filtered": false' in req.prompt
    assert '"persona_filtered": true' in req.prompt
    assert "用户喜欢吃火锅" in req.prompt


@pytest.mark.asyncio
async def test_handle_memory_recall_non_fake_modes_do_not_fetch_provider(
    memory_engine,
):
    """普通注入模式不应获取 provider，避免无关异常影响 recall。"""
    context = Mock()
    context.get_using_provider = Mock(side_effect=RuntimeError("should not be called"))

    h = EventHandler(
        context=context,
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
        conversation_manager=_make_recall_conversation_manager(),
    )

    recalled = Mock(
        content="用户喜欢吃火锅",
        final_score=0.88,
        metadata={"importance": 0.9, "create_time": 1700000000},
    )
    recalled.doc_id = 99
    memory_engine.search_memories = AsyncMock(return_value=[recalled])

    event = _make_event(group=False)
    req = _make_req("今天吃什么")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "p1"
        await h.handle_memory_recall(event, req)

    context.get_using_provider.assert_not_called()
    assert "用户喜欢吃火锅" in req.prompt


# ==================== top_k=0 回归测试 ====================


def _make_handler_with_top_k_0(memory_engine, memory_processor, conversation_manager):
    """创建一个 top_k=0 的 EventHandler 用于回归测试。"""
    return EventHandler(
        context=Mock(),
        config_manager=ConfigManager(
            {
                "recall_engine": {"top_k": 0, "injection_method": "system_prompt"},
                "reflection_engine": {"summary_trigger_rounds": 1},
                "session_manager": {"max_messages_per_session": 100},
            }
        ),
        memory_engine=memory_engine,
        memory_processor=memory_processor,
        conversation_manager=conversation_manager,
    )


@pytest.mark.asyncio
async def test_top_k_0_skips_search_memories(
    memory_engine, memory_processor, conversation_manager
):
    """top_k=0 时不应调用 memory_engine.search_memories()。"""
    handler = _make_handler_with_top_k_0(
        memory_engine, memory_processor, conversation_manager
    )
    event = _make_event(group=False)
    req = _make_req("hello world")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await handler.handle_memory_recall(event, req)

    memory_engine.search_memories.assert_not_awaited()
    assert req.prompt == "hello world"


@pytest.mark.asyncio
async def test_top_k_0_still_cleans_injected_memories(
    memory_engine, memory_processor, conversation_manager
):
    """top_k=0 时仍应清理历史注入的旧记忆片段。"""
    from astrbot_plugin_livingmemory.core.base.constants import (
        MEMORY_INJECTION_FOOTER,
        MEMORY_INJECTION_HEADER,
    )

    handler = _make_handler_with_top_k_0(
        memory_engine, memory_processor, conversation_manager
    )
    event = _make_event(group=False)
    req = _make_req("test query")
    req.system_prompt = f"你是助手。\n{MEMORY_INJECTION_HEADER}\n旧记忆内容\n{MEMORY_INJECTION_FOOTER}\n请回答。"

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await handler.handle_memory_recall(event, req)

    assert MEMORY_INJECTION_HEADER not in req.system_prompt
    assert MEMORY_INJECTION_FOOTER not in req.system_prompt
    memory_engine.search_memories.assert_not_awaited()


@pytest.mark.asyncio
async def test_top_k_0_still_stores_private_message(
    memory_engine, memory_processor, conversation_manager
):
    """top_k=0 时私聊场景仍应写入用户消息和消息数量控制。"""
    handler = _make_handler_with_top_k_0(
        memory_engine, memory_processor, conversation_manager
    )
    event = _make_event(group=False)
    req = _make_req("private message")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await handler.handle_memory_recall(event, req)

    conversation_manager.add_message_from_event.assert_awaited_once()
    conversation_manager.add_message_from_event.assert_awaited_with(
        event=event, role="user", content="private message"
    )


@pytest.mark.asyncio
async def test_top_k_0_does_not_store_group_message(
    memory_engine, memory_processor, conversation_manager
):
    """top_k=0 时群聊场景不应写入用户消息（群聊由 handle_all_group_messages 负责）。"""
    handler = _make_handler_with_top_k_0(
        memory_engine, memory_processor, conversation_manager
    )
    event = _make_event(group=True)
    req = _make_req("group message")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await handler.handle_memory_recall(event, req)

    conversation_manager.add_message_from_event.assert_not_awaited()
    memory_engine.search_memories.assert_not_awaited()


# ==================== system_prompt 自动回退测试 ====================


@pytest.mark.asyncio
async def test_system_prompt_auto_falls_back_to_extra_user_content(
    memory_engine, memory_processor, conversation_manager
):
    """配置 system_prompt 时应自动回退到 extra_user_content 方式。"""
    from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
    from astrbot_plugin_livingmemory.core.event_handler import EventHandler

    h = EventHandler(
        context=Mock(),
        config_manager=ConfigManager(
            {
                "recall_engine": {
                    "top_k": 3,
                    "injection_method": "system_prompt",
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
    # 设置 store mock 以支持 _enforce_message_limit 中的 await 调用
    h.conversation_manager.store = Mock()
    h.conversation_manager.store.get_message_count = AsyncMock(return_value=12)
    h.conversation_manager.store.connection = Mock()
    h.conversation_manager.get_session_metadata = AsyncMock(return_value=0)
    h.conversation_manager.update_session_metadata = AsyncMock()
    h.conversation_manager.invalidate_cache = AsyncMock()

    recalled = Mock(
        content="mem_fallback", final_score=0.8, metadata={"importance": 0.9}
    )
    memory_engine.search_memories = AsyncMock(return_value=[recalled])

    event = _make_event(group=False)
    req = _make_req("query text")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await h.handle_memory_recall(event, req)

    # system_prompt 已废弃，应自动回退到 extra_user_content（注入到 extra_user_content_parts）
    assert req.system_prompt == ""
    assert len(req.extra_user_content_parts) == 1
    assert "mem_fallback" in req.extra_user_content_parts[0].text
    assert getattr(req.extra_user_content_parts[0], "_no_save", False) is True


# ==================== 上下文扩展测试 ====================


@pytest.mark.asyncio
async def test_context_expansion_enriches_query(
    memory_engine, memory_processor, conversation_manager
):
    """启用 inject_with_recent_context 时，查询应拼接历史消息上下文。"""
    from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
    from astrbot_plugin_livingmemory.core.event_handler import EventHandler

    cm_mock = Mock()
    cm_mock.add_message_from_event = AsyncMock()
    # 设置 store mock 以支持 _enforce_message_limit 中的 await 调用
    cm_mock.store = Mock()
    cm_mock.store.get_message_count = AsyncMock(return_value=12)
    cm_mock.store.connection = Mock()
    cm_mock.get_session_metadata = AsyncMock(return_value=0)
    cm_mock.update_session_metadata = AsyncMock()
    cm_mock.invalidate_cache = AsyncMock()
    # 模拟返回 3 条消息（最新在前）: [当前消息, bot 回复, 用户上条]
    cm_mock.get_context = AsyncMock(
        return_value=[
            {"content": "当前用户消息"},
            {"content": "Bot 的上一条回复"},
            {"content": "用户之前说的事情"},
        ]
    )

    h = EventHandler(
        context=Mock(),
        config_manager=ConfigManager(
            {
                "recall_engine": {
                    "top_k": 3,
                    "injection_method": "extra_user_content",
                    "inject_with_recent_context": True,
                },
                "reflection_engine": {"summary_trigger_rounds": 1},
                "session_manager": {"max_messages_per_session": 100},
            }
        ),
        memory_engine=memory_engine,
        memory_processor=Mock(),
        conversation_manager=cm_mock,
    )

    recalled = Mock(
        content="mem_context", final_score=0.8, metadata={"importance": 0.9}
    )
    memory_engine.search_memories = AsyncMock(return_value=[recalled])

    event = _make_event(group=False)
    event.get_message_str = Mock(return_value="当前用户消息")
    req = _make_req("当前用户消息")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await h.handle_memory_recall(event, req)

    # 验证 search_memories 收到的 query 包含扩展内容
    call_kwargs = memory_engine.search_memories.await_args.kwargs
    assert "用户之前说的事情" in call_kwargs["query"]
    assert "Bot 的上一条回复" in call_kwargs["query"]


@pytest.mark.asyncio
async def test_context_expansion_skips_when_empty(
    memory_engine, memory_processor, conversation_manager
):
    """get_context 返回空或单条时，直接使用原始查询。"""
    from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
    from astrbot_plugin_livingmemory.core.event_handler import EventHandler

    h = EventHandler(
        context=Mock(),
        config_manager=ConfigManager(
            {
                "recall_engine": {
                    "top_k": 3,
                    "injection_method": "extra_user_content",
                    "inject_with_recent_context": True,
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

    # 只返回一条消息（只有当前消息，无历史）
    h.conversation_manager.get_context = AsyncMock(
        return_value=[
            {"content": "唯一一条消息"},
        ]
    )

    recalled = Mock(content="mem_skip", final_score=0.8, metadata={"importance": 0.9})
    memory_engine.search_memories = AsyncMock(return_value=[recalled])

    event = _make_event(group=False)
    event.get_message_str = Mock(return_value="唯一一条消息")
    req = _make_req("唯一一条消息")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await h.handle_memory_recall(event, req)

    call_kwargs = memory_engine.search_memories.await_args.kwargs
    # 不应包含 " | " 分隔符（因为没有足够的历史消息来拼接）
    assert " | " not in call_kwargs["query"]


# ==================== 总结重试测试 ====================


@pytest.mark.asyncio
async def test_pending_summary_retry_max_abandons(
    handler, conversation_manager, memory_engine
):
    """retry_count >= 3 时应放弃该范围，更新 last_summarized_index 并跳过总结。"""
    conversation_manager.get_session_metadata = AsyncMock(
        side_effect=lambda sid, key, default=None: {
            "last_summarized_index": 0,
            "pending_summary": {"start_index": 2, "end_index": 10, "retry_count": 3},
        }.get(key, default)
    )
    conversation_manager.store.get_message_count = AsyncMock(return_value=12)

    event = _make_event(group=False)
    resp = _make_resp("assistant reply")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await handler.handle_memory_reflection(event, resp)

    # 验证 pending_summary 被清除
    clear_calls = [
        c
        for c in conversation_manager.update_session_metadata.await_args_list
        if c.args[1] == "pending_summary"
    ]
    assert len(clear_calls) >= 1
    # 验证 last_summarized_index 被更新到 end_index
    skip_calls = [
        c
        for c in conversation_manager.update_session_metadata.await_args_list
        if c.args[1] == "last_summarized_index"
    ]
    assert any(c.args[2] == 12 for c in skip_calls)
    # 不应触发记忆总结
    memory_engine.add_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_summary_retry_merges_range(
    handler, conversation_manager, memory_engine
):
    """retry_count < 3 时应合并范围（start_index 使用 pending_start）。"""
    conversation_manager.get_session_metadata = AsyncMock(
        side_effect=lambda sid, key, default=None: {
            "last_summarized_index": 5,
            "pending_summary": {"start_index": 2, "retry_count": 1},
        }.get(key, default)
    )
    conversation_manager.store.get_message_count = AsyncMock(return_value=12)
    # 返回足够消息以满足 end_index - start_index >= 2
    msgs = [Mock(group_id=None) for _ in range(8)]
    conversation_manager.get_messages_range = AsyncMock(return_value=msgs)

    event = _make_event(group=False)
    resp = _make_resp("assistant reply")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await handler.handle_memory_reflection(event, resp)
        await handler.shutdown()

    # 应使用合并后的范围
    conversation_manager.get_messages_range.assert_awaited_once()
    call_kwargs = conversation_manager.get_messages_range.await_args.kwargs
    assert call_kwargs["start_index"] == 2  # pending_start
    assert call_kwargs["end_index"] == 12  # total_messages
