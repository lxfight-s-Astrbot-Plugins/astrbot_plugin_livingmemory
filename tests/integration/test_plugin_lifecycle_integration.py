"""
Integration tests for plugin lifecycle behavior in main.py.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import astrbot_plugin_livingmemory.main as plugin_main
import pytest


class _FakeInitializer:
    def __init__(self, context, config_manager, data_dir):
        del context, config_manager, data_dir
        self.memory_engine = None
        self.memory_processor = None
        self.conversation_manager = None
        self.index_validator = None
        self.db = None
        self._initialization_complete = False
        self._initialization_failed = False
        self._initialization_error = None
        self._provider_check_attempts = 0
        self.initialize = AsyncMock(return_value=False)
        self.ensure_initialized = AsyncMock(return_value=False)
        self.stop_background_tasks = AsyncMock()
        self.stop_scheduler = AsyncMock()

    @property
    def is_initialized(self) -> bool:
        return self._initialization_complete

    @property
    def is_failed(self) -> bool:
        return self._initialization_failed

    @property
    def error_message(self) -> str | None:
        return self._initialization_error


class _TestEvent:
    def plain_result(self, message):
        return message


async def _build_plugin(monkeypatch, tmp_path, config: dict | None = None):
    monkeypatch.setattr(plugin_main, "PluginInitializer", _FakeInitializer)
    monkeypatch.setattr(plugin_main.StarTools, "get_data_dir", lambda: tmp_path)
    plugin = plugin_main.LivingMemoryPlugin(context=Mock(), config=config or {})
    # Flush startup task callback queue to keep task set stable in assertions.
    await asyncio.sleep(0)
    return plugin


@pytest.mark.asyncio
async def test_initialization_status_messages_cover_all_states(monkeypatch, tmp_path):
    plugin = await _build_plugin(monkeypatch, tmp_path)
    plugin.initializer._provider_check_attempts = 3

    waiting = plugin._get_initialization_status_message()
    assert "后台初始化中" in waiting
    assert "3 次" in waiting

    plugin.initializer._initialization_failed = True
    plugin.initializer._initialization_error = "provider timeout"
    failed = plugin._get_initialization_status_message()
    assert "插件初始化失败" in failed
    assert "provider timeout" in failed
    assert "Embedding Provider" in failed

    plugin.initializer._initialization_failed = False
    plugin.initializer._initialization_complete = True
    ready = plugin._get_initialization_status_message()
    assert "插件已就绪" in ready

    await plugin.terminate()


@pytest.mark.asyncio
async def test_ensure_plugin_ready_returns_component_error_when_incomplete(
    monkeypatch, tmp_path
):
    plugin = await _build_plugin(monkeypatch, tmp_path)
    plugin.initializer._initialization_complete = True
    plugin.initializer.ensure_initialized = AsyncMock(return_value=True)

    ready, message = await plugin._ensure_plugin_ready()
    assert ready is False
    assert "插件核心组件未初始化" in message
    assert "/lmem status" in message

    await plugin.terminate()


@pytest.mark.asyncio
async def test_status_command_returns_not_ready_message_without_handler(
    monkeypatch, tmp_path
):
    plugin = await _build_plugin(monkeypatch, tmp_path)
    plugin._ensure_plugin_ready = AsyncMock(return_value=(True, ""))
    plugin.command_handler = None

    outputs = [msg async for msg in plugin.status(_TestEvent())]
    assert len(outputs) == 1
    assert "命令处理器尚未就绪" in outputs[0]

    await plugin.terminate()


@pytest.mark.asyncio
async def test_webui_start_stop_updates_command_handler(monkeypatch, tmp_path):
    class _FakeWebUIServer:
        def __init__(
            self,
            memory_engine,
            config,
            conversation_manager=None,
            index_validator=None,
        ):
            del memory_engine, config, conversation_manager, index_validator
            self.started = False
            self.stopped = False

        async def start(self):
            self.started = True

        async def stop(self):
            self.stopped = True

    plugin = await _build_plugin(
        monkeypatch,
        tmp_path,
        config={
            "webui_settings": {
                "enabled": True,
                "host": "127.0.0.1",
                "port": 6186,
                "access_password": "test-password",
            }
        },
    )
    monkeypatch.setattr(plugin_main, "WebUIServer", _FakeWebUIServer)

    plugin.initializer.memory_engine = SimpleNamespace(close=AsyncMock())
    plugin.initializer.conversation_manager = SimpleNamespace(store=None)
    plugin.initializer.index_validator = object()
    plugin.command_handler = SimpleNamespace(webui_server=None)

    await plugin._start_webui()
    assert plugin.webui_server is not None
    assert plugin.webui_server.started is True
    assert plugin.command_handler.webui_server is plugin.webui_server

    await plugin._stop_webui()
    assert plugin.webui_server is None
    assert plugin.command_handler.webui_server is None

    await plugin.terminate()


@pytest.mark.asyncio
async def test_terminate_cleans_background_tasks_and_resources(monkeypatch, tmp_path):
    plugin = await _build_plugin(monkeypatch, tmp_path)

    plugin.event_handler = SimpleNamespace(shutdown=AsyncMock())
    plugin.command_handler = SimpleNamespace(webui_server=None)
    plugin.initializer.conversation_manager = SimpleNamespace(
        store=SimpleNamespace(close=AsyncMock())
    )
    plugin.initializer.memory_engine = SimpleNamespace(close=AsyncMock())
    plugin.initializer.db = SimpleNamespace(close=AsyncMock())

    running_task = plugin._create_tracked_task(asyncio.sleep(3600))
    await asyncio.sleep(0)
    assert running_task in plugin._background_tasks

    await plugin.terminate()

    plugin.initializer.stop_background_tasks.assert_awaited_once()
    plugin.initializer.stop_scheduler.assert_awaited_once()
    plugin.event_handler.shutdown.assert_awaited_once()
    plugin.initializer.conversation_manager.store.close.assert_awaited_once()
    plugin.initializer.memory_engine.close.assert_awaited_once()
    plugin.initializer.db.close.assert_awaited_once()
    assert running_task.cancelled() or running_task.done()
    assert len(plugin._background_tasks) == 0
