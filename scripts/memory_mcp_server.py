#!/usr/bin/env python3
"""MCP stdio bridge for the LivingMemory external memory API.

This bridge is dependency-free (Python standard library only) so agent
terminals such as Codex, WorkBuddy or Qoder can attach the plugin's memory as an
MCP server without installing anything.

Usage::

    LIVINGMEMORY_API_BASE=http://127.0.0.1:7801/page/external \
    LIVINGMEMORY_API_KEY=lm_xxx \
    python scripts/memory_mcp_server.py

The transport is MCP stdio JSON-RPC 2.0. ``stdout`` carries JSON-RPC messages
only; all diagnostics are written to ``stderr``.

Exposed tools:

- ``search_memory``   -> ``POST /recall``
- ``save_memory``     -> ``POST /memories``
- ``list_personas``   -> ``GET  /personas``
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "livingmemory-memory"
SERVER_VERSION = "1.0.0"
HTTP_TIMEOUT_SECONDS = 30.0
ENV_API_BASE = "LIVINGMEMORY_API_BASE"
ENV_API_KEY = "LIVINGMEMORY_API_KEY"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_memory",
        "description": "Search long-term memories stored by the LivingMemory plugin.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search text"},
                "k": {
                    "type": "integer",
                    "description": "Maximum results (1-20, default 5)",
                    "minimum": 1,
                    "maximum": 20,
                },
                "persona": {"type": "string", "description": "Optional persona id"},
                "scope": {
                    "type": "string",
                    "description": "Optional memory scope (session id)",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "save_memory",
        "description": "Store one memory through the LivingMemory write pipeline.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Memory text (<= 8000 chars)",
                },
                "topics": {"type": "array", "items": {"type": "string"}},
                "key_facts": {"type": "array", "items": {"type": "string"}},
                "importance": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "Importance 0-1 (default 0.5)",
                },
                "persona": {"type": "string"},
                "scope": {"type": "string"},
                "source": {
                    "type": "string",
                    "description": "Optional client/source label recorded in metadata",
                },
                "idempotency_key": {
                    "type": "string",
                    "description": "Client retry key; repeated calls return the same memory",
                },
            },
            "required": ["content"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_personas",
        "description": "List personas and memory scopes available to this API key.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
]


class BridgeError(Exception):
    """Transport or API error surfaced as an MCP tool error."""

    def __init__(self, message: str, *, code: str = "api_error"):
        super().__init__(message)
        self.code = code


def _log(message: str) -> None:
    print(f"[livingmemory-mcp] {message}", file=sys.stderr, flush=True)


def _api_base() -> str:
    base = os.environ.get(ENV_API_BASE, "").strip().rstrip("/")
    if not base:
        raise BridgeError(
            f"{ENV_API_BASE} is not set (e.g. http://127.0.0.1:7801/page/external)",
            code="configuration_error",
        )
    return base


def _api_key() -> str:
    key = os.environ.get(ENV_API_KEY, "").strip()
    if not key:
        raise BridgeError(f"{ENV_API_KEY} is not set", code="configuration_error")
    return key


def api_request(method: str, path: str, payload: dict | None = None) -> dict:
    """Call the plugin HTTP API and return the decoded JSON payload.

    Raises:
        BridgeError: Network failure, non-2xx response or invalid JSON.
    """
    url = f"{_api_base()}{path}"
    data = None
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Accept": "application/json",
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            error_payload = json.loads(exc.read().decode("utf-8"))
            detail = str(error_payload.get("message") or "")
            code = str(error_payload.get("code") or "http_error")
        except Exception:
            code = "http_error"
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        suffix = f" ({detail})" if detail else ""
        if retry_after:
            suffix += f"; retry after {retry_after}s"
        raise BridgeError(
            f"API request failed with HTTP {exc.code}{suffix}", code=code
        ) from exc
    except urllib.error.URLError as exc:
        raise BridgeError(
            f"Cannot reach LivingMemory API at {url}: {exc.reason}",
            code="network_error",
        ) from exc
    try:
        parsed = json.loads(body or "{}")
    except json.JSONDecodeError as exc:
        raise BridgeError("API returned invalid JSON", code="invalid_response") from exc
    if not isinstance(parsed, dict):
        raise BridgeError("API returned an unexpected payload", code="invalid_response")
    if parsed.get("status") == "error":
        raise BridgeError(
            str(parsed.get("message") or "API error"),
            code=str(parsed.get("code") or "api_error"),
        )
    return parsed


# ==================== tool implementations ====================


def tool_search_memory(arguments: dict) -> dict:
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise BridgeError("query must not be empty", code="invalid_request")
    payload: dict[str, Any] = {"query": query}
    if arguments.get("k") is not None:
        payload["k"] = int(arguments["k"])
    if arguments.get("persona"):
        payload["persona_id"] = str(arguments["persona"])
    if arguments.get("scope"):
        payload["scope"] = str(arguments["scope"])
    return api_request("POST", "/recall", payload)


def tool_save_memory(arguments: dict) -> dict:
    content = str(arguments.get("content") or "").strip()
    if not content:
        raise BridgeError("content must not be empty", code="invalid_request")
    payload: dict[str, Any] = {"content": content}
    for field in ("topics", "key_facts"):
        value = arguments.get(field)
        if value:
            if not isinstance(value, list):
                raise BridgeError(f"{field} must be a list", code="invalid_request")
            payload[field] = [str(item) for item in value]
    if arguments.get("importance") is not None:
        payload["importance"] = float(arguments["importance"])
    if arguments.get("persona"):
        payload["persona_id"] = str(arguments["persona"])
    if arguments.get("scope"):
        payload["scope"] = str(arguments["scope"])
    if arguments.get("source"):
        payload["source"] = str(arguments["source"])
    if arguments.get("idempotency_key"):
        payload["idempotency_key"] = str(arguments["idempotency_key"])
    return api_request("POST", "/memories", payload)


def tool_list_personas(_arguments: dict) -> dict:
    return api_request("GET", "/personas")


TOOL_HANDLERS = {
    "search_memory": tool_search_memory,
    "save_memory": tool_save_memory,
    "list_personas": tool_list_personas,
}


# ==================== JSON-RPC plumbing ====================


def _tool_result(payload: dict) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "isError": False,
    }


def _tool_error(error: BridgeError) -> dict:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {"error": str(error), "code": error.code}, ensure_ascii=False
                ),
            }
        ],
        "isError": True,
    }


def handle_message(message: dict) -> dict | None:
    """Handle one JSON-RPC message; return the response dict or None for notifications."""
    method = str(message.get("method") or "")
    request_id = message.get("id")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}

    if method == "notifications/initialized" or method.startswith("notifications/"):
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                },
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            error = BridgeError(f"unknown tool: {name}", code="unknown_tool")
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": _tool_error(error),
            }
        try:
            result = handler(arguments)
        except BridgeError as exc:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": _tool_error(exc),
            }
        except (TypeError, ValueError) as exc:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": _tool_error(BridgeError(str(exc), code="invalid_request")),
            }
        return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result(result)}

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> None:
    """Run the stdio JSON-RPC loop until stdin closes."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):  # pragma: no cover - exotic streams
        pass
    _log("bridge ready; waiting for JSON-RPC on stdin")
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {exc}"},
            }
            print(json.dumps(response, ensure_ascii=False), flush=True)
            continue
        if not isinstance(message, dict):
            continue
        response = handle_message(message)
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
