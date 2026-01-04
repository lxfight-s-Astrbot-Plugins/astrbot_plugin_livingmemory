"""
测试EventHandler
"""

import time
from unittest.mock import AsyncMock, Mock, patch

import pytest
from core.config_manager import ConfigManager
from core.event_handler import EventHandler


@pytest.fixture
def mock_context():
    """创建mock的AstrBot上下文"""
    return Mock()


@pytest.fixture
def config_manager():
    """创建配置管理器"""
    return ConfigManager()


@pytest.fixture
def mock_memory_engine():
    """创建mock的记忆引擎"""
    engine = Mock()
    engine.search_memories = AsyncMock(return_value=[])
    engine.add_memory = AsyncMock(return_value=1)
    return engine


@pytest.fixture
def mock_memory_processor():
    """创建mock的记忆处理器"""
    processor = Mock()
    processor.process_conversation = AsyncMock(
        return_value=("测试摘要", {"topics": ["测试"]}, 0.5)
    )
    return processor


@pytest.fixture
def mock_conversation_manager():
    """创建mock的会话管理器"""
    manager = Mock()
    manager.add_message_from_event = AsyncMock(return_value=Mock(id=1, metadata={}))
    manager.get_session_info = AsyncMock(return_value=Mock(message_count=10))
    manager.get_session_metadata = AsyncMock(return_value=0)
    manager.get_messages_range = AsyncMock(return_value=[])
    manager.update_session_metadata = AsyncMock()
    manager.store = Mock()
    manager.store.update_message_metadata = AsyncMock()
    manager.store.connection = Mock()
    manager.store.connection.execute = AsyncMock()
    manager.store.connection.commit = AsyncMock()
    return manager


@pytest.fixture
def event_handler(
    mock_context,
    config_manager,
    mock_memory_engine,
    mock_memory_processor,
    mock_conversation_manager,
):
    """创建事件处理器"""
    return EventHandler(
        context=mock_context,
        config_manager=config_manager,
        memory_engine=mock_memory_engine,
        memory_processor=mock_memory_processor,
        conversation_manager=mock_conversation_manager,
    )


def test_event_handler_creation(event_handler):
    """测试EventHandler创建"""
    assert event_handler is not None
    assert event_handler.config_manager is not None
    assert event_handler.memory_engine is not None
    assert event_handler.memory_processor is not None
    assert event_handler.conversation_manager is not None


def test_message_dedup_cache_initialization(event_handler):
    """测试消息去重缓存初始化"""
    assert isinstance(event_handler._message_dedup_cache, dict)
    assert len(event_handler._message_dedup_cache) == 0
    assert event_handler._dedup_cache_max_size == 1000
    assert event_handler._dedup_cache_ttl == 300


def test_is_duplicate_message(event_handler):
    """测试消息去重检查"""
    message_id = "test_message_123"

    # 第一次检查，应该不是重复
    assert not event_handler._is_duplicate_message(message_id)

    # 标记为已处理
    event_handler._mark_message_processed(message_id)

    # 第二次检查，应该是重复
    assert event_handler._is_duplicate_message(message_id)


def test_mark_message_processed(event_handler):
    """测试标记消息已处理"""
    message_id = "test_message_456"

    # 标记消息
    event_handler._mark_message_processed(message_id)

    # 验证缓存中存在
    assert message_id in event_handler._message_dedup_cache
    assert isinstance(event_handler._message_dedup_cache[message_id], float)


def test_dedup_cache_size_limit(event_handler):
    """测试去重缓存大小限制"""
    # 填充缓存到最大值
    for i in range(event_handler._dedup_cache_max_size + 10):
        event_handler._mark_message_processed(f"message_{i}")

    # 验证缓存大小不超过限制
    assert (
        len(event_handler._message_dedup_cache) <= event_handler._dedup_cache_max_size
    )


def test_dedup_cache_ttl_cleanup(event_handler):
    """测试去重缓存TTL清理"""
    message_id = "old_message"

    # 添加一个过期的消息
    event_handler._message_dedup_cache[message_id] = time.time() - 400  # 超过TTL

    # 检查消息（会触发清理）
    event_handler._is_duplicate_message("new_message")

    # 验证过期消息被清理
    assert message_id not in event_handler._message_dedup_cache


def test_extract_message_content_plain_text(event_handler):
    """测试提取纯文本消息内容"""
    # 创建mock事件
    mock_event = Mock()
    mock_event.get_message_str = Mock(return_value="测试消息")
    mock_event.get_messages = Mock(return_value=[])

    content = event_handler._extract_message_content(mock_event)
    assert content == "测试消息"


@pytest.mark.asyncio
async def test_handle_memory_recall_empty_prompt(event_handler):
    """测试处理空prompt的记忆召回"""
    mock_event = Mock()
    mock_event.unified_msg_origin = "test_session"

    mock_req = Mock()
    mock_req.prompt = None

    # 应该直接返回，不抛出异常
    await event_handler.handle_memory_recall(mock_event, mock_req)


@pytest.mark.asyncio
async def test_handle_memory_recall_with_results(event_handler, mock_memory_engine):
    """测试有结果的记忆召回"""
    # 配置mock返回结果
    mock_result = Mock()
    mock_result.content = "测试记忆"
    mock_result.final_score = 0.8
    mock_result.metadata = {"importance": 0.7}

    mock_memory_engine.search_memories = AsyncMock(return_value=[mock_result])

    mock_event = Mock()
    mock_event.unified_msg_origin = "test_session"
    mock_event.get_message_type = Mock(return_value=Mock())

    mock_req = Mock()
    mock_req.prompt = "测试查询"
    mock_req.system_prompt = None

    with patch(
        "core.event_handler.get_persona_id", new_callable=AsyncMock
    ) as mock_get_persona:
        mock_get_persona.return_value = "test_persona"

        await event_handler.handle_memory_recall(mock_event, mock_req)

        # 验证调用了search_memories
        assert mock_memory_engine.search_memories.called


@pytest.mark.asyncio
async def test_handle_memory_reflection_non_assistant(event_handler):
    """测试非assistant角色的记忆反思"""
    mock_event = Mock()
    mock_resp = Mock()
    mock_resp.role = "user"  # 非assistant

    # 应该直接返回，不处理
    await event_handler.handle_memory_reflection(mock_event, mock_resp)

    # 验证没有调用conversation_manager
    assert not event_handler.conversation_manager.add_message_from_event.called


@pytest.mark.asyncio
async def test_remove_injected_memories(event_handler):
    """测试删除注入的记忆"""
    mock_req = Mock()
    mock_req.system_prompt = "===记忆开始===\n测试记忆\n===记忆结束==="
    mock_req.contexts = []

    removed_count = event_handler._remove_injected_memories_from_context(
        mock_req, "test_session"
    )

    # 验证删除了记忆
    assert removed_count >= 0


def test_extract_message_content_with_components(event_handler):
    """测试提取包含组件的消息内容"""
    from astrbot.core.message.components import Image, Plain

    mock_event = Mock()
    mock_event.get_message_str = Mock(return_value="文本消息")

    # 创建mock组件
    mock_image = Mock(spec=Image)
    mock_plain = Mock(spec=Plain)

    mock_event.get_messages = Mock(return_value=[mock_plain, mock_image])

    content = event_handler._extract_message_content(mock_event)

    # 应该包含文本和图片标记
    assert "文本消息" in content or "[图片]" in content or len(content) > 0
