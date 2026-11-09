"""Lifecycle tests: external API listener starts only when enabled and stops cleanly."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import astrbot_plugin_livingmemory.main as main_module
import pytest
from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.main import LivingMemoryPlugin

from .external_api_support import FakeMemoryEngine


def _make_plugin(tmp_path, *, enabled: bool) -> tuple[LivingMemoryPlugin, Mock]:
    plugin = LivingMemoryPlugin.__new__(LivingMemoryPlugin)
    plugin.context = Mock()
    plugin.config_manager = ConfigManager(
        {
            "external_api": {
                "enabled": enabled,
                "listen_host": "127.0.0.1",
                "listen_port": 7801,
            }
        }
    )
    initializer = Mock()
    initializer.memory_engine = FakeMemoryEngine(str(tmp_path / "external.db"))
    initializer.data_dir = str(tmp_path)
    initializer.is_initialized = True
    initializer.is_failed = False
    initializer._provider_check_attempts = 0
    initializer.stop_background_tasks = AsyncMock()
    initializer.stop_scheduler = AsyncMock()
    initializer.shutdown_index_persisters = AsyncMock()
    initializer.memory_processor = Mock()
    initializer.conversation_manager = None
    initializer.consolidation_manager = None
    initializer.index_validator = None
    initializer.db = None
    plugin.initializer = initializer
    plugin.event_handler = None
    plugin.command_handler = None
    plugin._background_tasks = set()
    plugin._component_init_lock = asyncio.Lock()
    plugin._llm_tools_registered = True
    plugin._terminating = False
    plugin._external_api_server = None
    plugin.page_api = Mock()
    plugin._backup_manager = Mock()
    return plugin, initializer


@pytest.mark.asyncio
async def test_listener_not_started_when_disabled(tmp_path):
    plugin, _initializer = _make_plugin(tmp_path, enabled=False)

    await plugin._ensure_external_api_server()

    assert plugin._external_api_server is None


@pytest.mark.asyncio
async def test_listener_starts_once_when_enabled(tmp_path, monkeypatch):
    plugin, _initializer = _make_plugin(tmp_path, enabled=True)
    real_get = plugin.config_manager.get
    monkeypatch.setattr(
        plugin.config_manager,
        "get",
        lambda key, default=None: (
            0 if key == "external_api.listen_port" else real_get(key, default)
        ),
    )

    await plugin._ensure_external_api_server()
    first = plugin._external_api_server
    await plugin._ensure_external_api_server()

    assert first is not None
    assert plugin._external_api_server is first
    assert first.running is True
    assert first.bound_port > 0

    await plugin._stop_external_api_server()
    assert plugin._external_api_server is None
    assert first.running is False
    # Stopping again is a safe no-op.
    await plugin._stop_external_api_server()


@pytest.mark.asyncio
async def test_terminate_stops_listener(tmp_path, monkeypatch):
    plugin, _initializer = _make_plugin(tmp_path, enabled=False)
    fake_server = Mock()
    plugin._external_api_server = fake_server
    monkeypatch.setattr(main_module, "get_active_plugin", Mock(return_value=plugin))
    monkeypatch.setattr(main_module, "set_active_plugin", Mock())

    await plugin.terminate()

    fake_server.stop.assert_called_once_with()
    assert plugin._external_api_server is None
