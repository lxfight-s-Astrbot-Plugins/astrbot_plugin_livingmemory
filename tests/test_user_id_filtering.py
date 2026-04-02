"""
Tests for user_id filtering logic in retrievers and event_handler metadata generation.
Covers:
  1. BM25/Vector retriever user_id OR matching (primary_user_id OR user_ids)
  2. Graph retriever _matches_user_id static method
  3. event_handler._storage_task group chat metadata generation (user_ids / primary_user_id)
  4. recall_memory / save_memory llm_tool basic behavior
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import aiosqlite
import pytest
from astrbot.api.platform import MessageType
from astrbot_plugin_livingmemory.core.processors.text_processor import TextProcessor
from astrbot_plugin_livingmemory.core.retrieval.bm25_retriever import BM25Retriever
from astrbot_plugin_livingmemory.core.retrieval.graph_keyword_retriever import (
    GraphKeywordRetriever,
)
from astrbot_plugin_livingmemory.core.retrieval.graph_vector_retriever import (
    GraphVectorRetriever,
)


# ── Helper: seed BM25 retriever with documents ──


async def _seed_bm25(tmp_path: Path, docs: list[tuple[int, str, dict]]) -> BM25Retriever:
    """Create a BM25Retriever and seed it with (id, text, metadata) tuples."""
    db_path = tmp_path / "bm25_user.db"
    retriever = BM25Retriever(str(db_path), TextProcessor())
    await retriever.initialize()

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY, text TEXT, metadata TEXT)"
        )
        for doc_id, text, meta in docs:
            await db.execute(
                "INSERT INTO documents(id, text, metadata) VALUES (?, ?, ?)",
                (doc_id, text, json.dumps(meta, ensure_ascii=False)),
            )
        await db.commit()

    for doc_id, text, meta in docs:
        await retriever.add_document(doc_id, text, meta)

    return retriever


# ══════════════════════════════════════════════════════════════════════════════
# 1. BM25 Retriever: user_id OR matching
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_bm25_user_filter_matches_primary_user_id(tmp_path: Path):
    """primary_user_id 匹配时应返回记忆。"""
    retriever = await _seed_bm25(tmp_path, [
        (1, "用户A的编程偏好", {
            "session_id": "s1", "persona_id": "p1",
            "primary_user_id": "user-A", "user_ids": ["user-A", "user-B"],
        }),
    ])
    results = await retriever.search("编程", limit=5, session_id="s1", persona_id="p1", user_id="user-A")
    assert len(results) == 1
    assert results[0].doc_id == 1


@pytest.mark.asyncio
async def test_bm25_user_filter_matches_user_ids_when_not_primary(tmp_path: Path):
    """user_id 不是 primary 但在 user_ids 列表中时，也应返回记忆。"""
    retriever = await _seed_bm25(tmp_path, [
        (1, "群聊项目讨论记录", {
            "session_id": "s1", "persona_id": "p1",
            "primary_user_id": "user-A", "user_ids": ["user-A", "user-B", "user-C"],
        }),
    ])
    # user-B 不是 primary，但在 user_ids 中
    results = await retriever.search("项目", limit=5, session_id="s1", persona_id="p1", user_id="user-B")
    assert len(results) == 1, "user_ids 中的参与者应能检索到记忆"


@pytest.mark.asyncio
async def test_bm25_user_filter_rejects_non_participant(tmp_path: Path):
    """完全不在 primary_user_id 和 user_ids 中的用户应检索不到。"""
    retriever = await _seed_bm25(tmp_path, [
        (1, "私密讨论内容", {
            "session_id": "s1", "persona_id": "p1",
            "primary_user_id": "user-A", "user_ids": ["user-A", "user-B"],
        }),
    ])
    results = await retriever.search("讨论", limit=5, session_id="s1", persona_id="p1", user_id="user-X")
    assert len(results) == 0, "非参与者不应检索到记忆"


@pytest.mark.asyncio
async def test_bm25_user_filter_backward_compat_no_primary(tmp_path: Path):
    """旧记忆无 primary_user_id 时，应回退到 user_ids 匹配。"""
    retriever = await _seed_bm25(tmp_path, [
        (1, "旧版本存储的记忆", {
            "session_id": "s1", "persona_id": "p1",
            "user_ids": ["user-A", "user-B"],
            # 无 primary_user_id
        }),
    ])
    results = await retriever.search("旧版本", limit=5, session_id="s1", persona_id="p1", user_id="user-B")
    assert len(results) == 1, "旧记忆应通过 user_ids 匹配"


@pytest.mark.asyncio
async def test_bm25_user_filter_backward_compat_no_user_fields(tmp_path: Path):
    """极旧记忆既无 primary_user_id 也无 user_ids 时，应被过滤掉。"""
    retriever = await _seed_bm25(tmp_path, [
        (1, "非常旧的记忆", {
            "session_id": "s1", "persona_id": "p1",
            # 无任何 user 字段
        }),
    ])
    results = await retriever.search("旧", limit=5, session_id="s1", persona_id="p1", user_id="user-A")
    assert len(results) == 0, "无 user 字段的记忆在 user_id 过滤下应被排除"


# ══════════════════════════════════════════════════════════════════════════════
# 2. Graph retriever _matches_user_id OR matching
# ══════════════════════════════════════════════════════════════════════════════


def test_graph_keyword_matches_primary():
    assert GraphKeywordRetriever._matches_user_id(
        {"primary_user_id": "user-A", "user_ids": ["user-A", "user-B"]}, "user-A"
    ) is True


def test_graph_keyword_matches_user_ids_not_primary():
    """非 primary 但在 user_ids 中应返回 True。"""
    assert GraphKeywordRetriever._matches_user_id(
        {"primary_user_id": "user-A", "user_ids": ["user-A", "user-B"]}, "user-B"
    ) is True


def test_graph_keyword_rejects_non_participant():
    assert GraphKeywordRetriever._matches_user_id(
        {"primary_user_id": "user-A", "user_ids": ["user-A", "user-B"]}, "user-X"
    ) is False


def test_graph_keyword_none_user_id_always_matches():
    assert GraphKeywordRetriever._matches_user_id({"primary_user_id": "user-A"}, None) is True


def test_graph_vector_matches_user_ids_not_primary():
    """GraphVectorRetriever 应有相同的 OR 匹配行为。"""
    assert GraphVectorRetriever._matches_user_id(
        {"primary_user_id": "user-A", "user_ids": ["user-A", "user-B"]}, "user-B"
    ) is True


def test_graph_vector_rejects_non_participant():
    assert GraphVectorRetriever._matches_user_id(
        {"primary_user_id": "user-A", "user_ids": ["user-A"]}, "user-X"
    ) is False


# ══════════════════════════════════════════════════════════════════════════════
# 3. event_handler._storage_task: group chat metadata generation
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_storage_task_group_chat_no_primary_user_id(
):
    """群聊总结不应设置 primary_user_id，因为无法从发言顺序判断记忆关于谁。"""
    from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
    from astrbot_plugin_livingmemory.core.event_handler import EventHandler
    from astrbot_plugin_livingmemory.core.models.conversation_models import Message

    memory_engine = Mock()
    captured_metadata = {}

    async def _capture(content, session_id, persona_id, importance, metadata):
        captured_metadata.update(metadata)
        return 1

    memory_engine.add_memory = AsyncMock(side_effect=_capture)

    conversation_manager = Mock()
    conversation_manager.get_session_metadata = AsyncMock(return_value=0)
    conversation_manager.update_session_metadata = AsyncMock()
    conversation_manager.invalidate_cache = AsyncMock()
    conversation_manager.store = Mock()
    conversation_manager.store.update_message_metadata = AsyncMock()

    memory_processor = Mock()
    memory_processor.process_conversation = AsyncMock(
        return_value=("群聊摘要", {"topics": ["讨论"]}, 0.6)
    )

    handler = EventHandler(
        context=Mock(),
        config_manager=ConfigManager({
            "recall_engine": {"top_k": 3},
            "reflection_engine": {"summary_trigger_rounds": 1},
            "session_manager": {"max_messages_per_session": 100},
        }),
        memory_engine=memory_engine,
        memory_processor=memory_processor,
        conversation_manager=conversation_manager,
    )

    messages = [
        Message(id=1, session_id="g1", role="user", content="A说的话",
                sender_id="user-A", sender_name="Alice", group_id="grp1", platform="test", metadata={}),
        Message(id=2, session_id="g1", role="user", content="B说的话",
                sender_id="user-B", sender_name="Bob", group_id="grp1", platform="test", metadata={}),
        Message(id=3, session_id="g1", role="assistant", content="Bot回复",
                sender_id="bot-1", sender_name="Bot", group_id="grp1", platform="test",
                metadata={"is_bot_message": True}),
        Message(id=4, session_id="g1", role="user", content="B又说了一句",
                sender_id="user-B", sender_name="Bob", group_id="grp1", platform="test", metadata={}),
    ]

    await handler._storage_task(
        session_id="g1",
        history_messages=messages,
        persona_id="p1",
        start_index=0,
        end_index=4,
        retry_count=0,
    )

    # 群聊应包含所有发言者
    assert "user_ids" in captured_metadata
    user_ids = captured_metadata["user_ids"]
    assert "user-A" in user_ids
    assert "user-B" in user_ids

    # 群聊不应设置 primary_user_id
    assert "primary_user_id" not in captured_metadata


@pytest.mark.asyncio
async def test_storage_task_private_chat_sets_primary_user_id():
    """私聊总结应设置 primary_user_id 为非 bot 用户。"""
    from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
    from astrbot_plugin_livingmemory.core.event_handler import EventHandler
    from astrbot_plugin_livingmemory.core.models.conversation_models import Message

    memory_engine = Mock()
    captured_metadata = {}

    async def _capture(content, session_id, persona_id, importance, metadata):
        captured_metadata.update(metadata)
        return 1

    memory_engine.add_memory = AsyncMock(side_effect=_capture)

    conversation_manager = Mock()
    conversation_manager.get_session_metadata = AsyncMock(return_value=0)
    conversation_manager.update_session_metadata = AsyncMock()
    conversation_manager.invalidate_cache = AsyncMock()
    conversation_manager.store = Mock()
    conversation_manager.store.update_message_metadata = AsyncMock()

    memory_processor = Mock()
    memory_processor.process_conversation = AsyncMock(
        return_value=("私聊摘要", {"topics": ["对话"]}, 0.7)
    )

    handler = EventHandler(
        context=Mock(),
        config_manager=ConfigManager({
            "recall_engine": {"top_k": 3},
            "reflection_engine": {"summary_trigger_rounds": 1},
            "session_manager": {"max_messages_per_session": 100},
        }),
        memory_engine=memory_engine,
        memory_processor=memory_processor,
        conversation_manager=conversation_manager,
    )

    # 私聊消息: group_id=None
    messages = [
        Message(id=1, session_id="p1", role="user", content="用户说的话",
                sender_id="user-A", sender_name="Alice", group_id=None, platform="test", metadata={}),
        Message(id=2, session_id="p1", role="assistant", content="Bot回复",
                sender_id="bot-1", sender_name="Bot", group_id=None, platform="test",
                metadata={"is_bot_message": True}),
    ]

    await handler._storage_task(
        session_id="p1",
        history_messages=messages,
        persona_id="persona_1",
        start_index=0,
        end_index=2,
        retry_count=0,
    )

    # 私聊应设置 primary_user_id
    assert captured_metadata.get("primary_user_id") == "user-A"
    assert "user_ids" in captured_metadata
    assert "user-A" in captured_metadata["user_ids"]


# ══════════════════════════════════════════════════════════════════════════════
# 4. recall_memory / save_memory llm_tool tests
# ══════════════════════════════════════════════════════════════════════════════


def _make_plugin_mock():
    """Create a minimal mock of the LivingMemory plugin for tool testing."""
    from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager

    plugin = Mock()
    plugin.config_manager = ConfigManager({
        "filtering_settings": {
            "use_user_filtering": False,
            "use_session_filtering": True,
            "use_persona_filtering": True,
        },
    })
    plugin.initializer = Mock()
    plugin.initializer.memory_engine = AsyncMock()
    plugin.initializer.memory_engine.search_memories = AsyncMock(return_value=[])
    plugin.initializer.memory_engine.add_memory = AsyncMock(return_value="doc-123")
    plugin.context = Mock()
    return plugin


def _make_tool_event(group: bool = False):
    event = Mock()
    event.unified_msg_origin = "test:group:g1" if group else "test:private:p1"
    event.get_message_type = Mock(
        return_value=MessageType.GROUP_MESSAGE if group else MessageType.FRIEND_MESSAGE
    )
    event.get_sender_id = Mock(return_value="user-1")
    event.get_sender_name = Mock(return_value="Tester")
    return event


@pytest.mark.asyncio
async def test_tool_save_memory_private_chat_metadata():
    """save_memory 工具在私聊中应设置正确的 interaction_type 和 user metadata。"""
    # Import the actual plugin class to test its tool methods
    from astrbot_plugin_livingmemory.main import LivingMemoryPlugin as LivingMemory

    event = _make_tool_event(group=False)
    plugin = _make_plugin_mock()

    # Bind the tool method to our mock
    tool_fn = LivingMemory.tool_save_memory
    # We need to simulate _ensure_plugin_ready returning (True, "")
    plugin._ensure_plugin_ready = AsyncMock(return_value=(True, ""))

    with patch(
        "astrbot_plugin_livingmemory.main.get_persona_id",
        new_callable=AsyncMock,
        return_value="persona_1",
    ):
        result = await tool_fn(plugin, event, content="用户喜欢Python", importance=0.8)

    assert "doc-123" in result
    # Check the metadata passed to add_memory
    call_kwargs = plugin.initializer.memory_engine.add_memory.call_args.kwargs
    assert call_kwargs["metadata"]["interaction_type"] == "private_chat"
    assert call_kwargs["metadata"]["primary_user_id"] == "user-1"
    assert call_kwargs["metadata"]["user_ids"] == ["user-1"]


@pytest.mark.asyncio
async def test_tool_save_memory_group_chat_metadata():
    """save_memory 工具在群聊中应设置 interaction_type=group_chat 且不写 primary_user_id。"""
    from astrbot_plugin_livingmemory.main import LivingMemoryPlugin as LivingMemory

    event = _make_tool_event(group=True)
    plugin = _make_plugin_mock()
    plugin._ensure_plugin_ready = AsyncMock(return_value=(True, ""))

    with patch(
        "astrbot_plugin_livingmemory.main.get_persona_id",
        new_callable=AsyncMock,
        return_value="persona_1",
    ):
        result = await LivingMemory.tool_save_memory(plugin, event, content="群聊讨论结果", importance=0.6)

    call_kwargs = plugin.initializer.memory_engine.add_memory.call_args.kwargs
    assert call_kwargs["metadata"]["interaction_type"] == "group_chat"
    assert call_kwargs["metadata"]["user_ids"] == ["user-1"]
    assert "primary_user_id" not in call_kwargs["metadata"]


@pytest.mark.asyncio
async def test_tool_recall_memory_returns_formatted_results():
    """recall_memory 工具应返回格式化的记忆列表。"""
    from astrbot_plugin_livingmemory.main import LivingMemoryPlugin as LivingMemory

    event = _make_tool_event(group=False)
    plugin = _make_plugin_mock()
    plugin._ensure_plugin_ready = AsyncMock(return_value=(True, ""))

    # Mock search results
    import time
    mem = Mock()
    mem.content = "用户喜欢吃寿司"
    mem.final_score = 0.85
    mem.metadata = {"importance": 0.9, "create_time": time.time()}
    plugin.initializer.memory_engine.search_memories = AsyncMock(return_value=[mem])

    with patch(
        "astrbot_plugin_livingmemory.main.get_persona_id",
        new_callable=AsyncMock,
        return_value="persona_1",
    ):
        result = await LivingMemory.tool_recall_memory(plugin, event, keyword="寿司", count=3)

    assert "寿司" in result
    assert "0.85" in result


@pytest.mark.asyncio
async def test_tool_recall_memory_returns_empty_message():
    """recall_memory 无结果时应返回提示信息。"""
    from astrbot_plugin_livingmemory.main import LivingMemoryPlugin as LivingMemory

    event = _make_tool_event(group=False)
    plugin = _make_plugin_mock()
    plugin._ensure_plugin_ready = AsyncMock(return_value=(True, ""))
    plugin.initializer.memory_engine.search_memories = AsyncMock(return_value=[])

    with patch(
        "astrbot_plugin_livingmemory.main.get_persona_id",
        new_callable=AsyncMock,
        return_value="persona_1",
    ):
        result = await LivingMemory.tool_recall_memory(plugin, event, keyword="不存在的主题", count=3)

    assert "没有找到" in result


@pytest.mark.asyncio
async def test_tool_save_memory_not_ready():
    """插件未就绪时 save_memory 应返回错误信息。"""
    from astrbot_plugin_livingmemory.main import LivingMemoryPlugin as LivingMemory

    event = _make_tool_event(group=False)
    plugin = _make_plugin_mock()
    plugin._ensure_plugin_ready = AsyncMock(return_value=(False, "初始化中"))

    with patch(
        "astrbot_plugin_livingmemory.main.get_persona_id",
        new_callable=AsyncMock,
        return_value="persona_1",
    ):
        result = await LivingMemory.tool_save_memory(plugin, event, content="test", importance=0.5)

    assert "未就绪" in result
    plugin.initializer.memory_engine.add_memory.assert_not_awaited()
