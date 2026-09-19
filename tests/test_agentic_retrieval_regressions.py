"""Beta 生命周期、真实检索路线与候选覆盖的回归测试。"""

import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine
from astrbot_plugin_livingmemory.core.models.memory_atom import MemoryAtom
from astrbot_plugin_livingmemory.core.processors.memory_processor_build import (
    MemoryProcessorBuildMixin,
)
from astrbot_plugin_livingmemory.core.retrieval.hybrid_retriever import HybridResult
from tests.test_agentic_engine_retrieval import (
    _SharedDbFaiss,
    _identities,
    _make_engine,
)
from tests.test_agentic_tools import _context, _event, _tools

SCOPE = "test:private:s1"


@pytest_asyncio.fixture
async def engine(tmp_path):
    instance = await _make_engine(tmp_path)
    yield instance
    await instance.close()


def candidate(doc_id, **metadata):
    return HybridResult(
        doc_id=doc_id,
        content=f"candidate {doc_id}",
        final_score=0.8,
        rrf_score=0.03,
        bm25_score=1.0,
        vector_score=1.0,
        metadata={"session_id": SCOPE, "status": "active", **metadata},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["conversation", "current_speaker"])
@pytest.mark.parametrize("hidden_state", ["forgotten", "expired_unswept"])
async def test_both_search_targets_hide_atoms_without_atomization(
    engine, target, hidden_state
):
    mid = await engine.add_memory(
        content="OLDTRIP active fact and FORGOTTEN_SECRET",
        session_id=SCOPE,
        metadata={"participant_identities": _identities("qq:1001")},
        atoms=[
            MemoryAtom(parent_memory_id=0, content="OLDTRIP active fact"),
            MemoryAtom(parent_memory_id=0, content="FORGOTTEN_SECRET"),
        ],
    )
    if hidden_state == "forgotten":
        await engine.db_connection.execute(
            "UPDATE memory_atoms SET status='forgotten' "
            "WHERE parent_memory_id=? AND content=?",
            (mid, "FORGOTTEN_SECRET"),
        )
    else:
        await engine.db_connection.execute(
            "UPDATE memory_atoms SET expires_at=1 "
            "WHERE parent_memory_id=? AND content=?",
            (mid, "FORGOTTEN_SECRET"),
        )
    await engine.db_connection.commit()
    engine.atom_enabled = False
    engine.atom_store = None
    engine.atom_retriever = None
    search, read = _tools(engine)
    direct = await read.call(_context(_event()), memory_id=mid)
    assert "FORGOTTEN_SECRET" not in direct
    raw = await search.call(_context(_event()), query="OLDTRIP", target=target)
    assert json.loads(raw)["count"] == 1
    assert "FORGOTTEN_SECRET" not in raw
    assert "OLDTRIP active fact" in raw


@pytest.mark.asyncio
@pytest.mark.parametrize("atom_marked", [False, True])
async def test_deep_read_without_graph_or_atom_table(tmp_path, atom_marked):
    holder = {}
    instance = MemoryEngine(
        db_path=str(tmp_path / "no_graph.db"),
        faiss_db=_SharedDbFaiss(holder),
        config={"graph_memory_enabled": False},
    )
    holder["engine"] = instance
    await instance.initialize()
    try:
        mid = await instance.add_memory(
            content="legacy facts",
            session_id=SCOPE,
            metadata={"atom_types": ["factual"]} if atom_marked else {},
            source_messages=[{"role": "user", "content": "retained original"}],
        )
        search, read = _tools(instance)
        out = json.loads(
            await read.call(_context(_event()), memory_id=mid, include_source=True)
        )
        if atom_marked:
            assert out["status"] == "lifecycle_limited"
            assert "retained original" not in json.dumps(out)
        else:
            assert out["status"] == "success"
            assert out["source_status"] == "ok"
            assert out["source_messages"][0]["content"] == "retained original"
            found = json.loads(
                await search.call(_context(_event()), query="legacy facts")
            )
            assert found["count"] == 1
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_storage_read_error_is_not_not_found(engine):
    mid = await engine.add_memory(content="visible memory", session_id=SCOPE)
    engine.faiss_db.document_storage.get_documents = AsyncMock(
        side_effect=RuntimeError("private database path must not reach the model")
    )
    _, read = _tools(engine)
    raw = await read.call(_context(_event()), memory_id=mid)
    assert json.loads(raw)["status"] == "error"
    assert "private database path" not in raw


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["session_id", "persona_id"])
async def test_stale_graph_candidate_cannot_cross_parent_memory_scope(engine, boundary):
    from astrbot_plugin_livingmemory.core.retrieval.graph_retriever import GraphResult

    allowed = {"session_id": SCOPE, "persona_id": "allowed"}
    actual = {**allowed, boundary: "other"}
    mid = await engine.add_memory(content="private parent facts", **actual)
    dual = engine.dual_route_retriever
    dual.document_retriever.search = AsyncMock(return_value=[])
    dual.graph_retriever.search = AsyncMock(
        return_value=[
            GraphResult(
                doc_id=mid,
                final_score=1.0,
                rrf_score=0.03,
                keyword_score=1.0,
                vector_score=None,
                content="stale graph match",
                metadata=allowed,
            )
        ]
    )
    engine.atom_retriever = None
    # 图索引可能落后于父文档；最终取到的父文档元数据才是这条结果的权限依据。
    raw = await dual.search("parent facts", **allowed)
    assert len(raw) == 1 and raw[0].metadata[boundary] == "other"
    outcome = await engine.search_memories_agentic(query="parent facts", **allowed)
    assert outcome.results == []
    assert outcome.status == "no_candidates"


@pytest.mark.asyncio
async def test_all_real_nested_routes_failing_is_error(engine):
    dual = engine.dual_route_retriever
    for retriever in (
        dual.document_retriever.bm25_retriever,
        dual.document_retriever.vector_retriever,
        dual.graph_retriever.keyword_retriever,
        dual.graph_retriever.vector_retriever,
        engine.atom_retriever,
    ):
        retriever.search = AsyncMock(side_effect=RuntimeError("private backend error"))
    search, _ = _tools(engine)
    raw = await search.call(_context(_event()), query="trip")
    out = json.loads(raw)
    assert out["status"] == "error" and out["count"] == 0
    assert out["partial"]
    for route in (
        "document.bm25",
        "document.vector",
        "graph.keyword",
        "graph.vector",
        "atoms",
    ):
        assert route in out["message"]
    assert "private backend error" not in raw


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["vector", "graph", "atoms"])
async def test_partial_route_failure_keeps_candidates_and_marks_coverage(engine, route):
    await engine.add_memory(content="PARTIALROUTE trip agreement", session_id=SCOPE)
    retriever = {
        "vector": engine.hybrid_retriever.vector_retriever,
        "graph": engine.dual_route_retriever.graph_retriever.keyword_retriever,
        "atoms": engine.atom_retriever,
    }[route]
    retriever.search = AsyncMock(side_effect=RuntimeError("route unavailable"))
    search, _ = _tools(engine)
    out = json.loads(await search.call(_context(_event()), query="PARTIALROUTE"))
    assert out["count"] > 0
    assert out["partial"] is True
    assert out["coverage"] == "limited"


@pytest.mark.asyncio
async def test_person_topic_matches_multiple_keywords(engine):
    mid = await engine.add_memory(
        content="HIKING was our favorite activity; the AGREEMENT was to go in May",
        session_id=SCOPE,
        metadata={"participant_identities": _identities("qq:1001")},
    )
    out = await engine.search_memories_agentic(
        target="current_speaker",
        query="HIKING AGREEMENT",
        session_id=SCOPE,
        identity_key="qq:1001",
    )
    assert [item.doc_id for item in out.results] == [mid]


@pytest.mark.asyncio
async def test_person_topic_matches_live_facts_beyond_parent_summary(engine):
    processor = MemoryProcessorBuildMixin()
    processor.config = {"atom_enabled": True}
    text, metadata = processor._build_storage_format(
        "",
        {
            "summary": "a shared weekend",
            "key_facts": [
                *[f"regular activity {i}" for i in range(5)],
                "HIKING at the mountain",
            ],
        },
        False,
    )
    assert "HIKING" not in text
    metadata["participant_identities"] = _identities("qq:1001")
    mid = await engine.add_memory(
        content=text,
        session_id=SCOPE,
        metadata=metadata,
        atoms=processor.classify_atoms_from_metadata(metadata, session_id=SCOPE),
    )
    out = await engine.search_memories_agentic(
        target="current_speaker",
        query="HIKING",
        session_id=SCOPE,
        identity_key="qq:1001",
    )
    assert [item.doc_id for item in out.results] == [mid]
    assert "HIKING" in out.results[0].content


@pytest.mark.asyncio
async def test_person_topic_does_not_match_only_forgotten_facts(engine):
    mid = await engine.add_memory(
        content="HIKING old summary",
        session_id=SCOPE,
        metadata={"participant_identities": _identities("qq:1001")},
        atoms=[
            MemoryAtom(parent_memory_id=0, content="active agreement"),
            MemoryAtom(parent_memory_id=0, content="HIKING secret"),
        ],
    )
    await engine.db_connection.execute(
        "UPDATE memory_atoms SET status='forgotten' WHERE parent_memory_id=? AND content=?",
        (mid, "HIKING secret"),
    )
    await engine.db_connection.commit()
    out = await engine.search_memories_agentic(
        target="current_speaker",
        query="HIKING",
        session_id=SCOPE,
        identity_key="qq:1001",
    )
    assert out.status == "no_candidates"


@pytest.mark.asyncio
async def test_successful_rerank_order_survives_beta(engine):
    ids = [
        await engine.add_memory(content=text, session_id=SCOPE)
        for text in ("lexical match but wrong event", "actual historical event")
    ]
    docs = [await engine.get_memory(mid) for mid in ids]
    rows = [
        SimpleNamespace(
            doc_id=mid,
            content=doc["text"],
            metadata=doc["metadata"],
            score=1.0 - i / 10,
        )
        for i, (mid, doc) in enumerate(zip(ids, docs))
    ]
    retriever = engine.hybrid_retriever
    retriever.bm25_retriever.search = AsyncMock(return_value=rows)
    retriever.vector_retriever.search = AsyncMock(return_value=rows)
    provider = SimpleNamespace(
        rerank=AsyncMock(
            return_value=[
                SimpleNamespace(index=0, relevance_score=0.1),
                SimpleNamespace(index=1, relevance_score=0.99),
            ]
        )
    )
    retriever.rerank_enabled = True
    retriever.rerank_provider_resolver = lambda: provider
    engine.dual_route_retriever = None
    engine.atom_retriever = None
    before = await retriever.search("historical event", k=2, session_id=SCOPE)
    assert [item.doc_id for item in before] == list(reversed(ids))
    after = await engine.search_memories_agentic(
        query="historical event", k=2, session_id=SCOPE
    )
    assert [item.doc_id for item in after.results] == list(reversed(ids))
    assert (
        after.results[0].score_breakdown["agentic_relevance"]
        > after.results[1].final_score
    )


@pytest.mark.asyncio
async def test_old_strong_match_is_not_truncated_before_beta_sort(engine):
    now = time.time()
    rows = []
    ids = []
    for i in range(4):
        mid = await engine.add_memory(content=f"past agreement {i}", session_id=SCOPE)
        ids.append(mid)
        rows.append(
            SimpleNamespace(
                doc_id=mid,
                content=f"past agreement {i}",
                score=1.0 - i / 10,
                metadata={
                    "session_id": SCOPE,
                    "status": "active",
                    "importance": 0.5,
                    "create_time": now - 400 * 86400 if i == 0 else now,
                    "last_access_time": now - 400 * 86400 if i == 0 else now,
                },
            )
        )
    dual = engine.dual_route_retriever
    dual.document_retriever.bm25_retriever.search = AsyncMock(return_value=rows)
    dual.document_retriever.vector_retriever.search = AsyncMock(return_value=rows)
    dual.graph_retriever.search = AsyncMock(return_value=[])
    engine.atom_retriever = None
    legacy = await dual.search("agreement", k=3, session_id=SCOPE)
    assert ids[0] not in [item.doc_id for item in legacy]
    beta = await engine.search_memories_agentic(
        query="agreement", k=1, session_id=SCOPE
    )
    assert [item.doc_id for item in beta.results] == [ids[0]]
    again = await dual.search("agreement", k=3, session_id=SCOPE)
    assert [item.doc_id for item in again] == [item.doc_id for item in legacy]


@pytest.mark.asyncio
@pytest.mark.parametrize("filter_kind", ["importance", "partial_time"])
async def test_filter_losses_expand_even_with_partial_hits(engine, filter_kind):
    if filter_kind == "importance":
        candidates = [
            candidate(i + 1, importance=0.1 if i < 3 else 0.9) for i in range(4)
        ]
        engine.config["min_importance_for_retrieval"] = 0.5
        k, extra, expected = 1, {}, [4]
    else:
        candidates = [
            candidate(
                i + 1,
                source_time_start=(
                    "2026-03-05T10:00:00" if i in (0, 6) else "2026-01-05T10:00:00"
                ),
            )
            for i in range(7)
        ]
        k, expected = 2, [1, 7]
        extra = {
            "start_ts": time.mktime(time.strptime("2026-03-01", "%Y-%m-%d")),
            "end_ts": time.mktime(time.strptime("2026-03-31", "%Y-%m-%d")),
        }
    calls = []

    async def route(
        query,
        k,
        session_id=None,
        persona_id=None,
        diagnostics=None,
        *,
        relevance_only=False,
    ):
        assert relevance_only
        calls.append(k)
        return candidates[:k]

    engine.hybrid_retriever = SimpleNamespace(search=route)
    engine.dual_route_retriever = None
    engine.atom_retriever = None
    out = await engine.search_memories_agentic(
        query="candidate", k=k, session_id=SCOPE, **extra
    )
    assert [item.doc_id for item in out.results] == expected
    assert len(calls) > 1 and max(calls) <= 120


@pytest.mark.asyncio
async def test_candidate_expansion_has_a_hard_limit_and_reports_it(engine):
    candidates = [candidate(i + 1, importance=0.1) for i in range(300)]
    calls = []

    async def route(query, k, *args, **kwargs):
        calls.append(k)
        return candidates[:k]

    engine.hybrid_retriever = SimpleNamespace(search=route)
    engine.dual_route_retriever = None
    engine.atom_retriever = None
    engine.config["min_importance_for_retrieval"] = 0.5
    out = await engine.search_memories_agentic(query="candidate", k=1, session_id=SCOPE)
    assert out.coverage == "limited" and out.has_more
    assert max(calls) == 120 and len(calls) <= 5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "timestamp", [1736000000, 1736000000000, "2025-01-04T19:33:20+00:00"]
)
async def test_person_cursor_uses_same_time_type_as_sql(engine, timestamp):
    ids = [
        await engine.add_memory(
            content=f"numeric event {i}",
            session_id=SCOPE,
            metadata={
                "participant_identities": _identities("qq:1001"),
                "source_time_start": timestamp,
            },
        )
        for i in range(3)
    ]
    first = await engine.search_memories_agentic(
        target="current_speaker",
        session_id=SCOPE,
        identity_key="qq:1001",
        k=1,
    )
    second = await engine.search_memories_agentic(
        target="current_speaker",
        session_id=SCOPE,
        identity_key="qq:1001",
        k=1,
        cursor=first.next_cursor,
        exclude_ids=[first.results[0].doc_id],
    )
    assert first.results[0].doc_id == ids[-1]
    assert second.results[0].doc_id == ids[-2]
