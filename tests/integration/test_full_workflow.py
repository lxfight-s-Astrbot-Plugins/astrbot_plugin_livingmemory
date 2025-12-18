"""
端到端集成测试
测试完整的记忆存储和召回流程
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from core.command_handler import CommandHandler
from core.config_manager import ConfigManager
from core.event_handler import EventHandler


@pytest.fixture
def integration_setup():
    """集成测试环境设置"""
    # 创建所有必需的mock对象
    mock_context = Mock()
    config_manager = ConfigManager()

    mock_memory_engine = Mock()
    mock_memory_engine.search_memories = AsyncMock(return_value=[])
    mock_memory_engine.add_memory = AsyncMock(return_value=1)
    mock_memory_engine.get_statistics = AsyncMock(return_value={
        "total_memories": 0,
        "sessions": {},
        "newest_memory": None,
    })

    mock_memory_processor = Mock()
    mock_memory_processor.process_conversation = AsyncMock(
        return_value=("测试摘要", {"topics": ["测试"]}, 0.5)
    )

    mock_conversation_manager = Mock()
    mock_conversation_manager.add_message_from_event = AsyncMock(
        return_value=Mock(id=1, metadata={})
    )
    mock_conversation_manager.get_session_info = AsyncMock(
        return_value=Mock(message_count=0)
    )
    mock_conversation_manager.get_session_metadata = AsyncMock(return_value=0)
    mock_conversation_manager.get_messages_range = AsyncMock(return_value=[])
    mock_conversation_manager.update_session_metadata = AsyncMock()
    mock_conversation_manager.clear_session = AsyncMock()

    event_handler = EventHandler(
        context=mock_context,
        config_manager=config_manager,
        memory_engine=mock_memory_engine,
        memory_processor=mock_memory_processor,
        conversation_manager=mock_conversation_manager,
    )

    command_handler = CommandHandler(
        config_manager=config_manager,
        memory_engine=mock_memory_engine,
        conversation_manager=mock_conversation_manager,
        index_validator=None,
    )

    return {
        "event_handler": event_handler,
        "command_handler": command_handler,
        "memory_engine": mock_memory_engine,
        "conversation_manager": mock_conversation_manager,
    }


@pytest.mark.asyncio
async def test_full_memory_workflow(integration_setup):
    """测试完整的记忆工作流程"""
    event_handler = integration_setup["event_handler"]
    command_handler = integration_setup["command_handler"]
    memory_engine = integration_setup["memory_engine"]

    # 1. 模拟用户消息
    mock_event = Mock()
    mock_event.unified_msg_origin = "test_session_123"
    mock_event.get_message_type = Mock(return_value=Mock())

    mock_req = Mock()
    mock_req.prompt = "你好，我是测试用户"
    mock_req.system_prompt = None

    # 2. 处理记忆召回（应该没有记忆）
    with patch("core.event_handler.get_persona_id", new_callable=AsyncMock) as mock_get_persona:
        mock_get_persona.return_value = "test_persona"
        await event_handler.handle_memory_recall(mock_event, mock_req)

    # 验证调用了search_memories
    assert memory_engine.search_memories.called

    # 3. 模拟LLM响应
    mock_resp = Mock()
    mock_resp.role = "assistant"
    mock_resp.completion_text = "你好！我是AI助手"

    # 4. 处理记忆反思（但消息数不够，不会触发总结）
    await event_handler.handle_memory_reflection(mock_event, mock_resp)

    # 5. 查询状态
    messages = []
    async for message in command_handler.handle_status(mock_event):
        messages.append(message)

    assert len(messages) > 0


@pytest.mark.asyncio
async def test_memory_search_workflow(integration_setup):
    """测试记忆搜索工作流程"""
    command_handler = integration_setup["command_handler"]
    memory_engine = integration_setup["memory_engine"]

    # 配置mock返回搜索结果
    mock_result = Mock()
    mock_result.doc_id = 1
    mock_result.final_score = 0.9
    mock_result.content = "这是一条测试记忆"

    memory_engine.search_memories = AsyncMock(return_value=[mock_result])

    # 执行搜索
    mock_event = Mock()
    mock_event.unified_msg_origin = "test_session"

    messages = []
    async for message in command_handler.handle_search(mock_event, "测试", 5):
        messages.append(message)

    # 验证返回了结果
    assert len(messages) > 0
    assert memory_engine.search_memories.called


@pytest.mark.asyncio
async def test_session_reset_workflow(integration_setup):
    """测试会话重置工作流程"""
    command_handler = integration_setup["command_handler"]
    conversation_manager = integration_setup["conversation_manager"]

    # 执行重置
    mock_event = Mock()
    mock_event.unified_msg_origin = "test_session"

    messages = []
    async for message in command_handler.handle_reset(mock_event):
        messages.append(message)

    # 验证调用了clear_session
    assert conversation_manager.clear_session.called
    assert len(messages) > 0


@pytest.mark.asyncio
async def test_multiple_messages_workflow(integration_setup):
    """测试多条消息的工作流程"""
    event_handler = integration_setup["event_handler"]
    conversation_manager = integration_setup["conversation_manager"]

    # 模拟多轮对话
    session_id = "test_session_multi"

    for i in range(5):
        # 用户消息
        mock_event = Mock()
        mock_event.unified_msg_origin = session_id
        mock_event.get_message_type = Mock(return_value=Mock())

        mock_req = Mock()
        mock_req.prompt = f"用户消息 {i}"
        mock_req.system_prompt = None

        with patch("core.event_handler.get_persona_id", new_callable=AsyncMock) as mock_get_persona:
            mock_get_persona.return_value = "test_persona"
            await event_handler.handle_memory_recall(mock_event, mock_req)

        # 助手响应
        mock_resp = Mock()
        mock_resp.role = "assistant"
        mock_resp.completion_text = f"助手响应 {i}"

        await event_handler.handle_memory_reflection(mock_event, mock_resp)

    # 验证调用了多次add_message_from_event
    assert conversation_manager.add_message_from_event.call_count >= 5


@pytest.mark.asyncio
async def test_error_handling_workflow(integration_setup):
    """测试错误处理工作流程"""
    command_handler = integration_setup["command_handler"]
    memory_engine = integration_setup["memory_engine"]

    # 配置mock抛出异常
    memory_engine.search_memories = AsyncMock(side_effect=Exception("测试错误"))

    # 执行搜索（应该捕获异常）
    mock_event = Mock()
    mock_event.unified_msg_origin = "test_session"

    messages = []
    async for message in command_handler.handle_search(mock_event, "测试", 5):
        messages.append(message)

    # 验证返回了错误消息
    assert len(messages) > 0
    assert "失败" in messages[0] or "错误" in messages[0]
