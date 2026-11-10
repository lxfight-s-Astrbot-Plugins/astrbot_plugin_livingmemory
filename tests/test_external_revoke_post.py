"""Tests for the POST alias used to revoke external API keys from the dashboard."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from astrbot_plugin_livingmemory.core.page_api import PluginPageApi
from astrbot_plugin_livingmemory.core.page_api_modules.external_handler import (
    ExternalHandler,
)
from astrbot_plugin_livingmemory.core.page_api_modules.utils import PageApiUtils

from .external_api_support import issue_key, make_service


def _handler_with_service(service) -> ExternalHandler:
    handler = ExternalHandler(PageApiUtils(), SimpleNamespace())
    handler._service = service
    handler._service_engine_id = id(service.memory_engine)
    return handler


@pytest.mark.asyncio
async def test_revoke_key_post_alias_revokes_key(tmp_path):
    service, engine = make_service(tmp_path)
    _, record = await issue_key(service, name="post-revoke")
    handler = _handler_with_service(service)
    request = MagicMock()
    request.get_json = AsyncMock(return_value={"id": record.id})
    with patch(
        "astrbot_plugin_livingmemory.core.page_api_modules.external_handler.request",
        request,
    ):
        payload = await handler.revoke_key_post({"memory_engine": engine})
    assert payload["status"] == "ok"
    assert payload["data"]["key"]["revoked_at"] is not None


@pytest.mark.asyncio
async def test_revoke_key_post_alias_requires_id(tmp_path):
    service, engine = make_service(tmp_path)
    handler = _handler_with_service(service)
    request = MagicMock()
    request.get_json = AsyncMock(return_value={})
    with patch(
        "astrbot_plugin_livingmemory.core.page_api_modules.external_handler.request",
        request,
    ):
        payload = await handler.revoke_key_post({"memory_engine": engine})
    assert payload["status"] == "error"
    assert payload["code"] == "invalid_request"


def test_revoke_post_route_registered():
    routes = {route[0]: route for route in PluginPageApi._ROUTES}
    suffix, handler_path, methods, _description = routes["external/keys/revoke"]
    assert suffix == "external/keys/revoke"
    assert handler_path == "external_revoke_key_post"
    assert methods == ["POST"]
