"""
Tests for user_id filtering and recent boost capabilities.
Covers:
  1. _matches_user_id OR matching (primary_user_id OR user_ids)
  2. BM25 retriever user_id filtering
  3. event_handler._storage_task metadata generation (user_ids / primary_user_id)
  4. MemorySearchTool user filtering passthrough
  5. HybridRetriever recent boost weighting
"""

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import aiosqlite
import pytest

from astrbot_plugin_livingmemory.core.processors.text_processor import TextProcessor
from astrbot_plugin_livingmemory.core.retrieval.bm25_retriever import (
    BM25Retriever,
    _matches_user_id as bm25_matches_user_id,
)
from astrbot_plugin_livingmemory.core.retrieval.vector_retriever import (
    _matches_user_id as vector_matches_user_id,
)
from astrbot_plugin_livingmemory.core.retrieval.graph_keyword_retriever import (
    _matches_user_id as graph_kw_matches_user_id,
)
from astrbot_plugin_livingmemory.core.retrieval.graph_vector_retriever import (
    _matches_user_id as graph_vec_matches_user_id,
)
from astrbot_plugin_livingmemory.core.retrieval.vector_retriever import VectorRetriever
from astrbot_plugin_livingmemory.core.retrieval.graph_vector_retriever import GraphVectorRetriever


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
# 1. _matches_user_id: OR matching across all retrievers
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("match_fn", [bm25_matches_user_id, vector_matches_user_id,
                                       graph_kw_matches_user_id, graph_vec_matches_user_id])
def test_matches_primary_user_id(match_fn):
    assert match_fn({"primary_user_id": "user-A", "user_ids": ["user-A", "user-B"]}, "user-A") is True


@pytest.mark.parametrize("match_fn", [bm25_matches_user_id, vector_matches_user_id,
                                       graph_kw_matches_user_id, graph_vec_matches_user_id])
def test_matches_user_ids_not_primary(match_fn):
    assert match_fn({"primary_user_id": "user-A", "user_ids": ["user-A", "user-B"]}, "user-B") is True


@pytest.mark.parametrize("match_fn", [bm25_matches_user_id, vector_matches_user_id,
                                       graph_kw_matches_user_id, graph_vec_matches_user_id])
def test_rejects_non_participant(match_fn):
    assert match_fn({"primary_user_id": "user-A", "user_ids": ["user-A"]}, "user-X") is False


@pytest.mark.parametrize("match_fn", [bm25_matches_user_id, vector_matches_user_id,
                                       graph_kw_matches_user_id, graph_vec_matches_user_id])
def test_no_user_fields_always_rejects(match_fn):
    assert match_fn({}, "user-A") is False


@pytest.mark.parametrize("match_fn", [bm25_matches_user_id, vector_matches_user_id,
                                       graph_kw_matches_user_id, graph_vec_matches_user_id])
def test_user_ids_only_no_primary(match_fn):
    assert match_fn({"user_ids": ["user-A", "user-B"]}, "user-B") is True


# ══════════════════════════════════════════════════════════════════════════════
# 2. BM25 Retriever: user_id filtering
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_bm25_user_filter_matches_primary_user_id(tmp_path: Path):
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
    retriever = await _seed_bm25(tmp_path, [
        (1, "群聊项目讨论记录", {
            "session_id": "s1", "persona_id": "p1",
            "primary_user_id": "user-A", "user_ids": ["user-A", "user-B", "user-C"],
        }),
    ])
    results = await retriever.search("项目", limit=5, session_id="s1", persona_id="p1", user_id="user-B")
    assert len(results) == 1


@pytest.mark.asyncio
async def test_bm25_user_filter_rejects_non_participant(tmp_path: Path):
    retriever = await _seed_bm25(tmp_path, [
        (1, "私密讨论内容", {
            "session_id": "s1", "persona_id": "p1",
            "primary_user_id": "user-A", "user_ids": ["user-A", "user-B"],
        }),
    ])
    results = await retriever.search("讨论", limit=5, session_id="s1", persona_id="p1", user_id="user-X")
    assert len(results) == 0


@pytest.mark.asyncio
async def test_bm25_user_filter_no_user_fields_excluded(tmp_path: Path):
    retriever = await _seed_bm25(tmp_path, [
        (1, "非常旧的记忆", {
            "session_id": "s1", "persona_id": "p1",
        }),
    ])
    results = await retriever.search("旧", limit=5, session_id="s1", persona_id="p1", user_id="user-A")
    assert len(results) == 0


# ══════════════════════════════════════════════════════════════════════════════
# 3. event_handler._storage_task: metadata generation
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_storage_task_group_chat_writes_user_ids_no_primary():
    """群聊总结应写入 user_ids 但不写 primary_user_id。"""
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

    assert "user_ids" in captured_metadata
    assert "user-A" in captured_metadata["user_ids"]
    assert "user-B" in captured_metadata["user_ids"]
    assert "bot-1" not in captured_metadata["user_ids"]
    assert "primary_user_id" not in captured_metadata


@pytest.mark.asyncio
async def test_storage_task_private_chat_writes_primary_user_id():
    """私聊总结应写入 primary_user_id 为非 bot 用户。"""
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

    assert captured_metadata.get("primary_user_id") == "user-A"
    assert "user_ids" in captured_metadata
    assert "user-A" in captured_metadata["user_ids"]
    assert "bot-1" not in captured_metadata["user_ids"]


# ══════════════════════════════════════════════════════════════════════════════
# 4. MemorySearchTool: user filtering passthrough
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_memory_search_tool_passthrough_user_id():
    """当 use_user_filtering=true 时，MemorySearchTool 应传递 user_id 给 search_memories。"""
    from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
    from astrbot_plugin_livingmemory.core.tools.memory_search_tool import MemorySearchTool

    config_manager = ConfigManager({
        "filtering_settings": {
            "use_user_filtering": True,
            "use_session_filtering": True,
            "use_persona_filtering": True,
        },
        "recall_engine": {"top_k": 5, "max_k": 10},
    })

    captured_kwargs = {}

    memory_engine = Mock()
    memory_engine.search_memories = AsyncMock(return_value=[])
    # Capture the call kwargs
    original_search = memory_engine.search_memories

    async def _capture_search(**kwargs):
        captured_kwargs.update(kwargs)
        return []

    memory_engine.search_memures = AsyncMock(side_effect=_capture_search)
    memory_engine.search_memories = AsyncMock(side_effect=_capture_search)

    # Mock context and event
    mock_context_wrapper = Mock()
    mock_inner_context = Mock()
    mock_event = Mock()
    mock_event.unified_msg_origin = "test:private:session-1"
    mock_event.get_sender_id = Mock(return_value="user-42")
    mock_inner_context.event = mock_event
    mock_context_wrapper.context = mock_inner_context

    tool = MemorySearchTool(
        context=Mock(),
        config_manager=config_manager,
        memory_engine=memory_engine,
    )

    await tool.call(mock_context_wrapper, query="测试查询", k=5)

    # Should have called search_memories with user_id and without session_id
    assert captured_kwargs.get("user_id") == "user-42"
    assert captured_kwargs.get("session_id") is None


@pytest.mark.asyncio
async def test_memory_search_tool_explicit_user_id_overrides_current_sender():
    """显式 user_id 应优先于当前发言者 sender_id。"""
    from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
    from astrbot_plugin_livingmemory.core.tools.memory_search_tool import MemorySearchTool

    config_manager = ConfigManager({
        "filtering_settings": {
            "use_user_filtering": True,
            "use_session_filtering": True,
            "use_persona_filtering": True,
        },
        "recall_engine": {"top_k": 5, "max_k": 10},
    })

    captured_kwargs = {}
    memory_engine = Mock()

    async def _capture_search(**kwargs):
        captured_kwargs.update(kwargs)
        return []

    memory_engine.search_memories = AsyncMock(side_effect=_capture_search)

    mock_context_wrapper = Mock()
    mock_inner_context = Mock()
    mock_event = Mock()
    mock_event.unified_msg_origin = "test:private:session-1"
    mock_event.get_sender_id = Mock(return_value="user-42")
    mock_inner_context.event = mock_event
    mock_context_wrapper.context = mock_inner_context

    tool = MemorySearchTool(
        context=Mock(),
        config_manager=config_manager,
        memory_engine=memory_engine,
    )

    await tool.call(
        mock_context_wrapper,
        query="测试查询",
        k=5,
        user_id="user-99",
    )

    assert captured_kwargs.get("user_id") == "user-99"
    assert captured_kwargs.get("session_id") is None


@pytest.mark.asyncio
async def test_memory_search_tool_no_user_filtering():
    """当 use_user_filtering=false 时，MemorySearchTool 不应传递 user_id。"""
    from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
    from astrbot_plugin_livingmemory.core.tools.memory_search_tool import MemorySearchTool

    config_manager = ConfigManager({
        "filtering_settings": {
            "use_user_filtering": False,
            "use_session_filtering": True,
            "use_persona_filtering": True,
        },
        "recall_engine": {"top_k": 5, "max_k": 10},
    })

    captured_kwargs = {}
    memory_engine = Mock()

    async def _capture_search(**kwargs):
        captured_kwargs.update(kwargs)
        return []

    memory_engine.search_memories = AsyncMock(side_effect=_capture_search)

    mock_context_wrapper = Mock()
    mock_inner_context = Mock()
    mock_event = Mock()
    mock_event.unified_msg_origin = "test:private:session-1"
    mock_event.get_sender_id = Mock(return_value="user-42")
    mock_inner_context.event = mock_event
    mock_context_wrapper.context = mock_inner_context

    tool = MemorySearchTool(
        context=Mock(),
        config_manager=config_manager,
        memory_engine=memory_engine,
    )

    await tool.call(mock_context_wrapper, query="测试查询", k=5)

    assert captured_kwargs.get("user_id") is None
    assert captured_kwargs.get("session_id") == "test:private:session-1"


# ══════════════════════════════════════════════════════════════════════════════
# 5. HybridRetriever: recent boost weighting
# ══════════════════════════════════════════════════════════════════════════════


def test_recent_boost_applied_to_recent_memories():
    """近期创建的记忆应获得额外加分。"""
    from astrbot_plugin_livingmemory.core.retrieval.hybrid_retriever import HybridRetriever
    from astrbot_plugin_livingmemory.core.retrieval.rrf_fusion import FusedResult

    config = {
        "recent_boost_hours": 48,
        "recent_boost_factor": 0.15,
        "decay_rate": 0.01,
        "importance_weight": 1.0,
        "score_alpha": 0.5,
        "score_beta": 0.25,
        "score_gamma": 0.25,
    }
    retriever = HybridRetriever(Mock(), Mock(), Mock(), config)

    now = time.time()
    fused_results = [
        FusedResult(
            doc_id=1, rrf_score=0.5, bm25_score=0.6, vector_score=0.7,
            content="近期记忆", metadata={"importance": 0.5, "create_time": now},
        ),
        FusedResult(
            doc_id=2, rrf_score=0.5, bm25_score=0.6, vector_score=0.7,
            content="旧记忆", metadata={"importance": 0.5, "create_time": now - 100 * 86400},
        ),
    ]

    results = retriever._apply_weighting(fused_results, now)

    # Both should have same base score, but recent one should have higher final
    recent = next(r for r in results if r.doc_id == 1)
    old = next(r for r in results if r.doc_id == 2)

    assert recent.final_score > old.final_score
    assert recent.score_breakdown["recent_boost"] > 0
    assert old.score_breakdown["recent_boost"] == 0.0


def test_recent_boost_disabled_when_hours_zero():
    """recent_boost_hours=0 时不应有近期加权。"""
    from astrbot_plugin_livingmemory.core.retrieval.hybrid_retriever import HybridRetriever
    from astrbot_plugin_livingmemory.core.retrieval.rrf_fusion import FusedResult

    config = {
        "recent_boost_hours": 0,
        "recent_boost_factor": 0.15,
        "decay_rate": 0.01,
        "importance_weight": 1.0,
        "score_alpha": 0.5,
        "score_beta": 0.25,
        "score_gamma": 0.25,
    }
    retriever = HybridRetriever(Mock(), Mock(), Mock(), config)

    now = time.time()
    fused_results = [
        FusedResult(
            doc_id=1, rrf_score=0.5, bm25_score=0.6, vector_score=0.7,
            content="近期记忆", metadata={"importance": 0.5, "create_time": now},
        ),
    ]

    results = retriever._apply_weighting(fused_results, now)
    assert results[0].score_breakdown["recent_boost"] == 0.0


def test_recent_boost_uses_create_time_not_last_access_time():
    """recent boost 应仅由 create_time 决定，而不是被最近访问时间重置。"""
    from astrbot_plugin_livingmemory.core.retrieval.hybrid_retriever import HybridRetriever
    from astrbot_plugin_livingmemory.core.retrieval.rrf_fusion import FusedResult

    config = {
        "recent_boost_hours": 48,
        "recent_boost_factor": 0.15,
        "decay_rate": 0.01,
        "importance_weight": 1.0,
        "score_alpha": 0.5,
        "score_beta": 0.25,
        "score_gamma": 0.25,
    }
    retriever = HybridRetriever(Mock(), Mock(), Mock(), config)

    now = time.time()
    fused_results = [
        FusedResult(
            doc_id=1,
            rrf_score=0.5,
            bm25_score=0.6,
            vector_score=0.7,
            content="旧但最近被访问的记忆",
            metadata={
                "importance": 0.5,
                "create_time": now - 100 * 86400,
                "last_access_time": now,
            },
        ),
    ]

    results = retriever._apply_weighting(fused_results, now)
    assert results[0].score_breakdown["recent_boost"] == 0.0


# ══════════════════════════════════════════════════════════════════════════════
# 6. Vector routes: fetch_k should expand for user-side filtering
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_vector_retriever_expands_fetch_k_when_user_filtering_only():
    class _FakeFaissDB:
        def __init__(self):
            self.calls = []

        async def retrieve(self, query, k, fetch_k, rerank, metadata_filters=None):
            self.calls.append(
                {
                    "query": query,
                    "k": k,
                    "fetch_k": fetch_k,
                    "metadata_filters": metadata_filters,
                }
            )
            return []

    faiss_db = _FakeFaissDB()
    retriever = VectorRetriever(faiss_db, text_processor=None, config={})

    await retriever.search(
        query="测试查询",
        k=5,
        session_id=None,
        persona_id=None,
        user_id="user-A",
    )

    assert faiss_db.calls[0]["fetch_k"] == 10
    assert faiss_db.calls[0]["metadata_filters"] is None


@pytest.mark.asyncio
async def test_vector_retriever_parses_string_metadata_for_user_filtering():
    class _FakeResult:
        def __init__(self, similarity, data):
            self.similarity = similarity
            self.data = data

    class _FakeFaissDB:
        async def retrieve(self, query, k, fetch_k, rerank, metadata_filters=None):
            del query, k, fetch_k, rerank, metadata_filters
            return [
                _FakeResult(
                    0.93,
                    {
                        "id": 7,
                        "text": "和 user-A 有关的记忆",
                        "metadata": json.dumps(
                            {
                                "primary_user_id": "user-A",
                                "user_ids": ["user-A", "user-B"],
                                "importance": 0.8,
                            },
                            ensure_ascii=False,
                        ),
                    },
                )
            ]

    retriever = VectorRetriever(_FakeFaissDB(), text_processor=None, config={})

    results = await retriever.search(
        query="测试查询",
        k=5,
        session_id=None,
        persona_id=None,
        user_id="user-A",
    )

    assert len(results) == 1
    assert results[0].doc_id == 7
    assert results[0].metadata["primary_user_id"] == "user-A"


@pytest.mark.asyncio
async def test_graph_vector_retriever_expands_fetch_k_when_user_filtering_only():
    class _FakeFaissDB:
        def __init__(self):
            self.calls = []

        async def retrieve(self, query, k, fetch_k, rerank, metadata_filters=None):
            self.calls.append(
                {
                    "query": query,
                    "k": k,
                    "fetch_k": fetch_k,
                    "metadata_filters": metadata_filters,
                }
            )
            return []

    faiss_db = _FakeFaissDB()
    retriever = GraphVectorRetriever(faiss_db, config={})

    await retriever.search(
        query="测试查询",
        k=5,
        session_id=None,
        persona_id=None,
        user_id="user-A",
    )

    assert faiss_db.calls[0]["fetch_k"] == 10
    assert faiss_db.calls[0]["metadata_filters"] is None
