"""Smoke tests for graph memory critical user flows."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from astrbot.api.platform import MessageType
from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.command_handler import CommandHandler
from astrbot_plugin_livingmemory.core.event_handler import EventHandler
from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine

from tests.test_graph_memory import _FakeFaissDB

SESSION_ID = "smoke-session"
PERSONA_ID = "persona_smoke"
MEMORY_CONTENT = "project sync record"
MEMORY_METADATA = {
    "topics": ["project sync"],
    "participants": ["alice"],
    "key_facts": ["meeting starts at 3pm tomorrow"],
    "canonical_summary": "project sync record",
}


async def _create_engine(tmp_path: Path) -> MemoryEngine:
    engine = MemoryEngine(
        db_path=str(tmp_path / "smoke_memory.db"),
        faiss_db=_FakeFaissDB(),
        graph_vector_db=_FakeFaissDB(),
        config={
            "fallback_enabled": True,
            "graph_memory_enabled": True,
            "document_route_weight": 0.6,
            "graph_route_weight": 0.4,
            "cross_route_bonus": 0.08,
        },
    )
    await engine.initialize()
    return engine


async def _seed_memory(engine: MemoryEngine) -> int:
    return await engine.add_memory(
        content=MEMORY_CONTENT,
        session_id=SESSION_ID,
        persona_id=PERSONA_ID,
        importance=0.9,
        metadata=dict(MEMORY_METADATA),
    )


async def _settle_background_tasks() -> None:
    await asyncio.sleep(0.05)


def _make_event(query: str):
    event = Mock()
    event.unified_msg_origin = SESSION_ID
    event.get_message_type = Mock(return_value=MessageType.FRIEND_MESSAGE)
    event.get_sender_id = Mock(return_value="user-1")
    event.get_self_id = Mock(return_value="bot-1")
    event.get_sender_name = Mock(return_value="SmokeUser")
    event.message_str = query
    event.get_message_str = Mock(return_value=query)
    event.get_messages = Mock(return_value=[])
    event.get_platform_name = Mock(return_value="test")
    event.plain_result = lambda message: message
    return event


def _make_request(prompt: str):
    req = Mock()
    req.prompt = prompt
    req.system_prompt = ""
    req.contexts = []
    req.extra_user_content_parts = []
    return req


@pytest.mark.asyncio
async def test_smoke_search_by_participant_hits_graph_route(tmp_path: Path):
    engine = await _create_engine(tmp_path)
    try:
        memory_id = await _seed_memory(engine)
        results = await engine.search_memories(
            query="alice",
            k=3,
            session_id=SESSION_ID,
            persona_id=PERSONA_ID,
        )

        assert results
        assert results[0].doc_id == memory_id
        assert (results[0].score_breakdown or {}).get("graph_keyword_score", 0.0) > 0
    finally:
        await _settle_background_tasks()
        await engine.close()


@pytest.mark.asyncio
async def test_smoke_search_by_summary_includes_four_mode_breakdown(tmp_path: Path):
    engine = await _create_engine(tmp_path)
    try:
        memory_id = await _seed_memory(engine)
        results = await engine.search_memories(
            query=MEMORY_CONTENT,
            k=3,
            session_id=SESSION_ID,
            persona_id=PERSONA_ID,
        )

        assert results
        assert results[0].doc_id == memory_id
        breakdown = results[0].score_breakdown or {}
        assert breakdown.get("document_vector_score", 0.0) > 0
        assert breakdown.get("graph_route_score", 0.0) > 0
        assert "graph_keyword_score" in breakdown
        assert "graph_vector_score" in breakdown
    finally:
        await _settle_background_tasks()
        await engine.close()


@pytest.mark.asyncio
async def test_smoke_rebuild_graph_command_reports_success(tmp_path: Path):
    engine = await _create_engine(tmp_path)
    try:
        memory_id = await _seed_memory(engine)
        assert engine.graph_memory_manager is not None
        await engine.graph_memory_manager.delete_memory(memory_id)

        handler = CommandHandler(
            context=Mock(),
            config_manager=ConfigManager(),
            memory_engine=engine,
            conversation_manager=None,
            index_validator=None,
            webui_server=None,
        )
        messages = [
            message
            async for message in handler.handle_rebuild_graph(
                _make_event("/lmem rebuild-graph")
            )
        ]

        assert len(messages) == 2
        assert messages[0].endswith("...")
        assert any(char.isdigit() for char in messages[1])
    finally:
        await _settle_background_tasks()
        await engine.close()


@pytest.mark.asyncio
async def test_smoke_event_recall_injects_memory_into_prompt(tmp_path: Path):
    engine = await _create_engine(tmp_path)
    try:
        await _seed_memory(engine)
        conversation_manager = Mock()
        conversation_manager.add_message_from_event = AsyncMock()
        conversation_manager.store = None

        handler = EventHandler(
            context=Mock(),
            config_manager=ConfigManager(
                {
                    "recall_engine": {
                        "top_k": 3,
                        "injection_method": "system_prompt",
                    },
                    "session_manager": {"max_messages_per_session": 100},
                }
            ),
            memory_engine=engine,
            memory_processor=Mock(),
            conversation_manager=conversation_manager,
        )
        req = _make_request("who is alice?")

        with patch(
            "astrbot_plugin_livingmemory.core.event_handler.get_persona_id",
            new_callable=AsyncMock,
        ) as get_persona:
            get_persona.return_value = PERSONA_ID
            await handler.handle_memory_recall(_make_event("alice"), req)

        assert "<RAG-Faiss-Memory>" in req.system_prompt
        assert "project sync record" in req.system_prompt
        conversation_manager.add_message_from_event.assert_awaited_once()
    finally:
        await _settle_background_tasks()
        await engine.close()
