"""Integration test: external API writes/queries go through the real MemoryEngine."""

from __future__ import annotations

import pytest
from astrbot_plugin_livingmemory.core.external_api import ExternalApiService
from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine

from .external_api_support import bearer, make_config
from .test_memory_engine import _FakeFaissDB


async def _make_engine(tmp_path) -> MemoryEngine:
    engine = MemoryEngine(
        db_path=str(tmp_path / "livingmemory.db"),
        faiss_db=_FakeFaissDB(),
        config={
            "atom_enabled": False,
            "graph_memory_enabled": False,
            "fallback_enabled": True,
            "rrf_k": 60,
            "memory_dedup": {"enabled": False},
        },
    )
    await engine.initialize()
    return engine


@pytest.mark.asyncio
async def test_write_recall_get_archive_with_real_engine(tmp_path):
    engine = await _make_engine(tmp_path)
    service = ExternalApiService(make_config(enabled=True), engine, tmp_path)
    try:
        _record, write_token = await service.create_key(
            "workbuddy", scopes=["read", "write"]
        )
        headers = bearer(write_token)

        created = await service.dispatch(
            "write",
            headers=headers,
            payload={
                "content": "external api integration memory alpha",
                "topics": ["integration"],
                "key_facts": ["alpha fact"],
                "importance": 0.8,
            },
        )
        assert created.status_code == 200
        assert created.payload["created"] is True
        memory_id = created.payload["memory_id"]

        stored = await engine.get_memory(memory_id)
        assert stored is not None
        assert stored["text"] == "external api integration memory alpha"
        metadata = stored["metadata"]
        assert metadata["source"] == "external_api"
        assert metadata["external_client"] == "workbuddy"
        assert metadata["external_key_id"] == _record.id
        assert metadata["topics"] == ["integration"]
        assert metadata["key_facts"] == ["alpha fact"]
        assert metadata["status"] == "active"

        fetched = await service.dispatch("get", headers=headers, memory_id=memory_id)
        assert fetched.status_code == 200
        assert fetched.payload["memory"]["content"] == (
            "external api integration memory alpha"
        )
        assert fetched.payload["memory"]["importance"] == 0.8

        recalled = await service.dispatch(
            "recall", headers=headers, payload={"query": "integration memory", "k": 5}
        )
        assert recalled.status_code == 200
        assert any(item["memory_id"] == memory_id for item in recalled.payload["items"])

        archived = await service.dispatch(
            "archive", headers=headers, memory_id=memory_id
        )
        assert archived.status_code == 200
        assert archived.payload["archived"] is True
        stored_after_archive = await engine.get_memory(memory_id)
        assert stored_after_archive["metadata"]["status"] == "archived"
        assert stored_after_archive["metadata"]["archived_by"] == "external_api"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_idempotent_write_creates_single_document(tmp_path):
    engine = await _make_engine(tmp_path)
    service = ExternalApiService(make_config(enabled=True), engine, tmp_path)
    try:
        _record, token = await service.create_key("script", scopes=["read", "write"])
        headers = bearer(token)
        payload = {
            "content": "idempotent external memory",
            "idempotency_key": "retry-1",
        }

        first = await service.dispatch("write", headers=headers, payload=payload)
        second = await service.dispatch("write", headers=headers, payload=payload)

        assert first.payload["created"] is True
        assert second.payload["created"] is False
        assert second.payload["memory_id"] == first.payload["memory_id"]
        # The fake FAISS backend is the document store in this harness: a
        # retried idempotent write must not create a second index entry.
        assert len(engine.faiss_db.docs) == 1
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_read_only_key_cannot_write_with_real_engine(tmp_path):
    engine = await _make_engine(tmp_path)
    service = ExternalApiService(make_config(enabled=True), engine, tmp_path)
    try:
        _record, token = await service.create_key("reader", scopes=["read"])
        response = await service.dispatch(
            "write",
            headers=bearer(token),
            payload={"content": "should be rejected"},
        )

        assert response.status_code == 403
        assert engine.faiss_db.docs == {}
    finally:
        await engine.close()
