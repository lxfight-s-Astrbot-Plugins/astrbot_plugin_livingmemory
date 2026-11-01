import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import aiosqlite

from astrbot_plugin_livingmemory.core.utils import get_persona_id
from astrbot_plugin_livingmemory.core.managers.memory_engine_crud import MemoryEngineCrudMixin
from astrbot_plugin_livingmemory.core.page_api import PluginPageApi
from tests.test_page_api import _mock_page_request, _patch_page_request, FakePlugin


class DummyEngine(MemoryEngineCrudMixin):
    def __init__(self, db_connection=None):
        self.db_connection = db_connection
        self.db_path = ":memory:"


@pytest.mark.asyncio
async def test_get_persona_id_trace():
    context = MagicMock()
    event = MagicMock()
    event.get_extra.side_effect = lambda k: "persona_from_trace" if k == "_trace_persona_id" else None
    persona = await get_persona_id(context, event)
    assert persona == "persona_from_trace"


@pytest.mark.asyncio
async def test_get_persona_id_session_service_config():
    context = MagicMock()
    event = MagicMock(spec=["unified_msg_origin"])
    event.unified_msg_origin = "umo_test"
    with patch("astrbot_plugin_livingmemory.core.utils.sp.get_async", new=AsyncMock(return_value={"persona_id": "persona_from_session"})):
        persona = await get_persona_id(context, event)
        assert persona == "persona_from_session"


@pytest.mark.asyncio
async def test_get_persona_id_conversation():
    context = MagicMock()
    context.conversation_manager.get_curr_conversation_id = AsyncMock(return_value="conv_1")
    conv = MagicMock()
    conv.persona_id = "persona_from_conv"
    context.conversation_manager.get_conversation = AsyncMock(return_value=conv)
    event = MagicMock(spec=["unified_msg_origin"])
    event.unified_msg_origin = "umo_test"
    with patch("astrbot_plugin_livingmemory.core.utils.sp.get_async", new=AsyncMock(return_value={})):
        persona = await get_persona_id(context, event)
        assert persona == "persona_from_conv"


@pytest.mark.asyncio
async def test_get_persona_id_astrbot4_agent_runner():
    context = MagicMock()
    context.conversation_manager.get_curr_conversation_id = AsyncMock(return_value=None)
    context.get_config.return_value = {
        "agent_runner": {
            "runner_type": "local",
            "config": {
                "persona": {
                    "persona_id": "ds2234（really）"
                }
            }
        }
    }
    event = MagicMock(spec=["unified_msg_origin"])
    event.unified_msg_origin = "umo_test"
    with patch("astrbot_plugin_livingmemory.core.utils.sp.get_async", new=AsyncMock(return_value={})):
        persona = await get_persona_id(context, event)
        assert persona == "ds2234（really）"


@pytest.mark.asyncio
async def test_get_persona_id_fallback_default():
    context = MagicMock()
    context.conversation_manager.get_curr_conversation_id = AsyncMock(return_value=None)
    context.get_config.return_value = {}
    context.persona_manager.get_default_persona_v3 = AsyncMock(return_value=None)
    event = MagicMock(spec=["unified_msg_origin"])
    event.unified_msg_origin = "umo_test"
    with patch("astrbot_plugin_livingmemory.core.utils.sp.get_async", new=AsyncMock(return_value={})):
        persona = await get_persona_id(context, event)
        assert persona == "default"


@pytest.mark.asyncio
async def test_get_persona_id_no_umo():
    context = MagicMock(spec=[])
    event = MagicMock(spec=[])
    persona = await get_persona_id(context, event)
    assert persona is None


@pytest.mark.asyncio
async def test_list_memories_crud_and_available_personas():
    async with aiosqlite.connect(":memory:") as db:
        db.row_factory = aiosqlite.Row
        await db.execute("""
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id TEXT UNIQUE,
                text TEXT,
                metadata TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        await db.execute(
            "INSERT INTO documents (doc_id, text, metadata, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("doc-1", "Memory 1", '{"persona_id": "persona_a", "status": "active"}', "2026-01-01", "2026-01-01"),
        )
        await db.execute(
            "INSERT INTO documents (doc_id, text, metadata, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("doc-2", "Memory 2", '{"persona_id": "persona_b", "status": "active"}', "2026-01-02", "2026-01-02"),
        )
        await db.commit()

        engine = DummyEngine(db_connection=db)
        personas = await engine.list_available_personas()
        assert personas == ["persona_a", "persona_b"]

        page_a = await engine.list_memories_page(persona_id="persona_a")
        assert page_a["total"] == 1
        assert page_a["items"][0]["doc_id"] == "doc-1"

        page_b = await engine.list_memories_page(persona_id="persona_b")
        assert page_b["total"] == 1
        assert page_b["items"][0]["doc_id"] == "doc-2"

        page_all = await engine.list_memories_page(persona_id="all")
        assert page_all["total"] == 2


@pytest.mark.asyncio
async def test_page_api_persona_support():
    plugin = FakePlugin(ready=True)
    api = PluginPageApi(plugin)
    engine = plugin.initializer.memory_engine
    engine.list_memories_page = AsyncMock(
        return_value={
            "items": [],
            "total": 0,
            "page": 1,
            "page_size": 20,
            "has_more": False,
            "sort": "created_desc",
        }
    )
    engine.list_available_personas = AsyncMock(return_value=["persona_a", "persona_b"])

    req = _mock_page_request(
        args={
            "page": "1",
            "page_size": "20",
            "persona_id": "persona_a",
        }
    )
    with _patch_page_request(req):
        result = await api.list_memories()

    assert result["status"] == "ok"
    assert result["data"]["personas"] == ["persona_a", "persona_b"]
    assert result["data"]["filters"]["persona_id"] == "persona_a"
    assert engine.list_memories_page.call_args.kwargs["persona_id"] == "persona_a"
