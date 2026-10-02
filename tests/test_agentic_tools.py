"""Beta 搜索/深读工具与最小合成集成场景的测试。"""

import json
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio

from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.models.memory_atom import AtomStatus, MemoryAtom
from astrbot_plugin_livingmemory.core.tools.memory_agentic_search_tool import (
    AgenticMemorySearchTool,
)
from astrbot_plugin_livingmemory.core.tools.memory_read_tool import MemoryReadTool
from tests.test_agentic_engine_retrieval import _identities, _make_engine


def _config(persona_filtering=False, **overrides):
    config = {
        "recall_engine": {"top_k": 5, "max_k": 10},
        "filtering_settings": {"use_persona_filtering": persona_filtering},
        "access_control": {"whitelist_enabled": False},
        "agent_tools": {
            "enable_recall_tool": True,
            "enable_agentic_recall_beta": True,
            "agentic_recall_max_calls": 4,
            "agentic_recall_time_budget_seconds": 20,
            "agentic_recall_max_result_chars": 12000,
        },
    }
    for key, value in overrides.items():
        config[key] = value
    return ConfigManager(config)


def _event(sender_id="1001", platform="qq", name="小明", umo="test:private:s1"):
    event = Mock()
    event.unified_msg_origin = umo
    event.get_sender_id = Mock(return_value=sender_id)
    event.get_sender_name = Mock(return_value=name)
    event.get_platform_name = Mock(return_value=platform)
    return event


def _context(event):
    """真实宿主形状：ContextWrapper.context -> AstrAgentContext（含 event）。"""
    inner = Mock()
    inner.event = event
    wrapper = Mock()
    wrapper.context = inner
    return wrapper


def _plugin_context(persona_id=None):
    """真实宿主形状：插件 Context（含 conversation_manager/persona_manager）。"""
    context = Mock()
    context.conversation_manager = Mock()
    context.conversation_manager.get_curr_conversation_id = AsyncMock(
        return_value="conv-1"
    )
    conversation = Mock()
    conversation.persona_id = persona_id
    context.conversation_manager.get_conversation = AsyncMock(
        return_value=conversation
    )
    context.persona_manager = Mock()
    context.persona_manager.get_default_persona_v3 = AsyncMock(return_value=None)
    return context


def _tools(engine, config=None, plugin_context=None):
    config = config or _config()
    search = AgenticMemorySearchTool(
        context=plugin_context or Mock(),
        config_manager=config,
        memory_engine=engine,
    )
    read = MemoryReadTool(
        context=plugin_context or Mock(),
        config_manager=config,
        memory_engine=engine,
    )
    return search, read


def _loads(result):
    return json.loads(result)


def _patched_host_sp():
    """测试环境桩掉宿主 service pool，使 get_persona_id 走会话人格分支。"""
    from unittest.mock import patch

    sp_mock = Mock()
    sp_mock.get_async = AsyncMock(return_value={})
    return patch("astrbot_plugin_livingmemory.core.utils.sp", sp_mock)


@pytest_asyncio.fixture
async def engine(tmp_path):
    instance = await _make_engine(tmp_path)
    yield instance
    await instance.close()


class TestPersonaIsolation:
    """R1：人格过滤在真实宿主上下文形状下必须生效。"""

    @pytest.mark.asyncio
    async def test_search_honors_persona_boundary(self, engine):
        await engine.add_memory(
            content="人格A的私有约定KEYWORD",
            session_id="test:private:s1",
            persona_id="personaA",
        )
        await engine.add_memory(
            content="人格B的私密约定KEYWORD",
            session_id="test:private:s1",
            persona_id="personaB",
        )
        search, _ = _tools(
            engine,
            config=_config(persona_filtering=True),
            plugin_context=_plugin_context(persona_id="personaA"),
        )
        with _patched_host_sp():
            out = _loads(await search.call(_context(_event()), query="KEYWORD", k=5))
        contents = " ".join(item["content"] for item in out["results"])
        assert "人格A的私有约定KEYWORD" in contents
        assert "人格B的私密约定KEYWORD" not in contents
        assert out["applied_filters"]["persona_filtered"] is True

    @pytest.mark.asyncio
    async def test_deep_read_cross_persona_id_is_not_found(self, engine):
        persona_b_id = await engine.add_memory(
            content="人格B的私密事实",
            session_id="test:private:s1",
            persona_id="personaB",
        )
        _, read = _tools(
            engine,
            config=_config(persona_filtering=True),
            plugin_context=_plugin_context(persona_id="personaA"),
        )
        with _patched_host_sp():
            out = _loads(
                await read.call(_context(_event()), memory_id=persona_b_id)
            )
        assert out["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_persona_filtering_disabled_sees_both(self, engine):
        await engine.add_memory(
            content="人格B的私密约定KEYWORD",
            session_id="test:private:s1",
            persona_id="personaB",
        )
        search, _ = _tools(
            engine,
            config=_config(persona_filtering=False),
            plugin_context=_plugin_context(persona_id="personaA"),
        )
        out = _loads(await search.call(_context(_event()), query="KEYWORD", k=5))
        assert out["applied_filters"]["persona_filtered"] is False
        assert len(out["results"]) == 1


class TestAgenticSearchTool:
    @pytest.mark.asyncio
    async def test_legacy_style_query_call_still_works(self, engine):
        await engine.add_memory(content="我们都喜欢周末打羽毛球的约定", session_id="test:private:s1")
        search, _ = _tools(engine)
        out = _loads(
            await search.call(_context(_event()), query="羽毛球 约定", k=3)
        )
        assert out["status"] == "success"
        assert out["count"] == 1
        item = out["results"][0]
        for field in (
            "id",
            "content",
            "score",
            "importance",
            "session_id",
            "create_time",
        ):
            assert field in item
        assert out["remaining_budget"]["remaining_calls"] == 3

    @pytest.mark.asyncio
    async def test_current_speaker_starts_without_keywords(self, engine):
        await engine.add_memory(
            content="小明A账号的露营经历",
            session_id="test:private:s1",
            metadata={"participant_identities": _identities("qq:1001")},
        )
        search, _ = _tools(engine)
        out = _loads(
            await search.call(_context(_event()), query="", target="current_speaker")
        )
        assert out["status"] == "success"
        assert out["count"] == 1
        assert out["identity_status"] == "identity_confirmed"
        assert out["results"][0]["match_basis"] == "identity_confirmed"

    @pytest.mark.asyncio
    async def test_missing_sender_returns_identity_unavailable(self, engine):
        search, _ = _tools(engine)
        out = _loads(
            await search.call(
                _context(_event(sender_id="")), query="", target="current_speaker"
            )
        )
        assert out["status"] == "identity_unavailable"

    @pytest.mark.asyncio
    async def test_budget_shared_and_exhausted(self, engine):
        await engine.add_memory(content="预算测试记忆A", session_id="test:private:s1")
        search, _ = _tools(engine)
        event = _event()
        last = None
        for _ in range(4):
            last = _loads(await search.call(_context(event), query="预算测试"))
        assert last["remaining_budget"]["remaining_calls"] == 0
        exhausted = _loads(await search.call(_context(event), query="预算测试"))
        assert exhausted["status"] == "budget_exhausted"
        assert exhausted["count"] == 0

    @pytest.mark.asyncio
    async def test_budget_isolated_between_events(self, engine):
        await engine.add_memory(content="预算隔离记忆", session_id="test:private:s1")
        search, _ = _tools(engine)
        for _ in range(4):
            await search.call(_context(_event()), query="预算隔离")
        fresh = _loads(await search.call(_context(_event("9999")), query="预算隔离"))
        # 新事件是新请求，预算独立
        assert fresh["status"] in ("success", "no_candidates")

    @pytest.mark.asyncio
    async def test_exclude_ids_continuation(self, engine):
        ids = [
            await engine.add_memory(
                content=f"续查测试经历{i}", session_id="test:private:s1"
            )
            for i in range(2)
        ]
        search, _ = _tools(engine)
        first = _loads(await search.call(_context(_event()), query="续查测试", k=1))
        second = _loads(
            await search.call(
                _context(_event()),
                query="续查测试",
                k=1,
                exclude_ids=[first["results"][0]["id"]],
            )
        )
        assert second["results"][0]["id"] != first["results"][0]["id"]
        assert second["results"][0]["id"] in ids

    @pytest.mark.asyncio
    async def test_bad_time_format_returns_invalid_query(self, engine):
        search, _ = _tools(engine)
        out = _loads(
            await search.call(_context(_event()), query="x", start_time="not-a-date")
        )
        assert out["status"] == "invalid_query"

    @pytest.mark.asyncio
    async def test_conversation_empty_query_keeps_baseline_error(self, engine):
        search, _ = _tools(engine)
        out = _loads(await search.call(_context(_event()), query="  "))
        assert out["status"] == "invalid_query"
        assert out["error"] == "query is empty"

    @pytest.mark.asyncio
    async def test_include_source_respects_one_total_output_cap(self, engine):
        """R5：多条原文共享一份总输出预算，不得各自拿满后拼接超限。"""
        config = _config()
        long_source = [
            {
                "role": "user",
                "sender_id": "1001",
                "timestamp": float(i),
                "content": f"第{i}条原文" + "长" * 900,
            }
            for i in range(3)
        ]
        for i in range(3):
            await engine.add_memory(
                content=f"上限测试记忆{i}",
                session_id="test:private:s1",
                source_messages=long_source,
            )
        search, _ = _tools(engine, config=config)
        out_raw = await search.call(
            _context(_event()), query="上限测试", k=3, include_source=True
        )
        assert len(out_raw) <= 12000
        out = json.loads(out_raw)
        assert out["status"] == "success"
        assert out["count"] == 3


class TestMemoryReadTool:
    @pytest.mark.asyncio
    async def test_read_facts_and_source_pages_with_cursor(self, engine):
        source = [
            {"role": "user", "sender_id": "1001", "timestamp": 100.0, "content": f"原文{i}"}
            for i in range(5)
        ]
        mid = await engine.add_memory(
            content="深读测试记忆",
            session_id="test:private:s1",
            source_messages=source,
        )
        _, read = _tools(engine)
        out = _loads(
            await read.call(_context(_event()), memory_id=mid, include_source=True)
        )
        assert out["status"] == "success"
        assert out["source_status"] == "ok"
        assert len(out["facts"]) >= 1
        assert len(out["source_messages"]) == 5
        assert out["has_more"] is False
        assert out["source_messages"][0]["role"] == "user"

    @pytest.mark.asyncio
    async def test_long_facts_have_a_working_continuation(self, engine):
        """R4：超长事实被截断后，用 next_cursor 能读到后半段（无原文记忆）。"""
        config = _config()
        long_fact = "事实" + "字" * 1500
        mid = await engine.add_memory(content=long_fact, session_id="test:private:s1")
        _, read = _tools(engine, config=config)

        # 收窄单次返回预算到 1500 字符，模拟截断
        from astrbot_plugin_livingmemory.core.tools.agentic_recall_budget import (
            get_or_create_budget,
        )

        event = _event()
        budget = get_or_create_budget(event, config)
        budget.max_result_chars = 1500

        first = _loads(await read.call(_context(event), memory_id=mid))
        assert first["status"] == "success"
        assert first["truncated"] is True
        assert first["has_more"] is True
        assert first["next_cursor"].startswith("f:")
        joined = "".join(first["facts"])
        assert len(joined) < len(long_fact)

        second = _loads(
            await read.call(_context(event), memory_id=mid, cursor=first["next_cursor"])
        )
        assert second["status"] == "success"
        rest = "".join(second["facts"])
        assert rest  # 续读拿到了剩余内容
        assert joined + rest == long_fact
        assert second["has_more"] is False

    @pytest.mark.asyncio
    async def test_single_long_source_has_a_working_continuation(self, engine):
        """R4：单条超长原文被截断后，续读能拿到剩余字符。"""
        config = _config()
        long_content = "段" + "文" * 2000
        mid = await engine.add_memory(
            content="长原文测试",
            session_id="test:private:s1",
            source_messages=[
                {
                    "role": "user",
                    "sender_id": "1001",
                    "timestamp": 1.0,
                    "content": long_content,
                }
            ],
        )
        _, read = _tools(engine, config=config)
        from astrbot_plugin_livingmemory.core.tools.agentic_recall_budget import (
            get_or_create_budget,
        )

        event = _event()
        budget = get_or_create_budget(event, config)
        budget.max_result_chars = 2000

        first = _loads(
            await read.call(_context(event), memory_id=mid, include_source=True)
        )
        assert first["source_status"] == "ok"
        assert first["truncated"] is True
        assert first["next_cursor"].startswith("s:")
        part1 = first["source_messages"][0]["content"]
        assert len(part1) < len(long_content)

        second = _loads(
            await read.call(
                _context(event),
                memory_id=mid,
                include_source=True,
                cursor=first["next_cursor"],
            )
        )
        part2 = "".join(m["content"] for m in second["source_messages"])
        assert part1 + part2 == long_content
        assert second["has_more"] is False

    @pytest.mark.asyncio
    async def test_unknown_id_not_found(self, engine):
        _, read = _tools(engine)
        out = _loads(await read.call(_context(_event()), memory_id=99999))
        assert out["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_cross_scope_id_indistinguishable(self, engine):
        mid = await engine.add_memory(
            content="隔离会话的秘密记忆", session_id="test:private:other"
        )
        _, read = _tools(engine)
        out = _loads(await read.call(_context(_event()), memory_id=mid))
        assert out["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_source_withheld_when_atoms_forgotten(self, engine):
        atoms = [
            MemoryAtom(parent_memory_id=0, content="有效事实"),
            MemoryAtom(parent_memory_id=0, content="被遗忘的事实"),
        ]
        source = [{"role": "user", "sender_id": "1001", "timestamp": 1.0, "content": "原文"}]
        mid = await engine.add_memory(
            content="混合生命周期记忆",
            session_id="test:private:s1",
            atoms=atoms,
            source_messages=source,
        )
        await engine.db_connection.execute(
            "UPDATE memory_atoms SET status = ? WHERE parent_memory_id = ? AND content LIKE ?",
            (AtomStatus.FORGOTTEN.value, mid, "%被遗忘%"),
        )
        await engine.db_connection.commit()

        _, read = _tools(engine)
        out = _loads(
            await read.call(_context(_event()), memory_id=mid, include_source=True)
        )
        assert out["status"] == "success"
        assert out["source_status"] == "lifecycle_limited"
        assert out["source_messages"] == []
        assert any("有效事实" in fact for fact in out["facts"])

    @pytest.mark.asyncio
    async def test_source_unavailable_when_not_retained(self, engine):
        mid = await engine.add_memory(
            content="没有保留原文的记忆", session_id="test:private:s1"
        )
        _, read = _tools(engine)
        out = _loads(
            await read.call(_context(_event()), memory_id=mid, include_source=True)
        )
        assert out["status"] == "success"
        assert out["source_status"] == "source_unavailable"

    @pytest.mark.asyncio
    async def test_fully_forgotten_memory_lifecycle_limited(self, engine):
        atoms = [MemoryAtom(parent_memory_id=0, content="唯一事实")]
        mid = await engine.add_memory(
            content="summary",
            session_id="test:private:s1",
            atoms=atoms,
        )
        await engine.db_connection.execute(
            "UPDATE memory_atoms SET status = ? WHERE parent_memory_id = ?",
            (AtomStatus.FORGOTTEN.value, mid),
        )
        await engine.db_connection.commit()
        _, read = _tools(engine)
        out = _loads(await read.call(_context(_event()), memory_id=mid))
        assert out["status"] == "lifecycle_limited"


class TestAgenticIntegrationChain:
    @pytest.mark.asyncio
    async def test_speaker_search_deepread_exclude_chain(self, engine):
        """交接文 §9.2：按发言人查 → 按 ID 深读 → 排除已看续查。"""
        await engine.add_memory(
            content="账号A去年一起爬山的共同经历",
            session_id="test:private:s1",
            metadata={
                "participant_identities": _identities("qq:1001"),
                "source_time_start": "2026-03-05T09:00:00",
            },
            source_messages=[
                {
                    "role": "user",
                    "sender_id": "1001",
                    "timestamp": 100.0,
                    "content": "周末去爬山吧",
                }
            ],
        )
        await engine.add_memory(
            content="账号A本月的无关新背景",
            session_id="test:private:s1",
            metadata={"participant_identities": _identities("qq:1001")},
        )
        await engine.add_memory(
            content="隔离会话的高度相似记录",
            session_id="test:private:other",
            metadata={"participant_identities": _identities("qq:1001")},
        )
        search, read = _tools(engine)
        event = _event()

        step1 = _loads(
            await search.call(_context(event), query="", target="current_speaker", k=5)
        )
        assert step1["status"] == "success"
        ids = {item["id"] for item in step1["results"]}
        assert len(ids) == 2  # 隔离会话记录不可见

        memory_id = step1["results"][0]["id"]
        step2 = _loads(
            await read.call(
                _context(event), memory_id=memory_id, include_source=True
            )
        )
        assert step2["status"] == "success"
        assert step2["source_status"] in ("ok", "source_unavailable")

        step3 = _loads(
            await search.call(
                _context(event),
                query="爬山",
                k=5,
                exclude_ids=[memory_id],
            )
        )
        assert step3["status"] == "success"
        assert all(item["id"] != memory_id for item in step3["results"])

        remaining = step3.get("remaining_budget", {})
        assert 0 <= remaining.get("remaining_calls", 0) <= 2
