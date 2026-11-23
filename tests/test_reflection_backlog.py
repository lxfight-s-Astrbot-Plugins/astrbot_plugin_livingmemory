"""反思积压消费、重试与并发上限的黑盒测试。

全部通过 ``handle_memory_reflection`` 触发后台总结，LLM Provider 是唯一桩
（外部服务）。断言对象是记忆库里的条数/窗口、会话游标与 Provider 观察到的
并发峰值。
"""

import asyncio
import time
from datetime import datetime
from types import SimpleNamespace

import pytest
import pytest_asyncio

from astrbot.core.message.components import Plain
from astrbot_plugin_livingmemory.core.base.exceptions import (
    LowQualityMemoryError,
    MemoryParseError,
)

from tests.real_engine_support import (
    HostContext,
    SummaryProvider,
    close_handler,
    make_handler,
)

GROUP_SESSION = "test:group:backlog"
PRIVATE_SESSION = "test:private:sid-1"


def _day(day: int, hour: int, minute: int = 0) -> float:
    return datetime(2026, 10, day, hour, minute).timestamp()


class _Event:
    def __init__(self, *, umo: str = GROUP_SESSION, text: str = "hello"):
        self.unified_msg_origin = umo
        self.message_str = text
        self.sender_id = "user-1"
        self.sender_name = "Tester"

    def get_message_type(self):
        from astrbot.api.platform import MessageType

        return (
            MessageType.GROUP_MESSAGE
            if self.unified_msg_origin.startswith("test:group")
            else MessageType.FRIEND_MESSAGE
        )

    def get_sender_id(self):
        return self.sender_id

    def get_self_id(self):
        return "bot-1"

    def get_sender_name(self):
        return self.sender_name

    def get_message_str(self):
        return self.message_str

    def get_messages(self):
        return [Plain(self.message_str)] if self.message_str else []

    def get_platform_name(self):
        return "test"

    def get_platform_id(self):
        return "test-instance"

    def get_group_id(self):
        return "backlog" if self.unified_msg_origin.startswith("test:group") else ""

    def get_extra(self, key, default=None):
        return default


def _req(prompt: str = "hello"):
    return SimpleNamespace(
        prompt=prompt,
        system_prompt="",
        contexts=[],
        extra_user_content_parts=[],
    )


def _resp(text: str = "assistant reply"):
    return SimpleNamespace(
        role="assistant",
        completion_text=text,
        is_chunk=False,
        tools_call_name=None,
        tools_call_extra_content=None,
    )


async def _seed(manager, session_id: str, timestamps: list[float]) -> None:
    for index, timestamp in enumerate(timestamps):
        await manager.add_message(
            session_id=session_id,
            role="user" if index % 2 == 0 else "assistant",
            content=f"message-{index}",
            sender_id="user-1" if index % 2 == 0 else "bot-1",
            group_id="group-1",
            timestamp=timestamp,
        )


async def _settle(handler, *, timeout: float = 3.0) -> None:
    """Wait for scheduled reflection tasks to finish before shutting down."""
    deadline = time.time() + timeout
    while handler._storage_tasks and time.time() < deadline:
        await asyncio.sleep(0.02)


async def _windows(engine) -> list[tuple[int, int]]:
    page = await engine.list_memories_page(page_size=50)
    windows = []
    for item in page["items"]:
        metadata = item["metadata"]
        if isinstance(metadata, str):
            import json

            metadata = json.loads(metadata)
        window = metadata.get("source_window") or {}
        windows.append((window.get("start_index"), window.get("end_index")))
    return sorted(windows)


@pytest_asyncio.fixture
async def backlog_bundle(tmp_path):
    items = await make_handler(
        tmp_path,
        config={
            "reflection_engine": {
                "summary_trigger_rounds": 1,
                "max_segments_per_run": 10,
                "window_split_gap_minutes": 180,
            },
            # 关闭近重复合并：否则分段摘要会被合并成一条记忆，
            # 无法观察“一次运行内消费多段”的能力
            "memory_dedup": {"enabled": False},
        },
        # 每段摘要不同，否则近重复检测会把第二段并入第一条记忆
        llm_provider=SummaryProvider(vary=True),
        host=HostContext(persona_id=None),
    )
    try:
        yield items
    finally:
        await close_handler(*items)


@pytest.mark.asyncio
async def test_day_segments_are_consumed_in_one_run(backlog_bundle):
    """跨天的积压会在同一次触发内逐段总结，而不是只处理最老的一段。"""
    handler, manager, store, engine, db = backlog_bundle
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
    await _seed(manager, GROUP_SESSION, timestamps)

    await handler.handle_memory_reflection(_Event(), _resp())
    await _settle(handler)
    await handler.shutdown()

    # 两天的对话各生成一条记忆，窗口边界落在日期切换处
    assert await _windows(engine) == [(0, 4), (4, 8)]


@pytest.mark.asyncio
async def test_segment_budget_limits_one_run(tmp_path):
    """单次运行最多消费 ``max_segments_per_run`` 段，剩余留给下次。"""
    handler, manager, store, engine, db = await make_handler(
        tmp_path,
        config={
            "reflection_engine": {
                "summary_trigger_rounds": 1,
                "max_segments_per_run": 1,
            },
            "memory_dedup": {"enabled": False},
        },
        host=HostContext(),
    )
    try:
        await _seed(
            manager,
            GROUP_SESSION,
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

        await handler.handle_memory_reflection(_Event(), _resp())
        await _settle(handler)
        await handler.shutdown()

        assert len(await _windows(engine)) == 1
        # 未消费的片段仍保留未总结状态
        cursor = await manager.get_session_metadata(
            GROUP_SESSION, "last_summarized_index", 0
        )
        assert cursor == 4
    finally:
        await close_handler(handler, manager, store, engine, db)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        MemoryParseError("LLM 响应无法解析为记忆 JSON"),
        LowQualityMemoryError("总结质量不达标"),
        RuntimeError("llm failed"),
    ],
    ids=["parse-failure", "low-quality", "provider-failure"],
)
async def test_failed_summary_is_retried_instead_of_stored(tmp_path, error):
    """总结失败时不落库，游标不动，并记录待重试范围。"""
    provider = SummaryProvider(error=error)
    handler, manager, store, engine, db = await make_handler(
        tmp_path,
        config={"reflection_engine": {"summary_trigger_rounds": 1}},
        llm_provider=provider,
        host=HostContext(),
    )
    try:
        await _seed(
            manager,
            GROUP_SESSION,
            [
                _day(25, 22, 0),
                _day(25, 22, 5),
                _day(25, 22, 10),
                _day(25, 22, 15),
            ],
        )

        await handler.handle_memory_reflection(_Event(), _resp())
        await _settle(handler)
        await handler.shutdown()

        assert (await engine.get_statistics())["total_memories"] == 0
        assert (
            await manager.get_session_metadata(
                GROUP_SESSION, "last_summarized_index", 0
            )
            == 0
        )
        pending = await manager.get_session_metadata(
            GROUP_SESSION, "pending_summary", None
        )
        assert pending == {"start_index": 0, "end_index": 4, "retry_count": 1}
    finally:
        await close_handler(handler, manager, store, engine, db)


@pytest.mark.asyncio
async def test_failed_summary_retries_and_skips_only_the_original_window(tmp_path):
    provider = SummaryProvider(error=LowQualityMemoryError("summary rejected"))
    handler, manager, store, engine, db = await make_handler(
        tmp_path, llm_provider=provider, host=HostContext()
    )
    try:
        await _seed(
            manager, GROUP_SESSION, [_day(25, 20, minute) for minute in range(4)]
        )
        await handler.handle_memory_reflection(_Event(), _resp())
        await _settle(handler)

        await _seed(
            manager, GROUP_SESSION, [_day(25, 20, minute) for minute in range(4, 8)]
        )
        for retry_count in (2, 3):
            await handler.handle_memory_reflection(_Event(), _resp())
            await _settle(handler)
            assert await manager.get_session_metadata(
                GROUP_SESSION, "pending_summary", None
            ) == {"start_index": 0, "end_index": 4, "retry_count": retry_count}

        await handler.handle_memory_reflection(_Event(), _resp())
        await _settle(handler)

        assert provider.calls == 3
        assert (
            await manager.get_session_metadata(
                GROUP_SESSION, "last_summarized_index", 0
            )
            == 4
        )
        assert (
            await manager.get_session_metadata(GROUP_SESSION, "pending_summary", None)
            is None
        )
    finally:
        await close_handler(handler, manager, store, engine, db)


@pytest.mark.asyncio
async def test_failed_summary_records_retained_window_after_head_trim(tmp_path):
    gate = asyncio.Event()
    provider = SummaryProvider(
        gate=gate, error=LowQualityMemoryError("summary rejected")
    )
    handler, manager, store, engine, db = await make_handler(
        tmp_path, llm_provider=provider, host=HostContext()
    )
    try:
        await _seed(
            manager, GROUP_SESSION, [_day(25, 20, minute) for minute in range(4)]
        )
        await handler.handle_memory_reflection(_Event(), _resp())
        async with asyncio.timeout(3):
            while provider.calls == 0:
                await asyncio.sleep(0.01)

        await manager.trim_session_messages(GROUP_SESSION, 2, delete_unsummarized=True)
        gate.set()
        await _settle(handler)

        assert await manager.get_session_metadata(
            GROUP_SESSION, "pending_summary", None
        ) == {"start_index": 0, "end_index": 2, "retry_count": 1}
    finally:
        await close_handler(handler, manager, store, engine, db)


async def _seed_old_round(manager, session_id: str, gap_minutes: float) -> None:
    old = time.time() - gap_minutes * 60
    await manager.add_message(
        session_id=session_id,
        role="user",
        content="很久以前的提问",
        sender_id="user-1",
        timestamp=old,
    )
    await manager.add_message(
        session_id=session_id,
        role="assistant",
        content="很久以前的回答",
        sender_id="bot-1",
        timestamp=old + 1,
    )


@pytest.mark.asyncio
async def test_long_idle_gap_triggers_a_catchup_summary(tmp_path):
    """空闲 100 分钟后，下一轮即使未达 10 轮也补一次总结。"""
    handler, manager, store, engine, db = await make_handler(
        tmp_path,
        config={"reflection_engine": {"summary_trigger_rounds": 10}},
        host=HostContext(),
    )
    try:
        await _seed_old_round(manager, PRIVATE_SESSION, gap_minutes=100)
        event = _Event(umo=PRIVATE_SESSION, text="现在的新问题")

        await handler.handle_memory_recall(event, _req("现在的新问题"))
        await handler.handle_memory_reflection(event, _resp("现在的新回答"))
        await _settle(handler)
        await handler.shutdown()

        assert (await engine.get_statistics())["total_memories"] == 1
        # 补总结标记已清除，恢复正常阈值
        assert (
            await manager.get_session_metadata(
                PRIVATE_SESSION, "idle_summary_pending", False
            )
            is False
        )
    finally:
        await close_handler(handler, manager, store, engine, db)


@pytest.mark.asyncio
async def test_short_gap_keeps_the_normal_threshold(tmp_path):
    """间隔未达空闲阈值时按 ``summary_trigger_rounds`` 判断，不补总结。"""
    handler, manager, store, engine, db = await make_handler(
        tmp_path,
        config={"reflection_engine": {"summary_trigger_rounds": 10}},
        host=HostContext(),
    )
    try:
        await _seed_old_round(manager, PRIVATE_SESSION, gap_minutes=0.5)
        event = _Event(umo=PRIVATE_SESSION, text="现在的新问题")

        await handler.handle_memory_recall(event, _req("现在的新问题"))
        await handler.handle_memory_reflection(event, _resp("现在的新回答"))
        await _settle(handler)
        await handler.shutdown()

        assert (await engine.get_statistics())["total_memories"] == 0
    finally:
        await close_handler(handler, manager, store, engine, db)


@pytest.mark.asyncio
async def test_idle_summary_minutes_zero_disables_catchup_and_clears_stale_flag(
    tmp_path,
):
    """idle_summary_minutes=0 时不触发空闲补总结，并清理残留的 idle_summary_pending（#480/#488）。"""
    handler, manager, store, engine, db = await make_handler(
        tmp_path,
        config={
            "reflection_engine": {
                "summary_trigger_rounds": 10,
                "idle_summary_minutes": 0,
            }
        },
        host=HostContext(),
    )
    try:
        await _seed_old_round(manager, PRIVATE_SESSION, gap_minutes=300)
        await manager.update_session_metadata(
            PRIVATE_SESSION, "idle_summary_pending", True
        )
        event = _Event(umo=PRIVATE_SESSION, text="关闭空闲补总结后的新问题")

        await handler.handle_memory_recall(event, _req("关闭空闲补总结后的新问题"))
        await handler.handle_memory_reflection(event, _resp("新回答"))
        await _settle(handler)
        await handler.shutdown()

        assert (await engine.get_statistics())["total_memories"] == 0
        assert (
            await manager.get_session_metadata(
                PRIVATE_SESSION, "idle_summary_pending", False
            )
            is False
        )
    finally:
        await close_handler(handler, manager, store, engine, db)


@pytest.mark.asyncio
async def test_idle_summary_minutes_custom_threshold_is_respected(tmp_path):
    """自定义 idle_summary_minutes=120：100 分钟不触发，150 分钟触发（#480）。"""
    handler, manager, store, engine, db = await make_handler(
        tmp_path,
        config={
            "reflection_engine": {
                "summary_trigger_rounds": 10,
                "idle_summary_minutes": 120,
            }
        },
        host=HostContext(),
    )
    try:
        await _seed_old_round(manager, PRIVATE_SESSION, gap_minutes=100)
        event = _Event(umo=PRIVATE_SESSION, text="100分钟后的问题")
        await handler.handle_memory_recall(event, _req("100分钟后的问题"))
        await handler.handle_memory_reflection(event, _resp("回答1"))
        await _settle(handler)
        assert (await engine.get_statistics())["total_memories"] == 0

        other_session = "qq:FriendMessage:idle-150"
        await _seed_old_round(manager, other_session, gap_minutes=150)
        event2 = _Event(umo=other_session, text="150分钟后的问题")
        await handler.handle_memory_recall(event2, _req("150分钟后的问题"))
        await handler.handle_memory_reflection(event2, _resp("回答2"))
        await _settle(handler)
        await handler.shutdown()

        assert (await engine.get_statistics())["total_memories"] == 1
    finally:
        await close_handler(handler, manager, store, engine, db)


@pytest.mark.asyncio
async def test_reflection_concurrency_is_capped(tmp_path):
    """多个会话同时触发总结时，同时进入 Provider 的任务数不超过上限。"""
    provider = SummaryProvider(delay=0.2)
    handler, manager, store, engine, db = await make_handler(
        tmp_path,
        config={"reflection_engine": {"summary_trigger_rounds": 1}},
        llm_provider=provider,
        host=HostContext(),
    )
    try:
        sessions = [f"test:group:team-{index}" for index in range(3)]
        for session in sessions:
            await _seed(
                manager,
                session,
                [
                    time.time() - 60,
                    time.time() - 59,
                ],
            )

        await asyncio.gather(
            *(
                handler.handle_memory_reflection(_Event(umo=s), _resp())
                for s in sessions
            )
        )
        await _settle(handler)
        await handler.shutdown()

        assert provider.calls >= 3
        assert provider.max_active <= 2
    finally:
        await close_handler(handler, manager, store, engine, db)


@pytest.mark.asyncio
async def test_failed_task_releases_its_concurrency_slot(tmp_path):
    """一次失败不会永久占用并发名额，后续会话仍能完成总结。"""
    provider = SummaryProvider(error=RuntimeError("boom"))
    handler, manager, store, engine, db = await make_handler(
        tmp_path,
        config={"reflection_engine": {"summary_trigger_rounds": 1}},
        llm_provider=provider,
        host=HostContext(),
    )
    try:
        for session in ("test:group:f1", "test:group:f2"):
            await _seed(
                manager,
                session,
                [time.time() - 60, time.time() - 59],
            )

        await asyncio.gather(
            *(
                handler.handle_memory_reflection(_Event(umo=session), _resp())
                for session in ("test:group:f1", "test:group:f2")
            )
        )
        await _settle(handler)
        await handler.shutdown()

        # 两次调用都发生（没有被卡死的信号量拦住）
        assert provider.calls == 2
    finally:
        await close_handler(handler, manager, store, engine, db)
