"""上下文扩展与废弃注入方式回退的黑盒测试。

真实引擎与真实会话库参与运行；断言注入内容是否包含由历史消息扩展出的
检索结果，而不是内部查询字符串。
"""

import time
from types import SimpleNamespace

import pytest
import pytest_asyncio

from astrbot.core.message.components import Plain

from tests.real_engine_support import HostContext, close_handler, make_handler

SCOPE = "test:private:sid-1"


class _Event:
    def __init__(self, text: str = "当前用户消息") -> None:
        self.unified_msg_origin = SCOPE
        self.message_str = text
        self.sender_id = "user-1"
        self.sender_name = "Tester"

    def get_message_type(self):
        from astrbot.api.platform import MessageType

        return MessageType.FRIEND_MESSAGE

    def get_sender_id(self):
        return self.sender_id

    def get_self_id(self):
        return "bot-1"

    def get_sender_name(self):
        return self.sender_name

    def get_message_str(self):
        return self.message_str

    def get_messages(self):
        return [Plain(self.message_str)]

    def get_platform_name(self):
        return "test"

    def get_platform_id(self):
        return "test-instance"

    def get_group_id(self):
        return ""

    def get_extra(self, key, default=None):
        return default


def _req(query: str):
    return SimpleNamespace(
        prompt=query,
        system_prompt="",
        contexts=[],
        extra_user_content_parts=[],
    )


def _injected(req) -> str:
    return "".join(getattr(part, "text", "") for part in req.extra_user_content_parts)


async def _seed_turn(manager, content: str, *, age_seconds: float) -> None:
    await manager.add_message(
        session_id=SCOPE,
        role="user",
        content=content,
        sender_id="user-1",
        timestamp=time.time() - age_seconds,
    )


@pytest.mark.asyncio
async def test_recent_context_expands_the_query(tmp_path):
    """开启上下文扩展后，上一轮对话的内容可以召回相关记忆。"""
    handler, manager, store, engine, db = await make_handler(
        tmp_path,
        config={
            "recall_engine": {
                "inject_with_recent_context": True,
                "recent_context_max_age_seconds": 7200,
                "min_similarity_for_retrieval": 0.0,
            }
        },
        host=HostContext(persona_id=None),
    )
    try:
        await engine.add_memory(
            "用户之前在讨论海边的书", session_id=SCOPE, persona_id=None
        )
        await _seed_turn(manager, "我周末在读海边的书", age_seconds=60)

        req = _req("这个怎么样")
        await handler.handle_memory_recall(_Event("这个怎么样"), req)

        # 当前查询「这个怎么样」本身不含关键词，命中说明历史被并入查询
        assert "海边的书" in _injected(req)
    finally:
        await close_handler(handler, manager, store, engine, db)


@pytest.mark.asyncio
async def test_stale_history_is_not_used_for_expansion(tmp_path):
    """超过 ``recent_context_max_age_seconds`` 的历史不参与查询扩展。"""
    handler, manager, store, engine, db = await make_handler(
        tmp_path,
        config={
            "recall_engine": {
                "inject_with_recent_context": True,
                "recent_context_max_age_seconds": 60,
                "min_similarity_for_retrieval": 0.99,
            }
        },
        host=HostContext(persona_id=None),
    )
    try:
        await engine.add_memory(
            "用户之前在讨论海边的书", session_id=SCOPE, persona_id=None
        )
        await _seed_turn(manager, "我周末在读海边的书", age_seconds=24 * 3600)

        req = _req("这个怎么样")
        await handler.handle_memory_recall(_Event("这个怎么样"), req)

        assert "海边的书" not in _injected(req)
    finally:
        await close_handler(handler, manager, store, engine, db)


@pytest.mark.asyncio
async def test_expansion_is_skipped_when_there_is_no_history(tmp_path):
    """没有历史消息时仍能按当前查询正常召回。"""
    handler, manager, store, engine, db = await make_handler(
        tmp_path,
        config={
            "recall_engine": {
                "inject_with_recent_context": True,
                "min_similarity_for_retrieval": 0.0,
            }
        },
        host=HostContext(persona_id=None),
    )
    try:
        await engine.add_memory(
            "用户周末在爬山", session_id=SCOPE, persona_id=None
        )

        req = _req("爬山")
        await handler.handle_memory_recall(_Event("爬山"), req)

        assert "爬山" in _injected(req)
    finally:
        await close_handler(handler, manager, store, engine, db)


@pytest.mark.asyncio
async def test_disabled_expansion_keeps_the_current_query(tmp_path):
    """关闭扩展时只按当前消息检索。"""
    handler, manager, store, engine, db = await make_handler(
        tmp_path,
        config={
            "recall_engine": {
                "inject_with_recent_context": False,
                "min_similarity_for_retrieval": 0.99,
            }
        },
        host=HostContext(persona_id=None),
    )
    try:
        await engine.add_memory(
            "用户之前在讨论海边的书", session_id=SCOPE, persona_id=None
        )
        await _seed_turn(manager, "我周末在读海边的书", age_seconds=60)

        req = _req("这个怎么样")
        await handler.handle_memory_recall(_Event("这个怎么样"), req)

        assert "海边的书" not in _injected(req)
    finally:
        await close_handler(handler, manager, store, engine, db)


@pytest.mark.asyncio
async def test_system_prompt_injection_mode_falls_back_to_temporary_parts(tmp_path):
    """``injection_method=system_prompt`` 已废弃，回退为临时片段且不改写 prompt。"""
    handler, manager, store, engine, db = await make_handler(
        tmp_path,
        config={
            "recall_engine": {
                "injection_method": "system_prompt",
                "min_similarity_for_retrieval": 0.0,
            }
        },
        host=HostContext(persona_id=None),
    )
    try:
        await engine.add_memory(
            "用户喜欢燕麦拿铁", session_id=SCOPE, persona_id=None
        )

        event = _Event("燕麦拿铁")
        req = _req("燕麦拿铁")
        await handler.handle_memory_recall(event, req)

        assert req.system_prompt == ""
        assert req.prompt == "燕麦拿铁"
        assert "燕麦拿铁" in _injected(req)
    finally:
        await close_handler(handler, manager, store, engine, db)
