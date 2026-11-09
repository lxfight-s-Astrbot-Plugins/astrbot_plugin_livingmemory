"""External memory API: authentication, rate limiting, audit and operations.

The external API lets agent terminals (Codex / WorkBuddy / Qoder, or the bundled
MCP bridge in ``scripts/memory_mcp_server.py``) read and write memories through
the plugin's own HTTP listener. It is disabled by default and reuses the stable
``MemoryEngine`` write/recall paths so dedup, embeddings, graph and atom indexes
behave exactly like in-chat writes.

Design notes:

- Only the SHA-256 hash of an API key is stored; the plaintext ``lm_...`` token
  is returned once at creation time.
- Rate limiting uses per-key in-memory token buckets (request bucket + write
  bucket) so it never adds a database write per request.
- Every request is appended to a JSONL audit log next to the plugin database.
  Content and plaintext keys are never written to that log.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from astrbot.api import logger

from ..storage.external_api_store import (
    ExternalApiKey,
    ExternalApiStore,
    ExternalApiStoreError,
    hash_api_key,
)
from .managers.backup_manager import PLUGIN_VERSION
from .memory_scope import GLOBAL_MEMORY_SCOPE

DEFAULT_MAX_REQUEST_BYTES = 262144
DEFAULT_RATE_LIMIT_PER_MIN = 60
DEFAULT_WRITE_LIMIT_PER_MIN = 20
DEFAULT_AUDIT_RETENTION_DAYS = 30
MAX_CONTENT_CHARS = 8000
MAX_QUERY_CHARS = 2000
MAX_K = 20
MAX_LIST_FIELD_ITEMS = 50
MAX_SOURCE_CHARS = 200
MAX_IDEMPOTENCY_KEY_CHARS = 200

READ_ACTIONS = frozenset({"personas", "health", "recall", "get"})
WRITE_ACTIONS = frozenset({"write", "archive"})


class ExternalApiError(Exception):
    """Structured API error mapped to a JSON error response and HTTP status."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        retry_after: int | None = None,
    ):
        super().__init__(message)
        self.status_code = int(status_code)
        self.code = str(code)
        self.message = str(message)
        self.retry_after = retry_after


@dataclass(slots=True)
class ExternalResponse:
    """One external API response: JSON payload plus transport metadata."""

    payload: dict[str, Any]
    status_code: int = 200
    headers: dict[str, str] = field(default_factory=dict)

    def with_audit_memory_id(self, memory_id: int | None) -> ExternalResponse:
        """Attach the affected memory id for audit logging (not serialized)."""
        self.payload.setdefault("_audit_memory_id", memory_id)
        return self


def _ok(payload: dict[str, Any]) -> ExternalResponse:
    return ExternalResponse({"status": "ok", **payload}, 200)


def _error(
    status_code: int, code: str, message: str, *, retry_after: int | None = None
) -> ExternalResponse:
    headers: dict[str, str] = {}
    if retry_after is not None:
        headers["Retry-After"] = str(int(retry_after))
    payload: dict[str, Any] = {"status": "error", "code": code, "message": message}
    if retry_after is not None:
        payload["retry_after"] = int(retry_after)
    return ExternalResponse(payload, status_code, headers)


def _header(headers: Mapping[str, str] | None, name: str) -> str:
    if not headers:
        return ""
    lowered = name.lower()
    for key, value in headers.items():
        if str(key).lower() == lowered:
            return str(value or "")
    return ""


def _optional_text(value: Any, field_name: str, max_chars: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ExternalApiError(400, "invalid_request", f"{field_name} must be a string")
    text = value.strip()
    if not text:
        return None
    if len(text) > max_chars:
        raise ExternalApiError(
            400,
            "invalid_request",
            f"{field_name} must be at most {max_chars} characters",
        )
    return text


def _string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ExternalApiError(400, "invalid_request", f"{field_name} must be a list")
    if len(value) > MAX_LIST_FIELD_ITEMS:
        raise ExternalApiError(
            400,
            "invalid_request",
            f"{field_name} must have at most {MAX_LIST_FIELD_ITEMS} items",
        )
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ExternalApiError(
                400, "invalid_request", f"{field_name} items must be non-empty strings"
            )
        result.append(item.strip())
    return result


class _TokenBucket:
    """Minimal token bucket: ``capacity`` burst, refilled at ``rate_per_second``."""

    __slots__ = ("capacity", "rate_per_second", "tokens", "updated_at")

    def __init__(self, capacity: float, rate_per_second: float, now: float):
        self.capacity = max(0.0, float(capacity))
        self.rate_per_second = max(0.0, float(rate_per_second))
        self.tokens = self.capacity
        self.updated_at = now

    def _refill(self, now: float) -> None:
        if now > self.updated_at and self.rate_per_second > 0:
            self.tokens = min(
                self.capacity,
                self.tokens + (now - self.updated_at) * self.rate_per_second,
            )
        self.updated_at = max(self.updated_at, now)

    def consume(self, now: float) -> float | None:
        """Consume one token; return ``None`` or the seconds until the next one."""
        if self.capacity <= 0 or self.rate_per_second <= 0:
            return None
        self._refill(now)
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return None
        return (1.0 - self.tokens) / self.rate_per_second


class ExternalRateLimiter:
    """Per-key in-memory token buckets for request and write limits."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._buckets: dict[tuple[int, str], _TokenBucket] = {}

    def reset(self) -> None:
        """Drop all buckets (used by tests and plugin reload)."""
        self._buckets.clear()

    def check(self, key: ExternalApiKey, *, write: bool) -> tuple[bool, int | None]:
        """Consume request (and write) quota.

        Args:
            key: Authenticated key with its configured limits.
            write: Whether the action also consumes the write bucket.

        Returns:
            ``(allowed, retry_after_seconds)``; ``retry_after`` is set on denial.
        """
        now = self._clock()
        denied = self._consume(key, "request", key.rate_limit_per_min, now)
        if denied is not None:
            return False, max(1, math.ceil(denied))
        if write:
            denied = self._consume(key, "write", key.write_limit_per_min, now)
            if denied is not None:
                return False, max(1, math.ceil(denied))
        return True, None

    def _consume(
        self, key: ExternalApiKey, kind: str, limit_per_min: int, now: float
    ) -> float | None:
        limit = int(limit_per_min or 0)
        if limit <= 0:
            return None
        bucket_id = (key.id, kind)
        bucket = self._buckets.get(bucket_id)
        if bucket is None:
            bucket = _TokenBucket(limit, limit / 60.0, now)
            self._buckets[bucket_id] = bucket
        return bucket.consume(now)


class ExternalAuditLog:
    """Append-only JSONL audit log with age-based retention trimming."""

    def __init__(
        self,
        path: Path,
        *,
        retention_days: int = DEFAULT_AUDIT_RETENTION_DAYS,
        clock=time.time,
    ):
        self.path = Path(path)
        self.retention_days = int(retention_days)
        self._clock = clock
        self._lock = asyncio.Lock()
        self._records_since_prune = 0

    async def record(self, entry: dict[str, Any]) -> None:
        """Append one sanitized audit record; trims the file periodically."""
        payload = {"ts": self._clock(), **entry}
        async with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            except OSError as exc:
                logger.warning("[ExternalAPI] 写入审计日志失败: %s", exc)
                return
            self._records_since_prune += 1
            if self._records_since_prune >= 200:
                await self._prune_locked()

    async def prune(self) -> int:
        """Drop records older than the configured retention window."""
        async with self._lock:
            return await self._prune_locked()

    async def _prune_locked(self) -> int:
        self._records_since_prune = 0
        if self.retention_days <= 0 or not self.path.exists():
            return 0
        cutoff = self._clock() - self.retention_days * 86400
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("[ExternalAPI] 读取审计日志失败: %s", exc)
            return 0
        kept: list[str] = []
        removed = 0
        for line in lines:
            try:
                record = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                removed += 1
                continue
            if isinstance(record, dict) and float(record.get("ts", 0)) >= cutoff:
                kept.append(line)
            else:
                removed += 1
        if removed:
            temp_path = self.path.with_suffix(".jsonl.tmp")
            try:
                temp_path.write_text(
                    "\n".join(kept) + ("\n" if kept else ""), encoding="utf-8"
                )
                temp_path.replace(self.path)
            except OSError as exc:
                logger.warning("[ExternalAPI] 清理审计日志失败: %s", exc)
                return 0
        return removed

    def read_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the newest audit records, oldest first within the window."""
        if limit <= 0 or not self.path.exists():
            return []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except OSError as exc:
            logger.warning("[ExternalAPI] 读取审计日志失败: %s", exc)
            return []
        records: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                record = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(record, dict):
                records.append(record)
        return records


class ExternalApiService:
    """Business logic behind the external HTTP endpoints and key management."""

    def __init__(
        self,
        config_manager: Any,
        memory_engine: Any,
        data_dir: str | Path,
        *,
        clock=time.monotonic,
    ):
        """Bind the service to the running plugin components.

        Args:
            config_manager: Plugin ``ConfigManager`` (provides ``external_api.*``).
            memory_engine: Initialized ``MemoryEngine`` used for reads and writes.
            data_dir: Plugin data directory that receives the audit JSONL file.
            clock: Monotonic clock used by rate limiting (injectable in tests).
        """
        self.config_manager = config_manager
        self.memory_engine = memory_engine
        self.data_dir = Path(data_dir)
        db_path = str(getattr(memory_engine, "db_path", "") or "")
        self.store = ExternalApiStore(db_path) if db_path else None
        self.rate_limiter = ExternalRateLimiter(clock=clock)
        self._clock = clock
        self._audit = self._build_audit_log()
        self._initialized = False
        self._init_lock = asyncio.Lock()

    # ==================== 配置 ====================

    def _config(self, key: str, default: Any) -> Any:
        getter = getattr(self.config_manager, "get", None)
        if not callable(getter):
            return default
        try:
            value = getter(f"external_api.{key}", default)
        except Exception:
            return default
        return default if value is None else value

    @property
    def enabled(self) -> bool:
        """Whether the external API is enabled by configuration."""
        return bool(self._config("enabled", False))

    @property
    def max_request_bytes(self) -> int:
        """Maximum accepted request body size in bytes."""
        return max(1, int(self._config("max_request_bytes", DEFAULT_MAX_REQUEST_BYTES)))

    @property
    def default_rate_limit_per_min(self) -> int:
        return max(
            0,
            int(self._config("default_rate_limit_per_min", DEFAULT_RATE_LIMIT_PER_MIN)),
        )

    @property
    def default_write_limit_per_min(self) -> int:
        return max(
            0,
            int(
                self._config("default_write_limit_per_min", DEFAULT_WRITE_LIMIT_PER_MIN)
            ),
        )

    @property
    def audit_retention_days(self) -> int:
        return int(self._config("audit_retention_days", DEFAULT_AUDIT_RETENTION_DAYS))

    @property
    def trust_proxy_headers(self) -> bool:
        return bool(self._config("trust_proxy_headers", False))

    def _build_audit_log(self) -> ExternalAuditLog:
        return ExternalAuditLog(
            self.data_dir / "external_api_audit.jsonl",
            retention_days=self.audit_retention_days,
        )

    # ==================== 初始化 ====================

    async def ensure_initialized(self) -> None:
        """Create the key/idempotency tables lazily and prune old audit rows."""
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            if self.store is None:
                raise ExternalApiError(
                    500, "internal_error", "memory engine is not available"
                )
            await self.store.initialize()
            self._audit.retention_days = self.audit_retention_days
            await self._audit.prune()
            self._initialized = True

    # ==================== Key 管理 ====================

    async def create_key(
        self,
        name: str,
        *,
        scopes: list[str],
        allowed_personas: list[str] | None = None,
        allowed_scopes: list[str] | None = None,
        rate_limit_per_min: int | None = None,
        write_limit_per_min: int | None = None,
    ) -> tuple[ExternalApiKey, str]:
        """Create one API key and return its one-time plaintext token."""
        await self.ensure_initialized()
        if self.store is None:  # pragma: no cover - guarded by ensure_initialized
            raise ExternalApiStoreError("memory engine is not available")
        return await self.store.create_key(
            name,
            scopes=scopes,
            allowed_personas=allowed_personas,
            allowed_scopes=allowed_scopes,
            rate_limit_per_min=(
                self.default_rate_limit_per_min
                if rate_limit_per_min is None
                else int(rate_limit_per_min)
            ),
            write_limit_per_min=(
                self.default_write_limit_per_min
                if write_limit_per_min is None
                else int(write_limit_per_min)
            ),
        )

    async def recent_audit(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the newest sanitized audit records for the dashboard."""
        return self._audit.read_recent(limit)

    async def list_keys(self) -> list[ExternalApiKey]:
        """List all API keys (never includes token hashes)."""
        await self.ensure_initialized()
        if self.store is None:  # pragma: no cover - guarded by ensure_initialized
            return []
        return await self.store.list_keys()

    async def revoke_key(self, identifier: str | int) -> ExternalApiKey | None:
        """Revoke one key by id or name."""
        await self.ensure_initialized()
        if self.store is None:  # pragma: no cover - guarded by ensure_initialized
            return None
        return await self.store.revoke_key(identifier)

    # ==================== 请求处理 ====================

    async def dispatch(
        self,
        action: str,
        *,
        headers: Mapping[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        memory_id: int | None = None,
        remote_addr: str | None = None,
    ) -> ExternalResponse:
        """Authenticate, authorize, rate-limit and execute one external action.

        Args:
            action: One of ``personas`` / ``health`` / ``recall`` / ``write`` /
                ``get`` / ``archive``.
            headers: Incoming HTTP headers (only ``Authorization`` is consumed).
            payload: Parsed JSON body for POST actions.
            memory_id: Target memory id for path-based actions.
            remote_addr: Client address for the audit log.

        Returns:
            Structured response with JSON payload and HTTP status code.
        """
        started = self._clock()
        action = str(action or "")
        audit: dict[str, Any] = {
            "action": action,
            "key_id": None,
            "key_name": None,
            "memory_id": None,
            "status_code": None,
            "remote_addr": remote_addr,
        }
        response: ExternalResponse | None = None
        try:
            if not self.enabled:
                response = _error(404, "not_found", "Not found")
                return response
            if action not in READ_ACTIONS and action not in WRITE_ACTIONS:
                raise ExternalApiError(404, "not_found", "Not found")

            await self.ensure_initialized()
            key, auth_error = await self._authenticate(headers)
            if auth_error is not None:
                response = auth_error
                return auth_error
            if key is None:  # pragma: no cover - defensive, authenticate covers this
                raise ExternalApiError(401, "unauthorized", "Invalid API key")
            audit["key_id"] = key.id
            audit["key_name"] = key.name

            if action in WRITE_ACTIONS and not key.can_write():
                raise ExternalApiError(
                    403, "forbidden", "This API key does not have the 'write' scope"
                )
            if action in READ_ACTIONS and not key.can_read():
                raise ExternalApiError(
                    403, "forbidden", "This API key does not have the 'read' scope"
                )

            allowed, retry_after = self.rate_limiter.check(
                key, write=action in WRITE_ACTIONS
            )
            if not allowed:
                raise ExternalApiError(
                    429,
                    "rate_limited",
                    "Rate limit exceeded",
                    retry_after=retry_after,
                )

            if action == "health":
                response = _ok(self._health_payload(key))
            elif action == "personas":
                response = _ok(await self._personas_payload(key))
            elif action == "recall":
                response = _ok(await self._recall_payload(key, payload or {}))
            elif action == "write":
                response = _ok(await self._write_payload(key, payload or {}))
            elif action == "get":
                response = _ok(await self._get_payload(key, memory_id))
            else:
                response = _ok(await self._archive_payload(key, memory_id))

            audit["memory_id"] = response.payload.pop("_audit_memory_id", None)
            await self._touch_key(key)
            return response
        except ExternalApiError as exc:
            response = _error(
                exc.status_code,
                exc.code,
                exc.message,
                retry_after=exc.retry_after,
            )
            audit["status_code"] = response.status_code
            return response
        except Exception as exc:  # pragma: no cover - defensive boundary
            logger.error("[ExternalAPI] 请求处理失败: %s", exc, exc_info=True)
            response = _error(500, "internal_error", "Internal server error")
            audit["status_code"] = response.status_code
            return response
        finally:
            if response is not None:
                audit["status_code"] = response.status_code
            audit.setdefault("latency_ms", 0)
            audit["latency_ms"] = round((self._clock() - started) * 1000.0, 2)
            await self._record_audit(audit)

    async def _authenticate(
        self, headers: Mapping[str, str] | None
    ) -> tuple[ExternalApiKey | None, ExternalResponse | None]:
        authorization = _header(headers, "authorization").strip()
        if not authorization.lower().startswith("bearer "):
            return None, self._unauthorized("Missing bearer token")
        token = authorization[7:].strip()
        if not token.startswith("lm_") or self.store is None:
            return None, self._unauthorized("Invalid API key")
        record = await self.store.find_active_key_by_hash(hash_api_key(token))
        if record is None:
            return None, self._unauthorized("Invalid or revoked API key")
        return record, None

    @staticmethod
    def _unauthorized(message: str) -> ExternalResponse:
        response = _error(401, "unauthorized", message)
        response.headers["WWW-Authenticate"] = "Bearer"
        return response

    async def _touch_key(self, key: ExternalApiKey) -> None:
        if self.store is None:  # pragma: no cover - store guaranteed by dispatch
            return
        try:
            await self.store.touch_last_used(key.id)
        except Exception as exc:  # pragma: no cover - observability only
            logger.warning("[ExternalAPI] 更新 last_used_at 失败: %s", exc)

    async def _record_audit(self, audit: dict[str, Any]) -> None:
        try:
            await self._audit.record(dict(audit))
        except Exception as exc:  # pragma: no cover - observability only
            logger.warning("[ExternalAPI] 记录审计失败: %s", exc)

    # ==================== 作用域/人格约束 ====================

    def _resolve_persona(self, key: ExternalApiKey, value: Any) -> str | None:
        persona = _optional_text(value, "persona_id", 200)
        allowed = key.allowed_personas
        if allowed:
            if persona is None:
                return allowed[0]
            if persona not in allowed:
                raise ExternalApiError(
                    403, "forbidden", "persona_id is outside this key's allowed list"
                )
        return persona

    def _resolve_scope(
        self, key: ExternalApiKey, value: Any, *, for_write: bool
    ) -> str | None:
        scope = _optional_text(value, "scope", 200)
        allowed = key.allowed_scopes
        if allowed:
            if scope is None:
                return allowed[0]
            if scope not in allowed:
                raise ExternalApiError(
                    403, "forbidden", "scope is outside this key's allowed list"
                )
            return scope
        if scope is None and for_write:
            return GLOBAL_MEMORY_SCOPE
        return scope

    def _memory_visible(self, key: ExternalApiKey, metadata: dict[str, Any]) -> bool:
        if key.allowed_personas:
            persona = metadata.get("persona_id")
            if persona not in key.allowed_personas:
                return False
        if key.allowed_scopes:
            scope = metadata.get("session_id")
            if scope not in key.allowed_scopes:
                return False
        return True

    # ==================== 具体动作 ====================

    def _health_payload(self, key: ExternalApiKey) -> dict[str, Any]:
        return {
            "plugin_version": PLUGIN_VERSION,
            "ready": self.memory_engine is not None,
            "scopes": list(key.scopes),
            "key": {"id": key.id, "name": key.name},
        }

    async def _personas_payload(self, key: ExternalApiKey) -> dict[str, Any]:
        personas, scopes = await self.list_personas_and_scopes()
        if key.allowed_personas:
            personas = [item for item in personas if item in key.allowed_personas]
        if key.allowed_scopes:
            scopes = [item for item in scopes if item in key.allowed_scopes]
        return {
            "personas": personas,
            "scopes": scopes,
            "allowed_personas": list(key.allowed_personas or []),
            "allowed_scopes": list(key.allowed_scopes or []),
        }

    async def list_personas_and_scopes(self) -> tuple[list[str], list[str]]:
        """Return active persona/scope values discovered in the memory store."""
        connection = getattr(self.memory_engine, "db_connection", None)
        if connection is None:
            return [], []
        try:
            cursor = await connection.execute(
                """
                SELECT DISTINCT json_extract(metadata, '$.persona_id')
                FROM documents
                WHERE COALESCE(json_extract(metadata, '$.status'), 'active') = 'active'
                  AND json_extract(metadata, '$.persona_id') IS NOT NULL
                ORDER BY 1 LIMIT 200
                """
            )
            personas = [str(row[0]) for row in await cursor.fetchall() if row[0]]
            cursor = await connection.execute(
                """
                SELECT DISTINCT json_extract(metadata, '$.session_id')
                FROM documents
                WHERE COALESCE(json_extract(metadata, '$.status'), 'active') = 'active'
                  AND json_extract(metadata, '$.session_id') IS NOT NULL
                ORDER BY 1 LIMIT 200
                """
            )
            scopes = [str(row[0]) for row in await cursor.fetchall() if row[0]]
            return personas, scopes
        except Exception as exc:
            logger.warning("[ExternalAPI] 读取人格/作用域列表失败: %s", exc)
            return [], []

    async def _recall_payload(
        self, key: ExternalApiKey, payload: dict[str, Any]
    ) -> dict[str, Any]:
        query = _optional_text(payload.get("query"), "query", MAX_QUERY_CHARS)
        if not query:
            raise ExternalApiError(400, "invalid_request", "query must not be empty")
        k_value = payload.get("k", 5)
        try:
            k = int(k_value)
        except (TypeError, ValueError) as exc:
            raise ExternalApiError(
                400, "invalid_request", "k must be an integer"
            ) from exc
        if k < 1 or k > MAX_K:
            raise ExternalApiError(
                400, "invalid_request", f"k must be between 1 and {MAX_K}"
            )
        persona_id = self._resolve_persona(key, payload.get("persona_id"))
        scope = self._resolve_scope(key, payload.get("scope"), for_write=False)

        results = await self.memory_engine.search_memories(
            query=query, k=k, session_id=scope, persona_id=persona_id
        )
        items: list[dict[str, Any]] = []
        for result in results or []:
            metadata = getattr(result, "metadata", None) or {}
            if not self._memory_visible(key, metadata):
                continue
            items.append(
                {
                    "memory_id": int(result.doc_id),
                    "content": result.content,
                    "score": round(float(result.final_score), 4),
                    "importance": float(metadata.get("importance", 0.5) or 0.5),
                    "create_time": metadata.get("create_time"),
                    "session_id": metadata.get("session_id"),
                    "persona_id": metadata.get("persona_id"),
                }
            )
        return {"items": items, "total": len(items)}

    async def _write_payload(
        self, key: ExternalApiKey, payload: dict[str, Any]
    ) -> dict[str, Any]:
        content = _optional_text(payload.get("content"), "content", MAX_CONTENT_CHARS)
        if not content:
            raise ExternalApiError(400, "invalid_request", "content must not be empty")
        topics = _string_list(payload.get("topics"), "topics")
        key_facts = _string_list(payload.get("key_facts"), "key_facts")
        importance_value = payload.get("importance", 0.5)
        try:
            importance = float(importance_value)
        except (TypeError, ValueError) as exc:
            raise ExternalApiError(
                400, "invalid_request", "importance must be a number"
            ) from exc
        if not 0.0 <= importance <= 1.0:
            raise ExternalApiError(
                400, "invalid_request", "importance must be between 0 and 1"
            )
        persona_id = self._resolve_persona(key, payload.get("persona_id"))
        scope = self._resolve_scope(key, payload.get("scope"), for_write=True)
        source = _optional_text(payload.get("source"), "source", MAX_SOURCE_CHARS)
        idempotency_key = _optional_text(
            payload.get("idempotency_key"),
            "idempotency_key",
            MAX_IDEMPOTENCY_KEY_CHARS,
        )

        if idempotency_key and self.store is not None:
            existing_id = await self.store.get_idempotency(idempotency_key)
            if existing_id is not None:
                return {
                    "memory_id": int(existing_id),
                    "created": False,
                    "_audit_memory_id": int(existing_id),
                }

        metadata: dict[str, Any] = {
            "source": "external_api",
            "external_client": key.name,
            "external_key_id": key.id,
        }
        if topics:
            metadata["topics"] = topics
        if key_facts:
            metadata["key_facts"] = key_facts
        if source:
            metadata["external_source"] = source

        memory_id = await self.memory_engine.add_memory(
            content,
            session_id=scope,
            persona_id=persona_id,
            importance=importance,
            metadata=metadata,
        )
        memory_id = int(memory_id)
        if idempotency_key and self.store is not None:
            winner = await self.store.save_idempotency(idempotency_key, memory_id)
            if winner != memory_id:
                return {
                    "memory_id": winner,
                    "created": False,
                    "_audit_memory_id": winner,
                }
        return {"memory_id": memory_id, "created": True, "_audit_memory_id": memory_id}

    async def _load_memory(
        self, key: ExternalApiKey, memory_id: int | None
    ) -> tuple[int, dict[str, Any]]:
        try:
            parsed_id = int(memory_id)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ExternalApiError(
                400, "invalid_request", "memory_id must be a positive integer"
            ) from exc
        if parsed_id <= 0:
            raise ExternalApiError(
                400, "invalid_request", "memory_id must be a positive integer"
            )
        memory = await self.memory_engine.get_memory(parsed_id)
        if not memory:
            raise ExternalApiError(404, "not_found", "Memory not found")
        metadata = memory.get("metadata") or {}
        if not self._memory_visible(key, metadata):
            raise ExternalApiError(
                403, "forbidden", "Memory is outside this key's allowed scope"
            )
        return parsed_id, memory

    async def _get_payload(
        self, key: ExternalApiKey, memory_id: int | None
    ) -> dict[str, Any]:
        parsed_id, memory = await self._load_memory(key, memory_id)
        metadata = memory.get("metadata") or {}
        return {
            "memory": {
                "memory_id": parsed_id,
                "content": memory.get("text", ""),
                "status": metadata.get("status", "active"),
                "importance": metadata.get("importance", 0.5),
                "create_time": metadata.get("create_time"),
                "session_id": metadata.get("session_id"),
                "persona_id": metadata.get("persona_id"),
                "topics": metadata.get("topics") or [],
                "key_facts": metadata.get("key_facts") or [],
                "source": metadata.get("source"),
                "external_client": metadata.get("external_client"),
            },
            "_audit_memory_id": parsed_id,
        }

    async def _archive_payload(
        self, key: ExternalApiKey, memory_id: int | None
    ) -> dict[str, Any]:
        parsed_id, _memory = await self._load_memory(key, memory_id)
        archived = await self.memory_engine.archive_memories(
            [parsed_id],
            audit={
                "archived_by": "external_api",
                "external_client": key.name,
                "external_key_id": key.id,
            },
        )
        return {
            "memory_id": parsed_id,
            "archived": bool(archived),
            "_audit_memory_id": parsed_id,
        }


__all__ = [
    "DEFAULT_AUDIT_RETENTION_DAYS",
    "DEFAULT_MAX_REQUEST_BYTES",
    "DEFAULT_RATE_LIMIT_PER_MIN",
    "DEFAULT_WRITE_LIMIT_PER_MIN",
    "MAX_CONTENT_CHARS",
    "MAX_K",
    "MAX_QUERY_CHARS",
    "ExternalApiError",
    "ExternalApiService",
    "ExternalAuditLog",
    "ExternalRateLimiter",
    "ExternalResponse",
]
