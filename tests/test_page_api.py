"""
Tests for PluginPageApi — WebUI REST API endpoints and helpers.
"""

from __future__ import annotations

import importlib
import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest
import pytest_asyncio
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

    async def list_memories_page(self, **kwargs):
        return {
            "items": [],
            "total": 0,
            "page": 1,
            "page_size": kwargs.get("page_size", 20),
            "has_more": False,
        }

    async def search_memories(self, query, k=5, session_id=None, persona_id=None):
        return []

    async def get_memory(self, memory_id: int):
        return None

    async def get_memory_record(self, memory_id: int):
        return None

    async def get_consolidation_stats(self):
        return {"consolidated_count": 0, "archived_count": 0}

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
    def __init__(self, *, initialized: bool = True, failed: bool = False):
        self.memory_engine = FakeMemoryEngine()
        self.conversation_manager = None
        self.index_validator = None
        self.memory_processor = None
        self.data_dir = "/tmp/test_plugin"
        self.is_initialized = initialized
        self.is_failed = failed


class FakePlugin:
    def __init__(
        self,
        *,
        ready=True,
        memory_engine=None,
        initialized=None,
        failed=False,
        block_waiting=False,
    ):
        self._ready = ready
        self._fail_message = "" if ready else "插件尚未就绪"
        self._block_waiting = block_waiting
        self.readiness_waits: list[bool] = []
        self.initializer = FakeInitializer(
            initialized=ready if initialized is None else initialized,
            failed=failed,
        )
        if memory_engine:
            self.initializer.memory_engine = memory_engine
        self._api_routes = []

    async def _ensure_plugin_ready(self, *, wait: bool = True):
        self.readiness_waits.append(wait)
        if wait and self._block_waiting:
            raise AssertionError("read endpoint must not wait for initialization")
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
    import astrbot_plugin_livingmemory.core.page_api_modules.memory_handler_io as memory_io_mod
    import astrbot_plugin_livingmemory.core.page_api_modules.memory_handler_update as memory_upd_mod
    import astrbot_plugin_livingmemory.core.page_api_modules.recall_handler as recall_mod

    # Patch all modules that use request
    modules = [mod, memory_mod, memory_io_mod, memory_upd_mod, recall_mod, graph_mod]
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


@pytest_asyncio.fixture
async def real_api(tmp_path):
    """Page API wired to a real MemoryEngine on a temporary database."""
    from tests.real_engine_support import close_engine, make_engine

    engine, db = await make_engine(tmp_path / "page-api")
    try:
        yield PluginPageApi(FakePlugin(memory_engine=engine)), engine
    finally:
        await close_engine(engine, db)


async def _stored_importance(engine, memory_id: int) -> float:
    """Read the importance actually persisted for a memory."""
    record = await engine.get_memory_record(memory_id)
    metadata = record["metadata"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    return float(metadata["importance"])


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
        assert result["code"] == "not_ready"
        assert result["init_state"] == "in_progress"
        assert "尚未就绪" in result["message"]

    @pytest.mark.asyncio
    async def test_failed_init_marks_not_ready_state(self):
        api = PluginPageApi(FakePlugin(ready=False, failed=True))
        result = await api.get_stats()
        assert result["code"] == "not_ready"
        assert result["init_state"] == "failed"


class TestListMemories:
    @pytest.mark.asyncio
    async def test_engine_failure_returns_error(self, api):
        async def raise_uninitialized(**kwargs):
            raise RuntimeError("数据库连接未初始化")

        api.plugin.initializer.memory_engine.list_memories_page = raise_uninitialized
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
    async def test_filters_select_the_matching_memories(self, real_api):
        """筛选参数的效果通过返回结果体现：只有匹配的记忆出现在列表里。"""
        api, engine = real_api
        kept = await engine.add_memory(
            "用户喜欢燕麦拿铁",
            session_id="s1",
            persona_id=None,
            metadata={"memory_type": "FACT"},
        )
        await engine.add_memory(
            "用户去过杭州",
            session_id="s2",
            persona_id=None,
            metadata={"memory_type": "EVENT"},
        )
        req = _mock_page_request(
            args={
                "page": "1",
                "page_size": "20",
                "session_id": "s1",
                "keyword": "燕麦",
                "status": "all",
                "type": "",
                "sort": "created_desc",
            }
        )

        with _patch_page_request(req):
            result = await api.list_memories()

        assert result["status"] == "ok"
        ids = [item["id"] for item in result["data"]["items"]]
        assert ids == [kept]
        assert result["data"]["items"][0]["metadata"]["memory_type"] == "FACT"

    @pytest.mark.asyncio
    async def test_list_items_omit_full_text_and_trim_metadata(self, api):
        engine = api.plugin.initializer.memory_engine
        long_text = "长" * 400
        engine.list_memories_page = AsyncMock(
            return_value={
                "items": [
                    {
                        "id": 5,
                        "doc_id": "uuid-5",
                        "text": long_text,
                        "metadata": json.dumps(
                            {
                                "memory_type": "FACT",
                                "importance": 0.7,
                                "status": "active",
                                "persona_summary": "摘要文本",
                                "consolidated_from": [1, 2],
                                "debug_only": "should-not-leak",
                            }
                        ),
                        "created_at": "c",
                        "updated_at": "u",
                    },
                    {
                        "id": 6,
                        "doc_id": "uuid-6",
                        "text": long_text,
                        "metadata": "{}",
                        "created_at": "c",
                        "updated_at": "u",
                    },
                ],
                "total": 2,
                "page": 1,
                "page_size": 20,
                "has_more": False,
                "sort": "created_desc",
            }
        )
        req = _mock_page_request(args={"page": "1", "page_size": "20"})
        with _patch_page_request(req):
            result = await api.list_memories()

        first = result["data"]["items"][0]
        assert first["summary"] == "摘要文本"
        assert "text" not in first, "列表不应携带完整正文"
        assert first["consolidated_count"] == 2
        assert first["metadata"] == {
            "memory_type": "FACT",
            "importance": 0.7,
            "status": "active",
        }
        second = result["data"]["items"][1]
        assert second["metadata"] == {}
        assert len(second["summary"]) == 281 and second["summary"].endswith("…")



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
                api.plugin.initializer.memory_engine,
                "get_memory_record",
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
                api.plugin.initializer.memory_engine,
                "get_memory_record",
                return_value={"id": 1, "text": "hello", "metadata": {}},
            ):
                result = await api.update_memory()
        assert result["status"] == "error"
        assert "重要性" in result["message"]

    @pytest.mark.asyncio
    async def test_importance_is_normalized_from_display_scale(self, real_api):
        """UI 传 8.5 表示 85%，落库必须是 0.85。"""
        api, engine = real_api
        memory_id = await engine.add_memory(
            "用户喜欢燕麦拿铁", session_id="s1", persona_id=None
        )
        req = _mock_page_request(
            get_json={"memory_id": memory_id, "field": "importance", "value": 8.5}
        )

        with _patch_page_request(req):
            result = await api.update_memory()

        assert result["status"] == "ok"
        assert await _stored_importance(engine, memory_id) == pytest.approx(0.85)

    @pytest.mark.asyncio
    async def test_display_scale_one_point_zero_means_ten_percent(self, real_api):
        """display 标度下 1.0 是 10%，而不是满分。"""
        api, engine = real_api
        memory_id = await engine.add_memory(
            "用户喜欢燕麦拿铁", session_id="s1", persona_id=None
        )
        req = _mock_page_request(
            get_json={
                "memory_id": memory_id,
                "field": "importance",
                "value": 1.0,
                "value_scale": "display",
            }
        )

        with _patch_page_request(req):
            result = await api.update_memory()

        assert result["status"] == "ok"
        assert await _stored_importance(engine, memory_id) == pytest.approx(0.1)

    @pytest.mark.asyncio
    async def test_normalized_scale_keeps_legacy_value(self, real_api):
        """未标注标度时沿用已归一化的 0.8。"""
        api, engine = real_api
        memory_id = await engine.add_memory(
            "用户喜欢燕麦拿铁", session_id="s1", persona_id=None
        )
        req = _mock_page_request(
            get_json={"memory_id": memory_id, "field": "importance", "value": 0.8}
        )

        with _patch_page_request(req):
            result = await api.update_memory()

        assert result["status"] == "ok"
        assert await _stored_importance(engine, memory_id) == pytest.approx(0.8)

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
                api.plugin.initializer.memory_engine,
                "get_memory_record",
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
            with patch.object(
                api.plugin.initializer.memory_engine,
                "get_memory_record",
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
            with patch.object(
                api.plugin.initializer.memory_engine,
                "get_memory_record",
                return_value=memory,
            ):
                result = await api.update_memory()
        assert result["status"] == "error"
        assert "不能为空" in result["message"]

    @pytest.mark.asyncio
    async def test_content_update_keeps_the_existing_importance(self, real_api):
        """只改正文时沿用原记忆的重要性，不会重置成默认值。"""
        api, engine = real_api
        memory_id = await engine.add_memory(
            "old content",
            session_id="s1",
            persona_id="p1",
            importance=0.7,
        )
        req = _mock_page_request(
            get_json={
                "memory_id": memory_id,
                "field": "content",
                "value": "new content",
            }
        )

        with _patch_page_request(req):
            result = await api.update_memory()

        assert result["status"] == "ok"
        new_id = result["data"]["new_memory_id"]
        record = await engine.get_memory_record(new_id)
        assert record["text"].startswith("new content")
        assert json.loads(record["metadata"])["importance"] == pytest.approx(0.7)

    @pytest.mark.asyncio
    async def test_structured_update_replaces_topics_and_key_facts(self, real_api):
        """结构化编辑后，落库内容去重并归一化重要性。"""
        api, engine = real_api
        memory_id = await engine.add_memory(
            "old summary",
            session_id="s1",
            persona_id=None,
            metadata={"topics": ["old"], "key_facts": ["old fact"]},
        )
        req = _mock_page_request(
            get_json={
                "memory_id": memory_id,
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

        with _patch_page_request(req):
            result = await api.update_memory()

        assert result["status"] == "ok"
        record = await engine.get_memory_record(result["data"]["new_memory_id"])
        metadata = json.loads(record["metadata"])
        assert metadata["topics"] == ["release", "deployment"]
        assert metadata["key_facts"] == ["Release is Friday"]
        assert metadata["importance"] == pytest.approx(0.8)
        assert "new summary" in record["text"]
















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
            with patch.object(
                api.plugin.initializer.memory_engine,
                "get_memory_record",
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
    async def test_recall_returns_scored_results(self, real_api):
        """召回测试接口返回真实检索结果与打分拆解，供仪表盘展示。"""
        api, engine = real_api
        await engine.add_memory(
            "用户喜欢燕麦拿铁",
            session_id="s1",
            persona_id=None,
            metadata={"session_id": "s1"},
        )
        req = _mock_page_request(
            get_json={"query": "燕麦拿铁", "k": 5, "session_id": "s1"}
        )
        with _patch_page_request(req):
            result = await api.test_recall()

        assert result["status"] == "ok"
        assert result["data"]["total"] >= 1
        item = result["data"]["results"][0]
        assert "燕麦拿铁" in item["content"]
        assert isinstance(item["similarity_score"], float)
        assert item["score_percentage"] == pytest.approx(
            item["similarity_score"] * 100, abs=0.01
        )
        assert isinstance(item["score_breakdown"], dict)


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
    async def test_overview_full_graph_uses_bounded_snapshot(self):
        snapshot = {
            "nodes": [{"id": 1, "type": "topic", "label": "all"}],
            "edges": [],
            "entries": [],
            "memories": [],
        }
        graph_store = SimpleNamespace(
            get_full_graph_snapshot=AsyncMock(return_value=snapshot),
            get_graph_snapshot=AsyncMock(),
            get_memory_entry_stats=AsyncMock(
                return_value={
                    "graph_nodes": 1,
                    "graph_edges": 1,
                    "graph_entries": 1,
                }
            ),
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
        assert result["data"]["snapshot"]["nodes"][0]["community"] == 0
        # 全量图请求必须返回节点，而不是空快照
        assert result["data"]["snapshot"]["nodes"]

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
            get_memory_entry_stats=AsyncMock(
                return_value={
                    "graph_nodes": 1,
                    "graph_edges": 1,
                    "graph_entries": 1,
                }
            ),
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
        # 未指定作用域时，检索与图查询都不按会话过滤（已由 filters 断言覆盖）
        assert data["snapshot"]["nodes"][0]["id"] == 56


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
        assert error["code"] == "not_ready"

    @pytest.mark.asyncio
    async def test_not_ready_can_skip_waiting(self, api_not_ready):
        components, error = await api_not_ready._ensure_plugin_ready(wait=False)
        assert components is None
        assert error["code"] == "not_ready"
        assert api_not_ready.plugin.readiness_waits == [False]


class TestReadiness:
    @pytest.mark.asyncio
    async def test_ready_reports_ready_without_message(self, api):
        result = await api.get_readiness()
        assert result["status"] == "ok"
        assert result["data"] == {"ready": True, "state": "ready", "message": ""}
        assert api.plugin.readiness_waits == [False]

    @pytest.mark.asyncio
    async def test_in_progress_reports_message(self, api_not_ready):
        result = await api_not_ready.get_readiness()
        assert result["data"]["ready"] is False
        assert result["data"]["state"] == "in_progress"
        assert "尚未就绪" in result["data"]["message"]

    @pytest.mark.asyncio
    async def test_failed_reports_failed_state(self):
        api = PluginPageApi(FakePlugin(ready=False, failed=True))
        result = await api.get_readiness()
        assert result["data"]["ready"] is False
        assert result["data"]["state"] == "failed"


class TestReadEndpointsDoNotWait:
    @pytest.mark.asyncio
    async def test_get_stats_fast_fails_on_not_ready_plugin(self):
        api = PluginPageApi(FakePlugin(ready=False, block_waiting=True))
        result = await api.get_stats()
        assert result["code"] == "not_ready"
        assert api.plugin.readiness_waits == [False]

    @pytest.mark.asyncio
    async def test_get_graph_overview_fast_fails_when_not_ready(self):
        api = PluginPageApi(FakePlugin(ready=False, block_waiting=True))
        result = await api.get_graph_overview()
        assert result["code"] == "not_ready"
        assert api.plugin.readiness_waits == [False]

    @pytest.mark.asyncio
    async def test_write_endpoint_keeps_waiting(self):
        api = PluginPageApi(FakePlugin(block_waiting=False))
        req = _mock_page_request(
            get_json={"memory_id": 3, "field": "status", "value": "active"}
        )
        with _patch_page_request(req):
            await api.update_memory()
        assert api.plugin.readiness_waits == [True]


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


class TestRouteRegistration:
    def test_registers_all_routes(self):
        plugin = FakePlugin()
        api = PluginPageApi(plugin)
        api.register_routes()
        assert len(plugin._api_routes) == len(api._ROUTES)

        paths = {route for route, _, _, _ in plugin._api_routes}
        prefix = PAGE_API_PREFIX
        assert f"{prefix}/readiness" in paths
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
        assert f"{prefix}/config" in paths
        assert f"{prefix}/config/update" in paths
        assert f"{prefix}/profiles" in paths
        assert f"{prefix}/profiles/detail" in paths
        assert f"{prefix}/profiles/history" in paths
        assert f"{prefix}/profiles/update" in paths
        assert f"{prefix}/profiles/delete" in paths
        assert f"{prefix}/profiles/pending" in paths
        assert f"{prefix}/profiles/pending/resolve" in paths
        assert f"{prefix}/traces" in paths
        assert f"{prefix}/traces/stats" in paths
        assert f"{prefix}/traces/<trace_id>" in paths
        assert f"{prefix}/memories/<memory_id>/lineage" in paths
        # External memory API: dashboard key management + Bearer routes.
        assert f"{prefix}/external/keys" in paths
        assert f"{prefix}/external/keys/<key_id>" in paths
        assert f"{prefix}/external/health" in paths
        assert f"{prefix}/external/personas" in paths
        assert f"{prefix}/external/recall" in paths
        assert f"{prefix}/external/memories" in paths
        assert f"{prefix}/external/memories/<memory_id>" in paths
        assert f"{prefix}/external/memories/<memory_id>/archive" in paths

    def test_route_prefix_contains_plugin_name(self):
        assert PLUGIN_NAME in PAGE_API_PREFIX


@pytest.mark.asyncio
async def test_memory_detail_includes_retained_source(real_api):
    """详情接口返回记忆时，附带写入时保留的原始消息。"""
    api, engine = real_api
    memory_id = await engine.add_memory(
        "summary",
        session_id="s1",
        persona_id=None,
        importance=0.9,
        source_messages=[{"role": "user", "content": "exact detail"}],
    )
    req = _mock_page_request(args={"memory_id": str(memory_id)})

    with _patch_page_request(req):
        result = await api.get_memory_detail()

    assert result["status"] == "ok"
    assert result["data"]["source_messages"] == [
        {"role": "user", "content": "exact detail"}
    ]


@pytest.mark.asyncio
async def test_resummarize_memory_rebuilds_from_retained_source(real_api):
    """重新总结用保留的原文重建记忆，旧记忆被替换。"""
    api, engine = real_api
    memory_id = await engine.add_memory(
        "old summary",
        session_id="s1",
        persona_id=None,
        importance=0.5,
        source_messages=[
            {
                "id": 1,
                "session_id": "s1",
                "role": "user",
                "content": "exact detail",
                "sender_id": "u1",
                "sender_name": "User",
                "group_id": None,
                "platform": "test",
                "timestamp": 1.0,
                "metadata": {},
            },
            {
                "id": 2,
                "session_id": "s1",
                "role": "assistant",
                "content": "response",
                "sender_id": "bot",
                "sender_name": "Bot",
                "group_id": None,
                "platform": "test",
                "timestamp": 2.0,
                "metadata": {"is_bot_message": True},
            },
        ],
    )
    # LLM 是外部服务：用固定返回的处理器桩代替
    api.plugin.initializer.memory_processor = MagicMock()
    api.plugin.initializer.memory_processor.process_conversation = AsyncMock(
        return_value=("new summary", {"topics": ["detail"]}, 0.9)
    )
    req = _mock_page_request(get_json={"memory_id": memory_id})

    with _patch_page_request(req):
        result = await api.resummarize_memory()

    assert result["status"] == "ok"
    new_id = result["data"]["new_memory_id"]
    record = await engine.get_memory_record(new_id)
    assert "new summary" in record["text"]
    assert await engine.get_memory(memory_id) is None


@pytest.mark.asyncio
async def test_export_memories_returns_native_json_content(real_api):
    """导出内容就是可再导入的原生 JSON，字段来自真实存储。"""
    api, engine = real_api
    memory_id = await engine.add_memory(
        "portable summary",
        session_id="s1",
        persona_id=None,
        importance=0.8,
        metadata={"topics": ["portable"]},
    )
    req = _mock_page_request(get_json={"format": "json", "memory_ids": [memory_id]})

    with _patch_page_request(req):
        result = await api.export_memories()

    assert result["status"] == "ok"
    exported = json.loads(result["data"]["content"])
    assert exported["format"] == "livingmemory"
    assert exported["memories"][0]["content"] == "portable summary"
    assert exported["memories"][0]["importance"] == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_export_all_limits_entries_and_reports_truncation(real_api, monkeypatch):
    """超过导出上限时截断并如实报告可用总数。"""
    io_module = importlib.import_module(
        "astrbot_plugin_livingmemory.core.page_api_modules.memory_handler_io"
    )
    monkeypatch.setattr(io_module, "MAX_EXPORT_ENTRIES", 2)
    api, engine = real_api
    for index in range(5):
        await engine.add_memory(f"memory {index}", session_id="s1", persona_id=None)

    req = _mock_page_request(get_json={"format": "json"})
    with _patch_page_request(req):
        result = await api.export_memories()

    data = result["data"]
    assert data["truncated"] is True
    assert data["memory_count"] == 2
    assert data["total_available"] == 5
    assert data["limit"] == 2


@pytest.mark.asyncio
async def test_export_by_id_reports_the_selected_count(real_api):
    """按 ID 导出时只有被选中的记忆进入文件。"""
    api, engine = real_api
    first = await engine.add_memory("first", session_id="s1", persona_id=None)
    second = await engine.add_memory("second", session_id="s1", persona_id=None)
    req = _mock_page_request(get_json={"format": "json", "memory_ids": [second]})

    with _patch_page_request(req):
        result = await api.export_memories()

    exported = json.loads(result["data"]["content"])
    contents = [item["content"] for item in exported["memories"]]
    assert result["data"]["memory_count"] == 1
    assert any("second" in content for content in contents)
    assert all("first" not in content for content in contents)
    assert first != second


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
async def test_import_remaps_retired_platform_in_session_id(real_api):
    """导入的旧平台会话 ID 应映射到当前平台，否则按会话过滤永远召回不到。"""
    api, engine = real_api
    api.plugin.initializer.conversation_manager = SimpleNamespace(
        store=SimpleNamespace(
            list_session_ids=AsyncMock(
                return_value=["default:FriendMessage:1163018485"]
            )
        )
    )
    content = json.dumps(
        [{"summary": "旧平台记忆", "session_id": "了了:FriendMessage:1163018485"}]
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
    assert result["data"]["imported_count"] == 1
    assert result["data"]["remapped_session_count"] == 1
    (memory_id,) = result["data"]["imported_ids"]
    record = await engine.get_memory_record(memory_id)
    metadata = json.loads(record["metadata"])
    # 退役平台前缀被改写为当前部署的会话 ID，导入后仍可召回
    assert metadata["session_id"] == "default:FriendMessage:1163018485"


@pytest.mark.asyncio
async def test_import_preview_reports_remapped_session_count():
    engine = FakeMemoryEngine()
    engine.get_memory_import_keys = AsyncMock(return_value=set())
    plugin = FakePlugin(memory_engine=engine)
    plugin.initializer.conversation_manager = SimpleNamespace(
        store=SimpleNamespace(
            list_session_ids=AsyncMock(
                return_value=["default:FriendMessage:1163018485"]
            )
        )
    )
    api = PluginPageApi(plugin)
    content = json.dumps(
        [{"summary": "旧平台记忆", "session_id": "了了:FriendMessage:1163018485"}]
    )
    req = _mock_page_request(
        get_json={"format": "json", "content": content, "dry_run": True}
    )

    with _patch_page_request(req):
        result = await api.import_memories()

    assert result["data"]["planned_import_count"] == 1
    assert result["data"]["remapped_session_count"] == 1


@pytest.mark.asyncio
async def test_import_source_only_conversation_uses_processor_and_retains_source(
    real_api,
):
    """纯对话条目经总结入库，并保留原文供追溯。"""
    api, engine = real_api
    api.plugin.initializer.memory_processor = MagicMock()
    api.plugin.initializer.memory_processor.process_conversation = AsyncMock(
        return_value=("generated summary", {"topics": ["import"]}, 0.7)
    )
    api.plugin.initializer.memory_processor.classify_atoms_from_metadata = MagicMock(
        return_value=[]
    )
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
    assert result["data"]["imported_count"] == 1
    (memory_id,) = result["data"]["imported_ids"]
    record = await engine.get_memory_record(memory_id)
    assert "generated summary" in record["text"]
    assert json.loads(record["metadata"])["memory_origin"] == "memory_import"
    source = await engine.get_memory_source(memory_id)
    assert [item["content"] for item in source] == ["The code is alpha", "Noted"]


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
    content = json.dumps([{"summary": "first"}, {"summary": "second"}])
    req = _mock_page_request(
        get_json={"format": "json", "content": content, "dry_run": False}
    )

    with _patch_page_request(req):
        result = await api.import_memories()

    assert result["status"] == "ok"
    assert result["data"]["imported_ids"] == [52]
    assert result["data"]["failed_count"] == 1
    assert result["data"]["errors"] == [{"index": 0, "error": "write failed"}]
    assert engine.add_memory.await_count == 2


class TestConsolidationHandler:
    @pytest.mark.asyncio
    async def test_run_delegates_to_manager(self):
        from astrbot_plugin_livingmemory.core.page_api_modules.consolidation_handler import (
            ConsolidationHandler,
        )
        from astrbot_plugin_livingmemory.core.page_api_modules.utils import PageApiUtils

        handler = ConsolidationHandler(PageApiUtils())

        class _RecordingManager:
            """Stub that only consolidates when asked to force."""

            def __init__(self) -> None:
                self.forced = None

            async def run_consolidation(self, *, force: bool = False):
                self.forced = force
                return {"groups": 2, "merged": 6, "archived": 6} if force else {}

        manager = _RecordingManager()
        result = await handler.run(manager)

        assert result["status"] == "ok"
        assert result["data"]["merged"] == 6
        # 手动触发必须强制合并，否则会被“无候选”短路
        assert manager.forced is True

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
        from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine
        from astrbot_plugin_livingmemory.core.page_api_modules.consolidation_handler import (
            ConsolidationHandler,
        )
        from astrbot_plugin_livingmemory.core.page_api_modules.utils import PageApiUtils

        db_path = str(tmp_path / "cons.db")
        engine = MemoryEngine.__new__(MemoryEngine)
        engine.db_connection = await aiosqlite.connect(db_path)
        engine.db_connection.row_factory = aiosqlite.Row
        try:
            await engine.db_connection.execute(
                "CREATE TABLE documents (id INTEGER PRIMARY KEY, text TEXT, metadata TEXT)"
            )
            await engine.db_connection.execute(
                "INSERT INTO documents(text, metadata) VALUES ('a', ?)",
                (json.dumps({"consolidated_from": [1, 2], "status": "active"}),),
            )
            await engine.db_connection.execute(
                "INSERT INTO documents(text, metadata) VALUES ('b', ?)",
                (json.dumps({"status": "archived"}),),
            )
            await engine.db_connection.commit()

            handler = ConsolidationHandler(PageApiUtils())
            result = await handler.get_status(engine, None, ConfigManager())
        finally:
            await engine.db_connection.close()

        assert result["status"] == "ok"
        assert result["data"]["consolidated_count"] == 1
        assert result["data"]["archived_count"] == 1
        assert result["data"]["config"]["enabled"] is False
