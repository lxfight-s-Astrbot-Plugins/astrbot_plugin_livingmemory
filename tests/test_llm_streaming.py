"""Tests for optional streaming LLM calls (#265)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.base.config_validator import validate_config
from astrbot_plugin_livingmemory.core.models.conversation_models import Message
from astrbot_plugin_livingmemory.core.processors import (
    memory_processor as processor_module,
)
from astrbot_plugin_livingmemory.core.processors.memory_processor import MemoryProcessor
from astrbot_plugin_livingmemory.core.prompts import prompt_manager

NON_STREAM_ERROR = (
    "Error code: 400 - Non-stream chat request is currently not supported"
)
FINAL_SUMMARY = (
    '{"summary":"用户喜欢喝咖啡","topics":["偏好"],'
    '"key_facts":["用户偏爱手冲咖啡"],"sentiment":"neutral","importance":0.7}'
)


class _StreamingProvider:
    """按 AstrBot 契约模拟 Provider：先返回增量 chunk，最后返回一次完整响应。"""

    def __init__(
        self,
        *,
        non_stream_result: str | None = None,
        non_stream_error: Exception | None = None,
        final_text: str | None = FINAL_SUMMARY,
        chunks: tuple[str, ...] = ("用户喜欢", "喝咖啡"),
        stream_error: Exception | None = None,
    ) -> None:
        self.stream_calls = 0
        self.stream_kwargs: list[dict] = []
        self._final_text = final_text
        self._chunks = chunks
        self._stream_error = stream_error
        if non_stream_error is not None:
            self.text_chat = AsyncMock(side_effect=non_stream_error)
        else:
            self.text_chat = AsyncMock(
                return_value=SimpleNamespace(completion_text=non_stream_result)
            )

    async def text_chat_stream(self, prompt=None, system_prompt=None):
        """模拟 AstrBot Provider.text_chat_stream 的异步生成器。"""
        self.stream_calls += 1
        self.stream_kwargs.append({"prompt": prompt, "system_prompt": system_prompt})
        for piece in self._chunks:
            yield SimpleNamespace(is_chunk=True, completion_text=piece)
        if self._stream_error is not None:
            raise self._stream_error
        if self._final_text is not None:
            yield SimpleNamespace(is_chunk=False, completion_text=self._final_text)


def _processor(provider, **config) -> MemoryProcessor:
    return MemoryProcessor(llm_provider=provider, config=config)


def _messages() -> list[Message]:
    return [
        Message(
            id=1,
            session_id="s1",
            role="user",
            content="我喜欢喝咖啡",
            sender_id="u1",
            sender_name="张三",
            platform="test",
        )
    ]


@pytest.fixture(autouse=True)
def _isolated_prompt_manager(monkeypatch, tmp_path):
    """使用独立的 PromptManager，避免受其他测试的全局单例影响。"""
    manager = prompt_manager.PromptManager(str(tmp_path))
    monkeypatch.setattr(prompt_manager, "get_prompt_manager", lambda: manager)


def test_config_defaults_to_auto():
    """配置默认值必须是 auto，且配置缺失时也按 auto 处理。"""
    assert validate_config({}).provider_settings.llm_stream_mode == "auto"
    assert ConfigManager().get("provider_settings.llm_stream_mode") == "auto"


@pytest.mark.asyncio
@pytest.mark.parametrize("config", [{}, {"llm_stream_mode": "auto"}])
async def test_auto_mode_prefers_non_stream(config):
    """auto 模式优先走非流式，兼容现有模型服务。"""
    provider = _StreamingProvider(non_stream_result='{"summary":"非流式"}')
    processor = _processor(provider, **config)

    result = await processor._call_llm_with_retry("p", "s", max_retries=1)

    assert result == '{"summary":"非流式"}'
    assert provider.text_chat.await_count == 1
    assert provider.stream_calls == 0


@pytest.mark.asyncio
async def test_auto_mode_falls_back_to_stream_on_non_stream_unsupported():
    """捕获“不支持非流式”的 400 错误后，在本次调用内改用流式并返回完整结果。"""
    provider = _StreamingProvider(non_stream_error=RuntimeError(NON_STREAM_ERROR))
    processor = _processor(provider, llm_stream_mode="auto")

    result = await processor._call_llm_with_retry("p", "s", max_retries=1)

    assert result == FINAL_SUMMARY
    assert provider.text_chat.await_count == 1
    assert provider.stream_calls == 1
    assert provider.stream_kwargs[-1] == {"prompt": "p", "system_prompt": "s"}


@pytest.mark.asyncio
async def test_auto_mode_remembers_stream_only_provider():
    """首次探测成功后，后续调用直接走流式，不再重复失败的非流式请求。"""
    provider = _StreamingProvider(non_stream_error=RuntimeError(NON_STREAM_ERROR))
    processor = _processor(provider, llm_stream_mode="auto")

    await processor._call_llm_with_retry("p", "s", max_retries=1)
    provider.text_chat.reset_mock()
    await processor._call_llm_with_retry("p", "s", max_retries=1)

    assert provider.text_chat.await_count == 0
    assert provider.stream_calls == 2


@pytest.mark.asyncio
async def test_auto_mode_ignores_unrelated_errors():
    """只有明确的“不支持非流式”错误才触发降级，其他错误保持原有重试/抛出行为。"""
    provider = _StreamingProvider(
        non_stream_error=RuntimeError("Error code: 500 - internal server error")
    )
    processor = _processor(provider, llm_stream_mode="auto")

    with pytest.raises(RuntimeError, match="500"):
        await processor._call_llm_with_retry("p", "s", max_retries=1)

    assert provider.stream_calls == 0


@pytest.mark.asyncio
async def test_unknown_stream_mode_falls_back_to_auto():
    """未知配置值按 auto 处理：优先非流式，并保留 400 降级能力。"""
    non_stream_provider = _StreamingProvider(non_stream_result="非流式结果")
    processor = _processor(non_stream_provider, llm_stream_mode="sometimes")
    assert (
        await processor._call_llm_with_retry("p", "s", max_retries=1) == "非流式结果"
    )
    assert non_stream_provider.stream_calls == 0

    falling_provider = _StreamingProvider(
        non_stream_error=RuntimeError(NON_STREAM_ERROR)
    )
    processor = _processor(falling_provider, llm_stream_mode="sometimes")
    assert (
        await processor._call_llm_with_retry("p", "s", max_retries=1) == FINAL_SUMMARY
    )
    assert falling_provider.stream_calls == 1


@pytest.mark.asyncio
async def test_never_mode_does_not_fall_back():
    """never 模式保持旧行为：非流式失败直接报错，不尝试流式。"""
    provider = _StreamingProvider(non_stream_error=RuntimeError(NON_STREAM_ERROR))
    processor = _processor(provider, llm_stream_mode="never")

    with pytest.raises(RuntimeError, match="Non-stream"):
        await processor._call_llm_with_retry("p", "s", max_retries=1)

    assert provider.stream_calls == 0


@pytest.mark.asyncio
async def test_always_mode_skips_non_stream():
    """always 模式始终使用流式接口，即使 Provider 支持非流式。"""
    provider = _StreamingProvider(non_stream_result="不应使用")
    processor = _processor(provider, llm_stream_mode="always")

    result = await processor._call_llm_with_retry("p", "s", max_retries=1)

    assert result == FINAL_SUMMARY
    assert provider.text_chat.await_count == 0
    assert provider.stream_calls == 1


@pytest.mark.asyncio
async def test_stream_only_provider_is_not_retried_as_non_stream(monkeypatch):
    """已确认只接受流式的 Provider，重试时不再探测非流式。"""
    monkeypatch.setattr(processor_module.asyncio, "sleep", AsyncMock())
    provider = _StreamingProvider(
        non_stream_error=RuntimeError(NON_STREAM_ERROR),
        stream_error=TimeoutError("stream timeout"),
    )
    processor = _processor(provider, llm_stream_mode="auto")

    with pytest.raises(TimeoutError, match="stream timeout"):
        await processor._call_llm_with_retry("p", "s", max_retries=2)

    assert provider.text_chat.await_count == 1
    assert provider.stream_calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("final_text", [None, ""])
async def test_stream_without_complete_response_raises(final_text):
    """只收到 chunk 或完整响应为空时按失败处理，不能把不完整结果当作成功。"""
    provider = _StreamingProvider(final_text=final_text)
    processor = _processor(provider, llm_stream_mode="always")

    with pytest.raises(RuntimeError, match="完整响应"):
        await processor._call_llm_with_retry("p", "s", max_retries=1)


@pytest.mark.asyncio
async def test_stream_interruption_raises():
    """流式中断（生成器内部抛错）按失败处理。"""
    provider = _StreamingProvider(
        chunks=("部分内容",), stream_error=TimeoutError("stream interrupted")
    )
    processor = _processor(provider, llm_stream_mode="always")

    with pytest.raises(TimeoutError, match="stream interrupted"):
        await processor._call_llm_with_retry("p", "s", max_retries=1)


@pytest.mark.asyncio
async def test_missing_provider_raises():
    """Provider 缺失时按原有逻辑失败，不吞异常。"""
    processor = _processor(None, llm_stream_mode="always")

    with pytest.raises(RuntimeError, match="Provider 不可用"):
        await processor._call_llm_with_retry("p", "s", max_retries=1)


@pytest.mark.asyncio
async def test_auto_fallback_summary_uses_complete_stream_result():
    """auto 降级后的流式完整结果继续走原有记忆处理流程。"""
    provider = _StreamingProvider(non_stream_error=RuntimeError(NON_STREAM_ERROR))
    processor = _processor(provider, llm_stream_mode="auto")

    content, metadata, importance = await processor.process_conversation(_messages())

    assert content == "用户喜欢喝咖啡 | 用户偏爱手冲咖啡"
    assert metadata["canonical_summary"] == content
    assert importance == 0.7
    assert provider.text_chat.await_count == 1
    assert provider.stream_calls == 1


@pytest.mark.asyncio
async def test_incomplete_stream_summary_fails_without_writing(monkeypatch):
    """流式结果不完整时 process_conversation 直接失败，不产出可写入的记忆。"""
    monkeypatch.setattr(processor_module.asyncio, "sleep", AsyncMock())
    provider = _StreamingProvider(final_text=None)
    processor = _processor(provider, llm_stream_mode="always")

    with pytest.raises(RuntimeError, match="完整响应"):
        await processor.process_conversation(_messages())
