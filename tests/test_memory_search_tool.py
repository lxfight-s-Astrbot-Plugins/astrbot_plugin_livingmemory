"""`recall_long_term_memory` 工具的黑盒测试。

工具、检索链、SQLite 与向量索引都是真实实现；只把 AstrBot 宿主（人格与
Provider 查找）替换为桩。断言对象是工具返回的 JSON 与库里的实际数据，
而不是内部协作对象的调用记录。
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio

from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.memory_scope import GLOBAL_MEMORY_SCOPE
from astrbot_plugin_livingmemory.core.tools.memory_search_tool import MemorySearchTool

from tests.real_engine_support import HostContext, close_engine, make_engine

SESSION_ID = "test:private:session-1"
USER_SCOPE = "livingmemory:user:test:user-1"


class _Event:
    def __init__(self, umo: str = SESSION_ID) -> None:
        self.unified_msg_origin = umo
        self.sender_id = "user-1"
        self.sender_name = "Tester"
        self.message_str = "hello"

    def get_sender_id(self):
        return self.sender_id

    def get_sender_name(self):
        return self.sender_name

    def get_self_id(self):
        return "bot-1"

    def get_platform_name(self):
        return "test"

    def get_platform_id(self):
        return "test-instance"

    def get_group_id(self):
        return ""

    def get_message_str(self):
        return self.message_str

    def get_messages(self):
        return []

    def get_extra(self, key, default=None):
        return default


def _run_context(umo: str = SESSION_ID):
    return SimpleNamespace(context=SimpleNamespace(event=_Event(umo)))


@pytest_asyncio.fixture
async def engine(tmp_path):
    engine, db = await make_engine(tmp_path / "engine")
    try:
        yield engine
    finally:
        await close_engine(engine, db)


def _tool(engine, *, config=None, context=None):
    return MemorySearchTool(
        context=context or HostContext(persona_id="persona-a"),
        config_manager=ConfigManager(config or {}),
        memory_engine=engine,
    )


async def _seed(engine, texts_scopes) -> list[int]:
    ids = []
    for content, scope, persona in texts_scopes:
        ids.append(
            await engine.add_memory(
                content, session_id=scope, persona_id=persona, importance=0.8
            )
        )
    return ids


@pytest.mark.asyncio
async def test_session_scope_only_returns_memories_from_that_session(engine):
    own, other = await _seed(
        engine,
        [
            ("User prefers jasmine tea", SESSION_ID, "persona-a"),
            ("User dislikes coffee", "test:private:other-session", "persona-a"),
        ],
    )
    tool = _tool(engine)

    result = json.loads(await tool.call(_run_context(), query="tea coffee"))

    ids = [item["id"] for item in result["results"]]
    assert own in ids
    assert other not in ids
    assert result["applied_filters"] == {
        "session_filtered": True,
        "persona_filtered": True,
    }


@pytest.mark.asyncio
async def test_disabling_session_filtering_reaches_other_sessions(engine):
    _, other = await _seed(
        engine,
        [
            ("User prefers jasmine tea", SESSION_ID, "persona-a"),
            ("User dislikes coffee", "test:private:other-session", "persona-a"),
        ],
    )
    tool = _tool(
        engine,
        config={"filtering_settings": {"use_session_filtering": False}},
    )

    result = json.loads(await tool.call(_run_context(), query="coffee tea"))

    assert other in [item["id"] for item in result["results"]]
    assert result["applied_filters"]["session_filtered"] is False


@pytest.mark.asyncio
async def test_user_scope_shares_memory_across_sessions(engine):
    shared, foreign = await _seed(
        engine,
        [
            ("User keeps a spare key", USER_SCOPE, "persona-a"),
            ("Another user keeps a key", "livingmemory:user:test:other-user", "persona-a"),
        ],
    )
    tool = _tool(
        engine,
        config={"filtering_settings": {"memory_scope_mode": "user"}},
    )

    result = json.loads(
        await tool.call(_run_context("test:private:another-session"), query="spare key")
    )

    ids = [item["id"] for item in result["results"]]
    assert shared in ids
    assert foreign not in ids
    assert result["applied_filters"]["session_filtered"] is False


@pytest.mark.asyncio
async def test_global_scope_crosses_sessions(engine):
    first, second = await _seed(
        engine,
        [
            ("User prefers jasmine tea", GLOBAL_MEMORY_SCOPE, "persona-a"),
            ("User dislikes coffee", GLOBAL_MEMORY_SCOPE, "persona-a"),
        ],
    )
    tool = _tool(engine, config={"filtering_settings": {"memory_scope_mode": "global"}})

    result = json.loads(await tool.call(_run_context(), query="tea coffee"))

    ids = [item["id"] for item in result["results"]]
    assert {first, second} <= set(ids)
    assert result["applied_filters"]["session_filtered"] is False


@pytest.mark.parametrize(
    ("settings", "expected_session_filtered"),
    [
        ({"use_session_filtering": True}, True),
        ({"use_session_filtering": False}, False),
        ({"memory_scope_mode": "session"}, True),
        ({"memory_scope_mode": "global"}, False),
        ({"memory_scope_mode": "user"}, False),
        (
            {"memory_scope_mode": "global", "isolated_sessions": SESSION_ID},
            True,
        ),
    ],
)
@pytest.mark.asyncio
async def test_reported_session_filtering_matches_effective_scope(
    engine, settings, expected_session_filtered
):
    tool = _tool(engine, config={"filtering_settings": settings})

    result = json.loads(await tool.call(_run_context(), query="anything"))

    assert result["applied_filters"]["session_filtered"] is expected_session_filtered


@pytest.mark.asyncio
async def test_persona_filtering_limits_results_to_the_current_persona(engine):
    own, other = await _seed(
        engine,
        [
            ("User prefers jasmine tea", SESSION_ID, "persona-a"),
            ("User dislikes coffee", SESSION_ID, "persona-b"),
        ],
    )
    tool = MemorySearchTool(
        context=HostContext(persona_id="persona-a"),
        config_manager=ConfigManager(
            {"filtering_settings": {"use_persona_filtering": True}}
        ),
        memory_engine=engine,
    )

    result = json.loads(await tool.call(_run_context(), query="tea coffee"))

    ids = [item["id"] for item in result["results"]]
    assert own in ids
    assert other not in ids
    assert result["applied_filters"]["persona_filtered"] is True


@pytest.mark.asyncio
async def test_denies_user_outside_the_whitelist(engine):
    await _seed(engine, [("User prefers jasmine tea", SESSION_ID, "persona-a")])
    tool = _tool(
        engine,
        config={"access_control": {"whitelist_enabled": True, "allowed_ids": "other"}},
    )

    result = json.loads(await tool.call(_run_context(), query="tea"))

    assert result["error"] == "memory access is not allowed"
    assert result["results"] == []


@pytest.mark.asyncio
async def test_results_carry_the_fields_the_model_needs(engine):
    (doc_id,) = await _seed(
        engine, [("User likes story games", SESSION_ID, "persona-a")]
    )
    tool = _tool(engine)

    result = json.loads(await tool.call(_run_context(), query="story games"))

    item = result["results"][0]
    assert item["id"] == doc_id
    assert "story games" in item["content"]
    assert item["importance"] == pytest.approx(0.8)
    assert item["session_id"] == SESSION_ID
    assert item["persona_id"] == "persona-a"
    assert isinstance(item["score"], float)
    assert item["create_time"] is not None


@pytest.mark.asyncio
async def test_include_source_returns_the_original_messages(engine):
    await engine.add_memory(
        "User described a bookshelf project",
        session_id=SESSION_ID,
        persona_id="persona-a",
        source_messages=[
            {"role": "user", "content": "I want to build a bookshelf"},
            {"role": "assistant", "content": "What wood?"},
        ],
    )
    tool = _tool(engine)

    with_source = json.loads(
        await tool.call(_run_context(), query="bookshelf", include_source=True)
    )
    without_source = json.loads(
        await tool.call(_run_context(), query="bookshelf", include_source=False)
    )

    assert with_source["results"][0]["source_messages"][0]["content"] == (
        "I want to build a bookshelf"
    )
    assert "source_messages" not in without_source["results"][0]


@pytest.mark.asyncio
async def test_k_is_limited_by_the_configured_maximum(engine):
    await _seed(
        engine,
        [
            (f"User likes tea variety {index}", SESSION_ID, "persona-a")
            for index in range(6)
        ],
    )
    tool = _tool(engine, config={"recall_engine": {"top_k": 5, "max_k": 3}})

    result = json.loads(await tool.call(_run_context(), query="tea variety", k=10))

    assert len(result["results"]) <= 3


@pytest.mark.asyncio
async def test_k_is_clamped_to_at_least_one(engine):
    await _seed(engine, [("User likes tea", SESSION_ID, "persona-a")])
    tool = _tool(engine)

    result = json.loads(await tool.call(_run_context(), query="tea", k=0))

    assert len(result["results"]) == 1


@pytest.mark.asyncio
async def test_invalid_k_falls_back_to_the_configured_default(engine):
    await _seed(engine, [("User likes tea", SESSION_ID, "persona-a")])
    tool = _tool(engine, config={"recall_engine": {"top_k": 2}})

    result = json.loads(await tool.call(_run_context(), query="tea", k="many"))

    assert len(result["results"]) == 1


@pytest.mark.asyncio
async def test_explicit_recall_records_access_but_passive_recall_does_not(
    engine, monkeypatch
):
    """显式检索计入访问统计，被动检索不计入（#427）。"""
    (doc_id,) = await _seed(engine, [("User likes tea", SESSION_ID, "persona-a")])
    # 访问统计按 3 秒窗口合并写；测试里把窗口压到 0 再触发检索，
    # 断言仍只看库里的最终数据。
    monkeypatch.setattr(engine, "_ACCESS_UPDATE_DEBOUNCE_SECONDS", 0.0)

    async def access_count() -> int:
        record = await engine.get_memory(doc_id)
        metadata = record["metadata"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        return int(metadata.get("access_count") or 0)

    await engine.search_memories("tea", 5, session_id=SESSION_ID, track_access=True)
    await asyncio.sleep(0.05)
    explicit = await access_count()

    await engine.search_memories("tea", 5, session_id=SESSION_ID, track_access=False)
    await asyncio.sleep(0.05)
    passive = await access_count()

    assert explicit >= 1
    assert passive == explicit


@pytest.mark.asyncio
async def test_empty_query_returns_a_structured_error(engine):
    tool = _tool(engine)

    result = json.loads(await tool.call(_run_context(), query="   "))

    assert result == {
        "query": "",
        "count": 0,
        "results": [],
        "error": "query is empty",
    }


@pytest.mark.asyncio
async def test_uninitialized_tool_reports_missing_dependencies():
    tool = MemorySearchTool(context=None, config_manager=None, memory_engine=None)

    result = json.loads(await tool.call(_run_context(), query="测试"))

    assert result["error"] == "memory search tool is not initialized"


@pytest.mark.asyncio
async def test_internal_failure_does_not_leak_details():
    """底层故障时返回稳定的 internal_error，不把异常文本透给模型。

    这里在工具的外部协作对象边界注入失败（引擎抛错），断言的是工具的
    错误契约，而不是引擎的内部实现。
    """
    class _BrokenEngine:
        async def search_memories(self, **_kwargs):
            raise RuntimeError("secret db path")

    tool = MemorySearchTool(
        context=HostContext(persona_id="persona-a"),
        config_manager=ConfigManager(),
        memory_engine=_BrokenEngine(),
    )

    result = json.loads(await tool.call(_run_context(), query="tea"))

    assert result["error"] == "internal_error"
    assert "secret" not in json.dumps(result)
