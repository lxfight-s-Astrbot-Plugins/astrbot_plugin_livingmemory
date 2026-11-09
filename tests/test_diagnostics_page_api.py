"""Tests for the diagnostics Page API: usage, channel overview and audit tail."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import aiosqlite
import pytest
from astrbot_plugin_livingmemory.core.page_api import PluginPageApi
from astrbot_plugin_livingmemory.core.page_api_modules.diagnostics_handler import (
    DiagnosticsHandler,
)
from astrbot_plugin_livingmemory.core.page_api_modules.utils import PageApiUtils

from .external_api_support import FakeMemoryEngine, make_config, make_service


def _usage_config(**overrides):
    values = {
        "provider_settings.llm_fallback_provider_ids": ["backup-a", "backup-b"],
        "provider_settings.llm_call_timeout_seconds": 45,
    }
    values.update(overrides)

    def get(path, default=None):
        return values.get(path, default)

    return SimpleNamespace(get=get)


async def _channel_db(tmp_path):
    conn = await aiosqlite.connect(str(tmp_path / "channels.db"))
    conn.row_factory = aiosqlite.Row
    await conn.execute(
        "CREATE TABLE documents (id INTEGER PRIMARY KEY, text TEXT, metadata TEXT)"
    )
    await conn.commit()
    return conn


async def _insert(conn, metadata: dict):
    await conn.execute(
        "INSERT INTO documents (text, metadata) VALUES (?, ?)",
        ("memory", json.dumps(metadata)),
    )


def test_usage_returns_snapshot_and_fallback_config():
    handler = DiagnosticsHandler(PageApiUtils())
    payload = handler.usage(_usage_config())
    assert payload["fallback_provider_ids"] == ["backup-a", "backup-b"]
    assert payload["timeout_seconds"] == 45
    assert {"calls", "total_tokens", "providers", "fallback_calls"} <= set(
        payload["usage"]
    )


@pytest.mark.asyncio
async def test_channels_aggregates_active_channel_memories(tmp_path):
    conn = await _channel_db(tmp_path)
    try:
        await _insert(
            conn,
            {
                "session_id": "livingmemory:channel:telegram:-1001",
                "status": "active",
                "create_time": 100.0,
            },
        )
        await _insert(
            conn,
            {
                "session_id": "livingmemory:channel:telegram:-1001",
                "status": "active",
                "create_time": 200.0,
            },
        )
        await _insert(
            conn,
            {
                "session_id": "livingmemory:channel:telegram:-1001",
                "status": "archived",
                "create_time": 300.0,
            },
        )
        await _insert(
            conn,
            {
                "session_id": "livingmemory:channel:discord:guild-2",
                "status": "active",
                "create_time": 150.0,
            },
        )
        await _insert(
            conn,
            {"session_id": "livingmemory:session:private", "status": "active"},
        )
        await conn.commit()

        handler = DiagnosticsHandler(PageApiUtils())
        engine = SimpleNamespace(db_connection=conn)
        payload = await handler.channels(engine)
    finally:
        await conn.close()

    assert payload["status"] == "ok"
    channels = payload["data"]["channels"]
    assert payload["data"]["total"] == 2
    by_scope = {item["scope"]: item for item in channels}
    telegram = by_scope["livingmemory:channel:telegram:-1001"]
    assert telegram["platform"] == "telegram"
    assert telegram["channel_id"] == "-1001"
    assert telegram["memory_count"] == 2
    assert telegram["latest_create_time"] == 200.0
    discord = by_scope["livingmemory:channel:discord:guild-2"]
    assert discord["memory_count"] == 1


@pytest.mark.asyncio
async def test_audit_tail_returns_sanitized_newest_records(tmp_path):
    service, _ = make_service(tmp_path)
    await service._audit.record({"action": "recall", "status_code": 200, "key_id": 1})
    await service._audit.record({"action": "write", "status_code": 201, "key_id": 2})
    records = await service.recent_audit(1)
    assert len(records) == 1
    assert records[0]["action"] == "write"
    assert records[0]["status_code"] == 201
    assert "token" not in json.dumps(records[0])


def test_routes_registered():
    routes = {route[0] for route in PluginPageApi._ROUTES}
    assert {"usage", "channels", "external/audit"} <= routes


@pytest.mark.asyncio
async def test_audit_page_handler_clamps_limit(tmp_path):
    engine = FakeMemoryEngine(str(tmp_path / "external_api.db"))
    plugin = SimpleNamespace(
        config_manager=make_config(enabled=True),
        initializer=SimpleNamespace(memory_engine=engine, data_dir=str(tmp_path)),
    )
    handler = PluginPageApi(plugin).external_handler
    service = handler.get_service(engine)
    await service._audit.record({"action": "health", "status_code": 200})
    request = MagicMock()
    request.args = MagicMock()
    request.args.get.side_effect = lambda key, default=None: (
        "500" if key == "limit" else default
    )
    with patch(
        "astrbot_plugin_livingmemory.core.page_api_modules.external_handler.request",
        request,
    ):
        payload = await handler.list_audit({"memory_engine": engine})
    assert payload["status"] == "ok"
    assert payload["data"]["total"] == 1
    assert payload["data"]["records"][0]["action"] == "health"


@pytest.mark.asyncio
async def test_channels_returns_error_when_query_fails():
    class BrokenConnection:
        async def execute(self, *_args, **_kwargs):
            raise RuntimeError("db down")

    handler = DiagnosticsHandler(PageApiUtils())
    payload = await handler.channels(SimpleNamespace(db_connection=BrokenConnection()))
    assert payload["status"] == "error"
