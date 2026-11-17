"""事件处理器黑盒测试：消息捕获、召回注入与存储。

真实的 ``EventHandler``、记忆引擎（SQLite + FAISS）、会话库与记忆处理器都参与
运行；只有 AstrBot 宿主（人格查找）与 LLM Provider（外部服务）使用桩。
断言对象是注入内容、会话库里的消息和记忆库里的数据。
"""

import json
import time
from types import SimpleNamespace

import pytest
import pytest_asyncio

from astrbot.core.message.components import Plain

from tests.real_engine_support import HostContext, close_handler, make_handler

SESSION_ID = "test:private:sid-1"
GROUP_SESSION = "test:group:g1"


class _Event:
    def __init__(
        self,
        *,
        group: bool = False,
        umo: str = SESSION_ID,
        message_str: str = "hello",
        checkpoint: str | None = None,
    ) -> None:
        self.unified_msg_origin = umo
        self._group = group
        self.message_str = message_str
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
        return "g1" if self._group else ""

    def get_extra(self, key, default=None):
        if self._checkpoint is not None:
            return self._checkpoint
        return default


def _now(offset: int = 0) -> float:
    """Realistic timestamps: Windows rejects epoch-adjacent values in astimezone."""
    return time.time() + offset


def _req(prompt: str = "hello", extra_parts=None):
    return SimpleNamespace(
        prompt=prompt,
        system_prompt="",
        contexts=[],
        extra_user_content_parts=list(extra_parts or []),
    )


def _resp(text: str):
    return SimpleNamespace(
        role="assistant",
        completion_text=text,
        is_chunk=False,
        tools_call_name=None,
        tools_call_extra_content=None,
    )


@pytest_asyncio.fixture
async def handler_bundle(tmp_path):
    bundle = await make_handler(tmp_path, host=HostContext(persona_id=None))
    try:
        yield bundle
    finally:
        await close_handler(*bundle)


async def _stored_messages(manager, session_id: str):
    return await manager.get_context(session_id, max_messages=100, format_for_llm=False)


def _text(message) -> str:
    return message.get("content") if isinstance(message, dict) else message.content


def _role(message) -> str:
    return message.get("role") if isinstance(message, dict) else message.role


def _injected(req) -> str:
    return "".join(getattr(part, "text", "") for part in req.extra_user_content_parts)


@pytest.mark.asyncio
async def test_message_dedup_cache_works(handler_bundle):
    handler, *_ = handler_bundle
    key = "id:123"

    assert await handler._message_utils.is_duplicate_message(key) is False
    await handler._message_utils.mark_message_processed(key)
    assert await handler._message_utils.is_duplicate_message(key) is True


@pytest.mark.asyncio
async def test_recall_appends_memories_as_temporary_parts(handler_bundle):
    handler, manager, store, engine, db = handler_bundle
    await engine.add_memory(
        "User keeps a spare key with Xiao Ning", session_id=SESSION_ID, persona_id=None
    )
    event = _Event(message_str="where is my spare key")
    req = _req("where is my spare key")

    await handler.handle_memory_recall(event, req)

    injected = _injected(req)
    assert "spare key" in injected
    assert "<RAG-Faiss-Memory>" in injected
    assert all(getattr(part, "_no_save", False) for part in req.extra_user_content_parts)
    # 注入不得改写 prompt 或 system_prompt
    assert req.prompt == "where is my spare key"
    assert req.system_prompt == ""


@pytest.mark.asyncio
async def test_private_message_is_captured_for_later_summaries(handler_bundle):
    handler, manager, store, engine, db = handler_bundle
    event = _Event(message_str="remember that I like jasmine tea")

    await handler.handle_memory_recall(event, _req("remember that I like jasmine tea"))

    texts = [_text(m) for m in await _stored_messages(manager, SESSION_ID)]
    assert "remember that I like jasmine tea" in texts


@pytest.mark.asyncio
async def test_reflection_turns_a_conversation_into_a_stored_memory(handler_bundle):
    handler, manager, store, engine, db = handler_bundle
    for index in range(2):
        await manager.add_message(
            session_id=SESSION_ID,
            role="user" if index == 0 else "assistant",
            content="I started running this week",
            sender_id="user-1",
            timestamp=_now(),
        )

    await handler.handle_memory_reflection(_Event(), _resp("Great, keep it up!"))
    await handler.shutdown()

    stats = await engine.get_statistics()
    assert stats["total_memories"] == 1


@pytest.mark.asyncio
async def test_group_messages_are_captured_without_summarising(handler_bundle):
    handler, manager, store, engine, db = handler_bundle
    event = _Event(group=True, umo=GROUP_SESSION, message_str="group chatter")

    await handler.handle_all_group_messages(event)

    texts = [_text(m) for m in await _stored_messages(manager, GROUP_SESSION)]
    assert "group chatter" in texts
    assert (await engine.get_statistics())["total_memories"] == 0


@pytest.mark.asyncio
async def test_group_capture_ignores_the_bot_own_messages(handler_bundle):
    handler, manager, store, engine, db = handler_bundle
    event = _Event(group=True, umo=GROUP_SESSION, message_str="I am the bot")
    event.sender_id = "bot-1"

    await handler.handle_all_group_messages(event)

    assert await _stored_messages(manager, GROUP_SESSION) == []


@pytest.mark.asyncio
async def test_message_limit_keeps_at_most_the_configured_window(tmp_path):
    config = {
        "session_manager": {"max_messages_per_session": 100, "cleanup_batch_size": 20}
    }
    handler, manager, store, engine, db = await make_handler(tmp_path, config=config)
    try:
        for index in range(150):
            await manager.add_message(
                session_id=SESSION_ID,
                role="user",
                content=f"msg-{index}",
                sender_id="user-1",
                timestamp=_now(),
            )

        await handler._message_utils.enforce_message_limit(SESSION_ID)

        info = await store.get_session(SESSION_ID)
        assert info.message_count <= 100
        remaining = [_text(m) for m in await _stored_messages(manager, SESSION_ID)]
        # 保留最近的消息，最旧的被裁掉
        assert "msg-149" in remaining
        assert "msg-0" not in remaining
    finally:
        await close_handler(handler, manager, store, engine, db)


@pytest.mark.asyncio
async def test_recall_is_skipped_without_any_user_content(handler_bundle):
    handler, manager, store, engine, db = handler_bundle
    await engine.add_memory("User keeps a spare key", session_id=SESSION_ID, persona_id=None)
    event = _Event(message_str="")
    req = _req(prompt="")

    await handler.handle_memory_recall(event, req)

    assert req.extra_user_content_parts == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "legacy_method", ["user_message_before", "user_message_after"]
)
async def test_deprecated_injection_modes_fall_back_to_extra_parts(
    tmp_path, legacy_method
):
    config = {"recall_engine": {"injection_method": legacy_method}}
    handler, manager, store, engine, db = await make_handler(tmp_path, config=config)
    try:
        await engine.add_memory(
            "User keeps a spare key", session_id=SESSION_ID, persona_id=None
        )
        event = _Event(message_str="user question")
        req = _req("user question")

        await handler.handle_memory_recall(event, req)

        # 废弃模式不改写 prompt，仍以临时片段追加
        assert req.prompt == "user question"
        assert "spare key" in _injected(req)
        assert all(
            getattr(part, "_no_save", False)
            for part in req.extra_user_content_parts
        )
    finally:
        await close_handler(handler, manager, store, engine, db)


@pytest.mark.asyncio
async def test_stored_memory_records_its_source_window(handler_bundle):
    handler, manager, store, engine, db = handler_bundle
    for index in range(2):
        await manager.add_message(
            session_id=SESSION_ID,
            role="user" if index == 0 else "assistant",
            content=f"turn-{index}",
            sender_id="user-1",
            timestamp=_now(),
        )

    await handler.handle_memory_reflection(_Event(), _resp("assistant reply"))
    await handler.shutdown()

    records = await engine.list_memories_page(page_size=10)
    metadata = records["items"][0]["metadata"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    window = metadata["source_window"]
    assert window["session_id"] == SESSION_ID
    # 窗口覆盖本次未总结的消息（含刚写入的助手回复），至少两条
    assert window["message_count"] >= 2
    assert window["end_index"] - window["start_index"] == window["message_count"]
    assert metadata["source_session_id"] == SESSION_ID


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("threshold", "expect_source"),
    [(0.5, True), (0.95, False)],
    ids=["above-threshold-keeps-source", "below-threshold-drops-source"],
)
async def test_source_messages_are_kept_only_above_the_importance_threshold(
    tmp_path, threshold, expect_source
):
    """总结重要性达到阈值时才保留原文，否则只存摘要（控制存储与隐私）。"""
    config = {"reflection_engine": {"source_retention_importance_threshold": threshold}}
    handler, manager, store, engine, db = await make_handler(tmp_path, config=config)
    try:
        for index in range(2):
            await manager.add_message(
                session_id=SESSION_ID,
                role="user" if index == 0 else "assistant",
                content=f"turn-{index}",
                sender_id="user-1",
                timestamp=_now(index),
            )

        await handler.handle_memory_reflection(_Event(), _resp("assistant reply"))
        await handler.shutdown()

        page = await engine.list_memories_page(page_size=10)
        memory_id = page["items"][0]["id"]
        source = await engine.get_memory_source(memory_id)
        if expect_source:
            contents = [item["content"] for item in source]
            # 原文包含本次总结窗口内的用户消息与助手回复
            assert {"turn-0", "turn-1", "assistant reply"} <= set(contents)
        else:
            assert not source
    finally:
        await close_handler(handler, manager, store, engine, db)


@pytest.mark.asyncio
async def test_already_summarised_ranges_are_not_summarised_again(handler_bundle):
    handler, manager, store, engine, db = handler_bundle
    await manager.add_message(
        session_id=SESSION_ID, role="user", content="old", sender_id="user-1"
    )
    await manager.update_session_metadata(SESSION_ID, "last_summarized_index", 1)

    await handler.handle_memory_reflection(_Event(), _resp("reply"))
    await handler.shutdown()

    assert (await engine.get_statistics())["total_memories"] == 0


@pytest.mark.asyncio
async def test_recall_uses_the_event_message_text_as_query(handler_bundle):
    handler, manager, store, engine, db = handler_bundle
    await engine.add_memory(
        "User asked about sourdough starters",
        session_id=SESSION_ID,
        persona_id=None,
    )
    event = _Event(message_str="sourdough starters")
    req = _req("sourdough starters")

    await handler.handle_memory_recall(event, req)

    assert "sourdough" in _injected(req)


@pytest.mark.asyncio
async def test_recall_runs_when_only_extra_content_parts_carry_text(handler_bundle):
    handler, manager, store, engine, db = handler_bundle
    await engine.add_memory(
        "User described a cat photo", session_id=SESSION_ID, persona_id=None
    )
    event = _Event(message_str="describe this photo")
    image_part = SimpleNamespace(text="<image_caption>cat</image_caption>")
    req = _req(prompt="", extra_parts=[image_part])

    await handler.handle_memory_recall(event, req)

    injected = _injected(req)
    assert "<image_caption>cat</image_caption>" in injected
    assert "cat photo" in injected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    ["api error: rate limit exceeded", ""],
    ids=["error-response", "empty-response"],
)
async def test_reflection_skips_unusable_responses(handler_bundle, text):
    handler, manager, store, engine, db = handler_bundle
    for index in range(2):
        await manager.add_message(
            session_id=SESSION_ID,
            role="user" if index == 0 else "assistant",
            content="turn",
            sender_id="user-1",
            timestamp=_now(),
        )

    await handler.handle_memory_reflection(_Event(), _resp(text))
    await handler.shutdown()

    assert (await engine.get_statistics())["total_memories"] == 0


@pytest.mark.asyncio
async def test_failed_summaries_are_abandoned_after_three_retries(handler_bundle):
    handler, manager, store, engine, db = handler_bundle
    for index in range(4):
        await manager.add_message(
            session_id=SESSION_ID,
            role="user" if index % 2 == 0 else "assistant",
            content=f"turn-{index}",
            sender_id="user-1",
            timestamp=_now(),
        )
    await manager.update_session_metadata(
        SESSION_ID,
        "pending_summary",
        {"start_index": 0, "end_index": 4, "retry_count": 3},
    )

    await handler.handle_memory_reflection(_Event(), _resp("assistant reply"))
    await handler.shutdown()

    metadata = await manager.get_session_metadata(SESSION_ID, "pending_summary", None)
    assert metadata is None
    # 放弃的范围不写入记忆
    assert (await engine.get_statistics())["total_memories"] == 0


@pytest.mark.asyncio
async def test_shutdown_marks_the_handler_as_stopped(handler_bundle):
    handler, *_ = handler_bundle

    await handler.shutdown()

    assert handler._shutting_down is True
