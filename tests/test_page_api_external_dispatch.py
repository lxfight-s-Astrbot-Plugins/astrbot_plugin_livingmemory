"""Regression tests: external Page API routes must dispatch through wrappers.

The route table previously referenced ``external_handler.<method>`` directly, so
the registered view was called without the ``ready`` dict and every external
route raised ``TypeError`` at runtime. These tests pin the wrapper registration.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from astrbot_plugin_livingmemory.core.page_api import PAGE_API_PREFIX, PluginPageApi

from .external_api_support import FakeMemoryEngine, make_config


class _FakeInitializer:
    def __init__(self, engine: FakeMemoryEngine, data_dir: str):
        self.memory_engine = engine
        self.data_dir = data_dir
        self.conversation_manager = None
        self.index_validator = None
        self.memory_processor = None
        self.consolidation_manager = None
        self.is_initialized = True
        self.is_failed = False


class _FakePlugin:
    def __init__(self, tmp_path):
        self.config_manager = make_config(enabled=True)
        self.initializer = _FakeInitializer(
            FakeMemoryEngine(str(tmp_path / "external.db")), str(tmp_path)
        )
        self.routes: list[tuple[str, object, list[str], str]] = []
        self.context = SimpleNamespace(register_web_api=self._register)

    def _register(self, route, handler, methods, description):
        self.routes.append((route, handler, methods, description))

    async def _ensure_plugin_ready(self, *, wait: bool = True):
        return True, ""


def _make_api(tmp_path) -> tuple[PluginPageApi, _FakePlugin]:
    plugin = _FakePlugin(tmp_path)
    api = PluginPageApi(plugin)
    api.register_routes()
    return api, plugin


def test_external_routes_resolve_to_page_api_wrappers(tmp_path):
    api, plugin = _make_api(tmp_path)
    external = [route for route in plugin.routes if "/external/" in route[0]]
    assert len(external) == 11
    for route, handler, _methods, _description in external:
        wrapped = getattr(handler, "__wrapped__", None)
        assert wrapped is not None, route
        assert getattr(wrapped, "__self__", None) is api, route


@pytest.mark.asyncio
async def test_external_key_routes_work_through_registration(tmp_path):
    api, plugin = _make_api(tmp_path)
    request = MagicMock()
    request.args = MagicMock()
    request.args.get.side_effect = lambda key, default=None: default
    request.get_json = MagicMock(return_value={})
    with patch(
        "astrbot_plugin_livingmemory.core.page_api_modules.external_handler.request",
        request,
    ):
        keys = await api.external_list_keys()
        audit = await api.external_list_audit()
    assert keys["status"] == "ok"
    assert keys["data"] == {"keys": [], "total": 0}
    assert audit["status"] == "ok"
    assert audit["data"]["records"] == []
