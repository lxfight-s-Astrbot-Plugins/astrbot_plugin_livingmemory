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


# ── New tests for dual-channel summary and quality validator ──────────────────


@pytest.mark.asyncio
async def test_dual_channel_summary_stores_canonical_and_persona():
    """
    process_conversation 应在 metadata 中同时存储
    canonical_summary（检索用）和 persona_summary（人格风格用）。
    """
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

    # canonical_summary 应存在且包含事实内容
    assert "canonical_summary" in metadata
    assert len(metadata["canonical_summary"]) > 0

    # persona_summary 应存在（等于原始 LLM summary）
    assert "persona_summary" in metadata
    assert "张三" in metadata["persona_summary"]

    # content 应使用 canonical_summary（事实导向）
    assert content == metadata["canonical_summary"]

    # schema 版本标记
    assert metadata.get("summary_schema_version") == "v2"


@pytest.mark.asyncio
async def test_canonical_summary_includes_key_facts():
    """canonical_summary 应将 key_facts 拼接到摘要中，提升检索覆盖率。"""
    llm = _DummyLLMProvider(
        """{
            "summary":"用户提到了一个重要事项",
            "topics":["备忘"],
            "key_facts":["明天下午三点开会", "需要准备PPT"],
            "sentiment":"neutral",
            "importance":0.7
        }"""
    )
    processor = MemoryProcessor(llm_provider=llm, context=None)

    content, metadata, _ = await processor.process_conversation(
        messages=_make_messages(),
        is_group_chat=False,
        save_original=False,
        persona_id=None,
    )

    # canonical_summary 应包含 key_facts 内容
    assert "明天下午三点开会" in metadata["canonical_summary"]
    assert "需要准备PPT" in metadata["canonical_summary"]


@pytest.mark.asyncio
async def test_summary_quality_normal_for_valid_response():
    """有效的 LLM 响应应标记为 summary_quality=normal。"""
    llm = _DummyLLMProvider(
        """{
            "summary":"用户告知明天下午三点有重要会议需要参加",
            "topics":["会议"],
            "key_facts":["明天下午三点开会"],
            "sentiment":"neutral",
            "importance":0.8
        }"""
    )
    processor = MemoryProcessor(llm_provider=llm, context=None)

    _, metadata, _ = await processor.process_conversation(
        messages=_make_messages(),
        is_group_chat=False,
        save_original=False,
        persona_id=None,
    )

    assert metadata.get("summary_quality") == "normal"


@pytest.mark.asyncio
async def test_summary_quality_low_for_empty_summary():
    """summary 为空时应标记为 summary_quality=low。"""
    llm = _DummyLLMProvider(
        """{
            "summary":"",
            "topics":["闲聊"],
            "key_facts":["用户问候"],
            "sentiment":"neutral",
            "importance":0.5
        }"""
    )
    processor = MemoryProcessor(llm_provider=llm, context=None)

    _, metadata, _ = await processor.process_conversation(
        messages=_make_messages(),
        is_group_chat=False,
        save_original=False,
        persona_id=None,
    )

    assert metadata.get("summary_quality") == "low"


@pytest.mark.asyncio
async def test_summary_quality_low_for_missing_key_facts():
    """key_facts 为空时应标记为 summary_quality=low。"""
    llm = _DummyLLMProvider(
        """{
            "summary":"用户进行了一次普通对话",
            "topics":["闲聊"],
            "key_facts":[],
            "sentiment":"neutral",
            "importance":0.5
        }"""
    )
    processor = MemoryProcessor(llm_provider=llm, context=None)

    _, metadata, _ = await processor.process_conversation(
        messages=_make_messages(),
        is_group_chat=False,
        save_original=False,
        persona_id=None,
    )

    assert metadata.get("summary_quality") == "low"


@pytest.mark.asyncio
async def test_summary_quality_low_for_generic_terms():
    """summary 包含泛化词（某用户、有人等）时应标记为 summary_quality=low。"""
    llm = _DummyLLMProvider(
        """{
            "summary":"某用户提到了一些事情",
            "topics":["闲聊"],
            "key_facts":["某用户说了话"],
            "sentiment":"neutral",
            "importance":0.5
        }"""
    )
    processor = MemoryProcessor(llm_provider=llm, context=None)

    _, metadata, _ = await processor.process_conversation(
        messages=_make_messages(),
        is_group_chat=False,
        save_original=False,
        persona_id=None,
    )

    assert metadata.get("summary_quality") == "low"


def test_validate_summary_quality_directly():
    """直接测试 _validate_summary_quality 的各种边界情况。"""
    from unittest.mock import MagicMock
    processor = MemoryProcessor(llm_provider=MagicMock(), context=None)

    # 正常情况
    assert processor._validate_summary_quality({
        "summary": "用户明确表示喜欢吃寿司",
        "key_facts": ["用户喜欢寿司"],
        "importance": 0.7,
    }) == "normal"

    # summary 过短
    assert processor._validate_summary_quality({
        "summary": "短",
        "key_facts": ["fact"],
        "importance": 0.5,
    }) == "low"

    # importance 超出范围
    assert processor._validate_summary_quality({
        "summary": "用户明确表示喜欢吃寿司",
        "key_facts": ["用户喜欢寿司"],
        "importance": 1.5,
    }) == "low"

    # 泛化词检测
    assert processor._validate_summary_quality({
        "summary": "有人提到了一些事情",
        "key_facts": ["有人说话"],
        "importance": 0.5,
    }) == "low"


# ── 群聊路径测试 ──────────────────────────────────────────────────────────────


def _make_group_messages():
    """构造一组群聊消息（含 group_id）"""
    return [
        Message(
            id=1,
            session_id="aiocqhttp:GroupMessage:88888",
            role="user",
            content="大家觉得 AI 工具怎么样？",
            sender_id="10001",
            sender_name="张三",
            group_id="88888",
            platform="aiocqhttp",
            metadata={},
        ),
        Message(
            id=2,
            session_id="aiocqhttp:GroupMessage:88888",
            role="user",
            content="我觉得 ChatGPT 写代码效率提升了 30%",
            sender_id="10002",
            sender_name="李四",
            group_id="88888",
            platform="aiocqhttp",
            metadata={},
        ),
        Message(
            id=3,
            session_id="aiocqhttp:GroupMessage:88888",
            role="assistant",
            content="AI 工具确实能提升效率，但需要仔细审查生成的代码",
            sender_id="bot",
            sender_name="Bot",
            group_id="88888",
            platform="aiocqhttp",
            metadata={"is_bot_message": True},
        ),
    ]


@pytest.mark.asyncio
async def test_process_group_chat_sets_interaction_type():
    """群聊路径应将 interaction_type 设置为 group_chat。"""
    llm = _DummyLLMProvider(
        """{
            "summary":"群聊讨论了 AI 工具的使用效果",
            "topics":["AI工具","工作效率"],
            "key_facts":["张三认为 ChatGPT 效率提升 30%","需要仔细审查 AI 生成代码"],
            "participants":["张三","李四"],
            "sentiment":"positive",
            "importance":0.75
        }"""
    )
    processor = MemoryProcessor(llm_provider=llm, context=None)

    content, metadata, importance = await processor.process_conversation(
        messages=_make_group_messages(),
        is_group_chat=True,
        save_original=False,
        persona_id=None,
    )

    assert metadata["interaction_type"] == "group_chat"
    assert importance == 0.75


@pytest.mark.asyncio
async def test_process_group_chat_extracts_participants():
    """群聊路径应正确提取 participants 字段。"""
    llm = _DummyLLMProvider(
        """{
            "summary":"群聊讨论了 AI 工具的使用效果",
            "topics":["AI工具"],
            "key_facts":["张三认为 ChatGPT 效率提升 30%"],
            "participants":["张三","李四","王五"],
            "sentiment":"positive",
            "importance":0.7
        }"""
    )
    processor = MemoryProcessor(llm_provider=llm, context=None)

    _, metadata, _ = await processor.process_conversation(
        messages=_make_group_messages(),
        is_group_chat=True,
        save_original=False,
        persona_id=None,
    )

    assert "participants" in metadata
    assert "张三" in metadata["participants"]
    assert "李四" in metadata["participants"]
    assert "王五" in metadata["participants"]


@pytest.mark.asyncio
async def test_process_group_chat_dual_channel_summary():
    """群聊路径也应生成双通道摘要（canonical_summary + persona_summary）。"""
    llm = _DummyLLMProvider(
        """{
            "summary":"群聊讨论了 AI 工具的使用效果，建议内部部署私有化 LLM",
            "topics":["AI工具","数据安全"],
            "key_facts":["建议公司内部部署私有化 LLM","注意数据安全"],
            "participants":["张三","李四"],
            "sentiment":"positive",
            "importance":0.8
        }"""
    )
    processor = MemoryProcessor(llm_provider=llm, context=None)

    content, metadata, _ = await processor.process_conversation(
        messages=_make_group_messages(),
        is_group_chat=True,
        save_original=False,
        persona_id=None,
    )

    assert "canonical_summary" in metadata
    assert "persona_summary" in metadata
    assert metadata.get("summary_schema_version") == "v2"
    # canonical_summary 应包含 key_facts
    assert "私有化 LLM" in metadata["canonical_summary"]
    # content 应等于 canonical_summary
    assert content == metadata["canonical_summary"]


@pytest.mark.asyncio
async def test_process_group_chat_missing_participants_uses_default():
    """群聊 LLM 响应缺少 participants 字段时，应使用空列表默认值。"""
    llm = _DummyLLMProvider(
        """{
            "summary":"群聊讨论了一些话题",
            "topics":["闲聊"],
            "key_facts":["大家聊了很多"],
            "sentiment":"neutral",
            "importance":0.5
        }"""
    )
    processor = MemoryProcessor(llm_provider=llm, context=None)

    _, metadata, _ = await processor.process_conversation(
        messages=_make_group_messages(),
        is_group_chat=True,
        save_original=False,
        persona_id=None,
    )

    # 缺少 participants 时应补充默认空列表
    assert "participants" in metadata
    assert isinstance(metadata["participants"], list)


@pytest.mark.asyncio
async def test_process_private_chat_no_participants_field():
    """私聊路径不应在 metadata 中包含 participants 字段。"""
    llm = _DummyLLMProvider(
        """{
            "summary":"用户告知明天下午三点有重要会议",
            "topics":["会议"],
            "key_facts":["明天下午三点开会"],
            "sentiment":"neutral",
            "importance":0.8
        }"""
    )
    processor = MemoryProcessor(llm_provider=llm, context=None)

    _, metadata, _ = await processor.process_conversation(
        messages=_make_messages(),
        is_group_chat=False,
        save_original=False,
        persona_id=None,
    )

    assert "participants" not in metadata
    assert metadata["interaction_type"] == "private_chat"


@pytest.mark.asyncio
async def test_process_group_chat_long_content():
    """群聊长内容（多条消息）应正常处理，不崩溃。"""
    long_messages = []
    for i in range(20):
        long_messages.append(
            Message(
                id=i + 1,
                session_id="aiocqhttp:GroupMessage:99999",
                role="user",
                content=f"成员{i % 5} 说：这是第 {i+1} 条消息，内容比较详细，包含了很多信息。" * 3,
                sender_id=str(10000 + i % 5),
                sender_name=f"成员{i % 5}",
                group_id="99999",
                platform="aiocqhttp",
                metadata={},
            )
        )

    llm = _DummyLLMProvider(
        """{
            "summary":"群聊成员进行了多轮讨论，涉及多个话题",
            "topics":["群聊","讨论"],
            "key_facts":["多名成员参与讨论","讨论内容丰富"],
            "participants":["成员0","成员1","成员2","成员3","成员4"],
            "sentiment":"neutral",
            "importance":0.6
        }"""
    )
    processor = MemoryProcessor(llm_provider=llm, context=None)

    content, metadata, importance = await processor.process_conversation(
        messages=long_messages,
        is_group_chat=True,
        save_original=False,
        persona_id=None,
    )

    assert isinstance(content, str) and len(content) > 0
    assert metadata["interaction_type"] == "group_chat"
    assert len(metadata["participants"]) == 5
    assert 0.0 <= importance <= 1.0


@pytest.mark.asyncio
async def test_process_group_chat_quality_low_for_generic_terms():
    """群聊总结包含泛化词时，summary_quality 应为 low。"""
    llm = _DummyLLMProvider(
        """{
            "summary":"某用户在群里说了一些话",
            "topics":["闲聊"],
            "key_facts":["有人说话了"],
            "participants":["某用户"],
            "sentiment":"neutral",
            "importance":0.4
        }"""
    )
    processor = MemoryProcessor(llm_provider=llm, context=None)

    _, metadata, _ = await processor.process_conversation(
        messages=_make_group_messages(),
        is_group_chat=True,
        save_original=False,
        persona_id=None,
    )

    assert metadata.get("summary_quality") == "low"
