"""
Tests for PluginPageApi — WebUI REST API endpoints and helpers.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
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

    async def replace_memory(self, memory_id: int, **kwargs):
        return 999

    async def get_memory_source(self, memory_id: int):
        return []

    async def get_memory_transfer_records(self, memory_ids=None):
        return []

    async def get_memory_import_keys(self):
        return set()

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
        self.memory_processor = None
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
    import astrbot_plugin_livingmemory.core.page_api_modules.graph_handler as graph_mod
    import astrbot_plugin_livingmemory.core.page_api_modules.memory_handler as memory_mod
    import astrbot_plugin_livingmemory.core.page_api_modules.recall_handler as recall_mod

    # Patch all modules that use request
    modules = [mod, memory_mod, recall_mod, graph_mod]
    old_values = []

    for module in modules:
        ns = vars(module)
        old_values.append((ns, ns.get("request")))
        ns["request"] = req

    try:
        yield
    finally:
        for ns, old in old_values:
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
        from astrbot_plugin_livingmemory.core.page_api_modules import PageApiUtils

        utils = PageApiUtils()
        result = utils.ok({"items": [1, 2]})
        assert result == {"status": "ok", "data": {"items": [1, 2]}}

    def test_ok_defaults_to_none_data(self):
        from astrbot_plugin_livingmemory.core.page_api_modules import PageApiUtils

        utils = PageApiUtils()
        result = utils.ok()
        assert result == {"status": "ok", "data": None}

    def test_error_returns_status_format(self):
        from astrbot_plugin_livingmemory.core.page_api_modules import PageApiUtils

        utils = PageApiUtils()
        result = utils.error("something went wrong")
        assert result == {"status": "error", "message": "something went wrong"}

    def test_error_converts_non_string(self):
        from astrbot_plugin_livingmemory.core.page_api_modules import PageApiUtils

        utils = PageApiUtils()
        result = utils.error(ValueError("boom"))
        assert result["status"] == "error"
        assert "boom" in result["message"]


class TestNumberHelpers:
    def test_importance_to_display_handles_non_numeric_values(self):
        from astrbot_plugin_livingmemory.core.page_api_modules import PageApiUtils

        utils = PageApiUtils()
        assert utils.importance_to_display("default") == 5.0


class TestOptionalText:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, None),
            ("", None),
            ("   ", None),
            ("None", None),
            ("null", None),
            ("undefined", None),
            (" s1 ", "s1"),
        ],
    )
    def test_optional_filter_values(self, raw, expected):
        from astrbot_plugin_livingmemory.core.page_api_modules import PageApiUtils

        utils = PageApiUtils()
        assert utils.optional_text(raw) == expected


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
        from astrbot_plugin_livingmemory.core.page_api_modules import PageApiUtils

        utils = PageApiUtils()
        assert utils.normalize_metadata(raw) == expected


class TestTokenizeGraphQuery:
    def test_empty_query(self):
        from astrbot_plugin_livingmemory.core.page_api_modules import PageApiUtils

        utils = PageApiUtils()
        assert utils.tokenize_graph_query("") == []
        assert utils.tokenize_graph_query("   ") == []

    def test_english_query(self):
        from astrbot_plugin_livingmemory.core.page_api_modules import PageApiUtils

        utils = PageApiUtils()
        tokens = utils.tokenize_graph_query("machine learning")
        assert "machine" in tokens
        assert "learning" in tokens

    def test_chinese_query(self):
        from astrbot_plugin_livingmemory.core.page_api_modules import PageApiUtils

        utils = PageApiUtils()
        tokens = utils.tokenize_graph_query("人工智能发展")
        assert len(tokens) >= 1

    def test_short_tokens_filtered(self):
        from astrbot_plugin_livingmemory.core.page_api_modules import PageApiUtils

        utils = PageApiUtils()
        tokens = utils.tokenize_graph_query("a b c d")
        assert all(len(t) >= 2 for t in tokens)

    def test_caps_returns_at_most_12(self):
        from astrbot_plugin_livingmemory.core.page_api_modules import PageApiUtils

        utils = PageApiUtils()
        tokens = utils.tokenize_graph_query(
            "a b c d e f g h i j k l m n o p q r s t u v w x y z"
        )
        assert len(tokens) <= 12

    def test_mixed_chinese_english(self):
        from astrbot_plugin_livingmemory.core.page_api_modules import PageApiUtils

        utils = PageApiUtils()
        tokens = utils.tokenize_graph_query("AI and 机器学习")
        assert len(tokens) >= 1


class TestBuildGraphViewPayload:
    def test_basic_structure(self):
        from astrbot_plugin_livingmemory.core.page_api_modules import PageApiUtils

        utils = PageApiUtils()
        snapshot = {"nodes": [], "edges": [], "entries": [], "memories": []}
        stats = {"graph_nodes": 0, "graph_edges": 0, "graph_entries": 0}
        result = utils.build_graph_view_payload(
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
        from astrbot_plugin_livingmemory.core.page_api_modules import PageApiUtils

        utils = PageApiUtils()
        snapshot = {
            "nodes": [
                {"id": 1, "type": "topic", "weight": 0.8, "degree": 3, "label": "AI"}
            ],
            "edges": [],
            "entries": [],
            "memories": [],
        }
        stats = {"graph_nodes": 1, "graph_edges": 0, "graph_entries": 0}
        result = utils.build_graph_view_payload(
            snapshot,
            stats,
            enabled=True,
            mode="query",
            matched_node_ids=[1],
            filters={},
        )
        assert result["snapshot"]["nodes"][0]["highlighted"] is True

    def test_top_nodes_sorted_by_weight(self):
        from astrbot_plugin_livingmemory.core.page_api_modules import PageApiUtils

        utils = PageApiUtils()
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
        result = utils.build_graph_view_payload(
            snapshot,
            stats,
            enabled=True,
            mode="overview",
            filters={},
        )
        top = result["top_nodes"]
        assert top[0]["id"] == 2  # higher weight first

    def test_node_type_breakdown(self):
        from astrbot_plugin_livingmemory.core.page_api_modules import PageApiUtils

        utils = PageApiUtils()
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
        result = utils.build_graph_view_payload(
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
        from astrbot_plugin_livingmemory.core.page_api_modules import PageApiUtils

        utils = PageApiUtils()
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

        result = utils.build_graph_view_payload(
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
        from astrbot_plugin_livingmemory.core.page_api_modules import PageApiUtils

        utils = PageApiUtils()
        engine = FakeMemoryEngine()
        engine.graph_store = object()
        assert utils.get_graph_store(engine) is engine.graph_store

    def test_returns_none_when_no_graph_store(self):
        from astrbot_plugin_livingmemory.core.page_api_modules import PageApiUtils

        utils = PageApiUtils()
        engine = FakeMemoryEngine()
        engine.graph_store = None
        assert utils.get_graph_store(engine) is None


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
                "astrbot_plugin_livingmemory.core.page_api_modules.memory_handler.aiosqlite"
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
    async def test_type_filter_and_sort_are_applied_in_sql(self, api, tmp_path):
        db_path = tmp_path / "memories.db"
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                """
                CREATE TABLE documents (
                    id INTEGER PRIMARY KEY,
                    doc_id TEXT,
                    text TEXT,
                    metadata TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )
            rows = [
                (
                    1,
                    "1",
                    "low preference",
                    {"memory_type": "PREFERENCE", "importance": 0.3, "create_time": 10},
                ),
                (
                    2,
                    "2",
                    "high preference",
                    {"memory_type": "PREFERENCE", "importance": 0.9, "create_time": 20},
                ),
                (
                    3,
                    "3",
                    "other fact",
                    {"memory_type": "FACT", "importance": 1.0, "create_time": 30},
                ),
            ]
            for memory_id, doc_id, text, metadata in rows:
                await db.execute(
                    """
                    INSERT INTO documents
                        (id, doc_id, text, metadata, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        memory_id,
                        doc_id,
                        text,
                        json.dumps(metadata),
                        "created",
                        "updated",
                    ),
                )
            await db.commit()

        api.plugin.initializer.memory_engine.db_path = str(db_path)
        req = _mock_page_request(
            args={
                "page": "1",
                "page_size": "20",
                "session_id": "",
                "keyword": "",
                "status": "all",
                "type": "PREFERENCE",
                "sort": "importance_desc",
            }
        )

        with _patch_page_request(req):
            result = await api.list_memories()

        assert result["status"] == "ok"
        assert result["data"]["total"] == 2
        assert result["data"]["filters"]["type"] == "PREFERENCE"
        assert result["data"]["sort"] == "importance_desc"
        assert [item["id"] for item in result["data"]["items"]] == [2, 1]

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
            with patch(
                "astrbot_plugin_livingmemory.core.page_api_modules.memory_handler.MemoryHandler._get_memory_record",
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
            with patch(
                "astrbot_plugin_livingmemory.core.page_api_modules.memory_handler.MemoryHandler._get_memory_record",
                return_value={"id": 1, "text": "hello", "metadata": {}},
            ):
                result = await api.update_memory()
        assert result["status"] == "error"
        assert "重要性" in result["message"]

    @pytest.mark.asyncio
    async def test_importance_valid_range(self, api):
        api.plugin.initializer.memory_engine.update_memory = AsyncMock(return_value=True)
        req = _mock_page_request(
            get_json={
                "memory_id": 1,
                "field": "importance",
                "value": 8.5,
            }
        )
        with _patch_page_request(req):
            with patch(
                "astrbot_plugin_livingmemory.core.page_api_modules.memory_handler.MemoryHandler._get_memory_record",
                return_value={"id": 1, "text": "hello", "metadata": {}},
            ):
                result = await api.update_memory()
        assert result["status"] == "ok"
        assert result["data"]["field"] == "importance"
        api.plugin.initializer.memory_engine.update_memory.assert_awaited_once()
        assert (
            api.plugin.initializer.memory_engine.update_memory.call_args.args[1][
                "importance"
            ]
            == 0.85
        )

    @pytest.mark.asyncio
    async def test_importance_display_scale_preserves_one_point_zero(self, api):
        api.plugin.initializer.memory_engine.update_memory = AsyncMock(return_value=True)
        req = _mock_page_request(
            get_json={
                "memory_id": 1,
                "field": "importance",
                "value": 1.0,
                "value_scale": "display",
            }
        )
        with _patch_page_request(req):
            with patch(
                "astrbot_plugin_livingmemory.core.page_api_modules.memory_handler.MemoryHandler._get_memory_record",
                return_value={"id": 1, "text": "hello", "metadata": {}},
            ):
                result = await api.update_memory()

        assert result["status"] == "ok"
        updates = api.plugin.initializer.memory_engine.update_memory.call_args.args[1]
        assert updates["importance"] == 0.1

    @pytest.mark.asyncio
    async def test_importance_auto_scale_keeps_legacy_normalized_value(self, api):
        api.plugin.initializer.memory_engine.update_memory = AsyncMock(return_value=True)
        req = _mock_page_request(
            get_json={
                "memory_id": 1,
                "field": "importance",
                "value": 0.8,
            }
        )
        with _patch_page_request(req):
            with patch(
                "astrbot_plugin_livingmemory.core.page_api_modules.memory_handler.MemoryHandler._get_memory_record",
                return_value={"id": 1, "text": "hello", "metadata": {}},
            ):
                result = await api.update_memory()

        assert result["status"] == "ok"
        updates = api.plugin.initializer.memory_engine.update_memory.call_args.args[1]
        assert updates["importance"] == 0.8

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
            with patch(
                "astrbot_plugin_livingmemory.core.page_api_modules.memory_handler.MemoryHandler._get_memory_record",
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
            with patch(
                "astrbot_plugin_livingmemory.core.page_api_modules.memory_handler.MemoryHandler._get_memory_record",
                return_value=None,
            ):
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
            with patch(
                "astrbot_plugin_livingmemory.core.page_api_modules.memory_handler.MemoryHandler._get_memory_record",
                return_value=memory,
            ):
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
            with patch(
                "astrbot_plugin_livingmemory.core.page_api_modules.memory_handler.MemoryHandler._get_memory_record",
                return_value=memory,
            ):
                api.plugin.initializer.memory_engine.replace_memory = AsyncMock(
                    return_value=999
                )
                result = await api.update_memory()

        assert result["status"] == "ok"
        assert (
            api.plugin.initializer.memory_engine.replace_memory.call_args.kwargs[
                "importance"
            ]
            == 0.5
        )

    @pytest.mark.asyncio
    async def test_structured_update_replaces_topics_and_key_facts_once(self, api):
        engine = api.plugin.initializer.memory_engine
        engine.replace_memory = AsyncMock(return_value=42)
        req = _mock_page_request(
            get_json={
                "memory_id": 1,
                "field": "structured",
                "value": {
                    "content": "new summary",
                    "topics": ["release", "release", " deployment "],
                    "key_facts": ["Release is Friday"],
                    "status": "active",
                    "type": "EVENT",
                    "importance": 8,
                },
                "value_scale": "display",
                "reason": "correct summary",
            }
        )
        memory = {
            "id": 1,
            "text": "old summary",
            "metadata": {
                "topics": ["old"],
                "key_facts": ["old fact"],
                "status": "active",
                "memory_type": "GENERAL",
                "importance": 0.5,
            },
        }
        with _patch_page_request(req):
            with patch(
                "astrbot_plugin_livingmemory.core.page_api_modules.memory_handler.MemoryHandler._get_memory_record",
                return_value=memory,
            ):
                result = await api.update_memory()

        assert result["status"] == "ok"
        assert result["data"]["new_memory_id"] == 42
        engine.replace_memory.assert_awaited_once()
        call = engine.replace_memory.call_args
        assert call.args == (1,)
        assert call.kwargs["content"] == "new summary"
        assert call.kwargs["importance"] == 0.8
        assert call.kwargs["metadata"]["topics"] == ["release", "deployment"]
        assert call.kwargs["metadata"]["key_facts"] == ["Release is Friday"]
        assert call.kwargs["metadata"]["canonical_summary"] == "new summary"

    @pytest.mark.asyncio
    async def test_structured_update_rejects_more_than_five_topics(self, api):
        engine = api.plugin.initializer.memory_engine
        engine.replace_memory = AsyncMock(return_value=42)
        req = _mock_page_request(
            get_json={
                "memory_id": 1,
                "field": "topics",
                "value": [f"topic-{index}" for index in range(6)],
            }
        )
        with _patch_page_request(req):
            with patch(
                "astrbot_plugin_livingmemory.core.page_api_modules.memory_handler.MemoryHandler._get_memory_record",
                return_value={"id": 1, "text": "summary", "metadata": {}},
            ):
                result = await api.update_memory()

        assert result["status"] == "error"
        assert "最多允许 5 项" in result["message"]
        engine.replace_memory.assert_not_awaited()


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
    async def test_overview_full_graph_uses_unlimited_snapshot(self):
        snapshot = {
            "nodes": [{"id": 1, "type": "topic", "label": "all"}],
            "edges": [],
            "entries": [],
            "memories": [],
        }
        graph_store = SimpleNamespace(
            get_full_graph_snapshot=AsyncMock(return_value=snapshot),
            get_graph_snapshot=AsyncMock(),
        )
        engine = FakeMemoryEngine(graph_store=graph_store)
        api = PluginPageApi(FakePlugin(memory_engine=engine))
        req = _mock_page_request(
            args={
                "full_graph": "true",
                "session_id": "scope-1",
                "persona_id": "persona-1",
            }
        )

        with _patch_page_request(req):
            result = await api.get_graph_overview()

        assert result["status"] == "ok"
        assert result["data"]["summary"]["visible_node_count"] == 1
        graph_store.get_full_graph_snapshot.assert_awaited_once_with(
            session_id="scope-1",
            persona_id="persona-1",
        )
        graph_store.get_graph_snapshot.assert_not_awaited()

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

    @pytest.mark.asyncio
    @pytest.mark.parametrize("session_filter", [None, "None"])
    async def test_query_expands_node_hits_without_text_recall(self, session_filter):
        snapshot = {
            "nodes": [
                {
                    "id": 56,
                    "type": "person",
                    "weight": 1.0,
                    "degree": 0,
                    "label": "luna",
                }
            ],
            "edges": [],
            "entries": [
                {
                    "id": 501,
                    "memory_id": 123,
                    "entry_type": "summary",
                    "relation_type": "mentions",
                    "content": "Luna appears in this memory",
                    "metadata": {"session_id": "s1"},
                    "node_ids": [56],
                }
            ],
            "memories": [
                {
                    "memory_id": 123,
                    "summary": "Luna appears in this memory",
                    "importance": 0.7,
                    "entry_count": 1,
                    "node_count": 1,
                    "edge_count": 0,
                }
            ],
        }
        graph_store = SimpleNamespace(
            search_nodes_by_tokens=AsyncMock(
                return_value=[
                    {
                        "id": 56,
                        "node_key": "person:luna",
                        "node_type": "person",
                        "node_value": "luna",
                        "canonical_value": "luna",
                        "metadata": {},
                    }
                ]
            ),
            get_entries_for_node_ids=AsyncMock(
                return_value=[
                    {
                        "entry_id": 501,
                        "source_memory_id": 123,
                        "content": "Luna appears in this memory",
                        "metadata": {"session_id": "s1"},
                        "score": 0.85,
                    }
                ]
            ),
            get_subgraph_for_memories=AsyncMock(return_value=snapshot),
        )
        engine = FakeMemoryEngine(graph_store=graph_store)
        engine.search_memories = AsyncMock(return_value=[])
        api = PluginPageApi(FakePlugin(memory_engine=engine))
        req = _mock_page_request(
            get_json={
                "query": "luna",
                "session_id": session_filter,
                "persona_id": "undefined",
            }
        )

        with _patch_page_request(req):
            result = await api.query_graph()

        assert result["status"] == "ok"
        data = result["data"]
        assert data["filters"]["session_id"] is None
        assert data["filters"]["persona_id"] is None
        assert data["matched_node_ids"] == [56]
        assert data["matched_memory_ids"] == [123]
        assert data["summary"]["visible_node_count"] == 1
        assert data["snapshot"]["nodes"][0]["highlighted"] is True
        assert data["retrieval"]["items"][0]["source"] == "graph_node"
        engine.search_memories.assert_awaited_once()
        assert engine.search_memories.call_args.kwargs["session_id"] is None
        graph_store.get_entries_for_node_ids.assert_awaited_once()
        assert (
            graph_store.get_entries_for_node_ids.call_args.kwargs["session_id"] is None
        )
        graph_store.get_subgraph_for_memories.assert_awaited_once()
        assert graph_store.get_subgraph_for_memories.call_args.args[0] == [123]


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
            "astrbot_plugin_livingmemory.core.managers.backup_manager.BackupManager.list_backups",
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
        assert len(plugin._api_routes) == 20

        paths = {route for route, _, _, _ in plugin._api_routes}
        prefix = PAGE_API_PREFIX
        assert f"{prefix}/stats" in paths
        assert f"{prefix}/memories" in paths
        assert f"{prefix}/memories/update" in paths
        assert f"{prefix}/memories/resummarize" in paths
        assert f"{prefix}/memories/export" in paths
        assert f"{prefix}/memories/import" in paths
        assert f"{prefix}/memories/batch-delete" in paths
        assert f"{prefix}/recall/test" in paths
        assert f"{prefix}/graph/overview" in paths
        assert f"{prefix}/graph/query" in paths
        assert f"{prefix}/backups" in paths
        assert f"{prefix}/consolidation/status" in paths
        assert f"{prefix}/consolidation/run" in paths

    def test_route_prefix_contains_plugin_name(self):
        assert PLUGIN_NAME in PAGE_API_PREFIX


@pytest.mark.asyncio
async def test_memory_detail_includes_retained_source():
    engine = FakeMemoryEngine()
    engine.get_memory_source = AsyncMock(
        return_value=[{"role": "user", "content": "exact detail"}]
    )
    api = PluginPageApi(FakePlugin(memory_engine=engine))
    api.memory_handler._get_memory_record = AsyncMock(
        return_value={
            "id": 7,
            "doc_id": "memory-7",
            "text": "summary",
            "metadata": {"importance": 0.9},
            "created_at": "2026-01-01",
            "updated_at": "2026-01-01",
        }
    )
    req = _mock_page_request(args={"memory_id": "7"})

    with _patch_page_request(req):
        result = await api.get_memory_detail()

    assert result["status"] == "ok"
    assert result["data"]["source_messages"] == [
        {"role": "user", "content": "exact detail"}
    ]
    engine.get_memory_source.assert_awaited_once_with(7)


@pytest.mark.asyncio
async def test_resummarize_memory_rebuilds_from_retained_source():
    engine = FakeMemoryEngine()
    engine.get_memory = AsyncMock(
        return_value={
            "id": 7,
            "text": "old summary",
            "metadata": {"session_id": "s1", "persona_id": "p1"},
        }
    )
    engine.get_memory_source = AsyncMock(
        return_value=[
            {
                "id": 1,
                "session_id": "s1",
                "role": "user",
                "content": "exact detail",
                "sender_id": "u1",
                "timestamp": 1.0,
                "metadata": {},
            },
            {
                "id": 2,
                "session_id": "s1",
                "role": "assistant",
                "content": "response",
                "sender_id": "bot",
                "timestamp": 2.0,
                "metadata": {"is_bot_message": True},
            },
        ]
    )
    engine.replace_memory = AsyncMock(return_value=8)
    plugin = FakePlugin(memory_engine=engine)
    processor = MagicMock()
    processor.process_conversation = AsyncMock(
        return_value=("new summary", {"topics": ["detail"]}, 0.9)
    )
    plugin.initializer.memory_processor = processor
    api = PluginPageApi(plugin)
    api.memory_handler._get_memory_record = AsyncMock(
        return_value={
            "id": 7,
            "text": "old summary",
            "metadata": {"session_id": "s1", "persona_id": "p1"},
        }
    )
    req = _mock_page_request(get_json={"memory_id": 7})

    with _patch_page_request(req):
        result = await api.resummarize_memory()

    assert result["status"] == "ok"
    assert result["data"]["new_memory_id"] == 8
    processor.process_conversation.assert_awaited_once()
    engine.replace_memory.assert_awaited_once()


@pytest.mark.asyncio
async def test_export_memories_returns_native_json_content():
    engine = FakeMemoryEngine()
    engine.get_memory_transfer_records = AsyncMock(
        return_value=[
            {
                "original_id": 7,
                "content": "portable summary",
                "importance": 0.8,
                "session_id": "s1",
                "persona_id": None,
                "metadata": {"topics": ["portable"]},
                "source_messages": [],
            }
        ]
    )
    api = PluginPageApi(FakePlugin(memory_engine=engine))
    req = _mock_page_request(
        get_json={"format": "json", "memory_ids": [7]}
    )

    with _patch_page_request(req):
        result = await api.export_memories()

    assert result["status"] == "ok"
    assert result["data"]["memory_count"] == 1
    exported = json.loads(result["data"]["content"])
    assert exported["format"] == "livingmemory"
    assert exported["memories"][0]["content"] == "portable summary"
    engine.get_memory_transfer_records.assert_awaited_once_with([7])


@pytest.mark.asyncio
async def test_import_preview_reports_duplicates_and_invalid_items():
    engine = FakeMemoryEngine()
    engine.get_memory_import_keys = AsyncMock(
        return_value={("existing summary", "s1", "")}
    )
    api = PluginPageApi(FakePlugin(memory_engine=engine))
    content = json.dumps(
        [
            {"summary": "existing summary", "session_id": "s1"},
            {"summary": "new summary", "session_id": "s1"},
            {"messages": [{"role": "user"}]},
        ]
    )
    req = _mock_page_request(
        get_json={
            "format": "json",
            "content": content,
            "dry_run": True,
            "duplicate_strategy": "skip",
        }
    )

    with _patch_page_request(req):
        result = await api.import_memories()

    assert result["status"] == "ok"
    assert result["data"]["valid_count"] == 2
    assert result["data"]["invalid_count"] == 1
    assert result["data"]["duplicate_count"] == 1
    assert result["data"]["planned_import_count"] == 1


@pytest.mark.asyncio
async def test_import_source_only_conversation_uses_processor_and_retains_source():
    engine = FakeMemoryEngine()
    engine.get_memory_import_keys = AsyncMock(return_value=set())
    engine.add_memory = AsyncMock(return_value=42)
    processor = MagicMock()
    processor.process_conversation = AsyncMock(
        return_value=("generated summary", {"topics": ["import"]}, 0.7)
    )
    processor.classify_atoms_from_metadata.return_value = []
    plugin = FakePlugin(memory_engine=engine)
    plugin.initializer.memory_processor = processor
    api = PluginPageApi(plugin)
    content = json.dumps(
        [
            {"role": "user", "content": "The code is alpha"},
            {"role": "assistant", "content": "Noted"},
        ]
    )
    req = _mock_page_request(
        get_json={
            "format": "json",
            "content": content,
            "dry_run": False,
            "duplicate_strategy": "skip",
        }
    )

    with _patch_page_request(req):
        result = await api.import_memories()

    assert result["status"] == "ok"
    assert result["data"]["imported_ids"] == [42]
    processor.process_conversation.assert_awaited_once()
    add_kwargs = engine.add_memory.await_args.kwargs
    assert add_kwargs["content"] == "generated summary"
    assert add_kwargs["importance"] == 0.7
    assert len(add_kwargs["source_messages"]) == 2
    assert add_kwargs["metadata"]["memory_origin"] == "memory_import"


@pytest.mark.asyncio
async def test_import_allow_strategy_keeps_duplicate_entries():
    engine = FakeMemoryEngine()
    engine.get_memory_import_keys = AsyncMock(
        return_value={("existing summary", "s1", "")}
    )
    engine.add_memory = AsyncMock(side_effect=[41, 42])
    api = PluginPageApi(FakePlugin(memory_engine=engine))
    content = json.dumps(
        [
            {"summary": "existing summary", "session_id": "s1"},
            {"summary": "existing summary", "session_id": "s1"},
        ]
    )
    req = _mock_page_request(
        get_json={
            "format": "json",
            "content": content,
            "dry_run": False,
            "duplicate_strategy": "allow",
        }
    )

    with _patch_page_request(req):
        result = await api.import_memories()

    assert result["status"] == "ok"
    assert result["data"]["imported_ids"] == [41, 42]
    assert result["data"]["skipped_duplicate_count"] == 0
    assert engine.add_memory.await_count == 2


@pytest.mark.asyncio
async def test_import_source_only_without_processor_reports_failure():
    engine = FakeMemoryEngine()
    engine.get_memory_import_keys = AsyncMock(return_value=set())
    engine.add_memory = AsyncMock()
    api = PluginPageApi(FakePlugin(memory_engine=engine))
    content = json.dumps(
        [
            {"role": "user", "content": "The code is alpha"},
            {"role": "assistant", "content": "Noted"},
        ]
    )
    req = _mock_page_request(
        get_json={"format": "json", "content": content, "dry_run": False}
    )

    with _patch_page_request(req):
        result = await api.import_memories()

    assert result["status"] == "ok"
    assert result["data"]["imported_count"] == 0
    assert result["data"]["failed_count"] == 1
    assert "记忆处理器未初始化" in result["data"]["errors"][0]["error"]
    engine.add_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_import_failure_does_not_stop_later_entries():
    engine = FakeMemoryEngine()
    engine.get_memory_import_keys = AsyncMock(return_value=set())
    engine.add_memory = AsyncMock(side_effect=[RuntimeError("write failed"), 52])
    api = PluginPageApi(FakePlugin(memory_engine=engine))
    content = json.dumps(
        [{"summary": "first"}, {"summary": "second"}]
    )
    req = _mock_page_request(
        get_json={"format": "json", "content": content, "dry_run": False}
    )

    with _patch_page_request(req):
        result = await api.import_memories()

    assert result["status"] == "ok"
    assert result["data"]["imported_ids"] == [52]
    assert result["data"]["failed_count"] == 1
    assert result["data"]["errors"] == [
        {"index": 0, "error": "write failed"}
    ]
    assert engine.add_memory.await_count == 2


class TestConsolidationHandler:
    @pytest.mark.asyncio
    async def test_run_delegates_to_manager(self):
        from astrbot_plugin_livingmemory.core.page_api_modules.consolidation_handler import (
            ConsolidationHandler,
        )
        from astrbot_plugin_livingmemory.core.page_api_modules.utils import PageApiUtils

        handler = ConsolidationHandler(PageApiUtils())
        manager = AsyncMock()
        manager.run_consolidation = AsyncMock(
            return_value={"groups": 2, "merged": 6, "archived": 6}
        )
        result = await handler.run(manager)
        assert result["status"] == "ok"
        assert result["data"]["merged"] == 6
        manager.run_consolidation.assert_awaited_once_with(force=True)

    @pytest.mark.asyncio
    async def test_run_returns_error_when_manager_none(self):
        from astrbot_plugin_livingmemory.core.page_api_modules.consolidation_handler import (
            ConsolidationHandler,
        )
        from astrbot_plugin_livingmemory.core.page_api_modules.utils import PageApiUtils

        handler = ConsolidationHandler(PageApiUtils())
        result = await handler.run(None)
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_get_status_returns_counts(self, tmp_path):
        from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
        from astrbot_plugin_livingmemory.core.page_api_modules.consolidation_handler import (
            ConsolidationHandler,
        )
        from astrbot_plugin_livingmemory.core.page_api_modules.utils import PageApiUtils

        db_path = str(tmp_path / "cons.db")
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "CREATE TABLE documents (id INTEGER PRIMARY KEY, text TEXT, metadata TEXT)"
            )
            await db.execute(
                "INSERT INTO documents(text, metadata) VALUES ('a', ?)",
                (json.dumps({"consolidated_from": [1, 2], "status": "active"}),),
            )
            await db.execute(
                "INSERT INTO documents(text, metadata) VALUES ('b', ?)",
                (json.dumps({"status": "archived"}),),
            )
            await db.commit()

        engine = SimpleNamespace(db_path=db_path)
        handler = ConsolidationHandler(PageApiUtils())
        result = await handler.get_status(engine, None, ConfigManager())

        assert result["status"] == "ok"
        assert result["data"]["consolidated_count"] == 1
        assert result["data"]["archived_count"] == 1
        assert result["data"]["config"]["enabled"] is False
