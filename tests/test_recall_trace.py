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
# Recall chain integration (real engine)
# ---------------------------------------------------------------------------


async def _real_engine(tmp_path, recorder, *, config=None):
    """Real engine with the trace recorder installed."""
    from tests.real_engine_support import make_engine

    engine, db = await make_engine(
        tmp_path / "trace",
        config={
            "min_similarity_for_retrieval": 0.0,
            **(config or {}),
        },
    )
    # 替换为测试自带的记录器（固定时钟、可控开关）
    engine.trace_recorder = recorder
    return engine, db


async def _seed(engine, rows):
    ids = []
    for content, session_id, metadata in rows:
        ids.append(
            await engine.add_memory(
                content, session_id=session_id, persona_id=None, metadata=metadata
            )
        )
    return ids


@pytest.mark.asyncio
async def test_recall_records_scope_candidates_and_session(tmp_path) -> None:
    """一次真实召回的追踪记录：作用域、候选与耗时都可从记录中读出。"""
    recorder = _recorder()
    engine, db = await _real_engine(tmp_path, recorder)
    try:
        ids = await _seed(
            engine,
            [
                ("用户喜欢燕麦拿铁", "livingmemory:session:x", {}),
                ("用户周末去爬山", "livingmemory:session:x", {}),
            ],
        )

        with trace_request_context(session_id="test:private:umo") as context:
            returned = await engine.search_memories(
                "燕麦拿铁", k=2, session_id="livingmemory:session:x"
            )
            trace_id = context.trace_id

        assert returned, "real recall returned nothing"
        trace = recorder.get(trace_id)
        assert trace["session_id"] == "test:private:umo"
        assert trace["scope"] == "livingmemory:session:x"
        assert trace["cache_hit"] is False
        assert set(trace["routes"]) <= {"document", "graph", "cache", "hybrid"}
        assert [candidate["doc_id"] for candidate in trace["candidates"]]
        assert trace["candidates"][0]["doc_id"] in ids
        assert trace["duration_ms"] >= 0
    finally:
        from tests.real_engine_support import close_engine

        await close_engine(engine, db)


@pytest.mark.asyncio
async def test_recall_records_filter_rejections(tmp_path) -> None:
    """被过滤策略拒绝的记忆计入 rejected 统计，不出现在候选里。"""
    recorder = _recorder()
    engine, db = await _real_engine(
        tmp_path,
        recorder,
        config={
            "min_importance_for_retrieval": 0.3,
            "min_similarity_for_retrieval": 0.6,
        },
    )
    try:
        kept = await engine.add_memory(
            "用户喜欢燕麦拿铁", session_id="livingmemory:global", importance=0.9
        )
        await engine.add_memory(
            "用户随口提过一句天气", session_id="livingmemory:global", importance=0.05
        )

        await engine.search_memories("燕麦拿铁 天气", k=5)

        trace = recorder.get(recorder.recent(1)[0]["id"])
        assert trace["filters"]["min_similarity"] == pytest.approx(0.6)
        assert trace["filters"]["min_importance"] == pytest.approx(0.3)
        assert trace["filters"]["rejected"]["importance"] >= 1
        candidates = [candidate["doc_id"] for candidate in trace["candidates"]]
        assert candidates == [kept]
        stats = recorder.stats()["filtered"]
        assert stats["importance"] >= 1
    finally:
        from tests.real_engine_support import close_engine

        await close_engine(engine, db)


@pytest.mark.asyncio
async def test_recall_cache_hit_is_recorded(tmp_path) -> None:
    """同一查询第二次命中检索缓存，追踪里标记 cache_hit。"""
    recorder = _recorder()
    engine, db = await _real_engine(tmp_path, recorder)
    engine.config["search_cache_enabled"] = True
    engine._search_cache_enabled = True
    try:
        await engine.add_memory(
            "用户喜欢燕麦拿铁", session_id="livingmemory:global"
        )

        await engine.search_memories("same query", k=1)
        await engine.search_memories("same query", k=1)

        rows = recorder.recent(5)
        assert len(rows) == 2
        assert rows[0]["cache_hit"] is True
        assert rows[1]["cache_hit"] is False
        assert rows[0]["routes"]["cache"]["candidates"] >= 1
        assert recorder.stats()["cache_hits"] == 1
    finally:
        from tests.real_engine_support import close_engine

        await close_engine(engine, db)


@pytest.mark.asyncio
async def test_recall_agentic_flag_is_recorded(tmp_path) -> None:
    recorder = _recorder()
    engine, db = await _real_engine(tmp_path, recorder)
    try:
        await engine.add_memory("用户喜欢燕麦拿铁", session_id="livingmemory:global")

        with trace_request_context(session_id="umo", agentic=True):
            await engine.search_memories("燕麦拿铁", k=1)

        trace = recorder.get(recorder.recent(1)[0]["id"])
        assert trace["agentic"] is True
    finally:
        from tests.real_engine_support import close_engine

        await close_engine(engine, db)


@pytest.mark.asyncio
async def test_recall_disabled_recorder_records_nothing(tmp_path) -> None:
    recorder = _recorder(enabled=False)
    engine, db = await _real_engine(tmp_path, recorder)
    try:
        await engine.add_memory("用户喜欢燕麦拿铁", session_id="livingmemory:global")

        returned = await engine.search_memories("燕麦拿铁", k=1)

        assert returned
        assert recorder.recent(5) == []
    finally:
        from tests.real_engine_support import close_engine

        await close_engine(engine, db)


# ---------------------------------------------------------------------------
# Injection update hook (driven through a real recall)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_injection_is_recorded_in_the_trace(tmp_path, monkeypatch):
    """自动注入后，追踪里记录了注入的条数与字符数。"""
    from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
    from astrbot_plugin_livingmemory.core.event_handler import EventHandler
    from tests.real_engine_support import (
        HostContext,
        close_engine,
        make_engine,
    )
    from astrbot.core.message.components import Plain

    recorder = _recorder()
    engine, db = await make_engine(
        tmp_path / "inject", config={"min_similarity_for_retrieval": 0.0}
    )
    engine.trace_recorder = recorder
    store = None
    try:
        await engine.add_memory(
            "用户喜欢燕麦拿铁", session_id="test:private:sid-1", persona_id=None
        )
        from astrbot_plugin_livingmemory.core.managers.conversation_manager import (
            ConversationManager,
        )
        from astrbot_plugin_livingmemory.storage.conversation_store import (
            ConversationStore,
        )

        store = ConversationStore(str(tmp_path / "conv.db"))
        await store.initialize()
        manager = ConversationManager(store=store)
        handler = EventHandler(
            context=HostContext(persona_id=None),
            config_manager=ConfigManager(
                {"recall_engine": {"top_k": 3, "min_similarity_for_retrieval": 0.0}}
            ),
            memory_engine=engine,
            memory_processor=None,
            conversation_manager=manager,
        )

        class _E:
            unified_msg_origin = "test:private:sid-1"
            message_str = "燕麦拿铁"

            def get_message_type(self):
                from astrbot.api.platform import MessageType

                return MessageType.FRIEND_MESSAGE

            def get_sender_id(self):
                return "user-1"

            def get_self_id(self):
                return "bot-1"

            def get_sender_name(self):
                return "Tester"

            def get_message_str(self):
                return "燕麦拿铁"

            def get_messages(self):
                return [Plain("燕麦拿铁")]

            def get_platform_name(self):
                return "test"

            def get_platform_id(self):
                return "inst"

            def get_group_id(self):
                return ""

            def get_extra(self, key, default=None):
                return default

        req = SimpleNamespace(
            prompt="燕麦拿铁",
            system_prompt="",
            contexts=[],
            extra_user_content_parts=[],
        )
        await handler.handle_memory_recall(_E(), req)
        await handler.shutdown()

        trace = recorder.get(recorder.recent(1)[0]["id"])
        assert trace["injected"], "no injected entries recorded"
        assert trace["injected_chars"] > 0
    finally:
        if store is not None:
            await store.close()
        await close_engine(engine, db)


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
