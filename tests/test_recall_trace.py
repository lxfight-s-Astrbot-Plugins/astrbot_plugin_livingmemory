"""Request-level recall trace: recorder, ring buffer, persistence and recall hooks."""

from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot_plugin_livingmemory.core.base.config_validator import (
    LivingMemoryConfig,
    TraceSettings,
)
from astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall import (
    MemoryRecall,
)
from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine
from astrbot_plugin_livingmemory.core.retrieval.hybrid_retriever import HybridResult
from astrbot_plugin_livingmemory.core.retrieval.recall_trace import (
    DEFAULT_TRACE_SETTINGS,
    RecallTraceRecorder,
    hash_query,
    snapshot_candidates,
    trace_request_context,
)

ROOT = Path(__file__).resolve().parents[1]
_DAY_SECONDS = 86400.0


def _clock_at(value: list[float]):
    return lambda: value[0]


def _recorder(tmp_path: Path | None = None, **overrides) -> RecallTraceRecorder:
    settings = dict(DEFAULT_TRACE_SETTINGS)
    settings.update(overrides)
    return RecallTraceRecorder(
        settings, trace_dir=tmp_path, clock=lambda: 1_700_000_000.0
    )


def _hybrid(
    doc_id: int,
    score: float = 0.8,
    *,
    metadata: dict | None = None,
    vector: float | None = 0.7,
) -> HybridResult:
    return HybridResult(
        doc_id=doc_id,
        final_score=score,
        rrf_score=score,
        bm25_score=0.5,
        vector_score=vector,
        content=f"memory {doc_id}",
        metadata=metadata if metadata is not None else {"status": "active"},
        score_breakdown={"document_keyword_score": 0.5},
    )


# ---------------------------------------------------------------------------
# Recorder core
# ---------------------------------------------------------------------------


def test_disabled_recorder_allocates_nothing() -> None:
    recorder = _recorder(enabled=False)

    assert recorder.begin(query="q") is None
    assert recorder.commit(None) is None
    assert recorder.recent(5) == []
    assert recorder.stats()["enabled"] is False


def test_ring_buffer_evicts_oldest_and_keeps_get_by_id() -> None:
    recorder = _recorder(max_records=2)
    ids = []
    for index in range(3):
        trace = recorder.begin(query=f"q{index}")
        recorder.route(trace, "document", candidates=index + 1, ms=1.0)
        ids.append(recorder.commit(trace))

    rows = recorder.recent(10)
    assert [row["id"] for row in rows] == [ids[2], ids[1]]
    assert recorder.get(ids[0]) is None
    assert recorder.get(ids[2]) is not None
    assert recorder.stats()["evicted"] == 1


def test_record_convenience_commits_immediately() -> None:
    recorder = _recorder()
    trace_id = recorder.record(query="q", session_id="s", agentic=True)

    assert trace_id is not None
    assert recorder.recent(1)[0]["id"] == trace_id
    assert recorder.get(trace_id)["agentic"] is True
    assert _recorder(enabled=False).record(query="q") is None


def test_query_text_only_stored_when_enabled() -> None:
    recorder = _recorder(include_query_text=False)
    trace = recorder.begin(query="very private query")
    recorder.commit(trace)

    row = recorder.recent(1)[0]
    assert row["query_hash"] == hash_query("very private query")
    assert recorder.get(trace.trace_id)["query_text"] is None

    recorder_with_text = _recorder(include_query_text=True)
    trace = recorder_with_text.begin(query="very private query")
    recorder_with_text.commit(trace)
    assert recorder_with_text.get(trace.trace_id)["query_text"] == "very private query"


def test_clear_and_stats_aggregation() -> None:
    recorder = _recorder()
    trace = recorder.begin(query="q")
    recorder.route(trace, "document", candidates=4, ms=2.0)
    recorder.route(trace, "graph", candidates=2, ok=False, error="timeout", ms=1.0)
    trace.cache_hit = True
    trace.filters = {
        "min_similarity": 0.5,
        "min_importance": 0.1,
        "rejected": {"status": 2, "importance": 1},
    }
    recorder.commit(trace)
    recorder.update_injected(
        trace.trace_id, [{"doc_id": 1, "score": 0.9, "chars": 10}], 10
    )

    stats = recorder.stats()
    assert stats["recorded"] == 1
    assert stats["cache_hits"] == 1
    assert stats["cache_misses"] == 0
    assert stats["routes"]["document"] == {"count": 1, "failures": 0, "avg_ms": 2.0}
    assert stats["routes"]["graph"]["failures"] == 1
    assert stats["filtered"] == {"status": 2, "importance": 1}
    assert stats["injected"] == {"traces": 1, "items": 1, "chars": 10}

    assert recorder.clear() == 1
    assert recorder.recent(1) == []


def test_find_citing_matches_candidates_and_injected() -> None:
    recorder = _recorder()
    trace = recorder.begin(query="q")
    trace.candidates = [{"doc_id": 7, "final_score": 0.8}]
    recorder.commit(trace)
    trace2 = recorder.begin(query="q2")
    recorder.commit(trace2)
    recorder.update_injected(
        trace2.trace_id, [{"doc_id": 7, "score": 0.5, "chars": 3}], 3
    )

    hits = recorder.find_citing(7, limit=5)
    assert {row["id"] for row in hits} == {trace.trace_id, trace2.trace_id}
    assert recorder.find_citing(999) == []


def test_snapshot_candidates_is_bounded_and_serialisable() -> None:
    results = [_hybrid(index, 1.0 - index / 100) for index in range(30)]
    rows = snapshot_candidates(results)
    assert len(rows) == 20
    assert rows[0]["doc_id"] == 0
    assert rows[0]["bm25"] == 0.5
    assert json.dumps(rows)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persistence_writes_jsonl_per_day(tmp_path: Path) -> None:
    clock = [1_700_000_000.0]
    recorder = RecallTraceRecorder(
        {**DEFAULT_TRACE_SETTINGS, "persist_enabled": True},
        trace_dir=tmp_path,
        clock=_clock_at(clock),
    )
    first = recorder.begin(query="q")
    recorder.commit(first)
    await recorder.flush_persist()

    day_one = datetime.fromtimestamp(clock[0], tz=timezone.utc).strftime("%Y%m%d")
    path = tmp_path / f"{day_one}.jsonl"
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["id"] == first.trace_id
    assert payload["query_text"] is None
    assert payload["query_hash"] == hash_query("q")

    clock[0] += _DAY_SECONDS
    second = recorder.begin(query="q2")
    recorder.commit(second)
    await recorder.flush_persist()

    day_two = datetime.fromtimestamp(clock[0], tz=timezone.utc).strftime("%Y%m%d")
    assert (tmp_path / f"{day_two}.jsonl").exists()
    assert recorder.stats()["persistence"]["persisted"] == 2


@pytest.mark.asyncio
async def test_persistence_skips_when_file_cap_reached(tmp_path: Path) -> None:
    recorder = RecallTraceRecorder(
        {
            **DEFAULT_TRACE_SETTINGS,
            "persist_enabled": True,
            "max_file_mb": 1,
        },
        trace_dir=tmp_path,
        clock=lambda: 1_700_000_000.0,
    )
    day = datetime.fromtimestamp(1_700_000_000.0, tz=timezone.utc).strftime("%Y%m%d")
    (tmp_path / f"{day}.jsonl").write_bytes(b"x" * (1024 * 1024 + 1))

    trace = recorder.begin(query="q")
    recorder.commit(trace)
    await recorder.flush_persist()

    stats = recorder.stats()["persistence"]
    assert stats["skipped"] == 1
    assert stats["persisted"] == 0


@pytest.mark.asyncio
async def test_persistence_prunes_expired_days(tmp_path: Path) -> None:
    clock = [1_700_000_000.0]
    recorder = RecallTraceRecorder(
        {
            **DEFAULT_TRACE_SETTINGS,
            "persist_enabled": True,
            "retention_days": 2,
        },
        trace_dir=tmp_path,
        clock=_clock_at(clock),
    )
    today = datetime.fromtimestamp(clock[0], tz=timezone.utc)
    old_day = (today - timedelta(days=5)).strftime("%Y%m%d")
    fresh_day = (today - timedelta(days=1)).strftime("%Y%m%d")
    old_file = tmp_path / f"{old_day}.jsonl"
    fresh_file = tmp_path / f"{fresh_day}.jsonl"
    old_file.write_text("{}\n", encoding="utf-8")
    fresh_file.write_text("{}\n", encoding="utf-8")

    trace = recorder.begin(query="q")
    recorder.commit(trace)
    await recorder.flush_persist()

    assert not old_file.exists()
    assert fresh_file.exists()


def test_persistence_off_by_default(tmp_path: Path) -> None:
    recorder = RecallTraceRecorder(DEFAULT_TRACE_SETTINGS, trace_dir=tmp_path)
    trace = recorder.begin(query="q")
    recorder.commit(trace)
    assert not list(tmp_path.glob("*.jsonl"))
    assert recorder.stats()["persistence"]["files"] == []


# ---------------------------------------------------------------------------
# Recall chain integration
# ---------------------------------------------------------------------------


def _make_engine(recorder, results, *, config=None, dual_search=None):
    engine = MemoryEngine.__new__(MemoryEngine)
    engine.config = {
        "min_similarity_for_retrieval": 0.0,
        "min_importance_for_retrieval": 0.0,
        "memory_type_filter": "all",
        "recent_memory_count": 0,
        "search_cache_enabled": True,
        "search_cache_ttl_seconds": 45.0,
        "search_cache_max_size": 16,
        **(config or {}),
    }
    engine.atom_enabled = False
    engine.atom_store = None
    engine.atom_retriever = None
    engine.hybrid_retriever = None
    engine.faiss_db = object()
    engine.trace_recorder = recorder
    engine._search_cache_enabled = True
    engine._search_cache_ttl = 45.0
    engine._search_cache_max_size = 16
    engine._search_cache_generation = 0
    engine._search_cache = OrderedDict()
    engine.dual_route_retriever = SimpleNamespace(
        search=dual_search or AsyncMock(return_value=results)
    )
    engine._update_access_times_internal = AsyncMock()
    engine._runtime_session_migration_needed = AsyncMock(return_value=False)
    return engine


async def _fake_dual_search(results):
    async def search(query, k, session_id=None, persona_id=None, diagnostics=None):
        if diagnostics is not None:
            diagnostics["routes"] = {"document": True, "graph": True}
            diagnostics["route_details"] = {
                "document": {"candidates": len(results) + 1, "ms": 1.5},
                "graph": {"candidates": 2, "ms": 2.5},
            }
        return results

    return search


@pytest.mark.asyncio
async def test_recall_records_routes_candidates_and_umo() -> None:
    recorder = _recorder()
    results = [_hybrid(1, 0.9), _hybrid(2, 0.7)]
    engine = _make_engine(
        recorder, results, dual_search=await _fake_dual_search(results)
    )

    with trace_request_context(session_id="test:private:umo") as context:
        returned = await engine.search_memories(
            "query", k=2, session_id="livingmemory:session:x"
        )
        trace_id = context.trace_id

    assert returned == results
    trace = recorder.get(trace_id)
    assert trace["session_id"] == "test:private:umo"
    assert trace["scope"] == "livingmemory:session:x"
    assert trace["cache_hit"] is False
    assert trace["routes"]["document"] == {
        "candidates": 3,
        "ok": True,
        "error": None,
        "ms": 1.5,
    }
    assert trace["routes"]["graph"]["candidates"] == 2
    assert [candidate["doc_id"] for candidate in trace["candidates"]] == [1, 2]
    assert trace["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_recall_records_filter_rejections() -> None:
    recorder = _recorder()
    results = [
        _hybrid(1, 0.9, metadata={"status": "archived"}),
        _hybrid(2, 0.9, metadata={"status": "active", "superseded_by": 5}),
        _hybrid(3, 0.9, metadata={"status": "active", "importance": 0.05}),
        _hybrid(4, 0.5, metadata={"status": "active"}, vector=0.1),
        _hybrid(
            5,
            0.9,
            metadata={"status": "active", "atom_types": ["semantic"]},
        ),
    ]
    engine = _make_engine(
        recorder,
        results,
        config={
            "min_importance_for_retrieval": 0.3,
            "min_similarity_for_retrieval": 0.6,
            "memory_type_filter": "event_only",
        },
    )

    await engine.search_memories("query", k=5)

    summary = recorder.recent(1)[0]
    trace = recorder.get(summary["id"])
    assert trace["filters"]["min_similarity"] == pytest.approx(0.6)
    assert trace["filters"]["min_importance"] == pytest.approx(0.3)
    assert trace["filters"]["rejected"] == {
        "status": 1,
        "superseded": 1,
        "importance": 1,
        "similarity": 1,
        "memory_type": 1,
    }
    assert trace["candidates"] == []
    stats = recorder.stats()["filtered"]
    assert stats["status"] == 1 and stats["superseded"] == 1


@pytest.mark.asyncio
async def test_recall_cache_hit_is_recorded() -> None:
    recorder = _recorder()
    results = [_hybrid(1, 0.9)]
    engine = _make_engine(recorder, results)

    await engine.search_memories("same query", k=1)
    await engine.search_memories("same query", k=1)

    rows = recorder.recent(5)
    assert len(rows) == 2
    assert rows[0]["cache_hit"] is True
    assert rows[1]["cache_hit"] is False
    assert rows[0]["routes"]["cache"]["candidates"] == 1
    assert recorder.stats()["cache_hits"] == 1


@pytest.mark.asyncio
async def test_recall_agentic_flag_and_error_trace() -> None:
    recorder = _recorder()
    failing = AsyncMock(side_effect=RuntimeError("retriever down"))
    engine = _make_engine(recorder, [], dual_search=failing)

    with trace_request_context(session_id="umo", agentic=True):
        with pytest.raises(RuntimeError):
            await engine.search_memories("query", k=1)

    trace = recorder.recent(1)[0]
    assert trace["agentic"] is True
    assert trace["routes"] == {}
    assert "RuntimeError" in (recorder.get(trace["id"])["error"] or "")


@pytest.mark.asyncio
async def test_recall_disabled_recorder_records_nothing() -> None:
    recorder = _recorder(enabled=False)
    results = [_hybrid(1, 0.9)]
    engine = _make_engine(recorder, results)

    returned = await engine.search_memories("query", k=1)

    assert returned == results
    assert recorder.recent(5) == []


# ---------------------------------------------------------------------------
# Injection update hook
# ---------------------------------------------------------------------------


def test_injection_update_attaches_items_and_chars() -> None:
    recorder = _recorder()
    trace = recorder.begin(query="q")
    recorder.commit(trace)

    recall = MemoryRecall.__new__(MemoryRecall)
    recall.memory_engine = SimpleNamespace(trace_recorder=recorder)
    context = SimpleNamespace(trace_id=trace.trace_id, agentic=False)
    memories = [_hybrid(1, 0.9), _hybrid(2, 0.5)]

    recall._record_injected_trace(context, memories, "formatted block")

    detail = recorder.get(trace.trace_id)
    assert [item["doc_id"] for item in detail["injected"]] == [1, 2]
    assert detail["injected_chars"] == len("formatted block")

    recall._record_trace_note(context, "no_results")
    assert "no_results" in recorder.get(trace.trace_id)["notes"]


def test_injection_update_ignores_unknown_trace() -> None:
    recorder = _recorder()
    recall = MemoryRecall.__new__(MemoryRecall)
    recall.memory_engine = SimpleNamespace(trace_recorder=recorder)

    recall._record_injected_trace(SimpleNamespace(trace_id="missing"), [], "")
    recall._record_trace_note(SimpleNamespace(trace_id=None), "x")
    assert recorder.recent(1) == []


# ---------------------------------------------------------------------------
# Configuration / i18n surface
# ---------------------------------------------------------------------------


def test_trace_settings_defaults_and_config_registration() -> None:
    settings = TraceSettings()
    assert settings.enabled is True
    assert settings.max_records == 50
    assert settings.persist_enabled is False
    assert settings.include_query_text is False
    assert settings.max_file_mb == 10
    assert settings.retention_days == 7

    config = LivingMemoryConfig()
    assert config.trace_settings == settings


def test_schema_and_locales_cover_trace_settings() -> None:
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    group = schema["trace_settings"]
    assert group["invisible"] is True
    assert set(group["items"]) == {
        "enabled",
        "max_records",
        "persist_enabled",
        "include_query_text",
        "max_file_mb",
        "retention_days",
    }

    for locale in ("zh-CN", "en-US", "ru-RU", "ja-JP"):
        data = json.loads(
            (ROOT / ".astrbot-plugin" / "i18n" / f"{locale}.json").read_text(
                encoding="utf-8"
            )
        )
        translated = data["config"]["trace_settings"]
        for key in group["items"]:
            assert translated[key]["description"]
            assert translated[key]["hint"]


def test_backend_i18n_trace_keys_match_all_languages() -> None:
    zh = json.loads((ROOT / "core" / "i18n" / "zh.json").read_text(encoding="utf-8"))

    def flat(data, prefix=""):
        keys = set()
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                keys |= flat(value, path)
            else:
                keys.add(path)
        return keys

    zh_trace = {key for key in flat(zh) if key.startswith("trace.")}
    assert zh_trace
    for language in ("en", "ru", "ja"):
        data = json.loads(
            (ROOT / "core" / "i18n" / f"{language}.json").read_text(encoding="utf-8")
        )
        assert {key for key in flat(data) if key.startswith("trace.")} == zh_trace
        assert "/lmem trace" in data["help"]["text"]


def test_persistence_uses_background_task_in_running_loop(tmp_path: Path) -> None:
    async def scenario() -> None:
        recorder = RecallTraceRecorder(
            {**DEFAULT_TRACE_SETTINGS, "persist_enabled": True},
            trace_dir=tmp_path,
        )
        trace = recorder.begin(query="q")
        recorder.commit(trace)
        assert recorder._persist_task is not None
        await asyncio.sleep(0)
        await recorder.flush_persist()
        assert recorder._persist_task is None
        assert list(tmp_path.glob("*.jsonl"))

    asyncio.run(scenario())


@pytest.mark.asyncio
async def test_dual_route_reports_route_details_in_diagnostics() -> None:
    from astrbot_plugin_livingmemory.core.retrieval.dual_route_retriever import (
        DualRouteRetriever,
    )
    from astrbot_plugin_livingmemory.core.retrieval.graph_retriever import (
        GraphResult,
    )

    class _DocRoute:
        async def search(self, query, k, session_id=None, persona_id=None, **kwargs):
            return [_hybrid(1)]

    class _GraphRoute:
        async def search(self, query, k, session_id=None, persona_id=None, **kwargs):
            return [
                GraphResult(
                    doc_id=2,
                    final_score=1.0,
                    rrf_score=1.0,
                    keyword_score=1.0,
                    vector_score=None,
                    content="graph memory",
                    metadata={},
                )
            ]

    async def loader(memory_id):
        return {"text": f"memory {memory_id}", "metadata": {}}

    retriever = DualRouteRetriever(
        document_retriever=_DocRoute(),  # type: ignore[arg-type]
        graph_retriever=_GraphRoute(),  # type: ignore[arg-type]
        memory_loader=loader,
        config={
            "document_route_weight": 0.65,
            "graph_route_weight": 0.35,
            "dynamic_route_weighting": False,
        },
    )
    diagnostics: dict = {}

    await retriever.search("query", k=2, diagnostics=diagnostics)

    assert diagnostics["route_details"]["document"]["candidates"] == 1
    assert diagnostics["route_details"]["graph"]["candidates"] == 1
    assert diagnostics["route_details"]["graph"]["ms"] >= 0
    assert diagnostics["routes"] == {"document": True, "graph": True}
