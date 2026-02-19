"""
Tests for MemoryProcessor.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from astrbot_plugin_livingmemory.core.models.conversation_models import Message
from astrbot_plugin_livingmemory.core.processors.memory_processor import MemoryProcessor


class _DummyLLMProvider:
    def __init__(self, completion_text: str):
        self._completion_text = completion_text
        self.text_chat = AsyncMock(side_effect=self._chat)

    async def _chat(self, prompt: str, system_prompt: str):
        return SimpleNamespace(completion_text=self._completion_text)


def _make_messages():
    return [
        Message(
            id=1,
            session_id="s1",
            role="user",
            content="明天下午三点开会",
            sender_id="u1",
            sender_name="张三",
            group_id=None,
            platform="test",
            metadata={},
        ),
        Message(
            id=2,
            session_id="s1",
            role="assistant",
            content="收到，我会提醒你",
            sender_id="bot",
            sender_name="Bot",
            group_id=None,
            platform="test",
            metadata={"is_bot_message": True},
        ),
    ]


@pytest.mark.asyncio
async def test_process_conversation_success():
    llm = _DummyLLMProvider(
        """{
            "summary":"我记录了张三明天下午三点开会，并给出提醒",
            "topics":["会议提醒"],
            "key_facts":["张三明天下午三点开会"],
            "sentiment":"neutral",
            "importance":0.8
        }"""
    )
    processor = MemoryProcessor(llm_provider=llm, context=None)

    content, metadata, importance = await processor.process_conversation(
        messages=_make_messages(),
        is_group_chat=False,
        save_original=False,
        persona_id=None,
    )

    assert "张三" in content
    assert metadata["interaction_type"] == "private_chat"
    assert "会议提醒" in metadata["topics"]
    assert importance == 0.8


@pytest.mark.asyncio
async def test_process_conversation_handles_non_json_response_with_fallback():
    llm = _DummyLLMProvider("summary=测试, importance=0.6")
    processor = MemoryProcessor(llm_provider=llm, context=None)

    content, metadata, importance = await processor.process_conversation(
        messages=_make_messages(),
        is_group_chat=False,
        save_original=False,
        persona_id=None,
    )

    assert isinstance(content, str) and len(content) > 0
    assert "topics" in metadata
    assert 0.0 <= importance <= 1.0


@pytest.mark.asyncio
async def test_persona_prompt_is_included_when_available():
    llm = _DummyLLMProvider(
        """{
            "summary":"我愉快地记录了这次交流",
            "topics":["闲聊"],
            "key_facts":["用户问候"],
            "sentiment":"positive",
            "importance":0.5
        }"""
    )
    context = Mock()
    context.persona_manager = Mock()
    context.persona_manager.get_persona = AsyncMock(
        return_value=SimpleNamespace(system_prompt="你是活泼助手")
    )

    processor = MemoryProcessor(llm_provider=llm, context=context)

    system_prompt = await processor._build_system_prompt_with_persona("persona_1")
    assert "人格设定" in system_prompt
    assert "活泼助手" in system_prompt
