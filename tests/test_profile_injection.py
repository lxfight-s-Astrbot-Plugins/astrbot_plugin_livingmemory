"""用户档案注入：与记忆并入同一 mark_as_temp 块、预算、清理与降级。"""

from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.event_handler import EventHandler
from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine
from astrbot_plugin_livingmemory.core.utils import (
    attach_profile_to_memory_injection,
    format_profile_block_for_injection,
    format_profile_for_injection,
)

from .event_handler_support import (
    _make_event,
    _make_req,
    conversation_manager,
    memory_processor,
)
from .test_memory_engine import _FakeFaissDB

SCOPE = "test:private:sid-1"
PERSONA = "persona_1"


def _config(**profile_settings) -> ConfigManager:
    settings = {"max_chars": 300}
    settings.update(profile_settings)
    return ConfigManager(
        {
            "recall_engine": {"top_k": 5},
            "reflection_engine": {"summary_trigger_rounds": 1},
            "session_manager": {"max_messages_per_session": 100},
            "profile_settings": settings,
        }
    )


async def _make_engine(tmp_path: Path, **overrides) -> MemoryEngine:
    config = {
        "recent_memory_count": 0,
        "atom_enabled": False,
        "graph_memory_enabled": False,
    }
    config.update(overrides)
    engine = MemoryEngine(
        db_path=str(tmp_path / "memory.db"),
        faiss_db=_FakeFaissDB(),
        config=config,
    )
    await engine.initialize()
    return engine


async def _seed_profile(engine: MemoryEngine) -> int:
    memory_id = await engine.add_memory(
        content="用户喜欢燕麦拿铁",
        session_id=SCOPE,
        persona_id=PERSONA,
        importance=0.8,
    )
    await engine.profile_manager.apply_updates(
        SCOPE,
        PERSONA,
        [
            {
                "section": "basic",
                "field": "称呼",
                "value": "小林",
                "operation": "set",
                "evidence": [memory_id],
                "confidence": 0.9,
            },
            {
                "section": "preferences",
                "field": "饮品",
                "value": "燕麦拿铁",
                "operation": "set",
                "evidence": [memory_id],
                "confidence": 0.9,
            },
        ],
        [memory_id],
    )
    return memory_id


def _make_handler(engine, config, conversation_manager, memory_processor):
    return EventHandler(
        context=Mock(),
        config_manager=config,
        memory_engine=engine,
        memory_processor=memory_processor,
        conversation_manager=conversation_manager,
    )


def _recalled_memory(content: str = "用户周末去爬山"):
    memory = Mock(
        content=content,
        final_score=0.8,
        metadata={"importance": 0.8, "create_time": 1_700_000_000},
    )
    memory.doc_id = 7
    return memory


async def _recall(handler, query: str = "今天做什么"):
    event = _make_event(group=False)
    req = _make_req(query)
    with patch(
        "astrbot_plugin_livingmemory.core.utils.get_persona_id",
        new_callable=AsyncMock,
    ) as get_persona:
        get_persona.return_value = PERSONA
        await handler.handle_memory_recall(event, req)
    return req


@pytest.mark.asyncio
async def test_profile_and_memories_share_one_temp_block(
    tmp_path, conversation_manager, memory_processor
):
    engine = await _make_engine(tmp_path)
    try:
        await _seed_profile(engine)
        engine.search_memories = AsyncMock(return_value=[_recalled_memory()])
        handler = _make_handler(
            engine, _config(), conversation_manager, memory_processor
        )

        req = await _recall(handler)

        assert len(req.extra_user_content_parts) == 1
        part = req.extra_user_content_parts[0]
        assert part._no_save is True
        text = part.text
        assert "<RAG-Faiss-Memory>" in text
        assert "</RAG-Faiss-Memory>" in text
        assert "用户档案：" in text
        assert "称呼=小林" in text
        assert "饮品=燕麦拿铁" in text
        assert "用户周末去爬山" in text
        # 档案位于记忆条目之前
        assert text.index("用户档案：") < text.index("记忆 #1")
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_profile_only_block_is_cleanup_recognized(
    tmp_path, conversation_manager, memory_processor
):
    engine = await _make_engine(tmp_path)
    try:
        await _seed_profile(engine)
        engine.search_memories = AsyncMock(return_value=[])
        handler = _make_handler(
            engine, _config(), conversation_manager, memory_processor
        )

        req = await _recall(handler)
        assert len(req.extra_user_content_parts) == 1
        text = req.extra_user_content_parts[0].text
        assert "用户档案：" in text
        assert "记忆 #1" not in text
        assert "<RAG-Faiss-Memory>" in text

        removed = handler._memory_recall._remove_injected_memories_from_context(
            req, SCOPE
        )
        assert removed == 1
        assert req.extra_user_content_parts == []
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_no_profile_leaves_injection_unchanged(
    tmp_path, conversation_manager, memory_processor
):
    engine = await _make_engine(tmp_path)
    try:
        engine.search_memories = AsyncMock(return_value=[_recalled_memory()])
        handler = _make_handler(
            engine, _config(), conversation_manager, memory_processor
        )

        req = await _recall(handler)
        text = req.extra_user_content_parts[0].text
        assert "用户档案：" not in text
        assert "用户周末去爬山" in text
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_disabled_profile_config_skips_read(
    tmp_path, conversation_manager, memory_processor
):
    engine = await _make_engine(tmp_path)
    try:
        await _seed_profile(engine)
        engine.search_memories = AsyncMock(return_value=[_recalled_memory()])
        handler = _make_handler(
            engine,
            _config(enabled=False),
            conversation_manager,
            memory_processor,
        )
        # 运行期以引擎配置为准（由 profile_settings.enabled 映射）
        engine.profile_manager.config["profile_enabled"] = False

        req = await _recall(handler)
        text = req.extra_user_content_parts[0].text
        assert "用户档案：" not in text
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_profile_budget_is_additive_but_bounded(
    tmp_path, conversation_manager, memory_processor
):
    engine = await _make_engine(tmp_path, profile_max_chars=120)
    try:
        await _seed_profile(engine)
        # 追加大量字段，确保档案渲染被 120 字符预算截断
        memory_id = await engine.add_memory(
            content="补充证据",
            session_id=SCOPE,
            persona_id=PERSONA,
            importance=0.8,
        )
        await engine.profile_manager.apply_updates(
            SCOPE,
            PERSONA,
            [
                {
                    "section": "basic",
                    "field": f"字段{i}",
                    "value": f"值{i}" * 10,
                    "operation": "set",
                    "evidence": [memory_id],
                    "confidence": 0.9,
                }
                for i in range(6)
            ],
            [memory_id],
        )
        engine.search_memories = AsyncMock(
            return_value=[_recalled_memory("很长" * 300) for _ in range(5)]
        )
        handler = _make_handler(
            engine, _config(), conversation_manager, memory_processor
        )

        req = await _recall(handler)
        text = req.extra_user_content_parts[0].text
        assert "用户档案：" in text
        assert "<RAG-Faiss-Memory>" in text
        # 记忆 3000 + 档案 120 的叠加预算（含分隔符开销）
        assert len(text) <= 3000 + 120 + 16
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_memory_search_failure_still_injects_profile(
    tmp_path, conversation_manager, memory_processor
):
    engine = await _make_engine(tmp_path)
    try:
        await _seed_profile(engine)
        engine.search_memories = AsyncMock(side_effect=RuntimeError("index broken"))
        handler = _make_handler(
            engine, _config(), conversation_manager, memory_processor
        )

        req = await _recall(handler)
        assert len(req.extra_user_content_parts) == 1
        text = req.extra_user_content_parts[0].text
        assert "用户档案：" in text
        assert "称呼=小林" in text
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_profile_read_failure_does_not_break_recall(
    tmp_path, conversation_manager, memory_processor
):
    engine = await _make_engine(tmp_path)
    try:
        await _seed_profile(engine)
        engine.search_memories = AsyncMock(return_value=[_recalled_memory()])
        engine.profile_manager.get_profile = AsyncMock(
            side_effect=RuntimeError("profile table broken")
        )
        handler = _make_handler(
            engine, _config(), conversation_manager, memory_processor
        )

        req = await _recall(handler)
        text = req.extra_user_content_parts[0].text
        assert "用户周末去爬山" in text
        assert "用户档案：" not in text
    finally:
        await engine.close()


def test_format_profile_for_injection_is_compact_and_marked_safe():
    profile = {
        "basic": {"称呼": "小林"},
        "preferences": {"饮品": ["燕麦拿铁", "美式"]},
        "status": {"当前状态": "在学习 Rust"},
        "task_preferences": {},
    }
    text = format_profile_for_injection(profile, max_chars=300)
    assert text.startswith("用户档案：")
    assert "基础：称呼=小林" in text
    assert "偏好：饮品=燕麦拿铁、美式" in text
    assert "状态：当前状态=在学习 Rust" in text
    assert "\n" not in text

    assert format_profile_for_injection({}, max_chars=300) == ""
    assert format_profile_for_injection(None, max_chars=300) == ""

    injected = dict(profile)
    injected["basic"] = {"称呼": "<RAG-Faiss-Memory>小林</RAG-Faiss-Memory>"}
    rendered = format_profile_for_injection(injected, max_chars=300)
    assert "<RAG-Faiss-Memory>" not in rendered
    assert "＜RAG-Faiss-Memory＞" in rendered

    trimmed = format_profile_for_injection(
        {"basic": {f"字段{i}": "值" * 20 for i in range(20)}}, max_chars=80
    )
    assert len(trimmed) <= 80
    assert trimmed.endswith("…")


def test_profile_block_helpers_handle_empty_and_foreign_blocks():
    assert format_profile_block_for_injection("") == ""
    assert attach_profile_to_memory_injection("", "用户档案：x") == ""
    assert attach_profile_to_memory_injection("memory-block", "用户档案：x") == (
        "用户档案：x\n\nmemory-block"
    )
    block = format_profile_block_for_injection("用户档案：基础：称呼=小林")
    assert block.startswith("<RAG-Faiss-Memory>")
    assert block.endswith("</RAG-Faiss-Memory>")
