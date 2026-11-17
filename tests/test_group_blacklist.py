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


# ---------------- 设置页选项来源 ----------------


def _handler(sessions: list, blacklisted) -> SettingsHandler:
    manager = SimpleNamespace(
        store=SimpleNamespace(get_recent_sessions=AsyncMock(return_value=sessions))
    )
    plugin = SimpleNamespace(
        initializer=SimpleNamespace(conversation_manager=manager),
        config_manager=SimpleNamespace(
            get=lambda key, default=None: (
                blacklisted if key == "filtering_settings.group_blacklist" else default
            )
        ),
    )
    return SettingsHandler(utils=Mock(), plugin=plugin)


@pytest.mark.asyncio
async def test_group_options_list_only_group_sessions():
    """选项来自插件已记录的群聊会话，私聊不出现。"""
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
    handler = _handler(sessions, [])

    options = await handler._group_options()

    assert list(options) == ["111"]
    assert "aiocqhttp" in options["111"]
    assert "42" in options["111"]


@pytest.mark.asyncio
async def test_group_options_keep_saved_entries_missing_from_sessions():
    """已保存但暂时没有新消息的群仍要出现在选项里，避免被误删。"""
    handler = _handler([], ["555", "666"])

    options = await handler._group_options()

    assert set(options) == {"555", "666"}


@pytest.mark.asyncio
async def test_group_options_survive_store_failure():
    """会话库读取失败时退化为只显示已保存项，不影响设置页加载。"""
    manager = SimpleNamespace(
        store=SimpleNamespace(
            get_recent_sessions=AsyncMock(side_effect=RuntimeError("db locked"))
        )
    )
    plugin = SimpleNamespace(
        initializer=SimpleNamespace(conversation_manager=manager),
        config_manager=SimpleNamespace(
            get=lambda key, default=None: (
                ["777"] if key == "filtering_settings.group_blacklist" else default
            )
        ),
    )
    handler = SettingsHandler(utils=Mock(), plugin=plugin)

    options = await handler._group_options()

    assert list(options) == ["777"]
