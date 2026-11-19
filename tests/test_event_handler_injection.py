"""注入方式兼容与 ``top_k=0`` 回归的黑盒测试。

全部通过公开钩子 ``handle_memory_recall`` 驱动，使用真实的会话库与记忆引擎；
断言对象是请求对象最终的状态（注入片段、system_prompt、contexts）与会话库里
的消息，而不是内部方法调用记录。
"""

import time
from types import SimpleNamespace

import pytest
import pytest_asyncio

from astrbot.core.agent.message import TextPart
from astrbot.core.message.components import Plain
from astrbot_plugin_livingmemory.core.base.constants import (
    MEMORY_INJECTION_FOOTER,
    MEMORY_INJECTION_HEADER,
)

from tests.real_engine_support import HostContext, close_handler, make_handler

SESSION_ID = "test:private:sid-1"
GROUP_SESSION = "test:group:g1"
FAKE_CALL_ID = "fake_recall_abc123def456"


class _Event:
    def __init__(self, *, group: bool = False, umo: str = SESSION_ID, text: str = "hello"):
        self.unified_msg_origin = umo
        self._group = group
        self.message_str = text
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
        return default


def _req(prompt: str = "hello", *, contexts=None, parts=None):
    return SimpleNamespace(
        prompt=prompt,
        system_prompt="",
        contexts=list(contexts or []),
        extra_user_content_parts=list(parts or []),
    )


def _injected(req) -> str:
    return "".join(getattr(part, "text", "") for part in req.extra_user_content_parts)


async def _stored(manager, session_id: str) -> list[str]:
    messages = await manager.get_context(
        session_id, max_messages=50, format_for_llm=False
    )
    return [
        message.get("content") if isinstance(message, dict) else message.content
        for message in messages
    ]


@pytest_asyncio.fixture
async def bundle(tmp_path):
    items = await make_handler(tmp_path, host=HostContext(persona_id=None))
    try:
        yield items
    finally:
        await close_handler(*items)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "legacy_mode",
    [
        "fake_tool_call",
        "fake_tool_call_deepseek_v4",
        "user_message_before",
        "user_message_after",
    ],
)
async def test_deprecated_injection_modes_revert_to_temporary_parts(
    tmp_path, legacy_mode
):
    """已废弃的注入方式不再改写历史，只以 ``_no_save`` 临时片段追加记忆。"""
    config = {"recall_engine": {"injection_method": legacy_mode}}
    handler, manager, store, engine, db = await make_handler(tmp_path, config=config)
    try:
        await engine.add_memory(
            "用户喜欢吃火锅", session_id=SESSION_ID, persona_id=None
        )
        event = _Event(text="今天吃什么")
        req = _req("今天吃什么")
        original_contexts = list(req.contexts)

        await handler.handle_memory_recall(event, req)

        assert "火锅" in _injected(req)
        assert all(
            getattr(part, "_no_save", False) for part in req.extra_user_content_parts
        )
        # 历史与 system_prompt 不被改写
        assert req.contexts == original_contexts
        assert req.prompt == "今天吃什么"
        assert req.system_prompt == ""
    finally:
        await close_handler(handler, manager, store, engine, db)


@pytest.mark.asyncio
async def test_legacy_fake_tool_call_pairs_are_removed_from_history(bundle):
    """旧版本写入历史的伪工具调用消息对，会在下一次召回时被清理。"""
    handler, manager, store, engine, db = bundle
    real_pair = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_real_abc123",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city": "上海"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_real_abc123",
            "content": '{"temperature": 25}',
        },
    ]
    fake_pair = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": FAKE_CALL_ID,
                    "type": "function",
                    "function": {
                        "name": "recall_long_term_memory",
                        "arguments": '{"query": "test", "k": 5}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": FAKE_CALL_ID,
            "content": '{"query": "test", "count": 1, "results": []}',
        },
    ]
    req = _req(
        "hello",
        contexts=[
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好啊"},
            *fake_pair,
            *real_pair,
            {"role": "user", "content": "最近怎么样"},
        ],
    )

    await handler.handle_memory_recall(_Event(), req)

    # 伪造消息对被移除，正常消息与真实工具调用保留
    assert FAKE_CALL_ID not in str(req.contexts)
    assert "call_real_abc123" in str(req.contexts)
    contents = [item["content"] for item in req.contexts if item["content"]]
    assert contents[:2] == ["你好", "你好啊"]
    assert contents[-1] == "最近怎么样"
    # 真实的工具结果仍在（说明只删了伪造的那一对）
    assert '{"temperature": 25}' in contents


@pytest.mark.asyncio
async def test_legacy_injected_system_prompt_block_is_cleaned(bundle):
    """旧版本写进 system_prompt 的记忆块会在下一次召回时被移除。"""
    handler, manager, store, engine, db = bundle
    req = _req("test query")
    req.system_prompt = (
        f"你是助手。\n{MEMORY_INJECTION_HEADER}\n旧记忆内容\n{MEMORY_INJECTION_FOOTER}\n请回答。"
    )

    await handler.handle_memory_recall(_Event(), req)

    assert MEMORY_INJECTION_HEADER not in req.system_prompt
    assert MEMORY_INJECTION_FOOTER not in req.system_prompt
    assert req.system_prompt == "你是助手。\n\n请回答。"


@pytest.mark.asyncio
async def test_only_livingmemory_temporary_parts_are_removed(bundle):
    """上一轮注入的临时片段被清掉，其它临时片段（如图片转述）保持原样。"""
    handler, manager, store, engine, db = bundle
    memory_part = TextPart(
        text=f"{MEMORY_INJECTION_HEADER}\n旧记忆内容\n{MEMORY_INJECTION_FOOTER}"
    ).mark_as_temp()
    image_part = TextPart(text="<image_caption>cat</image_caption>").mark_as_temp()
    req = _req("test query", parts=[memory_part, image_part])

    await handler.handle_memory_recall(_Event(), req)

    texts = [part.text for part in req.extra_user_content_parts]
    assert "<image_caption>cat</image_caption>" in texts
    assert all(MEMORY_INJECTION_HEADER not in text for text in texts)


@pytest.mark.asyncio
async def test_history_content_parts_are_left_untouched(bundle):
    """插件不改写历史消息：纯文本与多模态 parts 原样保留。"""
    handler, manager, store, engine, db = bundle
    text_only = [
        {"type": "text", "text": "hello"},
        {"type": "text", "text": "\nworld"},
    ]
    multimodal = [
        {"type": "text", "text": "look"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
    ]
    req = _req("test query", contexts=[
        {"role": "user", "content": text_only},
        {"role": "user", "content": multimodal},
    ])

    await handler.handle_memory_recall(_Event(), req)

    assert req.contexts[0]["content"] is text_only
    assert req.contexts[1]["content"] is multimodal


@pytest_asyncio.fixture
async def no_recall_bundle(tmp_path):
    items = await make_handler(
        tmp_path, config={"recall_engine": {"top_k": 0}}, host=HostContext()
    )
    try:
        yield items
    finally:
        await close_handler(*items)


@pytest.mark.asyncio
async def test_top_k_zero_disables_injection_but_keeps_capture(no_recall_bundle):
    """top_k=0 关闭召回注入，私聊消息仍会入库供后续总结。"""
    handler, manager, store, engine, db = no_recall_bundle
    await engine.add_memory("用户喜欢吃火锅", session_id=SESSION_ID, persona_id=None)
    event = _Event(text="private message")
    req = _req("private message")

    await handler.handle_memory_recall(event, req)

    assert req.extra_user_content_parts == []
    assert req.prompt == "private message"
    assert "private message" in await _stored(manager, SESSION_ID)
    assert (await engine.get_statistics())["total_memories"] == 1


@pytest.mark.asyncio
async def test_top_k_zero_does_not_capture_group_messages(no_recall_bundle):
    """群聊消息由群聊捕获处理，召回钩子不重复写入。"""
    handler, manager, store, engine, db = no_recall_bundle
    event = _Event(group=True, umo=GROUP_SESSION, text="group message")

    await handler.handle_memory_recall(event, _req("group message"))

    assert await _stored(manager, GROUP_SESSION) == []


@pytest.mark.asyncio
async def test_top_k_zero_still_cleans_previous_injections(no_recall_bundle):
    """即使关闭召回，也要清掉上一轮写入的旧注入内容。"""
    handler, manager, store, engine, db = no_recall_bundle
    memory_part = TextPart(
        text=f"{MEMORY_INJECTION_HEADER}\n旧记忆\n{MEMORY_INJECTION_FOOTER}"
    ).mark_as_temp()
    req = _req("test query", parts=[memory_part])
    req.system_prompt = f"{MEMORY_INJECTION_HEADER}\n旧记忆\n{MEMORY_INJECTION_FOOTER}"

    await handler.handle_memory_recall(_Event(), req)

    assert req.extra_user_content_parts == []
    assert req.system_prompt == ""


@pytest.mark.asyncio
async def test_injected_text_keeps_cleanup_markers(handler_bundle_check):
    """注入文本必须带定界标记，下一轮才能被识别并清理。"""
    handler, manager, store, engine, db = handler_bundle_check
    await engine.add_memory("用户喜欢吃火锅", session_id=SESSION_ID, persona_id=None)
    req = _req("今天吃什么")

    await handler.handle_memory_recall(_Event(text="今天吃什么"), req)
    injected = _injected(req)

    assert MEMORY_INJECTION_HEADER in injected
    assert MEMORY_INJECTION_FOOTER in injected


@pytest_asyncio.fixture
async def handler_bundle_check(tmp_path):
    items = await make_handler(tmp_path, host=HostContext())
    try:
        yield items
    finally:
        await close_handler(*items)
