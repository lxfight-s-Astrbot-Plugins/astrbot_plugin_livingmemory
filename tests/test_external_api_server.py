"""Transport tests for the standalone external API HTTP listener."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request

import pytest
from astrbot_plugin_livingmemory.core.external_api import ExternalResponse
from astrbot_plugin_livingmemory.core.external_api_server import (
    API_PATH_PREFIX,
    ExternalApiServer,
)


class FakeService:
    """Records dispatched actions and returns canned responses."""

    enabled = True
    max_request_bytes = 100
    trust_proxy_headers = False

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def dispatch(
        self,
        action: str,
        *,
        headers=None,
        payload=None,
        memory_id=None,
        remote_addr=None,
    ) -> ExternalResponse:
        self.calls.append(
            {
                "action": action,
                "headers": headers,
                "payload": payload,
                "memory_id": memory_id,
                "remote_addr": remote_addr,
            }
        )
        if payload and payload.get("simulate") == "rate":
            return ExternalResponse(
                {"status": "error", "code": "rate_limited", "message": "slow down"},
                429,
                {"Retry-After": "7"},
            )
        return ExternalResponse(
            {
                "status": "ok",
                "action": action,
                "memory_id": memory_id,
                "payload": payload,
            }
        )


def _request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[int, dict, dict]:
    request = urllib.request.Request(
        url, data=body, headers=headers or {}, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return (
                response.status,
                json.loads(response.read().decode("utf-8")),
                dict(response.headers),
            )
    except urllib.error.HTTPError as exc:
        return (
            exc.code,
            json.loads(exc.read().decode("utf-8")),
            dict(exc.headers),
        )


async def _start(service: FakeService, **kwargs) -> ExternalApiServer:
    server = ExternalApiServer(service, host="127.0.0.1", port=0, **kwargs)
    assert server.start() is True
    return server


@pytest.mark.asyncio
async def test_http_listener_forwards_actions_and_headers():
    service = FakeService()
    server = await _start(service)
    base = f"http://127.0.0.1:{server.bound_port}{API_PATH_PREFIX}"
    auth = {"Authorization": "Bearer lm_token"}
    try:
        status, payload, _headers = await asyncio.to_thread(
            _request, f"{base}/health", headers=auth
        )
        assert status == 200
        assert payload["action"] == "health"
        assert service.calls[-1]["headers"]["authorization"] == "Bearer lm_token"
        assert service.calls[-1]["remote_addr"] == "127.0.0.1"

        body = json.dumps({"query": "hello", "k": 3}).encode()
        status, payload, _headers = await asyncio.to_thread(
            _request,
            f"{base}/recall",
            method="POST",
            headers={**auth, "Content-Type": "application/json"},
            body=body,
        )
        assert status == 200
        assert payload["action"] == "recall"
        assert service.calls[-1]["payload"] == {"query": "hello", "k": 3}

        status, payload, _headers = await asyncio.to_thread(
            _request, f"{base}/memories/12", headers=auth
        )
        assert status == 200
        assert payload["memory_id"] == 12

        status, payload, _headers = await asyncio.to_thread(
            _request,
            f"{base}/memories/12/archive",
            method="POST",
            headers=auth,
            body=b"{}",
        )
        assert status == 200
        assert payload["action"] == "archive"
        assert payload["memory_id"] == 12
    finally:
        server.stop()


@pytest.mark.asyncio
async def test_http_listener_rejects_unknown_routes_and_methods():
    service = FakeService()
    server = await _start(service)
    base = f"http://127.0.0.1:{server.bound_port}{API_PATH_PREFIX}"
    try:
        status, payload, _headers = await asyncio.to_thread(_request, f"{base}/unknown")
        assert status == 404
        assert payload["code"] == "not_found"

        status, payload, _headers = await asyncio.to_thread(
            _request, f"{base}/health", method="DELETE"
        )
        assert status == 405
        assert payload["code"] == "method_not_allowed"

        # A non-numeric memory id is forwarded as None; the service turns it
        # into a 400 invalid_request response (covered by service tests).
        status, payload, _headers = await asyncio.to_thread(
            _request, f"{base}/memories/not-a-number"
        )
        assert status == 200
        assert payload["action"] == "get"
        assert payload["memory_id"] is None
    finally:
        server.stop()


@pytest.mark.asyncio
async def test_http_listener_enforces_body_limits_and_json():
    service = FakeService()
    service.max_request_bytes = 10
    server = await _start(service)
    base = f"http://127.0.0.1:{server.bound_port}{API_PATH_PREFIX}"
    auth = {"Authorization": "Bearer lm_token"}
    try:
        status, payload, _headers = await asyncio.to_thread(
            _request,
            f"{base}/recall",
            method="POST",
            headers={**auth, "Content-Type": "application/json"},
            body=b'{"query":"' + b"x" * 30 + b'"}',
        )
        assert status == 400
        assert payload["code"] == "request_too_large"
        assert service.calls == []

        status, payload, _headers = await asyncio.to_thread(
            _request,
            f"{base}/recall",
            method="POST",
            headers={**auth, "Content-Type": "application/json"},
            body=b"{not json",
        )
        assert status == 400
        assert payload["code"] == "invalid_request"
        assert service.calls == []
    finally:
        server.stop()


@pytest.mark.asyncio
async def test_http_listener_forwards_retry_after_header():
    service = FakeService()
    server = await _start(service)
    base = f"http://127.0.0.1:{server.bound_port}{API_PATH_PREFIX}"
    try:
        status, payload, headers = await asyncio.to_thread(
            _request,
            f"{base}/recall",
            method="POST",
            headers={
                "Authorization": "Bearer lm_token",
                "Content-Type": "application/json",
            },
            body=json.dumps({"query": "x", "simulate": "rate"}).encode(),
        )
        assert status == 429
        assert payload["code"] == "rate_limited"
        assert headers.get("Retry-After") == "7"
    finally:
        server.stop()


@pytest.mark.asyncio
async def test_http_listener_uses_forwarded_address_when_trusted():
    service = FakeService()
    service.trust_proxy_headers = True
    server = await _start(service)
    base = f"http://127.0.0.1:{server.bound_port}{API_PATH_PREFIX}"
    try:
        status, _payload, _headers = await asyncio.to_thread(
            _request,
            f"{base}/health",
            headers={
                "Authorization": "Bearer lm_token",
                "X-Forwarded-For": "203.0.113.9, 10.0.0.1",
            },
        )
        assert status == 200
        assert service.calls[-1]["remote_addr"] == "203.0.113.9"
    finally:
        server.stop()
