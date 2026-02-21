"""
Tests for features #77, #76, #74, #59.

#77 - /lmem summarize: manual memory summarization command
#76 - Token limit protection: truncation of long queries/content in VectorRetriever
#74 - Relative time → absolute date: current date injected into prompts
#59 - Group chat sender nicknames preserved in memory
"""

import re
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.command_handler import CommandHandler
from astrbot_plugin_livingmemory.core.managers.conversation_manager import (
    ConversationManager,
)
from astrbot_plugin_livingmemory.core.models.conversation_models import Message
from astrbot_plugin_livingmemory.core.processors.memory_processor import MemoryProcessor
from astrbot_plugin_livingmemory.core.retrieval.vector_retriever import VectorRetriever
from astrbot_plugin_livingmemory.storage.conversation_store import ConversationStore

from astrbot.api.platform import MessageType


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────


class _DummyLLMProvider:
    def __init__(self, completion_text: str):
        self._completion_text = completion_text
        self.text_chat = AsyncMock(side_effect=self._chat)

    async def _chat(self, prompt: str, system_prompt: str):
        # Expose the prompts so tests can inspect them.
        self._last_prompt = prompt
        self._last_system_prompt = system_prompt
        return SimpleNamespace(completion_text=self._completion_text)


def _make_private_messages():
    return [
        Message(
            id=1,
            session_id="test:private:s1",
            role="user",
            content="明天下午三点开会",
            sender_id="u1",
            sender_name="张三",
            group_id=None,
            platform="test",
            metadata={},
        ),
        Message(
            id=2,
            session_id="test:private:s1",
            role="assistant",
            content="收到，我会提醒你",
            sender_id="bot",
            sender_name="Bot",
            group_id=None,
            platform="test",
            metadata={"is_bot_message": True},
        ),
    ]


def _make_group_messages():
    return [
        Message(
            id=1,
            session_id="aiocqhttp:GroupMessage:88888",
            role="user",
            content="大家觉得 AI 工具怎么样？",
            sender_id="10001",
            sender_name="张三",
            group_id="88888",
            platform="aiocqhttp",
            metadata={},
        ),
        Message(
            id=2,
            session_id="aiocqhttp:GroupMessage:88888",
            role="user",
            content="我觉得 ChatGPT 写代码效率提升了 30%",
            sender_id="10002",
            sender_name="李四",
            group_id="88888",
            platform="aiocqhttp",
            metadata={},
        ),
        Message(
            id=3,
            session_id="aiocqhttp:GroupMessage:88888",
            role="assistant",
            content="AI 工具确实能提升效率，但需要仔细审查生成的代码",
            sender_id="bot",
            sender_name="Bot",
            group_id="88888",
            platform="aiocqhttp",
            metadata={"is_bot_message": True},
        ),
    ]


_VALID_JSON_RESPONSE = """{
    "summary": "张三提醒我明天下午三点开会，我确认了会议安排",
    "topics": ["会议提醒"],
    "key_facts": ["张三安排明天下午三点开会"],
    "sentiment": "neutral",
    "importance": 0.8
}"""

_VALID_GROUP_JSON_RESPONSE = """{
    "summary": "群聊讨论了 AI 工具的使用效果，张三和李四都参与了讨论",
    "topics": ["AI工具", "工作效率"],
    "key_facts": ["张三认为 ChatGPT 效率提升 30%", "需要仔细审查 AI 生成代码"],
    "participants": ["张三", "李四"],
    "sentiment": "positive",
    "importance": 0.75
}"""


# ─────────────────────────────────────────────────────────────────────────────
# #77 – /lmem summarize command
# ─────────────────────────────────────────────────────────────────────────────


def _make_command_handler(memory_processor=None, conversation_manager=None, memory_engine=None):
    """Build a CommandHandler with sensible defaults."""
    if memory_engine is None:
        memory_engine = Mock()
        memory_engine.db_path = "/tmp/test.db"
        memory_engine.get_statistics = AsyncMock(
            return_value={"total_memories": 0, "sessions": {}, "newest_memory": None}
        )
        memory_engine.add_memory = AsyncMock(return_value=1)

    if conversation_manager is None:
        conversation_manager = Mock()
        conversation_manager.store = Mock()
        conversation_manager.store.get_message_count = AsyncMock(return_value=5)
        conversation_manager.get_session_metadata = AsyncMock(return_value=0)
        conversation_manager.get_messages_range = AsyncMock(return_value=_make_private_messages())
        conversation_manager.update_session_metadata = AsyncMock()
        conversation_manager.clear_session = AsyncMock()

    return CommandHandler(
        context=Mock(),
        config_manager=ConfigManager(),
        memory_engine=memory_engine,
        conversation_manager=conversation_manager,
        index_validator=None,
        memory_processor=memory_processor,
        webui_server=None,
    )


class _MockEvent:
    unified_msg_origin = "test:private:session-1"

    def plain_result(self, message):
        return message

    def get_message_type(self):
        return None

    def get_sender_id(self):
        return "user-1"

    def get_self_id(self):
        return "bot-1"

    def get_sender_name(self):
        return "Tester"

    def get_extra(self, key, default=None):
        return default


@pytest.mark.asyncio
async def test_summarize_no_memory_processor_returns_error():
    """handle_summarize should return an error when _memory_processor is None."""
    handler = _make_command_handler(memory_processor=None)
    msgs = [m async for m in handler.handle_summarize(_MockEvent())]
    assert any("未初始化" in m for m in msgs)


@pytest.mark.asyncio
async def test_summarize_no_unsummarized_messages():
    """handle_summarize should report nothing to summarize when already up-to-date."""
    conv_mgr = Mock()
    conv_mgr.store = Mock()
    conv_mgr.store.get_message_count = AsyncMock(return_value=3)
    conv_mgr.get_session_metadata = AsyncMock(return_value=3)  # already summarized all
    conv_mgr.update_session_metadata = AsyncMock()

    handler = _make_command_handler(
        memory_processor=Mock(),
        conversation_manager=conv_mgr,
    )
    msgs = [m async for m in handler.handle_summarize(_MockEvent())]
    assert any("没有需要总结" in m for m in msgs)


@pytest.mark.asyncio
async def test_summarize_calls_processor_and_stores_memory():
    """handle_summarize should call process_conversation and add_memory."""
    memory_processor = Mock()
    memory_processor.process_conversation = AsyncMock(
        return_value=(
            "张三提醒我明天下午三点开会",
            {"topics": ["会议"], "source_window": {}},
            0.8,
        )
    )

    memory_engine = Mock()
    memory_engine.db_path = "/tmp/test.db"
    memory_engine.add_memory = AsyncMock(return_value=1)

    conv_mgr = Mock()
    conv_mgr.store = Mock()
    conv_mgr.store.get_message_count = AsyncMock(return_value=5)
    conv_mgr.get_session_metadata = AsyncMock(return_value=0)
    conv_mgr.get_messages_range = AsyncMock(return_value=_make_private_messages())
    conv_mgr.update_session_metadata = AsyncMock()

    context = Mock()
    context.conversation_manager = Mock()

    handler = CommandHandler(
        context=context,
        config_manager=ConfigManager(),
        memory_engine=memory_engine,
        conversation_manager=conv_mgr,
        index_validator=None,
        memory_processor=memory_processor,
        webui_server=None,
    )

    with patch(
        "astrbot_plugin_livingmemory.core.utils.get_persona_id",
        new=AsyncMock(return_value="persona_1"),
    ):
        msgs = [m async for m in handler.handle_summarize(_MockEvent())]

    # Should have called process_conversation
    memory_processor.process_conversation.assert_awaited_once()
    # Should have stored the memory
    memory_engine.add_memory.assert_awaited_once()
    # Should report success
    assert any("总结完成" in m for m in msgs)


@pytest.mark.asyncio
async def test_summarize_updates_last_summarized_index():
    """handle_summarize should update last_summarized_index to actual_count."""
    memory_processor = Mock()
    memory_processor.process_conversation = AsyncMock(
        return_value=(
            "summary text",
            {"topics": ["test"]},
            0.5,
        )
    )

    memory_engine = Mock()
    memory_engine.db_path = "/tmp/test.db"
    memory_engine.add_memory = AsyncMock(return_value=1)

    conv_mgr = Mock()
    conv_mgr.store = Mock()
    conv_mgr.store.get_message_count = AsyncMock(return_value=10)
    conv_mgr.get_session_metadata = AsyncMock(return_value=4)
    conv_mgr.get_messages_range = AsyncMock(return_value=_make_private_messages())
    conv_mgr.update_session_metadata = AsyncMock()

    context = Mock()

    handler = CommandHandler(
        context=context,
        config_manager=ConfigManager(),
        memory_engine=memory_engine,
        conversation_manager=conv_mgr,
        index_validator=None,
        memory_processor=memory_processor,
        webui_server=None,
    )

    with patch(
        "astrbot_plugin_livingmemory.core.utils.get_persona_id",
        new=AsyncMock(return_value=None),
    ):
        _ = [m async for m in handler.handle_summarize(_MockEvent())]

    # last_summarized_index should be updated to actual_count (10)
    conv_mgr.update_session_metadata.assert_any_await(
        _MockEvent.unified_msg_origin, "last_summarized_index", 10
    )


@pytest.mark.asyncio
async def test_summarize_help_text_includes_summarize_command():
    """The help text should mention /lmem summarize."""
    handler = _make_command_handler()
    msgs = [m async for m in handler.handle_help(_MockEvent())]
    assert any("summarize" in m for m in msgs)


# ─────────────────────────────────────────────────────────────────────────────
# #76 – Token limit protection in VectorRetriever
# ─────────────────────────────────────────────────────────────────────────────


def _make_vector_retriever():
    """Build a VectorRetriever with a mocked FaissVecDB."""
    faiss_db = Mock()
    faiss_db.retrieve = AsyncMock(return_value=[])
    faiss_db.insert = AsyncMock(return_value=1)
    return VectorRetriever(faiss_db=faiss_db)


@pytest.mark.asyncio
async def test_search_truncates_long_query():
    """Queries longer than 2000 chars should be truncated before calling faiss_db.retrieve."""
    retriever = _make_vector_retriever()
    long_query = "x" * 5000

    await retriever.search(long_query, k=5)

    call_args = retriever.faiss_db.retrieve.call_args
    actual_query = call_args.kwargs.get("query") or call_args.args[0]
    assert len(actual_query) <= 2000


@pytest.mark.asyncio
async def test_search_does_not_truncate_short_query():
    """Queries within 2000 chars should be passed through unchanged."""
    retriever = _make_vector_retriever()
    short_query = "这是一个正常长度的查询"

    await retriever.search(short_query, k=5)

    call_args = retriever.faiss_db.retrieve.call_args
    actual_query = call_args.kwargs.get("query") or call_args.args[0]
    assert actual_query == short_query


@pytest.mark.asyncio
async def test_add_document_truncates_long_content():
    """Content longer than 4000 chars should be truncated before calling faiss_db.insert."""
    retriever = _make_vector_retriever()
    long_content = "y" * 8000
    metadata = {
        "importance": 0.5,
        "create_time": time.time(),
        "last_access_time": time.time(),
        "session_id": "s1",
        "persona_id": None,
    }

    await retriever.add_document(long_content, metadata)

    call_args = retriever.faiss_db.insert.call_args
    actual_content = call_args.kwargs.get("content") or call_args.args[0]
    assert len(actual_content) <= 4000


@pytest.mark.asyncio
async def test_add_document_does_not_truncate_short_content():
    """Content within 4000 chars should be stored as-is."""
    retriever = _make_vector_retriever()
    short_content = "这是一段正常长度的记忆内容"
    metadata = {
        "importance": 0.5,
        "create_time": time.time(),
        "last_access_time": time.time(),
        "session_id": "s1",
        "persona_id": None,
    }

    await retriever.add_document(short_content, metadata)

    call_args = retriever.faiss_db.insert.call_args
    actual_content = call_args.kwargs.get("content") or call_args.args[0]
    assert actual_content == short_content


@pytest.mark.asyncio
async def test_search_empty_query_returns_empty():
    """Empty or whitespace-only queries should return [] without calling faiss_db."""
    retriever = _make_vector_retriever()

    assert await retriever.search("") == []
    assert await retriever.search("   ") == []
    retriever.faiss_db.retrieve.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_exactly_at_limit_not_truncated():
    """A query of exactly 2000 chars should not be truncated."""
    retriever = _make_vector_retriever()
    exact_query = "a" * 2000

    await retriever.search(exact_query, k=3)

    call_args = retriever.faiss_db.retrieve.call_args
    actual_query = call_args.kwargs.get("query") or call_args.args[0]
    assert len(actual_query) == 2000


# ─────────────────────────────────────────────────────────────────────────────
# #74 – Current date injected into prompts for relative time conversion
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_system_prompt_contains_current_date():
    """_build_system_prompt_with_persona should include today's date."""
    from datetime import datetime

    llm = _DummyLLMProvider(_VALID_JSON_RESPONSE)
    processor = MemoryProcessor(llm_provider=llm, context=None)

    system_prompt = await processor._build_system_prompt_with_persona(None)

    today = datetime.now().strftime("%Y-%m-%d")
    assert today in system_prompt, f"Expected {today!r} in system_prompt"


@pytest.mark.asyncio
async def test_system_prompt_with_persona_contains_current_date():
    """_build_system_prompt_with_persona with a persona should also include today's date."""
    from datetime import datetime

    llm = _DummyLLMProvider(_VALID_JSON_RESPONSE)
    context = Mock()
    context.persona_manager = Mock()
    context.persona_manager.get_persona = AsyncMock(
        return_value=SimpleNamespace(system_prompt="你是一个专业助手")
    )
    processor = MemoryProcessor(llm_provider=llm, context=context)

    system_prompt = await processor._build_system_prompt_with_persona("persona_1")

    today = datetime.now().strftime("%Y-%m-%d")
    assert today in system_prompt
    # Should also contain persona content
    assert "专业助手" in system_prompt


@pytest.mark.asyncio
async def test_prompt_template_contains_current_date_placeholder():
    """The prompt sent to LLM should have {current_date} replaced with today's date."""
    from datetime import datetime

    llm = _DummyLLMProvider(_VALID_JSON_RESPONSE)
    processor = MemoryProcessor(llm_provider=llm, context=None)

    await processor.process_conversation(
        messages=_make_private_messages(),
        is_group_chat=False,
        save_original=False,
        persona_id=None,
    )

    today = datetime.now().strftime("%Y-%m-%d")
    # The prompt passed to LLM should contain today's date (not the raw placeholder)
    assert today in llm._last_prompt
    assert "{current_date}" not in llm._last_prompt


@pytest.mark.asyncio
async def test_group_prompt_template_contains_current_date():
    """Group chat prompt should also have {current_date} replaced."""
    from datetime import datetime

    llm = _DummyLLMProvider(_VALID_GROUP_JSON_RESPONSE)
    processor = MemoryProcessor(llm_provider=llm, context=None)

    await processor.process_conversation(
        messages=_make_group_messages(),
        is_group_chat=True,
        save_original=False,
        persona_id=None,
    )

    today = datetime.now().strftime("%Y-%m-%d")
    assert today in llm._last_prompt
    assert "{current_date}" not in llm._last_prompt


@pytest.mark.asyncio
async def test_system_prompt_instructs_relative_time_conversion():
    """The system prompt should explicitly instruct LLM to convert relative time."""
    llm = _DummyLLMProvider(_VALID_JSON_RESPONSE)
    processor = MemoryProcessor(llm_provider=llm, context=None)

    system_prompt = await processor._build_system_prompt_with_persona(None)

    # Should mention relative time conversion
    assert any(
        keyword in system_prompt
        for keyword in ["相对时间", "今天", "明天", "转换"]
    )


# ─────────────────────────────────────────────────────────────────────────────
# #59 – Group chat sender nicknames preserved
# ─────────────────────────────────────────────────────────────────────────────


def test_format_for_llm_group_user_message_uses_nickname():
    """Group user messages should use sender_name in the [昵称 | ID | time] prefix."""
    msg = Message(
        id=1,
        session_id="aiocqhttp:GroupMessage:88888",
        role="user",
        content="大家好",
        sender_id="10001",
        sender_name="张三",
        group_id="88888",
        platform="aiocqhttp",
        metadata={},
    )
    formatted = msg.format_for_llm(include_sender_name=True)
    assert "张三" in formatted["content"]
    assert "10001" in formatted["content"]
    # Should NOT use [Bot: prefix for user messages
    assert "[Bot:" not in formatted["content"]


def test_format_for_llm_group_bot_message_uses_bot_prefix():
    """Group bot messages should use [Bot: 昵称] prefix."""
    msg = Message(
        id=2,
        session_id="aiocqhttp:GroupMessage:88888",
        role="assistant",
        content="AI 工具确实能提升效率",
        sender_id="bot-id",
        sender_name="MyBot",
        group_id="88888",
        platform="aiocqhttp",
        metadata={"is_bot_message": True},
    )
    formatted = msg.format_for_llm(include_sender_name=True)
    assert "[Bot: MyBot" in formatted["content"]


def test_format_for_llm_bot_detected_by_role_when_no_metadata():
    """When is_bot_message is not in metadata, role=assistant should still trigger [Bot:] prefix."""
    msg = Message(
        id=3,
        session_id="aiocqhttp:GroupMessage:88888",
        role="assistant",
        content="我来回答这个问题",
        sender_id="bot-id",
        sender_name="AstrBot",
        group_id="88888",
        platform="aiocqhttp",
        metadata={},  # no is_bot_message flag
    )
    formatted = msg.format_for_llm(include_sender_name=True)
    assert "[Bot:" in formatted["content"]


def test_format_for_llm_private_chat_no_prefix_change():
    """Private chat messages (no group_id) should not add sender prefix."""
    msg = Message(
        id=4,
        session_id="test:private:s1",
        role="user",
        content="你好",
        sender_id="u1",
        sender_name="张三",
        group_id=None,
        platform="test",
        metadata={},
    )
    formatted = msg.format_for_llm(include_sender_name=True)
    # Private chat: no [昵称 | ID] prefix added by format_for_llm
    assert formatted["content"] == "你好"


def test_format_for_llm_fallback_to_sender_id_when_no_name():
    """When sender_name is None, sender_id should be used as display name."""
    msg = Message(
        id=5,
        session_id="aiocqhttp:GroupMessage:88888",
        role="user",
        content="测试消息",
        sender_id="99999",
        sender_name=None,
        group_id="88888",
        platform="aiocqhttp",
        metadata={},
    )
    formatted = msg.format_for_llm(include_sender_name=True)
    assert "99999" in formatted["content"]


@pytest.mark.asyncio
async def test_add_message_from_event_sets_is_bot_message_for_assistant(tmp_path: Path):
    """add_message_from_event with role=assistant should set is_bot_message=True in metadata."""
    db_path = tmp_path / "cm_bot.db"
    store = ConversationStore(str(db_path))
    await store.initialize()
    manager = ConversationManager(store=store, max_cache_size=2, context_window_size=10)

    class _GroupEvent:
        unified_msg_origin = "aiocqhttp:GroupMessage:88888"

        def get_sender_id(self):
            return "bot-id"

        def get_sender_name(self):
            return "MyBot"

        def get_self_id(self):
            return "bot-id"

        def get_message_type(self):
            return MessageType.GROUP_MESSAGE

        def get_platform_name(self):
            return "aiocqhttp"

    event = _GroupEvent()
    msg = await manager.add_message_from_event(event, role="assistant", content="我来回答")

    assert msg.metadata.get("is_bot_message") is True
    await store.close()


@pytest.mark.asyncio
async def test_add_message_from_event_user_message_no_bot_flag(tmp_path: Path):
    """add_message_from_event with role=user should NOT set is_bot_message."""
    db_path = tmp_path / "cm_user.db"
    store = ConversationStore(str(db_path))
    await store.initialize()
    manager = ConversationManager(store=store, max_cache_size=2, context_window_size=10)

    class _GroupEvent:
        unified_msg_origin = "aiocqhttp:GroupMessage:88888"

        def get_sender_id(self):
            return "10001"

        def get_sender_name(self):
            return "张三"

        def get_self_id(self):
            return "bot-id"

        def get_message_type(self):
            return MessageType.GROUP_MESSAGE

        def get_platform_name(self):
            return "aiocqhttp"

    event = _GroupEvent()
    msg = await manager.add_message_from_event(event, role="user", content="大家好")

    assert not msg.metadata.get("is_bot_message", False)
    await store.close()


@pytest.mark.asyncio
async def test_group_memory_format_contains_nicknames():
    """
    When formatting group messages for LLM, each message should include
    the sender's actual nickname, not a generic placeholder.
    """
    messages = _make_group_messages()
    processor = MemoryProcessor(llm_provider=_DummyLLMProvider(_VALID_GROUP_JSON_RESPONSE), context=None)

    conversation_text = processor._format_conversation(messages)

    # User messages should show their nicknames
    assert "张三" in conversation_text
    assert "李四" in conversation_text
    # Bot message should use [Bot: ...] prefix
    assert "[Bot:" in conversation_text


@pytest.mark.asyncio
async def test_group_memory_quality_low_for_group_member_generic_term():
    """summary containing '群成员' should be flagged as low quality."""
    llm = _DummyLLMProvider(
        """{
            "summary": "群成员讨论了一些话题",
            "topics": ["闲聊"],
            "key_facts": ["群成员说了话"],
            "participants": ["群成员"],
            "sentiment": "neutral",
            "importance": 0.4
        }"""
    )
    processor = MemoryProcessor(llm_provider=llm, context=None)

    _, metadata, _ = await processor.process_conversation(
        messages=_make_group_messages(),
        is_group_chat=True,
        save_original=False,
        persona_id=None,
    )

    assert metadata.get("summary_quality") == "low"
