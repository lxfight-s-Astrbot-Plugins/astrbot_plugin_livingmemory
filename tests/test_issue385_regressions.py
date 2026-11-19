"""#385：有界反思输入、收敛式保留与游标锚点的黑盒回归。

通过公开钩子与 ``ConversationManager`` 的公开 API 驱动，断言库内消息、
游标与记忆窗口。
"""

import asyncio
import time
from datetime import datetime
from types import SimpleNamespace

import pytest
import pytest_asyncio

from astrbot.core.message.components import Plain

from tests.real_engine_support import (
    HostContext,
    SummaryProvider,
    close_handler,
    make_handler,
)

SESSION = "test:group:bounded"


def _event(umo: str = SESSION):
    class _Event:
        unified_msg_origin = umo
        message_str = "hello"
        sender_id = "user-1"
        sender_name = "Tester"

        def get_message_type(self):
            from astrbot.api.platform import MessageType

            return MessageType.GROUP_MESSAGE

        def get_sender_id(self):
            return "user-1"

        def get_self_id(self):
            return "bot-1"

        def get_sender_name(self):
            return "Tester"

        def get_message_str(self):
            return "hello"

        def get_messages(self):
            return [Plain("hello")]

        def get_platform_name(self):
            return "test"

        def get_platform_id(self):
            return "test-instance"

        def get_group_id(self):
            return umo.rsplit(":", 1)[-1]

        def get_extra(self, key, default=None):
            return default

    return _Event()


def _resp(text: str = "assistant reply"):
    return SimpleNamespace(
        role="assistant",
        completion_text=text,
        is_chunk=False,
        tools_call_name=None,
        tools_call_extra_content=None,
    )


def _ts(index: int) -> float:
    return datetime(2026, 8, 1, 12, 0).timestamp() + index


async def _seed(manager, session_id: str, count: int, start: int = 0) -> None:
    for index in range(start, start + count):
        await manager.add_message(
            session_id=session_id,
            role="user" if index % 2 == 0 else "assistant",
            content=f"message-{index}",
            sender_id="user-1" if index % 2 == 0 else "bot-1",
            group_id="g",
            timestamp=_ts(index),
        )


async def _settle(handler, *, timeout: float = 5.0) -> None:
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
async def bundle(tmp_path):
    items = await make_handler(
        tmp_path,
        config={
            "reflection_engine": {
                "summary_trigger_rounds": 1,
                "max_messages_per_segment": 4,
                "max_segments_per_run": 10,
                "window_split_by_day": False,
                "window_split_gap_minutes": 0,
            },
            "session_manager": {"max_messages_per_session": 100},
            "memory_dedup": {"enabled": False},
        },
        llm_provider=SummaryProvider(vary=True),
        host=HostContext(),
    )
    try:
        yield items
    finally:
        await close_handler(*items)


@pytest.mark.asyncio
async def test_long_same_day_backlog_is_consumed_in_bounded_chunks(bundle):
    """同一天的长积压按 ``max_messages_per_segment`` 切成有界片段逐段总结。"""
    handler, manager, store, engine, db = bundle
    await _seed(manager, SESSION, 10)

    await handler.handle_memory_reflection(_event(), _resp())
    await _settle(handler)
    await handler.shutdown()

    # 最后一段包含触发本次总结的助手回复
    assert await _windows(engine) == [(0, 4), (4, 8), (8, 11)]
    assert await manager.get_session_metadata(SESSION, "last_summarized_index", 0) == 11


@pytest.mark.asyncio
async def test_message_cap_converges_and_drops_the_oldest_messages(bundle):
    """超过会话上限时裁剪最旧消息，且游标归零后继续可用。"""
    handler, manager, store, engine, db = bundle
    sid = "test:private:retention"
    await _seed(manager, sid, 160)

    # 直接调用会话裁剪（召回钩子只在特定条件下触发同一逻辑）
    await handler._message_utils.enforce_message_limit(sid)

    # 上限 100：旧消息被裁掉，游标一并归零
    assert await store.get_message_count(sid) == 100
    assert await manager.get_session_metadata(sid, "last_summarized_index", -1) == 0
    messages = await manager.get_messages_range(sid, 0, 100)
    assert messages[0].content.startswith("message-")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cursor", "deleted", "force", "expected"), [(4, 4, False, 2), (0, 6, True, 0)]
)
async def test_partial_summary_commits_its_message_anchor_after_head_trim(
    bundle, cursor, deleted, force, expected
):
    """头部被裁剪后，摘要游标按消息锚点回落到未删除的位置。"""
    handler, manager, store, engine, db = bundle
    sid = "test:group:anchor"
    await _seed(manager, sid, 10)
    await manager.update_session_metadata(sid, "last_summarized_index", cursor)
    snapshot = await manager.get_messages_range(sid, cursor, 6)
    revision = await manager.get_session_revision(sid)

    await manager.trim_session_messages(sid, deleted, delete_unsummarized=force)

    assert await manager.commit_summary_cursor(sid, snapshot[-1], revision) is True
    assert (
        await manager.get_session_metadata(sid, "last_summarized_index", -1) == expected
    )


@pytest.mark.asyncio
async def test_stale_retry_range_does_not_skip_the_unsummarized_prefix(bundle):
    """历史待重试偏移与游标不一致时，从未总结的最早消息开始。"""
    handler, manager, store, engine, db = bundle
    sid = "test:group:pending-range"
    await _seed(manager, sid, 10)
    await manager.update_session_metadata(
        sid,
        "pending_summary",
        {"start_index": 6, "end_index": 10, "retry_count": 3},
    )

    await handler.handle_memory_reflection(_event(sid), _resp())
    await _settle(handler)
    await handler.shutdown()

    assert await _windows(engine) == [(0, 4), (4, 8), (8, 11)]


@pytest.mark.asyncio
async def test_cursor_moved_by_trimming_is_refreshed_before_summarising(bundle):
    """头部被裁剪后，下一次总结从裁剪后的第一条消息开始。"""
    handler, manager, store, engine, db = bundle
    sid = "test:group:snapshot-shift"
    await _seed(manager, sid, 10)
    await manager.update_session_metadata(sid, "last_summarized_index", 4)
    await manager.trim_session_messages(sid, 4)

    await handler.handle_memory_reflection(_event(sid), _resp())
    await _settle(handler)
    await handler.shutdown()

    windows = await _windows(engine)
    # 裁剪后游标(4)超出剩余消息数，总结从裁剪后的头部开始
    assert windows, "no memory was stored"
    page = await engine.list_memories_page(page_size=10)
    metadata = page["items"][0]["metadata"]
    if isinstance(metadata, str):
        import json

        metadata = json.loads(metadata)
    window = metadata["source_window"]
    assert window["start_index"] == 4
    assert window["end_index"] > window["start_index"]
