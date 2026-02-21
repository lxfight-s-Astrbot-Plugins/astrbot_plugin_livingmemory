"""
End-to-end integration tests with real plugin components and real SQLite/FAISS storage.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import httpx
import pytest
import pytest_asyncio
from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.command_handler import CommandHandler
from astrbot_plugin_livingmemory.core.event_handler import EventHandler
from astrbot_plugin_livingmemory.core.managers.conversation_manager import (
    ConversationManager,
)
from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine
from astrbot_plugin_livingmemory.core.processors.memory_processor import MemoryProcessor
from astrbot_plugin_livingmemory.storage.conversation_store import ConversationStore
from astrbot_plugin_livingmemory.webui.server import WebUIServer

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

    embedding_provider = _DeterministicEmbeddingProvider(dim=24)
    faiss_db = FaissVecDB(
        doc_store_path=str(memory_db_path),
        index_store_path=str(memory_index_path),
        embedding_provider=embedding_provider,
    )
    await faiss_db.initialize()

    memory_engine = MemoryEngine(
        db_path=str(memory_db_path),
        faiss_db=faiss_db,
        config={
            "fallback_enabled": True,
            "rrf_k": 60,
            "decay_rate": 0.01,
            "importance_weight": 1.0,
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
            "recall_engine": {"top_k": 5, "injection_method": "system_prompt"},
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
    webui_server = WebUIServer(
        memory_engine=memory_engine,
        conversation_manager=conversation_manager,
        index_validator=None,
        config={
            "host": "127.0.0.1",
            "port": 6186,
            "access_password": "test-password",
            "session_timeout": 3600,
        },
    )

    yield {
        "memory_engine": memory_engine,
        "conversation_manager": conversation_manager,
        "event_handler": event_handler,
        "command_handler": command_handler,
        "webui_server": webui_server,
        "memory_db_path": str(memory_db_path),
    }

    await event_handler.shutdown()
    await memory_engine.close()
    await faiss_db.close()
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
    )

    await event_handler.handle_memory_recall(event, req)
    assert "<RAG-Faiss-Memory>" in req.system_prompt
    assert "headphones" in req.system_prompt.lower()


@pytest.mark.asyncio
async def test_webui_api_with_real_database(real_db_stack):
    memory_engine = real_db_stack["memory_engine"]
    memory_db_path = real_db_stack["memory_db_path"]
    webui_server = real_db_stack["webui_server"]

    session_id = "test:private:webui-session"
    memory_id = await memory_engine.add_memory(
        content="I plan to visit Tokyo in spring.",
        session_id=session_id,
        persona_id="persona-real",
        importance=0.85,
        metadata={"memory_type": "PLAN"},
    )

    transport = httpx.ASGITransport(app=webui_server._app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        login_resp = await client.post(
            "/api/login",
            json={"password": "test-password"},
        )
        assert login_resp.status_code == 200
        token = login_resp.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        stats_resp = await client.get("/api/stats", headers=headers)
        assert stats_resp.status_code == 200
        assert stats_resp.json()["success"] is True
        assert stats_resp.json()["data"]["total_memories"] >= 1

        list_resp = await client.get(
            "/api/memories?page=1&page_size=20",
            headers=headers,
        )
        assert list_resp.status_code == 200
        assert list_resp.json()["success"] is True
        ids = [item["id"] for item in list_resp.json()["data"]["items"]]
        assert memory_id in ids

        search_resp = await client.post(
            "/api/memories/search",
            headers=headers,
            json={"query": "Tokyo", "k": 5, "session_id": session_id},
        )
        assert search_resp.status_code == 200
        assert search_resp.json()["success"] is True
        assert any(item["id"] == memory_id for item in search_resp.json()["data"])

        recall_resp = await client.post(
            "/api/recall/test",
            headers=headers,
            json={"query": "visit Tokyo", "k": 5, "session_id": session_id},
        )
        assert recall_resp.status_code == 200
        assert recall_resp.json()["success"] is True
        assert recall_resp.json()["data"]["total"] >= 1
        assert any(
            item["memory_id"] == memory_id
            for item in recall_resp.json()["data"]["results"]
        )

        delete_resp = await client.delete(f"/api/memories/{memory_id}", headers=headers)
        assert delete_resp.status_code == 200
        assert delete_resp.json()["success"] is True

    async with aiosqlite.connect(memory_db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM documents WHERE id = ?",
            (memory_id,),
        )
        row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 0


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
async def test_webui_messages_for_disabled_and_enabled_paths(real_db_stack):
    command_handler = real_db_stack["command_handler"]
    webui_server = real_db_stack["webui_server"]
    event = _TestEvent("test:private:webui-msg", "webui")

    disabled_output = [msg async for msg in command_handler.handle_webui(event)]
    assert len(disabled_output) == 1
    assert "WebUI 功能当前未启用" in disabled_output[0]
    assert "webui.enabled=false" in disabled_output[0]

    command_handler.config_manager = ConfigManager(
        {
            "webui_settings": {
                "enabled": True,
                "host": "0.0.0.0",
                "port": 6186,
                "access_password": "test-password",
            }
        }
    )
    command_handler.webui_server = webui_server

    enabled_output = [msg async for msg in command_handler.handle_webui(event)]
    assert len(enabled_output) == 1
    assert "访问地址: http://127.0.0.1:6186" in enabled_output[0]
    assert "记忆编辑与管理" in enabled_output[0]


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
