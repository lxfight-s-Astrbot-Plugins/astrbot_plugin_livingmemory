"""
Event handler tests: prompt fallback, context expansion and summary windows.
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.event_handler import EventHandler

from astrbot.api.platform import MessageType



from tests.event_handler_support import (
    _make_event,
    _make_real_handler,
    _make_req,
    _make_resp,
    _seed_turns,
    conversation_manager,
    handler,
    memory_engine,
    memory_processor,
)

# ==================== system_prompt 自动回退测试 ====================


@pytest.mark.asyncio
async def test_system_prompt_auto_falls_back_to_extra_user_content(
    memory_engine, memory_processor, conversation_manager
):
    """配置 system_prompt 时应自动回退到 extra_user_content 方式。"""
    from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
    from astrbot_plugin_livingmemory.core.event_handler import EventHandler

    h = EventHandler(
        context=Mock(),
        config_manager=ConfigManager(
            {
                "recall_engine": {
                    "top_k": 3,
                    "injection_method": "system_prompt",
                },
                "reflection_engine": {"summary_trigger_rounds": 1},
                "session_manager": {"max_messages_per_session": 100},
            }
        ),
        memory_engine=memory_engine,
        memory_processor=Mock(),
        conversation_manager=Mock(),
    )
    h.conversation_manager.add_message_from_event = AsyncMock()
    # 设置 store mock 以支持 _enforce_message_limit 中的 await 调用
    h.conversation_manager.store = Mock()
    h.conversation_manager.store.get_message_count = AsyncMock(return_value=12)
    h.conversation_manager.store.connection = Mock()
    h.conversation_manager.get_session_metadata = AsyncMock(return_value=0)
    h.conversation_manager.update_session_metadata = AsyncMock()
    h.conversation_manager.invalidate_cache = AsyncMock()

    recalled = Mock(
        content="mem_fallback", final_score=0.8, metadata={"importance": 0.9}
    )
    memory_engine.search_memories = AsyncMock(return_value=[recalled])

    event = _make_event(group=False)
    req = _make_req("query text")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await h.handle_memory_recall(event, req)

    # system_prompt 已废弃，应自动回退到 extra_user_content（注入到 extra_user_content_parts）
    assert req.system_prompt == ""
    assert len(req.extra_user_content_parts) == 1
    assert "mem_fallback" in req.extra_user_content_parts[0].text
    assert getattr(req.extra_user_content_parts[0], "_no_save", False) is True


# ==================== 上下文扩展测试 ====================


@pytest.mark.asyncio
async def test_context_expansion_enriches_query(
    memory_engine, memory_processor, conversation_manager
):
    """启用 inject_with_recent_context 时，查询应拼接历史消息上下文。"""
    from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
    from astrbot_plugin_livingmemory.core.event_handler import EventHandler

    cm_mock = Mock()
    cm_mock.add_message_from_event = AsyncMock()
    # 设置 store mock 以支持 _enforce_message_limit 中的 await 调用
    cm_mock.store = Mock()
    cm_mock.store.get_message_count = AsyncMock(return_value=12)
    cm_mock.store.connection = Mock()
    cm_mock.get_session_metadata = AsyncMock(return_value=0)
    cm_mock.update_session_metadata = AsyncMock()
    cm_mock.invalidate_cache = AsyncMock()
    # 模拟返回 3 条消息（最新在前）: [当前消息, bot 回复, 用户上条]
    cm_mock.get_context = AsyncMock(
        return_value=[
            {"content": "当前用户消息", "timestamp": time.time()},
            {"content": "Bot 的上一条回复", "timestamp": time.time() - 10},
            {"content": "用户之前说的事情", "timestamp": time.time() - 20},
        ]
    )

    h = EventHandler(
        context=Mock(),
        config_manager=ConfigManager(
            {
                "recall_engine": {
                    "top_k": 3,
                    "injection_method": "extra_user_content",
                    "inject_with_recent_context": True,
                },
                "reflection_engine": {"summary_trigger_rounds": 1},
                "session_manager": {"max_messages_per_session": 100},
            }
        ),
        memory_engine=memory_engine,
        memory_processor=Mock(),
        conversation_manager=cm_mock,
    )

    recalled = Mock(
        content="mem_context", final_score=0.8, metadata={"importance": 0.9}
    )
    memory_engine.search_memories = AsyncMock(return_value=[recalled])

    event = _make_event(group=False)
    event.get_message_str = Mock(return_value="当前用户消息")
    req = _make_req("当前用户消息")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await h.handle_memory_recall(event, req)

    # 验证 search_memories 收到的 query 包含扩展内容
    call_kwargs = memory_engine.search_memories.await_args.kwargs
    assert "用户之前说的事情" in call_kwargs["query"]
    assert "Bot 的上一条回复" in call_kwargs["query"]
    cm_mock.get_context.assert_awaited_once_with(
        event.unified_msg_origin,
        max_messages=5,
        format_for_llm=False,
    )


@pytest.mark.asyncio
async def test_context_expansion_excludes_stale_and_undated_messages(
    memory_engine, memory_processor, conversation_manager
):
    """超过最大间隔或缺失时间戳的消息不应污染扩展查询。"""
    now = time.time()
    cm_mock = Mock()
    cm_mock.add_message_from_event = AsyncMock()
    cm_mock.store = Mock()
    cm_mock.store.get_message_count = AsyncMock(return_value=4)
    cm_mock.store.connection = Mock()
    cm_mock.get_session_metadata = AsyncMock(return_value=0)
    cm_mock.update_session_metadata = AsyncMock()
    cm_mock.invalidate_cache = AsyncMock()
    cm_mock.get_context = AsyncMock(
        return_value=[
            {"content": "当前消息", "timestamp": now},
            {"content": "一小时内", "timestamp": now - 1800},
            {"content": "三小时前", "timestamp": now - 10800},
            {"content": "没有时间"},
        ]
    )

    h = EventHandler(
        context=Mock(),
        config_manager=ConfigManager(
            {
                "recall_engine": {
                    "top_k": 3,
                    "inject_with_recent_context": True,
                    "recent_context_max_age_seconds": 7200,
                }
            }
        ),
        memory_engine=memory_engine,
        memory_processor=Mock(),
        conversation_manager=cm_mock,
    )
    memory_engine.search_memories = AsyncMock(return_value=[])
    event = _make_event(group=False)
    event.get_message_str = Mock(return_value="当前消息")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await h.handle_memory_recall(event, _make_req("当前消息"))

    query = memory_engine.search_memories.await_args.kwargs["query"]
    assert "一小时内" in query
    assert "三小时前" not in query
    assert "没有时间" not in query


@pytest.mark.asyncio
async def test_context_expansion_skips_when_empty(
    memory_engine, memory_processor, conversation_manager
):
    """get_context 返回空或单条时，直接使用原始查询。"""
    from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
    from astrbot_plugin_livingmemory.core.event_handler import EventHandler

    h = EventHandler(
        context=Mock(),
        config_manager=ConfigManager(
            {
                "recall_engine": {
                    "top_k": 3,
                    "injection_method": "extra_user_content",
                    "inject_with_recent_context": True,
                },
                "reflection_engine": {"summary_trigger_rounds": 1},
                "session_manager": {"max_messages_per_session": 100},
            }
        ),
        memory_engine=memory_engine,
        memory_processor=Mock(),
        conversation_manager=Mock(),
    )
    h.conversation_manager.add_message_from_event = AsyncMock()

    # 只返回一条消息（只有当前消息，无历史）
    h.conversation_manager.get_context = AsyncMock(
        return_value=[
            {"content": "唯一一条消息"},
        ]
    )

    recalled = Mock(content="mem_skip", final_score=0.8, metadata={"importance": 0.9})
    memory_engine.search_memories = AsyncMock(return_value=[recalled])

    event = _make_event(group=False)
    event.get_message_str = Mock(return_value="唯一一条消息")
    req = _make_req("唯一一条消息")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await h.handle_memory_recall(event, req)

    call_kwargs = memory_engine.search_memories.await_args.kwargs
    # 不应包含 " | " 分隔符（因为没有足够的历史消息来拼接）
    assert " | " not in call_kwargs["query"]


# ==================== 总结重试测试 ====================


@pytest.mark.asyncio
async def test_pending_summary_retry_max_abandons(
    handler, conversation_manager, memory_engine
):
    """retry_count >= 3 时应放弃该范围，更新 last_summarized_index 并跳过总结。"""
    conversation_manager.get_session_metadata = AsyncMock(
        side_effect=lambda sid, key, default=None: {
            "last_summarized_index": 0,
            "pending_summary": {"start_index": 2, "end_index": 10, "retry_count": 3},
        }.get(key, default)
    )
    conversation_manager.store.get_message_count = AsyncMock(return_value=12)

    event = _make_event(group=False)
    resp = _make_resp("assistant reply")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_reflection.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await handler.handle_memory_reflection(event, resp)

    # 验证 pending_summary 被清除
    clear_calls = [
        c
        for c in conversation_manager.update_session_metadata.await_args_list
        if c.args[1] == "pending_summary"
    ]
    assert len(clear_calls) >= 1
    # 验证 last_summarized_index 被更新到 end_index
    skip_calls = [
        c
        for c in conversation_manager.update_session_metadata.await_args_list
        if c.args[1] == "last_summarized_index"
    ]
    assert any(c.args[2] == 12 for c in skip_calls)
    # 不应触发记忆总结
    memory_engine.add_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_summary_retry_merges_range(
    handler, conversation_manager, memory_engine
):
    """retry_count < 3 时应合并范围（start_index 使用 pending_start）。"""
    conversation_manager.get_session_metadata = AsyncMock(
        side_effect=lambda sid, key, default=None: {
            "last_summarized_index": 5,
            "pending_summary": {"start_index": 2, "retry_count": 1},
        }.get(key, default)
    )
    conversation_manager.store.get_message_count = AsyncMock(return_value=12)
    # 返回足够消息以满足 end_index - start_index >= 2
    msgs = [Mock(group_id=None) for _ in range(8)]
    conversation_manager.get_messages_range = AsyncMock(return_value=msgs)

    event = _make_event(group=False)
    resp = _make_resp("assistant reply")

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_reflection.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await handler.handle_memory_reflection(event, resp)
        await handler.shutdown()

    # 应使用合并后的范围
    conversation_manager.get_messages_range.assert_awaited_once()
    call_kwargs = conversation_manager.get_messages_range.await_args.kwargs
    assert call_kwargs["start_index"] == 2  # pending_start
    assert call_kwargs["end_index"] == 12  # total_messages


# ==================== #255 总结窗口按天/空闲间隔切分 ====================


def _window_handler(
    memory_engine, memory_processor, conversation_manager, **reflection_cfg
):
    """构建 reflection_engine 可定制的 EventHandler（其余依赖沿用 mock）。"""
    return EventHandler(
        context=Mock(),
        config_manager=ConfigManager(
            {
                "recall_engine": {"top_k": 3, "injection_method": "extra_user_content"},
                "reflection_engine": {"summary_trigger_rounds": 1, **reflection_cfg},
                "session_manager": {"max_messages_per_session": 100},
            }
        ),
        memory_engine=memory_engine,
        memory_processor=memory_processor,
        conversation_manager=conversation_manager,
    )


def _ts(day: int, hour: int, minute: int) -> float:
    return datetime(2025, 3, day, hour, minute).timestamp()


def _day_split_timestamps() -> list[float]:
    """4 条 03-01 晚间 + 8 条 03-02 上午：第一个边界在第 4 条之后。"""
    return [_ts(1, 22, i) for i in range(4)] + [_ts(2, 9, i) for i in range(8)]


def _gap_split_timestamps() -> list[float]:
    """6 条 09:00-09:05 + 6 条 12:06-12:11：同一天，间隔 181 分钟。"""
    return [_ts(1, 9, i) for i in range(6)] + [_ts(1, 12, 6 + i) for i in range(6)]


def _same_day_timestamps() -> list[float]:
    """12 条同一天、间隔 1 分钟的消息：没有边界，保持整段总结。"""
    return [_ts(1, 9, i) for i in range(12)]


@pytest.mark.parametrize(
    ("reflection_cfg", "timestamps", "expected_end"),
    [
        ({}, _day_split_timestamps(), 4),
        ({}, _gap_split_timestamps(), 6),
        ({}, _same_day_timestamps(), 12),
        ({"window_split_gap_minutes": 0}, _gap_split_timestamps(), 12),
        (
            {"window_split_by_day": False, "window_split_gap_minutes": 0},
            _day_split_timestamps(),
            12,
        ),
    ],
    ids=["day-split", "gap-split", "no-boundary", "gap-disabled", "split-disabled"],
)
@pytest.mark.asyncio
async def test_reflection_window_split_chooses_oldest_segment(
    memory_engine,
    memory_processor,
    conversation_manager,
    reflection_cfg,
    timestamps,
    expected_end,
):
    """#255：触发总结时 end_index 收敛到第一个切分边界，只总结最老片段。"""
    conversation_manager.get_messages_range = AsyncMock(
        return_value=[SimpleNamespace(timestamp=ts) for ts in timestamps]
    )
    conversation_manager.store.get_message_count = AsyncMock(return_value=12)
    handler = _window_handler(
        memory_engine, memory_processor, conversation_manager, **reflection_cfg
    )
    handler._memory_reflection._storage_task = AsyncMock()

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_reflection.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_1"
        await handler.handle_memory_reflection(_make_event(), _make_resp())
        await handler.shutdown()

    # 切分扫描与总结输入复用同一次范围读取
    conversation_manager.get_messages_range.assert_awaited_once()
    call_kwargs = conversation_manager.get_messages_range.await_args.kwargs
    assert call_kwargs["start_index"] == 0
    assert call_kwargs["end_index"] == 12

    storage_call = handler._memory_reflection._storage_task.await_args
    assert storage_call is not None
    assert storage_call.args[3] == 0  # start_index 不变
    assert storage_call.args[4] == expected_end
    assert len(storage_call.args[1]) == expected_end


