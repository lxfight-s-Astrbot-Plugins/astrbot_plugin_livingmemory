"""Tests for the external memory API service: auth, scopes, rate limit, audit."""

from __future__ import annotations

import json

import pytest
from astrbot_plugin_livingmemory.core.external_api import (
    MAX_CONTENT_CHARS,
    MAX_QUERY_CHARS,
    ExternalApiService,
)
from astrbot_plugin_livingmemory.storage.external_api_store import (
    ExternalApiStore,
    hash_api_key,
)

from .external_api_support import (
    FakeMemoryEngine,
    bearer,
    issue_key,
    make_config,
    make_service,
    read_audit_lines,
)


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestAuthentication:
    @pytest.mark.asyncio
    async def test_disabled_service_hides_everything(self, tmp_path):
        service, _engine = make_service(tmp_path, enabled=False)

        for action in ("health", "personas", "recall", "write", "get", "archive"):
            response = await service.dispatch(action)
            assert response.status_code == 404
            assert response.payload["code"] == "not_found"
            assert "External API" not in response.payload["message"]

    @pytest.mark.asyncio
    async def test_missing_or_invalid_token_is_unauthorized(self, tmp_path):
        service, _engine = make_service(tmp_path)

        for headers in (None, {}, {"Authorization": "Basic abc"}):
            response = await service.dispatch("health", headers=headers)
            assert response.status_code == 401
            assert response.payload["code"] == "unauthorized"
            assert response.headers["WWW-Authenticate"] == "Bearer"

        response = await service.dispatch(
            "health", headers={"Authorization": f"Bearer {generate_missing_token()}"}
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_revoked_key_is_unauthorized(self, tmp_path):
        service, _engine = make_service(tmp_path)
        token, record = await issue_key(service)
        await service.revoke_key(record.id)

        response = await service.dispatch("health", headers=bearer(token))

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_read_key_cannot_write(self, tmp_path):
        service, _engine = make_service(tmp_path)
        token, _record = await issue_key(service, scopes=["read"])

        response = await service.dispatch(
            "write", headers=bearer(token), payload={"content": "hello"}
        )

        assert response.status_code == 403
        assert response.payload["code"] == "forbidden"

    @pytest.mark.asyncio
    async def test_health_reports_version_and_scopes(self, tmp_path):
        service, _engine = make_service(tmp_path)
        token, record = await issue_key(service, scopes=["read", "write"])

        response = await service.dispatch("health", headers=bearer(token))

        assert response.status_code == 200
        assert response.payload["status"] == "ok"
        assert response.payload["plugin_version"]
        assert response.payload["ready"] is True
        assert response.payload["scopes"] == ["read", "write"]
        assert response.payload["key"] == {"id": record.id, "name": "test-key"}


def generate_missing_token() -> str:
    """Build a syntactically valid token that is not stored anywhere."""
    import secrets

    return f"lm_{secrets.token_urlsafe(16)}"


class TestPersonaAndScopeRestrictions:
    @pytest.mark.asyncio
    async def test_persona_allowlist_restricts_recall_and_defaults(self, tmp_path):
        service, engine = make_service(tmp_path)
        engine.set_search_results(
            [
                (1, "p1 memory", {"persona_id": "p1", "session_id": "s1"}, 0.9),
                (2, "p2 memory", {"persona_id": "p2", "session_id": "s1"}, 0.8),
            ]
        )
        token, _record = await issue_key(service, allowed_personas=["p1"])

        allowed = await service.dispatch(
            "recall", headers=bearer(token), payload={"query": "memory"}
        )
        denied = await service.dispatch(
            "recall",
            headers=bearer(token),
            payload={"query": "memory", "persona_id": "p2"},
        )

        assert allowed.status_code == 200
        assert engine.search_calls[-1]["persona_id"] == "p1"
        assert [item["memory_id"] for item in allowed.payload["items"]] == [1]
        assert denied.status_code == 403
        assert denied.payload["code"] == "forbidden"

    @pytest.mark.asyncio
    async def test_scope_allowlist_restricts_writes_and_defaults(self, tmp_path):
        service, engine = make_service(tmp_path)
        token, _record = await issue_key(
            service, scopes=["read", "write"], allowed_scopes=["s1"]
        )

        created = await service.dispatch(
            "write", headers=bearer(token), payload={"content": "scoped memory"}
        )
        denied = await service.dispatch(
            "write",
            headers=bearer(token),
            payload={"content": "other scope", "scope": "s2"},
        )

        assert created.status_code == 200
        assert engine.added[-1]["session_id"] == "s1"
        assert denied.status_code == 403
        assert engine.search_calls == []

    @pytest.mark.asyncio
    async def test_get_and_archive_enforce_visibility(self, tmp_path):
        service, engine = make_service(tmp_path)
        visible_id = engine.add_existing("visible", persona_id="p1", session_id="s1")
        hidden_id = engine.add_existing("hidden", persona_id="p2", session_id="s1")
        token, _record = await issue_key(
            service, scopes=["read", "write"], allowed_personas=["p1"]
        )

        visible = await service.dispatch(
            "get", headers=bearer(token), memory_id=visible_id
        )
        hidden = await service.dispatch(
            "get", headers=bearer(token), memory_id=hidden_id
        )
        archived = await service.dispatch(
            "archive", headers=bearer(token), memory_id=visible_id
        )

        assert visible.status_code == 200
        assert visible.payload["memory"]["content"] == "visible"
        assert hidden.status_code == 403
        assert archived.status_code == 200
        assert archived.payload["archived"] is True
        assert engine.archived == [[visible_id]]

    @pytest.mark.asyncio
    async def test_personas_endpoint_filters_allowlist(self, tmp_path):
        engine = FakeMemoryEngine(
            str(tmp_path / "external_api.db"),
            personas=["p1", "p2"],
            scopes=["s1", "s2"],
        )
        service, _engine = make_service(tmp_path, engine=engine)
        token, _record = await issue_key(
            service, allowed_personas=["p1"], allowed_scopes=["s1"]
        )

        response = await service.dispatch("personas", headers=bearer(token))

        assert response.status_code == 200
        assert response.payload["personas"] == ["p1"]
        assert response.payload["scopes"] == ["s1"]
        assert response.payload["allowed_personas"] == ["p1"]


class TestWriteBehaviour:
    @pytest.mark.asyncio
    async def test_write_uses_engine_and_records_provenance(self, tmp_path):
        service, engine = make_service(tmp_path)
        token, record = await issue_key(
            service, scopes=["read", "write"], allowed_scopes=["s1"]
        )

        response = await service.dispatch(
            "write",
            headers=bearer(token),
            payload={
                "content": "external memory",
                "topics": ["ops"],
                "key_facts": ["fact"],
                "importance": 0.7,
                "source": "workbuddy",
            },
        )

        assert response.status_code == 200
        assert response.payload["created"] is True
        assert response.payload["memory_id"] == engine.added[-1]["memory_id"]
        added = engine.added[-1]
        assert added["content"] == "external memory"
        assert added["session_id"] == "s1"
        assert added["importance"] == 0.7
        assert added["metadata"]["source"] == "external_api"
        assert added["metadata"]["external_client"] == "test-key"
        assert added["metadata"]["external_key_id"] == record.id
        assert added["metadata"]["topics"] == ["ops"]
        assert added["metadata"]["key_facts"] == ["fact"]
        assert added["metadata"]["external_source"] == "workbuddy"

    @pytest.mark.asyncio
    async def test_idempotency_returns_existing_memory(self, tmp_path):
        service, engine = make_service(tmp_path)
        token, _record = await issue_key(service, scopes=["read", "write"])

        first = await service.dispatch(
            "write",
            headers=bearer(token),
            payload={"content": "once", "idempotency_key": "abc"},
        )
        second = await service.dispatch(
            "write",
            headers=bearer(token),
            payload={"content": "once", "idempotency_key": "abc"},
        )

        assert first.payload["created"] is True
        assert second.payload["created"] is False
        assert second.payload["memory_id"] == first.payload["memory_id"]
        assert len(engine.added) == 1
        # The idempotency record is persisted for client retries.
        store = ExternalApiStore(engine.db_path)
        assert await store.get_idempotency("abc") == first.payload["memory_id"]

    @pytest.mark.asyncio
    async def test_recall_formats_items_and_filters_visibility(self, tmp_path):
        service, engine = make_service(tmp_path)
        engine.set_search_results(
            [
                (
                    7,
                    "matched memory",
                    {
                        "persona_id": "p1",
                        "session_id": "s1",
                        "importance": 0.8,
                        "create_time": 123.0,
                    },
                    0.912345,
                ),
                (8, "hidden memory", {"persona_id": "p2", "session_id": "s1"}, 0.5),
            ]
        )
        token, _record = await issue_key(service, allowed_personas=["p1"])

        response = await service.dispatch(
            "recall", headers=bearer(token), payload={"query": "m", "k": 5}
        )

        assert response.status_code == 200
        assert response.payload["total"] == 1
        item = response.payload["items"][0]
        assert item == {
            "memory_id": 7,
            "content": "matched memory",
            "score": 0.9123,
            "importance": 0.8,
            "create_time": 123.0,
            "session_id": "s1",
            "persona_id": "p1",
        }


class TestValidation:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("payload", "message_part"),
        [
            ({"query": ""}, "query"),
            ({"query": "x" * (MAX_QUERY_CHARS + 1)}, "query"),
            ({"query": "ok", "k": 0}, "k"),
            ({"query": "ok", "k": 21}, "k"),
            ({"query": "ok", "k": "abc"}, "k"),
        ],
    )
    async def test_recall_parameter_limits(self, tmp_path, payload, message_part):
        service, _engine = make_service(tmp_path)
        token, _record = await issue_key(service)

        response = await service.dispatch(
            "recall", headers=bearer(token), payload=payload
        )

        assert response.status_code == 400
        assert response.payload["code"] == "invalid_request"
        assert message_part in response.payload["message"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("payload", "message_part"),
        [
            ({}, "content"),
            ({"content": " "}, "content"),
            ({"content": "x" * (MAX_CONTENT_CHARS + 1)}, "content"),
            ({"content": "ok", "importance": 2.0}, "importance"),
            ({"content": "ok", "importance": "high"}, "importance"),
            ({"content": "ok", "topics": "not-a-list"}, "topics"),
            ({"content": "ok", "key_facts": [1]}, "key_facts"),
        ],
    )
    async def test_write_parameter_limits(self, tmp_path, payload, message_part):
        service, _engine = make_service(tmp_path)
        token, _record = await issue_key(service, scopes=["read", "write"])

        response = await service.dispatch(
            "write", headers=bearer(token), payload=payload
        )

        assert response.status_code == 400
        assert message_part in response.payload["message"]

    @pytest.mark.asyncio
    async def test_missing_or_invalid_memory_id(self, tmp_path):
        service, _engine = make_service(tmp_path)
        token, _record = await issue_key(service)

        for memory_id in (None, "abc", -1):
            response = await service.dispatch(
                "get", headers=bearer(token), memory_id=memory_id
            )
            assert response.status_code == 400

        missing = await service.dispatch("get", headers=bearer(token), memory_id=999)
        assert missing.status_code == 404
        assert missing.payload["code"] == "not_found"

    @pytest.mark.asyncio
    async def test_unknown_action_is_not_found(self, tmp_path):
        service, _engine = make_service(tmp_path)
        token, _record = await issue_key(service)

        response = await service.dispatch("delete_all", headers=bearer(token))

        assert response.status_code == 404


class TestRateLimiting:
    @pytest.mark.asyncio
    async def test_request_bucket_returns_429_with_retry_after(self, tmp_path):
        clock = FakeClock()
        service, engine = make_service(tmp_path)
        service.rate_limiter._clock = clock  # deterministic clock for buckets
        token, _record = await issue_key(service, rate_limit_per_min=2)

        statuses = []
        for _index in range(3):
            response = await service.dispatch("health", headers=bearer(token))
            statuses.append(response)
        assert [item.status_code for item in statuses] == [200, 200, 429]
        limited = statuses[-1]
        assert limited.payload["code"] == "rate_limited"
        assert int(limited.headers["Retry-After"]) >= 1
        assert limited.payload["retry_after"] >= 1

        clock.advance(30.0)  # half the window restores one token
        assert (
            await service.dispatch("health", headers=bearer(token))
        ).status_code == 200

    @pytest.mark.asyncio
    async def test_write_bucket_is_separate_from_read_bucket(self, tmp_path):
        service, _engine = make_service(tmp_path)
        token, _record = await issue_key(
            service,
            scopes=["read", "write"],
            rate_limit_per_min=10,
            write_limit_per_min=1,
        )

        first = await service.dispatch(
            "write", headers=bearer(token), payload={"content": "one"}
        )
        second = await service.dispatch(
            "write", headers=bearer(token), payload={"content": "two"}
        )
        read = await service.dispatch("health", headers=bearer(token))

        assert first.status_code == 200
        assert second.status_code == 429
        assert second.payload["code"] == "rate_limited"
        assert read.status_code == 200
        assert "Retry-After" in second.headers


class TestAudit:
    @pytest.mark.asyncio
    async def test_audit_records_metadata_without_content_or_token(self, tmp_path):
        service, _engine = make_service(tmp_path)
        token, record = await issue_key(service, scopes=["read", "write"])

        await service.dispatch(
            "write",
            headers=bearer(token),
            payload={"content": "sensitive memory content"},
            remote_addr="127.0.0.1",
        )
        await service.dispatch("health", headers=bearer(token))

        lines = read_audit_lines(tmp_path / "external_api_audit.jsonl")
        assert len(lines) == 2
        write_entry = lines[0]
        assert write_entry["action"] == "write"
        assert write_entry["key_id"] == record.id
        assert write_entry["key_name"] == "test-key"
        assert write_entry["status_code"] == 200
        assert write_entry["memory_id"] is not None
        assert write_entry["latency_ms"] >= 0
        assert write_entry["remote_addr"] == "127.0.0.1"
        raw = (tmp_path / "external_api_audit.jsonl").read_text(encoding="utf-8")
        assert "sensitive memory content" not in raw
        assert token not in raw

    @pytest.mark.asyncio
    async def test_audit_records_unauthorized_attempt_without_key(self, tmp_path):
        service, _engine = make_service(tmp_path)

        await service.dispatch("health", headers={"Authorization": "Bearer lm_bad"})

        lines = read_audit_lines(tmp_path / "external_api_audit.jsonl")
        assert len(lines) == 1
        assert lines[0]["status_code"] == 401
        assert lines[0]["key_id"] is None
        assert lines[0]["key_name"] is None

    @pytest.mark.asyncio
    async def test_audit_log_prunes_records_older_than_retention(self, tmp_path):
        service, _engine = make_service(tmp_path, audit_retention_days=1)
        audit_path = tmp_path / "external_api_audit.jsonl"
        audit_path.write_text(
            "\n".join(
                [
                    json.dumps({"ts": 1.0, "action": "old"}),
                    json.dumps({"ts": __import__("time").time(), "action": "new"}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        removed = await service._audit.prune()

        assert removed == 1
        lines = read_audit_lines(audit_path)
        assert [line["action"] for line in lines] == ["new"]


class TestStoreIsScopedToEngineDatabase:
    @pytest.mark.asyncio
    async def test_database_path_is_shared_with_engine(self, tmp_path):
        db_path = str(tmp_path / "shared.db")
        engine = FakeMemoryEngine(db_path)
        service, _engine = make_service(tmp_path, engine=engine)
        token, _record = await issue_key(service)

        assert service.store is not None
        assert service.store.db_path == db_path
        assert await service.store.find_active_key_by_hash(hash_api_key(token))


def test_service_uses_config_defaults(tmp_path):
    manager = make_config(default_rate_limit_per_min=7, default_write_limit_per_min=3)
    engine = FakeMemoryEngine(str(tmp_path / "external_api.db"))
    service = ExternalApiService(manager, engine, tmp_path)

    assert service.enabled is True
    assert service.default_rate_limit_per_min == 7
    assert service.default_write_limit_per_min == 3
    assert service.max_request_bytes == 262144
    assert service.audit_retention_days == 30
    assert service.trust_proxy_headers is False
