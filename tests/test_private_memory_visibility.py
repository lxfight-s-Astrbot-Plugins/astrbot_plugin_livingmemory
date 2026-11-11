"""Tests for optional private-memory visibility metadata."""

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.base.config_validator import RecallEngineConfig
from astrbot_plugin_livingmemory.core.event_handler_modules.memory_recall import (
    MemoryRecall,
)
from astrbot_plugin_livingmemory.core.managers.memory_engine_crud import (
    MemoryEngineCrudMixin,
)
from astrbot_plugin_livingmemory.core.prompts.prompt_manager import PromptManager
from astrbot_plugin_livingmemory.core.tools.memory_memorize_tool import (
    MemoryMemorizeTool,
)
from astrbot_plugin_livingmemory.core.utils import format_memories_for_injection
from astrbot_plugin_livingmemory.core.utils.memory_visibility import (
    is_undisclosed_private,
    normalize_disclosed,
    normalize_visibility,
)

PRIVATE_NOTICE = "（这是你私下经历的事，用户并不知道；不要把它当成已经告诉过对方。）"


class _DummyIndexWriter:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def add_memory(self, content, metadata, *, embedding=None):
        self.calls.append({"content": content, "metadata": dict(metadata)})
        return len(self.calls)


class _CrudHarness(MemoryEngineCrudMixin):
    """Minimal host exposing ``add_memory`` without storage dependencies."""

    def __init__(self) -> None:
        self.config = {}
        self.index_writer = _DummyIndexWriter()
        self.atom_enabled = False
        self.atom_store = None

    async def _write_memory_atoms(self, *args, **kwargs):
        return None

    async def _index_memory_graph(self, *args, **kwargs):
        return None

    async def _persist_memory_source(self, *args, **kwargs):
        return None

    def _invalidate_search_cache(self):
        return None


def _memory(metadata: dict) -> dict:
    return {"content": "private test memory", "score": 0.9, "metadata": metadata}


def test_visibility_helpers_default_to_public() -> None:
    assert normalize_visibility(None) == "public"
    assert normalize_visibility("PRIVATE") == "private"
    assert normalize_visibility("unknown") == "public"
    assert normalize_disclosed(None) is True
    assert normalize_disclosed(False) is False
    assert normalize_disclosed("false") is False
    assert is_undisclosed_private({}) is False
    assert is_undisclosed_private({"visibility": "private"}) is False
    assert (
        is_undisclosed_private({"visibility": "private", "disclosed": False}) is True
    )


def test_legacy_memory_without_visibility_gets_no_notice() -> None:
    result = format_memories_for_injection(
        [_memory({"importance": 0.5})], private_notice=PRIVATE_NOTICE
    )
    assert PRIVATE_NOTICE not in result


def test_private_undisclosed_memory_gets_notice() -> None:
    result = format_memories_for_injection(
        [_memory({"visibility": "private", "disclosed": False})],
        private_notice=PRIVATE_NOTICE,
    )
    assert PRIVATE_NOTICE in result


def test_private_disclosed_memory_gets_no_notice() -> None:
    result = format_memories_for_injection(
        [_memory({"visibility": "private", "disclosed": True})],
        private_notice=PRIVATE_NOTICE,
    )
    assert PRIVATE_NOTICE not in result


def test_public_undisclosed_memory_gets_no_notice() -> None:
    result = format_memories_for_injection(
        [_memory({"visibility": "public", "disclosed": False})],
        private_notice=PRIVATE_NOTICE,
    )
    assert PRIVATE_NOTICE not in result


def test_private_notice_can_be_disabled_or_customised() -> None:
    private_memory = _memory({"visibility": "private", "disclosed": False})

    disabled = format_memories_for_injection([private_memory], private_notice="")
    assert PRIVATE_NOTICE not in disabled

    custom = format_memories_for_injection(
        [private_memory], private_notice="CUSTOM PRIVATE NOTICE"
    )
    assert "CUSTOM PRIVATE NOTICE" in custom


def test_default_private_notice_is_available_as_prompt() -> None:
    content = PromptManager("").get_prompt("private_memory_injection_notice")
    assert "私下经历" in content
    assert "用户并不知道" in content


@pytest.mark.asyncio
async def test_add_memory_defaults_visibility_metadata() -> None:
    engine = _CrudHarness()

    await engine.add_memory(content="legacy write", session_id="s1")

    metadata = engine.index_writer.calls[0]["metadata"]
    assert metadata["visibility"] == "public"
    assert metadata["disclosed"] is True


@pytest.mark.asyncio
async def test_add_memory_accepts_private_visibility() -> None:
    engine = _CrudHarness()

    await engine.add_memory(
        content="private write",
        session_id="s1",
        visibility="private",
        disclosed=False,
    )

    metadata = engine.index_writer.calls[0]["metadata"]
    assert metadata["visibility"] == "private"
    assert metadata["disclosed"] is False


@pytest.mark.asyncio
async def test_add_memory_preserves_visibility_from_metadata() -> None:
    engine = _CrudHarness()

    await engine.add_memory(
        content="metadata write",
        session_id="s1",
        metadata={"visibility": "private", "disclosed": False},
    )

    metadata = engine.index_writer.calls[0]["metadata"]
    assert metadata["visibility"] == "private"
    assert metadata["disclosed"] is False


@pytest.mark.asyncio
async def test_add_memory_explicit_arguments_override_metadata() -> None:
    engine = _CrudHarness()

    await engine.add_memory(
        content="override write",
        session_id="s1",
        metadata={"visibility": "private", "disclosed": False},
        visibility="public",
        disclosed=True,
    )

    metadata = engine.index_writer.calls[0]["metadata"]
    assert metadata["visibility"] == "public"
    assert metadata["disclosed"] is True


def test_memorize_tool_exposes_visibility_parameters() -> None:
    properties = MemoryMemorizeTool().parameters["properties"]
    assert properties["visibility"]["enum"] == ["public", "private"]
    assert properties["visibility"]["default"] == "public"
    assert properties["disclosed"]["type"] == "boolean"
    assert properties["disclosed"]["default"] is True


@pytest.mark.asyncio
async def test_memorize_tool_passes_visibility_to_engine() -> None:
    memory_engine = Mock()
    memory_engine.add_memory = AsyncMock(return_value=42)
    memory_processor = Mock()
    memory_processor.build_memory_from_structured_data = Mock(
        return_value=("private summary", {}, 0.7)
    )
    event = Mock()
    event.unified_msg_origin = "test:private:session-1"
    event.get_message_type = Mock(return_value="friend")
    run_context = Mock()
    run_context.context = Mock()
    run_context.context.event = event
    tool = MemoryMemorizeTool(
        context=Mock(),
        memory_engine=memory_engine,
        memory_processor=memory_processor,
    )

    with patch(
        "astrbot_plugin_livingmemory.core.tools.memory_memorize_tool.get_persona_id",
        new_callable=AsyncMock,
        return_value="persona_a",
    ):
        raw_result = await tool.call(
            run_context,
            memory="robot did something alone",
            visibility="private",
            disclosed=False,
        )

    result = json.loads(raw_result)
    assert result["visibility"] == "private"
    assert result["disclosed"] is False
    call_kwargs = memory_engine.add_memory.await_args.kwargs
    assert call_kwargs["visibility"] == "private"
    assert call_kwargs["disclosed"] is False


def test_private_memory_notice_config_defaults_to_enabled() -> None:
    assert RecallEngineConfig().private_memory_notice_enabled is True
    config = ConfigManager(
        {"recall_engine": {"private_memory_notice_enabled": False}}
    )
    assert (
        config.get("recall_engine.private_memory_notice_enabled", True) is False
    )


def test_memory_recall_maps_config_to_notice_override() -> None:
    enabled = MemoryRecall(
        Mock(),
        ConfigManager({"recall_engine": {"private_memory_notice_enabled": True}}),
        Mock(),
        Mock(),
        Mock(),
    )
    disabled = MemoryRecall(
        Mock(),
        ConfigManager({"recall_engine": {"private_memory_notice_enabled": False}}),
        Mock(),
        Mock(),
        Mock(),
    )

    assert enabled._private_memory_notice() is None
    assert disabled._private_memory_notice() == ""
