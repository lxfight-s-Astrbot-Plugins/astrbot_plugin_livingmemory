"""
测试PluginInitializer
"""

from unittest.mock import Mock

import pytest
from core.config_manager import ConfigManager
from core.plugin_initializer import PluginInitializer


@pytest.fixture
def mock_context():
    """创建mock的AstrBot上下文"""
    context = Mock()
    context.get_provider_by_id = Mock(return_value=None)
    context.get_all_embedding_providers = Mock(return_value=[])
    context.get_using_provider = Mock(return_value=None)
    return context


@pytest.fixture
def config_manager():
    """创建配置管理器"""
    return ConfigManager()


@pytest.fixture
def plugin_initializer(mock_context, config_manager, tmp_path):
    """创建插件初始化器"""
    return PluginInitializer(mock_context, config_manager, str(tmp_path))


def test_plugin_initializer_creation(plugin_initializer):
    """测试PluginInitializer创建"""
    assert plugin_initializer is not None
    assert not plugin_initializer.is_initialized
    assert not plugin_initializer.is_failed
    assert plugin_initializer.error_message is None


def test_plugin_initializer_properties(plugin_initializer):
    """测试PluginInitializer属性"""
    # 初始状态
    assert plugin_initializer.embedding_provider is None
    assert plugin_initializer.llm_provider is None
    assert plugin_initializer.db is None
    assert plugin_initializer.memory_engine is None
    assert plugin_initializer.memory_processor is None
    assert plugin_initializer.conversation_manager is None


@pytest.mark.asyncio
async def test_ensure_initialized_timeout(plugin_initializer):
    """测试ensure_initialized超时"""
    # 设置一个很短的超时时间
    result = await plugin_initializer.ensure_initialized(timeout=0.1)
    assert result is False


def test_initialization_status_messages(plugin_initializer):
    """测试初始化状态消息"""
    # 未初始化状态
    assert not plugin_initializer.is_initialized
    assert not plugin_initializer.is_failed

    # 模拟初始化失败
    plugin_initializer._initialization_failed = True
    plugin_initializer._initialization_error = "Test error"
    assert plugin_initializer.is_failed
    assert plugin_initializer.error_message == "Test error"


def test_initialize_providers_with_config(mock_context, config_manager):
    """测试使用配置初始化Provider"""
    # 创建mock provider
    mock_embedding_provider = Mock()
    mock_llm_provider = Mock()

    # 配置返回mock provider
    mock_context.get_provider_by_id = Mock(
        side_effect=lambda id: mock_embedding_provider
        if "embedding" in id
        else mock_llm_provider
    )

    # 创建带配置的初始化器
    config = {
        "provider_settings": {
            "embedding_provider_id": "test_embedding",
            "llm_provider_id": "test_llm",
        }
    }
    config_manager = ConfigManager(config)
    initializer = PluginInitializer(mock_context, config_manager, "/tmp/test_data")

    # 初始化providers
    initializer._initialize_providers(silent=True)

    # 验证调用
    assert mock_context.get_provider_by_id.called


def test_initialize_providers_fallback(mock_context, config_manager):
    """测试Provider回退机制"""
    # 创建mock provider
    mock_embedding_provider = Mock()
    mock_llm_provider = Mock()

    # 配置回退行为
    mock_context.get_provider_by_id = Mock(return_value=None)
    mock_context.get_all_embedding_providers = Mock(
        return_value=[mock_embedding_provider]
    )
    mock_context.get_using_provider = Mock(return_value=mock_llm_provider)

    initializer = PluginInitializer(mock_context, config_manager, "/tmp/test_data")
    initializer._initialize_providers(silent=True)

    # 验证使用了回退机制
    assert mock_context.get_all_embedding_providers.called
    assert mock_context.get_using_provider.called


@pytest.mark.asyncio
async def test_wait_for_providers_timeout(plugin_initializer):
    """测试Provider等待超时"""
    result = await plugin_initializer._wait_for_providers_non_blocking(max_wait=0.1)
    assert result is False
    assert plugin_initializer._provider_check_attempts > 0


def test_provider_check_attempts_tracking(plugin_initializer):
    """测试Provider检查次数跟踪"""
    initial_attempts = plugin_initializer._provider_check_attempts
    plugin_initializer._initialize_providers(silent=True)
    # 检查次数应该保持不变（因为没有实际等待）
    assert plugin_initializer._provider_check_attempts == initial_attempts


def test_max_provider_attempts_limit(plugin_initializer):
    """测试Provider最大尝试次数限制"""
    assert plugin_initializer._max_provider_attempts == 60
    assert (
        plugin_initializer._provider_check_attempts
        < plugin_initializer._max_provider_attempts
    )
