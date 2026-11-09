"""Small standalone HTTP listener for the external memory API.

AstrBot exposes plugin web APIs behind the dashboard authentication layer, so
external agent terminals cannot reach them with just a ``lm_...`` API key. This
module therefore serves the external endpoints from a minimal, dependency-free
HTTP listener (stdlib ``http.server``) that runs in a background thread and
forwards requests to :class:`core.external_api.ExternalApiService` on the plugin
event loop.

Routes (all prefixed with ``/page/external`` to match the Page API layout):

- ``GET  /page/external/health``
- ``GET  /page/external/personas``
- ``POST /page/external/recall``
- ``POST /page/external/memories``
- ``GET  /page/external/memories/<id>``
- ``POST /page/external/memories/<id>/archive``
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from astrbot.api import logger

from .external_api import ExternalApiService

DEFAULT_LISTEN_HOST = "127.0.0.1"
DEFAULT_LISTEN_PORT = 7801
API_PATH_PREFIX = "/page/external"
_REQUEST_TIMEOUT_SECONDS = 90


def _error_payload(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "code": code, "message": message}


class _ExternalApiRequestHandler(BaseHTTPRequestHandler):
    """Dispatch one HTTP request to the async service running on the plugin loop."""

    server_version = "LivingMemoryExternalAPI/1.0"
    sys_version = ""

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib handler naming
        self._send_json(
            204,
            {},
            {"Allow": "GET, POST, OPTIONS", "Content-Length": "0"},
        )

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler naming
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler naming
        self._handle("POST")

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib handler naming
        self._handle("DELETE")

    # ==================== request plumbing ====================

    def _handle(self, method: str) -> None:
        server: ExternalApiServer = self.server  # type: ignore[assignment]
        service = server.service
        loop = server.loop
        path = self.path.split("?", 1)[0].rstrip("/")
        route = (
            path[len(API_PATH_PREFIX) :] if path.startswith(API_PATH_PREFIX) else None
        )
        if route is None:
            self._send_json(404, _error_payload("not_found", "Not found"))
            return

        action, memory_id = _match_route(method, route)
        if action is None:
            self._send_json(404, _error_payload("not_found", "Not found"))
            return
        if action == "method_not_allowed":
            self._send_json(
                405,
                _error_payload("method_not_allowed", "Method not allowed"),
                {"Allow": "GET, POST"},
            )
            return

        body: dict[str, Any] = {}
        if method == "POST":
            body, error = self._read_json_body(service.max_request_bytes)
            if error is not None:
                self._send_json(error.status_code, error.payload, error.headers)
                return

        headers = {key.lower(): value for key, value in self.headers.items()}
        remote_addr = self._remote_addr(service)
        if loop is None or loop.is_closed():
            self._send_json(
                503, _error_payload("unavailable", "Service is shutting down")
            )
            return
        try:
            future = asyncio.run_coroutine_threadsafe(
                service.dispatch(
                    action,
                    headers=headers,
                    payload=body,
                    memory_id=memory_id,
                    remote_addr=remote_addr,
                ),
                loop,
            )
            response = future.result(timeout=_REQUEST_TIMEOUT_SECONDS)
        except TimeoutError:
            self._send_json(504, _error_payload("timeout", "Request timed out"))
            return
        except Exception as exc:  # pragma: no cover - transport-level failure
            logger.error("[ExternalAPI] HTTP 请求执行失败: %s", exc, exc_info=True)
            self._send_json(500, _error_payload("internal_error", "Internal error"))
            return
        self._send_json(
            response.status_code,
            response.payload,
            {**response.headers, "Cache-Control": "no-store"},
        )

    def _read_json_body(
        self, max_bytes: int
    ) -> tuple[dict[str, Any], _BodyError | None]:
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or 0)
        except (TypeError, ValueError):
            return {}, _BodyError(
                400, _error_payload("invalid_request", "Invalid Content-Length")
            )
        if length < 0:
            return {}, _BodyError(
                400, _error_payload("invalid_request", "Invalid Content-Length")
            )
        if length > max_bytes:
            return {}, _BodyError(
                400,
                _error_payload(
                    "request_too_large",
                    f"Request body must not exceed {max_bytes} bytes",
                ),
            )
        if length == 0:
            return {}, None
        try:
            data = self.rfile.read(length)
        except OSError:
            return {}, _BodyError(
                400, _error_payload("invalid_request", "Failed to read request body")
            )
        try:
            parsed = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}, _BodyError(
                400, _error_payload("invalid_request", "Request body must be JSON")
            )
        if not isinstance(parsed, dict):
            return {}, _BodyError(
                400, _error_payload("invalid_request", "Request body must be an object")
            )
        return parsed, None

    def _remote_addr(self, service: ExternalApiService) -> str | None:
        if service.trust_proxy_headers:
            forwarded = self.headers.get("X-Forwarded-For", "")
            first = forwarded.split(",", 1)[0].strip()
            if first:
                return first
        try:
            return str(self.client_address[0])
        except (IndexError, TypeError):  # pragma: no cover - client_address is a tuple
            return None

    def _send_json(
        self,
        status_code: int,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(str(key), str(value))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:  # pragma: no cover - client disconnected
            pass

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        logger.debug("[ExternalAPI] %s - %s", self.address_string(), format % args)


class _BodyError:
    """Internal transport error carrying a ready response payload."""

    __slots__ = ("payload", "status_code")

    def __init__(self, status_code: int, payload: dict[str, Any]):
        self.status_code = status_code
        self.payload = payload

    @property
    def headers(self) -> dict[str, str]:
        return {"Cache-Control": "no-store"}

    @property
    def retry_after(self) -> int | None:  # pragma: no cover - uniform interface
        return None


def _match_route(method: str, route: str) -> tuple[str | None, int | None]:
    """Map ``(method, route)`` to a service action and optional memory id."""
    if route in ("", "/"):
        return None, None
    normalized = f"/{route.strip('/')}"
    if normalized == "/health":
        return ("health", None) if method == "GET" else ("method_not_allowed", None)
    if normalized == "/personas":
        return ("personas", None) if method == "GET" else ("method_not_allowed", None)
    if normalized == "/recall":
        return ("recall", None) if method == "POST" else ("method_not_allowed", None)
    if normalized == "/memories":
        return ("write", None) if method == "POST" else ("method_not_allowed", None)
    if normalized.startswith("/memories/"):
        suffix = normalized[len("/memories/") :]
        if suffix.endswith("/archive") and method == "POST":
            memory_id = suffix[: -len("/archive")]
            return ("archive", _parse_memory_id(memory_id))
        if method == "GET":
            return ("get", _parse_memory_id(suffix))
        return "method_not_allowed", None
    return None, None


def _parse_memory_id(value: str) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


class ExternalApiServer:
    """Background HTTP listener bound to the external API service."""

    def __init__(
        self,
        service: ExternalApiService,
        *,
        host: str = DEFAULT_LISTEN_HOST,
        port: int = DEFAULT_LISTEN_PORT,
    ):
        """Prepare a listener; call :meth:`start` to bind and serve.

        Args:
            service: External API service executing authenticated requests.
            host: Bind address (keep loopback unless a reverse proxy is used).
            port: Bind port.
        """
        self.service = service
        self.host = str(host)
        self.port = int(port)
        self.loop: asyncio.AbstractEventLoop | None = None
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._server is not None and self._thread is not None

    @property
    def bound_port(self) -> int:
        """Actual bound port (useful when configured port is 0 in tests)."""
        if self._server is None:
            return self.port
        return int(self._server.server_address[1])

    def start(self) -> bool:
        """Bind and start the listener thread; log and return False on failure."""
        if self.running:
            return True
        self.loop = asyncio.get_running_loop()
        try:
            server = ThreadingHTTPServer(
                (self.host, self.port), _ExternalApiRequestHandler
            )
        except OSError as exc:
            logger.error("[ExternalAPI] 无法监听 %s:%s: %s", self.host, self.port, exc)
            return False
        server.daemon_threads = True
        server.service = self.service  # type: ignore[attr-defined]
        server.loop = self.loop  # type: ignore[attr-defined]
        thread = threading.Thread(
            target=server.serve_forever,
            name="livingmemory-external-api",
            daemon=True,
        )
        thread.start()
        self._server = server
        self._thread = thread
        logger.info(
            "[ExternalAPI] 外部记忆 API 已监听 http://%s:%s%s",
            self.host,
            self.bound_port,
            API_PATH_PREFIX,
        )
        return True

    def stop(self) -> None:
        """Stop serving and release the listening socket."""
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is not None:
            try:
                server.shutdown()
            except Exception as exc:  # pragma: no cover - shutdown best effort
                logger.warning("[ExternalAPI] 关闭监听失败: %s", exc)
            finally:
                server.server_close()
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)


__all__ = [
    "API_PATH_PREFIX",
    "DEFAULT_LISTEN_HOST",
    "DEFAULT_LISTEN_PORT",
    "ExternalApiServer",
]
