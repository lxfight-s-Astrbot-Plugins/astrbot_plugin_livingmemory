"""
Tests for CommandHandler.
"""

from unittest.mock import AsyncMock, Mock

import pytest
from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.command_handler import CommandHandler


@pytest.fixture
def config_manager():
    return ConfigManager()


@pytest.fixture
def memory_engine():
    engine = Mock()
    engine.db_path = "/tmp/livingmemory-test.db"
    engine.get_statistics = AsyncMock(
        return_value={
            "total_memories": 2,
            "sessions": {"s1": 1, "s2": 1},
            "newest_memory": 1_700_000_000.0,
        }
    )
    engine.search_memories = AsyncMock(return_value=[])
    engine.delete_memory = AsyncMock(return_value=True)
    return engine


@pytest.fixture
def conversation_manager():
    manager = Mock()
    manager.clear_session = AsyncMock()
    return manager


@pytest.fixture
def index_validator():
    validator = Mock()
    validator.check_consistency = AsyncMock(
        return_value=Mock(
            is_consistent=True,
            needs_rebuild=False,
            reason="ok",
            documents_count=2,
            bm25_count=2,
            vector_count=2,
        )
    )
    validator.rebuild_indexes = AsyncMock(
        return_value={"success": True, "processed": 2, "errors": 0, "total": 2}
    )
    return validator


@pytest.fixture
def handler(config_manager, memory_engine, conversation_manager, index_validator):
    context = Mock()
    return CommandHandler(
        context=context,
        config_manager=config_manager,
        memory_engine=memory_engine,
        conversation_manager=conversation_manager,
        index_validator=index_validator,
        webui_server=None,
        initialization_status_callback=lambda: "ready",
    )


@pytest.mark.asyncio
async def test_handle_status_returns_report(handler, mock_event):
    messages = [msg async for msg in handler.handle_status(mock_event)]
    assert len(messages) == 1
    assert "LivingMemory" in messages[0]
    assert "总记忆数" in messages[0]


@pytest.mark.asyncio
async def test_handle_search_validates_inputs_and_calls_engine(handler, mock_event):
    empty = [msg async for msg in handler.handle_search(mock_event, "", 3)]
    assert "不能为空" in empty[0]

    _ = [msg async for msg in handler.handle_search(mock_event, "hello", 200)]
    # k should be clamped to 100.
    handler.memory_engine.search_memories.assert_awaited_with(
        query="hello", k=100, session_id=mock_event.unified_msg_origin
    )


@pytest.mark.asyncio
async def test_handle_search_renders_results(handler, mock_event, memory_engine):
    result = Mock(doc_id=7, final_score=0.88, content="hello memory")
    memory_engine.search_memories = AsyncMock(return_value=[result])

    messages = [msg async for msg in handler.handle_search(mock_event, "hello", 5)]
    assert len(messages) == 1
    assert "找到 1 条相关记忆" in messages[0]
    assert "ID: 7" in messages[0]


@pytest.mark.asyncio
async def test_handle_forget_success_and_not_found(handler, mock_event, memory_engine):
    success = [msg async for msg in handler.handle_forget(mock_event, 10)]
    assert "已删除记忆 #10" in success[0]

    memory_engine.delete_memory = AsyncMock(return_value=False)
    failed = [msg async for msg in handler.handle_forget(mock_event, 11)]
    assert "删除失败" in failed[0]


@pytest.mark.asyncio
async def test_handle_rebuild_index_branches(handler, mock_event, index_validator):
    # no rebuild needed
    msgs = [msg async for msg in handler.handle_rebuild_index(mock_event)]
    assert any("索引状态正常" in msg for msg in msgs)

    # rebuild needed
    index_validator.check_consistency = AsyncMock(
        return_value=Mock(
            is_consistent=False,
            needs_rebuild=True,
            reason="inconsistent",
            documents_count=3,
            bm25_count=2,
            vector_count=1,
        )
    )
    msgs2 = [msg async for msg in handler.handle_rebuild_index(mock_event)]
    assert any("开始重建索引" in msg for msg in msgs2)
    assert index_validator.rebuild_indexes.await_count >= 1


@pytest.mark.asyncio
async def test_handle_reset_and_help(handler, mock_event, conversation_manager):
    reset = [msg async for msg in handler.handle_reset(mock_event)]
    assert "已重置" in reset[0]
    conversation_manager.clear_session.assert_awaited_once()

    help_msg = [msg async for msg in handler.handle_help(mock_event)]
    assert "/lmem status" in help_msg[0]


def test_get_webui_url_logic(config_manager):
    handler = CommandHandler(
        context=Mock(),
        config_manager=config_manager,
        memory_engine=None,
        conversation_manager=None,
        index_validator=None,
        webui_server=None,
    )
    assert handler._get_webui_url() is None

    config = ConfigManager(
        {
            "webui_settings": {
                "enabled": True,
                "host": "0.0.0.0",
                "port": 8090,
                "access_password": "x",
            }
        }
    )
    handler2 = CommandHandler(
        context=Mock(),
        config_manager=config,
        memory_engine=None,
        conversation_manager=None,
        index_validator=None,
        webui_server=Mock(),
    )
    assert handler2._get_webui_url() == "http://127.0.0.1:8090"
