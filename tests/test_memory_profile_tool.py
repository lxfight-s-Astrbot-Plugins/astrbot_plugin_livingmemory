"""update_user_profile Agent 工具：证据/置信度/开关行为。"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot.api.platform import MessageType
from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine
from astrbot_plugin_livingmemory.core.tools.memory_profile_tool import (
    UpdateUserProfileTool,
)

import astrbot_plugin_livingmemory.core.tools.memory_profile_tool as tool_module

from .test_memory_engine import _FakeFaissDB

SCOPE = "test:private:profile-tool"
PERSONA = "persona-tool"


class _Event:
    unified_msg_origin = SCOPE

    def get_platform_id(self) -> str:
        return "test"

    def get_message_type(self):
        return MessageType.FRIEND_MESSAGE

    def get_sender_id(self) -> str:
        return "user-1"

    def get_sender_name(self) -> str:
        return "Tester"


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


def _tool(engine) -> UpdateUserProfileTool:
    return UpdateUserProfileTool(
        context=SimpleNamespace(),
        config_manager=ConfigManager({}),
        memory_engine=engine,
    )


def _agent_context():
    return SimpleNamespace(context=SimpleNamespace(event=_Event()))


@pytest.mark.asyncio
async def test_tool_writes_profile_with_evidence(
    tmp_path: Path, monkeypatch
):
    engine = await _make_engine(tmp_path)
    try:
        memory_id = await engine.add_memory(
            content="用户喜欢燕麦拿铁",
            session_id=SCOPE,
            persona_id=PERSONA,
            importance=0.8,
        )
        monkeypatch.setattr(
            tool_module, "get_persona_id", AsyncMock(return_value=PERSONA)
        )
        tool = _tool(engine)

        raw = await tool.call(
            _agent_context(),
            section="basic",
            field="称呼",
            value="小林",
            operation="set",
            confidence=0.9,
            evidence=[memory_id],
        )
        payload = json.loads(raw)
        assert payload["updated"] is True
        assert payload["version"] == 1
        profile = await engine.profile_manager.get_profile(SCOPE, PERSONA)
        assert profile["profile"]["basic"]["称呼"] == "小林"
        assert tool.name == "update_user_profile"
        assert set(tool.parameters["properties"]) >= {
            "section",
            "field",
            "value",
            "operation",
            "confidence",
            "evidence",
        }
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_tool_without_evidence_returns_pending(
    tmp_path: Path, monkeypatch
):
    engine = await _make_engine(tmp_path)
    try:
        monkeypatch.setattr(
            tool_module, "get_persona_id", AsyncMock(return_value=PERSONA)
        )
        tool = _tool(engine)

        raw = await tool.call(
            _agent_context(),
            section="preferences",
            field="饮品",
            value="燕麦拿铁",
            operation="set",
            confidence=0.9,
            evidence=[],
        )
        payload = json.loads(raw)
        assert payload["updated"] is False
        assert payload["pending"] == 1
        profile = await engine.profile_manager.get_profile(SCOPE, PERSONA)
        assert profile["profile"]["preferences"] == {}
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_tool_reports_disabled_or_uninitialized_manager(tmp_path: Path):
    engine = await _make_engine(tmp_path, profile_enabled=False)
    try:
        tool = _tool(engine)
        raw = await tool.call(
            _agent_context(),
            section="basic",
            field="称呼",
            value="小林",
        )
        assert json.loads(raw)["updated"] is False

        tool.memory_engine = SimpleNamespace(profile_manager=None)
        raw = await tool.call(
            _agent_context(),
            section="basic",
            field="称呼",
            value="小林",
        )
        assert json.loads(raw)["updated"] is False
        assert "initialized" in json.loads(raw)["error"]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_tool_rejects_invalid_arguments(tmp_path: Path, monkeypatch):
    engine = await _make_engine(tmp_path)
    try:
        monkeypatch.setattr(
            tool_module, "get_persona_id", AsyncMock(return_value=PERSONA)
        )
        tool = _tool(engine)
        raw = await tool.call(
            _agent_context(),
            section="unknown",
            field="x",
            value="y",
        )
        payload = json.loads(raw)
        assert payload["updated"] is False
        assert "invalid" in payload["error"]
    finally:
        await engine.close()
