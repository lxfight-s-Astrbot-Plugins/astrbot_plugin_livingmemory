"""WebUI 编辑/重试对账与反思守卫的黑盒测试。

真实会话库、记忆引擎与事件钩子参与运行；LLM 总结通过边界桩（Provider）
控制返回时机与内容。断言对象是库里最终的消息、游标与记忆，而不是内部调用记录。
"""

import asyncio
import json
import logging
import time
from types import SimpleNamespace

import pytest
import pytest_asyncio

from astrbot.api.provider import LLMResponse
from astrbot.core.message.components import Plain
from astrbot_plugin_livingmemory.core.models.conversation_models import Message

from tests.real_engine_support import (
    HostContext,
    close_handler,
    make_handler,
    make_engine,
)

SESSION_ID = "test:private:sid-1"


class _SummaryProvider:
    """Summary provider double: returns a fixed JSON payload, optionally gated.

    Args:
        importance: Importance reported in the summary payload.
        gate: Optional event the provider waits on before answering.
        error: Optional exception raised instead of answering.
    """

    def __init__(self, *, importance: float = 0.82, gate=None, error=None) -> None:
        self.importance = importance
        self.gate = gate
        self.error = error
        self.calls = 0

    async def text_chat(self, prompt=None, **_kwargs) -> LLMResponse:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        payload = {
            "summary": "用户在讨论跑步习惯与周末安排。",
            "topics": ["health"],
            "key_facts": ["用户开始跑步"],
            "sentiment": "positive",
            "importance": self.importance,
        }
        return LLMResponse(role="assistant", completion_text=json.dumps(payload))


class _Event:
    def __init__(
        self,
        *,
        group: bool = False,
        umo: str = SESSION_ID,
        text: str = "hello",
        checkpoint: str | None = None,
    ) -> None:
        self.unified_msg_origin = umo
        self._group = group
        self.message_str = text
        self._checkpoint = checkpoint
        self.sender_id = "user-1"
        self.sender_name = "Tester"

    def get_message_type(self):
        from astrbot.api.platform import MessageType

        return MessageType.GROUP_MESSAGE if self._group else MessageType.FRIEND_MESSAGE

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
        return "gid-1" if self._group else ""

    def get_extra(self, key, default=None):
        if self._checkpoint is not None:
            return self._checkpoint
        return default


def _req(prompt: str = "hello", *, contexts=None):
    return SimpleNamespace(
        prompt=prompt,
        system_prompt="",
        contexts=list(contexts or []),
        extra_user_content_parts=[],
    )


def _resp(text: str):
    return SimpleNamespace(
        role="assistant",
        completion_text=text,
        is_chunk=False,
        tools_call_name=None,
        tools_call_extra_content=None,
    )


def _checkpoint_contexts(*ids: str):
    return [{"role": "_checkpoint", "content": {"id": item}} for item in ids]


def _now(offset: int = 0) -> float:
    """Past-based timestamps so messages added later sort after seeded rows."""
    return time.time() - 60 + offset


async def _seed(manager, rows) -> None:
    """Seed (role, content, checkpoint) rows into the conversation store."""
    for index, (role, content, checkpoint) in enumerate(rows):
        await manager.add_message(
            session_id=SESSION_ID,
            role=role,
            content=content,
            sender_id="user-1" if role == "user" else "bot-1",
            metadata={"llm_checkpoint_id": checkpoint} if checkpoint else {},
            timestamp=_now(index),
        )


async def _rows(store) -> list[Message]:
    return await store.get_session_messages_asc(SESSION_ID)


def _dump(rows: list[Message]) -> list[tuple[str, str, str | None]]:
    return [
        (row.role, row.content, (row.metadata or {}).get("llm_checkpoint_id"))
        for row in rows
    ]


@pytest_asyncio.fixture
async def bundle(tmp_path):
    items = await make_handler(tmp_path, host=HostContext(persona_id=None))
    try:
        yield items
    finally:
        await close_handler(*items)


# ==================== 编辑/重试对账 ====================


@pytest.mark.asyncio
async def test_edit_and_resend_replaces_the_old_turn(bundle):
    """编辑上一条消息后重发：库内旧轮被清理，只保留编辑后的当前轮。"""
    handler, manager, store, engine, db = bundle
    await _seed(manager, [("user", "q1", "C1"), ("assistant", "a1", "C1")])

    event = _Event(text="q1-edited", checkpoint="C2")
    await handler.handle_memory_recall(event, _req("q1-edited", contexts=[]))

    assert _dump(await _rows(store)) == [("user", "q1-edited", "C2")]
    session = await store.get_session(SESSION_ID)
    assert session.message_count == 1


@pytest.mark.asyncio
async def test_regenerate_drops_the_reverted_tail(bundle):
    """重试上一条对话：被撤销的尾轮删除，头部轮次保留。"""
    handler, manager, store, engine, db = bundle
    await _seed(
        manager,
        [
            ("user", "q1", "C1"),
            ("assistant", "a1", "C1"),
            ("user", "q2", "C2"),
            ("assistant", "a2", "C2"),
        ],
    )

    event = _Event(text="q2", checkpoint="C3")
    await handler.handle_memory_recall(event, _req("q2", contexts=_checkpoint_contexts("C1")))

    assert _dump(await _rows(store)) == [
        ("user", "q1", "C1"),
        ("assistant", "a1", "C1"),
        ("user", "q2", "C3"),
    ]


@pytest.mark.asyncio
async def test_group_events_never_reconcile(bundle):
    """群聊消息不在 LLM 历史中，不能触发对账。"""
    handler, manager, store, engine, db = bundle
    await _seed(manager, [("user", "q1", "C1"), ("assistant", "a1", "C1")])

    event = _Event(group=True, umo="test:group:gid-1", text="hello")
    await handler.handle_memory_recall(event, _req("hello", contexts=[]))

    assert len(await _rows(store)) == 2


@pytest.mark.asyncio
async def test_requests_without_checkpoints_keep_the_history(bundle):
    """非 webchat 请求（无 checkpoint）不做对账，只追加新消息。"""
    handler, manager, store, engine, db = bundle
    await _seed(manager, [("user", "q1", None), ("assistant", "a1", None)])

    await handler.handle_memory_recall(_Event(text="q2"), _req("q2"))

    assert _dump(await _rows(store)) == [
        ("user", "q1", None),
        ("assistant", "a1", None),
        ("user", "q2", None),
    ]


@pytest.mark.asyncio
async def test_image_turn_is_preserved_while_a_later_rollback_is_applied(bundle):
    """纯图片轮（只有 assistant 行）不阻断对账，回滚仍能清理其后的轮次。"""
    handler, manager, store, engine, db = bundle
    await _seed(
        manager,
        [
            ("user", "q1", "C1"),
            ("assistant", "a1", "C1"),
            ("assistant", "a2", "C2"),
            ("user", "q3", "C3"),
            ("assistant", "a3", "C3"),
        ],
    )

    # 正常新请求 C4：LLM 历史与库一致 → 不删
    await handler.handle_memory_recall(
        _Event(text="q4", checkpoint="C4"),
        _req("q4", contexts=_checkpoint_contexts("C1", "C2", "C3")),
    )
    assert [row[0] for row in _dump(await _rows(store))] == [
        "user",
        "assistant",
        "assistant",
        "user",
        "assistant",
        "user",
    ]

    # 回滚 C3：清掉 C3 轮，保留图片轮 C2
    await handler.handle_memory_recall(
        _Event(text="q4-again", checkpoint="C5"),
        _req("q4-again", contexts=_checkpoint_contexts("C1", "C2")),
    )
    assert _dump(await _rows(store)) == [
        ("user", "q1", "C1"),
        ("assistant", "a1", "C1"),
        ("assistant", "a2", "C2"),
        ("user", "q4-again", "C5"),
    ]


# ==================== 反思守卫 ====================


@pytest.mark.asyncio
async def test_late_reply_from_a_replaced_turn_is_dropped(bundle):
    """新轮已开始后，旧轮迟到的回复被丢弃。"""
    handler, manager, store, engine, db = bundle
    await _seed(manager, [("user", "q1", "C1")])

    await handler.handle_memory_reflection(
        _Event(checkpoint="C2"), _resp("old reply")
    )

    assert await store.get_message_count(SESSION_ID) == 1


@pytest.mark.asyncio
async def test_dropped_reply_is_reported_with_both_checkpoints(bundle, caplog):
    """被取代的回复必须留下带 session 与旧→新 checkpoint 的告警。"""
    handler, manager, store, engine, db = bundle
    await _seed(manager, [("user", "q1", "C1")])

    with caplog.at_level(logging.WARNING):
        await handler.handle_memory_reflection(
            _Event(checkpoint="C2"), _resp("old reply")
        )

    assert await store.get_message_count(SESSION_ID) == 1
    warned = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert any(
        SESSION_ID in message and "C1" in message and "C2" in message
        for message in warned
    ), warned


@pytest.mark.asyncio
async def test_matching_checkpoint_reply_is_stored(bundle):
    handler, manager, store, engine, db = bundle
    await _seed(manager, [("user", "q1", "C1")])

    await handler.handle_memory_reflection(
        _Event(checkpoint="C1"), _resp("proper reply")
    )

    assert _dump(await _rows(store)) == [
        ("user", "q1", "C1"),
        ("assistant", "proper reply", "C1"),
    ]


@pytest.mark.asyncio
async def test_image_turn_reply_is_stored(bundle):
    """库尾为 assistant 且 checkpoint 不同（纯图片轮次）时，回复正常入库。"""
    handler, manager, store, engine, db = bundle
    await _seed(manager, [("user", "q1", "C1"), ("assistant", "a1", "C1")])

    await handler.handle_memory_reflection(
        _Event(checkpoint="C2"), _resp("image reply")
    )

    rows = await _rows(store)
    assert len(rows) == 3
    assert rows[-1].metadata["llm_checkpoint_id"] == "C2"


# ==================== 回滚 vs 后台总结 ====================


@pytest.mark.asyncio
async def test_rollback_during_a_slow_summary_discards_the_summary(tmp_path):
    """总结进行中发生编辑/重试：过期内容不得入库，游标也不得推进。"""
    gate = asyncio.Event()
    provider = _SummaryProvider(gate=gate)
    handler, manager, store, engine, db = await make_handler(
        tmp_path, llm_provider=provider, host=HostContext()
    )
    try:
        await _seed(
            manager,
            [
                ("user", "q1", "C1"),
                ("assistant", "a1", "C1"),
                ("user", "q2", "C2"),
                ("assistant", "a2", "C2"),
            ],
        )
        await handler.handle_memory_reflection(
            _Event(checkpoint="C3"), _resp("a3")
        )
        # 后台任务已启动并阻塞在 provider 上；此时用户编辑并重发
        await asyncio.sleep(0.05)
        await handler.handle_memory_recall(
            _Event(text="q2-edited", checkpoint="C4"),
            _req("q2-edited", contexts=_checkpoint_contexts("C1")),
        )
        gate.set()
        await handler.shutdown()

        assert (await engine.get_statistics())["total_memories"] == 0
        assert await manager.get_session_metadata(SESSION_ID, "last_summarized_index", 0) == 0
        assert await manager.get_session_metadata(SESSION_ID, "pending_summary", None) is None
        # 回滚本身生效：库内只剩 C1 轮与编辑后的新轮
        assert [row[2] for row in _dump(await _rows(store))] == ["C1", "C1", "C4"]
    finally:
        await close_handler(handler, manager, store, engine, db)


@pytest.mark.asyncio
async def test_failed_summary_after_rollback_records_no_pending_retry(tmp_path):
    """LLM 失败叠加回滚：不得把已撤销范围记成待重试。"""
    gate = asyncio.Event()
    provider = _SummaryProvider(gate=gate, error=RuntimeError("llm down"))
    handler, manager, store, engine, db = await make_handler(
        tmp_path, llm_provider=provider, host=HostContext()
    )
    try:
        await _seed(
            manager,
            [
                ("user", "q1", "C1"),
                ("assistant", "a1", "C1"),
                ("user", "q2", "C2"),
                ("assistant", "a2", "C2"),
            ],
        )
        await handler.handle_memory_reflection(_Event(checkpoint="C3"), _resp("a3"))
        await asyncio.sleep(0.05)
        await handler.handle_memory_recall(
            _Event(text="q2-edited", checkpoint="C4"),
            _req("q2-edited", contexts=_checkpoint_contexts("C1")),
        )
        gate.set()
        await handler.shutdown()

        assert await manager.get_session_metadata(SESSION_ID, "pending_summary", None) is None
        assert await manager.get_session_metadata(SESSION_ID, "last_summarized_index", 0) == 0
        assert (await engine.get_statistics())["total_memories"] == 0
    finally:
        await close_handler(handler, manager, store, engine, db)


# ==================== 游标与修订号（ConversationManager 公开 API） ====================


@pytest.mark.asyncio
async def test_summary_cursor_commit_is_rejected_after_a_rollback(bundle):
    handler, manager, store, engine, db = bundle
    await _seed(manager, [("user", "q1", "C1"), ("assistant", "a1", "C1")])
    messages = await manager.get_messages_range(SESSION_ID, 0, 2)

    await manager.bump_session_revision(SESSION_ID)

    assert (
        await manager.commit_summary_cursor(SESSION_ID, messages[-1], 0) is False
    )
    assert (
        await manager.get_session_metadata(SESSION_ID, "last_summarized_index", 0)
        == 0
    )


@pytest.mark.asyncio
async def test_checked_metadata_write_rejects_a_stale_revision(bundle):
    handler, manager, store, engine, db = bundle
    await _seed(manager, [("user", "q1", "C1"), ("assistant", "a1", "C1")])

    assert (
        await manager.update_session_metadata_if_revision(
            SESSION_ID, "last_summarized_index", 2, 0
        )
        is True
    )

    await manager.bump_session_revision(SESSION_ID)

    assert (
        await manager.update_session_metadata_if_revision(
            SESSION_ID, "last_summarized_index", 9, 0
        )
        is False
    )
    assert (
        await manager.get_session_metadata(SESSION_ID, "last_summarized_index", 0)
        == 2
    )
