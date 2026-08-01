"""
End-to-end integration tests with real plugin components and real SQLite/FAISS storage.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest
import pytest_asyncio
from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.command_handler import CommandHandler
from astrbot_plugin_livingmemory.core.event_handler import EventHandler
from astrbot_plugin_livingmemory.core.managers.conversation_manager import (
    ConversationManager,
)
from astrbot_plugin_livingmemory.core.managers.graph_memory_manager import (
    GraphMemoryManager,
)
from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine
from astrbot_plugin_livingmemory.core.processors.graph_extractor import GraphExtractor
from astrbot_plugin_livingmemory.core.processors.memory_processor import MemoryProcessor
from astrbot_plugin_livingmemory.core.retrieval.graph_vector_retriever import (
    GraphVectorRetriever,
)
from astrbot_plugin_livingmemory.storage.conversation_store import ConversationStore
from astrbot_plugin_livingmemory.storage.graph_store import GraphStore

from astrbot.api.platform import MessageType
from astrbot.api.provider import LLMResponse
from astrbot.core.db.vec_db.faiss_impl.vec_db import FaissVecDB
from astrbot.core.provider.provider import EmbeddingProvider


class _DeterministicEmbeddingProvider(EmbeddingProvider):
    def __init__(self, dim: int = 24):
        super().__init__({"id": "test-embedding", "type": "test"}, {})
        self._dim = dim

    async def get_embedding(self, text: str) -> list[float]:
        vector = [0.0] * self._dim
        for idx, byte in enumerate(text.encode("utf-8")):
            vector[idx % self._dim] += ((byte % 31) + 1) / 31.0
        norm = sum(v * v for v in vector) ** 0.5 or 1.0
        return [v / norm for v in vector]

    async def get_embeddings(self, text: list[str]) -> list[list[float]]:
        return [await self.get_embedding(item) for item in text]

    def get_dim(self) -> int:
        return self._dim


class _DeterministicLLMProvider:
    async def text_chat(
        self,
        prompt: str | None = None,
        session_id: str | None = None,
        image_urls: list[str] | None = None,
        func_tool=None,
        contexts=None,
        system_prompt: str | None = None,
        tool_calls_result=None,
        model: str | None = None,
        extra_user_content_parts=None,
        **kwargs,
    ) -> LLMResponse:
        del (
            session_id,
            image_urls,
            func_tool,
            contexts,
            system_prompt,
            tool_calls_result,
            model,
            extra_user_content_parts,
            kwargs,
        )
        prompt_text = (prompt or "").lower()

        if "running" in prompt_text:
            summary = (
                "I remember the user mentioned running and wants to keep the habit."
            )
            topics = ["health", "habit"]
            facts = ["user talked about running"]
            sentiment = "positive"
            importance = 0.82
        elif "headphone" in prompt_text:
            summary = "I remember the user is considering noise-cancelling headphones."
            topics = ["shopping", "audio"]
            facts = ["user asked about headphones"]
            sentiment = "neutral"
            importance = 0.75
        else:
            summary = "I remember the recent conversation and user preferences."
            topics = ["general"]
            facts = ["recent discussion happened"]
            sentiment = "neutral"
            importance = 0.7

        payload = {
            "summary": summary,
            "topics": topics,
            "key_facts": facts,
            "sentiment": sentiment,
            "importance": importance,
        }
        return LLMResponse(role="assistant", completion_text=json.dumps(payload))


class _ContextConversationManager:
    def __init__(self, persona_id: str):
        self._persona_id = persona_id

    async def get_curr_conversation_id(self, umo: str) -> str:
        return f"conv-{umo}"

    async def get_conversation(self, umo: str, session_id: str):
        del umo, session_id
        return SimpleNamespace(persona_id=self._persona_id)


class _ContextPersonaManager:
    def __init__(self, persona_id: str):
        self._persona_id = persona_id

    async def get_default_persona_v3(self, umo: str):
        del umo
        return {"name": self._persona_id}

    async def get_persona(self, persona_id: str):
        del persona_id
        return SimpleNamespace(system_prompt="You are calm and factual.")


@pytest.mark.asyncio
async def test_graph_memory_batches_real_faiss_index_writes(
    tmp_path: Path, monkeypatch
):
    """Graph vectors stay memory-grained and each operation saves at most once."""
    graph_store = GraphStore(str(tmp_path / "graph.db"))
    await graph_store.initialize()

    index_path = tmp_path / "graph.index"
    vector_db = FaissVecDB(
        doc_store_path=str(tmp_path / "graph_documents.db"),
        index_store_path=str(index_path),
        embedding_provider=_DeterministicEmbeddingProvider(dim=24),
    )
    await vector_db.initialize()

    write_paths: list[str] = []
    original_write_index = vector_db.embedding_storage._write_index

    def counted_write_index(index, path: str) -> None:
        write_paths.append(path)
        original_write_index(index, path)

    monkeypatch.setattr(
        vector_db.embedding_storage,
        "_write_index",
        counted_write_index,
    )

    manager = GraphMemoryManager(
        graph_store=graph_store,
        graph_vector_retriever=GraphVectorRetriever(vector_db),
        graph_extractor=GraphExtractor(),
    )
    metadata = {
        "session_id": "test:private:write-count",
        "persona_id": "persona-write-count",
        "importance": 0.8,
        "canonical_summary": "Alice and Bob planned the release",
        "topics": ["release", "deployment"],
        "participants": ["Alice", "Bob"],
        "key_facts": ["Release is Friday", "Bob owns deployment"],
    }

    try:
        await manager.index_memory(77, metadata["canonical_summary"], metadata)
        stats = await graph_store.get_memory_entry_stats()
        assert stats["graph_entries"] > 1
        assert vector_db.embedding_storage.index.ntotal == 1
        assert len(write_paths) == 1
        assert index_path.stat().st_size > 0

        await manager.index_memory(77, metadata["canonical_summary"], metadata)
        assert vector_db.embedding_storage.index.ntotal == 1
        assert len(write_paths) == 3

        await manager.delete_memory(77)
        assert vector_db.embedding_storage.index.ntotal == 0
        assert len(write_paths) == 4

        await manager.index_memory(78, metadata["canonical_summary"], metadata)
        await manager.index_memory(79, metadata["canonical_summary"], metadata)
        assert vector_db.embedding_storage.index.ntotal == 2
        assert len(write_paths) == 6

        await manager.batch_delete_memories([78, 79])
        assert vector_db.embedding_storage.index.ntotal == 0
        assert len(write_paths) == 7
    finally:
        await vector_db.close()


@pytest.mark.asyncio
async def test_graph_memory_full_rebuild_saves_real_faiss_once(
    tmp_path: Path, monkeypatch
):
    graph_store = GraphStore(str(tmp_path / "graph_rebuild.db"))
    await graph_store.initialize()

    index_path = tmp_path / "graph_rebuild.index"
    vector_db = FaissVecDB(
        doc_store_path=str(tmp_path / "graph_rebuild_documents.db"),
        index_store_path=str(index_path),
        embedding_provider=_DeterministicEmbeddingProvider(dim=24),
    )
    await vector_db.initialize()

    write_paths: list[str] = []
    original_write_index = vector_db.embedding_storage._write_index

    def counted_write_index(index, path: str) -> None:
        write_paths.append(path)
        original_write_index(index, path)

    monkeypatch.setattr(
        vector_db.embedding_storage,
        "_write_index",
        counted_write_index,
    )

    manager = GraphMemoryManager(
        graph_store=graph_store,
        graph_vector_retriever=GraphVectorRetriever(vector_db),
        graph_extractor=GraphExtractor(),
    )
    memories = [
        (
            memory_id,
            f"Release planning memory {memory_id}",
            {
                "session_id": "test:private:rebuild",
                "persona_id": "persona-rebuild",
                "importance": 0.8,
                "topics": ["release", f"phase-{memory_id}"],
                "participants": ["Alice", "Bob"],
                "key_facts": [
                    f"Release phase {memory_id} is Friday",
                    f"Bob owns deployment phase {memory_id}",
                ],
            },
        )
        for memory_id in range(1, 11)
    ]

    try:
        result = await manager.rebuild_memories(memories)

        stats = await graph_store.get_memory_entry_stats()
        assert result == {"rebuilt": 10, "skipped": 0}
        assert stats["graph_entries"] > result["rebuilt"]
        assert vector_db.embedding_storage.index.ntotal == result["rebuilt"]
        assert len(write_paths) == 1
    finally:
        await vector_db.close()


@pytest.mark.asyncio
async def test_memory_batch_delete_saves_real_faiss_index_once(
    tmp_path: Path, monkeypatch
):
    db_path = tmp_path / "memories.db"
    index_path = tmp_path / "memories.index"
    vector_db = FaissVecDB(
        doc_store_path=str(db_path),
        index_store_path=str(index_path),
        embedding_provider=_DeterministicEmbeddingProvider(dim=24),
    )
    await vector_db.initialize()
    engine = MemoryEngine(
        db_path=str(db_path),
        faiss_db=vector_db,
        config={"graph_memory_enabled": False},
    )
    await engine.initialize()

    first_id = await engine.add_memory("first", "s1", "p1", 0.5, {})
    second_id = await engine.add_memory("second", "s1", "p1", 0.5, {})

    write_paths: list[str] = []
    original_write_index = vector_db.embedding_storage._write_index

    def counted_write_index(index, path: str) -> None:
        write_paths.append(path)
        original_write_index(index, path)

    monkeypatch.setattr(
        vector_db.embedding_storage,
        "_write_index",
        counted_write_index,
    )

    try:
        deleted = await engine.batch_delete_memories([first_id, second_id])
        assert deleted == 2
        assert len(write_paths) == 1
        assert await engine.get_memory(first_id) is None
        assert await engine.get_memory(second_id) is None
    finally:
        await engine.close()
        await vector_db.close()


@pytest.mark.asyncio
async def test_memory_archive_retains_document_and_restore_rebuilds_real_indexes(
    tmp_path: Path,
):
    db_path = tmp_path / "archive_memories.db"
    vector_db = FaissVecDB(
        doc_store_path=str(db_path),
        index_store_path=str(tmp_path / "archive_memories.index"),
        embedding_provider=_DeterministicEmbeddingProvider(dim=24),
    )
    await vector_db.initialize()
    engine = MemoryEngine(
        db_path=str(db_path),
        faiss_db=vector_db,
        config={
            "graph_memory_enabled": False,
            "recent_memory_count": 0,
            "auto_archived_enabled": True,
        },
    )
    await engine.initialize()
    memory_id = await engine.add_memory(
        "release archive verification",
        "test:private:archive",
        "persona-archive",
        0.2,
        {"key_facts": ["release archive verification"]},
    )
    await engine.update_memory(
        memory_id,
        {"metadata": {"create_time": 1.0, "last_access_time": 1.0}},
    )

    try:
        assert vector_db.embedding_storage.index.ntotal == 1
        assert (
            await engine.cleanup_old_memories(
                days_threshold=1, importance_threshold=0.3
            )
            == 1
        )
        archived = await engine.get_memory(memory_id)
        assert archived is not None
        archived_metadata = archived["metadata"]
        if isinstance(archived_metadata, str):
            archived_metadata = json.loads(archived_metadata)
        assert archived_metadata["status"] == "archived"
        assert vector_db.embedding_storage.index.ntotal == 0
        assert await engine.search_memories("release", k=5) == []

        assert await engine.restore_memory(memory_id) is True
        restored = await engine.get_memory(memory_id)
        assert restored is not None
        restored_metadata = restored["metadata"]
        if isinstance(restored_metadata, str):
            restored_metadata = json.loads(restored_metadata)
        assert restored_metadata["status"] == "active"
        assert vector_db.embedding_storage.index.ntotal == 1
        results = await engine.search_memories("release", k=5)
        assert [result.doc_id for result in results] == [memory_id]
    finally:
        await engine.close()
        await vector_db.close()


class _TestContext:
    def __init__(self, persona_id: str):
        self.conversation_manager = _ContextConversationManager(persona_id)
        self.persona_manager = _ContextPersonaManager(persona_id)


class _TestEvent:
    def __init__(self, session_id: str, message: str):
        self.unified_msg_origin = session_id
        self._message = message

    def plain_result(self, message):
        return message

    def get_message_type(self):
        return MessageType.FRIEND_MESSAGE

    def get_sender_id(self):
        return "user-1"

    def get_self_id(self):
        return "bot-1"

    def get_sender_name(self):
        return "Tester"

    def get_message_str(self):
        return self._message

    def get_messages(self):
        return []

    def get_platform_name(self):
        return "test"


@pytest_asyncio.fixture
async def real_db_stack(tmp_path: Path):
    memory_db_path = tmp_path / "memory.db"
    memory_index_path = tmp_path / "memory.index"
    conversation_db_path = tmp_path / "conversation.db"
    graph_memory_db_path = tmp_path / "graph_memory.db"
    graph_memory_index_path = tmp_path / "graph_memory.index"

    embedding_provider = _DeterministicEmbeddingProvider(dim=24)
    faiss_db = FaissVecDB(
        doc_store_path=str(memory_db_path),
        index_store_path=str(memory_index_path),
        embedding_provider=embedding_provider,
    )
    await faiss_db.initialize()

    graph_faiss_db = FaissVecDB(
        doc_store_path=str(graph_memory_db_path),
        index_store_path=str(graph_memory_index_path),
        embedding_provider=embedding_provider,
    )
    await graph_faiss_db.initialize()

    memory_engine = MemoryEngine(
        db_path=str(memory_db_path),
        faiss_db=faiss_db,
        graph_vector_db=graph_faiss_db,
        config={
            "fallback_enabled": True,
            "rrf_k": 60,
            "decay_rate": 0.01,
            "importance_weight": 1.0,
            "graph_memory_enabled": True,
        },
    )
    await memory_engine.initialize()

    conversation_store = ConversationStore(str(conversation_db_path))
    await conversation_store.initialize()
    conversation_manager = ConversationManager(
        store=conversation_store,
        max_cache_size=20,
        context_window_size=20,
        session_ttl=3600,
    )

    context = _TestContext(persona_id="persona-real")
    memory_processor = MemoryProcessor(
        llm_provider=_DeterministicLLMProvider(),
        context=context,
    )

    config_manager = ConfigManager(
        {
            "recall_engine": {"top_k": 5, "injection_method": "extra_user_content"},
            "reflection_engine": {"summary_trigger_rounds": 1},
            "session_manager": {"max_messages_per_session": 100},
        }
    )

    event_handler = EventHandler(
        context=context,
        config_manager=config_manager,
        memory_engine=memory_engine,
        memory_processor=memory_processor,
        conversation_manager=conversation_manager,
    )
    command_handler = CommandHandler(
        context=context,
        config_manager=config_manager,
        memory_engine=memory_engine,
        conversation_manager=conversation_manager,
        index_validator=None,
    )

    yield {
        "memory_engine": memory_engine,
        "conversation_manager": conversation_manager,
        "event_handler": event_handler,
        "command_handler": command_handler,
        "memory_db_path": str(memory_db_path),
    }

    await event_handler.shutdown()
    await memory_engine.close()
    await faiss_db.close()
    await graph_faiss_db.close()
    await conversation_store.close()


@pytest.mark.asyncio
async def test_command_handlers_with_real_database(real_db_stack):
    memory_engine = real_db_stack["memory_engine"]
    command_handler = real_db_stack["command_handler"]
    conversation_manager = real_db_stack["conversation_manager"]
    memory_db_path = real_db_stack["memory_db_path"]

    session_id = "test:private:cmd-session"
    event = _TestEvent(session_id, "search me")

    memory_id = await memory_engine.add_memory(
        content="I prefer coffee in the morning.",
        session_id=session_id,
        persona_id="persona-real",
        importance=0.9,
        metadata={"memory_type": "PREFERENCE"},
    )

    search_output = [
        msg async for msg in command_handler.handle_search(event, query="coffee", k=5)
    ]
    assert len(search_output) == 1
    assert f"ID: {memory_id}" in search_output[0]

    status_output = [msg async for msg in command_handler.handle_status(event)]
    assert len(status_output) == 1
    assert "LivingMemory" in status_output[0]
    assert "总记忆数: 1" in status_output[0]

    await conversation_manager.add_message(
        session_id=session_id,
        role="user",
        content="temporary message",
        sender_id="user-1",
        sender_name="Tester",
        platform="test",
    )
    reset_output = [msg async for msg in command_handler.handle_reset(event)]
    assert "已重置" in reset_output[0]
    assert await conversation_manager.store.get_message_count(session_id) == 0

    forget_output = [
        msg async for msg in command_handler.handle_forget(event, memory_id)
    ]
    assert "已删除记忆" in forget_output[0]

    async with aiosqlite.connect(memory_db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM documents WHERE id = ?",
            (memory_id,),
        )
        row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 0


@pytest.mark.asyncio
async def test_normal_message_pipeline_with_real_database(real_db_stack):
    event_handler = real_db_stack["event_handler"]
    conversation_manager = real_db_stack["conversation_manager"]
    memory_db_path = real_db_stack["memory_db_path"]

    session_id = "test:private:pipeline-session"
    event = _TestEvent(session_id, "I went running yesterday.")
    req = SimpleNamespace(
        prompt="I went running yesterday.",
        system_prompt="",
        contexts=[],
    )
    resp = LLMResponse(role="assistant", completion_text="Great habit, keep going!")

    await event_handler.handle_memory_recall(event, req)
    await event_handler.handle_memory_reflection(event, resp)
    await event_handler.shutdown()

    assert await conversation_manager.store.get_message_count(session_id) == 2

    async with aiosqlite.connect(memory_db_path) as db:
        cursor = await db.execute(
            """
            SELECT text, metadata
            FROM documents
            WHERE json_extract(metadata, '$.session_id') = ?
            """,
            (session_id,),
        )
        rows = list(await cursor.fetchall())

    assert len(rows) >= 1
    assert any("running" in row[0].lower() for row in rows)


@pytest.mark.asyncio
async def test_recall_injection_with_real_database(real_db_stack):
    memory_engine = real_db_stack["memory_engine"]
    event_handler = real_db_stack["event_handler"]

    session_id = "test:private:recall-session"
    await memory_engine.add_memory(
        content="User is considering buying noise-cancelling headphones.",
        session_id=session_id,
        persona_id="persona-real",
        importance=0.95,
        metadata={"memory_type": "PREFERENCE"},
    )

    event = _TestEvent(session_id, "What headphones should I buy?")
    req = SimpleNamespace(
        prompt="What headphones should I buy?",
        system_prompt="",
        contexts=[],
        extra_user_content_parts=[],
    )

    await event_handler.handle_memory_recall(event, req)
    assert len(req.extra_user_content_parts) == 1
    assert "<RAG-Faiss-Memory>" in req.extra_user_content_parts[0].text
    assert "headphones" in req.extra_user_content_parts[0].text.lower()


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_command_validation_messages_with_real_database(real_db_stack):
    command_handler = real_db_stack["command_handler"]

    session_id = "test:private:validation-session"
    event = _TestEvent(session_id, "validation")

    blank_search_output = [
        msg async for msg in command_handler.handle_search(event, query="   ", k=5)
    ]
    assert len(blank_search_output) == 1
    assert "查询关键词不能为空" in blank_search_output[0]

    invalid_forget_output = [
        msg async for msg in command_handler.handle_forget(event, doc_id=-1)
    ]
    assert len(invalid_forget_output) == 1
    assert "记忆 ID 必须为非负整数" in invalid_forget_output[0]


@pytest.mark.asyncio
async def test_command_status_error_includes_suggestions(real_db_stack):
    command_handler = real_db_stack["command_handler"]
    event = _TestEvent("test:private:status-error", "status")

    command_handler.memory_engine.get_statistics = AsyncMock(
        side_effect=RuntimeError("db offline")
    )

    status_output = [msg async for msg in command_handler.handle_status(event)]
    assert len(status_output) == 1
    assert "获取状态失败" in status_output[0]
    assert "建议排查" in status_output[0]
    assert "错误详情: db offline" in status_output[0]


@pytest.mark.asyncio
async def test_rebuild_index_without_validator_returns_actionable_message(
    real_db_stack,
):
    command_handler = real_db_stack["command_handler"]
    event = _TestEvent("test:private:rebuild-no-validator", "rebuild")

    output = [msg async for msg in command_handler.handle_rebuild_index(event)]
    assert len(output) == 1
    assert "记忆引擎或索引验证器未初始化" in output[0]
    assert "/lmem status" in output[0]


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_cleanup_preview_and_exec_paths(real_db_stack):
    from astrbot_plugin_livingmemory.core.base.constants import (
        MEMORY_INJECTION_FOOTER,
        MEMORY_INJECTION_HEADER,
    )

    command_handler = real_db_stack["command_handler"]
    event = _TestEvent("test:private:cleanup-session", "cleanup")

    history_payload = [
        {
            "role": "system",
            "content": f"{MEMORY_INJECTION_HEADER}\nold memory\n{MEMORY_INJECTION_FOOTER}",
        },
        {
            "role": "user",
            "content": (
                "start\n"
                f"{MEMORY_INJECTION_HEADER}\nold memory\n{MEMORY_INJECTION_FOOTER}\n"
                "end"
            ),
        },
    ]

    class _HistoryConversationManager:
        def __init__(self, history: list[dict]):
            self._history = history
            self.updated_history: list[dict] | None = None

        async def get_curr_conversation_id(self, umo: str) -> str:
            del umo
            return "conv-cleanup-1"

        async def get_conversation(self, umo: str, session_id: str):
            del umo, session_id
            return SimpleNamespace(
                history=json.dumps(self._history, ensure_ascii=False)
            )

        async def update_conversation(
            self,
            unified_msg_origin: str,
            conversation_id: str,
            history: list[dict],
        ):
            del unified_msg_origin, conversation_id
            self.updated_history = history

    command_handler.context = SimpleNamespace(
        conversation_manager=_HistoryConversationManager(history_payload)
    )

    preview_output = [
        msg async for msg in command_handler.handle_cleanup(event, dry_run=True)
    ]
    assert any("预演模式：清理完成" in msg for msg in preview_output)
    assert any("未实际修改数据" in msg for msg in preview_output)
    assert command_handler.context.conversation_manager.updated_history is None

    exec_output = [
        msg async for msg in command_handler.handle_cleanup(event, dry_run=False)
    ]
    assert any("清理完成" in msg for msg in exec_output)
    assert any("AstrBot 对话历史已更新" in msg for msg in exec_output)
    assert command_handler.context.conversation_manager.updated_history is not None

    cleaned_history = command_handler.context.conversation_manager.updated_history
    assert cleaned_history is not None
    for item in cleaned_history:
        content = item.get("content", "")
        assert MEMORY_INJECTION_HEADER not in content
        assert MEMORY_INJECTION_FOOTER not in content
