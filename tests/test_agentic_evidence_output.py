"""Beta 完整 JSON 配额、连续续读与异步原文读取的回归测试。"""

import asyncio
import gc
import json
import warnings

import pytest
import pytest_asyncio

from astrbot_plugin_livingmemory.core.managers.agentic_recall_types import (
    AgenticRecallOutcome,
)
from astrbot_plugin_livingmemory.core.tools.agentic_recall_budget import (
    get_or_create_budget,
)
from tests.test_agentic_engine_retrieval import _identities, _make_engine
from tests.test_agentic_tools import _config, _context, _event, _tools

SCOPE = "test:private:s1"


@pytest_asyncio.fixture
async def engine(tmp_path):
    instance = await _make_engine(tmp_path)
    yield instance
    await instance.close()


def config_with_cap(cap):
    return _config(
        agent_tools={
            "enable_recall_tool": True,
            "enable_agentic_recall_beta": True,
            "agentic_recall_max_result_chars": cap,
        }
    )


async def source_memory(engine, content, messages, **kwargs):
    return await engine.add_memory(
        content=content,
        session_id=SCOPE,
        source_messages=[
            {
                "role": "user",
                "sender_id": "1001",
                "timestamp": float(i),
                "content": text,
            }
            for i, text in enumerate(messages)
        ],
        **kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "count,cap,with_source",
    [
        (10, 12000, False),
        (3, 12000, True),
        (1, 500, False),
        (1, 500, True),
    ],
)
async def test_search_caps_whole_nested_payload(engine, count, cap, with_source):
    for i in range(count):
        await source_memory(engine, f"CAPTEST item {i} " + "x" * 1400, ["s" * 9000])
    search, _ = _tools(engine, config_with_cap(cap))
    raw = await search.call(
        _context(_event()), query="CAPTEST", k=count, include_source=with_source
    )
    result = json.loads(raw)
    assert result["count"] > 0
    assert result["results"][0]["content"]
    assert len(raw) <= cap


@pytest.mark.asyncio
async def test_many_short_source_messages_obey_cap_and_all_can_be_read(engine):
    expected = [f"short sentence {i}" for i in range(140)]
    mid = await source_memory(engine, "short summary", expected)
    _, read = _tools(engine)
    cursor = ""
    text = []
    for _ in range(5):
        raw = await read.call(
            _context(_event()), memory_id=mid, include_source=True, cursor=cursor
        )
        assert len(raw) <= 12000
        page = json.loads(raw)
        text.extend(item["content"] for item in page["source_messages"])
        if not page["has_more"]:
            break
        assert page["next_cursor"] != cursor
        cursor = page["next_cursor"]
    assert "".join(text) == "".join(expected)
    assert not page["has_more"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["facts", "source"])
@pytest.mark.parametrize("cap", [500, 1500])
async def test_escaped_evidence_continuation_loses_no_characters(engine, kind, cap):
    text = '"quoted"\\path;\t' * 90
    mid = (
        await engine.add_memory(content=text, session_id=SCOPE)
        if kind == "facts"
        else await source_memory(engine, "short summary", [text])
    )
    _, read = _tools(engine, config_with_cap(cap))
    parts = []
    cursor = ""
    for _ in range(25):
        raw = await read.call(
            _context(_event()),
            memory_id=mid,
            include_source=kind == "source",
            cursor=cursor,
        )
        assert len(raw) <= cap
        page = json.loads(raw)
        parts.append(
            "".join(page["facts"])
            if kind == "facts"
            else "".join(item["content"] for item in page["source_messages"])
        )
        if not page["has_more"]:
            break
        assert page["next_cursor"] != cursor
        cursor = page["next_cursor"]
    assert not page["has_more"]
    assert "".join(parts) == text


@pytest.mark.asyncio
async def test_search_source_cursor_resumes_exactly_in_read_tool(engine):
    text = '"quoted"\\path;' * 300
    mid = await source_memory(engine, "SOURCEPAGE summary", [text])
    search, read = _tools(engine, config_with_cap(1000))
    first = json.loads(
        await search.call(_context(_event()), query="SOURCEPAGE", include_source=True)
    )
    item = first["results"][0]
    assert item["id"] == mid
    assert item["has_more"]
    joined = "".join(message["content"] for message in item["source_messages"])
    cursor = item["next_cursor"]
    for _ in range(20):
        page = json.loads(
            await read.call(
                _context(_event()), memory_id=mid, include_source=True, cursor=cursor
            )
        )
        joined += "".join(message["content"] for message in page["source_messages"])
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
    assert joined == text


@pytest.mark.asyncio
async def test_output_reduced_speaker_page_does_not_skip_unreturned_candidates(engine):
    ids = []
    for i in range(7):
        ids.append(
            await engine.add_memory(
                content=f"history {i} " + "detail " * 100,
                session_id=SCOPE,
                metadata={
                    "participant_identities": _identities("qq:1001"),
                    "source_time_start": f"2026-01-{i + 1:02d}T10:00:00",
                },
            )
        )
    search, _ = _tools(engine, config_with_cap(500))
    cursor = ""
    seen = []
    for _ in range(10):
        raw = await search.call(
            _context(_event()),
            target="current_speaker",
            k=5,
            search_offset=cursor,
            exclude_ids=seen,
        )
        assert len(raw) <= 500
        page = json.loads(raw)
        seen.extend(item["id"] for item in page["results"])
        if not page["has_more"]:
            break
        assert page["next_cursor"] and page["next_cursor"] != cursor
        cursor = page["next_cursor"]
    assert seen == list(reversed(ids))


@pytest.mark.asyncio
async def test_source_read_error_is_not_timeout_and_sibling_finishes(engine):
    first_id = await source_memory(engine, "SOURCEFAIL first", ["first source"])
    second_id = await source_memory(engine, "SOURCEFAIL second", ["second source"])
    original = engine.get_memory_source
    completed = asyncio.Event()

    async def read_source(mid):
        if mid == first_id:
            raise RuntimeError("temporary storage error")
        await asyncio.sleep(0.01)
        result = await original(mid)
        completed.set()
        return result

    engine.get_memory_source = read_source
    search, _ = _tools(engine)
    out = json.loads(
        await search.call(_context(_event()), query="SOURCEFAIL", include_source=True)
    )
    results = {item["id"]: item for item in out["results"]}
    assert completed.is_set()
    assert results[second_id]["source_status"] == "ok"
    assert results[first_id]["source_status"] == "error"
    assert out["partial"] is True
    assert out["remaining_budget"]["remaining_seconds"] > 10


@pytest.mark.asyncio
async def test_source_timeout_keeps_completed_items_and_awaits_cancel(engine):
    slow_id = await source_memory(engine, "TIMEDREAD slow", ["slow source"])
    fast_id = await source_memory(engine, "TIMEDREAD fast", ["fast source"])
    original = engine.get_memory_source
    started, stopped = asyncio.Event(), asyncio.Event()
    cfg = _config()
    event = _event()
    budget = get_or_create_budget(event, cfg)
    budget.time_budget_seconds = 0.3

    async def read_source(mid):
        if mid != slow_id:
            return await original(mid)
        started.set()
        try:
            await asyncio.sleep(10)
        finally:
            stopped.set()

    engine.get_memory_source = read_source
    search, _ = _tools(engine, cfg)
    out = json.loads(
        await search.call(_context(event), query="TIMEDREAD", include_source=True)
    )
    results = {item["id"]: item for item in out["results"]}
    assert started.is_set() and stopped.is_set()
    assert out["partial"] is True
    assert results[slow_id]["source_status"] == "timeout"
    assert results[fast_id]["source_status"] == "ok"
    assert results[fast_id]["source_messages"][0]["content"] == "fast source"
    assert out["remaining_budget"]["remaining_seconds"] == 0


@pytest.mark.asyncio
async def test_zero_time_before_source_creates_no_unawaited_coroutine(engine):
    await source_memory(engine, "ZEROPOOL summary", ["retained"])
    cfg = _config()
    event = _event()
    budget = get_or_create_budget(event, cfg)
    original = engine.search_memories_agentic

    async def exhaust_after_search(**kwargs):
        result = await original(**kwargs)
        budget.elapsed_seconds = budget.time_budget_seconds
        return result

    engine.search_memories_agentic = exhaust_after_search
    search, _ = _tools(engine, cfg)
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", RuntimeWarning)
        out = json.loads(
            await search.call(_context(event), query="ZEROPOOL", include_source=True)
        )
        gc.collect()
    assert out["partial"] is True
    assert not [
        str(item.message)
        for item in captured
        if "was never awaited" in str(item.message)
    ]


@pytest.mark.asyncio
async def test_read_cancellation_propagates_and_is_charged(engine):
    mid = await engine.add_memory(content="cancel probe", session_id=SCOPE)
    started, stopped = asyncio.Event(), asyncio.Event()

    async def wait_for_cancel(*args, **kwargs):
        started.set()
        try:
            await asyncio.sleep(10)
        finally:
            stopped.set()

    engine.resolve_live_facts = wait_for_cancel
    cfg = _config()
    _, read = _tools(engine, cfg)
    event = _event()
    budget = get_or_create_budget(event, cfg)
    task = asyncio.create_task(read.call(_context(event), memory_id=mid))
    await started.wait()
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()
    assert budget.elapsed_seconds >= 0.02


@pytest.mark.asyncio
async def test_engine_error_message_is_returned(engine):
    async def failed_search(**kwargs):
        return AgenticRecallOutcome(
            results=[],
            status="error",
            message="retrieval_routes_failed: vector unavailable",
        )

    engine.search_memories_agentic = failed_search
    search, _ = _tools(engine)
    out = json.loads(await search.call(_context(_event()), query="trip"))
    assert out["status"] == "error"
    assert "retrieval_routes_failed" in out["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["scope", "forgotten"])
async def test_source_recheck_never_keeps_now_inaccessible_summary(engine, changed):
    mid = await source_memory(engine, "RECHECK secret summary", ["private source"])
    original = engine.search_memories_agentic

    async def change_after_search(**kwargs):
        result = await original(**kwargs)
        key, value = (
            ("session_id", "other:scope")
            if changed == "scope"
            else ("status", "forgotten")
        )
        await engine.db_connection.execute(
            f"UPDATE documents SET metadata = json_set(metadata, '$.{key}', ?) WHERE id = ?",
            (value, mid),
        )
        await engine.db_connection.commit()
        return result

    engine.search_memories_agentic = change_after_search
    search, _ = _tools(engine)
    raw = await search.call(_context(_event()), query="RECHECK", include_source=True)
    out = json.loads(raw)
    assert "secret summary" not in raw and "private source" not in raw
    assert out["results"] == [] and out["partial"]


@pytest.mark.asyncio
async def test_source_cursor_requires_include_source_in_followup(engine):
    mid = await source_memory(engine, "source summary", ["source evidence"])
    _, read = _tools(engine)
    out = json.loads(await read.call(_context(_event()), memory_id=mid, cursor="s:0:0"))
    assert out["status"] == "invalid_query"
    assert "include_source=true" in out["error"]


def test_status_messages_obey_whole_json_cap_with_escaped_text():
    from astrbot_plugin_livingmemory.core.tools.agentic_recall_common import (
        bounded_status,
    )

    raw = bounded_status(
        {
            "status": "invalid_query",
            "count": 0,
            "results": [],
            "query": '"quoted"\\path;' * 300,
            "error": '"quoted"\\path;' * 300,
            "remaining_budget": {"remaining_calls": 3, "remaining_seconds": 20.0},
        },
        500,
    )
    assert len(raw) <= 500
    assert json.loads(raw)["status"] == "invalid_query"
