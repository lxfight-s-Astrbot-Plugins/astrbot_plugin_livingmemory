"""Page API: recall trace endpoints and memory lineage."""

from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest
from astrbot_plugin_livingmemory.core.page_api import PAGE_API_PREFIX, PluginPageApi
from astrbot_plugin_livingmemory.core.retrieval.recall_trace import RecallTraceRecorder


@contextmanager
def _patch_trace_request(args: dict | None = None):
    import astrbot_plugin_livingmemory.core.page_api_modules.trace_handler as mod

    old = mod.request
    mod.request = SimpleNamespace(args=args or {})
    try:
        yield
    finally:
        mod.request = old


class _FakeInitializer:
    def __init__(self, engine) -> None:
        self.memory_engine = engine
        self.data_dir = ""
        self.conversation_manager = None
        self.index_validator = None
        self.memory_processor = None
        self.consolidation_manager = None
        self.config_manager = None


class _FakePlugin:
    def __init__(self, engine) -> None:
        self.initializer = _FakeInitializer(engine)
        self.config_manager = SimpleNamespace(get=lambda key, default=None: default)
        self._api_routes: list[tuple] = []
        self.context = SimpleNamespace(
            register_web_api=self._register,
            get_config=lambda: {},
            platform_manager=SimpleNamespace(get_insts=lambda: []),
        )

    def _register(self, route, handler, methods, desc) -> None:
        self._api_routes.append((route, handler, methods, desc))

    async def _ensure_plugin_ready(self, *, wait: bool = True):
        return True, ""


class _NotReadyPlugin(_FakePlugin):
    async def _ensure_plugin_ready(self, *, wait: bool = True):
        return False, "插件尚未就绪"


def _recorder(**overrides) -> RecallTraceRecorder:
    settings = {
        "enabled": True,
        "max_records": 50,
        "persist_enabled": False,
        "include_query_text": False,
    }
    settings.update(overrides)
    return RecallTraceRecorder(settings)


def _seed(recorder: RecallTraceRecorder, *, session_id: str = "s1", doc_id: int = 7):
    trace = recorder.begin(query="coffee", session_id=session_id)
    recorder.route(trace, "document", candidates=3, ms=1.5)
    trace.candidates = [{"doc_id": doc_id, "final_score": 0.9}]
    trace_id = recorder.commit(trace)
    recorder.update_injected(
        trace_id, [{"doc_id": doc_id, "score": 0.9, "chars": 10}], 20
    )
    return trace_id


@pytest.mark.asyncio
async def test_trace_routes_registered() -> None:
    plugin = _FakePlugin(SimpleNamespace(trace_recorder=_recorder()))
    api = PluginPageApi(plugin)

    api.register_routes()
    paths = {route for route, _, _, _ in plugin._api_routes}

    assert len(plugin._api_routes) == len(api._ROUTES)
    assert f"{PAGE_API_PREFIX}/traces" in paths
    assert f"{PAGE_API_PREFIX}/traces/stats" in paths
    assert f"{PAGE_API_PREFIX}/traces/<trace_id>" in paths
    assert f"{PAGE_API_PREFIX}/memories/<memory_id>/lineage" in paths


@pytest.mark.asyncio
async def test_list_traces_returns_recent_summaries() -> None:
    recorder = _recorder()
    trace_id = _seed(recorder)
    _seed(recorder, session_id="s2")
    api = PluginPageApi(_FakePlugin(SimpleNamespace(trace_recorder=recorder)))

    with _patch_trace_request({"limit": "1"}):
        result = await api.list_traces()

    assert result["status"] == "ok"
    assert result["data"]["enabled"] is True
    assert result["data"]["count"] == 1
    assert result["data"]["traces"][0]["id"] != trace_id
    assert result["data"]["traces"][0]["session_id"] == "s2"


@pytest.mark.asyncio
async def test_list_traces_filters_by_session() -> None:
    recorder = _recorder()
    _seed(recorder, session_id="s1")
    _seed(recorder, session_id="s2")
    api = PluginPageApi(_FakePlugin(SimpleNamespace(trace_recorder=recorder)))

    with _patch_trace_request({"session_id": "s2"}):
        result = await api.list_traces()

    assert [row["session_id"] for row in result["data"]["traces"]] == ["s2"]


@pytest.mark.asyncio
async def test_list_traces_disabled_and_bad_limit() -> None:
    api = PluginPageApi(
        _FakePlugin(SimpleNamespace(trace_recorder=_recorder(enabled=False)))
    )
    with _patch_trace_request({}):
        disabled = await api.list_traces()
    assert disabled["data"]["enabled"] is False

    api = PluginPageApi(_FakePlugin(SimpleNamespace(trace_recorder=_recorder())))
    with _patch_trace_request({"limit": "abc"}):
        bad = await api.list_traces()
    assert bad["status"] == "error"


@pytest.mark.asyncio
async def test_get_trace_detail_and_missing() -> None:
    recorder = _recorder()
    trace_id = _seed(recorder)
    api = PluginPageApi(_FakePlugin(SimpleNamespace(trace_recorder=recorder)))

    with _patch_trace_request({}):
        found = await api.get_trace(trace_id)
        missing = await api.get_trace("nope")
        empty = await api.get_trace("")

    assert found["status"] == "ok"
    assert found["data"]["trace"]["id"] == trace_id
    assert found["data"]["trace"]["injected"][0]["doc_id"] == 7
    assert missing["status"] == "error"
    assert empty["status"] == "error"


@pytest.mark.asyncio
async def test_trace_stats_endpoint() -> None:
    recorder = _recorder()
    _seed(recorder)
    api = PluginPageApi(_FakePlugin(SimpleNamespace(trace_recorder=recorder)))

    with _patch_trace_request({}):
        result = await api.get_trace_stats()

    assert result["status"] == "ok"
    assert result["data"]["recorded"] == 1
    assert result["data"]["injected"]["items"] == 1


@pytest.mark.asyncio
async def test_trace_endpoints_fast_fail_when_plugin_not_ready() -> None:
    api = PluginPageApi(_NotReadyPlugin(SimpleNamespace(trace_recorder=_recorder())))

    with _patch_trace_request({}):
        assert (await api.list_traces())["code"] == "not_ready"
        assert (await api.get_trace("x"))["code"] == "not_ready"
        assert (await api.get_trace_stats())["code"] == "not_ready"
        assert (await api.get_memory_lineage("1"))["code"] == "not_ready"


@pytest.mark.asyncio
async def test_lineage_returns_sources_chain_graph_and_traces(tmp_path) -> None:
    db = await aiosqlite.connect(str(tmp_path / "memory.db"))
    db.row_factory = aiosqlite.Row
    await db.execute(
        "CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, "
        "text TEXT, metadata TEXT, created_at TEXT, updated_at TEXT)"
    )
    await db.execute(
        "INSERT INTO documents(id, doc_id, text, metadata, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            5,
            "uuid-5",
            "target memory",
            json.dumps({"status": "active", "superseded_by": 9}),
            "2026-01-01",
            "2026-01-02",
        ),
    )
    await db.execute(
        "INSERT INTO documents(id, doc_id, text, metadata, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            11,
            "uuid-11",
            "newer memory",
            json.dumps({"status": "active", "superseded_by": 5}),
            "2026-01-03",
            "2026-01-03",
        ),
    )
    await db.commit()

    recorder = _recorder()
    trace_id = _seed(recorder, doc_id=5)

    engine = SimpleNamespace(
        db_connection=db,
        trace_recorder=recorder,
        graph_store=SimpleNamespace(
            get_subgraph_for_memories=AsyncMock(
                return_value={"nodes": [{"id": 1}], "edges": [], "entries": []}
            )
        ),
        get_memory_source=AsyncMock(
            return_value=[{"role": "user", "content": "origin"}]
        ),
    )

    async def get_memory_record(memory_id):
        if memory_id == 5:
            return {
                "id": 5,
                "doc_id": "uuid-5",
                "text": "target memory",
                "metadata": {
                    "status": "active",
                    "superseded_by": 9,
                    "session_id": "s1",
                    "importance": 0.6,
                    "source_window": {"start_index": 1},
                },
                "created_at": "2026-01-01",
                "updated_at": "2026-01-02",
            }
        if memory_id == 9:
            return {
                "id": 9,
                "doc_id": "uuid-9",
                "text": "replacement",
                "metadata": {"status": "active"},
                "created_at": "2026-01-04",
                "updated_at": "2026-01-04",
            }
        return None

    engine.get_memory_record = get_memory_record
    api = PluginPageApi(_FakePlugin(engine))

    try:
        with _patch_trace_request({}):
            result = await api.get_memory_lineage("5")
    finally:
        await db.close()

    assert result["status"] == "ok"
    data = result["data"]
    assert data["memory"]["id"] == 5
    assert data["sources"]["count"] == 1
    assert data["sources"]["source_window"] == {"start_index": 1}
    assert [item["id"] for item in data["supersedes"]["chain"]] == [5, 9]
    assert data["supersedes"]["superseded_by"] == 9
    assert [item["id"] for item in data["supersedes"]["of"]] == [11]
    assert data["graph"]["nodes"] == [{"id": 1}]
    assert trace_id in {row["id"] for row in data["traces"]}


@pytest.mark.asyncio
async def test_lineage_rejects_unknown_memory() -> None:
    engine = SimpleNamespace(
        db_connection=None,
        trace_recorder=None,
        graph_store=None,
        get_memory_record=AsyncMock(return_value=None),
    )
    api = PluginPageApi(_FakePlugin(engine))

    with _patch_trace_request({}):
        result = await api.get_memory_lineage("5")

    assert result["status"] == "error"
