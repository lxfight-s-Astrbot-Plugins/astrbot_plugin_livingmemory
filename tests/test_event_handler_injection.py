"""
Event handler tests: fake tool-call injection modes and top_k=0 regressions.
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

# ==================== fake_tool_call 兼容清理测试 ====================


@pytest.mark.asyncio
async def test_handle_memory_recall_injection_fake_tool_call(handler, memory_engine):
    """injection_method=fake_tool_call 已废弃，应回退为 extra_user_content 临时片段注入。"""
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
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "p1"
        await h.handle_memory_recall(event, req)

    # 废弃模式回退：不应向 contexts 写入任何消息，也不应改写 prompt
    assert len(req.contexts) == 0
    assert req.prompt == "今天吃什么"
    assert req.system_prompt == ""

    # 记忆应以 mark_as_temp 临时片段追加到 extra_user_content_parts
    assert len(req.extra_user_content_parts) == 1
    text_part = req.extra_user_content_parts[0]
    assert "用户喜欢吃火锅" in text_part.text
    assert "<RAG-Faiss-Memory>" in text_part.text
    assert getattr(text_part, "_no_save", False) is True


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
    manager.reconcile_session_tail = AsyncMock(return_value=0)
    manager.get_last_message = AsyncMock(return_value=None)
    manager.store = Mock()
    manager.store.connection = None
    return manager


# ==================== 废弃注入方式回退测试 ====================


@pytest.mark.asyncio
async def test_handle_memory_recall_fake_tool_call_fallback_on_gemini(
    memory_engine,
):
    """fake_tool_call 已废弃，任何 Provider 下都应回退为 extra_user_content 注入。"""
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
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "p1"
        await h.handle_memory_recall(event, req)

    assert len(req.contexts) == 0
    assert req.system_prompt == ""
    assert req.prompt == "今天吃什么"
    assert len(req.extra_user_content_parts) == 1
    text_part = req.extra_user_content_parts[0]
    assert "用户喜欢吃火锅" in text_part.text
    assert "<RAG-Faiss-Memory>" in text_part.text
    assert getattr(text_part, "_no_save", False) is True


@pytest.mark.asyncio
async def test_handle_memory_recall_deprecated_modes_do_not_fetch_provider(
    memory_engine,
):
    """废弃模式的回退是纯配置映射，不应查询会话级 provider。"""
    context = Mock()
    context.get_using_provider = Mock(side_effect=RuntimeError("should not be called"))

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
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "p1"
        await h.handle_memory_recall(event, req)

    context.get_using_provider.assert_not_called()
    assert len(req.extra_user_content_parts) == 1


@pytest.mark.asyncio
async def test_handle_memory_recall_fake_tool_call_fallback_logs_once(
    memory_engine,
):
    """废弃注入方式的回退应只记录一条 warning，避免重复日志。"""
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
            "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
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
    assert "extra_user_content" in warning_text


@pytest.mark.asyncio
async def test_handle_memory_recall_injection_fake_tool_call_deepseek_v4(
    memory_engine,
):
    """DeepSeek V4 专用模式已废弃，应自动回退到 extra_user_content。"""
    context = Mock()
    context.get_using_provider = Mock(return_value=None)

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
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "p1"
        await h.handle_memory_recall(event, req)

    # 回退是纯配置映射，不查询 provider，也不写入 contexts
    context.get_using_provider.assert_not_called()
    assert len(req.contexts) == 0
    assert req.system_prompt == ""
    assert req.prompt == "今天吃什么"
    assert len(req.extra_user_content_parts) == 1
    text_part = req.extra_user_content_parts[0]
    assert "用户喜欢吃火锅" in text_part.text
    assert "<RAG-Faiss-Memory>" in text_part.text
    assert getattr(text_part, "_no_save", False) is True


@pytest.mark.asyncio
async def test_handle_memory_recall_injection_fake_tool_call_deepseek_v4_on_gemini(
    memory_engine,
):
    """DeepSeek V4 旧配置在任何 Provider 下都应回退到 extra_user_content。"""
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
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "p1"
        await h.handle_memory_recall(event, req)

    context.get_using_provider.assert_not_called()
    assert len(req.contexts) == 0
    assert req.system_prompt == ""
    assert req.prompt == "今天吃什么"
    assert len(req.extra_user_content_parts) == 1
    text_part = req.extra_user_content_parts[0]
    assert "用户喜欢吃火锅" in text_part.text
    assert "<RAG-Faiss-Memory>" in text_part.text
    assert getattr(text_part, "_no_save", False) is True


@pytest.mark.asyncio
async def test_handle_memory_recall_deepseek_v4_alias_falls_back_when_provider_lookup_fails(
    memory_engine,
):
    """DeepSeek V4 旧配置回退不依赖 provider，获取失败也不影响注入。"""
    context = Mock()
    context.get_using_provider = Mock(side_effect=ValueError("no provider"))

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
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "p1"
        await h.handle_memory_recall(event, req)

    assert len(req.contexts) == 0
    assert req.prompt == "今天吃什么"
    assert len(req.extra_user_content_parts) == 1
    assert getattr(req.extra_user_content_parts[0], "_no_save", False) is True
    assert req.system_prompt == ""


@pytest.mark.asyncio
async def test_handle_memory_recall_non_fake_modes_do_not_fetch_provider(
    memory_engine,
):
    """注入不获取 provider，避免无关异常影响 recall。"""
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
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "p1"
        await h.handle_memory_recall(event, req)

    context.get_using_provider.assert_not_called()
    # 废弃方式回退为 extra_user_content，prompt 不被改写
    assert req.prompt == "今天吃什么"
    assert len(req.extra_user_content_parts) == 1
    assert getattr(req.extra_user_content_parts[0], "_no_save", False) is True


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
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
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
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await handler.handle_memory_recall(event, req)

    assert MEMORY_INJECTION_HEADER not in req.system_prompt
    assert MEMORY_INJECTION_FOOTER not in req.system_prompt
    memory_engine.search_memories.assert_not_awaited()


@pytest.mark.asyncio
async def test_top_k_0_cleans_livingmemory_temp_extra_parts(
    memory_engine, memory_processor, conversation_manager
):
    """top_k=0 时应按 _no_save 清理 LivingMemory 上轮临时 extra_user_content。"""
    from astrbot.core.agent.message import TextPart
    from astrbot_plugin_livingmemory.core.base.constants import (
        MEMORY_INJECTION_FOOTER,
        MEMORY_INJECTION_HEADER,
    )

    handler = _make_handler_with_top_k_0(
        memory_engine, memory_processor, conversation_manager
    )
    event = _make_event(group=False)
    req = _make_req("test query")
    req.extra_user_content_parts = [
        TextPart(
            text=f"{MEMORY_INJECTION_HEADER}\n旧记忆内容\n{MEMORY_INJECTION_FOOTER}"
        ).mark_as_temp(),
        TextPart(text="<image_caption>cat</image_caption>").mark_as_temp(),
    ]

    await handler.handle_memory_recall(event, req)

    assert len(req.extra_user_content_parts) == 1
    assert req.extra_user_content_parts[0].text == "<image_caption>cat</image_caption>"
    memory_engine.search_memories.assert_not_awaited()


@pytest.mark.asyncio
async def test_top_k_0_leaves_history_content_parts_untouched(
    memory_engine, memory_processor, conversation_manager
):
    """插件不应改写历史消息：纯文本 parts 保持原样，不做格式归一化。"""
    handler = _make_handler_with_top_k_0(
        memory_engine, memory_processor, conversation_manager
    )
    event = _make_event(group=False)
    req = _make_req("test query")
    multimodal_content = [
        {"type": "text", "text": "look"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
    ]
    text_only_parts = [
        {"type": "text", "text": "hello"},
        {"type": "text", "text": "\nworld"},
    ]
    req.contexts = [
        {"role": "user", "content": text_only_parts},
        {"role": "user", "content": multimodal_content},
    ]

    await handler.handle_memory_recall(event, req)

    # 历史消息对象保持原样（同一对象、同一格式），插件不做任何改写
    assert req.contexts[0]["content"] is text_only_parts
    assert req.contexts[1]["content"] is multimodal_content
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
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
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
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await handler.handle_memory_recall(event, req)

    conversation_manager.add_message_from_event.assert_not_awaited()
    memory_engine.search_memories.assert_not_awaited()


