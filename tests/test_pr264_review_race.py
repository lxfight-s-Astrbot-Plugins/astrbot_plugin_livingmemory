"""PR #264 评审回归：回滚与游标提交的并发一致性。

评审（lxfight，2026-10-03）指出：``update_session_metadata_if_revision`` 通过
revision=0 校验后会持有会话锁、暂停在真正的元数据写入之前；此时并发执行
``reconcile_session_tail`` 能完成删除，随后放行旧写入，最终出现
``last_summarized_index=4 > message_count=2`` 的倒挂（反思逻辑不会自愈，
新消息会被跳过总结）。

修复采用三重防护：
1. 回滚的**游标钳制 + 修订号推进**与条件写入落在同一会话锁临界区；
2. 条件写入在锁内按**当前消息数**再次钳制 ``last_summarized_index``；
3. 对账短路路径与 ``clear_session`` 增加倒挂/越界自愈（含 ``pending_summary``）。

本文件第一个用例逐字保留评审者提供的复现。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from astrbot_plugin_livingmemory.core.models.conversation_models import Message

from tests.test_event_handler import _make_real_handler, _seed_turns

SID_PREFIX = "test:private:"


async def _cursor(manager, sid: str) -> int:
    return await manager.get_session_metadata(sid, "last_summarized_index", 0)


async def _force_dangling_cursor(manager, store, sid: str, value: int) -> None:
    """模拟"旧版本/外部写入"留下的倒挂游标。

    修复后公共写入（条件与无条件）都会按当前消息数钳制，已无法通过公共 API
    制造倒挂状态；因此这里直接走内部持久化助手，还原历史数据的真实形态，
    用来守护自愈路径。
    """
    session = await store.get_session(sid)
    metadata = dict(session.metadata or {})
    metadata["last_summarized_index"] = value
    # 直接写库：修复后所有公共写入路径都会原子钳制，只有"旧版本/外部写入"
    # 才可能留下这种倒挂状态，这里如实还原它的形态
    await store.connection.execute(
        "UPDATE sessions SET metadata = ? WHERE session_id = ?",
        (json.dumps(metadata, ensure_ascii=False), sid),
    )
    await store.connection.commit()


async def _force_pending_summary(manager, store, sid: str, pending) -> None:
    """模拟"历史/外部写入"留下的越界 pending_summary。

    公共写入会在 store 的写锁内即时钳制 pending，因此只能直接写库还原其形态。
    """
    session = await store.get_session(sid)
    metadata = dict(session.metadata or {})
    if pending is None:
        metadata.pop("pending_summary", None)
    else:
        metadata["pending_summary"] = pending
    await store.connection.execute(
        "UPDATE sessions SET metadata = ? WHERE session_id = ?",
        (json.dumps(metadata, ensure_ascii=False), sid),
    )
    await store.connection.commit()


async def _cleanup(handler, store) -> None:
    await handler.shutdown()
    await store.close()


# ---------------------------------------------------------------- 评审者复现
@pytest.mark.asyncio
async def test_rollback_atomic_with_cursor_commit(tmp_path):
    handler, manager, store = await _make_real_handler(tmp_path)
    sid = "test:private:review-race"
    await _seed_turns(manager, sid, ("C1", "C2"))
    started = asyncio.Event()
    release = asyncio.Event()
    deleted = asyncio.Event()
    original_update = manager.update_session_metadata
    original_delete = store.delete_messages_from_position

    async def delayed_update(session_id, key, value):
        if key == "last_summarized_index" and value == 4:
            started.set()
            await release.wait()
        await original_update(session_id, key, value)

    async def notify_delete(session_id, position, **kwargs):
        # 对账现在会携带快照 id 集合（按 id 精确删除，避免误删并发新增消息）；
        # 拦截点与时序假设不变，仅透传额外参数
        result = await original_delete(session_id, position, **kwargs)
        deleted.set()
        return result

    manager.update_session_metadata = delayed_update
    store.delete_messages_from_position = notify_delete
    tasks = []
    try:
        writer = asyncio.create_task(
            manager.update_session_metadata_if_revision(
                sid, "last_summarized_index", 4, 0
            )
        )
        tasks.append(writer)
        await asyncio.wait_for(started.wait(), 2)
        rollback = asyncio.create_task(manager.reconcile_session_tail(sid, ["C1"]))
        tasks.append(rollback)
        await asyncio.wait_for(deleted.wait(), 2)
        release.set()
        committed, removed = await asyncio.wait_for(
            asyncio.gather(writer, rollback), 2
        )
        count = await store.get_message_count(sid)
        cursor = await manager.get_session_metadata(sid, "last_summarized_index", 0)
        assert removed == 2
        assert count == 2
        assert await manager.get_session_revision(sid) == 1
        assert cursor <= count, (committed, cursor, count)
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await _cleanup(handler, store)


# ---------------------------------------------------------------- 反序：回滚先行
@pytest.mark.asyncio
async def test_cursor_write_rejected_after_rollback(tmp_path):
    """回滚先完成时，携带旧修订号的写入必须被拒绝，且状态保持自洽。"""
    handler, manager, store = await _make_real_handler(tmp_path)
    sid = SID_PREFIX + "race-reverse"
    try:
        await _seed_turns(manager, sid, ("C1", "C2"))
        removed = await manager.reconcile_session_tail(sid, ["C1"])
        assert removed == 2
        assert await manager.get_session_revision(sid) == 1

        committed = await manager.update_session_metadata_if_revision(
            sid, "last_summarized_index", 4, 0
        )
        assert committed is False
        count = await store.get_message_count(sid)
        assert count == 2
        assert await _cursor(manager, sid) == 0  # 被拒绝的写入不得留下任何游标
    finally:
        await _cleanup(handler, store)


# ---------------------------------------------------------------- 写入侧兜底钳制
@pytest.mark.asyncio
async def test_conditional_cursor_write_is_clamped(tmp_path):
    """修订号校验通过也不允许写入超过消息数的游标（兜底自愈）。"""
    handler, manager, store = await _make_real_handler(tmp_path)
    sid = SID_PREFIX + "clamp-write"
    try:
        await _seed_turns(manager, sid, ("C1", "C2"))  # 4 条消息
        committed = await manager.update_session_metadata_if_revision(
            sid, "last_summarized_index", 99, 0
        )
        assert committed is True
        assert await _cursor(manager, sid) == 4
    finally:
        await _cleanup(handler, store)


# ---------------------------------------------------------------- 清空会话同一窗口
@pytest.mark.asyncio
async def test_clear_session_atomic_against_late_cursor_write(tmp_path):
    """清空会话与迟到旧写入交错：最终 count=0、cursor=0（不得复活总结进度）。"""
    handler, manager, store = await _make_real_handler(tmp_path)
    sid = SID_PREFIX + "race-clear"
    await _seed_turns(manager, sid, ("C1", "C2"))
    started = asyncio.Event()
    release = asyncio.Event()
    original_update = manager.update_session_metadata

    async def delayed_update(session_id, key, value):
        if key == "last_summarized_index":
            started.set()
            await release.wait()
        await original_update(session_id, key, value)

    delete_started = asyncio.Event()
    original_delete_all = store.delete_session_messages

    async def notify_delete_all(session_id):
        delete_started.set()
        return await original_delete_all(session_id)

    manager.update_session_metadata = delayed_update
    store.delete_session_messages = notify_delete_all
    tasks = []
    try:
        writer = asyncio.create_task(
            manager.update_session_metadata_if_revision(
                sid, "last_summarized_index", 4, 0
            )
        )
        tasks.append(writer)
        await asyncio.wait_for(started.wait(), 2)
        clearer = asyncio.create_task(manager.clear_session(sid))
        tasks.append(clearer)
        # 关键时序断言：旧写入仍持锁暂停时，清空**不得**已经开始删除。
        # 修复前 clear_session 不取会话锁 -> 这里会置位 -> 用例失败。
        await asyncio.sleep(0.05)
        assert not delete_started.is_set(), "清空必须阻塞在旧写入持有的会话锁上"
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 3)

        count = await store.get_message_count(sid)
        cursor = await manager.get_session_metadata(sid, "last_summarized_index", 0)
        assert count == 0
        assert cursor == 0
        assert await manager.get_session_revision(sid) >= 1
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await _cleanup(handler, store)


@pytest.mark.asyncio
async def test_unconditional_metadata_write_is_clamped(tmp_path):
    """无条件写入路径（手动总结/反思自愈）也不得写出越界游标。

    否则旧游标会被写回，反思自愈再把它钳到消息数，重发的那一轮被静默吞掉。
    """
    handler, manager, store = await _make_real_handler(tmp_path)
    sid = SID_PREFIX + "clamp-unconditional"
    try:
        await _seed_turns(manager, sid, ("C1", "C2"))  # 4 条消息
        await manager.update_session_metadata(sid, "last_summarized_index", 99)
        assert await _cursor(manager, sid) == 4
    finally:
        await _cleanup(handler, store)


@pytest.mark.asyncio
async def test_self_heal_when_delete_reports_zero(tmp_path):
    """cutoff 已算出但删除返回 0（并发已被删掉）时，仍要修掉倒挂游标。"""
    handler, manager, store = await _make_real_handler(tmp_path)
    sid = SID_PREFIX + "deleted-zero"
    original_delete = store.delete_messages_from_position
    try:
        await _seed_turns(manager, sid, ("C1", "C2"))
        await _force_dangling_cursor(manager, store, sid, 99)

        async def zero_delete(session_id, position, **kwargs):
            return 0

        store.delete_messages_from_position = zero_delete
        # 库尾 checkpoint 与 ctx 不一致 -> 不走短路；cutoff 可算出 -> 走到删除
        assert await manager.reconcile_session_tail(sid, ["C1"]) == 0
        assert await _cursor(manager, sid) == 4
        assert await manager.get_session_revision(sid) >= 1
    finally:
        store.delete_messages_from_position = original_delete
        await _cleanup(handler, store)


# ---------------------------------------------------------------- 短路路径自愈
@pytest.mark.asyncio
async def test_shortcut_path_repairs_dangling_cursor(tmp_path):
    """库尾一致（短路返回）时仍要修掉倒挂游标，否则新消息永远跳过总结。"""
    handler, manager, store = await _make_real_handler(tmp_path)
    sid = SID_PREFIX + "self-heal"
    try:
        await _seed_turns(manager, sid, ("C1", "C2"))
        await _force_dangling_cursor(manager, store, sid, 10)

        removed = await manager.reconcile_session_tail(sid, ["C1", "C2"])
        assert removed == 0  # 尾行一致 -> 短路
        count = await store.get_message_count(sid)
        assert await _cursor(manager, sid) == count == 4
        assert await manager.get_session_revision(sid) >= 1
    finally:
        await _cleanup(handler, store)


@pytest.mark.asyncio
async def test_self_heal_clears_or_clamps_pending_summary(tmp_path):
    """越界的 pending_summary：起点越过末尾则清除，终点越界则收敛到当前长度。"""
    handler, manager, store = await _make_real_handler(tmp_path)
    sid = SID_PREFIX + "self-heal-pending"
    try:
        await _seed_turns(manager, sid, ("C1", "C2"))  # 4 条
        await _force_dangling_cursor(manager, store, sid, 10)
        await _force_pending_summary(
            manager, store, sid, {"start_index": 10, "end_index": 12, "retry_count": 1}
        )
        assert await manager.reconcile_session_tail(sid, ["C1", "C2"]) == 0
        count = await store.get_message_count(sid)
        assert await _cursor(manager, sid) == count
        assert (
            await manager.get_session_metadata(sid, "pending_summary", "missing")
        ) == "missing"

        # 终点越界但起点仍有效 -> 保留并收敛
        await _force_pending_summary(
            manager, store, sid, {"start_index": 2, "end_index": 99, "retry_count": 1}
        )
        assert await manager.reconcile_session_tail(sid, ["C1", "C2"]) == 0
        pending = await manager.get_session_metadata(sid, "pending_summary", None)
        assert isinstance(pending, dict)
        assert pending["end_index"] == count
        assert pending["start_index"] == 2
    finally:
        await _cleanup(handler, store)


@pytest.mark.asyncio
async def test_self_heal_on_conservative_bail_path(tmp_path):
    """无法安全判定删除范围（遗留数据无 checkpoint）时，仍要修掉倒挂游标。"""
    handler, manager, store = await _make_real_handler(tmp_path)
    sid = SID_PREFIX + "self-heal-bail"
    try:
        for index in range(2):
            await manager.add_message(
                session_id=sid, role="user", content=f"q{index}", sender_id="u1"
            )
            await manager.add_message(
                session_id=sid, role="assistant", content=f"a{index}", sender_id="bot1"
            )
        await _force_dangling_cursor(manager, store, sid, 99)

        # 库尾没有 checkpoint -> 不走短路；无 checkpoint 数据 -> 对账保守放弃
        assert await manager.reconcile_session_tail(sid, ["C1"]) == 0
        count = await store.get_message_count(sid)
        assert await _cursor(manager, sid) == count == 4
        assert await manager.get_session_revision(sid) >= 1
    finally:
        await _cleanup(handler, store)


@pytest.mark.asyncio
async def test_self_heal_on_empty_session(tmp_path):
    """空会话残留倒挂游标（清空与旧写入交错）也要被修掉。"""
    handler, manager, store = await _make_real_handler(tmp_path)
    sid = SID_PREFIX + "self-heal-empty"
    try:
        await _seed_turns(manager, sid, ("C1",))
        await _force_dangling_cursor(manager, store, sid, 7)
        await store.delete_session_messages(sid)

        assert await manager.reconcile_session_tail(sid, ["C1"]) == 0
        assert await _cursor(manager, sid) == 0
        assert await manager.get_session_revision(sid) >= 1
    finally:
        await _cleanup(handler, store)


# ---------------------------------------------------------------- 三项局限的回归
@pytest.mark.asyncio
async def test_concurrent_new_message_survives_rollback(tmp_path):
    """对账按快照 id 删除：快照之后并发新增的合法消息不得被误删。

    按位置/时间边界删除会把并发新增的消息一起删掉，并在库中留下永久缺口
    （对账再也认不出那个 checkpoint）。
    """
    handler, manager, store = await _make_real_handler(tmp_path)
    sid = SID_PREFIX + "concurrent-add"
    original_delete = store.delete_messages_from_position
    try:
        await _seed_turns(manager, sid, ("C1", "C2"))

        async def delete_after_new_message(session_id, position, **kwargs):
            # 模拟"快照读取之后、删除执行之前"并发到达的新消息
            await manager.add_message(
                session_id=session_id,
                role="user",
                content="q-C3",
                sender_id="u1",
                metadata={"llm_checkpoint_id": "C3"},
            )
            return await original_delete(session_id, position, **kwargs)

        store.delete_messages_from_position = delete_after_new_message
        removed = await manager.reconcile_session_tail(sid, ["C1"])
        assert removed == 2  # 只删除被撤销的 C2 两条

        refs = await store.get_session_message_refs_asc(sid)
        checkpoints = [
            (row["metadata"] or {}).get("llm_checkpoint_id") for row in refs
        ]
        assert checkpoints == ["C1", "C1", "C3"], checkpoints
    finally:
        store.delete_messages_from_position = original_delete
        await _cleanup(handler, store)


@pytest.mark.asyncio
async def test_trim_does_not_invalidate_inflight_summary(tmp_path):
    """滑动窗口清理不得推进修订号：在途任务的合法提交必须仍然成功。

    trim 只删**已总结**消息，任务范围的内容一条都没被删；任务提交的 end_index
    经原子钳制后恰好等于清理后的总数，本来就是正确游标。若 trim 推进修订号，
    该提交会被判废并触发补偿删除，把刚写完的合法记忆删掉。
    """
    handler, manager, store = await _make_real_handler(tmp_path)
    sid = SID_PREFIX + "trim-inflight"
    try:
        await _seed_turns(manager, sid, ("C1", "C2", "C3", "C4"))  # 8 条
        await manager.update_session_metadata(sid, "last_summarized_index", 4)

        deleted = await manager.trim_session_messages(sid, 2)
        assert deleted == 2
        assert await manager.get_session_revision(sid) == 0  # 不得推进

        count = await store.get_message_count(sid)
        assert count == 6
        # 在途任务在 trim 之前捕获的 end_index=8，必须被接受并钳制到 6
        committed = await manager.update_session_metadata_if_revision(
            sid, "last_summarized_index", 8, 0
        )
        assert committed is True
        assert await _cursor(manager, sid) == count
    finally:
        await _cleanup(handler, store)


@pytest.mark.asyncio
async def test_trim_keeps_inflight_summary_memory(tmp_path):
    """端到端：trim 与在途总结交错时，刚写入的合法记忆不得被补偿删除。"""
    from unittest.mock import AsyncMock

    engine = AsyncMock()
    engine.add_memory = AsyncMock(return_value=4242)
    engine.delete_memory = AsyncMock(return_value=True)
    handler, manager, store = await _make_real_handler(tmp_path, memory_engine=engine)
    sid = SID_PREFIX + "trim-memory"
    entered = asyncio.Event()
    release = asyncio.Event()
    task = None
    try:
        await _seed_turns(manager, sid, ("C1", "C2", "C3", "C4"))  # 8 条
        await manager.update_session_metadata(sid, "last_summarized_index", 4)

        async def slow_add_memory(**kwargs):
            entered.set()
            await release.wait()
            return 4242

        engine.add_memory = AsyncMock(side_effect=slow_add_memory)
        handler._memory_reflection.memory_processor.process_conversation = AsyncMock(
            return_value=("summary", {"topics": []}, 0.5)
        )
        task = asyncio.create_task(
            handler._memory_reflection._storage_task(
                session_id=sid,
                history_messages=[
                    Message(
                        id=5,
                        session_id=sid,
                        role="user",
                        content="q-C3",
                        sender_id="u1",
                        sender_name="User",
                        group_id=None,
                        platform="test",
                        metadata={},
                    ),
                    Message(
                        id=6,
                        session_id=sid,
                        role="assistant",
                        content="a-C3",
                        sender_id="bot",
                        sender_name="Bot",
                        group_id=None,
                        platform="test",
                        metadata={"is_bot_message": True},
                    ),
                ],
                persona_id="p1",
                start_index=4,
                end_index=8,
                retry_count=0,
                memory_scope="livingmemory:test",
                expected_revision=0,
            )
        )
        await asyncio.wait_for(entered.wait(), 3)
        # 记忆已写入、游标提交之前，发生常规滑动窗口清理
        assert await manager.trim_session_messages(sid, 2) == 2
        release.set()
        await asyncio.wait_for(task, 5)

        # 这次总结的范围没有被撤销 -> 记忆必须保留
        engine.delete_memory.assert_not_awaited()
        count = await store.get_message_count(sid)
        assert await _cursor(manager, sid) == count
    finally:
        release.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await _cleanup(handler, store)


@pytest.mark.asyncio
async def test_cancel_after_memory_write_compensates(tmp_path):
    """取消（回滚会 cancel 任务）发生在记忆写入之后时，仍要补偿删除。"""
    from unittest.mock import AsyncMock

    engine = AsyncMock()
    engine.add_memory = AsyncMock(return_value=4242)
    engine.delete_memory = AsyncMock(return_value=True)
    handler, manager, store = await _make_real_handler(tmp_path, memory_engine=engine)
    sid = SID_PREFIX + "cancel-compensate"
    committing = asyncio.Event()
    original_conditional = manager.update_session_metadata_if_revision
    task = None
    try:
        await _seed_turns(manager, sid, ("C1", "C2"))

        async def slow_conditional(session_id, key, value, expected_revision):
            committing.set()
            await asyncio.sleep(30)  # 停在游标提交处，等待被取消
            return await original_conditional(
                session_id, key, value, expected_revision
            )

        manager.update_session_metadata_if_revision = slow_conditional
        handler._memory_reflection.memory_processor.process_conversation = AsyncMock(
            return_value=("summary", {"topics": []}, 0.5)
        )
        task = asyncio.create_task(
            handler._memory_reflection._storage_task(
                session_id=sid,
                history_messages=[
                    Message(
                        id=1,
                        session_id=sid,
                        role="user",
                        content="q-C1",
                        sender_id="u1",
                        sender_name="User",
                        group_id=None,
                        platform="test",
                        metadata={},
                    ),
                    Message(
                        id=2,
                        session_id=sid,
                        role="assistant",
                        content="a-C1",
                        sender_id="bot",
                        sender_name="Bot",
                        group_id=None,
                        platform="test",
                        metadata={"is_bot_message": True},
                    ),
                ],
                persona_id="p1",
                start_index=0,
                end_index=2,
                retry_count=0,
                memory_scope="livingmemory:test",
                expected_revision=0,
            )
        )
        await asyncio.wait_for(committing.wait(), 3)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        engine.delete_memory.assert_awaited_once_with(4242)
    finally:
        manager.update_session_metadata_if_revision = original_conditional
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await _cleanup(handler, store)


@pytest.mark.asyncio
async def test_rollback_during_memory_write_compensates(tmp_path):
    """回滚发生在记忆写入期间：游标被拒绝，且刚落库的记忆必须被补偿删除。

    修订号校验发生在调用记忆引擎之前，仅靠它无法撤回已经 durable 的内容。
    """
    from unittest.mock import AsyncMock

    engine = AsyncMock()
    engine.add_memory = AsyncMock(return_value=4242)
    engine.delete_memory = AsyncMock(return_value=True)
    handler, manager, store = await _make_real_handler(tmp_path, memory_engine=engine)
    sid = SID_PREFIX + "compensate"
    entered = asyncio.Event()
    release = asyncio.Event()
    task = None
    try:
        await _seed_turns(manager, sid, ("C1", "C2"))

        async def slow_add_memory(**kwargs):
            entered.set()
            await release.wait()
            return 4242

        engine.add_memory = AsyncMock(side_effect=slow_add_memory)
        handler._memory_reflection.memory_processor.process_conversation = AsyncMock(
            return_value=("summary", {"topics": []}, 0.5)
        )
        task = asyncio.create_task(
            handler._memory_reflection._storage_task(
                session_id=sid,
                history_messages=[
                    Message(
                        id=1,
                        session_id=sid,
                        role="user",
                        content="q-C1",
                        sender_id="u1",
                        sender_name="User",
                        group_id=None,
                        platform="test",
                        metadata={},
                    ),
                    Message(
                        id=2,
                        session_id=sid,
                        role="assistant",
                        content="a-C1",
                        sender_id="bot",
                        sender_name="Bot",
                        group_id=None,
                        platform="test",
                        metadata={"is_bot_message": True},
                    ),
                ],
                persona_id="p1",
                start_index=0,
                end_index=4,
                retry_count=0,
                memory_scope="livingmemory:test",
                expected_revision=0,
            )
        )
        await asyncio.wait_for(entered.wait(), 3)
        # 写入期间发生回滚
        assert await manager.reconcile_session_tail(sid, ["C1"]) == 2
        release.set()
        await asyncio.wait_for(task, 5)

        engine.delete_memory.assert_awaited_once_with(4242)
        assert await _cursor(manager, sid) <= await store.get_message_count(sid)
    finally:
        release.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await _cleanup(handler, store)


# ---------------------------------------------------------------- 多种交错顺序
@pytest.mark.asyncio
async def test_interleavings_keep_invariant(tmp_path):
    """两种交错顺序反复执行，cursor <= count 恒成立。

    - 偶数轮：旧写入先通过校验并暂停 -> 回滚完成删除 -> 放行旧写入
      （此时 count 已缩到 2，必须由**写入侧钳制**兜住，即本次新增逻辑）；
    - 奇数轮：回滚先完成 -> 携带旧修订号的写入必须被拒绝，且不留游标。
    """
    handler, manager, store = await _make_real_handler(tmp_path)
    original_update = manager.update_session_metadata
    original_delete = store.delete_messages_from_position
    try:
        for round_index in range(4):
            sid = f"{SID_PREFIX}loop-{round_index}"
            await _seed_turns(manager, sid, ("C1", "C2"))

            if round_index % 2 == 0:
                started = asyncio.Event()
                release = asyncio.Event()
                deleted = asyncio.Event()

                async def delayed_update(session_id, key, value, _started=started):
                    if key == "last_summarized_index" and value == 4:
                        _started.set()
                        await release.wait()
                    await original_update(session_id, key, value)

                async def notify_delete(
                    session_id, position, _deleted=deleted, **kwargs
                ):
                    result = await original_delete(session_id, position, **kwargs)
                    _deleted.set()
                    return result

                manager.update_session_metadata = delayed_update
                store.delete_messages_from_position = notify_delete
                writer = asyncio.create_task(
                    manager.update_session_metadata_if_revision(
                        sid, "last_summarized_index", 4, 0
                    )
                )
                await asyncio.wait_for(started.wait(), 2)
                rollback = asyncio.create_task(
                    manager.reconcile_session_tail(sid, ["C1"])
                )
                await asyncio.wait_for(deleted.wait(), 2)
                release.set()  # 删除已完成（count=2）后才放行旧写入
                committed, removed = await asyncio.wait_for(
                    asyncio.gather(writer, rollback), 3
                )
                assert committed is True
                assert removed == 2
                assert await _cursor(manager, sid) == 2
            else:
                assert await asyncio.wait_for(
                    manager.reconcile_session_tail(sid, ["C1"]), 2
                ) == 2
                committed = await manager.update_session_metadata_if_revision(
                    sid, "last_summarized_index", 4, 0
                )
                assert committed is False
                assert await _cursor(manager, sid) == 0

            count = await store.get_message_count(sid)
            cursor = await manager.get_session_metadata(
                sid, "last_summarized_index", 0
            )
            assert count == 2
            assert cursor <= count, (round_index, cursor, count)
            manager.update_session_metadata = original_update
            store.delete_messages_from_position = original_delete
    finally:
        manager.update_session_metadata = original_update
        store.delete_messages_from_position = original_delete
        await _cleanup(handler, store)
