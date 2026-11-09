"""Persistent storage for the external memory API (API keys and idempotency).

The external API is default-off; this store only owns its plugin-private tables
and never touches ``documents`` or graph data. Audit records are written to a
JSONL file by :mod:`core.external_api`, not stored here.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger

from .schema import EXTERNAL_API_SCHEMA_STATEMENTS
from .sqlite_connection import open_connection

API_KEY_PREFIX = "lm_"
API_KEY_TOKEN_BYTES = 32
API_KEY_DISPLAY_PREFIX_LENGTH = 8
# Idempotency records are only useful for client retry windows; keep a month.
IDEMPOTENCY_RETENTION_SECONDS = 30 * 24 * 3600


class ExternalApiStoreError(ValueError):
    """Raised for invalid API-key operations (duplicate name, bad input)."""


def generate_api_key() -> str:
    """Return a fresh, URL-safe external API token (plaintext is never stored)."""
    return f"{API_KEY_PREFIX}{secrets.token_urlsafe(API_KEY_TOKEN_BYTES)}"


def hash_api_key(token: str) -> str:
    """Return the SHA-256 hex digest stored in ``external_api_keys.key_hash``."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def key_display_prefix(token: str) -> str:
    """Return the short, non-secret prefix used to identify a key in listings."""
    return token[:API_KEY_DISPLAY_PREFIX_LENGTH]


def _dump_list(values: list[str] | None) -> str | None:
    if values is None:
        return None
    return json.dumps(list(values), ensure_ascii=False)


def _load_list(raw: Any) -> list[str] | None:
    if raw is None or raw == "":
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, list):
        return None
    return [str(item) for item in parsed]


@dataclass(slots=True)
class ExternalApiKey:
    """One API key row (the plaintext token only exists at creation time)."""

    id: int
    name: str
    key_prefix: str
    scopes: list[str] = field(default_factory=lambda: ["read"])
    allowed_personas: list[str] | None = None
    allowed_scopes: list[str] | None = None
    rate_limit_per_min: int = 60
    write_limit_per_min: int = 20
    created_at: float = 0.0
    last_used_at: float | None = None
    revoked_at: float | None = None
    key_hash: str = ""

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None

    def can_read(self) -> bool:
        return "read" in self.scopes

    def can_write(self) -> bool:
        return "write" in self.scopes

    def to_public_dict(self) -> dict[str, Any]:
        """Serialize without the secret hash; safe for listings and commands."""
        return {
            "id": self.id,
            "name": self.name,
            "key_prefix": self.key_prefix,
            "scopes": list(self.scopes),
            "allowed_personas": list(self.allowed_personas)
            if self.allowed_personas is not None
            else None,
            "allowed_scopes": list(self.allowed_scopes)
            if self.allowed_scopes is not None
            else None,
            "rate_limit_per_min": self.rate_limit_per_min,
            "write_limit_per_min": self.write_limit_per_min,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "revoked_at": self.revoked_at,
        }


def _row_to_key(row: Any) -> ExternalApiKey:
    return ExternalApiKey(
        id=int(row["id"]),
        name=str(row["name"]),
        key_prefix=str(row["key_prefix"]),
        key_hash=str(row["key_hash"]),
        scopes=_load_list(row["scopes"]) or ["read"],
        allowed_personas=_load_list(row["allowed_personas"]),
        allowed_scopes=_load_list(row["allowed_scopes"]),
        rate_limit_per_min=int(row["rate_limit_per_min"]),
        write_limit_per_min=int(row["write_limit_per_min"]),
        created_at=float(row["created_at"]),
        last_used_at=row["last_used_at"],
        revoked_at=row["revoked_at"],
    )


class ExternalApiStore:
    """SQLite store for external API keys and request idempotency records."""

    def __init__(self, db_path: str):
        """Create a store bound to the plugin's SQLite database.

        Args:
            db_path: Path of the plugin database shared with the memory engine.
        """
        self.db_path = db_path

    async def initialize(self) -> None:
        """Create plugin-private tables if missing and prune stale idempotency rows."""
        db = await open_connection(self.db_path)
        try:
            for statement in EXTERNAL_API_SCHEMA_STATEMENTS:
                await db.execute(statement)
            await db.execute(
                "DELETE FROM external_api_idempotency WHERE created_at < ?",
                (time.time() - IDEMPOTENCY_RETENTION_SECONDS,),
            )
            await db.commit()
        finally:
            await db.close()

    async def create_key(
        self,
        name: str,
        *,
        scopes: list[str],
        allowed_personas: list[str] | None = None,
        allowed_scopes: list[str] | None = None,
        rate_limit_per_min: int = 60,
        write_limit_per_min: int = 20,
    ) -> tuple[ExternalApiKey, str]:
        """Persist a new key and return ``(record, plaintext_token)`` once."""
        name = str(name or "").strip()
        if not name:
            raise ExternalApiStoreError("API key name must not be empty")
        # Write access always implies read access: every external action needs to
        # look memories up before it can change them.
        if "write" in scopes:
            scopes = [*scopes, "read"]
        normalized_scopes = [scope for scope in ("read", "write") if scope in scopes]
        if not normalized_scopes:
            raise ExternalApiStoreError("API key needs at least the 'read' scope")
        token = generate_api_key()
        now = time.time()
        db = await open_connection(self.db_path)
        try:
            try:
                cursor = await db.execute(
                    """
                    INSERT INTO external_api_keys (
                        name, key_prefix, key_hash, scopes, allowed_personas,
                        allowed_scopes, rate_limit_per_min, write_limit_per_min,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        key_display_prefix(token),
                        hash_api_key(token),
                        _dump_list(normalized_scopes),
                        _dump_list(allowed_personas),
                        _dump_list(allowed_scopes),
                        int(rate_limit_per_min),
                        int(write_limit_per_min),
                        now,
                    ),
                )
            except Exception as exc:
                if "UNIQUE" in str(exc).upper():
                    raise ExternalApiStoreError(
                        f"API key name already exists: {name}"
                    ) from exc
                raise
            key_id = int(cursor.lastrowid or 0)
            await db.commit()
        finally:
            await db.close()
        record = ExternalApiKey(
            id=key_id,
            name=name,
            key_prefix=key_display_prefix(token),
            key_hash=hash_api_key(token),
            scopes=normalized_scopes,
            allowed_personas=allowed_personas,
            allowed_scopes=allowed_scopes,
            rate_limit_per_min=int(rate_limit_per_min),
            write_limit_per_min=int(write_limit_per_min),
            created_at=now,
        )
        logger.info("[ExternalAPI] Created API key %s (id=%s)", name, key_id)
        return record, token

    async def list_keys(self) -> list[ExternalApiKey]:
        """Return every key ordered by id (revoked keys included, for auditing)."""
        db = await open_connection(self.db_path)
        db.row_factory = _row_factory
        try:
            cursor = await db.execute("SELECT * FROM external_api_keys ORDER BY id ASC")
            rows = await cursor.fetchall()
        finally:
            await db.close()
        return [_row_to_key(row) for row in rows]

    async def get_key(self, identifier: str | int) -> ExternalApiKey | None:
        """Find one key by numeric id or unique name."""
        db = await open_connection(self.db_path)
        db.row_factory = _row_factory
        try:
            if str(identifier).strip().isdigit():
                cursor = await db.execute(
                    "SELECT * FROM external_api_keys WHERE id = ?",
                    (int(identifier),),
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM external_api_keys WHERE name = ?",
                    (str(identifier).strip(),),
                )
            row = await cursor.fetchone()
        finally:
            await db.close()
        return _row_to_key(row) if row is not None else None

    async def find_active_key_by_hash(self, key_hash: str) -> ExternalApiKey | None:
        """Return the non-revoked key matching a token hash, or ``None``."""
        if not key_hash:
            return None
        db = await open_connection(self.db_path)
        db.row_factory = _row_factory
        try:
            cursor = await db.execute(
                "SELECT * FROM external_api_keys WHERE key_hash = ? AND revoked_at IS NULL",
                (key_hash,),
            )
            row = await cursor.fetchone()
        finally:
            await db.close()
        return _row_to_key(row) if row is not None else None

    async def touch_last_used(self, key_id: int) -> None:
        """Record the last successful authentication time for one key."""
        db = await open_connection(self.db_path)
        try:
            await db.execute(
                "UPDATE external_api_keys SET last_used_at = ? WHERE id = ?",
                (time.time(), int(key_id)),
            )
            await db.commit()
        finally:
            await db.close()

    async def revoke_key(self, identifier: str | int) -> ExternalApiKey | None:
        """Revoke a key by id or name; already-revoked keys keep their first stamp."""
        key = await self.get_key(identifier)
        if key is None:
            return None
        if key.revoked_at is None:
            db = await open_connection(self.db_path)
            try:
                await db.execute(
                    "UPDATE external_api_keys SET revoked_at = ? WHERE id = ?",
                    (time.time(), key.id),
                )
                await db.commit()
            finally:
                await db.close()
            key = await self.get_key(key.id) or key
        logger.info("[ExternalAPI] Revoked API key %s (id=%s)", key.name, key.id)
        return key

    async def get_idempotency(self, idempotency_key: str) -> int | None:
        """Return the memory id already stored for an idempotency key."""
        db = await open_connection(self.db_path)
        try:
            cursor = await db.execute(
                "SELECT memory_id FROM external_api_idempotency WHERE idempotency_key = ?",
                (idempotency_key,),
            )
            row = await cursor.fetchone()
        finally:
            await db.close()
        return int(row[0]) if row is not None else None

    async def save_idempotency(self, idempotency_key: str, memory_id: int) -> int:
        """Record ``idempotency_key -> memory_id``; return the winning memory id."""
        db = await open_connection(self.db_path)
        try:
            await db.execute(
                "INSERT OR IGNORE INTO external_api_idempotency "
                "(idempotency_key, memory_id, created_at) VALUES (?, ?, ?)",
                (idempotency_key, int(memory_id), time.time()),
            )
            await db.commit()
            cursor = await db.execute(
                "SELECT memory_id FROM external_api_idempotency WHERE idempotency_key = ?",
                (idempotency_key,),
            )
            row = await cursor.fetchone()
        finally:
            await db.close()
        return int(row[0]) if row is not None else int(memory_id)


def _row_factory(cursor: Any, row: tuple) -> dict[str, Any]:
    return {column[0]: row[index] for index, column in enumerate(cursor.description)}


__all__ = [
    "API_KEY_DISPLAY_PREFIX_LENGTH",
    "API_KEY_PREFIX",
    "ExternalApiKey",
    "ExternalApiStore",
    "ExternalApiStoreError",
    "generate_api_key",
    "hash_api_key",
    "key_display_prefix",
]
