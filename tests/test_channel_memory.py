"""G3 频道公共记忆 + 群聊联合召回测试。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from astrbot.api.platform import MessageType
from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.base.config_validator import (
    ChannelMemoryConfig,
    LivingMemoryConfig,
)
from astrbot_plugin_livingmemory.core.event_handler import EventHandler
from astrbot_plugin_livingmemory.core.managers.conversation_manager import (
    ConversationManager,
)
from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine
from astrbot_plugin_livingmemory.core.memory_scope import (
    CHANNEL_SCOPE_PREFIX,
    channel_scope_parts,
    is_channel_scope,
    memory_scope_sql,
    memory_scope_visible,
    merge_memory_scopes,
    normalize_memory_scopes,
    resolve_channel_memory_scope,
    resolve_event_memory_access,
)
from astrbot_plugin_livingmemory.core.retrieval.hybrid_retriever import HybridResult
from astrbot_plugin_livingmemory.storage.conversation_store import ConversationStore
from tests.test_agentic_engine_retrieval import _SharedDbFaiss

CHANNEL = f"{CHANNEL_SCOPE_PREFIX}:qq:group-42"


class _FreshMetadataFaiss(_SharedDbFaiss):
    """Vector hit metadata must come from the documents table, like production."""

    async def retrieve(self, query, k, fetch_k, rerank, metadata_filters=None):
        results = await super().retrieve(
            query, k, fetch_k, rerank, metadata_filters=None
        )
        engine = self._holder["engine"]
        refreshed = []
        for hit in results:
            doc_id = int(hit.data["id"])
            cursor = await engine.db_connection.execute(
                "SELECT text, metadata FROM documents WHERE id = ?", (doc_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                continue
            refreshed.append(
                SimpleNamespace(
                    similarity=hit.similarity,
                    data={
                        "id": doc_id,
                        "text": row["text"],
                        "metadata": json.loads(row["metadata"] or "{}"),
                    },
                )
            )
        return refreshed[:k]


class _Event:
    def __init__(
        self,
        *,
        group: bool = True,
        group_id: str = "group-42",
        platform: str = "qq",
        session_id: str | None = None,
        sender_id: str = "user-a",
    ) -> None:
        self.unified_msg_origin = session_id or (
            f"{platform}:GroupMessage:{group_id}"
            if group
            else f"{platform}:FriendMessage:{sender_id}"
        )
        self.sender_id = sender_id
        self.sender_name = sender_id
        self.platform = platform
        self.group_id = group_id if group else ""
        self._group = group

    def get_message_type(self):
        return MessageType.GROUP_MESSAGE if self._group else MessageType.FRIEND_MESSAGE

    def get_group_id(self):
        return self.group_id

    def get_platform_name(self):
        return self.platform

    def get_sender_id(self):
        return self.sender_id

    def get_sender_name(self):
        return self.sender_name

    def get_self_id(self):
        return "bot-1"

    def get_extra(self, key, default=None):
        return default


def _config(**overrides) -> dict:
    channel = {
        "enabled": True,
        "min_user_slots": 1,
        "min_channel_slots": 1,
        "include_archived": False,
    }
    channel.update(overrides.pop("channel_memory", {}))
    config = {
        "channel_memory": channel,
        "filtering_settings": {
            "memory_scope_mode": "legacy",
            "use_session_filtering": True,
            "use_persona_filtering": False,
            "isolated_sessions": "",
        },
        "access_control": {"whitelist_enabled": False},
    }
    config.update(overrides)
    return config


# ---------------- 作用域解析 ----------------


def test_channel_scope_resolution_matrix():
    assert resolve_channel_memory_scope(_config(), _Event()) == CHANNEL
    assert resolve_channel_memory_scope(_config(), _Event(group=False)) is None
    assert (
        resolve_channel_memory_scope(
            _config(channel_memory={"enabled": False}), _Event()
        )
        is None
    )
    assert (
        resolve_channel_memory_scope(
            _config(filtering_settings={"memory_scope_mode": "global"}),
            _Event(),
        )
        is None
    )
    isolated = _config(
        filtering_settings={"isolated_sessions": "qq:GroupMessage:group-42"}
    )
    assert resolve_channel_memory_scope(isolated, _Event()) is None
    assert is_channel_scope(CHANNEL) is True
    assert channel_scope_parts(CHANNEL) == ("qq", "group-42")
    assert channel_scope_parts("livingmemory:user:qq:a") == (None, None)


@pytest.mark.asyncio
async def test_event_memory_access_exposes_channel_scope_only_for_groups():
    context = SimpleNamespace()
    group_access = await resolve_event_memory_access(_config(), context, _Event())
    assert group_access.allowed is True
    assert group_access.channel_scope == CHANNEL

    private_access = await resolve_event_memory_access(
        _config(), context, _Event(group=False)
    )
    assert private_access.channel_scope is None

    disabled_access = await resolve_event_memory_access(
        _config(channel_memory={"enabled": False}), context, _Event()
    )
    assert disabled_access.channel_scope is None


def test_scope_filter_helpers_support_single_and_multi_scope():
    clause, params = memory_scope_sql("session_id", "a")
    assert clause == "session_id = ?"
    assert params == ["a"]

    clause, params = memory_scope_sql("session_id", ["a", CHANNEL])
    assert clause == "session_id IN (?,?)"
    assert params == ["a", CHANNEL]

    assert normalize_memory_scopes(None) is None
    assert merge_memory_scopes("a", None) == "a"
    assert merge_memory_scopes("a", ["b", "a"]) == ["a", "b"]
    assert memory_scope_visible({"session_id": CHANNEL}, ["a", CHANNEL])
    assert not memory_scope_visible({"session_id": "x"}, ["a", CHANNEL])


def test_channel_config_defaults_match_schema_and_i18n():
    defaults = ChannelMemoryConfig()
    assert defaults.enabled is False
    assert defaults.min_user_slots == 1
    assert defaults.min_channel_slots == 1
    assert defaults.include_archived is False
    assert LivingMemoryConfig().channel_memory.enabled is False

    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8"))
    group = schema["channel_memory"]
    assert group["invisible"] is True
    assert group["items"]["enabled"]["default"] is False
    assert group["items"]["min_user_slots"]["default"] == 1
    assert group["items"]["min_channel_slots"]["default"] == 1
    assert group["items"]["include_archived"]["default"] is False

    for locale in ("zh-CN", "en-US", "ru-RU", "ja-JP"):
        data = json.loads(
            (root / f".astrbot-plugin/i18n/{locale}.json").read_text(encoding="utf-8")
        )
        block = data["config"]["channel_memory"]
        assert block["description"]
        assert len(block["hint"]) > 1
        for field in (
            "enabled",
            "min_user_slots",
            "min_channel_slots",
            "include_archived",
        ):
            assert block[field]["description"]
            assert block[field]["hint"]


# ---------------- 写入：群总结进入频道作用域 ----------------


async def _make_channel_handler(tmp_path: Path):
    store = ConversationStore(str(tmp_path / "conv.db"))
    await store.initialize()
    manager = ConversationManager(
        store=store, max_cache_size=10, context_window_size=20, session_ttl=3600
    )
    engine = Mock()
    engine.find_similar_memory = AsyncMock(return_value=None)
    engine.add_memory = AsyncMock(return_value=1)
    config_manager = ConfigManager(_config())
    handler = EventHandler(
        context=Mock(),
        config_manager=config_manager,
        memory_engine=engine,
        memory_processor=Mock(),
        conversation_manager=manager,
    )
    return handler, manager, store, engine, config_manager


def _configure_processor(handler):
    async def _summary(*args, **kwargs):
        return "群聊总结", {"topics": ["topic"]}, 0.6

    handler.memory_processor.process_conversation = AsyncMock(side_effect=_summary)
    handler.memory_processor.classify_atoms_from_metadata = Mock(return_value=[])


@pytest.mark.asyncio
async def test_group_summary_is_written_to_channel_scope(tmp_path: Path):
    handler, manager, store, engine, _ = await _make_channel_handler(tmp_path)
    event = _Event()
    session_id = event.unified_msg_origin
    for index in range(4):
        await manager.add_message(
            session_id=session_id,
            role="user" if index % 2 == 0 else "assistant",
            content=f"msg-{index}",
            sender_id="user-a",
            group_id="group-42",
            timestamp=time.time() + index,
        )
    _configure_processor(handler)
    plan = await handler._memory_reflection._build_reflection_window(
        event, session_id, 4, 0, None, None
    )
    assert plan is not None
    assert plan.memory_scope == CHANNEL

    await handler._memory_reflection._consume_reflection_segments(plan)
    await engine.add_memory.async_wait() if False else None
    assert engine.add_memory.await_count == 1
    kwargs = engine.add_memory.await_args.kwargs
    assert kwargs["session_id"] == CHANNEL
    assert kwargs["metadata"]["scope_kind"] == "channel"
    assert kwargs["metadata"]["channel_id"] == "group-42"
    assert kwargs["metadata"]["channel_platform"] == "qq"
    assert kwargs["metadata"]["source_session_id"] == session_id

    await handler.shutdown()
    await store.close()


@pytest.mark.asyncio
async def test_private_summary_stays_in_private_scope(tmp_path: Path):
    handler, manager, store, engine, _ = await _make_channel_handler(tmp_path)
    event = _Event(group=False, session_id="qq:FriendMessage:user-a")
    session_id = event.unified_msg_origin
    for index in range(4):
        await manager.add_message(
            session_id=session_id,
            role="user" if index % 2 == 0 else "assistant",
            content=f"msg-{index}",
            sender_id="user-a",
            timestamp=time.time() + index,
        )
    _configure_processor(handler)
    plan = await handler._memory_reflection._build_reflection_window(
        event, session_id, 4, 0, None, None
    )
    assert plan is not None
    assert plan.memory_scope == session_id
    await handler.shutdown()
    await store.close()


# ---------------- 联合召回 ----------------


def _make_engine(tmp_path: Path, name: str = "channel.db", **config) -> tuple:
    holder: dict = {}
    engine_config = {
        "atom_enabled": False,
        "graph_memory_enabled": False,
        "channel_min_user_slots": 1,
        "channel_min_channel_slots": 1,
        "search_cache_enabled": True,
        "search_cache_ttl_seconds": 60,
    }
    engine_config.update(config)
    faiss_cls = config.pop("_faiss_cls", _SharedDbFaiss)
    engine = MemoryEngine(
        db_path=str(tmp_path / name),
        faiss_db=faiss_cls(holder),
        config=engine_config,
    )
    holder["engine"] = engine
    return engine, holder


async def _seed_scope_memories(engine: MemoryEngine):
    channel_doc = await engine.add_memory(
        content="频道公共记忆：群里约定的火锅店叫蜀山",
        session_id=CHANNEL,
        persona_id="persona-1",
        importance=0.7,
    )
    user_a_doc = await engine.add_memory(
        content="A 的个人记忆：喜欢火锅",
        session_id="livingmemory:user:qq:user-a",
        persona_id="persona-1",
        importance=0.7,
    )
    private_doc = await engine.add_memory(
        content="A 在私聊里提到火锅",
        session_id="qq:FriendMessage:user-a",
        persona_id="persona-1",
        importance=0.7,
    )
    return channel_doc, user_a_doc, private_doc


@pytest.mark.asyncio
async def test_merged_recall_returns_user_and_channel_memories(tmp_path: Path):
    engine, _ = _make_engine(tmp_path)
    await engine.initialize()
    channel_doc, user_a_doc, private_doc = await _seed_scope_memories(engine)

    user_scope = "livingmemory:user:qq:user-a"
    plain = await engine.search_memories(
        "火锅", k=5, session_id=user_scope, persona_id="persona-1"
    )
    merged = await engine.search_memories(
        "火锅",
        k=5,
        session_id=user_scope,
        persona_id="persona-1",
        extra_scopes=[CHANNEL],
    )
    plain_ids = {item.doc_id for item in plain}
    merged_ids = {item.doc_id for item in merged}
    assert user_a_doc in plain_ids
    assert channel_doc not in plain_ids
    assert private_doc not in plain_ids
    assert {user_a_doc, channel_doc} <= merged_ids
    assert private_doc not in merged_ids
    await engine.close()


@pytest.mark.asyncio
async def test_merged_recall_cache_key_separates_scope_sets(tmp_path: Path):
    engine, _ = _make_engine(tmp_path)
    await engine.initialize()
    channel_doc, _, _ = await _seed_scope_memories(engine)
    user_scope = "livingmemory:user:qq:user-a"

    merged = await engine.search_memories(
        "火锅", k=5, session_id=user_scope, extra_scopes=[CHANNEL]
    )
    assert channel_doc in {item.doc_id for item in merged}
    plain = await engine.search_memories("火锅", k=5, session_id=user_scope)
    assert channel_doc not in {item.doc_id for item in plain}
    merged_again = await engine.search_memories(
        "火锅", k=5, session_id=user_scope, extra_scopes=[CHANNEL]
    )
    assert channel_doc in {item.doc_id for item in merged_again}
    await engine.close()


@pytest.mark.asyncio
async def test_two_users_share_channel_memory(tmp_path: Path):
    engine, _ = _make_engine(tmp_path)
    await engine.initialize()
    channel_doc = await engine.add_memory(
        content="频道公共记忆：周末团建去爬山",
        session_id=CHANNEL,
        persona_id="persona-1",
        importance=0.7,
    )
    for user in ("user-a", "user-b"):
        scope = f"livingmemory:user:qq:{user}"
        results = await engine.search_memories(
            "团建",
            k=5,
            session_id=scope,
            persona_id="persona-1",
            extra_scopes=[CHANNEL],
        )
        assert channel_doc in {item.doc_id for item in results}
    await engine.close()


@pytest.mark.asyncio
async def test_min_slots_keep_one_channel_and_one_user_result(tmp_path: Path):
    engine, _ = _make_engine(tmp_path)
    await engine.initialize()
    channel_doc = await engine.add_memory(
        content="团建爬山",
        session_id=CHANNEL,
        importance=0.5,
    )
    user_docs = [
        await engine.add_memory(
            content=f"团建爬山 个人记录 {index}",
            session_id="livingmemory:user:qq:user-a",
            importance=0.6,
        )
        for index in range(4)
    ]
    results = await engine.search_memories(
        "团建爬山",
        k=2,
        session_id="livingmemory:user:qq:user-a",
        extra_scopes=[CHANNEL],
    )
    ids = {item.doc_id for item in results}
    assert channel_doc in ids
    assert len(ids & set(user_docs)) == 1
    await engine.close()


@pytest.mark.asyncio
async def test_archived_channel_memory_excluded_from_recall(tmp_path: Path):
    engine, _ = _make_engine(tmp_path, _faiss_cls=_FreshMetadataFaiss)
    await engine.initialize()
    channel_doc = await engine.add_memory(
        content="频道归档记忆：旧活动",
        session_id=CHANNEL,
        importance=0.7,
    )
    await engine.archive_memories([channel_doc])
    results = await engine.search_memories(
        "旧活动",
        k=5,
        session_id="livingmemory:user:qq:user-a",
        extra_scopes=[CHANNEL],
    )
    assert channel_doc not in {item.doc_id for item in results}
    await engine.close()


@pytest.mark.asyncio
async def test_agentic_search_supports_extra_scopes(tmp_path: Path):
    engine, _ = _make_engine(tmp_path)
    await engine.initialize()
    channel_doc = await engine.add_memory(
        content="频道公共记忆：周三有分享会",
        session_id=CHANNEL,
        importance=0.7,
    )
    outcome = await engine.search_memories_agentic(
        target="conversation",
        query="分享会",
        k=5,
        session_id="livingmemory:user:qq:user-a",
        extra_scopes=[CHANNEL],
    )
    assert channel_doc in {item.doc_id for item in outcome.results}
    await engine.close()


def test_scope_slot_quota_keeps_channel_and_user_slots():
    engine = MemoryEngine(
        db_path=":memory:",
        faiss_db=Mock(),
        config={
            "channel_min_user_slots": 1,
            "channel_min_channel_slots": 1,
        },
    )

    def result(doc_id: int, score: float, scope: str) -> HybridResult:
        return HybridResult(
            doc_id=doc_id,
            final_score=score,
            rrf_score=0.0,
            bm25_score=None,
            vector_score=None,
            content=f"memory-{doc_id}",
            metadata={"session_id": scope},
        )

    results = [
        result(1, 0.9, "livingmemory:user:qq:user-a"),
        result(2, 0.8, "livingmemory:user:qq:user-a"),
        result(3, 0.7, "livingmemory:user:qq:user-a"),
        result(10, 0.1, CHANNEL),
        result(11, 0.05, CHANNEL),
    ]
    selected = engine._apply_scope_slot_quota(
        results,
        3,
        channel_scopes=[CHANNEL],
    )
    ids = [item.doc_id for item in selected]
    assert len(ids) == 3
    assert 10 in ids
    assert 1 in ids
    assert ids[0] == 1  # 结果仍按最终分数降序


# ---------------- Page API 过滤 ----------------


@pytest.mark.asyncio
async def test_list_memories_page_filters_channel_scope(tmp_path: Path):
    engine, _ = _make_engine(tmp_path)
    await engine.initialize()
    channel_doc = await engine.add_memory(
        content="频道记忆",
        session_id=CHANNEL,
        metadata={"scope_kind": "channel", "channel_id": "group-42"},
    )
    await engine.add_memory(content="个人记忆", session_id="livingmemory:user:qq:a")
    page = await engine.list_memories_page(scope="channel")
    assert {row["id"] for row in page["items"]} == {channel_doc}
    by_channel = await engine.list_memories_page(channel_id="group-42")
    assert {row["id"] for row in by_channel["items"]} == {channel_doc}
    await engine.close()


def test_archived_policy_only_relaxes_channel_scope_when_enabled():
    engine = MemoryEngine(
        db_path=":memory:", faiss_db=Mock(), config={"atom_enabled": False}
    )

    def archived(doc_id: int, scope: str):
        return HybridResult(
            doc_id=doc_id,
            final_score=0.9,
            rrf_score=0.0,
            bm25_score=None,
            vector_score=None,
            content="archived",
            metadata={"session_id": scope, "status": "archived"},
        )

    results = [archived(1, CHANNEL), archived(2, "livingmemory:user:qq:a")]
    assert engine._filter_by_retrieval_policy(results) == []
    kept = engine._filter_by_retrieval_policy(results, archived_scopes={CHANNEL})
    assert [item.doc_id for item in kept] == [1]


@pytest.mark.asyncio
async def test_statistics_expose_channel_scope_count(tmp_path: Path):
    engine, _ = _make_engine(tmp_path)
    await engine.initialize()
    await engine.add_memory(content="频道统计记忆", session_id=CHANNEL)
    stats = await engine.get_statistics()
    assert stats["sessions"].get(CHANNEL) == 1
    await engine.close()
