"""用户档案注入的黑盒测试：与记忆同一临时块、预算、开关与降级。

真实引擎（含 ProfileManager）、真实会话库与公开召回钩子参与运行；
断言对象是最终注入文本。
"""

import time
from types import SimpleNamespace

import pytest
import pytest_asyncio

from astrbot.core.message.components import Plain
from astrbot_plugin_livingmemory.core.utils import (
    attach_profile_to_memory_injection,
    format_profile_block_for_injection,
    format_profile_for_injection,
)

from tests.real_engine_support import HostContext, close_handler, make_handler

SCOPE = "test:private:sid-1"
PERSONA = "persona_1"


class _Event:
    unified_msg_origin = SCOPE
    message_str = "今天做什么"
    sender_id = "user-1"
    sender_name = "Tester"

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


def _req(query: str = "今天做什么"):
    return SimpleNamespace(
        prompt=query,
        system_prompt="",
        contexts=[],
        extra_user_content_parts=[],
    )


async def _seed_profile(engine) -> None:
    memory_id = await engine.add_memory(
        content="用户喜欢燕麦拿铁",
        session_id=SCOPE,
        persona_id=PERSONA,
        importance=0.8,
    )
    await engine.profile_manager.apply_updates(
        SCOPE,
        PERSONA,
        [
            {
                "section": "basic",
                "field": "称呼",
                "value": "小林",
                "operation": "set",
                "evidence": [memory_id],
                "confidence": 0.9,
            },
            {
                "section": "preferences",
                "field": "饮品",
                "value": "燕麦拿铁",
                "operation": "set",
                "evidence": [memory_id],
                "confidence": 0.9,
            },
        ],
        [memory_id],
    )


def _injected(req) -> str:
    return "".join(getattr(part, "text", "") for part in req.extra_user_content_parts)


async def _recall(handler, query: str = "今天做什么"):
    req = _req(query)
    await handler.handle_memory_recall(_Event(), req)
    return req


@pytest_asyncio.fixture
async def profile_bundle(tmp_path):
    items = await make_handler(
        tmp_path,
        config={
            "recall_engine": {"top_k": 5, "min_similarity_for_retrieval": 0.0},
            "profile_settings": {"enabled": True, "max_chars": 300},
        },
        host=HostContext(persona_id=PERSONA),
    )
    try:
        await _seed_profile(items[3])
        yield items
    finally:
        await close_handler(*items)


@pytest.mark.asyncio
async def test_profile_and_memories_share_one_temporary_block(profile_bundle):
    """档案与记忆进入同一个临时片段，便于统一清理。"""
    handler, manager, store, engine, db = profile_bundle
    await engine.add_memory(
        "用户周末去爬山", session_id=SCOPE, persona_id=PERSONA, importance=0.8
    )

    req = await _recall(handler, "爬山")

    injected = _injected(req)
    assert "小林" in injected
    assert "燕麦拿铁" in injected
    assert "爬山" in injected
    assert len(req.extra_user_content_parts) == 1
    assert getattr(req.extra_user_content_parts[0], "_no_save", False) is True


@pytest.mark.asyncio
async def test_profile_only_injection_is_still_cleanable(profile_bundle):
    """没有命中记忆时仍有档案块，并且同样带可清理标记。"""
    handler, manager, store, engine, db = profile_bundle

    req = await _recall(handler, "完全无关的查询词")

    injected = _injected(req)
    assert "小林" in injected
    from astrbot_plugin_livingmemory.core.base.constants import (
        MEMORY_INJECTION_HEADER,
    )

    assert MEMORY_INJECTION_HEADER in injected


@pytest.mark.asyncio
async def test_disabled_profile_settings_skip_the_profile(tmp_path):
    handler, manager, store, engine, db = await make_handler(
        tmp_path,
        config={
            "recall_engine": {"top_k": 5, "min_similarity_for_retrieval": 0.0},
            "profile_settings": {"enabled": False},
        },
        host=HostContext(persona_id=PERSONA),
    )
    try:
        await _seed_profile(engine)

        req = await _recall(handler)

        assert "小林" not in _injected(req)
    finally:
        await close_handler(handler, manager, store, engine, db)


@pytest.mark.asyncio
async def test_profile_text_is_capped_by_the_configured_budget(tmp_path):
    """档案注入受 max_chars 限制，不会挤占记忆预算。"""
    handler, manager, store, engine, db = await make_handler(
        tmp_path,
        config={
            "recall_engine": {"top_k": 5, "min_similarity_for_retrieval": 0.0},
            "profile_settings": {"enabled": True, "max_chars": 50},
        },
        host=HostContext(persona_id=PERSONA),
    )
    try:
        memory_id = await engine.add_memory(
            "用户喜欢燕麦拿铁", session_id=SCOPE, persona_id=PERSONA
        )
        await engine.profile_manager.apply_updates(
            SCOPE,
            PERSONA,
            [
                {
                    "section": "basic",
                    "field": "备注",
                    "value": "很长的档案内容" * 20,
                    "operation": "set",
                    "evidence": [memory_id],
                    "confidence": 0.9,
                }
            ],
            [memory_id],
        )

        req = await _recall(handler)

        injected = _injected(req)
        assert "很长的档案内容" in injected
        # 50 字符预算内被截断，不会注入全部重复内容
        assert injected.count("很长的档案内容") < 20
    finally:
        await close_handler(handler, manager, store, engine, db)


@pytest.mark.asyncio
async def test_recall_without_profile_still_injects_memories(tmp_path):
    handler, manager, store, engine, db = await make_handler(
        tmp_path,
        config={"recall_engine": {"min_similarity_for_retrieval": 0.0}},
        host=HostContext(persona_id=PERSONA),
    )
    try:
        await engine.add_memory(
            "用户周末去爬山", session_id=SCOPE, persona_id=PERSONA
        )

        req = await _recall(handler, "爬山")

        assert "爬山" in _injected(req)
    finally:
        await close_handler(handler, manager, store, engine, db)


# ---------------- 纯函数：注入块拼接与清理 ----------------


def test_format_profile_for_injection_is_compact_and_safe():
    formatted = format_profile_for_injection(
        {"basic": {"称呼": "小林"}, "preferences": {"饮品": "燕麦拿铁"}},
        max_chars=300,
    )

    assert "小林" in formatted
    assert "燕麦拿铁" in formatted
    assert len(formatted) <= 300


def test_profile_block_helpers_handle_empty_and_foreign_blocks():
    """空档案不产生块；外来文本不会被误认成档案块。"""
    assert format_profile_block_for_injection("") == ""
    assert "档案" not in format_profile_block_for_injection("普通的记忆注入")

    combined = attach_profile_to_memory_injection("记忆内容", "档案内容")
    assert "记忆内容" in combined
    assert "档案内容" in combined
