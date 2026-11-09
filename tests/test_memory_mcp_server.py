"""End-to-end tests for the stdlib MCP bridge (scripts/memory_mcp_server.py)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "scripts" / "memory_mcp_server.py"


class _FakeApiHandler(BaseHTTPRequestHandler):
    requests: list[dict] = []
    lock = threading.Lock()

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler naming
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler naming
        self._handle("POST")

    def _handle(self, method: str) -> None:
        raw_length = int(self.headers.get("Content-Length") or 0)
        raw_body = self.rfile.read(raw_length) if raw_length else b""
        payload = json.loads(raw_body) if raw_body else {}
        record = {
            "method": method,
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "payload": payload,
        }
        with self.lock:
            type(self).requests.append(record)

        if self.path == "/page/external/personas":
            self._json(200, {"status": "ok", "personas": ["p1"], "scopes": ["s1"]})
            return
        if self.path == "/page/external/recall":
            self._json(
                200,
                {
                    "status": "ok",
                    "items": [{"memory_id": 3, "content": "found"}],
                    "total": 1,
                },
            )
            return
        if self.path == "/page/external/memories":
            if payload.get("content") == "boom":
                self.send_response(429)
                self.send_header("Retry-After", "9")
                body = json.dumps(
                    {"status": "error", "code": "rate_limited", "message": "slow down"}
                ).encode()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self._json(200, {"status": "ok", "memory_id": 7, "created": True})
            return
        self._json(404, {"status": "error", "code": "not_found", "message": "nope"})

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass


def _start_fake_api() -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeApiHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    return server, thread, f"http://127.0.0.1:{port}/page/external"


def _run_bridge(messages: list[dict], api_base: str) -> list[dict]:
    stdin_data = "\n".join(json.dumps(message) for message in messages) + "\n"
    env = {
        **os.environ,
        "LIVINGMEMORY_API_BASE": api_base,
        "LIVINGMEMORY_API_KEY": "lm_test-token",
    }
    process = subprocess.run(
        [sys.executable, str(BRIDGE)],
        input=stdin_data,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        env=env,
        cwd=ROOT,
    )
    assert process.returncode == 0, process.stderr
    responses = []
    for line in process.stdout.splitlines():
        line = line.strip()
        if line:
            responses.append(json.loads(line))
    return responses


def _messages() -> list[dict]:
    return [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "search_memory",
                "arguments": {"query": "hello", "k": 3},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "save_memory",
                "arguments": {
                    "content": "remember this",
                    "topics": ["ops"],
                    "idempotency_key": "abc",
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "list_personas", "arguments": {}},
        },
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "unknown_tool", "arguments": {}},
        },
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "save_memory", "arguments": {"content": "boom"}},
        },
        {"jsonrpc": "2.0", "id": 8, "method": "ping"},
    ]


def test_bridge_tools_forward_to_http_api_and_map_errors():
    _FakeApiHandler.requests = []
    server, thread, api_base = _start_fake_api()
    try:
        responses = _run_bridge(_messages(), api_base)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    by_id = {response["id"]: response for response in responses}
    assert set(by_id) == {1, 2, 3, 4, 5, 6, 7, 8}

    initialize = by_id[1]["result"]
    assert initialize["protocolVersion"] == "2024-11-05"
    assert initialize["serverInfo"]["name"] == "livingmemory-memory"
    assert "tools" in initialize["capabilities"]

    assert {tool["name"] for tool in by_id[2]["result"]["tools"]} == {
        "search_memory",
        "save_memory",
        "list_personas",
    }

    search = json.loads(by_id[3]["result"]["content"][0]["text"])
    assert search["items"][0]["memory_id"] == 3
    assert by_id[3]["result"]["isError"] is False

    save = json.loads(by_id[4]["result"]["content"][0]["text"])
    assert save == {"status": "ok", "memory_id": 7, "created": True}
    personas = json.loads(by_id[5]["result"]["content"][0]["text"])
    assert personas["personas"] == ["p1"]

    unknown = by_id[6]["result"]
    assert unknown["isError"] is True
    assert "unknown_tool" in unknown["content"][0]["text"]

    rate_limited = by_id[7]["result"]
    assert rate_limited["isError"] is True
    assert "rate_limited" in rate_limited["content"][0]["text"]

    assert by_id[8]["result"] == {}

    paths = [
        (request["method"], request["path"]) for request in _FakeApiHandler.requests
    ]
    assert ("POST", "/page/external/recall") in paths
    assert ("POST", "/page/external/memories") in paths
    assert ("GET", "/page/external/personas") in paths
    for request in _FakeApiHandler.requests:
        assert request["authorization"] == "Bearer lm_test-token"
    save_request = next(
        request
        for request in _FakeApiHandler.requests
        if request["method"] == "POST"
        and request["payload"].get("content") == "remember this"
    )
    assert save_request["payload"]["topics"] == ["ops"]
    assert save_request["payload"]["idempotency_key"] == "abc"


def test_bridge_reports_missing_configuration_as_tool_error():
    _FakeApiHandler.requests = []
    server, thread, api_base = _start_fake_api()
    try:
        stdin_data = (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "list_personas", "arguments": {}},
                }
            )
            + "\n"
        )
        env = {**os.environ, "LIVINGMEMORY_API_BASE": api_base}
        env.pop("LIVINGMEMORY_API_KEY", None)
        process = subprocess.run(
            [sys.executable, str(BRIDGE)],
            input=stdin_data,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            env=env,
            cwd=ROOT,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert process.returncode == 0
    response = json.loads(process.stdout.strip().splitlines()[-1])
    result = response["result"]
    assert result["isError"] is True
    assert "LIVINGMEMORY_API_KEY" in result["content"][0]["text"]
    assert _FakeApiHandler.requests == []
