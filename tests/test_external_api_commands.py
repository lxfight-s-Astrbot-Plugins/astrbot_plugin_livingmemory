"""Tests for /lmem apikey command handling, help text and i18n coverage."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from astrbot_plugin_livingmemory.core.command_handler import CommandHandler
from astrbot_plugin_livingmemory.core.external_api import ExternalApiService

from .external_api_support import FakeMemoryEngine, make_config

ROOT = Path(__file__).resolve().parents[1]
TOKEN_RE = re.compile(r"lm_[A-Za-z0-9_\-]+")


class _Event:
    def __init__(self):
        self.messages: list[str] = []

    def plain_result(self, message):
        self.messages.append(message)
        return message


def _handler(tmp_path, *, enabled: bool = True) -> CommandHandler:
    engine = FakeMemoryEngine(str(tmp_path / "external_api.db"))
    return CommandHandler(
        context=object(),
        config_manager=make_config(enabled=enabled),
        memory_engine=engine,
        conversation_manager=None,
        index_validator=None,
    )


async def _run(handler: CommandHandler, event: _Event, **kwargs) -> str:
    messages = [message async for message in handler.handle_apikey(event, **kwargs)]
    assert len(messages) == 1
    return messages[0]


class TestApiKeyCommands:
    @pytest.mark.asyncio
    async def test_create_key_returns_token_once_and_works(self, tmp_path):
        handler = _handler(tmp_path)
        event = _Event()

        message = await _run(
            handler,
            event,
            action="create",
            name="workbuddy",
            mode="write",
            scopes="livingmemory:global",
        )

        assert "workbuddy" in message
        assert "仅显示一次" in message
        token = TOKEN_RE.search(message)
        assert token is not None
        plaintext = token.group(0)

        # The minted token authenticates against the same store.
        service = ExternalApiService(
            handler.config_manager, handler.memory_engine, tmp_path
        )
        health = await service.dispatch(
            "health",
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        assert health.status_code == 200
        assert health.payload["scopes"] == ["read", "write"]

    @pytest.mark.asyncio
    async def test_create_requires_name(self, tmp_path):
        handler = _handler(tmp_path)
        message = await _run(handler, _Event(), action="create", name=" ")

        assert "/lmem apikey create" in message

    @pytest.mark.asyncio
    async def test_list_shows_created_keys_with_limits(self, tmp_path):
        handler = _handler(tmp_path)
        await _run(handler, _Event(), action="create", name="reader", mode="read")
        await _run(
            handler,
            _Event(),
            action="create",
            name="writer",
            mode="write",
            rate=5,
        )

        message = await _run(handler, _Event(), action="list")

        assert "reader" in message
        assert "writer" in message
        assert "有效" in message

    @pytest.mark.asyncio
    async def test_list_when_empty(self, tmp_path):
        handler = _handler(tmp_path)
        message = await _run(handler, _Event(), action="list")
        assert "没有" in message

    @pytest.mark.asyncio
    async def test_revoke_by_name_and_id(self, tmp_path):
        handler = _handler(tmp_path)
        await _run(handler, _Event(), action="create", name="alpha", mode="read")
        await _run(handler, _Event(), action="create", name="beta", mode="read")

        revoked = await _run(handler, _Event(), action="revoke", name="alpha")
        assert "alpha" in revoked
        assert "吊销" in revoked

        revoked_by_id = await _run(handler, _Event(), action="revoke", name="2")
        assert "beta" in revoked_by_id

        missing = await _run(handler, _Event(), action="revoke", name="missing")
        assert "未找到" in missing

    @pytest.mark.asyncio
    async def test_disabled_service_blocks_management(self, tmp_path):
        handler = _handler(tmp_path, enabled=False)
        message = await _run(handler, _Event(), action="create", name="x")
        assert "未启用" in message

    @pytest.mark.asyncio
    async def test_unknown_action_and_usage(self, tmp_path):
        handler = _handler(tmp_path)
        unknown = await _run(handler, _Event(), action="delete")
        assert "未知操作" in unknown

        usage = await _run(handler, _Event(), action="revoke", name="")
        assert "/lmem apikey create" in usage


def test_help_text_lists_apikey_command_in_all_languages() -> None:
    for language in ("zh", "en", "ja", "ru"):
        data = json.loads((ROOT / "core/i18n" / f"{language}.json").read_text("utf-8"))
        help_text = data["help"]["text"]
        assert "/lmem apikey" in help_text, language
        assert "external_api.enabled" in help_text, language


def test_external_api_i18n_keys_are_consistent_across_languages() -> None:
    reference = None
    for language in ("zh", "en", "ja", "ru"):
        data = json.loads((ROOT / "core/i18n" / f"{language}.json").read_text("utf-8"))
        keys = set(data["external_api"]["command"])
        if reference is None:
            reference = keys
            assert "created" in keys and "list_row" in keys and "disabled" in keys
        assert keys == reference, language


def test_schema_and_config_locales_cover_external_api_fields() -> None:
    schema = json.loads((ROOT / "_conf_schema.json").read_text("utf-8"))
    items = schema["external_api"]["items"]

    assert set(items) == {
        "enabled",
        "listen_host",
        "listen_port",
        "max_request_bytes",
        "default_rate_limit_per_min",
        "default_write_limit_per_min",
        "audit_retention_days",
        "trust_proxy_headers",
    }
    assert items["enabled"]["default"] is False
    assert items["listen_port"]["default"] == 7801
    assert items["max_request_bytes"]["default"] == 262144

    for locale in ("zh-CN", "en-US", "ja-JP", "ru-RU"):
        data = json.loads(
            (ROOT / ".astrbot-plugin/i18n" / f"{locale}.json").read_text("utf-8")
        )
        labels = data["config"]["external_api"]
        for field in items:
            assert labels[field]["description"], (locale, field)
            assert labels[field]["hint"], (locale, field)
