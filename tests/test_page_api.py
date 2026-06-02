"""
Tests for PluginPageApi — WebUI REST API endpoints and helpers.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from astrbot_plugin_livingmemory.core.page_api import (
    PAGE_API_PREFIX,
    PLUGIN_NAME,
    PluginPageApi,
)

# ---------------------------------------------------------------------------
# Fake / stub helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeMemoryEngine:
    db_path: str = ":memory:"
    graph_store: Any = None
    stats: dict = field(
        default_factory=lambda: {
            "total_memories": 42,
            "status_breakdown": {"active": 30, "archived": 8, "deleted": 4},
            "sessions": {"s1": 10, "s2": 5},
        }
    )

    async def get_statistics(self):
        return self.stats

    async def search_memories(self, query, k=5, session_id=None, persona_id=None):
        return []

    async def get_memory(self, memory_id: int):
        return None

    async def add_memory(self, **kwargs):
        return 999

    async def delete_memory(self, memory_id: int):
        return True

    async def update_memory(self, memory_id: int, updates: dict):
        return True

    async def batch_delete_memories(self, memory_ids: list[int]):
        return len(memory_ids)

    async def close(self):
        pass


class FakeInitializer:
    def __init__(self):
        self.memory_engine = FakeMemoryEngine()
        self.conversation_manager = None
        self.index_validator = None
        self.data_dir = "/tmp/test_plugin"


class FakePlugin:
    def __init__(self, *, ready=True, memory_engine=None):
        self._ready = ready
        self._fail_message = "" if ready else "插件尚未就绪"
        self.initializer = FakeInitializer()
        if memory_engine:
            self.initializer.memory_engine = memory_engine
        self._api_routes = []

    async def _ensure_plugin_ready(self):
        return self._ready, self._fail_message

    @property
    def context(self):
        ctx = MagicMock()

        def _register(route, handler, methods, desc):
            self._api_routes.append((route, handler, methods, desc))

        ctx.register_web_api = _register
        return ctx


# ---------------------------------------------------------------------------
# Safe request mocking — Quart's request is a LocalProxy that throws
# RuntimeError when accessed outside a request context. Use patch.dict
# on the module's __dict__ to replace it without triggering the proxy.
# ---------------------------------------------------------------------------


def _mock_page_request(**overrides):
    """Build a MagicMock suitable for standing in as ``quart.request``.

    *overrides* can include ``args`` (dict), ``get_json`` (return value),
    and ``method`` (str).  All keys are optional.
    """
    req = MagicMock()

    args_mock = MagicMock()
    args_dict = overrides.get("args", {})
    args_mock.get.side_effect = lambda key, default=None: args_dict.get(key, default)
    req.args = args_mock

    json_value = overrides.get("get_json", {})
    req.get_json = AsyncMock(return_value=json_value)

    req.method = overrides.get("method", "GET")
    return req


@contextmanager
def _patch_page_request(req: MagicMock):
    """Temporarily replace ``page_api.request`` with *req*."""
    import astrbot_plugin_livingmemory.core.page_api as mod

    ns = vars(mod)
    old = ns.get("request")
    ns["request"] = req
    try:
        yield
    finally:
        if old is not None:
            ns["request"] = old
        else:
            ns.pop("request", None)


# Alias for brevity in tests
def _qp(req=None, **kw):
    """Quick-patch: ``with _qp(mock_req): ...``"""
    if req is None:
        req = _mock_page_request(**kw)
    return _patch_page_request(req)


# ---------------------------------------------------------------------------
# Helper method unit tests (no plugin needed)
# ---------------------------------------------------------------------------


class TestResponseHelpers:
    def test_ok_returns_status_format(self):
        result = PluginPageApi._ok({"items": [1, 2]})
        assert result == {"status": "ok", "data": {"items": [1, 2]}}

    def test_ok_defaults_to_none_data(self):
        result = PluginPageApi._ok()
        assert result == {"status": "ok", "data": None}

    def test_error_returns_status_format(self):
        result = PluginPageApi._error("something went wrong")
        assert result == {"status": "error", "message": "something went wrong"}

    def test_error_converts_non_string(self):
        result = PluginPageApi._error(ValueError("boom"))
        assert result["status"] == "error"
        assert "boom" in result["message"]


class TestNumberHelpers:
    def test_importance_to_display_handles_non_numeric_values(self):
        assert PluginPageApi._importance_to_display("default") == 5.0


class TestNormalizeMetadata:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ({"key": "value"}, {"key": "value"}),
            (None, {}),
            ("", {}),
            ('{"a":1}', {"a": 1}),
            ("not-json", {}),
            (123, {}),
            ([1, 2, 3], {}),  # valid JSON but not a dict
        ],
    )
    def test_normalize_metadata(self, raw, expected):
        assert PluginPageApi._normalize_metadata(raw) == expected


class TestTokenizeGraphQuery:
    def test_empty_query(self):
        assert PluginPageApi._tokenize_graph_query("") == []
        assert PluginPageApi._tokenize_graph_query("   ") == []

    def test_english_query(self):
        tokens = PluginPageApi._tokenize_graph_query("machine learning")
        assert "machine" in tokens
        assert "learning" in tokens

    def test_chinese_query(self):
        tokens = PluginPageApi._tokenize_graph_query("人工智能发展")
        assert len(tokens) >= 1

    def test_short_tokens_filtered(self):
        tokens = PluginPageApi._tokenize_graph_query("a b c d")
        assert all(len(t) >= 2 for t in tokens)

    def test_caps_returns_at_most_12(self):
        tokens = PluginPageApi._tokenize_graph_query(
            "a b c d e f g h i j k l m n o p q r s t u v w x y z"
        )
        assert len(tokens) <= 12

    def test_mixed_chinese_english(self):
        tokens = PluginPageApi._tokenize_graph_query("AI and 机器学习")
        assert len(tokens) >= 1


class TestBuildGraphViewPayload:
    def test_basic_structure(self):
        snapshot = {"nodes": [], "edges": [], "entries": [], "memories": []}
        stats = {"graph_nodes": 0, "graph_edges": 0, "graph_entries": 0}
        result = PluginPageApi._build_graph_view_payload(
            snapshot,
            stats,
            enabled=True,
            mode="overview",
            filters={},
        )
        assert result["enabled"] is True
        assert result["mode"] == "overview"
        assert "summary" in result
        assert "snapshot" in result
        assert "retrieval" in result

    def test_nodes_get_highlighted(self):
        snapshot = {
            "nodes": [
                {"id": 1, "type": "topic", "weight": 0.8, "degree": 3, "label": "AI"}
            ],
            "edges": [],
            "entries": [],
            "memories": [],
        }
        stats = {"graph_nodes": 1, "graph_edges": 0, "graph_entries": 0}
        result = PluginPageApi._build_graph_view_payload(
            snapshot,
            stats,
            enabled=True,
            mode="query",
            matched_node_ids=[1],
            filters={},
        )
        assert result["snapshot"]["nodes"][0]["highlighted"] is True

    def test_top_nodes_sorted_by_weight(self):
        snapshot = {
            "nodes": [
                {"id": 1, "type": "topic", "weight": 0.3, "degree": 1, "label": "B"},
                {"id": 2, "type": "topic", "weight": 0.9, "degree": 5, "label": "A"},
            ],
            "edges": [],
            "entries": [],
            "memories": [],
        }
        stats = {"graph_nodes": 2, "graph_edges": 0, "graph_entries": 0}
        result = PluginPageApi._build_graph_view_payload(
            snapshot,
            stats,
            enabled=True,
            mode="overview",
            filters={},
        )
        top = result["top_nodes"]
        assert top[0]["id"] == 2  # higher weight first

    def test_node_type_breakdown(self):
        snapshot = {
            "nodes": [
                {"id": 1, "type": "topic"},
                {"id": 2, "type": "topic"},
                {"id": 3, "type": "person"},
            ],
            "edges": [],
            "entries": [],
            "memories": [],
        }
        stats = {"graph_nodes": 3, "graph_edges": 0, "graph_entries": 0}
        result = PluginPageApi._build_graph_view_payload(
            snapshot,
            stats,
            enabled=True,
            mode="overview",
            filters={},
        )
        breakdown = result["summary"]["node_type_breakdown"]
        assert breakdown.get("topic") == 2
        assert breakdown.get("person") == 1

    def test_non_numeric_weights_and_importance_do_not_break_sorting(self):
        snapshot = {
            "nodes": [
                {"id": 1, "type": "topic", "weight": "auto", "degree": 1},
                {"id": 2, "type": "topic", "weight": 0.9, "degree": 0},
            ],
            "edges": [],
            "entries": [],
            "memories": [
                {"memory_id": 1, "importance": "default", "entry_count": 1},
                {"memory_id": 2, "importance": 0.8, "entry_count": 1},
            ],
        }
        stats = {"graph_nodes": 2, "graph_edges": 0, "graph_entries": 0}

        result = PluginPageApi._build_graph_view_payload(
            snapshot,
            stats,
            enabled=True,
            mode="overview",
            filters={},
        )

        assert result["top_nodes"][0]["id"] == 2
        assert result["top_memories"][0]["memory_id"] == 2


class TestGetGraphStore:
    def test_returns_graph_store_attribute(self):
        engine = FakeMemoryEngine()
        engine.graph_store = object()
        assert PluginPageApi._get_graph_store(engine) is engine.graph_store

    def test_returns_none_when_no_graph_store(self):
        engine = FakeMemoryEngine()
        engine.graph_store = None
        assert PluginPageApi._get_graph_store(engine) is None


# ---------------------------------------------------------------------------
# Endpoint tests (with mocked plugin)
# ---------------------------------------------------------------------------


@pytest.fixture
def api():
    plugin = FakePlugin()
    return PluginPageApi(plugin)


@pytest.fixture
def api_not_ready():
    plugin = FakePlugin(ready=False)
    return PluginPageApi(plugin)


class TestGetStats:
    @pytest.mark.asyncio
    async def test_returns_stats(self, api):
        result = await api.get_stats()
        assert result["status"] == "ok"
        assert result["data"]["total_memories"] == 42
        assert result["data"]["status_breakdown"]["active"] == 30

    @pytest.mark.asyncio
    async def test_plugin_not_ready(self, api_not_ready):
        result = await api_not_ready.get_stats()
        assert result["status"] == "error"
        assert "尚未就绪" in result["message"]


class TestListMemories:
    @pytest.mark.asyncio
    async def test_missing_db_path(self, api):
        api.plugin.initializer.memory_engine.db_path = None
        req = _mock_page_request(
            args={
                "page": "1",
                "page_size": "20",
                "session_id": "",
                "keyword": "",
                "status": "all",
            }
        )
        with _patch_page_request(req):
            result = await api.list_memories()
        assert result["status"] == "error"
        assert "db_path" in result["message"]

    @pytest.mark.asyncio
    async def test_invalid_pagination(self, api):
        req = _mock_page_request(
            args={
                "page": "not-a-number",
                "page_size": "20",
                "session_id": "",
                "keyword": "",
                "status": "all",
            }
        )
        with _patch_page_request(req):
            result = await api.list_memories()
        assert result["status"] == "error"
        assert "分页" in result["message"]

    @pytest.mark.asyncio
    async def test_valid_request(self, api):
        req = _mock_page_request(
            args={
                "page": "1",
                "page_size": "20",
                "session_id": "",
                "keyword": "",
                "status": "all",
            }
        )
        with _patch_page_request(req):
            with patch(
                "astrbot_plugin_livingmemory.core.page_api.aiosqlite"
            ) as mock_sqlite:
                mock_conn = AsyncMock()
                mock_conn.execute.return_value = mock_conn
                mock_conn.fetchone.return_value = {"total": 0}
                mock_conn.fetchall.return_value = []
                mock_sqlite.connect.return_value.__aenter__.return_value = mock_conn
                mock_sqlite.Row = dict

                result = await api.list_memories()
        assert result["status"] == "ok"
        assert result["data"]["total"] == 0
        assert result["data"]["items"] == []

    @pytest.mark.asyncio
    async def test_plugin_not_ready(self, api_not_ready):
        req = _mock_page_request()
        with _patch_page_request(req):
            result = await api_not_ready.list_memories()
        assert result["status"] == "error"


class TestUpdateMemory:
    @pytest.mark.asyncio
    async def test_missing_memory_id(self, api):
        req = _mock_page_request(get_json={"field": "importance", "value": 0.8})
        with _patch_page_request(req):
            result = await api.update_memory()
        assert result["status"] == "error"
        assert "memory_id" in result["message"]

    @pytest.mark.asyncio
    async def test_memory_id_not_integer(self, api):
        req = _mock_page_request(
            get_json={"memory_id": "abc", "field": "importance", "value": 0.8}
        )
        with _patch_page_request(req):
            result = await api.update_memory()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_missing_field_or_value(self, api):
        req = _mock_page_request(get_json={"memory_id": 1})
        with _patch_page_request(req):
            result = await api.update_memory()
        assert result["status"] == "error"
        assert "field" in result["message"]

    @pytest.mark.asyncio
    async def test_unsupported_field(self, api):
        req = _mock_page_request(
            get_json={
                "memory_id": 1,
                "field": "unsupported",
                "value": "x",
            }
        )
        with _patch_page_request(req):
            with patch.object(
                PluginPageApi,
                "_get_memory_record",
                return_value={"id": 1, "text": "hello", "metadata": {}},
            ):
                result = await api.update_memory()
        assert result["status"] == "error"
        assert "不支持" in result["message"]

    @pytest.mark.asyncio
    async def test_importance_out_of_range(self, api):
        req = _mock_page_request(
            get_json={
                "memory_id": 1,
                "field": "importance",
                "value": 100,
            }
        )
        with _patch_page_request(req):
            with patch.object(
                PluginPageApi,
                "_get_memory_record",
                return_value={"id": 1, "text": "hello", "metadata": {}},
            ):
                result = await api.update_memory()
        assert result["status"] == "error"
        assert "重要性" in result["message"]

    @pytest.mark.asyncio
    async def test_importance_valid_range(self, api):
        req = _mock_page_request(
            get_json={
                "memory_id": 1,
                "field": "importance",
                "value": 8.5,
            }
        )
        with _patch_page_request(req):
            with patch.object(
                PluginPageApi,
                "_get_memory_record",
                return_value={"id": 1, "text": "hello", "metadata": {}},
            ):
                result = await api.update_memory()
        assert result["status"] == "ok"
        assert result["data"]["field"] == "importance"

    @pytest.mark.asyncio
    async def test_status_invalid_value(self, api):
        req = _mock_page_request(
            get_json={
                "memory_id": 1,
                "field": "status",
                "value": "invalid",
            }
        )
        with _patch_page_request(req):
            with patch.object(
                PluginPageApi,
                "_get_memory_record",
                return_value={"id": 1, "text": "hello", "metadata": {}},
            ):
                result = await api.update_memory()
        assert result["status"] == "error"
        assert "状态" in result["message"]

    @pytest.mark.asyncio
    async def test_memory_not_found(self, api):
        req = _mock_page_request(
            get_json={
                "memory_id": 999,
                "field": "importance",
                "value": 0.5,
            }
        )
        with _patch_page_request(req):
            with patch.object(PluginPageApi, "_get_memory_record", return_value=None):
                result = await api.update_memory()
        assert result["status"] == "error"
        assert "不存在" in result["message"]

    @pytest.mark.asyncio
    async def test_content_update_empty_value(self, api):
        req = _mock_page_request(
            get_json={
                "memory_id": 1,
                "field": "content",
                "value": "   ",
            }
        )
        with _patch_page_request(req):
            memory = {
                "id": 1,
                "text": "hello",
                "metadata": {"session_id": "s1", "persona_id": "p1", "importance": 0.5},
            }
            with patch.object(PluginPageApi, "_get_memory_record", return_value=memory):
                result = await api.update_memory()
        assert result["status"] == "error"
        assert "不能为空" in result["message"]

    @pytest.mark.asyncio
    async def test_content_update_uses_default_for_legacy_importance(self, api):
        req = _mock_page_request(
            get_json={
                "memory_id": 1,
                "field": "content",
                "value": "new content",
            }
        )
        with _patch_page_request(req):
            memory = {
                "id": 1,
                "text": "old content",
                "metadata": {
                    "session_id": "s1",
                    "persona_id": "p1",
                    "importance": "default",
                },
            }
            with patch.object(PluginPageApi, "_get_memory_record", return_value=memory):
                api.plugin.initializer.memory_engine.add_memory = AsyncMock(
                    return_value=999
                )
                result = await api.update_memory()

        assert result["status"] == "ok"
        assert api.plugin.initializer.memory_engine.add_memory.call_args.kwargs[
            "importance"
        ] == 0.5


class TestBatchDeleteMemories:
    @pytest.mark.asyncio
    async def test_empty_list(self, api):
        req = _mock_page_request(get_json={"memory_ids": []})
        with _patch_page_request(req):
            result = await api.batch_delete_memories()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_invalid_type(self, api):
        req = _mock_page_request(get_json={"memory_ids": "not-a-list"})
        with _patch_page_request(req):
            result = await api.batch_delete_memories()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_valid_delete(self, api):
        req = _mock_page_request(get_json={"memory_ids": [1, 2, 3]})
        with _patch_page_request(req):
            result = await api.batch_delete_memories()
        assert result["status"] == "ok"
        assert result["data"]["deleted_count"] == 3
        assert result["data"]["total"] == 3

    @pytest.mark.asyncio
    async def test_mixed_valid_invalid_ids(self, api):
        req = _mock_page_request(get_json={"memory_ids": [1, "abc", 3]})
        with _patch_page_request(req):
            result = await api.batch_delete_memories()
        assert result["status"] == "ok"
        assert result["data"]["failed_count"] == 1
        assert "abc" in result["data"]["failed_ids"]


class TestTestRecall:
    @pytest.mark.asyncio
    async def test_empty_query(self, api):
        req = _mock_page_request(get_json={"query": "", "k": 5})
        with _patch_page_request(req):
            result = await api.test_recall()
        assert result["status"] == "error"
        assert "不能为空" in result["message"]

    @pytest.mark.asyncio
    async def test_invalid_k(self, api):
        req = _mock_page_request(get_json={"query": "hello", "k": "abc"})
        with _patch_page_request(req):
            result = await api.test_recall()
        assert result["status"] == "error"
        assert "k" in result["message"]

    @pytest.mark.asyncio
    async def test_valid_recall(self, api):
        req = _mock_page_request(get_json={"query": "test", "k": 5})
        with _patch_page_request(req):
            result = await api.test_recall()
        assert result["status"] == "ok"
        assert result["data"]["query"] == "test"
        assert result["data"]["total"] == 0

    @pytest.mark.asyncio
    async def test_recall_includes_score_breakdown_for_dashboard(self, api):
        api.plugin.initializer.memory_engine.search_memories = AsyncMock(
            return_value=[
                SimpleNamespace(
                    doc_id=7,
                    content="memory",
                    final_score=0.42,
                    metadata={"session_id": "s1"},
                    score_breakdown={
                        "document_keyword_score": 0.1,
                        "document_vector_score": 0.2,
                        "graph_keyword_score": 0.3,
                        "graph_vector_score": 0.4,
                    },
                )
            ]
        )
        req = _mock_page_request(get_json={"query": "test", "k": 5})
        with _patch_page_request(req):
            result = await api.test_recall()

        item = result["data"]["results"][0]
        assert item["score_breakdown"]["graph_vector_score"] == 0.4
        assert item["metadata"]["document_keyword_score"] == 0.1


class TestGraphEndpoints:
    @pytest.mark.asyncio
    async def test_overview_invalid_params(self, api):
        req = _mock_page_request(
            args={
                "session_id": "",
                "persona_id": "",
                "limit_memories": "abc",
                "limit_entries": "36",
                "limit_nodes": "48",
                "limit_edges": "72",
            }
        )
        with _patch_page_request(req):
            result = await api.get_graph_overview()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_overview_no_graph_store(self, api):
        req = _mock_page_request(
            args={
                "session_id": "",
                "persona_id": "",
                "limit_memories": "12",
                "limit_entries": "36",
                "limit_nodes": "48",
                "limit_edges": "72",
            }
        )
        with _patch_page_request(req):
            result = await api.get_graph_overview()
        assert result["status"] == "ok"
        assert result["data"]["enabled"] is False

    @pytest.mark.asyncio
    async def test_query_invalid_params(self, api):
        req = _mock_page_request(
            get_json={
                "query": "test",
                "limit_memories": "abc",
            }
        )
        with _patch_page_request(req):
            result = await api.query_graph()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_query_empty_no_graph_store(self, api):
        req = _mock_page_request(
            get_json={
                "query": "",
                "session_id": "",
                "limit_memories": 10,
                "limit_entries": 40,
                "limit_nodes": 56,
                "limit_edges": 96,
            }
        )
        with _patch_page_request(req):
            result = await api.query_graph()
        assert result["status"] == "ok"
        assert result["data"]["enabled"] is False


class TestListBackups:
    @pytest.mark.asyncio
    async def test_no_data_dir(self, api):
        api.plugin.initializer.data_dir = ""
        result = await api.list_backups()
        assert result["data"]["backups"] == []
        assert result["data"]["total"] == 0

    @pytest.mark.asyncio
    async def test_no_initializer(self, api):
        api.plugin.initializer = None
        result = await api.list_backups()
        assert result["data"]["backups"] == []
        assert result["data"]["total"] == 0

    @pytest.mark.asyncio
    async def test_with_backup_dir(self, api):
        with patch(
            "astrbot_plugin_livingmemory.core.page_api.BackupManager.list_backups",
            return_value=[],
        ):
            result = await api.list_backups()
        assert result["data"]["backups"] == []
        assert result["data"]["total"] == 0


# ---------------------------------------------------------------------------
# Ensure plugin ready helper
# ---------------------------------------------------------------------------


class TestEnsurePluginReady:
    @pytest.mark.asyncio
    async def test_ready_returns_components(self, api):
        components, error = await api._ensure_plugin_ready()
        assert error is None
        assert components is not None
        assert "memory_engine" in components
        assert isinstance(components["memory_engine"], FakeMemoryEngine)

    @pytest.mark.asyncio
    async def test_not_ready_returns_error(self, api_not_ready):
        components, error = await api_not_ready._ensure_plugin_ready()
        assert components is None
        assert error is not None
        assert error["status"] == "error"

    @pytest.mark.asyncio
    async def test_no_memory_engine(self, api):
        api.plugin.initializer.memory_engine = None
        components, error = await api._ensure_plugin_ready()
        assert components is None
        assert error["status"] == "error"
        assert "未初始化" in error["message"]


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


class TestRouteRegistration:
    def test_registers_all_ten_routes(self):
        plugin = FakePlugin()
        api = PluginPageApi(plugin)
        api.register_routes()
        assert len(plugin._api_routes) == 10

        paths = {route for route, _, _, _ in plugin._api_routes}
        prefix = PAGE_API_PREFIX
        assert f"{prefix}/stats" in paths
        assert f"{prefix}/memories" in paths
        assert f"{prefix}/memories/update" in paths
        assert f"{prefix}/memories/batch-delete" in paths
        assert f"{prefix}/recall/test" in paths
        assert f"{prefix}/graph/overview" in paths
        assert f"{prefix}/graph/query" in paths
        assert f"{prefix}/backups" in paths

    def test_route_prefix_contains_plugin_name(self):
        assert PLUGIN_NAME in PAGE_API_PREFIX
