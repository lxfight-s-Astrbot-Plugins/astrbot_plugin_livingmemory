"""
Tests for PluginInitializer state management and provider resolution.
"""

from unittest.mock import Mock

import pytest
from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.plugin_initializer import PluginInitializer


@pytest.fixture
def mock_context():
    context = Mock()
    context.get_provider_by_id = Mock(return_value=None)
    context.get_all_embedding_providers = Mock(return_value=[])
    context.get_using_provider = Mock(return_value=None)
    return context


@pytest.fixture
def initializer(mock_context, tmp_path):
    return PluginInitializer(mock_context, ConfigManager(), str(tmp_path))


def test_initializer_default_state(initializer):
    assert initializer.is_initialized is False
    assert initializer.is_failed is False
    assert initializer.error_message is None


@pytest.mark.asyncio
async def test_ensure_initialized_timeout(initializer):
    ok = await initializer.ensure_initialized(timeout=0.1)
    assert ok is False


def test_initialize_providers_with_fallback(monkeypatch, mock_context, tmp_path):
    class DummyEmbeddingProvider:
        pass

    class DummyProvider:
        pass

    # make isinstance checks pass
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.plugin_initializer.EmbeddingProvider",
        DummyEmbeddingProvider,
    )
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.plugin_initializer.Provider",
        DummyProvider,
    )

    emb = DummyEmbeddingProvider()
    llm = DummyProvider()
    mock_context.get_provider_by_id.return_value = None
    mock_context.get_all_embedding_providers.return_value = [emb]
    mock_context.get_using_provider.return_value = llm

    init = PluginInitializer(mock_context, ConfigManager(), str(tmp_path))
    init._initialize_providers(silent=True)

    assert init.embedding_provider is emb
    assert init.llm_provider is llm


@pytest.mark.asyncio
async def test_wait_for_providers_non_blocking_success(initializer):
    initializer._initialize_providers = Mock()
    initializer.embedding_provider = object()
    initializer.llm_provider = object()

    ok = await initializer._wait_for_providers_non_blocking(max_wait=0.1)
    assert ok is True


@pytest.mark.asyncio
async def test_retry_task_done_callback_clears_state(initializer):
    task = Mock()
    task.done.return_value = True
    task.cancelled.return_value = False
    task.exception.return_value = None
    initializer._retry_task = task

    initializer._on_retry_task_done(task)
    assert initializer._retry_task is None


@pytest.mark.asyncio
async def test_retry_initialization_timeout_sets_actionable_error(initializer):
    initializer._max_provider_attempts = 0
    initializer._provider_check_attempts = 0

    await initializer._retry_initialization()

    assert initializer.is_failed is True
    assert initializer.error_message is not None
    assert "Provider 初始化超时" in initializer.error_message
    assert "请检查 provider_settings 配置" in initializer.error_message
