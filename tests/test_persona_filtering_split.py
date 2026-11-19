"""use_persona_filtering 只控制召回，写入仍保留真实 persona_id（#438）。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_livingmemory.core.memory_scope import resolve_event_memory_access


class _Event:
    unified_msg_origin = "test:private:s1"

    def get_platform_id(self) -> str:
        return "test"

    def get_platform_name(self) -> str:
        return "test"

    def get_sender_id(self) -> str:
        return "u1"

    def get_sender_name(self) -> str:
        return "Tester"

    def get_group_id(self) -> str:
        return ""

    def get_extra(self, key, default=None):
        return default


def _config(use_persona_filtering: bool) -> dict:
    return {
        "filtering_settings": {
            "memory_scope_mode": "legacy",
            "use_session_filtering": True,
            "use_persona_filtering": use_persona_filtering,
            "isolated_sessions": "",
        },
        "access_control": {"whitelist_enabled": False},
    }


@pytest.mark.asyncio
async def test_persona_filtering_off_keeps_write_persona_but_drops_recall_filter(
    monkeypatch,
):
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.utils.get_persona_id",
        AsyncMock(return_value="persona-a"),
    )

    access = await resolve_event_memory_access(
        _config(False), SimpleNamespace(), _Event()
    )

    # 写入/档案仍拿到真实人格，避免新记忆 persona_id 全为空。
    assert access.persona_id == "persona-a"
    # 检索不按人格过滤，实现跨人格共享召回。
    assert access.recall_persona_id is None


@pytest.mark.asyncio
async def test_persona_filtering_on_filters_both_paths(monkeypatch):
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.utils.get_persona_id",
        AsyncMock(return_value="persona-a"),
    )

    access = await resolve_event_memory_access(
        _config(True), SimpleNamespace(), _Event()
    )

    assert access.persona_id == "persona-a"
    assert access.recall_persona_id == "persona-a"


@pytest.mark.asyncio
async def test_persona_filtering_off_without_resolvable_persona(monkeypatch):
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.utils.get_persona_id",
        AsyncMock(return_value=None),
    )

    access = await resolve_event_memory_access(
        _config(False), SimpleNamespace(), _Event()
    )

    assert access.persona_id is None
    assert access.recall_persona_id is None
