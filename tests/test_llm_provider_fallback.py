"""G5 LLM Provider fallback chain: config, resolution, timeout and usage."""

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_livingmemory.core.base import usage_metrics
from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.base.config_validator import validate_config
from astrbot_plugin_livingmemory.core.base.exceptions import (
    ConfigurationError,
    MemoryParseError,
)
from astrbot_plugin_livingmemory.core.models.conversation_models import Message
from astrbot_plugin_livingmemory.core.processors import (
    memory_processor as processor_module,
)
from astrbot_plugin_livingmemory.core.processors.memory_processor import MemoryProcessor
from astrbot_plugin_livingmemory.core.prompts import prompt_manager

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "_conf_schema.json"

SUMMARY = (
    '{"summary":"回退后的记忆总结内容","topics":["主题"],'
    '"key_facts":["关键事实"],"sentiment":"neutral","importance":0.6}'
)


class _HttpStatusError(Exception):
    """Provider error carrying an HTTP status code."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code} error")
        self.status_code = status_code


class _FakeProvider:
    """Minimal AstrBot chat provider double."""

    def __init__(
        self,
        provider_id: str,
        *,
        result: str = SUMMARY,
        error: Exception | None = None,
        stream_text: str | None = SUMMARY,
        stream_error: Exception | None = None,
        usage: object = None,
        delay: float = 0.0,
    ) -> None:
        self.provider_config = {"id": provider_id}
        self.calls = 0
        self.stream_calls = 0
        self._result = result
        self._error = error
        self._stream_text = stream_text
        self._stream_error = stream_error
        self._usage = usage
        self._delay = delay

    async def text_chat(self, prompt=None, system_prompt=None):
        self.calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return SimpleNamespace(completion_text=self._result, usage=self._usage)

    async def text_chat_stream(self, prompt=None, system_prompt=None):
        self.stream_calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._stream_error is not None:
            raise self._stream_error
        yield SimpleNamespace(is_chunk=True, completion_text="部分")
        yield SimpleNamespace(
            is_chunk=False, completion_text=self._stream_text, usage=self._usage
        )


class _FakeContext:
    """Minimal AstrBot Context double exposing providers."""

    def __init__(self, providers=(), default=None) -> None:
        self._providers = {p.provider_config["id"]: p for p in providers}
        self._default = default

    def get_provider_by_id(self, provider_id):
        return self._providers.get(provider_id)

    def get_all_providers(self):
        return list(self._providers.values())

    async def get_using_provider_async(self, umo=None):
        return self._default

    def get_using_provider(self, umo=None):
        return self._default


@pytest.fixture(autouse=True)
def _isolated_prompt_manager(monkeypatch, tmp_path):
    manager = prompt_manager.PromptManager(str(tmp_path))
    monkeypatch.setattr(prompt_manager, "get_prompt_manager", lambda: manager)


def _processor(
    *,
    primary_id: str = "primary",
    providers=(),
    config: dict | None = None,
    default=None,
) -> tuple[MemoryProcessor, _FakeContext]:
    context = _FakeContext(providers, default=default)
    processor = MemoryProcessor(
        context=context,
        llm_provider=primary_id,
        config=config or {},
    )
    return processor, context


def _messages() -> list[Message]:
    return [
        Message(
            id=1,
            session_id="s1",
            role="user",
            content="明天下午三点开会",
            sender_id="u1",
            sender_name="张三",
            platform="test",
            metadata={},
        )
    ]


def _capture_usage(monkeypatch) -> list[tuple]:
    recorded: list[tuple] = []
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.processors.memory_processor.record_llm_usage",
        lambda usage, **kwargs: recorded.append((usage, kwargs)),
    )
    return recorded


def _patch_sleep(monkeypatch) -> AsyncMock:
    sleep = AsyncMock()
    monkeypatch.setattr(processor_module.asyncio, "sleep", sleep)
    return sleep


# ---------------------------------------------------------------------------
# Configuration defaults and validation
# ---------------------------------------------------------------------------


def test_provider_config_defaults_disable_fallback() -> None:
    provider = validate_config({}).provider_settings
    assert provider.llm_fallback_provider_ids == []
    assert provider.llm_call_timeout_seconds == 60
    assert ConfigManager().get("provider_settings.llm_fallback_provider_ids") == []
    assert ConfigManager().get("provider_settings.llm_call_timeout_seconds") == 60


def test_provider_config_accepts_valid_values() -> None:
    provider = validate_config(
        {
            "provider_settings": {
                "llm_fallback_provider_ids": ["openai", "gemini"],
                "llm_call_timeout_seconds": 5,
            }
        }
    ).provider_settings
    assert provider.llm_fallback_provider_ids == ["openai", "gemini"]
    assert provider.llm_call_timeout_seconds == 5

    max_timeout = validate_config(
        {"provider_settings": {"llm_call_timeout_seconds": 600}}
    ).provider_settings
    assert max_timeout.llm_call_timeout_seconds == 600


@pytest.mark.parametrize("timeout", [4, 601, -1, 0])
def test_provider_config_rejects_out_of_range_timeout(timeout: int) -> None:
    with pytest.raises(ValueError):
        validate_config({"provider_settings": {"llm_call_timeout_seconds": timeout}})


@pytest.mark.parametrize(
    "fallback_ids",
    [
        ["openai", 1],
        [None],
        "openai",
        {"id": "openai"},
    ],
)
def test_provider_config_rejects_non_string_fallback_list(fallback_ids) -> None:
    with pytest.raises(ValueError):
        validate_config(
            {"provider_settings": {"llm_fallback_provider_ids": fallback_ids}}
        )


def test_invalid_fallback_config_fails_fast_in_config_manager() -> None:
    with pytest.raises(ConfigurationError):
        ConfigManager({"provider_settings": {"llm_fallback_provider_ids": [1, 2]}})


def test_schema_exposes_fallback_settings() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8-sig"))
    items = schema["provider_settings"]["items"]

    fallback = items["llm_fallback_provider_ids"]
    assert fallback["type"] == "list"
    assert fallback["default"] == []
    assert fallback["description"] and fallback["hint"]

    timeout = items["llm_call_timeout_seconds"]
    assert timeout["type"] == "int"
    assert timeout["default"] == 60
    assert timeout["slider"] == {"min": 5, "max": 600, "step": 1}


def test_processor_normalizes_fallback_config() -> None:
    processor, _ = _processor(
        config={
            "llm_fallback_provider_ids": [" fb1 ", "", "fb1", 3, "fb2"],
            "llm_call_timeout_seconds": 0,
        }
    )
    assert processor._fallback_provider_ids == ["fb1", "fb2"]
    assert processor._llm_call_timeout_seconds == 60


# ---------------------------------------------------------------------------
# Provider order and fallback semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_primary_success_does_not_use_fallback(monkeypatch) -> None:
    primary = _FakeProvider("primary")
    backup = _FakeProvider("backup")
    processor, _ = _processor(
        providers=[primary, backup],
        config={"llm_fallback_provider_ids": ["backup"]},
    )
    recorded = _capture_usage(monkeypatch)

    text = await processor._call_llm_with_retry("prompt", "system")

    assert text == SUMMARY
    assert primary.calls == 1
    assert backup.calls == 0
    assert recorded[-1][1]["provider_id"] == "primary"
    assert recorded[-1][1]["fallback_used"] is False


@pytest.mark.asyncio
async def test_primary_429_retries_then_uses_fallback(monkeypatch) -> None:
    primary = _FakeProvider("primary", error=_HttpStatusError(429))
    backup = _FakeProvider("backup")
    processor, _ = _processor(
        providers=[primary, backup],
        config={"llm_fallback_provider_ids": ["backup"]},
    )
    _patch_sleep(monkeypatch)
    recorded = _capture_usage(monkeypatch)

    text = await processor._call_llm_with_retry("prompt", "system")

    assert text == SUMMARY
    assert primary.calls == 2  # 首调 + 一次瞬时重试
    assert backup.calls == 1
    assert recorded[-1][1]["provider_id"] == "backup"
    assert recorded[-1][1]["fallback_used"] is True


@pytest.mark.asyncio
async def test_primary_auth_failure_switches_without_retry(monkeypatch) -> None:
    primary = _FakeProvider("primary", error=_HttpStatusError(401))
    backup = _FakeProvider("backup")
    processor, _ = _processor(
        providers=[primary, backup],
        config={"llm_fallback_provider_ids": ["backup"]},
    )
    _patch_sleep(monkeypatch)

    text = await processor._call_llm_with_retry("prompt", "system")

    assert text == SUMMARY
    assert primary.calls == 1  # 鉴权失败不重试，直接切下一个
    assert backup.calls == 1


@pytest.mark.asyncio
async def test_primary_timeout_switches_to_fallback() -> None:
    primary = _FakeProvider("primary", delay=0.3)
    backup = _FakeProvider("backup")
    processor, _ = _processor(
        providers=[primary, backup],
        config={
            "llm_fallback_provider_ids": ["backup"],
            "llm_call_timeout_seconds": 0.05,
        },
    )

    text = await processor._call_llm_with_retry("prompt", "system", max_retries=1)

    assert text == SUMMARY
    assert primary.calls == 1
    assert backup.calls == 1


@pytest.mark.asyncio
async def test_missing_primary_uses_fallback() -> None:
    backup = _FakeProvider("backup")
    processor, _ = _processor(
        primary_id="not-registered",
        providers=[backup],
        config={"llm_fallback_provider_ids": ["backup"]},
    )

    text = await processor._call_llm_with_retry("prompt", "system")

    assert text == SUMMARY
    assert backup.calls == 1


@pytest.mark.asyncio
async def test_unresolvable_fallback_id_is_skipped(caplog) -> None:
    primary = _FakeProvider("primary", error=_HttpStatusError(503))
    backup = _FakeProvider("backup")
    processor, _ = _processor(
        providers=[primary, backup],
        config={"llm_fallback_provider_ids": ["missing", "backup"]},
    )

    with caplog.at_level(logging.WARNING):
        text = await processor._call_llm_with_retry("prompt", "system", max_retries=1)

    assert text == SUMMARY
    assert backup.calls == 1
    assert "missing" in caplog.text


@pytest.mark.asyncio
async def test_duplicate_provider_ids_are_not_tried_twice() -> None:
    primary = _FakeProvider("primary", error=_HttpStatusError(503))
    backup = _FakeProvider("backup")
    processor, _ = _processor(
        providers=[primary, backup],
        config={"llm_fallback_provider_ids": ["primary", "backup", "backup"]},
    )

    chain = await processor._resolve_llm_provider_chain()

    assert [provider_id for provider_id, _ in chain] == ["primary", "backup"]


@pytest.mark.asyncio
async def test_all_providers_fail_raises_last_error(monkeypatch) -> None:
    primary = _FakeProvider("primary", error=_HttpStatusError(503))
    backup = _FakeProvider("backup", error=_HttpStatusError(502))
    processor, _ = _processor(
        providers=[primary, backup],
        config={"llm_fallback_provider_ids": ["backup"]},
    )
    _patch_sleep(monkeypatch)

    with pytest.raises(_HttpStatusError) as exc_info:
        await processor._call_llm_with_retry("prompt", "system", max_retries=1)

    assert exc_info.value.status_code == 502
    assert backup.calls == 1


@pytest.mark.asyncio
async def test_format_error_does_not_switch_provider() -> None:
    primary = _FakeProvider("primary", error=ValueError("response format invalid"))
    backup = _FakeProvider("backup")
    processor, _ = _processor(
        providers=[primary, backup],
        config={"llm_fallback_provider_ids": ["backup"]},
    )

    with pytest.raises(ValueError, match="format invalid"):
        await processor._call_llm_with_retry("prompt", "system")

    assert primary.calls == 1
    assert backup.calls == 0


@pytest.mark.asyncio
async def test_parse_failure_after_llm_call_does_not_switch_provider() -> None:
    """LLM 返回不可解析 JSON 时按解析失败处理，不尝试备用 Provider。"""
    primary = _FakeProvider("primary", result="这不是 JSON")
    backup = _FakeProvider("backup")
    processor, _ = _processor(
        providers=[primary, backup],
        config={"llm_fallback_provider_ids": ["backup"]},
    )

    with pytest.raises(MemoryParseError):
        await processor.process_conversation(_messages())

    assert primary.calls == 1
    assert backup.calls == 0


@pytest.mark.asyncio
async def test_fallback_covers_process_conversation(monkeypatch) -> None:
    primary = _FakeProvider("primary", error=_HttpStatusError(500))
    backup = _FakeProvider("backup")
    processor, _ = _processor(
        providers=[primary, backup],
        config={"llm_fallback_provider_ids": ["backup"]},
    )
    _patch_sleep(monkeypatch)

    content, metadata, importance = await processor.process_conversation(_messages())

    assert "回退后的记忆总结内容" in content
    assert metadata["summary_quality"] == "normal"
    assert importance == 0.6
    assert backup.calls == 1


@pytest.mark.asyncio
async def test_fallback_covers_merge_memories(monkeypatch) -> None:
    merge_json = (
        '{"summary":"合并摘要","key_facts":["事实"],"topics":["主题"],"importance":0.5}'
    )
    primary = _FakeProvider("primary", error=_HttpStatusError(500))
    backup = _FakeProvider("backup", result=merge_json)
    processor, _ = _processor(
        providers=[primary, backup],
        config={"llm_fallback_provider_ids": ["backup"]},
    )
    _patch_sleep(monkeypatch)

    merged = await processor.merge_memories(
        [{"content": "旧记忆", "metadata": {"summary": "旧摘要"}}]
    )

    assert merged["summary"] == "合并摘要"
    assert backup.calls == 1


# ---------------------------------------------------------------------------
# Streaming compatibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_only_primary_falls_back_to_stream_capable_provider(
    monkeypatch,
) -> None:
    """auto 模式：主 Provider 探测为仅流式后流式失败，回退 Provider 独立判断。"""
    primary = _FakeProvider(
        "primary",
        error=RuntimeError(
            "Error code: 400 - Non-stream chat request is currently not supported"
        ),
        stream_error=_HttpStatusError(503),
    )
    backup = _FakeProvider("backup")
    processor, _ = _processor(
        providers=[primary, backup],
        config={"llm_fallback_provider_ids": ["backup"], "llm_stream_mode": "auto"},
    )

    text = await processor._call_llm_with_retry("prompt", "system", max_retries=1)

    assert text == SUMMARY
    assert primary.calls == 1
    assert primary.stream_calls == 1
    assert backup.calls == 1
    # 仅主 Provider 被记住为流式专用，回退 Provider 不受影响
    assert "primary" in processor._stream_only_providers
    assert "backup" not in processor._stream_only_providers


@pytest.mark.asyncio
async def test_streaming_timeout_switches_to_fallback() -> None:
    primary = _FakeProvider("primary", delay=0.3)
    backup = _FakeProvider("backup")
    processor, _ = _processor(
        providers=[primary, backup],
        config={
            "llm_fallback_provider_ids": ["backup"],
            "llm_stream_mode": "always",
            "llm_call_timeout_seconds": 0.05,
        },
    )

    text = await processor._call_llm_with_retry("prompt", "system", max_retries=1)

    assert text == SUMMARY
    assert primary.stream_calls == 1
    assert backup.stream_calls == 1


# ---------------------------------------------------------------------------
# Usage metrics integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_call_is_visible_in_usage_snapshot(monkeypatch) -> None:
    primary = _FakeProvider("primary", error=_HttpStatusError(500))
    backup = _FakeProvider("backup")
    processor, _ = _processor(
        providers=[primary, backup],
        config={"llm_fallback_provider_ids": ["backup"]},
    )
    _patch_sleep(monkeypatch)
    before = usage_metrics.get_usage_snapshot()

    await processor._call_llm_with_retry("prompt", "system", max_retries=1)

    after = usage_metrics.get_usage_snapshot()
    assert (
        after["providers"].get("backup", 0) - before["providers"].get("backup", 0) == 1
    )
    assert after["fallback_calls"] == before["fallback_calls"] + 1
    assert after["calls"] == before["calls"] + 1


@pytest.mark.asyncio
async def test_fallback_without_usage_still_estimates_tokens(monkeypatch) -> None:
    primary = _FakeProvider("primary", error=_HttpStatusError(500))
    backup = _FakeProvider("backup", result=SUMMARY, usage=None)
    processor, _ = _processor(
        providers=[primary, backup],
        config={"llm_fallback_provider_ids": ["backup"]},
    )
    _patch_sleep(monkeypatch)
    before = usage_metrics.get_usage_snapshot()

    await processor._call_llm_with_retry("prompt", "system", max_retries=1)

    after = usage_metrics.get_usage_snapshot()
    assert after["estimated_calls"] == before["estimated_calls"] + 1
    assert after["input_tokens"] > before["input_tokens"]
    assert after["fallback_calls"] == before["fallback_calls"] + 1
