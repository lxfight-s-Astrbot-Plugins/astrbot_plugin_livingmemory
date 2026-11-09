"""Tests for the Page API external routes (key management + Bearer endpoints)."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from astrbot_plugin_livingmemory.core.page_api import PAGE_API_PREFIX, PluginPageApi

from .external_api_support import FakeMemoryEngine, make_config


class FakeInitializer:
    def __init__(self, memory_engine: FakeMemoryEngine, data_dir: str):
        self.memory_engine = memory_engine
        self.data_dir = data_dir
        self.conversation_manager = None
        self.index_validator = None
        self.memory_processor = None
        self.consolidation_manager = None
        self.is_initialized = True
        self.is_failed = False


class FakePlugin:
    def __init__(self, tmp_path, *, enabled: bool = True):
        self.config_manager = make_config(enabled=enabled)
        self.initializer = FakeInitializer(
            FakeMemoryEngine(str(tmp_path / "external_api.db")), str(tmp_path)
        )
        self.routes: list[tuple[str, object, list[str], str]] = []
        self.context = SimpleNamespace(register_web_api=self._register_web_api)

    def _register_web_api(self, route, handler, methods, description):
        self.routes.append((route, handler, methods, description))

    async def _ensure_plugin_ready(self, *, wait: bool = True):
        return True, ""


def _make_api(tmp_path, *, enabled: bool = True) -> tuple[PluginPageApi, FakePlugin]:
    plugin = FakePlugin(tmp_path, enabled=enabled)
    api = PluginPageApi(plugin)
    api.register_routes()
    return api, plugin


def _request(
    *,
    headers=None,
    json_body=None,
    remote_addr="127.0.0.1",
    content_length: int = 0,
) -> MagicMock:
    request = MagicMock()
    request.headers = headers or {}
    request.content_length = content_length
    request.get_json = AsyncMock(return_value=json_body)
    request.args = MagicMock()
    request.args.get.side_effect = lambda key, default=None: default
    request.remote_addr = remote_addr
    return request


@contextmanager
def _patch_request(request: MagicMock):
    import astrbot_plugin_livingmemory.core.page_api as page_api_module
    import astrbot_plugin_livingmemory.core.page_api_modules.external_handler as handler_module

    modules = (page_api_module, handler_module)
    saved = [(module, vars(module).get("request")) for module in modules]
    for module in modules:
        vars(module)["request"] = request
    try:
        yield
    finally:
        for module, old in saved:
            if old is None:
                vars(module).pop("request", None)
            else:
                vars(module)["request"] = old


def test_routes_are_registered_under_the_page_api_prefix(tmp_path):
    _api, plugin = _make_api(tmp_path)

    routes = {
        (route, tuple(methods)) for route, _handler, methods, _desc in plugin.routes
    }

    expected = {
        (f"{PAGE_API_PREFIX}/external/keys", ("GET",)),
        (f"{PAGE_API_PREFIX}/external/keys", ("POST",)),
        (f"{PAGE_API_PREFIX}/external/keys/<key_id>", ("DELETE",)),
        (f"{PAGE_API_PREFIX}/external/health", ("GET",)),
        (f"{PAGE_API_PREFIX}/external/personas", ("GET",)),
        (f"{PAGE_API_PREFIX}/external/recall", ("POST",)),
        (f"{PAGE_API_PREFIX}/external/memories", ("POST",)),
        (f"{PAGE_API_PREFIX}/external/memories/<memory_id>", ("GET",)),
        (f"{PAGE_API_PREFIX}/external/memories/<memory_id>/archive", ("POST",)),
    }
    assert expected <= routes


class TestKeyManagement:
    @pytest.mark.asyncio
    async def test_create_list_and_revoke_key(self, tmp_path):
        api, _plugin = _make_api(tmp_path)
        create_request = _request(
            json_body={
                "name": "workbuddy",
                "mode": "write",
                "allowed_personas": ["p1"],
                "allowed_scopes": ["s1"],
                "rate_limit_per_min": 30,
            }
        )
        with _patch_request(create_request):
            created = await api.external_create_key()

        assert created["status"] == "ok"
        token = created["data"]["token"]
        assert token.startswith("lm_")
        key = created["data"]["key"]
        assert key["name"] == "workbuddy"
        assert key["scopes"] == ["read", "write"]
        assert key["rate_limit_per_min"] == 30
        assert key["allowed_personas"] == ["p1"]
        assert key["allowed_scopes"] == ["s1"]
        assert "key_hash" not in key and "token" not in key

        with _patch_request(_request()):
            listed = await api.external_list_keys()
        assert listed["status"] == "ok"
        assert [item["name"] for item in listed["data"]["keys"]] == ["workbuddy"]

        with _patch_request(_request()):
            revoked = await api.external_revoke_key("1")
        assert revoked["status"] == "ok"
        assert revoked["data"]["key"]["revoked_at"] is not None

        with _patch_request(_request()):
            missing = await api.external_revoke_key("99")
        assert missing["status"] == "error"
        assert missing["code"] == "not_found"

    @pytest.mark.asyncio
    async def test_create_key_validates_payload(self, tmp_path):
        api, _plugin = _make_api(tmp_path)

        with _patch_request(_request(json_body={})):
            missing_name = await api.external_create_key()
        assert missing_name["status"] == "error"
        assert missing_name["code"] == "invalid_request"

        with _patch_request(_request(json_body={"name": "x", "mode": "admin"})):
            bad_mode = await api.external_create_key()
        assert bad_mode["code"] == "invalid_request"

        with _patch_request(_request(json_body={"name": "x", "scopes": []})):
            empty_scopes = await api.external_create_key()
        assert empty_scopes["code"] == "invalid_request"

    @pytest.mark.asyncio
    async def test_management_routes_are_hidden_when_disabled(self, tmp_path):
        api, _plugin = _make_api(tmp_path, enabled=False)

        with _patch_request(_request()):
            listed = await api.external_list_keys()
        assert listed == {
            "status": "error",
            "code": "not_found",
            "message": "Not found",
            "http_status": 404,
        }


class TestBearerRoutes:
    @pytest.mark.asyncio
    async def test_health_requires_valid_key(self, tmp_path):
        api, plugin = _make_api(tmp_path)
        service = api.external_handler.get_service()
        _record, token = await service.create_key("reader", scopes=["read"])

        with _patch_request(_request()):
            unauthorized = await api.external_health()
        assert unauthorized["status"] == "error"
        assert unauthorized["code"] == "unauthorized"
        assert unauthorized["http_status"] == 401

        with _patch_request(_request(headers={"Authorization": f"Bearer {token}"})):
            healthy = await api.external_health()
        assert healthy["status"] == "ok"
        assert healthy["ready"] is True
        assert healthy["http_status"] == 200
        assert plugin.initializer.memory_engine is not None

    @pytest.mark.asyncio
    async def test_recall_and_write_through_page_routes(self, tmp_path):
        api, plugin = _make_api(tmp_path)
        engine = plugin.initializer.memory_engine
        engine.set_search_results(
            [(5, "matched", {"persona_id": "p", "session_id": "s"}, 0.7)]
        )
        service = api.external_handler.get_service()
        _record, token = await service.create_key("writer", scopes=["read", "write"])
        headers = {"Authorization": f"Bearer {token}"}

        with _patch_request(
            _request(headers=headers, json_body={"query": "remember", "k": 3})
        ):
            recalled = await api.external_recall()
        assert recalled["status"] == "ok"
        assert recalled["total"] == 1
        assert recalled["items"][0]["memory_id"] == 5

        with _patch_request(
            _request(
                headers=headers,
                json_body={"content": "page api memory", "topics": ["t"]},
            )
        ):
            written = await api.external_write_memory()
        assert written["status"] == "ok"
        assert written["created"] is True
        assert engine.added[-1]["metadata"]["source"] == "external_api"

        with _patch_request(_request(headers=headers)):
            fetched = await api.external_get_memory(str(written["memory_id"]))
        assert fetched["status"] == "ok"
        assert fetched["memory"]["content"] == "page api memory"

        with _patch_request(_request(headers=headers)):
            archived = await api.external_archive_memory(str(written["memory_id"]))
        assert archived["status"] == "ok"
        assert archived["archived"] is True

    @pytest.mark.asyncio
    async def test_write_requires_write_scope(self, tmp_path):
        api, _plugin = _make_api(tmp_path)
        service = api.external_handler.get_service()
        _record, token = await service.create_key("reader", scopes=["read"])

        with _patch_request(
            _request(
                headers={"Authorization": f"Bearer {token}"},
                json_body={"content": "denied"},
            )
        ):
            response = await api.external_write_memory()

        assert response["status"] == "error"
        assert response["code"] == "forbidden"
        assert response["http_status"] == 403

    @pytest.mark.asyncio
    async def test_invalid_memory_id_in_path(self, tmp_path):
        api, _plugin = _make_api(tmp_path)
        service = api.external_handler.get_service()
        _record, token = await service.create_key("reader", scopes=["read"])

        with _patch_request(_request(headers={"Authorization": f"Bearer {token}"})):
            response = await api.external_get_memory("abc")

        assert response["status"] == "error"
        assert response["code"] == "invalid_request"
        assert response["http_status"] == 400

    @pytest.mark.asyncio
    async def test_oversized_body_is_rejected_before_parsing(self, tmp_path):
        api, plugin = _make_api(tmp_path)
        service = api.external_handler.get_service()
        _record, token = await service.create_key("writer", scopes=["read", "write"])
        request = _request(
            headers={"Authorization": f"Bearer {token}"},
            json_body={"content": "x"},
            content_length=service.max_request_bytes + 1,
        )

        with _patch_request(request):
            response = await api.external_write_memory()

        assert response["status"] == "error"
        assert response["code"] == "request_too_large"
        assert response["http_status"] == 400
        assert plugin.initializer.memory_engine.added == []

    @pytest.mark.asyncio
    async def test_bearer_routes_are_hidden_when_disabled(self, tmp_path):
        api, _plugin = _make_api(tmp_path, enabled=False)

        with _patch_request(_request()):
            response = await api.external_health()

        assert response["status"] == "error"
        assert response["code"] == "not_found"
        assert response["http_status"] == 404
