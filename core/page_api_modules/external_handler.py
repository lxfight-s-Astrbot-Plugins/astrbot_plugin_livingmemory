"""Page API adapter for the external memory API.

Two groups of routes live here:

- Dashboard management routes (host-authenticated): list/create/revoke API keys.
  The plaintext token is returned exactly once by ``create_key``.
- External client routes (Bearer ``lm_...`` key): personas/health/recall/write/
  get/archive. They are also reachable through the plugin's own listener in
  :mod:`core.external_api_server`; going through the Page API keeps a single
  implementation and lets dashboard-side tools verify the same contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from quart import request

from .utils import PageApiUtils


def _request_headers() -> dict[str, str]:
    try:
        headers = request.headers
    except RuntimeError:  # pragma: no cover - outside a request context
        return {}
    try:
        return {str(key): str(value) for key, value in headers.items()}
    except Exception:  # pragma: no cover - defensive for fake request objects
        return {}


def _request_remote_addr() -> str | None:
    try:
        return request.remote_addr
    except Exception:  # pragma: no cover - fake request objects
        return None


class ExternalHandler:
    """Handler for API-key management and Bearer-authenticated external routes."""

    def __init__(self, utils: PageApiUtils, plugin: Any):
        """Bind the handler to the plugin (used to build the API service lazily).

        Args:
            utils: Shared page API response helpers.
            plugin: Running ``LivingMemoryPlugin`` instance.
        """
        self.utils = utils
        self.plugin = plugin
        self._service: Any = None
        self._service_engine_id: int | None = None

    # ==================== service plumbing ====================

    def get_service(self, memory_engine: Any = None) -> Any:
        """Return (and cache) the external API service for the current engine."""
        from ..external_api import ExternalApiService

        engine = memory_engine
        if engine is None:
            initializer = getattr(self.plugin, "initializer", None)
            engine = getattr(initializer, "memory_engine", None)
        if engine is None:
            raise RuntimeError("记忆引擎尚未初始化")
        if self._service is not None and self._service_engine_id == id(engine):
            return self._service
        initializer = getattr(self.plugin, "initializer", None)
        data_dir = getattr(initializer, "data_dir", None) or str(
            Path(str(getattr(engine, "db_path", "."))).parent
        )
        config_manager = getattr(self.plugin, "config_manager", None) or getattr(
            initializer, "config_manager", None
        )
        service = ExternalApiService(config_manager, engine, data_dir)
        self._service = service
        self._service_engine_id = id(engine)
        return service

    async def _disabled_response(self, service: Any) -> dict[str, Any] | None:
        if service.enabled:
            return None
        return {
            "status": "error",
            "code": "not_found",
            "message": "Not found",
            "http_status": 404,
        }

    # ==================== Dashboard key management ====================

    async def list_keys(self, ready: dict[str, Any]) -> dict[str, Any]:
        """Return every API key (prefix and limits only, never the token)."""
        service = self.get_service(ready.get("memory_engine"))
        disabled = await self._disabled_response(service)
        if disabled is not None:
            return disabled
        keys = [key.to_public_dict() for key in await service.list_keys()]
        return self.utils.ok({"keys": keys, "total": len(keys)})

    async def create_key(self, ready: dict[str, Any]) -> dict[str, Any]:
        """Create one API key and return its plaintext token exactly once."""
        service = self.get_service(ready.get("memory_engine"))
        disabled = await self._disabled_response(service)
        if disabled is not None:
            return disabled
        payload = await request.get_json(silent=True) or {}
        name = str(payload.get("name") or "").strip()
        if not name:
            return {**self.utils.error("name 不能为空"), "code": "invalid_request"}
        mode = str(payload.get("mode") or "").strip().lower()
        scopes = payload.get("scopes")
        if isinstance(scopes, list):
            normalized_scopes = [str(item).strip().lower() for item in scopes]
        elif mode in ("write", "write_only"):
            normalized_scopes = ["read", "write"]
        elif mode == "read" or not mode:
            normalized_scopes = ["read"]
        else:
            return {
                **self.utils.error("mode 必须是 read 或 write"),
                "code": "invalid_request",
            }
        normalized_scopes = [
            scope for scope in ("read", "write") if scope in normalized_scopes
        ]
        if not normalized_scopes:
            return {
                **self.utils.error("scopes 至少包含 read"),
                "code": "invalid_request",
            }
        personas = self._string_list(payload.get("allowed_personas"))
        scopes_allow = self._string_list(payload.get("allowed_scopes"))
        try:
            rate_limit = self._optional_positive_int(payload.get("rate_limit_per_min"))
            write_limit = self._optional_positive_int(
                payload.get("write_limit_per_min")
            )
        except ValueError as exc:
            return {**self.utils.error(str(exc)), "code": "invalid_request"}
        try:
            record, token = await service.create_key(
                name,
                scopes=normalized_scopes,
                allowed_personas=personas,
                allowed_scopes=scopes_allow,
                rate_limit_per_min=rate_limit,
                write_limit_per_min=write_limit,
            )
        except Exception as exc:
            return {**self.utils.error(str(exc)), "code": "invalid_request"}
        return self.utils.ok({"key": record.to_public_dict(), "token": token})

    async def revoke_key(self, ready: dict[str, Any], key_id: str) -> dict[str, Any]:
        """Revoke one API key by numeric id (path parameter)."""
        service = self.get_service(ready.get("memory_engine"))
        disabled = await self._disabled_response(service)
        if disabled is not None:
            return disabled
        try:
            parsed_id = int(key_id)
        except (TypeError, ValueError):
            return {
                **self.utils.error("key_id 必须是整数"),
                "code": "invalid_request",
            }
        record = await service.revoke_key(parsed_id)
        if record is None:
            return {**self.utils.error("API key 不存在"), "code": "not_found"}
        return self.utils.ok({"key": record.to_public_dict()})

    async def list_audit(self, ready: dict[str, Any]) -> dict[str, Any]:
        """Return the newest external API audit records (sanitized, read-only)."""
        service = self.get_service(ready.get("memory_engine"))
        disabled = await self._disabled_response(service)
        if disabled is not None:
            return disabled
        try:
            limit = int(request.args.get("limit", 50))
        except (TypeError, ValueError):
            return {**self.utils.error("limit 必须是整数"), "code": "invalid_request"}
        limit = max(1, min(limit, 200))
        records = await service.recent_audit(limit)
        return self.utils.ok({"records": records, "total": len(records)})

    async def revoke_key_post(self, ready: dict[str, Any]) -> dict[str, Any]:
        """Revoke one API key via POST (iframe bridge only supports GET/POST)."""
        payload = await request.get_json(silent=True) or {}
        key_id = payload.get("id", payload.get("key_id"))
        if key_id is None:
            return {**self.utils.error("id 不能为空"), "code": "invalid_request"}
        return await self.revoke_key(ready, str(key_id))

    # ==================== Bearer-authenticated external routes ====================

    async def handle_health(self) -> dict[str, Any]:
        return await self._dispatch_external("health")

    async def handle_personas(self) -> dict[str, Any]:
        return await self._dispatch_external("personas")

    async def handle_recall(self) -> dict[str, Any]:
        return await self._dispatch_external("recall")

    async def handle_write_memory(self) -> dict[str, Any]:
        return await self._dispatch_external("write")

    async def handle_get_memory(self, memory_id: str) -> dict[str, Any]:
        return await self._dispatch_external("get", memory_id=memory_id)

    async def handle_archive_memory(self, memory_id: str) -> dict[str, Any]:
        return await self._dispatch_external("archive", memory_id=memory_id)

    async def _dispatch_external(
        self, action: str, *, memory_id: str | None = None
    ) -> dict[str, Any]:
        """Forward one external request through the shared service pipeline."""
        service = self.get_service()
        payload: dict[str, Any] = {}
        if action in ("recall", "write"):
            try:
                content_length = int(request.content_length or 0)
            except (AttributeError, TypeError, ValueError):
                content_length = 0
            if content_length > service.max_request_bytes:
                return {
                    "status": "error",
                    "code": "request_too_large",
                    "message": (
                        "Request body must not exceed "
                        f"{service.max_request_bytes} bytes"
                    ),
                    "http_status": 400,
                }
            parsed = await request.get_json(silent=True)
            payload = parsed if isinstance(parsed, dict) else {}
        parsed_memory_id = None
        if memory_id is not None:
            try:
                parsed_memory_id = int(memory_id)
            except (TypeError, ValueError):
                return {
                    "status": "error",
                    "code": "invalid_request",
                    "message": "memory_id must be a positive integer",
                    "http_status": 400,
                }
        response = await service.dispatch(
            action,
            headers=_request_headers(),
            payload=payload,
            memory_id=parsed_memory_id,
            remote_addr=_request_remote_addr(),
        )
        payload_out = dict(response.payload)
        payload_out.setdefault("http_status", response.status_code)
        return payload_out

    # ==================== helpers ====================

    @staticmethod
    def _string_list(value: Any) -> list[str] | None:
        if value is None:
            return None
        if isinstance(value, str):
            items = [item.strip() for item in value.split(",")]
        elif isinstance(value, list):
            items = [str(item).strip() for item in value]
        else:
            raise ValueError("列表字段必须是数组或逗号分隔字符串")
        cleaned = [item for item in items if item]
        return cleaned or None

    @staticmethod
    def _optional_positive_int(value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("限流值必须是整数") from exc
        if parsed < 1:
            raise ValueError("限流值必须大于 0")
        return parsed


__all__ = ["ExternalHandler"]
