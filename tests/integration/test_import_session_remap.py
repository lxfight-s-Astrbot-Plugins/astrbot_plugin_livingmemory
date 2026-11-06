"""Regression: imports from a retired platform remain recallable after session remap."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_livingmemory.core.managers.conversation_manager import ConversationManager
from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine
from astrbot_plugin_livingmemory.core.page_api_modules.memory_handler import MemoryHandler
from astrbot_plugin_livingmemory.core.page_api_modules.utils import PageApiUtils
from astrbot_plugin_livingmemory.core.processors.memory_processor import MemoryProcessor
from astrbot_plugin_livingmemory.storage.conversation_store import ConversationStore
from astrbot.core.db.vec_db.faiss_impl.vec_db import FaissVecDB

from tests.integration.test_real_db_end_to_end import _DeterministicEmbeddingProvider
from tests.test_page_api import _mock_page_request, _patch_page_request


@pytest.mark.asyncio
async def test_old_platform_import_is_recallable_after_remap(tmp_path: Path):
    embedding = _DeterministicEmbeddingProvider(dim=24)
    faiss_db = FaissVecDB(str(tmp_path / "m.db"), str(tmp_path / "m.index"), embedding)
    await faiss_db.initialize()
    graph_db = FaissVecDB(str(tmp_path / "g.db"), str(tmp_path / "g.index"), embedding)
    await graph_db.initialize()
    engine = MemoryEngine(
        db_path=str(tmp_path / "m.db"),
        faiss_db=faiss_db,
        graph_vector_db=graph_db,
        config={"graph_memory_enabled": True},
    )
    await engine.initialize()
    store = ConversationStore(str(tmp_path / "c.db"))
    await store.initialize()
    await store.create_session("default:FriendMessage:1163018485", "default")
    manager = ConversationManager(store=store)
    try:
        processor = MemoryProcessor(llm_provider=AsyncMock(), context=None)
        handler = MemoryHandler(PageApiUtils())
        content = json.dumps(
            [
                {
                    "summary": "用户喜欢下雨天散步",
                    "session_id": "了了:FriendMessage:1163018485",
                    "metadata": {
                        "session_id": "了了:FriendMessage:1163018485",
                        "importance": 0.7,
                    },
                }
            ]
        )
        existing_sessions = await store.list_session_ids()
        assert existing_sessions == ["default:FriendMessage:1163018485"]

        req = _mock_page_request(
            get_json={
                "format": "json",
                "content": content,
                "dry_run": False,
                "duplicate_strategy": "skip",
            }
        )
        with _patch_page_request(req):
            result = await handler.import_memories(
                engine,
                processor,
                current_platforms={"default"},
                existing_session_ids=existing_sessions,
            )
        print("import:", result)
        assert result["data"]["imported_count"] == 1
        assert result["data"]["remapped_session_count"] == 1
        stored = await engine.get_memory(result["data"]["imported_ids"][0])
        assert "default:FriendMessage:1163018485" in stored["metadata"]

        hits = await engine.search_memories(
            "下雨天散步", k=5, session_id="default:FriendMessage:1163018485"
        )
        print("recall:", [h.content for h in hits])
        assert any("下雨天" in h.content for h in hits)
    finally:
        await engine.close()
        await faiss_db.close()
        await graph_db.close()
        await store.close()
