"""#344 反思积压：同一次后台任务内按切分片段连续消费。"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest

from tests.event_handler_support import _make_real_handler


def _day(day: int, hour: int, minute: int = 0) -> float:
    return datetime(2026, 10, day, hour, minute).timestamp()


async def _seed_messages(manager, session_id: str, timestamps: list[float]) -> None:
    for index, timestamp in enumerate(timestamps):
        await manager.add_message(
            session_id=session_id,
            role="user" if index % 2 == 0 else "assistant",
            content=f"message-{index}",
            sender_id="u1" if index % 2 == 0 else "bot1",
            group_id="group-1",
            timestamp=timestamp,
        )


def _configure_processor(handler, *, side_effect=None):
    processor = handler.memory_processor

    async def _summary(*args, **kwargs):
        return "summary", {"topics": ["t1"]}, 0.6

    processor.process_conversation = AsyncMock(
        side_effect=side_effect or _summary
    )
    processor.classify_atoms_from_metadata = Mock(return_value=[])


async def _build_plan(handler, session_id: str, total_messages: int):
    return await handler._memory_reflection._build_reflection_plan_for_cursor(
        session_id,
        total_messages,
        0,
        None,
        None,
        "persona-1",
        session_id,
    )


async def _run_scheduled(handler, plan) -> None:
    await handler._memory_reflection._schedule_reflection(plan)
    await asyncio.gather(*list(handler._storage_tasks))


@pytest.mark.asyncio
async def test_backlog_consumes_all_day_segments_in_one_run(tmp_path):
    handler, manager, store = await _make_real_handler(tmp_path)
    session_id = "test:group:backlog"
    timestamps = [
        _day(25, 22, 0),
        _day(25, 22, 5),
        _day(25, 22, 10),
        _day(25, 22, 15),
        _day(26, 0, 1),
        _day(26, 0, 5),
        _day(26, 0, 10),
        _day(26, 0, 15),
    ]
    await _seed_messages(manager, session_id, timestamps)
    _configure_processor(handler)
    handler.memory_engine.find_similar_memory = AsyncMock(return_value=None)
    handler.memory_engine.add_memory = AsyncMock(return_value=1)

    try:
        plan = await _build_plan(handler, session_id, len(timestamps))
        assert (plan.start_index, plan.end_index) == (0, 4)

        await _run_scheduled(handler, plan)

        assert handler.memory_processor.process_conversation.await_count == 2
        assert await manager.get_session_metadata(
            session_id, "last_summarized_index", 0
        ) == 8
        assert await manager.get_session_metadata(session_id, "pending_summary", None) is None
        windows = [
            call.kwargs["metadata"]["source_window"]
            for call in handler.memory_engine.add_memory.await_args_list
        ]
        assert [
            (window["start_index"], window["end_index"]) for window in windows
        ] == [(0, 4), (4, 8)]
    finally:
        await handler.shutdown()
        await store.close()


@pytest.mark.asyncio
async def test_backlog_respects_segment_budget(tmp_path):
    handler, manager, store = await _make_real_handler(tmp_path)
    session_id = "test:group:budget"
    await _seed_messages(
        manager,
        session_id,
        [
            _day(25, 22, 0),
            _day(25, 22, 5),
            _day(25, 22, 10),
            _day(25, 22, 15),
            _day(26, 0, 1),
            _day(26, 0, 5),
            _day(26, 0, 10),
            _day(26, 0, 15),
        ],
    )
    handler.config_manager._config["reflection_engine"]["max_segments_per_run"] = 1
    _configure_processor(handler)
    handler.memory_engine.find_similar_memory = AsyncMock(return_value=None)
    handler.memory_engine.add_memory = AsyncMock(return_value=1)

    try:
        plan = await _build_plan(handler, session_id, 8)
        await _run_scheduled(handler, plan)

        assert handler.memory_processor.process_conversation.await_count == 1
        assert await manager.get_session_metadata(
            session_id, "last_summarized_index", 0
        ) == 4
    finally:
        await handler.shutdown()
        await store.close()


@pytest.mark.asyncio
async def test_backlog_stops_when_segment_fails(tmp_path):
    handler, manager, store = await _make_real_handler(tmp_path)
    session_id = "test:group:failure"
    await _seed_messages(
        manager,
        session_id,
        [
            _day(25, 22, 0),
            _day(25, 22, 5),
            _day(25, 22, 10),
            _day(25, 22, 15),
            _day(26, 0, 1),
            _day(26, 0, 5),
            _day(26, 0, 10),
            _day(26, 0, 15),
        ],
    )
    _configure_processor(handler, side_effect=RuntimeError("llm failed"))
    handler.memory_engine.add_memory = AsyncMock(return_value=1)

    try:
        plan = await _build_plan(handler, session_id, 8)
        await _run_scheduled(handler, plan)

        assert handler.memory_processor.process_conversation.await_count == 1
        assert await manager.get_session_metadata(
            session_id, "last_summarized_index", 0
        ) == 0
        assert await manager.get_session_metadata(
            session_id, "pending_summary", None
        ) == {"start_index": 0, "end_index": 4, "retry_count": 1}
        handler.memory_engine.add_memory.assert_not_awaited()
    finally:
        await handler.shutdown()
        await store.close()
