"""Shared fixtures and helpers for the event handler test modules."""

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
    processor.classify_atoms_from_metadata = Mock(return_value=[])
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
    manager.reconcile_session_tail = AsyncMock(return_value=0)
    manager.get_last_message = AsyncMock(return_value=None)
    manager.store = Mock()
    manager.store.get_message_count = AsyncMock(return_value=12)
    manager.store.get_last_message = AsyncMock(return_value=None)
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


def _make_event(group: bool = False, checkpoint: str | None = None):
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
    if checkpoint is not None:
        event.get_extra = Mock(return_value=checkpoint)
    return event




async def _make_real_handler(tmp_path, memory_engine=None):
    """构建使用真实 SQLite 的 EventHandler（memory_engine 用 mock）。"""
    from astrbot_plugin_livingmemory.core.managers.conversation_manager import (
        ConversationManager,
    )
    from astrbot_plugin_livingmemory.storage.conversation_store import (
        ConversationStore,
    )

    store = ConversationStore(str(tmp_path / "conv-history.db"))
    await store.initialize()
    manager = ConversationManager(
        store=store, max_cache_size=10, context_window_size=20, session_ttl=3600
    )

    if memory_engine is None:
        engine = Mock()
        engine.search_memories = AsyncMock(return_value=[])
        engine.add_memory = AsyncMock(return_value=1)
        memory_engine = engine

    handler = EventHandler(
        context=Mock(),
        config_manager=ConfigManager(
            {
                "recall_engine": {
                    "top_k": 3,
                    "injection_method": "extra_user_content",
                },
                "reflection_engine": {"summary_trigger_rounds": 100},
                "session_manager": {"max_messages_per_session": 100},
                "filtering_settings": {"use_persona_filtering": False},
            }
        ),
        memory_engine=memory_engine,
        memory_processor=Mock(),
        conversation_manager=manager,
    )
    return handler, manager, store
async def _seed_turns(manager, sid: str, checkpoints: tuple[str, ...]) -> None:
    """写入若干完整轮次（user + assistant，均带 checkpoint）。"""
    for checkpoint in checkpoints:
        await manager.add_message(
            session_id=sid,
            role="user",
            content=f"q-{checkpoint}",
            sender_id="u1",
            metadata={"llm_checkpoint_id": checkpoint},
        )
        await manager.add_message(
            session_id=sid,
            role="assistant",
            content=f"a-{checkpoint}",
            sender_id="bot1",
            metadata={"llm_checkpoint_id": checkpoint},
        )
