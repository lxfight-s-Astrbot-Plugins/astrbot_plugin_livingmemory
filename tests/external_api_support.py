"""Shared stubs for external memory API tests (service/routes/commands/server)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.external_api import ExternalApiService


class _FakeCursor:
    def __init__(self, rows: list[tuple]):
        self._rows = rows

    async def fetchall(self) -> list[tuple]:
        return self._rows


class _FakeConnection:
    """Minimal aiosqlite-like connection for the persona/scope discovery query."""

    def __init__(self, personas: list[str], scopes: list[str]):
        self.personas = personas
        self.scopes = scopes

    async def execute(self, sql: str, params: Any = ()) -> _FakeCursor:
        if "persona_id" in sql:
            return _FakeCursor([(value,) for value in self.personas])
        if "session_id" in sql:
            return _FakeCursor([(value,) for value in self.scopes])
        return _FakeCursor([])


class FakeMemoryEngine:
    """In-memory stand-in for the MemoryEngine methods the external API uses."""

    def __init__(
        self,
        db_path: str,
        *,
        personas: list[str] | None = None,
        scopes: list[str] | None = None,
    ):
        self.db_path = db_path
        self.memories: dict[int, dict[str, Any]] = {}
        self.added: list[dict[str, Any]] = []
        self.archived: list[list[int]] = []
        self.search_results: list[Any] = []
        self.search_calls: list[dict[str, Any]] = []
        self.db_connection = _FakeConnection(personas or [], scopes or [])
        self._next_id = 1

    def add_existing(
        self,
        content: str = "existing memory",
        *,
        persona_id: str | None = None,
        session_id: str | None = None,
        importance: float = 0.5,
        status: str = "active",
        metadata: dict | None = None,
    ) -> int:
        memory_id = self._next_id
        self._next_id += 1
        merged = {
            "session_id": session_id,
            "persona_id": persona_id,
            "importance": importance,
            "create_time": 1000.0 + memory_id,
            "status": status,
            **(metadata or {}),
        }
        self.memories[memory_id] = {
            "id": memory_id,
            "text": content,
            "metadata": merged,
        }
        return memory_id

    def set_search_results(self, rows: list[tuple[int, str, dict, float]]) -> None:
        self.search_results = [
            SimpleNamespace(
                doc_id=memory_id,
                content=content,
                metadata=metadata,
                final_score=score,
            )
            for memory_id, content, metadata, score in rows
        ]

    async def search_memories(
        self,
        query: str,
        k: int = 5,
        session_id: str | None = None,
        persona_id: str | None = None,
    ) -> list[Any]:
        self.search_calls.append(
            {
                "query": query,
                "k": k,
                "session_id": session_id,
                "persona_id": persona_id,
            }
        )
        return self.search_results

    async def add_memory(
        self,
        content: str,
        session_id: str | None = None,
        persona_id: str | None = None,
        importance: float = 0.5,
        metadata: dict | None = None,
        **_kwargs: Any,
    ) -> int:
        memory_id = self.add_existing(
            content,
            persona_id=persona_id,
            session_id=session_id,
            importance=importance,
            metadata=metadata,
        )
        self.added.append(
            {
                "memory_id": memory_id,
                "content": content,
                "session_id": session_id,
                "persona_id": persona_id,
                "importance": importance,
                "metadata": metadata or {},
            }
        )
        return memory_id

    async def close(self) -> None:
        return None

    async def get_memory(self, memory_id: int) -> dict[str, Any] | None:
        return self.memories.get(int(memory_id))

    async def archive_memories(
        self, memory_ids: list[int], *, audit: dict | None = None
    ) -> int:
        self.archived.append(list(memory_ids))
        count = 0
        for memory_id in memory_ids:
            memory = self.memories.get(int(memory_id))
            if memory is None:
                continue
            if str(memory["metadata"].get("status") or "active") == "archived":
                continue
            memory["metadata"]["status"] = "archived"
            memory["metadata"].update(audit or {})
            count += 1
        return count


def make_config(enabled: bool = True, **overrides: Any) -> ConfigManager:
    """Build a real ConfigManager with the external API enabled by default."""
    external_api = {
        "enabled": enabled,
        "listen_host": "127.0.0.1",
        "listen_port": 7801,
        "max_request_bytes": 262144,
        "default_rate_limit_per_min": 60,
        "default_write_limit_per_min": 20,
        "audit_retention_days": 30,
        "trust_proxy_headers": False,
    }
    external_api.update(overrides)
    return ConfigManager({"external_api": external_api})


def make_service(
    tmp_path,
    *,
    enabled: bool = True,
    engine: FakeMemoryEngine | None = None,
    config: ConfigManager | None = None,
    **overrides: Any,
) -> tuple[ExternalApiService, FakeMemoryEngine]:
    """Build a service backed by a real SQLite file and the fake engine."""
    db_path = str(tmp_path / "external_api.db")
    engine = engine or FakeMemoryEngine(db_path)
    manager = config or make_config(enabled=enabled, **overrides)
    service = ExternalApiService(manager, engine, tmp_path)
    return service, engine


async def issue_key(
    service: ExternalApiService,
    *,
    name: str = "test-key",
    scopes: list[str] | None = None,
    allowed_personas: list[str] | None = None,
    allowed_scopes: list[str] | None = None,
    rate_limit_per_min: int | None = None,
    write_limit_per_min: int | None = None,
) -> tuple[str, Any]:
    """Create a key and return ``(plaintext_token, record)``."""
    record, token = await service.create_key(
        name,
        scopes=scopes or ["read"],
        allowed_personas=allowed_personas,
        allowed_scopes=allowed_scopes,
        rate_limit_per_min=rate_limit_per_min,
        write_limit_per_min=write_limit_per_min,
    )
    return token, record


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def read_audit_lines(path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


__all__ = [
    "FakeMemoryEngine",
    "bearer",
    "issue_key",
    "make_config",
    "make_service",
    "read_audit_lines",
]
