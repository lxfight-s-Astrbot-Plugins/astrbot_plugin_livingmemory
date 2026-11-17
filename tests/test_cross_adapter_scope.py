"""跨适配器共享用户记忆：作用域解耦与旧作用域并联召回（#442）。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_livingmemory.core.memory_scope import (
    known_platform_names,
    legacy_user_scopes,
    resolve_event_memory_access,
    resolve_user_memory_scope,
    user_scope_ignores_platform,
)

PLATFORM_SCOPES = (
    "livingmemory:user:aiocqhttp:ghost",
    "livingmemory:user:qq_official:ghost",
)


class _Event:
    def __init__(self, platform: str = "aiocqhttp") -> None:
        self.unified_msg_origin = f"{platform}:FriendMessage:ghost"
        self.sender_id = "ghost"
        self.sender_name = "Ghost"
        self.platform = platform

    def get_sender_id(self):
        return self.sender_id

    def get_sender_name(self):
        return self.sender_name

    def get_platform_name(self):
        return self.platform

    def get_platform_id(self):
        return f"{self.platform}-instance"

    def get_group_id(self):
        return ""

    def get_extra(self, key, default=None):
        return default


def _config(ignore_platform: bool) -> dict:
    return {
        "access_control": {"whitelist_enabled": False, "identity_aliases": ""},
        "filtering_settings": {
            "memory_scope_mode": "user",
            "use_session_filtering": True,
            "use_persona_filtering": True,
            "user_scope_ignore_platform": ignore_platform,
        },
    }


class _UnknownPlatformContext:
    """宿主 Context 的替身：不暴露平台实例。"""

    platform_manager = None

    def get_config(self):
        return {}


def test_flag_defaults_to_off():
    assert user_scope_ignores_platform({"filtering_settings": {}}) is False


def test_scope_keeps_platform_by_default():
    assert (
        resolve_user_memory_scope(_config(False), _Event()) == PLATFORM_SCOPES[0]
    )


def test_scope_drops_platform_when_enabled():
    assert resolve_user_memory_scope(_config(True), _Event()) == (
        "livingmemory:user:ghost"
    )


def test_legacy_scopes_are_empty_when_flag_is_off():
    assert legacy_user_scopes(_config(False), _Event(), ("aiocqhttp",)) == ()


def test_legacy_scopes_cover_every_known_platform():
    scopes = legacy_user_scopes(
        _config(True), _Event(), ("aiocqhttp", "qq_official")
    )

    assert set(scopes) == set(PLATFORM_SCOPES)
    # 去重：当前事件平台已包含在已知平台里
    assert len(scopes) == len(set(scopes))


def test_legacy_scopes_always_include_current_platform():
    scopes = legacy_user_scopes(_config(True), _Event(), ())

    assert scopes == (PLATFORM_SCOPES[0],)


def test_known_platform_names_reads_instances_and_config():
    class _Platform:
        def __init__(self, name: str) -> None:
            self._name = name

        def meta(self):
            return SimpleNamespace(name=self._name)

    context = SimpleNamespace(
        platform_manager=SimpleNamespace(
            get_insts=lambda: [_Platform("aiocqhttp"), _Platform("qq_official")]
        ),
        get_config=lambda: {"platform": [{"type": "telegram"}, "junk"]},
    )

    assert known_platform_names(context) == (
        "aiocqhttp",
        "qq_official",
        "telegram",
    )


def test_known_platform_names_tolerates_broken_host_objects():
    def _boom():
        raise RuntimeError("platform manager not ready")

    context = SimpleNamespace(
        platform_manager=SimpleNamespace(get_insts=_boom),
        get_config=lambda: {"platform": None},
    )

    assert known_platform_names(context) == ()
    assert known_platform_names(SimpleNamespace()) == ()


@pytest.mark.asyncio
async def test_access_exposes_legacy_scopes_only_when_enabled(monkeypatch):
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.utils.get_persona_id",
        AsyncMock(return_value="p1"),
    )
    context = SimpleNamespace(
        platform_manager=SimpleNamespace(get_insts=lambda: []),
        get_config=lambda: {"platform": [{"type": "qq_official"}]},
    )

    off = await resolve_event_memory_access(_config(False), context, _Event())
    on = await resolve_event_memory_access(_config(True), context, _Event())

    assert off.scope == PLATFORM_SCOPES[0]
    assert off.legacy_scopes == ()
    assert on.scope == "livingmemory:user:ghost"
    # 旧格式的两个平台作用域都会并联召回，历史记忆无需迁移
    assert set(on.legacy_scopes) == set(PLATFORM_SCOPES)
    assert on.extra_read_scopes == list(on.legacy_scopes)


@pytest.mark.asyncio
async def test_extra_read_scopes_include_channel_scope(monkeypatch):
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.utils.get_persona_id",
        AsyncMock(return_value=None),
    )
    config = _config(True)
    config["channel_memory"] = {"enabled": True, "min_user_slots": 1}

    class _GroupEvent(_Event):
        def get_group_id(self):
            return "group-42"

    event = _GroupEvent()
    event.unified_msg_origin = "aiocqhttp:GroupMessage:group-42"

    access = await resolve_event_memory_access(
        config, _UnknownPlatformContext(), event
    )

    assert access.channel_scope is not None
    assert access.extra_read_scopes[0] == access.channel_scope
    assert set(access.extra_read_scopes[1:]) == set(access.legacy_scopes)


@pytest.mark.asyncio
async def test_private_chat_has_no_channel_scope(monkeypatch):
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.utils.get_persona_id",
        AsyncMock(return_value=None),
    )
    config = _config(True)
    config["channel_memory"] = {"enabled": True}

    access = await resolve_event_memory_access(
        config, _UnknownPlatformContext(), _Event()
    )

    assert access.channel_scope is None
    assert access.extra_read_scopes == list(access.legacy_scopes)
