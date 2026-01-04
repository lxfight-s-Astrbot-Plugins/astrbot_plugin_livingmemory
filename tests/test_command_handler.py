"""
测试CommandHandler
"""

from unittest.mock import AsyncMock, Mock

import pytest
from core.command_handler import CommandHandler
from core.config_manager import ConfigManager


@pytest.fixture
def config_manager():
    """创建配置管理器"""
    return ConfigManager()


@pytest.fixture
def mock_memory_engine():
    """创建mock的记忆引擎"""
    engine = Mock()
    engine.get_statistics = AsyncMock(
        return_value={
            "total_memories": 100,
            "sessions": {"session1": 10},
            "newest_memory": 1234567890.0,
        }
    )
    engine.search_memories = AsyncMock(return_value=[])
    engine.delete_memory = AsyncMock(return_value=True)
    engine.db_path = "/tmp/test.db"
    return engine


@pytest.fixture
def mock_conversation_manager():
    """创建mock的会话管理器"""
    manager = Mock()
    manager.clear_session = AsyncMock()
    return manager


@pytest.fixture
def mock_index_validator():
    """创建mock的索引验证器"""
    validator = Mock()
    validator.check_consistency = AsyncMock(
        return_value=Mock(
            is_consistent=True,
            needs_rebuild=False,
            reason="索引正常",
            documents_count=100,
            bm25_count=100,
            vector_count=100,
        )
    )
    validator.rebuild_indexes = AsyncMock(
        return_value={
            "success": True,
            "processed": 100,
            "errors": 0,
            "total": 100,
        }
    )
    return validator


@pytest.fixture
def command_handler(
    config_manager, mock_memory_engine, mock_conversation_manager, mock_index_validator
):
    """创建命令处理器"""
    return CommandHandler(
        config_manager=config_manager,
        memory_engine=mock_memory_engine,
        conversation_manager=mock_conversation_manager,
        index_validator=mock_index_validator,
        webui_server=None,
        initialization_status_callback=lambda: "✅ 插件已就绪",
    )


def test_command_handler_creation(command_handler):
    """测试CommandHandler创建"""
    assert command_handler is not None
    assert command_handler.config_manager is not None
    assert command_handler.memory_engine is not None
    assert command_handler.conversation_manager is not None
    assert command_handler.index_validator is not None


@pytest.mark.asyncio
async def test_handle_status(command_handler):
    """测试status命令"""
    mock_event = Mock()

    messages = []
    async for message in command_handler.handle_status(mock_event):
        messages.append(message)

    # 验证返回了状态消息
    assert len(messages) > 0
    assert "LivingMemory" in messages[0] or "状态" in messages[0]


@pytest.mark.asyncio
async def test_handle_status_no_engine(config_manager):
    """测试没有引擎时的status命令"""
    handler = CommandHandler(
        config_manager=config_manager,
        memory_engine=None,
        conversation_manager=None,
        index_validator=None,
    )

    mock_event = Mock()

    messages = []
    async for message in handler.handle_status(mock_event):
        messages.append(message)

    # 验证返回了错误消息
    assert len(messages) > 0
    assert "未初始化" in messages[0]


@pytest.mark.asyncio
async def test_handle_search(command_handler):
    """测试search命令"""
    mock_event = Mock()
    mock_event.unified_msg_origin = "test_session"

    messages = []
    async for message in command_handler.handle_search(mock_event, "测试查询", 5):
        messages.append(message)

    # 验证调用了search_memories
    assert command_handler.memory_engine.search_memories.called
    assert len(messages) > 0


@pytest.mark.asyncio
async def test_handle_search_with_results(command_handler, mock_memory_engine):
    """测试有结果的search命令"""
    # 配置mock返回结果
    mock_result = Mock()
    mock_result.doc_id = 1
    mock_result.final_score = 0.8
    mock_result.content = "测试记忆内容"

    mock_memory_engine.search_memories = AsyncMock(return_value=[mock_result])

    mock_event = Mock()
    mock_event.unified_msg_origin = "test_session"

    messages = []
    async for message in command_handler.handle_search(mock_event, "测试", 5):
        messages.append(message)

    # 验证返回了搜索结果
    assert len(messages) > 0
    assert "找到" in messages[0] or "记忆" in messages[0]


@pytest.mark.asyncio
async def test_handle_forget(command_handler):
    """测试forget命令"""
    mock_event = Mock()

    messages = []
    async for message in command_handler.handle_forget(mock_event, 1):
        messages.append(message)

    # 验证调用了delete_memory
    assert command_handler.memory_engine.delete_memory.called
    assert len(messages) > 0
    assert "删除" in messages[0]


@pytest.mark.asyncio
async def test_handle_forget_not_found(command_handler, mock_memory_engine):
    """测试删除不存在的记忆"""
    mock_memory_engine.delete_memory = AsyncMock(return_value=False)

    mock_event = Mock()

    messages = []
    async for message in command_handler.handle_forget(mock_event, 999):
        messages.append(message)

    # 验证返回了失败消息
    assert len(messages) > 0
    assert "失败" in messages[0] or "不存在" in messages[0]


@pytest.mark.asyncio
async def test_handle_rebuild_index(command_handler):
    """测试rebuild-index命令"""
    mock_event = Mock()

    messages = []
    async for message in command_handler.handle_rebuild_index(mock_event):
        messages.append(message)

    # 验证调用了check_consistency
    assert command_handler.index_validator.check_consistency.called
    assert len(messages) > 0


@pytest.mark.asyncio
async def test_handle_rebuild_index_needed(command_handler, mock_index_validator):
    """测试需要重建索引的情况"""
    # 配置mock返回需要重建
    mock_index_validator.check_consistency = AsyncMock(
        return_value=Mock(
            is_consistent=False,
            needs_rebuild=True,
            reason="索引不一致",
            documents_count=100,
            bm25_count=90,
            vector_count=95,
        )
    )

    mock_event = Mock()

    messages = []
    async for message in command_handler.handle_rebuild_index(mock_event):
        messages.append(message)

    # 验证调用了rebuild_indexes
    assert mock_index_validator.rebuild_indexes.called
    assert len(messages) > 1  # 应该有多条消息


@pytest.mark.asyncio
async def test_handle_webui(command_handler):
    """测试webui命令"""
    mock_event = Mock()

    messages = []
    async for message in command_handler.handle_webui(mock_event):
        messages.append(message)

    # 验证返回了WebUI信息
    assert len(messages) > 0
    assert "WebUI" in messages[0]


@pytest.mark.asyncio
async def test_handle_reset(command_handler):
    """测试reset命令"""
    mock_event = Mock()
    mock_event.unified_msg_origin = "test_session"

    messages = []
    async for message in command_handler.handle_reset(mock_event):
        messages.append(message)

    # 验证调用了clear_session
    assert command_handler.conversation_manager.clear_session.called
    assert len(messages) > 0
    assert "重置" in messages[0]


@pytest.mark.asyncio
async def test_handle_help(command_handler):
    """测试help命令"""
    mock_event = Mock()

    messages = []
    async for message in command_handler.handle_help(mock_event):
        messages.append(message)

    # 验证返回了帮助信息
    assert len(messages) > 0
    assert "使用指南" in messages[0] or "help" in messages[0].lower()


def test_get_webui_url_disabled(command_handler):
    """测试WebUI禁用时的URL获取"""
    url = command_handler._get_webui_url()
    # 默认配置下WebUI是禁用的
    assert url is None


def test_get_webui_url_enabled(config_manager):
    """测试WebUI启用时的URL获取"""
    # 创建启用WebUI的配置
    config = {
        "webui_settings": {
            "enabled": True,
            "host": "127.0.0.1",
            "port": 8080,
        }
    }
    config_manager = ConfigManager(config)

    # 创建mock webui_server
    mock_webui = Mock()

    handler = CommandHandler(
        config_manager=config_manager,
        memory_engine=None,
        conversation_manager=None,
        index_validator=None,
        webui_server=mock_webui,
    )

    url = handler._get_webui_url()
    assert url == "http://127.0.0.1:8080"
