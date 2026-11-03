"""#385: bounded reflection input, convergent retention and serialized writes."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.managers.consolidation_manager import (
    MemoryConsolidationManager,
)
from tests.event_handler_support import _make_real_handler
from tests.test_reflection_backlog import (
    _seed_messages,
    _configure_processor,
    _build_plan,
    _run_scheduled,
)


@pytest.mark.asyncio
async def test_long_same_day_backlog_is_consumed_in_bounded_chunks(tmp_path):
    handler, manager, store = await _make_real_handler(tmp_path)
    cfg = ConfigManager(
        {
            "reflection_engine": {
                "max_messages_per_segment": 4,
                "max_segments_per_run": 10,
            },
            "filtering_settings": {"use_persona_filtering": False},
        }
    )
    handler._memory_reflection.config_manager = cfg
    sid = "test:group:bounded"
    await _seed_messages(manager, sid, [1_780_000_000.0 + i for i in range(10)])
    _configure_processor(handler)
    handler.memory_engine.find_similar_memory = AsyncMock(return_value=None)
    try:
        plan = await _build_plan(handler, sid, 10)
        assert plan.end_index == 4
        await _run_scheduled(handler, plan)
        assert [
            len(call.kwargs["messages"])
            for call in handler.memory_processor.process_conversation.await_args_list
        ] == [4, 4, 2]
        assert await manager.get_session_metadata(sid, "last_summarized_index", 0) == 10
    finally:
        await handler.shutdown()
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cursor", [0, 10])
async def test_message_cap_converges_even_with_unsummarized_overflow(tmp_path, cursor):
    handler, manager, store = await _make_real_handler(tmp_path)
    sid = "test:group:retention"
    await _seed_messages(manager, sid, [1_780_000_000.0 + i for i in range(160)])
    await manager.update_session_metadata(sid, "last_summarized_index", cursor)
    revision = await manager.get_session_revision(sid)
    try:
        await handler._message_utils.enforce_message_limit(sid)
        assert await store.get_message_count(sid) == 100
        assert await manager.get_session_metadata(sid, "last_summarized_index", -1) == 0
        assert await manager.get_session_revision(sid) == revision
        messages = await manager.get_messages_range(sid, 0, 100)
        assert messages[0].content == "message-60"
    finally:
        await handler.shutdown()
        await store.close()


@pytest.mark.asyncio
async def test_consolidation_waits_for_reflection_writes_but_not_its_llm(tmp_path):
    handler, manager, store = await _make_real_handler(tmp_path)
    engine = handler.memory_engine
    engine.archive_memories = AsyncMock(return_value=2)
    cfg = ConfigManager({"memory_consolidation": {"enabled": True}})
    merger = Mock()
    merger.merge_memories = AsyncMock(
        return_value={
            "summary": "merged",
            "key_facts": [],
            "topics": [],
            "importance": 0.5,
        }
    )
    consolidation = MemoryConsolidationManager(engine, merger, cfg)
    handler._memory_reflection.consolidation_manager = consolidation
    group = [
        {"id": 1, "metadata": {"session_id": "test:group:a"}},
        {"id": 2, "metadata": {"session_id": "test:group:a"}},
    ]
    try:
        async with consolidation.write_lock:
            task = asyncio.create_task(consolidation._consolidate_group(group, {}))
            await asyncio.sleep(0)
            assert merger.merge_memories.await_count == 1
            engine.add_memory.assert_not_awaited()
        await task
        engine.add_memory.assert_awaited_once()
    finally:
        await handler.shutdown()
        await store.close()


@pytest.mark.asyncio
async def test_consolidation_is_deferred_until_reflection_backlog_finishes(tmp_path):
    from types import SimpleNamespace

    handler, manager, store = await _make_real_handler(tmp_path)
    sid = "test:group:deferred"
    await _seed_messages(manager, sid, [1_780_000_000.0 + i for i in range(4)])
    _configure_processor(handler)
    handler.memory_engine.find_similar_memory = AsyncMock(return_value=None)
    consolidation = SimpleNamespace(write_lock=asyncio.Lock(), maybe_run=AsyncMock())
    handler._memory_reflection.consolidation_manager = consolidation
    try:
        plan = await _build_plan(handler, sid, 4)
        consolidation.maybe_run.assert_not_awaited()
        await _run_scheduled(handler, plan)
        await asyncio.gather(*list(handler._storage_tasks))
        consolidation.maybe_run.assert_awaited_once_with("reflection")
        assert await manager.get_session_metadata(sid, "last_summarized_index", 0) == 4
    finally:
        await handler.shutdown()
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cursor, deleted, force, expected", [(4, 4, False, 2), (0, 6, True, 0)]
)
async def test_partial_summary_commits_its_message_anchor_after_head_trim(
    tmp_path, cursor, deleted, force, expected
):
    handler, manager, store = await _make_real_handler(tmp_path)
    sid = "test:group:anchor"
    await _seed_messages(manager, sid, [1_780_000_000.0 + i for i in range(10)])
    await manager.update_session_metadata(sid, "last_summarized_index", cursor)
    snapshot = await manager.get_messages_range(sid, cursor, 6)
    revision = await manager.get_session_revision(sid)
    try:
        await manager.trim_session_messages(sid, deleted, delete_unsummarized=force)
        assert await manager.commit_summary_cursor(sid, snapshot[-1], revision) is True
        assert (
            await manager.get_session_metadata(sid, "last_summarized_index", -1)
            == expected
        )
    finally:
        await handler.shutdown()
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("retries", [1, 3])
async def test_pending_retry_never_skips_later_unsummarized_chunks(tmp_path, retries):
    handler, manager, store = await _make_real_handler(tmp_path)
    handler._memory_reflection.config_manager = ConfigManager(
        {"reflection_engine": {"max_messages_per_segment": 4}}
    )
    sid = "test:group:pending-range"
    await _seed_messages(manager, sid, [1_780_000_000.0 + i for i in range(10)])
    try:
        plan = await handler._memory_reflection._build_reflection_plan_for_cursor(
            sid,
            10,
            0,
            {"start_index": 6, "end_index": 10, "retry_count": retries},
            None,
            None,
            sid,
        )
        if retries == 1:
            assert (plan.start_index, plan.end_index) == (0, 4)
        else:
            assert plan is None
            assert (
                await manager.get_session_metadata(sid, "last_summarized_index", 0) == 4
            )
    finally:
        await handler.shutdown()
        await store.close()


@pytest.mark.asyncio
async def test_snapshot_refreshes_a_cursor_that_moved_before_history_fetch(tmp_path):
    handler, manager, store = await _make_real_handler(tmp_path)
    sid = "test:group:snapshot-shift"
    await _seed_messages(manager, sid, [1_780_000_000.0 + i for i in range(10)])
    await manager.update_session_metadata(sid, "last_summarized_index", 4)
    try:
        await manager.trim_session_messages(sid, 4)
        plan = await handler._memory_reflection._build_reflection_plan_for_cursor(
            sid, 10, 4, None, 0, None, sid
        )
        assert plan.start_index == 0
        assert plan.history_messages[0].content == "message-4"
    finally:
        await handler.shutdown()
        await store.close()
