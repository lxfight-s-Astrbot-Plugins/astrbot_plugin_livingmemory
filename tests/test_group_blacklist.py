"""群聊记忆黑名单：访问闸门与设置页选项来源（#440）。

黑名单只在插件配置中维护（不提供命令），因此这里不测任何命令交互。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from astrbot_plugin_livingmemory.core.memory_scope import (
    is_event_memory_allowed,
    is_group_blacklisted,
)
from astrbot_plugin_livingmemory.core.page_api_modules.settings_handler import (
    SettingsHandler,
)

GROUP = "998877"
SESSION = "test:GroupMessage:998877"


class _Event:
    def __init__(self, session_id: str = SESSION, group_id: str = GROUP) -> None:
        self.unified_msg_origin = session_id
        self.group_id = group_id
        self.sender_id = "user-1"

    def get_sender_id(self):
        return self.sender_id

    def get_sender_name(self):
        return "Tester"

    def get_platform_name(self):
        return "test"

    def get_platform_id(self):
        return "test-instance"

    def get_group_id(self):
        return self.group_id


def _config(blacklist, *, whitelist: bool = False) -> dict:
    return {
        "access_control": {"whitelist_enabled": whitelist, "allowed_ids": ""},
        "filtering_settings": {"group_blacklist": blacklist},
    }


# ---------------- 匹配规则 ----------------


def test_blacklist_matches_group_id():
    assert is_group_blacklisted(_config([GROUP]), _Event()) is True


def test_blacklist_matches_full_session_id():
    assert is_group_blacklisted(_config([SESSION]), _Event()) is True


def test_blacklist_matches_session_prefix_and_suffix():
    assert is_group_blacklisted(_config(["test:GroupMessage"]), _Event()) is True
    assert is_group_blacklisted(_config(["GroupMessage:998877"]), _Event()) is True
    assert is_group_blacklisted(_config([GROUP]), _Event()) is True


def test_blacklist_accepts_newline_text_for_manual_edits():
    """即使直接从配置文件粘贴多行文本，也按列表解析。"""
    assert is_group_blacklisted(_config("111\n998877"), _Event()) is True


def test_blacklist_ignores_other_groups():
    assert is_group_blacklisted(_config(["111222"]), _Event()) is False


def test_empty_blacklist_matches_nothing():
    assert is_group_blacklisted(_config([]), _Event()) is False
    assert is_group_blacklisted(_config(None), _Event()) is False


def test_blacklist_is_case_insensitive():
    assert (
        is_group_blacklisted(
            _config(["TEST:GroupMessage:998877"]), _Event(session_id=SESSION.lower())
        )
        is True
    )


def test_blacklist_blocks_even_when_whitelist_is_disabled():
    assert is_event_memory_allowed(_config([GROUP]), _Event()) is False


def test_blacklist_wins_over_whitelist_allowance():
    config = _config([GROUP], whitelist=True)
    config["access_control"]["allowed_ids"] = "user-1"
    assert is_event_memory_allowed(config, _Event()) is False


def test_private_session_is_not_blocked_by_group_id_rule():
    private = _Event(session_id="test:FriendMessage:user-1", group_id="")
    assert is_group_blacklisted(_config([GROUP]), private) is False
    assert is_group_blacklisted(_config(["test:FriendMessage:user-1"]), private) is True


# ---------------- 设置页选项来源（走公开配置接口） ----------------


def _settings_handler(sessions, blacklisted):
    manager = SimpleNamespace(
        store=SimpleNamespace(get_recent_sessions=AsyncMock(return_value=sessions))
    )
    plugin = SimpleNamespace(
        context=SimpleNamespace(
            get_config=lambda: {"platform": []},
            platform_manager=SimpleNamespace(get_insts=lambda: []),
        ),
        initializer=SimpleNamespace(
            conversation_manager=manager, is_initialized=False, memory_engine=None
        ),
        config_manager=SimpleNamespace(
            get=lambda key, default=None: (
                blacklisted if key == "filtering_settings.group_blacklist" else default
            )
        ),
    )
    utils = SimpleNamespace(
        ok=lambda payload: payload,
        error=lambda message: {"status": "error", "message": message},
    )
    return SettingsHandler(utils=utils, plugin=plugin)


def _group_field(payload) -> dict:
    fields = [
        field
        for section in payload["sections"]
        for field in section["fields"]
    ]
    return next(
        field
        for field in fields
        if field["path"] == "filtering_settings.group_blacklist"
    )


@pytest.mark.asyncio
async def test_settings_api_offers_recorded_groups_as_choices():
    """配置接口把插件记录过的群聊作为选项返回，用户不需要手填会话 ID。"""
    from quart import Quart

    sessions = [
        SimpleNamespace(
            session_id="test:GroupMessage:111",
            platform="aiocqhttp",
            message_count=42,
        ),
        SimpleNamespace(
            session_id="test:FriendMessage:user-1", platform="test", message_count=3
        ),
    ]
    handler = _settings_handler(sessions, [])

    async with Quart(__name__).test_request_context("/"):
        payload = await handler.get_config()

    field = _group_field(payload)
    assert field["type"] == "group-list"
    assert field["options"] == ["111"]
    assert "aiocqhttp" in field["option_labels"][0]


@pytest.mark.asyncio
async def test_settings_api_keeps_saved_groups_missing_from_sessions():
    """已保存但暂时没有新消息的群仍出现在选项里，避免被误删。"""
    from quart import Quart

    handler = _settings_handler([], ["555", "666"])

    async with Quart(__name__).test_request_context("/"):
        payload = await handler.get_config()

    assert set(_group_field(payload)["options"]) == {"555", "666"}


@pytest.mark.asyncio
async def test_settings_api_survives_conversation_store_failure():
    """会话库读取失败时只显示已保存项，设置页仍可打开。"""
    from quart import Quart

    manager = SimpleNamespace(
        store=SimpleNamespace(
            get_recent_sessions=AsyncMock(side_effect=RuntimeError("db locked"))
        )
    )
    plugin = SimpleNamespace(
        context=SimpleNamespace(
            get_config=lambda: {"platform": []},
            platform_manager=SimpleNamespace(get_insts=lambda: []),
        ),
        initializer=SimpleNamespace(
            conversation_manager=manager, is_initialized=False, memory_engine=None
        ),
        config_manager=SimpleNamespace(
            get=lambda key, default=None: (
                ["777"] if key == "filtering_settings.group_blacklist" else default
            )
        ),
    )
    utils = SimpleNamespace(
        ok=lambda payload: payload,
        error=lambda message: {"status": "error", "message": message},
    )
    handler = SettingsHandler(utils=utils, plugin=plugin)

    async with Quart(__name__).test_request_context("/"):
        payload = await handler.get_config()

    assert list(_group_field(payload)["options"]) == ["777"]
