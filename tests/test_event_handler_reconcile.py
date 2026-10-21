"""
Event handler tests: WebUI edit/retry reconciliation and reflection guards.
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

# ==================== WebUI 编辑/重试历史对账测试 ====================


@pytest.mark.asyncio
async def test_edit_resend_reconciles_store_to_llm_history(tmp_path):
    """编辑上一条消息并重发（整轮回滚）：库内旧轮应被清理，当前轮只存一次。"""
    handler, manager, store = await _make_real_handler(tmp_path)
    sid = "test:private:sid-1"
    await manager.add_message(
        session_id=sid,
        role="user",
        content="q1",
        sender_id="u1",
        metadata={"llm_checkpoint_id": "C1"},
    )
    await manager.add_message(
        session_id=sid,
        role="assistant",
        content="a1",
        sender_id="bot1",
        metadata={"llm_checkpoint_id": "C1"},
    )

    # 编辑后 LLM 历史为空（整轮被撤销），新请求携带新 checkpoint C2
    event = _make_event(checkpoint="C2")
    req = _make_req("q1-edited")
    req.contexts = []

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = None
        await handler.handle_memory_recall(event, req)

    rows = await store.get_session_messages_asc(sid)
    assert [m.role for m in rows] == ["user"]
    assert rows[0].content == "q1-edited"
    assert rows[0].metadata["llm_checkpoint_id"] == "C2"
    session = await store.get_session(sid)
    assert session.message_count == 1

    await handler.shutdown()
    await store.close()


@pytest.mark.asyncio
async def test_regenerate_reconciles_reverted_tail(tmp_path):
    """重试上一条对话：库内被撤销的尾轮应被删除，头部轮次保留。"""
    handler, manager, store = await _make_real_handler(tmp_path)
    sid = "test:private:sid-1"
    for checkpoint, user_text, bot_text in (
        ("C1", "q1", "a1"),
        ("C2", "q2", "a2"),
    ):
        await manager.add_message(
            session_id=sid,
            role="user",
            content=user_text,
            sender_id="u1",
            metadata={"llm_checkpoint_id": checkpoint},
        )
        await manager.add_message(
            session_id=sid,
            role="assistant",
            content=bot_text,
            sender_id="bot1",
            metadata={"llm_checkpoint_id": checkpoint},
        )

    # LLM 历史只剩 C1 轮（C2 轮被重试撤销），新请求 checkpoint C3
    event = _make_event(checkpoint="C3")
    req = _make_req("q2")
    req.contexts = [{"role": "_checkpoint", "content": {"id": "C1"}}]

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = None
        await handler.handle_memory_recall(event, req)

    rows = await store.get_session_messages_asc(sid)
    assert [m.role for m in rows] == ["user", "assistant", "user"]
    assert [m.metadata.get("llm_checkpoint_id") for m in rows] == [
        "C1",
        "C1",
        "C3",
    ]
    session = await store.get_session(sid)
    assert session.message_count == 3

    await handler.shutdown()
    await store.close()


@pytest.mark.asyncio
async def test_reconcile_skips_group_events(tmp_path):
    """群聊事件不应触发对账（被动捕获的消息不在 LLM 历史中）。"""
    handler, manager, store = await _make_real_handler(tmp_path)
    sid = "test:private:sid-1"
    await manager.add_message(
        session_id=sid,
        role="user",
        content="q1",
        sender_id="u1",
        metadata={"llm_checkpoint_id": "C1"},
    )
    await manager.add_message(
        session_id=sid,
        role="assistant",
        content="a1",
        sender_id="bot1",
        metadata={"llm_checkpoint_id": "C1"},
    )

    event = _make_event(group=True)
    event.unified_msg_origin = "test:group:gid-1"
    req = _make_req("hello")
    req.contexts = []

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = None
        await handler.handle_memory_recall(event, req)

    # 群聊既有消息未被删除，也未存储用户消息
    rows = await store.get_session_messages_asc(sid)
    assert len(rows) == 2

    await handler.shutdown()
    await store.close()


@pytest.mark.asyncio
async def test_reconcile_skips_without_checkpoints(tmp_path):
    """非 webchat：contexts 与库内均无 checkpoint → 零行为变化。"""
    handler, manager, store = await _make_real_handler(tmp_path)
    sid = "test:private:sid-1"
    await manager.add_message(
        session_id=sid,
        role="user",
        content="q1",
        sender_id="u1",
    )
    await manager.add_message(
        session_id=sid,
        role="assistant",
        content="a1",
        sender_id="bot1",
    )

    event = _make_event(group=False)
    req = _make_req("q2")
    req.contexts = [{"role": "user", "content": "q1"}]

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = None
        await handler.handle_memory_recall(event, req)

    rows = await store.get_session_messages_asc(sid)
    assert len(rows) == 3  # 旧两条保留 + 新用户消息
    assert rows[-1].role == "user"
    assert rows[-1].content == "q2"

    await handler.shutdown()
    await store.close()


@pytest.mark.asyncio
async def test_reflection_guard_skips_stale_assistant(tmp_path):
    """重试瞬间迟到的旧 bot 回复应被跳过，不污染会话库。"""
    handler, manager, store = await _make_real_handler(tmp_path)
    sid = "test:private:sid-1"
    await manager.add_message(
        session_id=sid,
        role="user",
        content="q1",
        sender_id="u1",
        metadata={"llm_checkpoint_id": "C1"},
    )

    # 旧轮次（C1）的回复在新轮次（C2）之后才到达 → 应跳过
    event = _make_event(checkpoint="C2")
    resp = _make_resp("old reply")
    await handler.handle_memory_reflection(event, resp)

    assert await store.get_message_count(sid) == 1

    await handler.shutdown()
    await store.close()


@pytest.mark.asyncio
async def test_reflection_guard_logs_warning_for_superseded_reply(tmp_path, caplog):
    """#289：被取代的回复被丢弃时必须告警，并带上 session 与旧->新 checkpoint。"""
    handler, manager, store = await _make_real_handler(tmp_path)
    sid = "test:private:sid-1"
    await manager.add_message(
        session_id=sid,
        role="user",
        content="q1",
        sender_id="u1",
        metadata={"llm_checkpoint_id": "C1"},
    )

    event = _make_event(checkpoint="C2")
    with caplog.at_level(logging.WARNING):
        await handler.handle_memory_reflection(event, _make_resp("old reply"))

    # 行为不变：回复仍被丢弃
    assert await store.get_message_count(sid) == 1
    warned = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert any(
        sid in message and "C1" in message and "C2" in message and "被取代" in message
        for message in warned
    ), warned

    await handler.shutdown()
    await store.close()


@pytest.mark.asyncio
async def test_reflection_guard_stores_matching_assistant(tmp_path):
    """checkpoint 匹配时助手消息正常入库，且带 checkpoint 标记。"""
    handler, manager, store = await _make_real_handler(tmp_path)
    sid = "test:private:sid-1"
    await manager.add_message(
        session_id=sid,
        role="user",
        content="q1",
        sender_id="u1",
        metadata={"llm_checkpoint_id": "C1"},
    )

    event = _make_event(checkpoint="C1")
    resp = _make_resp("proper reply")
    await handler.handle_memory_reflection(event, resp)

    rows = await store.get_session_messages_asc(sid)
    assert len(rows) == 2
    assert rows[1].role == "assistant"
    assert rows[1].metadata["llm_checkpoint_id"] == "C1"

    await handler.shutdown()
    await store.close()


@pytest.mark.asyncio
async def test_reconcile_gated_on_checkpoint_extra(
    handler, conversation_manager
):
    """非 webchat（事件无 llm_checkpoint_id extra）不触发对账，避免每次读库。"""
    # 无 checkpoint extra（Mock 自动返回 Mock，非 str）→ 不调用对账
    event = _make_event(group=False)
    req = _make_req("hello")
    req.contexts = [{"role": "_checkpoint", "content": {"id": "C1"}}]
    await handler.handle_memory_recall(event, req)
    conversation_manager.reconcile_session_tail.assert_not_awaited()

    # 携带 checkpoint extra → 调用对账
    event2 = _make_event(group=False, checkpoint="C9")
    await handler.handle_memory_recall(event2, req)
    conversation_manager.reconcile_session_tail.assert_awaited_once()
    call_args = conversation_manager.reconcile_session_tail.await_args.args
    assert call_args[0] == event2.unified_msg_origin


@pytest.mark.asyncio
async def test_image_turn_does_not_break_reconciliation(tmp_path):
    """纯图片轮次（user 行未入库）不应让对账静默失效；回滚仍被清理。"""
    handler, manager, store = await _make_real_handler(tmp_path)
    sid = "test:private:sid-1"

    for role, content, checkpoint in (
        ("user", "q1", "C1"),
        ("assistant", "a1", "C1"),
        # 纯图片轮 C2：user 行未入库，仅 assistant 行存在
        ("assistant", "a2", "C2"),
        ("user", "q3", "C3"),
        ("assistant", "a3", "C3"),
    ):
        await manager.add_message(
            session_id=sid,
            role=role,
            content=content,
            sender_id="u1" if role == "user" else "bot1",
            metadata={"llm_checkpoint_id": checkpoint},
        )

    # 正常新请求 C4：LLM 历史与库一致（含图片轮）→ 不删任何内容
    event = _make_event(checkpoint="C4")
    req = _make_req("q4")
    req.contexts = [
        {"role": "_checkpoint", "content": {"id": c}} for c in ("C1", "C2", "C3")
    ]
    with patch(
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = None
        await handler.handle_memory_recall(event, req)

    rows = await store.get_session_messages_asc(sid)
    assert [m.role for m in rows] == [
        "user",
        "assistant",
        "assistant",
        "user",
        "assistant",
        "user",
    ]

    # 回滚 C3/C4：对账仍能清理回滚尾部（图片轮 C2 保留）
    event2 = _make_event(checkpoint="C5")
    req2 = _make_req("q4-again")
    req2.contexts = [
        {"role": "_checkpoint", "content": {"id": c}} for c in ("C1", "C2")
    ]
    with patch(
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = None
        await handler.handle_memory_recall(event2, req2)

    rows = await store.get_session_messages_asc(sid)
    assert [m.role for m in rows] == ["user", "assistant", "assistant", "user"]
    assert [m.metadata.get("llm_checkpoint_id") for m in rows] == [
        "C1",
        "C1",
        "C2",
        "C5",
    ]

    await handler.shutdown()
    await store.close()


@pytest.mark.asyncio
async def test_reflection_guard_allows_image_turn_reply(tmp_path):
    """库尾为 assistant 且 checkpoint 不同（纯图片轮次回复）应正常入库。"""
    handler, manager, store = await _make_real_handler(tmp_path)
    sid = "test:private:sid-1"
    await manager.add_message(
        session_id=sid,
        role="user",
        content="q1",
        sender_id="u1",
        metadata={"llm_checkpoint_id": "C1"},
    )
    await manager.add_message(
        session_id=sid,
        role="assistant",
        content="a1",
        sender_id="bot1",
        metadata={"llm_checkpoint_id": "C1"},
    )

    # 图片轮 C2 的回复：无 user 行，尾行 = a(C1)（assistant 且 checkpoint 不同）
    event = _make_event(checkpoint="C2")
    resp = _make_resp("image reply")
    await handler.handle_memory_reflection(event, resp)

    rows = await store.get_session_messages_asc(sid)
    assert len(rows) == 3
    assert rows[-1].metadata["llm_checkpoint_id"] == "C2"

    await handler.shutdown()
    await store.close()


# ---------------------------------------------------------------------------
# WebUI 编辑/重试回滚 vs 后台总结任务：修订号 / 原子游标 / 任务取消
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_bumps_revision_and_cancels_inflight_task(tmp_path):
    """检测到回滚时：推进会话修订号，并取消该会话进行中的总结任务。"""
    handler, manager, store = await _make_real_handler(tmp_path)
    sid = "test:private:sid-1"
    await _seed_turns(manager, sid, ("C1", "C2"))
    assert await manager.get_session_revision(sid) == 0

    # 模拟一个仍在执行（等待 LLM）的后台总结任务
    async def _slow_task():
        await asyncio.sleep(30)

    inflight = asyncio.create_task(_slow_task())
    handler._storage_session_tasks[sid] = inflight

    # LLM 历史只剩 C1 轮 → C2 轮被回滚
    event = _make_event(checkpoint="C3")
    req = _make_req("q-C2-edited")
    req.contexts = [{"role": "_checkpoint", "content": {"id": "C1"}}]

    with patch(
        "astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = None
        await handler.handle_memory_recall(event, req)

    assert await manager.get_session_revision(sid) == 1

    await asyncio.wait({inflight}, timeout=2)
    assert inflight.cancelled(), "回滚应取消该会话进行中的总结任务"

    await handler.shutdown()
    await store.close()


@pytest.mark.asyncio
async def test_slow_summary_discarded_when_rollback_happens(tmp_path):
    """慢总结期间发生编辑/重试回滚：不得写入过期内容，也不得复活旧游标。"""
    engine = Mock()
    engine.search_memories = AsyncMock(return_value=[])
    engine.add_memory = AsyncMock(return_value=1)
    handler, manager, store = await _make_real_handler(tmp_path, memory_engine=engine)
    sid = "test:private:sid-1"
    await _seed_turns(manager, sid, ("C1", "C2"))

    messages = await manager.get_messages_range(sid, 0, 4)
    assert len(messages) == 4

    async def _slow_process(**_kwargs):
        # LLM 慢返回期间：用户编辑上一条消息并重发（等价于回滚 C2 轮）
        await manager.reconcile_session_tail(sid, ["C1"])
        return "summary", {"topics": ["t"]}, 0.5

    processor = Mock()
    processor.process_conversation = AsyncMock(side_effect=_slow_process)
    processor.classify_atoms_from_metadata = Mock(return_value=[])
    handler._memory_reflection.memory_processor = processor

    await handler._memory_reflection._storage_task(
        session_id=sid,
        history_messages=messages,
        persona_id=None,
        start_index=0,
        end_index=4,
        retry_count=0,
    )

    # 过期总结被丢弃：不写记忆、不推进游标、不记录待重试范围
    engine.add_memory.assert_not_awaited()
    assert await manager.get_session_metadata(sid, "last_summarized_index", 0) == 0
    assert await manager.get_session_metadata(sid, "pending_summary", None) is None
    # 回滚本身仍然生效：库内只剩 C1 轮
    rows = await store.get_session_messages_asc(sid)
    assert [m.metadata.get("llm_checkpoint_id") for m in rows] == ["C1", "C1"]

    await handler.shutdown()
    await store.close()


@pytest.mark.asyncio
async def test_failed_summary_does_not_record_pending_after_rollback(tmp_path):
    """LLM 失败叠加回滚：不得记录已失效范围的待重试总结（否则下次会总结已撤销内容）。"""
    engine = Mock()
    engine.search_memories = AsyncMock(return_value=[])
    engine.add_memory = AsyncMock(return_value=1)
    handler, manager, store = await _make_real_handler(tmp_path, memory_engine=engine)
    sid = "test:private:sid-1"
    await _seed_turns(manager, sid, ("C1", "C2"))
    messages = await manager.get_messages_range(sid, 0, 4)

    async def _failing_process(**_kwargs):
        await manager.reconcile_session_tail(sid, ["C1"])
        raise RuntimeError("llm down")

    processor = Mock()
    processor.process_conversation = AsyncMock(side_effect=_failing_process)
    processor.classify_atoms_from_metadata = Mock(return_value=[])
    handler._memory_reflection.memory_processor = processor

    await handler._memory_reflection._storage_task(
        session_id=sid,
        history_messages=messages,
        persona_id=None,
        start_index=0,
        end_index=4,
        retry_count=0,
    )

    assert await manager.get_session_metadata(sid, "pending_summary", None) is None
    assert await manager.get_session_metadata(sid, "last_summarized_index", 0) == 0

    await handler.shutdown()
    await store.close()


@pytest.mark.asyncio
async def test_summary_cursor_commit_is_rejected_after_revision_change(
    handler, conversation_manager
):
    """游标写入必须原子校验修订号：回滚后拒绝把 last_summarized_index 写回旧位置。"""
    conversation_manager.update_session_metadata_if_revision = AsyncMock(
        return_value=False
    )
    conversation_manager.update_session_metadata = AsyncMock()

    committed = await handler._memory_reflection._commit_summary_cursor("s1", 12, 3)

    assert committed is False
    conversation_manager.update_session_metadata.assert_not_awaited()


@pytest.mark.asyncio
async def test_metadata_update_if_revision_rejects_stale_revision(tmp_path):
    """会话修订号变化后，带校验的元数据写入必须被拒绝。"""
    handler, manager, store = await _make_real_handler(tmp_path)
    sid = "test:private:sid-1"
    await _seed_turns(manager, sid, ("C1",))

    assert (
        await manager.update_session_metadata_if_revision(
            sid, "last_summarized_index", 2, 0
        )
        is True
    )
    assert await manager.get_session_metadata(sid, "last_summarized_index", 0) == 2

    await manager.bump_session_revision(sid)
    assert (
        await manager.update_session_metadata_if_revision(
            sid, "last_summarized_index", 9, 0
        )
        is False
    )
    assert await manager.get_session_metadata(sid, "last_summarized_index", 0) == 2

    await handler.shutdown()
    await store.close()


@pytest.mark.asyncio
async def test_storage_task_aborts_when_caller_revision_is_stale(tmp_path):
    """调用方在读取总结范围前捕获的修订号已过期：连 LLM 都不应再调用。

    覆盖"范围计算完成 → 后台任务启动"之间发生回滚的窗口。
    """
    engine = Mock()
    engine.search_memories = AsyncMock(return_value=[])
    engine.add_memory = AsyncMock(return_value=1)
    handler, manager, store = await _make_real_handler(tmp_path, memory_engine=engine)
    sid = "test:private:sid-1"
    await _seed_turns(manager, sid, ("C1", "C2"))
    messages = await manager.get_messages_range(sid, 0, 4)

    # 范围读取之后、任务启动之前发生回滚
    await manager.bump_session_revision(sid)

    processor = Mock()
    processor.process_conversation = AsyncMock(return_value=("summary", {}, 0.5))
    processor.classify_atoms_from_metadata = Mock(return_value=[])
    handler._memory_reflection.memory_processor = processor

    await handler._memory_reflection._storage_task(
        session_id=sid,
        history_messages=messages,
        persona_id=None,
        start_index=0,
        end_index=4,
        retry_count=0,
        expected_revision=0,
    )

    processor.process_conversation.assert_not_awaited()
    engine.add_memory.assert_not_awaited()
    assert await manager.get_session_metadata(sid, "last_summarized_index", 0) == 0

    await handler.shutdown()
    await store.close()
