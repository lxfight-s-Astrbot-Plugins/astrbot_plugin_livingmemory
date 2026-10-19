"""管理员专用长期记忆遗忘工具（#279）测试：权限闸门、校验、限流与审计。"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, Mock, patch

import pytest

from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.tools.memory_forget_tool import (
    MemoryForgetTool,
)

SESSION_ID = "test:private:session-1"
INVALID_IDS_ERROR = "memory_ids must be a non-empty list of integers"


@pytest.fixture(autouse=True)
def _patch_persona_id():
    """默认人格过滤开启；统一把 get_persona_id 桩成 persona_a。"""
    with patch(
        "astrbot_plugin_livingmemory.core.tools.agentic_recall_common.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = "persona_a"
        yield get_persona


def _config(*, agent_tools=None, persona_filtering=True, whitelist=None):
    config = {
        "filtering_settings": {"use_persona_filtering": persona_filtering},
        "agent_tools": {
            "forget_tool_max_memories": 3,
            "forget_tool_cooldown_seconds": 0,
            **(agent_tools or {}),
        },
    }
    if whitelist is not None:
        config["access_control"] = {
            "whitelist_enabled": True,
            "allowed_ids": whitelist,
        }
    return ConfigManager(config)


def _memory(
    memory_id: int,
    *,
    session_id: str = SESSION_ID,
    persona_id: str = "persona_a",
    status: str = "active",
) -> dict:
    return {
        "id": memory_id,
        "text": f"memory {memory_id}",
        "metadata": {
            "session_id": session_id,
            "persona_id": persona_id,
            "status": status,
        },
    }


def _make_engine(memories: dict[int, dict]) -> Mock:
    engine = Mock()
    engine.get_memory = AsyncMock(side_effect=lambda memory_id: memories.get(memory_id))
    engine.archive_memories = AsyncMock(
        side_effect=lambda ids, **kwargs: len([i for i in ids if i in memories])
    )
    return engine


def _make_run_context(*, is_admin: bool = True) -> Mock:
    event = Mock()
    event.unified_msg_origin = SESSION_ID
    event.is_admin = Mock(return_value=is_admin)
    run_context = Mock()
    run_context.context = Mock()
    run_context.context.event = event
    return run_context


def _tool(engine: Mock, config: ConfigManager | None = None) -> MemoryForgetTool:
    return MemoryForgetTool(
        context=Mock(),
        config_manager=config or _config(),
        memory_engine=engine,
    )


@pytest.mark.asyncio
async def test_forget_tool_returns_not_initialized_error():
    tool = MemoryForgetTool(context=None, config_manager=None, memory_engine=None)

    result = json.loads(await tool.call(_make_run_context(), memory_ids=[1]))

    assert result == {
        "forgotten": False,
        "error": "memory forget tool is not initialized",
    }


@pytest.mark.asyncio
async def test_forget_tool_rejects_whitelist_denied_session():
    engine = _make_engine({1: _memory(1)})
    tool = _tool(engine, config=_config(whitelist="another-user"))
    run_context = _make_run_context()
    run_context.context.event.get_sender_id = Mock(return_value="user-1")

    result = json.loads(await tool.call(run_context, memory_ids=[1]))

    assert result == {"forgotten": False, "error": "memory access is not allowed"}
    engine.archive_memories.assert_not_awaited()


@pytest.mark.asyncio
async def test_forget_tool_rejects_non_admin():
    engine = _make_engine({1: _memory(1)})
    tool = _tool(engine)

    result = json.loads(
        await tool.call(_make_run_context(is_admin=False), memory_ids=[1])
    )

    assert result == {"forgotten": False, "error": "admin permission required"}
    engine.archive_memories.assert_not_awaited()


@pytest.mark.asyncio
async def test_forget_tool_rejects_when_admin_check_raises():
    engine = _make_engine({1: _memory(1)})
    tool = _tool(engine)
    run_context = _make_run_context()
    run_context.context.event.is_admin = Mock(side_effect=RuntimeError("boom"))

    result = json.loads(await tool.call(run_context, memory_ids=[1]))

    assert result["forgotten"] is False
    assert result["error"] == "admin permission required"
    engine.archive_memories.assert_not_awaited()


@pytest.mark.parametrize(
    "memory_ids",
    [[], "1", [1, "2"], [1, True], [0], [-3], [1.5]],
    ids=["empty", "string", "mixed", "bool", "zero", "negative", "float"],
)
@pytest.mark.asyncio
async def test_forget_tool_rejects_invalid_memory_ids(memory_ids):
    engine = _make_engine({1: _memory(1)})
    tool = _tool(engine)

    result = json.loads(await tool.call(_make_run_context(), memory_ids=memory_ids))

    assert result["forgotten"] is False
    assert result["error"] == INVALID_IDS_ERROR
    engine.archive_memories.assert_not_awaited()


@pytest.mark.asyncio
async def test_forget_tool_rejects_too_many_ids():
    engine = _make_engine({index: _memory(index) for index in range(1, 5)})
    tool = _tool(
        engine, config=_config(agent_tools={"forget_tool_max_memories": 3})
    )

    result = json.loads(
        await tool.call(_make_run_context(), memory_ids=[1, 2, 3, 4])
    )

    assert result == {
        "forgotten": False,
        "error": "too many memory_ids",
        "max_memories": 3,
    }
    engine.archive_memories.assert_not_awaited()


@pytest.mark.parametrize(
    ("memories", "memory_ids", "invalid_ids"),
    [
        ({1: _memory(1)}, [1, 9], [9]),
        ({1: _memory(1), 2: _memory(2, status="archived")}, [1, 2], [2]),
        (
            {1: _memory(1), 2: _memory(2, session_id="test:private:other")},
            [1, 2],
            [2],
        ),
        ({1: _memory(1), 2: _memory(2, persona_id="persona_b")}, [1, 2], [2]),
    ],
    ids=["missing-id", "archived-id", "other-session", "other-persona"],
)
@pytest.mark.asyncio
async def test_forget_tool_rejects_scope_mismatch_all_or_nothing(
    memories, memory_ids, invalid_ids
):
    engine = _make_engine(memories)
    tool = _tool(engine)

    result = json.loads(
        await tool.call(_make_run_context(), memory_ids=memory_ids)
    )

    assert result["forgotten"] is False
    assert result["invalid_ids"] == invalid_ids
    engine.archive_memories.assert_not_awaited()


@pytest.mark.asyncio
async def test_forget_tool_archives_visible_memories_with_audit():
    engine = _make_engine({1: _memory(1), 2: _memory(2)})
    tool = _tool(engine)

    result = json.loads(
        await tool.call(
            _make_run_context(),
            memory_ids=[2, 1, 2],
            reason="  用户要求删除  ",
        )
    )

    assert result["forgotten"] is True
    assert result["memory_ids"] == [2, 1]
    assert result["archived_count"] == 2
    assert result["reversible"] is True
    call = engine.archive_memories.await_args
    assert call.args[0] == [2, 1]
    assert call.kwargs["audit"] == {
        "archived_by": "llm_tool",
        "archive_reason": "用户要求删除",
        "archive_session_id": SESSION_ID,
    }


@pytest.mark.asyncio
async def test_forget_tool_reports_failure_when_nothing_archived():
    engine = _make_engine({1: _memory(1)})
    engine.archive_memories = AsyncMock(return_value=0)
    tool = _tool(engine)

    result = json.loads(await tool.call(_make_run_context(), memory_ids=[1]))

    assert result == {
        "forgotten": False,
        "error": "no active memories were archived",
    }
    # 失败不消耗冷却时间，也未记录限流状态
    assert tool.rate_limit_state == {}


@pytest.mark.asyncio
async def test_forget_tool_enforces_cooldown_then_allows_after_window():
    engine = _make_engine({1: _memory(1), 2: _memory(2)})
    tool = _tool(
        engine, config=_config(agent_tools={"forget_tool_cooldown_seconds": 60})
    )

    first = json.loads(await tool.call(_make_run_context(), memory_ids=[1]))
    assert first["forgotten"] is True

    second = json.loads(await tool.call(_make_run_context(), memory_ids=[2]))
    assert second["forgotten"] is False
    assert second["error"] == "rate limited"
    assert 1 <= second["retry_after_seconds"] <= 60
    assert engine.archive_memories.await_count == 1

    # 把上次归档时间拨到冷却窗口之前，下一次调用应被允许
    tool.rate_limit_state[SESSION_ID] = time.time() - 61
    third = json.loads(await tool.call(_make_run_context(), memory_ids=[2]))
    assert third["forgotten"] is True
    assert engine.archive_memories.await_count == 2


@pytest.mark.asyncio
async def test_forget_tool_zero_cooldown_allows_repeated_calls():
    engine = _make_engine({1: _memory(1), 2: _memory(2)})
    tool = _tool(
        engine, config=_config(agent_tools={"forget_tool_cooldown_seconds": 0})
    )

    first = json.loads(await tool.call(_make_run_context(), memory_ids=[1]))
    second = json.loads(await tool.call(_make_run_context(), memory_ids=[2]))

    assert first["forgotten"] is True
    assert second["forgotten"] is True
    assert engine.archive_memories.await_count == 2


@pytest.mark.asyncio
async def test_forget_tool_hides_internal_exception_details():
    engine = _make_engine({1: _memory(1)})
    engine.archive_memories = AsyncMock(side_effect=RuntimeError("secret db path"))
    tool = _tool(engine)

    raw_result = await tool.call(_make_run_context(), memory_ids=[1])

    assert json.loads(raw_result) == {
        "forgotten": False,
        "error": "internal_error",
    }
    assert "secret db path" not in raw_result


@pytest.mark.asyncio
async def test_forget_tool_propagates_cancellation():
    engine = _make_engine({1: _memory(1)})
    engine.get_memory = AsyncMock(side_effect=asyncio.CancelledError())
    tool = _tool(engine)

    with pytest.raises(asyncio.CancelledError):
        await tool.call(_make_run_context(), memory_ids=[1])
