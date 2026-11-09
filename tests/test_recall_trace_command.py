"""``/lmem trace`` command output."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from astrbot_plugin_livingmemory.core.command_handler import CommandHandler
from astrbot_plugin_livingmemory.core.retrieval.recall_trace import RecallTraceRecorder


class _Event:
    def __init__(self) -> None:
        self.unified_msg_origin = "test:private:session"

    def plain_result(self, message):
        return message


def _handler(recorder: RecallTraceRecorder) -> CommandHandler:
    handler = CommandHandler.__new__(CommandHandler)
    handler.memory_engine = SimpleNamespace(trace_recorder=recorder)
    handler.config_manager = None
    return handler


async def _run(handler: CommandHandler, arg: str = "") -> list[str]:
    return [message async for message in handler.handle_trace(_Event(), arg)]


def _seed(recorder: RecallTraceRecorder):
    trace = recorder.begin(query="coffee", session_id="test:private:session")
    recorder.route(trace, "document", candidates=3, ms=2.0)
    trace.filters = {
        "min_similarity": 0.5,
        "min_importance": 0.1,
        "rejected": {"status": 2},
    }
    trace.candidates = [
        {
            "doc_id": 9,
            "final_score": 0.8,
            "rrf": 0.8,
            "bm25": 0.5,
            "vector": 0.7,
            "breakdown": {},
        }
    ]
    trace_id = recorder.commit(trace)
    recorder.update_injected(trace_id, [{"doc_id": 9, "score": 0.8, "chars": 12}], 42)
    return trace_id


@pytest.mark.asyncio
async def test_trace_command_disabled() -> None:
    recorder = RecallTraceRecorder({"enabled": False})
    messages = await _run(_handler(recorder))

    assert len(messages) == 1
    assert "召回追踪已关闭" in messages[0]


@pytest.mark.asyncio
async def test_trace_command_empty() -> None:
    messages = await _run(_handler(RecallTraceRecorder({})))

    assert messages == ["暂无召回追踪记录。"]


@pytest.mark.asyncio
async def test_trace_command_lists_recent_summaries() -> None:
    recorder = RecallTraceRecorder({})
    _seed(recorder)
    _seed(recorder)

    messages = await _run(_handler(recorder), "2")

    assert len(messages) == 1
    text = messages[0]
    assert "最近召回追踪（2 条）" in text
    assert "路由=document:3" in text
    assert "注入=1条/42字符" in text


@pytest.mark.asyncio
async def test_trace_command_detail_by_id() -> None:
    recorder = RecallTraceRecorder({})
    trace_id = _seed(recorder)

    messages = await _run(_handler(recorder), trace_id)

    text = messages[0]
    assert trace_id in text
    assert "路由 document: 候选=3 ok=True" in text
    assert "拒绝原因: status:2" in text
    assert "候选 #9: 分数=0.8" in text
    assert "注入: 1 条 / 42 字符" in text


@pytest.mark.asyncio
async def test_trace_command_unknown_id() -> None:
    recorder = RecallTraceRecorder({})
    messages = await _run(_handler(recorder), "deadbeef")

    assert messages == ["未找到追踪记录: deadbeef"]


def test_plugin_registers_trace_command() -> None:
    import inspect

    from astrbot_plugin_livingmemory.main import LivingMemoryPlugin

    assert inspect.isasyncgenfunction(LivingMemoryPlugin.trace)
