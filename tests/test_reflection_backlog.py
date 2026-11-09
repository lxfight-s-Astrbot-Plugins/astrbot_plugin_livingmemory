"""#344 反思积压：同一次后台任务内按切分片段连续消费。"""

import asyncio
import time
from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest
from astrbot_plugin_livingmemory.core.base.exceptions import (
    LowQualityMemoryError,
    MemoryParseError,
)

from tests.event_handler_support import (
    _make_event,
    _make_real_handler,
    _make_req,
    _make_resp,
)


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

    processor.process_conversation = AsyncMock(side_effect=side_effect or _summary)
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
        assert (
            await manager.get_session_metadata(session_id, "last_summarized_index", 0)
            == 8
        )
        assert (
            await manager.get_session_metadata(session_id, "pending_summary", None)
            is None
        )
        windows = [
            call.kwargs["metadata"]["source_window"]
            for call in handler.memory_engine.add_memory.await_args_list
        ]
        assert [(window["start_index"], window["end_index"]) for window in windows] == [
            (0, 4),
            (4, 8),
        ]
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
        assert (
            await manager.get_session_metadata(session_id, "last_summarized_index", 0)
            == 4
        )
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
        assert (
            await manager.get_session_metadata(session_id, "last_summarized_index", 0)
            == 0
        )
        assert await manager.get_session_metadata(
            session_id, "pending_summary", None
        ) == {"start_index": 0, "end_index": 4, "retry_count": 1}
        handler.memory_engine.add_memory.assert_not_awaited()
    finally:
        await handler.shutdown()
        await store.close()


@pytest.mark.parametrize(
    "error",
    [
        MemoryParseError("LLM 响应无法解析为记忆 JSON"),
        LowQualityMemoryError("总结质量不达标"),
    ],
    ids=["parse-failure", "low-quality"],
)
@pytest.mark.asyncio
async def test_rejected_summary_keeps_pending_retry_without_storing(tmp_path, error):
    """解析失败/低质量总结不落库，按现有 pending retry 机制保留窗口。"""
    handler, manager, store = await _make_real_handler(tmp_path)
    session_id = "test:group:rejected"
    await _seed_messages(
        manager,
        session_id,
        [
            _day(25, 22, 0),
            _day(25, 22, 5),
            _day(25, 22, 10),
            _day(25, 22, 15),
        ],
    )
    _configure_processor(handler, side_effect=error)
    handler.memory_engine.add_memory = AsyncMock(return_value=1)

    try:
        plan = await _build_plan(handler, session_id, 4)
        await _run_scheduled(handler, plan)

        handler.memory_engine.add_memory.assert_not_awaited()
        assert handler.memory_processor.process_conversation.await_count == 1
        assert await manager.get_session_metadata(
            session_id, "pending_summary", None
        ) == {"start_index": 0, "end_index": 4, "retry_count": 1}
        assert (
            await manager.get_session_metadata(session_id, "last_summarized_index", 0)
            == 0
        )
    finally:
        await handler.shutdown()
        await store.close()


async def _seed_old_round(manager, session_id: str, gap_minutes: float) -> None:
    """写入上一轮对话（时间戳在 gap_minutes 分钟前）。"""
    old = time.time() - gap_minutes * 60
    await manager.add_message(
        session_id=session_id,
        role="user",
        content="很久以前的提问",
        sender_id="u1",
        timestamp=old,
    )
    await manager.add_message(
        session_id=session_id,
        role="assistant",
        content="很久以前的回答",
        sender_id="bot1",
        timestamp=old + 1,
    )


@pytest.mark.asyncio
async def test_idle_gap_triggers_catchup_summary_below_threshold(tmp_path):
    """空闲 100 分钟后，下一轮对话即使未满 10 轮也立即补一次总结。"""
    handler, manager, store = await _make_real_handler(tmp_path)
    handler.config_manager._config["reflection_engine"]["summary_trigger_rounds"] = 10
    session_id = "test:private:sid-1"
    await _seed_old_round(manager, session_id, gap_minutes=100)
    _configure_processor(handler)
    handler.memory_engine.find_similar_memory = AsyncMock(return_value=None)
    handler.memory_engine.add_memory = AsyncMock(return_value=1)
    event = _make_event()

    try:
        await handler.handle_memory_recall(event, _make_req("现在的新问题"))
        prepared = await handler._memory_reflection._reflection_precheck(
            event, _make_resp("现在的新回答")
        )
        assert prepared is not None
        assert (
            await manager.get_session_metadata(
                session_id, "idle_summary_pending", False
            )
            is True
        )

        await handler.handle_memory_reflection(event, _make_resp("现在的新回答"))
        await asyncio.gather(*list(handler._storage_tasks))

        assert handler.memory_processor.process_conversation.await_count == 1
        assert handler.memory_engine.add_memory.await_count == 1
        assert (
            await manager.get_session_metadata(
                session_id, "idle_summary_pending", False
            )
            is False
        )
    finally:
        await handler.shutdown()
        await store.close()


@pytest.mark.asyncio
async def test_no_idle_gap_keeps_normal_trigger_threshold(tmp_path):
    """间隔未达空闲阈值时不补总结，仍按 summary_trigger_rounds 触发。"""
    handler, manager, store = await _make_real_handler(tmp_path)
    handler.config_manager._config["reflection_engine"]["summary_trigger_rounds"] = 10
    session_id = "test:private:sid-1"
    await _seed_old_round(manager, session_id, gap_minutes=0.5)
    _configure_processor(handler)
    handler.memory_engine.find_similar_memory = AsyncMock(return_value=None)
    handler.memory_engine.add_memory = AsyncMock(return_value=1)
    event = _make_event()

    try:
        await handler.handle_memory_recall(event, _make_req("现在的新问题"))
        await handler.handle_memory_reflection(event, _make_resp("现在的新回答"))
        await asyncio.gather(*list(handler._storage_tasks))

        assert handler.memory_processor.process_conversation.await_count == 0
        assert handler.memory_engine.add_memory.await_count == 0
        assert (
            await manager.get_session_metadata(
                session_id, "idle_summary_pending", False
            )
            is False
        )
    finally:
        await handler.shutdown()
        await store.close()


# ==================== R3-b 全局反思并发上限 ====================


@pytest.mark.asyncio
async def test_reflection_concurrency_capped_at_two(tmp_path):
    """3 个反思任务并发时最多 2 个同时进入受保护的总结阶段。"""
    from astrbot_plugin_livingmemory.core.event_handler_modules.memory_reflection import (
        _ReflectionPlan,
    )

    handler, _manager, store = await _make_real_handler(tmp_path)
    reflection = handler._memory_reflection

    started = 0
    peak = 0
    two_entered = asyncio.Event()
    release = asyncio.Event()

    async def _probe(plan):
        nonlocal started, peak
        started += 1
        peak = max(peak, started)
        if started >= 2:
            two_entered.set()
        try:
            await release.wait()
        finally:
            started -= 1

    reflection._consume_reflection_segments = _probe
    plans = [
        _ReflectionPlan(
            session_id=f"test:group:concurrency-{index}",
            history_messages=[],
            persona_id=None,
            start_index=0,
            end_index=2,
            retry_count=0,
            memory_scope=f"test:group:concurrency-{index}",
            expected_revision=None,
        )
        for index in range(3)
    ]
    try:
        for plan in plans:
            await reflection._schedule_reflection(plan)

        await asyncio.wait_for(two_entered.wait(), timeout=2)
        for _ in range(10):
            await asyncio.sleep(0)
        # 第 3 个任务必须阻塞在信号量上，不能进入受保护段
        assert started == 2
        assert peak == 2

        release.set()
        await asyncio.gather(*list(handler._storage_tasks))
        assert peak == 2
    finally:
        release.set()
        await handler.shutdown()
        await store.close()


@pytest.mark.asyncio
async def test_reflection_slot_released_after_task_failure(tmp_path):
    """任务异常退出时信号量额度必须释放，后续反思仍可执行。"""
    from astrbot_plugin_livingmemory.core.event_handler_modules.memory_reflection import (
        _ReflectionPlan,
    )

    handler, _manager, store = await _make_real_handler(tmp_path)
    reflection = handler._memory_reflection
    failed = asyncio.Event()

    async def _failing_probe(plan):
        failed.set()
        raise RuntimeError("summary failed")

    reflection._consume_reflection_segments = _failing_probe
    plan = _ReflectionPlan(
        session_id="test:group:failure-release",
        history_messages=[],
        persona_id=None,
        start_index=0,
        end_index=2,
        retry_count=0,
        memory_scope="test:group:failure-release",
        expected_revision=None,
    )
    try:
        await reflection._schedule_reflection(plan)
        await asyncio.wait_for(failed.wait(), timeout=2)
        await asyncio.gather(*list(handler._storage_tasks), return_exceptions=True)

        entered = asyncio.Event()

        async def _probe(_plan):
            entered.set()

        reflection._consume_reflection_segments = _probe
        await reflection._schedule_reflection(
            _ReflectionPlan(
                session_id="test:group:after-failure",
                history_messages=[],
                persona_id=None,
                start_index=0,
                end_index=2,
                retry_count=0,
                memory_scope="test:group:after-failure",
                expected_revision=None,
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=2)
        await asyncio.gather(*list(handler._storage_tasks))
    finally:
        await handler.shutdown()
        await store.close()
