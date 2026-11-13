"""Regression tests: retrieval must not pollute access_count.

检索本身不强化；access_count / last_access_time 只有显式检索、显式深读
（或调用方明确传 track_access=True）才允许更新。被动注入、近期补位与
分页续读都不强化。
"""

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
import pytest_asyncio
from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine
from astrbot_plugin_livingmemory.core.retrieval.hybrid_retriever import HybridResult

from tests.event_handler_support import (
    _make_event,
    _make_real_handler,
    _make_req,
)
from tests.test_agentic_engine_retrieval import _make_engine as _make_agentic_engine
from tests.test_agentic_tools import (
    _config as _agentic_config,
    _context as _agentic_context,
    _event as _agentic_event,
    _tools as _agentic_tools,
)


@dataclass
class _FakeRetrieveResult:
    similarity: float
    data: dict


class _FakeDocumentStorage:
    def __init__(self, db: "_FakeFaissDB"):
        self._db = db

    async def get_documents(self, metadata_filters, ids=None, limit=50, offset=0):
        docs = list(self._db.docs.values())
        if ids is not None:
            id_set = set(ids)
            docs = [d for d in docs if d["id"] in id_set]
        for key, value in (metadata_filters or {}).items():
            docs = [d for d in docs if d["metadata"].get(key) == value]
        return [dict(d) for d in docs[offset : offset + limit]]

    async def count_documents(self, metadata_filters):
        docs = list(self._db.docs.values())
        for key, value in (metadata_filters or {}).items():
            docs = [d for d in docs if d["metadata"].get(key) == value]
        return len(docs)


class _FakeFaissDB:
    def __init__(self):
        self.docs: dict[int, dict] = {}
        self._next_id = 1
        self.document_storage = _FakeDocumentStorage(self)

    async def insert(self, content: str, metadata: dict) -> int:
        doc_id = self._next_id
        self._next_id += 1
        self.docs[doc_id] = {
            "id": doc_id,
            "doc_id": f"uuid-{doc_id}",
            "text": content,
            "metadata": dict(metadata),
        }
        return doc_id

    async def retrieve(self, query, k, fetch_k, rerank, metadata_filters=None):
        results = []
        for doc in self.docs.values():
            text = doc["text"]
            score = 0.9 if query in text else 0.2
            results.append(
                _FakeRetrieveResult(
                    similarity=score,
                    data={
                        "id": doc["id"],
                        "text": text,
                        "metadata": dict(doc["metadata"]),
                    },
                )
            )
        results.sort(key=lambda item: item.similarity, reverse=True)
        return results[:k]

    async def delete(self, uuid_doc_id: str) -> None:
        for doc_id, doc in list(self.docs.items()):
            if doc["doc_id"] == uuid_doc_id:
                self.docs.pop(doc_id, None)
                break

    async def close(self) -> None:
        return None


async def _make_engine(
    tmp_path: Path,
    *,
    cache: bool = False,
    extra_config: dict | None = None,
    name: str = "access_stats.db",
) -> MemoryEngine:
    config = {
        "atom_enabled": False,
        "recent_memory_count": 0,
        "search_cache_enabled": cache,
    }
    if extra_config:
        config.update(extra_config)
    engine = MemoryEngine(
        db_path=str(tmp_path / name),
        faiss_db=_FakeFaissDB(),
        config=config,
    )
    await engine.initialize()
    engine.dual_route_retriever = None
    return engine


@pytest_asyncio.fixture
async def engine_factory(tmp_path: Path):
    """构造本文件使用的引擎，并保证每个实例都被关闭。"""
    created: list[MemoryEngine] = []

    async def make(**kwargs) -> MemoryEngine:
        instance = await _make_engine(tmp_path, **kwargs)
        created.append(instance)
        return instance

    try:
        yield make
    finally:
        for instance in created:
            await instance.close()


@pytest_asyncio.fixture
async def engine(engine_factory):
    return await engine_factory()


async def _insert_document(
    engine: MemoryEngine,
    *,
    doc_id: int = 1,
    content: str = "记忆内容",
    access_count: int = 0,
    last_access_time: float = 1000.0,
    extra_metadata: dict | None = None,
) -> dict:
    metadata = {
        "importance": 0.5,
        "create_time": 900.0,
        "last_access_time": last_access_time,
        "access_count": access_count,
        "status": "active",
        "session_id": "s1",
        "persona_id": "p1",
    }
    if extra_metadata:
        metadata.update(extra_metadata)
    await engine.db_connection.execute(
        """
        INSERT OR REPLACE INTO documents(
            id, doc_id, text, metadata, created_at, updated_at
        ) VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (doc_id, f"uuid-{doc_id}", content, json.dumps(metadata)),
    )
    await engine.db_connection.commit()
    return metadata


def _result_for(doc_id: int, content: str = "记忆内容") -> HybridResult:
    return HybridResult(
        doc_id=doc_id,
        final_score=0.9,
        rrf_score=0.9,
        bm25_score=0.9,
        vector_score=0.9,
        content=content,
        metadata={
            "importance": 0.5,
            "status": "active",
            "session_id": "s1",
            "persona_id": "p1",
        },
        score_breakdown={},
    )


async def _read_metadata(engine: MemoryEngine, doc_id: int) -> dict:
    cursor = await engine.db_connection.execute(
        "SELECT metadata FROM documents WHERE id = ?", (doc_id,)
    )
    row = await cursor.fetchone()
    return json.loads(row["metadata"])


async def _insert_raw_metadata(
    engine: MemoryEngine, doc_id: int, metadata: str | None
) -> None:
    await engine.db_connection.execute(
        """
        INSERT OR REPLACE INTO documents(
            id, doc_id, text, metadata, created_at, updated_at
        ) VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (doc_id, f"uuid-{doc_id}", "记忆内容", metadata),
    )
    await engine.db_connection.commit()


async def _drop_json_expression_indexes(engine: MemoryEngine) -> None:
    """模拟带脏行的历史库：表达式索引会让非法 JSON 无法入库。"""
    cursor = await engine.db_connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'index' AND tbl_name = 'documents' "
        "AND sql LIKE '%json_extract%'"
    )
    for row in await cursor.fetchall():
        await engine.db_connection.execute(f'DROP INDEX IF EXISTS "{row[0]}"')
    await engine.db_connection.commit()


@pytest.mark.asyncio
async def test_access_update_tolerates_null_and_invalid_metadata(engine):
    """NULL / 非法 JSON metadata 的行不能导致整批访问统计回滚。"""
    await _insert_document(engine, doc_id=1)
    # 当前 schema 的表达式索引会拒绝非法 JSON 入库（malformed JSON），
    # 先移除这些索引来构造脏行，模拟历史库。
    await _drop_json_expression_indexes(engine)
    await _insert_raw_metadata(engine, 2, None)
    await _insert_raw_metadata(engine, 3, "{not-json")
    engine.hybrid_retriever = Mock()
    engine.hybrid_retriever.search = AsyncMock(
        return_value=[_result_for(1), _result_for(2), _result_for(3)]
    )

    results = await engine.search_memories(
        "记忆", k=5, session_id="s1", track_access=True
    )
    assert [result.doc_id for result in results] == [1, 2, 3]
    await engine._flush_access_updates()

    normal = await _read_metadata(engine, 1)
    assert normal["access_count"] == 1
    assert normal["last_access_time"] > 1000.0
    # 异常行由 CASE 的 ELSE 分支重建为合法 JSON，不会抛错、也不会回滚正常行。
    for doc_id in (2, 3):
        rewritten = await _read_metadata(engine, doc_id)
        assert rewritten["access_count"] == 1
        assert rewritten["last_access_time"] > 0


@pytest.mark.asyncio
async def test_default_search_does_not_touch_access_stats(engine):
    original = await _insert_document(engine)
    engine.hybrid_retriever = Mock()
    engine.hybrid_retriever.search = AsyncMock(return_value=[_result_for(1)])

    results = await engine.search_memories("记忆", k=5, session_id="s1")

    assert [result.doc_id for result in results] == [1]
    assert engine._access_update_pending == set()
    metadata = await _read_metadata(engine, 1)
    assert metadata["access_count"] == original["access_count"]
    assert metadata["last_access_time"] == original["last_access_time"]


@pytest.mark.asyncio
async def test_cache_hit_with_default_options_does_not_touch_access_stats(
    engine_factory,
):
    engine = await engine_factory(cache=True)
    original = await _insert_document(engine)
    engine.hybrid_retriever = Mock()
    engine.hybrid_retriever.search = AsyncMock(return_value=[_result_for(1)])

    await engine.search_memories("记忆", k=5, session_id="s1")
    # 第二次走缓存；默认参数下缓存命中同样不得写访问统计。
    await engine.search_memories("记忆", k=5, session_id="s1")

    assert engine.hybrid_retriever.search.await_count == 1
    assert engine._access_update_pending == set()
    metadata = await _read_metadata(engine, 1)
    assert metadata["access_count"] == original["access_count"]
    assert metadata["last_access_time"] == original["last_access_time"]


@pytest.mark.asyncio
async def test_track_access_true_still_strengthens_fresh_and_cached(engine_factory):
    engine = await engine_factory(cache=True)
    await _insert_document(engine)
    engine.hybrid_retriever = Mock()
    engine.hybrid_retriever.search = AsyncMock(return_value=[_result_for(1)])

    # Fresh search with explicit tracking.
    await engine.search_memories("记忆", k=5, session_id="s1", track_access=True)
    await engine._flush_access_updates()
    metadata = await _read_metadata(engine, 1)
    assert metadata["access_count"] == 1
    assert metadata["last_access_time"] > 1000.0

    # Same query now hits the search cache; tracking must still work there.
    await engine.search_memories("记忆", k=5, session_id="s1", track_access=True)
    await engine._flush_access_updates()
    metadata = await _read_metadata(engine, 1)
    assert metadata["access_count"] == 2


@pytest.mark.asyncio
async def test_recent_fillers_are_not_strengthened_on_fresh_or_cached_search(
    engine_factory,
):
    engine = await engine_factory(
        cache=True,
        extra_config={"recent_memory_count": 2},
    )
    await _insert_document(engine, doc_id=1, content="相关性命中记忆")
    await _insert_document(
        engine,
        doc_id=2,
        content="近期补位记忆",
        extra_metadata={"create_time": time.time()},
    )
    engine.hybrid_retriever = Mock()
    engine.hybrid_retriever.search = AsyncMock(return_value=[_result_for(1)])

    results = await engine.search_memories(
        "相关性", k=5, session_id="s1", track_access=True
    )
    assert [result.doc_id for result in results] == [1, 2]
    await engine._flush_access_updates()

    relevant = await _read_metadata(engine, 1)
    filler = await _read_metadata(engine, 2)
    assert relevant["access_count"] == 1
    assert filler["access_count"] == 0
    assert filler["last_access_time"] == 1000.0

    # 缓存路径同样只强化相关性结果，补位记忆保持未使用。
    cached_results = await engine.search_memories(
        "相关性", k=5, session_id="s1", track_access=True
    )
    assert engine.hybrid_retriever.search.await_count == 1
    assert [result.doc_id for result in cached_results] == [1, 2]
    await engine._flush_access_updates()

    relevant = await _read_metadata(engine, 1)
    filler = await _read_metadata(engine, 2)
    assert relevant["access_count"] == 2
    assert filler["access_count"] == 0
    assert filler["last_access_time"] == 1000.0


@pytest.mark.asyncio
async def test_agentic_conversation_access_tracking_is_opt_in(engine):
    original = await _insert_document(engine)
    engine.dual_route_retriever = None
    engine.hybrid_retriever = Mock()
    engine.hybrid_retriever.search = AsyncMock(return_value=[_result_for(1)])

    outcome = await engine.search_memories_agentic(query="记忆", k=5, session_id="s1")
    assert [result.doc_id for result in outcome.results] == [1]
    await asyncio.sleep(0)  # let any fire-and-forget tracking task run
    assert engine._access_update_pending == set()
    metadata = await _read_metadata(engine, 1)
    assert metadata["access_count"] == original["access_count"]
    assert metadata["last_access_time"] == original["last_access_time"]

    outcome = await engine.search_memories_agentic(
        query="记忆", k=5, session_id="s1", track_access=True
    )
    assert [result.doc_id for result in outcome.results] == [1]
    await asyncio.sleep(0)  # let the fire-and-forget tracking task queue its write
    await engine._flush_access_updates()
    metadata = await _read_metadata(engine, 1)
    assert metadata["access_count"] == 1
    assert metadata["last_access_time"] > 1000.0


@pytest.mark.asyncio
async def test_agentic_participant_access_tracking_is_opt_in(engine):
    original = await _insert_document(
        engine,
        extra_metadata={
            "participant_identities": [{"identity_key": "speaker-1"}],
            "participants": ["Tester"],
        },
    )

    outcome = await engine.search_memories_agentic(
        target="current_speaker",
        query="",
        k=5,
        session_id="s1",
        identity_key="speaker-1",
    )
    assert [result.doc_id for result in outcome.results] == [1]
    await asyncio.sleep(0)  # let any fire-and-forget tracking task run
    assert engine._access_update_pending == set()
    metadata = await _read_metadata(engine, 1)
    assert metadata["access_count"] == original["access_count"]
    assert metadata["last_access_time"] == original["last_access_time"]

    outcome = await engine.search_memories_agentic(
        target="current_speaker",
        query="",
        k=5,
        session_id="s1",
        identity_key="speaker-1",
        track_access=True,
    )
    assert [result.doc_id for result in outcome.results] == [1]
    await asyncio.sleep(0)  # let the fire-and-forget tracking task queue its write
    await engine._flush_access_updates()
    metadata = await _read_metadata(engine, 1)
    assert metadata["access_count"] == 1
    assert metadata["last_access_time"] > 1000.0


@pytest.mark.asyncio
async def test_beta_search_tool_strengthens_returned_memories(tmp_path: Path):
    """Beta 显式检索与基线工具语义一致：返回的记忆计入一次使用。"""
    engine = await _make_agentic_engine(tmp_path, name="beta-search.db")
    try:
        memory_id = await engine.add_memory(
            content="Beta 显式检索计数关键词", session_id="test:private:s1"
        )
        search, _ = _agentic_tools(engine)
        out = json.loads(
            await search.call(_agentic_context(_agentic_event()), query="计数关键词")
        )
        assert [item["id"] for item in out["results"]] == [memory_id]
        await asyncio.sleep(0)
        await engine._flush_access_updates()
        metadata = await _read_metadata(engine, memory_id)
        assert metadata["access_count"] == 1
        assert metadata["last_access_time"] > 1000.0
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_beta_include_source_counts_each_document_once(tmp_path: Path):
    """同一次工具调用内，include_source 深读不得对同一 doc 二次计数。"""
    engine = await _make_agentic_engine(tmp_path, name="beta-source.db")
    try:
        memory_id = await engine.add_memory(
            content="Beta 原文读取计数关键词",
            session_id="test:private:s1",
            source_messages=[
                {
                    "role": "user",
                    "sender_id": "1001",
                    "timestamp": 1.0,
                    "content": "原文内容",
                }
            ],
        )
        search, read = _agentic_tools(engine, config=_agentic_config())
        event = _agentic_event()
        out = json.loads(
            await search.call(
                _agentic_context(event),
                query="计数关键词",
                include_source=True,
            )
        )
        assert [item["id"] for item in out["results"]] == [memory_id]
        await asyncio.sleep(0)
        await engine._flush_access_updates()
        metadata = await _read_metadata(engine, memory_id)
        assert metadata["access_count"] == 1

        # 搜索结果给出的 s:0:0 属于续读，不得再次强化。
        continued = json.loads(
            await read.call(
                _agentic_context(event),
                memory_id=memory_id,
                include_source=True,
                cursor="s:0:0",
            )
        )
        assert continued["status"] == "success"
        await asyncio.sleep(0)
        await engine._flush_access_updates()
        metadata = await _read_metadata(engine, memory_id)
        assert metadata["access_count"] == 1
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_include_source_and_pagination_within_flush_window_counts_once(
    tmp_path: Path,
):
    """同一次工具调用内：检索 + 原文 + 两次续读只计一次，不随时序变化。"""
    engine = await _make_agentic_engine(tmp_path, name="include-source-pages.db")
    try:
        memory_id = await engine.add_memory(
            content="分页续读计数关键词",
            session_id="test:private:s1",
            source_messages=[
                {
                    "role": "user",
                    "sender_id": "1001",
                    "timestamp": float(index),
                    "content": f"原文第{index}段" + "字" * 1000,
                }
                for index in range(4)
            ],
        )
        config = _agentic_config(
            agent_tools={
                "enable_recall_tool": True,
                "enable_agentic_recall_beta": True,
                "agentic_recall_max_calls": 4,
                "agentic_recall_time_budget_seconds": 20,
                "agentic_recall_max_result_chars": 1500,
            }
        )
        search, read = _agentic_tools(engine, config=config)
        event = _agentic_event()
        context = _agentic_context(event)

        out = json.loads(
            await search.call(context, query="计数关键词", include_source=True)
        )
        assert [item["id"] for item in out["results"]] == [memory_id]
        assert out["results"][0]["next_cursor"]  # 原文仍有后续页
        await asyncio.sleep(0)  # 让检索的强化任务入队
        await engine._flush_access_updates()
        metadata = await _read_metadata(engine, memory_id)
        assert metadata["access_count"] == 1

        # 连续续读两页都带非空 cursor。每步立即 flush，显式模拟 3 秒合并
        # 窗口在调用间到期，避免集合去重掩盖“续读也强化”的错误；流程不等待
        # 真实定时器，避免测试变成时间敏感的 flaky。
        cursor = "s:0:0"
        for _ in range(2):
            page = json.loads(
                await read.call(
                    context,
                    memory_id=memory_id,
                    include_source=True,
                    cursor=cursor,
                )
            )
            assert page["status"] == "success"
            assert page["next_cursor"]  # 后续仍有内容，下一次仍是续读
            await asyncio.sleep(0)
            await engine._flush_access_updates()
            metadata = await _read_metadata(engine, memory_id)
            assert metadata["access_count"] == 1
            cursor = page["next_cursor"]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_deep_read_strengthens_first_page_only(tmp_path: Path):
    """真实 MemoryReadTool：首屏成功 +1，cursor 续读与 not_found 不强化。"""
    engine = await _make_agentic_engine(tmp_path, name="deep-read.db")
    try:
        long_content = "事实" + "字" * 1500
        memory_id = await engine.add_memory(
            content=long_content, session_id="test:private:s1"
        )
        config = _agentic_config()
        _, read = _agentic_tools(engine, config=config)
        event = _agentic_event()

        from astrbot_plugin_livingmemory.core.tools.agentic_recall_budget import (
            get_or_create_budget,
        )

        budget = get_or_create_budget(event, config)
        budget.max_result_chars = 1500

        first = json.loads(
            await read.call(_agentic_context(event), memory_id=memory_id)
        )
        assert first["status"] == "success"
        assert first["next_cursor"].startswith("f:")
        await engine._flush_access_updates()
        metadata = await _read_metadata(engine, memory_id)
        assert metadata["access_count"] == 1

        second = json.loads(
            await read.call(
                _agentic_context(event),
                memory_id=memory_id,
                cursor=first["next_cursor"],
            )
        )
        assert second["status"] == "success"
        await engine._flush_access_updates()
        metadata = await _read_metadata(engine, memory_id)
        assert metadata["access_count"] == 1

        missing = json.loads(
            await read.call(_agentic_context(event), memory_id=99999)
        )
        assert missing["status"] == "not_found"
        assert engine._access_update_pending == set()
        metadata = await _read_metadata(engine, memory_id)
        assert metadata["access_count"] == 1
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_auto_injection_with_real_engine_does_not_touch_access_stats(
    tmp_path: Path,
):
    """真实引擎 + 自动注入：检索到记忆也不产生待写访问统计。"""
    engine = await _make_agentic_engine(tmp_path, name="auto-injection.db")
    try:
        memory_id = await engine.add_memory(
            content="hello 命中的记忆，自动注入不得强化",
            session_id="test:private:sid-1",
        )
        before = await _read_metadata(engine, memory_id)
        handler, _manager, store = await _make_real_handler(
            tmp_path, memory_engine=engine
        )
        try:
            req = _make_req("hello")
            with patch(
                "astrbot_plugin_livingmemory.core.utils.get_persona_id",
                new_callable=AsyncMock,
            ) as get_persona:
                get_persona.return_value = "persona-live"
                await handler.handle_memory_recall(_make_event(), req)

            # 先确认这次注入真的检索到了记忆，避免测试空转。
            injected = " ".join(
                getattr(part, "text", "") for part in req.extra_user_content_parts
            )
            assert "自动注入不得强化" in injected

            await asyncio.sleep(0)
            assert engine._access_update_pending == set()
            after = await _read_metadata(engine, memory_id)
            assert after.get("access_count", 0) == 0
            assert after["last_access_time"] == before["last_access_time"]
        finally:
            await store.close()
    finally:
        await engine.close()
